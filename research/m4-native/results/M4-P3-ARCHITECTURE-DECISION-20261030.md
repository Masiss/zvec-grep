# M4 P3 architecture decision — 2026-10-30

**Status: DECIDED — `REWORK`; not production GO.**

**Status update 2026-10-31: P3 research closed as `PARTIAL`; production remains `NO-GO`.** Native FTS tail is attributed to native schema-lock wait, not FTS search (`collection_query` p99 184.7 ms vs `schema_handle_lock` p99 184.0 ms; inner FTS stages <0.5 ms p99). Multi-host remains `OPERATION_BLOCKED`; old 421 full corpus is `PROVENANCE_BLOCKED` and camera 421 is the replacement diagnostic. Exact 421 per-query trace is optional and needs a fresh operation. See [`M4-P3-NATIVE-FTS-STAGE-ATTRIBUTION-20261031.md`](M4-P3-NATIVE-FTS-STAGE-ATTRIBUTION-20261031.md) and [`M4-P3-TRIAGE-20261030.md`](M4-P3-TRIAGE-20261030.md). No new benchmark was launched for this closure.

**Status update 2026-10-30: REOPENED; Stage A/B and Stage C executed; Stage E blocked; Stage E2 safety-stopped; camera 300 at 768 MiB is qgroup-blocked.** See [`M4-P3-STAGE-A-RESULT-20261030.md`](M4-P3-STAGE-A-RESULT-20261030.md), [`M4-P3-STAGE-C-RESULT-20261030.md`](M4-P3-STAGE-C-RESULT-20261030.md), [`M4-P3-STAGE-E-BLOCKED-20261030.md`](M4-P3-STAGE-E-BLOCKED-20261030.md), [`M4-P3-STAGE-E2-ATTEMPT-20261030.md`](M4-P3-STAGE-E2-ATTEMPT-20261030.md), [`camera 421 memory`](M4-P3-STAGE-E3-CAMERA-421-MEMORY-20261030.md), and [`camera 300 memory`](M4-P3-STAGE-E4-CAMERA-300-MEMORY-20261030.md). Stage C: M4 0 drops/0 cap/0 fence both pairs (254/254, 317/317), p95 57/62 ms; native dropped 23/14, not qualification. Stage E corpus absent. Stage E2 219-file hit fixture limit then watchdog telemetry abort. Camera 421: 512 MiB memory. Camera 300 at 512 MiB: 512 MiB memory. Camera 300 at 768 MiB: peak 585.9 MiB, reached repeat_rescan_1, then qgroup 256 MiB failed (referenced 205,467,648, available 62,967,808 < 67,108,864). **Overall remains `REWORK`; no production GO.** Stage D not run; Stage A/C/E2/E3/E4 authorizations consumed.

 This is a decision record, not a new benchmark, implementation, migration, or production authorization. No new pair was launched; the latest one-pair authorization remains consumed.

## Decision

| Layer | Decision | Basis |
|---|---|---|
| Current M4 publication design (whole-pass `PassGate` fence) | **REWORK / reject as the current research design** | Prior lifecycle runs repeatedly recorded multi-second waits during rescan/update/rename. |
| Selective visibility + commit-scoped fence | **Keep as research candidate only; not qualified** | Gate 2 tests cover selected concurrency/recovery paths, not exhaustive crash, power-loss, mixed-snapshot, or qualification behavior. |
| M4 performance vs native | **INCONCLUSIVE / NOT COMPARABLE** | Latest pair has queue-full drops, cap omissions, overloaded passes, and different offered/accepted load; p95 is descriptive only. |
| Full-corpus feasibility | **OPERATION_BLOCKED** | Only the 64-file diagnostic pilot is measured; full corpus is 421 files / about 7.07 MB. The 512 MiB envelope and memory remedy are unresolved. |
| Production integration | **NO-GO** | No migration, rollback, watcher batching, observability, support envelope, or production owner authorization. |

**Overall:** M4 remains **research-only**. The correct next state is **REWORK of the publication/fencing design**, not a production GO and not a final rejection of every future incremental-index approach.

## Evidence reviewed

- [M4 recovery and execution plan](M4-RECOVERY-EXECUTION-PLAN-20261027.md) — Gate A research-scope PASS; Gate B PARTIAL / OPERATION_BLOCKED / OWNER_DECISION_REQUIRED; architecture REWORK, not production GO.
- [Latest Gate B query-drop report](M4-GATE-B-QUERY-DROP-CAUSE-20261027.md) and [query-latency report](M4-GATE-B-QUERY-LATENCY-20261026.md).
- Latest authorized pair `m4-rust-lifecycle-pair-20261027t052952z-332a873b`:
  - status `PAIR_COMPLETED_WITH_WORKSPACE_CAVEATS`;
  - M4: 308 queue-full drops, 0 disconnected, 42 cap omissions;
  - native: 130 queue-full drops, 0 disconnected, 0 cap omissions;
  - four passes per arm marked overloaded;
  - query p95 M4 3,076,026 μs vs native 796,373 μs, but **not comparable** because offered/accepted load differs and inventory/soft-budget peaks are unknown.
- [Current checkpoint](ACTIVE-EXECUTION.md) and [handoff](HANDOFF-20261029.md).
- [Native lock-split result](M4-PHASE3-QUERY-LOCK-SPLIT-PROTOTYPE-20261030.md) — a separate native-engine optimization; it reduces a measured query stall and passes correctness/recovery/TSAN, but it does **not** qualify M4 or the M4 publication design.

## Why not GO

1. **No measurement-valid performance comparison.** Queue-full drops and cap omissions mean the two arms did not execute the same offered load; p95 is not a performance result.
2. **No representative corpus.** The pilot is 64 files; the full corpus is 421 files. The pilot is diagnostic, not qualification.
3. **Resource envelope unresolved.** The 512 MiB memory ceiling and workspace capacity/headroom route are not qualified for full-corpus lifecycle work. Increasing caps is not an accepted remedy.
4. **Correctness coverage is not exhaustive.** Selected concurrency/recovery tests pass, but crash points, power-loss behavior, mixed snapshots under all filters, and parity are not qualified.
5. **Production prerequisites absent.** No migration/rebuild, rollback, watcher batching, support/observability, or owner production decision.

## Why not a final NO-GO on the M4 idea

There is not enough valid evidence for a final NO-GO on the overall incremental-index idea. The evidence supports rejecting the **current whole-pass fence** and blocking qualification until the dependencies are resolved. A different publication/fencing design or a bounded remediation may still be viable.

The native lock-split result is **not** evidence for M4. It is a separate native optimization candidate and must go through its own native-first release path.

## Conditions to reopen P3

A new measurement or qualification attempt requires all of the following:

1. **Fresh explicit owner authorization** with a new operation ID; no retry or reuse of the consumed authorization.
2. **Preregistered numeric acceptance envelope** for correctness, query latency/wait, CPU, peak memory, I/O, storage growth, recovery, and parity — before seeing candidate results.
3. **Representative full-corpus workload** (421 files minimum) with frozen mutation trace, query set, source hashes, and rebuild conditions.
4. **Safe resource result** under the unchanged 512 MiB / zero-swap / two-CPU envelope; do not raise caps to force a pass.
5. **Trustworthy offered-load telemetry** with zero missed starts, or an explicitly bounded queue model that records queue delay and dropped work.
6. **Counterbalanced independent pairs** sufficient for the preregistered uncertainty; a single pair remains exploratory.
7. **Expanded crash/recovery/power-loss and parity coverage**, including selective-visibility boundaries.
8. **Separate production decision** covering migration/rebuild, rollback, watcher batching, observability, support, and owner approval.

## Non-actions

- No new benchmark or pair was launched for this decision.
- No production code, default, schema, migration, quota, reserve, or cleanup policy was changed.
- No evidence was deleted or relabeled.
- No claim of M4 performance win, production readiness, or final rejection of the M4 idea is made.

## Evidence index

- [M4 recovery plan](M4-RECOVERY-EXECUTION-PLAN-20261027.md)
- [Gate B query-drop report](M4-GATE-B-QUERY-DROP-CAUSE-20261027.md)
- [Gate B query-latency report](M4-GATE-B-QUERY-LATENCY-20261026.md)
- [Current checkpoint](ACTIVE-EXECUTION.md)
- [Latest handoff](HANDOFF-20261029.md)
- [Native lock-split prototype result](M4-PHASE3-QUERY-LOCK-SPLIT-PROTOTYPE-20261030.md)
- [Upgrade roadmap](../../../docs/technical/optimization-upgrade-plan-20261027.md)
