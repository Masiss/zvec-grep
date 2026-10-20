# R1 replacement — telemetry admission refusal, no native GO

**PARTIAL / OPERATION_BLOCKED. Native measurement objective NOT fulfilled.** The authorized replacement was actually attempted, not replaced by synthetic tests. Its mandatory watchdog admission gate refused the first sample before native GO. No automatic retry or arm two. Raw **SAFETY_STOP remains SAFETY_STOP**; subsequent independent verification proves current cleanup complete, not that the failed measurement was valid.

## Accounting and authority

Owner amendment: `owner-replacement-authorization.md`. Historical Original Pair 1 remains consumed NO-LAUNCH/CONTROL_FAILURE with zero native arms. Snapshot `ledger-before-replacement.json` and final verification prove all three original slot objects unchanged. Replacement R1 is a separately charged additional slot, not original slot2. Maximum **four total attempts / at most three executable pairs**. Consumed **two** (original1 + R1); original two conditional slots unspent. No fifth slot or further expansion.

Replacement ID: **m4-contention-campaign-20261020-replacement-1**. Job **job-1335348-244**. Exact bounded command/plan digest/runner log/exit=1: `replacement-1-execution/`. Raw root: `../m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/m4-contention-campaign-20261020-replacement-1/`.

## Correction, provenance and caps

Execution HEAD **14f77dc**; exact execution controller SHA-256 **da8c14b07e3ac3f6ebb9ddf370204ae0e00d2fcc57177355b86ea1a36f6d0669**. Native measurement C++ SHA-256 **43d62bc410d22366cf96a78883d8838c94408fcb2482c899c911f095749ffa34**; full-sync executable **ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f**. Fresh static prerequisite verification matched 61 compiler inputs, six actual runtime libraries and 50 retained durability entries, with corrected full-sync/WAL fault/recovery evidence. No native source or measurement instrumentation change, no rebuild or stale-binary performance claim.

Before the real attempt, a fresh tiny kernel fixture verified `CPUQuota=200%`, `cpu.max=200000 100000`, `memory.max=1073741824`, `memory.swap.max=0`, `pids.max=128`, normal exit/retirement. This correctly verified CPUQuota correction/caps but did not yet assert the strict process-IO contract; that coverage gap is addressed below.

Approved resource vector unchanged: memory1GiB/swap0/quota2CPU/tasks128, sampled per-arm workspace512MiB allocated/1GiB apparent, host reserve1GiB/disk2GiB, monitor gap2s, watchdog115s/systemd120s, GNU supervisor TERM355s/KILL+5s. Fresh sequential Btrfs roots, tracing off, no other active owned arm. Resource/capability/approval context: `resource-plan.approved-replacement-1.json`, `replacement-1-execution/prerequisites.json`. Existing I/O/external-observer risk acceptance retained; no qualification criteria changed.

**Actual R1 service creation, budget application and controller kernel readback succeeded**, unlike Original Pair1. Receipt records exact CPU/memory/swap/pids values. Watchdog config was then written; admission readiness **did not** pass, so the native start marker/GO was never released. Correct cap readback does not substitute for valid telemetry.

## Mandatory admission failure and cleanup

Saved first cgroup sample: bootstrap PID397739 only, process memory complete=true, process IO complete=false; no native child. Watchdog reason **required_telemetry_missing**, detail **ValueError: incomplete process-tree telemetry**. No OOM/max/pids distress; bootstrap memory peak **16,023,552 B**, swap peak0, owned CPU counter55,717us, nr_throttled0. These are bootstrap-only observations, not native costs.

Raw watchdog: measurement INCOMPLETE, ABORTED_CONTAINED; `watchdog-abort.json` records cleanup_complete=true and successful cgroup.kill. Raw controller: LAUNCH_FAILED/CONTROLLER_ERROR, benchmark_started=false, controller_cleanup.verified=false; final unit_cleanup.verified=true. Outer raw operation status **SAFETY_STOP / CONTAINMENT_OR_CLEANUP_UNVERIFIED** is preserved verbatim. Do not silently replace the earlier unverified flag with later success.

Before any independent correction/test, exact-unit post-stop verification found `m4-contained-95bdf38a0e2140ff.service` not-found/inactive, cgroup absent, socket directory absent, native root absent and private workspace empty. This resolves current physical cleanup; it does **not** waive the telemetry failure or authorize resuming measurement. Evidence: `replacement-1-execution/post-stop-verification.json`. No unrelated process was signalled or changed. The reason for the earlier controller-versus-final-retirement verification timing difference is not established; no cleanup code was relaxed to hide it.

## New evidence / requested analysis

Reproducible file-only reader: `analyze_replacement.py`; input hashes/result: `replacement-1-analysis.json`.

- Valid ambient observation: **1200 samples / 59.951112561 s**. Approved capacity/integrity allowed exploratory continuation. Raw unchanged stability diagnostic failed load drift.
- Load1 **3.81–4.82**, CPU PSI some avg10 **0–0.37%**, IO PSI full avg10 **1.76–5.05%**, memory PSI full avg10 **0–0.55%**.
- MemAvailable **5,605,092–6,440,432 KiB**; disk free minimum **17,269,014,528 B**. Host OOM delta0; swap-in/out **12,099/7,647 pages**. Context only, not M4 attribution or universal safety.
- Historical raw FAIL counts: load **1200**, disk **1200**, IO PSI **487**, memory **0**. These remain FAIL/nonblocking as approved and **did not cause the admission refusal**.
- **Writer p50/p95/p99: unavailable, not zero (N=0).** No native build/upsert/flush/post-M4 SyncTree/finalization cycles, query/coherence/reopen outcomes, native CPU/PSS/RAM/IO/write/maintenance comparison or arm-order asymmetry. No sum of independent p95s; no candidate regression/win/qualification claim.
- H1: ambient variability observed, native order effect unmeasured. H2/H3: no native/vector/sync data. H4: host versus implementation unresolved. H5: deterministic process-IO key-format defect confirmed; not the historical writer-tail root cause. Observer overhead/device-write ownership not isolated.

## Independent minimal parser correction

Linux `/proc/PID/io` lines use `read_bytes:` / `write_bytes:`. `parse_kv` preserved colons, but `read_cgroup_metric` required uncolonized keys. Thus an otherwise readable sample was marked incomplete and recorded zero placeholders. The saved false completeness flag must remain false; those old process-IO zeros are invalid, not zero-write evidence. This diagnosis does not invalidate independently reported C++ physical-write counters from older non-containment experiments.

One production-independent research line now removes only a trailing colon from parsed keys. Normal cgroup keys (usage_usec, oom, max) are unchanged; file/read/permission/PID errors still mark samples incomplete. **No completeness predicate, guard, deadline or resource cap was relaxed.** Corrected controller hash **c4bc68dec018e863dc7525b320c8d19b7f20fdd516ba63751651c91b90569833**.

New regression checks both proc and cgroup formats. Existing real tiny full-cap test now also asserts first sampled IO/memory completeness and calls the unchanged strict `contention_runtime_failures(first, None)` contract, requiring no failures on actual kernel telemetry. No synthetic flags are patched to make R1 pass; raw R1 is untouched. Only after independent cleanup verification, test job **job-1335348-248** ran **58 focused tests PASS** (15 lifecycle/24 contention/5 policy/7 legacy/1 tiny pair/6 offline bounds), fresh three-file `ty check` PASS. Eight archived synthetic units retired. Logs/source hashes/first real-kernel sample: `proc-io-parser-tests/`. These are independent correctness fixtures, **not a native retry or a successful replacement pair**.

## Decision and recovery condition

**KEEP** integer CPU quota correction, the one-line IO parser correction, strict telemetry and full sync. **DEFER** writer-tail optimization: requested native measurement has not been obtained, no resource/performance tradeoff can be established.

Historical original slots and the separate R1 remain charged. Two original slots remain conditional/unspent, but after this formal admission failure there is no authorized automatic retry/new-ID substitute. Do not invent a discriminating performance result from zero native samples. Owner must explicitly decide whether another attempt after the integrity correction may use one existing unspent slot; **no additional/fifth slot or cap increase is requested or authorized**. Any future approved attempt needs fresh provenance/capacity, actual caps and complete watchdog readiness before native GO.

Current cleanup is independently verified; unsafe measurement dependents stayed stopped. Independent diagnosis/focused correction/reporting completed without production/native/ABI/schema/index changes, other-project intervention, old receipt reclassification, budget expansion or push. Ledger, RESULTS and canonical §56 distinguish the original raw safety stop from later cleanup verification and the still-unfulfilled measurement objective.
