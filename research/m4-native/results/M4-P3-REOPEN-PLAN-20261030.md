# M4 P3 reopen plan — 2026-10-30

**Status: REOPENED — Stage A/B executed, Stage C passed, Stage E blocked, Stage E2 safety-stopped, camera 300 at 768 MiB is qgroup-blocked; Stage D not run.** Stage A/B: [`M4-P3-STAGE-A-RESULT-20261030.md`](M4-P3-STAGE-A-RESULT-20261030.md). Stage C: [`M4-P3-STAGE-C-RESULT-20261030.md`](M4-P3-STAGE-C-RESULT-20261030.md). Stage E: [`M4-P3-STAGE-E-BLOCKED-20261030.md`](M4-P3-STAGE-E-BLOCKED-20261030.md). Stage E2: [`M4-P3-STAGE-E2-ATTEMPT-20261030.md`](M4-P3-STAGE-E2-ATTEMPT-20261030.md). Camera 421: [`M4-P3-STAGE-E3-CAMERA-421-MEMORY-20261030.md`](M4-P3-STAGE-E3-CAMERA-421-MEMORY-20261030.md). Camera 300 512M: [`M4-P3-STAGE-E4-CAMERA-300-MEMORY-20261030.md`](M4-P3-STAGE-E4-CAMERA-300-MEMORY-20261030.md). Camera 300 768M: [`M4-P3-STAGE-E4B-CAMERA-300-QGROUP-20261030.md`](M4-P3-STAGE-E4B-CAMERA-300-QGROUP-20261030.md). Stage A/C/E2/E3/E4 authorizations are consumed; no further run is authorized. This document reopens P3 as a planning state. It does not authorize a benchmark, operation ID, corpus run, code change, quota change, production action, or reuse of a consumed authorization. The previous `REWORK` decision remains the disposition of the previous campaign; this plan replaces the method, not the evidence.

## 1. What the previous P3 logs actually show

The latest authorized pair was `m4-rust-lifecycle-pair-20261027t052952z-332a873b` (64 files, 694,421 bytes). Both arms exited 0 with complete hard-safety telemetry and verified cleanup, but the pair is `PAIR_COMPLETED_WITH_WORKSPACE_CAVEATS` and `query_latency_comparison = NOT_COMPARABLE_SAMPLING_INCOMPLETE`.

| Arm | Due slots | Offered | Accepted / started | Queue-full | Cap omissions | Overloaded passes | p95 (descriptive) |
|---|---:|---:|---:|---:|---:|---:|---:|
| M4 | 582 | 540 | 232 / 232 | 308 | 42 | 4 | 3,076,026 μs |
| Native | 558 | 558 | 428 / 428 | 130 | 0 | 4 | 796,373 μs |

The direct cause of dropped offers was **queue saturation**, not receiver disconnection. The fixed 25 ms offered load (one worker, queue capacity 8) exceeded both arms' capacity. Because the accepted load differs (232 vs 428), the p95 values are **not comparable** and are not an architecture verdict.

Other limits in the previous campaign:

- Only a 64-file diagnostic pilot; full corpus is 421 files / about 7.07 MB.
- Workspace inventory/soft-budget peaks were unsampled; the qgroup bound is not an atomic lifetime inventory.
- The 512 MiB cgroup envelope and a 4,096-document memory abort remain unresolved; no memory remedy is qualified.
- Gate A is research-scope PASS; crash/power-loss/mixed-snapshot/parity coverage is not exhaustive.
- The whole-pass `PassGate` fence is REWORK; selective visibility + commit-scoped fence is a research candidate, not qualified.
- The native lock-split result is a separate native optimization and is not M4 evidence.

## 2. Root causes to separate

1. **Measurement-design defect:** fixed offered load above capacity makes latency incomparable. This is fixable before touching architecture.
2. **Architecture defect:** the whole-pass fence causes long waits; the candidate fence is not yet qualified.
3. **Scale/resource defect:** 64-file pilot and 512 MiB envelope do not represent the 421-file corpus.
4. **Correctness/durability gap:** crash, power-loss, mixed snapshots, and parity are not exhaustive.

Do not mix these in one pair again. Each stage below answers one question.

## 3. New measurement design

### Stage A — Instrumentation and rate ladder (no architecture claim)

Add a **rate-ladder** mode to the existing harness:

- Offered rates: 5, 10, 20, 40 QPS (or equivalent intervals), one worker, queue capacity 8.
- Closed-loop mode: fixed concurrency 1, 4, 8 outstanding queries.
- Per rate/concurrency, record for every slot: due, offered, accepted, started, completed, queue-full, cap-omitted, missed start, queue delay, service time, end-to-end latency, fence wait.
- Reconcile: `due = accepted + queue_full + cap_omitted + missed + failed` (exactly, per pass).
- Report **goodput**, **drop rate**, **queue delay**, and **latency conditional on accepted**. Do not report cross-arm p95 unless the validity gate passes.
- Run on the 64-file pilot only. This is a method-validation stage, not an architecture comparison.

**Gate A:** counters reconcile every pass; zero query failures; the lowest rate has zero drops/cap omissions/missed starts for both arms; otherwise report capacity and stop.

### Stage B — Matched-load latency comparison

Choose a rate below both arms' measured capacity (target ≤80% of the lower capacity) so that both accept all offers:

- zero queue-full, zero cap omissions, zero missed starts, zero failures;
- at least 20 successful completions per pass and enough passes for the preregistered uncertainty;
- counterbalanced M4-first/native-first pairs;
- paired bootstrap on p50/p95 and wait, with a preregistered threshold.

**Gate B:** if validity is not met, no latency claim. If M4 is slower at matched load, that is a real architecture signal; if equal, capacity may still differ.

### Stage C — Capacity/throughput comparison

At fixed offered load and closed-loop concurrency, compare:

- max sustainable goodput;
- drop rate at fixed load;
- queue delay;
- latency at matched utilization (not raw p95).

**Gate C:** report capacity ratio separately from latency. A capacity regression is not hidden inside a p95 number.

## 4. Architecture remediation

Before a qualification pair:

- Replace or bound the whole-pass `PassGate`; define the selective-visibility/commit-scoped invariants:
  - every query sees a consistent snapshot;
  - no mixed generation beyond a documented bound;
  - writer/delete/rename visibility is explicit;
  - no unbounded wait.
- Add wait attribution: schema/publication lock vs queue vs service time. Reuse the native lock-split instrumentation lessons where they apply, without claiming they qualify M4.
- Expand crash/power-loss/mixed-snapshot/parity tests before performance.

**Gate D:** correctness/durability invariants pass; no unbounded wait in the measured path. Otherwise return to REWORK.

## 5. Scale and resource

- Use the 64-file pilot to validate the method and architecture, not to claim full-corpus performance.
- Move to the 421-file corpus only if:
  - Stage A/B method is valid;
  - peak memory fits the unchanged 512 MiB / zero-swap / two-CPU envelope;
  - workspace inventory and qgroup headroom are measured;
  - a full-corpus trace, query set, and source hashes are frozen.
- Do not raise caps to force a pass. If the envelope cannot fit, the result is **OPERATION_BLOCKED**.

## 6. Authorization and stop conditions

- Reopening this document does **not** authorize a run.
- Each stage needs a fresh operation ID, fresh preflight, and owner authorization; the previous one-pair authorization is consumed and must not be reused.
- Stop and report if:
  - counters do not reconcile;
  - the lowest useful rate still saturates both arms;
  - matched-load validity cannot be achieved;
  - the 512 MiB envelope cannot fit the full corpus;
  - the candidate fence has an unbounded wait or a correctness gap.
- Production remains a separate P6 decision; no production change is implied by P3.

## 7. First authorized slice to request

The smallest useful next slice is **Stage A on the 64-file pilot**:

- no architecture change;
- no full corpus;
- no production;
- one bounded rate-ladder/closed-loop validation run per arm, with the existing hard controls;
- deliverable: reconciled counters + capacity table + validity verdict.

Only after Stage A passes should Stage B (matched-load latency) be requested.

## Evidence

- [Gate B query-drop cause](M4-GATE-B-QUERY-DROP-CAUSE-20261027.md)
- [M4 recovery plan](M4-RECOVERY-EXECUTION-PLAN-20261027.md)
- [Previous P3 decision](M4-P3-ARCHITECTURE-DECISION-20261030.md)
- [Current checkpoint](ACTIVE-EXECUTION.md)
- [Latest handoff](HANDOFF-20261029.md)
- [Upgrade roadmap](../../../docs/technical/optimization-upgrade-plan-20261027.md)
- Raw pair: `../../.work/m4-native-workspace-quota/m4-rust-lifecycle-pair-20261027t052952z-332a873b/pair-result.json`
