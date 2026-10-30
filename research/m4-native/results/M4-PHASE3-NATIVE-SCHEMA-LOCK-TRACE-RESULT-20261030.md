# Phase 3 native schema-lock owner trace — fresh1 result (2026-10-30)

## Outcome

**The one authorized diagnostic ran once and completed with caveats.** The test returned 0 (`1 passed`), but the runner status is `DIAGNOSTIC_COMPLETED_WITH_CAVEATS` / `COMPLETED_WITH_WORKSPACE_UNCERTAINTY`; `performance_eligible=false`. This identifies a strong lock owner for most of the reproduced stalls, but one >100 ms query wait remains unexplained. Phase 3 remains **PARTIAL**; this run does not qualify performance or authorize a fix, another run, or production changes.

## Main finding: `optimize_seal` owns the lock for most long waits

The new trace records **251** `Collection::query` total spans. **Four** exceeded 100 ms; the maximum was 502.958 ms. Calling-thread CPU for these four was only 0.232–0.278 ms. For each, almost all elapsed time was in `collection_query_stage:schema_handle_lock`; setup, query validation, segment lookup, and SQL execution were small.

| Query total | Query thread | Shared-lock wait | Matching same-object lock interval |
|---:|---:|---:|---|
| 230.995 ms | 10543 | 230.416 ms | `optimize_seal_exclusive_hold`, thread 9785; overlaps 230.405 ms (99.995%) |
| 210.666 ms | 9998 | 210.371 ms | `optimize_seal_exclusive_hold`, thread 9785; overlaps 210.198 ms (99.917%) |
| 183.575 ms | 11374 | 182.970 ms | `optimize_seal_exclusive_hold`, thread 9785; overlaps 182.958 ms (99.994%) |
| 502.958 ms | 9998 | 502.642 ms | `collection_flush_stage:schema_handle_lock_held`, thread 9785; overlaps 502.631 ms (99.998%); no exclusive wait/hold span overlaps |

For the first three rows, the query and exclusive-holder spans have the same collection object ID, distinct thread IDs, and near-complete interval overlap. Under the observed `schema_handle_mtx_` shared-vs-exclusive modes, this directly accounts for those waits: **`optimize_seal`'s exclusive critical section is the owner observed blocking those queries.** Its exclusive-hold spans had p50 87.960 ms and max 235.186 ms.

The fourth query is not attributed. Its shared-lock wait overlaps a flush's shared-lock hold, but the query also requests a shared lock. No instrumented exclusive waiter or holder on that object overlaps this wait, so the flush overlap alone does not explain the block. Two additional query waits of 55.105 ms and 36.086 ms likewise overlap only flush shared-hold spans. These three waits remain unresolved; do not label flush as their cause.

The trace also found **five `fetch_shared_wait` spans above 10 ms** (127.638, 76.505, 67.393, 54.930, and 53.906 ms). Each overlaps a same-object `optimize_seal_exclusive_hold` from thread 9785 by 99.85–99.97%. Thus the exclusive seal interval affects fetches as well as direct collection queries.

All recorded `query_unsafe_setup` spans were at most 0.010 ms; `query_validate` at most 0.017 ms. FTS recall-stage maxima were below 0.4 ms (`prepare` 0.396 ms, filter 0.294 ms, FTS search 0.123 ms, result fetch 0.183 ms, schema fetch 0.100 ms). The former pre-validation gap was therefore covered and was not the source of the long queries in this run.

## Evidence integrity and workload

- Operation: `m4-rust-lifecycle-pair-20261030t015522z-nquery-locktrace-fresh1`; native-only, one arm, no M4 comparison.
- Pinned binary: `zg_engine-28bb1915fe9f2135`, SHA-256 `c6b1b11effe9026c0a85fc0b262bc3056ab2b84252fe95350d17a44c41ff8f19`.
- Pinned v4 native library SHA-256: `65817813bacc710b29ea01d273ca604c0e1177b1e5aabf618604fe45c701a971`.
- Corpus: 64-file Rust pilot, 694,421 bytes, manifest SHA-256 `7fbc9f951a9b1f75388150a891c984f23dd72717a6ce59070f89fd9f3ad832a3`.
- Test stdout: 1 passed, 0 failed, 603 filtered out, 7.52 s. The runner recorded 246 accepted/started query slots, 15 dropped slots, and 0 query failures. These are diagnostic-workload facts only; no latency or performance comparison is claimed.
- Dedicated `native-profile.jsonl`: 3,076,038 bytes, 13,886 event lines, 13,886 parsed, 0 malformed and 0 invalid-clock records; SHA-256 `180698e7c8a1599ae751a7b44378b2017160e9219a46e7de6ce26652b430c8de`. Events were written separately from Rust stderr, so this trace has no timestamp-interleaving corruption.
- Reproducible join and overlap details: [`fresh1-analysis.json`](native-query-timeline-20261029/m4-rust-lifecycle-pair-20261030t015522z-nquery-locktrace-fresh1-analysis.json), SHA-256 `6fb459bf510de7414c8cd8b12b360d167f85440cd085d2bfe8e1b185612be426`. Raw operation result and receipt remain under `.work/m4-native-workspace-quota/m4-rust-lifecycle-pair-20261030t015522z-nquery-locktrace-fresh1/`.

## Resource and containment evidence

- Fresh no-launch preflight passed for the exact operation mode, native-only order, binary/library hashes, quota proof, and disk capacity. Immediately before launch, qgroup `0/259` remained at 53,837,824 B referenced / 113,934,336 B headroom against the 167,772,160 B cap; the 64 MiB reserve and 32 MiB workspace allowance were unchanged. Disk free was 18,431,315,968 B; the preflight required 18,286,108,864 B. No quota increase or cleanup was performed.
- During the run, 11 live qgroup samples were complete: peak referenced 83,963,904 B, minimum headroom 83,808,256 B, above the 67,108,864 B reserve. Postflight qgroup sampling passed (81,346,560 B referenced; 86,425,600 B headroom). A fresh post-run query at 2026-10-30T02:02:57Z showed 57,925,632 B referenced / 109,846,528 B headroom. `0/258` was unchanged. Post-run disk free was 18,424,922,112 B.
- Watchdog: 11 samples, no abort; process-tree sampling complete. Cgroup memory peak was 471,957,504 B of the 536,870,912 B limit; swap peak and OOM counters were zero. Process-tree maxima were RSS 481,392 KiB, PSS 467,566 KiB, swap 0. The cgroup I/O controller was unavailable.
- At arm start, host `MemAvailable` was 3,345,168 KiB, below the historical 5,477,762 KiB observation threshold; that comparison is explicitly observation-only and did not block or abort. Host load-1 was 2.50 and I/O PSI full avg10 was 1.19, both below their observational thresholds. Host/process before/after snapshots are retained alongside the gate files.
- Workspace inventory samples were incomplete/unknown for all 11 watchdog samples. Although the postflight traversal completed and the hard qgroup capacity remained within bounds, no atomic inventory or verified lifetime soft-budget peak exists. This is why the runner retained `COMPLETED_WITH_WORKSPACE_UNCERTAINTY`, not PASS. The transient systemd unit was verified collected; its stop command returned 5, while final unit result and workload return code were 0.

## Decision and next engineering direction

The main demonstrated blocker is **excessive exclusive-lock hold time in `optimize_seal`**. A reasonable research follow-up is to inspect whether sealing work can be moved outside the exclusive schema-lock critical section, preserving publication/schema invariants, and validate correctness before any new measurement. Do not implement that change speculatively under this trace authorization.

The three query waits that overlap only a flush shared hold still need explanation; the present trace contains no overlapping exclusive span for them. The single fresh-run authorization is now consumed. Any code change or additional trace requires a new owner instruction. No M4 arm, pair comparison, performance claim, or production action was performed.