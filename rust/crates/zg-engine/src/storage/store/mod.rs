//! Index lifecycle and operations that span multiple tables.

use std::{
    collections::{BTreeMap, HashMap, HashSet},
    fs::{self, DirBuilder, File, OpenOptions},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, MutexGuard, OnceLock, Weak},
    time::Instant,
};

#[cfg(test)]
use std::{
    cell::{Cell, RefCell},
    sync::atomic::{AtomicBool, AtomicU64, Ordering},
};

#[cfg(test)]
thread_local! {
    static STATE_LOCK_WAIT_TELEMETRY: Cell<(u64, u64, u64)> = const { Cell::new((0, 0, 0)) };
    static STATE_LOCK_TRACE: RefCell<Option<StateLockTrace>> = const { RefCell::new(None) };
    static STATE_LOCK_STAGE: Cell<&'static str> = const { Cell::new("other") };
}

#[cfg(all(test, target_os = "linux"))]
pub(crate) fn monotonic_time_ns_for_test() -> u64 {
    let elapsed = rustix::time::clock_gettime(rustix::time::ClockId::Monotonic);
    u64::try_from(elapsed.tv_sec)
        .unwrap_or(0)
        .saturating_mul(1_000_000_000)
        .saturating_add(u64::try_from(elapsed.tv_nsec).unwrap_or(0))
}

#[cfg(all(test, not(target_os = "linux")))]
pub(crate) fn monotonic_time_ns_for_test() -> u64 {
    0
}

#[cfg(test)]
pub(crate) type StateLockSpan = (&'static str, &'static str, u128, u128);
#[cfg(test)]
pub(crate) type NativeFtsSpan = (u128, u128, u64);

#[cfg(test)]
struct StateLockTrace {
    origin: Instant,
    spans: Vec<StateLockSpan>,
    dropped: usize,
    native_fts_spans: Vec<NativeFtsSpan>,
    native_fts_dropped: usize,
    cap: usize,
}

#[cfg(test)]
const STATE_LOCK_TRACE_CAP: usize = 4096;

#[cfg(test)]
struct StateLockStage(&'static str);

#[cfg(test)]
impl StateLockStage {
    fn enter(stage: &'static str) -> Self {
        Self(STATE_LOCK_STAGE.with(|current| current.replace(stage)))
    }
}

#[cfg(test)]
impl Drop for StateLockStage {
    fn drop(&mut self) {
        STATE_LOCK_STAGE.with(|current| current.set(self.0));
    }
}

#[cfg(test)]
fn record_state_lock_span(kind: &'static str, start: Instant, end: Instant) {
    STATE_LOCK_TRACE.with(|trace| {
        let mut trace = trace.borrow_mut();
        let Some(trace) = trace.as_mut() else { return };
        if trace.spans.len() >= trace.cap {
            trace.dropped = trace.dropped.saturating_add(1);
            return;
        }
        let stage = STATE_LOCK_STAGE.with(Cell::get);
        trace.spans.push((
            stage,
            kind,
            start.saturating_duration_since(trace.origin).as_nanos(),
            end.saturating_duration_since(trace.origin).as_nanos(),
        ));
    });
}

#[cfg(test)]
pub(crate) fn record_native_fts_span_for_test(start: Instant, end: Instant, thread_cpu_ns: u64) {
    STATE_LOCK_TRACE.with(|trace| {
        let mut trace = trace.borrow_mut();
        let Some(trace) = trace.as_mut() else { return };
        if trace.native_fts_spans.len() >= trace.cap {
            trace.native_fts_dropped = trace.native_fts_dropped.saturating_add(1);
            return;
        }
        trace.native_fts_spans.push((
            start.saturating_duration_since(trace.origin).as_nanos(),
            end.saturating_duration_since(trace.origin).as_nanos(),
            thread_cpu_ns,
        ));
    });
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

use crate::{
    EngineError, EngineResult,
    domain::{Entity, EntityId, FileId, FileIndexStatus, FileRecord, FragmentId},
    utils::{atomic_write as write_record, sha256_hex, sync_directory},
};

use super::{
    directories::Directories,
    entities::{self, Entities},
    files::Files,
    fragments::{self, Fragments},
    manifest::{
        EntityManifestEntry, FileManifest, FragmentManifestEntry, MAX_FILE_MANIFEST_BYTES,
        ManifestStore,
    },
    types::{
        IndexedFragment, StorageSearchFilter, StorageSearchHit, StoredEntity, StoredFileAttributes,
        StoredSearchData, WorkspaceIndexStorageOptions,
    },
    zvec::{corrupt, initialize, native},
};
use crate::domain::model::EmbeddingModelInfo;

type StoreRegistry = Mutex<HashMap<PathBuf, Weak<SharedStore>>>;
static STORES: OnceLock<StoreRegistry> = OnceLock::new();

struct SharedStore {
    state: Mutex<StoreState>,
    /// Serializes native maintenance (optimize/flush) against close/retire so a
    /// collection is never closed while native maintenance is in flight. Lock
    /// order is maintenance -> mutation_gate -> state; writers and finalization
    /// acknowledgement take mutation_gate -> state. Queries take only state.
    maintenance: Mutex<()>,
    /// Serializes writes against checkpoints while fragment flush runs without
    /// the state lock, allowing read-only queries to proceed over stable data.
    mutation_gate: Mutex<()>,
    #[cfg(test)]
    checkpoint_fragment_flush_pause:
        Mutex<Option<(std::sync::mpsc::Sender<()>, std::sync::mpsc::Receiver<()>)>>,
    #[cfg(test)]
    maintenance_started: AtomicBool,
    schema: Vec<EmbeddingModelInfo>,
    /// Storage directory, used for the persisted maintenance marker.
    storage_path: std::path::PathBuf,
    read_only: bool,
    // The native handles must close before the operating-system lock is released.
    _lock: File,
}

struct StoreState {
    files: Files,
    directories: Directories,
    entities: Entities,
    fragments: Fragments,
    manifests: ManifestStore,
    finalize: FinalizeState,
    mutation_stats: MutationStats,
    /// Monotonic counter bumped on every accepted mutation that marks finalize
    /// debt. Maintenance acknowledges only the snapshot it optimized.
    mutation_epoch: u64,
    /// Fragment-maintenance debt: increments only for mutations that mark the
    /// fragment collection, not metadata-only changes.
    fragments_debt: u64,
    /// Fragment debt acknowledged by the last completed maintenance run.
    fragments_ack_epoch: u64,
    /// Wall-clock time the current pending debt began (0 = no debt pending).
    pending_since_ms: u64,
    /// A deletion is pending and must force maintenance even at completeness 1.
    fragments_delete_pending: bool,
    #[cfg(test)]
    optimize_calls: OptimizeCalls,
    closed: bool,
}

#[derive(Clone, Copy, Debug, Default)]
struct MutationStats {
    files_replaced: u64,
    files_deleted: u64,
    files_snapshot_updated: u64,
    /// Native rows written/deleted per collection (write-amplification audit).
    entities_written: u64,
    fragments_written: u64,
    entities_deleted: u64,
    fragments_deleted: u64,
}

/// Which native collections need `optimize()` after the accepted writes.
///
/// Each collection is finalized independently. The scalar tables (`directories`,
/// `entities`, `files`) have no native completeness probe, so a mutation forces
/// their optimize. `fragments` additionally carries the vector/FTS indexes: an
/// incomplete vector index always forces its optimize (covering flush-only
/// writers and process restarts), and fragment deletions force it explicitly
/// because tombstones leave the HNSW search path only after optimize.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct FinalizeState(u8);

impl FinalizeState {
    const DIRECTORIES: u8 = 1 << 0;
    const ENTITIES: u8 = 1 << 1;
    const FILES: u8 = 1 << 2;
    const FRAGMENTS: u8 = 1 << 3;

    fn mark(&mut self, collection: u8) {
        self.0 |= collection;
    }

    fn contains(self, collection: u8) -> bool {
        self.0 & collection != 0
    }

    fn clear(&mut self) {
        self.0 = 0;
    }
}

impl StoreState {
    /// Mark native finalize debt and advance the mutation epoch so maintenance
    /// can acknowledge only the exact snapshot it optimized.
    fn mark_finalize(&mut self, collection: u8) {
        self.finalize.mark(collection);
        self.mutation_epoch = self.mutation_epoch.wrapping_add(1);
        // Fragment-maintenance debt only advances for mutations that actually
        // touch the fragment projection, not metadata-only changes.
        if collection & FinalizeState::FRAGMENTS != 0 {
            self.fragments_debt = self.fragments_debt.wrapping_add(1);
        }
    }

    /// Deletion leaves tombstones that only native optimize removes, even when
    /// completeness is already 1.0, so it forces immediate maintenance.
    fn mark_fragments_delete(&mut self) {
        self.mark_finalize(FinalizeState::FRAGMENTS);
        self.fragments_delete_pending = true;
    }
}

/// Minimum bounded-deferred maintenance policy. Not a universal optimum.
const DEFAULT_MAINTENANCE_THRESHOLD: u64 = 50;
const DEFAULT_MAINTENANCE_MAX_AGE_MS: u64 = 600_000;

#[derive(Clone, Copy, Debug)]
struct MaintenancePolicy {
    /// Pending fragment-mutation debt required to start maintenance.
    threshold: u64,
    /// Wall-clock deadline after which maintenance must run regardless of count.
    max_age_ms: u64,
}

impl MaintenancePolicy {
    fn from_env() -> Self {
        // Bounded deferral is opt-in: default is eager (0). K=50 is the
        // experimental canary value (set ZVEC_GREP_MAINTENANCE_THRESHOLD=50),
        // not a universal production default.
        let default_threshold = 0;
        let threshold = std::env::var("ZVEC_GREP_MAINTENANCE_THRESHOLD")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(default_threshold);
        let max_age_ms = std::env::var("ZVEC_GREP_MAINTENANCE_MAX_AGE_MS")
            .ok()
            .and_then(|value| value.parse::<u64>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(DEFAULT_MAINTENANCE_MAX_AGE_MS);
        MaintenancePolicy {
            threshold,
            max_age_ms,
        }
    }
}

fn wall_clock_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |elapsed| {
            u64::try_from(elapsed.as_millis()).unwrap_or(u64::MAX)
        })
}

/// Process-wide physical write bytes (includes async `RocksDB` background work).
fn io_write_bytes() -> u64 {
    std::fs::read_to_string("/proc/self/io")
        .ok()
        .and_then(|text| {
            text.lines()
                .find_map(|line| line.strip_prefix("write_bytes:"))
                .and_then(|value| value.trim().parse::<u64>().ok())
        })
        .unwrap_or(0)
}

fn delete_marker_path(storage_path: &std::path::Path) -> std::path::PathBuf {
    storage_path.join("maintenance.delete")
}

/// Persist (or clear) the pending-deletion marker so a restart/reopen still
/// recognizes tombstone maintenance that completeness cannot detect.
/// Durably persist (or clear) the pending-deletion marker using the same
/// fsync-file + fsync-directory publish as the workspace manifest, so the
/// intent survives power loss before any tombstone is flushed.
fn persist_delete_marker(storage_path: &std::path::Path, pending: bool) -> EngineResult<()> {
    let path = delete_marker_path(storage_path);
    if pending {
        write_record(&path, b"1")?;
    } else if path.exists() {
        fs::remove_file(&path)
            .map_err(|error| io_error("remove maintenance marker", &path, &error))?;
        sync_directory(storage_path)?;
    }
    Ok(())
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct OptimizeCalls {
    directories: u64,
    entities: u64,
    files: u64,
    fragments: u64,
}

pub(crate) struct IndexStore {
    shared: Mutex<Option<Arc<SharedStore>>>,
    read_only: bool,
    #[cfg(test)]
    fail_next_staged_publish_after_projection: AtomicBool,
    #[cfg(test)]
    fail_next_optimize: AtomicBool,
    #[cfg(test)]
    close_waited_for_maintenance: AtomicBool,
    #[cfg(test)]
    last_finalize_saw_closed: AtomicBool,
    #[cfg(test)]
    maintenance_threshold_override: AtomicU64,
    #[cfg(test)]
    maintenance_max_age_override: AtomicU64,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SchemaRecord {
    embeddings: Vec<EmbeddingModelInfo>,
}

impl SchemaRecord {
    fn new(embeddings: &[EmbeddingModelInfo]) -> Self {
        Self {
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
    for embedding in embeddings {
        embedding.validate()?;
        if !(1..=20_000).contains(&embedding.dimension) {
            return Err(EngineError::invalid_argument(
                "embedding dimension must be in 1..=20,000",
            ));
        }
        if !models.insert(embedding.model.reference()) {
            return Err(EngineError::invalid_argument(
                "embedding model references must be unique",
            ));
        }
    }
    Ok(())
}

impl IndexStore {
    pub(crate) fn open(options: WorkspaceIndexStorageOptions) -> EngineResult<Self> {
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
        if !read_only {
            let mut builder = DirBuilder::new();
            builder.recursive(false);
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }
            if let Err(error) = builder.create(home)
                && !(error.kind() == std::io::ErrorKind::AlreadyExists && home.is_dir())
            {
                return Err(io_error("create index directory", home, &error));
            }
        }
        let home = fs::canonicalize(home)
            .map_err(|error| io_error("locate index directory", home, &error))?;
        let path = home.join("storage");
        let mut registry = registry()?;
        if let Some(shared) = registry.get(&path).and_then(Weak::upgrade) {
            if read_only && shared.read_only {
                return Ok(Self {
                    shared: Mutex::new(Some(shared)),
                    read_only,
                    #[cfg(test)]
                    fail_next_staged_publish_after_projection: AtomicBool::new(false),
                    #[cfg(test)]
                    fail_next_optimize: AtomicBool::new(false),
                    #[cfg(test)]
                    close_waited_for_maintenance: AtomicBool::new(false),
                    #[cfg(test)]
                    last_finalize_saw_closed: AtomicBool::new(false),
                    #[cfg(test)]
                    maintenance_threshold_override: AtomicU64::new(u64::MAX),
                    #[cfg(test)]
                    maintenance_max_age_override: AtomicU64::new(u64::MAX),
                });
            }
            return Err(EngineError::resource_busy(
                "workspace storage is already open",
            ));
        }
        let (lock, schema) = prepare_storage(&home, &path, options)?;
        let files = Files::open(&path, read_only)?;
        let directories = Directories::open(&path, read_only)?;
        let entities = Entities::open(&path, read_only)?;
        let fragments = Fragments::open(&path, &schema, read_only)?;
        let manifests = ManifestStore::open(&path, read_only)?;
        if !read_only {
            directories.load()?;
        }
        if !read_only {
            // Repeat the same range after failures that leave directories in place.
            sync_directory(&path)?;
            sync_directory(&home)?;
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
                manifests,
                finalize: FinalizeState::default(),
                mutation_stats: MutationStats::default(),
                mutation_epoch: 0,
                fragments_debt: 0,
                fragments_ack_epoch: 0,
                pending_since_ms: 0,
                fragments_delete_pending: false,
                #[cfg(test)]
                optimize_calls: OptimizeCalls::default(),
                closed: false,
            }),
            maintenance: Mutex::new(()),
            mutation_gate: Mutex::new(()),
            #[cfg(test)]
            checkpoint_fragment_flush_pause: Mutex::new(None),
            #[cfg(test)]
            maintenance_started: AtomicBool::new(false),
            schema,
            storage_path: path.clone(),
            read_only,
            _lock: lock,
        });
        if !read_only {
            let mut state = lock_state(&shared)?;
            if delete_marker_path(&path).exists() {
                state.fragments_delete_pending = true;
                state.pending_since_ms = wall_clock_ms();
            } else if state.fragments.needs_optimize()? {
                // Insert-only debt survives restart via native completeness;
                // start the (best-effort) age deadline so it is not deferred
                // forever when no new mutation arrives after reopen.
                state.pending_since_ms = wall_clock_ms();
            }
        }
        registry.insert(path, Arc::downgrade(&shared));
        Ok(Self {
            shared: Mutex::new(Some(shared)),
            read_only,
            #[cfg(test)]
            fail_next_staged_publish_after_projection: AtomicBool::new(false),
            #[cfg(test)]
            fail_next_optimize: AtomicBool::new(false),
            #[cfg(test)]
            close_waited_for_maintenance: AtomicBool::new(false),
            #[cfg(test)]
            last_finalize_saw_closed: AtomicBool::new(false),
            #[cfg(test)]
            maintenance_threshold_override: AtomicU64::new(u64::MAX),
            #[cfg(test)]
            maintenance_max_age_override: AtomicU64::new(u64::MAX),
        })
    }

    pub(crate) fn exists(storage_path: &Path) -> EngineResult<bool> {
        match fs::metadata(storage_path.join("storage")) {
            Ok(metadata) => Ok(metadata.is_dir()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
            Err(error) => Err(io_error("inspect storage", storage_path, &error)),
        }
    }

    pub(crate) fn delete(storage_path: &Path) -> EngineResult<()> {
        if !Self::exists(storage_path)? {
            return Ok(());
        }
        let home = fs::canonicalize(storage_path)
            .map_err(|error| io_error("locate storage", storage_path, &error))?;
        let path = home.join("storage");
        let mut registry = registry()?;
        if registry.get(&path).and_then(Weak::upgrade).is_some() {
            return Err(EngineError::resource_busy(
                "cannot delete open workspace storage",
            ));
        }
        let _lock = acquire_storage_lock(&home, false)?;
        fs::remove_dir_all(&path)
            .map_err(|error| io_error("delete workspace storage", &path, &error))?;
        sync_directory(&home)?;
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
        {
            let acquired = Instant::now();
            record_state_lock_wait_for_test(
                u64::try_from(acquired.duration_since(lock_started).as_nanos()).unwrap_or(u64::MAX),
            );
            record_state_lock_span("read_wait", lock_started, acquired);
        }
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
        let _mutation = shared
            .mutation_gate
            .lock()
            .map_err(|_| EngineError::internal("storage mutation gate was poisoned"))?;
        self.write_locked(&shared, operation)
    }

    /// Runs a write while the caller owns `SharedStore::mutation_gate`.
    fn write_locked<T>(
        &self,
        shared: &SharedStore,
        operation: impl FnOnce(&mut StoreState) -> EngineResult<T>,
    ) -> EngineResult<T> {
        if self.read_only {
            return Err(EngineError::permission_denied(
                "cannot write read-only workspace storage",
            ));
        }
        #[cfg(test)]
        let lock_started = Instant::now();
        let mut state = lock_state(shared)?;
        #[cfg(test)]
        let acquired = Instant::now();
        #[cfg(test)]
        {
            record_state_lock_wait_for_test(
                u64::try_from(acquired.duration_since(lock_started).as_nanos()).unwrap_or(u64::MAX),
            );
            record_state_lock_span("write_wait", lock_started, acquired);
        }
        assert_usable(&state)?;
        let result = operation(&mut state);
        #[cfg(test)]
        {
            drop(state);
            record_state_lock_span("write_hold", acquired, Instant::now());
        }
        result
    }

    #[cfg(test)]
    pub(crate) fn take_state_lock_wait_telemetry_for_test(&self) -> (u64, u64, u64) {
        take_state_lock_wait_for_test()
    }

    #[cfg(test)]
    pub(crate) fn take_native_fts_query_telemetry_for_test(&self) -> (u64, u64) {
        fragments::take_native_fts_query_telemetry_for_test()
    }

    #[cfg(test)]
    pub(crate) fn take_native_fts_spans_for_test(&self) -> (Vec<NativeFtsSpan>, usize) {
        STATE_LOCK_TRACE.with(|trace| {
            trace.borrow_mut().as_mut().map_or_else(
                || (Vec::new(), 0),
                |trace| {
                    (
                        std::mem::take(&mut trace.native_fts_spans),
                        std::mem::take(&mut trace.native_fts_dropped),
                    )
                },
            )
        })
    }

    #[cfg(test)]
    pub(crate) fn begin_state_lock_trace_for_test(&self, origin: Instant, cap: usize) {
        STATE_LOCK_TRACE.with(|trace| {
            *trace.borrow_mut() = Some(StateLockTrace {
                origin,
                spans: Vec::new(),
                dropped: 0,
                native_fts_spans: Vec::new(),
                native_fts_dropped: 0,
                cap: cap.min(STATE_LOCK_TRACE_CAP),
            });
        });
    }

    #[cfg(test)]
    pub(crate) fn take_state_lock_trace_for_test(
        &self,
        finish: bool,
    ) -> (Vec<StateLockSpan>, usize) {
        STATE_LOCK_TRACE.with(|trace| {
            let mut trace = trace.borrow_mut();
            if finish {
                trace
                    .take()
                    .map_or_else(|| (Vec::new(), 0), |trace| (trace.spans, trace.dropped))
            } else {
                trace.as_mut().map_or_else(
                    || (Vec::new(), 0),
                    |trace| {
                        (
                            std::mem::take(&mut trace.spans),
                            std::mem::take(&mut trace.dropped),
                        )
                    },
                )
            }
        })
    }

    pub(crate) fn is_read_only(&self) -> bool {
        self.read_only
    }

    pub(crate) fn load_embedding_manifest(
        &self,
        file_id: FileId,
    ) -> EngineResult<Option<FileManifest>> {
        self.read(|state| Ok(load_indexed_manifest(state, file_id)?.1))
    }

    pub(crate) fn fetch_fragment_vectors(
        &self,
        model: &str,
        fragment_ids: &[String],
    ) -> EngineResult<HashMap<String, Vec<f32>>> {
        self.read(|state| state.fragments.fetch_vectors(model, fragment_ids))
    }

    #[cfg(test)]
    fn fail_next_staged_publish_after_projection(&self) {
        self.fail_next_staged_publish_after_projection
            .store(true, Ordering::Release);
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

    pub(crate) fn replace_file(
        &self,
        file: &FileRecord,
        entities: &[Entity],
        entries: &[IndexedFragment],
    ) -> EngineResult<()> {
        let shared = self.shared()?;
        validate_batch(file, entities, entries, &shared.schema)?;
        let mut file = file.clone();
        file.index_status = FileIndexStatus::Indexed {
            indexed_epoch_ms: wall_clock_ms(),
            entity_count: u64::try_from(entities.len())
                .map_err(|_| EngineError::invalid_argument("entity count exceeds u64"))?,
        };
        file.validate()?;
        #[cfg(test)]
        let _stage = StateLockStage::enter("replace_file");
        self.write(|state| replace_file(state, &file, entities, entries, &shared.storage_path))
    }

    pub(crate) fn replace_file_from_staged(
        &self,
        staged: crate::storage::staging::DiskStagedBatches,
    ) -> EngineResult<()> {
        let shared = self.shared()?;
        let mut file = staged.file().clone();

        // Validate the full staged replacement before touching the current file,
        // while retaining only one entity batch and one projection batch.
        let mut preflight_projections = staged.fragment_reader()?;
        staged.for_each_entity_batch(|entities| {
            validate_entities(&file, entities)?;
            for entity in entities {
                let mut encoder =
                    fragments::ProjectionEncoder::new(&file, std::slice::from_ref(entity), &[])?;
                let mut remaining = entity.fragments.len();
                while remaining > 0 {
                    let requested = remaining.min(crate::storage::staging::STAGING_BATCH_FRAGMENTS);
                    let batch = preflight_projections.next_batch(requested)?;
                    if batch.len() != requested {
                        return Err(EngineError::invalid_argument(
                            "canonical fragment is missing its search projection",
                        ));
                    }
                    for entry in &batch {
                        validate_vector(
                            &entry.vector,
                            model_schema(&shared.schema, &entry.model)?,
                        )?;
                    }
                    drop(encoder.prepare(&batch)?);
                    remaining -= batch.len();
                }
                encoder.finish()?;
            }
            drop(Entities::prepare(entities)?);
            Ok(())
        })?;
        preflight_projections.finish()?;

        file.index_status = FileIndexStatus::Indexed {
            indexed_epoch_ms: wall_clock_ms(),
            entity_count: u64::try_from(staged.entity_count())
                .map_err(|_| EngineError::invalid_argument("entity count exceeds u64"))?,
        };
        file.validate()?;
        #[cfg(test)]
        let _stage = StateLockStage::enter("replace_staged");
        let result = self.write(|state| {
            state.files.validate(&file)?;

            // Check ownership while holding the writer lock, still before the
            // old generation is removed.
            let mut ownership_projections = staged.fragment_reader()?;
            staged.for_each_entity_batch(|entities| {
                state.entities.validate_ownership(entities, file.id)?;
                for entity in entities {
                    let mut remaining = entity.fragments.len();
                    while remaining > 0 {
                        let requested =
                            remaining.min(crate::storage::staging::STAGING_BATCH_FRAGMENTS);
                        let batch = ownership_projections.next_batch(requested)?;
                        if batch.len() != requested {
                            return Err(EngineError::invalid_argument(
                                "canonical fragment is missing its search projection",
                            ));
                        }
                        state.fragments.validate_ownership(&batch, file.id)?;
                        remaining -= batch.len();
                    }
                }
                Ok(())
            })?;
            ownership_projections.finish()?;
            let directories = state.directories.ensure(&file.relative_path)?;
            if state.directories.took_write() {
                state.mark_finalize(FinalizeState::DIRECTORIES);
            }
            state.mark_finalize(FinalizeState::FILES);
            let new_manifest = build_staged_file_manifest(&staged, &file, &directories)?;
            let (had_file_record, old_manifest) = load_indexed_manifest(state, file.id)?;
            let incremental = match (&old_manifest, &new_manifest) {
                (Some(old), Some(new)) => Some(build_incremental_plan(old, new)),
                _ => None,
            };
            persist_replacement_delete_intent(
                state,
                &shared.storage_path,
                had_file_record,
                incremental.as_ref(),
            )?;

            // Fail closed: drop the previous published snapshot before the first
            // row mutation. A `NotIndexed` file stays searchable only while a
            // validated manifest exists, so an interrupted apply cannot expose a
            // spliced generation; see `load_search_hits`.
            state.manifests.delete(file.id)?;
            let mut unfinished = file.clone();
            unfinished.index_status = FileIndexStatus::NotIndexed;
            state.files.put(&unfinished, &directories)?;
            if let Some(plan) = incremental.as_ref() {
                if plan.has_entity_changes() {
                    state.mark_finalize(FinalizeState::ENTITIES);
                }
                if plan.has_fragment_deletions() {
                    state.mark_finalize(FinalizeState::FRAGMENTS);
                }
            } else {
                // A full replacement deletes and rewrites every projection.
                state.mark_finalize(FinalizeState::ENTITIES);
                state.mark_finalize(FinalizeState::FRAGMENTS);
            }
            let mut unfinished = file.clone();
            unfinished.index_status = FileIndexStatus::NotIndexed;
            state.files.put(&unfinished, &directories)?;
            if let Some(plan) = incremental.as_ref() {
                for (model, fragment_ids) in &plan.delete_fragments {
                    state.fragments.delete_fragment_ids(model, fragment_ids)?;
                }
                state.entities.delete_ids(&plan.delete_entities)?;
                state.mutation_stats.fragments_deleted =
                    state.mutation_stats.fragments_deleted.saturating_add(
                        plan.delete_fragments
                            .values()
                            .map(|ids| u64::try_from(ids.len()).unwrap_or(u64::MAX))
                            .sum(),
                    );
                state.mutation_stats.entities_deleted = state
                    .mutation_stats
                    .entities_deleted
                    .saturating_add(u64::try_from(plan.delete_entities.len()).unwrap_or(u64::MAX));
            } else {
                state.fragments.delete_file(file.id)?;
                state.entities.delete_file(file.id)?;
            }

            let mut apply_projections = staged.fragment_reader()?;
            staged.for_each_entity_batch(|entities| {
                let entity_docs =
                    select_entity_docs(Entities::prepare(entities)?, incremental.as_ref())?;
                state.mutation_stats.entities_written = state
                    .mutation_stats
                    .entities_written
                    .saturating_add(u64::try_from(entity_docs.len()).unwrap_or(u64::MAX));
                state.entities.write(&entity_docs)?;
                for entity in entities {
                    let mut encoder = fragments::ProjectionEncoder::new(
                        &file,
                        std::slice::from_ref(entity),
                        &directories,
                    )?;
                    let mut remaining = entity.fragments.len();
                    while remaining > 0 {
                        let requested =
                            remaining.min(crate::storage::staging::STAGING_BATCH_FRAGMENTS);
                        let batch = apply_projections.next_batch(requested)?;
                        if batch.len() != requested {
                            return Err(EngineError::invalid_argument(
                                "canonical fragment is missing its search projection",
                            ));
                        }
                        let docs =
                            select_fragment_docs(encoder.prepare(&batch)?, incremental.as_ref())?;
                        state.mutation_stats.fragments_written = state
                            .mutation_stats
                            .fragments_written
                            .saturating_add(u64::try_from(docs.len()).unwrap_or(u64::MAX));
                        state.fragments.write(&docs)?;
                        #[cfg(test)]
                        if self
                            .fail_next_staged_publish_after_projection
                            .swap(false, Ordering::AcqRel)
                        {
                            return Err(EngineError::storage_failure(
                                "injected staged publish failure after projection batch",
                            ));
                        }
                        remaining -= batch.len();
                    }
                    encoder.finish()?;
                }
                Ok(())
            })?;
            apply_projections.finish()?;
            if let Some(mut manifest) = new_manifest {
                if !state.manifests.publish(&mut manifest)? {
                    state.manifests.delete(file.id)?;
                }
            } else {
                state.manifests.delete(file.id)?;
            }

            let result = state.files.put(&file, &directories);
            if result.is_ok() {
                state.mutation_stats.files_replaced =
                    state.mutation_stats.files_replaced.saturating_add(1);
            }
            result
        });
        let _ = staged.discard();
        result
    }

    /// Update only the persisted source snapshot after content was revalidated.
    ///
    /// This deliberately does not touch entities, fragments, vectors, or the
    /// optimize marker. A same-content rewrite only needs its metadata brought
    /// in line with the scanner so the next reconciliation can skip the read.
    pub(crate) fn update_file_snapshot(&self, file: &FileRecord) -> EngineResult<()> {
        file.validate()?;
        self.write(|state| {
            state.files.validate(file)?;
            let directories = state.directories.ensure(&file.relative_path)?;
            let result = state.files.put(file, &directories);
            if result.is_ok() {
                state.mutation_stats.files_snapshot_updated = state
                    .mutation_stats
                    .files_snapshot_updated
                    .saturating_add(1);
            }
            result
        })
    }

    pub(crate) fn mark_file_failed(&self, file: &FileRecord, error: &str) -> EngineResult<()> {
        file.validate()?;
        let mut file = file.clone();
        file.index_status = FileIndexStatus::Failed {
            error: error.to_owned(),
        };
        file.validate()?;
        let shared = self.shared()?;
        self.write(|state| {
            state.files.validate(&file)?;
            // Persistent read/extraction failures are retried by reconciliation.
            // Rewriting an identical terminal record would delete projections and
            // append two file rows again, filling another native segment per run.
            // Keep retries and changed failures intact, but make this write idempotent.
            if state.files.fetch(&[file.id])?.get(&file.id) == Some(&file) {
                tracing::debug!(file_id = %file.id, "unchanged file failure write skipped");
                return Ok(());
            }
            replace_file(state, &file, &[], &[], &shared.storage_path)
        })
    }

    pub(crate) fn delete_file(&self, file_id: FileId) -> EngineResult<()> {
        self.delete_files(std::slice::from_ref(&file_id))
    }

    pub(crate) fn delete_files(&self, file_ids: &[FileId]) -> EngineResult<()> {
        if file_ids.is_empty() {
            return Ok(());
        }
        let shared = self.shared()?;
        // Take the mutation gate before writing the durable marker so a
        // checkpoint cannot clear it while the delete waits to mutate state.
        let _mutation = shared
            .mutation_gate
            .lock()
            .map_err(|_| EngineError::internal("storage mutation gate was poisoned"))?;
        // Durably record the deletion intent BEFORE any mutation: if this fails
        // the delete fails (fail-safe), so a crash can never persist tombstones
        // without a marker or silently lose the pending maintenance.
        persist_delete_marker(&shared.storage_path, true)?;

        #[cfg(test)]
        let _stage = StateLockStage::enter("delete_files");
        self.write_locked(&shared, |state| {
            // Vector indexes retain deleted entries until native optimize removes
            // their tombstones from the HNSW search path.
            state.mark_finalize(FinalizeState::ENTITIES);
            state.mark_finalize(FinalizeState::FILES);
            state.mark_fragments_delete();
            // Preserve resumable deletion state before removing projections.
            for file_id in file_ids {
                state.files.mark_deleting(*file_id)?;
            }
            state.fragments.delete_files(file_ids)?;
            state.entities.delete_files(file_ids)?;
            for file_id in file_ids {
                state.manifests.delete(*file_id)?;
            }
            let result = state.files.delete_many(file_ids);
            if result.is_ok() {
                state.mutation_stats.files_deleted = state
                    .mutation_stats
                    .files_deleted
                    .saturating_add(u64::try_from(file_ids.len()).unwrap_or(u64::MAX));
            }
            result
        })
    }

    /// Flush accepted writes, including any unfinished file's retry state.
    #[cfg(test)]
    pub(crate) fn checkpoint_only(&self) -> EngineResult<()> {
        self.checkpoint()
    }

    #[cfg(test)]
    pub(crate) fn pause_next_fragment_checkpoint_for_test(
        &self,
    ) -> EngineResult<(std::sync::mpsc::Receiver<()>, std::sync::mpsc::Sender<()>)> {
        let shared = self.shared()?;
        let (started_tx, started_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let mut pause = shared
            .checkpoint_fragment_flush_pause
            .lock()
            .map_err(|_| EngineError::internal("checkpoint test hook was poisoned"))?;
        if pause.is_some() {
            return Err(EngineError::internal(
                "fragment checkpoint test hook is already installed",
            ));
        }
        *pause = Some((started_tx, release_rx));
        Ok((started_rx, release_tx))
    }

    pub(crate) fn checkpoint(&self) -> EngineResult<()> {
        if self.read_only {
            return Ok(());
        }
        let shared = self.shared()?;
        // Serialize flush against optimize/close and every mutation. The native
        // Collection API is internally thread-safe, so queries may use cloned
        // fragment handles while their durable flush runs outside `state`.
        let _maintenance = shared
            .maintenance
            .lock()
            .map_err(|_| EngineError::internal("maintenance lease was poisoned"))?;
        let _mutation = shared
            .mutation_gate
            .lock()
            .map_err(|_| EngineError::internal("storage mutation gate was poisoned"))?;
        #[cfg(test)]
        let _stage = StateLockStage::enter("checkpoint");

        let fragment_handles = {
            #[cfg(test)]
            let lock_started = Instant::now();
            let state = lock_state(&shared)?;
            #[cfg(test)]
            let acquired = Instant::now();
            #[cfg(test)]
            {
                record_state_lock_wait_for_test(
                    u64::try_from(acquired.duration_since(lock_started).as_nanos())
                        .unwrap_or(u64::MAX),
                );
                record_state_lock_span("write_wait", lock_started, acquired);
            }
            assert_usable(&state)?;
            // Persist the deletion intent BEFORE flushing tombstone state.
            checkpoint_step("checkpoint_delete_marker", || {
                persist_delete_marker(&shared.storage_path, state.fragments_delete_pending)
            })?;
            checkpoint_step("checkpoint_flush_directories", || state.directories.flush())?;
            checkpoint_step("checkpoint_flush_entities", || state.entities.flush())?;
            let handles = state.fragments.handles();
            #[cfg(test)]
            {
                drop(state);
                record_state_lock_span("write_hold", acquired, Instant::now());
            }
            #[cfg(not(test))]
            drop(state);
            handles
        };

        checkpoint_step("checkpoint_flush_fragments", || {
            #[cfg(test)]
            pause_checkpoint_fragment_flush_for_test(&shared)?;
            for (_, collection) in &fragment_handles {
                native(collection.flush(), "flush fragment collection")?;
            }
            Ok(())
        })?;

        #[cfg(test)]
        let lock_started = Instant::now();
        let state = lock_state(&shared)?;
        #[cfg(test)]
        let acquired = Instant::now();
        #[cfg(test)]
        {
            record_state_lock_wait_for_test(
                u64::try_from(acquired.duration_since(lock_started).as_nanos()).unwrap_or(u64::MAX),
            );
            record_state_lock_span("write_wait", lock_started, acquired);
        }
        assert_usable(&state)?;
        let result = checkpoint_step("checkpoint_flush_files", || state.files.flush());
        #[cfg(test)]
        {
            drop(state);
            record_state_lock_span("write_hold", acquired, Instant::now());
        }
        result
    }

    /// Persist retry state, run native optimize for the snapshot of pending debt
    /// OUTSIDE the state lock so search and writers stay available, then
    /// acknowledge only the exact debt that was optimized.
    pub(crate) fn finalize_writes(&self) -> EngineResult<()> {
        #[cfg(feature = "native-profile")]
        let finalization_profile_started = std::env::var("ZVEC_GREP_NATIVE_PROFILE")
            .is_ok_and(|value| value == "1")
            .then(Instant::now);
        let checkpoint_io_before = io_write_bytes();
        let checkpoint_started = Instant::now();
        self.checkpoint()?;
        let checkpoint_micros = elapsed_micros(checkpoint_started);
        let checkpoint_write_bytes = io_write_bytes().saturating_sub(checkpoint_io_before);
        if self.read_only {
            return Ok(());
        }
        let shared = self.shared()?;

        // Phase A: snapshot debt and check out the collection handles under the
        // state lock, then release it before any native optimize runs.
        let snapshot = {
            let state = lock_state(&shared)?;
            if state.closed {
                return Err(EngineError::resource_closed("workspace storage is closed"));
            }
            let finalize = state.finalize;
            let probe_started = Instant::now();
            let fragments_incomplete = state.fragments.needs_optimize()?;
            let fragments_completeness_before =
                state.fragments.completeness_summary().unwrap_or(f32::NAN);
            let optimize_probe_micros = elapsed_micros(probe_started);
            let mutation_stats = state.mutation_stats;

            // An incomplete vector index needs finalization, and a deletion
            // forces it because tombstones leave the HNSW search path only after
            // optimize even when completeness reads 1.0. Insertion-only debt is
            // bounded-deferred: maintenance starts at a mutation threshold, an
            // age deadline, or a hard safety bound (never unbounded).
            let policy = self.maintenance_policy();
            let finalize_fragments_flag = finalize.contains(FinalizeState::FRAGMENTS);
            let fragments_delete = state.fragments_delete_pending;
            let wants_fragments =
                finalize_fragments_flag || fragments_incomplete || fragments_delete;
            let debt = state
                .fragments_debt
                .saturating_sub(state.fragments_ack_epoch);
            // Best-effort maximum age: evaluated only when finalize_writes runs,
            // i.e. at the next maintenance opportunity, NOT a real-time timer.
            // A true hard deadline needs an independent maintenance owner.
            let aged = state.pending_since_ms != 0
                && wall_clock_ms().saturating_sub(state.pending_since_ms) >= policy.max_age_ms;
            let run_fragments = wants_fragments
                && (fragments_delete || policy.threshold == 0 || debt >= policy.threshold || aged);
            let deferred = wants_fragments && !run_fragments;
            let fragments_optimize_reason = match (
                finalize_fragments_flag,
                fragments_incomplete,
                fragments_delete,
            ) {
                (true, true, _) => "both",
                (true, false, true) => "finalize_flag+delete",
                (true, false, false) => "finalize_flag",
                (false, true, true) => "incomplete+delete",
                (false, true, false) => "incomplete",
                (false, false, true) => "delete",
                (false, false, false) => "none",
            };
            let defer_reason = if !deferred {
                "none"
            } else if aged {
                "age"
            } else {
                "debt"
            };

            let optimize_directories = !deferred && finalize.contains(FinalizeState::DIRECTORIES);
            let optimize_entities = !deferred && finalize.contains(FinalizeState::ENTITIES);
            let optimize_files = !deferred && finalize.contains(FinalizeState::FILES);
            let optimize_fragments = !deferred && run_fragments;
            let snapshot_epoch = state.mutation_epoch;
            let snapshot_fragments_debt = state.fragments_debt;

            let directories_handle = optimize_directories.then(|| state.directories.handle());
            let entities_handle = optimize_entities.then(|| state.entities.handle());
            let files_handle = optimize_files.then(|| state.files.handle());
            let fragment_handles = if optimize_fragments {
                state.fragments.handles()
            } else {
                Vec::new()
            };

            (
                optimize_directories,
                optimize_entities,
                optimize_files,
                optimize_fragments,
                finalize_fragments_flag,
                fragments_incomplete,
                fragments_optimize_reason,
                fragments_completeness_before,
                snapshot_epoch,
                snapshot_fragments_debt,
                optimize_probe_micros,
                mutation_stats,
                directories_handle,
                entities_handle,
                files_handle,
                fragment_handles,
                deferred,
                defer_reason,
                policy,
            )
        };
        let (
            optimize_directories,
            optimize_entities,
            optimize_files,
            optimize_fragments,
            finalize_fragments_flag,
            fragments_incomplete,
            fragments_optimize_reason,
            fragments_completeness_before,
            snapshot_epoch,
            snapshot_fragments_debt,
            optimize_probe_micros,
            mutation_stats,
            directories_handle,
            entities_handle,
            files_handle,
            fragment_handles,
            deferred,
            defer_reason,
            policy,
        ) = snapshot;

        // Phase B: native optimize WITHOUT the state lock. Search and writers
        // proceed concurrently (native contract: optimize phase 2 is lock-free;
        // verified by Gate H). The maintenance lease keeps close/retire from
        // closing a native collection while we are optimizing it.
        let mut optimize_error: Option<EngineError> = None;
        let mut directories_optimize_micros = 0;
        let mut entities_optimize_micros = 0;
        let mut files_optimize_micros = 0;
        let mut fragments_optimize_micros = 0;
        let mut directories_optimize_write_bytes = 0;
        let mut entities_optimize_write_bytes = 0;
        let mut files_optimize_write_bytes = 0;
        let mut fragments_optimize_write_bytes = 0;
        #[cfg(test)]
        if self.fail_next_optimize.swap(false, Ordering::SeqCst) {
            optimize_error = Some(EngineError::storage_failure(
                "injected optimize failure (test)",
            ));
        }
        if optimize_error.is_none()
            && (optimize_directories || optimize_entities || optimize_files || optimize_fragments)
        {
            let _lease = shared
                .maintenance
                .lock()
                .map_err(|_| EngineError::internal("maintenance lease was poisoned"))?;
            #[cfg(test)]
            shared.maintenance_started.store(true, Ordering::SeqCst);
            if let Some(handle) = &directories_handle {
                let io_before = io_write_bytes();
                let started = Instant::now();
                if let Err(error) = native(handle.optimize(), "optimize directories") {
                    optimize_error = Some(error);
                }
                directories_optimize_micros = elapsed_micros(started);
                directories_optimize_write_bytes = io_write_bytes().saturating_sub(io_before);
            }
            if optimize_error.is_none()
                && let Some(handle) = &entities_handle
            {
                let io_before = io_write_bytes();
                let started = Instant::now();
                if let Err(error) = native(handle.optimize(), "optimize entities") {
                    optimize_error = Some(error);
                }
                entities_optimize_micros = elapsed_micros(started);
                entities_optimize_write_bytes = io_write_bytes().saturating_sub(io_before);
            }
            if optimize_error.is_none()
                && let Some(handle) = &files_handle
            {
                let io_before = io_write_bytes();
                let started = Instant::now();
                if let Err(error) = native(handle.optimize(), "optimize source files") {
                    optimize_error = Some(error);
                }
                files_optimize_micros = elapsed_micros(started);
                files_optimize_write_bytes = io_write_bytes().saturating_sub(io_before);
            }
            if optimize_error.is_none() {
                let io_before = io_write_bytes();
                for (_, handle) in &fragment_handles {
                    let started = Instant::now();
                    match native(handle.optimize(), "optimize fragment collection") {
                        Ok(()) => {}
                        Err(error) => {
                            optimize_error = Some(error);
                            break;
                        }
                    }
                    fragments_optimize_micros += elapsed_micros(started);
                }
                fragments_optimize_write_bytes = io_write_bytes().saturating_sub(io_before);
            }
            #[cfg(test)]
            shared.maintenance_started.store(false, Ordering::SeqCst);
        }

        let fragments_completeness_after_optimize = if fragment_handles.is_empty() {
            fragments_completeness_before
        } else {
            Fragments::handles_completeness(&fragment_handles).unwrap_or(f32::NAN)
        };

        // Phase C: acknowledge only the exact debt that was optimized. New
        // mutations during Phase B advanced `mutation_epoch`; a failed or
        // incomplete optimize keeps the flags for the next round.
        let acknowledged;
        #[cfg(feature = "native-profile")]
        let epoch_unchanged_for_profile;
        {
            let _mutation = shared
                .mutation_gate
                .lock()
                .map_err(|_| EngineError::internal("storage mutation gate was poisoned"))?;
            let mut state = lock_state(&shared)?;
            let epoch_unchanged = state.mutation_epoch == snapshot_epoch;
            #[cfg(feature = "native-profile")]
            {
                epoch_unchanged_for_profile = epoch_unchanged;
            }
            let debt = state
                .fragments_debt
                .saturating_sub(state.fragments_ack_epoch);
            #[cfg(test)]
            self.last_finalize_saw_closed
                .store(state.closed, Ordering::SeqCst);
            if state.closed {
                // The store was closed while we were optimizing: do not mutate
                // debt or the marker after close. close() already flushed.
                acknowledged = false;
            } else if deferred {
                // Keep the debt and (re)start the age deadline; never clear flags.
                if state.pending_since_ms == 0 {
                    state.pending_since_ms = wall_clock_ms();
                }
                acknowledged = false;
            } else {
                let optimize_succeeded = optimize_error.is_none();
                let fragments_clean =
                    !optimize_fragments || fragments_completeness_after_optimize >= 1.0;
                acknowledged = optimize_succeeded && epoch_unchanged && fragments_clean;
                if acknowledged {
                    state.finalize.clear();
                    state.fragments_ack_epoch = snapshot_fragments_debt;
                    state.pending_since_ms = 0;
                    state.fragments_delete_pending = false;
                    let _ = persist_delete_marker(&shared.storage_path, false);
                } else if state.pending_since_ms == 0 {
                    state.pending_since_ms = wall_clock_ms();
                }
            }
            state.mutation_stats = MutationStats::default();

            #[cfg(test)]
            if !deferred && optimize_error.is_none() {
                state.optimize_calls.directories += u64::from(optimize_directories);
                state.optimize_calls.entities += u64::from(optimize_entities);
                state.optimize_calls.files += u64::from(optimize_files);
                state.optimize_calls.fragments += u64::from(optimize_fragments);
            }

            if deferred {
                tracing::info!(
                    checkpoint_micros,
                    checkpoint_write_bytes,
                    optimize_probe_micros,
                    debt,
                    threshold = policy.threshold,
                    max_age_ms = policy.max_age_ms,
                    defer_reason,
                    fragments_incomplete,
                    finalize_fragments_flag,
                    fragments_optimize_reason,
                    fragments_completeness_before,
                    entities_written = mutation_stats.entities_written,
                    fragments_written = mutation_stats.fragments_written,
                    entities_deleted = mutation_stats.entities_deleted,
                    fragments_deleted = mutation_stats.fragments_deleted,
                    "index storage finalization deferred native optimize"
                );
            } else if optimize_directories
                || optimize_entities
                || optimize_files
                || optimize_fragments
            {
                tracing::info!(
                    checkpoint_micros,
                    checkpoint_write_bytes,
                    optimize_probe_micros,
                    directories_optimize_micros,
                    entities_optimize_micros,
                    files_optimize_micros,
                    fragments_optimize_micros,
                    directories_optimize_write_bytes,
                    entities_optimize_write_bytes,
                    files_optimize_write_bytes,
                    fragments_optimize_write_bytes,
                    files_replaced = mutation_stats.files_replaced,
                    files_deleted = mutation_stats.files_deleted,
                    files_snapshot_updated = mutation_stats.files_snapshot_updated,
                    entities_written = mutation_stats.entities_written,
                    fragments_written = mutation_stats.fragments_written,
                    entities_deleted = mutation_stats.entities_deleted,
                    fragments_deleted = mutation_stats.fragments_deleted,
                    optimize_directories,
                    optimize_entities,
                    optimize_files,
                    optimize_fragments,
                    fragments_incomplete,
                    finalize_fragments_flag,
                    fragments_optimize_reason,
                    fragments_completeness_before,
                    fragments_completeness_after_optimize,
                    acknowledged,
                    epoch_unchanged,
                    "index storage finalization"
                );
            } else {
                tracing::info!(
                    checkpoint_micros,
                    checkpoint_write_bytes,
                    optimize_probe_micros,
                    files_replaced = mutation_stats.files_replaced,
                    files_deleted = mutation_stats.files_deleted,
                    files_snapshot_updated = mutation_stats.files_snapshot_updated,
                    entities_written = mutation_stats.entities_written,
                    fragments_written = mutation_stats.fragments_written,
                    entities_deleted = mutation_stats.entities_deleted,
                    fragments_deleted = mutation_stats.fragments_deleted,
                    fragments_incomplete,
                    finalize_fragments_flag,
                    fragments_optimize_reason,
                    fragments_completeness_before,
                    "index storage finalization skipped native optimize"
                );
            }
        }

        #[cfg(feature = "native-profile")]
        if let Some(finalization_profile_started) = finalization_profile_started {
            eprintln!(
                "NATIVE_PROFILE event=store_finalize elapsed_micros={} checkpoint_micros={} checkpoint_write_bytes={} optimize_probe_micros={} directories_optimize_micros={} entities_optimize_micros={} files_optimize_micros={} fragments_optimize_micros={} directories_optimize_write_bytes={} entities_optimize_write_bytes={} files_optimize_write_bytes={} fragments_optimize_write_bytes={} files_replaced={} files_deleted={} files_snapshot_updated={} entities_written={} fragments_written={} entities_deleted={} fragments_deleted={} optimize_directories={} optimize_entities={} optimize_files={} optimize_fragments={} deferred={} defer_reason={} maintenance_threshold={} maintenance_max_age_ms={} fragments_incomplete={} finalize_fragments_flag={} fragments_optimize_reason={} fragments_completeness_before={:?} fragments_completeness_after={:?} acknowledged={} epoch_unchanged={} optimize_error_code={:?}",
                elapsed_micros(finalization_profile_started),
                checkpoint_micros,
                checkpoint_write_bytes,
                optimize_probe_micros,
                directories_optimize_micros,
                entities_optimize_micros,
                files_optimize_micros,
                fragments_optimize_micros,
                directories_optimize_write_bytes,
                entities_optimize_write_bytes,
                files_optimize_write_bytes,
                fragments_optimize_write_bytes,
                mutation_stats.files_replaced,
                mutation_stats.files_deleted,
                mutation_stats.files_snapshot_updated,
                mutation_stats.entities_written,
                mutation_stats.fragments_written,
                mutation_stats.entities_deleted,
                mutation_stats.fragments_deleted,
                optimize_directories,
                optimize_entities,
                optimize_files,
                optimize_fragments,
                deferred,
                defer_reason,
                policy.threshold,
                policy.max_age_ms,
                fragments_incomplete,
                finalize_fragments_flag,
                fragments_optimize_reason,
                fragments_completeness_before,
                fragments_completeness_after_optimize,
                acknowledged,
                epoch_unchanged_for_profile,
                optimize_error.as_ref().map(|error| error.code()),
            );
        }

        if let Some(error) = optimize_error {
            return Err(error);
        }
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn fragment_debt_for_test(&self) -> EngineResult<u64> {
        let shared = self.shared()?;
        let state = lock_state(&shared)?;
        Ok(state
            .fragments_debt
            .saturating_sub(state.fragments_ack_epoch))
    }

    #[cfg(test)]
    pub(crate) fn mutation_stats_for_test(
        &self,
    ) -> EngineResult<(u64, u64, u64, u64, u64, u64, u64)> {
        let shared = self.shared()?;
        let state = lock_state(&shared)?;
        let m = state.mutation_stats;
        Ok((
            m.files_replaced,
            m.files_deleted,
            m.files_snapshot_updated,
            m.entities_written,
            m.fragments_written,
            m.entities_deleted,
            m.fragments_deleted,
        ))
    }

    #[cfg(test)]
    pub(crate) fn finalize_debt_for_test(&self) -> EngineResult<(bool, u64)> {
        let shared = self.shared()?;
        let state = lock_state(&shared)?;
        let pending = state.finalize.contains(FinalizeState::DIRECTORIES)
            || state.finalize.contains(FinalizeState::ENTITIES)
            || state.finalize.contains(FinalizeState::FILES)
            || state.finalize.contains(FinalizeState::FRAGMENTS)
            || state.fragments_delete_pending
            || state.fragments.needs_optimize()?;
        Ok((pending, state.mutation_epoch))
    }

    #[cfg(test)]
    pub(crate) fn set_fail_next_optimize_for_test(&self) {
        self.fail_next_optimize.store(true, Ordering::SeqCst);
    }

    #[cfg(test)]
    pub(crate) fn set_maintenance_policy_for_test(&self, threshold: u64, max_age_ms: u64) {
        self.maintenance_threshold_override
            .store(threshold, Ordering::SeqCst);
        self.maintenance_max_age_override
            .store(max_age_ms, Ordering::SeqCst);
    }

    fn maintenance_policy(&self) -> MaintenancePolicy {
        #[cfg(test)]
        {
            let threshold = self.maintenance_threshold_override.load(Ordering::SeqCst);
            let max_age = self.maintenance_max_age_override.load(Ordering::SeqCst);
            if threshold != u64::MAX || max_age != u64::MAX {
                let threshold = if threshold != u64::MAX {
                    threshold
                } else {
                    DEFAULT_MAINTENANCE_THRESHOLD
                };
                let max_age_ms = if max_age != u64::MAX {
                    max_age
                } else {
                    DEFAULT_MAINTENANCE_MAX_AGE_MS
                };
                return MaintenancePolicy {
                    threshold,
                    max_age_ms,
                };
            }
        }
        MaintenancePolicy::from_env()
    }

    pub(crate) fn close(&self) -> EngineResult<()> {
        // Take (and release) the lease so concurrent closes serialize, but do
        // NOT hold the lease while waiting for native maintenance.
        let shared = {
            let mut lease = self
                .shared
                .lock()
                .map_err(|_| EngineError::internal("storage lease lock was poisoned"))?;
            lease.take()
        };
        let Some(shared) = shared else {
            return Ok(());
        };
        if self.read_only {
            return Ok(());
        }
        // Wait for any in-flight native maintenance before closing collections.
        // `try_lock` records deterministically whether we actually had to wait.
        let _maintenance = match shared.maintenance.try_lock() {
            Ok(guard) => guard,
            Err(std::sync::TryLockError::WouldBlock) => {
                #[cfg(test)]
                self.close_waited_for_maintenance
                    .store(true, Ordering::SeqCst);
                shared
                    .maintenance
                    .lock()
                    .map_err(|_| EngineError::internal("maintenance lease was poisoned"))?
            }
            Err(std::sync::TryLockError::Poisoned(_)) => {
                return Err(EngineError::internal("maintenance lease was poisoned"));
            }
        };
        let _mutation = shared
            .mutation_gate
            .lock()
            .map_err(|_| EngineError::internal("storage mutation gate was poisoned"))?;
        let mut state = lock_state(&shared)?;
        // Reject a write that acquired its Arc before close but is still waiting
        // for this lock. A successful close commits every accepted write.
        state.closed = true;
        flush(&state)
    }

    #[cfg(test)]
    pub(crate) fn maintenance_started_for_test(&self) -> bool {
        self.shared()
            .map(|shared| shared.maintenance_started.load(Ordering::SeqCst))
            .unwrap_or(false)
    }

    #[cfg(test)]
    pub(crate) fn close_waited_for_maintenance_for_test(&self) -> bool {
        self.close_waited_for_maintenance.load(Ordering::SeqCst)
    }

    #[cfg(test)]
    pub(crate) fn last_finalize_saw_closed_for_test(&self) -> bool {
        self.last_finalize_saw_closed.load(Ordering::SeqCst)
    }
}

fn load_indexed_manifest(
    state: &StoreState,
    file_id: FileId,
) -> EngineResult<(bool, Option<FileManifest>)> {
    let Some(file) = state.files.fetch(&[file_id])?.remove(&file_id) else {
        return Ok((false, None));
    };
    let FileIndexStatus::Indexed { entity_count, .. } = file.index_status else {
        return Ok((true, None));
    };
    let Some(manifest) = state.manifests.load(file_id)? else {
        return Ok((true, None));
    };
    if manifest.entities.len() != entity_count as usize
        || manifest
            .fragments
            .iter()
            .any(|entry| !state.fragments.supports_model(&entry.model))
    {
        return Ok((true, None));
    }
    Ok((true, Some(manifest)))
}

fn persist_replacement_delete_intent(
    state: &mut StoreState,
    storage_path: &std::path::Path,
    had_file_record: bool,
    incremental: Option<&IncrementalPlan>,
) -> EngineResult<()> {
    let deletes_fragments =
        incremental.map_or(had_file_record, |plan| plan.has_fragment_deletions());
    if deletes_fragments {
        // PK deletes and full replacements leave native tombstones too. Persist
        // before dropping the manifest or mutating rows so reopen cannot lose
        // the force-maintenance intent when completeness remains 1.0.
        persist_delete_marker(storage_path, true)?;
        state.fragments_delete_pending = true;
    }
    Ok(())
}

// Cross-table writes stay under the store's single writer lock.
fn replace_file(
    state: &mut StoreState,
    file: &FileRecord,
    entities: &[Entity],
    entries: &[IndexedFragment],
    storage_path: &std::path::Path,
) -> EngineResult<()> {
    state.files.validate(file)?;
    state.entities.validate_ownership(entities, file.id)?;
    state.fragments.validate_ownership(entries, file.id)?;
    let directories = state.directories.ensure(&file.relative_path)?;
    if state.directories.took_write() {
        state.mark_finalize(FinalizeState::DIRECTORIES);
    }
    state.mark_finalize(FinalizeState::FILES);
    // Encode every projection before changing the previous indexed generation.
    let entity_docs = Entities::prepare(entities)?;
    let fragment_docs = Fragments::prepare(file, entities, entries, &directories)?;
    let new_manifest = build_file_manifest(file, entities, entries, &directories)?;
    let (had_file_record, old_manifest) = load_indexed_manifest(state, file.id)?;
    let incremental = match (&old_manifest, &new_manifest) {
        (Some(old), Some(new)) => Some(build_incremental_plan(old, new)),
        _ => None,
    };
    persist_replacement_delete_intent(state, storage_path, had_file_record, incremental.as_ref())?;

    // Fail closed: drop the previous published snapshot before the first row
    // mutation so an interrupted apply cannot expose a spliced generation.
    state.manifests.delete(file.id)?;
    let mut unfinished = file.clone();
    unfinished.index_status = FileIndexStatus::NotIndexed;
    state.files.put(&unfinished, &directories)?;

    match (incremental, new_manifest) {
        (Some(plan), Some(mut manifest)) => {
            if plan.has_entity_changes() {
                state.mark_finalize(FinalizeState::ENTITIES);
            }
            if plan.has_fragment_deletions() {
                state.mark_finalize(FinalizeState::FRAGMENTS);
            }
            for (model, fragment_ids) in &plan.delete_fragments {
                state.fragments.delete_fragment_ids(model, fragment_ids)?;
            }
            state.entities.delete_ids(&plan.delete_entities)?;
            state.mutation_stats.fragments_deleted =
                state.mutation_stats.fragments_deleted.saturating_add(
                    plan.delete_fragments
                        .values()
                        .map(|ids| u64::try_from(ids.len()).unwrap_or(u64::MAX))
                        .sum(),
                );
            state.mutation_stats.entities_deleted = state
                .mutation_stats
                .entities_deleted
                .saturating_add(u64::try_from(plan.delete_entities.len()).unwrap_or(u64::MAX));
            let mut entity_writes = Vec::new();
            for doc in entity_docs {
                if plan.entity_doc_keys.contains(super::zvec::doc_key(&doc)?) {
                    entity_writes.push(doc);
                }
            }
            let mut fragment_writes = BTreeMap::new();
            for (model, docs) in fragment_docs {
                let mut selected = Vec::new();
                for doc in docs {
                    if plan
                        .fragment_doc_keys
                        .contains(&(model.clone(), super::zvec::doc_key(&doc)?.to_owned()))
                    {
                        selected.push(doc);
                    }
                }
                if !selected.is_empty() {
                    fragment_writes.insert(model, selected);
                }
            }
            state.entities.write(&entity_writes)?;
            state.fragments.write(&fragment_writes)?;
            state.mutation_stats.entities_written = state
                .mutation_stats
                .entities_written
                .saturating_add(u64::try_from(entity_writes.len()).unwrap_or(u64::MAX));
            state.mutation_stats.fragments_written =
                state.mutation_stats.fragments_written.saturating_add(
                    fragment_writes
                        .values()
                        .map(|docs| u64::try_from(docs.len()).unwrap_or(u64::MAX))
                        .sum(),
                );
            if !state.manifests.publish(&mut manifest)? {
                state.manifests.delete(file.id)?;
            }
        }
        (_, manifest) => {
            // A full replacement deletes and rewrites every projection.
            state.mark_finalize(FinalizeState::ENTITIES);
            state.mark_finalize(FinalizeState::FRAGMENTS);
            state.fragments.delete_file(file.id)?;
            state.entities.delete_file(file.id)?;
            state.entities.write(&entity_docs)?;
            state.fragments.write(&fragment_docs)?;
            state.mutation_stats.entities_written = state
                .mutation_stats
                .entities_written
                .saturating_add(u64::try_from(entity_docs.len()).unwrap_or(u64::MAX));
            state.mutation_stats.fragments_written =
                state.mutation_stats.fragments_written.saturating_add(
                    fragment_docs
                        .values()
                        .map(|docs| u64::try_from(docs.len()).unwrap_or(u64::MAX))
                        .sum(),
                );
            if let Some(mut manifest) = manifest {
                if !state.manifests.publish(&mut manifest)? {
                    state.manifests.delete(file.id)?;
                }
            } else {
                state.manifests.delete(file.id)?;
            }
        }
    }

    let result = state.files.put(file, &directories);
    if result.is_ok() {
        state.mutation_stats.files_replaced = state.mutation_stats.files_replaced.saturating_add(1);
    }
    result
}

struct IncrementalPlan {
    entity_doc_keys: HashSet<String>,
    fragment_doc_keys: HashSet<(String, String)>,
    delete_entities: Vec<EntityId>,
    delete_fragments: HashMap<String, Vec<FragmentId>>,
}

impl IncrementalPlan {
    /// Canonical entity rows whose stored payload or metadata changed.
    fn has_entity_changes(&self) -> bool {
        !self.entity_doc_keys.is_empty() || !self.delete_entities.is_empty()
    }

    /// Fragment rows removed by this replacement; tombstones need an optimize.
    fn has_fragment_deletions(&self) -> bool {
        !self.delete_fragments.is_empty()
    }
}

fn select_entity_docs(
    docs: Vec<zvec_rust::Doc>,
    plan: Option<&IncrementalPlan>,
) -> EngineResult<Vec<zvec_rust::Doc>> {
    let Some(plan) = plan else {
        return Ok(docs);
    };
    let mut selected = Vec::new();
    for doc in docs {
        if plan.entity_doc_keys.contains(super::zvec::doc_key(&doc)?) {
            selected.push(doc);
        }
    }
    Ok(selected)
}

fn select_fragment_docs(
    docs: BTreeMap<String, Vec<zvec_rust::Doc>>,
    plan: Option<&IncrementalPlan>,
) -> EngineResult<BTreeMap<String, Vec<zvec_rust::Doc>>> {
    let Some(plan) = plan else {
        return Ok(docs);
    };
    let mut selected_by_model = BTreeMap::new();
    for (model, docs) in docs {
        let mut selected = Vec::new();
        for doc in docs {
            if plan
                .fragment_doc_keys
                .contains(&(model.clone(), super::zvec::doc_key(&doc)?.to_owned()))
            {
                selected.push(doc);
            }
        }
        if !selected.is_empty() {
            selected_by_model.insert(model, selected);
        }
    }
    Ok(selected_by_model)
}

fn build_incremental_plan(old: &FileManifest, new: &FileManifest) -> IncrementalPlan {
    let old_entities = old
        .entities
        .iter()
        .map(|entry| (entry.entity_id.as_str(), entry.fingerprint.as_str()))
        .collect::<HashMap<_, _>>();
    let new_entities = new
        .entities
        .iter()
        .map(|entry| (entry.entity_id.as_str(), entry.fingerprint.as_str()))
        .collect::<HashMap<_, _>>();
    let entity_doc_keys = new
        .entities
        .iter()
        .filter(|entry| {
            old_entities.get(entry.entity_id.as_str()) != Some(&entry.fingerprint.as_str())
        })
        .map(|entry| entry.entity_id.clone())
        .collect::<HashSet<_>>();
    let delete_entities = old
        .entities
        .iter()
        .filter(|entry| !new_entities.contains_key(entry.entity_id.as_str()))
        .map(|entry| EntityId::from_string(entry.entity_id.clone()))
        .collect::<Vec<_>>();

    let old_fragments = old
        .fragments
        .iter()
        .map(|entry| (entry.fragment_id.as_str(), entry))
        .collect::<HashMap<_, _>>();
    let new_fragments = new
        .fragments
        .iter()
        .map(|entry| (entry.fragment_id.as_str(), entry))
        .collect::<HashMap<_, _>>();
    let mut delete_fragments = HashMap::<String, Vec<FragmentId>>::new();
    for entry in &old.fragments {
        let keep_old_projection = new_fragments
            .get(entry.fragment_id.as_str())
            .is_some_and(|new_entry| new_entry.model == entry.model);
        if !keep_old_projection {
            delete_fragments
                .entry(entry.model.clone())
                .or_default()
                .push(FragmentId::from_string(entry.fragment_id.clone()));
        }
    }
    let fragment_doc_keys = new
        .fragments
        .iter()
        .filter(|entry| {
            !old_fragments
                .get(entry.fragment_id.as_str())
                .is_some_and(|old_entry| {
                    old_entry.model == entry.model
                        && old_entry.embedding_fingerprint == entry.embedding_fingerprint
                        && old_entry.projection_fingerprint == entry.projection_fingerprint
                })
        })
        .map(|entry| (entry.model.clone(), entry.fragment_id.clone()))
        .collect::<HashSet<_>>();

    IncrementalPlan {
        entity_doc_keys,
        fragment_doc_keys,
        delete_entities,
        delete_fragments,
    }
}

fn build_file_manifest(
    file: &FileRecord,
    entities: &[Entity],
    entries: &[IndexedFragment],
    directories: &[crate::domain::DirectoryId],
) -> EngineResult<Option<FileManifest>> {
    if entries
        .iter()
        .any(|entry| entry.embedding_fingerprint.is_none())
    {
        return Ok(None);
    }
    let mut manifest = FileManifest::new(file.id);
    let mut estimated_bytes = 16_usize;
    for entity in entities {
        let fingerprint = entity_manifest_fingerprint(entity)?;
        estimated_bytes = estimated_bytes.saturating_add(48);
        if estimated_bytes > MAX_FILE_MANIFEST_BYTES {
            return Ok(None);
        }
        manifest.entities.push(EntityManifestEntry {
            entity_id: entity.id.as_str().to_owned(),
            fingerprint,
        });
    }

    let directory_ids = directories.iter().map(|id| id.get()).collect::<Vec<_>>();
    let path_bytes = file.relative_path.as_path().as_os_str().as_encoded_bytes();
    let entities_by_id = entities
        .iter()
        .map(|entity| (entity.id.as_str(), entity))
        .collect::<HashMap<_, _>>();
    for entry in entries {
        let Some(entity) = entities_by_id.get(entry.entity_id.as_str()) else {
            return Err(EngineError::invalid_argument(
                "fragment manifest references an unknown entity",
            ));
        };
        if !entity
            .fragments
            .iter()
            .any(|fragment| fragment.id == entry.fragment_id)
        {
            return Err(EngineError::invalid_argument(
                "fragment manifest references an unknown canonical fragment",
            ));
        }
        let embedding_fingerprint = entry
            .embedding_fingerprint
            .as_ref()
            .expect("validated above");
        let metadata_fingerprint =
            fragments::metadata_projection_fingerprint(entity.metadata.as_ref())?;
        let projection_fingerprint = projection_manifest_fingerprint(
            file,
            path_bytes,
            &directory_ids,
            entry,
            &metadata_fingerprint,
        )?;
        estimated_bytes = estimated_bytes
            .saturating_add(102)
            .saturating_add(entry.model.len());
        if estimated_bytes > MAX_FILE_MANIFEST_BYTES {
            return Ok(None);
        }
        manifest.fragments.push(FragmentManifestEntry {
            fragment_id: entry.fragment_id.as_str().to_owned(),
            entity_id: entry.entity_id.as_str().to_owned(),
            model: entry.model.clone(),
            embedding_fingerprint: embedding_fingerprint.clone(),
            projection_fingerprint,
        });
    }
    manifest.normalize();
    if manifest.encode_bounded()?.is_none() {
        return Ok(None);
    }
    Ok(Some(manifest))
}

fn build_staged_file_manifest(
    staged: &crate::storage::staging::DiskStagedBatches,
    file: &FileRecord,
    directories: &[crate::domain::DirectoryId],
) -> EngineResult<Option<FileManifest>> {
    let mut manifest = FileManifest::new(file.id);
    let mut metadata_fingerprints = HashMap::new();
    let mut estimated_bytes = 16_usize;
    let mut too_large = false;
    staged.for_each_entity_batch(|entities| {
        if too_large {
            return Ok(());
        }
        for entity in entities {
            let fingerprint = entity_manifest_fingerprint(entity)?;
            let metadata_fingerprint =
                fragments::metadata_projection_fingerprint(entity.metadata.as_ref())?;
            estimated_bytes = estimated_bytes.saturating_add(48);
            if estimated_bytes > MAX_FILE_MANIFEST_BYTES {
                too_large = true;
                break;
            }
            metadata_fingerprints.insert(entity.id.as_str().to_owned(), metadata_fingerprint);
            manifest.entities.push(EntityManifestEntry {
                entity_id: entity.id.as_str().to_owned(),
                fingerprint,
            });
        }
        Ok(())
    })?;
    if too_large {
        return Ok(None);
    }

    let directory_ids = directories.iter().map(|id| id.get()).collect::<Vec<_>>();
    let path_bytes = file.relative_path.as_path().as_os_str().as_encoded_bytes();
    let mut projections = staged.fragment_reader()?;
    let mut missing_embedding_fingerprint = false;
    loop {
        let batch = projections.next_batch(crate::storage::staging::STAGING_BATCH_FRAGMENTS)?;
        if batch.is_empty() {
            break;
        }
        for entry in batch {
            let Some(embedding_fingerprint) = entry.embedding_fingerprint.as_ref() else {
                missing_embedding_fingerprint = true;
                continue;
            };
            let Some(metadata_fingerprint) = metadata_fingerprints.get(entry.entity_id.as_str())
            else {
                return Err(EngineError::invalid_argument(
                    "staged projection references an unknown entity",
                ));
            };
            estimated_bytes = estimated_bytes
                .saturating_add(102)
                .saturating_add(entry.model.len());
            if estimated_bytes > MAX_FILE_MANIFEST_BYTES {
                too_large = true;
                continue;
            }
            let projection_fingerprint = projection_manifest_fingerprint(
                file,
                path_bytes,
                &directory_ids,
                &entry,
                metadata_fingerprint,
            )?;
            manifest.fragments.push(FragmentManifestEntry {
                fragment_id: entry.fragment_id.as_str().to_owned(),
                entity_id: entry.entity_id.as_str().to_owned(),
                model: entry.model,
                embedding_fingerprint: embedding_fingerprint.clone(),
                projection_fingerprint,
            });
        }
    }
    projections.finish()?;
    if too_large || missing_embedding_fingerprint {
        return Ok(None);
    }
    manifest.normalize();
    if manifest.encode_bounded()?.is_none() {
        return Ok(None);
    }
    Ok(Some(manifest))
}

fn entity_manifest_fingerprint(entity: &Entity) -> EngineResult<String> {
    let (payload, metadata) = entities::encode_staged_entity(entity)?;
    let encoded =
        serde_json::to_vec(&(entity.file_id.get(), entity.id.as_str(), payload, metadata))
            .map_err(|error| {
                EngineError::internal(format!("encode entity manifest fingerprint: {error}"))
            })?;
    Ok(sha256_hex(&encoded))
}

fn projection_manifest_fingerprint(
    file: &FileRecord,
    path_bytes: &[u8],
    directory_ids: &[u32],
    entry: &IndexedFragment,
    metadata_fingerprint: &str,
) -> EngineResult<String> {
    let encoded = serde_json::to_vec(&(
        file.id.get(),
        path_bytes,
        directory_ids,
        entry.entity_id.as_str(),
        entry.fragment_id.as_str(),
        metadata_fingerprint,
        entry.model.as_str(),
        entry.fts_text.as_str(),
    ))
    .map_err(|error| {
        EngineError::internal(format!("encode projection manifest fingerprint: {error}"))
    })?;
    Ok(sha256_hex(&encoded))
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
    // Cache published-snapshot trust per file, since it costs a manifest read.
    let mut snapshot_trusted = HashMap::new();
    for entity in stored_entities.into_values() {
        // Fail closed: serve a file's rows only when the file is fully indexed or
        // a validated published manifest survives. `replace_file` deletes the
        // manifest before its first row mutation, so a `NotIndexed` file with no
        // manifest is mid-apply/partial and must stay hidden, while a pending
        // retry with an intact last generation remains searchable.
        let Some(file) = files.get(&entity.file_id) else {
            continue;
        };
        if !file.index_status.is_indexed() {
            let trusted = if let Some(trusted) = snapshot_trusted.get(&file.id) {
                *trusted
            } else {
                let trusted = state.manifests.load(file.id)?.is_some();
                snapshot_trusted.insert(file.id, trusted);
                trusted
            };
            if !trusted {
                continue;
            }
        }
        for fragment in &entity.fragments {
            if fragments
                .insert(fragment.id.as_str().to_owned(), fragment.clone())
                .is_some()
            {
                return Err(corrupt("duplicate canonical fragment identity"));
            }
        }
        entities.insert(
            entity.id.clone(),
            StoredEntity {
                entity,
                file: file.clone(),
            },
        );
    }
    for hit in hits {
        if let Some(owner) = entities.get(&hit.entity_id)
            && owner.file.id != hit.file_id
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
    checkpoint_step("checkpoint_flush_directories", || state.directories.flush())?;
    checkpoint_step("checkpoint_flush_entities", || state.entities.flush())?;
    checkpoint_step("checkpoint_flush_fragments", || state.fragments.flush())?;
    checkpoint_step("checkpoint_flush_files", || state.files.flush())
}

#[cfg(test)]
fn pause_checkpoint_fragment_flush_for_test(shared: &SharedStore) -> EngineResult<()> {
    let pause = shared
        .checkpoint_fragment_flush_pause
        .lock()
        .map_err(|_| EngineError::internal("checkpoint test hook was poisoned"))?
        .take();
    let Some((started, release)) = pause else {
        return Ok(());
    };
    started
        .send(())
        .map_err(|_| EngineError::internal("checkpoint test hook observer disconnected"))?;
    release
        .recv_timeout(std::time::Duration::from_secs(5))
        .map_err(|error| {
            EngineError::internal(format!("checkpoint test hook release failed: {error}"))
        })
}

fn checkpoint_step(
    name: &'static str,
    operation: impl FnOnce() -> EngineResult<()>,
) -> EngineResult<()> {
    #[cfg(test)]
    let trace_started =
        STATE_LOCK_TRACE.with(|trace| trace.borrow().as_ref().map(|_| Instant::now()));
    #[cfg(test)]
    let _stage = StateLockStage::enter(name);
    #[cfg(not(test))]
    let _ = name;

    let result = operation();
    #[cfg(test)]
    if let Some(started) = trace_started {
        record_state_lock_span("checkpoint_step", started, Instant::now());
    }
    result
}

fn elapsed_micros(started: Instant) -> u64 {
    u64::try_from(started.elapsed().as_micros()).unwrap_or(u64::MAX)
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
    validate_entities(file, entities)?;
    fragments::validate_projections(entities, entries)?;
    for entry in entries {
        validate_vector(&entry.vector, model_schema(schema, &entry.model)?)?;
    }
    Ok(())
}

fn validate_entities(file: &FileRecord, entities: &[Entity]) -> EngineResult<()> {
    file.validate()?;
    let mut entity_ids = HashSet::new();
    let mut fragment_ids = HashSet::new();
    for entity in entities {
        if entity.file_id != file.id {
            return Err(EngineError::invalid_argument(
                "entity belongs to a different file",
            ));
        }
        if !entity_ids.insert(&entity.id) {
            return Err(EngineError::invalid_argument("duplicate entity id"));
        }
        entity.validate()?;
        entities::validate_content(&entity.content)?;
        for fragment in &entity.fragments {
            if !fragment_ids.insert(&fragment.id) {
                return Err(EngineError::invalid_argument("duplicate fragment id"));
            }
        }
    }
    Ok(())
}

fn model_schema<'a>(
    schemas: &'a [EmbeddingModelInfo],
    model: &str,
) -> EngineResult<&'a EmbeddingModelInfo> {
    schemas
        .iter()
        .find(|schema| schema.model.reference() == model)
        .ok_or_else(|| EngineError::invalid_argument(format!("unknown embedding model {model:?}")))
}

fn validate_vector(vector: &[f32], schema: &EmbeddingModelInfo) -> EngineResult<()> {
    if vector.len() != schema.dimension || vector.iter().any(|value| !value.is_finite()) {
        return Err(EngineError::invalid_argument(format!(
            "expected a finite {}-dimensional vector, got {} values",
            schema.dimension,
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
    if descriptor.exists() {
        let schema = read_json::<SchemaRecord>(&descriptor)?.embeddings()?;
        if let WorkspaceIndexStorageOptions::ReadWrite { embeddings, .. } = &options {
            if schema.len() != embeddings.len() {
                return Err(EngineError::invalid_argument(
                    "embedding model set changed; rebuild the index",
                ));
            }
            for stored in &schema {
                let current =
                    model_schema(embeddings, &stored.model.reference()).map_err(|_| {
                        EngineError::invalid_argument(
                            "embedding model set changed; rebuild the index",
                        )
                    })?;
                stored.ensure_index_compatible(current)?;
            }
        }
        return Ok(schema);
    }
    if fs::read_dir(path)
        .map_err(|error| io_error("inspect storage directory", path, &error))?
        .next()
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
    home: &Path,
    path: &Path,
    options: WorkspaceIndexStorageOptions,
) -> EngineResult<(File, Vec<EmbeddingModelInfo>)> {
    let lock = acquire_storage_lock(home, options.is_read_only())?;
    if !options.is_read_only() {
        let mut builder = DirBuilder::new();
        builder.recursive(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        if let Err(error) = builder.create(path)
            && !(error.kind() == std::io::ErrorKind::AlreadyExists && path.is_dir())
        {
            return Err(io_error("create storage directory", path, &error));
        }
    }
    Ok((lock, load_schema(path, options)?))
}

fn acquire_storage_lock(home: &Path, shared: bool) -> EngineResult<File> {
    let path = home.join("storage.lock");
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

#[cfg(test)]
mod tests;

#[cfg(test)]
mod tables_tests;
