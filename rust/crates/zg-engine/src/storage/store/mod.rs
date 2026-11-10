//! Index lifecycle and operations that span multiple tables.

use std::{
    collections::{HashMap, HashSet},
    fs::{self, File, OpenOptions},
    io::Read,
    path::{Path, PathBuf},
    sync::{Arc, Mutex, MutexGuard, OnceLock, Weak},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

#[cfg(test)]
use std::cell::Cell;

#[cfg(test)]
thread_local! {
    static STATE_LOCK_WAIT_TELEMETRY: Cell<(u64, u64, u64)> = const { Cell::new((0, 0, 0)) };
}

#[cfg(test)]
thread_local! {
    static DIRTY_OPTIMIZE_TEST_HOOK: Cell<(u64, bool)> = const { Cell::new((0, false)) };
}
#[cfg(test)]
static DIRTY_OPTIMIZE_TEST_ACTIVE: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
#[cfg(test)]
static FINALIZE_TEST_PAUSE_AFTER_SNAPSHOT: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
#[cfg(test)]
static FINALIZE_TEST_SNAPSHOT_REACHED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);
#[cfg(test)]
static FINALIZE_TEST_SNAPSHOT_RELEASE: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// Arm the scalar optimize stage from tests. The hook is thread-local so tests
/// running in parallel cannot inject delay/failure into another store.
#[cfg(test)]
pub(crate) fn set_dirty_optimize_test_hook(delay_ms: u64, fail: bool) {
    DIRTY_OPTIMIZE_TEST_HOOK.with(|hook| hook.set((delay_ms, fail)));
}

#[cfg(test)]
pub(crate) fn dirty_optimize_active_for_test() -> bool {
    DIRTY_OPTIMIZE_TEST_ACTIVE.load(std::sync::atomic::Ordering::Acquire)
}

#[cfg(test)]
pub(crate) fn set_dirty_optimize_test_failure(fail: bool) {
    DIRTY_OPTIMIZE_TEST_HOOK.with(|hook| {
        let (delay, _) = hook.get();
        hook.set((delay, fail));
    });
}

// These pause controls are retained for the blocked handle-based maintenance
// regression; they have no production effect.
#[cfg(test)]
pub(crate) fn pause_finalize_after_snapshot_for_test() {
    use std::sync::atomic::Ordering;
    FINALIZE_TEST_SNAPSHOT_REACHED.store(false, Ordering::SeqCst);
    FINALIZE_TEST_SNAPSHOT_RELEASE.store(false, Ordering::SeqCst);
    FINALIZE_TEST_PAUSE_AFTER_SNAPSHOT.store(true, Ordering::SeqCst);
}

#[cfg(test)]
pub(crate) fn finalize_snapshot_reached_for_test() -> bool {
    FINALIZE_TEST_SNAPSHOT_REACHED.load(std::sync::atomic::Ordering::Acquire)
}

#[cfg(test)]
pub(crate) fn release_finalize_snapshot_for_test() {
    FINALIZE_TEST_SNAPSHOT_RELEASE.store(true, std::sync::atomic::Ordering::SeqCst);
}

#[cfg(test)]
fn record_state_lock_wait_for_test(wait_ns: u64) {
    STATE_LOCK_WAIT_TELEMETRY.with(|telemetry| {
        let (count, total_ns, max_ns) = telemetry.get();
        telemetry.set((
            count.saturating_add(1),
            total_ns.saturating_add(wait_ns),
            max_ns.max(wait_ns),
        ));
    });
}

#[cfg(test)]
fn take_state_lock_wait_for_test() -> (u64, u64, u64) {
    STATE_LOCK_WAIT_TELEMETRY.with(|telemetry| telemetry.replace((0, 0, 0)))
}

use serde::{Deserialize, Serialize};
#[cfg(test)]
use zvec_rust::Collection;

use crate::{
    EngineError, EngineResult,
    domain::{Entity, FileId, FileIndexStatus, FileRecord},
    utils::{
        atomic_write as write_record, directory_exists, ensure_directory, file_exists,
        open_regular_file, reject_symlink, sync_directory,
    },
};

use super::{
    maintenance::{DebtRecord, MaintenancePolicy},
    directories::Directories,
    entities::Entities,
    files::Files,
    fragments::{self, Fragments},
    graph::{self, OpenMode, SqliteGraphStorage},
    manifest::{FileManifest, ManifestStore},
    types::{
        IndexedFragment, StorageSearchFilter, StorageSearchHit, StoredEntity, StoredFileAttributes,
        StoredSearchData, WorkspaceIndexStorageOptions,
    },
    zvec::{corrupt, initialize},
};
use crate::domain::FileGraphResult;
use crate::domain::model::EmbeddingModelInfo;

mod resolution;

type StoreRegistry = Mutex<HashMap<PathBuf, Weak<SharedStore>>>;
static STORES: OnceLock<StoreRegistry> = OnceLock::new();

struct SharedStore {
    state: Mutex<StoreState>,
    schema: Vec<EmbeddingModelInfo>,
    manifest: ManifestStore,
    read_only: bool,
    // The native handles must close before the operating-system lock is released.
    _lock: File,
}

// Stage wall durations from one finalization. Fragments and scalar collections
// run concurrently, so these durations are not additive.
pub(crate) type OptimizeStageTimings = Vec<(String, Duration)>;

const DIRTY_DIRECTORIES: u8 = 1 << 0;
const DIRTY_ENTITIES: u8 = 1 << 1;
const DIRTY_FILES: u8 = 1 << 2;
const DIRTY_FRAGMENTS: u8 = 1 << 3;
const DIRTY_ALL: u8 = DIRTY_DIRECTORIES | DIRTY_ENTITIES | DIRTY_FILES | DIRTY_FRAGMENTS;

struct StoreState {
    files: Files,
    directories: Directories,
    entities: Entities,
    // Fragments are cloned out of the state lock for native maintenance. All
    // fragment methods are &self; the store lock still serializes table writes.
    fragments: Arc<Fragments>,
    graph: Option<SqliteGraphStorage>,
    optimize_marker: PathBuf,
    needs_optimize: bool,
    dirty: u8,
    /// Writes since the last successful scalar optimization (deferral bound).
    pending_writes: usize,
    /// Set when a persisted debt record forces a catch-up on this session.
    forced_maintenance: bool,
    policy: MaintenancePolicy,
    write_generation: u64,
    optimizing: bool,
    closed: bool,
}

pub(crate) struct IndexStore {
    shared: Mutex<Option<Arc<SharedStore>>>,
    read_only: bool,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SchemaRecord {
    path_encoding: PathEncoding,
    embeddings: Vec<EmbeddingModelInfo>,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum PathEncoding {
    Components,
}

impl SchemaRecord {
    fn new(embeddings: &[EmbeddingModelInfo]) -> Self {
        Self {
            path_encoding: PathEncoding::Components,
            embeddings: embeddings.to_vec(),
        }
    }

    fn embeddings(self) -> EngineResult<Vec<EmbeddingModelInfo>> {
        validate_models(&self.embeddings).map_err(|error| {
            EngineError::storage_failure(format!(
                "invalid stored embedding model information: {error}"
            ))
        })?;
        Ok(self.embeddings)
    }
}

fn validate_models(embeddings: &[EmbeddingModelInfo]) -> EngineResult<()> {
    if embeddings.is_empty() {
        return Err(EngineError::invalid_argument(
            "at least one embedding model is required",
        ));
    }
    let mut models = std::collections::HashSet::new();
    let mut collections = HashSet::new();
    for embedding in embeddings {
        if !(1..=20_000).contains(&embedding.dimension()) {
            return Err(EngineError::invalid_argument(
                "embedding dimension must be in 1..=20,000",
            ));
        }
        if !models.insert(embedding.model().reference()) {
            return Err(EngineError::invalid_argument(
                "embedding model references must be unique",
            ));
        }
        let collection = fragments::fragment_collection_name(embedding);
        if !collections.insert(collection.clone()) {
            return Err(EngineError::invalid_argument(format!(
                "embedding models share index collection {collection}; use different models"
            )));
        }
    }
    Ok(())
}

impl IndexStore {
    pub(crate) fn open(options: WorkspaceIndexStorageOptions) -> EngineResult<Self> {
        // Graph extraction and resolution are not enabled in this release.
        // Re-enabling graph indexing requires rebuilding any unmaintained graph.
        Self::open_with_graph(options, false)
    }

    fn open_with_graph(
        options: WorkspaceIndexStorageOptions,
        graph_enabled: bool,
    ) -> EngineResult<Self> {
        Self::open_with_graph_policy(options, graph_enabled, MaintenancePolicy::from_env())
    }

    fn open_with_graph_policy(
        options: WorkspaceIndexStorageOptions,
        graph_enabled: bool,
        policy: MaintenancePolicy,
    ) -> EngineResult<Self> {
        if let WorkspaceIndexStorageOptions::ReadWrite { embeddings, .. } = &options {
            validate_models(embeddings)?;
        }
        initialize()?;
        let home = options.storage_path();
        if !home.is_absolute() {
            return Err(EngineError::invalid_argument(
                "storage path must be absolute",
            ));
        }
        let read_only = options.is_read_only();
        let outer = home
            .file_name()
            .and_then(|_| home.parent())
            .map(Path::to_path_buf);
        if read_only {
            directory_exists(home)?;
        } else {
            ensure_directory(home)?;
        }
        let path = fs::canonicalize(home)
            .map_err(|error| io_error("locate index directory", home, &error))?;
        let mut registry = registry()?;
        if let Some(shared) = registry.get(&path).and_then(Weak::upgrade) {
            if read_only && shared.read_only {
                return Ok(Self {
                    shared: Mutex::new(Some(shared)),
                    read_only,
                });
            }
            return Err(EngineError::resource_busy(
                "workspace storage is already open",
            ));
        }
        let (lock, schema) = prepare_storage(&path, options)?;
        let manifest = ManifestStore::open(&path, read_only)?;
        let optimize_marker = path.join("optimize.pending");
        let needs_optimize = file_exists(&optimize_marker)?;
        // A persisted debt record is authoritative when it parses; an empty or
        // unknown marker keeps the historical fail-safe "everything is dirty".
        let (dirty, pending_writes) = match read_debt(&optimize_marker, needs_optimize)? {
            Some(record) => (record.dirty, record.pending),
            None if needs_optimize => (DIRTY_ALL, 0),
            None => (0, 0),
        };
        let files = Files::open(&path, read_only)?;
        let directories = Directories::open(&path, read_only)?;
        let entities = Entities::open(&path, read_only)?;
        let fragments = Arc::new(Fragments::open(&path, &schema, read_only)?);
        // Ordinary readers do not depend on the graph's availability.
        let graph = if read_only || !graph_enabled {
            None
        } else {
            let graph_path = path.join("graph").join("graph.sqlite");
            // Current extractors produce no graph, so pre-graph indexes can
            // initialize an empty database without rebuilding canonical data.
            let graph =
                SqliteGraphStorage::open(&graph_path, OpenMode::ReadWrite).map_err(graph_error)?;
            sync_directory(&path.join("graph"))?;
            Some(graph)
        };
        if !read_only {
            directories.load()?;
        }
        if !read_only {
            // Repeat the same range after failures that leave directories in place.
            sync_directory(&path)?;
            if let Some(outer) = outer {
                sync_directory(&outer)?;
            }
        }
        let shared = Arc::new(SharedStore {
            state: Mutex::new(StoreState {
                files,
                directories,
                entities,
                fragments,
                graph,
                optimize_marker,
                needs_optimize,
                dirty,
                pending_writes,
                forced_maintenance: needs_optimize,
                policy,
                write_generation: 0,
                optimizing: false,
                closed: false,
            }),
            schema,
            manifest,
            read_only,
            _lock: lock,
        });
        registry.insert(path, Arc::downgrade(&shared));
        Ok(Self {
            shared: Mutex::new(Some(shared)),
            read_only,
        })
    }

    pub(crate) fn delete(storage_path: &Path) -> EngineResult<()> {
        if !directory_exists(storage_path)? {
            return Ok(());
        }
        let home = fs::canonicalize(storage_path)
            .map_err(|error| io_error("locate storage", storage_path, &error))?;
        let path = home.clone();
        let mut registry = registry()?;
        if registry.get(&path).and_then(Weak::upgrade).is_some() {
            return Err(EngineError::resource_busy(
                "cannot delete open workspace storage",
            ));
        }
        // The caller's workspace write lock prevents new opens while this inner lock is closed.
        drop(acquire_storage_lock(&home, false)?);
        fs::remove_dir_all(&path)
            .map_err(|error| io_error("delete workspace storage", &path, &error))?;
        if let Some(parent) = home.parent() {
            sync_directory(parent)?;
        }
        registry.remove(&path);
        Ok(())
    }

    fn shared(&self) -> EngineResult<Arc<SharedStore>> {
        self.shared
            .lock()
            .map_err(|_| EngineError::internal("storage lease lock was poisoned"))?
            .as_ref()
            .cloned()
            .ok_or_else(|| EngineError::resource_closed("workspace storage is closed"))
    }

    fn read<T>(&self, operation: impl FnOnce(&StoreState) -> EngineResult<T>) -> EngineResult<T> {
        let shared = self.shared()?;
        #[cfg(test)]
        let lock_started = Instant::now();
        let state = lock_state(&shared)?;
        #[cfg(test)]
        record_state_lock_wait_for_test(
            u64::try_from(lock_started.elapsed().as_nanos()).unwrap_or(u64::MAX),
        );
        assert_usable(&state)?;
        operation(&state)
    }

    fn write<T>(
        &self,
        operation: impl FnOnce(&mut StoreState) -> EngineResult<T>,
    ) -> EngineResult<T> {
        if self.read_only {
            return Err(EngineError::permission_denied(
                "cannot write read-only workspace storage",
            ));
        }
        let shared = self.shared()?;
        #[cfg(test)]
        let lock_started = Instant::now();
        let mut state = lock_state(&shared)?;
        #[cfg(test)]
        record_state_lock_wait_for_test(
            u64::try_from(lock_started.elapsed().as_nanos()).unwrap_or(u64::MAX),
        );
        assert_usable(&state)?;
        operation(&mut state)
    }

    #[cfg(test)]
    pub(crate) fn take_state_lock_wait_telemetry_for_test() -> (u64, u64, u64) {
        take_state_lock_wait_for_test()
    }

    /// Clone the scalar collection handles for handle-lifetime and native
    /// concurrency regression tests.
    #[cfg(test)]
    pub(crate) fn scalar_handles_for_test(
        &self,
    ) -> EngineResult<(Arc<Collection>, Arc<Collection>, Arc<Collection>)> {
        let shared = self.shared()?;
        let state = lock_state(&shared)?;
        Ok((
            state.directories.handle(),
            state.entities.handle(),
            state.files.handle(),
        ))
    }

    pub(crate) fn is_read_only(&self) -> bool {
        self.read_only
    }

    pub(crate) fn list_files(&self) -> EngineResult<Vec<FileRecord>> {
        self.read(|state| state.files.list())
    }

    pub(crate) fn list_file_paths(&self) -> EngineResult<Vec<(FileId, PathBuf)>> {
        self.read(|state| state.files.list_paths())
    }

    pub(crate) fn list_file_attributes(&self) -> EngineResult<Vec<StoredFileAttributes>> {
        self.read(|state| state.files.list_attributes())
    }

    pub(crate) fn resolve_file_ids(&self, paths: &[PathBuf]) -> EngineResult<Vec<FileId>> {
        self.write(|state| state.files.resolve_ids(paths))
    }

    pub(crate) fn has_non_unicode_file_names(&self) -> EngineResult<bool> {
        self.read(|state| state.files.has_non_unicode_file_names())
    }

    pub(crate) fn load_search_hits(
        &self,
        hits: &[StorageSearchHit],
    ) -> EngineResult<StoredSearchData> {
        self.read(|state| load_search_hits(state, hits))
    }

    pub(crate) fn search_fts(
        &self,
        query: &str,
        limit: usize,
        filter: Option<&StorageSearchFilter>,
    ) -> EngineResult<Vec<StorageSearchHit>> {
        self.read(|state| {
            if limit == 0 || fragments::empty_filter(filter) {
                return Ok(Vec::new());
            }
            let filter = fragments::build_filter(filter, &|path| state.directories.get(path))?;
            state.fragments.search_fts(query, limit, filter.as_deref())
        })
    }

    pub(crate) fn search_vector(
        &self,
        model: &str,
        vector: &[f32],
        limit: usize,
        filter: Option<&StorageSearchFilter>,
    ) -> EngineResult<Vec<StorageSearchHit>> {
        let shared = self.shared()?;
        validate_vector(vector, model_schema(&shared.schema, model)?)?;
        self.read(|state| {
            if limit == 0 || fragments::empty_filter(filter) {
                return Ok(Vec::new());
            }
            let filter = fragments::build_filter(filter, &|path| state.directories.get(path))?;
            state
                .fragments
                .search_vector(model, vector, limit, filter.as_deref())
        })
    }

    #[cfg(test)]
    pub(crate) fn replace_file(
        &self,
        file: &FileRecord,
        entities: &[Entity],
        entries: &[IndexedFragment],
    ) -> EngineResult<()> {
        self.replace_file_with_graph(file, entities, entries, &FileGraphResult::default())
    }

    pub(crate) fn load_embedding_manifest(
        &self,
        file_id: FileId,
    ) -> EngineResult<Option<FileManifest>> {
        Ok(self.shared()?.manifest.load(file_id))
    }

    pub(crate) fn publish_embedding_manifest(
        &self,
        manifest: &mut FileManifest,
    ) -> EngineResult<bool> {
        self.shared()?.manifest.publish(manifest)
    }

    pub(crate) fn delete_embedding_manifest(&self, file_id: FileId) -> EngineResult<()> {
        self.shared()?.manifest.delete(file_id)
    }

    pub(crate) fn load_reusable_vectors(
        &self,
        model: &str,
        fragment_ids: &[String],
    ) -> EngineResult<HashMap<String, Vec<f32>>> {
        self.read(|state| state.fragments.load_vectors(model, fragment_ids))
    }

    /// Replace canonical content and relationships under the same store lock.
    pub(crate) fn replace_file_with_graph(
        &self,
        file: &FileRecord,
        entities: &[Entity],
        entries: &[IndexedFragment],
        graph: &FileGraphResult,
    ) -> EngineResult<()> {
        let shared = self.shared()?;
        validate_batch(file, entities, entries, &shared.schema)?;
        SqliteGraphStorage::validate_snapshot(file.id().get(), graph).map_err(graph_error)?;
        let ids: HashSet<_> = entities.iter().map(|entity| entity.id().as_str()).collect();
        if graph
            .nodes
            .iter()
            .any(|node| !ids.contains(node.id.as_str()))
        {
            return Err(EngineError::invalid_argument(
                "graph entity is absent from canonical file entities",
            ));
        }
        let mut file = file.clone();
        file.set_index_status(FileIndexStatus::Indexed {
            indexed_epoch_ms: now_epoch_ms()?,
            entity_count: u64::try_from(entities.len())
                .map_err(|_| EngineError::invalid_argument("entity count exceeds u64"))?,
        })?;
        self.write(|state| replace_file(state, &file, entities, entries, graph))
    }

    pub(crate) fn mark_file_failed(&self, file: &FileRecord, error: &str) -> EngineResult<()> {
        let mut file = file.clone();
        file.set_index_status(FileIndexStatus::Failed {
            error: error.to_owned(),
        })?;
        self.write(|state| replace_file(state, &file, &[], &[], &FileGraphResult::default()))
    }

    pub(crate) fn update_file_snapshot(&self, file: &FileRecord) -> EngineResult<()> {
        self.write(|state| {
            let existing = state.files.fetch(&[file.id()])?.remove(&file.id());
            let valid = existing.as_ref().is_some_and(|existing| {
                existing.index_status().is_indexed()
                    && existing.index_status() == file.index_status()
                    && existing.relative_path() == file.relative_path()
                    && existing.snapshot().size_bytes == file.snapshot().size_bytes
                    && existing.snapshot().content_hash == file.snapshot().content_hash
            });
            if !valid {
                return Err(EngineError::invalid_argument(
                    "snapshot refresh requires the same indexed file, content hash and size",
                ));
            }
            let directories = state.directories.ensure(file.relative_path())?;
            state.files.put(file, &directories)
        })
    }

    pub(crate) fn delete_file(&self, file_id: FileId) -> EngineResult<()> {
        self.write(|state| {
            state.write_generation = state.write_generation.wrapping_add(1);
            state.needs_optimize = true;
            state.dirty = DIRTY_ALL;
            state.pending_writes = state.pending_writes.saturating_add(1);
            let old_ids = invalidation_entity_ids(state, file_id)?;
            state.files.mark_deleting(file_id)?;
            if let Some(graph) = state.graph.as_mut() {
                graph
                    .delete_file_graph(file_id.get(), &old_ids)
                    .map_err(graph_error)?;
            }
            state.fragments.delete_file(file_id)?;
            state.entities.delete_file(file_id)?;
            state.files.delete(file_id)
        })?;
        // A stale manifest only disables reuse; deleting it is best-effort and
        // must not turn a committed deletion into a failed pass.
        if let Err(error) = self.delete_embedding_manifest(file_id) {
            tracing::warn!(file_id = file_id.get(), %error, "cannot delete reconciliation manifest");
        }
        Ok(())
    }

    /// Build indexes and compact accepted writes when needed.
    ///
    /// Native fragment maintenance runs outside the store state lock. The
    /// persistent marker remains the crash-retry barrier, and the generation
    /// check preserves optimization debt from writes that arrive while native
    /// work is in flight. Directory/entity/file maintenance stays under the
    /// store lock because those tables still require mutable handles. The
    /// stages run sequentially: the M3-B overlap candidate was rejected after
    /// it increased concurrent-reader latency (see docs/release/BENCHMARK-RESULTS.md).
    pub(crate) fn finalize_writes(&self) -> EngineResult<()> {
        self.finalize_writes_detailed().map(|_| ())
    }

    /// Same as [`Self::finalize_writes`], also returning each maintenance
    /// stage's wall duration.
    ///
    /// The deferred policies keep the same durability barrier: the debt record
    /// is written before any native work starts and rewritten with whatever is
    /// still owed after the pass. Only the bits optimized in this pass are
    /// cleared, so a write that lands during native work keeps its debt.
    pub(crate) fn finalize_writes_detailed(&self) -> EngineResult<OptimizeStageTimings> {
        if self.read_only {
            return Ok(Vec::new());
        }
        let plan = self.write(plan_maintenance)?;
        let Some(plan) = plan else {
            return Ok(Vec::new());
        };
        let fragment_started = Instant::now();
        let fragment_result = plan
            .fragments
            .as_ref()
            .map_or(Ok(()), |fragments| fragments.optimize());
        let fragment_elapsed = fragment_started.elapsed();
        let mut stage_timings = Vec::new();
        if plan.fragments.is_some() {
            stage_timings.push(("index_optimize_fragments".to_owned(), fragment_elapsed));
        }
        self.write(|state| {
            let mut dirty_timings = Vec::new();
            let result = fragment_result
                .and_then(|()| optimize_dirty(state, plan.scalar_dirty, &mut dirty_timings));
            stage_timings.extend(dirty_timings);
            state.optimizing = false;
            result?;
            settle_debt_after_pass(state, &plan)
        })
        .map(|()| stage_timings)
    }

    #[cfg(test)]
    fn finalize_writes_with(
        &self,
        optimize: impl FnOnce(&mut StoreState) -> EngineResult<()>,
    ) -> EngineResult<()> {
        if self.read_only {
            return Ok(());
        }
        self.write(|state| {
            // Also finish indexes created by versions that only flushed writes.
            if state.needs_optimize || state.fragments.needs_optimize()? {
                state.needs_optimize = true;
                // Fragments can finish before another collection fails. Persist retry
                // intent before starting any worker so reopening retains that work.
                write_record(&state.optimize_marker, b"")?;
                optimize(state)?;
                fs::remove_file(&state.optimize_marker).map_err(|error| {
                    io_error("clear pending optimization", &state.optimize_marker, &error)
                })?;
                // A crash before this removal persists can only cause a safe extra retry.
                state.needs_optimize = false;
            }
            Ok(())
        })
    }

    pub(crate) fn close(&self) -> EngineResult<()> {
        if self.read_only {
            let mut lease = self
                .shared
                .lock()
                .map_err(|_| EngineError::internal("storage lease lock was poisoned"))?;
            let _ = lease.take();
            return Ok(());
        }
        // Do not hold the lease lock while waiting: finalize_writes needs the
        // same lock to reacquire the state mutex and clear `optimizing`.
        let shared = {
            let lease = self
                .shared
                .lock()
                .map_err(|_| EngineError::internal("storage lease lock was poisoned"))?;
            lease.as_ref().cloned()
        };
        let Some(shared) = shared else {
            return Ok(());
        };
        let mut state = lock_state(&shared)?;
        // Native fragment maintenance may be outside the state lock. Wait for it
        // before flushing or releasing the storage lease.
        while state.optimizing {
            drop(state);
            std::thread::yield_now();
            state = lock_state(&shared)?;
        }
        drop(state);
        // Serialize the final flush through the lease. A concurrent close may
        // have won the race; in that case it owns the flush.
        let mut lease = self
            .shared
            .lock()
            .map_err(|_| EngineError::internal("storage lease lock was poisoned"))?;
        let Some(shared) = lease.take() else {
            return Ok(());
        };
        let mut state = lock_state(&shared)?;
        // Reject a write that acquired its Arc before close but is still waiting
        // for this lock. A successful close commits every accepted write.
        state.closed = true;
        let flush_result = flush(&state);
        let graph_close = state
            .graph
            .take()
            .map_or(Ok(()), |graph| graph.close().map_err(graph_error));
        flush_result.and(graph_close)
    }
}

/// One pass of maintenance: which collections run now and what they cover.
struct MaintenancePlan {
    fragments: Option<Arc<Fragments>>,
    fragment_dirty: bool,
    scalar_dirty: u8,
    pending_at_snapshot: usize,
    generation: u64,
}

/// Decides whether this pass optimizes now or defers under the debt record.
/// Returns `None` when there is no work at all.
fn plan_maintenance(state: &mut StoreState) -> EngineResult<Option<MaintenancePlan>> {
    if state.optimizing {
        return Ok(None);
    }
    if state.fragments.needs_optimize()? {
        state.dirty |= DIRTY_FRAGMENTS;
    }
    if !state.needs_optimize && state.dirty == 0 {
        return Ok(None);
    }
    let dirty = state.dirty;
    let fragment_dirty = dirty & DIRTY_FRAGMENTS != 0;
    let scalar_dirty = dirty & !DIRTY_FRAGMENTS;
    let forced = state.forced_maintenance || MaintenancePolicy::forced();
    let due = match state.policy.limit() {
        None => true,
        Some(limit) => forced || state.pending_writes >= limit.max(1),
    };
    let run_fragments = fragment_dirty && (!state.policy.defers_fragments() || due);
    let run_scalar = scalar_dirty != 0 && due;

    if !run_fragments && !run_scalar {
        // Debt is real but under the bound: persist it and publish as usual.
        state.needs_optimize = true;
        write_debt(&state.optimize_marker, dirty, state.pending_writes)?;
        return Ok(None);
    }

    let plan = MaintenancePlan {
        fragments: (run_fragments && fragment_dirty).then(|| Arc::clone(&state.fragments)),
        fragment_dirty: run_fragments && fragment_dirty,
        scalar_dirty: if run_scalar { scalar_dirty } else { 0 },
        pending_at_snapshot: state.pending_writes,
        generation: state.write_generation,
    };
    state.needs_optimize = true;
    state.optimizing = true;
    state.forced_maintenance = false;
    // Crash barrier: record what this pass intends to maintain.
    let in_flight = (if plan.fragment_dirty {
        DIRTY_FRAGMENTS
    } else {
        0
    }) | plan.scalar_dirty;
    write_debt(&state.optimize_marker, in_flight, state.pending_writes)?;
    Ok(Some(plan))
}

/// Clears only the debt the pass actually settled and persists the remainder.
fn settle_debt_after_pass(state: &mut StoreState, plan: &MaintenancePlan) -> EngineResult<()> {
    let mut settled = 0u8;
    if plan.fragment_dirty {
        settled |= DIRTY_FRAGMENTS;
    }
    settled |= plan.scalar_dirty;
    // A write that landed during native work was not part of this pass, so its
    // debt is kept in full (the historical conservative behaviour).
    if state.write_generation == plan.generation {
        state.dirty &= !settled;
        if plan.scalar_dirty != 0 {
            state.pending_writes = 0;
        }
    }
    state.needs_optimize = state.dirty != 0;
    if state.dirty == 0 {
        if file_exists(&state.optimize_marker)? {
            fs::remove_file(&state.optimize_marker).map_err(|error| {
                io_error("clear pending optimization", &state.optimize_marker, &error)
            })?;
        }
    } else {
        write_debt(&state.optimize_marker, state.dirty, state.pending_writes)?;
    }
    Ok(())
}

fn optimize_dirty(
    state: &StoreState,
    dirty: u8,
    timings: &mut Vec<(String, Duration)>,
) -> EngineResult<()> {
    #[cfg(test)]
    {
        let (delay, fail) = DIRTY_OPTIMIZE_TEST_HOOK.with(Cell::get);
        if fail {
            return Err(EngineError::storage_failure(
                "injected dirty collection optimize failure",
            ));
        }
        if delay > 0 {
            use std::sync::atomic::Ordering;
            DIRTY_OPTIMIZE_TEST_ACTIVE.store(true, Ordering::Release);
            std::thread::sleep(Duration::from_millis(delay));
            DIRTY_OPTIMIZE_TEST_ACTIVE.store(false, Ordering::Release);
        }
    }
    let directories = &state.directories;
    let entities = &state.entities;
    let files = &state.files;
    std::thread::scope(|scope| {
        let mut workers = Vec::new();
        if dirty & DIRTY_DIRECTORIES != 0 {
            workers.push((
                "index_optimize_directories",
                scope.spawn(move || {
                    let started = Instant::now();
                    let result = directories.optimize();
                    (result, started.elapsed())
                }),
            ));
        }
        if dirty & DIRTY_ENTITIES != 0 {
            workers.push((
                "index_optimize_entities",
                scope.spawn(move || {
                    let started = Instant::now();
                    let result = entities.optimize();
                    (result, started.elapsed())
                }),
            ));
        }
        if dirty & DIRTY_FILES != 0 {
            workers.push((
                "index_optimize_files",
                scope.spawn(move || {
                    let started = Instant::now();
                    let result = files.optimize();
                    (result, started.elapsed())
                }),
            ));
        }
        let mut result = Ok(());
        for (name, worker) in workers {
            let (next, elapsed) = worker.join().unwrap_or_else(|_| {
                (
                    Err(EngineError::internal("collection optimize worker panicked")),
                    Duration::ZERO,
                )
            });
            timings.push((name.to_owned(), elapsed));
            result = result.and(next);
        }
        result
    })
}

#[cfg(test)]
fn optimize_collections(
    operations: [&(dyn Fn() -> EngineResult<()> + Sync); 4],
) -> EngineResult<()> {
    std::thread::scope(|scope| {
        let workers = operations.map(|optimize| scope.spawn(optimize));
        let mut result = Ok(());
        // Keep the store locked until every collection settles, even after an error.
        for worker in workers {
            let next = worker.join().unwrap_or_else(|_| {
                Err(EngineError::internal("collection optimize worker panicked"))
            });
            result = result.and(next);
        }
        result
    })
}

// Cross-table writes stay under the store's single writer lock.
fn replace_file(
    state: &mut StoreState,
    file: &FileRecord,
    entities: &[Entity],
    entries: &[IndexedFragment],
    graph: &FileGraphResult,
) -> EngineResult<()> {
    if state.graph.is_none()
        && (!graph.nodes.is_empty() || !graph.edges.is_empty() || !graph.pending_refs.is_empty())
    {
        return Err(graph_disabled());
    }
    state.write_generation = state.write_generation.wrapping_add(1);
    state.files.validate(file)?;
    state.entities.validate_ownership(entities, file.id())?;
    state.fragments.validate_ownership(entries, file.id())?;
    state.needs_optimize = true;
    state.dirty = DIRTY_ALL;
    state.pending_writes = state.pending_writes.saturating_add(1);
    let directories = state.directories.ensure(file.relative_path())?;
    // Encode every projection before removing any of the previous file's data.
    let entity_docs = Entities::prepare(entities)?;
    let fragment_docs = Fragments::prepare(file, entities, entries, &directories)?;
    let old_ids = invalidation_entity_ids(state, file.id())?;
    let mut unfinished = file.clone();
    unfinished.set_index_status(FileIndexStatus::NotIndexed)?;
    state.files.put(&unfinished, &directories)?;
    // zvec writes are durable on success: record retry intent before advancing SQLite.
    if let Some(storage) = state.graph.as_mut() {
        storage
            .write_file_graph(file.id().get(), graph, &old_ids)
            .map_err(graph_error)?;
    }
    state.fragments.delete_file(file.id())?;
    state.entities.delete_file(file.id())?;
    state.entities.write(&entity_docs)?;
    state.fragments.write(&fragment_docs)?;
    // Indexed is the publication marker: all content must already be durable.
    state.files.put(file, &directories)
}

fn invalidation_entity_ids(state: &StoreState, file_id: FileId) -> EngineResult<Vec<String>> {
    let Some(graph) = state.graph.as_ref() else {
        return Ok(Vec::new());
    };
    // An empty graph has no incoming relationships to invalidate. Avoid a
    // canonical entity scan on every file while extractors produce no graph.
    if graph.is_empty().map_err(graph_error)? {
        return Ok(Vec::new());
    }
    state.entities.list_ids_for_file(file_id)
}

fn graph_disabled() -> EngineError {
    EngineError::unsupported("graph indexing is disabled")
}

fn graph_error(error: graph::Error) -> EngineError {
    match error {
        graph::Error::InvalidInput(message) => EngineError::invalid_argument(message),
        error => EngineError::storage_failure(format!("workspace graph operation failed: {error}")),
    }
}

fn load_search_hits(
    state: &StoreState,
    hits: &[StorageSearchHit],
) -> EngineResult<StoredSearchData> {
    let entity_ids = hits
        .iter()
        .map(|hit| hit.entity_id.clone())
        .collect::<HashSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let file_ids = hits
        .iter()
        .map(|hit| hit.file_id)
        .collect::<HashSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    let stored_entities = state.entities.fetch(&entity_ids)?;
    let files = state.files.fetch(&file_ids)?;
    let mut entities = HashMap::new();
    let mut fragments = HashMap::new();
    for entity in stored_entities.into_values() {
        // A replacement or deletion can stop between collection writes.
        let Some(file) = files.get(&entity.file_id()) else {
            continue;
        };
        for fragment in entity.fragments() {
            if fragments
                .insert(fragment.id.as_str().to_owned(), fragment.clone())
                .is_some()
            {
                return Err(corrupt("duplicate canonical fragment identity"));
            }
        }
        entities.insert(
            entity.id().clone(),
            StoredEntity {
                entity,
                file: file.clone(),
            },
        );
    }
    for hit in hits {
        if let Some(owner) = entities.get(&hit.entity_id)
            && owner.file.id() != hit.file_id
        {
            return Err(corrupt("search identities differ from their stored entity"));
        }
    }
    Ok(StoredSearchData {
        entities,
        fragments,
    })
}

fn flush(state: &StoreState) -> EngineResult<()> {
    state.directories.flush()?;
    state.entities.flush()?;
    state.fragments.flush()?;
    state.files.flush()
}

fn assert_usable(state: &StoreState) -> EngineResult<()> {
    if state.closed {
        return Err(EngineError::resource_closed("workspace storage is closed"));
    }
    Ok(())
}

fn validate_batch(
    file: &FileRecord,
    entities: &[Entity],
    entries: &[IndexedFragment],
    schema: &[EmbeddingModelInfo],
) -> EngineResult<()> {
    let mut entity_ids = HashSet::new();
    let mut fragment_ids = HashSet::new();
    for entity in entities {
        if entity.file_id() != file.id() {
            return Err(EngineError::invalid_argument(
                "entity belongs to a different file",
            ));
        }
        if !entity_ids.insert(entity.id()) {
            return Err(EngineError::invalid_argument("duplicate entity id"));
        }
        for fragment in entity.fragments() {
            if !fragment_ids.insert(&fragment.id) {
                return Err(EngineError::invalid_argument("duplicate fragment id"));
            }
        }
    }
    fragments::validate_projections(entities, entries)?;
    for entry in entries {
        validate_vector(&entry.vector, model_schema(schema, &entry.model)?)?;
    }
    Ok(())
}

fn model_schema<'a>(
    schemas: &'a [EmbeddingModelInfo],
    model: &str,
) -> EngineResult<&'a EmbeddingModelInfo> {
    schemas
        .iter()
        .find(|schema| schema.model().reference() == model)
        .ok_or_else(|| EngineError::invalid_argument(format!("unknown embedding model {model:?}")))
}

fn validate_vector(vector: &[f32], schema: &EmbeddingModelInfo) -> EngineResult<()> {
    if vector.len() != schema.dimension() || vector.iter().any(|value| !value.is_finite()) {
        return Err(EngineError::invalid_argument(format!(
            "expected a finite {}-dimensional vector, got {} values",
            schema.dimension(),
            vector.len()
        )));
    }
    Ok(())
}

fn load_schema(
    path: &Path,
    options: WorkspaceIndexStorageOptions,
) -> EngineResult<Vec<EmbeddingModelInfo>> {
    let descriptor = path.join("schema.json");
    if file_exists(&descriptor)? {
        let schema = read_json::<SchemaRecord>(&descriptor)
            .map_err(|error| {
                EngineError::storage_failure(format!(
                    "unsupported or invalid storage schema: {error}; rebuild the index"
                ))
            })?
            .embeddings()?;
        if let WorkspaceIndexStorageOptions::ReadWrite { embeddings, .. } = &options {
            if schema.len() != embeddings.len() {
                return Err(EngineError::invalid_argument(
                    "embedding model set changed; rebuild the index",
                ));
            }
            for stored in &schema {
                let current =
                    model_schema(embeddings, &stored.model().reference()).map_err(|_| {
                        EngineError::invalid_argument(
                            "embedding model set changed; rebuild the index",
                        )
                    })?;
                stored.ensure_index_compatible(current).map_err(|error| {
                    error.with_help("Rebuild the index with `zg --index --rebuild`.")
                })?;
            }
        }
        for collection in [
            "files".to_owned(),
            "directories".to_owned(),
            "entities".to_owned(),
        ]
        .into_iter()
        .chain(schema.iter().map(fragments::fragment_collection_name))
        {
            let collection_path = path.join(&collection);
            if !directory_exists(&collection_path)? {
                return Err(EngineError::storage_failure(format!(
                    "index collection {collection} is missing; rebuild the index"
                )));
            }
        }
        return Ok(schema);
    }
    if fs::read_dir(path)
        .map_err(|error| io_error("inspect storage directory", path, &error))?
        .find(|entry| {
            entry
                .as_ref()
                .map_or(true, |entry| entry.file_name() != "storage.lock")
        })
        .transpose()
        .map_err(|error| io_error("inspect storage entry", path, &error))?
        .is_some()
    {
        return Err(EngineError::storage_failure(
            "existing workspace storage is missing its schema; rebuild the index",
        ));
    }
    let WorkspaceIndexStorageOptions::ReadWrite { embeddings, .. } = options else {
        return Err(EngineError::not_found(
            "workspace storage schema does not exist",
        ));
    };
    validate_models(&embeddings)?;
    write_record(
        &descriptor,
        &serde_json::to_vec(&SchemaRecord::new(&embeddings)).map_err(|error| json_error(&error))?,
    )?;
    Ok(embeddings)
}


/// Reads the durable maintenance debt record. Any damage degrades to `None`,
/// which callers interpret as the fail-safe "all dirty" marker.
fn read_debt(path: &Path, present: bool) -> EngineResult<Option<DebtRecord>> {
    if !present {
        return Ok(None);
    }
    let Ok(file) = open_regular_file(path) else {
        return Ok(None);
    };
    let mut bytes = Vec::new();
    if file.take(4096).read_to_end(&mut bytes).is_err() {
        return Ok(None);
    }
    Ok(DebtRecord::decode(&bytes))
}

fn write_debt(path: &Path, dirty: u8, pending: usize) -> EngineResult<()> {
    let record = DebtRecord::new(dirty, pending);
    match record.encode() {
        Some(bytes) => write_record(path, &bytes),
        None => write_record(path, b""),
    }
}

fn registry() -> EngineResult<MutexGuard<'static, HashMap<PathBuf, Weak<SharedStore>>>> {
    let mut stores = STORES
        .get_or_init(|| Mutex::new(HashMap::new()))
        .lock()
        .map_err(|_| EngineError::internal("storage registry lock was poisoned"))?;
    stores.retain(|_, store| store.strong_count() != 0);
    Ok(stores)
}

fn lock_state(shared: &SharedStore) -> EngineResult<MutexGuard<'_, StoreState>> {
    shared
        .state
        .lock()
        .map_err(|_| EngineError::internal("workspace storage lock was poisoned"))
}

fn prepare_storage(
    path: &Path,
    options: WorkspaceIndexStorageOptions,
) -> EngineResult<(File, Vec<EmbeddingModelInfo>)> {
    let lock = acquire_storage_lock(path, options.is_read_only())?;
    Ok((lock, load_schema(path, options)?))
}

fn acquire_storage_lock(home: &Path, shared: bool) -> EngineResult<File> {
    let path = home.join("storage.lock");
    reject_symlink(&path)?;
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(&path)
        .map_err(|error| io_error("open storage lock", &path, &error))?;
    let result = if shared {
        file.try_lock_shared()
    } else {
        file.try_lock()
    };
    result.map_err(|error| storage_lock_error(home, error))?;
    Ok(file)
}

fn storage_lock_error(home: &Path, error: std::fs::TryLockError) -> EngineError {
    match error {
        std::fs::TryLockError::WouldBlock => EngineError::resource_busy(format!(
            "workspace storage is locked by another reader or writer: {}",
            home.display()
        )),
        std::fs::TryLockError::Error(error) => io_error("lock workspace storage", home, &error),
    }
}

fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> EngineResult<T> {
    let bytes = fs::read(path).map_err(|error| io_error("read storage record", path, &error))?;
    serde_json::from_slice(&bytes).map_err(|error| {
        EngineError::storage_failure(format!(
            "invalid storage record {}: {error}",
            path.display()
        ))
    })
}

pub(super) fn io_error(action: &str, path: &Path, error: &std::io::Error) -> EngineError {
    EngineError::from_io(
        format!("cannot {action} {}: {error}", path.display()),
        error,
    )
}

fn json_error(error: &serde_json::Error) -> EngineError {
    EngineError::storage_failure(format!("cannot encode storage record: {error}"))
}

fn now_epoch_ms() -> EngineResult<u64> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|value| u64::try_from(value.as_millis()).ok())
        .ok_or_else(|| EngineError::internal("system clock cannot represent the index timestamp"))
}

#[cfg(test)]
mod tests;

#[cfg(test)]
mod tables_tests;

#[cfg(test)]
mod graph_tests;
