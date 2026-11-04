//! Ignored, operation-driven lifecycle harness for Gate B.
//! Run only through an approved M4 containment operation with an immutable
//! committed-HEAD corpus and fresh, quota-contained arm/index/output roots.

use super::*;
use std::{
    fs,
    sync::{
        Arc, Mutex as StdMutex,
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        mpsc::{SyncSender, TrySendError, sync_channel},
    },
    time::Instant,
};

use crate::{EngineError, storage::types::WorkspaceIndexStorageOptions};
use serde_json::{Value, json};

const QUERY: &str = "gatebmarker";

fn query_filter() -> String {
    std::env::var("M4_GATE_B_QUERY_FILTER").unwrap_or_else(|_| "**/*.rs".to_string())
}

fn corpus_extension() -> String {
    std::env::var("M4_GATE_B_CORPUS_EXTENSION")
        .unwrap_or_else(|_| ".rs".to_string())
        .trim_start_matches('.')
        .to_string()
}
type StateLockTrace = (Vec<(&'static str, &'static str, u128, u128)>, usize);
type NativeFtsSpanTrace = (Vec<(u128, u128, u64)>, usize);

#[derive(Clone, Copy)]
struct OfferedQuery {
    slot: u64,
    scheduled_at: Instant,
    enqueued_at: Instant,
    schedule_lag_us: u128,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum QueryOfferFailure {
    QueueFull,
    ReceiverDisconnected,
}

#[derive(Clone, Default)]
struct QueryWorkerMetrics {
    response_us: Vec<u128>,
    successful_response_us: Vec<u128>,
    service_us: Vec<u128>,
    queue_delay_us: Vec<u128>,
    start_lag_us: Vec<u128>,
    fence_wait_us: Vec<u128>,
    store_lock_acquisitions: Vec<u64>,
    store_lock_wait_total_ns: Vec<u64>,
    store_lock_wait_max_ns: Vec<u64>,
    native_fts_calls: Vec<u64>,
    native_fts_wall_ns: Vec<u64>,
    native_fts_query_spans: Vec<NativeFtsSpanTrace>,
    query_lock_traces: Arc<StdMutex<Vec<StateLockTrace>>>,
    schedule_lag_us: Vec<u128>,
    failures: Vec<String>,
}

fn query_latency_summary(values: &[u128]) -> Value {
    json!({
        "samples": values.len(),
        "p50_us": percentile(values, 50),
        "p95_us": percentile(values, 95),
        "max_us": values.iter().max().copied(),
    })
}

fn query_load_snapshot(
    pass: &str,
    phase: &str,
    elapsed_ms: u64,
    late_threshold_us: u128,
    offered: u64,
    accepted: u64,
    dropped: u64,
    queue_full: u64,
    receiver_disconnected: u64,
    not_offered_by_cap: u64,
    started: u64,
    completed: u64,
    failures: u64,
    queue_depth: usize,
    metrics: &QueryWorkerMetrics,
) -> Value {
    json!({
        "schema_version": 2,
        "pass": pass,
        "snapshot_phase": phase,
        "elapsed_ms": elapsed_ms,
        "late_threshold_us": late_threshold_us,
        "offered_slots": offered,
        "accepted_slots": accepted,
        "dropped_slots": dropped,
        "missed_start_slots": dropped.saturating_add(not_offered_by_cap),
        "queue_full_slots": queue_full,
        "receiver_disconnected_slots": receiver_disconnected,
        "not_offered_by_cap": not_offered_by_cap,
        "started_queries": started,
        "completed_queries": completed,
        "failed_queries": failures,
        "queued_queries": queue_depth,
        "inflight_queries": started.saturating_sub(completed),
        "unstarted_accepted_slots": accepted.saturating_sub(started),
        "late_start_slots": metrics
            .start_lag_us
            .iter()
            .filter(|lag| **lag >= late_threshold_us)
            .count(),
        "late_completion_slots": metrics
            .response_us
            .iter()
            .filter(|elapsed| **elapsed >= late_threshold_us)
            .count(),
        "attempt_response_latency": query_latency_summary(&metrics.response_us),
        "successful_response_latency": query_latency_summary(&metrics.successful_response_us),
        "service_latency": query_latency_summary(&metrics.service_us),
        "queue_delay": query_latency_summary(&metrics.queue_delay_us),
        "start_lag": query_latency_summary(&metrics.start_lag_us),
        "fence_wait": query_latency_summary(&metrics.fence_wait_us),
        "schedule_lag": query_latency_summary(&metrics.schedule_lag_us),
    })
}

#[derive(Default)]
struct PassMetrics {
    elapsed_us: u128,
    trace_origin_monotonic_ns: u64,
    trace_origin_monotonic_bracket_ns: u64,
    index_pipeline_timings: Vec<crate::api::index::result::TimingEntry>,
    writer_lock_trace: StateLockTrace,
    query_lock_traces: Vec<StateLockTrace>,
    reader_query_us: Vec<u128>,
    reader_query_attempt_response_us: Vec<u128>,
    reader_query_service_us: Vec<u128>,
    reader_query_queue_delay_us: Vec<u128>,
    reader_query_start_lag_us: Vec<u128>,
    reader_query_late_threshold_us: u128,
    reader_query_fence_wait_us: Vec<u128>,
    reader_query_store_lock_acquisitions: Vec<u64>,
    reader_query_store_lock_wait_total_ns: Vec<u64>,
    reader_query_store_lock_wait_max_ns: Vec<u64>,
    reader_query_native_fts_calls: Vec<u64>,
    reader_query_native_fts_wall_ns: Vec<u64>,
    reader_query_native_fts_spans: Vec<NativeFtsSpanTrace>,
    reader_query_schedule_lag_us: Vec<u128>,
    reader_query_offered_slots: u64,
    reader_query_accepted_slots: u64,
    reader_query_dropped_slots: Vec<u64>,
    reader_query_queue_full_slots: u64,
    reader_query_receiver_disconnected_slots: u64,
    reader_query_not_offered_by_cap: u64,
    reader_query_max_queue_depth: usize,
    reader_query_queue_capacity: usize,
    reader_query_failures: Vec<String>,
}

fn due_slots(elapsed: Duration, interval: Duration) -> u64 {
    ((elapsed.as_nanos() / interval.as_nanos()) + 1).min(u64::MAX as u128) as u64
}

fn offer_query(
    sender: &SyncSender<OfferedQuery>,
    query: OfferedQuery,
    queue_depth: &AtomicUsize,
    max_queue_depth: &AtomicUsize,
) -> Result<(), (OfferedQuery, QueryOfferFailure)> {
    let pending = queue_depth.fetch_add(1, Ordering::AcqRel) + 1;
    match sender.try_send(query) {
        Ok(()) => {
            max_queue_depth.fetch_max(pending, Ordering::Relaxed);
            Ok(())
        }
        Err(TrySendError::Full(query)) => {
            queue_depth.fetch_sub(1, Ordering::AcqRel);
            Err((query, QueryOfferFailure::QueueFull))
        }
        Err(TrySendError::Disconnected(query)) => {
            queue_depth.fetch_sub(1, Ordering::AcqRel);
            Err((query, QueryOfferFailure::ReceiverDisconnected))
        }
    }
}

#[cfg(test)]
mod offered_load_tests {
    use super::*;

    #[test]
    fn offered_deadlines_and_cap_account_for_every_due_slot() {
        let interval = Duration::from_millis(25);
        let due = due_slots(Duration::from_millis(55), interval);
        let offered = due.min(2);
        assert_eq!(due, 3);
        assert_eq!(offered, 2);
        assert_eq!(due - offered, 1);
    }

    #[test]
    fn queue_full_is_a_counted_drop_not_a_silently_skipped_sample() {
        let (sender, receiver) = sync_channel(1);
        let queue_depth = AtomicUsize::new(0);
        let max_queue_depth = AtomicUsize::new(0);
        let now = Instant::now();
        let first = OfferedQuery {
            slot: 0,
            scheduled_at: now,
            enqueued_at: now,
            schedule_lag_us: 0,
        };
        let second = OfferedQuery {
            slot: 1,
            scheduled_at: now,
            enqueued_at: now,
            schedule_lag_us: 10,
        };

        assert!(offer_query(&sender, first, &queue_depth, &max_queue_depth).is_ok());
        let (dropped, reason) = offer_query(&sender, second, &queue_depth, &max_queue_depth)
            .expect_err("full queue must reject and expose the offer");
        assert_eq!(reason, QueryOfferFailure::QueueFull);
        assert_eq!(dropped.slot, 1);
        assert_eq!(queue_depth.load(Ordering::Relaxed), 1);
        assert_eq!(max_queue_depth.load(Ordering::Relaxed), 1);
        assert_eq!(receiver.recv().expect("accepted offer").slot, 0);
        queue_depth.fetch_sub(1, Ordering::AcqRel);
        assert_eq!(queue_depth.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn disconnected_query_receiver_is_counted_separately_from_queue_saturation() {
        let (sender, receiver) = sync_channel(1);
        drop(receiver);
        let queue_depth = AtomicUsize::new(0);
        let max_queue_depth = AtomicUsize::new(0);
        let now = Instant::now();
        let query = OfferedQuery {
            slot: 3,
            scheduled_at: now,
            enqueued_at: now,
            schedule_lag_us: 0,
        };

        let (dropped, reason) = offer_query(&sender, query, &queue_depth, &max_queue_depth)
            .expect_err("disconnected receiver must reject the offer");
        assert_eq!(reason, QueryOfferFailure::ReceiverDisconnected);
        assert_eq!(dropped.slot, 3);
        assert_eq!(queue_depth.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn query_load_snapshot_preserves_offered_started_completed_and_dropped_counts() {
        let mut metrics = QueryWorkerMetrics::default();
        metrics.response_us = vec![100, 200, 300];
        metrics.start_lag_us = vec![1, 250, 300];
        metrics.successful_response_us = vec![100, 300];
        metrics.service_us = vec![10, 20, 30];
        metrics.queue_delay_us = vec![5, 15, 25];
        metrics.fence_wait_us = vec![0, 10, 20];
        metrics.schedule_lag_us = vec![1, 2, 3];
        let snapshot = query_load_snapshot(
            "rescan", "test", 1_250, 250, 50, 48, 2, 1, 1, 4, 10, 7, 1, 8, &metrics,
        );
        assert_eq!(snapshot["schema_version"], 2);
        assert_eq!(snapshot["snapshot_phase"], "test");
        assert_eq!(snapshot["offered_slots"], 50);
        assert_eq!(snapshot["accepted_slots"], 48);
        assert_eq!(snapshot["dropped_slots"], 2);
        assert_eq!(snapshot["missed_start_slots"], 6);
        assert_eq!(snapshot["queue_full_slots"], 1);
        assert_eq!(snapshot["receiver_disconnected_slots"], 1);
        assert_eq!(
            snapshot["queue_full_slots"].as_u64().unwrap()
                + snapshot["receiver_disconnected_slots"].as_u64().unwrap(),
            snapshot["dropped_slots"].as_u64().unwrap(),
        );
        assert_eq!(snapshot["not_offered_by_cap"], 4);
        assert_eq!(snapshot["started_queries"], 10);
        assert_eq!(snapshot["completed_queries"], 7);
        assert_eq!(snapshot["failed_queries"], 1);
        assert_eq!(snapshot["inflight_queries"], 3);
        assert_eq!(snapshot["queued_queries"], 8);
        assert_eq!(snapshot["unstarted_accepted_slots"], 38);
        assert_eq!(snapshot["late_start_slots"], 2);
        assert_eq!(snapshot["late_completion_slots"], 1);
        assert_eq!(snapshot["start_lag"]["p95_us"], 300);
        assert_eq!(snapshot["attempt_response_latency"]["samples"], 3);
        assert_eq!(snapshot["attempt_response_latency"]["p95_us"], 300);
        assert_eq!(snapshot["successful_response_latency"]["p95_us"], 300);
        assert_eq!(snapshot["service_latency"]["p95_us"], 30);
        assert_eq!(snapshot["queue_delay"]["p95_us"], 25);
        assert_eq!(snapshot["fence_wait"]["p95_us"], 20);
        assert_eq!(snapshot["schedule_lag"]["p95_us"], 3);
    }
}

trait LifecycleQueryTelemetry {
    fn take_fence_wait_us(&self) -> u64 {
        0
    }

    fn take_store_lock_wait(&self) -> (u64, u64, u64) {
        (0, 0, 0)
    }

    fn take_native_fts_query(&self) -> (u64, u64) {
        (0, 0)
    }

    fn take_native_fts_spans(&self) -> NativeFtsSpanTrace {
        (Vec::new(), 0)
    }

    fn begin_lock_trace(&self, _origin: Instant, _cap: usize) {}

    fn take_lock_trace(&self, _finish: bool) -> StateLockTrace {
        (Vec::new(), 0)
    }
}

impl LifecycleQueryTelemetry for IndexStore {
    fn take_store_lock_wait(&self) -> (u64, u64, u64) {
        self.take_state_lock_wait_telemetry_for_test()
    }

    fn take_native_fts_query(&self) -> (u64, u64) {
        self.take_native_fts_query_telemetry_for_test()
    }

    fn take_native_fts_spans(&self) -> NativeFtsSpanTrace {
        self.take_native_fts_spans_for_test()
    }

    fn begin_lock_trace(&self, origin: Instant, cap: usize) {
        self.begin_state_lock_trace_for_test(origin, cap);
    }

    fn take_lock_trace(&self, finish: bool) -> StateLockTrace {
        self.take_state_lock_trace_for_test(finish)
    }
}

impl LifecycleQueryTelemetry for M4ResearchStorage {
    fn take_fence_wait_us(&self) -> u64 {
        self.pass_gate.take_blocked_wait_us()
    }

    fn take_store_lock_wait(&self) -> (u64, u64, u64) {
        self.native.take_state_lock_wait_telemetry_for_test()
    }

    fn take_native_fts_query(&self) -> (u64, u64) {
        self.native.take_native_fts_query_telemetry_for_test()
    }

    fn take_native_fts_spans(&self) -> NativeFtsSpanTrace {
        self.native.take_native_fts_spans_for_test()
    }

    fn begin_lock_trace(&self, origin: Instant, cap: usize) {
        self.native.begin_state_lock_trace_for_test(origin, cap);
    }

    fn take_lock_trace(&self, finish: bool) -> StateLockTrace {
        self.native.take_state_lock_trace_for_test(finish)
    }
}

fn measured_pass<S>(
    pass_name: &str,
    workspace: &Workspace,
    storage: &S,
    scanner: &NativeScanner,
    model: &FixedEmbeddingModel,
    changes: &[WorkspaceChange],
) -> EngineResult<PassMetrics>
where
    S: IndexStorage + SearchStorage + LifecycleQueryTelemetry,
{
    let done = Arc::new(AtomicBool::new(false));
    let done_at = Arc::new(StdMutex::new(None::<Instant>));
    // These three participants must start together; a two-party barrier lets the
    // late scheduler block on the next generation and silently omit query offers.
    let start = Arc::new(std::sync::Barrier::new(3));
    let runtime = tokio::runtime::Handle::current();
    let reader_interval = Duration::from_millis(
        std::env::var("M4_GATE_B_READER_QUERY_INTERVAL_MS")
            .expect("M4_GATE_B_READER_QUERY_INTERVAL_MS")
            .parse::<u64>()
            .expect("valid reader query interval"),
    );
    let max_reader_slots = std::env::var("M4_GATE_B_READER_QUERY_MAX_SAMPLES")
        .expect("M4_GATE_B_READER_QUERY_MAX_SAMPLES")
        .parse::<usize>()
        .expect("valid reader query sample limit");
    let queue_capacity = std::env::var("M4_GATE_B_READER_QUERY_QUEUE_CAPACITY")
        .expect("M4_GATE_B_READER_QUERY_QUEUE_CAPACITY")
        .parse::<usize>()
        .expect("valid reader query queue capacity");
    assert!(!reader_interval.is_zero() && max_reader_slots > 0 && queue_capacity > 0);
    let late_threshold_us = reader_interval.as_micros();
    let monotonic_origin_before_ns = crate::storage::monotonic_time_ns_for_test();
    let trace_origin = Instant::now();
    let monotonic_origin_after_ns = crate::storage::monotonic_time_ns_for_test();
    let trace_origin_monotonic_bracket_ns =
        monotonic_origin_after_ns.saturating_sub(monotonic_origin_before_ns);
    let trace_origin_monotonic_ns =
        monotonic_origin_before_ns.saturating_add(trace_origin_monotonic_bracket_ns / 2);

    let (index_result, elapsed_us, schedule, worker, worker_metrics, writer_lock_trace) =
        tokio::task::block_in_place(|| {
            std::thread::scope(|scope| {
                let (sender, receiver) = sync_channel::<OfferedQuery>(queue_capacity);
                let queue_depth = Arc::new(AtomicUsize::new(0));
                let max_queue_depth = Arc::new(AtomicUsize::new(0));
                let started_queries = Arc::new(AtomicU64::new(0));
                let completed_queries = Arc::new(AtomicU64::new(0));
                let failed_queries = Arc::new(AtomicU64::new(0));
                let worker_started_queries = Arc::clone(&started_queries);
                let worker_completed_queries = Arc::clone(&completed_queries);
                let worker_failed_queries = Arc::clone(&failed_queries);
                let worker_queue_depth = Arc::clone(&queue_depth);
                let worker_start = Arc::clone(&start);
                let worker_metrics = Arc::new(StdMutex::new(QueryWorkerMetrics::default()));
                let worker_metrics_sink = Arc::clone(&worker_metrics);
                let worker_runtime = runtime.clone();
                let worker = scope.spawn(move || {
                    storage.begin_lock_trace(trace_origin, 64);
                    worker_start.wait();
                    for offered in receiver {
                        worker_queue_depth.fetch_sub(1, Ordering::AcqRel);
                        worker_started_queries.fetch_add(1, Ordering::Relaxed);
                        let query_started = Instant::now();
                        let query_start_lag_us = offered.scheduled_at.elapsed().as_micros();
                        let queue_delay_us = query_started
                            .saturating_duration_since(offered.enqueued_at)
                            .as_micros();
                        let _ = storage.take_fence_wait_us();
                        let _ = storage.take_store_lock_wait();
                        let _ = storage.take_native_fts_query();
                        let _ = storage.take_native_fts_spans();
                        let _ = storage.take_lock_trace(false);
                        let result = worker_runtime.block_on(search_workspace_index(
                            &workspace.root,
                            plan(
                                QUERY,
                                vec![crate::domain::GlobRule::from(query_filter().as_str())],
                            ),
                            storage,
                            &[],
                        ));
                        let service_us = query_started.elapsed().as_micros();
                        let fence_wait_us = u128::from(storage.take_fence_wait_us());
                        let (lock_acquisitions, lock_wait_total_ns, lock_wait_max_ns) =
                            storage.take_store_lock_wait();
                        let (native_fts_calls, native_fts_wall_ns) =
                            storage.take_native_fts_query();
                        let native_fts_spans = storage.take_native_fts_spans();
                        let query_lock_trace = storage.take_lock_trace(false);
                        let response_us = offered.scheduled_at.elapsed().as_micros();
                        let mut metrics = worker_metrics_sink.lock().expect("query worker metrics");
                        metrics.schedule_lag_us.push(offered.schedule_lag_us);
                        metrics.queue_delay_us.push(queue_delay_us);
                        metrics.start_lag_us.push(query_start_lag_us);
                        metrics.service_us.push(service_us);
                        metrics.fence_wait_us.push(fence_wait_us);
                        metrics.store_lock_acquisitions.push(lock_acquisitions);
                        metrics.store_lock_wait_total_ns.push(lock_wait_total_ns);
                        metrics.store_lock_wait_max_ns.push(lock_wait_max_ns);
                        metrics.native_fts_calls.push(native_fts_calls);
                        metrics.native_fts_wall_ns.push(native_fts_wall_ns);
                        metrics.native_fts_query_spans.push(native_fts_spans);
                        metrics
                            .query_lock_traces
                            .lock()
                            .expect("query lock trace")
                            .push(query_lock_trace);
                        metrics.response_us.push(response_us);
                        worker_completed_queries.fetch_add(1, Ordering::Relaxed);
                        match result {
                            Ok(result)
                                if result.hits.iter().all(|hit| !hit.evidence.is_empty()) =>
                            {
                                metrics.successful_response_us.push(response_us);
                            }
                            Ok(_) => {
                                worker_failed_queries.fetch_add(1, Ordering::Relaxed);
                                metrics.failures.push(format!(
                                    "query slot {} returned a non-hydratable hit",
                                    offered.slot
                                ));
                            }
                            Err(error) => {
                                worker_failed_queries.fetch_add(1, Ordering::Relaxed);
                                metrics
                                    .failures
                                    .push(format!("query slot {} failed: {error}", offered.slot));
                            }
                        }
                    }
                    let _ = storage.take_lock_trace(true);
                });

                let scheduler_query_metrics = Arc::clone(&worker_metrics);
                let scheduler_done = Arc::clone(&done);
                let scheduler_done_at = Arc::clone(&done_at);
                let scheduler_start = Arc::clone(&start);
                let scheduler_queue_depth = Arc::clone(&queue_depth);
                let scheduler_max_queue_depth = Arc::clone(&max_queue_depth);
                let scheduler = scope.spawn(move || {
                    scheduler_start.wait();
                    let schedule_origin = Instant::now();
                    let mut slot = 0_u64;
                    let mut offered_slots = 0_u64;
                    let mut accepted_slots = 0_u64;
                    let mut dropped_slots = Vec::new();
                    let mut queue_full_slots = 0_u64;
                    let mut receiver_disconnected_slots = 0_u64;
                    let mut schedule_lag_us = Vec::new();
                    let mut last_snapshot = schedule_origin;
                    loop {
                        let elapsed = schedule_origin.elapsed();
                        let all_due_slots = due_slots(elapsed, reader_interval);
                        let due_slots = all_due_slots.min(max_reader_slots as u64);
                        if last_snapshot.elapsed() >= Duration::from_secs(1) {
                            let capped_misses = if slot >= max_reader_slots as u64 {
                                all_due_slots.saturating_sub(slot)
                            } else {
                                0
                            };
                            let metrics = scheduler_query_metrics
                                .lock()
                                .expect("query worker metrics")
                                .clone();
                            eprintln!(
                                "QUERY_LOAD_SNAPSHOT {}",
                                query_load_snapshot(
                                    pass_name,
                                    "in_progress",
                                    u64::try_from(elapsed.as_millis()).unwrap_or(u64::MAX),
                                    late_threshold_us,
                                    offered_slots,
                                    accepted_slots,
                                    dropped_slots.len() as u64,
                                    queue_full_slots,
                                    receiver_disconnected_slots,
                                    capped_misses,
                                    started_queries.load(Ordering::Relaxed),
                                    completed_queries.load(Ordering::Relaxed),
                                    failed_queries.load(Ordering::Relaxed),
                                    scheduler_queue_depth.load(Ordering::Relaxed),
                                    &metrics,
                                )
                            );
                            last_snapshot = Instant::now();
                        }
                        if slot < due_slots {
                            let deadline =
                                schedule_origin + reader_interval * u32::try_from(slot).unwrap();
                            let now = Instant::now();
                            let lag_us = now.saturating_duration_since(deadline).as_micros();
                            schedule_lag_us.push(lag_us);
                            offered_slots += 1;
                            let offered = OfferedQuery {
                                slot,
                                scheduled_at: deadline,
                                enqueued_at: now,
                                schedule_lag_us: lag_us,
                            };
                            match offer_query(
                                &sender,
                                offered,
                                &scheduler_queue_depth,
                                &scheduler_max_queue_depth,
                            ) {
                                Ok(()) => accepted_slots += 1,
                                Err((dropped, reason)) => {
                                    match reason {
                                        QueryOfferFailure::QueueFull => queue_full_slots += 1,
                                        QueryOfferFailure::ReceiverDisconnected => {
                                            receiver_disconnected_slots += 1
                                        }
                                    }
                                    dropped_slots.push(dropped.slot);
                                }
                            }
                            slot += 1;
                            continue;
                        }
                        if scheduler_done.load(Ordering::Acquire) {
                            break;
                        }
                        std::thread::sleep(reader_interval.min(Duration::from_millis(1)));
                    }
                    let completed_at = scheduler_done_at
                        .lock()
                        .expect("index completion timestamp")
                        .unwrap_or_else(Instant::now);
                    let total_due = due_slots(
                        completed_at.saturating_duration_since(schedule_origin),
                        reader_interval,
                    );
                    let not_offered_by_cap = total_due.saturating_sub(slot);
                    let metrics = scheduler_query_metrics
                        .lock()
                        .expect("query worker metrics")
                        .clone();
                    eprintln!(
                        "QUERY_LOAD_SNAPSHOT {}",
                        query_load_snapshot(
                            pass_name,
                            "index_complete",
                            u64::try_from(completed_at.duration_since(schedule_origin).as_millis())
                                .unwrap_or(u64::MAX),
                            late_threshold_us,
                            offered_slots,
                            accepted_slots,
                            dropped_slots.len() as u64,
                            queue_full_slots,
                            receiver_disconnected_slots,
                            not_offered_by_cap,
                            started_queries.load(Ordering::Relaxed),
                            completed_queries.load(Ordering::Relaxed),
                            failed_queries.load(Ordering::Relaxed),
                            scheduler_queue_depth.load(Ordering::Relaxed),
                            &metrics,
                        )
                    );
                    (
                        offered_slots,
                        accepted_slots,
                        dropped_slots,
                        queue_full_slots,
                        receiver_disconnected_slots,
                        schedule_lag_us,
                        not_offered_by_cap,
                        scheduler_max_queue_depth.load(Ordering::Relaxed),
                    )
                });

                storage.begin_lock_trace(trace_origin, 4096);
                start.wait();
                let started = Instant::now();
                let models: [&dyn IndexEmbeddingRuntime; 1] = [model];
                let result = runtime.block_on(index_workspace(&IndexingContext {
                workspace_index: workspace,
                storage,
                scanner,
                embedding_models: &models,
                embedding_concurrency: Some(1),
                on_progress: Some(IndexProgressReporter::new(move |event| {
                    let count = event.files_indexed;
                    if count.is_none_or(|value| value % 25 == 0) {
                        eprintln!(
                            "INDEX_PROGRESS phase={:?} indexed={count:?} total={:?} failed={:?} detail={:?}",
                            event.phase, event.files_total, event.files_failed, event.detail,
                        );
                    }
                })),
                signal: None,
                changes,
            }));
                let elapsed_us = started.elapsed().as_micros();
                *done_at.lock().expect("index completion timestamp") = Some(Instant::now());
                done.store(true, Ordering::Release);
                let writer_lock_trace = storage.take_lock_trace(true);
                (
                    result,
                    elapsed_us,
                    scheduler.join(),
                    worker.join(),
                    worker_metrics,
                    writer_lock_trace,
                )
            })
        });

    let schedule =
        schedule.map_err(|_| EngineError::storage_failure("query scheduler thread panicked"))?;
    worker.map_err(|_| EngineError::storage_failure("query worker thread panicked"))?;
    let worker = worker_metrics.lock().expect("query worker metrics").clone();
    let final_snapshot = query_load_snapshot(
        pass_name,
        "worker_drained",
        u64::try_from(elapsed_us / 1_000).unwrap_or(u64::MAX),
        late_threshold_us,
        schedule.0,
        schedule.1,
        schedule.2.len() as u64,
        schedule.3,
        schedule.4,
        schedule.6,
        worker.response_us.len() as u64,
        worker.response_us.len() as u64,
        worker.failures.len() as u64,
        0,
        &worker,
    );
    eprintln!("QUERY_LOAD_SNAPSHOT {}", final_snapshot);
    let index_result = index_result?;
    let query_lock_traces = worker
        .query_lock_traces
        .lock()
        .expect("query lock trace")
        .clone();
    Ok(PassMetrics {
        elapsed_us,
        trace_origin_monotonic_ns,
        trace_origin_monotonic_bracket_ns,
        index_pipeline_timings: index_result.timings,
        writer_lock_trace,
        query_lock_traces,
        reader_query_us: worker.successful_response_us,
        reader_query_attempt_response_us: worker.response_us,
        reader_query_service_us: worker.service_us,
        reader_query_queue_delay_us: worker.queue_delay_us,
        reader_query_start_lag_us: worker.start_lag_us,
        reader_query_late_threshold_us: late_threshold_us,
        reader_query_fence_wait_us: worker.fence_wait_us,
        reader_query_store_lock_acquisitions: worker.store_lock_acquisitions,
        reader_query_store_lock_wait_total_ns: worker.store_lock_wait_total_ns,
        reader_query_store_lock_wait_max_ns: worker.store_lock_wait_max_ns,
        reader_query_native_fts_calls: worker.native_fts_calls,
        reader_query_native_fts_wall_ns: worker.native_fts_wall_ns,
        reader_query_native_fts_spans: worker.native_fts_query_spans,
        reader_query_schedule_lag_us: schedule.5,
        reader_query_offered_slots: schedule.0,
        reader_query_accepted_slots: schedule.1,
        reader_query_dropped_slots: schedule.2,
        reader_query_queue_full_slots: schedule.3,
        reader_query_receiver_disconnected_slots: schedule.4,
        reader_query_not_offered_by_cap: schedule.6,
        reader_query_max_queue_depth: schedule.7,
        reader_query_queue_capacity: queue_capacity,
        reader_query_failures: worker.failures,
    })
}

async fn marker_hits<S: SearchStorage>(
    workspace: &Workspace,
    storage: &S,
) -> EngineResult<Vec<PathBuf>> {
    let result = search_workspace_index(
        &workspace.root,
        plan(
            QUERY,
            vec![crate::domain::GlobRule::from(query_filter().as_str())],
        ),
        storage,
        &[],
    )
    .await?;
    Ok(result
        .hits
        .into_iter()
        .map(|hit| hit.file.relative_path.as_path().to_path_buf())
        .collect())
}

fn append_marker(root: &Path, relative: &Path, suffix: &str) -> EngineResult<()> {
    use std::io::Write;

    let path = root.join(relative);
    let mut file = fs::OpenOptions::new()
        .append(true)
        .open(&path)
        .map_err(|error| EngineError::storage_failure(error.to_string()))?;
    let marker = if relative.extension().and_then(|ext| ext.to_str()) == Some("rs") {
        format!("\npub fn m4_gateb_marker_{suffix}() {{ let _marker = \"{QUERY}\"; }}")
    } else {
        format!("\n// m4_gateb_marker_{suffix} {QUERY}")
    };
    writeln!(file, "{marker}").map_err(|error| EngineError::storage_failure(error.to_string()))?;
    file.sync_all()
        .map_err(|error| EngineError::storage_failure(error.to_string()))
}

fn percentile(values: &[u128], percentile: usize) -> Option<u128> {
    if values.is_empty() {
        return None;
    }
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    let rank = (sorted.len() * percentile).div_ceil(100).max(1) - 1;
    sorted.get(rank).copied()
}

async fn execute_pass<S, C>(
    name: &str,
    workspace: &Workspace,
    storage: &S,
    scanner: &NativeScanner,
    model: &FixedEmbeddingModel,
    changes: &[WorkspaceChange],
    compact: &C,
    passes: &mut Vec<Value>,
    compaction_us: &mut Vec<Option<u128>>,
) -> EngineResult<()>
where
    S: IndexStorage + SearchStorage + LifecycleQueryTelemetry,
    C: Fn() -> EngineResult<Option<u128>>,
{
    let metrics = measured_pass(name, workspace, storage, scanner, model, changes)?;
    let post_fence_us = metrics
        .reader_query_attempt_response_us
        .iter()
        .zip(&metrics.reader_query_fence_wait_us)
        .map(|(wall, wait)| wall.saturating_sub(*wait))
        .collect::<Vec<_>>();
    let late_start_slots = metrics
        .reader_query_start_lag_us
        .iter()
        .filter(|lag| **lag >= metrics.reader_query_late_threshold_us)
        .count();
    let late_completion_slots = metrics
        .reader_query_attempt_response_us
        .iter()
        .filter(|elapsed| **elapsed >= metrics.reader_query_late_threshold_us)
        .count();
    let missed_start_slots =
        metrics.reader_query_dropped_slots.len() as u64 + metrics.reader_query_not_offered_by_cap;
    let mut pass_report = json!({
        "name": name,
        "trace_origin_monotonic_ns": metrics.trace_origin_monotonic_ns,
        "trace_origin_monotonic_bracket_ns": metrics.trace_origin_monotonic_bracket_ns,
        "trace_origin_monotonic_ns_scope": "Linux CLOCK_MONOTONIC midpoint around the Instant origin used by relative FTS/writer trace offsets; align these pass offsets with native C++ segment_flush_stage wall_start_ns/wall_end_ns. The bracket width bounds the clock-read uncertainty and does not alter the workload.",
        "index_and_finalize_wall_us": metrics.elapsed_us,
        "concurrent_query_sampling_capped": metrics.reader_query_not_offered_by_cap > 0,
        "concurrent_query_samples": metrics.reader_query_us.len(),
        "concurrent_query_attempted": metrics.reader_query_attempt_response_us.len(),
        "concurrent_query_offered_slots": metrics.reader_query_offered_slots,
        "concurrent_query_accepted_slots": metrics.reader_query_accepted_slots,
        "concurrent_query_missed_start_slots": missed_start_slots,
        "concurrent_query_late_start_slots": late_start_slots,
        "concurrent_query_late_completion_slots": late_completion_slots,
        "concurrent_query_late_threshold_us": metrics.reader_query_late_threshold_us,
        "concurrent_query_dropped_slots": metrics.reader_query_dropped_slots,
        "concurrent_query_queue_full_slots": metrics.reader_query_queue_full_slots,
        "concurrent_query_receiver_disconnected_slots": metrics.reader_query_receiver_disconnected_slots,
        "concurrent_query_slots_not_offered_by_cap": metrics.reader_query_not_offered_by_cap,
        "concurrent_query_max_queue_depth": metrics.reader_query_max_queue_depth,
        "concurrent_query_queue_capacity": metrics.reader_query_queue_capacity,
        "concurrent_query_queue_overloaded": !metrics.reader_query_dropped_slots.is_empty()
            || metrics.reader_query_not_offered_by_cap > 0
            || metrics.reader_query_max_queue_depth >= metrics.reader_query_queue_capacity,
        "concurrent_query_failures": metrics.reader_query_failures,
        "concurrent_query_p50_us": percentile(&metrics.reader_query_us, 50),
        "concurrent_query_p95_us": percentile(&metrics.reader_query_us, 95),
        "concurrent_query_wall_us": metrics.reader_query_us,
        "concurrent_query_attempt_response_us": metrics.reader_query_attempt_response_us,
        "concurrent_query_service_us": metrics.reader_query_service_us,
        "concurrent_query_queue_delay_us": metrics.reader_query_queue_delay_us,
        "concurrent_query_start_lag_us": metrics.reader_query_start_lag_us,
        "concurrent_query_fence_wait_us": metrics.reader_query_fence_wait_us,
        "concurrent_query_store_lock_acquisitions": metrics.reader_query_store_lock_acquisitions,
        "concurrent_query_store_lock_wait_total_ns": metrics.reader_query_store_lock_wait_total_ns,
        "concurrent_query_store_lock_wait_max_ns": metrics.reader_query_store_lock_wait_max_ns,
        "concurrent_query_store_lock_wait_scope": "test-only thread-local time waiting in IndexStore read/write state-mutex acquisition on the query worker thread; excludes native index service time and other worker threads",
        "concurrent_query_native_fts_calls": metrics.reader_query_native_fts_calls,
        "concurrent_query_native_fts_wall_ns": metrics.reader_query_native_fts_wall_ns,
        "concurrent_query_native_fts_spans": metrics.reader_query_native_fts_spans,
        "concurrent_query_native_fts_spans_scope": "per-query native FTS call records as [start_ns,end_ns,thread_cpu_ns]; wall offsets use the same per-pass monotonic origin as state_lock_writer_trace; thread_cpu_ns is Linux CLOCK_THREAD_CPUTIME_ID around the native collection call (zero when unavailable); excludes request setup and hydration; records are bounded by the same per-thread trace cap",
        "concurrent_query_native_fts_scope": "test-only thread-local wall duration inside native collection FTS query calls on the query worker; includes native internal waits, excludes store state-lock wait, request setup and hydration; per-call Linux thread CPU durations are included in native_fts_spans; zero calls means not observed, not zero service time",
        "concurrent_query_post_fence_us": post_fence_us,
        "concurrent_query_schedule_lag_us": metrics.reader_query_schedule_lag_us,
    });
    pass_report["state_lock_writer_trace"] = json!(metrics.writer_lock_trace);
    pass_report["state_lock_query_traces"] = json!(metrics.query_lock_traces);
    pass_report["state_lock_trace_scope"] = json!(
        "per-pass common monotonic origin; tuple=(stage,kind,start_ns,end_ns); writer capped at 4096 spans, each query capped at 64; second tuple element is omitted count; only indexing and query worker threads are covered"
    );
    pass_report["index_pipeline_timings"] = json!(metrics.index_pipeline_timings);
    pass_report["index_pipeline_timings_scope"] = json!(
        "existing aggregate pipeline stage wall timings; include work outside state mutex and cannot identify the holder of a query's lock wait"
    );
    passes.push(pass_report);
    compaction_us.push(compact()?);
    Ok(())
}

async fn run_arm<S, C>(
    arm: &str,
    workspace: &Workspace,
    storage: &S,
    scanner: &NativeScanner,
    model: &FixedEmbeddingModel,
    compact: C,
    initial_open_us: u128,
    report_path: &Path,
) -> EngineResult<()>
where
    S: IndexStorage + SearchStorage + LifecycleQueryTelemetry,
    C: Fn() -> EngineResult<Option<u128>>,
{
    let mut passes = Vec::new();
    let mut compaction_us = Vec::new();
    execute_pass(
        "initial_rescan",
        workspace,
        storage,
        scanner,
        model,
        &[WorkspaceChange::Rescan],
        &compact,
        &mut passes,
        &mut compaction_us,
    )
    .await?;

    let mut files = storage.list_files()?;
    files.sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
    let corpus_ext = corpus_extension();
    let mut corpus_files = files
        .into_iter()
        .filter(|file| {
            file.relative_path
                .extension()
                .and_then(|ext| ext.to_str())
                .is_some_and(|ext| ext == corpus_ext)
        })
        .map(|file| file.relative_path.as_path().to_path_buf())
        .collect::<Vec<_>>();
    if corpus_files.len() < 2 {
        return Err(EngineError::storage_failure(
            "frozen corpus must contain at least two indexed files of the configured extension",
        ));
    }
    let update_path = corpus_files.remove(0);
    let rename_source = corpus_files.remove(0);

    append_marker(&workspace.root, &update_path, "updated")?;
    execute_pass(
        "single_file_update",
        workspace,
        storage,
        scanner,
        model,
        &[WorkspaceChange::Upsert(update_path.clone())],
        &compact,
        &mut passes,
        &mut compaction_us,
    )
    .await?;
    let after_update = marker_hits(workspace, storage).await?;
    if !after_update.iter().any(|path| path == &update_path) {
        return Err(EngineError::storage_failure(
            "updated marker was not searchable and hydratable",
        ));
    }

    append_marker(&workspace.root, &rename_source, "renamed")?;
    let rename_extension = rename_source
        .extension()
        .and_then(|ext| ext.to_str())
        .unwrap_or("rs");
    let rename_target = rename_source.with_file_name(format!(
        "{}.m4gateb-renamed.{rename_extension}",
        rename_source
            .file_stem()
            .and_then(|name| name.to_str())
            .unwrap_or("source")
    ));
    if workspace.root.join(&rename_target).exists() {
        return Err(EngineError::storage_failure(
            "rename target already exists in immutable corpus",
        ));
    }
    fs::rename(
        workspace.root.join(&rename_source),
        workspace.root.join(&rename_target),
    )
    .map_err(|error| EngineError::storage_failure(error.to_string()))?;
    execute_pass(
        "rename",
        workspace,
        storage,
        scanner,
        model,
        &[
            WorkspaceChange::Delete(rename_source.clone()),
            WorkspaceChange::Upsert(rename_target.clone()),
        ],
        &compact,
        &mut passes,
        &mut compaction_us,
    )
    .await?;
    let after_rename = marker_hits(workspace, storage).await?;
    if !after_rename.iter().any(|path| path == &rename_target)
        || after_rename.iter().any(|path| path == &rename_source)
    {
        return Err(EngineError::storage_failure(
            "renamed marker did not replace the old searchable path",
        ));
    }

    fs::remove_file(workspace.root.join(&rename_target))
        .map_err(|error| EngineError::storage_failure(error.to_string()))?;
    execute_pass(
        "delete",
        workspace,
        storage,
        scanner,
        model,
        &[WorkspaceChange::Delete(rename_target.clone())],
        &compact,
        &mut passes,
        &mut compaction_us,
    )
    .await?;
    let after_delete = marker_hits(workspace, storage).await?;
    if after_delete.iter().any(|path| path == &rename_target) {
        return Err(EngineError::storage_failure(
            "deleted path remained searchable",
        ));
    }

    execute_pass(
        "repeat_rescan_1",
        workspace,
        storage,
        scanner,
        model,
        &[WorkspaceChange::Rescan],
        &compact,
        &mut passes,
        &mut compaction_us,
    )
    .await?;
    execute_pass(
        "repeat_rescan_2",
        workspace,
        storage,
        scanner,
        model,
        &[WorkspaceChange::Rescan],
        &compact,
        &mut passes,
        &mut compaction_us,
    )
    .await?;
    let final_hits = marker_hits(workspace, storage).await?;
    if !final_hits.iter().any(|path| path == &update_path)
        || final_hits.iter().any(|path| path == &rename_target)
    {
        return Err(EngineError::storage_failure(
            "final marker query disagrees with completed mutation trace",
        ));
    }

    let query_samples = passes
        .iter()
        .flat_map(|pass| {
            pass["concurrent_query_wall_us"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_u64)
                .map(u128::from)
        })
        .collect::<Vec<_>>();
    let fence_wait_samples = passes
        .iter()
        .flat_map(|pass| {
            pass["concurrent_query_fence_wait_us"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_u64)
                .map(u128::from)
        })
        .collect::<Vec<_>>();
    let store_lock_wait_ns_samples = passes
        .iter()
        .flat_map(|pass| {
            pass["concurrent_query_store_lock_wait_total_ns"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_u64)
                .map(u128::from)
        })
        .collect::<Vec<_>>();
    let store_lock_acquisition_count = passes
        .iter()
        .flat_map(|pass| {
            pass["concurrent_query_store_lock_acquisitions"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_u64)
        })
        .sum::<u64>();
    let native_fts_wall_ns_samples = passes
        .iter()
        .flat_map(|pass| {
            let counts = pass["concurrent_query_native_fts_calls"]
                .as_array()
                .into_iter()
                .flatten();
            let walls = pass["concurrent_query_native_fts_wall_ns"]
                .as_array()
                .into_iter()
                .flatten();
            counts.zip(walls).filter_map(|(count, wall)| {
                if count.as_u64()? > 0 {
                    wall.as_u64().map(u128::from)
                } else {
                    None
                }
            })
        })
        .collect::<Vec<_>>();
    let started_slots = passes
        .iter()
        .filter_map(|pass| pass["concurrent_query_attempted"].as_u64())
        .sum::<u64>();
    let attempted_slots = passes
        .iter()
        .filter_map(|pass| pass["concurrent_query_offered_slots"].as_u64())
        .sum::<u64>();
    let accepted_slots = passes
        .iter()
        .filter_map(|pass| pass["concurrent_query_accepted_slots"].as_u64())
        .sum::<u64>();
    let dropped_slots = passes
        .iter()
        .filter_map(|pass| pass["concurrent_query_dropped_slots"].as_array())
        .map(|slots| slots.len() as u64)
        .sum::<u64>();
    let queue_full_slots = passes
        .iter()
        .filter_map(|pass| pass["concurrent_query_queue_full_slots"].as_u64())
        .sum::<u64>();
    let receiver_disconnected_slots = passes
        .iter()
        .filter_map(|pass| pass["concurrent_query_receiver_disconnected_slots"].as_u64())
        .sum::<u64>();
    let not_offered_by_cap = passes
        .iter()
        .filter_map(|pass| pass["concurrent_query_slots_not_offered_by_cap"].as_u64())
        .sum::<u64>();
    let scheduled_slots = attempted_slots + not_offered_by_cap;
    let missed_schedule_slots = dropped_slots + not_offered_by_cap;
    let late_start_slots = passes
        .iter()
        .filter_map(|pass| pass["concurrent_query_late_start_slots"].as_u64())
        .sum::<u64>();
    let late_completion_slots = passes
        .iter()
        .filter_map(|pass| pass["concurrent_query_late_completion_slots"].as_u64())
        .sum::<u64>();
    let failure_count = passes
        .iter()
        .filter_map(|pass| pass["concurrent_query_failures"].as_array())
        .map(|failures| failures.len())
        .sum::<usize>();
    let max_schedule_lag_us = passes
        .iter()
        .filter_map(|pass| pass["concurrent_query_schedule_lag_us"].as_array())
        .flatten()
        .filter_map(Value::as_u64)
        .max()
        .unwrap_or(0);
    let capped_passes = passes
        .iter()
        .filter(|pass| pass["concurrent_query_sampling_capped"] == true)
        .count();
    let reader_interval_us = std::env::var("M4_GATE_B_READER_QUERY_INTERVAL_MS")
        .expect("M4_GATE_B_READER_QUERY_INTERVAL_MS")
        .parse::<u64>()
        .expect("valid reader query interval")
        * 1_000;
    let query_p95_comparable = query_samples.len() >= 20
        && query_samples.len() as u64 == started_slots
        && accepted_slots == attempted_slots
        && missed_schedule_slots == 0
        && failure_count == 0
        && capped_passes == 0
        && max_schedule_lag_us <= reader_interval_us;
    let mut report = json!({
        "schema_version": 1,
        "arm": arm,
        "initial_open_us": initial_open_us,
        "reader_query_interval_ms": std::env::var("M4_GATE_B_READER_QUERY_INTERVAL_MS")
            .expect("M4_GATE_B_READER_QUERY_INTERVAL_MS")
            .parse::<u64>()
            .expect("valid reader query interval"),
        "reader_query_max_samples_per_pass": std::env::var(
            "M4_GATE_B_READER_QUERY_MAX_SAMPLES",
        )
        .expect("M4_GATE_B_READER_QUERY_MAX_SAMPLES")
        .parse::<usize>()
        .expect("valid reader query sample limit"),
        "reader_query_queue_capacity": std::env::var(
            "M4_GATE_B_READER_QUERY_QUEUE_CAPACITY",
        )
        .expect("M4_GATE_B_READER_QUERY_QUEUE_CAPACITY")
        .parse::<usize>()
        .expect("valid reader query queue capacity"),
        "workspace_root": workspace.root,
        "indexed_file_count": storage.list_files()?.len(),
        "mutations": [
            {"kind": "upsert", "path": update_path},
            {"kind": "rename", "from": rename_source, "to": rename_target},
            {"kind": "delete", "path": rename_target},
        ],
        "passes": passes,
        "m4_compaction_wall_us_after_each_pass": compaction_us,
        "concurrent_query_total": query_samples.len(),
        "concurrent_query_scheduled_slots": scheduled_slots,
        "concurrent_query_offered_slots": attempted_slots,
        "concurrent_query_accepted_slots": accepted_slots,
        "concurrent_query_started": started_slots,
        "concurrent_query_dropped_slots": dropped_slots,
        "concurrent_query_queue_full_slots": queue_full_slots,
        "concurrent_query_receiver_disconnected_slots": receiver_disconnected_slots,
        "concurrent_query_slots_not_offered_by_cap": not_offered_by_cap,
        "concurrent_query_schedule_slots_missed": missed_schedule_slots,
        "concurrent_query_late_start_slots": late_start_slots,
        "concurrent_query_late_completion_slots": late_completion_slots,
        "concurrent_query_failures": failure_count,
        "concurrent_query_max_schedule_lag_us": max_schedule_lag_us,
        "concurrent_query_sampling_capped_passes": capped_passes,
        "concurrent_query_overloaded_passes": passes
            .iter()
            .filter(|pass| pass["concurrent_query_queue_overloaded"] == true)
            .count(),
        "concurrent_query_p50_us": percentile(&query_samples, 50),
        "concurrent_query_p95_us": percentile(&query_samples, 95),
        "concurrent_query_fence_wait_p50_us": percentile(&fence_wait_samples, 50),
        "concurrent_query_fence_wait_p95_us": percentile(&fence_wait_samples, 95),
        "concurrent_query_store_lock_acquisition_count": store_lock_acquisition_count,
        "concurrent_query_store_lock_wait_p50_ns_per_query": percentile(&store_lock_wait_ns_samples, 50),
        "concurrent_query_store_lock_wait_p95_ns_per_query": percentile(&store_lock_wait_ns_samples, 95),
        "concurrent_query_store_lock_wait_max_ns_per_query": store_lock_wait_ns_samples.iter().max(),
        "concurrent_query_store_lock_wait_scope": "test-only thread-local time waiting in IndexStore read/write state-mutex acquisition on the query worker thread; excludes native index service time and other worker threads",
        "concurrent_query_latency_p95_comparable": query_p95_comparable,
        "concurrent_query_latency_percentile_caveat": "Offered slots are generated at fixed wall-clock deadlines; each attempted start records scheduler lag, accepted requests record queue/service/end-to-end response and fence wait, queue-full offers are counted as drops, and capped-but-unoffered due slots are counted separately. Do not compare aggregate query p95 unless both arms have at least 20 successful completions, every attempted offer was accepted, zero drops/unoffered slots/query failures, and maximum scheduler lag is no greater than one arrival interval. Failed, dropped, capped, and overload samples remain explicit.",
        "final_marker_paths": final_hits,
        "result_scope": "research-only Rust indexing/search pipeline; external containment receipt supplies cgroup/process telemetry",
    });
    report["concurrent_query_native_fts_observed_query_count"] =
        json!(native_fts_wall_ns_samples.len());
    report["concurrent_query_native_fts_wall_p50_ns_per_observed_query"] =
        json!(percentile(&native_fts_wall_ns_samples, 50));
    report["concurrent_query_native_fts_wall_p95_ns_per_observed_query"] =
        json!(percentile(&native_fts_wall_ns_samples, 95));
    report["concurrent_query_native_fts_scope"] = json!(
        "test-only thread-local native collection FTS call wall time; excludes state-lock acquisition wait; includes native internal waits; zero calls are excluded, not treated as zero latency"
    );
    let bytes = serde_json::to_vec_pretty(&report)
        .map_err(|error| EngineError::storage_failure(error.to_string()))?;
    fs::write(report_path, bytes)
        .map_err(|error| EngineError::storage_failure(error.to_string()))?;
    if failure_count > 0 {
        return Err(EngineError::storage_failure(format!(
            "concurrent offered-load query failures: {failure_count}"
        )));
    }
    Ok(())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "Gate B measurement harness; run only through a fresh approved M4 containment operation"]
async fn gate_b_full_lifecycle_arm() {
    let arm = std::env::var("M4_GATE_B_ARM").expect("M4_GATE_B_ARM=native|m4");
    assert!(arm == "native" || arm == "m4", "unsupported arm {arm}");
    let workspace_root =
        PathBuf::from(std::env::var_os("M4_GATE_B_WORKSPACE").expect("M4_GATE_B_WORKSPACE"))
            .canonicalize()
            .expect("canonical workspace root");
    let index_root =
        PathBuf::from(std::env::var_os("M4_GATE_B_INDEX_ROOT").expect("M4_GATE_B_INDEX_ROOT"));
    let report_path =
        PathBuf::from(std::env::var_os("M4_GATE_B_REPORT").expect("M4_GATE_B_REPORT"));
    let reader_interval_ms = std::env::var("M4_GATE_B_READER_QUERY_INTERVAL_MS")
        .expect("M4_GATE_B_READER_QUERY_INTERVAL_MS")
        .parse::<u64>()
        .expect("valid reader query interval");
    let max_reader_samples = std::env::var("M4_GATE_B_READER_QUERY_MAX_SAMPLES")
        .expect("M4_GATE_B_READER_QUERY_MAX_SAMPLES")
        .parse::<usize>()
        .expect("valid reader query sample limit");
    assert!(reader_interval_ms > 0 && max_reader_samples > 0);
    assert!(!index_root.exists(), "index root must be unique and fresh");
    assert!(
        !report_path.exists(),
        "report path must be unique and fresh"
    );
    fs::create_dir_all(&index_root).expect("create operation-owned index root");

    let model = FixedEmbeddingModel::new();
    let workspace = Workspace {
        name: format!("m4-gate-b-{arm}"),
        root: workspace_root,
        scan: ScanRules::default(),
        index: IndexState::Enabled(IndexDescriptor::single(model.info.clone())),
        created_epoch_ms: 1,
        updated_epoch_ms: 1,
    };
    let scanner = NativeScanner::default();
    let open_started = Instant::now();
    if arm == "native" {
        let storage = IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
            storage_path: index_root.clone(),
            embeddings: vec![model.info.clone()],
        })
        .expect("open native index");
        let open_us = open_started.elapsed().as_micros();
        run_arm(
            &arm,
            &workspace,
            &storage,
            &scanner,
            &model,
            || Ok(None),
            open_us,
            &report_path,
        )
        .await
        .expect("native full lifecycle workload");
        drop(storage);
        let reopen_started = Instant::now();
        let reopened = IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
            storage_path: index_root,
            embeddings: vec![model.info.clone()],
        })
        .expect("reopen native index");
        let reopen_us = reopen_started.elapsed().as_micros();
        let hits = marker_hits(&workspace, &reopened)
            .await
            .expect("native query after reopen");
        assert!(!hits.is_empty(), "native marker survives reopen");
        let mut value: Value =
            serde_json::from_slice(&fs::read(&report_path).expect("read native arm report"))
                .expect("parse native arm report");
        value["reopen_us"] = json!(reopen_us);
        value["post_reopen_marker_paths"] = json!(hits);
        fs::write(
            &report_path,
            serde_json::to_vec_pretty(&value).expect("serialize native report"),
        )
        .expect("write native reopen metrics");
    } else {
        let storage = M4ResearchStorage::open(&index_root, &model.info)
            .expect("open M4 sidecar/native stores");
        let open_us = open_started.elapsed().as_micros();
        let compact_storage = &storage.sidecar;
        run_arm(
            &arm,
            &workspace,
            &storage,
            &scanner,
            &model,
            || {
                let started = Instant::now();
                compact_storage
                    .lock()
                    .expect("M4 sidecar")
                    .compact_now()
                    .map_err(EngineError::storage_failure)?;
                Ok(Some(started.elapsed().as_micros()))
            },
            open_us,
            &report_path,
        )
        .await
        .expect("M4 full lifecycle workload");
        drop(storage);
        let reopen_started = Instant::now();
        let reopened = M4ResearchStorage::open(&index_root, &model.info)
            .expect("reopen M4 sidecar/native stores");
        let reopen_us = reopen_started.elapsed().as_micros();
        let hits = marker_hits(&workspace, &reopened)
            .await
            .expect("M4 query after reopen");
        assert!(!hits.is_empty(), "M4 marker survives reopen");
        let mut value: Value =
            serde_json::from_slice(&fs::read(&report_path).expect("read M4 arm report"))
                .expect("parse M4 arm report");
        value["reopen_us"] = json!(reopen_us);
        value["post_reopen_marker_paths"] = json!(hits);
        fs::write(
            &report_path,
            serde_json::to_vec_pretty(&value).expect("serialize M4 report"),
        )
        .expect("write M4 reopen metrics");
    }
}

#[test]
fn gate_b_percentile_uses_nearest_rank_and_empty_is_none() {
    assert_eq!(percentile(&[40, 10, 30, 20], 50), Some(20));
    assert_eq!(percentile(&[40, 10, 30, 20], 95), Some(40));
    assert_eq!(percentile(&[], 95), None);
}
