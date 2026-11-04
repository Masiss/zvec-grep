# M4 native500x20 after pending-drain correction — 2026-10-21

## Outcome

**The ONE newly authorized native arm completed:20commits,80reader queries,
exit0, coherence errors0, post-close reopen generation21/live500/errors0.**
Writer commit **p50 231.399ms / p95 267.359ms / p99 369.428ms**.
This closes the requested bounded single diagnostic, **not qualification or the
broader comparative writer-tail mission**.

Raw receipt is **COMPLETED_WITH_WORKSPACE_UNCERTAINTY**, overall measurement
quality **INCOMPLETE**. Accounting is not fabricated complete:27/104workspace
samples are UNKNOWN (24known races,3pending-drain UNVERIFIABLE). Mandatory hard
safety/process telemetry is complete; no abort, OOM/distress/reserve failure.
One arm only; no retry, second arm, treatment or guard change. New1/1consumed,
zero remaining; historical campaign4/4 and preceding separate1/1spent unchanged.

Authority: [owner instruction](owner-authorization.md), [new plan](approved-resource-plan.json),
[new ledger](new-attempt-ledger.json). File-only reproducible analysis:
`python research/m4-native/results/pending-drain-native-20261021/analyze.py`.
[Analysis](analysis.json), [per-round data](per-round.csv),
[verification](verification.json), [checksums](SHA256SUMS.txt).

## Verification and actual execution

Question: does4579e5a allow the authorized native500x20 to finish while retaining
pending/race UNKNOWN and every hard gate? Discriminating evidence is a completed
20cycle payload plus reopen/coherence verdicts, exact pending rows not fabricated
complete, no hard gate failure and verified cleanup. This is not a performance
comparison or a claim about every possible filesystem race.

- Runtime runner/policy remain byte-identical to **4579e5a**; no harness built or
  runtime/production/native source changed. Existing one-shot launcher and fixed
  rehearsal were copied byte-for-byte into the new evidence scope.
- Exact correction/source hashes verified against retained final-verification;
  **34focused tests PASS**, including pending queue tolerance, raw UNKNOWN,
  mixed overflow/scope/EIO errors, missing hard telemetry, soft overages, reserve
  failures and strict other-mode behavior. No rerun of the prior81test campaign.
- **Actual same-controller e2e rehearsal PASS**, fixed32KiB/1s Python (not native),
 19over-budget samples and non-PASS warning receipt, exact hard caps/context,
  exit0, verified owned service/cgroup/socket cleanup. No native budget consumed.
- Fresh native readiness/provenance **PASS**: pinned full-sync ELF,61compiler
  inputs, runtime libraries and retained completed correctness/durability/fault
  evidence match. No unresolved owned units. Current hard capacity/integrity
  decisions pass; historical host envelope comparisons remain raw context only.
- Native job **job-8740-44**, PID **784988**, bootstrap identity in raw run-state,
  unit **m4-contained-a92795d9db7a4185.service**, start
  **2026-10-21T02:44:03.108833Z**, exit **02:44:09.221028Z**.
  Command-start to observed-exit **6.112181178s**; systemd service6.387s includes
  bootstrap/control. Native main benchmark payload whole-wall5.5758474s excludes
  separate post-close reopening; these are different intervals, not conflicting clocks.
- Same binary SHA **ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f**,
  full sync unchanged, tracing/fault injection off,500docs20rounds,4reader threads.
  Fresh private0700 Btrfs workspace including analyzer temp data; retained path
  in raw command/outcome, never deleted or reused. No other-project intervention.

Raw receipts: `m4-contention-pending-drain-native-20261021-1/aa-first/containment/`.
Before/after snapshots, contemporaneous top processes and actual native affinity
are retained; native affinity0–19, CPU quota2core-equivalent rather than a2CPU pin.

## Writer and native/post-M4 stages

All ms; nearest-rank definition matches source `PercentileMs`:
index=ceil(q*n)-1. **n=20; p95 is second-largest and p99 is maximum**,
not a stable population-tail estimate. Per-round commit includes build/publication/
retirement after reader release; excludes base construction and separate reopen.
Quantiles of different stages are not additive.

| Stage                              |     p50 |     p95 | p99=max |
| ---------------------------------- | ------: | ------: | ------: |
| Writer commit wall                 | 231.399 | 267.359 | 369.428 |
| Writer commit thread CPU           | 155.785 | 187.603 | 278.175 |
| Native vector build wall           | 102.032 | 117.603 | 223.503 |
| Native vector upsert wall          |   4.664 |  19.290 |  20.465 |
| Native vector flush wall           |  19.391 |  24.453 |  31.131 |
| M4 update wall                     | 103.357 | 119.271 | 121.150 |
| M4 open wall (inside M4 update)     |  85.989 |  95.473 |  96.290 |
| Post-M4 SyncTree wall              |  15.385 |  26.212 |  26.304 |
| Snapshot finalization wall         |   0.286 |   0.352 |   0.415 |
| Generation publish wall            |   3.698 |   4.478 |   5.179 |
| Retirement wall                    |   0.614 |   4.924 |   6.709 |
| Entire post-M4-to-round-end interval |  24.051 |  37.658 |  40.929 |

The last row is derived per round from `round_end_ms - m4_build_end_ms`, so it
includes SyncTree, finalization/publication/retirement and intervening work; it is
not a sum of separately selected stage percentiles. Arrays and absolute stage
boundaries are retained in raw stdout; `per-round.csv` joins each commit to its stages.

Summed20commit wall **4837.616ms**, writer commit-thread CPU **3308.866ms**.
Summed native vector build is44.97% of commit wall, M4 update42.69%; post-M4
SyncTree7.66%, entire post-M4 interval10.66% (SyncTree is a subset, do not add both).
The wall-minus-writer-CPU gap is not pure I/O wait: scheduling, other threads,
fsync/locks and instrumentation can contribute. No causal decomposition is claimed.

Slowest **round7, zero-based**, compacts:369.428ms total; native223.503ms
(including native open180.306ms), M4update104.606ms, post-M4SyncTree21.367ms,
wholepost-M437.658ms. Five compaction rounds have median246.019ms vs229.670ms
for15noncompaction rounds. These are associated observations in one trajectory,
not an isolated compaction causal experiment. Native/M4 open/build dominate this
run more than post-M4sync; no optimization or candidate superiority follows.

Readers:80queries, p50/p95/p99 **2.092/2.966/4.332ms**, coherence0.
Post-close reopen: **460.217ms**, processCPU510.993ms, generation21/live500/errors0.
Final catalog:1retained generation,0pinned retired generations,1current shard,
0stale vector rows,5compactions. Source checks throw on coherence/reopen mismatch;
exit0 and all three payloads support these bounded correctness verdicts, not
universal ranking/durability/production qualification.

## CPU, memory, I/O and containment

Hard controls unchanged: **RAM1GiB,swap0,CPU200%,tasks128,watchdog115s/systemd120s**;
host RAMreserve1GiB, diskreserve2GiB plus conservative two-root512MiB allowances.
All runtime hard capacity decisions allowed continuation, sample gap max70.061ms
vs2s limit. No OOM/memory.max/swap/task distress or cgroup CPU throttling event.

- Kernel cgroup lifetime peak **323,055,616B =308.090MiB**, swap0; CPU
  **4,978,732us** (includes bootstrap/workers/readers/main/reopen, not isolated writer).
- Native main phase processCPU **4336.282ms**; writercommit3308.866ms,
  basewriter585.108ms, reader threads136.476ms, residual305.833ms. Separate reopen
  processCPU510.993ms. Scope and instrumentation differ from cgroup lifetime CPU.
- Native process maxRSS223,556KiB before reopen;305,504KiB high-water after reopen.
  Watchdog sampled process-tree maxima PSS275,706KiB/RSS290,228KiB, tasks80/128.
  Sampled values are not kernel lifetime peaks; do not equate PSS/RSS/cgroup memory.
- Raw native `physical_write_bytes` is actually **/proc/self/io write_bytes**:
  main214,171,648B + reopen6,086,656B = **220,258,304B (210.055MiB)**.
  Sampled process-tree writes220,262,400B have different bootstrap/tree scope.
  These are kernel-accounted write traffic, **not physical device attribution**,
  simultaneous occupancy or per-writer-only writes. No delegated cgroup IO limit.
- Workspace:77complete traversals (not atomic),24partial known races,
 3rawUNVERIFIABLE pending-after-drain rows, all three continued UNKNOWN with no
  extra drain/rescan. Soft-budget warning samples0 means **no sampled overage**,
  not proof budgets never exceeded. Complete-traversal observed maxima
  allocated18,665,472B/apparent34,150,594B are not verified lifetime peaks.
  `workspace_verified_peak=null`, incomplete peak fields remain null; credit0.
  Native final root logical6,181,823B is retained size, not peak occupancy.
- Observer cost: mean scan5.056ms/max14.240ms; mean whole sample8.830ms/max19.437ms.
  These are wall brackets, not observer CPU. External sampling/diagnostic overhead
  may perturb timings; there is no no-observer comparator in this one-arm task.

Verified controller terminal cleanup; independent exact unit not-found/inactive,
cgroup/socket absent, all owned units absent. No unknown data removed, no resource
reclamation or signal sent to unrelated projects. Partial historical artifacts
remain untouched; the completed workload root is retained separately.

## Host contention and remaining uncertainty

104runtime observations: load1 **6.12–6.32**, MemAvailable **3,184,020–4,239,384KiB**,
disk available **16,304,246,784–16,327,151,616B**. Host PSI avg10:
I/O full **0.75–1.34%**, I/O some1.25–1.68%, memory full **5.63–9.73%**,
memory some6.07–10.52%, CPU some **2.00–3.66%**. This was not an idle-host run.
Host swap-in increased **51,823pages**, swap-out/direct reclaim/OOM counters did
not increase across sampled endpoints; owned cgroup swap stayed0. These are
shared-host observations, not evidence that M4 caused another project's swapping.
Host diskstats (including stacked devices) are retained separately, not summed
into claimed M4 physical-device writes. No stationary baseline/control arm was
requested or created, so incremental host impact is unidentified.

One run,20cycles, no order comparison, confidence intervals or stable-tail claim.
Workspace accounting intentionally remains incomplete even though native work
completed. Soft budgets provide no hard filesystem quota or deterministic
overshoot guarantee. No throughput/regression/promotion/qualification inference.
The run validates the pending-drain continuation in native execution **only for
the three observed exact pending cases**, not all possible filesystem races.

## Acceptance and authority closure

1. Pending correction, source/binary provenance and caps verified; unchanged4579e5a.
2. Focused34tests and actual same-controller watchdog rehearsal completed.
3. Fresh readiness PASS, exactly one new native500x20 arm completed.
4. Contention-aware soft accounting plus hard independent safety retained;
   no other-project/production intervention, no guard changes or new harness.
5. No watchdog abort occurred; raw uncertainty and all receipts preserved.
6. Writer/stage/CPU/RAM/IO/host context analyzed, single-run limitations explicit.
7. RESULTS/README/policy narrative/canonical handoff updated; evidence committed,
   no push. Runtime/native code and historical ledgers/receipts unchanged.

**Requested bounded diagnostic COMPLETE WITH CAVEATS.** Broader M4 qualification,
comparative causality and migration remain unapproved/unresolved. New1/1spent,
zero remaining; no further native run, retry or admission polling authorized.
