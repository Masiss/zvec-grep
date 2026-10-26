# M4 recovery and execution plan — 2026-10-27

**Purpose:** turn the current M4 research state into an ordered, executable path toward a defensible GO / REWORK / NO-GO decision. This is a planning artifact only. No implementation, test suite, benchmark, containment launch, or production action was performed for this plan.

## Executive status

- **Gate A:** PASS only for the research integration's pass-intent, changed-file masking, query-scoped read view and fail-closed recovery behavior covered by focused tests. It does not establish power-loss durability, production readiness, or latency acceptability.
- **Publication architecture:** the original whole-pass `PassGate` fence is **REWORK** for continued research: prior lifecycle runs repeatedly recorded multi-second waits during rescan/update/rename. The selective-visibility plus commit-scoped query/writer fence is the current research candidate, not qualified.
- **Gate B:** **PARTIAL / OPERATION_BLOCKED.** No valid matched Native–M4 lifecycle or query-latency result exists. The 64-file pilot is diagnostic only. The latest pair (`b6a1348d`) ran the M4 arm, then stopped the native arm when the bounded live workspace traversal exceeded its per-scan entry/time budget. No native report or parity result was produced.
- **Measurement:** earlier query p95s are non-comparable because fixed-cadence single-flight query schedules missed hundreds of slots. The 5.53 s pilot p95 from 16 observations is not a valid estimate. The later selective-fence M4 arm still missed 208 slots; its 71.7 ms aggregate p95 is not comparable. These data cannot establish that the candidate is fast enough or that the observed fence maxima are a representative distribution.
- **Containment:** preserve the existing 512 MiB memory / zero-swap / two-CPU / 128-pid / runtime / host-disk-reserve limits, and the applicable workspace controls. Do not bypass observer failures, loosen a hard limit, retry a stopped operation, delete evidence, or treat a 64-file run as full-corpus qualification. Any future launch needs a fresh explicit operation authorization, fresh operation identity, valid preflight, and the required recovery conditions.
- **Decision today:** **REWORK / not production GO**. There is not enough valid evidence for a final NO-GO on the overall M4 idea; there is enough to reject the whole-pass fence as the current research design and to block qualification until the dependencies below are resolved.

## Source-first status and evidence reviewed

The active checkpoint and latest Gate B reports were compared with the current source and raw run artifacts. The primary evidence is:

- `results/ACTIVE-EXECUTION.md` and `results/M4-GATE-B-QUERY-LATENCY-20261026.md`.
- `results/M4-GATE-B-PREFLIGHT-20261026.md` and `results/M4-GATE-B-64-FILE-PILOT-20261026.md`.
- `results/M4-GATE-B-QUERY-LATENCY-20261026/launch-{f4a09e2c,7c21f33a,b6a1348d}.json` and the b6a1348d pair receipts, watchdog summary/abort, runtime samples and retained workspace.
- Current implementation: `tools/m4_containment_runner.py` (`workspace_usage`, `workspace_capacity_decision`, watchdog sampling and arm completion), `tools/run_rust_lifecycle_pair.py`, `docs/benchmark-policy.json`, `rust/crates/zg-engine/src/pipelines/indexed_search/{pipeline.rs,storage.rs,m4_integration_tests.rs,m4_integration_tests/gate_b_lifecycle.rs}`, and the research FFI/adapter.

The observer's `workspace_usage` performs a fresh recursive descriptor walk on every watchdog sample (one pass per sample; current scan budget 0.25 seconds, entry budget 16,384), installs inotify watches while walking directories, and drains only one bounded event buffer. Its deadline is cooperative and cannot bound a blocked filesystem syscall. In the b6 native arm, the raw failure names `index/storage/reconciliation` and then the workspace-root cooperative deadline; the watchdog classified it `WORKSPACE_CAPACITY_UNVERIFIABLE`, with `workspace_samples_complete=false`. The measured memory peak was 260.4 MiB, below the 512 MiB cap; this stop was an **observer/accounting failure**, not the previous memory-cap stop. The small tree measured after termination cannot reconstruct peak live state.

The evidence supports this diagnosis: walking a changing tree while each 0.5-second monitor tick launches another bounded full traversal is an observer-capacity problem; it is not evidence that the workload exceeded the workspace's physical limit. That causal explanation remains a hypothesis until isolated tests establish scan cost and workload sensitivity. Current policy defaults to fail-closed for work-budget failure; the exact Btrfs quota attestation is registered for a different benchmark purpose, not this lifecycle pair. Do not silently reuse that proof or convert incomplete traversal into PASS.

The source implements the candidate's changed-file mask and query-scoped read guard: query embedding occurs before acquiring the read view, then retrieval through hydration is held within it. This reduces the time a query can block a publication commit, but correctness depends on the read-view/fence boundary, pending-mask lifecycle and cross-store publish order. Existing focused tests exercise selected concurrency/recovery paths; they do not yet exhaust crash points, power-loss behavior, mixed snapshots under all filters, or resource/performance qualification. The full-corpus workload remains 421 files / about 7.07 MB; the diagnostic pilot used 64 / about 0.69 MB. Neither is evidence of general repository workload representativeness by itself.

## Critical path and decision gates

These gates are ordered dependencies. A failed gate returns to the named remediation; it does not authorize bypass or automatic retry.

### Gate 0 — Freeze evidence and define the claim

1. Treat the existing stopped operation as immutable evidence. Keep the pair result, arm receipts, watchdog abort, runtime samples and workspace intact; verify hashes before any separately authorized archival/cleanup.
2. Freeze the source revision, source manifest, Rust/native binaries, configuration, corpus mutation trace, query set, candidate limits and analysis script for each later experiment. Label the 64-file workload **diagnostic**, not qualification.
3. Before collecting more data, preregister the decision metrics and thresholds: lifecycle completion and correctness; query latency (including blocked/wait time); CPU, peak memory, I/O, temporary/final storage growth; recovery; and parity. Existing materials do not provide a complete, owner-approved numeric performance acceptance envelope. Do not invent one after seeing results.

**Exit:** immutable evidence manifest and a reviewed protocol that states what can be concluded and what is explicitly out of scope. No benchmark is required for this planning gate.

### Gate 1 — Make measurement/containment independently trustworthy

1. Reproduce the observer failure with bounded synthetic workspaces and controlled concurrent file creation/replacement; record scan duration, entries, directory count, event queue state and watchdog scheduling gaps. Test quiet and mutating trees, including permission/I/O errors, depth/entry limits, event overflow and slow/blocking filesystem behavior. Keep all fatal cases fail-closed.
2. Separate *capacity enforcement* from *best-effort workspace inventory*. Evaluate two defensible routes, without implementing either under this plan:
   - **Preferred if owner/policy accepts it:** register a root-owned, UUID/subvolume/qgroup-bound quota proof for this exact lifecycle purpose, with its existing cap and minimum free reserve. The proof must be checked by the trusted preflight/runner, not supplied by the workload; hard host disk reserve and all cgroup/process controls remain in force. Incomplete inventory must remain explicitly `UNKNOWN/INCOMPLETE`; the quota proof must not be described as physical-device usage or lifetime inventory.
   - Otherwise, reduce observer cost while retaining current fail-closed semantics: avoid a full recursive walk every 500 ms (for example, use a bounded baseline plus incremental event accounting only if event loss/overflow, replacement and missed-event conditions are detectable and fatal). Prove that the monitor cadence and a single blocked syscall cannot silently create an unobserved gap. Do not merely raise the entry/time budget or permit continuation on an unverified scan.
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

## Immediate next actions (planning only; not yet authorized for execution)

1. Preserve the b6a1348d stopped evidence and do not relaunch it.
2. Ask the owner to choose the observer-certification route in Gate 1: exact-purpose root-attested qgroup registration under unchanged caps, or retain scan-based fail-closed accounting and first resolve/test its capacity failure. The current quota registration is not for this lifecycle pair.
3. Implement and test only the selected observer/telemetry correction; keep failures fail-closed. No benchmark yet.
4. Expand the selective-fence correctness matrix and crash-point tests; update the invariant documentation from actual source.
5. Preregister latency/load/performance thresholds, then seek fresh authorization for a new 64-file diagnostic pair only if Gates 1–2 and no-launch preflight pass.
6. Attempt representative full-corpus Gate B only after the 64-file protocol is measurement-valid and the native arm demonstrably fits the current 512 MiB limit. If it does not, return a resource-bounded REWORK/NO-GO decision rather than raising limits.

## Completion statement

This document is an execution plan, not evidence that any gate has been newly passed. The latest verified state remains Gate A research-scope PASS; whole-pass fence REWORK; selective-fence candidate unqualified; Gate B partial/blocked; overall M4 **REWORK / not production GO**. No new benchmark authorization is inferred from this plan.

## Navigation

- [Active execution checkpoint](ACTIVE-EXECUTION.md)
- [Gate B query-latency evidence](M4-GATE-B-QUERY-LATENCY-20261026.md)
- [Gate B preflight and containment](M4-GATE-B-PREFLIGHT-20261026.md)
- [Gate B diagnostic pilot](M4-GATE-B-64-FILE-PILOT-20261026.md)
- [Benchmark policy](../docs/benchmark-policy.md)
- [Rust incremental-index design](../../../rust/docs/m4-incremental-native-index.md)
