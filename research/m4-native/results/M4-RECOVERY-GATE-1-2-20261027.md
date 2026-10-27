# M4 Recovery — Gates 1–2 execution update (2026-10-27)

## Status

**Gate 1 measurement/containment infrastructure: PASS for the bounded diagnostic setup. Gate 2 selective-publication correctness: PASS for tested research cases, not exhaustive qualification. Gate 3: NOT RUN — current operation authorization is exhausted. Gate 4: OPEN — native 512 MiB headroom remains unqualified. Gate 5: OUT OF SCOPE without a separate production decision. Overall: PARTIAL / OWNER_DECISION_REQUIRED; no production GO.**

No benchmark workload was launched in this update. A no-launch preflight passed, but the active policy states the 2026-10-26 authorization covered one fresh 64-file diagnostic pair and that the 2026-10-27 Technical Lead direction authorizes infrastructure implementation, not a new operation. The prior one-pair authorization has already been consumed. Do not infer launch authority from the successful preflight.

## Gate 1 — measurement integrity and capacity certification

### Workspace observer: selected approach

The old recursive workspace inventory was not a reliable hard-cap mechanism under its time/entry budget. The selected design uses the registered Btrfs simple-quota qgroup as the kernel-enforced allocated-byte boundary; sampled per-file inventory is diagnostic only. Incomplete inventory remains explicitly unknown and cannot establish an overage or make a pass. The policy still enforces the per-arm allocation allowance and 64 MiB minimum-free reserve, plus the independent filesystem free-space reserve and cgroup controls.

This is preferable to (a) increasing observer work/time limits, which repeats the unbounded-work problem; or (b) introducing another filesystem quota mechanism, which would require unnecessary host/admin policy changes when the registered qgroup already supplies the hard bound. Qgroup inconsistency, identity mismatch, stale attestation, or insufficient headroom remain fail-closed.

### Live no-launch evidence

`run_rust_lifecycle_pair.py --validate-only` passed at 2026-10-27. It reported:

- Root filesystem free: **21,756,448,768 bytes**; policy-required free: **18,286,108,864 bytes**; margin: **3,470,339,904 bytes**.
- Registered qgroup `0/259`, maximum **134,217,728 bytes**, current referenced **16,461,824 bytes**, available **117,755,904 bytes**, required minimum free **67,108,864 bytes**.
- The pair reserves another **33,554,432 bytes** of permitted workspace growth while retaining the minimum-free reserve.
- `benchmark_started: false`; no workspace materialization or arm launch occurred.

`m4_containment_runner.py probe` confirmed cgroup v2 with delegated CPU, memory, swap and pids controllers and host diskstats/PSI/vmstat telemetry. The current delegation does **not** expose cgroup `io.stat` or `io.max`; per-arm physical-I/O attribution is therefore unavailable and must not be claimed. A host snapshot captured for the no-launch check is `M4-GATE-B-QUERY-LATENCY-20261027/host-snapshot-no-launch.txt`.

### Offered-load query telemetry

The research Rust lifecycle harness now offers queries at fixed wall-clock deadlines with a bounded FIFO and a single query worker. It records scheduled/attempted/accepted/started/completed counts, schedule lag, queue delay, service time, end-to-end response time, fence wait, queue-full drops, cap-denied due slots, failures and overload. A delayed scheduler catches up the original deadlines rather than silently shifting the offered rate; late starts remain visible and invalidate p95 eligibility when lag exceeds one interval. The workload rate/cadence is not reduced to manufacture zero misses.

`run_rust_lifecycle_pair.py` independently maps reports by `native`/`m4` role and validates that both arms used the same query interval, per-pass cap, queue capacity, filter and route. It makes p95 non-comparable if either arm reports a missing/invalid counter, fewer than 20 successful completions, a drop/cap/missed slot, query failure, incomplete sample cap, scheduler lag above one arrival interval, or a role/protocol mismatch. P95 values stay labeled by arm regardless of execution order.

Regression evidence:

- Python containment/pair-analysis suite: **24 passed, 0 failed**. The synthetic offered-load test uses known native/M4 p95s and reversed arm ordering, then separately injects queue drops, cap-denied starts, missed schedules, failures, sampling caps, a 25,001 µs lag against a 25 ms interval, too few completions, and a cross-arm queue-cap mismatch; every invalid trace is rejected without relabeling or suppressing the bad counters.
- Rust M4 integration binary: **9 passed, 0 failed, 1 ignored**. The ignored test is the contained measurement workload and was not run. Added offered-deadline/cap and counted-drop regressions pass.

A separate `cargo test --no-default-features` invocation failed at test compilation because it omitted the optional `m4-research-integration` feature (`m4-rust-bridge-research` unresolved). This was a command-configuration error, not evidence of a source failure; the already-built feature-enabled test binary was run directly with the required native library path and passed the focused suite above. No additional build was forced into the nearly quota-constrained workspace.

## Gate 2 — selective visibility and recovery

The current research integration tests exercise the selected durable pass-intent + selective file hiding + short cross-store commit fence design:

- During preparation, changed files are hidden while an unaffected-file query completes within the test bound; old and new revisions of the changed file are both hidden.
- A query overlapping the cross-store commit boundary waits, then observes the published revision after release; reopen preserves the new searchable revision and does not restore the old one.
- Interrupted multi-file publication fails closed through reopen and retry.
- Existing watcher-driven indexing/search, filtering/refill, hydration, pass-manifest bounds and percentile/queue regressions also pass.

This is correctness evidence for the research pipeline, not power-loss qualification, a proof over every interleaving, rollback to an old revision, or production integration. Gate A remains PASS only in research scope; the selective-fence architecture remains unqualified for production.

## Gate 3–5 blockers and next authorized action

1. **No fresh operation authority.** The machine policy records `single_pair_only: true`, the 2026-10-26 owner authorization for one fresh 64-file diagnostic pair, and says the 2026-10-27 infrastructure direction does not itself authorize another run. Do not launch or retry. A fresh explicit operation authorization is needed for another bounded pair.
2. **Qualification thresholds are not preregistered.** The 64-file workload can only diagnose the measurement path; it cannot establish representative performance. Before qualification, the owner must approve numeric acceptance limits for throughput/lifecycle regression, query wait/latency, memory, and resource efficiency. Do not invent thresholds.
3. **Memory/full-corpus envelope remains open.** A prior contained native diagnostic reached the existing **512 MiB** cgroup ceiling. This update neither raises that limit nor demonstrates a safe full-corpus memory result. Any implementation-side remedy must preserve the frozen comparison and pass correctness tests before a new authorized operation.
4. **Resource attribution limitation.** Without cgroup I/O counters, keep physical-I/O conclusions descriptive and host-scoped unless a policy-approved measurement path supplies per-arm attribution.
5. **Production remains excluded.** No runtime wiring, deployment, migration, limit increase, or production cutover is authorized.

When authority and thresholds are available, rerun exact no-launch preflight immediately before a new operation; use a fresh operation ID; retain all failed/stopped evidence; require arm-one exit, complete hard-safety telemetry, verified cleanup and allowed postflight before arm two; classify query p95 as diagnostic unless both arms pass the offered-load eligibility checks. Then require a representative workload and counterbalanced independent pairs before a GO/REWORK/NO-GO qualification decision.

## Reproducibility

Relevant implementation and policy files: `research/m4-native/tools/m4_containment_runner.py`, `research/m4-native/tools/run_rust_lifecycle_pair.py`, `research/m4-native/docs/benchmark-policy.json`, and `rust/crates/zg-engine/src/pipelines/indexed_search/m4_integration_tests/gate_b_lifecycle.rs`. Raw results from older stopped operations remain immutable under their original operation IDs; no result from this no-launch update is a Native–M4 performance comparison.

No new production runtime wiring, deployment, push, or benchmark launch was performed in this update. Existing uncommitted core-Rust M4 candidate changes are preserved outside the tooling/evidence commit; they remain research-only and are not approved for production integration.

## Decision

**REWORK / OWNER_DECISION_REQUIRED** for Gate B qualification. Keep Gate A's research correctness result, retain the selective-fence candidate for further qualification, and do not claim performance superiority or production readiness.

---

Status recorded: 2026-10-27. This update is an execution checkpoint, not a benchmark report.
