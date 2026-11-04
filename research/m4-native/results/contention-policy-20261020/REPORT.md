# M4 writer-tail under real contention — engineering closure, 2026-10-20

## Status and authorization

**Engineering implementation: implemented and verified across all scoped enforcement layers. Real A/A: NOT AUTHORIZED / NOT RUN. Writer-tail cause: INCONCLUSIVE.** No performance readiness capture, ambient measurement, idle polling, M4 performance executable, production/native/daemon/index change, or unrelated-project process intervention was performed. Tiny synthetic systemd fixtures and offline/static checks are not performance experiments. No request was made to stop other work.

The active owner mission supersedes the earlier one-I/O-guard exception proposal as a design direction. The proposed document remains historical/inactive; earlier no-launch decisions remain valid. Qualification and legacy diagnostic stay on the original policy. All pre-existing JSON fields compare identically to `475ea4c`; only `contention_aware` was added.

## 1. Policy and enforcement

All four historical limits (MemAvailable, filesystem free bytes, host I/O PSI full avg10, load-1) have empirical successful-run provenance, not independently validated universal safety provenance. The new explicitly selected `contention-aware` mode retains each original observed value/threshold/PASS-or-FAIL with timestamps and authority, and adds a separate approved-workload-capacity continuation decision. It never converts a raw historical FAIL into PASS. Qualification and default diagnostic still enforce their historical limits and original stability/pressure/cadence semantics.

The new mode requires an explicit operation-bound approved resource plan, current policy digest, verified pinned full-sync binary, tracing-off fixed 500×20 workload, owner execution and host-I/O risk acceptance references, and justified finite resource budgets. The only shipped template is `docs/contention-plan.pending.json`: incomplete, unapproved and rejected. No default real budget or approval was invented.

| Layer | New mode behavior |
| --- | --- |
| Outer readiness | One capacity/integrity check, raw historical failures retained. Real root must be Btrfs. No idle retry. Unique operation directory consumes the attempt. |
| Ambient baseline | Observe the real host, including CPU/memory/I/O PSI, swap/reclaim/OOM counters and diskstats. Validate actual brackets, monotonic counters, complete required fields and the plan's maximum monitoring gap. Natural pressure/stability drift is descriptive, not a reason to wait for idle. |
| Contained-arm preflight | Same capacity policy, full-sync source/build/runtime/durability verification, exact operation/command binding, fresh owned root only. Missing capacity or telemetry blocks before M4 starts. |
| Kernel controls / runtime | Approved finite M4 `memory.max`, `memory.swap.max`, `cpu.max` applied and read back. Owned-workspace limits remain sampled-abort controls. Retain before/during/after host and owned-process/cgroup data. |
| Independent watchdog | Same explicit context/budget, no inherited legacy-envelope abort. Validates and acknowledges canonical context digest before M4 release; final receipt must echo it. Continues pidfd/controller-loss, timeout, OOM, capacity, telemetry and owned cleanup checks. |
| Recovery | Three consecutive valid 10-second capacity/integrity observations; historical/first-three-window pressure bounds remain raw descriptors. Capacity/integrity failure stops immediately rather than polling for an idle host. Recovery timeout excludes arm-one runtime in this mode only. |
| Classification | Every new-mode operation is `EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING`, even without an exceedance. Performance eligibility is false. Old analyzers that could imply qualification are not used for this mode. |

Capacity criteria derive from the explicit workload plan, not a replacement PSI/load safety threshold: before an arm RAM covers the memory cap plus approved host reserve; runtime preserves the reserve. SwapFree covers the remaining permitted M4 swap allocation (no additional swap reservation when its cap is zero). Filesystem free space covers unconsumed owned-workspace allocation plus reserve, including retained first-arm roots. These are risk-budget/reservation decisions with an explicit evidence basis, not universal safety certifications. Failure is not attributed to M4 merely because it happened while M4 was active.

## 2. Actual blockers and abort behavior

Still hard: correctness/durability/source/runtime mismatch, missing/invalid/nonfinite/out-of-range or inconsistent required telemetry, unapproved/mismatched context, memory/swap/disk capacity shortfall, observed host OOM kill, owned OOM or memory/swap allocation-limit distress, cap mismatch, incomplete process attribution telemetry, overlong monitor gap, controller loss, timeout, workspace ceiling violation and unverified cleanup. The mode cannot activate a global host-guard disable or the synthetic fixture override. Neither other harnesses nor environment variables inherit it.

Only the owned cgroup descendants/unit are terminated. Final unit retirement remains verified; no second arm follows first-arm failure, failed recovery, identity/integrity failure or cleanup failure. Existing indexes cannot be selected as the contention workload root. Duplicate attempts cannot overwrite prior operation receipts. No automatic retries are implemented.

### Completion-protocol finding and repair

An intermediate full regression run failed the supervisor-crash fixture: the initial `watchdog-abort.json` existed while socket cleanup was still pending. Subsequent inspection established that the exact owned cgroup and socket were absent, and only that fixture unit (`m4-contained-642256e8ba9a4dd5.service`) was verified/retired. The failed log and receipt remain under `contained-fixtures-final/` and `test-receipt-final.json`; they were not relabeled PASS.

Repair: distinguish abort initiation (`cleanup_complete=false`) from terminal publication after socket cleanup (`cleanup_complete=true`, explicit socket verification). The fixture now waits, within its unchanged deadline, for terminal publication before asserting socket absence and independently verified cgroup/unit retirement. It does not weaken cleanup assertions, extend a deadline, sleep until a presumed success, or rerun a failed benchmark guard. A post-repair suite passed, then the final frozen-source release suite passed with the added capacity/data-protection tests.

Early development also found two fixture defects, not policy waivers: reading cleanup status from a pre-final summary rather than the final abort receipt, and omitting a required epoch timestamp in synthetic recovery rows. Fixtures were corrected without relaxing integrity or cleanup requirements.

## 3. Verification and evidence

Final receipt: `test-receipt-release.json`, job **`job-1335348-183`**, source hashes checked unchanged before/after verification.

- **22** new contention policy/watchdog/integration tests.
- **5** shared policy tests; **4** harness-scenario contract tests.
- **12** tiny real-systemd containment/lifecycle tests (signals, timeout, controller crash, retirement included).
- **7** legacy A/A gate tests; **1** tiny real-systemd sequential-pair integration test.
- **51 total PASS**. No M4 500×20 executable was launched; pair tests use a Python fixture or mock arm callback.
- Fresh bounded `ty check` on six changed Python files: **PASS**, `typecheck.log` / `typecheck-exit.txt`. Active LSP probes had no remaining primary type errors, but silent-on-clean coverage was partially inconclusive and 30 auxiliary unchecked-throwing-call heuristic findings remain; these fail-closed filesystem/parse calls are not hidden or presented as a fully clean static scan.
- Scoped `git diff --check` and final exact staging checks are retained separately.

Earlier receipts/logs are retained with their version names, including the intermediate failed suite. `contained-fixtures-release/` is the final lifecycle evidence, not a replacement for the failure record. Synthetic OOM event tests do not claim a new real OOM or device/power-failure experiment.

## 4. Full-sync binary provenance

`full-sync-provenance-verification.json` records:

- Binary SHA-256 **`ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f`**, retained at `../durability-restore-20261020/m4_bridge_integration.full-sync`.
- Final compiled bridge source is byte-identical to correction commit **`7c882729acca6bb5c0a6a22651512c056f059db6`**; all **61** current compiler inputs and **six actual resolved runtime libraries** match the build receipt.
- GCC 16.2.1; `-DM4_USE_ZVEC_NATIVE_TOKENIZER=1`; `-O3 -DNDEBUG -std=gnu++17 -Wall -Wextra -Werror -pthread`; retained CMake/link/configuration evidence.
- All **50** retained durability-manifest entries match. Existing focused correctness evidence and the corrected five-case fsync/EIO/SIGKILL matrix with four recovery oracles apply to this exact binary. These are process-crash/error checks, not physical power-loss guarantees. The old `57f3a19e…ba342f` sync-reduction binary is not accepted by the new mode.

**Chronology clarification:** the retained rebuild receipt predates publication of commit `7c88272` at 12:01:53 +07, but its final compiled source bytes exactly match that commit and all current dependency hashes. There is no evidence of a rebuild after that commit timestamp, and none is claimed. This mission changed no C++ compiler input and did not perform a redundant rebuild just to create a newer receipt. Content identity, not an assumed build date, establishes which durability behavior this binary contains.

## 5. Offline writer-tail continuation

`offline-writer-tail.json` pins the source CSV hash and analyzes the existing **80 historical filter-identity A/B cycles** only. They are not newly measured full-sync results or certified durability-equivalent comparisons. Each of the four arms contains 15 non-compacting cycles:

| Historical order/arm | Non-compacting commit median / max (ms) |
| --- | ---: |
| Candidate-first / baseline | 202.437 / 270.468 |
| Candidate-first / candidate | 230.494 / 593.906 |
| Baseline-first / baseline | 202.778 / 239.984 |
| Baseline-first / candidate | 199.055 / 477.128 |

The candidate-first slowest native-vector cycle has **307.111 ms elapsed**, **50.569 ms caller-thread CPU**, **593.906 ms commit**, **137.755 ms writer-thread CPU**, no compaction, and **88.847 ms post-M4-to-build-end residual**. Thus a long writer tail does not require compaction in that trace. It does not prove compaction/previous writeback irrelevant, nor that all wall-minus-caller-CPU is I/O wait: native vector workers can consume CPU on other threads.

Post-M4 residual maxima in the four arms are 42.484 / 88.847 / 54.369 / 111.209 ms. Older residual telemetry does not isolate every SyncTree/finalization substage or fsync wait. The pinned full-sync binary already emits native-vector open/conversion/upsert/flush, post-M4 SyncTree, snapshot-finalization wall/caller-CPU arrays and stage timestamps. Their offsets map through `whole_start_monotonic_ns` to observer monotonic timestamps; new-mode observer read brackets disclose alignment uncertainty. No heavy tracing was added.

Still missing for causal discrimination: stage-specific native worker CPU, scheduler runqueue/off-CPU delay versus blocking I/O, syscall/fsync wait attribution, reliable lifetime I/O for exited processes without delegated cgroup I/O accounting, and a matched counterfactual for uncontrolled host work. The older CSV explicitly marks per-cycle host pressure and worker/fsync CPU as unmeasured. During-minus-baseline PSI/swap in a future authorized run is temporal association, not identified incremental causality. No speculative performance fix was made.

## 6. Remaining operational risks and next action

No delegated cgroup I/O controller: **no hard per-arm I/O isolation/cap**, no guarantee of zero host impact and no device-write attribution from host PSI or process I/O alone. Workspace/disk checks are sampled and can overshoot during concurrent Btrfs allocation; there is no filesystem quota. Page cache, writeback and other projects can change shared headroom. External watchdog/observer resources are outside the workload cgroup caps; its memory/sampling/serialization cost must be covered in the approved reserve/runtime plan. Short-lived process races may invalidate required process snapshots rather than being silently retried. Caps may themselves change scheduling/latency and must be reported as part of the experiment, not an architecture advantage.

**Next executable action is an owner decision**, not an idle-readiness loop: approve concrete evidence-based M4 caps, host/swap/disk capacity and observer-gap/runtime budgets, a unique operation and current policy identity, and explicit host-I/O risk acceptance plus one bounded pair's execution authorization. Only then may the existing A/A runner use the approved contention plan, revalidate provenance/capacity once and execute. Any capacity/integrity refusal is retained and stops that operation; it does not authorize changing another project's workload or retrying. Until then the pending plan remains unapproved and no real A/A runs.

Production, native libraries, daemon, ABI, schema, existing indexes and unrelated Rust/research changes were left untouched. Engineering closure is distinct from performance/production readiness and from authorization to measure.
