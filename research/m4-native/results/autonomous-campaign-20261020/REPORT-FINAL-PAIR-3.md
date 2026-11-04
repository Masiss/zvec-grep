# Final original slot3 — actual native launch, workspace-scan integrity abort

**PARTIAL / OPERATION_BLOCKED / BUDGET_EXHAUSTED.** Native execution was actually reached: **one native arm launched, zero completed arms/pairs**, no arm2. The watchdog stopped arm1 on a workspace enumeration/stat race, not a RAM-capacity/CPUQuota/proc-IO failure. Complete writer-tail measurements remain unavailable. No guard was ignored, no fifth attempt or automatic retry.

## Authority and accounting

After the previous capacity refusal was explained, current owner explicitly said “chạy đi” / “trong prompt có nói rõ mà”. `owner-final-slot-authorization.md` binds this new execution direction to the **last existing original slot**, without resetting prior outcomes or changing caps. Question: obtain the first coherent500×20 native A/A data and separate native/post-M4/finalization tails plus arm-order variability after the demonstrated launch path.

ID **m4-contention-campaign-20261020-pair-3**, job **job-8740-9**, supervisor exit1. **4/4 total attempts consumed, zero remaining**. `ledger-before-final-slot.json` and final analysis prove Original1, R1 and original2 histories unchanged. Rehearsal still consumes zero native slots. It is not counted as the actual native arm reported here.

## Fresh checks and real native launch

No runner, guard, native/C++ source, ABI/schema/production-index or durability modification. Current controller **aa1103b48981d1dbe2a04233ca7ba2019c9719c61534a48243c9ca565c891543** still matches the actual strict rehearsal. Native measurement source **43d62bc410d22366cf96a78883d8838c94408fcb2482c899c911f095749ffa34**, full-sync ELF **ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f** unchanged. Fresh static61 compiler-input/six loaded-library/50 durability-evidence identity verification PASS; no rebuild needed for these unchanged compiler inputs, no stale-binary performance claim. These checks do not rerun or replace retained correctness/fault tests.

Approved resource vector unchanged: CPUQuota200%/cpu.max200000100000, memory1GiB/swap0/tasks128, sampled workspace512MiB allocated/1GiB apparent, host reserve1GiB/disk2GiB, monitor-gap2s, watchdog115s/systemd120s, GNU TERM355s/KILL+5s, tracing off, fresh Btrfs root, one arm at a time. Fresh capacity/integrity and no-unresolved-owned-unit checks PASS.

Ambient **1200 valid samples /59.950846022s**. Raw stability quality FAIL on MemAvailable drift retained/nonblocking. Load1 **4.52–5.73**, host CPU PSI some avg10 **0%**, IO full **1.29–3.19%**, memory full **0–2.17%**, MemAvailable **2,243,664–9,445,100KiB**. This is contention context, not host idleness or native attribution.

Native ELF child **PID139597** started at **2026-10-20T16:14:40.679230Z** inside real unit **m4-contained-580593efa0104d61.service**. `command-start.json`, binary provenance, raw command and actual cap readback prove native—not synthetic—launch. Bootstrap PID139593 was separate. **18 cgroup/runtime samples,17 active**, last active observation966.446534ms after GO. All process IO/memory samples remained complete.

## Formal failure, observed cause and cleanup

At **16:14:41.687678Z**, watchdog **workspace_telemetry_incomplete**:

`root/generation-1/fts/tombstones-2-1.bin.tmp.139597: [Errno 2] No such file or directory`

`workspace_usage` in `tools/m4_containment_runner.py:545` enumerates names via `os.walk`, then stats each path; any stat OSError makes scan_complete=false. Native `src/index.cc:783` AtomicWrite creates `.tmp.PID`, fsyncs and renames it to the final filename (or unlinks on error). The saved ENOENT is an enumeration/stat race consistent with that transient atomic-publication lifecycle. Exact syscall/removal actor was not traced; this is not proof of engine corruption or a native correctness/durability failure. The stable32KiB rehearsal did not exercise rename churn.

The failed scan's **3,153,920B allocated /6,197,220B apparent** are partial counts, **not a verified peak or cap proof**. Do not simply ignore ENOENT, mark it complete, rescan until green or relax completeness requirements. No scanner repair/native rerun was attempted within this exhausted budget.

Watchdog intentionally SIGTERM'd native PID139597; observed workload returncode **-15 /signal15**. Raw arm **WATCHDOG_ABORT / ABORTED_CONTAINED**; outer operation **OPERATION_BLOCKED / OPERATION_LEVEL_GATE_REFUSAL**. Raw failure_class UNKNOWN and measurement_quality COMPLETE remain verbatim: the latter reflects process/cgroup sampling availability, **not valid workspace telemetry or a completed native measurement**. Supplementary analysis does not turn either raw label into PASS.

Watchdog cleanup_complete=true, cgroup.kill success; controller unit retirement verified/collected. Independent exact-unit inspection: not-found/inactive, cgroup/socket absent, no remaining owned units. Arm2 never created. Private research workspace `/var/tmp/m4-writer-sync-pair-z9h8aj8w` retained with partial generation1, not deleted or reused. Later quiet workspace inventory is explicitly not a repair of the failed live scan. No unrelated process/project intervention.

## Partial resources, not writer-tail results

Aborted cgroup observed peak **219,193,344B**, swap0; final owned CPU counter **1,087,464us** including bootstrap/native/readers, not isolated writer CPU. No memory max/OOM, swap fail/max, task max or CPU throttling events. Max sampled aggregate PSS199,961KiB/RSS214,288KiB, process-tree physical writes12,025,856B. These are partial observations; exited-worker IO can disappear from aggregated proc samples, not a full-workload write/cost total. No complete reader/coherence/reopen verdict.

Native stdout is empty: **zero completed cycle payloads; writer p50/p95/p99 unavailable, not zero**. No trustworthy per-cycle native-vector/post-M4/SyncTree/finalization durations or A/A order comparison. No candidate regression, performance win, qualification claim, addition of independent p95s or wall-minus-caller-CPU-as-IO inference. H1–H4 still unresolved; H5 workspace-observer integration artifact observed, not the historical writer-tail root cause. Native affinity/per-process CPU and contemporaneous top-process attribution were not captured directly; operator affinity/top-process inspection afterward is labelled delayed and not a native before/after substitute.

## Source/tooling verification and next action

New one-off files `launch_final_pair_3.py` and `analyze_final_pair_3.py` record the exact single launch, receipts and reproducible file-only analysis; no new rehearsal framework/runner architecture. Guard policy unchanged. Existing59-test/rehearsal results bind unchanged controller source; they are not rerun benchmark evidence. New two-file typecheck and analysis replay recorded separately.

An editor-only clang diagnostic cascade arose from fallback compilation without the research include directory (`m4.h` missing). **No native source was changed**: `research/m4-native/.clangd` now selects stable `.work/build/lsp`, whose compile database is mirrored byte-for-byte from the already verified CMake build. No compiler/include flags overridden. The first standalone `clangd --check` exceeded its60s limit after AST construction while exhaustively testing token/refactoring features; its timeout/log are retained, **not reported PASS**. A bounded syntax-only check with the actual recorded CMake compiler/flags then **PASS**, and the active three-file LSP probe confirmed all3 paths clean after correcting compilation context. Evidence is archived. Neither check rebuilds an executable or reruns native work.

**KEEP** CPU/proc-IO corrections, strict native binding, resource limits and full sync; **DEFER** writer-tail optimization. Highest-value follow-up is a focused, bounded race-aware workspace accounting design/regression covering atomic rename/unlink with explicit uncertainty/completeness—not silently swallowing errors or consuming more native attempts. There is **no remaining native slot**. Any subsequent native campaign needs new owner authority; none is launched/requested as an automatic fifth attempt. Mission objective of completed native writer-tail measurements remains **NOT fulfilled**, despite successfully proving actual native launch.

Full raw operation root is the sibling writer-tail-causal directory named by this ID. `pair-3-execution/` contains fresh provenance, exact bounded command/PIDs, exit, host snapshots and cleanup proof. Reproducible analysis/input hashes: `analyze_final_pair_3.py`, `final-pair-3-analysis.json`; separate final-slot checksums. Canonical§58 and RESULTS updated, historical receipts unchanged, unrelated three Rust edits preserved, commits not pushed.
