//! Metadata-only exact-symbol lookup over an indexed workspace.
//!
//! This path resolves a workspace, optionally refreshes it, and reads stored
//! declaration metadata directly. It never runs a full-text or vector query.

use std::path::{Path, PathBuf};

use crate::{
    EngineError,
    api::{
        context::options::RefreshPolicy,
        index::IndexOptions,
        symbols::{SymbolCoverage, SymbolItem, SymbolRole, SymbolsOptions, SymbolsResult},
    },
    domain::{IndexState, Workspace},
    models::ModelRuntimeManager,
    pipelines::{
        indexed_search::context::file_freshness_status, indexing::service::WorkspaceIndexService,
    },
    storage::{
        read_session::ReadSessionCache,
        types::{SymbolCursorKey, SymbolDeclarationPage, SymbolFileCoverage},
    },
    utils::{directory_exists, resolve_directory, sha256_hex},
    workspace::{
        find_nearest_workspace,
        lock::{LockWait, try_acquire_workspace_read},
        manifest::read_workspace_manifest,
    },
};

#[allow(clippy::too_many_lines)]
pub(crate) async fn symbols(
    indexing: &WorkspaceIndexService,
    models: &ModelRuntimeManager,
    options: &SymbolsOptions,
    read_sessions: Option<&ReadSessionCache>,
) -> Result<SymbolsResult, EngineError> {
    let name = options.normalized_name()?.to_owned();
    let limit = options.effective_limit()?;
    let path = options
        .path
        .as_deref()
        .map(normalize_path)
        .filter(|path| !path.is_empty());
    let wait = LockWait::new(options.signal.as_ref(), options.lock_timeout_ms)?;
    wait.check_cancelled()?;
    let requested_root = resolve_directory(options.root.as_deref())?;
    let Some((source_root, layout)) = find_nearest_workspace(&requested_root)? else {
        return Err(workspace_index_unavailable(
            &requested_root,
            "no workspace manifest was found",
        ));
    };
    if options
        .refresh
        .map_or(options.auto_update, |policy| policy == RefreshPolicy::Wait)
        && indexing
            .workspace_needs_refresh(&source_root, &layout, &wait, options.signal.clone())
            .await?
    {
        indexing
            .index(models, refresh_options(options, source_root.clone()))
            .await?;
    }

    let wait = LockWait::new(options.signal.as_ref(), options.lock_timeout_ms)?;
    let _lock = loop {
        wait.check_cancelled()?;
        if let Some(lock) = try_acquire_workspace_read(&source_root, &layout, "symbols")? {
            break lock;
        }
        wait.wait_for_retry(layout.storage_dir(), "symbols").await?;
    };
    let workspace = read_workspace_manifest(&source_root, &layout)?.ok_or_else(|| {
        workspace_index_unavailable(&source_root, "workspace manifest disappeared")
    })?;
    if workspace.index() == &IndexState::Disabled {
        return Err(EngineError::unsupported(format!(
            "workspace indexing is disabled at {}",
            source_root.display()
        )));
    }
    if !workspace.index_enabled() {
        return Err(workspace_index_unavailable(
            &source_root,
            "workspace index has not been built",
        ));
    }
    if !directory_exists(workspace.layout().index_dir())? {
        return Err(workspace_index_unavailable(
            &source_root,
            "index storage is missing",
        ));
    }
    let generation = workspace
        .storage_generation()
        .expect("published index generation")
        .to_owned();
    let updated_ms = workspace.updated_epoch_ms();
    let query_hash = symbol_query_hash(
        &name,
        options.scope.as_deref(),
        &options.symbol_types,
        path.as_deref(),
    );
    let after = options
        .cursor
        .as_deref()
        .map(|cursor| decode_cursor(cursor, &generation, updated_ms, &query_hash))
        .transpose()?;
    let storage = match read_sessions {
        Some(cache) => cache.acquire(&layout, workspace.layout().index_dir(), &generation)?,
        None => {
            crate::storage::read_session::ReadSessionLease::open(workspace.layout().index_dir())?
        }
    };
    let page = storage.storage().enumerate_symbol_declarations(
        &name,
        &options.symbol_types,
        options.scope.as_deref(),
        path.as_deref(),
        after.as_ref(),
        limit,
    );
    let close = storage.close();
    let page = page?;
    close?;
    let next_cursor = page
        .has_more
        .then(|| {
            page.items
                .last()
                .map(crate::storage::types::SymbolDeclaration::cursor_key)
        })
        .flatten()
        .map(|key| encode_cursor(&generation, updated_ms, &query_hash, &key));
    let total = page.total;
    let coverage = page.coverage;
    let truncated = page.has_more;
    let next_cursor = next_cursor.filter(|_| truncated);
    let items = symbol_items(&workspace, page, &name);
    Ok(SymbolsResult {
        root: source_root,
        name,
        total,
        truncated,
        next_cursor,
        coverage: coverage.into(),
        items,
    })
}

fn symbol_items(workspace: &Workspace, page: SymbolDeclarationPage, name: &str) -> Vec<SymbolItem> {
    page.items
        .into_iter()
        .map(|declaration| {
            let relative_path = declaration.file.relative_path().as_path().to_path_buf();
            let absolute_path = workspace.source_root().join(&relative_path);
            let status = file_freshness_status(workspace.source_root(), &declaration.file);
            SymbolItem {
                entity_id: declaration.entity_id.as_str().to_owned(),
                name: name.to_owned(),
                symbol_type: declaration.metadata.symbol_type,
                scope: declaration.metadata.scope,
                signature: declaration.metadata.signature,
                language: declaration.metadata.language,
                relative_path,
                absolute_path,
                range: declaration.source_range.into(),
                role: SymbolRole::Definition,
                status,
            }
        })
        .collect()
}

const CURSOR_VERSION: u32 = 1;

#[derive(serde::Deserialize, serde::Serialize)]
struct SymbolCursorEnvelope {
    v: u32,
    generation: String,
    updated_ms: u64,
    query: String,
    after: SymbolCursorKey,
}

fn symbol_query_hash(
    name: &str,
    scope: Option<&str>,
    symbol_types: &[crate::domain::SymbolType],
    path: Option<&str>,
) -> String {
    let mut types = symbol_types
        .iter()
        .map(|kind| kind.as_str())
        .collect::<Vec<_>>();
    types.sort_unstable();
    types.dedup();
    sha256_hex(
        format!(
            "{name}\0{}\0{}\0{}",
            scope.unwrap_or(""),
            types.join(","),
            path.unwrap_or("")
        )
        .as_bytes(),
    )
}

fn encode_cursor(generation: &str, updated_ms: u64, query: &str, key: &SymbolCursorKey) -> String {
    let envelope = SymbolCursorEnvelope {
        v: CURSOR_VERSION,
        generation: generation.to_owned(),
        updated_ms,
        query: query.to_owned(),
        after: key.clone(),
    };
    let bytes = serde_json::to_vec(&envelope).expect("symbol cursor serialization");
    let checksum = sha256_hex(&bytes);
    format!("v{CURSOR_VERSION}:{}:{checksum}", hex::encode(bytes))
}

fn decode_cursor(
    cursor: &str,
    generation: &str,
    updated_ms: u64,
    query: &str,
) -> Result<SymbolCursorKey, EngineError> {
    let mut parts = cursor.splitn(3, ':');
    let version = parts.next().unwrap_or_default();
    let payload = parts.next().unwrap_or_default();
    let checksum = parts.next().unwrap_or_default();
    if version != format!("v{CURSOR_VERSION}") {
        return Err(invalid_cursor("unsupported cursor version"));
    }
    let bytes = hex::decode(payload).map_err(|_| invalid_cursor("cursor is not valid hex"))?;
    if sha256_hex(&bytes) != checksum {
        return Err(invalid_cursor("cursor checksum does not match"));
    }
    let envelope: SymbolCursorEnvelope =
        serde_json::from_slice(&bytes).map_err(|_| invalid_cursor("cursor payload is invalid"))?;
    if envelope.v != CURSOR_VERSION {
        return Err(invalid_cursor("cursor payload version is unsupported"));
    }
    if envelope.generation != generation || envelope.updated_ms != updated_ms {
        return Err(EngineError::invalid_argument(
            "symbol cursor was issued for a different index snapshot; restart enumeration",
        ));
    }
    if envelope.query != query {
        return Err(invalid_cursor(
            "cursor belongs to different symbol query parameters",
        ));
    }
    Ok(envelope.after)
}

fn invalid_cursor(reason: &str) -> EngineError {
    EngineError::invalid_argument(format!("invalid symbol cursor: {reason}"))
}

impl From<SymbolFileCoverage> for SymbolCoverage {
    fn from(coverage: SymbolFileCoverage) -> Self {
        Self {
            indexed_files: coverage.indexed,
            unfinished_files: coverage.unfinished,
            failed_files: coverage.failed,
            complete: coverage.unfinished == 0,
        }
    }
}

fn normalize_path(path: &str) -> String {
    path.replace('\\', "/").trim_start_matches("./").to_owned()
}

fn refresh_options(options: &SymbolsOptions, root: PathBuf) -> IndexOptions {
    IndexOptions {
        root: Some(root),
        require_existing_index: true,
        changes: vec![crate::api::index::options::WorkspaceChange::Rescan],
        signal: options.signal.clone(),
        allow_remote: options.allow_remote,
        authorized_remote: options.authorized_remote.clone(),
        api_key: options.api_key.clone(),
        embedding_concurrency: options.embedding_concurrency,
        lock_timeout_ms: options.lock_timeout_ms,
        runtime_device: options.device,
        runtime_model_cache: options.model_cache.clone(),
        ..IndexOptions::default()
    }
}

fn workspace_index_unavailable(root: &Path, reason: &str) -> EngineError {
    EngineError::not_found(format!("workspace index at {}: {reason}", root.display()))
}
