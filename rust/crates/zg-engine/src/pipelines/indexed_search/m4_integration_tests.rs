#![cfg(test)]

use std::{
    collections::{BTreeSet, HashMap},
    path::{Path, PathBuf},
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};

use async_trait::async_trait;
use m4_rust_bridge_research::{Adapter, Hit};
use tempfile::tempdir;
use zg_host_native::{
    AllowAllPaths, NativeScanner, NativeWatcherConfig, NativeWatcherFactory, RootSpec, TaskControl,
    WatchRequest, WorkspaceChange as HostWorkspaceChange, WorkspaceWatcherFactoryPort,
};

use crate::{
    EngineError, EngineResult,
    api::{
        context::{
            options::{ContextRoute, ContextRouteMode, QueryFilter},
            result::MatchedBy,
        },
        index::{options::WorkspaceChange, progress::IndexProgressReporter},
    },
    domain::{
        Content, Entity, EntityId, EntityMetadata, FileId, FileRecord, IndexDescriptor, IndexState,
        ScanRules, SymbolType, Workspace,
        model::{EmbeddingModelInfo, EmbeddingResult, Metric, ModelInfo},
    },
    models::{EmbeddingConcurrencyDefaults, EmbeddingOptions, EmbeddingPrepareOptions, ModelError},
    pipelines::{
        indexed_search::{
            pipeline::{SearchPlan, SearchPlanResult, search_workspace_index},
            storage::SearchStorage,
        },
        indexing::{
            pipeline::{IndexEmbeddingRuntime, IndexingContext, index_workspace},
            storage::IndexStorage,
        },
    },
    storage::{
        IndexStore,
        manifest::FileManifest,
        staging::{DiskStagedBatches, STAGING_BATCH_FRAGMENTS},
        types::{
            IndexedFragment, StoragePathFilter, StorageSearchFilter, StorageSearchHit,
            StorageSearchPath, StoredFileAttributes, StoredSearchData,
            WorkspaceIndexStorageOptions,
        },
    },
};

const MAX_RESEARCH_FILE_FRAGMENTS: usize = 512;
const MAX_RESEARCH_FILE_TEXT_BYTES: usize = 16 * 1024 * 1024;
const MAX_RESEARCH_SEARCH_CANDIDATES: usize = 4096;

#[derive(Default)]
struct PassGate {
    state: Mutex<bool>,
    changed: Condvar,
}

impl PassGate {
    fn begin(&self) {
        *self.state.lock().expect("pass gate") = true;
    }

    fn wait(&self) {
        let mut active = self.state.lock().expect("pass gate");
        while *active {
            active = self.changed.wait(active).expect("pass gate wait");
        }
    }

    fn finish(&self) {
        *self.state.lock().expect("pass gate") = false;
        self.changed.notify_all();
    }
}

struct PendingUpdate {
    file_id: u64,
    revision: u64,
    staged: DiskStagedBatches,
}

struct M4ResearchStorage {
    native: IndexStore,
    sidecar: Mutex<Adapter>,
    pending_updates: Mutex<Vec<PendingUpdate>>,
    pending_deletes: Mutex<Vec<FileId>>,
    pass_gate: PassGate,
    fail_after_native_commit: AtomicBool,
}

impl M4ResearchStorage {
    fn open(root: &Path, model: &EmbeddingModelInfo) -> EngineResult<Self> {
        let native = IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
            storage_path: root.join("native"),
            embeddings: vec![model.clone()],
        })?;
        let mut sidecar =
            Adapter::open(&root.join("m4-sidecar")).map_err(EngineError::storage_failure)?;
        let mut staged_ids = Vec::new();
        for file in native.list_files()? {
            let state = sidecar
                .file_state(u64::from(file.id.get()))
                .map_err(EngineError::storage_failure)?;
            if state.found != 0 && state.visibility == 1 {
                staged_ids.push(file.id);
            }
        }
        if !staged_ids.is_empty() {
            native.delete_files(&staged_ids)?;
            native.finalize_writes()?;
            for file_id in staged_ids {
                let id = u64::from(file_id.get());
                let revision = sidecar
                    .file_state(id)
                    .map_err(EngineError::storage_failure)?
                    .revision
                    .checked_add(1)
                    .ok_or_else(|| {
                        EngineError::storage_failure("M4 recovery revision exhausted")
                    })?;
                sidecar
                    .delete(id, revision)
                    .map_err(EngineError::storage_failure)?;
            }
        }
        Ok(Self {
            native,
            sidecar: Mutex::new(sidecar),
            pending_updates: Mutex::new(Vec::new()),
            pending_deletes: Mutex::new(Vec::new()),
            pass_gate: PassGate::default(),
            fail_after_native_commit: AtomicBool::new(false),
        })
    }

    fn stage_replacement(
        &self,
        file: &FileRecord,
        entities: &[Entity],
        entries: &[IndexedFragment],
    ) -> EngineResult<()> {
        let mut entries = entries.to_vec();
        entries.sort_unstable_by(|left, right| left.fragment_id.cmp(&right.fragment_id));
        let staged = make_staged(file.clone(), entities.to_vec(), &entries)?;
        let docs = bounded_docs(&entries)?;
        let file_id = u64::from(file.id.get());

        self.pass_gate.begin();
        let mut sidecar = self.sidecar.lock().expect("M4 adapter");
        let state = sidecar
            .file_state(file_id)
            .map_err(EngineError::storage_failure)?;
        let revision = state
            .revision
            .checked_add(1)
            .ok_or_else(|| EngineError::storage_failure("M4 revision exhausted"))?;
        if let Err(error) = sidecar.stage_file(file_id, revision, &docs) {
            self.pass_gate.finish();
            return Err(EngineError::storage_failure(error));
        }
        self.pending_updates
            .lock()
            .expect("pending updates")
            .push(PendingUpdate {
                file_id,
                revision,
                staged,
            });
        Ok(())
    }

    fn stage_disk_replacement(&self, staged: DiskStagedBatches) -> EngineResult<()> {
        if staged.fragment_count() > MAX_RESEARCH_FILE_FRAGMENTS {
            return Err(EngineError::invalid_argument(format!(
                "research sidecar fixture is limited to {MAX_RESEARCH_FILE_FRAGMENTS} fragments per file"
            )));
        }
        let file = staged.file().clone();
        let mut entities = Vec::with_capacity(staged.entity_count());
        staged.for_each_entity_batch(|batch| {
            entities.extend_from_slice(batch);
            Ok(())
        })?;
        let mut reader = staged.fragment_reader()?;
        let mut entries = Vec::with_capacity(staged.fragment_count());
        loop {
            let batch = reader.next_batch(STAGING_BATCH_FRAGMENTS)?;
            if batch.is_empty() {
                break;
            }
            entries.extend(batch);
        }
        reader.finish()?;
        self.stage_replacement(&file, &entities, &entries)
    }

    fn search_m4(
        &self,
        query: &str,
        limit: usize,
        filter: Option<&StorageSearchFilter>,
    ) -> EngineResult<Vec<StorageSearchHit>> {
        self.pass_gate.wait();
        if limit == 0 {
            return Ok(Vec::new());
        }
        if limit > MAX_RESEARCH_SEARCH_CANDIDATES {
            return Err(EngineError::invalid_argument(
                "research M4 search limit exceeds 4096",
            ));
        }

        let mut scan = limit.max(32).min(MAX_RESEARCH_SEARCH_CANDIDATES);
        let mut sidecar = self.sidecar.lock().expect("M4 adapter");
        loop {
            let raw = sidecar
                .search(query, scan, scan as u32)
                .map_err(EngineError::storage_failure)?;
            let mapped = self.map_hits(&raw)?;
            let data = self.native.load_search_hits(&mapped)?;
            let visible = mapped
                .into_iter()
                .filter(|hit| self.matches_filter(hit, &data, filter))
                .collect::<Vec<_>>();
            if visible.len() >= limit || raw.len() < scan || scan == MAX_RESEARCH_SEARCH_CANDIDATES
            {
                return Ok(visible.into_iter().take(limit).collect());
            }
            scan = (scan * 2).min(MAX_RESEARCH_SEARCH_CANDIDATES);
        }
    }

    fn map_hits(&self, raw: &[Hit]) -> EngineResult<Vec<StorageSearchHit>> {
        let mut manifests = HashMap::<FileId, Option<FileManifest>>::new();
        let mut mapped = Vec::with_capacity(raw.len());
        for hit in raw {
            let Ok(file_id) = u32::try_from(hit.file_id).map(FileId::new) else {
                continue;
            };
            if let std::collections::hash_map::Entry::Vacant(entry) = manifests.entry(file_id) {
                entry.insert(self.native.load_embedding_manifest(file_id)?);
            }
            let Some(manifest) = manifests.get(&file_id).and_then(Option::as_ref) else {
                continue;
            };
            let Some(fragment) = manifest.fragments.get(hit.ordinal as usize) else {
                continue;
            };
            mapped.push(StorageSearchHit {
                document_id: fragment.fragment_id.clone(),
                entity_id: EntityId::from_string(fragment.entity_id.clone()),
                file_id,
                path: StorageSearchPath::Fts,
                score: f64::from(hit.score),
                m4_revision: Some(hit.revision),
            });
        }
        Ok(mapped)
    }

    fn matches_filter(
        &self,
        hit: &StorageSearchHit,
        data: &StoredSearchData,
        filter: Option<&StorageSearchFilter>,
    ) -> bool {
        if !data.fragments.contains_key(&hit.document_id) {
            return false;
        }
        let Some(stored) = data.entities.get(&hit.entity_id) else {
            return false;
        };
        let Some(filter) = filter else {
            return true;
        };
        if filter
            .file_ids
            .as_ref()
            .is_some_and(|values| values.is_empty())
            || filter
                .entity_ids
                .as_ref()
                .is_some_and(|values| values.is_empty())
            || filter
                .symbol_names
                .as_ref()
                .is_some_and(|values| values.is_empty())
            || filter
                .symbol_types
                .as_ref()
                .is_some_and(|values| values.is_empty())
        {
            return false;
        }
        if filter
            .file_ids
            .as_ref()
            .is_some_and(|values| !values.contains(&hit.file_id))
            || filter
                .entity_ids
                .as_ref()
                .is_some_and(|values| !values.contains(&hit.entity_id))
        {
            return false;
        }
        if let Some(predicate) = filter.path.as_ref()
            && !path_matches(predicate, stored.file.relative_path.as_path())
        {
            return false;
        }
        if let Some(names) = filter.symbol_names.as_ref() {
            let actual = match stored.entity.metadata.as_ref() {
                Some(EntityMetadata::Code(code)) => code.symbol_name.as_ref(),
                _ => None,
            };
            if !actual.is_some_and(|actual| names.iter().any(|name| name == actual)) {
                return false;
            }
        }
        if let Some(types) = filter.symbol_types.as_ref() {
            let actual = match stored.entity.metadata.as_ref() {
                Some(EntityMetadata::Code(code)) => code.symbol_type,
                _ => None,
            };
            if !actual.is_some_and(|actual| types.contains(&actual)) {
                return false;
            }
        }
        true
    }
}

impl IndexStorage for M4ResearchStorage {
    fn is_read_only(&self) -> bool {
        self.native.is_read_only()
    }

    fn list_files(&self) -> EngineResult<Vec<FileRecord>> {
        self.native.list_files()
    }

    fn resolve_file_ids(&self, paths: &[PathBuf]) -> EngineResult<Vec<FileId>> {
        self.native.resolve_file_ids(paths)
    }

    fn replace_file(
        &self,
        file: &FileRecord,
        entities: &[Entity],
        entries: &[IndexedFragment],
    ) -> EngineResult<()> {
        self.stage_replacement(file, entities, entries)
    }

    fn replace_file_from_staged(&self, staged: DiskStagedBatches) -> EngineResult<()> {
        self.stage_disk_replacement(staged)
    }

    fn load_embedding_manifest(&self, file_id: FileId) -> EngineResult<Option<FileManifest>> {
        self.native.load_embedding_manifest(file_id)
    }

    fn fetch_fragment_vectors(
        &self,
        model: &str,
        fragment_ids: &[String],
    ) -> EngineResult<HashMap<String, Vec<f32>>> {
        self.native.fetch_fragment_vectors(model, fragment_ids)
    }

    fn update_file_snapshot(&self, file: &FileRecord) -> EngineResult<()> {
        self.native.update_file_snapshot(file)
    }

    fn mark_file_failed(&self, file: &FileRecord, error: &str) -> EngineResult<()> {
        self.native.mark_file_failed(file, error)
    }

    fn delete_file(&self, file_id: FileId) -> EngineResult<()> {
        self.delete_files(&[file_id])
    }

    fn delete_files(&self, file_ids: &[FileId]) -> EngineResult<()> {
        if !file_ids.is_empty() {
            self.pass_gate.begin();
            self.pending_deletes
                .lock()
                .expect("pending deletes")
                .extend_from_slice(file_ids);
        }
        Ok(())
    }

    fn finalize_writes(&self) -> EngineResult<()> {
        let result = (|| {
            let mut sidecar = self.sidecar.lock().expect("M4 adapter");
            let updates =
                std::mem::take(&mut *self.pending_updates.lock().expect("pending updates"));
            let deletes =
                std::mem::take(&mut *self.pending_deletes.lock().expect("pending deletes"));
            let mut publish_after_commit = Vec::with_capacity(updates.len());
            for pending in updates {
                self.native.replace_file_from_staged(pending.staged)?;
                publish_after_commit.push((pending.file_id, pending.revision));
            }
            if !deletes.is_empty() {
                self.native.delete_files(&deletes)?;
            }
            self.native.finalize_writes()?;
            if self.fail_after_native_commit.swap(false, Ordering::SeqCst) {
                return Err(EngineError::storage_failure(
                    "injected crash window after native commit and before M4 publication",
                ));
            }
            for (file_id, revision) in publish_after_commit {
                sidecar
                    .publish(file_id, revision)
                    .map_err(EngineError::storage_failure)?;
            }
            for file_id in deletes {
                let id = u64::from(file_id.get());
                let revision = sidecar
                    .file_state(id)
                    .map_err(EngineError::storage_failure)?
                    .revision
                    .checked_add(1)
                    .ok_or_else(|| {
                        EngineError::storage_failure("M4 deletion revision exhausted")
                    })?;
                sidecar
                    .delete(id, revision)
                    .map_err(EngineError::storage_failure)?;
            }
            Ok(())
        })();
        self.pass_gate.finish();
        result
    }
}

impl SearchStorage for M4ResearchStorage {
    fn list_file_paths(&self) -> EngineResult<Vec<(FileId, PathBuf)>> {
        self.pass_gate.wait();
        self.native.list_file_paths()
    }

    fn list_file_attributes(&self) -> EngineResult<Vec<StoredFileAttributes>> {
        self.pass_gate.wait();
        self.native.list_file_attributes()
    }

    fn has_non_unicode_file_names(&self) -> EngineResult<bool> {
        self.pass_gate.wait();
        self.native.has_non_unicode_file_names()
    }

    fn load_search_hits(&self, hits: &[StorageSearchHit]) -> EngineResult<StoredSearchData> {
        self.pass_gate.wait();
        let mut sidecar = self.sidecar.lock().expect("M4 adapter");
        let mut valid = Vec::with_capacity(hits.len());
        for hit in hits {
            let Some(revision) = hit.m4_revision else {
                valid.push(hit.clone());
                continue;
            };
            let state = sidecar
                .file_state(u64::from(hit.file_id.get()))
                .map_err(EngineError::storage_failure)?;
            if state.found != 0 && state.visibility == 2 && state.revision == revision {
                valid.push(hit.clone());
            }
        }
        self.native.load_search_hits(&valid)
    }

    fn search_fts(
        &self,
        query: &str,
        limit: usize,
        filter: Option<&StorageSearchFilter>,
    ) -> EngineResult<Vec<StorageSearchHit>> {
        self.search_m4(query, limit, filter)
    }

    fn search_vector(
        &self,
        model: &str,
        vector: &[f32],
        limit: usize,
        filter: Option<&StorageSearchFilter>,
    ) -> EngineResult<Vec<StorageSearchHit>> {
        self.pass_gate.wait();
        self.native.search_vector(model, vector, limit, filter)
    }
}

impl Drop for M4ResearchStorage {
    fn drop(&mut self) {
        self.pass_gate.finish();
    }
}

fn make_staged(
    file: FileRecord,
    entities: Vec<Entity>,
    entries: &[IndexedFragment],
) -> EngineResult<DiskStagedBatches> {
    let mut staged = DiskStagedBatches::create(file, entities)?;
    for batch in entries.chunks(STAGING_BATCH_FRAGMENTS) {
        staged.append(batch.to_vec())?;
    }
    Ok(staged)
}

fn bounded_docs(entries: &[IndexedFragment]) -> EngineResult<Vec<String>> {
    if entries.len() > MAX_RESEARCH_FILE_FRAGMENTS {
        return Err(EngineError::invalid_argument(format!(
            "research sidecar fixture is limited to {MAX_RESEARCH_FILE_FRAGMENTS} fragments per file"
        )));
    }
    let mut total = 0usize;
    let mut docs = Vec::with_capacity(entries.len());
    for entry in entries {
        total = total.saturating_add(entry.fts_text.len());
        if entry.fts_text.len() > (2 << 20) || total > MAX_RESEARCH_FILE_TEXT_BYTES {
            return Err(EngineError::invalid_argument(
                "research sidecar fixture exceeds its 16 MiB text budget",
            ));
        }
        docs.push(entry.fts_text.clone());
    }
    Ok(docs)
}

fn path_matches(filter: &StoragePathFilter, path: &Path) -> bool {
    match filter {
        StoragePathFilter::All => true,
        StoragePathFilter::None => false,
        StoragePathFilter::Directory(directory) => path
            .parent()
            .is_some_and(|parent| parent.starts_with(directory.as_path())),
        StoragePathFilter::FileNameExact(name) => {
            path.file_name().and_then(|value| value.to_str()) == Some(name)
        }
        StoragePathFilter::FileNamePrefix(prefix) => path
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|name| name.starts_with(prefix)),
        StoragePathFilter::FileNameSuffix(suffix) => path
            .file_name()
            .and_then(|value| value.to_str())
            .is_some_and(|name| name.ends_with(suffix)),
        StoragePathFilter::And(filters) => filters.iter().all(|filter| path_matches(filter, path)),
        StoragePathFilter::Or(filters) => filters.iter().any(|filter| path_matches(filter, path)),
        StoragePathFilter::Not(filter) => !path_matches(filter, path),
    }
}

struct FixedEmbeddingModel {
    info: EmbeddingModelInfo,
}

impl FixedEmbeddingModel {
    fn new() -> Self {
        Self {
            info: EmbeddingModelInfo {
                model: ModelInfo {
                    provider: "local".into(),
                    name: "m4-e2e-fixture".into(),
                    endpoint: None,
                },
                dimension: 2,
                metric: Metric::Cosine,
                max_batch_size: 16,
                max_input_tokens: Some(256),
                max_image_bytes: None,
            },
        }
    }
}

#[async_trait]
impl IndexEmbeddingRuntime for FixedEmbeddingModel {
    fn info(&self) -> &EmbeddingModelInfo {
        &self.info
    }

    fn concurrency_defaults(&self) -> EmbeddingConcurrencyDefaults {
        EmbeddingConcurrencyDefaults {
            initial: 1,
            maximum: 1,
        }
    }

    async fn prepare(
        &self,
        _options: EmbeddingPrepareOptions,
        _progress: Option<IndexProgressReporter>,
    ) -> Result<(), ModelError> {
        Ok(())
    }

    async fn embed(
        &self,
        contents: &[Vec<Content>],
        _options: EmbeddingOptions,
        _progress: Option<IndexProgressReporter>,
    ) -> Result<EmbeddingResult, ModelError> {
        Ok(EmbeddingResult {
            vectors: vec![vec![1.0, 0.0]; contents.len()],
            truncated: Vec::new(),
        })
    }
}

async fn run_index_pass(
    workspace: &Workspace,
    storage: &M4ResearchStorage,
    scanner: &NativeScanner,
    model: &FixedEmbeddingModel,
    changes: &[WorkspaceChange],
) -> EngineResult<()> {
    let models: [&dyn IndexEmbeddingRuntime; 1] = [model];
    index_workspace(&IndexingContext {
        workspace_index: workspace,
        storage,
        scanner,
        embedding_models: &models,
        embedding_concurrency: Some(1),
        on_progress: None,
        signal: None,
        changes,
    })
    .await?;
    Ok(())
}

fn plan(query: &str, globs: Vec<crate::domain::GlobRule>) -> SearchPlan {
    SearchPlan {
        routes: vec![ContextRoute {
            mode: ContextRouteMode::Fts,
            query: query.to_owned(),
        }],
        limit: Some(10),
        trace: false,
        prefer_symbol: false,
        filter: QueryFilter {
            globs,
            ..QueryFilter::default()
        },
    }
}

async fn search(
    storage: &M4ResearchStorage,
    root: &Path,
    query: &str,
    globs: Vec<crate::domain::GlobRule>,
) -> EngineResult<SearchPlanResult> {
    search_workspace_index(root, plan(query, globs), storage, &[]).await
}

#[tokio::test]
async fn real_index_and_search_pipelines_publish_filtered_hydratable_m4_results() {
    let temp = tempdir().expect("workspace");
    let root = temp.path().join("workspace");
    std::fs::create_dir_all(root.join("src")).expect("src dir");
    std::fs::create_dir_all(root.join("tests")).expect("tests dir");
    std::fs::write(
        root.join("src/main.rs"),
        "pub fn orchard_function() { /* orchard */ let _alpha = \"stableterm\"; }",
    )
    .expect("code source");
    std::fs::write(root.join("tests/other.txt"), "orchard beta stableterm").expect("source");

    let model = FixedEmbeddingModel::new();
    let workspace = Workspace {
        name: "m4-e2e".into(),
        root: root.clone(),
        scan: ScanRules::default(),
        index: IndexState::Enabled(IndexDescriptor::single(model.info.clone())),
        created_epoch_ms: 1,
        updated_epoch_ms: 1,
    };
    let scanner = NativeScanner::default();
    let index_root = temp.path().join("index");
    std::fs::create_dir_all(&index_root).expect("index parent");
    let storage = M4ResearchStorage::open(&index_root, &model.info).expect("open stores");
    let initial_changes = [WorkspaceChange::Rescan];
    run_index_pass(&workspace, &storage, &scanner, &model, &initial_changes)
        .await
        .expect("real indexing pipeline");

    let all = search(&storage, &root, "orchard", Vec::new())
        .await
        .expect("M4 search pipeline");
    assert_eq!(all.hits.len(), 2);
    let native_all =
        search_workspace_index(&root, plan("orchard", Vec::new()), &storage.native, &[])
            .await
            .expect("native retrieval baseline");
    let m4_paths = all
        .hits
        .iter()
        .map(|hit| hit.file.relative_path.as_path().to_path_buf())
        .collect::<BTreeSet<_>>();
    let native_paths = native_all
        .hits
        .iter()
        .map(|hit| hit.file.relative_path.as_path().to_path_buf())
        .collect::<BTreeSet<_>>();
    assert_eq!(
        m4_paths, native_paths,
        "M4 and native retrieval parity on the same corpus/query"
    );
    assert!(
        all.hits
            .iter()
            .all(|hit| hit.matched_by == MatchedBy::Fts && !hit.evidence.is_empty())
    );
    let src_glob = vec![crate::domain::GlobRule {
        pattern: "src/**".into(),
        case_insensitive: false,
    }];
    let filtered = search(&storage, &root, "orchard", src_glob.clone())
        .await
        .expect("filtered M4 search");
    assert_eq!(filtered.hits.len(), 1);
    assert_eq!(
        filtered.hits[0].file.relative_path.as_path(),
        Path::new("src/main.rs")
    );
    let native_filtered = search_workspace_index(
        &root,
        plan("orchard", src_glob.clone()),
        &storage.native,
        &[],
    )
    .await
    .expect("native metadata/path-filter baseline");
    assert_eq!(filtered.hits.len(), native_filtered.hits.len());
    assert_eq!(
        filtered.hits[0].file.relative_path, native_filtered.hits[0].file.relative_path,
        "M4 and native path-filter retrieval parity"
    );
    let mut symbol_plan = plan("orchard", Vec::new());
    symbol_plan.filter.symbol_types = vec![SymbolType::Function];
    let symbol_hits = search_workspace_index(&root, symbol_plan, &storage, &[])
        .await
        .expect("M4 symbol metadata filter");
    assert_eq!(symbol_hits.hits.len(), 1);
    assert!(matches!(
        symbol_hits.hits[0].entity.metadata.as_ref(),
        Some(EntityMetadata::Code(code)) if code.symbol_type == Some(SymbolType::Function)
    ));
    let mut native_symbol_plan = plan("orchard", Vec::new());
    native_symbol_plan.filter.symbol_types = vec![SymbolType::Function];
    let native_symbol_hits =
        search_workspace_index(&root, native_symbol_plan, &storage.native, &[])
            .await
            .expect("native function-symbol metadata filter baseline");
    let symbol_paths = symbol_hits
        .hits
        .iter()
        .map(|hit| hit.file.relative_path.as_path().to_path_buf())
        .collect::<BTreeSet<_>>();
    let native_symbol_paths = native_symbol_hits
        .hits
        .iter()
        .map(|hit| hit.file.relative_path.as_path().to_path_buf())
        .collect::<BTreeSet<_>>();
    assert_eq!(symbol_paths, native_symbol_paths, "symbol-filter parity");
    let pre_update_hits = storage
        .search_fts("orchard", 10, None)
        .expect("capture pre-update hits");
    assert!(pre_update_hits.iter().all(|hit| hit.m4_revision.is_some()));

    std::fs::write(
        root.join("src/main.rs"),
        "pub fn plum_function() { /* plum */ let _replacement = \"stableterm\"; }",
    )
    .expect("updated code source");
    let update = [WorkspaceChange::Upsert(PathBuf::from("src/main.rs"))];
    storage
        .fail_after_native_commit
        .store(true, Ordering::SeqCst);
    assert!(
        run_index_pass(&workspace, &storage, &scanner, &model, &update)
            .await
            .is_err(),
        "the interrupted pass must not acknowledge the cross-store update"
    );
    let after_update = storage
        .load_search_hits(&pre_update_hits)
        .expect("revalidate pre-update hits");
    assert_eq!(
        after_update.fragments.len(),
        1,
        "only the unchanged file remains hydratable"
    );
    assert!(
        !after_update
            .entities
            .values()
            .any(|stored| stored.file.relative_path.as_path() == Path::new("src/main.rs")),
        "stale M4 revision is rejected at hydration"
    );
    assert_eq!(
        search(&storage, &root, "orchard", Vec::new())
            .await
            .expect("unchanged orchard remains searchable")
            .hits
            .len(),
        1
    );
    assert_eq!(
        search(&storage, &root, "plum", Vec::new())
            .await
            .expect("unpublished M4 revision is hidden")
            .hits
            .len(),
        0
    );

    drop(storage);
    let storage = M4ResearchStorage::open(&index_root, &model.info)
        .expect("reopen and fail-close the incomplete cross-store revision");
    let retry = [WorkspaceChange::Rescan];
    run_index_pass(&workspace, &storage, &scanner, &model, &retry)
        .await
        .expect("retry from source through the real indexing pipeline");
    assert_eq!(
        search(&storage, &root, "plum", Vec::new())
            .await
            .expect("retried update")
            .hits
            .len(),
        1
    );
    assert_eq!(
        search(&storage, &root, "orchard", Vec::new())
            .await
            .expect("only the second file still matches orchard")
            .hits
            .len(),
        1
    );

    std::fs::rename(root.join("tests/other.txt"), root.join("docs-renamed.txt"))
        .expect("rename source");
    let rename = [
        WorkspaceChange::Delete(PathBuf::from("tests/other.txt")),
        WorkspaceChange::Upsert(PathBuf::from("docs-renamed.txt")),
    ];
    run_index_pass(&workspace, &storage, &scanner, &model, &rename)
        .await
        .expect("rename through index pipeline");
    let renamed = search(&storage, &root, "orchard", Vec::new())
        .await
        .expect("post-rename query");
    assert_eq!(renamed.hits.len(), 1);
    assert_eq!(
        renamed.hits[0].file.relative_path.as_path(),
        Path::new("docs-renamed.txt")
    );

    let active = storage
        .sidecar
        .lock()
        .expect("M4 adapter")
        .file_state(u64::from(renamed.hits[0].file.id.get()))
        .expect("published state");
    assert_eq!(active.visibility, 2);
    assert_eq!(active.expected, active.acknowledged);
    storage
        .native
        .finalize_writes()
        .expect("durability checkpoint");
    storage.native.close().expect("close native store");
}

#[tokio::test]
async fn native_watcher_changes_drive_index_and_search_pipelines() {
    let temp = tempdir().expect("workspace");
    let root = temp.path().join("workspace");
    std::fs::create_dir_all(root.join("src")).expect("src dir");
    let source = root.join("src/watched.txt");
    std::fs::write(&source, "amber initialmarker").expect("initial source");

    let factory = NativeWatcherFactory::new().with_config(NativeWatcherConfig {
        debounce: Duration::from_millis(20),
        max_wait: Duration::from_millis(60),
        reconcile_interval: None,
        resume_check_interval: None,
        poll_interval: Some(Duration::from_millis(20)),
        ..NativeWatcherConfig::default()
    });
    let watch = factory
        .watch(
            &WatchRequest {
                root: RootSpec::new(root.clone(), Arc::new(AllowAllPaths)),
            },
            &TaskControl::default(),
        )
        .await
        .expect("native filesystem watcher");

    let model = FixedEmbeddingModel::new();
    let workspace = Workspace {
        name: "m4-watcher-e2e".into(),
        root: root.clone(),
        scan: ScanRules::default(),
        index: IndexState::Enabled(IndexDescriptor::single(model.info.clone())),
        created_epoch_ms: 1,
        updated_epoch_ms: 1,
    };
    let scanner = NativeScanner::default();
    let index_root = temp.path().join("index");
    std::fs::create_dir_all(&index_root).expect("index parent");
    let storage = M4ResearchStorage::open(&index_root, &model.info).expect("open stores");
    run_index_pass(
        &workspace,
        &storage,
        &scanner,
        &model,
        &[WorkspaceChange::Rescan],
    )
    .await
    .expect("initial real indexing pipeline");
    assert_eq!(
        search(&storage, &root, "amber", Vec::new())
            .await
            .expect("initial indexed query")
            .hits
            .len(),
        1
    );
    watch
        .flush_pending()
        .await
        .expect("drain initial watcher events");

    std::fs::write(&source, "blue updatedmarker").expect("mutate watched source");
    let batch = tokio::time::timeout(
        Duration::from_secs(5),
        watch.next_changes(&TaskControl::default()),
    )
    .await
    .expect("watcher observes update before deadline")
    .expect("watcher delivers update batch");
    assert!(
        batch
            .changes
            .contains(&HostWorkspaceChange::Upsert(PathBuf::from(
                "src/watched.txt"
            ))),
        "real watcher reports the changed file, got {:?}",
        batch.changes
    );
    let changes = batch
        .changes
        .into_iter()
        .map(|change| match change {
            HostWorkspaceChange::Upsert(path) => WorkspaceChange::Upsert(path),
            HostWorkspaceChange::Delete(path) => WorkspaceChange::Delete(path),
            HostWorkspaceChange::RescanDirectory(path) => WorkspaceChange::RescanDirectory(path),
            HostWorkspaceChange::DeletePrefix(path) => WorkspaceChange::DeletePrefix(path),
            HostWorkspaceChange::Rescan => WorkspaceChange::Rescan,
        })
        .collect::<Vec<_>>();
    run_index_pass(&workspace, &storage, &scanner, &model, &changes)
        .await
        .expect("watcher-driven indexing pass");
    let updated = search(&storage, &root, "updatedmarker", Vec::new())
        .await
        .expect("query after watcher-driven publication");
    assert_eq!(updated.hits.len(), 1);
    assert_eq!(
        updated.hits[0].file.relative_path.as_path(),
        Path::new("src/watched.txt")
    );
    assert!(!updated.hits[0].evidence.is_empty());
    assert_eq!(
        search(&storage, &root, "amber", Vec::new())
            .await
            .expect("old term is retired after publication")
            .hits
            .len(),
        0
    );

    watch.close().await.expect("close native watcher");
    storage
        .native
        .finalize_writes()
        .expect("durability checkpoint");
    storage.native.close().expect("close native index");
}
