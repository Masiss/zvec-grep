# M4 benchmark policy and executable contract

**Scope:** standalone research prototype under `research/m4-native/`. This policy does not authorize production integration, migration, deployment, native checkout changes, or any change to the production ABI/schema. The machine-readable values are in adjacent `benchmark-policy.json`; runner code loads that file, and tests verify the enforcement mappings.

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

## Diagnostic, qualification, and recovery

1. **Preflight:** capture current host state and test the unchanged resource guards. Any failure means no benchmark launch. Record all failed metric/value/threshold pairs; do not relabel an empirical guard as universal safety.
2. **Ambient baseline:** capture the required 60-second telemetry. Missing/malformed/short coverage is `MEASUREMENT_INVALID`, not stability drift. Absolute resource and baseline-relative memory/swap pressure failures block both modes.
3. **Stability quality:** qualification stops without arms when the accepted stability envelope fails. Diagnostic may continue after a valid stability-only failure, and only with every hard operational/containment/pressure and integrity check passing. Its output remains exploratory.
4. **Inter-arm recovery:** run no arms concurrently. Arm two starts only after three consecutive valid 10-second recovery windows meeting the current mode's recovery requirements and all operational resource/pressure guards. A failed window resets the consecutive count. Timeout or missing telemetry blocks arm two; preserve arm one's receipt.
5. **During an arm:** runtime violations abort the owned unit, retain the receipt, and clean only its cgroup descendants. Recheck the host and exact resource root before any later arm.
6. **After an operation:** compare only matched binaries/source/native libraries, query IDs, mutation trace, build flags, cache/maintenance conditions and fresh roots. Record physical-write measurements separately from logical bytes and process-tree I/O. One exploratory pair cannot establish a durable performance decision.

Calibration receipts and the canonical handoff preserve dated examples of policy refusals and measurement-quality rejections. These distinguish operation-level blocking from mission status; consult the receipt and handoff for the exact observation at the time rather than copying an old host snapshot here.

## Operation and mission status contract

A runner reports the **operation**, not the whole mission. Current runner receipts use operation-specific values such as `PASS`, `PREFLIGHT_BLOCKED`, `WATCHDOG_ABORT`, `TELEMETRY_INCOMPLETE`, and `OPERATION_COMPLETE`, carry `mission_status=NOT_EVALUATED_BY_RUNNER`, and never infer mission completion. The mission-level vocabulary in `benchmark-policy.json` (`SUCCESS`, `PARTIAL`, `OPERATION_BLOCKED`, `OWNER_DECISION_REQUIRED`, `SAFETY_STOP`, `EXHAUSTED`) is for the active agent/user review, not a runner's self-assigned mission status. Reconcile active user authorization and every remaining DoD item at resume; historical handoff text is continuity evidence, not authority.

## Verification

`python3 research/m4-native/tools/test_m4_benchmark_policy.py` tests policy loading, unchanged thresholds, gate metadata, fixture-override restrictions, and classification. `python3 research/m4-native/tools/test_m4_harness_scenarios.py` statically checks scenarios A–J and the authority/continuation contract in root and scoped instructions; it is a bounded contract rehearsal, not an LLM evaluation. `test_aa_gate_policy.py` exercises baseline decision branches; containment integration/fault tests exercise process cleanup and no-launch receipts. These checks validate software/contracts only and do not prove that an LLM will follow instructions.
