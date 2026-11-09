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
        symbols::{SymbolItem, SymbolRole, SymbolsOptions, SymbolsResult},
    },
    domain::{IndexState, Workspace},
    models::ModelRuntimeManager,
    pipelines::{
        indexed_search::context::file_freshness_status, indexing::service::WorkspaceIndexService,
    },
    storage::{read_session::ReadSessionCache, types::SymbolDeclarationPage},
    utils::{directory_exists, resolve_directory},
    workspace::{
        find_nearest_workspace,
        lock::{LockWait, try_acquire_workspace_read},
        manifest::read_workspace_manifest,
    },
};

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
    let storage = match read_sessions {
        Some(cache) => cache.acquire(
            &layout,
            workspace.layout().index_dir(),
            workspace
                .storage_generation()
                .expect("published index generation"),
        )?,
        None => {
            crate::storage::read_session::ReadSessionLease::open(workspace.layout().index_dir())?
        }
    };
    let page = storage.storage().enumerate_symbol_declarations(
        &name,
        &options.symbol_types,
        options.scope.as_deref(),
        path.as_deref(),
        limit,
    );
    let close = storage.close();
    let page = page?;
    close?;
    let total = page.total;
    let items = symbol_items(&workspace, page, &name);
    let truncated = total > items.len();
    Ok(SymbolsResult {
        root: source_root,
        name,
        total,
        truncated,
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
