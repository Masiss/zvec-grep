# Index/Finalize Qualification

Status: **preregistered v1; not run**. UX V2 remains closed at commit `2478776`.
This track is about deciding whether the current M3 dirty-finalize behavior is
good enough, or whether Deferred Maintenance #10 is worth pursuing.

## Non-negotiable constraints

- Do **not** reopen native concurrent `optimize()` on zvec 0.8.0.
- Do **not** change the scheduler.
- Do **not** change policy or thresholds.
- Do **not** change index format, publication, or RC behavior in this track.
- No optimization without a measured bottleneck and a preregistered acceptance
  rule.

## Decision question

Under multi-workspace resident-daemon load, is the current M3 dirty-finalize
behavior acceptable on memory and resource contention, or does it create a
deferred-maintenance problem large enough to justify #10?

The decision is **not** based on single-job completion time. A single job can
look fast while the daemon and active engines hold much more memory or cause
more contention than an operator can sustain.

## Required measurements

Every future benchmark must record:

1. **Total daemon RSS** including the daemon process and all active engine
   processes/threads that own index storage, not just the CLI process.
2. **Peak total RSS** over the run, sampled at a fixed interval and at phase
   boundaries.
3. **Per-workspace RSS and storage growth**, especially when several workspaces
   are active at once.
4. **Resource contention**: CPU runnable/load, memory pressure/PSI, swap, disk
   I/O, and scheduler queue/full states.
5. **Job-level timings** as secondary context only: queue wait, dirty finalize,
   optimize, publish, and query visibility.
6. **Correctness guards**: retrieval parity, delete visibility, reopen/recovery,
   and no two writers per root.

Preferred process accounting: daemon PID plus descendant/engine PIDs, with
`smaps_rollup`/PSS and `/proc` RSS, plus cgroup peak when available. CLI RSS may
be recorded but is never the headline memory number.

## Workload matrix

At minimum:

- one workspace, low churn;
- multiple workspaces (e.g. 4 and 8) with mixed low/high churn;
- repeated dirty finalize versus clean/no-op refresh;
- delete-heavy and insert-heavy mutation patterns;
- concurrent reads while finalize/optimize is active;
- restart/reopen after dirty state.

The matrix must be preregistered before execution, including repetition count,
host affinity, load controls, and aggregation. Do not cherry-pick a single
workspace/pair.

## Decision rules (to be frozen before measuring)

- **GOOD ENOUGH / close M3:** peak total RSS and contention stay within the
  owner-approved envelope across the multi-workspace matrix, with no correctness
  or retrieval regression, and no unbounded growth under repeated dirty cycles.
- **INCONCLUSIVE:** measurement or environment cannot separate M3 dirty finalize
  from unrelated load, or the envelope was not preregistered.
- **#10 CANDIDATE:** a reproducible multi-workspace case shows material peak
  RSS/contention reduction potential from deferring maintenance, while
  correctness, delete visibility, and retrieval parity remain intact.
- **NO-GO:** any case requires native concurrent optimize, scheduler/policy
  changes, or threshold changes prohibited above.

## Preregistration

The frozen v1 protocol is in `index-finalize-preregistration.md` and
`benchmarks/harness-v2/operations/index-finalize/qualification-protocol.json`.
It fixes the matrix, fixture, repetitions, envelope, measurement design,
preflight, correctness guards, and decision rules before any run.

## Evidence to produce

- A baseline report with host inventory, load before/after, per-process memory
  series, phase boundaries, and raw commands.
- A short bottleneck statement with measured attribution.
- A decision record: `GOOD ENOUGH`, `INCONCLUSIVE`, `#10 CANDIDATE`, or `NO-GO`.
- Explicit list of limitations and what would change the decision.

## Relationship to UX V2 baseline

`ux-v2-performance-baseline.md` measured CLI wall time and CLI RSS only. It is
useful for UX control-plane overhead, but it is **not** evidence for or against
M3 dirty finalize or #10 because it did not measure total daemon/engine memory or
multi-workspace contention.
