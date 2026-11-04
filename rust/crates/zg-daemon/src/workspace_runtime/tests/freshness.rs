use super::*;
use crate::job_scheduler::JobReason;

fn fixture(
    executor: Arc<dyn IndexExecutor>,
) -> (WorkspaceRuntimeManager, mpsc::Sender<WorkspaceChangeBatch>) {
    let (sender, receiver) = mpsc::channel(8);
    (
        WorkspaceRuntimeManager::new(
            executor,
            Arc::new(ManualWatcherFactory {
                receiver: Arc::new(tokio::sync::Mutex::new(receiver)),
                watches: Mutex::new(Vec::new()),
                closes: Arc::new(AtomicUsize::new(0)),
            }),
            SchedulerConfig::default(),
        ),
        sender,
    )
}

async fn deliver(
    manager: &WorkspaceRuntimeManager,
    sender: &mpsc::Sender<WorkspaceChangeBatch>,
    root: &std::path::Path,
    change: WorkspaceChange,
) {
    sender
        .send(WorkspaceChangeBatch {
            changes: vec![change],
        })
        .await
        .expect("event");
    let runtime = manager
        .runtime(root.to_path_buf(), &IndexOptions::default())
        .expect("runtime");
    manager.flush_watcher(&runtime).await.expect("flush");
    runtime.settle_jobs(&manager.inner.scheduler, None).await;
}

#[tokio::test]
async fn native_lifecycle_workspace_reopens_closed_storage_and_preserves_index() {
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().join("source");
    std::fs::create_dir_all(&root).expect("source root");
    let source = root.join("reopen.rs");
    std::fs::write(&source, "fn reopenleaseprobe() {}\n").expect("source fixture");
    let index_root = workspace.path().join("index");
    let native = NativeLifecycleTestWorkspace::open(&root, &index_root).expect("open storage");

    native
        .index(&[IndexChange::Rescan])
        .await
        .expect("index fixture");
    let before = native
        .search_paths("reopenleaseprobe")
        .await
        .expect("query before reopen");
    assert!(before.contains(&std::path::PathBuf::from("reopen.rs")));

    native.reopen().await.expect("close and reopen storage");
    let after = native
        .search_paths("reopenleaseprobe")
        .await
        .expect("query after reopen");
    assert_eq!(after, before);
}

#[tokio::test]
async fn repeated_clean_refreshes_and_narrow_changes_do_not_rescan() {
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().canonicalize().expect("root");
    let executor = Arc::new(RecordingExecutor::default());
    let (manager, sender) = fixture(executor.clone());
    let options = IndexOptions {
        root: Some(root.clone()),
        ..IndexOptions::default()
    };
    assert!(
        manager
            .refresh_index(options.clone(), true)
            .await
            .expect("first reconcile")
    );
    for wait in [true, false, true, false] {
        assert!(
            !manager
                .refresh_index(options.clone(), wait)
                .await
                .expect("clean")
        );
    }
    assert_eq!(executor.calls.lock().expect("calls").len(), 1);
    deliver(
        &manager,
        &sender,
        &root,
        WorkspaceChange::Upsert("changed.rs".into()),
    )
    .await;
    assert!(
        !manager
            .refresh_index(options.clone(), true)
            .await
            .expect("narrow change indexed")
    );
    assert_eq!(
        executor.calls.lock().expect("calls")[1].changes,
        [IndexChange::Upsert("changed.rs".into())]
    );
    deliver(&manager, &sender, &root, WorkspaceChange::Rescan).await;
    assert!(
        !manager
            .refresh_index(options, true)
            .await
            .expect("watcher rescan is sufficient")
    );
    assert_eq!(executor.calls.lock().expect("calls").len(), 3);
    manager.shutdown_all().await.expect("shutdown");
}

#[tokio::test]
async fn concurrent_waits_share_a_refresh_and_background_does_not_wait_for_it() {
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().canonicalize().expect("root");
    let (started, mut jobs) = mpsc::unbounded_channel();
    let executor = Arc::new(GatedExecutor {
        started,
        release: tokio::sync::Semaphore::new(0),
    });
    let (manager, _sender) = fixture(executor.clone());
    let options = IndexOptions {
        root: Some(root),
        ..IndexOptions::default()
    };
    let first = tokio::spawn({
        let manager = manager.clone();
        let options = options.clone();
        async move { manager.refresh_index(options, true).await }
    });
    jobs.recv().await.expect("first refresh");
    let second = tokio::spawn({
        let manager = manager.clone();
        let options = options.clone();
        async move { manager.refresh_index(options, true).await }
    });
    assert!(
        tokio::time::timeout(
            Duration::from_secs(1),
            manager.refresh_index(options, false)
        )
        .await
        .expect("background returns")
        .expect("scheduled")
    );
    executor.release.add_permits(1);
    assert!(first.await.expect("task").expect("refreshed"));
    assert!(!second.await.expect("task").expect("reused proof"));
    assert!(jobs.try_recv().is_err());
    manager.shutdown_all().await.expect("shutdown");
}

#[tokio::test]
async fn a_new_recovery_epoch_cannot_be_cleared_by_an_older_full_refresh() {
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().canonicalize().expect("root");
    let (started, mut jobs) = mpsc::unbounded_channel();
    let executor = Arc::new(GatedExecutor {
        started,
        release: tokio::sync::Semaphore::new(0),
    });
    let (manager, _sender) = fixture(executor.clone());
    let options = IndexOptions {
        root: Some(root.clone()),
        ..IndexOptions::default()
    };
    let waiting = tokio::spawn({
        let manager = manager.clone();
        async move { manager.refresh_index(options, true).await }
    });
    jobs.recv().await.expect("first refresh");
    manager
        .runtime(root, &IndexOptions::default())
        .expect("runtime")
        .require_reconciliation();
    executor.release.add_permits(1);
    let second = jobs
        .recv()
        .await
        .expect("new recovery needs another refresh");
    assert_eq!(second.changes, [IndexChange::Rescan]);
    assert!(!waiting.is_finished());
    executor.release.add_permits(1);
    waiting.await.expect("task").expect("fresh");
    manager.shutdown_all().await.expect("shutdown");
}

#[tokio::test]
async fn watcher_jobs_do_not_override_the_saved_scan_scope() {
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().canonicalize().expect("root");
    let executor = Arc::new(RecordingExecutor::default());
    let (manager, sender) = fixture(executor.clone());
    let options = IndexOptions {
        root: Some(root.clone()),
        scan: zg_engine::api::index::options::ScanRulesUpdate {
            max_depth: Some(Some(1)),
            ..Default::default()
        },
        ..IndexOptions::default()
    };
    manager
        .refresh_index(options.clone(), true)
        .await
        .expect("initial scoped refresh");
    deliver(
        &manager,
        &sender,
        &root,
        WorkspaceChange::Upsert("src/changed.rs".into()),
    )
    .await;
    let calls = executor.calls.lock().expect("calls");
    assert_eq!(calls.len(), 2);
    // The native watcher resolves the saved manifest policy; watcher jobs must
    // not replay one-operation scan overrides from the initial request.
    assert_eq!(
        calls[1].scan,
        zg_engine::api::index::options::ScanRulesUpdate::default()
    );
    assert_eq!(
        calls[1].changes,
        [IndexChange::Upsert("src/changed.rs".into())]
    );
    drop(calls);
    manager.shutdown_all().await.expect("shutdown");
}

#[tokio::test]
async fn a_successful_narrow_job_does_not_cover_an_earlier_failed_batch() {
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().canonicalize().expect("root");
    let executor = Arc::new(RecordingExecutor::default());
    let (manager, sender) = fixture(executor.clone());
    let options = IndexOptions {
        root: Some(root.clone()),
        ..IndexOptions::default()
    };
    manager
        .refresh_index(options.clone(), true)
        .await
        .expect("initial refresh");
    executor.fail.store(true, Ordering::Release);
    deliver(
        &manager,
        &sender,
        &root,
        WorkspaceChange::Upsert("failed.rs".into()),
    )
    .await;
    executor.fail.store(false, Ordering::Release);
    deliver(
        &manager,
        &sender,
        &root,
        WorkspaceChange::Upsert("later.rs".into()),
    )
    .await;
    assert!(
        !manager
            .runtime(root, &options)
            .expect("runtime")
            .is_reconciled()
    );
    assert!(
        manager
            .refresh_index(options, true)
            .await
            .expect("repair lost batch")
    );
    assert_eq!(executor.calls.lock().expect("calls").len(), 4);
    assert_eq!(
        executor.calls.lock().expect("calls")[3].changes,
        [IndexChange::Rescan]
    );
    manager.shutdown_all().await.expect("shutdown");
}

#[tokio::test]
async fn restarting_a_watcher_requires_a_new_reconciliation() {
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().canonicalize().expect("root");
    let executor = Arc::new(RecordingExecutor::default());
    let (manager, _sender) = fixture(executor.clone());
    let options = IndexOptions {
        root: Some(root.clone()),
        ..IndexOptions::default()
    };
    manager
        .refresh_index(options.clone(), true)
        .await
        .expect("initial refresh");
    manager.stop_watching(&root).await.expect("watcher stopped");
    assert!(
        manager
            .refresh_index(options, true)
            .await
            .expect("repair watcher gap")
    );
    assert_eq!(executor.calls.lock().expect("calls").len(), 2);
    manager.shutdown_all().await.expect("shutdown");
}

struct PartiallyFailingExecutor;
#[async_trait]
impl IndexExecutor for PartiallyFailingExecutor {
    async fn index(&self, _options: IndexOptions) -> Result<IndexResult, EngineError> {
        Ok(IndexResult {
            files_failed: 1,
            ..IndexResult::default()
        })
    }
}

#[tokio::test]
async fn partial_index_failure_is_not_cached_as_fresh_or_retried_forever() {
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().canonicalize().expect("root");
    let (manager, _sender) = fixture(Arc::new(PartiallyFailingExecutor));
    let options = IndexOptions {
        root: Some(root.clone()),
        ..IndexOptions::default()
    };
    let error = tokio::time::timeout(
        Duration::from_secs(2),
        manager.refresh_index(options.clone(), true),
    )
    .await
    .expect("bounded failure")
    .expect_err("not fresh");
    assert_eq!(error.code(), EngineError::STORAGE_FAILURE);
    assert!(
        !manager
            .runtime(root, &options)
            .expect("runtime")
            .is_reconciled()
    );
    manager.shutdown_all().await.expect("shutdown");
}

#[tokio::test]
async fn cancelled_wait_releases_refresh_coordination_without_losing_the_job_proof() {
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().canonicalize().expect("root");
    let (started, mut jobs) = mpsc::unbounded_channel();
    let executor = Arc::new(GatedExecutor {
        started,
        release: tokio::sync::Semaphore::new(0),
    });
    let (manager, _sender) = fixture(executor.clone());
    let options = IndexOptions {
        root: Some(root),
        ..IndexOptions::default()
    };
    let first = tokio::spawn({
        let manager = manager.clone();
        let options = options.clone();
        async move { manager.refresh_index(options, true).await }
    });
    jobs.recv().await.expect("first refresh");
    first.abort();
    assert!(first.await.expect_err("cancelled").is_cancelled());
    executor.release.add_permits(1);
    assert!(
        !manager
            .refresh_index(options, true)
            .await
            .expect("finish existing work")
    );
    assert!(jobs.try_recv().is_err());
    manager.shutdown_all().await.expect("shutdown");
}

#[tokio::test]
async fn native_watcher_clean_refreshes_reuse_the_initial_reconciliation() {
    use zg_engine::api::index::options::EmbeddingModelSpec;
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().canonicalize().expect("root");
    let engine = Arc::new(zg_engine::ZvecGrep::new());
    let manager = WorkspaceRuntimeManager::native(engine.clone());
    let options = IndexOptions {
        root: Some(root.clone()),
        embedding: Some(EmbeddingModelSpec {
            reference: "qwen/text-embedding-v4".into(),
            revision: None,
            cache_dir: None,
            endpoint: None,
            device: zg_engine::api::index::options::Device::Cpu,
        }),
        endpoint: Some("http://127.0.0.1:1/empty-index".into()),
        api_key: Some("fixture-key".into()),
        allow_remote: true,
        ..IndexOptions::default()
    };
    manager
        .submit_index(options.clone(), true)
        .await
        .expect("empty index");
    manager
        .refresh_index(options.clone(), true)
        .await
        .expect("initial watcher reconciliation");
    let id = manager.job_for_root(&root).expect("initial job").id;
    for wait in [true, false, true] {
        assert!(
            !manager
                .refresh_index(options.clone(), wait)
                .await
                .expect("unchanged workspace")
        );
        assert_eq!(manager.job_for_root(&root).expect("same job").id, id);
    }
    manager.shutdown_all().await.expect("shutdown");
    engine.close();
}

struct M4ResearchExecutor {
    index: Arc<M4ResearchWorkspace>,
}

#[async_trait]
impl IndexExecutor for M4ResearchExecutor {
    async fn index(&self, options: IndexOptions) -> Result<IndexResult, EngineError> {
        self.index.index(&options.changes).await
    }
}

#[tokio::test]
async fn daemon_watcher_failure_keeps_m4_dirty_until_full_recovery_retry() {
    let temp = tempdir().expect("workspace");
    let root = temp.path().join("workspace");
    std::fs::create_dir_all(root.join("src")).expect("source directory");
    std::fs::write(root.join("src/a.rs"), "initialalpha").expect("first source");
    std::fs::write(root.join("src/b.rs"), "initialbeta").expect("second source");
    std::fs::write(root.join("stable.txt"), "stablemarker").expect("stable source");
    let root = root.canonicalize().expect("workspace root");
    let index = Arc::new(
        M4ResearchWorkspace::open(&root, &temp.path().join("index"))
            .expect("M4 research workspace"),
    );
    let factory = NativeWatcherFactory::new().with_config(NativeWatcherConfig {
        debounce: Duration::from_millis(30),
        max_wait: Duration::from_millis(100),
        reconcile_interval: None,
        resume_check_interval: None,
        poll_interval: Some(Duration::from_millis(15)),
        ..NativeWatcherConfig::default()
    });
    let manager = WorkspaceRuntimeManager::new(
        Arc::new(M4ResearchExecutor {
            index: Arc::clone(&index),
        }),
        Arc::new(factory),
        SchedulerConfig::default(),
    );
    let options = IndexOptions {
        root: Some(root.clone()),
        ..IndexOptions::default()
    };
    manager
        .refresh_index(options.clone(), true)
        .await
        .expect("initial daemon-managed indexing");
    assert_eq!(
        index
            .search_count("stablemarker")
            .await
            .expect("initial query"),
        1
    );
    let initial_revision = manager.runtime_snapshot(&root).dirty_revision;

    index.fail_after_first_publish_once();
    std::fs::write(root.join("src/a.rs"), "updatedalpha").expect("update first source");
    std::fs::write(root.join("src/b.rs"), "updatedbeta").expect("update second source");
    tokio::time::timeout(Duration::from_secs(8), async {
        loop {
            let runtime = manager.runtime_snapshot(&root);
            let job = manager.job_for_root(&root);
            if runtime.dirty_revision > initial_revision
                && runtime.indexed_revision < runtime.dirty_revision
                && job.is_some_and(|job| job.state == JobState::Failed)
            {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("real watcher schedules a failed M4 update job");
    let failed = manager.runtime_snapshot(&root);
    assert!(failed.indexed_revision < failed.dirty_revision);
    assert_eq!(
        index
            .search_count("updatedalpha")
            .await
            .expect("hidden failed update"),
        0
    );
    assert_eq!(
        index
            .search_count("updatedbeta")
            .await
            .expect("no partial file result"),
        0
    );
    assert_eq!(
        index
            .search_count("stablemarker")
            .await
            .expect("unmodified file remains"),
        1
    );

    assert!(
        manager
            .refresh_index(options, true)
            .await
            .expect("full reconciliation retries unacknowledged changes")
    );
    assert_eq!(
        index
            .search_count("updatedalpha")
            .await
            .expect("first retry result"),
        1
    );
    assert_eq!(
        index
            .search_count("updatedbeta")
            .await
            .expect("second retry result"),
        1
    );
    manager.shutdown_all().await.expect("daemon shutdown");
}

#[tokio::test]
async fn watcher_batch_runs_on_its_lane_while_manual_blocks_another_root() {
    let workspace = tempdir().expect("workspace");
    let other = tempdir().expect("other workspace");
    let root = workspace.path().canonicalize().expect("root");
    let (started, mut jobs) = mpsc::unbounded_channel();
    let executor = Arc::new(GatedExecutor {
        started,
        release: tokio::sync::Semaphore::new(1),
    });
    let (sender, receiver) = mpsc::channel(8);
    let manager = WorkspaceRuntimeManager::new(
        executor.clone(),
        Arc::new(ManualWatcherFactory {
            receiver: Arc::new(tokio::sync::Mutex::new(receiver)),
            watches: Mutex::new(Vec::new()),
            closes: Arc::new(AtomicUsize::new(0)),
        }),
        SchedulerConfig {
            manual_concurrency: 1,
            watch_concurrency: 1,
            queue_capacity: 8,
        },
    );
    let options = IndexOptions {
        root: Some(root.clone()),
        ..IndexOptions::default()
    };
    assert!(
        manager
            .refresh_index(options.clone(), true)
            .await
            .expect("initial refresh")
    );
    jobs.recv().await.expect("initial scan");

    let blocker = manager
        .inner
        .scheduler
        .submit(
            other.path().to_path_buf(),
            IndexOptions::default(),
            JobReason::Manual,
        )
        .expect("other manual writer");
    jobs.recv().await.expect("other writer running");

    // Do not call the shared `deliver` helper here: it waits for tracked jobs
    // to settle, but the watcher job is intentionally gated below.
    sender
        .send(WorkspaceChangeBatch {
            changes: vec![WorkspaceChange::Upsert("not-lost.rs".into())],
        })
        .await
        .expect("event");
    manager
        .flush_watcher(&manager.runtime(root.clone(), &options).expect("runtime"))
        .await
        .expect("flush");
    let watcher = jobs.recv().await.expect("watcher uses its own lane");
    assert_eq!(
        watcher.changes,
        [IndexChange::Upsert("not-lost.rs".into())],
        "a watcher batch must remain available while a manual job runs elsewhere"
    );

    // Release both lanes: the manual blocker and the watcher job.
    executor.release.add_permits(2);
    manager
        .inner
        .scheduler
        .wait(blocker.job.id)
        .await
        .expect("manual blocker finishes");
    manager.inner.scheduler.wait_for_root_idle(&root).await;
    let runtime = manager.runtime(root, &options).expect("runtime");
    runtime.settle_jobs(&manager.inner.scheduler, None).await;
    assert!(
        runtime.is_reconciled(),
        "the watcher change was indexed without a manual repair scan"
    );
    manager.shutdown_all().await.expect("shutdown");
}

#[tokio::test]
async fn background_refresh_accounts_for_late_watcher_notifications() {
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().canonicalize().expect("root");
    let (started, mut jobs) = mpsc::unbounded_channel();
    let executor = Arc::new(GatedExecutor {
        started,
        release: tokio::sync::Semaphore::new(1),
    });
    let (manager, sender) = fixture(executor.clone());
    let options = IndexOptions {
        root: Some(root.clone()),
        ..IndexOptions::default()
    };
    manager
        .refresh_index(options.clone(), true)
        .await
        .expect("initial reconciliation");
    jobs.recv().await.expect("initial job");
    assert!(
        !manager
            .refresh_index(options.clone(), false)
            .await
            .expect("idle without events")
    );

    // Native backends can deliver another notification after a fresh query returns.
    sender
        .send(WorkspaceChangeBatch {
            changes: vec![WorkspaceChange::Upsert("fresh.txt".into())],
        })
        .await
        .expect("late notification");
    let runtime = manager.runtime(root, &options).expect("runtime");
    manager
        .flush_watcher(&runtime)
        .await
        .expect("notification delivered");
    let incremental = jobs.recv().await.expect("watcher job started");
    assert_eq!(
        incremental.changes,
        [IndexChange::Upsert("fresh.txt".into())]
    );
    let scheduled = tokio::time::timeout(
        Duration::from_secs(1),
        manager.refresh_index(options.clone(), false),
    )
    .await
    .expect("background does not wait for watcher job")
    .expect("background status");
    assert!(scheduled, "the late watcher job is still running");

    executor.release.add_permits(1);
    assert!(
        !manager
            .refresh_index(options.clone(), true)
            .await
            .expect("drain existing update")
    );
    assert!(
        !manager
            .refresh_index(options, false)
            .await
            .expect("idle again")
    );
    assert!(
        jobs.try_recv().is_err(),
        "no unnecessary full reconciliation"
    );
    manager.shutdown_all().await.expect("shutdown");
}

#[tokio::test]
async fn concurrent_waiters_receive_progress_before_shared_refresh_finishes() {
    use zg_engine::api::index::progress::{
        IndexProgress, IndexProgressPhase, IndexProgressReporter,
    };
    let workspace = tempdir().expect("workspace");
    let (started, mut jobs) = mpsc::unbounded_channel();
    let executor = Arc::new(GatedExecutor {
        started,
        release: tokio::sync::Semaphore::new(0),
    });
    let (manager, _sender) = fixture(executor.clone());
    let options = IndexOptions {
        root: Some(workspace.path().to_path_buf()),
        ..IndexOptions::default()
    };
    let first = tokio::spawn({
        let manager = manager.clone();
        let options = options.clone();
        async move { manager.refresh_index(options, true).await }
    });
    let running = jobs.recv().await.expect("shared job");
    let (progress, mut received) = mpsc::unbounded_channel();
    let second = tokio::spawn({
        let manager = manager.clone();
        async move {
            manager
                .refresh_index(
                    IndexOptions {
                        on_progress: Some(IndexProgressReporter::new(move |event| {
                            let _ = progress.send(event);
                        })),
                        ..options
                    },
                    true,
                )
                .await
        }
    });
    running
        .on_progress
        .expect("scheduler reporter")
        .report(IndexProgress {
            phase: IndexProgressPhase::Indexing,
            files_total: Some(7),
            files_indexed: Some(2),
            files_failed: Some(0),
            detail: None,
            embedding: None,
        });
    let event = tokio::time::timeout(Duration::from_secs(2), received.recv())
        .await
        .expect("second waiter receives live progress")
        .expect("event");
    assert_eq!(event.files_total, Some(7));
    assert!(!first.is_finished());
    assert!(!second.is_finished());
    executor.release.add_permits(1);
    first.await.expect("first task").expect("first refresh");
    second.await.expect("second task").expect("second refresh");
    assert!(jobs.try_recv().is_err());
    manager.shutdown_all().await.expect("shutdown");
}

#[tokio::test]
async fn public_wait_reuses_reconciliation_without_engine_rescan() {
    use zg_engine::api::{
        context::options::{ContextRoute, ContextRouteMode, RefreshPolicy},
        index::options::EmbeddingModelSpec,
    };
    use zg_transport_mcp::IndexOperationProvider;
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().canonicalize().expect("root");
    let engine = zg_engine::ZvecGrep::new();
    engine
        .index(IndexOptions {
            root: Some(root.clone()),
            embedding: Some(EmbeddingModelSpec {
                reference: "qwen/text-embedding-v4".into(),
                revision: None,
                cache_dir: None,
                endpoint: None,
                device: zg_engine::api::index::options::Device::Cpu,
            }),
            endpoint: Some("http://127.0.0.1:1/empty-index".into()),
            api_key: Some("fixture-key".into()),
            allow_remote: true,
            ..IndexOptions::default()
        })
        .await
        .expect("empty index");
    let executor = Arc::new(RecordingExecutor::default());
    let (manager, _sender) = fixture(executor.clone());
    let request = zg_engine::api::context::ContextOptions {
        root: Some(root.clone()),
        refresh: Some(RefreshPolicy::Wait),
        routes: vec![ContextRoute {
            mode: ContextRouteMode::Fts,
            query: "probe".into(),
        }],
        ..Default::default()
    };
    manager
        .search(&engine, request.clone())
        .await
        .expect("initial reconciliation");
    // Keep the controlled watcher quiet. Resident Wait covers delivered events,
    // so an unreported file must not trigger another engine disk scan/index job.
    std::fs::write(root.join("probe.txt"), "probe").expect("scan probe");
    for _ in 0..2 {
        let reply = manager
            .search(&engine, request.clone())
            .await
            .expect("reuse daemon proof");
        assert_eq!(reply.freshness.as_deref(), Some("fresh"));
    }
    assert_eq!(executor.calls.lock().expect("calls").len(), 1);
    assert_eq!(
        engine
            .context(request)
            .await
            .expect_err("direct Wait checks disk")
            .code(),
        EngineError::PERMISSION_DENIED
    );
    manager.shutdown_all().await.expect("shutdown");
    engine.close();
}

struct BufferedWatcher {
    session: ManualWatchSession,
    sender: mpsc::Sender<WorkspaceChangeBatch>,
    pending: AtomicBool,
    flushed: mpsc::UnboundedSender<()>,
}

#[async_trait]
impl WorkspaceWatchSessionPort for BufferedWatcher {
    async fn next_changes(&self, control: &TaskControl) -> Result<WorkspaceChangeBatch, HostError> {
        self.session.next_changes(control).await
    }

    async fn flush_pending(&self) -> Result<(), HostError> {
        if self.pending.swap(false, Ordering::AcqRel) {
            self.sender
                .send(WorkspaceChangeBatch {
                    changes: vec![WorkspaceChange::Upsert("during-index.txt".into())],
                })
                .await
                .expect("buffered event");
        }
        let _ = self.flushed.send(());
        Ok(())
    }

    async fn close(&self) -> Result<(), HostError> {
        Ok(())
    }
}

struct BufferedWatcherFactory(Arc<BufferedWatcher>);

#[async_trait]
impl WorkspaceWatcherFactoryPort for BufferedWatcherFactory {
    async fn watch(
        &self,
        _request: &WatchRequest,
        _control: &TaskControl,
    ) -> Result<Arc<dyn WorkspaceWatchSessionPort>, HostError> {
        Ok(self.0.clone())
    }
}

#[tokio::test]
async fn waiting_for_existing_job_flushes_events_buffered_during_that_job() {
    let workspace = tempdir().expect("workspace");
    let root = workspace.path().canonicalize().expect("root");
    let (started, mut jobs) = mpsc::unbounded_channel();
    let executor = Arc::new(GatedExecutor {
        started,
        release: tokio::sync::Semaphore::new(1),
    });
    let (sender, receiver) = mpsc::channel(8);
    let (flushed, mut barriers) = mpsc::unbounded_channel();
    let watcher = Arc::new(BufferedWatcher {
        session: ManualWatchSession {
            receiver: Arc::new(tokio::sync::Mutex::new(receiver)),
            closes: Arc::new(AtomicUsize::new(0)),
        },
        sender,
        pending: AtomicBool::new(false),
        flushed,
    });
    let manager = WorkspaceRuntimeManager::new(
        executor.clone(),
        Arc::new(BufferedWatcherFactory(watcher.clone())),
        SchedulerConfig::default(),
    );
    let options = IndexOptions {
        root: Some(root.clone()),
        ..Default::default()
    };
    manager
        .refresh_index(options.clone(), true)
        .await
        .expect("initial reconcile");
    jobs.recv().await.expect("initial job");
    watcher
        .sender
        .send(WorkspaceChangeBatch {
            changes: vec![WorkspaceChange::Upsert("before-index.txt".into())],
        })
        .await
        .expect("first event");
    jobs.recv().await.expect("existing job");
    while barriers.try_recv().is_ok() {}
    let waiting = tokio::spawn({
        let manager = manager.clone();
        async move { manager.refresh_index(options, true).await }
    });
    barriers.recv().await.expect("pre-wait flush");
    watcher.pending.store(true, Ordering::Release);
    executor.release.add_permits(1);
    let followup = tokio::time::timeout(Duration::from_secs(2), jobs.recv())
        .await
        .expect("post-wait flush schedules buffered event")
        .expect("followup");
    assert_eq!(
        followup.changes,
        [IndexChange::Upsert("during-index.txt".into())]
    );
    assert!(!waiting.is_finished());
    executor.release.add_permits(1);
    waiting.await.expect("wait task").expect("fresh");
    assert!(!watcher.pending.load(Ordering::Acquire));
    manager.shutdown_all().await.expect("shutdown");
}

const P1_NATIVE_QUERY: &str = "p1nativebaseline";
const P1_NATIVE_QUERY_INTERVAL: Duration = Duration::from_millis(250);
const P1_NATIVE_QUERY_MAX_SAMPLES: usize = 4096;
const P1_NATIVE_WATCHER_DEADLINE: Duration = Duration::from_secs(10);
const P1_NATIVE_WRITER_IDLE_DEADLINE: Duration = Duration::from_secs(30);
const P1_NATIVE_QUERY_DEADLINE: Duration = Duration::from_secs(30);
const P1_NATIVE_START_DEADLINE: Duration = Duration::from_secs(30);

struct NativeBaselineExecutor {
    workspace: Arc<NativeLifecycleTestWorkspace>,
}

#[async_trait]
impl IndexExecutor for NativeBaselineExecutor {
    async fn index(&self, options: IndexOptions) -> Result<IndexResult, EngineError> {
        self.workspace.index(&options.changes).await
    }
}

#[derive(Clone)]
struct NativeBaselineQueryObservation {
    completed_at: std::time::Instant,
    paths: Vec<std::path::PathBuf>,
}

#[derive(Default)]
struct NativeBaselineQueryMetrics {
    attempts: usize,
    response_us: Vec<u128>,
    schedule_lag_us: Vec<u128>,
    missed_slots: u64,
    failures: Vec<String>,
    capped: bool,
}

fn p1_lock<T>(mutex: &std::sync::Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn p1_publish_query_observation(
    latest: &tokio::sync::watch::Sender<Option<NativeBaselineQueryObservation>>,
    completed_at: std::time::Instant,
    result: Result<Vec<std::path::PathBuf>, String>,
) {
    if let Ok(paths) = result {
        let _ = latest.send(Some(NativeBaselineQueryObservation {
            completed_at,
            paths,
        }));
    }
}

#[test]
fn p1_query_failure_is_not_an_empty_successful_visibility_observation() {
    let (latest, receiver) = tokio::sync::watch::channel(None);
    let path = std::path::PathBuf::from("visible.rs");
    let first = std::time::Instant::now();
    p1_publish_query_observation(&latest, first, Ok(vec![path.clone()]));
    p1_publish_query_observation(
        &latest,
        std::time::Instant::now(),
        Err("query failed".into()),
    );
    let observed = receiver
        .borrow()
        .clone()
        .expect("successful observation retained");
    assert_eq!(observed.completed_at, first);
    assert_eq!(observed.paths, vec![path]);
    let empty_at = std::time::Instant::now();
    p1_publish_query_observation(&latest, empty_at, Ok(Vec::new()));
    let empty = receiver
        .borrow()
        .clone()
        .expect("empty success is observable");
    assert_eq!(empty.completed_at, empty_at);
    assert!(empty.paths.is_empty());
}

#[tokio::test]
async fn p1_failed_query_cannot_satisfy_delete_visibility() {
    let (latest, mut receiver) = tokio::sync::watch::channel(None);
    let path = std::path::PathBuf::from("visible.rs");
    p1_publish_query_observation(&latest, std::time::Instant::now(), Ok(vec![path.clone()]));
    let observed_at = std::time::Instant::now();
    p1_publish_query_observation(
        &latest,
        std::time::Instant::now(),
        Err("query failed".into()),
    );
    assert!(
        tokio::time::timeout(
            Duration::from_millis(25),
            p1_wait_query_state(&mut receiver, observed_at, |paths| !paths.contains(&path)),
        )
        .await
        .is_err(),
        "a failed query must not be interpreted as deletion"
    );
    let empty_at = std::time::Instant::now();
    p1_publish_query_observation(&latest, empty_at, Ok(Vec::new()));
    assert_eq!(
        p1_wait_query_state(&mut receiver, observed_at, |paths| !paths.contains(&path)).await,
        empty_at
    );
}

async fn native_baseline_query_sampler(
    workspace: Arc<NativeLifecycleTestWorkspace>,
    stop: tokio_util::sync::CancellationToken,
    latest: tokio::sync::watch::Sender<Option<NativeBaselineQueryObservation>>,
    metrics: Arc<Mutex<NativeBaselineQueryMetrics>>,
) {
    let started = tokio::time::Instant::now();
    let mut next = started + P1_NATIVE_QUERY_INTERVAL;
    loop {
        tokio::select! {
            () = stop.cancelled() => break,
            () = tokio::time::sleep_until(next) => {}
        }
        if p1_lock(&metrics).attempts >= P1_NATIVE_QUERY_MAX_SAMPLES {
            p1_lock(&metrics).capped = true;
            break;
        }
        let now = tokio::time::Instant::now();
        let lag = now.saturating_duration_since(next);
        let missed = (lag.as_nanos() / P1_NATIVE_QUERY_INTERVAL.as_nanos()) as u64;
        let query_started = std::time::Instant::now();
        let result = workspace
            .search_paths(P1_NATIVE_QUERY)
            .await
            .map_err(|error| error.to_string());
        let completed_at = std::time::Instant::now();
        let response_us = completed_at.duration_since(query_started).as_micros();
        {
            let mut metrics = p1_lock(&metrics);
            metrics.attempts += 1;
            metrics.missed_slots += missed;
            metrics.schedule_lag_us.push(lag.as_micros());
            match &result {
                Err(failure) => metrics.failures.push(failure.clone()),
                Ok(_) => metrics.response_us.push(response_us),
            }
        }
        p1_publish_query_observation(&latest, completed_at, result);
        next +=
            P1_NATIVE_QUERY_INTERVAL.saturating_mul(u32::try_from(missed + 1).unwrap_or(u32::MAX));
    }
}

fn p1_nearest_rank_p95(values: &[u128]) -> Option<u128> {
    if values.is_empty() {
        return None;
    }
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    let rank = (sorted.len() * 95).div_ceil(100).max(1) - 1;
    sorted.get(rank).copied()
}

fn p1_watcher_observation(
    manager: &WorkspaceRuntimeManager,
    root: &std::path::Path,
) -> Option<(u64, std::time::Instant)> {
    let runtime = p1_lock(&manager.inner.runtimes).get(root).cloned()?;
    *p1_lock(&runtime.watcher_observation)
}

async fn p1_index_completion(
    manager: &WorkspaceRuntimeManager,
    root: &std::path::Path,
    minimum_revision: u64,
) -> (u64, std::time::Instant) {
    let runtime = p1_lock(&manager.inner.runtimes)
        .get(root)
        .cloned()
        .expect("workspace runtime");
    tokio::time::timeout(P1_NATIVE_WRITER_IDLE_DEADLINE, async {
        loop {
            let completion = *p1_lock(&runtime.index_completion);
            if let Some(completion) = completion
                && completion.0 >= minimum_revision
            {
                return completion;
            }
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
    })
    .await
    .expect("successful index completion was not recorded")
}

fn p1_arm_next_watcher_event(manager: &WorkspaceRuntimeManager, root: &std::path::Path) {
    let runtime = p1_lock(&manager.inner.runtimes)
        .get(root)
        .cloned()
        .expect("workspace runtime");
    *p1_lock(&runtime.watcher_observation) = None;
}

async fn p1_wait_watcher_event(
    manager: &WorkspaceRuntimeManager,
    root: &std::path::Path,
    after_revision: u64,
) -> (u64, std::time::Instant) {
    tokio::time::timeout(P1_NATIVE_WATCHER_DEADLINE, async {
        loop {
            if let Some((revision, observed_at)) = p1_watcher_observation(manager, root)
                && revision > after_revision
            {
                return (revision, observed_at);
            }
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
    })
    .await
    .expect("native watcher did not observe mutation within 10 seconds")
}

async fn p1_wait_runtime_idle(manager: &WorkspaceRuntimeManager, root: &std::path::Path) {
    tokio::time::timeout(P1_NATIVE_WRITER_IDLE_DEADLINE, async {
        loop {
            let runtime = manager.runtime_snapshot(root);
            let job = manager.job_for_root(root);
            if job
                .as_ref()
                .is_some_and(|job| job.state == JobState::Failed)
            {
                panic!("native watcher indexing job failed: {job:?}");
            }
            let active = job
                .as_ref()
                .is_some_and(|job| matches!(job.state, JobState::Queued | JobState::Running));
            if runtime.dirty_revision == runtime.indexed_revision && !active {
                break;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .expect("native watcher indexing did not settle within 30 seconds");
}

async fn p1_wait_query_state<F>(
    latest: &mut tokio::sync::watch::Receiver<Option<NativeBaselineQueryObservation>>,
    observed_at: std::time::Instant,
    predicate: F,
) -> std::time::Instant
where
    F: Fn(&[std::path::PathBuf]) -> bool,
{
    tokio::time::timeout(P1_NATIVE_QUERY_DEADLINE, async {
        loop {
            let observation = latest.borrow().clone();
            if let Some(observation) = observation
                && observation.completed_at >= observed_at
                && predicate(&observation.paths)
            {
                return observation.completed_at;
            }
            latest.changed().await.expect("query sampler stopped early");
        }
    })
    .await
    .expect("expected indexed result did not become query-visible within 30 seconds")
}

fn p1_append_marker(root: &std::path::Path, path: &std::path::Path, suffix: &str) {
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .append(true)
        .open(root.join(path))
        .expect("open mutation file");
    writeln!(
        file,
        "\npub fn p1_native_{suffix}() {{ let _ = \"{P1_NATIVE_QUERY}\"; }}"
    )
    .expect("append marker");
    file.sync_all().expect("sync mutation file");
}

async fn p1_ready_for_fs_change(manager: &WorkspaceRuntimeManager, root: &std::path::Path) -> u64 {
    p1_wait_runtime_idle(manager, root).await;
    let runtime = manager
        .runtime(root.to_path_buf(), &IndexOptions::default())
        .expect("workspace runtime");
    manager
        .flush_watcher(&runtime)
        .await
        .expect("flush watcher");
    p1_wait_runtime_idle(manager, root).await;
    let revision = manager.runtime_snapshot(root).dirty_revision;
    p1_arm_next_watcher_event(manager, root);
    revision
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "single authorized P1 native baseline; launch only through its resource-contained runner"]
async fn p1_native_lifecycle_baseline_once() {
    use zg_engine::api::index::options::WorkspaceChange as IndexChange;

    let operation_id = std::env::var("P1_NATIVE_OPERATION_ID").expect("operation id");
    assert_eq!(
        std::env::var("P1_NATIVE_QUERY_INTERVAL_MS").expect("frozen query interval"),
        P1_NATIVE_QUERY_INTERVAL.as_millis().to_string()
    );
    assert_eq!(
        std::env::var("P1_NATIVE_QUERY_MAX_SAMPLES").expect("frozen query sample cap"),
        P1_NATIVE_QUERY_MAX_SAMPLES.to_string()
    );
    assert_eq!(
        std::env::var("P1_NATIVE_QUERY_SLO_MAX_US").expect("frozen query SLO"),
        "300000"
    );
    assert_eq!(
        std::env::var("P1_NATIVE_FRESHNESS_SLO_MAX_US").expect("frozen freshness SLO"),
        "5000000"
    );
    assert_eq!(
        std::env::var("P1_NATIVE_WATCHER_DEADLINE_S").expect("watcher deadline"),
        "10"
    );
    assert_eq!(
        std::env::var("P1_NATIVE_WRITER_IDLE_DEADLINE_S").expect("writer deadline"),
        "30"
    );
    assert_eq!(
        std::env::var("P1_NATIVE_QUERY_DEADLINE_S").expect("query deadline"),
        "30"
    );
    assert_eq!(
        std::env::var("P1_NATIVE_START_DEADLINE_S").expect("start deadline"),
        "30"
    );
    let root = std::path::PathBuf::from(
        std::env::var_os("P1_NATIVE_WORKSPACE").expect("operation-owned source fixture"),
    )
    .canonicalize()
    .expect("canonical source fixture");
    let index_root = std::path::PathBuf::from(
        std::env::var_os("P1_NATIVE_INDEX_ROOT").expect("operation-owned index root"),
    );
    let report_path = std::path::PathBuf::from(
        std::env::var_os("P1_NATIVE_REPORT").expect("operation report path"),
    );
    assert!(!index_root.exists(), "index root must be fresh");
    assert!(!report_path.exists(), "report path must be fresh");
    std::fs::create_dir_all(&index_root).expect("create index root");

    let native = Arc::new(
        NativeLifecycleTestWorkspace::open(&root, &index_root)
            .expect("open native lifecycle test workspace"),
    );
    assert!(
        native
            .search_paths(P1_NATIVE_QUERY)
            .await
            .expect("initial query")
            .is_empty(),
        "frozen corpus must not contain the synthetic query marker"
    );
    let watcher = NativeWatcherFactory::new().with_config(NativeWatcherConfig {
        debounce: Duration::from_millis(30),
        max_wait: Duration::from_millis(100),
        reconcile_interval: None,
        resume_check_interval: None,
        poll_interval: Some(Duration::from_millis(15)),
        ..NativeWatcherConfig::default()
    });
    let manager = WorkspaceRuntimeManager::new(
        Arc::new(NativeBaselineExecutor {
            workspace: Arc::clone(&native),
        }),
        Arc::new(watcher),
        SchedulerConfig::default(),
    );

    let metrics = Arc::new(Mutex::new(NativeBaselineQueryMetrics::default()));
    let stop_queries = tokio_util::sync::CancellationToken::new();
    let (latest_tx, mut latest_rx) = tokio::sync::watch::channel(None);
    let query_task = tokio::spawn(native_baseline_query_sampler(
        Arc::clone(&native),
        stop_queries.clone(),
        latest_tx,
        Arc::clone(&metrics),
    ));

    let initial_started = std::time::Instant::now();
    manager
        .submit_index(
            IndexOptions {
                root: Some(root.clone()),
                ..IndexOptions::default()
            },
            true,
        )
        .await
        .expect("initial native index");
    let initial_index_us = initial_started.elapsed().as_micros();
    tokio::time::timeout(P1_NATIVE_START_DEADLINE, async {
        while !manager.runtime_snapshot(&root).watcher_active {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .expect("native watcher did not activate");
    p1_wait_runtime_idle(&manager, &root).await;
    tokio::time::sleep(Duration::from_millis(300)).await;
    p1_wait_runtime_idle(&manager, &root).await;
    let indexed_paths = native.file_paths().await.expect("indexed paths");
    assert!(
        indexed_paths.len() >= 7,
        "pilot corpus too small: {}",
        indexed_paths.len()
    );

    let update_path = indexed_paths[0].clone();
    let burst_paths = indexed_paths[1..5].to_vec();
    let rename_source = indexed_paths[5].clone();
    let mut freshness = Vec::new();

    let baseline = p1_ready_for_fs_change(&manager, &root).await;
    p1_append_marker(&root, &update_path, "updated");
    let (revision, observed_at) = p1_wait_watcher_event(&manager, &root, baseline).await;
    let update_visible = p1_wait_query_state(&mut latest_rx, observed_at, |paths| {
        paths.contains(&update_path)
    })
    .await;
    p1_wait_runtime_idle(&manager, &root).await;
    let (completed_revision, index_completed_at) =
        p1_index_completion(&manager, &root, revision).await;
    assert!(completed_revision >= revision);
    freshness.push(serde_json::json!({
        "event": "single_file_update", "revision": revision,
        "watcher_observed_to_visible_us": update_visible.duration_since(observed_at).as_micros(),
        "watcher_observed_to_index_complete_us": index_completed_at.duration_since(observed_at).as_micros(),
    }));

    let baseline = p1_ready_for_fs_change(&manager, &root).await;
    for (index, path) in burst_paths.iter().enumerate() {
        p1_append_marker(&root, path, &format!("burst_{index}"));
    }
    let (revision, observed_at) = p1_wait_watcher_event(&manager, &root, baseline).await;
    let burst_visible = p1_wait_query_state(&mut latest_rx, observed_at, |paths| {
        burst_paths.iter().all(|path| paths.contains(path))
    })
    .await;
    p1_wait_runtime_idle(&manager, &root).await;
    let (completed_revision, index_completed_at) =
        p1_index_completion(&manager, &root, revision).await;
    assert!(completed_revision >= revision);
    freshness.push(serde_json::json!({
        "event": "burst_upsert", "revision": revision,
        "changed_files": burst_paths.len(),
        "watcher_observed_to_visible_us": burst_visible.duration_since(observed_at).as_micros(),
        "watcher_observed_to_index_complete_us": index_completed_at.duration_since(observed_at).as_micros(),
    }));

    let rename_target = rename_source.with_file_name(format!(
        "{}.p1-renamed.rs",
        rename_source
            .file_stem()
            .and_then(|value| value.to_str())
            .unwrap_or("source"),
    ));
    assert!(
        !root.join(&rename_target).exists(),
        "rename target must be absent"
    );
    let baseline = p1_ready_for_fs_change(&manager, &root).await;
    p1_append_marker(&root, &rename_source, "renamed");
    std::fs::rename(root.join(&rename_source), root.join(&rename_target)).expect("rename fixture");
    let (revision, observed_at) = p1_wait_watcher_event(&manager, &root, baseline).await;
    let rename_visible = p1_wait_query_state(&mut latest_rx, observed_at, |paths| {
        paths.contains(&rename_target) && !paths.contains(&rename_source)
    })
    .await;
    p1_wait_runtime_idle(&manager, &root).await;
    let (completed_revision, index_completed_at) =
        p1_index_completion(&manager, &root, revision).await;
    assert!(completed_revision >= revision);
    freshness.push(serde_json::json!({
        "event": "rename", "revision": revision,
        "watcher_observed_to_visible_us": rename_visible.duration_since(observed_at).as_micros(),
        "watcher_observed_to_index_complete_us": index_completed_at.duration_since(observed_at).as_micros(),
    }));

    let baseline = p1_ready_for_fs_change(&manager, &root).await;
    std::fs::remove_file(root.join(&rename_target)).expect("delete renamed fixture");
    let (revision, observed_at) = p1_wait_watcher_event(&manager, &root, baseline).await;
    let delete_visible = p1_wait_query_state(&mut latest_rx, observed_at, |paths| {
        !paths.contains(&rename_target)
    })
    .await;
    p1_wait_runtime_idle(&manager, &root).await;
    let (completed_revision, index_completed_at) =
        p1_index_completion(&manager, &root, revision).await;
    assert!(completed_revision >= revision);
    freshness.push(serde_json::json!({
        "event": "delete", "revision": revision,
        "watcher_observed_to_visible_us": delete_visible.duration_since(observed_at).as_micros(),
        "watcher_observed_to_index_complete_us": index_completed_at.duration_since(observed_at).as_micros(),
    }));

    tokio::time::sleep(Duration::from_secs(2)).await;
    let reopen_started = std::time::Instant::now();
    native.reopen().await.expect("reopen native store");
    let reopen_us = reopen_started.elapsed().as_micros();
    let final_paths = native
        .search_paths(P1_NATIVE_QUERY)
        .await
        .expect("post-reopen query");
    assert!(burst_paths.iter().all(|path| final_paths.contains(path)));
    assert!(final_paths.contains(&update_path));
    assert!(!final_paths.contains(&rename_source));
    assert!(!final_paths.contains(&rename_target));

    let rescan_started = std::time::Instant::now();
    for (pass, name) in ["repeat_rescan_1", "repeat_rescan_2"]
        .into_iter()
        .enumerate()
    {
        manager
            .submit_index(
                IndexOptions {
                    root: Some(root.clone()),
                    changes: vec![IndexChange::Rescan],
                    ..IndexOptions::default()
                },
                true,
            )
            .await
            .unwrap_or_else(|error| panic!("{name} failed: {error}"));
        p1_wait_runtime_idle(&manager, &root).await;
        if pass == 0 {
            assert!(
                native
                    .search_paths(P1_NATIVE_QUERY)
                    .await
                    .expect("rescan query")
                    .contains(&update_path)
            );
        }
    }
    let repeat_rescan_us = rescan_started.elapsed().as_micros();

    stop_queries.cancel();
    query_task.await.expect("query sampler task");
    manager
        .shutdown_all()
        .await
        .expect("shutdown native runtime");
    let query_metrics = p1_lock(&metrics);
    let query_p95_us = p1_nearest_rank_p95(&query_metrics.response_us);
    let max_schedule_lag_us = query_metrics
        .schedule_lag_us
        .iter()
        .copied()
        .max()
        .unwrap_or(0);
    let query_slo_status = if query_metrics.response_us.len() < 20
        || query_metrics.missed_slots != 0
        || !query_metrics.failures.is_empty()
        || query_metrics.capped
        || max_schedule_lag_us > P1_NATIVE_QUERY_INTERVAL.as_micros()
    {
        "INCONCLUSIVE"
    } else if query_p95_us.is_some_and(|p95| p95 <= 300_000) {
        "PASS"
    } else {
        "FAIL"
    };
    let freshness_values = freshness
        .iter()
        .filter_map(|item| item["watcher_observed_to_visible_us"].as_u64())
        .collect::<Vec<_>>();
    let freshness_slo_status = if freshness_values.len() != 4 {
        "INCONCLUSIVE"
    } else if freshness_values.iter().any(|value| *value > 5_000_000) {
        "FAIL"
    } else {
        "PASS"
    };
    let report = serde_json::json!({
        "schema_version": 1,
        "operation_id": operation_id,
        "evidence_class": "EXPLORATORY_SINGLE_RUN",
        "source_commit": std::env::var("P1_NATIVE_SOURCE_COMMIT").unwrap_or_default(),
        "corpus_commit": std::env::var("P1_NATIVE_CORPUS_COMMIT").unwrap_or_default(),
        "trace": ["initial_index", "single_file_update", "burst_upsert_4_files", "rename", "delete", "idle_2s", "reopen", "repeat_rescan_1", "repeat_rescan_2"],
        "initial_index_wall_us": initial_index_us,
        "reopen_us": reopen_us,
        "repeat_rescan_pair_wall_us": repeat_rescan_us,
        "freshness": {
            "definition": "from WorkspaceRuntime watch_loop receipt of normalized batch to first successful FTS query response reflecting the mutation; query cadence quantizes visibility by at most one scheduled interval",
            "slo_max_us": 5_000_000,
            "events": freshness,
            "observed_max_us": freshness_values.iter().max(),
            "slo_status": freshness_slo_status,
        },
        "query": {
            "route": "FTS",
            "filter": "**/*.rs",
            "query": P1_NATIVE_QUERY,
            "interval_ms": P1_NATIVE_QUERY_INTERVAL.as_millis(),
            "max_samples": P1_NATIVE_QUERY_MAX_SAMPLES,
            "response_scope": "search_workspace_index wall time including native store lock wait, retrieval, and result hydration; excludes daemon transport/serialization",
            "attempts": query_metrics.attempts,
            "successful_samples": query_metrics.response_us.len(),
            "successful_response_us": query_metrics.response_us,
            "p95_us_nearest_rank": query_p95_us,
            "missed_schedule_slots": query_metrics.missed_slots,
            "max_schedule_lag_us": max_schedule_lag_us,
            "failures": query_metrics.failures,
            "sample_cap_reached": query_metrics.capped,
            "slo_max_us": 300_000,
            "slo_status": query_slo_status,
        },
        "limitations": ["one frozen 64-file Rust fixture", "one operation; no repeats", "deterministic fixed test embedding; no model inference", "query route is FTS", "single daemon test process; not transport-level RPC latency"],
    });
    std::fs::write(
        &report_path,
        serde_json::to_vec_pretty(&report).expect("serialize report"),
    )
    .expect("write baseline report");
    assert!(
        freshness_values.len() == 4,
        "all four watcher-to-visible events must be measured before considering SLO status"
    );
}
