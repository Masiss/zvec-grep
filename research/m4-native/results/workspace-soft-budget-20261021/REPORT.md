# M4 exploratory workspace soft-budget amendment — 2026-10-21

## Final outcome

**PARTIAL / OPERATION_BLOCKED / NEW_ATTEMPT_EXHAUSTED.** Amendment committed
as **`fdef863`** before execution. Fresh readiness/provenance PASS; native arm
launched once, aborted on workspace accounting coverage, zero completed arms or
cycle payloads. **No writer-tail p50/p95/p99/stage/coherence result is available.**
New authority **1/1 consumed, zero remaining**; old campaign4/4 remains unchanged.
Cleanup verified, no native retry. A direct pending-event classification defect
was corrected prospectively and synthetic-tested only, not used to reclassify or
repeat the aborted operation. No additional repair branch/infrastructure started.

## Engineering verification (before the new native attempt)

Owner explicitly approved the amendment and one separate native diagnostic attempt;
[authority](owner-authorization.md). Old campaign remains 4/4 exhausted, unchanged.
[Approved single-arm plan](approved-resource-plan.json), [new ledger](new-attempt-ledger.json).
No quota infrastructure, production/runtime native code or other-project intervention.

Only an operation-bound contention-aware exploratory context uses monitored soft
budgets. Legacy/qualification/other benchmarks retain strict workspace decisions.
Raw scanner observations are untouched. Known `PARTIAL_KNOWN_RACE` continues as
UNKNOWN/INCOMPLETE; no capacity proof or verified lifetime peak is fabricated.
Observed soft breaches record metric/observed/budget/excess bytes and warnings.
Permission/EIO/scope/coverage errors remain fatal, as do missing hard safety
telemetry, OOM/limit distress, monitor gap, RAM/disk reserve failure and timeout.
Hard RAM1GiB/swap0/CPU200%/tasks128,115s watchdog120s systemd lifetime,
1GiB host RAM/2GiB disk reserves remain; disk checks conservatively still reserve
both512MiB arm allowances, including for the authorized single arm.

Completion with workspace uncertainty is explicitly
`COMPLETED_WITH_WORKSPACE_UNCERTAINTY`, measurement quality INCOMPLETE, workspace
UNKNOWN; observed breach gives `COMPLETED_WITH_WORKSPACE_WARNINGS`. Neither is
PASS or qualifying evidence. Hard safety completeness and workspace completeness
are separate fields. Verified cleanup remains mandatory.

[Engineering receipt](engineering-verification.json): **79 distinct tests PASS**
(32contention +19workspace +15lifecycle +5policy +7AApolicy +1syntheticpair).
Seven new focused tests cover both overages, race uncertainty, unknown scope,
hard disk reserve and missing process telemetry despite races, mode isolation and
invalid measurements. Final32 rerun pins final test/runner source. Initial
wrong test reason assertion preserved: the guard correctly aborted, only the
assertion was corrected. Initial lifecycle CLI omitted required output-dir
(parser exit2, no workload); corrected invocation passed, raw log retained.

**Actual independent-watchdog rehearsal PASS**, fixed Python32KiB/1s, not native:
18over-budget samples, receipt `COMPLETED_WITH_WORKSPACE_WARNINGS` (not PASS),
raw complete sampled accounting (not peak proof), exact context binding/hard caps,
exit0 and verified unit/cgroup/socket retirement. Tiny workspace removed. Evidence:
`m4-contention-rehearsal-soft-1/` and `tests/rehearse.log`.
Direct four-path `ty check` PASS. Active four-path LSP0errors,2confirmed clean,
2inconclusive; not claimed four-path clean. Native workload source untouched; no native
build/oracle/sanitizer rerun needed. Retained completed correctness/durability
receipts and compile/library hashes must freshly match before native execution.
Test-only formatting was applied to eliminate existing/new style diagnostics.

## One new attempt boundary

`launch_once.py` claims a new exclusive operation directory and consumes the new
max1ledger BEFORE readiness; refusal cannot be retried. It verifies the exact
full-sync ELF,61compiler inputs, linked libraries and retained completed
correctness/fault evidence, same-controller real rehearsal, current capability,
no owned unresolved service, then fresh hard-capacity/integrity decision. Native
runs only on PASS, one500x20 arm, no second arm, treatment or tracing. The old
ledger is read-only and hashed before/after. Runtime hard checks remain active.
A150s external timeout bounds the launcher; the independent watchdog owns only
its cgroup. All native data including temporary analyzer data remain inside a
fresh private0700 Btrfs workspace, retained afterward. Before/after host snapshots,
top processes, native affinity observation and per-process/cgroup runtime metrics
are retained. Concurrent host activity is context, not causal attribution.

## Native execution — actual aborted arm

Operation `m4-contention-soft-budget-native-20261021-1`, job `job-8740-36`,
launcher exit3 (abort, not unhandled launch failure). Fresh static full-sync ELF,
61compiler inputs, runtime libraries and completed correctness/fault receipts
matched; same-controller actual rehearsal verified, no unresolved owned units.
Readiness hard capacity/integrity PASS. Historical successful-run envelope memory
and disk comparisons were FAIL and retained as context, correctly nonblocking
only in the explicitly selected contention-aware mode. They were not silently
changed or misreported PASS.

Native **PID716330**, start **2026-10-21T02:32:52.935899Z**, unit
`m4-contained-adf6803b58cc4074.service`, bootstrap716259. Exact full-sync SHA
`ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f`.
Native ran **1.175346733s** from command-start to observed exit, then returned
**-15/SIGTERM**; systemd reports bootstrap final signal9 separately. All hard
caps unchanged, tracing off, one arm only, no retry or candidate comparison.
Private Btrfs root `/var/tmp/m4-soft-native-ilm3j5wc` retained, not deleted/reused;
temporary analyzer data were inside this monitored root.

At02:32:54.073089Z scanner saw two `DIRECTORY_ACTIVITY` records with inotify
mask2 (MODIFY), then select reported the queue readable after one bounded drain.
It emitted `MUTATION_COVERAGE_LOST: events exceed one bounded drain`, causing
raw `inventory_status=UNVERIFIABLE`, stateUNKNOWN, qualityINCOMPLETE and watchdog
`workspace_capacity_unverifiable`. No observed overflow/unmount/truncated event
or root/permission/EIO error was present. A readable queue can be new writes
arriving after the read, not proof the 64KiB drain buffer overflowed. Scanner
last duration41,152,320ns,167entries,135files,32directories; none of the work/time/
entry/depth budgets was exceeded. This was a direct remaining overstrict
accounting-classification defect for the owner-approved soft-budget mode, NOT
evidence of RAM/disk-reserve distress. Exact syscall/actor was not traced.

### Partial resources and impact (not whole-arm costs)

Twenty runtime samples; process/cgroup required safety telemetry complete,
workspace accounting incomplete. All recorded hard reserve decisions allowed
continuation; final memory/swap/task limit events zero, no OOM or throttling.
- cgroup memory peak **229,666,816B**, swap0; CPU **824,026us**, nr_throttled0.
- sampled tasks max76/128; process-tree PSS221,518KiB/RSS236,216KiB.
- sampled process-tree writes max**39,514,112B**: partial cumulative /proc traffic,
  not a physical device counter, allocation peak, or isolated writer measurement.
- last partial scan allocated**14,897,152B**, apparent**22,994,122B**, both below
  soft budgets in that partial observation. These are NOT verified lifetime peaks.
  Complete-traversal maxima14,499,840B/22,896,842B remain sampled observations only;
  `workspace_verified_peak=null` and peak_sampled fields are null due incompleteness.
- host load1:1.81–2.14; MemAvailable4,418,612–5,221,452KiB;
  disk available16,543,498,240–16,552,894,464B. Host I/O full, memory full,
  CPU some avg10 all0 in these short samples (smoothed metrics, not proof no stalls).
  Host-wide changes are association, not identified incremental M4 impact.
- native affinity/top/status were actually captured in `native-affinity.json`;
  host-before/after and top-process snapshots retained. No unrelated process was
  signalled, stopped, deprioritized or made an admission prerequisite.

Native stdout was empty: **0 complete cycle payloads**, all writer latency/stage
and reader coherence/reopen verdicts unavailable, NOT zero or successful. Directory
names reaching generation4 are not a substitute for validated completed cycles.
No writer-tail result, regression/win, qualification, A/A comparison or arm-order
variability can be inferred.

Watchdog SIGTERM native, final owned cgroup.kill success. Runner retirement
verified; independent inspection found exact unit not-found/inactive, owned
cgroup/socket absent and no remaining `m4-contained-*` unit. Raw early bootstrap
population and terminal retirement are kept separate. Partial workspace retained.

### Direct defect correction — prospective, no new attempt

Under the already-approved soft-budget semantics, only the exact pending-queue
issue above (plus known mutation issues) is now tolerable as accounting UNKNOWN.
Raw scanner remains unchanged: still UNVERIFIABLE/INCOMPLETE, no extra drain,
retry, rescan, fabricated COMPLETE inventory or capacity/peak proof. Tolerance
requires root identity and every issue example present. Actual observed overflow/
unmount/truncated events, scope replacement, permission/EIO and work-budget
failures still abort, including when mixed with pending events. Other modes keep
strict behavior. Independent hard safety telemetry and reserves remain mandatory.

Two new deterministic regressions simulate pending-after-drain and prove no
scope/overflow/error/incomplete-example hiding. Final **81distincttests PASS**
(34contention/19workspace/15lifecycle/5policy/7AApolicy/1syntheticpair), pinned
`final-synthetic/receipt.json`. This is a narrowly scoped direct defect correction,
not another infrastructure study. The correction was **not run natively**: the
single new attempt is spent. The original execution plan/policy/source identity
stays pinned to engineering commitfdef863; its old policy SHA intentionally does
not authorize a future run under the newer policy. Neither it nor raw abort/
receipts/ledgers is rewritten to claim success.

## Acceptance and remaining status

- Exploratory soft-budget policy/runner, raw overages/UNKNOWN, mode isolation,
  hard safety/correctness/durability/cleanup preservation: implemented/tested.
- Focused tests, actual watchdog rehearsal and pre-native commit: completed.
- One fresh readiness then one actual native attempt: performed, no retry.
- Historical4/4ledger/receipts and other projects/production: untouched.
- **Native writer-tail measurements: NOT obtained**; partial arm only. New1/1
  consumed, zero remaining. Report/RESULTS/handoff reflect failure, not completion.
- Remaining condition: a separately renewed finite native execution authorization
  would be needed to validate the prospective direct correction and obtain cycles.
  This task grants no additional attempt; do not launch/poll/retry from this report.

File-only reproducible analysis: `python research/m4-native/results/workspace-soft-budget-20261021/analyze_native.py`.
Raw arm artifacts, independent cleanup and fresh provenance: `m4-contention-soft-budget-native-20261021-1/`.
Summary `native-analysis.json`, final software/identity checks `final-verification.json`,
manifest `SHA256SUMS.txt`. No native source or durability change, no production work,
no push. Three pre-existing Rust modifications remain outside these commits.
