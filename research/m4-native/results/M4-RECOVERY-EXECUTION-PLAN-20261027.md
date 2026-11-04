# M4 recovery and execution plan — 2026-10-27

**P3 status 2026-10-30:** previous decision `REWORK`; now **REOPENED plan-only** — [`M4-P3-REOPEN-PLAN-20261030.md`](M4-P3-REOPEN-PLAN-20261030.md). No new run authorized. See [`M4-P3-ARCHITECTURE-DECISION-20261030.md`](M4-P3-ARCHITECTURE-DECISION-20261030.md).

**Purpose:** turn the current M4 research state into an ordered, executable path toward a defensible GO / REWORK / NO-GO decision. This is a planning artifact only. No implementation, test suite, benchmark, containment launch, or production action was performed for this plan.

## Executive status

- **Gate A:** PASS only for the research integration's pass-intent, changed-file masking, query-scoped read view and fail-closed recovery behavior covered by focused tests. It does not establish power-loss durability, production readiness, or latency acceptability.
- **Publication architecture:** the original whole-pass `PassGate` fence is **REWORK** for continued research: prior lifecycle runs repeatedly recorded multi-second waits during rescan/update/rename. The selective-visibility plus commit-scoped query/writer fence is the current research candidate, not qualified.
- **Gate B:** **PARTIAL / OPERATION_BLOCKED / OWNER_DECISION_REQUIRED.** The latest authorized 64-file pair (`m4-rust-lifecycle-pair-20261027t052952z-332a873b`) completed both arms, but is `PAIR_COMPLETED_WITH_WORKSPACE_CAVEATS`; it provides no eligible Native–M4 comparison. Cause-specific counters show queue saturation, not receiver disconnection. Earlier timeout, refusal and qgroup-stop attempts remain separate historical operations.
- **Measurement:** in the latest pair, M4 had 308 queue-full drops, 42 cap omissions and p95 3,076,026 μs; native had 130 queue-full drops and p95 796,373 μs. Four passes per arm were marked overloaded. Because offered/accepted load differs and inventory/soft-budget peaks were not sampled in-run, these p95s are descriptive only, not a performance result or qualification. Do not infer a throughput win/regression.
- **Containment:** preserve existing 512 MiB memory / zero-swap / two-CPU / 128-pid / runtime / host-disk-reserve limits and registered workspace qgroup controls. Both latest workload exits, hard-safety telemetry and unit cleanup passed; post-run qgroup had 125,833,216 B available. The pair authorization is consumed. Do not retry, create a new preflight, manually clean evidence or loosen a control under it. Any later launch requires fresh explicit operation-specific authorization, a fresh ID, and passing preflight.
- **Decision today:** **REWORK / not production GO**. There is not enough valid evidence for a final NO-GO on the overall M4 idea; there is enough to reject the whole-pass fence as the current research design and to block qualification until the dependencies below are resolved.

## Source-first status and evidence reviewed

The active checkpoint and latest Gate B reports were compared with the current source and raw run artifacts. The primary evidence is:

- `results/ACTIVE-EXECUTION.md`, `results/M4-GATE-B-QUERY-LATENCY-20261026.md`, and the latest [query-drop cause report](M4-GATE-B-QUERY-DROP-CAUSE-20261027.md).
- `results/M4-GATE-B-PREFLIGHT-20261026.md`, `results/M4-GATE-B-64-FILE-PILOT-20261026.md`, and prior attempt receipts retained under the operation-specific evidence roots.
- Latest raw pair: `.work/m4-native-workspace-quota/m4-rust-lifecycle-pair-20261027t052952z-332a873b/pair-result.json`.
- Current implementation: `tools/m4_containment_runner.py` (`workspace_usage`, `workspace_quota_bounded_observation`, `workspace_accounting_summary`, `workspace_capacity_decision` and watchdog sampling), `tools/run_rust_lifecycle_pair.py`, `docs/benchmark-policy.json`, and Rust query/lifecycle telemetry under `rust/crates/zg-engine/src/pipelines/indexed_search/`.

For a lifecycle run with a valid registered qgroup contract, the watchdog now uses a cheap root-scope check plus sampled kernel-maintained qgroup capacity; it does not recursively inventory the tree on each tick. The report deliberately keeps inventory/soft-budget peaks UNKNOWN and reports qgroup completeness/headroom separately. A qgroup is only a sampled capacity bound for the registered Btrfs subvolume; it is not physical/global metadata accounting, an atomic file inventory or a lifetime peak. Postflight traversal is one non-atomic observation and cannot establish the maximum workspace usage.

Older generic observer paths and earlier operations did use recursive `workspace_usage` walks and encountered their own limits; those historical failures are preserved in their reports. They are not the direct cause of dropped query offers in the latest pair. For that pair, cause-specific counters show all measured drops were queue-full, not receiver-disconnected. No repeated recursive scan was added: O(entries) metadata work could perturb the measured workload while still failing to prove an atomic/lifetime peak.

The source implements the candidate's changed-file mask and query-scoped read guard: query embedding occurs before acquiring the read view, then retrieval through hydration is held within it. This reduces the time a query can block a publication commit, but correctness depends on the read-view/fence boundary, pending-mask lifecycle and cross-store publish order. Existing focused tests exercise selected concurrency/recovery paths; they do not yet exhaust crash points, power-loss behavior, mixed snapshots under all filters, or resource/performance qualification. The full-corpus workload remains 421 files / about 7.07 MB; the diagnostic pilot used 64 / about 0.69 MB. Neither is evidence of general repository workload representativeness by itself.

## Critical path and decision gates

These gates are ordered dependencies. A failed gate returns to the named remediation; it does not authorize bypass or automatic retry.

### Gate 0 — Freeze evidence and define the claim

1. Treat the existing stopped operation as immutable evidence. Keep the pair result, arm receipts, watchdog abort, runtime samples and workspace intact; verify hashes before any separately authorized archival/cleanup.
2. Freeze the source revision, source manifest, Rust/native binaries, configuration, corpus mutation trace, query set, candidate limits and analysis script for each later experiment. Label the 64-file workload **diagnostic**, not qualification.
3. Before collecting more data, preregister the decision metrics and thresholds: lifecycle completion and correctness; query latency (including blocked/wait time); CPU, peak memory, I/O, temporary/final storage growth; recovery; and parity. Existing materials do not provide a complete, owner-approved numeric performance acceptance envelope. Do not invent one after seeing results.

**Exit:** immutable evidence manifest and a reviewed protocol that states what can be concluded and what is explicitly out of scope. No benchmark is required for this planning gate.

### Gate 1 — Make measurement/containment independently trustworthy

1. For the registered lifecycle qgroup path, preserve the low-overhead scope+qgroup sampling; do not introduce recursive in-run scans. Synthetic regression tests now cover the distinction between complete qgroup samples and unknown inventory. Only if an owner-approved future claim requires file-inventory/soft-budget peaks, separately evaluate a bounded inventory observer on synthetic quiet/mutating trees, including permission/I/O errors, entry limits, event overflow and slow/blocking syscalls; keep uncertainty explicit and fatal cases fail-closed.
2. **Current implementation status:** capacity enforcement is separated from best-effort workspace inventory for the registered lifecycle qgroup profile. Trusted preflight/runner validation binds the qgroup proof to the approved subvolume; per-sample qgroup checks enforce the existing capacity bound, while inventory and soft-budget peaks remain `UNKNOWN` and are not promoted to PASS. Tests cover the capacity decision and summary semantics. Do not add repeated recursive scans or infer lifetime inventory from qgroup samples. Any broader claim still needs an owner-approved accounting contract; preserve the hard host disk reserve and all cgroup/process controls.
3. Unit-test the capacity decision and watchdog protocol separately from benchmark execution. Include threshold boundaries and prove that missing/mismatched/stale attestation, qgroup inconsistency, disk-reserve breach, monitor gap, cgroup cap, OOM event and cleanup failure still block/stop. Verify operation-owned cleanup and preserve failure receipts.
4. Query telemetry must use a declared offered-load protocol. The current single-flight sampler intentionally skips scheduled starts while a query is running; percentile over completed queries is then a conditional sample and can hide overload (coordinated-omission/survivorship bias). Keep scheduled, started, completed, late and missed counts; per-query latency and PassGate wait; per-pass distributions; and monitor gaps. For a latency percentile to be eligible, both arms must execute the same query mix and offered schedule with **zero missed starts** and sufficient prespecified observations, or use an explicitly bounded queueing model that records queue delay and dropped/rejected work. Do not queue unbounded work or let load generation destabilize the benchmark. If any cadence slot is missed, label that percentile non-comparable; do not compress missed intervals or discard slow/failed queries.
5. Validate the analysis with deterministic synthetic traces (known latency values, missed slots, stalls and arm-order labels) before it consumes a new workload report.

**Exit:** adversarial observer/policy tests pass; a no-launch preflight confirms every independent hard control; telemetry reports missed/censored work honestly and produces the expected classification. If capacity cannot be proven without weakening policy, stop here as **OPERATION_BLOCKED**.

### Gate 2 — Qualify selective visibility and commit-scoped fencing for correctness

Build on the existing research seam, not production wiring. Extend deterministic tests for:

- Queries to unchanged files while a mutation is staged; changed/renamed/deleted files are masked for the entire pending interval, and no query sees staged or half-published state.
- A query that starts before commit keeps one coherent read view through candidate retrieval and result hydration; a query at the commit boundary waits and sees one complete revision. Exercise vector, lexical/hybrid paths, filters, ranking, hydration and pagination/refill where applicable.
- Concurrent independent queries, overlapping or serialized writers, repeated updates to one path, rename/delete/recreate and cancellation/error paths. Verify lock ordering and absence of deadlock/starvation; collect wait ownership in tests.
- Crash/reopen/retry at each durable transition: before intent, after durable intent, after native finalize but before M4 publication, during partial M4 publication, after completion marker, and during cleanup/recovery. Verify manifest/checksum/fsync ordering, fail-closed masks, generation consistency, idempotent retry and WAL/tombstone reclamation boundaries.
- Native/M4 result parity on frozen fixtures, including IDs, paths, filters and ranking; distinguish expected eventual availability during a pending pass from stable-state parity.

Current tests are evidence for selected cases only. Passing ordinary process-restart tests is not a power-loss claim; if power-loss durability is required, design and run the appropriate fault-injection qualification under separate authorization.

**Exit:** deterministic correctness/recovery suite passes with no mixed snapshot; documented invariants and failures match implementation. Any stale/mixed result, lost durable intent, unrecoverable state, or lock deadlock is a **REWORK** blocker.

### Gate 3 — Measurement-valid diagnostic pair

Only after Gates 1–2 pass, the owner explicitly authorizes one fresh bounded diagnostic operation, and a new preflight approves it:

1. Start on the frozen 64-file diagnostic corpus to verify the complete harness and observer. Do not call this full Gate B. If this scope is still covered by an existing explicit authorization, confirm it in the operation record; otherwise request fresh approval. A stopped arm is never retried under the same operation ID.
2. Run both arms over exactly the same six-pass mutation trace and query schedule, identical Rust engine, native index baseline and pinned build. Counterbalance order across separately authorized pairs; a single fixed-order pair is descriptive and cannot establish a causal win. Avoid introducing extra repetitions if resource headroom or policy does not support them.
3. Require both arm exit codes, correctness/reopen checks, complete hard-safety telemetry, verified cleanup, valid workspace capacity decision and postflight PASS before calling the pair complete. A complete but soft-budget-warning result may be useful descriptively only if the policy explicitly allows it; it is not qualification.
4. Report lifecycle phase timings; query scheduled/start/completion counts, p50/p95/p99 and tail maximum with confidence/limitations; PassGate wait; CPU time/pressure; cgroup peak and `memory.events`; process RSS/PSS where available; I/O bytes/pressure/limitations; workspace and final index size; compaction/optimize time; and all dropped/missed/error observations. Host before/after state accompanies each measurement. The available CGroup environment lacks `io.stat`/`io.max`, so any I/O comparison must state that containment limitation.

**Exit:** one valid matched diagnostic pair with comparable query schedules and zero safety/measurement integrity failures. If only 64-file works, report only that scope and do not infer full-corpus behavior.

### Gate 4 — Representative full-lifecycle qualification

1. Resolve resource feasibility before launch. The prior full-corpus native arm hit the existing 512 MiB cgroup ceiling during rescan; the latest observer stop is separate. Identify and test an implementation-side memory remedy on frozen correctness tests, or conclude the current design cannot fit the approved envelope. Do not increase memory, swap, runtime or disk reserves to force a pass.
2. Define representative corpora and churn: full 421-file Rust source manifest as a minimum bridge from pilot, plus a separately agreed representative production-like corpus if one exists; include file-size/type distribution, add/update/rename/delete ratios, repeated rescans, stable and churn phases, and representative lexical/vector/hybrid queries. Record source revision and hashes.
3. Compare the existing native `optimize` maintenance path against M4 flush/compaction on the **same** mutation trace, rebuild conditions, model/index parameters, query set and warm/cold protocol. Include steady-state retrieval parity and reopen/recovery. Keep M4 operations on the research path.
4. Run enough independent, counterbalanced pairs for prespecified uncertainty; one pair or one host session cannot support qualification. If the approved policy permits only a single pair, explicitly classify results as exploratory and seek a policy/authorization decision before repeated measurement.
5. Score every preregistered dimension independently: correctness/parity; per-phase and end-to-end latency; query wait/tails; CPU; peak RAM/swap; I/O and write amplification; temporary plus final storage and growth/reclamation across lifecycle; crash/reopen/retry; and containment/cleanup. A missing dimension is `NOT QUALIFIED`, not inferred from another metric.

**Exit:** all preregistered correctness and resource gates pass with representative, measurement-valid evidence and uncertainty acceptable for the intended claim. Any hard-resource stop, invalid query sampling, incomplete arm or unbounded growth keeps Gate B open.

### Gate 5 — Architecture decision and production boundary

- **GO to the next explicitly authorized integration milestone** only if selective visibility/fencing passes Gate 2, representative matched qualification passes Gate 4, and migration/rollback, watcher batching, snapshot visibility, durability, operational limits and support/observability are separately addressed. GO here is not automatic production deployment authorization.
- **REWORK** if correctness holds but fencing tails, full-corpus resource use, compaction/storage growth, recovery behavior or incomplete evidence misses preregistered criteria. State the failing phase and return to the narrowest dependency; do not restart a broad campaign by default.
- **NO-GO for the current M4 design** if a required correctness/durability invariant cannot be met, the current design cannot stay within authorized hard limits after reasonable bounded remediation, parity is irreparably violated, or representative measurements repeatedly fail agreed thresholds. Preserve evidence; distinguish rejecting this design from rejecting all future incremental-index approaches.

**Production integration remains outside the present authorization.** It requires a separate owner decision after research qualification; no code in this plan wires M4 into production, changes policy, or migrates existing indexes.

## Dependency and ownership summary

| Dependency | Blocks | Required evidence / decision |
| --- | --- | --- |
| Workspace observer/capacity certification | Any valid lifecycle pair | Root-bound proof for this exact purpose or adversarially verified fail-closed observer; no stale/foreign attestation |
| Query offered-load and percentile integrity | Query latency decision | No missed scheduled starts (or an explicit bounded queue model), retained slow/failure observations, validated analyzer |
| Selective-visibility/recovery correctness | Architecture decision | Snapshot boundary, changed-file mask and crash-point invariants across native/M4 publication |
| 512 MiB native peak and workspace headroom | Full-corpus Gate B | Safe implementation-side memory/resource result within unchanged policy; fresh preflight |
| Representative matched workload and order | Qualification | Frozen matched corpus/mutations/queries, counterbalanced independent pairs, preregistered thresholds |
| I/O telemetry availability | I/O efficiency claim | Instrumented trustworthy I/O metric or explicitly qualified limitation; current cgroup I/O controllers unavailable |
| Production readiness/rollback | Any production GO | Separate migration, fallback/rollback, watcher, operational and owner approval |

## Remaining execution actions (no new benchmark authorization)

1. Preserve all prior refusals/stops plus the completed but non-comparable pair `m4-rust-lifecycle-pair-20261027t052952z-332a873b`; do not relaunch or clean any retained operation evidence without separate authorization.
2. **Completed:** selected exact-purpose root-attested Btrfs qgroup certification under unchanged caps, with sampled inventory diagnostic-only and incomplete inventory remaining unknown.
3. **Completed:** fixed-deadline offered-load telemetry distinguishes queue-full drops from disconnected receivers and has regression coverage. Latest pair identifies queue saturation, but query p95 remains ineligible due drops, M4 cap omissions and overloaded passes. Workspace inventory/soft-budget peak remains UNKNOWN; see [latest query-drop report](M4-GATE-B-QUERY-DROP-CAUSE-20261027.md).
4. **Completed:** add a watchdog-recorded operator-stop command bound to the exact active unit/cgroup; validate its no-overwrite request path with a contained synthetic fixture. Preserve the original dc8e1120 `CONTAINMENT_UNVERIFIED` classification.
5. **Implemented, not arm-qualified:** added `cfg(test)` `IndexStore` mutex-wait attribution and periodic fixed-deadline query-load JSON snapshots to captured arm stderr, plus snapshot-schema and thread-local reset regressions. This allows service-time/lock-wait analysis and leaves interim offer/start/completion/drop counters before final report serialization; end-to-end persistence through an actual externally stopped arm remains unverified. Keep workload and production runtime unchanged.
6. Expand selective-fence correctness/crash-point coverage independently. A new diagnostic pair requires fresh explicit owner authorization and passing no-launch preflight; full-corpus Gate B additionally needs preregistered thresholds and a safe native memory result under the unchanged 512 MiB cap.

## Completion statement

This document began as an execution plan; the execution updates below record subsequent implementation and test evidence. Current status is Gate A research-scope PASS; Gate 1 diagnostic infrastructure PASS for the bounded setup with qgroup capacity evidence but UNKNOWN inventory/soft-budget peaks; Gate 2 selective-fence correctness PASS for tested cases but not exhaustive qualification; Gate 3 pair completed with caveats and no eligible comparison; Gate 4 numeric thresholds/resource envelope open; Gate 5 outside current authority. Overall M4 remains **PARTIAL / OPERATION_BLOCKED / OWNER_DECISION_REQUIRED; architecture REWORK, not production GO**. The latest one-pair authorization is consumed; no further benchmark is currently authorized.

## Latest execution update — 2026-10-27 (cause-specific query diagnostic)

The fresh operation `m4-rust-lifecycle-pair-20261027t052952z-332a873b` passed no-launch validation and completed M4-first/native-second. Both arms exited 0, hard-safety telemetry was complete, and containment cleanup was verified. Cause-specific counters show M4: 308 queue-full drops, 0 disconnected, 42 not offered by cap; native: 130 queue-full drops, 0 disconnected, 0 cap omissions. Four passes per arm were overloaded; the query p95 gate is `NOT_COMPARABLE_SAMPLING_INCOMPLETE`. Both arms also retain workspace inventory/soft-peak uncertainty despite complete qgroup samples. Post-run qgroup headroom was 125,833,216 B. The operation authorization is consumed; no retry, new preflight, cleanup or control change.

A read-only review confirmed that exact in-run file inventory would require potentially perturbative recursive metadata walks and still would not prove an atomic/lifetime peak. The qgroup/inventory distinction is already explicit in observer output; this continuation extracted the existing aggregation to `workspace_accounting_summary` and added synthetic tests for complete and incomplete qgroup samples. It changes no sampling cadence or workload behavior. Verification: containment suite 26 passed, Rust M4 integration namespace 11 passed / 1 intentionally ignored, benchmark policy tests 30 passed; no benchmark was run for this follow-up. Full details: [query-drop report](M4-GATE-B-QUERY-DROP-CAUSE-20261027.md), [handoff](HANDOFF-20261027.md).

## Earlier execution update — 2026-10-27

Gate 1 selected the registered kernel-enforced qgroup plus light sampled telemetry. Live no-launch validation passed with qgroup `0/259` available headroom 117,755,904 bytes, a 67,108,864-byte required minimum-free reserve and a 33,554,432-byte per-arm allocation budget. The root-disk free-space reserve also passed. The observer no longer relies on recursive inventory as the hard capacity authority; incomplete inventory is explicitly unknown.

The fixed-deadline offered-load reader and independent cross-arm report analyzer were implemented. The Python containment/pair-analysis suite passed 24/24; synthetic traces exercise known p95 values, reversed arm order, queue drops, cap-denied/missed starts, query failure, sampling cap, scheduler stall and protocol mismatch. The already-built feature-enabled Rust M4 test binary passed 9 tests; the contained lifecycle workload remained ignored. Selective visibility, unaffected reads, commit fencing, reopen/retry and existing search/filter/watcher regressions are covered in the passing tests, but this is not exhaustive or power-loss qualification.

No benchmark workload was launched. Machine policy says the explicit 2026-10-26 one-pair authorization has been consumed and the 2026-10-27 direction does not authorize a new operation. A fresh explicit owner authorization is required for another diagnostic pair. Representative qualification also awaits numeric preregistered acceptance thresholds, a safe memory result under the unchanged 512 MiB cap, and counterbalanced representative runs. cgroup `io.stat`/`io.max` are unavailable, so per-arm physical-I/O attribution is not supported.

Detailed evidence: [`M4-RECOVERY-GATE-1-2-20261027.md`](M4-RECOVERY-GATE-1-2-20261027.md) and [`M4-RECOVERY-GATE-3-DIAGNOSTIC-20261027.md`](M4-RECOVERY-GATE-3-DIAGNOSTIC-20261027.md). Existing stopped operations remain immutable and are not retried.

### Earlier Gate 3 measurement follow-up — 2026-10-27

The latest diagnostic (`dc8e1120`) remains invalid for comparison: the native arm stopped after 50/64 files and the M4 arm never started. Its qgroup and cgroup limits passed until the operator stop, but it had no completed query telemetry. The original stop classification remains `CONTAINMENT_UNVERIFIED` because the transient unit was removed before workload-exit telemetry was captured.

The containment runner now offers `stop --receipt-dir ... --confirm-unit ...`. It requires the exact active unit, a live operation-owned cgroup, a completed initial watchdog safety sample, a started workload, and no terminal marker. The stop request is written durably and atomically without overwrite; the watchdog verifies the operation binding, records `operator_requested_stop`, and owns subsequent cgroup cleanup. A late request after the workload completion marker does not abort completed work. The synthetic systemd-backed regression initially exposed a race when cancellation preceded the watchdog baseline; the command now refuses that state. Final containment suite: **26 passed**. This validates only the control path, not benchmark performance or qualification.

Fresh Rust verification compiled the current feature-enabled M4 integration test source and passed **9 tests, 1 intentionally ignored**. The reported `reader_sampling_capped` finding and the cited line-595 finding do not match the current file: line 343 is the search call's `&[]`, line 595 is blank, and the aggregate uses `Value::as_u64`. LSP recheck was inconclusive, so the compiler/test run is the verification evidence. The compiler also reports `DEFAULT_MAINTENANCE_THRESHOLD` as dead code in the library build; tracing shows it is referenced only by a `#[cfg(test)]` fallback. It is outside this measurement-control change and was not edited.

Next safe work remains research/test-only: attribute native search service versus shared-state mutex wait, retain offered-load completion/drop telemetry on abnormal test termination, and extend selective-fence crash/concurrency coverage. No benchmark or production-runtime change was made.

### Earlier Gate 3 attempt — 2026-10-27 preflight refused

After Gate 2 staging-mask failure recovery and selective-delete tests passed, the one additional owner-authorized 64-file pair was attempted with a fresh `--validate-only`. It refused before workspace materialization or arm launch: qgroup available 89,235,456 B versus 100,663,296 B required, a shortfall of 11,427,840 B. The authorization was consumed. See [`M4-GATE-B-PREFLIGHT-REFUSAL-20261027.md`](M4-GATE-B-PREFLIGHT-REFUSAL-20261027.md).

### Earlier Gate 3 attempt — 2026-10-27 quota recovery and timeout

The owner approved archival and removal of only the prior stopped `dc8e1120` workspace. Its no-reflink archive and 368-entry SHA-256 manifest were verified outside the quota subvolume. Qgroup headroom recovered to 117,755,904 B, and fresh `--validate-only` passed for `m4-rust-lifecycle-pair-20261027-034750-2d863149`. The authorized M4-first arm then hit the 900-second runtime limit during initial indexing at 50/64 files. The native arm never launched; no comparison exists. The operation authorization is consumed. The owner subsequently approved archival and removal of only this timed-out operation root; its 449-entry no-reflink SHA-256 archive was verified before deletion. At that checkpoint qgroup was 117,755,904 B available and no new preflight or benchmark had yet run; subsequent authorization outcomes follow below. Captured stderr had no query snapshots, but the binary did contain the marker; the earlier negative check was a `pipefail`/`rg -q` false negative. Static inspection found a two-party start barrier for three test participants, which could strand the query scheduler; this was corrected to three parties and the non-ignored Gate B suite passed (10 passed, 1 intentionally ignored). The initial-indexing stall remains unexplained. The owner authorized one fresh pair using the post-fix binary, but the generated operation ID `m4-rust-lifecycle-pair-20261027T045033Z-9da70ee5` used uppercase `T/Z`; `--validate-only` refused it under the lowercase ID contract. The owner then authorized one additional pair with a lowercase-valid ID. Its immediate preflight passed, but native-first execution completed only the native arm; before M4 allocation, the qgroup gate observed 97,935,360 B available versus 100,663,296 B required. M4 did not start; a later read-only qgroup check showed 117,407,744 B, which does not authorize retry. That authorization is consumed. The raw result remains unchanged despite a wrapper classification mismatch; the classifier was corrected and its regression tests passed 2/2. See `M4-GATE-B-SECOND-ARM-QGROUP-STOP-20261027.md` and `M4-GATE-B-PREFLIGHT-ID-REFUSAL-20261027.md`. That cleanup completed with verified manifests: the two archives contain 265 entries/9,465,806 B and 20 entries/466,984 B respectively; sources were removed only after verification. The authorized pair `m4-rust-lifecycle-pair-20261027t051335z-c8cf6382` passed preflight and completed both arms with parity pass, but status is `PAIR_COMPLETED_WITH_WORKSPACE_CAVEATS`; query drops/missed starts/sampling caps make p95 non-comparable. Current qgroup is 102,072,320 B available, 1,409,024 B above the next-workspace requirement. Authorization consumed; no retry, new preflight, or manual cleanup. Further operation requires fresh owner authority and sufficient headroom. See `M4-GATE-B-PAIR-COMPLETED-WORKSPACE-CAVEATS-20261027.md`. Full-corpus Gate 4 still needs owner-approved numeric thresholds; production remains out of scope. Details: [`M4-GATE-B-QUOTA-RECOVERY-TIMEOUT-20261027.md`](M4-GATE-B-QUOTA-RECOVERY-TIMEOUT-20261027.md).

## Navigation

- [Active execution checkpoint](ACTIVE-EXECUTION.md)
- [Gate B query-latency evidence](M4-GATE-B-QUERY-LATENCY-20261026.md)
- [Gate B preflight and containment](M4-GATE-B-PREFLIGHT-20261026.md)
- [Gate B diagnostic pilot](M4-GATE-B-64-FILE-PILOT-20261026.md)
- [Gate B query-drop cause and workspace caveats — 2026-10-27](M4-GATE-B-QUERY-DROP-CAUSE-20261027.md)
- [M4 mission handoff — 2026-10-27](HANDOFF-20261027.md)
- [Gate B preflight refusal — 2026-10-27](M4-GATE-B-PREFLIGHT-REFUSAL-20261027.md)
- [Gate B quota recovery and diagnostic timeout — 2026-10-27](M4-GATE-B-QUOTA-RECOVERY-TIMEOUT-20261027.md)
- [Benchmark policy](../docs/benchmark-policy.md)
- [Rust incremental-index design](../../../rust/docs/m4-incremental-native-index.md)
