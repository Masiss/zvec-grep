# Phase 1 baseline and acceptance — 2026-10-27

**Status: PASS — P1 baseline gate completed with one valid exploratory run; not statistical qualification.** P1-A offline evidence review and retrieval-harness protocol checks are complete. The v7 native lifecycle run (#1710) completed the frozen trace under containment. Query p95 was **181.592 ms** with valid sampling and status **PASS** against 300 ms; freshness max was **385.027 ms**, status **PASS** against 5 s. The earlier v6 run remains disclosed as **INCONCLUSIVE** and is not pooled with v7. The single valid v7 run is exploratory evidence, not statistical or production qualification. The P2-A/B correctness-only slice has 17 focused passes across separate runs, including maintenance/reopen/delete, watcher scheduling, retry/epoch recovery, PK idempotency, current-source incremental PK-delete marker recovery (1/1), staged replacement (1/1), plus real native partial mutation; the partial-delete regression subsequently passed through the C-ABI interposer and ordinary Rust fallback (1/1 each). Phase 2's performance decision is to retain the native baseline without selecting a candidate; this is not performance qualification. See the [plan](optimization-upgrade-plan-20261027.md). No production/default change was made. See [P0 baseline](optimization-p0-baseline-20261027.md).

## Scope and identity

- Local source `HEAD`: `d3b2a95bb238bce47e2dcb87034e5600d9de56e0`; the working tree is dirty (P0 manifest records the tracked patch digest and source inventory). Read-only GitHub API checks found this SHA on none of the configured/benchmark remotes (`Masiss/zvec-grep`, `zvec-ai/zvec-grep`, `Cuiyus/zvec-grep`). No commit or push was made. A local package built from the captured Rust WIP snapshot is identified by its SHA-256 below.
- Retrieval protocol inputs were checked without running the retrieval workload. Current hashes:

| Input | SHA-256 |
| --- | --- |
| `benchmarks/zg-retrieval/configs/protocol.json` | `d29aa4c76d1c35c0f9cb1cbdd7707aff3be86fc72ae9ec942a3f30f6c5891c82` |
| `benchmarks/zg-retrieval/data/source.lock.json` | `c1352c5659d7511e1decfdbb357c90697e9f43f85f46a9b316041dee2d2a223b` |
| `benchmarks/zg-retrieval/data/queries.jsonl` | `e9128cce8697d62196834f80b777bed60ac85b919b194cbac8e96ea58ab31bea` |
| `benchmarks/zg-retrieval/gold/files-v1.json` | `a363e61342b54ed0001dc076286afd1112d7a63f121c94c278de3addf7e15912` |

## Offline findings

### Native/M4 lifecycle diagnostic

The best current diagnostic is the retained operation [`m4-rust-lifecycle-pair-20261027t052952z-332a873b`](../../research/m4-native/results/M4-GATE-B-QUERY-DROP-CAUSE-20261027.md), raw `pair-result.json` at `research/m4-native/.work/m4-native-workspace-quota/m4-rust-lifecycle-pair-20261027t052952z-332a873b/`. It ran M4 first, then native, with the same pinned 64-file workload; each workload report says **63 indexed files** and six passes.

`index_and_finalize_wall_us` is the available pass-level measurement; it is not a breakdown into scan/extract/embed/write/checkpoint/finalize/publish. Initial open and reopen are separate. All timings below are single-run diagnostics, not eligible Native-vs-M4 performance comparisons.

| Operation / pass | M4 ms | Native ms |
| --- | ---: | ---: |
| Initial open | 1,013.9 | 933.2 |
| Initial rescan (`index_and_finalize`) | 7,448.8 | 5,527.8 |
| Single-file update | 3,175.2 | 3,176.6 |
| Rename | 3,339.2 | 3,123.0 |
| Delete | 412.4 | 2,014.4 |
| Repeat rescan 1 | 48.1 | 22.9 |
| Repeat rescan 2 | 60.3 | 23.4 |
| Reopen | 453.6 | 254.5 |
| M4 compaction after passes | 165.6 / 62.0 / 57.2 / 48.9 / 38.0 / 41.3 | N/A |

The aggregate query figures are **not comparable**: M4 offered 540 slots, started 232, dropped 308 to its full queue, and omitted 42 more due to its cap; native offered 558, started 428, and dropped 130 to its full queue. Descriptive aggregate p95s were 3,076 ms (M4) and 796 ms (native), but both reports mark p95 non-comparable. Do not use them as a win/regression or qualification result. The route was FTS, not hybrid/vector; there is no relevance/gold score in this lifecycle trace.

Both workload arms exited 0, hard-safety telemetry was complete, and unit cleanup was verified. However, workspace inventory/soft-budget peak remains unknown; only qgroup capacity was sampled (minimum headroom 95,916,032 B M4 / 100,605,952 B native). Qgroup success is not proof of complete workspace accounting. The run's registered outcome is `PAIR_COMPLETED_WITH_WORKSPACE_CAVEATS`, `performance_eligible=false`.

The arms were ordered M4 then native. Host snapshots show I/O pressure already higher at the native arm's start (some/full PSI avg10 1.86/1.54 versus 0.42/0.35 at M4 start); this is a carryover/context confound, not a causal explanation. The watchdog observer itself recorded about 9.70 CPU-seconds over 9.93 seconds of loop work for M4 and 7.75 over 7.89 for native. These are observer costs, not application CPU. The cgroup memory-peak observations were 475,172,864 B and 469,872,640 B respectively, with zero swap peak; the available telemetry does not isolate per-stage application CPU.

**P1-A conclusion:** this trace identifies where *pass-level wall time* was spent and confirms queue saturation, but cannot answer the requested scan/extract/embed/write/checkpoint/publish cost split or provide valid query p95. It is a diagnostic, not the P1 native baseline.

### Retrieval-only evidence

The frozen [`zg-retrieval` protocol](../../benchmarks/zg-retrieval/README.md) already provides a defensible code-search quality contract: SWE-QA20, 20 questions over 11 repositories, three modes (hybrid/FTS/vector), five repetitions, local `potion-code-16m-v2`, and primary `ndcg.repository_macro.ndcg_at_10`. Its quality gate remains `report-only-until-a-reviewed-baseline-is-frozen`; latency is explicitly not a controlled speed comparison.

Offline validation: `TMPDIR="$PWD/.tmp" node --test benchmarks/zg-retrieval/test/*.test.mjs` — **178 passed, 0 failed**. This validates protocol/scoring code only; it does not produce search-quality or performance measurements.

A successful [retrieval-only workflow run `38342219806`](https://github.com/Cuiyus/zvec-grep/actions/runs/38342219806) (2026-10-13, Rust/local/all) ran all four suites, but its report/evidence artifacts use 14-day retention and are no longer available in the run's artifact list; only the 38.1 MB candidate package remains. Its combined check summary is unavailable through the current API. Consequently no retrievable numeric score from that run is used here. We did fetch the separate [SWE-QA-Bench e2e run `35206585943`](https://github.com/Cuiyus/zvec-grep/actions/runs/35206585943) artifact referenced by `source.lock.json` and verified its report hash (`090c51dba3195c7311fd94b8548ef9111a06a7b6cbbec3f6caa3d224a5e4b758`); that is agent-level answer/tool-use evidence, **not** the retrieval-only baseline. Likewise, the BrowseComp-Plus report is an e2e study, not a current Rust retrieval-quality result.

**P1-C conclusion:** the code-search suite and metric are frozen and its offline tests pass, but the prior retrieval report is unavailable and no current baseline score is verified. The SWE-QA20 suite is the recommended first workload; there is no need to run all four suites for the first baseline.

### Authorized local WIP package (not a benchmark)

- Source archive: `.tmp/phase1-wip-package-20261027T070707Z/source-tree.tar.gz`, SHA-256 `943fc8b6db19e447f457842725bfa32018417b42970b4dbae8a7bf73c8f5c964`; content snapshot ID `cbda2fc67efc63e7e1dd30f8e75d77cd9910fa75ce86c6da177c1c2b2aa0cf4c` (553 Rust-workspace/path-dependency files; other user research/private data intentionally excluded).
- Local npm package: `.tmp/phase1-wip-package-20261027T070707Z/zvec-zvec-grep-0.0.1.tgz`, Linux x64 GNU, 40,626,512 bytes, SHA-256 `767725ceeb1b4722508c16984806caeed5b613d0b5ad8ca6211c320f313acc23`. Full identity is in `.tmp/phase1-wip-package-20261027T070707Z/package-identity.json`.
- `npm ci` and `npm run pack:local` passed; Cargo release build completed in 14m30s with one job. Embedded binary/runtime checksums were independently verified. `node scripts/npm-local.mjs smoke --no-build` passed package install/version and direct-vs-server fixture parity.
- One existing compile warning remains: `DEFAULT_MAINTENANCE_THRESHOLD` is unused at `rust/crates/zg-engine/src/storage/store/mod.rs:172`. No benchmark queries or corpus indexing occurred during this package build/smoke; both later native attempts are detailed below. No model inference, remote dispatch, commit, or push occurred. The package smoke used only a README fixture.

## Owner-approved provisional acceptance and frozen native protocol

Quality and lifecycle efficiency remain separate baselines. The owner approved the following target and one-run scope in message #576; these are evaluation targets, not claims that current performance meets them:

- **End-to-end query p95 ≤300 ms**, including native store lock wait, retrieval and result hydration.
- **Watcher freshness ≤5 s**, measured from the runtime watcher receiving a normalized event batch until a successful query first reflects the mutation.
- First baseline: **native lifecycle only**. SWE-QA20 is deferred.
- Authorization history: operations #576, #1024, #1288, #1407, and #1524 each used their owner authorization; #1670 was consumed by v6, then the structured owner decision #1710 authorized exactly one fresh follow-up under v7. Both one-use ledgers are consumed. No ledger was altered or reused; no further baseline run is authorized by #1710.
- No production runtime/default changes, deploy, commit or push are authorized by this decision.

The workload/resource schedule is represented by protocol v6 in [`benchmarks/native-lifecycle/protocol.json`](../../benchmarks/native-lifecycle/protocol.json) (SHA-256 `a7c7c4e447627bf353b1628cba9b93fd269bb310d77945b473607c0f2211ad2c`), executed once under #1670. Earlier attempts remain bound to their protocol hashes in their manifests. Key bindings for v6:

- Candidate base HEAD `d3b2a95bb238bce47e2dcb87034e5600d9de56e0`; local WIP package SHA-256 `767725ce…13acc23`; launch manifest also binds the complete tracked diff and compiled test binary hashes. The measured implementation is local WIP, not a clean-HEAD or production-transport measurement.
- Frozen corpus: 64 Rust source files / 694,421 bytes at commit `34d75aa14c760d302e497831c7501e1a31400f2b`, manifest SHA-256 `7fbc9f95…832a3`.
- Trace: initial index → one-file update → four-file burst → rename → delete → 2 s idle → close/reopen → two repeat rescans.
- Query schedule: FTS over `**/*.rs`, one serial query every 250 ms, no queue/backfill, missed slots counted; nearest-rank p95 requires at least 20 successful samples and zero missed slots/failures/cap. Freshness has 250 ms sampling resolution and a 30 s per-event observation deadline.
- Containment for replacement v2: user systemd scope, 200% CPU, 1 GiB memory, no swap, 128 PIDs, 900 s timeout, 16 GB minimum free-space reserve, and 3 GiB minimum host `MemAvailable`; all generated files/TMPDIR stay in repository `.tmp`. No per-workspace quota is claimed. The first attempt used 512 MiB and hit that cap.

For the first attempt, the runner verified candidate/corpus/binary identities and host headroom, then consumed the #576 ledger immediately before launch. A separate harmless no-op scope verified the effective cgroup controls; its guard passed. The replacement uses a separate per-authorization ledger for #1024, preserving the first receipt.

### Authorized native attempt — failed; no baseline

Operation `p1-native-lifecycle-20261027t081854z-4258f847` (authorization message #576) launched at `2026-10-27T08:19:06Z` using the frozen protocol. The test failed after 6.9 s at `freshness.rs:1220`: runtime revision/job state could report idle just before the test-only `index_completion` timestamp was recorded. The panic was a harness synchronization race, not a measured product performance result. The run reached only the first single-file update; `native-baseline.json` was never written, query p95 and freshness results are absent, and the lifecycle trace did not complete.

Evidence retained under `.tmp/native-lifecycle-baseline-20261027/p1-native-lifecycle-20261027t081854z-4258f847/`:

- `runner-result.json`: `PARTIAL_OR_BLOCKED`, exit 101, no report; SHA-256 `6f5fce70…3df928`.
- `workload.log`: panic and `/usr/bin/time` output; SHA-256 `467dc412…953256`.
- `containment.jsonl`: 14 samples over 6.6 s, 13 with cgroup data; SHA-256 `be6f8ea3…ee9dad9`.
- `run-manifest.json`: frozen source/protocol/binary identities and command; SHA-256 `b7ae0c19…a0fb1`.
- Effective cgroup limits matched: CPU 200%, memory 512 MiB, swap 0, PIDs 128. Host/disk guards stayed above reserves (min `MemAvailable` 4,936,896,512 bytes / 4.60 GiB; min free space 16.52 GB). The memory peak reached the 512 MiB cap and `memory.events.max` reached 8,857, but OOM/kill counters stayed 0. This is containment evidence only, not an accepted resource or performance baseline.

The test helper now waits, with a 30 s bound, for completion of the specific watcher revision before reading its timestamp. `TMPDIR="$PWD/../.tmp" cargo test --locked --offline -p zg-daemon --lib --no-run` passed after this change (one existing dead-code warning). A separate prelaunch defect (tar directory entries were rejected during fixture staging) was fixed and verified offline before the first actual launch; its retained record is at `.tmp/native-lifecycle-baseline-20261027/p1-native-lifecycle-20261027t080726z-01e5ecd1/setup-failure.json` and consumed no authorization.

### Owner-authorized replacement v2 — stopped by host-memory reserve

Operation `p1-native-lifecycle-20261027t084040z-16cf3681` (#1024) launched with protocol SHA-256 `57a92156…c67afa`, the fixed harness, and a 1 GiB cgroup memory cap. The pre-exec guard passed, but after 16.8 s the monitored host `MemAvailable` fell to 2,986,024,960 bytes, below the frozen 3 GiB floor (by 235,200,512 bytes). The runner stopped the systemd scope as designed; this was a resource-gate stop, not an application OOM. The operation returned `PARTIAL_OR_BLOCKED` (runner code -15), produced no `native-baseline.json`, and yielded no accepted p95/freshness results. The per-authorization ledger for #1024 is consumed; do not retry or weaken the reserve automatically.

Evidence retained under `.tmp/native-lifecycle-baseline-20261027/p1-native-lifecycle-20261027t084040z-16cf3681/`:

- `runner-result.json`: `host_memavailable_breach:2986024960`; SHA-256 `a5d01c32…361eef1`.
- `workload.log`: verified cgroup guard and test start; SHA-256 `f84f0a4d…e1fdb73`.
- `containment.jsonl`: 34 samples over 16.8 s, 33 with cgroup data; SHA-256 `740d93e3…8349168`.
- `run-manifest.json`: protocol v2 and current source/binary identities; SHA-256 `5354ee24…49973320`.
- Disk remained above reserve (min 16,563,638,272 B). Cgroup memory peaked at 555,982,848 B of 1 GiB; PIDs peaked at 52/128; cgroup OOM/kill counters were 0. `MemAvailable` recovered to 3,353,722,880 B in the after-snapshot, but the minimum crossing remains a valid stop and does not qualify the run.

### Owner-authorized final attempt — stopped by disk reserve

Operation `p1-native-lifecycle-20261027t095443z-8bff3c86` (#1288) used the frozen protocol v3 and passed pre-exec containment. The filesystem had 16,019,968,000 bytes free at launch, just 19,968,000 bytes above the 16,000,000,000-byte reserve. The disk monitor observed a minimum of 15,993,958,400 bytes—6,041,600 bytes below the reserve—and stopped the systemd scope after the test process had started. Host `MemAvailable` stayed above its 3 GiB floor (minimum 3,998,826,496 bytes); cgroup memory peaked at 623,800,320 of 1 GiB, with zero OOM/kill events. This was a disk-resource guard stop, not an application failure. No test result or `native-baseline.json` was written, so the run has no query/freshness metrics and cannot be used as a baseline. Protocol policy says no automatic retry; authorization #1288 is consumed.

Preserved evidence under `.tmp/native-lifecycle-baseline-20261027/p1-native-lifecycle-20261027t095443z-8bff3c86/`: `runner-result.json` (SHA-256 `6e3c973a50ef380a96285002e260989ea17bc04cfc487aa34aab39fa9d27c05b`), `run-manifest.json` (`353a47b07f550bd6e0c65a0f94c5d58793ce4a76489fe596d770b9d3d223dac9`), `workload.log` (`bc8429c584c47076f80e6c082530f3c01111ae558b3a386d548a015b54a2f504`), and 47 containment samples (`containment.jsonl`, `9c8463f013b5daab7bf2861f318fc68af77de7d992463bd1d4e032247346d027`).

### Owner-authorized v4 run — test-only watcher-completion marker timeout

Operation `p1-native-lifecycle-20261027t101041z-4c376ca1` (#1407) used frozen protocol v4 (SHA-256 `1a2902df1b148a63c3e23748bea71bfd6acb1a5aded64a1e55e408ac8abbc903`) with a 1 GiB launch-only disk cushion above the 16 GB operational floor. All pre-exec checks passed. The test binary failed after 36.53 s (exit 101) at `freshness.rs:992`, called from the first single-file watcher update: `p1_index_completion` timed out waiting for its test-only completion marker. Source review found the marker was recorded by `on_index_succeeded` for manual indexing, but not by successful watcher jobs in `watch_loop`. This is a harness observability defect; the failed run does not establish that the watcher index failed or succeeded, and it yielded no accepted p95/freshness result. `native-baseline.json` was not written; runner status is `PARTIAL_OR_BLOCKED`, with no resource-stop reason.

Resource evidence shows the containment envelope remained intact: 72 samples, minimum free disk 31,462,510,592 B, minimum host `MemAvailable` 4,943,360,000 B, cgroup memory peak 596,795,392 B of 1 GiB, and zero OOM/kill events. The preflight/after snapshots show elevated I/O PSI (preflight `some avg10=4.60`, `avg60=3.80`; after `avg10=1.98`, `avg60=3.10`), retained as a measurement-quality caveat, not a formal stop condition. Evidence is preserved under `.tmp/native-lifecycle-baseline-20261027/p1-native-lifecycle-20261027t101041z-4c376ca1/`; `runner-result.json` SHA-256 `d5d054ec77f05beb04e49435f36b3a4ee99cd6f1257e05bae0e1fd978be2a816`, `workload.log` `078c12859f4ccdab369296d638a6148c03cfd18f39670e2018a9f7d97aec8695`, and `containment.jsonl` `b43dfcac3205e9acc1fdf3c79523a8c9e9781c20ac2e64ea5ab26d46325c7563`. Authorization #1407 is consumed; protocol v4 forbids a repeat.

After the operation, I changed only `#[cfg(test)]` instrumentation so a successful watcher job records its completion revision, then `rustfmt --check`, LSP diagnostics, and `cargo test --locked --offline -p zg-daemon --lib --no-run` passed. This post-run repair was not included in the measured source diff and was not benchmarked; a future measurement would need a new authorization and a protocol bound to the repaired candidate.

### Owner-authorized v5 run — test-only store-reopen failure

Operation `p1-native-lifecycle-20261027t102114z-31ff5740` (#1524) passed preflight/containment, then exited 101 after 16.49 s at `freshness.rs:1309` with `ZG.ENGINE.RESOURCE_BUSY: workspace storage is already open`. The test adapter's `reopen()` attempted `IndexStore::open` on the same path before closing its original lease. This is a harness lifecycle defect; the trace did not complete and produced no `native-baseline.json`, p95, or freshness result. No resource-stop reason was recorded.

Resource evidence remained within the frozen envelope: 33 telemetry samples; minimum free disk 31,177,555,968 B; minimum host `MemAvailable` 5,128,380,416 B; cgroup memory peak 680,296,448 B of 1 GiB, zero OOM/kill events, and peak PIDs 46/128. Evidence is preserved under `.tmp/native-lifecycle-baseline-20261027/p1-native-lifecycle-20261027t102114z-31ff5740/`: `runner-result.json` SHA-256 `6fded9fae1947181535a219eac75a59b989a7c1121956badb93cfdbf7edb9d4e`, `run-manifest.json` `c286f9ed088ba1126cc57b8cb06c1e26d2d94cf14ca2e93d87b7b518baf45d89`, `workload.log` `2e79e9f086acbc8ff8be04b47d9d64e510bd27da0b4d78372fea5b22ac5752db`, and `containment.jsonl` `30b1bdc9fb883a067bd54cac789d80c2c3a67f32f4f9af5b77f3895e44eded7f`.

After the failure, `NativeLifecycleTestWorkspace::reopen` was corrected to close/flush the old store lease under the exclusive storage guard before opening the same path. The daemon test harness compile passed; the focused regression `workspace_runtime::tests::freshness::native_lifecycle_workspace_reopens_closed_storage_and_preserves_index` passed (1/1), confirming the indexed fixture survives reopen. The initial v6 candidate was then updated with owner authorization #1670 (final protocol SHA-256 `a7c7c4e4…2211ad2c`), bound to tracked diff `f90c13c0…d115db5` and test binary `e58b5d61…3db0fc58`, and executed as documented below. The #1524 one-use ledger remained consumed; I did not bypass or reset it.

### Owner-authorized v6 baseline — full trace, query SLO inconclusive

Operation `p1-native-lifecycle-20261027t112116z-ecf82762` (#1670) completed the full frozen trace with exit 0. All pre-exec containment checks passed. The 44 successful query samples produced nearest-rank p95 **208,336 µs**, but the protocol correctly marks the query SLO **INCONCLUSIVE**: there was one missed scheduled slot, maximum schedule lag was **315,569 µs** (>250,000 µs), and one response took **564,070 µs**. Do not claim query-SLO PASS from the point estimate. Freshness passed for all four events: max watcher-observed-to-visible **425,027 µs** against 5,000,000 µs. Per-event visibility was 236,240 µs (single-file update), 425,027 µs (four-file burst), 310,590 µs (rename), and 234,089 µs (delete).

Descriptive trace values: initial index 3,791,203 µs; close/reopen 247,996 µs; two repeat rescans 41,083 µs total. The fixture is 64 Rust files, embedding is fixed/deterministic, and the report has no scan/extract/embed/write stage breakdown; these values are not a statistically qualified performance claim. Host I/O PSI was elevated before and after the run (pre `some/full avg10=5.32/5.10`, post `4.72/4.57`), a measurement-quality caveat but not a guard stop.

Containment evidence: 25 samples; min free disk 30,946,865,152 B; min host `MemAvailable` 6,440,714,240 B; cgroup memory peak 723,173,376/1,073,741,824 B; swap 0, peak PIDs 50/128, and OOM/kill counters 0. Evidence is under `.tmp/native-lifecycle-baseline-20261027/p1-native-lifecycle-20261027t112116z-ecf82762/`; `native-baseline.json` SHA-256 `9ae2014d26077e94d1823c48b93cdb74a300c94263ddee3108ad588431f03dc9`, `runner-result.json` `6ee8ade4ccc2a05db9e5f7cc1c8a566607906905be334975196db1d82ad97a4e`, `run-manifest.json` `83a01c82c2f54b98d4009a771f4e01f37421974c8a32731e1466adb97353c66a`, and `containment.jsonl` `a87eb7862de668b0e68a2564b68eedba611ac7431623a33a341ef25b988e4c14`.

The #1670 ledger was consumed by v6. After diagnosing its slow-query-driven missed slot, the owner approved exactly one follow-up via structured decision #1710. V7 preserved the same workload, query schedule, thresholds, and resource envelope under a new protocol identity. That one-use authorization is now consumed; do not run again under it.

### Owner-approved v7 follow-up — valid sample, both SLOs PASS

Operation `p1-native-lifecycle-20261027t114352z-181846d9` (#1710, structured owner approval) completed the full trace with exit 0. All pre-exec containment checks passed. Query sampling had **44 successful samples, zero failures, zero missed slots, and max schedule lag 229,325 µs** (below the 250,000 µs interval). Nearest-rank p95 was **181,592 µs** (181.592 ms), so query SLO status is **PASS** against 300,000 µs. One sample was 477,875 µs; this is above the threshold but does not change the p95 disposition under the frozen nearest-rank rule. Freshness status is **PASS** for all four events, max **385,027 µs** against 5,000,000 µs: update 268,755 µs; four-file burst 385,027 µs; rename 320,544 µs; delete 190,199 µs.

Descriptive trace: initial index 3,719,258 µs; close/reopen 250,380 µs; two repeat rescans 43,638 µs total. Watcher-observed-to-index-completion values were 1,390,383 µs (update), 1,495,340 µs (burst), 1,476,751 µs (rename), and 319,038 µs (delete). The trace does not decompose scan/extract/embed/write/checkpoint/finalize/publish, so these totals cannot identify a stage-level bottleneck.

Containment evidence: 24 samples; min free disk 30,554,157,056 B; min host `MemAvailable` 6,870,491,136 B; cgroup memory peak 722,833,408/1,073,741,824 B; swap 0; peak PIDs 49/128; OOM/kill counters 0. Host I/O PSI remained elevated (pre `some/full avg10=4.37/4.30`, post `4.37/4.04`), but no guard stopped the run. Evidence is under `.tmp/native-lifecycle-baseline-20261027/p1-native-lifecycle-20261027t114352z-181846d9/`: `native-baseline.json` SHA-256 `bbb86a68765b27b49e551fb7a76f0b3c161c0929f276b73e4ce9009d73455eb3`, `runner-result.json` `b9c87fdbd5fd6bf5e262c5791a28902e4016418c57391a52c9dc6e0b1c96cd21`, `run-manifest.json` `783fd36051198cb109bd32e7183002b9ce81066a51c80c15da806c4b177ff2d1`, `workload.log` `f4a1ea357d8a7b57fe5a24b24c17ba25bff4265970ba6b68a7680c7561c914f4`, and `containment.jsonl` `d404571736cb19f11a5bc2b052cce86e32079a224d019696f9ea6cd2f2bc7bd9`.

### P1 disposition and bottleneck implications

P1 gate is **PASS**: the frozen native workload completed under the reviewed resource envelope, and the v7 query/freshness SLO checks both passed. Evidence class remains `EXPLORATORY_SINGLE_RUN`, not statistical qualification. Do not pool v6 and v7: v6 had one 564,070 µs response that produced one missed slot and an INCONCLUSIVE query result; v7 had one 477,875 µs response but no missed slot and valid p95 sampling. Both show a slow tail sample, but per-query timestamps and lock/retrieval/hydration substage metrics are absent, so its cause cannot be attributed to writer lock wait, retrieval, or hydration. Freshness is comfortably below its 5 s target in this fixture; no stage-level evidence justifies a performance optimization candidate. Keep P2 correctness/maintenance work separate from performance tuning, and do not make a production claim from these exploratory runs.

## Work performed and not performed

- Performed: offline analysis of retained M4/native trace; SWE-QA20 protocol/artifact review; 178 offline retrieval-protocol tests; immutable local WIP package build/smoke test; froze native protocols v1–v7; compiled the test-only real-watcher/native-store harness; passed the focused close/reopen persistence regression (1/1); ran seven authorization-backed operations (#576 harness race, #1024 host-memory stop, #1288 disk stop, #1407 watcher-completion marker timeout, #1524 same-path store-reopen failure, #1670 completed trace with query SLO inconclusive, #1710 completed follow-up with query and freshness SLO PASS); repaired corpus staging, completion-marker instrumentation, and test-only close/reopen handling, with post-fix daemon test-harness compile PASS. Under separate owner direction, added a P2-A/B delete-marker recovery fix and targeted regression assertions; ran eight Step-2 maintenance/reopen/delete tests plus three daemon scheduler/coalescing tests (11 passed), then ran ignored `gate_k_optimize_failure_recoverable` under a 180 s timeout and 1 GiB cgroup (1 passed in 1.72 s: injected failure retained debt and retry cleared it/converged), and ran `a_new_recovery_epoch_cannot_be_cleared_by_an_older_full_refresh` under the same envelope (1 passed). The first Gate K wrapper invocation failed before test discovery because it ran Cargo from the repository root; the corrected invocation from `rust/` ran the test successfully. Follow-up review added durable intent before normal/staged incremental PK deletions and a reopen regression, which passed 1/1 against current source (`STEP2_PK_REOPEN pending=true pending_after=false optimize_before=0 optimize_after=1`); the staged replacement regression also passed 1/1. Evidence is in `.tmp/p2-correctness-20261027/`, including `.tmp/p2-correctness-20261027/p2-gate-k-optimize-failure-retry-20261027T115021Z-2cad2cac/test.log` (SHA-256 `f6a994f5615aed278dd04b5b85af3ea3ec21201d561e8b0e7ecc96510122a58f`), `.tmp/p2-correctness-20261027/p2-recovery-epoch-20261027T115242Z-c819592d/test.log` (SHA-256 `f63599c8088e2f447bd706a419a4193d1ded3b1d2499df23e22ce710f836f2a5), `.tmp/p2-correctness-20261027/p2-pk-delete-actual-test-20261027T121830Z-c75b2a8d/test.log` (SHA-256 `6a6eaa08f8c8fb42370fc0ba3782a39b9e9b9edd02b7bb092cc76c236580c4bf), and `.tmp/p2-correctness-20261027/p2-staged-replacement-verify-20261027T122406Z-e2f6becc/test.log` (SHA-256 `19d0531cf62c1f106bff01ec8322082b86512e5b8e80d96490aa010015a8c1c1).
- Not performed: statistical/production performance qualification; SWE-QA20 benchmark; model inference; benchmark dispatch; the other ignored Gate K tests; P2 performance analysis/tuning or any performance claim; deploy/default change; commit/push. The #1407 full-lifecycle operation was not repeated after its test-only instrumentation failure.
- Test/build scratch and operation evidence stay under repository `.tmp`; historical fetched artifacts are in ignored `.tmp/phase1-retrieval/`.

**P1 gate: PASS (exploratory baseline only).** V7 had valid query sampling and both SLO statuses PASS; v6 remains an inconclusive, separately reported run and was not pooled. #1710 authorized exactly one follow-up and its ledger is consumed. No further baseline run is authorized. P2-A/B correctness-only gate is PASS with 17 focused test results across separate runs; the latest incremental PK-delete recovery, staged replacement, and real partial-mutation/injected-result handling tests passed against current source; the same partial-delete regression also passed with the test-only native C-ABI interposer and without it. Other ignored Gate K cases remain unrun. Phase 2 retains the native baseline and selects no performance candidate; stage-level bottleneck attribution and statistical/production performance qualification remain unproven. No performance tuning, default/runtime change, deployment, commit, or push occurred.
