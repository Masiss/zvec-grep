# M4 incremental index — evidence (2026-10-17)

This file keeps the historical S2 evidence (streaming compaction via temp
sections), the S3 slice (direct v2 writes, no temp-section copy), the S4
durability/delete-heavy/scaling validation, and the S5 debt-policy experiment.

Single run per case, each in its own process (clean `VmHWM`), same fixture and
toolchain (`g++ 16.2.1`, `-O2`). S2/S3 used `/tmp` tmpfs; S4 used `/var/tmp`
btrfs and reports `/proc/self/io` physical bytes. The older
`total_phys_write_bytes` field in S2/S3 JSON is the prototype's logical write
counter, not a disk-device counter. Host was under variable background load
(observed load ~6–12, MemAvailable ~0.5–5.7 GB); absolute latency is
directional.

Commands:

```sh
# pre: in-memory merge buffer
/tmp/m4_bench_pre_final2 --one <mode> <docs> <updates>
# post: streaming k-way merge
/tmp/m4_bench_post_final2 --one <mode> <docs> <updates>
```

Raw JSON: `benchmark-20261017-pre.jsonl`, `benchmark-20261017-post.jsonl`.
The pre binary was built from a snapshot taken immediately before the streaming
change, with the same telemetry and benchmark harness. The older
`benchmark-20261017-initial-inmemory.jsonl` is the previous gate's artifact and
is not used for this comparison. Environment: `environment.txt`.

## Incremental workload (12 updates; build is separate)

| docs | mode | build wall | update p50 | update CPU total | compaction wall (total) | writes | disk | peak RSS | peak PSS | compaction tracked peak | query p95 | runs |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10k | in-memory | 0.64 s | 0.351 ms | 6.1 ms | 0.37 s | 17.0 MB | 5.8 MB | 94.8 MB | 88.5 MB | 19.0 MB | 0.41 ms | 7 |
| 10k | streaming | 0.66 s | 0.388 ms | 12.6 ms | 0.25 s | 22.1 MB | 5.8 MB | 35.6 MB | 30.0 MB | 8.8 MB | 0.43 ms | 7 |
| 50k | in-memory | 4.40 s | 0.342 ms | 6.0 ms | 3.25 s | 111.5 MB | 15.1 MB | 421.0 MB | 412.0 MB | 94.3 MB | 1.70 ms | 7 |
| 50k | streaming | 4.97 s | 0.347 ms | 10.5 ms | 2.71 s | 163.6 MB | 15.1 MB | 95.3 MB | 89.5 MB | 8.8 MB | 2.01 ms | 7 |
| 150k | in-memory | 18.61 s | 0.227 ms | 3.7 ms | 14.17 s | 424.3 MB | 40.7 MB | 1196.5 MB | 1190.0 MB | 282.5 MB | 3.36 ms | 7 |
| 150k | streaming | 18.34 s | 0.238 ms | 5.6 ms | 11.22 s | 670.3 MB | 40.7 MB | 241.7 MB | 230.1 MB | 8.8 MB | 3.46 ms | 7 |
| 300k | in-memory | 80.07 s | 0.357 ms | 9.6 ms | 74.52 s | 1174.8 MB | 81.4 MB | 2345.5 MB | 2328.2 MB | 565.0 MB | 9.98 ms | 7 |
| 300k | streaming | 47.06 s | 0.425 ms | 16.0 ms | 36.43 s | 1992.7 MB | 81.4 MB | 464.8 MB | 459.5 MB | 8.8 MB | 11.98 ms | 7 |

Peak PSS reduction: **2.95× / 4.60× / 5.17× / 5.07×** at 10k / 50k / 150k /
300k. Compaction working-set reduction (in-memory estimate vs streaming
tracked budget): **2.2× / 10.7× / 32× / 64×**. Update p50 remains sub-ms at
every size. Query p95 is comparable at 10k–150k and +20% at 300k.

Costs:

- Logical writes rise **1.30× / 1.47× / 1.58× / 1.70×** because streamed
  dict/meta/payload/docs are written to temp files and then copied into the
  final segment. This is bounded (<2×) but is the main follow-up.
- Build wall is equal at 10k–150k and **41% lower** at 300k (the in-memory
  baseline was under memory pressure). Compaction wall is lower at 10k–300k
  (0.66× / 0.84× / 0.79× / 0.49×).
- Final storage and run count are unchanged (7 runs, same bytes).

## S3: direct v2 writes vs S2 temp-section streaming

Paired `--one incremental` runs in separate processes, same fixture and
toolchain. `/tmp` is tmpfs, so `prototype writes` is the logical counter
(segment + metadata bytes), not a disk-device counter. The 150k pair was run
again in reversed order because the first S3 run overlapped heavy host load;
the table uses the reversed-order pair. Absolute wall time remains directional.

| docs | mode | build wall | compaction wall (total) | prototype writes | disk | peak PSS | compaction tracked peak | query p95 | runs |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10k | S2 temp | 0.517 s | 0.216 s | 22.1 MB | 5.80 MB | 31.0 MB | 8.80 MB | 0.384 ms | 7 |
| 10k | S3 direct | 0.673 s | 0.226 s | 17.1 MB | 5.83 MB | 31.0 MB | 4.57 MB | 0.362 ms | 7 |
| 50k | S2 temp | 4.398 s | 2.529 s | 163.6 MB | 15.10 MB | 89.6 MB | 8.80 MB | 2.030 ms | 7 |
| 50k | S3 direct | 4.698 s | 2.497 s | 112.3 MB | 15.13 MB | 90.2 MB | 4.59 MB | 1.883 ms | 7 |
| 150k | S2 temp | 19.400 s | 12.298 s | 670.3 MB | 40.72 MB | 231.8 MB | 8.80 MB | 3.922 ms | 7 |
| 150k | S3 direct | 18.469 s | 12.288 s | 427.1 MB | 40.75 MB | 238.3 MB | 4.60 MB | 5.332 ms | 7 |
| 300k | S2 temp | 50.956 s | 37.402 s | 1992.7 MB | 81.40 MB | 455.1 MB | 8.80 MB | 10.212 ms | 7 |
| 300k | S3 direct | 52.772 s | 37.666 s | 1180.9 MB | 81.43 MB | 457.7 MB | 4.61 MB | 9.416 ms | 7 |

Write reduction: **23% / 31% / 36% / 41%** at 10k / 50k / 150k / 300k.
Compaction wall ratio S3/S2: **1.05× / 0.99× / 1.00× / 1.01×**. Build wall
ratio: **1.30× / 1.07× / 0.95× / 1.04×**; the 10k regression is +156 ms of
fixed overhead (dictionary/final header), not a scaling slope. Peak PSS is
effectively unchanged (direct writes reduce write I/O, not the live index
working set); the tracked compaction working set is ~4.6 MB versus 8.8 MB.
Final storage is unchanged. Query p95 is −6% / −7% / +36% / −8%; 150k is the
noisiest case (repeated runs varied by >30% in both directions), so it is not
treated as a demonstrated regression. At 300k, S3 writes 41% fewer bytes at
essentially the same wall time and peak PSS.

The physical-write check uses `/var/tmp` (btrfs) and `/proc/self/io`; tmpfs
cannot expose physical writes. Physical bytes (build + 12 updates, including
filesystem/CoW effects) were **241.0 MB vs 162.7 MB** at 50k and **911.7 MB vs
573.7 MB** at 150k (S2 vs S3), i.e. −32% / −37%, consistent with the logical
counter. Btrfs wall time is noisier because writeback dominates; the tmpfs
table is the latency comparison. Raw JSON: `benchmark-20261017-s3.jsonl`.

## Controlled rebuild-on-mutation baseline

| docs | mode | update p50 | update CPU total | compaction wall | writes | peak PSS | query p95 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 10k | in-memory | 237.7 ms | 2025 ms | 2.19 s | 39.9 MB | 91.9 MB | 0.33 ms |
| 10k | streaming | 121.9 ms | 1009 ms | 1.22 s | 67.9 MB | 32.1 MB | 0.40 ms |
| 50k | in-memory | 1081.2 ms | 11827 ms | 12.94 s | 220.1 MB | 478.9 MB | 1.41 ms |
| 50k | streaming | 431.3 ms | 3595 ms | 5.51 s | 380.9 MB | 91.8 MB | 1.24 ms |

Streaming is **2.0–3.3× lower CPU** on the O(collection) baseline and **2.9–5.2×
lower peak PSS**, at the same ~1.7× write cost. This is the strongest evidence
that the merge path itself is the improvement, not fixture noise.

## Correctness / safety

- Independent full-scan oracle: **11,536 checks, 0 failures** (expanded in
  S4; the S3-era gate was 6,290).
- ASan + UBSan: **clean** (11,536 checks, 0 failures).
- Covered: OR/AND/phrase FTS, scalar EQ/range, randomized updates/deletes,
  flush/compaction/reopen, WAL replay after destruction without flush,
  bounded run count, corpus-growth stale-bound regression, hybrid ranking,
  and a forced compaction-memory-budget failure that leaves the old runs
  searchable.
- Streaming output is read back by the same `Segment::Open` and rechecked by
  every reopen/compaction oracle test.
- Injected faults at three boundaries: after segment rename (before publish),
  before manifest publish, and after durable manifest publish. The first two
  leave the old runs searchable; the third is recovered by reopening and
  seeing the new run.

## S4: delete-heavy churn and scaling (2026-10-17)

Workload: stable live cardinality, 8 cycles, 6,000 ops/cycle (2,000
delete+replacement-insert pairs, 4,000 updates), all docs contain a `common`
term and `status=active`. `/var/tmp` (btrfs), real filesystem, one process per
case. `fsync_wal=false` for this benchmark; WAL/durability uses the separate
correctness gate with fsync on. `query p95` is the last churn cycle before the
final drain; `post p95` is measured after `CompactNow`.

| docs | build wall / CPU | churn wall / CPU | total CPU | max dead | runs max | compactions | tracked peak | churn logical / physical | total logical / physical | final disk | peak PSS | reopen | p95 last / post | p99 last / post |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 500k | 61.9 s / 73.3 s | 50.8 s / 65.6 s | 138.9 s | 47,085 | 6 | 55 | 4.65 MB | 234.2 / 253.0 MB | 1,279.2 / 1,480.0 MB | 137.8 MB | 624.5 MB | 0.80 s | 70.3 / 30.6 ms | 73.8 / 30.8 ms |
| 1M | 151.9 s / 181.3 s | 87.8 s / 118.6 s | 299.9 s | 47,727 | 6 | 116 | 4.74 MB | 372.0 / 390.1 MB | 2,804.3 / 3,186.6 MB | 275.4 MB | 1,205 MB | 0.82 s | 168.4 / 37.4 ms | 209.1 / 37.9 ms |

Host headroom before each run was ~4.0 GiB available (load ~9); the 1M process
peaked at 1,205 MB PSS with ~30 GiB free on `/var/tmp`, so 1M was run after
500k passed. Both cases ended with `live_docs` exactly equal to the live set,
`dead_docs=0`, one run, no temp/orphan files, and no WAL files after the final
flush. Query p95/p99 rose 2.3× (500k) and 4.5× (1M) while tombstones accumulated to 47k,
then fell back to 30.6/37.4 ms after the final compaction. This is visible
read amplification from tombstone filtering plus run fan-out; the run cap and
auto-compaction kept both bounded. There was no correctness or liveness
mismatch. Final storage is ~275 B per live doc at both sizes; process PSS is
~1.3 KB/live doc at 500k and ~1.2 KB/live doc at 1M.

Correctness/recovery in the same tree:

- Oracle suite: **11,536 checks, 0 failures**; ASan/UBSan clean.
- Added delete-heavy churn oracle: updates, deletes, replacement inserts,
  reinsert of deleted keys, exact `common` count, live-token equality,
  FTS/INVERT/range/phrase/hybrid checks after repeated compactions.
- Added WAL replay after churn (mutations without flush), pre-manifest fault
  after churn, `dead_docs=0` and one-run GC checks, and no-temp-file check.
- Existing fault tests cover after-rename, before-manifest and after-manifest
  boundaries; manifest fallback and tombstone GC remain green.

Limitations of this evidence:

- `fsync_wal=false` in the large churn benchmark; durability is validated by
  the smaller fsync-on correctness test, not by the 1M timing run.
- Physical writes include btrfs CoW/metadata and base compaction; churn-only
  and total columns are both reported. Final storage is 137.8 MB / 275.4 MB;
  total physical writes are 1.48 GB / 3.19 GB because the base build performs
  repeated tiered merges before the final live-set drain.
- Peak PSS includes the benchmark's live-key vector and full-count liveness
  query, not just the index. The tracked compaction budget is separate
  (4.65/4.74 MB).
- Query latency under debt is a policy/performance issue, not a correctness
  failure. A production SLA would need a compaction-debt trigger tuned for
  latency, not only run count/tombstone ratio.

## S5: compaction debt policy and query latency (2026-10-17)

`QueryDiagnostics` now records unique runs read, term×run parts, postings
scanned, dead postings filtered, liveness lookups, live candidates and
setup/execution/finalize timing. The policy is research-only and **default
off**; it runs only after `Flush`, never per mutation, and keeps format v2,
BM25, shared liveness, WAL/manifest and search semantics unchanged.

Policy: one adjacent-pair merge per flush with the highest
`dead_postings / rewritten_docs`, minimum 3 runs, 1 s cooldown, and input cap
`live_docs/4`. The base corpus is built with the policy **off**; ON runs
reopen after the base compaction so bulk-load behavior is not compared.

| case | mean p95 | median p95 | max p95 | post p95 | mean runs | mean scans | mean dead | churn merges | logical writes | physical writes | CPU | peak PSS | update p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 500k OFF | 51.8 ms | 53.8 ms | 69.7 ms | 20.8 ms | 4.38 | 146k | 5,170 | 3 | 234.2 MB | 252.4 MB | 96.2 s | 617.6 MB | 0.077 ms |
| 500k ON | 31.3 ms | 31.4 ms | 38.9 ms | 24.7 ms | 2.00 | 124k | 3,957 | 7 | 281.0 MB | 300.5 MB | 73.9 s | 600.4 MB | 0.062 ms |
| 1M OFF | 186.0 ms | 187.4 ms | 236.6 ms | 60.7 ms | 4.38 | 276k | 5,044 | 3 | 372.0 MB | 391.3 MB | 411.1 s | 1,240.2 MB | 0.206 ms |
| 1M ON | 73.8 ms | 72.4 ms | 89.6 ms | 54.3 ms | 2.00 | 238k | 3,852 | 7 | 419.5 MB | 439.0 MB | 226.5 s | 1,127.6 MB | 0.082 ms |

Mean query p95 falls **40% at 500k** and **60% at 1M**; max p95 falls 44%/62%.
Runs stay at 2, scans fall 15%/14%, and dead postings fall 23%/24%. Setup and
finalize are negligible; at 1M OFF execution averaged 95.4 ms, while ON query
execution fell to 54.1 ms. Runs read and dead-posting fraction both correlate
with p95 (`r≈0.86` / `r≈0.84`), so tombstone ratio alone is not the signal.

Trade-off: churn logical writes rise **20% at 500k / 12.7% at 1M**, physical
writes **19% / 12.2%**; update p95 does not regress in these runs. Post-drain
p95 was 20.8→24.7 ms at 500k and 60.7→54.3 ms at 1M, so it is not uniformly
better; the churn-window distribution is the decision metric. The policy keeps
the tracked compaction peak at 4.74 MB. Churn maintenance merges are
3→7 (one bounded merge per affected cycle), not a full compaction per latency
spike; total CPU falls because queries scan fewer runs/dead postings. Final
storage is unchanged (137.8/275.4 MB).

**S5 verdict: GO for research policy.** The structural policy materially
reduces the latency tail at a bounded 13–20% write cost and lower CPU, without
changing format or search semantics. Production remains **NO-GO**: the
prototype is single-threaded/one FTS field, and policy tuning beyond this
single A/B is not approved.

## Verdict

**GO for the S5 research policy** (and S4 remains GO for research): mean p95
falls 40%/60% at 500k/1M, max p95 44%/62%, with 13–20% more churn writes and
lower CPU. **NO-GO for production integration or migration**: the format is
research-only and the prototype is single-threaded with one FTS field; no
further policy tuning is approved without a new experiment.

Follow-up, in order:

1. Stop policy tuning; the S5 policy is the accepted research point.
2. Multi-field/tokenizer parity and background compaction, with the same
   oracle/WAL/fault gates.
3. Production migration remains unapproved.

## M4-P4: isolated Native Zvec vs M4 FTS/INVERT A/B (2026-10-17)

Three paired 1,200-file runs on `/var/tmp` btrfs completed in isolated fresh
index directories. On the retained workload, snapshots matched and both arms
had zero independent-oracle retrieval mismatches and zero cross-engine
retrieval-set mismatches. Median M4 whole-process CPU was 4.72 s vs 6.63 s
(native), peak RSS/PSS was 277/270 MiB vs 564/556 MiB, and final index space
was 7.24 vs 36.3–37.4 MB. Trade-offs: M4 wall time +25%, update/delete p95
slower, and physical writes 527.5 vs 101.2 MB (5.22×); timed search CPU was
0.155 vs 1.401 s. Six top-10 order cases differed (mean Jaccard 0.9943).

**P4 verdict: PARTIAL, research-only.** A small smoke exposed native-only
extra hits for `"json parse"` against the independent analyzer oracle/M4.
That exact query was removed from both full-run arms and replaced by
`"json request"`; the finding is retained and not counted as passing
compatibility. Further investigation or behavior changes require separate
approval. Do not infer whole-`zg` performance and do not integrate/migrate to
production. Full metrics, provenance, host state and raw artifacts are in
`m4-p4-native-vs-m4-20261017.md`, its sibling provenance/checksums, and
`results/artifacts/m4-p4-*.tar.gz`.

## M4-P4.1: write-amplification attribution (2026-10-18)

One 60-file traced diagnostic matched 1,088 live docs / 967,119 tokens. M4
had 1,588 WAL fsyncs (one per mutation) vs 4 native WAL fsyncs (four explicit
flushes), while WAL syscall bytes were nearly equal (4.49 vs 4.44 MB). Process
physical writes were 132.41 vs 35.79 MB (3.70×), but successful writes to
index files totaled less for M4 (8.74 vs 16.21 MB). This strongly points to
sync granularity/filesystem writeback as the primary cause; the exact physical
byte share is unproven. The 5.22× P4 full-run result was not rerun here.

Durability boundaries differed (`fsync_wal=true` for M4; native WAL sync at
flush), and final maintenance states were not equivalent/proven: M4 ended with
2 runs / 0 dead docs; native pending compaction debt was unavailable. No engine,
format, or durability behavior changed. Diagnostic markers are now opt-in and
default-off; a 60-file default-off run confirmed no extra output. Detailed
evidence, limits and the format-preserving batch recommendation:
`m4-p4.1-write-amplification-20261018.md`;
raw traces and host snapshots: `artifacts/m4-p4.1-writeamp-60-20261018.tar.gz`.
P4 remains PARTIAL, research-only; production integration/migration remain
unapproved.

## M4 Marathon — P4.2 durable atomic batch and Branch A (2026-10-18)

P4.2 adds `ApplyBatch` only under `research/m4-native/`: max 256 mutations / 8 MiB, one checksummed WAL transaction, fsync before visibility when enabled, and locked all-or-nothing reader visibility. Existing per-document APIs are unchanged. Current strict correctness and ASan+UBSan/leak-detection gates each pass `checks=18157 failures=0`; focused CMake correctness + Jieba identity tests pass 2/2. Batch tests cover pre-WAL failure, crash after sync and partial in-memory apply, torn-tail repair and append, same-PK update/delete order, non-reused DocIds, concurrent reader atomicity, concurrent same-PK writers, active-reader compaction/tombstone GC, and one WAL sync per durable batch.

Two successful paired 60-file synthetic FTS/INVERT runs show parity with the independent oracle and between arms. In the telemetry-enabled pair, cumulative M4 WAL fsyncs were 1,588 (per-document) vs 10 (batch, including initial setup and explicit flushes); physical writes were 125.90 MB vs 12.06 MB; CPU 1.514 vs 1.097 s; mutation bursts 398/482/501 ms vs 75/75/79 ms. Three further attempts stopped at the configured I/O PSI guard before candidate execution; partial runs were preserved and excluded, with no further benchmark started after the stops. These results are single-run per pair and synthetic; no production or whole-`zg` claim. Detailed report/raw profiles: `m4-p4.2-durable-batch-20261018.md`, `/var/tmp/m4-p42-ab-60-retry-20261018/` and `/var/tmp/m4-p42-ab-60-repeat-20261018/`.

Branch A native adapter is NO-GO at the current seam: native batch APIs expose per-document results and `CollectionImpl::write_impl` loops per doc; native segment/IDMap/delete-store/version/recovery and FTS/INVERT are coupled, so an independent M4 store cannot share a commit/visibility point with vector writes. No native checkout file was edited. Trace: `m4-marathon-branch-a-20261018.md`. Branch B's independent M4 candidate passed the stated adversarial gates; see `m4-marathon-correctness-20261018.md`. Production integration/migration remain NO-GO.

## Phase A — P4 native vs M4 ApplyBatch (2026-10-18)

On the 1,200-file P4 fixture, two untraced alternating-order pairs show M4 ApplyBatch lower in all primary resource metrics: mean process CPU 3.17 vs 6.82 s (−53.5%), physical writes 52.04 vs 100.68 MB (−48.3%), peak PSS 276 vs 532 MiB (−48.2%), and mean query p95 ~1.3–1.5 vs 21–26 ms. The separate traced pair counted 28 successful M4 WAL-path fsyncs vs 4 native (116 vs 57 total `fsync` calls); these durability boundaries differ (M4 fsyncs every bounded batch, native syncs at explicit flushes). Both pairs matched live doc/token/file snapshots; M4 had zero logical-oracle retrieval mismatches. The retained `json parse` query produced native-only extra hits (32/32/32/44 by stage, no M4-only hits) in both pairs; full query/ranking detail and precise caveats are in `m4-phase-a-native-vs-m4-batch-20261018.md`. Four `common_timeout` top-10 order divergences remain, consistent with known live-vs-historical ranking-stat semantics. This is a synthetic research fixture, not a whole-`zg` or production result.

Phase B classification: no new B1/B2 issue was shown by P4, but Phase D's bounded 50k/4-cycle churn trace did show a B3 p95 tail rise (1.49→2.20 ms) as run/dead-posting debt grew. Re-testing the accepted S5 policy (base built OFF, opt-in after reopen; ratio 0.01/cap 250k) reduced mean cycle p95 1.92→1.57 ms and final-cycle p95 2.20→1.88 ms, with +9.7% total physical writes and near-flat CPU. Keep the policy opt-in/default-OFF; do not tune its threshold. Strict oracle and ASan+UBSan both pass 18,157/0; Jieba identity test passes 173/0. Details and host artifacts: `m4-phase-b-d-churn-debt-20261018.md` and `m4-phase-d-churn-50k-4x2000-20261018/`.

## Phase E — final P4 A/B and mission decision (2026-10-18)

Compared native with the best research candidate: M4 ApplyBatch plus S5 policy enabled only after the initial base flush/reopen. Two untraced pairs show CPU 3.24 vs 6.74 s (−51.9%), physical writes 53.47 vs 99.35 MB (−46.2%), PSS 276 vs 532 MiB (−48.1%), peak CPU 108 vs 175% of one core (−38.5%), and query p95 ~1.18–1.53 vs 19.81–25.57 ms. Exact after-base-policy trace confirms 28 successful M4 WAL fsyncs vs 4 native; native-only `json parse` extras and four live-vs-historical top-10 order cases remain visible. Full decision/trade-offs: `m4-phase-e-final-native-ab-20261018.md`.

**Mission result: SUCCESS for the independent research candidate only.** ApplyBatch + optional S5 policy passes correctness/durability and improves the stated synthetic churn fixture with quantified write/durability trade-offs. Native compatibility is still PARTIAL, and no whole-`zg`/production claim is made. Production integration, migration, schema/ABI changes, and deployment remain NO-GO/unauthorized. Raw Phase A/E configs, query-level comparisons, stdout, profiles, host snapshots, and syscall logs are archived at `artifacts/m4-native-m4batch-phase-a-e-20261018.tar.gz` with a sibling SHA256 manifest.

## M4 Bridge — cross-component snapshot correctness (2026-10-18)

The research bridge race was an inconsistent mutable native segment view: the vector index emitted row IDs 49–71 while `DocFilter`'s forward bitmap contained 49 rows. A coordinator shared/exclusive snapshot lease now keeps the native vector search and M4 FTS generation coherent, including a single hybrid read lease across both components. Deterministic publish, new/old-generation, compaction/GC, crash/reopen/WAL and hybrid assertions pass `checks=1240 failures=0`, including three concurrent hybrid readers blocked across staged publish; five additional fresh-process repetitions pass 1,139 checks each. Current-source ASan+UBSan+LeakSanitizer also passes 1,240/0. The one-run 500-row profile measures writer 45.11 ms and a deliberately blocked query 45.80 ms; this is an explicit full-commit reader-stall cost, not a scale/performance claim. Old generation rows are retained; generation-level reclamation remains unimplemented. Production stays NO-GO. Report and raw host/time/JSON evidence: `m4-bridge-snapshot-consistency-20261018.md`, `/var/tmp/m4-bridge-lock-perf-20261018-074647/`.

## M4 Bridge — B2 delta-vector lifecycle (2026-10-18)

The standalone B2 prototype has immutable vector base+delta shards, global top-k merge, scalar-filter refill, four-shard compaction, durable catalog/manifest/WAL recovery, crash boundaries, a pinned hybrid reader, and harness-local pin-aware retirement/backpressure checks. Current source `research/m4-native/tests/m4_bridge_integration.cc` SHA-256 `d9b37b64c96efba86d7879ab763b6914d9c5b0fd71976390f4a28b0c2e77992f`; normal binary `d9161e9684d07abeb33d4539aafdedfd7bcf2a26eed1910279355985f5bce091`.

Current-source candidate smoke passed `checks=1380 failures=0`; B1 lifecycle regression passed `checks=3549 failures=0 generation=22 live_rows=23 max_retained_generations=3`; current-source ASan/UBSan/LSan passed 1380/0 with no diagnostics. Paired B1/B2 500×20 and 1,000×5 were each run twice with reversed order on repeat. Two-run mean commit p95: 791.1→577.9 ms (-26.9%) at 500×20, and 821.1→576.5 ms (-29.8%) at 1,000×5. Mean process CPU was lower 31.9%/40.2%, physical writes 6.5%/5.4% lower; B2 reader p95 was 53.2%/32.1% higher. At 1,000×5 B2 retained 1.86× bytes with 11 stale rows after one compaction. These small synthetic single-host repeats are descriptive only, not significance or architecture/production GO. Full paired data and receipts: `m4-b2-cost-model-20261018/paired-benchmark/summary.md`.

B2 remains research-only: retirement/GC/backpressure helpers are not integrated into one publisher; full M4 FTS/INVERT rebuilds per mutation, and compaction rebuilds all live vectors. PSS, peak CPU%, reader blocked-wait, and per-compaction physical writes were not measured; B1 process-wide per-stage CPU was contaminated by reader work and is excluded. Production integration/migration remain unapproved. Continue with the previously scoped usage-first M4-P2 scalar/filter compatibility audit, not BM25 tuning.

## B2/B3 M4 `Index::Open` attribution and analyzer-cache candidate (2026-10-18)

Instrumented `Index::Open` CPU/wall stages show that 46–52 ms CPU per open is native Jieba analyzer initialization, with another ~45 ms in initial and verification fingerprint reads. Segment loading is <0.5 ms median and therefore does not warrant a segment-handle cache. The opt-in weak-reference analyzer cache keeps both fingerprint checks, shares only immutable analyzer state while indexes are alive, and leaves run ownership, WAL, manifest, and recovery unchanged. Two 500×20 B3 pairs (second reversed order) show mean writer CPU −47.6%, total CPU −40.0%, commit p95 −32.8%, and M4 Open CPU p50 −51.2%; both cache arms have zero coherence errors and 20/20 cache hits. Reader p95 varied between pairs and physical-write counters are not attributable. M4 correctness 18,157/0 and ASan/UBSan/LSan candidate 578/0 pass. This is a standalone opt-in research candidate, not a production or architecture GO. Full attribution, commands, hashes, host state, and raw outputs: `m4-m4-open-attribution-cache-20261018.md` and `m4-b2-cost-model-20261018/paired-benchmark/open-attribution/`.

## B3.1 reader-compaction crossover — 2026-10-18

One owner-requested 1,024-doc AB/BA crossover compared the existing natural
four-shard vector compaction with a no-writer control. Same fixed 8 query IDs ×
4 repeats, paired per `(pair, query_id, repetition)`, with native-vector and
M4 wall/CPU, per-thread scheduler delay, and system PSI. All active queries
finished inside the compaction windows. Native API paired median delta was
+0.330 ms in pair 1, +0.026 ms in reversed-order pair 2, combined +0.135 ms
(p10/p90 −0.383/+0.564 ms); the effect did not reproduce consistently. Close
the compaction-causality hypothesis as unsupported for this fixture; do not
change search or compaction policy. Next focus: independent search-execution
cost on a fixed snapshot without a writer. Exit 0, 48,891 checks, zero failures.
Full paired analysis, receipts and checksums:
`m4-b2-cost-model-20261018/paired-benchmark/reader-compaction-crossover-20261018/`.

## Search instrumentation + PK lookup microbenchmark — 2026-10-18

Current-source warning-clean M4 correctness and ASan+UBSan+LSan each pass
18,456 checks, zero failures after fixing WAND upper-bound accounting for delta
cursors and accounting for valid AND short-circuit paths. A reversed-order
baseline/candidate scalar-INVERT PK lookup microbenchmark (1,024 hits/query,
3,000 measured queries/run, 50 warmups) gives candidate median 101.761 ms vs
baseline 240.001 ms (−57.6%); this is a narrow isolated result, not evidence of
end-to-end latency improvement. Host had active GUI/browser processes and
nonzero I/O PSI, so broader bridge/HNSW profiles were not launched. Marathon
DoD remains incomplete. Full commands, resource receipts, raw output, hashes
and caveats: `m4-b2-cost-model-20261018/paired-benchmark/pk-lookup-microbench-20261018/analysis.md`.

Current-source bridge recovery/lifecycle ASan/UBSan validation passed
`checks=3581 failures=0 generation=22 live_rows=23 max_retained_generations=3`.
The fixed-snapshot diagnostics A/A sequence is complete: all eight arms exited
0, and query signatures/counter invariants matched. The sequential native-HNSW
+ M4 counter rerun also exited 0 after the exact empirical four-metric resource
envelope passed. It covered 32 queries across vector-filter match counts 1,
341, 512 and 1,024 and top-k 8/64; all query signatures exactly match the
preceding sequential cohort. Median query wall was 0.898 ms (p95 1.082 ms),
vector 0.690 ms, native API 0.580 ms, M4 0.210 ms. Native trace recorded 30
filtered-HNSW decisions on the 1,024-doc base and 40 native route-counter
records. This is a one-reader synthetic attribution profile, not a paired
performance win or a representative production workload. It confirms similar
M4 operation counts across the native vector-filter selectivity strata, while
HNSW traversal remains active; no optimization is selected from this run alone.

Full rerun analysis, raw traces, source/binary hashes and validated checksums:
`m4-b2-cost-model-20261018/paired-benchmark/native-hnsw-attribution-20261018/hnsw-route-coverage-rerun2-20261018/analysis.md`.
A/A analysis and receipts:
`m4-b2-cost-model-20261018/paired-benchmark/reader-search-aa-current-20261018/analysis.md`.

## Repeated M4 filter-candidate cache — 2026-10-18

Added regression coverage for the existing bounded `Options::cache_filter_candidates`:
uncached/cached hit-set and score parity, exact within-index hit ordering and
DocId stability across cache miss/hit, mutation invalidation and repopulation.
The final warning-clean oracle passes `checks=18481 failures=0`; the final
ASan/UBSan/LSan run also passes `checks=18481 failures=0`.

One four-arm, 4-reader ABBA profile (control/cache/cache/control) used the same
1,024-doc fixed snapshot, 32 queries, four vector-filter selectivities and
top-k 8/64. All four harness runs exited 0 (`checks=8616 failures=0` each),
all 32 query hit signatures matched, and filter-cache telemetry reported 0/32
hits in both controls and 32/32 in both cache arms. Per-query M4 wall p50
paired deltas were −0.264 ms and −0.117 ms; reader-thread M4 CPU p50 deltas
were −0.126 ms and −0.139 ms. End-to-end query p50 deltas were −0.472 ms and
−0.503 ms. M4 reader-CPU deltas were negative for every query in both pairs;
end-to-end and M4-wall ranges include both signs. There are only two ABBA pairs,
and counter-only diagnostics/native tracing add
instrumentation. The usual postings/liveness counters are unchanged as
expected: they count membership checks after candidate construction, not the
skipped `FilterCandidates` expression scan. The direct scan count/time is not
exposed by this counters-only mode. Treat this as a promising local signal, not
a general latency claim. Keep the existing option research-only/default-off;
no production default or integration change is justified.

Full preparation, build provenance, raw arm logs/traces, host receipts, source
hashes and analysis:
`m4-b2-cost-model-20261018/paired-benchmark/filter-candidate-cache-optimization-20261018/analysis.md`.

A separate diagnostics-off primary control profile completed once on the same
fixed 256-D hybrid fixture: 32 query signatures match the counter-only control,
checks pass, and route tracing records 120 HNSW dispatch and 10 brute-force-by-
PK events. Median query/M4 wall is 1.478/0.314 ms. This is a one-run,
native-instrumented profile, not paired latency evidence. Analysis and receipts:
`m4-b2-cost-model-20261018/paired-benchmark/reader-search-primary-control-20261018/analysis.md`.
The diagnostics-off primary cache ABBA completed after two no-launch preflight
stops at I/O PSI full avg10 5.11% and 5.50%; the threshold was never relaxed.
All four subsequent arms passed pre/post resource gates and exited 0. Hit
signatures and native dispatch counts match in every arm (120 HNSW, 10
brute-force-by-PK). Paired query-wall p50 deltas are −0.404/−0.287 ms; M4-wall
−0.272/−0.189 ms; M4 reader-CPU −0.145/−0.124 ms. M4 CPU deltas were negative
for all 32 query keys in both orders; query-wall ranges overlap zero and this
remains a two-pair synthetic test with native route tracing enabled, not a
production latency claim. Detailed results, logs, receipts and hashes:
`m4-b2-cost-model-20261018/paired-benchmark/filter-candidate-cache-primary-abba-20261018/analysis.md`.
No production/default change follows. Broader M4 marathon DoD remains
incomplete; production remains untouched.

## Search execution cost-model point checks — 2026-10-19

Current-source Flat/HNSW fixture point checks at 1,024 and 10,000 docs (256-D,
eight queries, one run/mode/size) suggest a potential small-shard crossover:
HNSW was slower at 1,024 (+0.022 ms paired median; 6/8 slower) and faster at
10,000 (−0.111 ms; 3/8 slower), while median HNSW distances fell from 1,051
to 5,887 against Flat's 1,024 and 10,000. This is exploratory, not a stable
latency win or threshold. At 10k, separate HNSW builds produced 78/80 vs
80/80 exact-hit overlap; discrepancy unresolved. Host CPU activity and query
latency spread limit inference. No production changes.

Raw receipts, route counters, summaries and analysis:
`m4-search-execution-marathon-20261018/phase-b-cost-model/analysis.md`.
Next: investigate the 10k recall discrepancy, then run one paired diagnostics-
off whole-hybrid route comparison on the frozen 1,024-doc fixture. Keep
counters-on operation accounting separate from primary latency; marathon DoD
remains incomplete.

## M4 filter-candidate cache capacity cost check — 2026-10-19

Phase C attribution found scalar filter candidate construction dominated M4 FTS
on this synthetic whole-hybrid workload. The existing opt-in cache already
helps repeated filters when eligible, but its 64-KiB cap rejected the 80,000-
byte, 10k-DocId candidate vector. In isolated research code, raised the cap to
128 KiB; cache remains default-off. At 10k, cache-on logged 32/32 hits per
backend; median filter time fell 1.485→0.00037 ms (Flat) and
1.425→0.00030 ms (HNSW), FTS stage 1.537→0.045 ms and 1.476→0.046 ms.
Candidate vector capacity was 80,000 bytes; max RSS 379,200→380,284 KiB.
At 1,024 docs, cache hits were 32/32 and filter time ~0.099→0.0003 ms, with
identical Flat/HNSW hit lists across arms. One run per arm, diagnostic timing,
synthetic fixture: promising cost evidence, not a stable latency claim. At
10k, independently built HNSW arms still differed on one top-k query; this is
not attributed to the M4 cache and no ANN quality conclusion follows.

Strict oracle and ASan/UBSan/LSan passed `checks=18481 failures=0` on the
temporary cap-128 source and again after restoring the canonical 64-KiB bound,
before the separate Boolean-constant simplification below.
The prior canonical handoff explicitly closed this cache optimization branch
and prohibited another implementation attempt, so the cap change was rolled
back to 64 KiB. Cap-128 measurements remain exploratory, not an accepted
candidate; cache stays default-off. Full raw evidence, checksums, host receipts
and summaries:
`m4-search-execution-marathon-20261018/phase-b-cost-model/filter-candidate-cache-20261019/`.
Marathon DoD remains incomplete. Source review later confirmed HNSW graph-level
randomness across independent builds; see the next section.

## M4 Boolean filter constant simplification — 2026-10-19

The hybrid bridge constructs `AND(generation = snapshot, ALL)`. `EvalFilter`
previously built a `LiveUniverse()` vector for that identity and intersected it
with the generation candidates. Added oracle cases for `AND(ALL,x)`,
`OR(NONE,x)`, absorbing constants and negated compositions, then skipped direct
constant identities under De Morgan negation. This is a distinct correction;
the previously closed candidate-cache-cap branch remains rolled back at 64 KiB.

Strict oracle and ASan/UBSan/LSan on the current candidate both pass
`checks=18643 failures=0`; warning-clean CMake correctness/bridge targets.
Two 10k, 32-query diagnostics-off pairs in opposite order show paired median
whole-hybrid ratios candidate/baseline 0.745/0.769 (Flat) and 0.666/0.791
(HNSW), with 64/64 Flat top-k matches. Candidate was faster on 30/32 and 23/32
Flat queries, 32/32 and 22/32 HNSW queries. Separate attribution showed M4
filter time 1.432→0.558 ms (Flat) and 1.477→0.550 ms (HNSW); run/posting
counts are unchanged. Candidate DocId-vector capacity increases 80,000→131,072
bytes and observed max RSS by roughly 0.3–0.5 MiB. One synthetic fixture;
reverse pair had higher host load, so report within-pair deltas/ratios only.
Research-only KEEP for the standalone evaluator; no production/default change.

The native profile HNSW source seeds random-level assignment from
`system_clock` (`hnsw_algorithm.h:52`); the fixture fixes M=16/ef=100 but no
seed. Exact HNSW top-k equality across independent graph builds is therefore
not deterministic; preserve per-build Flat overlap and do not change ANN
parameters. Detailed raw arm logs, host snapshots, hashes, summaries and
analysis: `m4-search-execution-marathon-20261018/phase-b-cost-model/filter-constant-identity-20261019/`.

A resource-gated paired baseline/candidate reader-compaction crossover
completed on 1,024 docs (four readers, 64 queries per condition per binary).
Each compaction arm overlapped concurrent upsert/delete and vector/M4
compaction; both binaries passed `checks=48909 failures=0`. This supplies
eight fixed query IDs and one maintenance generation. M4 median wall was
0.522/0.437 ms baseline and 0.482/0.473 ms candidate for control/compaction;
M4 CPU was effectively flat. The paired process end-to-end/native medians were
not attributable because HNSW graphs are independently time-seeded.

A resource-gated sustained 500-doc × 20-round pair is now complete (candidate
first, baseline second): 4 readers, 80 queries/arm, five compactions, 21
published generations, final 500 live docs. Every reader pin was unambiguous;
all 80 queries per arm had generation lag 0 at completion, and both arms had
zero coherence errors. Candidate/baseline measured physical writes were
251,011,072/247,373,824 B (+1.47%); retained root bytes matched. Reader
p95/p99 was 3.406/4.598 vs 3.289/3.983 ms; writer-commit p95/p99 was
431/594 vs 247/270 ms. This single pair shows no sustained win and raises a
writer-tail concern, but is insufficient to attribute the large delta. The
sustained harness uses one fixed vector/FTS query, so the prior 1,024-doc
crossover supplies query-ID diversity. Static Flat/HNSW quality evidence is
referenced in the cost-model analysis; this workload checks live-generation
coherence, not ANN recall. Full raw records, PSS samples, receipts, hashes and
analysis: `m4-search-execution-marathon-20261018/phase-b-cost-model/sustained-multicycle-500x20-20261019/`.

The reverse-order sustained 500×20 pair (baseline then candidate) completed
2026-10-19 under the validated resource envelope, using the identical binaries,
mode/query mix and fresh roots. Candidate writer p95/p99 were 477.1/498.4 ms vs
baseline 240.0/242.0 ms (+98.8%/+106.0%); this repeats the higher candidate
writer tails in the candidate-first pair (+74.7%/+119.6%), so the direction
survives order reversal but is not causally attributed by only two pairs.
Reader p95/p99 were 4.689/8.540 vs 4.582/7.726 ms (+2.3%/+10.5%). Process CPU
was +4.0%, while physical writes were −8.85% and sampled peak PSS −2.4%;
results are mixed and do not establish a general win or regression. Candidate
had unusually slow first four vector-build cycles (204.8–256.7 ms), alongside
higher vector-build p95 and writer tails; no claim that the filter-only change
caused this is justified. Same five compactions, generation 21, 500 live docs,
zero stale vector rows/coherence errors. M4 Open p50 was 55.3 vs 54.6 ms and
reopen 294 vs 381 ms (one observation each). No query interval overlapped a
publish (0/80), so generation-lag-zero completion does not verify reader
pinning across publish. Raw per-cycle latencies and full analysis:
`m4-search-execution-marathon-20261018/phase-b-cost-model/sustained-multicycle-500x20-reverse-20261019/README.md`.
The two-pair sustained claim remains inconclusive on causality; candidate stays
research-only, and production integration/migration remain NO-GO. Do not
optimize candidate based only on these runs. Current-source oracle/sanitizer
evidence is separate and was not rerun for this benchmark-only pair.

The 2026-10-19 writer-tail reconstruction joined all 80 existing per-cycle
records and reviewed the bridge/native durability call graph; it does not
causally attribute the first-cycle candidate tails. A test-only trace build now
records per-phase native-vector/M4/generation preparation timings and buffered
`fsync`/`fdatasync`/`syncfs`/`msync` events with paths and TIDs. Warning-clean
builds and a 64×1 tmpfs functional smoke pass (360 sync events, no drops or
failures, coherent 4-reader reopen); this is not performance or persistent
storage evidence. No source behavior was corrected. A 500×20 diagnostics-off/on
A/A remains blocked by the latest resource preflight: MemAvailable 4,581,316
KiB, 896,446 KiB below the 5,477,762-KiB minimum; load, I/O PSI and `/var/tmp`
free space pass. This was superseded by later passing per-arm receipts. The v2
OFF/ON A/A completed but had excessive overhead (commit p95 212.2→293.7 ms);
its 3,399 sync records had zero drops/failures, useful for coverage only.
A bounded research-harness correction skips outer per-file fsync of M4 FTS
after durable `Index::Flush`, retaining directory sync and all other syncs.
Three resource-gated same-source 500×20 pairs completed, all with five
compactions, generation 21, 500 live docs, zero stale rows/errors and equal
retained bytes. Treatment/control commit p95/p99 were 213.7/214.1 vs
361.0/383.1 ms (treatment first), 369.2/390.3 vs 257.4/258.7 ms (treatment
second), and 211.5/228.6 vs 464.4/497.1 ms (treatment first). Outcomes are
order-sensitive and inconclusive; in all three pairs the second arm had higher
writer p95/p99 regardless of variant (+68.9%/+78.9%, +43.5%/+50.9%, and
+119.6%/+117.4%), and start I/O PSI also rose first-to-second each time
(0.32→1.23%, 2.33→3.02%, 0.43→1.34%). Carry-over/host pressure is a plausible
confound, not a proven cause; no stable causal win/regression is claimed.
Current-source M4/vector/lifecycle oracles report 18,643/1,401/3,581 checks,
zero failures; ASan/UBSan/LSan reports `ASAN_PASS`. Harness-only change, no
production or M4-library change. The 50-ms `/proc` sampler records process
I/O, PSI avg10+totals, memory/swap, swap-in/out, diskstats and monotonic time;
per-sample file flushes were removed. Eight-row sampler smoke passed. The
commit/PSI join analyzer passed a two-cycle synthetic monotonic-alignment test;
pre/during/post windows stop at adjacent cycle boundaries to avoid overlap. The new
same-binary A/A orchestrator uses two fresh roots, a 60-s baseline stability
check and three consecutive 10-s recovery windows with pressure/load/I/O/swap
activity no worse than baseline maxima, MemAvailable/SwapFree no lower than
baseline minima, plus the existing resource gate; no fixed-sleep assumption. Both old 60-s host-only captures were rejected as references: they
flushed each row and drifted; follow-up PSI full avg10 was 0.92–5.31%, swap-in
8,236 pages, swap-out 24, and MemAvailable minimum 4,931,476 KiB. The sampler is now buffered. The rejected-baseline evidence and updated resource status are recorded below.

No A/A workload launched. Three 60-s ambient captures (50-ms sampling, 1,200 rows each) failed the unchanged stability gate. The latest post-attempt snapshot (2026-10-19 16:39:41 +07:00) passes all four resource limits: MemAvailable 9,221,468 KiB, load-1 2.57, I/O PSI full avg10 3.49%, and `/var/tmp` free 19,255,046,144 B. The remaining blocker is host stability during the 60-s baseline, not disk capacity. Full per-window evidence and resume condition: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-resource-gate-wait-20261019/README.md`. If A/A reproduces second-arm slowness
despite verified recovery, that shows only order/host drift; further evidence
must separate filesystem writeback/page cache, thermal throttling, swap and
background-process activity. If not, the next comparison must be
baseline/candidate under matching host conditions. A/A binary SHA-256
`70eb45587252770b9f3fbcf8d057f6aff63ca03041a31d0a0981262853dd97a8`.
Receipt:
`m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-final-resource-no-launch-20261019Tcurrent.json`.
Runner: `.../run_host_carryover_aa.py`; cycle analyzer:
`.../analyze_host_pressure.py`. Sync-elision remains diagnostic-only and not
durability-equivalent: source trace shows `FlushInternal` truncates WAL after
manifest publication without fsync; the skipped outer file fsync may be what
persists the truncation. No power-loss recovery proof exists. Raw artifacts
and interpretation:
`m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/`;
canonical handoff `rust/docs/m4-incremental-native-index.md` §35.

## M4 writer-tail stability and A/A readiness investigation — 2026-10-19

Offline replay of the three pre-existing buffered 60-s baselines confirmed the unchanged all-feature stability rule rejected every trace: the first reported feature is `load1_max`, but the raw profiles have 23/21/15 feature-window misses. The captures are complete and cadence-valid (1,200 rows, six ~9.95-s windows each); the failures are not missing samples or short windows. I/O PSI full avg10 crossed its 4.46% snapshot ceiling in 1,128/1,200, 1,058/1,200 and 1,158/1,200 samples. No baseline supports the claim that only tiny benign load noise caused rejection.

After completing the offline audit, synthetic replays and process/Btrfs attribution, one authorized post-audit attempt used the same binary. Both the pre-launch snapshot and runner preflight passed the unchanged four limits; the valid 60-s baseline failed the same strict envelope (`load1_max` drift down), so the runner launched no arm. During this capture, MemAvailable was below the existing 5,477,762-KiB limit in 64/1,200 samples (minimum 5,304,712; shortfall 173,050 KiB). A sanitized post-capture snapshot then showed 5,472,864 KiB, 4,898 KiB below the limit; load, I/O PSI and `/var/tmp` passed. No retry was started. The current A/A is **not run**, and there is no writer-tail result, variant comparison or causal conclusion.

Diagnostics now distinguish `SAFETY_GATE_FAIL`, `STABILITY_GATE_FAIL` and `MEASUREMENT_INVALID`; validate timestamp monotonicity, cadence gaps, sample brackets and full 10-s windows; and retain all failed feature/window values without changing thresholds. Current replay covers the three historical traces, the separate post-audit attempt and 11 synthetic cases. The arm-time process/PSI sampler now buffers records until the child exits and records monotonic sample brackets; a smoke confirms no telemetry output file is opened while the measured process is live. Analyzer synthetic join passes non-overlap/monotonic checks. Host pressure attribution remains inconclusive for the original failures: later read-only observations saw I/O stalls, swap-in and block writes not explained by sampled process `write_bytes`, but no evidence identifies a causal process or Btrfs compaction. No process was interrupted.

Policy outcome: retain all established limits and the first-three/last-three envelope. A continuous application of absolute resource limits is a bounded option for owner approval, strengthened by the post-audit memory dip; no policy was changed and no rejected baseline was reclassified as PASS. Detailed audit, policy options, raw host attribution, exact execution receipt, tests and checksums:
`m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/`; canonical handoff `rust/docs/m4-incremental-native-index.md` §36.

A fresh recheck at 18:23 +07 passed the read-only and runner preflight limits,
but its valid 60-s ambient baseline again failed the unchanged stability gate
(`load1_max` drifted below the first-three-window envelope: 0.84 vs 0.88).
I/O PSI full avg10 also peaked at 8.02% during the baseline, above the 4.46%
preflight ceiling. The runner launched neither arm. This adds no A/A or latency
evidence and does not justify relaxing the gate. Raw receipt:
`m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/host-carryover-aa-pair-20261019T112337Z/`;
runner log `/tmp/pi-bg/job-2966907-46.log`; canonical handoff §37.

The owner-requested DIAGNOSTIC/QUALIFICATION split was then implemented in the
research runner. All four existing preflight limits remain HARD SAFETY and are
also watched during arms; only a valid stability-envelope failure is
non-blocking in DIAGNOSTIC. Existing first-three-window SwapFree/memory-PSI
pressure bounds are retained as additional hard stops, with no new numeric
thresholds. Four policy tests and Python compilation pass. One diagnostic
attempt had passing point snapshots but failed the independent baseline
memory/swap safety check: SwapFree fell 70,800 KiB below its initial three-
window floor and memory PSI reached 0.70% from a 0.00% envelope; I/O PSI full
avg10 was 6.05–10.77% across the ambient windows. No arm ran. A subsequent
source audit added samplewise checking of the unchanged hard I/O PSI ceiling;
the same raw trace also violates it. This is a **single-run safety refusal**,
not a project block and not an A/A result. Details and artifacts:
`m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/diagnostic-fixes-and-tests.md`;
receipt `.../host-carryover-aa-pair-20261019T121316Z/`; handoff §38.

## M4 cgroup-v2 containment and safety calibration — 2026-10-19

Implemented the research-only per-arm transient user-service runner and independent watchdog in `research/m4-native/tools/m4_containment_runner.py`. The initial suite had 8 cases; the current suite has 9 tests covering hard-gate refusal, memory/swap pressure rules, missing telemetry, normal descendant ownership, nonzero and SIGTERM/SIGKILL exits, runtime abort, and controller SIGKILL recovery/cleanup. Current suite: 9 passed, 0 failed; no privileged actions or production changes. Initial supervisor-crash testing caught and fixed a watchdog NameError; final crash receipts verify `supervisor_lost`, post-receipt `cgroup.kill`, socket cleanup and removal of the owned cgroup. Earlier failure receipts remain retained.

Capability evidence: cgroup v2 and delegated CPU/memory/pids work through `Delegate=yes`; user-slice I/O is not delegated, so `io.stat`/`io.max` and an I/O cap are unavailable. `io.pressure`, host diskstats and vmstat are sampled. Historical 500×20 process PSS peaks were 304,227/286,915 KiB and sampled swap 0/0, but no cgroup memory or active workspace peak was recorded; therefore no numerical cgroup memory/swap cap or workspace quota is selected. Approved budgets only; sampled workspace limits are not kernel quotas. All established hard guards remain unchanged.

No 500×20/A/A workload ran at the original §39 snapshot. Snapshot 2026-10-19 20:14:49 +07:00 had MemAvailable 5,771,328 KiB PASS; `/var/tmp` free 15,469,010,944 B FAIL by 2,749,989,056 B; I/O PSI full avg10 4.32% PASS; load-1 1.62 PASS. The owner-authorized cleanup later restored disk headroom. The existing A/A orchestrator is now wired per arm to containment; current readiness is documented below. Full policy, metric semantics, receipts, probe, snapshot and checksums: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/containment-calibration.md` and `containment-tests-20261019T131427Z/`; canonical handoff §39.

## Same-binary A/A readiness — 2026-10-19

The orchestrator now records/enforces exact binary SHA-256, current Git revision and deterministic M4 source-tree and measurement-runner digests, plus resolved shared-library paths/hashes, before each arm. Integration coverage checks identity equality across both isolated arms. Current checks: containment 9/9, real-systemd sequencing integration 1/1, gate policy 4/4, `py_compile`, and `git diff --check` all PASS. No active M4 workload or running transient M4 service remained after tests.

A recorded readiness snapshot at 20:56:34 +07 had MemAvailable 6,886,092 KiB PASS (+1,408,330); `/var/tmp` free 19,470,082,048 B PASS (+1,251,082,048); load-1 1.76 PASS; I/O PSI full avg10 **4.52% FAIL** (unchanged 4.46% ceiling exceeded by 0.06 percentage points). No baseline capture or benchmark arm launched. Receipt: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-readiness-gate-20261019T205634+0700.json`. The source-build provenance is partial: current bridge harness source checksum differs from the archived build checksum, so any A/A result will apply to the preserved binary, not certify the current harness source. The measured runner source-tree fingerprint is `ebda49add2738cef83622ccf21e94073897f4159b5fcb71c7ec11fcb7294318d`; exact details are in the receipt and canonical handoff §40. Resume once the host naturally meets all four unchanged hard limits; do not weaken thresholds or poll in a retry loop.

## Harness repair package closure — 2026-10-19

Packaged the repository-global and scoped M4 instructions, machine-readable policy and explanatory Markdown, containment runner, same-binary A/A orchestrators, policy/containment/scenario regression tests, calibration/readiness receipts, `README.md`, `RESULTS.md`, and canonical handoff. Exact staged paths are limited to the research M4 package and its handoff; no production Rust `.rs` changes are in the staged set. Policy enforcement reads `benchmark-policy.json`; Markdown contains no independent numeric thresholds. Legacy retained orchestrators now load the JSON policy, covered by regression tests.

Focused verification for this closure: policy tests **5/5**, harness scenario/termination contracts **4/4**, A/A gate tests **7/7**, synthetic containment tests **11/11**, tiny contained A/A sequencing integration **1/1**, `py_compile`, and staged whitespace check. These are harness/control-flow checks only; no benchmark arm launched. The diagnostic same-binary binary provenance remains partial and is handled separately in the writer-tail investigation. The isolated package was committed as `e1309f31d5d8788e1da5095ccd2fad62e02b8fed` (`research(m4): package writer-tail harness repair`); Phase B evidence remains separate.

## Writer-tail cause review and current-source build provenance — 2026-10-19

**Disposition: root cause remains unresolved; no new measurement launched.** Offline review of both order directions in the sustained 500×20 filter-identity A/B finds candidate writer p95/p99 above baseline in each: candidate-first +74.7%/+119.6%; baseline-first reverse pair +98.8%/+106.0%. This weakens the simple “candidate ran second” explanation, but two pairs do not establish causality. Reader CPU rose only modestly (+6.2% in the first pair, +2.5% in reverse), while candidate native-vector-build p95 rose to 246.4/232.1 ms from 111.0/119.3 ms and M4-update p95 to 107.7/113.5 ms from 103.7/78.3 ms. `commit_residual_after_vector_m4_publish_retire` p95 also rose (72.6/128.4 vs 43.9/56.5 ms). Native-vector build writer-thread CPU p95 rose much less (70.1/66.4 vs 60.5/53.5 ms), so most of the wall-time delta is not accounted for by bridge-writer-thread CPU; this is consistent with wait/other-worker/host delay but does not identify which. The retained measured binary emitted total vector build wall/CPU only—not per-cycle open, document-conversion, upsert, and flush wall/CPU arrays. Current source now contains those finer arrays, but its binary is not built or measured yet. These are concurrent stage correlations, not a causal decomposition; per-cycle host-pressure is also absent.

The high-tail cycles do not require scheduled compaction: in the candidate-first pair both p95 and max commit observations are non-compaction cycles; in the reverse pair p95 is non-compaction while the max is a compaction cycle. Thus vector compaction is not a necessary explanation, though ordinary native vector build/upsert/flush remains implicated as a proximate stage. The largest candidate commit tails cluster early: candidate-first pair cycles 0–1 were 593.9/431.1 ms, with total vector builds 307.1/246.4 ms and no compaction; in the reverse pair cycles 0–3 were 406.6–498.4 ms, with vector builds 204.8–256.7 ms (the first three non-compaction, fourth compaction), followed by substantial overlap with baseline. Initial base-vector build was 159.1 ms for candidate vs 115.9 ms baseline in candidate-first order, and 292.8 vs 107.8 ms in reverse order. This suggests startup/cold-state or early-cycle effects worth testing alongside candidate-associated reader/native contention; it does not identify the cause. Candidate physical writes were 8.85% lower in reverse order, which does not support a general write-amplification explanation. Three separate sync-reduction pairs showed a second-arm tail pattern and increasing host I/O pressure, supporting carry-over as a plausible confound for those pairs, but not proving it explains the order-reversed filter-identity A/B. The 1,024-document reader/compaction cohort has no writer-ack latency and is not evidence on this question. Hypotheses remaining: candidate-associated reader/native contention, host carry-over/scheduler or I/O interference, native vector upsert/flush variability, M4 update variability, and uninstrumented commit residual.

## 2026-10-19 continuation — guard scope, current-source build, and post-M4 attribution

**Build-guard scope:** `research/m4-native/docs/benchmark-policy.json` defines the four unchanged limits (MemAvailable, `/var/tmp` retained free bytes, I/O PSI full avg10, load-1) for standalone research measurement/A/A operations. Their callers are measurement preflight, contained arm runtime checks, and watchdog; CMake configure/compile is not a caller and the policy does not classify compilation as a measurement. Build instructions provide commands, not build-specific resource thresholds; no separate owner-approved numeric build-only thresholds exist. The earlier stressed snapshot was therefore not treated as a compile prohibition. Instead, compilation was allowed only after a fresh build-specific RAM/swap/disk/process review and under a bounded transient systemd cgroup; the empirical benchmark thresholds were neither changed nor bypassed for a benchmark.

**Build and runtime verification:** at 22:58:34 +07 before compilation, MemAvailable 7,440,828 KiB, SwapFree 11,834,068 KiB, load-1 2.50, I/O PSI full avg10 3.82%, `/var/tmp` free 14,298,071,040 B, build tree 446,525 B, comparable previous bridge output 10,162,330 B, and M4 research workspace 540,363,155 B. The benchmark disk floor was below this free-space reading, but it was out-of-scope for compilation. Current source built with the recorded one-job command in `m4-current-source-build-20261019.service`, KillMode=control-group and RuntimeMaxSec=600; no numeric memory ceiling was set. Build job `job-207232-294` succeeded in 46.828 s (46.119 CPU s), peak memory 623M, swap 0B. The receipt fingerprints 59 actual in-repo/native-profile compiler dependencies, CMake/toolchain/configuration, binary SHA-256 `57f3a19e0fb5dfe4d2d64ea4831f276a81a586c22ee30309d6249ed986ba342f`, ELF build ID, and resolved linked-library hashes. Correctness-smoke job `job-207232-302` passed the fresh-root `--generation-lifecycle` test with 3,581 checks/0 failures (26.877 s; peak memory reported 2.2G; swap 0B) in its own control-group service. It was not a performance arm. Full receipt: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/current-source-build-20261019/current-source-build-receipt.json`; retained binary and configuration are in the same directory, sealed by `SHA256SUMS.txt`.

**Offline stage update:** the existing two sustained 500×20 pairs remain historical-binary observations. In addition to the previously measured vector/M4/publish stages, `build_end_ms - m4_build_end_ms` is derivable from their raw timeline markers. Its p50/p95 was candidate-first 28.55/39.10 ms vs baseline 26.30/33.14; reversed order 16.41/102.89 vs 29.09/50.01. The reverse candidate’s top cycles had 111.21/102.89/85.00 ms in this post-M4 interval. Source places post-M4 `SyncTree` and snapshot finalization before `build_end`; the interval is therefore sync/finalization-containing, not a pure fsync timer. The M4 clone timer also includes its own `SyncTree` work. These observations localize substantial elapsed time to several measured/derived regions, but still do not establish variant causality or a single root cause. Details and per-cycle rows: `stage-decomposition-offline-20261019.md`.

**Single permitted readiness attempt:** job `job-207232-317`, after current-source identity/build validation, ran the qualification-mode same-binary A/A runner through one fresh baseline preflight at 23:07:48 +07 and returned `OPERATION_BLOCKED` at `baseline-preflight`, gate `M4-RG-DISK`: resource-root free 14,288,429,056 B vs unchanged minimum 18,219,000,000 B (short 3,930,570,944 B). MemAvailable 6,043,024 KiB, I/O PSI full avg10 1.79%, and load-1 2.36 passed. No 60-second ambient baseline and no benchmark/A/A arm launched; no automatic retry. Receipt and SHA-256 manifest (`SHA256SUMS.txt`, manifest SHA-256 `1fea67dd70db4136f2b6f34edc35c95791e1e485d00e16c6233ce255e793dc14e`): `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/host-carryover-aa-pair-20261019T160748Z/`. Do not free space by deleting unreviewed artifacts or weaken the threshold. Recovery requires a fresh snapshot satisfying all unchanged guards and a new explicitly authorized attempt.

**Status: `PARTIAL / OPERATION_BLOCKED` for this A/A operation; mission remains open.** Build scope and current-source binary provenance/runtime correctness are now established. Same-binary A/A, current-source sustained validation, adversarial validation, and causal attribution remain outstanding. No production migration is authorized by this work.

## A/A read-only readiness snapshot — 2026-10-20

At 11:05:30 +07 (04:05:30Z), a read-only readiness snapshot was recorded at `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-resource-gate-readiness-20261020T040530Z.json`. This was not an A/A operation attempt: no runner, 60-second ambient baseline, output-root creation, or benchmark arm ran. Three resource guards passed—MemAvailable 6,313,076 KiB (floor 5,477,762), `/var/tmp` free 22,254,964,736 B (floor 18,219,000,000), and I/O PSI full avg10 0.27% (ceiling 4.46%); **M4-RG-LOAD failed** at 4.35 vs 3.14. Stop before A/A; do not retry by polling or change thresholds.

Pinned identity remained unchanged: binary SHA-256 `57f3a19e0fb5dfe4d2d64ea4831f276a81a586c22ee30309d6249ed986ba342f`, source revision `e1309f31d5d8788e1da5095ccd2fad62e02b8fed`, current source-tree SHA-256 `e6e4702945f2ee29f1a651d6a51d1226c104a4c0b16c4d7a485e6f09c2334ec0`, and measurement-runner-tree SHA-256 `dec0a54c33304f295a34a4201a108f806d798738e67461d4a1d3f5839e851b30`. The retained build manifest verifies 16/16 entries; `/var/tmp` remains the protocol resource root. Required tokenizer dictionary files are present.

Containment remains **PASS as harness evidence** (11/11 synthetic tests and 1/1 tiny sequencing integration in the packaged test record), but a live service launch was not probed. User systemd currently reports `degraded` with 17 inactive failed `m4-contained-*` units whose listed commands are synthetic 30-second fixtures; no active M4 unit or matching M4/A/A process was found. Cgroup v2 user-service `cpu memory pids` controllers are enabled, consistent with policy; no I/O throttling claim. Do not reset or alter those units in this task.

The prior one-shot readiness authorization was consumed by job `job-207232-317`, which blocked on disk before baseline/arms. The current task text conditions execution on existing authorization and does not unambiguously renew that one-shot, so another attempt is not authorized on that basis. Separately, this snapshot fails `M4-RG-LOAD`. The prior A/A receipt's six SHA256SUMS entries verify, but the digest previously printed for that manifest in this report is malformed (65 characters); the verified actual manifest SHA-256 is `1fea67dd70db4136f2b6f34edc35c957ee1e485d00e16c6233ce255e793dc14e`. Preserve the historical value; use the readiness JSON for the correction and new snapshot digest.

**Decision: `BLOCKED / NO LAUNCH`.** Next: require a new explicit owner authorization and a later fresh snapshot passing all four unchanged guards; then let the qualification runner enforce its baseline, telemetry/stability, containment, and inter-arm recovery gates. No cleanup, migration, threshold change, or production work was performed.

## 2026-10-20 continuation — workspace closure and diagnostic A/A no-launch

Workspace migration is committed separately as `d6280b2` (`research(m4): localize temporary workspace`). Exact scope: `research/m4-native/.gitignore`, `AGENTS.md`, `README.md`, and `tools/test_m4_containment_runner.py`; unrelated Rust edits and untracked M4 source/evidence were not staged. `.work/{tmp,build,sanitizer,scratch}` is ignored. Protocol benchmark data roots remain explicit (`/var/tmp` where Btrfs is required); project-local `TMPDIR` applies only where compatible and does not silently redirect a benchmark from tmpfs to Btrfs or vice versa. Focused regression results: containment runner 11/11, benchmark policy 5/5, A/A gate 7/7, contained-pair integration 1/1. No benchmark was used for workspace verification.

Current-source provenance was rechecked without rebuilding: the retained binary is SHA-256 `57f3a19e0fb5dfe4d2d64ea4831f276a81a586c22ee30309d6249ed986ba342f`, build ID `e889629e65a9ac929fa89a4a95ef549b1ec39062`; Release GCC 16.2.1 `-O3 -DNDEBUG`; native runtime library hash `6d55b9131766d943fbad1f77dbebc6a2db7951ce8d89ad46a92b7619cc92d950`. The retained build manifest passed 16/16, all 59 compiler dependency hashes plus CMake and bridge source hashes matched, and generation lifecycle smoke remains 3,581/3,581 PASS. No stale binary was substituted.

The one newly authorized diagnostic A/A readiness sample failed hard guards `M4-RG-MEM` (1,136,008 KiB vs 5,477,762 minimum; short 4,341,754 KiB) and `M4-RG-LOAD` (6.11 vs 3.14 maximum; +2.97). `M4-RG-DISK` passed at 22,254,845,952 B free (4,035,845,952 B headroom) and `M4-RG-IOPSI` passed at 0.00% vs 4.46%. SwapFree was 13,767,716 KiB; user systemd reported `degraded`, so containment readiness was not asserted. No ambient baseline or arm started, and no retry/poll was made. Receipt: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-resource-gate-readiness-20261020Tcurrent-no-launch.json` (SHA-256 `6a4fa3076eb21942f08d16483cf26daff23447de8714a53b5ef478f590aa5be5`). Future measurement requires new explicit authorization and one fresh passing preflight; do not delete unknown artifacts or weaken guards.

| Hypothesis | Offline evidence and status |
| --- | --- |
| H1 host carry-over/order | Reversal weakens a simple second-arm explanation for the two filter A/B pairs; three separate sync-reduction pairs show order-associated tails/rising I/O PSI. No cycle-aligned host telemetry in the filter pairs and no A/A arms here. Plausible confound, **inconclusive**. |
| H2 native vector build | Candidate p95 246.42/232.13 ms vs baseline 110.98/119.29 ms; writer-thread CPU delta was much smaller; tails occur without scheduled compaction. Strongest proximate-stage correlation, but worker CPU/host state missing; **causality unproven**. |
| H3 post-M4 finalization | Post-M4 p95 39.10 vs 33.14 ms candidate-first and 102.89 vs 50.01 ms reversed; includes `SyncTree`/snapshot finalization, not pure fsync. **Localized but confounded/inconclusive**. |
| H4 M4/vector interaction | M4-update p95 107.69/113.55 ms vs baseline 103.70/78.34 ms; M4 clone timer itself includes `SyncTree`; no same-cycle host/native-worker isolation. **Inconclusive**. |

No single root cause is established; best current characterization is compound wall-time delay across vector-build and sync/finalization-containing spans, with host carry-over plausible. Confidence in any single cause is low. No speculative code change is justified. Mission remains `PARTIAL / OPERATION_BLOCKED`; current-source sustained/adversarial validation and causal attribution remain open, production migration is not authorized.

## Containment signal propagation and failed-unit retirement — 2026-10-20

Bounded owner-requested runner correction only: bootstrap now maps subprocess `-signal` to conventional exit `128 + signal`, retaining signed `workload_returncode` and explicit `workload_signal` alongside `bootstrap_exit_status`. Unit retirement preserves systemd metadata and receipt before stop, verifies inactive/absent unit and empty/absent cgroup, then resets failed state. No `--collect` or early metadata removal. Unverified cleanup remains fail-closed and does not reset failed state.

Synthetic containment regressions **12/12 PASS**: exit 7, SIGTERM/SIGKILL receipts retain 7/−15/−9; bootstrap/systemd report 7/143/137; owned units are collected after metadata retention. Initial evidence: `containment-signal-retirement-20261020/`. Closure audit prevents a not-found unit from overriding a known populated cgroup and preserves `pre_retirement_outcome` on cleanup failure. Source-matched 12/12 closure regressions include loaded/not-found unit responses with a populated cgroup; both refuse reset and report `CLEANUP_FAILED` / `SAFETY_STOP`. Current source hashes, receipts and log: `containment-lifecycle-closure-20261020/`. No benchmark, readiness retry, production change, or broader harness repair.

## Mission closure: containment → causal engineering decision — 2026-10-20

**Status: PARTIAL / OPERATION_BLOCKED. Causal classification: INCONCLUSIVE; decision: DEFER performance correction.** Prior continuity is separately packaged in `b30c86f`; lifecycle repair/source-matched 12/12 tests in `bfaf443`. The sole readiness observation identified four failed supervisor-crash fixture units with already-cleaned cgroups. `7777af8` fixes that concrete test teardown omission: preserve crash metadata/receipt, verify cgroup absence, retire owned unit; focused suite 12/12 PASS. Only the four units matched to owned run-state/abort receipts were archived/retired, not unknown units or host processes. Evidence: `containment-crash-fixture-retirement-20261020/`.

The single evaluation passed all unchanged resource limits: MemAvailable **5,885,576 KiB**, `/var/tmp` free **19,843,407,872 B**, I/O PSI full avg10 **1.37%**, load-1 **2.67**. Overall readiness did not pass: user systemd was `degraded` at that observation, and the executable still uses diagnostic outer FTS file-sync elision including WAL after truncate, with durability equivalence explicitly unproven (§35). No new corruption is claimed, but correctness/reopen checks do not waive this mission prerequisite. No ambient baseline or arm launched, no baseline-pressure/cadence/integrity qualification, no retry after fixture cleanup. No-launch is not performance evidence.

All 59 compiler dependency hashes, CMake/binary, all six recorded shared-library hashes and 16-file build manifest match current artifacts. Binary `57f3a19e…ba342f`; generation lifecycle remains 3,581/0. No rebuild or production change.

New direct offline join validates **80 historical cycles and 320 reader events**. All readers released their snapshot before same-cycle M4 build/publish; max reader windows per arm were 4.075/4.665/7.825/8.616 ms. This weakens direct late reader-lock contention, not shared CPU/cache/writeback interference. Native vector span includes shard setup/open/conversion/upsert/flush **and durable shard metadata**, not pure HNSW; compaction aliases it, not an additional cost. Post-M4 contains sync and snapshot/Coordinator finalization, not pure fsync or manifest publish. Candidate first-four/later-sixteen commit medians were 337.907/232.241 ms candidate-first and 476.895/197.386 ms reversed. Early effects are descriptive, not excluded from reported latency. No independent percentiles were combined.

| Hypothesis | Support | Contradiction / limitation | Confidence / implication |
| --- | --- | --- | --- |
| H1 host/order | Separate pairs show order/PSI association; early clustering. | Candidate direction survives reversal; no A/A or cycle-aligned host PSI. | Low causal; test non-variant variability. |
| H2 native build | Large same-cycle spans with much lower writer-thread CPU. | Span includes durable metadata; native worker CPU unavailable. | High localization, low causality; existing substep timers needed. |
| H3 post-M4 | Reverse cycles 1/2 show 111.209/102.894 ms there. | Candidate-first cycle 1 tails with only 24.991 ms post-M4. | Medium localization, low causality; not sole cause. |
| H4 shared bottleneck | Same flush/metadata path; readers already gone at late stages. | New writer collection is not pinned readers' old collection; no named shared bottleneck measured. | Low; no speculative lock correction. |
| H5 measurement artifact | Different historical/current fingerprints, weaker reverse provenance, broad timers/known trace overhead. | Raw workload arrays/coherence consistent; direction repeated. | High limitations, low sole-cause confidence. |

**One proposed next experiment, requiring owner approval:** after restoring normal full outer file-sync behavior or an approved durability-equivalence proof, one diagnostics-off same-binary 500×20 A/A using existing fine-stage timers and host telemetry, fresh Btrfs roots and unchanged gates. It can reveal asymmetry without a code variant, not establish candidate regression. No new infrastructure, policy change or A/B campaign proposed. Runner/fixture correction CPU, RAM/PSS, physical writes and maintenance impact: **NOT MEASURED**. Existing evidence is exhausted at its timing resolution; missing historic host/worker telemetry cannot be reconstructed.

Full report/commands, raw-cycle joins, input hashes, provenance, no-launch and retention plan: `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/mission-closure-20261020/DECISION.md`; canonical handoff §47. The binary remains retained outside Git under the original results path, with exact hash/retention plan; raw arm stdout/process-sampling inputs are packaged separately from lifecycle. No push. This decision supersedes earlier next-action notes; further measurements need new authorization, not an automatic retry.

## Outer FTS/WAL durability restoration — 2026-10-20

Owner-authorized small research-only correction: `BuildDeltaVectorSnapshot` now performs full `SyncTree(generation_root)` before publication; the broad skip of `fts/` regular files is removed. Clone still skips only immutable hardlinked segment inodes. No WAL/snapshot design, file format, policy, native or production code changed. Previous outer skip omitted the file fsync after `FlushInternal` WAL truncate; directory fsync was not a substitute. Restores the original stronger sequence, not a physical power-loss guarantee.

Historical filter-identity A/B intended only an evaluator difference; no intentional write-order/WAL/fsync difference is documented, but complete original source/fingerprint provenance is insufficient for exact certification. Sync-reduction treatment/control **did deliberately differ** in outer FTS/WAL file syncs and cannot substantiate durability-equivalent performance claims.

Fresh Release binary **`ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f`**; old diagnostic `57f3a19e…ba342f` retained, not overwritten. New receipt records exact commands, flags, actual 61 compiler dependencies, ELF/runtime identity and six shared-library hashes. Only bridge source differs among the prior receipt's 59 inputs.

Focused tests: **1,401 vector/recovery + 582 incremental FTS checks, zero failures**. Five syscall/fault cases and four recovery oracles PASS: old binary lacks post-truncate outer WAL fsync; restored binary syncs it; injected EIO/SIGKILL at the decoded WAL fsync cannot publish the new generation; pre-publish crash retains old generation; post-publish-WAL crash promotes the new one with coherence. These are process-crash/error tests, not device/cache power-loss simulation. Initial fault-test format assertion failure is retained with diagnosis; a corrected strace signal assertion passed on fresh roots without weakening a guard.

Build/correctness/fault jobs were bounded transient cgroups; no measurement or readiness attempt. Reported peak memory 630.7M/933M/394.7M, swap zero. Physical writes, peak PSS, maintenance impact and steady-state latency **NOT MEASURED**. Writer-tail remains INCONCLUSIVE; further A/A requires new authorization and new full-sync provenance/readiness checks. Evidence: `durability-restore-20261020/REPORT.md`, manifests, build receipt/config, original/corrected syscall traces and recovery logs; canonical handoff §48. Both binaries remain retained outside Git at explicit result paths.

## Full-sync same-binary A/A: authorized single no-launch — 2026-10-20

**OPERATION_BLOCKED; zero arms, no ambient baseline or retry.** Prerequisites passed: full-sync executable `ef0221f0…1212e7f`, 61 compiler dependencies, all six actual linked-library hashes and 50 retained manifest entries match; systemd running, no failed units/active M4 tests, lifecycle source matches verified regression evidence; Btrfs resource root `/var/tmp`. Durability correction remains valid. Live arm containment/baseline-pressure/cadence integrity were not exercised after preflight refusal.

The sole evaluation at **12:05:35 +07** failed `M4-RG-MEM` **4,134,692 KiB < 5,477,762** (short 1,343,070) and `M4-RG-LOAD` **6.56 > 3.14** (excess 3.42). Disk **22,096,510,976 B** and I/O PSI full avg10 **1.06%** passed unchanged limits. Job `job-1335348-140` stopped at baseline preflight; no workload roots or arms created, no polling, guard relaxation or process signaling.

Metadata-only orchestrator correction selects the actual build receipt (`M4_AA_BUILD_RECEIPT`) and configured full-sync description instead of stale elision text/archive; no gate, workload or telemetry changes. Existing gate regressions 7/7 PASS. Receipts were checked to contain the actual full-sync identity.

Evidence under `m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/`: `full-sync-aa-20261020/` and `host-carryover-aa-pair-20261020T050534Z/`, sealed with checksums; canonical handoff §49. No new per-cycle comparisons are possible; H1–H5 and writer-tail cause remain INCONCLUSIVE. Historical measurements are not relabeled current full-sync results. Another attempt requires fresh authorization and all unchanged readiness requirements; no automatic retry. Production untouched.

### Later explicit owner authorizations — 2026-10-20

Two separate user messages authorized one attempt each; both stopped at their sole resource preflight, with no ambient baseline or arms:

- “chạy đi, tôi tắt zen rồi”, 12:13:05 +07: MemAvailable 9,133,088 KiB PASS; load-1 **4.84 > 3.14 FAIL**; disk 21,718,110,208 B and I/O PSI 1.95% PASS. Job `job-1335348-147`; receipts `full-sync-aa-20261020-after-zen/` and `host-carryover-aa-pair-20261020T051305Z/`.
- “retry đi”, 12:14:02 +07: MemAvailable **4,450,464 < 5,477,762 KiB FAIL** (short 1,027,298); load-1 **4.40 > 3.14 FAIL**; disk 21,698,637,824 B and I/O PSI 1.69% PASS. Job `job-1335348-149`; receipts `full-sync-aa-20261020-owner-retry-2/` and `host-carryover-aa-pair-20261020T051402Z/`.

All receipt paths are under the same writer-tail causal directory above. Full-sync binary/source/runtime checks and systemd running passed. No autonomous retry/poll, policy changes or process interference; historical/new host snapshots do not supply a latency experiment. Writer-tail remains INCONCLUSIVE; another attempt needs new authorization. Canonical handoff §50.

### New owner authorization: I/O PSI refusal — 2026-10-20

“ổn rồi đó, chạy đi” authorized one new attempt. Binary/compiler/runtime/manifest, lifecycle/systemd and Btrfs prerequisites passed. At **13:09:15 +07**, sole readiness preflight failed **M4-RG-IOPSI: 4.68% > 4.46%** (+0.22 percentage points); memory **9,933,104 KiB**, load-1 **1.53**, disk **21,736,423,424 B** passed. Job `job-1335348-153`: OPERATION_BLOCKED, no ambient baseline/arms/roots or automatic retry/poll. Under the causal directory, receipts/checksums: `full-sync-aa-20261020-owner-retry-3/` and `host-carryover-aa-pair-20261020T060915Z/`. No new performance evidence; writer-tail INCONCLUSIVE. Further attempt needs new authorization; canonical handoff §51.

## I/O PSI policy audit and inactive amendment — 2026-10-20

**OWNER_DECISION_REQUIRED; no benchmark/readiness/baseline capture.** Exact ID `M4-RG-IOPSI`: currently a hard operational resource guard in both modes (`HISTORICAL_ENVELOPE_RESOURCE_GUARD`), not a universal safety limit and not a current measurement-quality-only waiver. Its 4.46% full-avg10 threshold comes from the declared historical successful-run envelope. Legacy `hard_safety_failures` fields are compatibility aliases, not classification authority. Existing diagnostic continuation permits only valid stability-envelope failures after all resource, pressure, integrity and containment checks pass; it cannot waive I/O PSI at preflight, baseline, watchdog/runtime or recovery.

Draft `../docs/io-psi-diagnostic-amendment.proposed.md` proposes an explicitly approved, one-attempt full-sync 500×20 diagnostic A/A exception for valid I/O PSI exceedances only. Raw comparisons stay FAIL; evidence stays exploratory. Qualification/default behavior, 4.46% threshold, all other guards and recovery counts/timeouts remain unchanged. Missing/nonfinite/invalid telemetry is not eligible. Owner must accept that there is no independently established safe upper I/O pressure limit or delegated per-arm I/O control. Proposal approval must precede implementation and synthetic acceptance checks; a benchmark still requires explicit execution authorization. No live JSON or runner edits were made.

Validation: **5 policy + 7 A/A gate tests PASS**, plus synthetic exact-4.68% replay confirming both modes still block and diagnostic recovery refuses. Retained active-policy/runner hashes are identical before/after. Receipts: `io-psi-policy-audit-20261020/`; canonical handoff §52. No new host/performance observation; writer-tail remains INCONCLUSIVE. No retry is authorized by this audit.

## Contention-aware diagnostic engineering — 2026-10-20

**IMPLEMENTED / SYNTHETICALLY VERIFIED; real A/A NOT AUTHORIZED / NOT RUN; writer-tail INCONCLUSIVE.** Owner's real-contention mission replaces the prior narrow I/O-only exception design, without activating that draft or reclassifying old refusals. Qualification and default diagnostic retain every pre-existing executable policy field (comparison against `475ea4c` PASS). New explicit `contention-aware` mode records all four empirical historical guard PASS/FAIL comparisons but does not require an idle host. It instead requires an approved operation-bound resource plan: finite owned memory/swap/CPU caps, workload-derived RAM/swap/disk reservations, sampled workspace bounds, timeout/monitor resolution, current policy/binary identity, and separate execution/I/O-risk approval references. No real approved plan is supplied; the pending template is deliberately rejected.

Outer preflight, ambient samples, contained-arm preflight, runtime/watchdog, recovery and evidence receipts share the scoped context. Independent watchdog validates/read-backs kernel caps and acknowledges a canonical context digest before releasing M4. Missing/invalid/inconsistent/incomplete telemetry, real capacity shortfall, observed host OOM, owned allocation/swap/oom distress, cap/controller loss, timeout, correctness/durability/identity or cleanup failure still stop M4 only. Recovery observes three valid capacity/integrity windows, stopping on failure rather than waiting for idle. Existing index roots and duplicate operation reuse are refused. Results are always **EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING**; raw historical FAILs stay FAIL.

**51 tests PASS** (22 new contention, 5 shared policy, 4 harness contracts, 12 tiny real-systemd containment/lifecycle, 7 legacy gate, 1 tiny sequential-pair integration), source frozen before/after; fresh six-file `ty check` PASS. Intermediate controller-crash fixture failure retained: abort initiation was visible before socket cleanup; terminal cleanup publication/verification now removes the race without weakening assertions or extending deadlines. The exact failed fixture unit was verified and retired, not unrelated services. Final suite job `job-1335348-183`; no performance executable or readiness/ambient measurement launched.

Static full-sync verification PASS: **ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f**, source byte-identical to `7c88272`, 61 compiler inputs, six actual runtime libraries, 50 durability-manifest entries. Retained rebuild predates git commit publication; no post-commit-timestamp rebuild is claimed, and no C++ input changed. Existing corrected five-case fault/four-recovery evidence applies to these exact bytes, not physical power-loss safety.

Offline 80-cycle replay: long writer tails also occur without compaction (candidate-first candidate max 593.906 ms, native-vector elapsed/caller CPU 307.111/50.569 ms in that cycle). Native-worker CPU, runqueue vs blocking-I/O delay and per-syscall fsync wait remain unseparated; wall-minus-caller CPU is not identified I/O wait. Post-M4 residuals cannot all be called SyncTree without the newer split-stage telemetry. No speculative fix or performance win/regression is claimed.

Evidence/report: `contention-policy-20261020/REPORT.md`, test/provenance receipts, failed and final fixture records, reproducible offline review and checksums; policy guide and rejected template under `../docs/`; canonical handoff §53. **Remaining launch decision:** owner must choose/approve concrete evidence-based resource budgets and one operation, accept missing hard I/O isolation and sampled disk/observer limitations, and separately authorize a bounded pair. No idle-readiness loop or request to quiet other projects. No production/native/daemon/ABI/schema/index or unrelated Rust change.

## Autonomous writer-tail campaign preparation — 2026-10-20

**PARTIAL / OWNER_DECISION_REQUIRED_RESOURCE_PLAN; research INCONCLUSIVE, NOT COMPLETE.** New owner mission grants engineering/offline autonomy and a maximum three sequential diagnostic pairs **after resource/risk approval**. No envelope approved: campaign unactivated, **0/3**, no native benchmark, readiness/ambient observation or idle polling. No performance win/regression or production claim. A/B/C completion conditions remain unmet.

**`f6c182e`** fixes a concrete missing task/thread bound in existing contention containment: `pids_max`/TasksMax/pids.max, kernel read-back, task telemetry and fail-closed distress. Tiny owned-unit 64-task cap/cleanup verified; not a real approved budget. Qualification/default-diagnostic JSON unchanged outside the new section versus `6267e69`; historical raw FAILs preserved, no old guard relaxed. **56 distinct focused tests PASS**, fresh six-file `ty check` PASS; final selection in `analysis-release-v2-verification.json` and referenced receipts/logs. Earlier analysis checks are not counted twice.

Source exclusions: direct reader/schema-mutex contention on an unpublished new shard and intentional cohort waits within measured native/post-M4 stages do not fit inspected ownership/clock placement. Native-vector envelope includes collection/six scalar-index setup, HNSW, upsert, flush and durable metadata, so not pure HNSW. Old residual cannot isolate post-M4 SyncTree from finalization. No unsafe fsync/index removal or latency fix. New offline resource extraction from four retained arms: about 302 MiB process RSS, 4.96–6.38 s old wall, 216–239 MiB process writes, 6.85 MiB retained root; **not** cgroup/task/workspace peaks or current full-sync measurements. Pure offline counter bounds preserve read brackets/eight-digit clock serialization uncertainty and kernel accounting lag, without physical-wait attribution; no real causal output yet.

Concrete **pending** proposal: M4 1 GiB RAM/zero swap, two CPU-equivalent quota, 128 tasks; sampled 512 MiB allocated/1 GiB apparent workspace per arm; 1 GiB host-memory and 2 GiB disk reserves; watchdog 120 s (systemd 125 s), monitor gap 2 s; proposed GNU supervisor prefix `timeout --signal=TERM --kill-after=5s 355s` (360 s ceiling, not tool auto-background), maximum three sequential pairs; verified owned cleanup still required. These are unvalidated administrative budgets, not safety facts. No hard IO/total-write isolation, disk quota, exact device attribution or observer hard caps. Shared Btrfs/writeback/sampled overshoot/inherited-limit/other-project risks require explicit owner acceptance. Current proposal policy SHA `be7eca56…`; no approval inferred from engineering autonomy.

Static current full-sync ELF `ef0221f0…`, 61 compiler inputs, six runtime libraries and 50 durability entries revalidated; retained corrected fault/recovery evidence applies to exact bytes. No rebuild/correctness repetition. First static-module import lacked required environment **before any guard ran**; configuration failure retained separately, then first actual static verification PASS. No failed operation retried. Production/native libraries/daemon/ABI/schema/indexes and unrelated Rust edits untouched.

Evidence, source-map/hypotheses, pending exact JSON, test/provenance receipts, reproducible extraction, conservative counter analysis and budget ledger: `autonomous-campaign-20261020/`; canonical handoff **§54**. **Next:** owner approves/edits proposed envelope/IO risks for up to three named pairs; then one fresh capacity/provenance decision and one bounded pair, analyze before another. Claimed/preflighted refusal/abort consumes its slot; not permission for automatic retry or cap increase. Do not spend slots merely to exhaust budget. Research acceptance remains open.
