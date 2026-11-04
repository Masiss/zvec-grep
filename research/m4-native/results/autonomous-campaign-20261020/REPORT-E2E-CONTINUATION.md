# End-to-end rehearsal and original slot2 continuation

**PARTIAL / OPERATION_BLOCKED.** The actual strict contention launch path passed the authorized tiny rehearsal. Continuation then reached a fresh **formal memory-capacity refusal before native invocation**. Native writer-tail measurements remain unavailable. We did not stop merely because synthetic tests passed; we stopped at the explicit resource gate. No native retry after refusal, including after the user-reported crash.

## 1. Authority, source and accounting

Current authority: `owner-rehearsal-continuation-authorization.md`; prior caps/risks/qualification/correctness/durability remain unchanged. Before this phase2/4 consumed; rehearsal consumes zero native slots. Original slot2 charged conservatively at plan preparation, so **3/4 attempts consumed, one original conditional slot unspent; native arms launched0, completed native pairs0**. `ledger-before-e2e-continuation.json` preserves prior state. Historical Original1 and R1 objects/receipts remain unchanged; no reset, fifth slot or disguised replacement.

Completed source commit **277245a** adds only a fixed tiny synthetic command admission to the existing contention path, associated rejection/IO-field regressions and one-off rehearsal driver. Native tracing-off500×20 command/binary/path/budget binding remains enforced. Rehearsal context must have a distinct purpose, rehearsal-prefixed operation ID, exact fixed workload and current Python hash; arbitrary commands and native commands under rehearsal context are rejected. Native context cannot run the synthetic command. No change to caps, policy JSON, production/native C++, ABI/schema/index or sync behavior.

Actual execution controller SHA-256 **aa1103b48981d1dbe2a04233ca7ba2019c9719c61534a48243c9ca565c891543**. Native source still **43d62bc410d22366cf96a78883d8838c94408fcb2482c899c911f095749ffa34**, full-sync ELF **ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f**. No compiled-source change or native rebuild/performance claim. Fresh continuation static verification matched61 compiler inputs, six runtime libraries,50 retained durability evidence entries; completed full-sync correctness/fault tests were checksum-verified, not rerun.

## 2. Rehearsal coverage — actual components, no mocks

Job **job-1335348-259**, operation **m4-contention-rehearsal-e2e-1**, `rehearse_launch_path.py`. Fixed Python workload writes/fsyncs/reads32KiB, holds1s and removes its own payload in a fresh0700 Btrfs `/var/tmp` root. No native500×20 invocation.

Verified end-to-end:

- Actual systemd transient unit, delegated cgroup, bootstrap and child startup/identity.
- CPUQuota200%, real `cpu.max=200000 100000`, memory1073741824B, swap0, tasks128; sampled workspace caps unchanged. Watchdog115s/systemd120s as approved; separate tiny rehearsal supervisor TERM30s/KILL+5s, no increased lifetime.
- Actual `/proc/PID/io` parser for `rchar`, `wchar`, `read_bytes`, `write_bytes`, `cancelled_write_bytes`; exact worker dump: **1326832,32768,0,49152,0** respectively. Physical read0 is valid with complete fields, unlike R1's incomplete placeholders. Character counts are not physical IO.
- **Independent watchdog in real contention-aware mode**, same resource/schema validation as native mode, exact context-digest acknowledgement before GO; no fixture-mode host-guard override.
- **12 real runtime samples**, all passed unchanged strict `contention_runtime_failures`; process IO/memory complete, no abort. Receipt published, expected worker exit0, verified unit collection/cgroup/socket cleanup and empty workspace. Generic receipt `benchmark_started=true` means this **fixed synthetic Python command**, not a native arm.
- Cgroup memory peak **22,282,240 B**, swap0, sampled allocated workspace peak **32,768 B**. These are synthetic correctness observations, not native costs or benchmark evidence.

Raw config/runtime/receipt/start/exit/stdout/stderr/unit state and immediate host-before/after: `m4-contention-rehearsal-e2e-1/`; bounded exact invocation/exit0: `e2e-rehearsal-execution/`. Post-crash independent verification again found unit `m4-contained-579ca03808454887.service` not-found/inactive, cgroup/socket absent, no active owned units/controllers/native processes.

Preparation verification: **59 focused tests PASS** (15 lifecycle,25 contention,5 policy,7 legacy,1 tiny pair,6 bounds). Initial four-file typecheck found two Path-typing errors; corrected only annotations, reran25 contention tests and fresh four-file `ty` PASS. Original failed typecheck preserved, no guard failure retried. Tested source hashes match unchanged current source after crash. Evidence: `e2e-admission-checks/`.

## 3. Continuation and the precise refusal

Original slot2 ID **m4-contention-campaign-20261020-pair-2**, approved context `resource-plan.approved-pair-2.json`, same policy/caps/workload as approved R1 except operation/authority references. Intended supervisor GNU TERM355s/KILL+5s, no auto-retry.

A preparatory wrapper job261 initially lacked the sibling import path. It failed **before any host snapshot, policy evaluation, native operation directory or command invocation**. Raw `ModuleNotFoundError` retained. Fixed only that wrapper import setup in the one-off `launch_original_pair_2.py`; it continued the same already-debited slot to its **first** resource check, not a retry of a refused native operation or a new slot.

Job **job-1335348-262** passed static provenance, context validation, rehearsal prerequisite and no-active-unit checks. Fresh decision at **2026-10-20T15:44:07.135544Z**:

| Formal guard | Observed | Required | Shortfall |
|---|---:|---:|---:|
| **M4-CA-MEM-CAPACITY** | **2,020,233,216 B** (1,972,884KiB) | **2,147,483,648 B** (2,097,152KiB) | **127,250,432 B** (124,268KiB) |

Required capacity is approved1GiB M4 budget +1GiB host reserve, not the older historical5.48GiB threshold. `continuation_allowed=false`, cause of host capacity not established. Historical memory/disk FAIL remain nonblocking observations; IO PSI full avg10 .16%/load1 1.8 passed their historical comparisons. Disk17,017,061,376B and SwapFree15,045,128KiB did not trigger another current blocking failure. This is **not a parser, CPUQuota, qualification-drift or native failure**.

Saved raw decision/provenance/host/capability: `pair-2-execution/after-import-correction/prerequisites.json`. The guard stopped before invoking the native orchestrator: no60s baseline, no native command launch record, no native operation directory/unit/arm. Do not create a fake native runner receipt; supervisory reconstruction is separately labelled under `pair-2-execution/post-crash-reconciliation/`.

## 4. Crash reconciliation and limitations

After user reported a crash, background registry/log and original job exit status were unavailable. We do **not** invent an exit code or claim the host-crash cause. Saved formal refusal is authoritative; current no-process/no-unit/path evidence and source control flow confirm no native invocation. Delayed host snapshot is explicitly post-crash closure, not an immediate after-sample, not a new preflight and not grounds to replace the failed decision with PASS. No operation was relaunched, no other project was killed/reconfigured.

The tiny rehearsal does not test native initialization/vector building,20-cycle pressure, reader/reopen correctness or native durability/performance. Per-process IO aggregation can lose exited workers; character/cancelled fields were checked in parser/worker dump, not introduced as aggregate performance counters. No hard IO cap or hard external-observer CPU/RAM limit; sampled workspace limitations and accepted shared-Btrfs risks remain unchanged. No claim all possible telemetry/lifecycle failures are eliminated.

## 5. Writer-tail evidence and next action

**Native writer samples N=0; p50/p95/p99 unavailable, not0.** Native-stage/post-M4/finalization durations, host-to-stage attribution, native CPU/PSS/IO/writes/maintenance costs, reader correctness/coherence/reopen and A/A order variability all remain unmeasured. H1–H4 unresolved. H5's known parser artifact corrected; actual control path now demonstrated with strict synthetic context, not a historical writer-tail diagnosis. No candidate regression, win, qualification or added-independent-p95 claim.

**KEEP** CPU/parser corrections, fixed tiny end-to-end admission/rejection tests, strict telemetry, approved caps and full sync. **DEFER** latency optimization. Goal of obtaining native measurements remains **NOT fulfilled**; a green rehearsal is not mission completion.

Highest-value next action is an owner decision on a further native measurement **after capacity recovery**, within the existing one conditional slot if explicitly permitted. Corrected parser itself no longer needs separate permission; the *new formal capacity refusal* does prohibit automatic retry/new-ID substitution. No discriminating native question can be inferred from absent paired data, so final slot was not spent. Do not increase caps/budget, poll/retry until green, request other workloads stop or use post-crash resources as retroactive approval. Current authorized independent reporting/evidence work is completed and committed; no push. Canonical handoff§57 and RESULTS reflect this partial outcome.

Reproducible file-only analysis/input hashes: `analyze_e2e_continuation.py`, `e2e-continuation-analysis.json`; archive checksum manifest adjacent. Unrelated three Rust edits preserved.
