# Current-tree native lifecycle profile — 2026-10-27

**Status: optimizer-thread A/B completed as a single exploratory pair; 4 threads is not selected.** The 4-thread arm reduced four-mutation finalization by only 1.13% (51.8 ms), while its query p95 was 320.494 ms and failed the 300 ms provisional SLO (control: 263.321 ms, pass). Freshness passed in both arms. One pair cannot establish statistical significance; keep the default at 2 threads and make no production change.

## Protocol and identity

- Operation: `p2-native-profile-20261027t134637z-d0a1aa13`.
- Frozen profile protocol: `benchmarks/native-lifecycle/p2-native-profile-protocol.json`, SHA-256 `c64cfc9a316a2c6474955ef525abec4304c29eb83ee30c952f9458b88c23deb8`. Runner SHA-256 `599e7cc1e77ea67510cabd94d919add0629ddb96622ea19fc2600afde0124e99`; exec guard SHA-256 `baf315eaf5555ea3c90d1a6620f469f8371d0ee2d9c17dc8549591241a655006`.
- Reused the P1 frozen trace and corpus: commit `34d75aa14c760d302e497831c7501e1a31400f2b`, 64 Rust files / 694,421 bytes; corpus manifest SHA-256 `7fbc9f951a9b1f75388150a891c984f23dd72717a6ce59070f89fd9f3ad832a3`; workload protocol SHA-256 `a589de61776e2d1d7fddc3630f5bf6d479bfd9160701522991605e75c7edb459`. Same 250 ms serial FTS schedule, 300 ms query target and 5 s watcher-observed-to-visible target. Deterministic test embedding; no network/inference.
- Source HEAD `d3b2a95bb238bce47e2dcb87034e5600d9de56e0`; tracked worktree diff SHA-256 `afc005b3690be34d6f73fcf56b896187f833d25ada6652564d7534adaa425cb6`.
- Test binary after build SHA-256 `b967f69667b249f37bac8c25723ad0e5fe5012ac283c9a8c44362c551914fc5e`; `libzvec_c_api.so` SHA-256 `58381ac7b12afd5eeae3dc10325914a28fc3157061291bb693a9ed757d815b8a`.
- Test `workspace_runtime::tests::freshness::p1_native_lifecycle_baseline_once`: **1 passed**, 12.37 s test body. The complete build+test scope returned 0 in 3:36.62.

## Findings

The profile emitted eight completed scheduler jobs with 8.683 s summed job wall time; recorded stage timers sum to 8.661 s. `index_optimize` accounts for 5.715 s (65.8% of summed job wall). That stage is measured around `context.storage.finalize_writes()`, so it includes checkpoint/probes and all selected native collection finalization—not only fragment `Collection::optimize()`.

| Trace work | Job wall | `index_optimize` | Share of job |
| --- | ---: | ---: | ---: |
| Initial index, 64 files | 3.822 s | 1.429 s | 37.4% |
| Single-file update | 1.651 s | 1.557 s | 94.3% |
| Four-file burst | 1.500 s | 1.234 s | 82.3% |
| Rename/replacement | 1.353 s | 1.229 s | 90.8% |
| Delete follow-up | 0.291 s | 0.265 s | 90.9% |
| Unchanged scans after reopen | 0.021–0.027 s | 0.0004 s each | <2% |

For the four actual watcher mutation jobs, `index_optimize` totals 4.285 s of 4.794 s (89.4%). The residual between watcher-observed-to-index-complete and executor duration is approximately 13–19 ms per mutation in this trace; scheduler wait is not the dominant measured cost. `/proc/self/io` process-wide `write_bytes` deltas during those jobs were 61.9, 64.0, 64.7 and 15.6 MiB. These process counters include concurrent query sampling and are correlational, not exclusive native-operation attribution.

The query report has 45 successful samples and no query failures. Nearest-rank p95 is 260.040 ms, but one schedule slot was missed and maximum lag was 274.846 ms (>250 ms); under the frozen validity rule, query SLO status is **INCONCLUSIVE**, not PASS. The largest query was 523.367 ms. Freshness is **PASS for this run**: maximum 444.914 ms across update, burst, rename and delete, below 5 s. This remains exploratory single-run evidence, not qualification.

## Containment and measurement limits

The effective scope passed all checks: CPU 200%, memory max 1 GiB, swap 0, pids max 128. Host `MemAvailable` was 7.26 GiB before launch and stayed at or above 6.22 GiB during the run; `.tmp` free space was 30.20 GB at launch and at least 30.03 GB during the run, above the 3 GiB/16 GB reserves. I/O PSI was already elevated before launch (some/full avg10 6.09%/6.01%) and was 4.38%/4.12% after. The cgroup recorded memory peak at its 1 GiB limit and 657,417 `max` events, but zero OOM/OOM-kill events; pids peak was 51. The scope was removed at exit, so final cgroup state is unavailable; 407 live cgroup samples are retained. The build and workload shared the scope, so this peak cannot be attributed to the test body alone. These constraints keep the result diagnostic.

Scheduler telemetry was enabled with `ZVEC_GREP_NATIVE_PROFILE=1` in the test harness; it records stage timings and process deltas without a post-job sleep. The prior two-second “post-job tail” was removed because `finish_job()` schedules a follow-up before returning, which made that tail overlap subsequent work. Scheduler telemetry is disabled outside `cfg(test)`. The finalization probe is now behind an explicit opt-in `zg-engine/native-profile` Cargo feature and the same environment guard; it is not enabled by default.

## Follow-up attempts: substage capture

- Operation `p2-native-substage-profile-20261027t142307z-f5acfee1` completed under the same frozen lifecycle trace and resource envelope. The test passed **1/1** (14.56 s body; 3:11.65 build+test scope), but the workload log contains **zero** `NATIVE_PROFILE event=store_finalize` records.
- Cause: the added telemetry used `#[cfg(test)]` inside `zg-engine`, but `zg-engine` is compiled as a normal dependency of the `zg-daemon` test; Rust does not set `cfg(test)` for that dependency. This is an instrumentation integration defect, not an application test failure. No finalization substage data was obtained; do not pool this run with the earlier profile.
- Query p95 was 220.590 ms over 52 successful samples, but two schedule slots were missed and max lag was 586.247 ms, so query SLO status is **INCONCLUSIVE**. Freshness max was 496.539 ms (**PASS for this run only**).
- The containment guard passed. During the build+test scope, host memory remained at least 7.44 GiB available and disk at least 29.78 GB free; cgroup memory peaked at its 1 GiB cap with zero OOM events, pids peak 53. This is diagnostic evidence, not a performance comparison.
- Corrected the instrumentation to use an explicit `zg-engine/native-profile` Cargo feature, disabled by default and gated by `ZVEC_GREP_NATIVE_PROFILE=1`. A compile-only test-harness build with that feature **PASSed**; log: `.tmp/p2-native-substage-compile-20261027T143527Z/build.log` (SHA-256 `22d4cdbc6bc32a32f0720dedd0d92b9bacb96de8004948bcf75d56866bbfa470`). The corrected implementation was then run under a new one-run authorization and protocol below.

Artifacts: `.tmp/native-lifecycle-profile/p2-native-substage-profile-20261027t142307z-f5acfee1/`; profile report SHA-256 `1a0022d5ab8fd202d60ca05158c493b72964f9747ad921f31a70f6d2358b24f9`, workload log SHA-256 `2cc5e131319239b3c6f22aa6b8025a2374b8839a94e91d2e7115542402bbca94`, containment SHA-256 `6ad283cc79646d665d4a8e274f465a19195d8acc178ddea4b4f6276b890969cf`. Authorization ledger: `.tmp/p2-native-substage-profile-authorization-consumed-20261027.json`.

### Corrected v2 profile

- Operation `p2-native-substage-v2-20261027t145503z-2a65b72b`; protocol `benchmarks/native-lifecycle/p2-native-finalize-profile-v2-protocol.json` (SHA-256 `055e783fdc5d11329d817eeb3faf8880635030cbc764d1d288d09b8f9ab860be`); runner SHA-256 `14279448d374c1c28273770fbdd3c0ec97dc35c2f2982a2a4e4776a92fd9e3da`. Same frozen workload and cgroup policy, explicit feature enabled, source remained working-tree HEAD `d3b2a95…`; tracked diff SHA-256 `83128b5738d23b1cc5cc876fc17df096f9ea1c521770597ddf0c2240b190addf`.
- Test passed **1/1** in 12.12 s; scope wall time 12.71 s, process max RSS 664,424 KiB. Eight finalization events were captured. The four watcher mutation finalizations totalled 3.734 s:

| Finalization | Total | Checkpoint | Entities | Files | Fragments | Relevant state |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Initial index (64 files) | 1,730.877 ms | 787.205 ms | 178.131 ms | 279.068 ms | 282.459 ms | 64 replaced; incomplete/finalize |
| Single-file update | 1,185.860 ms | 71.096 ms | 142.523 ms | 219.074 ms | 752.936 ms | `fragments_incomplete=true` |
| Four-file burst | 1,070.546 ms | 74.033 ms | 115.240 ms | 208.544 ms | 672.510 ms | `fragments_incomplete=true` |
| Rename/replacement | 1,216.898 ms | 89.679 ms | 134.699 ms | 236.283 ms | 754.471 ms | one replaced + one deleted; incomplete/delete |
| Delete follow-up | 261.142 ms | 39.377 ms | 0.149 ms | 218.053 ms | 0.362 ms | one deleted; delete forces optimize |

  Across those four watcher mutation finalizations, fragment optimization was 2.180 s (**58.4%**), file optimization 0.882 s (23.6%), entity optimization 0.393 s (10.5%), checkpoint 0.274 s (7.3%); remaining measured time was 5.417 ms. The three repeat/no-op finalizations had no native optimize work. This is per-stage timing evidence from one trace, not a paired performance comparison.
- Query p95 was 151.674 ms over 44 successful responses, but one slot was missed and max schedule lag was 318.036 ms (>250 ms), so query status remains **INCONCLUSIVE**. Freshness max was 340.236 ms (**PASS for this run only**). Containment passed: host memory minimum 5.50 GiB, free disk minimum 28.23 GB, cgroup memory peak 811,094,016 bytes, pids peak 49, zero OOM events.
- Artifact hashes: profile result `7a101536941a00d52bb30030e3b000e3bfb862c1064dd62e22808aa0ebd2588e`; workload log `ebfb550c658bd44d13b61ccc1916ec6c8302c2f5b9f70e5bae44856f160bbb47`; containment `bc0bf9fe0b901ac4b2b145cfb7f766cb66670d8f85fdd9e957aa32716a09cbe3`; manifest `a82cc4ea11f0ed6a4d7d4b17ba505699c2473d2694bc07cb4a911a0ea8982f70`. Authorization ledger: `.tmp/p2-native-substage-v2-authorization-consumed-20261027.json`.

## Owner-authorized optimizer-thread A/B attempt

- Pair `p2-native-optthreads-ab-20261027t151858z-d36a1394`, protocol `benchmarks/native-lifecycle/p2-native-optimize-threads-ab-protocol.json` (SHA-256 `a675c579e8306d2b5f0e7a89cecfe0669c675da5fe950702ca53c40cb59b9c45`). Control used `ZVEC_GREP_OPTIMIZE_THREADS=0` (no override; effective default 2). Candidate was preregistered as 4 threads.
- The control test passed 1/1 and emitted all eight finalization events. Its four mutation finalizations totalled **3.575409 s**, including **2.078068 s** in fragment optimize. This is a control-only observation; it is not compared with the earlier diagnostic run.
- The query schedule was invalid under the frozen rule: 40 successful samples, one missed slot, max lag **281.214 ms** (>250 ms). The p95 was **145.688 ms**, but query SLO remains **INCONCLUSIVE**. Freshness max was **392.045 ms** (PASS for this run only).
- The v1 pair stopped after its control because that protocol treated an invalid query schedule as a pair stop. No candidate ran in v1; status `INCONCLUSIVE_QUERY_SCHEDULE_STOP`, and its authorization was consumed. A fresh v2 pair was then authorized. Its first preflight (15:33 UTC) was blocked by low available memory and did not launch or consume its ledger; after the owner said “go,” the new preflight passed and the pair ran. V2 status is `PAIR_COMPLETE_EXPLORATORY`; its ledger is consumed.
- Containment passed for the control: cgroup memory limit 1 GiB, sampled `memory.current` maximum 857,980,928 bytes, process max RSS 720,416 KiB, no OOM/OOM-kill or CPU throttling; sampled host `MemAvailable` minimum 6,209,126,400 bytes and disk minimum 28,197,965,824 bytes. Artifacts: `.tmp/native-lifecycle-profile/p2-native-optthreads-ab-20261027t151858z-d36a1394-pair/` and `.tmp/native-lifecycle-profile/p2-native-optthreads-ab-20261027t151858z-d36a1394-control-20261027t151859z-dec6dc39/`. Pair-manifest SHA-256 `88b14a85373ff8938529afa28b13e10127d09ae7a4ddb81b62c12cccd273cc97`; control profile SHA-256 `65a0ae61194bc19baddc0d17d07c6ebf06e5ac533b6ab360d37fdfafac3e3f80`; workload log SHA-256 `803c80002c6f5ebfe8cec11f9d0ed466aecba6b048469984f3932e426fe63e40`.

### V2 pair authorization and completed measurement

Protocol v2 (`benchmarks/native-lifecycle/p2-native-optimize-threads-ab-v2-protocol.json`, SHA-256 `5ce9c18d2aa6b65db0908cb146cb928bfbb9f45ebf482b8ea7592a997e50aae5`) kept mutation finalization as primary; a query schedule miss only invalidates that arm's query p95 and does not stop the other arm. The initial preflight receipt is `.tmp/native-lifecycle-profile/p2-native-optthreads-ab2-preflight-blocked-20261027T153317Z.json` (no launch); after the owner re-requested launch, preflight passed at 15:37 UTC (MemAvailable 9,656,479,744 bytes, disk 28,201,115,648 bytes). Pair `p2-native-optthreads-ab2-20261027t153804z-f479eee4` then completed one control and one candidate run. Both tests passed 1/1; each emitted eight finalization events; both query schedules were valid with zero missed slots. The 2-thread control query p95 was 263.321 ms (SLO PASS), freshness max 629.776 ms (PASS). The 4-thread candidate query p95 was 320.494 ms (SLO FAIL), freshness max 444.235 ms (PASS).

| Arm | Mutation finalize total | Fragment optimize | Entity optimize | File optimize | Checkpoint | Query p95 / SLO | Freshness max / SLO |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 threads (control) | 4,568.694 ms | 1,999.138 ms | 799.949 ms | 1,369.385 ms | 396.494 ms | 263.321 ms / PASS | 629.776 ms / PASS |
| 4 threads (candidate) | 4,516.861 ms | 2,133.970 ms | 648.704 ms | 1,332.813 ms | 397.847 ms | 320.494 ms / FAIL | 444.235 ms / PASS |

Candidate finalization was 51.833 ms faster (-1.13%), but fragment optimize itself was 134.832 ms slower (+6.74%). Query p95 rose 57.173 ms (+21.71%) and crossed the provisional 300 ms target. This is one pair, so do not interpret small timing differences as statistically established; the measured candidate nevertheless fails the provisional query SLO in this run. The v2 authorization was consumed by this pair. Containment passed: cgroup memory limit 1 GiB; sampled peak memory 641,732,608 bytes (control) / 794,963,968 bytes (candidate); zero OOM events and zero CPU throttling. Sampled host MemAvailable minima were 9,023,537,152 / 8,914,927,616 bytes; disk minima 28,182,134,784 / 28,173,537,280 bytes. Host I/O PSI was elevated (some/full avg10 about 3–5.5%), a caveat for a single pair. Artifacts: pair `.tmp/native-lifecycle-profile/p2-native-optthreads-ab2-20261027t153804z-f479eee4-pair/`; control `.tmp/native-lifecycle-profile/p2-native-optthreads-ab2-20261027t153804z-f479eee4-control-20261027t153805z-fdfac86a/`; candidate `.tmp/native-lifecycle-profile/p2-native-optthreads-ab2-20261027t153804z-f479eee4-candidate-20261027t153819z-8bddc757/`. SHA-256: pair manifest `95537dbd793cf424a61773f2c103e048898f62e19f430dc09f57c350dd5c43c2`; control/candidate profile results `81e09ad55d06fc31e5d2af30cbb6abaa54de931a3903201619557e15c5fbc029` / `2b2e5d85a89c9948e3bf120f1c69f79123f776b98ba4c3ff6d588323fa127946`; control/candidate workload logs `fbb7c0bd41d8e31cbf32cc85d440b4bcc6a3c0a1a34a018882a650b566e155b0` / `513467655e571cc9b6a73e1b4c3969a9933df7524430586703e47aceeb0d9855`; containment `f76a4b91e7049585c4f9fb8892ec71fa5567e8af9166de4325ebc073cd101072` / `fa4883d579aa39b17ad36ca3b8d04ff31e3ddac6430846a04938d376d52aecb2`. The consumed v2 authorization ledger is `.tmp/p2-native-optimize-threads-ab-v2-consumed-20261027.json` (SHA-256 `7dd099a15a06e5ab606794124b6020b4183af9f35780919783e7a1714c4cbd4f).

## Candidate decision / next step

The dominant bucket is finalization, but updates/replacements and delete paths carry fragment state that requires the current synchronous maintenance behavior for visibility. The existing `ZVEC_GREP_MAINTENANCE_THRESHOLD=50` experiment only defers insertion-only debt and cannot safely remove the forced delete optimization. The delete-only follow-up spent just 0.362 ms in fragment optimization, so deferring that required path would not materially improve this trace. Do not change the maintenance threshold or bypass synchronous delete visibility.

The 2-vs-4 optimizer-thread candidate does not justify adoption: the primary finalize-time improvement was only 1.13% in one pair, while candidate query p95 exceeded the provisional SLO (320.494 ms >300 ms). Keep the existing 2-thread default and synchronous completion behavior. No follow-up measurement is authorized under the consumed pair budget; any new candidate or experiment needs a separate protocol and authorization.

## Evidence

All run artifacts are under `.tmp/native-lifecycle-profile/p2-native-profile-20261027t134637z-d0a1aa13/`:

- `profile-result.json` (SHA-256 `58fdbcb93f85f8847900da0e9358526e72fd14275ad9ab4ec7f6717811ca0bd4`)
- `workload.log` (SHA-256 `18ca24882125e697055d0d3272c400fa4a8e18346ce33af5b0d41d8a991de6d9`)
- `containment.jsonl` (SHA-256 `2f135c9059616f930235fd91f39f2f2b5b3cf917b08e749383a7bdb3d3ac9a2b`)
- `run-manifest.json`, `runner-result.json`, `host-before.json`, `host-after.json`
- One-run authorization ledger: `.tmp/p2-native-profile-authorization-consumed-20261027.json`

Runner used for this operation: `.tmp/run-p2-native-profile.py`. Nothing was staged or committed.
