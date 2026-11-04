# M4 I/O PSI classification audit and proposed diagnostic exception

**Status: PROPOSED / OWNER_DECISION_REQUIRED — NOT ACTIVE.**

Prepared 2026-10-20 against commit `475ea4c`. This document is not loaded by any runner. It neither changes `benchmark-policy.json` nor authorizes a benchmark, readiness capture, retry, or guard override. Existing refusals remain valid. Owner approval must precede implementation; a benchmark additionally needs explicit execution authorization and passing applicable prerequisites.

## 1. Audit: what the current policy actually says

The executable ID is **`M4-RG-IOPSI`**, not `M4-RG-IO-PSI`.

| Axis | Finding |
| --- | --- |
| Active classification | **Operational resource guard**, specifically `HISTORICAL_ENVELOPE_RESOURCE_GUARD` in the A/A baseline decision. Not `M4-MQ-STABILITY`, and not a validated universal host-safety limit. |
| Metric and threshold | Host-wide I/O PSI **full avg10 <= 4.46 percent**; `resource_guards.io_full_avg10_max` in `benchmark-policy.json`. Not process I/O, disk utilization, or an arm-specific I/O cap. |
| Threshold provenance | Policy attributes it to ten successful historical B3.1 arm-before receipts. This audit verifies that declared provenance and its executable mapping; it does not independently establish safety or estimate rejection rates from those historical runs. |
| Active consequence | Hard no-launch / runtime stop for the affected operation in **both qualification and diagnostic modes**. An empirical origin does not currently make it advisory. |
| Safety distinction | Separate containment/OOM/timeout/cleanup and telemetry guards remain mandatory. Historical-envelope failure alone is `OPERATION_BLOCKED`, not proof of host danger or grounds for whole-mission `SAFETY_STOP`. |
| Ambiguous legacy wording | `hard_safety_failures` is explicitly a compatibility alias for `resource_guard_failures`; `hard_safety_window_pass` also retains historical naming. Neither overrides the current JSON classification. |

### Source and enforcement map

Paths below are relative to `research/m4-native/` except the canonical handoff.

- `docs/benchmark-policy.json`: `authority.historical_envelope_source`, `resource_guard_metadata.M4-RG-IOPSI`, `measurement_quality`, `containment`, and `continuation_rules`.
- `docs/benchmark-policy.md`: gate-class table, enforcement map, and diagnostic/qualification/recovery rules. It explicitly separates operational resource guards from containment safety and measurement quality.
- `tools/m4_containment_runner.py:200–251`: `resource_guard_decisions` and `hard_gate_failures` use the JSON threshold and metadata; no diagnostic-mode exception.
- `tools/m4_containment_runner.py`: `run_arm` preflight and independent watchdog (host checks around line 676) enforce the same guard, independently of the outer A/A decision.
- `results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/run_sync_reduction_pair.py`: `preflight` records per-guard failures; sampled runtime resource checks also enforce the JSON values.
- Same directory, `run_host_carryover_aa.py:204–300`: snapshot resource checks, samplewise ambient-baseline checks, and `diagnostic_window_safe` all include the absolute I/O PSI limit.
- Same file, `run_contained_pair`: inter-arm recovery requires three consecutive valid 10-second windows, the absolute guards, and baseline memory/swap pressure bounds; diagnostic mode omits qualification profile matching, **not** I/O PSI checks.
- Same file, `baseline_gate_decision:533–590`: integrity and pressure-evidence completeness first; absolute resource failures block both modes before stability-only diagnostic continuation can apply.
- `tools/test_m4_benchmark_policy.py` and same causal directory's `test_aa_gate_policy.py`: regression coverage explicitly expects I/O PSI failure to block diagnostic as well as qualification.

### Existing diagnostic continuation

Only a **valid stability-envelope failure** (`M4-MQ-STABILITY`) may be non-blocking in diagnostic mode. All absolute resource guards, baseline-derived memory/swap pressure, complete finite correctly bracketed telemetry, containment, correctness/durability, provenance and recovery prerequisites still apply. Results remain `EXPLORATORY / NOT QUALIFICATION EVIDENCE`; one exploratory pair cannot establish a stable improvement/regression or GO.

Therefore the existing continuation rule **does not authorize proceeding on an I/O PSI refusal**. Merely selecting `--mode diagnostic` or changing only the outer preflight would not suffice and would be an unauthorized partial bypass.

### Last recorded refusal, not a new readiness observation

Canonical handoff §51 records 2026-10-20 13:09:15 +07: I/O PSI full avg10 **4.68% > 4.46%**, excess **0.22 percentage points**, while memory/load/disk passed. No ambient baseline or arm launched. This receipt remains a valid refusal under the active policy; it is not retroactively reclassified or made benchmark evidence by this proposal. No current host-readiness inference follows from it.

## 2. Minimal amendment for owner approval

**Proposal ID: `M4-IOPSI-DIAG-ONEPAIR-v1`.** Approve a narrowly scoped diagnostic exception to the historical I/O PSI envelope, not a declaration that arbitrary I/O pressure is safe, and not a global reclassification of resource guards.

### Proposed normative policy delta (inactive)

1. **Qualification is unchanged.** Keep policy threshold `io_full_avg10_max = 4.46`, the `<=` comparison, historical provenance, hard preflight/baseline/runtime/recovery behavior, all stability/profile requirements, and every other gate. No qualification evidence may come from the exception.
2. **Default diagnostic behavior is also unchanged.** A diagnostic operation may use this exception only when its receipt names this approved amendment, records the owner's approval and explicit execution authorization, and identifies the single authorized operation. No general-purpose `ignore host guards` switch, environment-only bypass, automatic retry, or reuse of the synthetic test-fixture override.
3. **Scope is one attempt at one same-binary full-sync 500×20 diagnostic A/A**, tracing off, fresh Btrfs roots, unchanged approved telemetry and existing runtime/recovery timeouts. Verify the binary/source/build/runtime-library identity afresh; the previously pinned binary is `ef0221f0…1212e7f`. Approval does not authorize any candidate comparison, workload expansion, or extra pair. A no-launch, abort, or completion consumes the attempt; another attempt needs new owner authorization.
4. **Only a valid numeric I/O PSI envelope exceedance becomes non-blocking for that scoped diagnostic.** Apply this consistently at outer preflight, ambient-baseline samples, each contained-arm preflight, runtime sampler, independent watchdog, and inter-arm recovery. The raw guard comparison must still report `FAIL`, observed value, threshold, phase and timestamp. Record a separate `continuation_allowed` decision tied to the amendment; never turn a failed comparison into `PASS` or describe this as safety qualification. Existing stability-only diagnostic continuation remains available under its current conditions.
5. **All other blockers remain hard.** Missing, nonnumeric, nonfinite, out-of-range (outside 0–100 percent) or otherwise invalid required I/O telemetry is an integrity failure, not an eligible exceedance. Preserve every other resource threshold, baseline-derived memory/swap pressure bound, integrity/cadence/window check, containment lifecycle/OOM/timeout/cleanup, source/runtime identity, correctness and durability prerequisite. Mixed I/O-plus-other failures must block/abort. No second arm after failed recovery, identity, integrity or cleanup.
6. **Recovery remains three consecutive valid 10-second windows.** For this exception only, a finite I/O PSI exceedance must be recorded but must not alone reset recovery. All remaining resource/pressure/integrity checks and existing recovery timeout remain in force. Otherwise this amendment would only move the refusal from preflight to arm two.
7. **Evidence is irrevocably exploratory for the scoped operation**, even if every recorded sample happens to fall within the historical envelope. Emit `EXPLORATORY / NOT QUALIFICATION EVIDENCE`, amendment/approval identity, raw exceedance observations and phase/window counts, maximum observed I/O PSI, and unchanged stop reasons. Preserve per-cycle writer-tail/native-vector-build/post-M4-finalization and host-pressure telemetry, but make no isolated causal attribution or stable win/regression claim from one pressure-contaminated pair. Do not rerun automatically to improve the result.

### Implementation boundary after approval

No implementation is included in this draft. If approved, update `benchmark-policy.json` with an explicit operation-scoped approved exception and mode/phase semantics, update `benchmark-policy.md`, and thread the approved context into **every enforcement layer above**, including the independent watchdog. Do not globally remove I/O PSI from `resource_guard_metadata`, mutate the shared threshold dictionary, disable `enforce_host_gates`, or change qualification routing. Shared legacy harnesses remain on the existing policy unless explicitly wired and tested for this exact scoped exception; they cannot inherit an ambient bypass. Record implementation checksums, policy version/approval and evidence class in the operation receipt. Update RESULTS and canonical handoff before requesting launch.

### Required acceptance checks before any benchmark

Use synthetic unit/integration fixtures only:

- At exactly 4.46%, the comparison passes; at 4.68%, qualification and unapproved/default diagnostic still block at preflight/baseline and abort at runtime, and cannot satisfy recovery on that value.
- With the explicit approved scoped exception, valid 4.68% observations remain raw `FAIL` but permit exploratory continuation at **all** listed phases if and only if every remaining prerequisite passes.
- Mixed failures (I/O plus memory, load, disk, memory/swap pressure, integrity, identity or containment) remain blocked/aborted; no second arm after a failed required recovery or cleanup check.
- Missing/NaN/infinity/string/out-of-range I/O telemetry blocks both modes; it cannot be misinterpreted as an eligible high-pressure sample.
- Exception context survives independent-watchdog handoff; absent/mismatched/expired approval context cannot activate it. No leakage to qualification, another operation, a legacy harness or a concurrent arm.
- Qualification thresholds, stability decisions, recovery counts/timeouts and containment termination/retirement tests remain unchanged and green. Exception receipts cannot be promoted to qualification, even with no actual exceedance. No retry after a consumed attempt.

### Evidence limitations, risk and rollback

The historical envelope is not a validated universal safety boundary. Equally, this audit supplies **no independent safe upper I/O PSI limit**. The host has no delegated cgroup I/O controller or enabled per-arm I/O ceiling; existing CPU/memory/pids delegation is not per-arm I/O isolation. Proceeding outside this envelope may degrade host responsiveness, increase writer tails and confound attribution. The independent watchdog and bounded workload reduce some operational risks but cannot prove host I/O safety. Owner approval must explicitly accept that uncertainty; do not invent an alternative higher 'safe' threshold from the last refusal.

Rejecting or deferring the proposal leaves the current no-launch behavior intact. After implementation, removing/disabling the operation-specific exception restores the existing diagnostic behavior without changing the qualification threshold; any active arm must first be stopped through verified owned-unit cleanup. Retain the original receipt and all exception evidence without re-labeling it. No automatic fallback benchmark, threshold escalation, process termination outside the owned unit, or extra retry is authorized.

## 3. Owner decision and next action

**Requested decision:** approve or reject `M4-IOPSI-DIAG-ONEPAIR-v1`, including its explicit host-I/O risk acceptance, diagnostic-only scope, unchanged qualification and remaining hard gates.

Approval of this policy proposal alone authorizes neither an immediate benchmark nor a skipped implementation check. Next action after approval: implement the scoped exception, pass the synthetic acceptance checks, retain updated policy/runner provenance, then obtain explicit authorization for the one attempt. Until then: **no benchmark, baseline, readiness probe, or retry**.

Audit validation receipts are under `results/io-psi-policy-audit-20261020/`. They are synthetic policy checks, not measurements of the host or the M4 binary. Whole-mission performance status remains **INCONCLUSIVE**.
