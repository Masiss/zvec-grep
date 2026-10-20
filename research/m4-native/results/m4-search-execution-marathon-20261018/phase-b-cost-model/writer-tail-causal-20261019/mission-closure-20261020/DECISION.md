# Writer-tail engineering decision — 2026-10-20

## Objective and disposition

Explain candidate-associated writer tails without trading them for memory/writes/debt. **INCONCLUSIVE; DEFER performance optimization and production integration.** This is an evidence-limited engineering decision, not a benchmark win or a claim that host pressure caused the tails. No performance code changed. The authorized A/A did not launch; no readiness retry occurred.

## Containment closure and version control

- Prior build/no-launch continuity is separately committed as `b30c86f`.
- Lifecycle repair is committed as `bfaf443` (`fix(m4): preserve signals and retire contained units safely`). It preserves child signal codes separately from bootstrap statuses and gates retirement on empty/absent cgroups. Audit additionally rejects a not-found unit overriding a known populated cgroup, and retains pre-retirement outcome on cleanup failure.
- Source-matched synthetic lifecycle tests passed 12/12. No production/Rust source was staged.
- Investigation of the sole readiness snapshot found four failed units created by earlier deliberately killed supervisor fixtures. Their processes/cgroups had been cleaned, but the dead controller could not retire the unit, and test teardown omitted retirement. The fixture now retains abort metadata/receipt, verifies cgroup absence, then calls existing retirement (follow-up commit `7777af8`). Another bounded 12/12 synthetic run verified this concrete teardown correction; it is not a benchmark retry.
- Only those four units were matched to `.work/scratch/*/supervisor-crash/run-state.json` and `watchdog-abort.json`, checked for absent cgroups, archived and retired. Unknown units/processes were untouched. Detailed evidence: `results/containment-crash-fixture-retirement-20261020/`. This does not silently collect a real controller-crash diagnostic before an owner has retained its evidence.

## Provenance and one no-launch decision

`provenance.json` rechecks all 59 actual compiler dependencies, CMake, executable, all six recorded shared-library hashes and the 16-file build manifest. All match. Binary SHA-256 remains `57f3a19e0fb5dfe4d2d64ea4831f276a81a586c22ee30309d6249ed986ba342f`; GCC 16.2.1, Release `-O3 -DNDEBUG`. Build revision differs from current documentation/runner commits, but actual bridge/library inputs match. Generation lifecycle evidence is 3,581 checks/0 failures; not a power-loss test. No source rebuild was necessary for a runner/test-only change.

Exactly one resource evaluation used the existing runner's `host_snapshot` and `resource_guard_decisions`, loading canonical JSON:

| Guard | Observed | Unchanged threshold | Result |
| --- | ---: | ---: | --- |
| M4-RG-MEM | 5,885,576 KiB | >= 5,477,762 | PASS |
| M4-RG-DISK | 19,843,407,872 B | >= 18,219,000,000 | PASS |
| M4-RG-IOPSI | 1.37% | <= 4.46% | PASS |
| M4-RG-LOAD | 2.67 | <= 3.14 | PASS |

**Readiness as a whole did not pass.** User systemd was `degraded` with the four fixture units above. More importantly, current bridge `BuildDeltaVectorSnapshot` still calls `SyncTree(generation_root, true)` (line 1720), suppressing the outer FTS file sync, including `wal.log` after `FlushInternal` resets/truncates WAL. Handoff §35 explicitly withholds durability-equivalence proof. This is a known unresolved durability prerequisite, not a newly observed corruption or failed recovery test. The permission for diagnostic A/A does not waive the mission's no-known-durability-blocker requirement. Do not interpret ordinary reopen/oracle success as proof of power-loss equivalence.

No ambient baseline, cadence/integrity qualification, baseline-pressure derivation or arm ran. Those checks are **NOT EVALUATED**, not PASS. Root filesystem remains Btrfs at `/var/tmp`; no temp-root substitution. After fixture cleanup, no further host readiness sample was taken. `readiness-no-launch.json` preserves the exact observation, systemd probe, affinity, process inventory and host telemetry. Evidence class: **NO_LAUNCH / NOT PERFORMANCE EVIDENCE**.

## Direct raw-cycle reconstruction

`analyze_existing_cycles.py` reads the four original `stdout.jsonl` files directly, validates 20 writer cycles/80 reader queries per arm and zero coherence/reopen errors, and joins by arm+round. Outputs: 80-row `cycles.csv`, `cycle-summary.json` with input hashes. No independent percentiles are added or subtracted. Arithmetic residual is calculated only within the same cycle and remains unassigned.

All **320 reader queries finished before their same-cycle M4 build began and before publish**. Maximum last-reader window after query release was 4.075/4.665 ms baseline/candidate candidate-first and 7.825/8.616 ms in reverse order. This materially weakens a direct reader-held lock explanation for long M4/post-M4/publish spans. It does not exclude initial CPU/cache interference, delayed writeback, shared allocation/native worker contention or costs that outlive the query.

| Historical arm | First 4 commit median | Later 16 commit median |
| --- | ---: | ---: |
| Candidate-first baseline | 200.339 ms | 223.319 ms |
| Candidate-first candidate | 337.907 ms | 232.241 ms |
| Reverse baseline | 212.018 ms | 212.602 ms |
| Reverse candidate | 476.895 ms | 197.386 ms |

These are descriptive partitions, not a removal of inconvenient early samples from the benchmark. Candidate tails cluster early, but reverse candidate later median is below baseline; a uniform per-cycle candidate slowdown is not supported.

Examples from matching cycles (not aggregate percentile subtraction):

| Arm/cycle | Commit | Vector span / writer-thread CPU | M4 | Post-M4 | Publish | Compaction |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Candidate-first candidate 0 | 593.906 | 307.111 / 50.569 | 133.115 | 88.847 | 48.735 | no |
| Candidate-first candidate 1 | 431.073 | 246.424 / 39.767 | 106.772 | 24.991 | 4.927 | no |
| Reverse candidate 1 | 476.662 | 220.136 / 58.047 | 113.549 | 111.209 | 12.750 | no |
| Reverse candidate 2 | 477.128 | 232.130 / 66.363 | 93.952 | 102.894 | 20.671 | no |
| Reverse candidate 3 | 498.380 | 256.684 / 73.305 | 115.250 | 84.995 | 5.169 | yes |

Units ms. Native worker CPU is missing: wall minus writer-thread CPU is **not** a measurement of fsync wait, scheduler wait, or total native CPU. Compaction is not necessary for tails. Same logical mutation count (7 per cycle), vector rows (6 normally, 500 every fourth round), and query count are retained in raw evidence. Historical binary hashes differ from current binary and remain historical-only evidence. Candidate-first fingerprints are baseline `dcb16239…02690`, candidate `1b70bed4…0decb`; reverse command roots match but lack an independent arm-local `provenance-sha256.txt`, so exact reverse binary identity is weaker than current-source provenance.

Historical resource reconciliation (not the impact of the new runner repair): candidate-first whole-process CPU was 4,101.867 vs 3,476.330 ms, instrumented workload physical writes 251,011,072 vs 247,373,824 B, sampled peak PSS 286,915 vs 304,227 KiB. Reverse candidate/baseline CPU was 3,393.355/3,262.193 ms, writes 226,091,008/248,045,568 B, sampled peak PSS 288,416/295,621 KiB. Root bytes after close were 7,177,536 for every arm; sampled swap peak was zero. These mixed changes do not support a general write-amplification cause. Sampled PSS and process-I/O totals have different measurement boundaries from the workload's own resource record; do not combine them into one physical attribution or equate the differing RSS observations.

## Source-level stage boundaries

References below are current source, whose input hashes are in `source-inputs.json`; they explain instrumentation, not retroactively certify every historical executable.

- `tests/m4_bridge_integration.cc:3075–3081`: readiness barrier precedes `commit_start`; barrier wait is excluded. Commit includes reader release, mutation/version preparation, generation WAL, build, durable manifest publish, atomic pointer publish and retirement. This is not just a storage-library commit.
- `BuildDeltaVectorSnapshot:1529–1613`: vector span includes fresh shard/bridge metadata, `OpenVector`, conversion, upsert result handling, `zvec_collection_flush`, **DurableWrite(shard-rows.bin)** and bookkeeping. Compaction span aliases this vector span on compaction rounds; do not add it twice. It is not a pure HNSW build timer.
- Native `src/db/collection.cc:1652,1702`: upsert delegates to `write_impl`, acquiring collection schema shared lock and write mutex. `CollectionImpl::flush:495` acquires the collection's exclusive schema lock. `src/db/index/segment/segment.cc:2164` flushes WAL, stores, scalar/FTS/vector indexers, id map and version state. Lock wait and I/O are included in API wall time, not separately timed historically.
- Writer opens a **new vector shard collection**; readers pin old immutable snapshots/shards. A reader's collection lock is not automatically the writer's fresh collection lock. Direct same-object lock contention has no demonstrated path here; host/native global resources can still be shared.
- Generation metadata `1616–1631` occurs after vector timing but before M4 timing: two durable snapshot/catalog writes plus bridge manifest. Its cost lies outside those two aggregates.
- M4 `1632–1716` includes clone/open/apply/flush. `CloneM4IndexSnapshot:1467–1490` hardlinks immutable segment files, copies mutable state and calls `SyncTree(destination,false,true)`, skipping only shared segment inode fsync, not directory sync. Clone is not pure file-copy cost.
- Post-M4 `1717–1748` includes generation tree sync, new vector shard tree sync, root directory sync, snapshot allocation/copy, Coordinator construction and shard Coordinator construction. The derived historical `build_end - m4_build_end` is this enclosing region, not pure fsync. Manifest publish and pointer exchange occur **after** it, not inside it.
- M4 `src/index.cc` flush writes/checks metadata and manifest then `wal_.Reset()`; resetting WAL size without a subsequent file sync is the documented diagnostic durability gap. Do not optimize by widening this skip.

## Competing hypotheses and engineering decision

| Hypothesis | Support | Contradiction / limitation | Confidence | Smallest discriminating implication |
| --- | --- | --- | --- | --- |
| H1 Host/order | Separate sync-reduction pairs show second-arm tails/rising I/O PSI. Startup/cold-state pattern in filter pairs. | Filter candidate tail direction survives order reversal; no per-cycle host telemetry; no valid A/A. | Low causal confidence | Same-binary A/A with contemporaneous telemetry can reveal non-variant asymmetry, not prove a specific host mechanism. |
| H2 Native build | Large vector span in the same tail cycles; writer-thread CPU much smaller than wall. | Span contains metadata fsync/setup as well as native operations; worker CPU/wait breakdown missing. | High localization, low root-cause confidence | Use existing fine open/convert/upsert/flush timers in a future durability-preserving A/A; no new tracing. |
| H3 Post-M4 | Reverse candidate cycles 1/2 have 111.209/102.894 ms in enclosing interval; readers already gone. | Candidate-first cycle 1 tails with only 24.991 ms there; vector/M4/publish also vary. | Medium localization, low causal confidence | Existing post-sync/finalize timers can distinguish these enclosing costs in the same future A/A. |
| H4 Shared bottleneck | Both arms execute same broad fresh-shard flush/durable metadata path; direct late reader-lock explanation weakened. | No evidence identifies shared allocator, pool, scheduler or filesystem as causal. | Low | Correlate same-cycle stage and host telemetry, without attributing shared locks by analogy. |
| H5 Measurement artifact | Historical fingerprints differ; finer timer boundaries absent; trace OFF/ON known overhead; vector label overly broad; reverse provenance weaker. | Raw cycle arrays coherent, workload counts match, zero coherence errors, direction repeated under reversal. Current-source dependencies and all shared libs match. | High on limitations; low that artifact alone explains tails | Use byte-identical executable and existing diagnostics-off instrumentation; avoid comparing old/current latencies. |

**Decision: DEFER correction/optimization.** There is no identified candidate-specific mechanism justifying a writer optimization, lock refactor, broader sync elision or production change. No causal performance correction was implemented. Retrieval/freshness/durability contracts remain unchanged by the runner/test closure. CPU, peak RAM/PSS, I/O/physical writes and maintenance-debt impact of closure are **NOT MEASURED**; short synthetic tests are not performance measurements. Retained evidence consumes ordinary research disk, itemized in the retention receipt.

## Exactly one proposed next experiment (requires owner approval)

After resolving the durability prerequisite with a durable-equivalent binary (restoring normal full outer file-sync semantics or establishing equivalence through an approved recovery proof), preregister **one same-binary 500×20 A/A** using existing fine stage timers, diagnostics/tracing off, fresh Btrfs roots, the unchanged resource/integrity/pressure/containment gates, and current host telemetry. Analyze same-cycle vector substeps, post-sync/finalize, manifest publish and host pressure. This tests whether large asymmetry can occur without a code variant. It cannot identify candidate regression or a named host mechanism by itself. No new benchmark campaign, instrumentation, policy weakening, or repeat-for-good-results is proposed.

The current mission's single resource evaluation is consumed; fixture retirement is not permission to reevaluate. Durable-mode work/measurement scope and the next A/A require owner direction/authorization. All offline work supported by these 80 cycles is exhausted at the existing timing resolution: native worker CPU and historical cycle-aligned host telemetry cannot be reconstructed from snapshots after the fact.
