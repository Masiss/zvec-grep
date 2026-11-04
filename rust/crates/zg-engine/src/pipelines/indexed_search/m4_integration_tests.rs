#![allow(dead_code)]

use std::{
    collections::{BTreeSet, HashMap, HashSet},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        Condvar, Mutex, RwLock,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
};
#[cfg(test)]
use std::{sync::Arc, time::Duration};

use async_trait::async_trait;
use m4_rust_bridge_research::{Adapter, Hit};
#[cfg(test)]
use tempfile::tempdir;
use zg_host_native::NativeScanner;
#[cfg(test)]
use zg_host_native::{
    AllowAllPaths, NativeWatcherConfig, NativeWatcherFactory, RootSpec, TaskControl, WatchRequest,
    WorkspaceChange as HostWorkspaceChange, WorkspaceWatcherFactoryPort,
};

#[cfg(test)]
use crate::{api::context::result::MatchedBy, domain::SymbolType};

use crate::{
    EngineError, EngineResult,
    api::{
        context::options::{ContextRoute, ContextRouteMode, QueryFilter},
        index::{IndexResult, options::WorkspaceChange, progress::IndexProgressReporter},
    },
    domain::{
        Content, Entity, EntityId, EntityMetadata, FileId, FileRecord, IndexDescriptor, IndexState,
        ScanRules, Workspace,
        model::{EmbeddingModelInfo, EmbeddingResult, Metric, ModelInfo},
    },
    models::{EmbeddingConcurrencyDefaults, EmbeddingOptions, EmbeddingPrepareOptions, ModelError},
    pipelines::{
        indexed_search::{
            pipeline::{SearchPlan, SearchPlanResult, search_workspace_index},
            storage::{SearchQueryGuard, SearchStorage},
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

const MAX_RESEARCH_FILE_FRAGMENTS: usize = 4096;
const MAX_RESEARCH_FILE_TEXT_BYTES: usize = 16 * 1024 * 1024;
const MAX_RESEARCH_SEARCH_CANDIDATES: usize = 4096;

#[derive(Default)]
struct PassGate {
    state: Mutex<PassGateState>,
    changed: Condvar,
    blocked_wait_us: AtomicU64,
}

#[derive(Default)]
struct PassGateState {
    // `writer_active` briefly closes reader admission while pre-commit readers
    // drain. `commit_active` serializes writers but does not block readers whose
    // view is protected by the complete pending-file mask.
    writer_active: bool,
    commit_active: bool,
    active_readers: usize,
}

struct PassReadGuard<'a> {
    gate: &'a PassGate,
}

struct PassCommitGuard<'a> {
    gate: &'a PassGate,
}

struct PassPublicationGuard<'a> {
    gate: &'a PassGate,
}

impl SearchQueryGuard for PassReadGuard<'_> {}

impl Drop for PassReadGuard<'_> {
    fn drop(&mut self) {
        let mut state = self.gate.state.lock().expect("pass gate");
        state.active_readers -= 1;
        if state.active_readers == 0 {
            self.gate.changed.notify_all();
        }
    }
}

impl Drop for PassCommitGuard<'_> {
    fn drop(&mut self) {
        self.gate.finish();
    }
}

impl Drop for PassPublicationGuard<'_> {
    fn drop(&mut self) {
        let mut state = self.gate.state.lock().expect("pass gate");
        state.writer_active = false;
        self.gate.changed.notify_all();
    }
}

impl PassCommitGuard<'_> {
    fn close_readers(&self) -> PassPublicationGuard<'_> {
        self.gate.close_readers()
    }
}

impl PassGate {
    fn begin(&self) -> PassCommitGuard<'_> {
        let mut state = self.state.lock().expect("pass gate");
        while state.commit_active {
            state = self.changed.wait(state).expect("pass gate writer wait");
        }
        state.commit_active = true;
        state.writer_active = true;
        while state.active_readers != 0 {
            state = self.changed.wait(state).expect("pass gate drain wait");
        }
        // The complete changed-file mask is installed before begin(). Drain
        // readers that could have captured the old view, then admit new masked
        // reads while the serialized writer commits native and sidecar state.
        state.writer_active = false;
        self.changed.notify_all();
        PassCommitGuard { gate: self }
    }

    fn close_readers(&self) -> PassPublicationGuard<'_> {
        let mut state = self.state.lock().expect("pass gate");
        assert!(
            state.commit_active,
            "publication barrier requires an active writer"
        );
        assert!(!state.writer_active, "publication barrier cannot be nested");
        state.writer_active = true;
        while state.active_readers != 0 {
            state = self
                .changed
                .wait(state)
                .expect("pass gate publication drain wait");
        }
        PassPublicationGuard { gate: self }
    }

    fn read(&self) -> PassReadGuard<'_> {
        let started = std::time::Instant::now();
        let mut state = self.state.lock().expect("pass gate");
        let mut blocked = false;
        while state.writer_active {
            blocked = true;
            state = self.changed.wait(state).expect("pass gate read wait");
        }
        state.active_readers += 1;
        drop(state);
        if blocked {
            let elapsed_us = started.elapsed().as_micros().min(u128::from(u64::MAX)) as u64;
            self.blocked_wait_us
                .fetch_add(elapsed_us, Ordering::Relaxed);
        }
        PassReadGuard { gate: self }
    }

    fn take_blocked_wait_us(&self) -> u64 {
        self.blocked_wait_us.swap(0, Ordering::Relaxed)
    }

    fn finish(&self) {
        let mut state = self.state.lock().expect("pass gate");
        state.writer_active = false;
        state.commit_active = false;
        self.changed.notify_all();
    }
}

#[cfg(test)]
#[test]
fn pass_gate_serializes_commits_but_admits_masked_readers() {
    let gate = Arc::new(PassGate::default());
    let first_commit = gate.begin();
    let (attempted_tx, attempted_rx) = std::sync::mpsc::channel();
    let (entered_tx, entered_rx) = std::sync::mpsc::channel();
    let second_gate = Arc::clone(&gate);
    let second_writer = std::thread::spawn(move || {
        attempted_tx.send(()).expect("signal second writer attempt");
        let _second_commit = second_gate.begin();
        entered_tx.send(()).expect("signal second writer entered");
    });
    attempted_rx
        .recv_timeout(Duration::from_secs(1))
        .expect("second writer attempted");
    assert!(
        entered_rx.recv_timeout(Duration::from_millis(50)).is_err(),
        "a second writer cannot overlap an active commit"
    );

    let reader_gate = Arc::clone(&gate);
    let (reader_tx, reader_rx) = std::sync::mpsc::channel();
    let reader = std::thread::spawn(move || {
        let _read = reader_gate.read();
        reader_tx.send(()).expect("signal masked reader entered");
    });
    reader_rx
        .recv_timeout(Duration::from_secs(1))
        .expect("masked reader can proceed while a commit is active");
    drop(first_commit);
    entered_rx
        .recv_timeout(Duration::from_secs(1))
        .expect("second writer enters after the first commit completes");
    second_writer.join().expect("second writer exits");
    reader.join().expect("reader exits");
}

struct PendingUpdate {
    file_id: u64,
    revision: u64,
    staged: DiskStagedBatches,
}

struct M4ResearchStorage {
    native: IndexStore,
    sidecar_root: PathBuf,
    sidecar: Mutex<Adapter>,
    pending_updates: Mutex<Vec<PendingUpdate>>,
    pending_deletes: Mutex<Vec<FileId>>,
    pass_gate: PassGate,
    pending_mask: RwLock<HashSet<FileId>>,
    pending_read_revisions: Mutex<HashMap<FileId, u64>>,
    fail_after_pass_intent: AtomicBool,
    fail_after_native_commit: AtomicBool,
    #[cfg(test)]
    fail_after_mask_before_sidecar_stage: AtomicBool,
    fail_after_first_m4_publish: AtomicBool,
    #[cfg(test)]
    publication_pause: Mutex<Option<Arc<PublicationPause>>>,
    #[cfg(test)]
    commit_pause: Mutex<Option<Arc<PublicationPause>>>,
    #[cfg(test)]
    sidecar_publish_pause: Mutex<Option<Arc<PublicationPause>>>,
    #[cfg(test)]
    hydration_pause: Mutex<Option<Arc<PublicationPause>>>,
    #[cfg(test)]
    cancel_after_next_stage: Mutex<Option<tokio_util::sync::CancellationToken>>,
}

impl M4ResearchStorage {
    fn open(root: &Path, model: &EmbeddingModelInfo) -> EngineResult<Self> {
        let native = IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
            storage_path: root.join("native"),
            embeddings: vec![model.clone()],
        })?;
        let sidecar_root = root.join("m4-sidecar");
        let mut sidecar = Adapter::open(&sidecar_root).map_err(EngineError::storage_failure)?;
        recover_pending_pass(&native, &mut sidecar, &sidecar_root)?;
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
            sidecar_root,
            sidecar: Mutex::new(sidecar),
            pending_updates: Mutex::new(Vec::new()),
            pending_deletes: Mutex::new(Vec::new()),
            pass_gate: PassGate::default(),
            pending_mask: RwLock::new(HashSet::new()),
            pending_read_revisions: Mutex::new(HashMap::new()),
            fail_after_pass_intent: AtomicBool::new(false),
            fail_after_native_commit: AtomicBool::new(false),
            #[cfg(test)]
            fail_after_mask_before_sidecar_stage: AtomicBool::new(false),
            fail_after_first_m4_publish: AtomicBool::new(false),
            #[cfg(test)]
            publication_pause: Mutex::new(None),
            #[cfg(test)]
            commit_pause: Mutex::new(None),
            #[cfg(test)]
            sidecar_publish_pause: Mutex::new(None),
            #[cfg(test)]
            hydration_pause: Mutex::new(None),
            #[cfg(test)]
            cancel_after_next_stage: Mutex::new(None),
        })
    }

    fn stage_replacement(
        &self,
        file: &FileRecord,
        entities: &[Entity],
        entries: &[IndexedFragment],
    ) -> EngineResult<()> {
        self.ensure_no_pending_pass()?;
        // Native staged replacement consumes projections in entity order. Keep
        // the pipeline's canonical order for that boundary; sort only the
        // independent sidecar representation used for deterministic publication.
        let staged = make_staged(file.clone(), entities.to_vec(), entries)?;
        let mut sidecar_entries = entries.to_vec();
        sidecar_entries.sort_unstable_by(|left, right| left.fragment_id.cmp(&right.fragment_id));
        let docs = bounded_docs(&sidecar_entries)?;
        let file_id = u64::from(file.id.get());
        let inserted_mask = self
            .pending_mask
            .write()
            .expect("pending file mask")
            .insert(file.id);

        let result = (|| {
            #[cfg(test)]
            if self
                .fail_after_mask_before_sidecar_stage
                .swap(false, Ordering::SeqCst)
            {
                return Err(EngineError::storage_failure(
                    "injected failure after changed-file mask and before sidecar staging",
                ));
            }
            let mut sidecar = self.sidecar.lock().expect("M4 adapter");
            let state = sidecar
                .file_state(file_id)
                .map_err(EngineError::storage_failure)?;
            let revision = state
                .revision
                .checked_add(1)
                .ok_or_else(|| EngineError::storage_failure("M4 revision exhausted"))?;
            if state.found != 0 && state.visibility == 2 {
                self.pending_read_revisions
                    .lock()
                    .expect("pending read revisions")
                    .entry(file.id)
                    .or_insert(state.revision);
            }
            if let Err(error) = sidecar.stage_file(file_id, revision, &docs) {
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
            #[cfg(test)]
            if let Some(signal) = self
                .cancel_after_next_stage
                .lock()
                .expect("cancel-after-stage hook")
                .take()
            {
                signal.cancel();
            }
            Ok(())
        })();
        if result.is_err() && inserted_mask {
            self.pending_mask
                .write()
                .expect("pending file mask")
                .remove(&file.id);
            self.pending_read_revisions
                .lock()
                .expect("pending read revisions")
                .remove(&file.id);
        }
        result
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
        if limit == 0 {
            return Ok(Vec::new());
        }
        if limit > MAX_RESEARCH_SEARCH_CANDIDATES {
            return Err(EngineError::invalid_argument(
                "research M4 search limit exceeds 4096",
            ));
        }

        let mut scan = limit.max(32).min(MAX_RESEARCH_SEARCH_CANDIDATES);
        let masked = self.pending_file_mask();
        let mut sidecar = self.sidecar.lock().expect("M4 adapter");
        loop {
            let raw = sidecar
                .search(query, scan, scan as u32)
                .map_err(EngineError::storage_failure)?;
            let mapped = self.map_hits(&raw)?;
            let data = self.native.load_search_hits(&mapped)?;
            let visible = mapped
                .into_iter()
                .filter(|hit| !masked.contains(&hit.file_id))
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
        self.ensure_no_pending_pass()?;
        if !file_ids.is_empty() {
            self.pending_mask
                .write()
                .expect("pending file mask")
                .extend(file_ids.iter().copied());
            self.pending_deletes
                .lock()
                .expect("pending deletes")
                .extend_from_slice(file_ids);
        }
        Ok(())
    }

    fn finalize_writes(&self) -> EngineResult<()> {
        let updates = std::mem::take(&mut *self.pending_updates.lock().expect("pending updates"));
        let deletes = std::mem::take(&mut *self.pending_deletes.lock().expect("pending deletes"));
        if updates
            .len()
            .checked_add(deletes.len())
            .is_none_or(|count| count > MAX_PENDING_PASS_IDS)
        {
            return Err(EngineError::invalid_argument(
                "research sidecar pass exceeds 65,536-file recovery limit",
            ));
        }
        let pass_ids = updates
            .iter()
            .map(|pending| pending.file_id)
            .chain(deletes.iter().map(|file_id| u64::from(file_id.get())))
            .collect::<BTreeSet<_>>();
        let pass_manifest = if pass_ids.is_empty() {
            None
        } else {
            Some(encode_pending_pass(&pass_ids)?)
        };
        let result = (|| {
            if let Some(manifest) = pass_manifest.as_deref() {
                persist_pending_pass(&self.sidecar_root, manifest)?;
            }
            #[cfg(test)]
            if let Some(pause) = self
                .publication_pause
                .lock()
                .expect("publication pause")
                .take()
            {
                pause.entered.wait();
                pause.release.wait();
            }
            if self.fail_after_pass_intent.swap(false, Ordering::SeqCst) {
                return Err(EngineError::storage_failure(
                    "injected crash after durable pass intent and before native mutation",
                ));
            }
            if pass_manifest.is_some() {
                let masked = self.pending_file_mask();
                for raw_id in &pass_ids {
                    let id = u32::try_from(*raw_id).map(FileId::new).map_err(|_| {
                        EngineError::storage_failure("M4 pass file id exceeds native range")
                    })?;
                    if !masked.contains(&id) {
                        return Err(EngineError::storage_failure(
                            "M4 commit refused because a changed file is not masked from readers",
                        ));
                    }
                }
            }
            // This drains queries that began before every pass ID was masked.
            // New queries are admitted during commit and filter all changed IDs
            // until the native and sidecar publication is complete.
            let _commit_guard = pass_manifest.as_ref().map(|_| self.pass_gate.begin());
            let mut publish_after_commit = Vec::with_capacity(updates.len());
            for pending in updates {
                self.native.replace_file_from_staged(pending.staged)?;
                publish_after_commit.push((pending.file_id, pending.revision));
            }
            if !deletes.is_empty() {
                self.native.delete_files(&deletes)?;
            }
            self.native.finalize_writes()?;
            #[cfg(test)]
            if let Some(pause) = self.commit_pause.lock().expect("commit pause").take() {
                pause.entered.wait();
                pause.release.wait();
            }
            if self.fail_after_native_commit.swap(false, Ordering::SeqCst) {
                return Err(EngineError::storage_failure(
                    "injected crash window after native commit and before M4 publication",
                ));
            }
            // Sidecar publication is masked: every changed file is hidden from
            // readers, so readers may continue while individual files publish.
            // Only the final mask clear needs a short reader-admission barrier.
            for (published, (file_id, revision)) in publish_after_commit.iter().enumerate() {
                let mut sidecar = self.sidecar.lock().expect("M4 adapter");
                sidecar
                    .publish(*file_id, *revision)
                    .map_err(EngineError::storage_failure)?;
                drop(sidecar);
                #[cfg(test)]
                if published == 0
                    && let Some(pause) = self
                        .sidecar_publish_pause
                        .lock()
                        .expect("sidecar publication pause")
                        .take()
                {
                    pause.entered.wait();
                    pause.release.wait();
                }
                if published == 0
                    && self
                        .fail_after_first_m4_publish
                        .swap(false, Ordering::SeqCst)
                {
                    return Err(EngineError::storage_failure(
                        "injected crash after first file publication in a multi-file pass",
                    ));
                }
            }
            for file_id in deletes {
                let id = u64::from(file_id.get());
                let mut sidecar = self.sidecar.lock().expect("M4 adapter");
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
                drop(sidecar);
            }
            if pass_manifest.is_some() {
                clear_pending_pass(&self.sidecar_root)?;
                let _publication_barrier =
                    _commit_guard.as_ref().map(PassCommitGuard::close_readers);
                let mut masked = self.pending_mask.write().expect("pending file mask");
                for file_id in pass_ids {
                    masked.remove(&FileId::new(u32::try_from(file_id).map_err(|_| {
                        EngineError::storage_failure("pending file id exceeds native range")
                    })?));
                }
                self.pending_read_revisions
                    .lock()
                    .expect("pending read revisions")
                    .clear();
            }
            Ok(())
        })();
        result
    }
}

impl M4ResearchStorage {
    fn pending_file_mask(&self) -> HashSet<FileId> {
        self.pending_mask.read().expect("pending file mask").clone()
    }

    fn ensure_no_pending_pass(&self) -> EngineResult<()> {
        if pending_pass_path(&self.sidecar_root).exists() {
            return Err(EngineError::storage_failure(
                "M4 workspace publication is incomplete; reopen to recover before querying",
            ));
        }
        Ok(())
    }
}

impl SearchStorage for M4ResearchStorage {
    fn begin_query(&self) -> EngineResult<Box<dyn SearchQueryGuard + '_>> {
        Ok(Box::new(self.pass_gate.read()))
    }

    fn list_file_paths(&self) -> EngineResult<Vec<(FileId, PathBuf)>> {
        let masked = self.pending_file_mask();
        Ok(self
            .native
            .list_file_paths()?
            .into_iter()
            .filter(|(file_id, _)| !masked.contains(file_id))
            .collect())
    }

    fn list_file_attributes(&self) -> EngineResult<Vec<StoredFileAttributes>> {
        let masked = self.pending_file_mask();
        Ok(self
            .native
            .list_file_attributes()?
            .into_iter()
            .filter(|file| !masked.contains(&file.id))
            .collect())
    }

    fn has_non_unicode_file_names(&self) -> EngineResult<bool> {
        self.native.has_non_unicode_file_names()
    }

    fn load_search_hits(&self, hits: &[StorageSearchHit]) -> EngineResult<StoredSearchData> {
        #[cfg(test)]
        if let Some(pause) = self.hydration_pause.lock().expect("hydration pause").take() {
            pause.entered.wait();
            pause.release.wait();
        }
        let mut sidecar = self.sidecar.lock().expect("M4 adapter");
        let prior_revisions = self
            .pending_read_revisions
            .lock()
            .expect("pending read revisions")
            .clone();
        let mut valid = Vec::with_capacity(hits.len());
        for hit in hits {
            let Some(revision) = hit.m4_revision else {
                valid.push(hit.clone());
                continue;
            };
            let state = sidecar
                .file_state(u64::from(hit.file_id.get()))
                .map_err(EngineError::storage_failure)?;
            let current_revision =
                state.found != 0 && state.visibility == 2 && state.revision == revision;
            let pre_stage_revision = prior_revisions.get(&hit.file_id) == Some(&revision);
            if current_revision || pre_stage_revision {
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
        let masked = self.pending_file_mask();
        Ok(self
            .native
            .search_vector(model, vector, limit, filter)?
            .into_iter()
            .filter(|hit| !masked.contains(&hit.file_id))
            .collect())
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

const MAX_PENDING_PASS_IDS: usize = 65_536;
const MAX_PENDING_PASS_BYTES: usize = 2 << 20;

fn pending_pass_path(sidecar_root: &Path) -> PathBuf {
    sidecar_root.join("workspace-pass.pending")
}

fn pass_checksum(bytes: &[u8]) -> u64 {
    bytes.iter().fold(0xcbf29ce484222325u64, |hash, byte| {
        (hash ^ u64::from(*byte)).wrapping_mul(0x100000001b3)
    })
}

fn encode_pending_pass(ids: &BTreeSet<u64>) -> EngineResult<Vec<u8>> {
    if ids.len() > MAX_PENDING_PASS_IDS {
        return Err(EngineError::invalid_argument(
            "research sidecar pass exceeds 65,536-file recovery limit",
        ));
    }
    let mut payload = format!("M4PASS1\n{}\n", ids.len());
    for id in ids {
        payload.push_str(&format!("{id}\n"));
    }
    let checksum = pass_checksum(payload.as_bytes());
    payload.push_str(&format!("{checksum:016x}\n"));
    if payload.len() > MAX_PENDING_PASS_BYTES {
        return Err(EngineError::invalid_argument(
            "research sidecar recovery manifest exceeds 2 MiB",
        ));
    }
    Ok(payload.into_bytes())
}

fn decode_pending_pass(bytes: &[u8]) -> EngineResult<BTreeSet<u64>> {
    if bytes.len() > MAX_PENDING_PASS_BYTES {
        return Err(EngineError::storage_failure(
            "M4 workspace recovery manifest exceeds 2 MiB",
        ));
    }
    let text = std::str::from_utf8(bytes)
        .map_err(|_| EngineError::storage_failure("malformed M4 workspace recovery manifest"))?;
    let mut lines = text.lines();
    if lines.next() != Some("M4PASS1") {
        return Err(EngineError::storage_failure(
            "unsupported M4 workspace recovery manifest",
        ));
    }
    let count = lines
        .next()
        .and_then(|line| line.parse::<usize>().ok())
        .filter(|count| *count <= MAX_PENDING_PASS_IDS)
        .ok_or_else(|| EngineError::storage_failure("invalid M4 recovery file count"))?;
    let mut ids = BTreeSet::new();
    let mut payload_len = b"M4PASS1\n".len() + lines_count_bytes(count);
    for _ in 0..count {
        let line = lines
            .next()
            .ok_or_else(|| EngineError::storage_failure("truncated M4 recovery file list"))?;
        let id = line
            .parse::<u64>()
            .map_err(|_| EngineError::storage_failure("invalid M4 recovery file id"))?;
        if !ids.insert(id) {
            return Err(EngineError::storage_failure(
                "duplicate M4 recovery file id",
            ));
        }
        payload_len += line.len() + 1;
    }
    let checksum_line = lines
        .next()
        .ok_or_else(|| EngineError::storage_failure("missing M4 recovery checksum"))?;
    if lines.next().is_some() {
        return Err(EngineError::storage_failure(
            "trailing M4 recovery manifest data",
        ));
    }
    let checksum = u64::from_str_radix(checksum_line, 16)
        .map_err(|_| EngineError::storage_failure("invalid M4 recovery checksum"))?;
    if payload_len > bytes.len()
        || pass_checksum(&bytes[..payload_len]) != checksum
        || !bytes.ends_with(b"\n")
    {
        return Err(EngineError::storage_failure(
            "M4 recovery checksum mismatch",
        ));
    }
    Ok(ids)
}

fn lines_count_bytes(count: usize) -> usize {
    count.to_string().len() + 1
}

fn sync_directory(path: &Path) -> EngineResult<()> {
    std::fs::File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(|error| EngineError::storage_failure(error.to_string()))
}

fn persist_pending_pass(sidecar_root: &Path, bytes: &[u8]) -> EngineResult<()> {
    std::fs::create_dir_all(sidecar_root)
        .map_err(|error| EngineError::storage_failure(error.to_string()))?;
    let target = pending_pass_path(sidecar_root);
    let temporary = sidecar_root.join(format!("workspace-pass.pending.tmp.{}", std::process::id()));
    let mut file = std::fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .open(&temporary)
        .map_err(|error| EngineError::storage_failure(error.to_string()))?;
    if let Err(error) = file.write_all(bytes).and_then(|()| file.sync_all()) {
        let _ = std::fs::remove_file(&temporary);
        return Err(EngineError::storage_failure(error.to_string()));
    }
    drop(file);
    if let Err(error) = std::fs::rename(&temporary, &target) {
        let _ = std::fs::remove_file(&temporary);
        return Err(EngineError::storage_failure(error.to_string()));
    }
    sync_directory(sidecar_root)
}

fn clear_pending_pass(sidecar_root: &Path) -> EngineResult<()> {
    let path = pending_pass_path(sidecar_root);
    match std::fs::remove_file(path) {
        Ok(()) => sync_directory(sidecar_root),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(EngineError::storage_failure(error.to_string())),
    }
}

fn recover_pending_pass(
    native: &IndexStore,
    sidecar: &mut Adapter,
    sidecar_root: &Path,
) -> EngineResult<()> {
    let path = pending_pass_path(sidecar_root);
    let file = match std::fs::File::open(&path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(EngineError::storage_failure(error.to_string())),
    };
    if file
        .metadata()
        .map_err(|error| EngineError::storage_failure(error.to_string()))?
        .len()
        > MAX_PENDING_PASS_BYTES as u64
    {
        return Err(EngineError::storage_failure(
            "M4 workspace recovery manifest exceeds 2 MiB",
        ));
    }
    let mut bytes = Vec::new();
    file.take((MAX_PENDING_PASS_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|error| EngineError::storage_failure(error.to_string()))?;
    let ids = decode_pending_pass(&bytes)?;
    let native_files = native
        .list_files()?
        .into_iter()
        .filter(|file| ids.contains(&u64::from(file.id.get())))
        .map(|file| file.id)
        .collect::<Vec<_>>();
    if !native_files.is_empty() {
        native.delete_files(&native_files)?;
        native.finalize_writes()?;
    }
    for id in ids {
        let state = sidecar
            .file_state(id)
            .map_err(EngineError::storage_failure)?;
        if state.found == 0 {
            continue;
        }
        let revision = state
            .revision
            .checked_add(1)
            .ok_or_else(|| EngineError::storage_failure("M4 recovery revision exhausted"))?;
        sidecar
            .delete(id, revision)
            .map_err(EngineError::storage_failure)?;
    }
    clear_pending_pass(sidecar_root)
}

#[cfg(test)]
#[test]
fn workspace_pass_manifest_is_bounded_and_checksummed() {
    let ids = BTreeSet::from([4, 17, u64::MAX]);
    let encoded = encode_pending_pass(&ids).expect("bounded pass manifest");
    assert_eq!(decode_pending_pass(&encoded).expect("valid manifest"), ids);

    let mut corrupted = encoded.clone();
    corrupted[8] ^= 1;
    assert!(decode_pending_pass(&corrupted).is_err());

    let too_many = (0..=MAX_PENDING_PASS_IDS as u64).collect::<BTreeSet<_>>();
    assert!(encode_pending_pass(&too_many).is_err());
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
) -> EngineResult<IndexResult> {
    run_index_pass_with_signal(workspace, storage, scanner, model, changes, None).await
}

async fn run_index_pass_with_signal(
    workspace: &Workspace,
    storage: &M4ResearchStorage,
    scanner: &NativeScanner,
    model: &FixedEmbeddingModel,
    changes: &[WorkspaceChange],
    signal: Option<tokio_util::sync::CancellationToken>,
) -> EngineResult<IndexResult> {
    let models: [&dyn IndexEmbeddingRuntime; 1] = [model];
    index_workspace(&IndexingContext {
        workspace_index: workspace,
        storage,
        scanner,
        embedding_models: &models,
        embedding_concurrency: Some(1),
        on_progress: None,
        signal,
        changes,
    })
    .await
}

/// Native-only deterministic workspace used by one-shot lifecycle measurements
/// in `zg-daemon` tests. It shares the native IndexStore between the writer and
/// concurrent readers and never loads a production embedding model.
pub struct NativeLifecycleTestWorkspace {
    workspace: Workspace,
    index_root: PathBuf,
    scanner: NativeScanner,
    model: FixedEmbeddingModel,
    storage: tokio::sync::RwLock<std::sync::Arc<IndexStore>>,
    writer: tokio::sync::Mutex<()>,
}

impl NativeLifecycleTestWorkspace {
    pub fn open(root: &Path, index_root: &Path) -> EngineResult<Self> {
        let root = root
            .canonicalize()
            .map_err(|error| EngineError::storage_failure(error.to_string()))?;
        std::fs::create_dir_all(index_root)
            .map_err(|error| EngineError::storage_failure(error.to_string()))?;
        let model = FixedEmbeddingModel::new();
        let storage =
            std::sync::Arc::new(IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
                storage_path: index_root.to_path_buf(),
                embeddings: vec![model.info.clone()],
            })?);
        Ok(Self {
            workspace: Workspace {
                name: "p1-native-lifecycle".into(),
                root,
                scan: ScanRules::default(),
                index: IndexState::Enabled(IndexDescriptor::single(model.info.clone())),
                created_epoch_ms: 1,
                updated_epoch_ms: 1,
            },
            index_root: index_root.to_path_buf(),
            scanner: NativeScanner::default(),
            model,
            storage: tokio::sync::RwLock::new(storage),
            writer: tokio::sync::Mutex::new(()),
        })
    }

    pub async fn index(&self, changes: &[WorkspaceChange]) -> EngineResult<IndexResult> {
        let _writer = self.writer.lock().await;
        let storage = std::sync::Arc::clone(&*self.storage.read().await);
        let models: [&dyn IndexEmbeddingRuntime; 1] = [&self.model];
        index_workspace(&IndexingContext {
            workspace_index: &self.workspace,
            storage: storage.as_ref(),
            scanner: &self.scanner,
            embedding_models: &models,
            embedding_concurrency: Some(1),
            on_progress: None,
            signal: None,
            changes,
        })
        .await
    }

    pub async fn search_paths(&self, query: &str) -> EngineResult<Vec<PathBuf>> {
        let storage = self.storage.read().await;
        let result = search_workspace_index(
            &self.workspace.root,
            plan(query, vec![crate::domain::GlobRule::from("**/*.rs")]),
            storage.as_ref(),
            &[],
        )
        .await?;
        Ok(result
            .hits
            .into_iter()
            .map(|hit| hit.file.relative_path.as_path().to_path_buf())
            .collect())
    }

    pub async fn file_paths(&self) -> EngineResult<Vec<PathBuf>> {
        let storage = std::sync::Arc::clone(&*self.storage.read().await);
        let mut paths = storage
            .list_files()?
            .into_iter()
            .map(|file| file.relative_path.as_path().to_path_buf())
            .collect::<Vec<_>>();
        paths.sort();
        Ok(paths)
    }

    pub async fn reopen(&self) -> EngineResult<()> {
        let _writer = self.writer.lock().await;
        let mut storage = self.storage.write().await;
        // Release the native storage lease before opening the same path again.
        // The write guard above ensures no concurrent query still uses this store.
        storage.close()?;
        let replacement =
            std::sync::Arc::new(IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
                storage_path: self.index_root.clone(),
                embeddings: vec![self.model.info.clone()],
            })?);
        *storage = replacement;
        Ok(())
    }
}

/// Test-only workspace driver used by `zg-daemon` to exercise the real runtime
/// watcher/job lifecycle against the research M4 sidecar without production wiring.
pub struct M4ResearchWorkspace {
    workspace: Workspace,
    index_root: PathBuf,
    scanner: NativeScanner,
    model: FixedEmbeddingModel,
    storage: tokio::sync::Mutex<Option<M4ResearchStorage>>,
    fail_after_first_publish: AtomicBool,
    #[cfg(test)]
    publication_pause: Mutex<Option<Arc<PublicationPause>>>,
}

impl M4ResearchWorkspace {
    pub fn open(root: &Path, index_root: &Path) -> EngineResult<Self> {
        let root = root
            .canonicalize()
            .map_err(|error| EngineError::storage_failure(error.to_string()))?;
        std::fs::create_dir_all(index_root)
            .map_err(|error| EngineError::storage_failure(error.to_string()))?;
        let model = FixedEmbeddingModel::new();
        let workspace = Workspace {
            name: "m4-daemon-research-test".into(),
            root,
            scan: ScanRules::default(),
            index: IndexState::Enabled(IndexDescriptor::single(model.info.clone())),
            created_epoch_ms: 1,
            updated_epoch_ms: 1,
        };
        let storage = M4ResearchStorage::open(index_root, &model.info)?;
        Ok(Self {
            workspace,
            index_root: index_root.to_path_buf(),
            scanner: NativeScanner::default(),
            model,
            storage: tokio::sync::Mutex::new(Some(storage)),
            fail_after_first_publish: AtomicBool::new(false),
            #[cfg(test)]
            publication_pause: Mutex::new(None),
        })
    }

    pub async fn index(&self, changes: &[WorkspaceChange]) -> EngineResult<IndexResult> {
        let mut storage = self.storage.lock().await;
        if storage.is_none() {
            *storage = Some(M4ResearchStorage::open(&self.index_root, &self.model.info)?);
        }
        let current = storage.as_mut().expect("M4 store opened");
        current.fail_after_first_m4_publish.store(
            self.fail_after_first_publish.swap(false, Ordering::SeqCst),
            Ordering::SeqCst,
        );
        #[cfg(test)]
        {
            *current.publication_pause.lock().expect("publication pause") = self
                .publication_pause
                .lock()
                .expect("workspace publication pause")
                .take();
        }
        let result = run_index_pass(
            &self.workspace,
            current,
            &self.scanner,
            &self.model,
            changes,
        )
        .await;
        if result.is_err() {
            storage.take();
            match M4ResearchStorage::open(&self.index_root, &self.model.info) {
                Ok(recovered) => *storage = Some(recovered),
                Err(recovery) => {
                    return Err(EngineError::storage_failure(format!(
                        "M4 index pass failed ({}) and recovery failed ({recovery})",
                        result.expect_err("error checked").message()
                    )));
                }
            }
        }
        result
    }

    pub async fn search_count(&self, query: &str) -> EngineResult<usize> {
        let mut storage = self.storage.lock().await;
        if storage.is_none() {
            *storage = Some(M4ResearchStorage::open(&self.index_root, &self.model.info)?);
        }
        let current = storage.as_ref().expect("M4 store opened");
        Ok(search(current, &self.workspace.root, query, Vec::new())
            .await?
            .hits
            .len())
    }

    pub fn fail_after_first_publish_once(&self) {
        self.fail_after_first_publish.store(true, Ordering::SeqCst);
    }

    #[cfg(test)]
    fn pause_next_publication(&self) -> (Arc<std::sync::Barrier>, Arc<std::sync::Barrier>) {
        let pause = Arc::new(PublicationPause {
            entered: Arc::new(std::sync::Barrier::new(2)),
            release: Arc::new(std::sync::Barrier::new(2)),
        });
        let entered = Arc::clone(&pause.entered);
        let release = Arc::clone(&pause.release);
        *self.publication_pause.lock().expect("publication pause") = Some(pause);
        (entered, release)
    }
}

#[cfg(test)]
struct PublicationPause {
    entered: Arc<std::sync::Barrier>,
    release: Arc<std::sync::Barrier>,
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

#[cfg(test)]
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
    let m4_rank_order = all
        .hits
        .iter()
        .map(|hit| hit.file.relative_path.as_path().to_path_buf())
        .collect::<Vec<_>>();
    let native_rank_order = native_all
        .hits
        .iter()
        .map(|hit| hit.file.relative_path.as_path().to_path_buf())
        .collect::<Vec<_>>();
    assert_eq!(
        m4_rank_order, native_rank_order,
        "M4 and native FTS return the same ranked paths on the parity fixture"
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
    let filtered_old_hits = storage
        .load_search_hits(&pre_update_hits)
        .expect("unchanged-file hits remain hydratable while changed files are masked");
    assert!(filtered_old_hits.fragments.len() < pre_update_hits.len());
    let unaffected = search(&storage, &root, "orchard", Vec::new())
        .await
        .expect("unaffected files remain searchable during pending recovery");
    assert_eq!(unaffected.hits.len(), 1);
    assert_eq!(
        unaffected.hits[0].file.relative_path.as_path(),
        Path::new("tests/other.txt"),
        "the changed file is hidden without taking the whole workspace offline"
    );

    drop(storage);
    let storage = M4ResearchStorage::open(&index_root, &model.info)
        .expect("reopen and fail-close the incomplete cross-store revision");
    assert_eq!(
        search(&storage, &root, "orchard", Vec::new())
            .await
            .expect("unchanged orchard remains searchable after recovery")
            .hits
            .len(),
        1
    );
    assert_eq!(
        search(&storage, &root, "plum", Vec::new())
            .await
            .expect("unpublished M4 revision is hidden after recovery")
            .hits
            .len(),
        0
    );
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

    std::fs::remove_file(root.join("docs-renamed.txt")).expect("delete renamed source");
    run_index_pass(
        &workspace,
        &storage,
        &scanner,
        &model,
        &[WorkspaceChange::Delete(PathBuf::from("docs-renamed.txt"))],
    )
    .await
    .expect("delete renamed file");
    assert!(
        search(&storage, &root, "orchard", Vec::new())
            .await
            .expect("query after delete")
            .hits
            .is_empty(),
        "deleted file terms must no longer be visible"
    );

    std::fs::write(root.join("docs-renamed.txt"), "recreatedmarker")
        .expect("recreate source at the same path");
    run_index_pass(
        &workspace,
        &storage,
        &scanner,
        &model,
        &[WorkspaceChange::Upsert(PathBuf::from("docs-renamed.txt"))],
    )
    .await
    .expect("index recreated file");
    let recreated = search(&storage, &root, "recreatedmarker", Vec::new())
        .await
        .expect("query recreated file");
    assert_eq!(recreated.hits.len(), 1);
    assert_eq!(
        recreated.hits[0].file.relative_path.as_path(),
        Path::new("docs-renamed.txt")
    );
    assert!(
        search(&storage, &root, "orchard", Vec::new())
            .await
            .expect("old deleted content remains absent")
            .hits
            .is_empty()
    );

    storage
        .native
        .finalize_writes()
        .expect("durability checkpoint");
    storage.native.close().expect("close native store");
}

#[cfg(test)]
#[tokio::test]
async fn interrupted_multi_file_publication_fails_closed_until_reopen_and_retry() {
    let temp = tempdir().expect("workspace");
    let root = temp.path().join("workspace");
    std::fs::create_dir_all(root.join("src")).expect("src dir");
    std::fs::write(root.join("src/a.rs"), "oldalpha").expect("first source");
    std::fs::write(root.join("src/b.rs"), "oldbeta").expect("second source");
    std::fs::write(root.join("stable.txt"), "stablemarker").expect("stable source");

    let model = FixedEmbeddingModel::new();
    let workspace = Workspace {
        name: "m4-multifile-recovery".into(),
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
    .expect("initial index");

    std::fs::write(root.join("src/a.rs"), "newalpha").expect("update first source");
    std::fs::write(root.join("src/b.rs"), "newbeta").expect("update second source");
    storage.fail_after_pass_intent.store(true, Ordering::SeqCst);
    let changes = [
        WorkspaceChange::Upsert(PathBuf::from("src/a.rs")),
        WorkspaceChange::Upsert(PathBuf::from("src/b.rs")),
    ];
    assert!(
        run_index_pass(&workspace, &storage, &scanner, &model, &changes)
            .await
            .is_err(),
        "a durable pass intent without native mutation is not an ACK"
    );
    assert_eq!(
        search(&storage, &root, "newalpha", Vec::new())
            .await
            .expect("pending pass masks changed files without failing unrelated reads")
            .hits
            .len(),
        0,
        "the durable pass mask prevents readers from observing an uncommitted revision"
    );

    drop(storage);
    let storage = M4ResearchStorage::open(&index_root, &model.info)
        .expect("reopen recovers all files named by the incomplete pass");
    assert_eq!(
        search(&storage, &root, "newalpha", Vec::new())
            .await
            .expect("query after pre-native recovery")
            .hits
            .len(),
        0
    );
    assert_eq!(
        search(&storage, &root, "newbeta", Vec::new())
            .await
            .expect("second file remains hidden")
            .hits
            .len(),
        0
    );
    assert_eq!(
        search(&storage, &root, "stablemarker", Vec::new())
            .await
            .expect("unmodified file stays visible")
            .hits
            .len(),
        1
    );

    storage
        .fail_after_first_m4_publish
        .store(true, Ordering::SeqCst);
    assert!(
        run_index_pass(
            &workspace,
            &storage,
            &scanner,
            &model,
            &[WorkspaceChange::Rescan],
        )
        .await
        .is_err(),
        "a repeated publication failure must remain unacknowledged"
    );
    assert!(
        search(&storage, &root, "newalpha", Vec::new())
            .await
            .expect("changed revisions remain masked after publication failure")
            .hits
            .is_empty(),
        "repeated failure keeps changed files hidden without fencing unrelated queries"
    );
    drop(storage);
    let storage = M4ResearchStorage::open(&index_root, &model.info)
        .expect("second reopen recovers the repeated failure");
    assert_eq!(
        search(&storage, &root, "stablemarker", Vec::new())
            .await
            .expect("stable file remains visible after repeated recovery")
            .hits
            .len(),
        1
    );
    for term in ["oldalpha", "oldbeta", "newalpha", "newbeta"] {
        assert!(
            search(&storage, &root, term, Vec::new())
                .await
                .expect("changed file remains hidden after recovery")
                .hits
                .is_empty(),
            "failed cross-store publication exposed {term} before retry"
        );
    }

    run_index_pass(
        &workspace,
        &storage,
        &scanner,
        &model,
        &[WorkspaceChange::Rescan],
    )
    .await
    .expect("rescan retries after repeated unacknowledged failures");
    for term in ["newalpha", "newbeta"] {
        assert_eq!(
            search(&storage, &root, term, Vec::new())
                .await
                .expect("both files republished after retry")
                .hits
                .len(),
            1
        );
    }
}

#[cfg(test)]
#[tokio::test]
async fn cancellation_after_staging_fails_closed_across_reopen_and_rescan() {
    let temp = tempdir().expect("workspace");
    let root = temp.path().join("workspace");
    std::fs::create_dir_all(root.join("src")).expect("source directory");
    std::fs::write(root.join("src/a.rs"), "beforetoken").expect("source");
    std::fs::write(root.join("stable.txt"), "stablemarker").expect("stable source");

    let model = FixedEmbeddingModel::new();
    let workspace = Workspace {
        name: "m4-cancellation-recovery".into(),
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
    .expect("initial index");

    std::fs::write(root.join("src/a.rs"), "aftertoken").expect("updated source");
    let signal = tokio_util::sync::CancellationToken::new();
    *storage
        .cancel_after_next_stage
        .lock()
        .expect("cancel-after-stage hook") = Some(signal.clone());
    assert!(
        run_index_pass_with_signal(
            &workspace,
            &storage,
            &scanner,
            &model,
            &[WorkspaceChange::Upsert(PathBuf::from("src/a.rs"))],
            Some(signal.clone()),
        )
        .await
        .is_err(),
        "cancellation after durable staging must stop before pass finalization"
    );
    assert!(signal.is_cancelled());
    assert!(
        search(&storage, &root, "beforetoken", Vec::new())
            .await
            .expect("changed file remains masked after cancellation")
            .hits
            .is_empty()
    );
    assert_eq!(
        search(&storage, &root, "stablemarker", Vec::new())
            .await
            .expect("unaffected files stay searchable after cancellation")
            .hits
            .len(),
        1
    );

    drop(storage);
    let storage = M4ResearchStorage::open(&index_root, &model.info)
        .expect("shutdown/reopen discards the uncommitted staged revision");
    for term in ["beforetoken", "aftertoken"] {
        assert!(
            search(&storage, &root, term, Vec::new())
                .await
                .expect("cancelled file stays fail-closed after reopen")
                .hits
                .is_empty(),
            "cancelled update exposed {term} after restart"
        );
    }
    assert_eq!(
        search(&storage, &root, "stablemarker", Vec::new())
            .await
            .expect("stable file survives cancellation recovery")
            .hits
            .len(),
        1
    );

    run_index_pass(
        &workspace,
        &storage,
        &scanner,
        &model,
        &[WorkspaceChange::Rescan],
    )
    .await
    .expect("rescan recovers the cancelled source update");
    assert_eq!(
        search(&storage, &root, "aftertoken", Vec::new())
            .await
            .expect("rescanned update")
            .hits
            .len(),
        1
    );
    assert!(
        search(&storage, &root, "beforetoken", Vec::new())
            .await
            .expect("old term is retired by rescan")
            .hits
            .is_empty()
    );
}

#[cfg(test)]
#[tokio::test(flavor = "multi_thread", worker_threads = 3)]
async fn publication_masks_changed_files_without_blocking_unaffected_queries() {
    let temp = tempdir().expect("workspace");
    let root = temp.path().join("workspace");
    std::fs::create_dir_all(root.join("src")).expect("source directory");
    std::fs::write(root.join("src/a.rs"), "beforetoken").expect("source");
    std::fs::write(root.join("src/b.rs"), "deletetoken").expect("source to delete");
    std::fs::write(root.join("src/c.rs"), "independenttoken").expect("unaffected source");
    let index_root = temp.path().join("index");
    std::fs::create_dir_all(&index_root).expect("index parent");
    let model = Arc::new(FixedEmbeddingModel::new());
    let workspace = Workspace {
        name: "publication-mask-test".into(),
        root: root.clone(),
        scan: ScanRules::default(),
        index: IndexState::Enabled(IndexDescriptor::single(model.info.clone())),
        created_epoch_ms: 1,
        updated_epoch_ms: 1,
    };
    let scanner = NativeScanner::default();
    let storage = Arc::new(M4ResearchStorage::open(&index_root, &model.info).expect("open store"));
    run_index_pass(
        &workspace,
        &storage,
        &scanner,
        &model,
        &[WorkspaceChange::Rescan],
    )
    .await
    .expect("initial index");
    assert_eq!(
        search(&storage, &root, "beforetoken", Vec::new())
            .await
            .unwrap()
            .hits
            .len(),
        1
    );
    assert_eq!(
        search(&storage, &root, "independenttoken", Vec::new())
            .await
            .unwrap()
            .hits
            .len(),
        1
    );

    std::fs::write(root.join("src/a.rs"), "aftertoken").expect("updated source");
    storage
        .fail_after_mask_before_sidecar_stage
        .store(true, Ordering::SeqCst);
    assert!(
        run_index_pass(
            &workspace,
            &storage,
            &scanner,
            &model,
            &[WorkspaceChange::Upsert(PathBuf::from("src/a.rs"))],
        )
        .await
        .is_err(),
        "pre-publication staging failure must be observable to the caller"
    );
    assert_eq!(
        search(&storage, &root, "beforetoken", Vec::new())
            .await
            .expect("failed preparation leaves prior revision searchable")
            .hits
            .len(),
        1
    );
    assert_eq!(
        search(&storage, &root, "aftertoken", Vec::new())
            .await
            .expect("failed preparation must not expose the new revision")
            .hits
            .len(),
        0
    );

    std::fs::remove_file(root.join("src/b.rs")).expect("remove source to delete");
    let pause = Arc::new(PublicationPause {
        entered: Arc::new(std::sync::Barrier::new(2)),
        release: Arc::new(std::sync::Barrier::new(2)),
    });
    let commit_pause = Arc::new(PublicationPause {
        entered: Arc::new(std::sync::Barrier::new(2)),
        release: Arc::new(std::sync::Barrier::new(2)),
    });
    let entered = Arc::clone(&pause.entered);
    let release = Arc::clone(&pause.release);
    let commit_entered = Arc::clone(&commit_pause.entered);
    let commit_release = Arc::clone(&commit_pause.release);
    let sidecar_publish_pause = Arc::new(PublicationPause {
        entered: Arc::new(std::sync::Barrier::new(2)),
        release: Arc::new(std::sync::Barrier::new(2)),
    });
    let sidecar_publish_entered = Arc::clone(&sidecar_publish_pause.entered);
    let sidecar_publish_release = Arc::clone(&sidecar_publish_pause.release);
    *storage.publication_pause.lock().expect("publication pause") = Some(pause);
    *storage.commit_pause.lock().expect("commit pause") = Some(commit_pause);
    *storage
        .sidecar_publish_pause
        .lock()
        .expect("sidecar publication pause") = Some(sidecar_publish_pause);
    let writer_storage = Arc::clone(&storage);
    let writer_workspace = workspace.clone();
    let writer_model = Arc::clone(&model);
    let writer = tokio::spawn(async move {
        run_index_pass(
            &writer_workspace,
            &writer_storage,
            &scanner,
            &writer_model,
            &[
                WorkspaceChange::Upsert(PathBuf::from("src/a.rs")),
                WorkspaceChange::Delete(PathBuf::from("src/b.rs")),
            ],
        )
        .await
    });
    tokio::task::spawn_blocking(move || entered.wait())
        .await
        .expect("publication pause reached");
    assert!(
        index_root
            .join("m4-sidecar/workspace-pass.pending")
            .exists(),
        "durable pass intent must precede native commit and M4 publication"
    );

    let reader_storage = Arc::clone(&storage);
    let reader_root = root.clone();
    let reader = tokio::spawn(async move {
        search(
            &reader_storage,
            &reader_root,
            "independenttoken",
            Vec::new(),
        )
        .await
        .map(|result| result.hits.len())
    });
    assert_eq!(
        tokio::time::timeout(Duration::from_millis(500), reader)
            .await
            .expect("unaffected search must not wait for the whole publication pass")
            .expect("query task")
            .expect("query succeeds"),
        1
    );
    assert_eq!(
        search(&storage, &root, "beforetoken", Vec::new())
            .await
            .unwrap()
            .hits
            .len(),
        0,
        "old revision is hidden while the new revision is being committed"
    );
    assert_eq!(
        search(&storage, &root, "aftertoken", Vec::new())
            .await
            .unwrap()
            .hits
            .len(),
        0,
        "new revision is not visible until both stores publish"
    );
    assert_eq!(
        search(&storage, &root, "deletetoken", Vec::new())
            .await
            .unwrap()
            .hits
            .len(),
        0,
        "deleted revision is hidden during the publication pass"
    );
    tokio::task::spawn_blocking(move || release.wait())
        .await
        .expect("release preparation pause");
    tokio::task::spawn_blocking(move || commit_entered.wait())
        .await
        .expect("commit fence reached");
    {
        let state = storage.pass_gate.state.lock().expect("pass gate");
        assert!(
            state.commit_active,
            "writer owns the commit serialization gate"
        );
        assert!(
            !state.writer_active,
            "masked readers are admitted after pre-commit readers drain"
        );
    }
    let fenced_storage = Arc::clone(&storage);
    let fenced_root = root.clone();
    let fenced_query = tokio::spawn(async move {
        search(
            &fenced_storage,
            &fenced_root,
            "independenttoken",
            Vec::new(),
        )
        .await
        .map(|result| result.hits.len())
    });
    assert_eq!(
        tokio::time::timeout(Duration::from_millis(500), fenced_query)
            .await
            .expect("masked unaffected query must complete during native commit")
            .expect("query task")
            .expect("query succeeds"),
        1
    );
    for term in ["beforetoken", "aftertoken", "deletetoken"] {
        assert!(
            search(&storage, &root, term, Vec::new())
                .await
                .expect("masked changed-file query during commit")
                .hits
                .is_empty(),
            "changed or deleted file leaked during commit for term {term}"
        );
    }
    tokio::task::spawn_blocking(move || commit_release.wait())
        .await
        .expect("release native commit pause");
    tokio::task::spawn_blocking(move || sidecar_publish_entered.wait())
        .await
        .expect("partial sidecar publication reached");
    {
        let state = storage.pass_gate.state.lock().expect("pass gate");
        assert!(
            state.commit_active,
            "writer remains serialized during publication"
        );
        assert!(
            !state.writer_active,
            "readers remain admitted during masked sidecar publication"
        );
    }
    let boundary_storage = Arc::clone(&storage);
    let boundary_root = root.clone();
    let boundary_query = tokio::spawn(async move {
        search(
            &boundary_storage,
            &boundary_root,
            "independenttoken",
            Vec::new(),
        )
        .await
        .map(|result| result.hits.len())
    });
    assert_eq!(
        tokio::time::timeout(Duration::from_millis(500), boundary_query)
            .await
            .expect("unaffected query is admitted during masked sidecar publication")
            .expect("query task")
            .expect("query succeeds"),
        1
    );
    for term in ["beforetoken", "aftertoken", "deletetoken"] {
        assert!(
            search(&storage, &root, term, Vec::new())
                .await
                .expect("changed-file query during sidecar publication")
                .hits
                .is_empty(),
            "changed or deleted file leaked during sidecar publication for term {term}"
        );
    }
    tokio::task::spawn_blocking(move || sidecar_publish_release.wait())
        .await
        .expect("release sidecar publication pause");
    writer
        .await
        .expect("index task")
        .expect("publication succeeds");
    assert_eq!(
        search(&storage, &root, "aftertoken", Vec::new())
            .await
            .unwrap()
            .hits
            .len(),
        1
    );
    assert_eq!(
        search(&storage, &root, "beforetoken", Vec::new())
            .await
            .unwrap()
            .hits
            .len(),
        0
    );
    assert_eq!(
        search(&storage, &root, "deletetoken", Vec::new())
            .await
            .unwrap()
            .hits
            .len(),
        0,
        "deleted revision stays absent after publication"
    );

    drop(storage);
    let reopened = M4ResearchStorage::open(&index_root, &model.info)
        .expect("reopen after durable index acknowledgement");
    assert_eq!(
        search(&reopened, &root, "aftertoken", Vec::new())
            .await
            .unwrap()
            .hits
            .len(),
        1
    );
    assert_eq!(
        search(&reopened, &root, "beforetoken", Vec::new())
            .await
            .unwrap()
            .hits
            .len(),
        0
    );
    assert_eq!(
        search(&reopened, &root, "deletetoken", Vec::new())
            .await
            .unwrap()
            .hits
            .len(),
        0
    );
}

#[cfg(test)]
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn query_keeps_pre_stage_revision_through_hydration() {
    let temp = tempdir().expect("workspace");
    let root = temp.path().join("workspace");
    std::fs::create_dir_all(root.join("src")).expect("source directory");
    let source = root.join("src/a.rs");
    std::fs::write(&source, "beforecoherenttoken").expect("initial source");
    let index_root = temp.path().join("index");
    std::fs::create_dir_all(&index_root).expect("index parent");
    let model = Arc::new(FixedEmbeddingModel::new());
    let workspace = Workspace {
        name: "query-read-view-test".into(),
        root: root.clone(),
        scan: ScanRules::default(),
        index: IndexState::Enabled(IndexDescriptor::single(model.info.clone())),
        created_epoch_ms: 1,
        updated_epoch_ms: 1,
    };
    let storage = Arc::new(M4ResearchStorage::open(&index_root, &model.info).expect("open store"));
    run_index_pass(
        &workspace,
        &storage,
        &NativeScanner::default(),
        &model,
        &[WorkspaceChange::Rescan],
    )
    .await
    .expect("initial index");

    let pause = Arc::new(PublicationPause {
        entered: Arc::new(std::sync::Barrier::new(2)),
        release: Arc::new(std::sync::Barrier::new(2)),
    });
    let entered = Arc::clone(&pause.entered);
    let release = Arc::clone(&pause.release);
    *storage.hydration_pause.lock().expect("hydration pause") = Some(pause);
    let reader_storage = Arc::clone(&storage);
    let reader_root = root.clone();
    let reader = tokio::spawn(async move {
        search(
            &reader_storage,
            &reader_root,
            "beforecoherenttoken",
            Vec::new(),
        )
        .await
    });
    tokio::task::spawn_blocking(move || entered.wait())
        .await
        .expect("query reached hydration after recall");

    std::fs::write(&source, "aftercoherenttoken").expect("updated source");
    let writer_storage = Arc::clone(&storage);
    let writer_workspace = workspace.clone();
    let writer_model = Arc::clone(&model);
    let writer = tokio::spawn(async move {
        run_index_pass(
            &writer_workspace,
            &writer_storage,
            &NativeScanner::default(),
            &writer_model,
            &[WorkspaceChange::Upsert(PathBuf::from("src/a.rs"))],
        )
        .await
    });
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let state = storage.pass_gate.state.lock().expect("pass gate");
            let writer_waiting_for_reader = state.writer_active && state.active_readers == 1;
            drop(state);
            if writer_waiting_for_reader {
                break;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("writer reached the query/read-view fence");
    let pending_mask_set_before_hydration = !storage.pending_file_mask().is_empty();
    tokio::task::spawn_blocking(move || release.wait())
        .await
        .expect("release query hydration");

    let reader_result = reader
        .await
        .expect("query task")
        .expect("query before commit succeeds");
    writer
        .await
        .expect("writer task")
        .expect("update publishes");
    assert!(
        pending_mask_set_before_hydration,
        "the staged file must be hidden from new reads before publication"
    );
    assert_eq!(
        reader_result.hits.len(),
        1,
        "pre-stage query sees old revision"
    );
    let Content::Text(hydrated_content) = &reader_result.hits[0].entity.content else {
        panic!("expected hydrated text content");
    };
    assert!(
        hydrated_content.contains("beforecoherenttoken"),
        "hydration stays on the pre-stage revision while publication waits for the reader"
    );
    assert!(
        !hydrated_content.contains("aftercoherenttoken"),
        "staged content must not leak into an in-flight read view"
    );
    assert_eq!(
        search(&storage, &root, "aftercoherenttoken", Vec::new())
            .await
            .expect("query after commit")
            .hits
            .len(),
        1,
        "new revision becomes visible after commit"
    );
    assert_eq!(
        search(&storage, &root, "beforecoherenttoken", Vec::new())
            .await
            .expect("old revision query after commit")
            .hits
            .len(),
        0
    );
}

#[cfg(test)]
#[tokio::test]
async fn filtered_m4_search_refills_candidates_before_hydration() {
    let temp = tempdir().expect("workspace");
    let root = temp.path().join("workspace");
    std::fs::create_dir_all(root.join("src")).expect("source directory");
    for index in 0..39 {
        std::fs::write(
            root.join("src").join(format!("{index:02}.txt")),
            format!("needle needle needle common{index}"),
        )
        .expect("write high-ranked file");
    }
    std::fs::write(root.join("zz-target.txt"), "needle raretarget").expect("write filtered target");

    let model = FixedEmbeddingModel::new();
    let workspace = Workspace {
        name: "m4-refill-test".into(),
        root: root.clone(),
        scan: ScanRules::default(),
        index: IndexState::Enabled(IndexDescriptor::single(model.info.clone())),
        created_epoch_ms: 1,
        updated_epoch_ms: 1,
    };
    let index_root = temp.path().join("index");
    std::fs::create_dir_all(&index_root).expect("index parent");
    let storage = M4ResearchStorage::open(&index_root, &model.info).expect("open stores");
    run_index_pass(
        &workspace,
        &storage,
        &NativeScanner::default(),
        &model,
        &[WorkspaceChange::Rescan],
    )
    .await
    .expect("index refill corpus");

    let first_page = storage
        .search_fts("needle", 32, None)
        .expect("unfiltered first page");
    let target_id = storage
        .native
        .list_file_paths()
        .expect("list indexed paths")
        .into_iter()
        .find_map(|(file_id, path)| (path == Path::new("zz-target.txt")).then_some(file_id))
        .expect("target file id");
    assert!(
        first_page.iter().all(|hit| hit.file_id != target_id),
        "filtered target must lie beyond the initial candidate window"
    );

    let filter = StorageSearchFilter {
        path: Some(StoragePathFilter::FileNameExact("zz-target.txt".into())),
        ..StorageSearchFilter::default()
    };
    let hits = storage
        .search_fts("needle", 1, Some(&filter))
        .expect("candidate-refilled filtered search");
    assert_eq!(
        hits.len(),
        1,
        "search must refill beyond filtered candidates"
    );
    assert_eq!(hits[0].file_id, target_id);
    let hydrated = storage
        .load_search_hits(&hits)
        .expect("hydrate refilled result");
    assert!(
        hydrated
            .entities
            .values()
            .any(|entity| { entity.file.relative_path.as_path() == Path::new("zz-target.txt") })
    );
}

#[cfg(test)]
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

#[cfg(test)]
mod gate_b_lifecycle;
