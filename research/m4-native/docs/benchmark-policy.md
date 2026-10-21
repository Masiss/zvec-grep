# M4 benchmark policy and executable contract

**Scope:** standalone research prototype under `research/m4-native/`. This policy does not authorize production integration, migration, deployment, native checkout changes, or any change to the production ABI/schema. The machine-readable values are in adjacent `benchmark-policy.json`; runner code loads that file, and tests verify the enforcement mappings.

## Workspace observation integrity (observer schema 2)

`workspace_observer` in the JSON controls one bounded, no-follow descriptor traversal per sample: entry/depth/issue/event limits and a cooperative deadline, no retry/rescan. A blocked filesystem syscall is not preempted by that deadline; independent containment remains required. Per-pass inotify/identity checks detect known mutations but are not an independent accounting backend (mmap/writeback, recursive coverage and queue loss remain limitations).

Complete traversal means **sampled, not atomic**. Known-race partial observations remain incomplete, with raw issues retained; permission/EIO/scope/budget/coverage failures remain unverifiable. Without independently verified scoped capacity evidence, incomplete observations abort (`workspace_capacity_unverifiable`), never PASS. Sampled limit excess aborts with observed value/threshold, without overclaiming simultaneous aggregate usage. No upper-bound or complete-snapshot backend is implemented; verified capacity/peak stays unavailable. Externally supplied assertions cannot certify one.

Runtime/postflight use the same capacity decision. Incomplete workspace sampling makes measurement quality INCOMPLETE; partial totals cannot update sampled workspace peaks, and incomplete series have null sampled peaks. Gross Btrfs/time-skewed allocation receives **zero shared-disk reserve credit**, not a deduction from approved reserves. Existing resource thresholds and native durability remain unchanged. Details, limitations, raw test failures and final 72-test verification: `../results/workspace-observer-repair-20261020/REPORT.md`. Native launch readiness remains blocked; this correction authorizes no native attempt.

## Authority and interpretation

The active user instruction authorizes the operation. Repository-wide mission and execution rules are in root `AGENTS.md`; M4 design/workflow constraints are in `research/m4-native/AGENTS.md`. `benchmark-policy.json` is the sole machine-readable source of resource-guard values and classifications. This Markdown document explains policy meaning and control flow; it is not parsed by enforcement code and intentionally does not duplicate numeric thresholds. Guard values are owner-controlled.

The resource guards derive from the successful historical B3.1 arm-before envelope preserved in the canonical M4 handoff and result artifacts. They are empirical limits, not a validated model of universal host safety. A current failing guard forbids or terminates the affected benchmark operation; that refusal does not prove a host just beyond the threshold is unsafe or block unrelated, authorized, low-risk work. I/O PSI is host-wide, not an arm-specific I/O cap.

Do not change a threshold, weaken an assertion, or bypass a refusal in research code without explicit owner approval. An amendment must include the proposed values/classification, evidence and uncertainty, risk assessment, fallback/rollback, acceptance tests, and an update to this JSON, this document, `RESULTS.md`, and the canonical handoff. A resource refusal alone is not evidence that a threshold should be changed.

## Gate classes

| Class | Purpose / authority | Current enforcement | Failure scope and recovery |
|---|---|---|---|
| Correctness and durability | M4 format, search, recovery, WAL/manifest invariants and approved contract | Oracle/regression, fault/recovery, build and sanitizer tests; candidate acceptance | A correctness failure is not bypassed by a diagnostic timing run. Candidate stays NO-GO. Diagnose/fix in scope or stop that candidate; another independently approved branch may continue. |
| Containment safety | Keep an operation and its descendants within the owned per-arm service; terminate on timeout, controller loss, OOM, or telemetry failure | transient systemd user service, delegated cgroup v2, watchdog/pidfd, cgroup event checks, final cleanup receipt | Abort the affected arm; verify owned descendants are gone. Never kill unrelated host processes. If cleanup is unverified, `SAFETY_STOP` the operation and investigate before another run. |
| Operational resource guards | Avoid launching outside the currently adopted historical measurement envelope | Current exact thresholds in `benchmark-policy.json`; `run_sync_reduction_pair.py::preflight` and sampled runtime checks; A/A baseline/recovery checks; `m4_containment_runner.py::hard_gate_failures` and watchdog | Hard refusal for the affected arm/pair only. Recovery requires a new passing snapshot and every required sample/baseline check. Do not auto-retry indefinitely; retain a reasoned receipt and continue safe independent tasks. Numeric change requires owner approval. |
| Measurement integrity | Establish that telemetry is present, finite, correctly bracketed, monotonic and sufficiently complete | `run_host_carryover_aa.py::validate_baseline_samples`, recovery validation, process/cgroup sample completeness | Blocks both modes when invalid; diagnostic mode cannot manufacture evidence from missing/corrupt samples. Recover by fixing telemetry or recapturing under the already-approved policy. |
| Measurement quality | Decide whether performance comparisons support the intended claim | Six-window stability assessment and inter-arm matching/recovery logic in the A/A orchestrator | Qualification requires the adopted quality envelope. Diagnostic may bypass only a valid stability-envelope failure when all operational resource, pressure, containment, and integrity guards pass; label output `EXPLORATORY / NOT QUALIFICATION EVIDENCE`. No stable win/regression or GO from one exploratory pair. |
| Historical planning heuristic | Estimate workload duration, RAM, disk headroom, or likely host contention from prior runs | Planning/reporting only unless explicitly promoted into the executable policy | Not independently a launch gate. State source, age, workload match and uncertainty. Never present a point estimate or empirical envelope as a universal safety fact. |

## Current operational guard values

| Gate ID | Metric | Threshold source | Source/evidence classification |
|---|---|---|---|
| `M4-RG-MEM` | `MemAvailable` | `benchmark-policy.json` | Empirical successful-run envelope; operation-level hard refusal while adopted |
| `M4-RG-DISK` | resource-root free space | `benchmark-policy.json` | Empirical retained-capacity envelope; not GNU-time output bytes |
| `M4-RG-IOPSI` | host I/O PSI full avg10 | `benchmark-policy.json` | Empirical successful-run envelope; not universal safety and not per-arm I/O control |
| `M4-RG-LOAD` | host load-1 | `benchmark-policy.json` | Empirical successful-run envelope; not universal safety |

The exact values and their metadata exist only in **`benchmark-policy.json`**. This table is descriptive and does not define independent thresholds.

## Enforcement map

- `run_sync_reduction_pair.py::preflight` records snapshot, observed value, required value, gate ID, evidence class, source of authority, affected-operation scope, and no-launch outcome. Its runtime sampler enforces the same adopted values.
- `run_host_carryover_aa.py` validates the ambient baseline samplewise, separates telemetry integrity, resource/pressure refusal, and stability quality, and writes operation-level status. It never owns whole-mission completion.
- `m4_containment_runner.py::resource_guard_decisions`, `hard_gate_failures`, `run_arm`, and the independent watchdog enforce/record the same resource values for contained work. Baseline-derived memory/swap limits are additional operation guards, not replacements for the policy file. The only host-guard override is constrained to explicit, temp-tree synthetic test fixtures; those receipts are marked `test_fixture_mode=true` and `performance_eligible=false` and cannot wrap benchmark-named or benchmark-argument commands.
- `run_sync_reduction_pair.py`, `run_host_carryover_aa.py`, and the retained `run_diag_aa_v2.py` / `run_matched_evaluator_pair.py` load the guard values from this JSON. The latter two had duplicated literals; they now map their local resource-root key (where needed) from the canonical `var_tmp_bytes_min`. `test_m4_benchmark_policy.py` guards these files against reintroducing threshold literals. The markdown table is explanatory and is not parsed by runners.
- `m4_containment_runner.py` has observed cgroup-v2 CPU, memory and pids delegation. I/O is not delegated on the calibrated host. Therefore `io.max`/`io.stat` may be unavailable; host diskstats and process-tree `/proc` I/O have different scopes and cannot be called per-arm physical-write attribution. No cgroup resource ceiling is implied unless its budget is explicitly approved. Sampled workspace size is not a kernel quota.

## Qualification and legacy diagnostic (unchanged)

1. **Preflight:** capture current host state and test the unchanged resource guards. Any failure means no benchmark launch. Record all failed metric/value/threshold pairs; do not relabel an empirical guard as universal safety.
2. **Ambient baseline:** capture the required 60-second telemetry. Missing/malformed/short coverage is `MEASUREMENT_INVALID`, not stability drift. Absolute resource and baseline-relative memory/swap pressure failures block both modes.
3. **Stability quality:** qualification stops without arms when the accepted stability envelope fails. Diagnostic may continue after a valid stability-only failure, and only with every hard operational/containment/pressure and integrity check passing. Its output remains exploratory.
4. **Inter-arm recovery:** run no arms concurrently. Arm two starts only after three consecutive valid 10-second recovery windows meeting the current mode's recovery requirements and all operational resource/pressure guards. A failed window resets the consecutive count. Timeout or missing telemetry blocks arm two; preserve arm one's receipt.
5. **During an arm:** runtime violations abort the owned unit, retain the receipt, and clean only its cgroup descendants. Recheck the host and exact resource root before any later arm.
6. **After an operation:** compare only matched binaries/source/native libraries, query IDs, mutation trace, build flags, cache/maintenance conditions and fresh roots. Record physical-write measurements separately from logical bytes and process-tree I/O. One exploratory pair cannot establish a durable performance decision.

Calibration receipts and the canonical handoff preserve dated examples of policy refusals and measurement-quality rejections. These distinguish operation-level blocking from mission status; consult the receipt and handoff for the exact observation at the time rather than copying an old host snapshot here.

## Contention-aware diagnostic (explicitly selected, non-qualifying)

The owner mission **M4 Writer-tail Under Real Host Contention** authorizes this research-only implementation and synthetic tests, **not a real pair**. It supersedes the design of `io-psi-diagnostic-amendment.proposed.md`; that narrower draft was never activated. All earlier no-launch receipts remain valid under their then-active policy. The qualification and legacy diagnostic rules above remain unchanged. Statements above about mandatory historical envelope/first-three-window pressure checks refer to those two modes; the following is the only scoped alternative.

`run_host_carryover_aa.py --mode contention-aware --contention-plan <approved-operation-plan.json>` selects the new path. There is no environment-only override, global ignore switch, or inherited exception in other harnesses. The plan must bind the exact policy SHA-256, named operation/root, Btrfs resource root, pinned full-sync binary, tracing-off same-binary workload, explicit owner execution and I/O-risk acceptance references, and justified finite M4 resource budgets. No real approved plan is shipped. `contention-plan.pending.json` is deliberately incomplete and rejected.

### What changes and what does not

- **All four historical comparisons remain raw PASS/FAIL**, with original values, observed metrics, timestamps and provenance. For this mode only they describe host contention, not universal safety or mandatory idle-host readiness. A distinct `continuation_allowed` decision describes actual approved workload capacity; a raw FAIL is never rewritten to PASS.
- The ambient capture observes the real host. First-three-window pressure/stability bounds are retained as historical descriptors, not enforced against later natural contention. No request to stop, pause, throttle or kill another project's workload; no idle polling or automatic retry.
- Host capacity is checked against the **explicit resource plan**, not an invented higher load/PSI limit: pre-arm RAM must cover M4's memory cap plus the approved host reserve; runtime preserves the reserve. SwapFree must cover the remaining approved M4 swap allocation (no extra requirement when its swap cap is zero). Disk must cover the remaining owned-workspace allocation and reserve, including retention of the first arm's root. Other workloads may consume capacity; a shortfall stops M4 without attributing the cause to M4 or changing those workloads.
- Finite `memory.max`, `memory.swap.max` (zero allowed), `cpu.max` and `pids.max` are mandatory on M4's owned service. `TasksMax`/`pids.max` bound tasks including native worker threads, not only the process IDs listed by `cgroup.procs`. Read-back is checked before release and by the independent watchdog. Workspace ceilings are **sampled abort controls**, not kernel/filesystem quotas. No delegated I/O controller exists; neither `io.max` nor hard I/O isolation is claimed.
- Preserve source/build/dependency/runtime-library identity and completed correctness/durability/fault evidence. Static full-sync verification runs before the pair and each arm; the old sync-reduction binary cannot qualify for this mode. No C++ or production changes are part of this policy.

### Monitoring and abort contract

Before/during/after each arm retain host PSI (CPU/memory/I/O), MemAvailable, swap and reclaim/OOM counters, shared filesystem/diskstats, owned cgroup CPU/memory/swap/PSI/task counts and task-limit events, process-tree PSS/RSS/swap/I/O, workspace measurements and lifecycle receipts. Native stages remain in the existing stdout timeline; no heavy tracing is enabled. Compare ambient/during host pressure and owned CPU/memory separately. `contention-analysis.json` reports temporal pressure differences and explicitly cannot identify a causal incremental M4 effect from a concurrent uncontrolled host. Process-tree I/O snapshots can lose exited workers and are not device write attribution.

Required telemetry must be present, finite, in range, internally consistent and monotonic. This mode uses the plan's explicitly bounded maximum observation gap and actual read brackets rather than requiring nominal idle-host sample cadence. Missing/overlong coverage blocks; reduced but valid resolution is disclosed. Qualification/legacy cadence and integrity checks are untouched. Capacity failure, observed host OOM, owned OOM/allocation/swap/task-limit distress, cap mismatch, malformed/incomplete process telemetry, controller loss, timeout, correctness/durability or cleanup failure still block/abort. PSI alone is not grounds to attribute the failure to M4.

The independent watchdog validates operation/mode/budget/context binding and acknowledges a canonical context digest **before the supervisor releases M4**. Its final summary must echo that digest. It samples the same contention policy, not legacy historical guards. Timeout and verified owned-unit retirement remain in place. Cleanup touches only the owned cgroup descendants. Three valid capacity/integrity recovery windows are required; a capacity/integrity failure stops the pair immediately instead of waiting for an idle host. The recovery timeout starts after arm one in this mode only. A no-launch/abort/completion consumes the operation directory; duplicate attempts cannot overwrite it or reuse its arm receipts.

Every result, even with no historical exceedance, is **`EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING`**, never qualification/performance-eligible. No candidate win, proved regression or production readiness follows. Qualification/default diagnostic and unrelated harnesses keep their old behavior.

### Remaining limits and launch decision

Cgroup memory/CPU caps constrain only the owned service, not the external observer. Host reserves must account for observer/kernel needs and other active work; approval is an operational risk decision, not a universal safety guarantee. I/O interference, shared Btrfs allocation races, page-cache effects and sampling gaps remain possible. A sampled workspace guard can overshoot between reads; no kernel disk quota or per-arm I/O hard cap exists. Kernel OOM/cgroup events and reserves cannot predict every global failure. The observer itself can add scheduling and I/O pressure.

Real launch therefore still requires a separately approved, evidence-based resource plan and explicit acceptance of these limits, followed by one bounded operation. Do not weaken a real capacity/integrity refusal or ask the owner to quiet unrelated workloads. Current engineering verification is not a resource-readiness measurement or real workload safety certificate.

## Operation and mission status contract

A runner reports the **operation**, not the whole mission. Current runner receipts use operation-specific values such as `PASS`, `PREFLIGHT_BLOCKED`, `WATCHDOG_ABORT`, `TELEMETRY_INCOMPLETE`, and `OPERATION_COMPLETE`, carry `mission_status=NOT_EVALUATED_BY_RUNNER`, and never infer mission completion. The mission-level vocabulary in `benchmark-policy.json` (`SUCCESS`, `PARTIAL`, `OPERATION_BLOCKED`, `OWNER_DECISION_REQUIRED`, `SAFETY_STOP`, `EXHAUSTED`) is for the active agent/user review, not a runner's self-assigned mission status. Reconcile active user authorization and every remaining DoD item at resume; historical handoff text is continuity evidence, not authority.

## Verification

`python3 research/m4-native/tools/test_m4_benchmark_policy.py` tests policy loading, unchanged thresholds, gate metadata, fixture-override restrictions, and classification. `python3 research/m4-native/tools/test_m4_harness_scenarios.py` statically checks scenarios A–J and the authority/continuation contract in root and scoped instructions; it is a bounded contract rehearsal, not an LLM evaluation. `test_aa_gate_policy.py` exercises baseline decision branches; containment integration/fault tests exercise process cleanup and no-launch receipts. These checks validate software/contracts only and do not prove that an LLM will follow instructions.
