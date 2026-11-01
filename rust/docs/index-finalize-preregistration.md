# Index/Finalize Qualification — preregistration v1

Status: **preregistered; execution INCONCLUSIVE**. The full matrix was not run; see the result report. UX V2 remains closed at `2478776`.
Machine-readable protocol:
`benchmarks/harness-v2/operations/index-finalize/qualification-protocol.json`
(protocol SHA-256 in the adjacent `.sha256` file). This commit does not change
engine, scheduler, policy, thresholds, or #10 behavior.

## Question

Under multi-workspace resident-daemon load, is current M3 dirty finalize good
enough, or is Deferred Maintenance #10 warranted?

Memory and contention are primary. Single-job time is diagnostic only.

## Frozen workload matrix

| Axis | Values |
|---|---|
| Workspace count | 1, 4, 8 |
| Finalize state | dirty insert-heavy, dirty delete-heavy, clean |
| Restart/reopen | after dirty insert-heavy and after dirty delete-heavy |
| Measured repetitions | 3 per cell |
| Warmups | 1 per cell, excluded |
| Total measured runs | 45 |
| Order | randomized blocks, seed `20261101`, counterbalanced |

### Fixture

- Seed `20261101`.
- 2,000 files/workspace × 8 KiB = 16,000,000 source bytes/workspace.
- Layout `src/chunk_{chunk}/file_{ordinal}.rs`; deterministic query terms and
  delete set fixed before measurement.
- One warm index per workspace; no model download in measured phases.
- One daemon state home, separate workspace roots; no cross-root reuse.

### Churn

- `insert-heavy`: +10% insert, 5% modify, 0% delete.
- `delete-heavy`: 0% insert, 5% modify, 10% delete.
- `clean`: no mutation; run a deliberate refresh/finalize cycle and expect no
  native optimize under M3-A.
- `restart`: stop daemon, wait exit, start daemon, wait ready, run one query and
  one reopen check.

## Resource envelope (frozen for v1)

Primary:

- total peak PSS of daemon + engines ≤ 7 GiB;
- total peak RSS ≤ 10 GiB;
- cgroup `memory.peak` ≤ 8 GiB;
- swap increase ≤ 512 MiB; `MemAvailable` floor 2 GiB.

Contention:

- memory PSI `full avg10` p95 ≤ 5%;
- I/O PSI `full avg10` p95 ≤ 10%;
- CPU PSI `some avg10` p95 ≤ 20%;
- `memory.events` max/oom/oom_kill = 0; `pids.events.max` = 0;
- scheduler queue-full count = 0.

Host preflight must also pass: cgroup v2 controllers, delegated systemd user
scope, `memory.peak`, per-process `smaps_rollup`, at least 10 GiB free disk,
4 GiB `MemAvailable`, load1 ≤ 1.0 per selected CPU, model cache present, no
foreign daemon, and recorded binary/protocol hashes. Preflight fails closed.

## Measurement design

- cgroup v2 scope wraps daemon + CLI + engine descendants; reset `memory.peak`
  per run.
- 250 ms cgroup sampling; 1 s per-process sampling; 500 ms queue sampling.
- Record daemon PID plus all engine PIDs, PSS/RSS/swap, CPU ticks, I/O, cgroup
  memory/swap/PSI/io/pids, host snapshot, and diskstats.
- Phase boundaries: start, churn done, submit, job start, finalize start/done,
  query done, restart ready, end.
- Distinguish **scheduler wait** (`waited_millis` when queued; observation
  timestamps when running) from **engine execution** (started→terminal). Where
  JobView cannot separate them, use daemon log timestamps and label the result
  `timing_incomplete`; do not invent a value.
- Queue activity from `zg --server jobs --json`: queued/running, queue position,
  waited time, queue-full, state/root.

## Correctness guards

Every measured cell must also pass:

- exact retrieval parity for the fixed query set (FTS and vector routes);
- delete visibility: deleted paths absent everywhere;
- recovery: stop/start + reopen yields the same visible sets;
- single-writer/root: zero overlapping writer ownership, zero violations;
- no index corruption and no failed job hidden as success.

## PASS / FAIL / decision

- **PASS**: all correctness guards and all primary/contention envelopes hold in
  every cell/repetition.
- **FAIL**: any correctness violation, forbidden change requirement, or
  envelope breach.
- **INCONCLUSIVE**: incomplete telemetry, cgroup reset failure, or host
  interference; never downgrade to PASS.
- **GOOD_ENOUGH**: PASS on the full matrix and no reproducible evidence that
  deferring maintenance materially lowers peak memory/contention.
- **#10 CANDIDATE**: reproducible envelope failure plus a compliant
  deferred-finalize experiment showing ≥20% lower peak total PSS or cgroup peak
  with parity/delete/recovery intact.
- **NO_GO**: decision would require native concurrent optimize, scheduler,
  policy, or threshold changes.

## Existing infrastructure reused

- `benchmarks/harness-v2/host/primitives.py`: host snapshot, process resource
  sample, cgroup path/membership, service properties, receipt directory.
- `benchmarks/harness-v2/measurement/primitives.py`: clocks, bounded SHA-256,
  strict JSON, receipt writing.
- `research/m4-native/tools/m4_containment_runner.py`: reference cgroup memory,
  swap, PSI, I/O and smaps aggregation patterns.
- Harness V2 contracts: receipt/policy provenance. No new policy or threshold is
  introduced.

## Observability gaps (do not expand scope)

1. `JobView` has `waited_millis` but no engine start/finish timestamps or engine
   CPU. Running-job scheduler wait vs engine time needs daemon logs.
2. The daemon is one process hosting multiple workspace engines; cgroup/process
   totals cannot attribute memory per workspace without new instrumentation.
3. `memory.peak` is per run, not phase-scoped; native optimize/finalize peak is
   not isolated.
4. No native optimize internal phase boundary or per-collection memory
   attribution.
5. Queue polling observes and perturbs; 500 ms samples are not a gate.
6. No permitted native concurrent-optimize telemetry; none is required here.

## Readiness

**Ready to run baseline after one remaining artifact:** a generated fixture
manifest (paths, sizes, hashes, query/delete sets) must be frozen and hashed at
run start. The protocol, host preflight, cgroup requirements, envelope, and
decision rules are frozen. Until that manifest and preflight pass, status is
`READY_TO_PREPARE`, not `READY_TO_MEASURE`.

No benchmark, engine change, scheduler change, policy change, threshold change,
or #10 implementation was performed.
