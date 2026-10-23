# M4 `Index::Open` attribution and analyzer-cache candidate

**Current disposition (2026-10-23):** The historical decision below kept reuse opt-in. Under the autonomous immutable-shard optimization mission, the same verified, weak-reference-only candidate is now enabled by default in standalone M4 (`Options::reuse_analyzer_cache=true`). Its correctness and lifecycle suite passed under the new default. Production remains out of scope; the paired measurements and their reader-tail uncertainty below remain unchanged.

Date: 2026-10-18 (measurement record; current disposition updated 2026-10-23)

Scope: standalone `research/m4-native/` only. No production/native HNSW code, schema, or index format changed.

## Question and attribution method

The previous B3 harness reported roughly 108 ms writer-thread CPU per `m4::Index::Open`. The timed call in `tests/m4_bridge_integration.cc` encloses only `m4::Index::Open`; native Zvec collection/HNSW setup is outside that call. Added optional `OpenDiagnostics` to time the Open phases using both steady-clock wall time and `CLOCK_THREAD_CPUTIME_ID`; M4 stages include analyzer fingerprint, analyzer initialization and its verification fingerprint, segment opens, tombstones, stats, WAL replay, live-key map, and garbage collection. The B3 benchmark records per-round values and the adjacent native-vector build phase separately. The measurement did not infer Open cost from total commit time.

Resource-gated 500-doc × 20-round B3 runs used four concurrent readers and 80 queries/arm. `Index::Open` stage p50 on the no-cache baseline:

| Stage | CPU / wall p50 |
|---|---:|
| Total M4 `Index::Open` (outer writer-thread CPU) | 93.1 ms |
| Initial analyzer fingerprint (SHA-256 of configured dictionary/model) | 24.8 ms CPU |
| Native Jieba analyzer initialization | 46.0 ms CPU |
| Post-init fingerprint verification | 20.7 ms CPU |
| Segment open/load (7 segments, ~174 KB at median) | 0.376 ms wall |
| Stats checkpoint/log load | 0.713 ms wall |
| WAL replay | 0.003 ms wall |
| Live-key map rebuild | 0.023 ms wall |
| Garbage collection | 0.029 ms wall |

The analyzer stages account for about 91.5 ms of the 93.1 ms measured Open CPU. Segment loading is under 0.5 ms despite 1–10 runs during this workload. Therefore **cached immutable segment handles are not the relevant optimization**. The 108 ms was M4 analyzer identity/tokenizer work, specifically Jieba initialization plus two fingerprint passes—not HNSW initialization or M4 segment loading. The native-vector build phase was independently around 31–35 ms CPU p50 and did not move consistently with the analyzer cache; it is not part of the M4 `Index::Open` timer.

## Candidate

Implemented opt-in `Options::reuse_analyzer_cache` (default OFF). M4 still computes and verifies the manifest analyzer fingerprint on each open. On a matching fingerprint, indexes in the same process share the immutable `Analyzer::Impl` (native Jieba + lowercase filter) by `shared_ptr`; the registry itself retains only `weak_ptr`s. A pinned old generation therefore owns its analyzer for exactly its own lifetime; dropping generations releases it normally. This cache does not retain segments, change run/file ownership, alter WAL/manifest/recovery order, or persist state. The native Jieba tokenizer documents concurrent `tokenize()` safety. Cache reuse is recorded in diagnostics, and the candidate correctness test requires a hit on the incremental open.

This deliberately leaves both fingerprint passes in place to preserve the existing dictionary-change-during-initialization check. It removes analyzer reconstruction while retaining manifest identity validation and TOCTOU verification.

## Candidate benchmark

Two sequential 500×20 B3 control/cache pairs were measured, with the second pair in reverse arm order using the same current binary. Both pairs had 20/20 expected analyzer-cache hits in the cache arm and zero coherence errors. Resource gates passed.

| Metric | Pair 1: no cache → cache | Pair 2: cache → no cache |
|---|---:|---:|
| Whole-process CPU | 4.282 → 2.688 s (−37.2%) | 2.579 → 4.489 s (cache −42.5%) |
| Writer commit-thread CPU | 3.600 → 1.972 s (−45.2%) | 1.886 → 3.757 s (cache −49.8%) |
| Commit p95 | 328.4 → 211.5 ms (−35.6%) | 220.5 → 314.2 ms (cache −29.8%) |
| M4 `Index::Open` writer CPU p50 | 93.1 → 47.1 ms (−49.4%) | 47.7 → 101.3 ms (cache −52.9%) |
| Jieba analyzer-init CPU p50 | 46.0 → 0.002 ms | 0.002 → 52.3 ms |
| Peak RSS | 292.5 → 216.8 MiB | 217.4 → 292.3 MiB |

Across the two pairs, ratio-of-sums decreases were **40.0% whole-process CPU, 47.6% writer-thread CPU, 32.8% commit p95, and 51.2% M4 Open CPU p50**. The result is consistent with the measured removal of ~46–52 ms analyzer initialization per Open; hashing and segment-open costs remain essentially unchanged. The native-vector build CPU did not exhibit a consistent improvement.

Reader p95 varied in direction between pairs (2.49→3.95 ms in pair 1, 3.14→2.42 ms in pair 2), so this sample does **not** establish a reader-latency effect. Process physical-write counters also varied (225.0→226.6 MB and 227.3→208.8 MB) and are not attributed to this in-memory cache. Treat this as a bounded prototype result, not a general workload guarantee.

Host receipts, commands, raw JSON, `/usr/bin/time -v`, logs and hashes are in `results/m4-b2-cost-model-20261018/paired-benchmark/open-attribution/`. The same-source reverse-order pair used binary SHA-256 `3766ef8af7dfe1b13e003cb3935f2756fb0973f6e8afda6f9fd6aec8ef1fb53c`; per-source hashes are in `reverse-source-binary.sha256`.

## Verification

- CMake build: `/tmp/m4-p4-build`, target `m4_bridge_integration` — PASS.
- M4 correctness oracle: `checks=18157 failures=0` — PASS.
- Native Jieba compatibility CTest: `1/1 passed` (`jieba-compatibility.stdout`) — PASS.
- Incremental M4 cache/recovery candidate: `checks=578 failures=0`, 2 changed upserts, 3 mutations, 1 shared run — PASS. It checks old pinned readers, differential ranking/filter results, published-generation reopen, and recovery.
- ASan/UBSan/LSan candidate: `checks=578 failures=0`, `detect_leaks=1:abort_on_error=1`, `halt_on_error=1`, stderr empty — PASS.
- Two 500×20 B3 benchmark pairs: zero coherence errors — PASS; single-host descriptive evidence only.

## Decision and next step

Keep the analyzer cache opt-in and research-only. Do not add a segment-handle cache: it targets <0.5 ms in this measured profile. The measured candidate substantially reduces repeated B3 open CPU without changing persisted ownership or recovery, but reader tail and broader workload impact remain open. Any future default-on or production adoption requires a separate authorization and broader lifecycle/concurrency review.

Exact next action: retain this candidate for the M4 research branch; continue the separately queued usage-first scalar/filter compatibility mapping. No production integration or migration is authorized.

---

## Reproduction commands

Initial baseline / candidate commands (exact invocations, including dictionary path, are archived in the adjacent `.command.txt` files):

```sh
# Initial no-cache baseline used the pre-cache binary, where cache was not yet an option:
/tmp/m4-p4-build/m4_bridge_integration /var/tmp/m4-open-attribution-basecpu-500x20-20261018 <jieba-dict> --incremental-fts-sustained 500 20
# Candidate enables the opt-in cache:
/tmp/m4-p4-build/m4_bridge_integration /var/tmp/m4-open-attribution-cache-500x20-20261018 <jieba-dict> --incremental-fts-sustained 500 20
```

The reverse-order same-binary repeat and exact commands are archived in `paired-benchmark/open-attribution/reverse-*.command.txt`; its no-cache control uses `--incremental-fts-sustained-no-analyzer-cache`.

ASan/UBSan candidate command and source/binary hashes are archived in `paired-benchmark/open-attribution/cache-candidate-asan.command.txt` and `cache-candidate-asan-source-binary.sha256`.

(The initial baseline binary predates the no-cache CLI alias; it ran the same B3 control path with the default-off cache option. The reverse-order repeat uses the explicit no-cache alias.)

## Host-state receipts

- Initial attribution: `host-before-basecpu.json`, `host-after-basecpu.json` (load1 2.12→2.19, MemAvailable 6.89 GiB, full-I/O PSI avg10 0.43→1.77).
- First cache benchmark: `host-before-cache.json`, `host-after-cache.json` (load1 2.24→2.22, MemAvailable 6.90→6.94 GiB, full-I/O PSI avg10 1.29→1.88).
- Reverse-order pair: `host-before-reverse.json`, `host-after-reverse-cache.json`, `host-after-reverse-control.json` (load1 3.51→3.71→3.81, MemAvailable 5.20→5.00→5.35 GiB, full-I/O PSI avg10 0.91→1.93→2.68).
- All recorded resource gates passed (load1 <12, MemAvailable >3 GiB, full-I/O PSI avg10 <5, `/var/tmp` free >10 GiB).

The 108 ms source-state measurement is not promoted as an invariant; these attribution runs show 93–101 ms no-cache Open CPU p50 on the current harness and roughly 47–48 ms with reuse.

Note: The no-cache command shown above is available in the current binary; archived baseline artifacts preserve the exact command used for each run.

Verification outcome: PASS for the scoped research candidate and its tests; production status remains NOT APPROVED.

<!-- Correctness sources and timings remain in the adjacent raw artifacts; do not replace them with this summary. -->

## Artifact index

- `basecpu-500x20.stdout`, `cache-500x20.stdout`, `reverse-cache-500x20.stdout`, `reverse-control-500x20.stdout`
- Corresponding `.time.txt`, `.stderr`, `.command.txt`, resource snapshots, and source/binary hash receipts
- Final normal correctness: `cache-candidate-final-correctness.stdout` (`checks=578 failures=0`); earlier pre-assert smoke: `cache-candidate-correctness.stdout` (`checks=577 failures=0`)
- ASan/UBSan/LSan: `cache-candidate-asan.stdout` (`checks=578 failures=0`) and zero-byte `cache-candidate-asan.stderr`
- `/var/tmp/m4-open-attribution-basecpu-500x20-20261018`
- `/var/tmp/m4-open-attribution-cache-500x20-20261018`
- `/var/tmp/m4-open-cache-reverse-cache-500x20-20261018`
- `/var/tmp/m4-open-cache-reverse-control-500x20-20261018`

Do not remove these `/var/tmp` run roots as part of this slice.

---

The report contains the exact measurement and implementation decision; no inference about unmeasured HNSW initialization or physical-write causality is intended.

Progress: candidate implementation, correctness/recovery, sanitizer, and paired benchmark complete.
