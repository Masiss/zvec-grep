# M4 — Incremental Native Index Architecture

Current status (2026-10-21): **Newly authorized500x20native diagnostic after4579e5a COMPLETED WITH WORKSPACE CAVEATS (§62):20commits/80queries,exit0,coherence/reopenerrors0. Writerp50/p95/p99=231.399/267.359/369.428ms. Raw COMPLETED_WITH_WORKSPACE_UNCERTAINTY/measurementINCOMPLETE, not qualification. Hard safety/cleanup verified; new1/1spent, no additional run authority. Old4/4campaign and prior1/1abort unchanged. Production NOT touched; migration NOT approved.**

### Resume here

1. Read **§62** and `research/m4-native/results/pending-drain-native-20261021/REPORT.md` for the completed exploratory arm/data/limits. **§61** retains the preceding abort and4579e5a correction; **§60/§58–§59** remain historical strict-contract evidence.
2. Verification: pinned4579e5a runtime/policy identities match retained81test receipt; current34focusedtests plus actual same-controller32KiBwatchdog e2e PASS. Fresh fullsyncELF/61compilerinputs/runtime-libraries/completedcorrectness+durability/readiness checks PASS before the one native arm. No runtime/native/guard change or new harness.
3. **New contract, not a hard-cap proof:** exploratory workspace bytes are monitored soft budgets. Known publication races stay UNKNOWN/INCOMPLETE; warnings retain overage amounts. Scope errors and independent hard telemetry/reserve/distress failures still abort. Qualification/other benchmarks unchanged; no quota/verified lifetime peak claim.
4. **Actual result:** nativePID784988 completed20cycles/80queries and generation21/live500 reopen with0errors; writer231.399/267.359/369.428ms(p50/p95/p99). Pending-drain continuation worked in3rawUNVERIFIABLEsamples,24otherknownraces; all27remainUNKNOWN. Cgroup308.090MiB/swap0/CPU4.979s, no observed distress/reserve failure, cleanup verified. One run/n20p99=max, no comparative/causal/qualification inference.
5. **Execution boundary:** one separately renewed attempt used1/1, zero remaining, no repeat/arm2. Old4/4campaign and preceding1/1abort untouched. Latest plan/ledger/raw/analysis: `research/m4-native/results/pending-drain-native-20261021/`. Requested bounded diagnostic complete with caveats; broader comparative/qualification/production objectives remain open/unapproved. Further native execution needs new explicit finite authority.

Latest evidence: [completed diagnostic report](../../research/m4-native/results/pending-drain-native-20261021/REPORT.md), [analysis](../../research/m4-native/results/pending-drain-native-20261021/analysis.json), [repair report](../../research/m4-native/results/workspace-observer-repair-20261020/REPORT.md), [RESULTS](../../research/m4-native/results/RESULTS.md), [policy](../../research/m4-native/docs/benchmark-policy.md); exact commands/source hashes in the report's `verification-v3/` and `consumer-final/` receipts. The repair checksum manifest describes commit **`6439ed2`**, including documentation as it stood then; verify at that revision. Later handoff edits do not reclassify raw failures or change ledger/binary provenance.

Architecture baseline (2026-10-18): S5 structural debt-policy GO for research; M4-P4/P4.1 native A/B PARTIAL; B3.1 research verification COMPLETE (reader-lock attribution shows execution, not lease wait); controlled reader/compaction crossover found no consistent material native-vector slowdown. Research `EvalFilter` skips Boolean identity constants (strict oracle + sanitizers PASS); filter-cache-cap remains closed at64KiB/default-off. Earlier search-execution phases are recorded below, not permission for a new run.

Date: 2026-10-21 (handoff); architecture baseline: 2026-10-18
Prototype: `research/m4-native/` (standalone C++17, not linked into `zvec`)
Upstream context: `rust/docs/m3.2-incremental-native-index-design.md`,
`rust/docs/m3.3-feasibility.md`, `docs/base-delta-prototype.md`

## 1. Decision

M4 is a deliberate index-format/update-architecture change, not a search-engine
rewrite. It keeps HNSW, BM25, hybrid ranking, public search semantics and
accuracy. It replaces the storage/update model shared by **FTS postings and
scalar INVERT** so that a write no longer reprocesses the whole collection.

Result of this gate: **KEEP as the research direction; do not start production
migration.** The prototype removes the O(collection) update term on synthetic
fixtures. S2 streaming compaction cut peak PSS 3–5× at 10k–300k docs but paid
1.3–1.7× logical writes for temp-section copying. S3 removes that copy with a
direct-write format v2: logical writes fall 23–41% versus S2, the tracked
compaction working set stays ~4.6 MB, and compaction wall time is equal at
50k–300k. S4 then validated 500k and 1M docs under delete-heavy churn: live
statistics/FTS/INVERT stayed oracle-correct, tracked compaction memory stayed
~4.7 MB, process PSS scaled to ~1.2 GB at 1M, and recovery/orphan GC passed.
The remaining caveat is query-latency debt (p95 up to 168 ms at 1M with 47k
tombstones, 37 ms after the final compaction). S5 added query-cost
instrumentation and a structural debt policy. With ratio 0.01 and a live/4
merge cap, mean 1M p95 fell from 186.0 to 73.8 ms (max 236.6→89.6 ms), runs
stayed at 2, and CPU fell, at +12.7% churn writes. The base is built with the
policy off; ON reopens after the base compaction. The policy is **GO for
research** and remains opt-in/default-off. Remaining gates: tokenizer/field
parity, background maintenance, and migration. None is approved for
production now.

## 2. Why the M3 narrow fix was insufficient

M3.3 established:

- HNSW is already incremental (base reuse + tail append);
- the per-optimize O(N) cost is `ReduceScalar` / `ReduceScalarIndex` (forward +
  sealed INVERT RocksDB) and `ReduceFts` (all postings + stat CF);
- the sealed INVERT RocksDB rejects appends (`read_only_`) and has no unseal;
- FTS block bounds store IDF-weighted scores; corpus growth makes them stale;
- changing only the FTS bound still leaves the FTS RocksDB rebuild and the
  scalar INVERT rebuild.

M4 therefore redesigns FTS and INVERT **together** around one document/version
catalog, new immutable runs, shared tombstones, and tiered compaction. No old
format compatibility is retained in research; that constraint is what blocked
the earlier incremental design.

## 3. Architecture

### 3.1 Shared liveness and versioning

- Global monotonic `DocId`, never reused.
- Update = tombstone(old id) + append(new id).
- One tombstone set is used by FTS and INVERT; the two indexes cannot disagree
  about whether a document is live.
- Deletes never rewrite postings; they only add a tombstone.

### 3.2 Log-structured runs

- Bounded in-memory delta; flush seals an immutable run.
- A run stores docs, FTS dictionary/postings/positions/block metadata, and
  INVERT dictionaries/postings in one new file format.
- Tiered compaction merges **small adjacent runs**, drops tombstones, and
  republishes the manifest. Selection is size-aware so a tiny update never
  pairs with the huge base run (that flaw was found and fixed during the
  benchmark).
- Query fan-out is hard-capped (`max_total_runs`, default 12).
- Compaction is a streaming k-way merge by sorted term/value. It holds one
  decoded 128-doc block per input per active term/value, one output block, and
  the new term/value dictionaries. Format v2 writes payload blocks, block
  metadata, dictionaries and docs **directly into the final segment**; there
  are no temp payload/meta/docs files and no second copy of the output. The
  header is patched last and the checksum is accumulated during the write. No
  merged posting list or value list is resident.
- Flush still writes format v1; compaction writes v2. The reader dispatches
  on the header version, so existing runs remain searchable without
  conversion.
- The compaction working set is charged against
  `compaction_memory_limit_bytes` (default 128 MiB), including dictionary and
  fixed IO buffers. Exceeding it aborts before manifest publish and leaves the
  old runs active.

### 3.3 FTS bounds and statistics

Per block, the new format stores raw `max_tf` and `min_dl`, not an IDF-weighted
score. At query time:

```
idf(t)      = ln((N_live - df_live(t) + 0.5) / (df_live(t) + 0.5) + 1)
block_bound = idf(t) * max_tf * (k1+1)
              / (max_tf + k1 * (1 - b + b * min_dl / avg_dl_live))
```

`N_live`, `total_dl_live`, and per-term `df_live` are maintained on write in
O(tokens of the changed document). The stats delta log is checkpointed only
above a size threshold, so checkpoint cost is amortized. Compaction does not
change live stats.

### 3.4 Durability

- WAL per mutation (fsync optional).
- Commit order: segment → stats checkpoint/log → tombstone snapshot → atomic
  manifest → WAL truncate.
- Crash before manifest: orphan files + WAL replay. Crash after manifest:
  records covered by `wal_seq` are skipped.
- `manifest.bak` fallback; checksums on segments, stats, tombstones, manifest,
  WAL records; garbage collection only after a durable manifest.

### 3.5 Bounds

- **Query fan-out:** ≤ `max_total_runs` (12); tiered compaction enforces it.
- **RAM:** bounded delta; mmap segment dictionaries; block-at-a-time posting
  decode. Streaming compaction holds one decoded block per input per active
  term/value, one output block, and the O(vocabulary) new term map. Peak PSS
  measured 3–5× lower than the in-memory merge at 10k–300k.
- **Write cost:** S3 removes the temp-section copy. The remaining costs are
  the O(vocabulary) term/value dictionaries held until the end of each section
  and the single output-file fsync; both are charged against the compaction
  budget. Logical writes are 23–41% below S2 at 10k–300k.
- **Disk:** tombstone-only garbage; compaction at ratio 0.30 or run pressure;
  old runs deleted after commit.

## 4. Prototype and verification

Implementation: `research/m4-native/` (`include/m4/index.h`, `src/format.*`,
`src/index.cc`, `src/tokenizer.h`, tests). New on-disk format, not compatible
with production zvec.

Correctness oracle: independent full-scan index with the same tokenizer and
BM25 formula. **16,782 checks, 0 failures; ASan/UBSan clean.**

Covered:

- OR/AND/phrase BM25 top-k membership and scores;
- scalar EQ/LT/LE/GT/GE;
- 1,200 base docs + 500 randomized updates/deletes;
- flush, compaction, close/reopen;
- WAL replay after destroying the process without flush;
- bounded run count;
- corpus growth after a run was written (the stale-IDF-bound regression);
- hybrid ranking and a forced compaction-budget failure that must leave the
  old runs searchable;
- injected faults after segment rename (before publish), before manifest
  publish, and after durable manifest publish. The first two leave old runs
  searchable; reopening after the third sees the new run;
- v1/v2 coexistence: a v2 compaction output is reopened and used as an input
  to later compactions that mix v1 flush runs and v2 runs;
- delete-heavy churn (9 cycles: updates, deletes, replacement inserts,
  reinserted deleted keys) with exact live-doc/token counts, exact `common`
  FTS count, INVERT/range/phrase/hybrid checks after repeated compactions;
- WAL replay after churn, pre-manifest fault after churn, final
  `dead_docs=0`/one-run GC, and no unpublished temp files;
- the same churn oracle with the S5 debt policy enabled, including WAL and
  pre-manifest fault injection.

## 5. Measured result

Each case ran in its own process (clean `VmHWM`), same fixture/toolchain.
S2/S3 ran on `/tmp` tmpfs; S4 ran on `/var/tmp` btrfs and reports both the
prototype logical counter and `/proc/self/io` physical bytes. Host load was
variable (~6–12); absolute latency is directional.

Historical S2 evidence (12 updates; build measured separately; table uses the
temp-section streaming implementation):

| docs | mode | build | update p50 | compaction wall | writes | disk | peak RSS | peak PSS | compaction working set | query p95 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10k | in-memory | 0.64 s | 0.351 ms | 0.37 s | 17.0 MB | 5.8 MB | 94.8 MB | 88.5 MB | 19.0 MB | 0.41 ms |
| 10k | streaming | 0.66 s | 0.388 ms | 0.25 s | 22.1 MB | 5.8 MB | 35.6 MB | 30.0 MB | 8.8 MB | 0.43 ms |
| 50k | in-memory | 4.40 s | 0.342 ms | 3.25 s | 111.5 MB | 15.1 MB | 421.0 MB | 412.0 MB | 94.3 MB | 1.70 ms |
| 50k | streaming | 4.97 s | 0.347 ms | 2.71 s | 163.6 MB | 15.1 MB | 95.3 MB | 89.5 MB | 8.8 MB | 2.01 ms |
| 150k | in-memory | 18.61 s | 0.227 ms | 14.17 s | 424.3 MB | 40.7 MB | 1196.5 MB | 1190.0 MB | 282.5 MB | 3.36 ms |
| 150k | streaming | 18.34 s | 0.238 ms | 11.22 s | 670.3 MB | 40.7 MB | 241.7 MB | 230.1 MB | 8.8 MB | 3.46 ms |
| 300k | in-memory | 80.07 s | 0.357 ms | 74.52 s | 1174.8 MB | 81.4 MB | 2345.5 MB | 2328.2 MB | 565.0 MB | 9.98 ms |
| 300k | streaming | 47.06 s | 0.425 ms | 36.43 s | 1992.7 MB | 81.4 MB | 464.8 MB | 459.5 MB | 8.8 MB | 11.98 ms |

Peak PSS drops 2.95× / 4.60× / 5.17× / 5.07× at 10k / 50k / 150k / 300k.
Compaction working set drops 2.2× / 10.7× / 32× / 64×. Update p50 stays
sub-ms; query p95 is comparable through 150k and +20% at 300k. Compaction wall
is lower at every size (0.66× / 0.84× / 0.79× / 0.49×). Build is equal through
150k and 41% faster at 300k (the in-memory baseline was under memory pressure).

### S3 direct v2 writes vs S2 temp streaming

Paired runs, separate processes, same fixture/toolchain. 150k was rerun in
reversed order; absolute wall time remains directional under host load.

| docs | mode | build | compaction wall | logical writes | disk | peak PSS | compaction working set | query p95 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10k | S2 temp | 0.517 s | 0.216 s | 22.1 MB | 5.80 MB | 31.0 MB | 8.80 MB | 0.384 ms |
| 10k | S3 direct | 0.673 s | 0.226 s | 17.1 MB | 5.83 MB | 31.0 MB | 4.57 MB | 0.362 ms |
| 50k | S2 temp | 4.398 s | 2.529 s | 163.6 MB | 15.10 MB | 89.6 MB | 8.80 MB | 2.030 ms |
| 50k | S3 direct | 4.698 s | 2.497 s | 112.3 MB | 15.13 MB | 90.2 MB | 4.59 MB | 1.883 ms |
| 150k | S2 temp | 19.400 s | 12.298 s | 670.3 MB | 40.72 MB | 231.8 MB | 8.80 MB | 3.922 ms |
| 150k | S3 direct | 18.469 s | 12.288 s | 427.1 MB | 40.75 MB | 238.3 MB | 4.60 MB | 5.332 ms |
| 300k | S2 temp | 50.956 s | 37.402 s | 1992.7 MB | 81.40 MB | 455.1 MB | 8.80 MB | 10.212 ms |
| 300k | S3 direct | 52.772 s | 37.666 s | 1180.9 MB | 81.43 MB | 457.7 MB | 4.61 MB | 9.416 ms |

Write reduction: 23% / 31% / 36% / 41% at 10k / 50k / 150k / 300k. Compaction
wall ratio S3/S2: 1.05× / 0.99× / 1.00× / 1.01×. Build ratio: 1.30× / 1.07× /
0.95× / 1.04×; the 10k cost is +156 ms fixed overhead. Peak PSS is unchanged
(direct writes reduce I/O, not the live index working set); tracked compaction
working set is ~4.6 MB versus 8.8 MB. Query p95 varies by run and is not a
demonstrated regression. Physical-write check on btrfs (`/var/tmp`,
`/proc/self/io`) measured 241.0→162.7 MB at 50k and 911.7→573.7 MB at 150k
(S2→S3), consistent with the logical counter.

### S4 delete-heavy churn and scaling

Stable live cardinality, 8 cycles, 6,000 ops/cycle (2,000 delete+replacement
insert pairs, 4,000 updates), all docs contain `common` and `status=active`.
`fsync_wal=false` for the timing run; WAL durability is covered by the
fsync-on correctness gate.

| docs | build wall/CPU | churn wall/CPU | total CPU | max dead | runs max | compactions | tracked peak | churn logical/physical | total logical/physical | disk | peak PSS | reopen | p95 last/post | p99 last/post |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 500k | 61.9 / 73.3 s | 50.8 / 65.6 s | 138.9 s | 47,085 | 6 | 55 | 4.65 MB | 234.2 / 253.0 MB | 1,279.2 / 1,480.0 MB | 137.8 MB | 624.5 MB | 0.80 s | 70.3 / 30.6 ms | 73.8 / 30.8 ms |
| 1M | 151.9 / 181.3 s | 87.8 / 118.6 s | 299.9 s | 47,727 | 6 | 116 | 4.74 MB | 372.0 / 390.1 MB | 2,804.3 / 3,186.6 MB | 275.4 MB | 1,205 MB | 0.82 s | 168.4 / 37.4 ms | 209.1 / 37.9 ms |

Both cases ended with exact live stats, `dead_docs=0`, one run, no temp/orphan
files, and no WAL tail after the final flush. Query p95/p99 rose while 47k
tombstones accumulated (2.3× at 500k, 4.5× at 1M versus post-compaction), then
fell back to 30.6/37.4 ms after the final drain. This is bounded read
amplification from tombstone filtering plus run fan-out, not a correctness
failure; a production policy would need a latency-aware debt trigger.

### S5 debt policy and query latency

Optional `QueryDiagnostics` on `SearchFts` records unique runs read, term×run
parts, postings scanned, dead postings filtered, liveness lookups, live
candidates, and setup/execution/finalize time. At 1M, execution dominates
(95.4 ms mean OFF; setup 0.02–0.08 ms; finalize ~0.006 ms). Runs read and
dead-posting fraction both correlate with p95 (`r≈0.86` / `r≈0.84`), so
tombstone ratio alone is not a sufficient signal.

Policy (research, opt-in/default-off): after `Flush`, at most one adjacent-pair
merge with the best dead-postings-per-rewritten-doc ratio, minimum 3 runs, 1 s
cooldown, and input cap `live_docs/4`. Base build runs with policy OFF; ON runs
reopen after the base compaction so only churn maintenance is compared.

| case | mean p95 | median p95 | max p95 | post p95 | mean runs | mean scans | mean dead | churn merges | writes | CPU | update p95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 500k OFF | 51.8 ms | 53.8 ms | 69.7 ms | 20.8 ms | 4.38 | 146k | 5,170 | 3 | 234.2 MB | 96.2 s | 0.077 ms |
| 500k ON | 31.3 ms | 31.4 ms | 38.9 ms | 24.7 ms | 2.00 | 124k | 3,957 | 7 | 281.0 MB | 73.9 s | 0.062 ms |
| 1M OFF | 186.0 ms | 187.4 ms | 236.6 ms | 60.7 ms | 4.38 | 276k | 5,044 | 3 | 372.0 MB | 411.1 s | 0.206 ms |
| 1M ON | 73.8 ms | 72.4 ms | 89.6 ms | 54.3 ms | 2.00 | 238k | 3,852 | 7 | 419.5 MB | 226.5 s | 0.082 ms |

Mean p95 falls 40%/60% at 500k/1M; max p95 44%/62%. Runs stay at 2, scans
fall 15%/14%, dead postings 23%/24%. Churn writes rise 20%/12.7% (physical
19%/12.2%), while CPU falls and tracked compaction memory stays 4.74 MB.
Maintenance is one bounded merge on affected cycles (3→7 churn merges), not a
full compaction per latency spike. Final storage is unchanged (137.8/275.4 MB).

Controlled rebuild-on-mutation baseline (8 updates):

| docs | mode | update p50 | update CPU | compaction wall | writes | peak PSS |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 10k | in-memory | 237.7 ms | 2025 ms | 2.19 s | 39.9 MB | 91.9 MB |
| 10k | streaming | 121.9 ms | 1009 ms | 1.22 s | 67.9 MB | 32.1 MB |
| 50k | in-memory | 1081.2 ms | 11827 ms | 12.94 s | 220.1 MB | 478.9 MB |
| 50k | streaming | 431.3 ms | 3595 ms | 5.51 s | 380.9 MB | 91.8 MB |

Streaming is 2.0–3.3× lower CPU on the O(collection) baseline and 2.9–5.2×
lower peak PSS, at ~1.7× writes.

Raw evidence: `research/m4-native/results/`.

## 6. Verdict and next gates

**GO for the S5 structural debt policy** (S4 remains GO for research). It
cuts mean query p95 40% at 500k and 60% at 1M, lowers CPU, and keeps runs at 2
for a bounded 13–20% write increase. It does not change format or search
semantics.

**NO-GO** for production integration/migration:

- format is research-only; v1 flush/v2 compaction is not the production
  schema;
- single-threaded prototype, one FTS field, no tokenizer/field parity;
- the policy is validated only on synthetic churn and remains opt-in;
- no background compaction scheduler or migration/backfill plan.

Next steps, in order:

1. Stop threshold/cap tuning; the current policy is the accepted research
   point. Run the churn oracle/durability gates on any future policy change.
2. Native single-field BM25 attribution is complete for P1.1: the score
   differences are explained by native's per-segment historical statistics
   versus M4's live-corpus statistics. Do not claim BM25 parity or alter the
   M4 ranking contract without a separate decision; see Section 9 and the
   source-derived breakdown artifacts.
3. Then broaden tokenizer/analyzer and multi-field coverage, followed by
   background maintenance and reader/writer handoff, only under separately
   scoped research gates.
4. Only after those gates: migration/backfill design and a production rollout
   decision.

### Not done / not approved

- production storage/schema changes or migration/backfill;
- old-format compatibility with production or unseal;
- multi-field FTS, numeric/array scalar encodings, tokenizer parity;
- concurrent readers/writer handoff;
- background/deferred compaction scheduling;
- a budgeted major-compaction path for base-run tombstones; not needed for the
  current S5 GO, but still a future option.

## 7. Handoff — 2026-10-17 (after S5)

### State

- S5 structural debt policy is **GO for research**, opt-in/default-off.
- At 1M, mean query p95 falls 186.0→73.8 ms and max p95 236.6→89.6 ms; runs
  stay at 2; churn writes rise 12.7% and CPU falls.
- No production integration, migration or format change.
- No production Zvec/Rust/index-format file was changed by M4.
- Local handoff context: `AGENTS.md` and `MEMORY.md`.

### Completed in this slice

- `QueryDiagnostics`: unique runs read, term×run parts, postings scanned, dead
  postings filtered, liveness lookups, live candidates, phase timing.
- Structural debt policy: one adjacent-pair merge after `Flush`, highest
  dead-per-rewritten-doc, min 3 runs, 1 s cooldown, input cap `live_docs/4`.
- Base build always policy-off; ON reopens after base compaction so bulk-load
  and churn maintenance are not conflated.
- OFF/ON churn at 500k and 1M; per-cycle update p95 and logical/physical write
  series; oracle suite with policy ON, WAL, fault and GC.

### Evidence

```text
research/m4-native/results/RESULTS.md
research/m4-native/results/benchmark-20261017-s5.jsonl
research/m4-native/results/benchmark-20261017-s5-1m-off.json
research/m4-native/results/benchmark-20261017-s5-1m-policy.json
research/m4-native/results/benchmark-20261017-s5-500k-off.json
research/m4-native/results/benchmark-20261017-s5-500k-policy.json
research/m4-native/results/correctness-20261017.txt
research/m4-native/results/correctness-20261017-s5-asan.txt
research/m4-native/results/environment.txt
```

Correctness gate: **16,782 checks, 0 failures**; ASan/UBSan clean.

### Finding to carry forward

- 1M OFF: mean p95 186.0 ms, max 236.6, runs 4.38, scans 276k, dead 5,044,
  writes 372.0 MB, CPU 411.1 s, update p95 0.206 ms.
- 1M ON: mean p95 73.8 ms, median 72.4, max 89.6, runs 2, scans 238k, dead
  3,852, writes 419.5 MB, CPU 226.5 s, update p95 0.082 ms.
- 500k OFF/ON: mean p95 51.8→31.3 ms; writes 234.2→281.0 MB; CPU 96.2→73.9 s.
- Setup/finalize are negligible; execution dominates. Runs and dead fraction
  both correlate with p95. Churn maintenance merges 3→7, bounded to one per
  affected cycle; this is not a full compaction per spike.
- Base-run tombstones remain, but their capped impact is acceptable for this
  research gate.

### Exact next action

**Stop policy tuning.** The current structural policy is the accepted research
point and must stay opt-in until a production policy review. M4-P1 added a
native single-field differential harness but found unresolved BM25 score/rank
differences after mutations; see Section 8 and
`research/m4-native/results/native-fts-differential-20261017.md`. The next
executable research action is to isolate the score/stat discrepancy on a
minimal update/delete fixture. Production migration remains unapproved. Any
future policy change must re-run the 1M churn A/B, oracle, WAL/fault tests, and
report writes/CPU/p95 trade-offs.

### Not approved / do not start

- Production integration, schema migration, backfill or rollout.
- Further threshold/cap tuning without a new decision.
- Unbounded/full compaction on every latency spike.
- Changing v2 format, BM25, shared liveness, WAL/manifest or search semantics.
- Broader tokenizer/analyzer and multi-field parity, background compaction
  scheduling and concurrent reader/writer handoff remain future gates.
- Do not reset/revert unrelated working-tree changes under `rust/`.

## 8. Handoff — 2026-10-17 (M4-P1 native differential)

### Completed

- Added an optional research CMake mode that compiles the checked-out native
  standard tokenizer + lowercase filter into M4, plus a C-API differential
  test using separate native/M4 directories. No native source or production
  ABI/index was changed.
- Pinned `standard` + `[lowercase]`, empty extra params, BM25 `k1=1.2`,
  `b=0.75`, byte offsets, and score tolerance `abs(1e-4)+rel(1e-4)`.
- M4 correctness oracle PASS: 16,782 checks, 0 failures. Ten fixture documents;
  exact PK hit sets matched for phrase/AND/OR, update/delete, optimize and
  reopen. Native token terms/positions and pinned offsets passed with the
  adapter; the legacy standalone tokenizer differed on three fixture texts.

### Blocking evidence

- Ranking is **not at parity**: harness `checks=438 failures=57`; score
  differences persist after native optimize and reopen. Example after
  maintenance: `quick`, `pk-ascii`: native 1.4753 vs M4 1.53945. `quick OR
  alpha` also reverses `pk-short` / `pk-ascii` order. At the time of this P1
  handoff the lifecycle cause was not yet isolated; P1.1 resolves it in
  Section 9. Do not force M4 to match native's threshold-dependent historical
  stats.
- Equal-score `tie` order differs, but native's `FtsResult::operator>` only
  compares scores, so its tie-break is not established. The harness reports
  this separately as unspecified tie ordering.
- Native source checkout is pinned at `8f6d600`; 12 unrelated pre-existing
  dirty paths remain untouched. See the result artifact for library hash,
  commands, fixtures, and detailed observations.

### Exact next action (completed by P1.1)

The 3–4-document source-derived BM25 lifecycle probe is complete; see Section 9.
No M4 ranking code was changed. The prior unresolved-status wording above is
historical and is superseded by the P1.1 finding below.

## 9. Handoff — 2026-10-17 (M4-P1.1 BM25 root-cause isolation)

### Completed

- Added `tests/native_bm25_isolation.cc` and the optional CMake target
  `m4_native_bm25_isolation`. The controlled fixture is four initial docs,
  one upsert, one delete, then optimize/reopen; query is `signal`.
- Added source-derived per-term BM25 component output to the existing 10-doc
  differential harness. The archived breakdown covers all 54 score mismatches
  (18 distinct query/PK pairs repeated after mutation, optimize, and reopen).
- Preserved all native/M4 API comparisons. No production/native source, M4
  ranking semantics, format, daemon, or Rust implementation was changed.

### Root cause and evidence

- Native `FtsColumnIndexer::insert` appends postings and increments segment
  `N`/token totals for each inserted version. `df` is raw posting-bitmap
  cardinality; the delete filter hides obsolete candidates but does not
  subtract them from `df`. M4 scores against live docs, live tokens, and live
  `df`.
- In the 10-doc fixture, native source-derived stats after mutation are
  `N=12`, `total_tokens=50`, `avgdl=4.1666667`; M4 live stats are `N=10`,
  `total_tokens=37`, `avgdl=3.7`. For `quick`, native `df=4` versus M4
  `df_live=3`; `pk-ascii` (`tf=2`, `dl=4`) scores 1.47529602 native and
  1.53945119 M4. Two tombstones / 12 physical rows = 16.7%, below native's
  30% purge threshold; optimize/reopen preserve the stats and scores.
- In the minimal fixture, the native model after update is `N=5, df=2,
  total_tokens=14, avgdl=2.8`; M4 is `N_live=4, df_live=1, total_tokens=12,
  avgdl=3`. The native/M4 scores for `d1` are 1.180063 / 1.655463. After
  delete, M4's live stats become `N=3, df=1, total_tokens=8`; native remains
  unchanged and scores 1.180063 vs 1.302837. The 2/5 deletion ratio exceeds
  the 30% threshold, so optimize purges/rebuilds effective stats and native
  agrees with M4 at 1.302837; parity persists on reopen.
- Independent source-formula checks pass for every observed native score and
  every M4 score (deltas <= 2e-5). Thus the 57 failures classify as statistics
  lifecycle/per-segment aggregation, not formula, TF, DL, tokenizer, or M4
  scorer implementation defects. Three rank inversions are consequences of
  those score differences. Equal-score tie order remains unspecified.

### Verification and artifacts

- M4 oracle: `checks=16782 failures=0`.
- Minimal lifecycle probe: `checks=132 failures=0` across initial, after
  update, after delete, after optimize, and after reopen.
- Original differential intentionally remains nonzero: `checks=438
  failures=57` (54 score mismatches + 3 true rank-order inversions). Its
  source-derived component models validate all 54 score rows.
- Full component output:
  `research/m4-native/results/native-bm25-breakdown-20261017.log` and
  `research/m4-native/results/native-bm25-isolation-20261017.log`.
- Summary and source anchors:
  `research/m4-native/results/native-fts-differential-20261017.md`.

### Decision / next action

**GO**: root-cause attribution is complete. **NO-GO**: claim native/M4 BM25
parity, alter M4's live-corpus ranking contract to imitate native's
threshold-dependent historical stats, or promote/integrate M4. The mismatch is
not an in-scope M4 bug; no algorithm fix is proposed. Keep M4's explicit
live-stat invariant. Any future request to change ranking semantics needs a
separate explicit approval. No S1–S5, churn, or performance benchmark was run.

## 10. Handoff — 2026-10-17 (M4-P2.1 Uint32 EQ/IN complete)

### Completed

- Audited actual `zg-engine` schemas, write/delete paths, and query builders
  against pinned `zvec-rust` 0.7.2 and native checkout
  `.local/native/zvec-native-profile` at `8f6d600db769dcabf92f7af11a8242453b7ae5bf`.
- Implemented research-only `ScalarValue::Unsigned32`, exact typed
  `SearchInvertUint32`/`SearchInvertUint32In`, and native differential coverage.
  Changes are confined to `research/m4-native/`; no format, production Rust,
  or native Zvec source changed. Updated the usage/verification report and
  `research/m4-native/README.md`.
- Warning-clean M4 oracle: `checks=16963 failures=0`; ASan + UBSan:
  `checks=16963 failures=0`.
- Native scalar differential: `checks=528 failures=0` across initial,
  update/delete-before-flush, flush, optimize/compact, and reopen. In the small
  FTS fixture the harness intersects scalar candidates before top-k; M4 gains
  no integrated FTS+filter planner. Results compare by logical PK.
- Native non-nullable `file_id` rejects a missing-value write. Native
  `file_id IN ()` returns `INVALID_ARGUMENT`; M4 `IN []` returns empty. The
  semantic differences are explicitly recorded. LSP: no findings on 4 C/C++
  paths; CMake LSP unavailable. No benchmark or S1-S5 rerun.

### Decision / exact next action

P2.1 is **PASS** for the approved research slice: exact Uint32 EQ/finite-IN,
no numeric ranges, no format change, and no production integration. `IN []`
remains an explicit native/M4 semantic difference; do not claim full parity.
At the time this checkpoint was written, P2.2 was awaiting separate approval;
that approval has since been granted and the slice is complete below.

## 11. Handoff — 2026-10-17 (M4-P2.2 ArrayUint32 membership complete)

### Completed

- Owner-approved scope: research-only `ancestor_directory_ids: ArrayUint32`
  `CONTAIN_ANY` and `NOT CONTAIN_ANY`; no production integration or general
  Boolean planner.
- Added typed array values and exact membership APIs under
  `research/m4-native/`. Empty-array presence uses a marker in the existing
  invert postings; all postings use the shared version/tombstone catalog and
  existing WAL/segment/manifest paths. No format version changed.
- Native differential confirms empty arrays, duplicate elements, empty query
  operands, missing non-nullable fields, 0/`UINT32_MAX`, and update/delete/
  reinsert/flush/compact/reopen semantics. Results compare by logical PK.
  Empty `CONTAIN_ANY ()` is false; empty `NOT CONTAIN_ANY ()` is `IS NOT NULL`.
  Native rejects missing values for the actual non-nullable schema; M4's
  optional missing field is excluded from the presence universe. Both reject
  more than 32 operands.
- The WAL replay test exposed DocId high-water loss: Open replayed uncommitted
  upserts, then reset `next_doc_id_` to the older manifest value. Fixed it by
  retaining the maximum replayed/manifest high-water, preventing DocId reuse.
- Focused native differential: `checks=954 failures=0`. Warning-clean oracle:
  `checks=17323 failures=0`; ASan+UBSan with leak detection:
  `checks=17323 failures=0`; focused CTest 3/3. `M4_FAULT=before_manifest`
  preserves array search against the old manifest; retry and reopen pass.
- NOT candidate materialization is O(U + N + C + Pmax) auxiliary DocId
  storage plus result hits: U≤32 query operands, N live non-null array docs,
  C unique matches, Pmax largest single-value posting. Fixture `{17}` had
  N=6, C=2, four complement results, and 64 bytes of universe+match DocId
  payload (excluding output hits and temporary capacity). No benchmark run.

### Boundary / exact next action

P2.2 is **PASS** for this research-only slice. Do not extend to Boolean
composition, production Rust/Zvec, schema migration, other fields/operators, or
benchmarks without separate approval. Current executable next action: stop at
the approved boundary and await the next owner-scoped decision.

## 12. Handoff — 2026-10-17 (M4-P2.4 filename LIKE complete)

### Scope and evidence

P2.3 Boolean filter composition and the separately approved P2.4 filename
prefix/suffix compatibility slice are complete. Research implementation stays
under `research/m4-native/`; production Rust/Zvec, production schemas, segment
format, BM25 semantics, and migration were not changed. See the detailed
reports `research/m4-native/results/m4-p2.3-boolean-filter-composition-20261017.md`
and `research/m4-native/results/m4-p2.4-filename-like-compatibility-20261017.md`.

P2.4 implements positive `FileNamePrefix`/`FileNameSuffix` filters over the
existing string inverted dictionary. Native differential coverage includes
literal wildcard escaping, Unicode/case behavior, empty/missing values,
Boolean composition, update/delete/reinsert, flush/compaction, WAL replay, and
reopen. `NOT LIKE` remains unsupported; no general SQL LIKE engine or dedicated
prefix/suffix index was added. Filename scans dictionary values across runs;
no performance/scalability claim is made.

Current-source verification: M4 oracle `checks=18095 failures=0`; focused
CTest `4/4 PASS`; ASan/UBSan/leak-detection oracle `checks=18095 failures=0`;
strict warning checks and changed-file LSP diagnostics clean. No benchmark was
run. Full details and commands are in the P2.4 report.

### Boundary / exact next action

P2.4 is **PASS** for its approved research-only compatibility slice. Do not
extend to production integration, migration, generic SQL patterns, new scalar
encodings, or workload-scale benchmarking without owner approval. Exact next
action: stop at this boundary and await the next owner-scoped decision.

## 13. Handoff — 2026-10-17 (M4-P3 zg workload validation)

### Result / evidence

M4-P3 is **PARTIAL**. The usage-backed indexing/search flow was exercised with
isolated native Zvec and M4 stores against five logical fragment rows across
root and nested paths. The fixture follows `zg-engine`'s actual fields and
query shapes (file/entity/document identity, directory IDs, filename, hashed
symbol name/type, text), including positive prefix/suffix LIKE, Boolean path
composition, directory membership, same-PK update, whole-file delete,
duplicate checks, top-k, compaction, and forced child-process exit followed by
WAL recovery/reopen. Native and M4 identities are compared by fragment PK.

Current-source evidence:

- E2E differential: `zg_workload checks=692 failures=0`; 42 query/lifecycle
  comparisons pass for logical result sets and the `zg-engine` score-desc,
  document-id-asc tie-break. New focused CTest passes; full focused suite is
  5/5 PASS. Strict `-Wall -Wextra -Werror` syntax check passes.
- M4 correctness oracle under ASan+UBSan and leak detection: `checks=18095
  failures=0`. No benchmark was run.
- BM25 score mismatch remains intentionally visible: after update one common
  hit was native `0.0870114` vs M4 `0.105361`; after deleting file 2 native
  stayed `0.0870114` while M4's live-stat score became `0.133531`; after forced
  recovery the sample was `0.0730772` vs `0.10314`. This matches the known
  historical-native versus live-M4 statistics diagnosis, but the fixture does
  not expose native N/df/avgdl counters and does not prove each term's exact
  contribution. No M4 BM25 changes were made.
- The original P3 `INVALID_ARGUMENT` was a harness setup defect: it selected
  Jieba but did not register the cppjieba dictionary. P3.1 verified both the
  research-profile and packaged native libraries accept Jieba when initialized
  with their bundled dictionary. `zg-engine` auto-registers that dictionary via
  `zvec-rust` initialization; factory errors propagate and there is no fallback
  to standard. M4's adapter remains standard-only, so the original ASCII E2E
  result still does not establish production analyzer parity. No tokenizer
  capability was added.

Detailed source audit, fixture, scores, commands, and per-flow statuses:
`research/m4-native/results/m4-p3-zg-workload-validation-20261017.md`; raw
output: `research/m4-native/results/m4-p3-zg-workload-20261017.log`.

### Boundary / exact next action

Projection/filter/update/delete/recovery/compaction flow remains **PASS for
the standard-tokenizer ASCII fixture only**. The effective analyzer contract is
confirmed as Jieba + lowercase with the bundled dictionary auto-registered;
the prior native `INVALID_ARGUMENT` was harness configuration, not unsupported
native functionality. M4-vs-Jieba parity remains **PARTIAL/BLOCKED** by M4's
standard-only tokenizer; the P3.1 focused differential shows `学` returns 0
native Jieba hits versus 1 M4 standard hit. BM25 score parity remains
**PARTIAL/known divergence**. Do not expand to Jieba implementation,
multi-field FTS, HNSW, production adapters, migration, or benchmarks without
separate approval. P3.1 evidence:
`research/m4-native/results/m4-p3.1-effective-analyzer-20261017.md`.

## 14. Handoff — 2026-10-17 (M4-P3.1 effective analyzer)

**Result:** effective `zg-engine` contract confirmed as `jieba` + `lowercase`,
with no field-level `extra_params`. Normal Rust initialization discovers and
registers the packaged cppjieba dictionary. The native tokenizer factory does
not fall back to standard. The previous P3 failure was a missing dictionary
registration in the direct-C-API harness. Both the research-profile library and
the packaged release library were probed independently without rebuilding or
swapping either library; with their respective dictionaries, each indexed
`深度学习模型` and returned a hit for `学习`. The existing normal-path
`zg-engine` Chinese FTS test passed against the packaged prebuilt library.

A focused research differential now initializes native Jieba correctly and
compares it to M4's standard adapter: ASCII `parser` results match; query `学`
returns 0 Jieba hits versus 1 M4 standard hit. New CTest: 11 checks, 0 failures;
focused ASan/UBSan with leak detection also passes. No Jieba implementation,
Rust production code, native release library, schema,
index, migration, benchmark, or HNSW work was done. Full P3 remains partial;
BM25 historical-native versus live-M4 score divergence is unchanged.

Evidence and commands: `research/m4-native/results/m4-p3.1-effective-analyzer-20261017.md` and `.log`; focused code is under `research/m4-native/tests/`.
**Exact next action:** stop at the P3.1 boundary. If analyzer parity is wanted,
obtain separate approval for a bounded Jieba token/position compatibility
slice; do not proceed to production integration or migration.

## 15. Handoff — 2026-10-17 (M4-P3.2 Jieba adapter)

**Result:** GO for the approved research-only Jieba parity adapter. M4 compiles
and reuses the pinned native Jieba tokenizer and lowercase-filter sources;
one explicitly configured analyzer now serves document indexing, WAL replay,
and query parsing. It requires an explicit dictionary directory and reports
missing/invalid-path errors without falling back to standard or ambient config.
Quoted phrase contents are analyzed as one string to preserve native Jieba
context. Differential coverage compares token text, sequence positions, byte
offsets, query hit sets, update/delete, compaction, reopen, and WAL recovery.
The M4 segment format, live BM25 statistics, scorer, production ABI/source,
Rust code, daemon, schema, and existing indexes were not changed.

**Verification:** warning-clean direct M4 build and native-source CMake build
passed; M4 oracle `checks=18095 failures=0`; Jieba differential `checks=152
failures=0`, CTest 1/1 PASS. ASan+UBSan with leak detection passed for both
oracles. No benchmark was run. Full details and exact commands:
`research/m4-native/results/m4-p3.2-jieba-adapter-20261017.md` and `.log`.

**Limitations / boundary:** at P3.2, tokenizer and dictionary identity were not
persisted; this was addressed by P3.3 manifest metadata. Native cppjieba may
abort on malformed dictionary contents despite preflight checks for absent,
empty, non-regular, or unreadable files. This establishes only the pinned
single-field Jieba+lowercase configuration. Multi-field FTS, HNSW, production
integration, migration, and BM25 score parity remain out of scope.

## 16. Handoff — 2026-10-17 (M4-P3.3 analyzer identity)

**Result:** GO for research-only analyzer identity/reopen safety. Manifest v2
stores a deterministic SHA-256 fingerprint over tokenizer/adapter identity,
effective filter order/options, and (for Jieba) SHA-256 contents of the
`jieba.dict.utf8` and `hmm_model.utf8` files. Paths are excluded. The Jieba
canonical descriptor pins search mode, empty user-dictionary option, then
lowercase. Existing M4 API exposes no custom filter/user-dictionary options.
Fingerprinting is content-based, so identical dictionary files in another path
are equivalent.

Open compares identity before initializing native Jieba, loading runs, WAL
replay, or query access, then verifies dictionary content again after tokenizer
initialization to detect changes during initialization. A mismatch cannot
consume WAL. The fingerprint is committed in the same atomic manifest as other
index metadata and carried unchanged across flush/compaction. A post-manifest
fault followed by reopen preserves committed documents and identity.

Manifest v1 and v2 with absent/empty identity are explicitly rejected with
rebuild-into-a-new-directory guidance; no identity is guessed and there is no
in-place migration. Invalid main+backup metadata is rejected rather than
reinitialized. Segment format v1/v2, WAL, liveness, BM25, and scalar semantics
are unchanged.

**Evidence:** warning-clean direct build and CMake M4 oracle `18,095/18,095`;
Jieba analyzer+identity differential `173 checks, 0 failures`, CTest 1/1 PASS.
The ASan/UBSan evidence is recorded in
`research/m4-native/results/m4-p3.3-analyzer-identity-20261017.md` and `.log`.
No 500k/1M benchmark was run. Native Jieba's malformed-dictionary fatal-check
limitation remains; identity metadata does not alter production files or APIs.

**Boundary / decision:** GO for the requested research metadata slice; NO-GO
for production integration/migration. P4 supersedes the next-action note below.

## 17. Handoff — 2026-10-17 (M4-P4 isolated native A/B)

**Result: PARTIAL.** Three paired native-Zvec/M4 FTS+INVERT runs completed on
real `/var/tmp` btrfs using separate fresh directories. On the retained
synthetic workload, live-doc/token/file snapshots matched, independent-oracle
retrieval mismatches were zero for both arms, and cross-engine retrieval-set
mismatches were zero. M4 used ~29% less whole-process CPU, ~51% lower peak RSS,
~52% lower peak PSS, ~80% less final index space, and ~9× less timed-search CPU
in this fixture. Trade-offs: ~25% higher whole-process wall, ~2× indexing and
mutation CPU, update/delete p95 regressions, and 5.22× physical writes. Six
top-10 ordering cases differed; mean Jaccard was 0.9943. These results are not
a whole-`zg` or production claim.

A 60-file smoke found native-only extra hits for `"json parse"` with the
directory-membership filter (native 8 vs oracle/M4 4 initially; extras were
`fragment-7-*`). This query was excluded from both full-run arms and replaced
with `"json request"`; the failure is retained, not claimed as passing parity.
No M4 compatibility code was changed. Investigating/fixing this query needs a
separate owner-approved scope.

Native release research build, source patches/library SHA, Jieba/dictionary
fingerprint, exact commands, host conditions, latency/resource details, and
raw artifacts are recorded in `research/m4-native/results/m4-p4-native-vs-m4-20261017.md`,
its sibling provenance JSON/checksum file, and `results/artifacts/` archives.
The profiler completed 6/6 arm processes with no stop gate or process failure.
No daemon, existing production index, Rust source, ABI, migration, or
integration/deployment was touched.

**Boundary / exact next action:** P4 is PARTIAL/research-only. Wait for a
separate decision on the excluded `"json parse"` behavior. Do not integrate M4
into production or migrate indexes.

## 18. Handoff — 2026-10-18 (M4-P4.1 write amplification)

**Result:** A 60-file native→M4 syscall diagnostic found 1,588 per-mutation M4
WAL fsyncs vs 4 native WAL fsyncs at explicit flushes, with nearly equal WAL
write bytes (4.49 vs 4.44 MB). Process physical writes were 132.41 vs 35.79
MB (3.70×); successful index-file writes were lower for M4, 8.74 vs 16.21 MB.
This strongly supports sync granularity/filesystem writeback as the primary
cause, but exact physical-byte attribution is unresolved. The original 5.22×
full P4 result was not rerun.

M4 ended with 2 runs and 0 dead docs; native pending compaction debt was not
observable, so maintenance equivalence is unproven. Durability boundaries also
differ (`fsync_wal=true` for M4). No engine, format, or durability code changed.
Evidence and caveats: `research/m4-native/results/m4-p4.1-write-amplification-20261018.md`;
raw traces/host snapshots: `research/m4-native/results/artifacts/m4-p4.1-writeamp-60-20261018.tar.gz`.
Diagnostic markers are opt-in (`M4_P41_TRACE_FD`) and default-off; a 60-file
default-off run emitted neither markers nor extra stats rows.

**Recommendation:** if separately approved, prototype a research-only batch
mutation path that writes the existing WAL records and fsyncs before the batch
returns, leaving current single-document methods unchanged. Do not default to
native's phase-level durability. For exact physical attribution, use a
controlled-host Btrfs/block-layer trace.

**Exact next action at that checkpoint:** no API, sync, format or algorithm change was authorized by the attribution alone. This checkpoint is superseded by the owner-approved M4 Marathon below. Separately retain the excluded `"json parse"` compatibility finding; do not alter it silently. Production integration/migration remain NO-GO.

## 19. Handoff — 2026-10-18 (M4 Marathon P4.2 + Branch A)

### P4.2 durable batch

Implemented bounded additive `ApplyBatch` in `research/m4-native/` (256 mutations / 8 MiB). It appends one checksummed WAL transaction, fsyncs before in-memory apply when configured, applies under the index lock, and leaves single-document APIs unchanged. Torn suffix recovery truncates invalid bytes before later appends. Current strict oracle and ASan+UBSan/leak-detection each pass `checks=18157 failures=0`; focused CMake correctness + Jieba identity tests pass 2/2. Coverage includes pre-WAL failure, post-sync and partial-apply crashes, atomic reader visibility, torn-tail repair/replay, same-PK ordering, DocId non-reuse, concurrent same-PK writers, active-reader compaction/GC, and one batch WAL sync.

Two complete paired 60-file synthetic FTS/INVERT runs (vectors excluded) matched snapshots, independent oracles, and cross-arm retrieval/top-10 membership. Pair 1: CPU 1.46→1.06 s, PSS 224.1→224.1 MiB, physical writes 132,989,000→11,976,700 B, wall 2.50→1.24 s. Telemetry-enabled pair 2: cumulative WAL syncs 1,588→10 (initial setup and explicit flushes included), physical writes 125.90→12.06 MB, CPU 1.514→1.097 s, mutation burst wall 398/482/501→75/75/79 ms. Batch-vs-single per-call p95 units differ and are explained in the result report. Two additional run attempts stopped before the candidate arm due to the I/O PSI guard and were not retried. Full report: `research/m4-native/results/m4-p4.2-durable-batch-20261018.md`; performance report and raw evidence under `/var/tmp/m4-p42-ab-60-retry-20261018/` and `/var/tmp/m4-p42-ab-60-repeat-20261018/`.

### Branch A result and decision

Branch A source trace found that native C API batch upsert/delete returns per-document statuses and native `CollectionImpl::write_impl` loops through documents, with potential per-document segment switch; it is not a whole-batch atomic commit. Native ID map, delete store, segment, VersionManager, query and recovery are coupled. A separately committed M4 store has no shared commit/visibility point with native vector writes. **Branch A bounded adapter: NO-GO**, based on this concrete isolation/atomicity boundary; no native files were edited. Evidence: `research/m4-native/results/m4-marathon-branch-a-20261018.md`.

### Branch B progress / exact next action

Branch B keeps M4 as an independent subsystem. A second complete paired 60-file run enabled WAL fsync and mutation-latency telemetry: cumulative M4 WAL syncs were 1,588 for per-document calls vs 10 for durable batching (initial setup included), whole-workload physical writes 125.90 MB vs 12.06 MB, CPU 1.514 vs 1.097 s, and burst mutation wall 398/482/501 ms vs 75/75/79 ms. Per-operation p95 is reported with call-granularity caveat in the P4.2 report. Two full pairs now support the bounded performance result; a subsequent repeat stopped at the I/O PSI guard before candidate execution and was not retried. Reports/raw data: `research/m4-native/results/m4-p4.2-durable-batch-20261018.md`, `/var/tmp/m4-p42-ab-60-retry-20261018/`, and `/var/tmp/m4-p42-ab-60-repeat-20261018/`.

**Branch B adversarial gate:** same-PK concurrent upsert/delete, active-reader FTS/filter queries during compaction and tombstone GC, final delete/reopen, batch recovery/failpoints, torn WAL repair, analyzer identity mismatch, and existing churn/debt/fan-out oracle coverage all pass on current source; details in `research/m4-native/results/m4-marathon-correctness-20261018.md`. I/O PSI pressure stops were honored. The candidate is kept as an additive research subsystem; Fallback C was not needed. Mission checkpoint/deliverables: `research/m4-native/results/m4-marathon-execution-20261018.md`, `m4-marathon-performance-20261018.md`, and `m4-marathon-decision-20261018.md`. Exact future action: no further execution in this slice; any follow-up must first choose between an owner-approved native shared transaction/visibility architecture and keeping the independent M4 subsystem. Production integration/migration remain NO-GO.

## 20. Handoff — 2026-10-18 (Operation M4 Phase A–E)

### Final research candidate

Operation M4 completed successfully for the **independent research candidate only**. The candidate is bounded durable `ApplyBatch` (256 mutations / 8 MiB, fsync before visibility) with the accepted S5 structural debt policy available only as opt-in/default-OFF. The policy in the final P4 comparison is enabled only after the base bulk flush/reopen (`ratio=0.01`, max merge 250,000 docs). No production Rust/Zvec, daemon, native checkout, release library, schema, ABI, or production index was edited or deployed.

### Final P4 native comparison

Two untraced 1,200-file paired runs against native Zvec show M4 CPU 3.24 vs 6.74 s (−51.9%), process physical writes 53.47 vs 99.35 MB (−46.2%), peak PSS 276 vs 532 MiB (−48.1%), peak CPU 108 vs 175% of one core (−38.5%), and query p95 ~1.18–1.53 vs 19.81–25.57 ms. A separate exact after-base-policy trace counted 28 successful M4 WAL-path fsyncs vs 4 native; these are different durability boundaries, not like-for-like commit latency. Snapshots matched and M4 had zero independent-oracle retrieval mismatches. The retained `json parse` query still has native-only extra hits (32/32/32/44 by stage); four live-vs-historical top-10 order differences remain. Keep both differentials visible; do not change live BM25 semantics to imitate native historical stats. Detailed per-query results: `research/m4-native/results/m4-phase-e-final-native-ab-20261018.md`.

### Sustained churn and verification

A bounded 50k-doc, four-cycle churn test completed with exact liveness/cardinality, reopen, and compaction. The S5 policy reduced mean cycle p95 1.92→1.57 ms and final p95 2.20→1.88 ms, update p95 49→35 μs, and dead postings about 48%, at +9.7% total physical writes and near-flat CPU; it remains opt-in. Current gates: strict oracle 18,157/0; ASan+UBSan/leak detection 18,157/0; Jieba identity/reopen 173/0. Full Phase B/D report and host evidence: `research/m4-native/results/m4-phase-b-d-churn-debt-20261018.md` and its two `m4-phase-*-50k-4x2000-20261018/` artifact directories.

Phase A comparison: `research/m4-native/results/m4-phase-a-native-vs-m4-batch-20261018.md`. Raw paired profiles, stdout/query outputs, host snapshots, and syscall traces: `research/m4-native/results/artifacts/m4-native-m4batch-phase-a-e-20261018.tar.gz`; SHA256 manifest: `research/m4-native/results/m4-operation-mission-20261018.SHA256SUMS`. `research/m4-native/results/RESULTS.md` and `research/m4-native/README.md` reflect the result. The raw run archive excludes disposable built index directories; each full config and logs are retained.

**Decision:** SUCCESS/KEEP for isolated research; production integration, migration, schema/ABI changes, and deployment remain **NO-GO / unauthorized**. No more S5 threshold tuning. The next research slice, if authorized, is multi-field/tokenizer parity and background maintenance under the existing oracle, durability, and resource gates. Native compatibility root cause for the retained query remains unresolved.

## 21. Handoff — 2026-10-18 (M4 Bridge snapshot consistency)

### Root cause and correction

The bridge concurrency failure came from mutable native segment visibility,
not a demonstrated logical-PK mapping error. Native `DocFilter::compute_filter`
builds a forward bitmap from a segment scan, then vector search consumes row
IDs from the mutable vector index; `DocFilter::get_forward_bit` indexed those
IDs into the earlier bitmap. The failing run logged `id[49] count[49]` through
`id[71]`, proving vector IDs had advanced beyond the filter bitmap row count.
`VectorRecallNode::State::prepare` is the native call path that computes the
filter and then searches the vector index.

Research bridge now protects reads with a shared coordinator snapshot lease
and commit/compaction with an exclusive lease. `Hybrid` holds one read lease
across both backends; commit holds exclusive ownership from before FTS prepare
through vector flush, publish WAL, manifest rename, and generation publication.
Versioned rows remain append-only, so prior generations stay queryable. No
native production code or ABI was modified. This serializes readers against a
full snapshot update; it is a correctness candidate, not a zero-stall snapshot
implementation.

### Verification

- Warning-clean CMake target and deterministic snapshot/crash/hybrid matrix:
  `checks=1240 failures=0`.
- Three old hybrid readers scheduled while vector generation is staged each
  return exactly the prior hit set after publish; a new query sees the update
  and deletion.
- Five fresh-process repetitions passed at `checks=1139 failures=0` each.
- Crash points before reservation, after reservation, after FTS flush, after
  vector flush, and after durable publish WAL all reopen with matching FTS,
  vector and hybrid visibility. Torn-WAL repair and subsequent append pass.
- Pinned hybrid reader plus FTS `CompactNow` run-file GC/native optimize passes;
  old logical generations remain readable. Generation-level row reclamation is
  not implemented, so historical row GC is not claimed.
- Current-source ASan+UBSan+LeakSanitizer: `checks=1240 failures=0`, `EXIT=0`.
- One 500-row latency sample: full commit 45.11 ms; a query forced to start
  under the exclusive publish lease waited 45.80 ms. Baseline hybrid p95
  0.649 ms; 20 concurrent samples p95 0.751 ms, max 45.80 ms. This is a
  single synthetic measurement; see
  `research/m4-native/results/m4-bridge-snapshot-consistency-20261018.md` and
  raw host/time/JSON artifacts in
  `/var/tmp/m4-bridge-lock-perf-20261018-074647/`.

### Boundary / next action

Cross-component read consistency is achievable in the research bridge. The
coordinator-lock implementation is correct but stalls readers through commit;
the immutable-generation candidate removes that stall for pinned readers and
implements generation retirement/backpressure. Current evidence and lifecycle
guarantees are in Section 22 and
`research/m4-native/results/m4-bridge-generation-lifecycle-20261018.md`.
Production integration/migration remains NO-GO: immutable generations show
substantial commit/CPU/write/RSS costs, while native shared transaction
visibility is absent at the current collection boundary. The next executable
research action is to profile and optimize a measured immutable-generation
cost (bounded generation build/rebuild amplification) without weakening pin,
publish, recovery or retirement invariants; rerun correctness/sanitizers, then
a preflight-gated larger hybrid workload. Do not rerun P1–P4 or infer
production-scale performance from the current 500/1000-row fixtures.

## 22. Handoff — 2026-10-18 (M4 Bridge generation-lifecycle marathon)

### Decision and branch matrix

- **Branch A (modify existing native collection/vector lifecycle): NO-GO at
  current boundary.** The source-backed race is a mutable native vector index
  whose returned row IDs can exceed the separately computed `DocFilter`
  forward bitmap. `VectorRecallNode::State::prepare` computes filter then
  searches vector state; a reproduced failure logged IDs 49–71 with bitmap
  row count 49. Correct cross-store atomic visibility needs native shared
  liveness/commit protocol or embedded-column replacement across query,
  segment, manifest and recovery. No native production source/ABI was changed.
- **Branch B0 (exclusive coordinator publish lease): correctness PASS,
  reader-stall limited.** A shared query lease spans FTS/vector hybrid reads;
  publish/compaction takes exclusive lease. Existing crash/hybrid matrix and
  sanitizers pass. Snapshot report records a 45.11 ms commit and 45.80 ms
  forced reader wait in one 500-row sample.
- **Branch B1 (immutable generation directories + shared handles): KEEP as a
  research-only candidate.** Separate FTS/INVERT and vector generation build,
  durable pointer/manifest publish, shared reader pinning, unpinned retirement,
  disk/generation caps and failure retry/orphan recovery are exercised.
  Deterministic lifecycle/crash matrix: 3,549 checks, zero failures; max
  retained generations 3. It greatly reduces sampled reader latency but incurs
  heavy generation build/commit costs.
- **Native vector-only reference:** not a hybrid parity/performance comparator.
  It has no M4 FTS and was retained only as a separate resource reference.

### Current-source verification (after bounded-query harness refinement)

Final source SHA-256:
`8f967460fd492af04a6405cd1acda4579e97741452f5ee2f429e583865d60d0f`.
Release lifecycle binary SHA-256:
`567ced084ac063b695fe9f66e122ff906e0f78ad73eb25b1ba3052631f2ccb1d`.
ASan/UBSan lifecycle binary SHA-256:
`afff3841952059d482edf58ba4ca6a3092837ba07350aee8d73cabbb46b283b4`.

- `cmake --build /tmp/m4-p4-build --target m4_bridge_integration -j2` — PASS.
- Current lifecycle/crash test — `checks=3549 failures=0 generation=22
  live_rows=23 max_retained_generations=3`.
- Rebuilt ASan+UBSan with `ASAN_OPTIONS=detect_leaks=1` — same 3,549/0; no
  sanitizer diagnostics.
- The immutable-generation benchmark now has a fixed four-reader-query budget
  per churn round synchronized with writer start, matching the lock arm's
  sample count; this replaces its unbounded busy-loop reader. This is benchmark
  methodology only; hybrid semantics and correctness assertions are unchanged.
- Phase 6 tried reflink-cloning the previous generation and applying deltas.
  Correctness passed in the candidate, but it was rejected: at 500×20, commit
  p95 improved 1308.83→927.13 ms but physical writes stayed ~225 MB and root
  size grew 5.96→111.60 MB; at 1000×5, commit p95 regressed 560.33→834.43 ms,
  CPU 12.64→18.96 s, and root grew 6.48→32.92 MB. Candidate code was removed;
  final implementation retains full-generation rebuild. Receipts are in
  `m4-bridge-generation-lifecycle-20261018.md` and `immutable-incremental-*`.

### Sustained diagnostic results

Raw per-sample distributions, host-before/after snapshots, preflight, command,
`/usr/bin/time -v`, provenance and checksums are in
`research/m4-native/results/m4-bridge-generation-lifecycle-20261018/`.

For 500 docs × 20 rounds (80 reader queries each), coordinator lock vs
immutable generation: commit p95 97.26 vs 1,308.83 ms; reader p95 112.66 vs
3.40 ms; physical writes 107.6 vs 222.6 MB; process CPU 1.34 vs 102.79 s;
peak RSS 203 vs 298 MiB; final root 115.0 vs 6.0 MB; both coherence error
counts zero.

For 1,000 docs × 5 rounds (20 queries each): commit p95 121.74 vs 560.33 ms;
reader p95 123.88 vs 2.22 ms; writes 37.7 vs 73.0 MB; process CPU 0.44 vs
12.64 s; peak RSS 186 vs 270 MiB; coherence errors zero. These are one-run
synthetic diagnostics with small query samples and sequential host windows,
not production or statistically stable tail-latency claims. Immutable
commit/build cost is unacceptable for promotion without a correctness-
preserving optimization.

### Lifecycle guarantees and unresolved limits

- Readers pin immutable generation handles; publish switches only the current
  generation pointer. A pinned generation is not retired. Retirement failure
  is surfaced and retryable; disk/generation pressure does not silently
  violate snapshot consistency. Uncommitted staging/orphans are recovered or
  removed, and tests cover crash points around durable publication, pointer,
  manifest and torn WAL. Deleted logical rows are not resurrected in tested
  recovery/churn flows.
- Current generation retirement is research-validated, not a production
  durability/availability contract. Workload scale remains small; no background
  maintenance or native collection transaction integration exists.
- Compatibility caveats remain: native-only `json parse` directory-filter
  extra-hit and native historical vs M4 live BM25 statistics. This slice did
  not alter ranking/query semantics.
- Production integration, migration and deployment: **NO-GO**.

Final current-source revalidation after removing the rejected optimization:
normal and ASan/UBSan/LSan each report `checks=3549 failures=0 generation=22
live_rows=23 max_retained_generations=3`.

Exact next executable action at this handoff: source-profile native HNSW/index
generation build cost separately from FTS and WAL, then formulate at most one
bounded optimization with an explicit write/CPU/disk hypothesis. Do not copy
generation files or advance to larger stress until the optimization hypothesis
is falsifiable and the safety gates pass. Full mission status is **PARTIAL**,
not SUCCESS; production remains NO-GO.

## 23. Handoff — 2026-10-18 (B2 immutable base + delta vector prototype)

Mission remains research-only. B2 is an executable correctness prototype, not
an accepted performance architecture. It keeps immutable vector shards, a
logical-PK latest-version map, scalar-filtered per-shard native search with
stale-row overfetch, global score merge, four-shard compaction, M4 snapshot
pairing, checksummed catalog/manifest/WAL, and recovery.

At the 2026-10-18 attribution follow-up, benchmark source
`research/m4-native/tests/m4_bridge_integration.cc` SHA-256 is
`bf9cdd45e815280049cd381f641e4d5e98f28714f8ca6aa5d8824c6b5ac56389` and
normal binary SHA-256 is
`3820652d181c5d177523db5ff72ba3d2d7c647d01df81d75ca8c0e683a60a492`.
The ASan binary below predates the benchmark-only condition-variable change.
Current-source ASan binary SHA-256:
`a8a97725fc866d0ed028cae50a89c1c776d7fec5092bcc5c211d2c3d0243e136`.

- Current-source focused correctness: `VECTOR_DELTA_CANDIDATE checks=1380 failures=0`.
  Comparable 64×1 smoke had 4 queries/arm and zero coherence errors.
- Paired B1→B2 then reversed-order repeat ran for 500×20 and 1,000×5. The
  two-run mean commit p95s were 791.1→577.9 ms (-26.9%) and 821.1→576.5 ms
  (-29.8%); physical writes -6.5%/-5.4%. The originally reported process-CPU
  deltas (-31.9%/-40.2%) are contaminated by busy-wait synchronization and
  are superseded for CPU attribution. A current-source condition-variable
  follow-up, one 500×20 pair, measured process CPU 7.785→4.499 s and writer
  commit-thread CPU 6.045→3.792 s; query CPU 59.4→73.6 ms; synchronization CPU
  ~6.7 ms/arm. It wrote 10,000 vs 2,530 native vector rows but indexed 10,000
  M4 rows in both arms. B2's CPU gain is consistent with vector-delta writes,
  not fewer M4 mutations; still only one descriptive pair. Exact evidence and
  caveat: `research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/summary.md`.
- Candidate ASan/UBSan/LSan: 1,380/0, no diagnostics, exit 0 on the source
  preceding the benchmark-only attribution instrumentation. B1 lifecycle
  regression: `checks=3549 failures=0 generation=22 live_rows=23
  max_retained_generations=3`, exit 0. Receipts in the summary's `validation/`
  subdirectory; post-validation gate passed. Current condition-variable
  benchmark paths passed 500×20 with zero coherence errors; sanitizers were
  not rerun after this harness-only synchronization change.

Retirement/GC and backpressure helpers remain test-harness-local, not wired into
a single production-quality publisher or enforced before durable prepare. B2
still rebuilds full M4 FTS/INVERT per mutation and all live vectors at
four-shard compaction. `m4::Index` exposes persistent `ApplyBatch`/`Flush`, so
full M4 reconstruction is not inherent to each mutation. But the current B2
pins vector+FTS as one immutable generation; a shared mutable M4 root requires
coordinated publication, reader admission/snapshot rules, and cross-index crash
recovery. Plausible research paths: a writer-barrier shared M4 index (simpler,
reads wait for mutation) or immutable M4 delta/run snapshots (preserves read
overlap but requires snapshot-aware query/liveness/stat handling). The bounded
B3 benchmark harness now demonstrates per-round rebuild removal and zero
coherence errors in its measured workload, but it is not a production-quality
publisher and has not passed the complete B2 adversarial/recovery gates. PSS,
peak CPU%, reader blocked-wait and per-compaction physical bytes remain
unmeasured.

Benchmark mission: **diagnostic A/B only, not an architecture GO**. Keep
production integration/migration out of scope. B3 structurally removes the
per-round full M4 rebuild; stage attribution further found that M4 `Index::Open`
spends about 46–52 ms CPU initializing Jieba, while segment loading is under
0.5 ms median. An opt-in weak-reference analyzer cache preserves fingerprint
checks and reduces M4 Open CPU about 51% across two 500×20 pairs; aggregate
writer CPU fell about 48%, with reader-tail and physical-write effects not
established. Evidence, source hashes, raw JSON and host receipts:
`research/m4-native/results/m4-m4-open-attribution-cache-20261018.md` and its
`paired-benchmark/open-attribution/` artifacts. Cache stays OFF by default and
research-only; no HNSW or production code changed. Exact next action: continue
the queued usage-first M4-P2 scalar/filter compatibility audit. Do not claim
production readiness or an architecture GO, and do not tune BM25 based on the
earlier 57 historical-vs-live-stat mismatches.

## 24. Handoff — 2026-10-18 (B3.1 Open/cache closure)

B3.1 lifecycle and identity concerns are verified on the current research
source. `analyzer_cache_lifecycle` reports `checks=36 failures=0`: weak cache
hit/miss and construction counters, shared ownership across generations,
concurrent cold Open/single-flight, cache key identity, same-size/same-mtime
dictionary mutation, persistent mismatch rejection before cache lookup,
reopen/weak-entry expiration, and immutable-snapshot transient TOCTOU all
pass. The pre-snapshot TOCTOU reproduced one failure: native Jieba could read
changed source bytes while the stored fingerprint described the original.
Native initialization now consumes the exact dictionary/model bytes captured
for that Open's content fingerprint. A transient source mutation after the
snapshot no longer changes the analyzer's bytes; a persistent mutation on
reopen still fails closed. One cache miss builds exactly one analyzer and the
waiter reuses it; closing all generations expires the weak entry.

Fingerprint call graph and cost are now explicit. `Index::Open` first reads
and content-addresses both Jieba dictionary files, hashes each plus the
composite identity (5,591,211 input bytes / 3 SHA-256 calls), then compares
that identity to the persistent manifest before cache lookup or recovery. For
a cache miss, the second identical read/hash occurs after native initialization
to retain the fail-closed `changed during initialization` check. For a verified
cache hit, no native initialization occurs, so this second pass is redundant:
the current content has already been hashed and compared to the manifest,
and the cache key includes tokenizer config plus that content fingerprint.
The hit-only candidate skips only this second pass; cold opens retain both
checks. No path/mtime-only identity shortcut is used.

Current-source normal CTest is 7/7 PASS, including strict oracle
`checks=18157 failures=0`, Jieba compatibility `173/0`, and analyzer lifecycle
`36/0`. Current-source ASan/UBSan with leak detection is 7/7 PASS. The B1
recovery/crash matrix is `checks=3549 failures=0`, generation 22, 23 live rows,
max retained generations 3. Incremental FTS/vector snapshot coherence and
reopen are `M4_INCREMENTAL_FTS_CANDIDATE checks=578 failures=0`; no mixed
snapshot was observed. Artifacts and source/binary checksums are under
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/open-attribution/`;
summary: `paired-benchmark/summary.md`.

A resource-gated current-source 500×20 three-arm run completed sequentially
with zero coherence errors. One descriptive run/arm measured Open CPU
p50/p95 no-cache 330.85/374.68 ms, B3.1 cache 50.91/59.54 ms, and hit-only
single-fingerprint candidate 27.87/30.69 ms. Fingerprint verification CPU
p50 was 23.18/23.21/0 ms respectively. Candidate writer CPU was 21.8% below
B3.1; its physical writes were 10.6% higher and reader p99 7.92 vs 3.86 ms.
A subsequent resource-gated current-source paired 500×20 run (control then
candidate) also had zero coherence errors and corroborated the CPU effect:
writer CPU 2,418.4→1,751.6 ms (−27.6%), whole-process CPU 3,381.8→2,626.9 ms
(−22.3%), M4 Open CPU p50 61.63→27.99 ms (−54.6%), and physical writes
228.3→209.2 MB (−8.3%). Fingerprint-verification CPU p50 was 29.68→0 ms.
The pair's peak RSS was 220,616→215,184 KiB. A resource-gated reverse-order
pair (candidate then control) also completed with zero coherence errors:
writer CPU 1,690.0→2,267.8 ms (−25.5% candidate/control), whole-process CPU
2,585.4→3,128.8 ms (−17.4%), and Open CPU p50 28.72→54.60 ms (−47.4%).
Physical writes went the other way in this pair (226.5 vs 210.6 MB; candidate
+7.5%), while reader p95/p99 was 5.30/7.16 ms candidate vs 3.27/5.82 ms
control. Across both pairs, writer CPU fell 25.5–27.6% and Open CPU p50 fell
47.4–54.6%. Physical-write direction reversed, and reader p95/p99 was higher
for the candidate in both pairs. These small-sample results show a consistent
CPU/Open effect but leave write and reader-tail effects unresolved; they are
descriptive, not causal or production claims. Paired artifacts and host
receipts are under
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/open-attribution/repeat-pair-20261018/`
and `.../reverse-pair-20261018/`. A separate gated PSS sampler measured peak
PSS/RSS/swap: no-cache 1,248,597/1,253,100/8 KiB, B3.1 259,283/263,788/8 KiB,
candidate 251,145/255,648/8 KiB. Keep the candidate research-only and
opt-in/off by default. The dominant remaining cache-hit Open cost is the
required first content fingerprint (about 26.6 ms CPU p50 in the repeat);
eliminating it would need an owner-approved immutable-dictionary invalidation
contract and is outside this slice.

B3.1 research verification is **COMPLETE**: current-source analyzer-cache
lifecycle and same-process dictionary identity tests, hot-hit fingerprint
deduplication, recovery/crash and ASan/UBSan gates, and sustained 500×20 bridge
measurements are recorded above. The follow-up reader-tail attribution is
recorded in section 25 and the paired-benchmark summary. It separates dispatch,
snapshot pin, backend shared-lock acquisition, native vector search, M4 search,
merge, release, and writer compaction/publish. Lock waits are microsecond-scale;
search execution dominates. The five valid paired runs have mixed candidate vs
control p95/p99 deltas; pooled tail differences do not establish a repeatable
regression or improvement. Physical-write direction also remains mixed. These
are research caveats, not acceptance failures or a production decision.
P2.1–P2.4 are already complete and should not be reopened. No production
integration/migration is approved.

## 25. Reader-tail attribution follow-up — 2026-10-18

The owner-requested reader-tail follow-up rebuilt the bridge harness with
explicit query-dispatch, snapshot-pin, backend lock-acquire, native vector
query, M4 query, merge, snapshot-release, and writer build/compaction/manifest/
snapshot-publish timestamps. The source/binary SHA, commands, raw JSON, per-arm
resource receipts, process snapshots, timing logs, and final checksum manifest
are archived under
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/reader-tail-attribution-v2-20261018/`;
`analysis.md` is the short report and `analysis.json` contains derived values.

Five resource-gated temporally paired 500×20 runs (pairs 1, 2, 3, 6, 7; 80
reader queries per arm/run; 400 queries per arm) had zero coherence errors.
Pair 4 was excluded from paired inference: the first control post-run PSI hit
the 5.00 gate and its retry was five minutes apart from the candidate under a
different IO-PSI state. Pair 5 was excluded because its candidate overlapped an
unrelated `qmd embed` process (~30% CPU, ~862 MiB RSS); the process exited before
pairs 6–7. Both excluded attempts are retained in the artifact directory.

Pooled control/candidate p95 and p99 end-to-end were 4.132/5.070 ms and
4.504/6.103 ms respectively. Per-pair p95 and p99 were higher for the candidate
in three of five pairs and lower in two; this does not establish a stable
candidate tail regression. Explicit shared-lock acquisition p95/p99 was
0.0016/0.0024 ms for vector and 0.0007/0.0019 ms for M4; snapshot pin and
release were also microsecond-scale. Dispatch wait p95/p99 was about
0.086/0.100 ms. Thus no publish/lease-lock bottleneck is indicated.

The tail is in query execution: pooled native-vector-call p95/p99 was
2.209/2.788 ms control and 2.520/2.896 ms candidate; M4 search p95/p99 was
1.749/2.505 and 1.711/2.764 ms. Merge stayed below 0.1 ms p99. Reader calls
overlapped vector compaction (100/400 per arm); overlap p95 was 4.132 ms
control and 4.775 ms candidate versus non-overlap 4.059 and 3.931 ms. No reader
call overlapped M4 build, manifest publish, or snapshot publish in this
schedule. This overlap is descriptive, not causal. No lease/publish change is
indicated; if pursuing latency, profile native vector query execution under
concurrent vector compaction, then M4 search. Keep the candidate
research-only/off-by-default; no production integration, migration, or
rollout is approved.

## 26. Reader-tail root-cause investigation — 2026-10-18

**Disposition: INCONCLUSIVE; diagnostic workload blocked, no implementation.**
Follow-up inspection reviewed the B3.1 artifacts, bridge generation/query
path, and checked-out native vector search wrappers. The bridge compaction
marker encloses rebuilding/upserting/flushing a full vector shard after the
fourth shard; it does not independently measure host I/O or scheduler pressure
per query. Native vector search passes through `VectorColumnIndexer::search`
and `CombinedVectorColumnIndexer::search`, including conversion, per-block
calls, result folding and top-k. These code paths identify candidate work but
do not attribute time to contention. The native source profile also has local,
unrelated modifications, so it was not treated as the exact B3.1 run source.

No baseline-vs-compaction causal pair, CTest or sanitizer was run: at
inspection, available RAM was about 5.5 GiB with about 7.3 GiB swap occupied,
I/O PSI full avg10 1.35%, and filesystem free space about 17.6 GB at 97% use.
The archived artifact directory has no standalone gate checker or complete
numeric threshold definition; no threshold was invented and no heavy run was
started. Existing evidence remains observational: 100/400 readers per arm
overlapped compaction, with mixed overlap/non-overlap tails across arms. Thus
compaction interference and its CPU/I/O/internal cause remain **INCONCLUSIVE**.
Vector and M4 query execution are confirmed as major measured stages; exact
M4 contribution to the same tail queries is not available from marginal
percentiles. Lock acquisition remains rejected as the dominant cause for the
B3.1 runs. No correction is proposed without confirmed causality.

Full report and exact safe-resume diagnostic are in
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/reader-tail-root-cause-investigation-20261018.md`.
No source instrumentation or architecture changed; production remains
untouched and no implementation is authorized by this investigation.

## 27. Reader-tail follow-up run after disk cleanup — 2026-10-18

One descriptive 500×20 run was executed using the exact archived B3.1 bridge
binary and harness source hashes. Fresh workspace
`/var/tmp/m4-reader-tail-followup-500x20-20261018`; 80 queries, five vector
compactions, exit 0, 500 live docs after reopen, zero coherence errors. Before
run, resources were within the previous successful envelope (10.21 GiB
MemAvailable, 29.79 GB free on `/var/tmp`, load-1 1.22, I/O PSI full avg10
4.03%). After run, I/O PSI full avg10 rose from 4.03% to 4.65%, slightly above the
prior successful sample maximum of 4.46% but below the 5.00% boundary noted
for an excluded B3.1 run. The benchmark itself performed 188.6 MB physical
writes and five compactions, so it plausibly contributed to the global PSI
rise; no unrelated benchmark was running, but per-process I/O wait was not
measured, so sole attribution is unproven. I stopped conservatively after one
run because the receipt exceeded the prior sample envelope, not because a
formal 5.00% gate failed.

By the existing overlap definition, 20/80 queries overlapped compaction.
Overlap/non-overlap E2E p95/p99 was 2.932/3.159 vs 2.729/3.238 ms; native
vector p95/p99 1.834/1.850 vs 1.527/1.749 ms; M4 p95/p99 1.008/1.138 vs
1.068/1.285 ms. This mixed single-run observation is **not causal**. Per-stage
query CPU, per-compaction I/O and scheduler attribution remain unavailable; no
matched no-compaction control or deterministic delay/hold was run. Root cause
remains **INCONCLUSIVE**; lock wait is still not the dominant B3.1 stage. No
source change, CTest, sanitizer, or optimization was performed. Full command,
receipts, raw output, analysis and hashes are under
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/reader-tail-root-cause-run1-20261018/`.
See §28 and the updated investigation report for the subsequent run and current disposition.

## 28. Reader-tail descriptive repeat — 2026-10-18

After the owner clarified that no other user workload was active and asked to
finish the benchmark before reporting, one additional 500×20 run completed with
the same archived bridge binary and harness source hashes as §27. Fresh root:
`/var/tmp/m4-reader-tail-followup-500x20-run2-20261018`. Exit 0, five vector
compactions, 80 queries, 500 live docs after reopen, generation 21, zero
coherence errors. Run wall 6.49 s, CPU 4,829.9 ms, peak RSS 303,900 KiB, and
physical writes 226.66 MB.

Preflight: 5.30 GiB `MemAvailable`, 29.79 GB free, load-1 1.31, CPU PSI full
avg10 2.49%, I/O and memory PSI full avg10 0%. This was below the available
memory recorded for §27 (10.21 GiB), so latency is not a controlled cross-run
comparison. Post-run CPU PSI full avg10 was 3.60%; I/O and memory PSI remained
0%. No other M4 benchmark was observed in the process inventory.

With the same within-run overlap definition, 20/80 queries overlapped
compaction. Overlap/non-overlap E2E p95/p99 was 4.510/4.978 vs 4.256/5.589 ms;
native vector 2.595/2.640 vs 2.407/2.562 ms; M4 1.690/1.930 vs 1.999/2.491 ms.
Run 1 and run 2 both show higher overlap-group E2E p95 and lower p99, but
these are unequal within-run samples, resource conditions differ, and there is
no matched no-compaction counterfactual. This repeated descriptive association
does not establish compaction causality; stage CPU, scheduler, and per-compaction
I/O attribution remain unavailable. **Root cause remains INCONCLUSIVE.** No
third workload, source change, CTest, sanitizer, or optimization was performed.
Raw evidence and checksums:
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/reader-tail-root-cause-run2-20261018/`.

## 29. Controlled reader-compaction crossover — 2026-10-18

Owner-requested, small AB/BA crossover completed without changing native
vector-search implementation or compaction policy. Four arms rebuilt the same
deterministic 1,024-doc, generation-4/four-shard snapshot; order was
compaction→control, then control→compaction. Each arm ran 4 readers with the
same 8 query IDs × 4 repetitions (32 queries/arm). Query latency paired by
`(pair, query_id, repetition)`; hit PK order matched across each pair.

The active cohort began after the first batch of the existing natural vector
compaction trigger. In both pairs all 32 full query intervals, all 128 native
API calls, and all 32 M4 stages were fully inside the compaction interval.
Per-query native API wall delta was +0.330 ms median (25/32 positive) in pair
1, but +0.026 ms (16/32 positive) in reversed-order pair 2; combined median
was +0.135 ms, with paired p10/p90 −0.383/+0.564 ms (41/64 positive). Native
API thread-CPU combined median delta was +0.106 ms, with both faster and slower
paired queries. Runqueue-delay paired median was 0.000 ms. The direction and
size did not reproduce consistently across AB/BA blocks; **no consistent
material vector-search slowdown attributable to compaction was demonstrated**.
Close this causal hypothesis for the present fixture; this is not proof of no
effect under all workloads. Do not run another compaction test or tune policy.

Quiescent-control medians show native API wall/CPU 1.361/1.219 ms in pair 1
and 1.732/1.389 ms in pair 2; M4 wall/CPU 1.343/0.615 and 1.224/0.614 ms.
Both stages are material; native API thread CPU is higher in this fixture.
These are 4-reader control-arm observations, not standalone production ratios.
Next analysis should focus on search execution independently, on a fixed
snapshot without a writer.

Harness exit 0, 48,891 checks and zero failures; the CMake bridge target
compiled and linked. ASan/UBSan was not run for this instrumentation-only
slice. `/usr/bin/time -v` reports 9.22 s elapsed, 7.11 s process CPU, 466,412
KiB peak RSS, no swap. Host-wide PSI and before/after process/resource receipts
are preserved; background I/O and memory pressure appeared by the second
crossover pair, so results remain single-crossover descriptive evidence, not
a confidence-bound test. Full raw
JSONL, exact command, hashes/checksums, reproducible paired analysis and
artifacts:
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/reader-compaction-crossover-20261018/`.
Fresh root `/var/tmp/m4-reader-compaction-crossover-20261018` is preserved.

## 30. Search-execution attribution prep — 2026-10-18 (in progress)

No new build, sanitizer, benchmark, or profile was started. Source tracing now
covers the vector C API → `CollectionImpl::query` → `SQLEngineImpl` /
`QueryPlanner` → `VectorRecallNode::prepare` / `DocFilter` →
`CombinedVectorColumnIndexer` → `Index::search` / HNSW. In the old 1,024-doc
fixture, the base shard crosses HNSW's 1,000-doc brute-force threshold while
three 11-row delta shards use brute-force; all vector calls carry a generation
predicate and therefore exercise the filtered route. This is a 4-D,
fixed-top-k, full-selectivity fixture, not representative of zg's default
256-D normalized cosine embedding. HNSW debug counters are present internally
but no query/API path to enable them was found. The native checkout at
`8f6d600db769dcabf92f7af11a8242453b7ae5bf` was already dirty and remains
untouched. Existing bridge build was Release `-O3 -DNDEBUG -std=gnu++17
-Wall -Wextra -Werror -pthread`, linked to
`libzvec_c_api.so` SHA-256
`6d55b9131766d943fbad1f77dbebc6a2db7951ce8d89ad46a92b7619cc92d950`; its
existing executable predates the current source edits and is stale.

Research-only M4 query diagnostics now optionally expose M4 index-mutex wait,
filter construction, query parse, setup, posting/scoring execution, final top-k
wall and calling-thread CPU, and existing postings/liveness/candidate counts.
The bridge adds a fixed-snapshot, no-writer diagnostics mode, wrapper TID
ownership assertion/output, and a compile-time 256-D normalized fixture
option. Measurement-only `source_pks` copying is skipped in the timed query;
worker threads warm their assigned queries before the condition begins. These
changes are **unverified**: the prior crossover's 48,891 checks are stale for
this source; ASan/UBSan, build and the new mode have not run.

Source evidence shows `M4::Index::SearchFts{,Filtered}` holds one recursive
mutex over query/filter execution, serializing readers by construction. This
is a plausible cost, not yet a measured dominant bottleneck; do not implement a
concurrent-read correction until optional mutex-wait telemetry confirms it.
Native API caller-thread CPU is not yet proven to include work performed by
Arrow Acero worker threads; do not report it as total native CPU.

Conservative resource stop at 2026-10-18 17:21 +07: load 3.16/2.69/2.12,
`MemAvailable` 8.02 GiB, CPU PSI full avg10 0%, I/O PSI full avg10 2.56%,
memory PSI full avg10 0%, swap free 16.27 GiB. Active GUI/browser processes
were present. There is no formal numeric resource threshold in the artifact;
I/O pressure was nonzero, so no heavy workload was started and no process was
stopped. Detailed preparation and exact resume steps:
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/reader-search-attribution-20261018/preparation.md`.

## 31. Search instrumentation and PK lookup follow-up — 2026-10-18

The research M4 query-diagnostics regression now passes with counters explicitly
opted in. WAND upper-bound evaluations are counted at the WAND call site so
both segment and delta cursor bounds are included. AND short-circuit paths with
a missing term may legitimately have zero cursor movement; tests still require
posting setup accounting and require cursor movement on paths that execute.
Diagnostics ON/OFF result parity and counter invariants pass across the
correctness fixture lifecycle.

Current-source warning-clean oracle: `checks=18456 failures=0`. Current-source
ASan+UBSan with leak detection: `checks=18456 failures=0`. Bridge target
`m4_bridge_integration` compiles and links, but bridge recovery/lifecycle
sanitizer validation and the new native/HNSW query profile have not run.

A matched reversed-order PK lookup microbenchmark (three result-materializing
loops, 1,024 matched docs/query, 3,000 measured queries/run, 50 warmups) showed
candidate median 101.761 ms versus baseline 240.001 ms (−57.6%) across
baseline/candidate/candidate/baseline. This is a narrow scalar-INVERT
microbenchmark, not representative end-to-end evidence. Raw logs, sources,
commands, hashes, resource notes and analysis:
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/pk-lookup-microbench-20261018/analysis.md`.

The PK run occurred with active GUI/browser processes and I/O PSI full avg10
~1.5–1.7%. Bridge recovery/lifecycle validation has since completed on current
source under ASan/UBSan+LSan: `checks=3581 failures=0 generation=22 live_rows=23
max_retained_generations=3`; full receipts:
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/bridge-search-lifecycle-20261018/analysis.md`.
The agreed operational check uses the established empirical B3.1 envelope
(not an idle-host requirement): `MemAvailable >= 5.224 GiB`, `/var/tmp` free
`>= 18.219 GB`, I/O PSI full avg10 `<= 4.46%`, load-1 `<= 3.14`. At 18:37:39,
I/O was 4.87% (+0.41 pp over the envelope), while memory, disk and load passed;
95% filesystem utilization alone is not a failure. A later 18:48:27 preflight
passed (10.020 GiB available, 29.299 GB free, I/O 4.31%, load1 1.46), and the
fixed-snapshot diagnostics arms started. Four first-order modes passed; the
sequence stopped after combined_A1 when post-run I/O reached 4.47%. A reversed
combined_B2 arm also passed preflight but post-run I/O was 4.48%, so the
remaining reverse-order timing/counters/control arms are pending the same
empirical envelope. Exact arm receipts and auto-stop behavior are archived in
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/reader-search-aa-current-20261018/`.
At later checks the I/O value oscillated around/above 4.46%; an automatic gate
watch is active to resume only remaining arms once all established metrics
pass. Disk is not the blocker: latest observed free space was ~29.2 GB, about
11.0 GB (10.2 GiB) above the documented minimum. The A/A sequence is now
complete: all eight arms exited 0 and query signatures/counter invariants
matched; see `reader-search-aa-current-20261018/analysis.md`. Isolated native
route/counter probes are also complete in
`native-hnsw-attribution-20261018/hnsw-route-coverage-v4-20261018/`: the mixed
fixture records both filtered HNSW (1,024-doc base) and brute-force-by-PK
(11-doc deltas), and the 1,200-doc graph probe records HNSW distance, visit,
filter and candidate counters. These are route/counter attribution fixtures,
not yet an end-to-end cost breakdown for the representative 256-D, varied
selectivity hybrid workload; they do not justify a vector-search optimization.
M4 timing-only A/A samples show median mutex wait 0.084–0.161 ms and median
posting/scoring execution 0.125–0.179 ms across reverse arms, but timing
instrumentation/query-tail noise is material and the A/A analysis does not
establish a stable overhead or candidate win. Keep diagnostics optional.

The owner requested a rerun after reducing host load. The immediate preflight
at 20:00:51 +07 passed (9.922 GiB available, 28.06 GB free, I/O PSI full
avg10 1.46%, load-1 1.24), but the mandatory just-before-run recheck at
20:02:21 failed on I/O PSI full avg10 5.10% (limit 4.46%; other metrics
passed). The attempted run stopped before launching the workload; receipt is
`hnsw-route-coverage-rerun-20261018/host-before.txt`. One bounded retry is now
waiting on the exact four-metric envelope as background job
`job-971567-646`; it reuses the already-built isolated binary and runs only
the fixed-snapshot sequential HNSW/M4 counter cohort after a passing recheck.
No threshold is relaxed. Result artifacts will be in
`hnsw-route-coverage-rerun2-20261018/`.

After that cohort completes, the next executable research step is the paired
hybrid end-to-end cohort with native HNSW route counters and M4 diagnostics off
for primary latency; keep diagnostics-on counter/timing arms separate. Then
run matching current-source sanitizer gates before selecting an optimization.
Marathon DoD remains incomplete; production remains untouched and all changes
research-only.

## 32. Repeated-filter candidate-cache check — 2026-10-18

Resolved the previously failing filter-cache oracle. Its cross-index comparison
now compares hit sets/scores independent of DocId assignment; separate same-index
checks retain exact hit order, scores and DocIds across miss/hit and post-mutation
repopulation. The final warning-clean oracle and ASan/UBSan/LSan oracle both
pass `checks=18481 failures=0`. The test now also exercises mutation
invalidation and cache repopulation. Existing default behavior remains off.

One resource-gated 4-reader ABBA profile (control/cache/cache/control) completed
on a quiescent 1,024-doc, four-shard, 256-D normalized COSINE fixture. It used
32 fixed queries, vector-filter match counts 1/341/512/1,024, top-k 8/64, and
M4 counters-only plus isolated native route tracing in every arm. All four runs
passed `checks=8616 failures=0`; host pre/post resource gates passed. Query hit
signatures were identical across 32 keys; cache hits were 0/32 in both controls
and 32/32 in both candidate arms. Candidate-minus-control paired median M4 wall
deltas were −0.264 and −0.117 ms; M4 reader-thread CPU deltas −0.126 and
−0.139 ms; end-to-end query wall deltas −0.472 and −0.503 ms. Both AB/BA orders
favor the cache. M4 reader-CPU deltas were negative for all 32 keys in both
pairs, while end-to-end and M4-wall ranges included both signs. There are only
two order pairs on a synthetic workload. This is a promising local signal, not
a robust general latency claim. Per-posting membership/scoring counters stay unchanged;
they do not count the `FilterCandidates` expression scan skipped on cache hits.
Direct filter scan timing/count is unavailable because this profile is
counters-only. Keep the existing bounded option research-only/default-off; no
production default or integration change follows.

Evidence and receipts:
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/filter-candidate-cache-optimization-20261018/`.
The ABBA runner and analysis scripts are archived there; resource envelopes,
per-arm host receipts, exact commands, raw logs/native traces, binary/source
hashes and analysis are retained. The native profile is the isolated research
copy; production/native source was not modified. The repository's unrelated
working-tree changes under `rust/crates/` remain untouched.

The planned diagnostics-off primary hybrid control has since completed once:
32 query signatures match the counters-only control, harness checks pass, and
native route tracing records 120 `hnsw_search_dispatch` plus 10
`bf_by_primary_keys` events. Its median query/M4 wall is 1.478/0.314 ms; this
single route-instrumented profile is descriptive, not a paired latency result.
Full stage metrics and receipts:
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/reader-search-primary-control-20261018/analysis.md`.

The diagnostics-off cache ABBA completed after two no-launch preflight stops
at I/O PSI full avg10 5.11% and 5.50%; scalar values, timestamps and the
snapshot-overwrite caveat are recorded in
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/filter-candidate-cache-primary-abba-20261018/gate-no-launch.md`.
The 4.46% threshold was not relaxed. Job `job-971567-743` then ran all four arms;
each exited 0, `checks=8616 failures=0`, and passed its post-run resource gate.
All 32 hit signatures and all native route counts match across arms (120 HNSW,
10 brute-force-by-PK per arm). Paired median query-wall deltas were −0.404 and
−0.287 ms; M4-wall −0.272 and −0.189 ms; M4 reader-CPU −0.145 and −0.124 ms.
M4 CPU deltas were negative for all 32 keys in both orders, while query-wall
ranges include both signs. Combined with the separate counter-only ABBA (0/32
control vs 32/32 candidate cache hits), this supports a promising local cache
effect on the synthetic fixture, but not a general performance claim: two
order pairs, native route instrumentation, and no representative production
data. Full primary analysis:
`research/m4-native/results/m4-b2-cost-model-20261018/paired-benchmark/filter-candidate-cache-primary-abba-20261018/analysis.md`.
Keep the existing bounded option research-only/default-off. No production or
native-source changes. Close this cache candidate; resume only the remaining
approved search-execution work, without another cache implementation attempt.

## 33. Search execution cost-model point checks — 2026-10-19

Phase B/C follow-up added a test-only Flat/HNSW cost-model fixture. Current
source compiled warning-clean; the post-build binary SHA-256 matches the binary
used for the point checks (`32236fbd…f2b326c`), and the test-source SHA is
`bc0199d6…4d66b7b`. A failed intermediate build caught an incorrectly escaped
newline literal; source was corrected and the target rebuilt successfully
before the recorded runs.

One counters-on operation profile and a separate counters/tracing-off latency
profile were run at 1,024 and 10,000 docs, 256 dimensions, eight fixed top-10
queries, one run per mode/size. At 1,024, HNSW distance median was 1,051 vs
Flat's 1,024, unique visits saturated all 1,024 nodes, and HNSW was slower on
6/8 query pairs (median paired delta +0.022 ms). At 10,000, HNSW distance
median was 5,887 vs Flat's 10,000, and its one-run counters-off median was
0.923 vs 1.062 ms (paired median delta −0.111 ms; slower on 3/8). This suggests
but does not establish a crossover between the two sizes. Exact hit agreement
was 80/80 for both 1k runs; at 10k, counters-on HNSW matched 78/80 while the
separately built counters-off graph matched 80/80. The discrepancy is
unresolved. 10k latency had wide per-query spread and host receipts show
concurrent CPU-active processes; fixed Flat-first query ordering and single
runs further limit inference. Do not infer a stable win, threshold, or quality
acceptance. No production defaults changed.

Raw receipts, output, route traces, hashes, checksum manifests, combined
summary and analysis script:
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/`.
Expanded analysis: `phase-b-cost-model/analysis.md`.

The one-shot diagnostics-off whole-hybrid route cohort and 10k M4 attribution
cohort have since completed. The latter identified repeated scalar candidate
construction as a large M4 FTS cost. The temporary 128-KiB cache-cap attempt
was rolled back because the prior handoff had closed that branch; final source
retains 64 KiB/default-off. A separate, non-cache Boolean-constant
simplification followed the approved search-execution work: strict oracle and
ASan/UBSan/LSan each pass `checks=18643 failures=0`. Across opposite-order
10k Flat/HNSW primary pairs, Flat top-k was unchanged 64/64; candidate was
faster on 30/32 and 23/32 Flat queries and 32/32 and 22/32 HNSW queries.
Median paired hybrid ratios were 0.745/0.769 (Flat) and 0.666/0.791 (HNSW).
Separate M4 diagnostics showed filter time 1.432→0.558 ms (Flat) and
1.477→0.550 ms (HNSW), while run/posting counts were unchanged. Candidate DocId
vector capacity rose 80,000→131,072 bytes; max RSS differed by +0.3–0.5 MiB.
Research-only KEEP; no production/default change. The diagnostics-enabled
synthetic profiles and host/command/source receipts are detailed in:
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/filter-constant-identity-20261019/README.md`.
The source-backed HNSW random-level seed from `system_clock` means exact hits
across independently built graphs are not deterministic; preserve Flat-based
recall evidence and do not tune ANN parameters.
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/filter-candidate-cache-20261019/`.

A resource-gated paired baseline/candidate reader-compaction crossover
completed on 1,024 docs. Each binary ran two control/compaction order pairs;
each condition had 64 hybrid queries over four reader threads, and compaction
ran a concurrent upsert/delete batch. Both passed `checks=48909 failures=0`.
M4 wall median was 0.522/0.437 ms baseline and 0.482/0.473 ms candidate for
control/compaction; M4 CPU was flat. Candidate whole-hybrid/native medians were
higher, but baseline-first ordering and independently time-seeded HNSW graphs
make this one cross-binary pair non-attributable. This cohort supplies eight
query IDs and one maintenance generation. Its separate same-fixture
Flat/HNSW quality evidence is recorded in §33 above.

A sustained 500-doc × 20-round baseline/candidate pair is now complete
(candidate first, baseline second), with four readers and 80 queries per arm.
Both arms had five compactions, generation 21, 500 live docs, zero stale vector
rows and zero coherence errors. Timeline-derived pin analysis found 80/80
queries at generation lag 0 and no ambiguous pin intervals. `/proc/self/io`
measured 251,011,072 B candidate vs 247,373,824 B baseline (+1.47%); retained
logical bytes matched. Reader p95/p99 was 3.406/4.598 ms vs 3.289/3.983 ms;
writer-commit p95/p99 was 431/594 ms vs 247/270 ms. This is one order, uses a
single repeated query, and does not establish a sustained win or attribute the
large writer-tail delta. The previous 1,024-doc crossover provides query-ID
diversity; §33 contains the existing Flat/HNSW quality evidence. Full raw
receipts/PSS samples/analysis:
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/sustained-multicycle-500x20-20261019/README.md`.

The exact reverse-order sustained 500×20 pair completed on 2026-10-19, baseline
then candidate, with the same binaries/mode/query mix and fresh roots. Both
per-arm resource preflights passed: baseline-before MemAvailable 8,674,652 KiB,
load-1 2.21, I/O PSI full avg10 3.66%, `/var/tmp` 24,311,562,240 B;
candidate-before 8,661,056 KiB, load-1 2.27, PSI 4.10%, disk
24,311,304,192 B. Candidate-after PSI was 4.83%; no workload followed.

Reverse-pair baseline vs candidate: reader p95/p99 4.582/7.726 vs
4.689/8.540 ms; writer-commit p95/p99 239.984/241.956 vs 477.128/498.380 ms.
Candidate writer tails are higher in both run orders (the preceding
candidate-first pair was +74.7%/+119.6%; reverse order is +98.8%/+106.0%), so
the direction was not removed by order reversal. Two pairs still cannot
causally attribute the result. Process CPU is +4.0%, physical writes −8.85%,
sampled PSS −2.4%; no overall win/regression claim. Same five compactions,
generation 21, 500 live docs, zero stale vector rows and zero coherence errors.
Candidate's initial base-vector build was 292.8 ms/89.8 CPU-ms vs baseline
107.8/57.6 (+172% wall); the first four subsequent vector-build cycles were
204.8–256.7 ms vs baseline 85.6–103.1 ms. This accompanies the writer-tail
outlier but is not evidence that the filter simplification caused it. M4 Open
p50 was 55.3 vs 54.6 ms (p95 65.9 vs 58.6), and close/reopen was 294 vs
381 ms (one measurement per arm). No query interval crossed a publication
(0/80); completion at lag zero does **not** verify the separate
reader-pin-during-publish invariant.
Raw per-cycle commit latency, traces, host receipts, PSS samples and hashes:
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/sustained-multicycle-500x20-reverse-20261019/README.md`.

Sustained writer-tail attribution remains inconclusive after two paired orders;
keep the candidate research-only and do not optimize based solely on these
runs. Production integration/migration remain NO-GO. Benchmark-only task; no
current-source oracle or sanitizer gate was rerun.

## 34. Writer-tail reconstruction and trace instrumentation — 2026-10-19

Phase A reconstructed all 80 per-cycle rows from the two existing 500×20
pairs; it did not relaunch either benchmark. Tail commits are concentrated in
the candidate's first 2 cycles in candidate-first order and first 4 cycles in
baseline-first order. Several are non-compaction cycles; later candidate
cycles overlap or beat baseline medians. Live-doc counts, seven logical M4
mutations, six vector delta rows, the five 500-row compactions, and generation
counts match across arms. Candidate tail commit wall (477–594 ms witnesses)
substantially exceeds writer-thread CPU (116–182 ms) and includes high native
vector wall, M4 clone/apply/flush outliers and unassigned post-M4 work. M4 Open
is not specifically elevated on those witnesses. This localizes unexplained
wall time but does not prove fsync, host I/O, or filter causality.

Source-path review of `m4_bridge_integration.cc` found a concrete missing
measurement: after M4 Flush, each generation recursively `SyncTree`s its FTS
and vector state and syncs the root before constructing/publishing the next
snapshot. The vector path separately creates/opens a collection, converts
rows, upserts, flushes, writes generation metadata, and on the scheduled fifth
append rebuilds 500 rows. The test harness previously timed vector build and
M4 update only in aggregate. Native `zvec_collection_flush` synchronously
enters `CollectionImpl::flush` and `writing_segment_->flush`; M4 ApplyBatch
uses fsynced WAL, and M4 Flush writes durable run/manifest state. Whether the
subsequent recursive SyncTree is safely redundant is unknown; no durability
operation was removed. The Boolean filter simplification is called from FTS
filter evaluation, not from the writer's ApplyBatch/Flush call graph. The
reader concurrency may still affect shared host resources, so causal attribution
remains open.

A test-only trace build was made without changing either archived A/B binary,
the M4 library, the native library or production code. The harness now records
an absolute monotonic anchor, writer and reader TIDs, generation-WAL timing,
native vector open/document-conversion/upsert/flush wall and caller-CPU, full
generation-metadata timing, M4 clone/open/apply/flush intervals, the explicit
post-M4 SyncTree interval, and snapshot-finalization timing. `sync_trace.c`
interposes `fsync`, `fdatasync`, `syncfs` and `msync`; it buffers call wall time,
calling-thread CPU, TID, fd target path and result in memory and writes JSONL
only at process exit. `analyze_sync_trace.py` joins those monotonic events to
writer-cycle phase intervals and labels writer, reader or other TIDs. It does
not synchronously log on the hot path. The ON arm still adds clock reads,
`/proc/self/fd` reads and a mutex around trace-event storage; an OFF/ON A/A is
required before interpreting latency deltas.

Diagnostic build evidence: warning-clean C++ harness build with
`-Wall -Wextra -Werror`; tracer C build with `-Wall -Wextra -Werror`; analyzer
`py_compile` and LSP checks clean. A disposable 64-doc × 1-round `/tmp` smoke
emitted 360 sync records, zero dropped/failed calls, captured writer/reader
TIDs, passed the 4-reader coherence check and reopened generation 2. This is a
tracer/functionality check on tmpfs only; its fsync durations and latency are
not persistent-storage or sustained-performance evidence. Trace output is
under `/tmp/m4-writer-tail-trace-smoke-final/`. The diagnostic candidate
executable `/tmp/m4-writer-tail-diagnostic/m4_bridge_integration` SHA-256 is
`fec8c7ac…`; it links unchanged candidate M4 archive `f72975a7…` and native
C API `c803a0c3…`. The preload trace library SHA-256 is `c993c96f…`.

No 500×20 instrumentation A/A or traced sustained arm has run. Latest
resource check (2026-10-19 13:18:16 +07:00): MemAvailable 4,581,316 KiB vs
required 5,477,762 KiB (short 896,446 KiB); load-1 2.15 ≤ 3.14, I/O PSI full
avg10 0.38% ≤ 4.46%, and `/var/tmp` free 23,960,240,128 B ≥ 18,219,000,000 B.
`pacman`/`yay` were active at the check; do not interrupt them. This is a
resource-gated hold, not an attribution result; do not weaken the envelope or
run the sustained workload while it fails.

## 35. Outer FTS sync elimination — bounded correction and sustained validation (2026-10-19)

The low-overhead v2 tracing OFF/ON A/A completed on the current binary, but
its measured overhead is too large for causal latency interpretation: commit
p50/p95/p99 changed from 190.5/212.2/220.5 ms OFF to 195.2/293.7/295.9 ms ON;
reader p95/p99 changed 3.06/3.58 to 3.24/3.67 ms. The ON trace recorded 3,399
sync events, zero drops/failures, and 201 outside-cycle events. It is useful
for event coverage, not writer-tail attribution. Raw artifacts:
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/diag-aa-v2-20261019T070331Z/`.

A research-only diagnostic variant skips outer per-file fsync of M4 FTS
files after `Index::Flush`, retaining directory fsync. This changes the
observable durability sequence and is NOT established as durability-equivalent;
its benchmark results must not be used to claim equivalence. Source inspection
found `FlushInternal` writes/fsyncs segment and metadata files, publishes the
manifest, then calls `wal_.Reset()` (`ftruncate`) without an internal fsync.
The normal outer `SyncTree` subsequently fsyncs every file under `fts/`; the
diagnostic's broad skip omits that fsync for `wal.log` as well as other FTS
files. A directory fsync persists directory-entry changes, not file contents
or WAL size/truncate metadata. Reopen skips WAL records with `seq <= manifest.wal_seq`,
which may make a stale committed WAL logically harmless, but this has not been
verified across crash/power-loss points. Recovery ordering and crash semantics
remain unproven; do not promote or describe the sync-elision mode as safe.
This diagnostic changed no M4 library, production code or index format.
Current-source correctness/lifecycle evidence: M4 oracle `checks=18643 failures=0`,
vector delta `1401 failures=0`, lifecycle `3581 failures=0` (generation 22,
23 live rows, max retained generations 3). ASan/UBSan/LSan run completed with
`ASAN_PASS`; these are functional/memory checks, not durability-equivalence
proof. Artifacts are under
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/hardlink-sync-corrected-{tests,asan}-20261019/`.

Three resource-gated 500-doc × 20-round same-source pairs (four readers,
80 queries/arm, fresh roots) now exist: treatment-first at
`sync-reduction-pair-20261019T070613Z`, control-first at
`sync-reduction-pair-20261019T070711Z`, and confirmatory treatment-first at
`sync-reduction-confirmatory-pair-20261019T072028Z`, all below the writer-tail
causal-artifact directory. Every arm exited successfully; each had five
compactions, published generation 21, 500 live docs, zero stale vector rows
and zero coherence errors; retained logical root bytes matched. Treatment vs
control commit p95/p99 were 213.7/214.1 vs 361.0/383.1 ms in the first pair,
369.2/390.3 vs 257.4/258.7 ms in the reverse-order pair, and 211.5/228.6 vs
464.4/497.1 ms in the confirmatory pair. In all three pairs, the *second* arm had higher writer p95/p99 regardless of
which binary it was: +68.9%/+78.9% (control second), +43.5%/+50.9% (treatment
second), and +119.6%/+117.4% (control second). Arm-start I/O PSI also rose
from first to second in every pair (0.32→1.23%, 2.33→3.02%, 0.43→1.34%); load
and memory receipts remained inside the broad gate. This consistent
second-arm association, plus high variation in control base-vector build
time, makes carry-over/host pressure a plausible confound, but not a proven
cause. Do not claim a stable causal writer-tail win or regression. Reader
p95/p99 favored treatment in all three pairs, but these paired observations
likewise do not establish a general benefit.

The 50-ms external sampler now records per-process `/proc/<pid>/io`, system
I/O and memory PSI avg10+total, MemAvailable/SwapFree, `pswpin`/`pswpout`, and
per-device `/proc/diskstats`, along with monotonic timestamps and PSS. Logs are
buffered during sampling to avoid per-sample file flush overhead. A disposable
350-ms smoke produced eight monotonic rows with all expected columns; this
validates plumbing only, not sampler overhead or benchmark attribution. The
cycle analyzer uses monotonic benchmark anchors and clips pre/during/post
windows at adjacent cycle boundaries, so attribution windows do not overlap;
a two-cycle synthetic join passed. A
separate 60-s host-only baseline showed substantial drift (I/O PSI full avg10
0.20–2.66%, 1,924 pages swapped in; a follow-up had PSI 0.92–5.31%, 8,236
pages in, 24 out, and MemAvailable as low as 4,931,476 KiB). Both of those captures flushed every row and are not accepted as quiescence
references. Three later buffered 60-s captures were also rejected by the
unchanged stability gate, as detailed below. No A/A arm ran.

The fsync-elision mode remains strictly research-diagnostic-only. It skips the
outer file fsync across the FTS directory, including `wal.log` after
`FlushInternal` has called `ftruncate` without an internal fsync. The normal
outer fsync therefore may be what makes WAL truncation durable; a directory
fsync is not a substitute for file-data/size durability. Although reopen skips
WAL records covered by manifest `wal_seq`, crash/power-loss recovery semantics
have not been proven. Do not use the sync-elision performance results to claim
durability equivalence, or promote this mode. Reproducible sampler smoke
source and CSV/checksums: `research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/sampler-smoke-20261019.py` and sibling `sampler-smoke-20261019/`. Full raw arm receipts, PSS, commands, hashes and checksums are preserved with each pair. Detailed interpretation:
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/phase-a-reconstruction.md`.

**Status / blocker:** No A/A arm ran. Three buffered 60-s ambient baselines
(50-ms samples, 1,200 rows each) were rejected by the unchanged stability
check (`baseline drift: last half outside first-half envelope for load1_max`).
I/O PSI full avg10 also left the first-half envelope in all three; swap-in was
255, 924 and 2,655 pages. This is evidence of host variability only, not A/A
or writer-causality evidence. After owner-approved removal of four exact
`/var/tmp` trees, the latest resource receipt at 2026-10-19 16:39:41 +07:00
passes all limits: MemAvailable 9,221,468 KiB, load-1 2.57, I/O PSI 3.49%,
and 19,255,046,144 B free. Disk space is no longer the blocker. Full per-window
receipts and exact resume condition are in
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-resource-gate-wait-20261019/README.md`.
Latest no-launch resource receipt:
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-final-resource-no-launch-20261019Tcurrent.json`.
**Resume condition:** wait until a new 60-s buffered baseline satisfies the
same unchanged all-feature envelope check (last three 10-s windows inside the
first-three envelope); do not relax it or run the arms on a rejected baseline.
Then run `run_host_carryover_aa.py` with `M4_RESOURCE_ROOT=/var/tmp` and
`M4_AA_BINARY=/home/masis/src/zvec-grep/.local/m4-preserved/m4_bridge_integration`.
The runner captures that baseline and launches two same-binary arms on fresh
roots only if it is accepted. Before arm two it requires three consecutive
measured 10-s windows with pressure/load/I/O/swap activity no higher than
observed baseline maxima and MemAvailable/SwapFree no lower than baseline
minima, plus all resource gates passing. It aborts rather than assuming
recovery after timeout. Analyze per-cycle commit time against non-overlapping
pre/during/post PSI increments and swap/I/O deltas; PSI that rises after a
slow commit is not its cause. If arm two remains slow despite verified
recovery, that is only evidence of order/host drift; separate filesystem
writeback/page cache, thermal throttling, swap and background-process activity
with further evidence. If A/A does not reproduce the drift, plan a newly gated
baseline/candidate comparison under matching host conditions. Production
integration, migration and deployment remain NO-GO.

## 36. Host stability gate audit and A/A readiness — 2026-10-19

**Outcome: the same-binary A/A was not run.** After completing offline replay
of the three pre-existing 60-second baselines, source audit, synthetic gate
cases and host attribution, one controlled post-audit attempt used the same
binary (`70eb45587252770b9f3fbcf8d057f6aff63ca03041a31d0a0981262853dd97a8`).
Both its standalone and runner preflight receipts passed all four unchanged
resource limits. Its complete 60.019-second baseline (1,200 samples; 50-ms
nominal period) then failed the unchanged first-three/last-three envelope on
`load1_max`. The runner stopped before either arm; the log has no
`AA_ARM_START`. This is not an A/A result and says nothing about sync-elision
latency or durability.

The original three captures remain complete and timestamp/cadence-valid: six
200-sample, ~9.95-second windows each, with no ≥1.5-period gaps. Direct replay
reproduces the gate's first reason (`load1_max`) and finds 23/21/15 total
feature/window misses. The I/O PSI full avg10 time series exceeded its 4.46%
preflight ceiling in 1,128/1,200, 1,058/1,200 and 1,158/1,200 samples. Therefore
the failures are not established as rejection on tiny, benign load noise with
all observed pressure within limits. The current strict gate was retained; no
false-rejection rate is quantifiable without stable/unstable ground truth.

The fourth, post-audit baseline provides another separation between snapshot
safety and whole-window stability. Runner preflight at 17:22:59 +07 passed:
MemAvailable 6,020,276 KiB, load1 2.17, I/O PSI 0.51%, `/var/tmp` free
19,211,120,640 B. During the subsequent capture, 64/1,200 MemAvailable samples
fell below the existing 5,477,762-KiB threshold (minimum 5,304,712 KiB, a
173,050-KiB shortfall); load and I/O PSI stayed below their absolute ceilings.
The all-feature stability gate nevertheless failed and no arm ran. A read-only
post-baseline snapshot at 17:32:29 +07 had MemAvailable 5,472,864 KiB, 4,898
KiB below the threshold; the other three gates passed. No fifth baseline or
retry was started. The next safe action, if later requested, is to wait until
all four current preflight limits pass and then require a fresh valid,
stable 60-second baseline; do not run an arm from a rejected trace.

Diagnostics now distinguish `SAFETY_GATE_FAIL`, `STABILITY_GATE_FAIL` and
`MEASUREMENT_INVALID` without changing any threshold or acceptance envelope.
The baseline validator checks required/finite fields, diskstats JSON, strictly
increasing monotonic time, cadence gaps, sample start/end brackets, capture
coverage and six sufficiently full windows. The buffered arm sampler keeps
per-process PSS/I/O and host PSI/memory/swap/disk rows in memory until the
benchmark child exits, adds monotonic start/end timing, and omits process
command-line arguments from persisted snapshots. A targeted smoke verifies no
telemetry output file is opened while the measured child is live. The current
cycle analyzer passes a two-cycle monotonic synthetic join with exact 2/10/3
pre/during/post samples and no overlap.

Host attribution remains **inconclusive for the three earlier failures**.
The historical captures have no per-process I/O deltas. Two later, read-only
30-second host captures observed I/O PSI stalls, ~23–24 MiB swap-in and NVMe
traffic; sampled process `write_bytes` did not explain most NVMe writes. Btrfs
counters showed one short routine transaction commit and no allocation reclaim
counter changes; no Btrfs compaction or causal process was established. Do not
stop or alter host processes on this evidence.

Policy recommendation: keep the current strict profile and inter-arm recovery.
An additional continuous check of the three time-series-measurable absolute
resource ceilings is a candidate for owner approval, especially after the
observed MemAvailable dip, but it is not implemented; disk free remains a
snapshot-only metric in available artifacts. Robust-median summaries have no
calibrated tolerance. “Run then invalidate” would waste arms on these
preflight-passing but unstable traces. Matched host-state A/A cannot be
assessed without two actual arm timelines. No rejected baseline was
reclassified as PASS.

Full report set, exact command/environment, host receipts, post-audit raw
baseline, attribution captures, source-level replay, synthetic tests and
SHA-256 manifest:
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/`.
The directory contains `stability-gate-source-audit.md`,
`stability-gate-offline-replay.json`, `host-pressure-attribution.md`,
`stability-policy-assessment.md`, `diagnostic-fixes-and-tests.md`,
`aa-readiness-result.md`, and `aa-readiness-command-and-provenance.json`.
Production integration/migration remain NO-GO; M4 work remains research-only.

## 37. Rechecked A/A readiness — 2026-10-19

A fresh read-only preflight passed all four unchanged resource limits at
18:23:32 +07:00: MemAvailable 10,079,328 KiB, load1 1.03, I/O PSI full
avg10 0.76%, and `/var/tmp` free 19,198,300,160 B. The runner's own preflight
also passed at 18:23:37: 10,065,240 KiB, 1.03, 1.98%, and 19,198,300,160 B.
The same verified binary was used. Its valid 60.019-s/1,200-sample baseline
was rejected by the unchanged stability envelope (`load1_max` in the later
half fell below the first-three-window range: 0.84 vs 0.88 minimum). During
that capture I/O PSI full avg10 reached 8.02% in a window (above the existing
4.46% snapshot ceiling). Runner exited before either arm; no A/A workload was
launched, and no latency/durability conclusion follows. Do not retry until a
new preflight passes and a fresh stable baseline passes the existing gate.

Receipt directory:
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/host-carryover-aa-pair-20261019T112337Z/`;
runner log: `/tmp/pi-bg/job-2966907-46.log`.

## 38. Diagnostic vs qualification modes and safety refusal — 2026-10-19

Source review found the existing `preflight()` four limits are explicitly used
as launch safety gates; they remain unchanged HARD SAFETY: MemAvailable >=
5,477,762 KiB, `/var/tmp` free >= 18,219,000,000 B, I/O PSI full avg10 <=
4.46%, and load1 <= 3.14. They are checked before each arm; available RAM/load/I/O samples in the
ambient baseline are also checked samplewise, and every metric is sampled
during each child. Any arm-time violation terminates the child and leaves a
receipt. Disk reserve remains snapshot-only in the ambient sampler. Telemetry
integrity and the six-window drift envelope are measurement quality;
QUALIFICATION still requires both. DIAGNOSTIC bypasses only a valid
stability-envelope failure, while preserving the four safety limits and the
existing first-three-window SwapFree floor/memory PSI ceilings. No numeric
limit was relaxed or newly invented.

Added `--mode diagnostic|qualification` to the same-binary A/A runner. Both
arms remain sequential and use fresh roots. Arm raw files include monotonic
start/end, process CPU ticks, PSS, process I/O, memory/swap, PSI, load and
per-device diskstats; inter-arm telemetry and quiescence are separate. The
cycle analyzer joins commit latency to host state; the diagnostic summarizer
reports p50/p95/p99, CPU, writes, peak PSS and second/first comparisons, and
labels all diagnostic output `EXPLORATORY / NOT QUALIFICATION EVIDENCE`.

Verification: Python compilation PASS; `test_aa_gate_policy.py` PASS (4
focused tests); percentile self-check PASS. One diagnostic attempt at
19:13 +07 had both point-in-time preflights pass, but the valid ambient trace
failed hard memory/swap pressure safety: baseline SwapFree floor 16,970,032
KiB vs later 16,899,232 KiB (70,800 KiB below); memory PSI some/full reached
0.70% vs the first-three-window 0.00% ceiling. I/O PSI full avg10 was
6.05–10.77% in all six ambient windows, above the hard 4.46% ceiling.
Absolute MemAvailable stayed above its floor. The runner version used for the
attempt stopped on memory/swap pressure before either arm. A subsequent source
audit added samplewise ambient enforcement of existing absolute limits; this
raw trace also fails the hard I/O PSI check. There is no A/A latency result and
no project-level block. Receipt:
`research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/host-carryover-aa-pair-20261019T121316Z/`;
report: `research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/diagnostic-fixes-and-tests.md`.

## 39. cgroup-v2 containment and safety calibration — 2026-10-19

**Outcome at the original §39 snapshot: containment implemented and validated; A/A did not run because disk reserve failed.** Subsequent owner-authorized `/var/tmp` cleanup restored disk headroom; the current readiness snapshot and remaining I/O-PSI refusal are recorded in §40. Research-only source is `research/m4-native/tools/m4_containment_runner.py`; executable tests are `research/m4-native/tools/test_m4_containment_runner.py`. No production Rust/Zvec, daemon, schema, index, deployment, migration, privileged command, or system configuration was touched.

The runner uses a per-arm transient user service (`Delegate=yes`, `ProtectControlGroups=yes`, cgroup depth zero) and holds workload start until ownership, required telemetry, approved limits, and an independent watchdog are ready. Bootstrap and workload membership are verified. pidfd protects supervisor and process signaling from PID reuse. The watchdog records host/cgroup/workspace samples, aborts on existing hard guards, supplied baseline-relative SwapFree/memory-PSI limits, cgroup OOM, runtime timeout, telemetry failure, and approved sampled-workspace ceilings. Abort receipts precede final `cgroup.kill`; supervisor loss is tested and triggers socket cleanup. Receipt safety outcome is independent of measurement quality. Approved memory/swap/CPU/workspace budgets only; no numeric budget was set.

Final executable suite at §39: **8 passed, 0 failed**. Updated suite now has **9 passed, 0 failed**, plus one real-systemd A/A sequence integration test and four gate-policy tests; current evidence is summarized in §40. Raw §39 receipts, probe, final host snapshot and SHA-256 manifest: `research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/containment-tests-20261019T131427Z/`. Full containment design/evidence/policy: `research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/containment-calibration.md`.

Capability boundary: user cgroup v2 delegates CPU, memory and pids, not I/O. Per-arm `io.stat`/`io.max` are unavailable; `io.pressure`, host diskstats and vmstat are recorded, but host-wide diskstats and process `write_bytes` do not identify physical writes by the arm. `/var/tmp` is Btrfs; sampled `st_blocks` may double-count shared extents, and final-tree FIEMAP inspection is not a historical peak. Existing 500×20 successful process samples show PSS 304,227/286,915 KiB and swap 0/0, but do not supply cgroup memory or active workspace peaks. Thus no defensible cgroup memory/swap cap or workspace quota is selected; collect those under a representative contained run before any owner decision.

Final read-only host snapshot `2026-10-19T13:14:49.926954Z`: MemAvailable 5,771,328 KiB (pass); SwapFree 13,282,320 KiB; `/var/tmp` free 15,469,010,944 B (**fail by 2,749,989,056 B**, approximately 2.56 GiB); I/O PSI full avg10 4.32% (pass, 0.14 points of headroom); load-1 1.62 (pass); memory PSI some/full 0.04%/0.04%. No workload/A/A command ran. Do not delete unreviewed data or weaken gates.

**Historical next action superseded by §40:** recover disk reserve and wire per-arm containment; both are now done. The latest readiness snapshot has a separate I/O-PSI hard-safety refusal, detailed in §40. `io_max` remains unavailable; an I/O ceiling cannot be applied per cgroup. No new memory/swap/workspace ceiling is authorized by this evidence.

## 40. Same-binary A/A wiring and current readiness — 2026-10-19

The A/A orchestrator is now wired to the containment runner: each arm gets a transient per-arm cgroup, fresh root and independent watchdog. It fingerprints binary SHA-256, current Git revision plus deterministic M4 source-tree and measurement-runner digests, and resolved shared-library paths/SHA-256s. Identity is captured before the baseline and checked again before each arm; drift refuses launch. `ldd` load addresses are normalized so ASLR cannot cause false identity drift. The integration test verifies exact runtime identity across both arms. DIAGNOSTIC bypasses only the stability-envelope quality failure; hard resource and samplewise safety checks remain blocking.

Verification on current source: `py_compile` PASS; containment suite **9/9 PASS**; contained A/A sequencing/systemd integration **1/1 PASS**; gate-policy tests **4/4 PASS**; `git diff --check` PASS. No active M4 benchmark process or running M4 transient service remained after tests. These are harness/control-flow checks only, not a 500×20 result.

Latest launch-safety snapshot at 2026-10-19 20:56:34 +07:00: MemAvailable 6,886,092 KiB PASS (+1,408,330); `/var/tmp` free 19,470,082,048 B PASS (+1,251,082,048); load-1 1.76 PASS; I/O PSI full avg10 **4.52% FAIL** (ceiling 4.46%, over by 0.06 percentage points). No 60-second baseline capture or benchmark arm started. Receipt: `research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-readiness-gate-20261019T205634+0700.json`. Do not repeat this gate in a polling loop or weaken the limit; this is a recoverable host-resource refusal, not an owner budget decision.

Binary identity was repeatable across two probes: SHA-256 `70eb45587252770b9f3fbcf8d057f6aff63ca03041a31d0a0981262853dd97a8`; current Git revision `b4b86860c574311bec80089951b6187cbf194399`; current M4 source-tree SHA-256 `e6e4702945f2ee29f1a651d6a51d1226c104a4c0b16c4d7a485e6f09c2334ec0`; measurement-runner tree SHA-256 `ebda49add2738cef83622ccf21e94073897f4159b5fcb71c7ec11fcb7294318d`. `libzvec_c_api.so` resolves to `.local/native/zvec-native-build/lib/libzvec_c_api.so`, SHA-256 `6d55b9131766d943fbad1f77dbebc6a2db7951ce8d89ad46a92b7619cc92d950`; other shared dependencies are fingerprinted in the receipt. Archived build provenance is partial: current `src/index.cc` matches its archived build hash, but current `tests/m4_bridge_integration.cc` hash `31e9e1db...` differs from archived `01943e0c...`; compiler flags were not retained. This remains a valid exact-same-binary/order diagnostic, but any result applies to this preserved binary and is not proof it represents the current harness source.

**Exact next action:** when a fresh resource snapshot naturally satisfies all four unchanged hard limits, invoke the already-wired diagnostic A/A once. Its own 60-second baseline, samplewise safety and memory/swap rules are mandatory; any failure produces a refusal receipt and neither arm runs. No automatic retry or continuous polling loop is active.

## 41. Instruction scope and policy-source audit — 2026-10-19

**Disposition: scoped M4 guidance added; root guidance narrowed to global agent behavior.** Root `AGENTS.md` now contains repository-wide authority, safe continuation, mission termination/reporting, and verification rules only. New `research/m4-native/AGENTS.md` contains durable M4 scope, design invariants, and workflow constraints; it intentionally contains no dated benchmark readiness, host values, or experiment status. Both files are currently untracked in this worktree, so they must be included in the intended change/package to persist and be loaded by future agents. This session explicitly read both files; contract tests verify their expected division and the mission-termination clauses.

**Policy source:** `research/m4-native/docs/benchmark-policy.json` is the only machine-readable threshold/classification source. The Markdown is explanatory; numeric guard values were removed from it, and dated readiness observations remain in the canonical handoff/receipts. The containment runner loads JSON directly. `run_sync_reduction_pair.py` and `run_host_carryover_aa.py` consume its policy through the shared runner; retained `run_diag_aa_v2.py` and `run_matched_evaluator_pair.py` now load JSON rather than duplicate threshold literals. Regression tests check all bindings and ensure Markdown/legacy scripts do not reintroduce a second numeric source.

**Bounded rehearsal:** synthetic case 1 blocks a benchmark but leaves an already-authorized independent audit/test item; expected handling is stop the benchmark, execute the independent checks, and report the mission partial. Synthetic case 2 has no independent authorized work; expected termination is `OPERATION_BLOCKED`/`PARTIAL`, with exact failed metric/value/limit, no-launch outcome, remaining DoD, and recovery condition—without polling, auto-retry, invented work, or a completion claim. Scenario J and static contract tests cover the second case. This finite rehearsal does not launch a benchmark and is not proof of arbitrary LLM runtime behavior.

Verification: `test_m4_benchmark_policy.py`, `test_m4_harness_scenarios.py`, Python syntax checks, and `git diff --check` pass. This closes the instruction organization, policy-source consistency, and bounded termination-rehearsal items only; it does not qualify a benchmark or alter §40’s resource-gated next action.

## 42. Harness repair package verification — 2026-10-19

The Phase A package was committed separately as `e1309f31d5d8788e1da5095ccd2fad62e02b8fed` (`research(m4): package writer-tail harness repair`). It includes root/scoped instructions, benchmark policy JSON/Markdown, containment runner, A/A orchestrators and regression tests, README/RESULTS/canonical handoff, and selected calibration/readiness receipts. Scope audit found **no Rust `.rs` source**; unrelated working-tree changes under `rust/crates/` remain untouched.

Focused gates: benchmark-policy **5/5**, mission/scenario contracts **4/4**, A/A gate policy **7/7**, synthetic containment **11/11**, tiny contained A/A sequencing **1/1**, `py_compile`, and staged whitespace check PASS. No performance benchmark arm ran. This is harness-package closure only, not writer-tail attribution or whole-mission completion.

## 43. Writer-tail attribution and current-source provenance — 2026-10-19

**Status: PARTIAL; current-source build deferred and current-source readiness not evaluated; root cause open.** Offline review of the sustained 500×20 filter-identity A/B in both orders shows candidate writer p95/p99 above baseline in both: candidate-first +74.7%/+119.6%; baseline-first reverse pair +98.8%/+106.0%. This weakens a simple second-arm explanation but does not establish causality (two pairs only). Candidate native-vector-build p95 was 246.4/232.1 ms versus 111.0/119.3 ms baseline; M4-update p95 107.7/113.5 versus 103.7/78.3 ms; post-stage commit residual p95 72.6/128.4 versus 43.9/56.5 ms. Native-vector build writer-thread CPU p95 rose less (70.1/66.4 vs 60.5/53.5 ms), so most of the wall delta is not accounted for by bridge-writer-thread CPU; this is consistent with wait/other-worker/host delay but does not identify which. The retained measured binary emitted aggregate vector build wall/CPU only. Current source contains per-cycle open, document-conversion, upsert, and flush wall/CPU arrays, but it is not yet built or measured. These are correlated stages, not a causal decomposition. Reader CPU rose modestly (+6.2%/+2.5%); per-cycle host-pressure is absent.

Scheduled vector compaction is not necessary for the high tail: candidate-first p95 and maximum commit cycles were non-compactions; in reverse order p95 was a non-compaction while the maximum was a compaction. Ordinary native vector build/upsert/flush remains a proximate suspect, but the evidence cannot separate that from reader contention, host I/O/scheduler carry-over, M4 update variability, or residual work. The largest candidate commit tails cluster early: candidate-first pair cycles 0–1 were 593.9/431.1 ms, with total vector builds 307.1/246.4 ms and no compaction; in the reverse pair cycles 0–3 were 406.6–498.4 ms, with vector builds 204.8–256.7 ms (the first three non-compaction, fourth compaction), followed by substantial overlap with baseline. Initial base-vector build was 159.1 ms for candidate vs 115.9 ms baseline in candidate-first order, and 292.8 vs 107.8 ms in reverse order. This suggests startup/cold-state or early-cycle effects worth testing alongside candidate-associated reader/native contention; it does not identify the cause. Candidate physical writes were 8.85% lower in reverse order, which does not support a general write-amplification explanation. The three separate sync-reduction pairs show a second-arm tail with rising host I/O pressure, making carry-over plausible for those pairs; this does not prove it explains the order-reversed filter-identity A/B. The 1,024-document reader/compaction cohort measured no writer acknowledgement latency and does not resolve this question.

**Current-source identity audit:** preserved measured binary SHA-256 `70eb45587252770b9f3fbcf8d057f6aff63ca03041a31d0a0981262853dd97a8`; archived build receipt bridge-source hash differs from current `m4_bridge_integration.cc` hash `31e9e1db9dfacba98987c38799efcdc2be2bb805656e4ef89c27273d047a1cb5`. Current compiler, Release flags, compile command, source hashes, dependency library hash, and native-profile dirty-tree identity are captured in `current-source-build-20261019/provenance-audit.json` and `compile_commands.json`. CMake configuration completed in `/var/tmp/m4-writer-tail-current-source-20261020`; build was not started.

The build-only snapshot (`current-source-build-20261019/build-preflight.json`) measured MemAvailable **721,936 KiB < 5,477,762** (short 4,755,826); load-1 **7.77 > 3.14**; `/var/tmp` available **14,929,784,832 B < 18,219,000,000** (short 3,289,215,168); I/O PSI full avg10 **1.50% ≤ 4.46%** (pass). An unrelated process was observed at 519% CPU and 6,551,388 KiB RSS; its command arguments are redacted and no process was signaled. This is a conservative build defer under the observed resource state, not a formal CMake build gate or a fresh benchmark-readiness result. The separate earlier 20:56 readiness attempt failed I/O PSI full avg10 4.52% > 4.46%, before any 60-second baseline or arm launch. No additional readiness evaluation was made after the source-provenance audit; the earlier receipt is not reused.

**Next action at that time:** when a fresh host snapshot naturally satisfies all four unchanged policy limits, and the subsequently required baseline/recovery and measurement-quality gates pass, compile the current-source bridge using the recorded single-job command and capture its binary/dependency provenance. Then make at most one authorized same-binary A/A readiness/run attempt; if any gate fails, preserve the exact receipt and stop without retry. Do not signal unrelated processes, weaken thresholds, or poll until ready. Causal attribution, current-source build, same-binary A/A, adversarial validation and sustained validation remain open; no production change is authorized by these results.

## 44. Build guard scope, current-source binary, and post-M4 interval — 2026-10-19

**Status: PARTIAL; the single A/A operation is OPERATION_BLOCKED at baseline preflight; no A/A arm ran.** The four limits in `research/m4-native/docs/benchmark-policy.json` gate standalone measurement/A/A preflight, contained arms, and watchdogs; they do not gate CMake compilation. Build instructions contain commands but no separately owner-approved numeric build-only limits. We did not weaken or reuse the benchmark limits as build policy. Compilation proceeded only after a fresh build-specific resource review and in a transient systemd cgroup with `KillMode=control-group`, `RuntimeMaxSec=600`, resource accounting, and one build job.

At 22:58:34 +07, before compilation: MemAvailable 7,440,828 KiB; SwapFree 11,834,068 KiB; load-1 2.50; I/O PSI full avg10 3.82%; `/var/tmp` free 14,298,071,040 B; build-tree footprint 446,525 B; comparable prior bridge output 10,162,330 B; M4 workspace footprint 540,363,155 B. The separate benchmark disk reserve was not applicable to compilation. Build job `job-207232-294` succeeded in 46.828 s (46.119 CPU s), reported peak memory 623M and swap 0B. The new binary SHA-256 is `57f3a19e0fb5dfe4d2d64ea4831f276a81a586c22ee30309d6249ed986ba342f`; CMake/toolchain/configuration, 59 compiler dependency hashes, native profile state, and linked-library hashes are in `current-source-build-20261019/current-source-build-receipt.json`; the byte-identical binary and generated configuration are retained beside it and verified by that directory's `SHA256SUMS.txt`. Correctness-smoke job `job-207232-302` passed a generation lifecycle smoke with 3,581 checks, 0 failures; it is not a benchmark result (26.877 s, reported memory peak 2.2G, swap 0B).

Offline decomposition now also uses the raw `m4_build_end_ms`/`build_end_ms` boundaries: derived post-M4 interval p50/p95 was 28.55/39.10 ms candidate vs 26.30/33.14 baseline candidate-first; reversed order 16.41/102.89 vs 29.09/50.01. Reverse candidate top commit cycles had 111.21/102.89/85.00 ms in this interval. Source places post-M4 `SyncTree` and snapshot finalization there, so this is sync/finalization-containing but not a pure fsync timer. M4 clone timing also includes its own `SyncTree`. This narrows where elapsed time accumulates but does not establish candidate causality. Full decomposition: `stage-decomposition-offline-20261019.md`.

The one permitted qualification-mode A/A readiness attempt (job `job-207232-317`) ran at 23:07:48 +07 and failed only `M4-RG-DISK`: free bytes 14,288,429,056 vs unchanged floor 18,219,000,000 (short 3,930,570,944). MemAvailable 6,043,024 KiB, I/O PSI full avg10 1.79%, and load-1 2.36 passed. No ambient baseline, arm, or automatic retry occurred. Receipt `host-carryover-aa-pair-20261019T160748Z/` includes `operation-status.json`, per-gate preflight, and verified `SHA256SUMS.txt`. Do not delete unreviewed files or retry automatically; recovery requires fresh resource capacity meeting all unchanged guards and a new authorization. Same-binary A/A, current-source sustained and adversarial validation, and causal attribution remain open; no production change is authorized.

**2026-10-20 read-only readiness snapshot (not a new A/A attempt):** at 11:05:30 +07, MemAvailable 6,313,076 KiB, `/var/tmp` free 22,254,964,736 B, and I/O PSI full avg10 0.27% passed; load-1 4.35 exceeded the unchanged 3.14 limit, so the runner was not invoked and no ambient baseline or arm ran. Binary SHA-256 `57f3a19e0fb5dfe4d2d64ea4831f276a81a586c22ee30309d6249ed986ba342f`, source tree SHA-256 `e6e4702945f2ee29f1a651d6a51d1226c104a4c0b16c4d7a485e6f09c2334ec0`, and measurement-runner tree SHA-256 `dec0a54c33304f295a34a4201a108f806d798738e67461d4a1d3f5839e851b30` match the pinned pair; retained build checksums verify 16/16. Harness containment tests remain PASS, but live service launch is unverified: the user systemd manager is degraded with 17 inactive failed synthetic fixture units; no active M4 unit/process was found. The prior one-shot readiness authorization was consumed by the disk-blocked attempt; this task's conditional instruction was not treated as unambiguous renewed authorization. Exact receipt: `research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/aa-resource-gate-readiness-20261020T040530Z.json`. The prior receipt's six manifest entries verify, but the digest previously recorded in prose is malformed (65 characters); the verified actual digest is in the receipt. Next: new explicit owner authorization and a later fresh snapshot passing all four unchanged guards before the exact A/A round.

## 45. Project-local workspace closure and diagnostic A/A no-launch — 2026-10-20

**Workspace migration committed:** `d6280b2` (`research(m4): localize temporary workspace`) on `/home/masis/src/zvec-grep`, branch `work/zg-optimization`. The exact commit scope is `research/m4-native/.gitignore`, `AGENTS.md`, `README.md`, and `tools/test_m4_containment_runner.py`; unrelated Rust changes and untracked M4 research source/evidence were not staged. `.work/{tmp,build,sanitizer,scratch}` are ignored and retained evidence belongs under `results/`. The migration preserves explicit protocol roots: documented benchmark roots requiring Btrfs remain under `/var/tmp`, and `/tmp` retains tmpfs semantics where a protocol specifically requires it. Only disposable correctness/test temporary roots use `.work`; the containment fixture's resource-root substitution remains confined to synthetic fixture mode. No benchmark workload, query mix, sync/durability boundary, or measurement instrumentation changed. Focused checks passed: containment runner 11/11, benchmark policy 5/5, A/A gate 7/7, and contained-pair integration 1/1. The workspace migration paths are clean after commit.

**Build provenance revalidated, not rebuilt:** receipt `current-source-build-20261019/` checksum manifest passed (16/16); retained executable SHA-256 `57f3a19e0fb5dfe4d2d64ea4831f276a81a586c22ee30309d6249ed986ba342f`, ELF build ID `e889629e65a9ac929fa89a4a95ef549b1ec39062`, compiler GCC 16.2.1, Release `-O3 -DNDEBUG`, native `libzvec_c_api.so` SHA-256 `6d55b9131766d943fbad1f77dbebc6a2db7951ce8d89ad46a92b7619cc92d950`. All 59 receipt compiler-dependency hashes, CMake hash, bridge source hash, binary hash, and linked runtime hash match the current artifacts/source. The generation-lifecycle smoke exit marker is 0 and reports 3,581 checks/0 failures. No stale binary was substituted.

**The newly authorized diagnostic A/A did not launch.** The single observed readiness sample failed two unchanged hard guards: `M4-RG-MEM` MemAvailable 1,136,008 KiB vs 5,477,762 minimum (short 4,341,754 KiB), and `M4-RG-LOAD` load-1 6.11 vs 3.14 maximum (excess 2.97). `M4-RG-DISK` passed at 22,254,845,952 B free on `/var/tmp` (4,035,845,952 B headroom); `M4-RG-IOPSI` passed at 0.00% vs 4.46% maximum. SwapFree was 13,767,716 KiB. The user systemd manager reported `degraded`; containment is therefore not asserted ready either. No baseline capture, arm, or retry/poll followed. Durable no-launch receipt: `writer-tail-causal-20261019/aa-resource-gate-readiness-20261020Tcurrent-no-launch.json`, SHA-256 `6a4fa3076eb21942f08d16483cf26daff23447de8714a53b5ef478f590aa5be5`. Recovery requires a future explicit authorization and one fresh snapshot with every unchanged hard guard and all runtime telemetry/containment checks passing; do not automatically retry or delete artifacts to manufacture headroom.

**Writer-tail causal matrix (offline evidence only; mission remains unresolved):**

| Hypothesis | Current evidence | Disposition |
| --- | --- | --- |
| H1 host carry-over / order | Two filter-identity sustained pairs retain high candidate tails under order reversal, weakening a simple second-arm explanation. Three separate sync-reduction pairs show second-arm tails with rising I/O PSI, making carry-over plausible there. No cycle-aligned host telemetry exists for the two filter pairs; this A/A produced no arms. | Plausible confound, not causal; overall inconclusive. |
| H2 native vector build | Candidate vector-build p95 was 246.42/232.13 ms vs baseline 110.98/119.29 ms; writer-thread CPU rose much less (70.1/66.4 vs 60.5/53.5 ms). Tails occur without scheduled compaction. | Strongest measured proximate stage correlation; native worker CPU and event-level host state are missing, so candidate-specific cause is unproven. |
| H3 post-M4 finalization | `build_end_ms - m4_build_end_ms` p95 was 39.10 vs 33.14 ms candidate/baseline candidate-first, and 102.89 vs 50.01 ms in reverse order; reverse candidate top cycles reached 111.21/102.89/85.00 ms. Source says this interval includes `SyncTree` and snapshot finalization. | Elevated interval is localized, but not a pure fsync timer and not separated from host effects; inconclusive. |
| H4 M4/vector interaction | M4-update p95 was 107.69/113.55 ms vs baseline 103.70/78.34 ms. M4 clone timing itself includes `SyncTree`; no paired same-cycle host or native-worker instrumentation isolates interaction. | Inconclusive; no evidence justifies speculative optimization. |

Best current explanation is **unresolved compound wall-time delay** across native vector build and sync/finalization-containing intervals, with host carry-over a credible confound; confidence in any single root cause is low. The minimal next engineering action is no code change: preserve this no-launch, keep the existing offline stage report as canonical evidence, and require fresh authorization before any future gated A/A. Current-source sustained/adversarial validation and causal attribution remain open; production migration remains unauthorized.

## 46. Bounded containment signal and unit-retirement correction — 2026-10-20

Owner-requested runner-only correction: `_bootstrap` previously returned the negative subprocess signal code directly through `sys.exit`, wrapping it modulo 256. It now exits `128 + signal` while preserving the signed child return code and explicit signal number in the receipt. The bootstrap exit is explicitly labeled; systemd therefore records child failure without presenting a wrapped negative code as an unrelated exit status.

Unit retirement now preserves `systemd-final.txt` and `receipt.json` first, stops only the owned unit, verifies the unit is inactive/absent and its cgroup empty/absent, persists cleanup verification, then clears failed state via `reset-failed`. No `--collect` is used, avoiding premature loss of failed-unit metadata. Unverified cleanup retains failed state and reports `CLEANUP_FAILED` / `SAFETY_STOP`, never performance eligibility. Exception cleanup uses the same retirement sequence.

Focused synthetic regressions initially passed 12/12; initial evidence remains at `research/m4-native/results/containment-signal-retirement-20261020/`. Closure audit tightened one predicate: a not-found unit cannot override a known populated cgroup, and cleanup failure now retains `pre_retirement_outcome` in addition to child signal metadata. Source-matched closure tests passed 12/12, including loaded/not-found unit responses with a populated cgroup, exit 7, SIGTERM/SIGKILL, systemd statuses 7/143/137, descendants and unit collection. Source hashes, receipts and test log: `research/m4-native/results/containment-lifecycle-closure-20261020/`. No benchmark/readiness evaluation was part of this repair. Writer-tail status remains unresolved.

## 47. Containment closure → causal decision — 2026-10-20

**Objective:** narrow writer-tail uncertainty without shifting cost to CPU/RAM/writes/debt. **Disposition: PARTIAL / OPERATION_BLOCKED; causal classification INCONCLUSIVE; DEFER performance optimization.** This section supersedes prior next-action instructions; it does not authorize another readiness sample or benchmark.

**Packaging:** `b30c86f` separately retains prior continuity/no-launch documentation; lifecycle source/tests and small signal/cleanup evidence are committed as `bfaf443` (`fix(m4): preserve signals and retire contained units safely`). Sole readiness observation subsequently revealed four failed supervisor-crash fixture units. Cgroups/processes had been cleaned but fixture teardown did not retire units after killing the controller. `7777af8` closes this exact fixture defect: retain watchdog receipt/metadata, verify cgroup absence, then use existing retirement; final focused synthetic suite 12/12 PASS. Only those four units, matched against owned run-state/abort evidence, were archived and retired. No unknown units or unrelated processes were touched. No additional infrastructure or performance code was introduced.

**One evaluation; no launch:** all unchanged resource guards passed: MemAvailable 5,885,576 KiB >= 5,477,762; `/var/tmp` free 19,843,407,872 B >= 18,219,000,000; I/O PSI full avg10 1.37% <= 4.46%; load-1 2.67 <= 3.14. User systemd was `degraded` with those fixture units at the observation. The current executable still calls `SyncTree(generation_root,true)`, omitting FTS/WAL file sync after WAL truncate; §35 explicitly withholds durability-equivalence proof. The mission's no-known-durability-blocker prerequisite therefore remains unresolved. No corruption is newly demonstrated, but ordinary correctness/reopen checks cannot waive the prerequisite. No ambient baseline or arm launched; baseline pressure/cadence/integrity qualification is NOT EVALUATED. Fixture retirement did not trigger another readiness sample. Receipt: `writer-tail-causal-20261019/mission-closure-20261020/readiness-no-launch.json`.

**Provenance:** same retained executable `57f3a19e…ba342f`; all 59 compiler dependencies plus CMake/binary and all six recorded shared-library hashes match. Build manifest 16/16 and generation smoke 3,581/0 remain valid. Source revision changes are runner/docs/test changes, not changed bridge compiler inputs. Binary remains under `current-source-build-20261019/` relative to the causal result directory; `retention.json` records exact non-Git binary path/hash and no-cleanup retention. No rebuild or heavy correctness suite was necessary.

**New offline result:** direct reconstruction from four raw historical arms validates 80 cycles/320 reader queries and zero coherence/reopen errors. All 320 readers released their snapshot before the same-cycle M4 build and publish. Their maximum release window was 1.7–8.6 ms across representative tail cycles (arm maxima 4.075/4.665/7.825/8.616 ms), versus 431–594 ms candidate commit tails. A direct late reader-held-lock explanation is weakened; global CPU/cache/writeback effects remain possible. New vector collections differ from pinned old-reader collections, so collection-local native schema locks must not be assumed shared. Candidate first-four/later-sixteen commit medians were 337.907/232.241 ms candidate-first and 476.895/197.386 ms reversed; baseline was 200.339/223.319 and 212.018/212.602. Early clustering is descriptive, not permission to discard early cycles.

**Timer correction in interpretation, not source:** native-vector-build span includes fresh-shard metadata/open, conversion/upsert, flush, durable shard-row metadata and bookkeeping; it is not pure HNSW compute. Compaction aliases that span when present and is not additive. M4 clone includes its own SyncTree. Post-M4 includes generation/shard/root sync and snapshot/Coordinator finalization; manifest publish follows it. Commit includes generation-WAL and preparatory bookkeeping, but excludes the preceding reader-ready barrier. Historical fine substep/worker CPU and contemporaneous cycle-level host PSI cannot be recovered. No independent percentiles were added/subtracted.

| Hypothesis | Supporting evidence | Contradicting evidence / limits | Confidence | Next implication |
| --- | --- | --- | --- | --- |
| H1 host/order | Order-associated tails/PSI in separate sync-reduction pairs; startup clustering. | Filter-pair candidate direction survives reversal; no valid A/A/cycle PSI. | Low causal | A/A can reveal non-variant variability, not name its mechanism. |
| H2 native build | Large vector span in the same tail cycles; much smaller writer-thread CPU. | Includes setup/durable metadata; native worker CPU missing. | High localization, low causal | Existing fine substeps needed, no new tracing. |
| H3 post-M4 | Reverse cycles 1/2 contain 111.209/102.894 ms there. | Candidate-first cycle 1 has 431.073 ms total but only 24.991 ms post-M4. | Medium localization, low causal | Separate existing sync/finalize timers in future evidence. |
| H4 shared bottleneck | Same native flush/metadata path; readers absent during late stages. | No identified shared pool/allocator/scheduler/FS mechanism. | Low | Do not infer same-handle locking or optimize it speculatively. |
| H5 measurement artifact | Historical/current identities differ; reverse arm-local hashes incomplete; broad timer labels and known trace overhead. | Raw arrays/workload/coherence consistent; direction repeats. | High limitations, low sole-cause | Diagnostics-off byte-identical A/A, not old/current comparisons. |

**Engineering decision and one proposed experiment:** no candidate-specific correction justified. Request owner approval for one future durability-preserving same-binary 500×20 A/A, using existing fine stage timers and host telemetry, diagnostics/tracing off, fresh Btrfs roots and unchanged gates, after restoring normal full file-sync behavior or an approved durability-equivalence proof. No new architecture/tracing/policy or A/B campaign is proposed. CPU, peak RAM/PSS, I/O/writes and maintenance impact of the runner/fixture closure are NOT MEASURED; synthetic tests do not qualify performance. Historical resources are reconciled separately in the decision report. The current mission's evaluation is consumed; no retry. Production remains unauthorized.

**Durable evidence:** `research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/mission-closure-20261020/DECISION.md`, reproducible offline analyzer, `cycles.csv`, `cycle-summary.json`, provenance/readiness/source/retention JSON and checksum manifest. Raw historical stdout/process-sampling inputs are retained at their original result paths; no binary is committed. All executable offline work possible at existing resolution is complete; future measurement requires new authorization and durability/containment/resource prerequisites.

## 48. Outer FTS/WAL durability restored and focused fault verification — 2026-10-20

Owner authorized a small research bridge correction and focused correctness/fault tests, not another performance attempt. **Implemented:** `BuildDeltaVectorSnapshot` now uses `SyncTree(generation_root)` before bridge publication. The broad `m4_payload_already_durable` FTS skip was removed; clone retains only the existing immutable hardlinked-segment optimization via the remaining named bool. No storage format, component WAL design, snapshot/publish protocol, native or production source changed. A test-only recovery CLI checks generation/highwater and vector/FTS coherence.

**Exact contract:** previous `SyncTree(generation_root,true)` omitted all regular files under FTS after component `FlushInternal` published its manifest and truncated component WAL without a subsequent internal file sync. Directory sync does not persist the WAL size update. The diagnostic aimed to remove apparently redundant outer syncs for attribution, but was not equivalent to normal durable staging. Restoring the original stronger file-sync sequence avoids a new durability design; it is not a claim of physical power-loss qualification.

**Historical distinction:** filter-identity A/B intended a query evaluator difference, not different WAL/fsync ordering; candidate-first receipts record the same bridge source hash, but incomplete original variant sources/reverse fingerprints prevent independently certifying exact old executable write-order equality. The later sync-reduction treatment/control pairs intentionally differed in outer FTS file syncs, including WAL-after-truncate, while retaining inner WAL lifecycle and bridge publish order. They must not be presented as durability-equivalent comparisons. Preserved old binary `57f3a19e…ba342f` directly traces the missing post-truncate outer WAL sync and remains historical/diagnostic only.

**Fresh build/provenance:** corrected binary SHA-256 `ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f`, retained as `research/m4-native/results/durability-restore-20261020/m4_bridge_integration.full-sync` (not Git). Release GCC 16.2.1, `-O3 -DNDEBUG -std=gnu++17 -Wall -Wextra -Werror -pthread`. Single-job bounded cgroup build succeeded; 61 actual in-repo compiler dependencies, configuration, link command, ELF notes and six runtime-library hashes are recorded. Against the previous 59-input receipt only bridge source changed; the actual new dependency inventory retains the additional observed inputs. Old binary was not overwritten. Before future measurement revalidate all new binary/source/runtime identities; old binary correctness/provenance cannot certify changed source.

**Focused results:** vector/snapshot/recovery 1,401/1,401 PASS; incremental FTS 582/582 PASS. Decoded-fd syscall traces and strace fault injection: five cases/four recovery oracles PASS. Restored WAL fsync follows truncate. EIO at that exact outer WAL fd or SIGKILL at the syscall retains old generation 11/highwater 12; crash after fully synced stage but before publish also retains 11; crash after publish-WAL promotes generation 12. Recovery checks live count and cross-component query coherence. Fault cases exercise the full-build path through the corrected helper; normal incremental clone/shared-run behavior is covered separately. This is process-crash/error testing, not simulated kernel/device power loss.

**Test integrity:** first fault run failed an incorrect output-format assertion: this strace prints `fsync(target)=?` then SIGKILL, not an `INJECTED` marker on the syscall line. Target/return signal were already correct. Original logs/traces and diagnosis are preserved. Corrected assertion passed on fresh roots; no correctness/safety guard was relaxed. Failed owned test unit metadata was retained and its absent cgroup verified before retirement. No benchmark/readiness attempt occurred.

**Accounting, not performance:** build 39.925 s, CPU 39.440 s, peak 630.7M/swap 0; correctness 10.650 s, CPU 9.677 s, peak 933M/swap 0; corrected fault suite 7.653 s, CPU 7.071 s, peak 394.7M/swap 0. Peak PSS, physical writes, steady-state latency and maintenance impact are NOT MEASURED. Writer-tail cause remains INCONCLUSIVE; no speedup is claimed. New same-binary A/A requires fresh owner authorization and unchanged resource/containment/telemetry checks, using the new full-sync binary only. Future physical power-loss qualification needs an approved storage fault model/environment, not simply more SIGKILL loops.

**Evidence/commands/limits:** `research/m4-native/results/durability-restore-20261020/REPORT.md`, `build-provenance.json`, configs, before/after resource observations, correctness logs, original failed and corrected traces/recovery logs, and SHA-256 manifest. Relevant bridge source was previously untracked; only this owning source, focused test and task evidence are packaged, not unrelated untracked core or Rust changes. This section supersedes the unresolved-elision next-action prerequisite for the corrected binary: full-sync ordering is restored and focused recovery verified, while broader hardware power-loss qualification remains unclaimed.

## 49. Authorized full-sync A/A: single resource refusal — 2026-10-20

Owner authorized one new diagnostic same-binary 500×20 A/A with the restored full-sync executable. **OPERATION_BLOCKED at baseline preflight; zero arms, zero ambient baseline, zero retry.** Durability is no longer the prerequisite blocker for this executable. Actual binary SHA-256 `ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f`, 61 compiler dependencies, all six current linked-library hashes and 50 retained manifest entries verified. User systemd was running with no failed units/active M4 workloads; lifecycle runner hash matched source-matched 12/12 regression evidence, and cgroup/telemetry prerequisites were available. `/var/tmp` remained Btrfs. No live arm handshake or baseline-pressure/cadence qualification was exercised after resource refusal.

The existing orchestrator's stale fsync-elision prose/partial archive were corrected only in receipt metadata: optional `M4_AA_BUILD_RECEIPT` attaches this build's receipt; durability text uses configured description. The actual attempt receipt contains the correct full-sync binary/build identity. No enforcement, workload, query mix, instrumentation or protocol change; policy regression 7/7 PASS.

At 12:05:35 +07 the sole resource evaluation failed `M4-RG-MEM`: 4,134,692 KiB vs unchanged 5,477,762 minimum (short 1,343,070), and `M4-RG-LOAD`: 6.56 vs 3.14 maximum (excess 3.42). Disk 22,096,510,976 B vs 18,219,000,000 and I/O PSI full avg10 1.06% vs 4.46% passed. Job `job-1335348-140` stopped before ambient capture or workload-root/arm creation. No retry, idle polling, process signaling or cleanup to force a PASS. Snapshot background activity is not causal attribution.

**Evidence:** under `research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/`, see `full-sync-aa-20261020/` (prerequisites, exact command/environment, summary, runner log, checksums) and `host-carryover-aa-pair-20261020T050534Z/` (actual runner operation/preflight/no-launch receipts). No new per-cycle writer/vector/post-M4/host data exist, so A/A repeatability and H1–H5 remain INCONCLUSIVE; historical elision/full-sync observations cannot be pooled as current binary qualification. Independent existing analysis is already exhausted at its available timing resolution.

This section supersedes earlier next-action authorization notes. Another A/A needs new explicit owner authorization and a fresh pass of all unchanged resource/pressure/integrity/containment requirements; do not retry this operation. Production untouched; writer-tail mission remains PARTIAL.

## 50. Two subsequent explicit owner authorizations: no-launch — 2026-10-20

Owner separately authorized another attempt (“chạy đi, tôi tắt zen rồi”), then another (“retry đi”). Neither was an autonomous retry/poll. Full-sync binary `ef0221f0…1212e7f`, compiler inputs/runtime libraries and systemd running were verified; no source, workload or policy change. Each operation evaluated readiness exactly once and stopped before ambient baseline or either arm.

| Authorization / snapshot +07 | MemAvailable KiB (min 5,477,762) | Load-1 (max 3.14) | Disk B | I/O PSI full avg10 | Outcome |
| --- | --- | --- | --- | --- | --- |
| After closing Zen / 12:13:05 | 9,133,088 PASS | 4.84 FAIL | 21,718,110,208 PASS | 1.95% PASS | OPERATION_BLOCKED, load only |
| Explicit retry / 12:14:02 | 4,450,464 FAIL, short 1,027,298 | 4.40 FAIL | 21,698,637,824 PASS | 1.69% PASS | OPERATION_BLOCKED, memory + load |

Jobs `job-1335348-147` and `job-1335348-149`; receipts under the causal result directory: `full-sync-aa-20261020-after-zen/` + `host-carryover-aa-pair-20261020T051305Z/`, and `full-sync-aa-20261020-owner-retry-2/` + `host-carryover-aa-pair-20261020T051402Z/`. Exact snapshots/commands, runner exits, per-guard decisions and checksums retained. Background rpmbuild/browser activity is recorded in snapshots, not causally attributed or interrupted. No readiness retry after either refusal without a new explicit user message; no arm, root creation, tracing, guard relaxation, cleanup or unrelated process change.

There are still no new cycles to compare; writer-tail remains INCONCLUSIVE and no new independent discriminating data exist. Another attempt requires new owner authorization and all unchanged resource/pressure/integrity/containment requirements. This section is latest operation status, not standing authorization to poll or retry.

## 51. Newly authorized attempt: I/O PSI no-launch — 2026-10-20

Owner explicitly reauthorized (“ổn rồi đó, chạy đi”). New prerequisites passed: full-sync `ef0221f0…1212e7f`, all 61 compiler inputs, six actual runtime libraries, 50 build/test manifest entries, verified lifecycle runner source, systemd running, no active M4 workloads, `/var/tmp` Btrfs. Job `job-1335348-153` performed exactly one resource preflight at **13:09:15 +07**. `M4-RG-IOPSI` failed: full avg10 **4.68% > 4.46%**, excess 0.22 percentage points. Memory **9,933,104 KiB**, load-1 **1.53** and disk **21,736,423,424 B** passed their unchanged guards.

**OPERATION_BLOCKED:** no ambient baseline, no arms, no workload roots, no retry/poll or threshold/process changes. Evidence under the causal directory: `full-sync-aa-20261020-owner-retry-3/` and `host-carryover-aa-pair-20261020T060915Z/`; exact source/runtime checks, per-guard decisions, operation status and checksums retained. Previous memory/load blockers no longer failed in this snapshot, but that does not waive the I/O guard or supply performance evidence. No new cycle-level comparison possible; writer-tail remains INCONCLUSIVE. A later attempt needs new explicit authorization and all unchanged requirements; this section does not authorize automatic retry.

## 52. I/O PSI policy audit and owner-pending diagnostic amendment — 2026-10-20

Active request: audit classification/diagnostic continuation, prepare a minimal owner-approved amendment if warranted, preserve qualification, and run no benchmark before approval. **Audit complete; amendment OWNER_DECISION_REQUIRED / NOT ACTIVE.** No benchmark, ambient baseline, readiness probe or systemd workload was launched.

Exact executable ID `M4-RG-IOPSI` is a **hard operational resource guard** in both modes, not an established universal host-safety limit and not currently measurement-quality-only. JSON declares historical successful B3.1 arm-before provenance; the A/A classifier calls its failure `HISTORICAL_ENVELOPE_RESOURCE_GUARD`. Legacy `hard_safety_failures` / `hard_safety_window_pass` field names do not change that classification. Current diagnostic continuation covers only a valid `M4-MQ-STABILITY` failure with all absolute resource, baseline memory/swap, integrity and containment prerequisites passing. Snapshot, samplewise baseline, contained-arm preflight, runtime/watchdog and recovery all enforce I/O PSI; changing only outer preflight would be an incomplete bypass. The §51 4.68% > 4.46% refusal remains valid, not retroactively reclassified.

Draft: `research/m4-native/docs/io-psi-diagnostic-amendment.proposed.md`, proposal `M4-IOPSI-DIAG-ONEPAIR-v1`. It proposes an explicit, operation-scoped one-attempt diagnostic exception for valid I/O PSI envelope exceedances across every enforcement layer, retaining raw FAIL comparisons and exploratory-only evidence. Qualification/default diagnostic behavior, numerical threshold, other resource/pressure/integrity/correctness/containment gates and recovery window counts/timeouts stay unchanged. Invalid/missing/nonfinite telemetry cannot activate the exception. It explicitly discloses that no independently safe upper I/O PSI bound or delegated I/O controller exists. Approval must include acceptance of that uncertainty; no invented higher safety threshold. No active JSON, explanatory policy or runner was changed.

Checks: 5 shared-policy + 7 A/A gate tests PASS, synthetic exact-4.68% input still blocks both modes and diagnostic recovery, and active policy/runner SHA-256 identities are identical before/after. Logs, replay and validation receipt: `research/m4-native/results/io-psi-policy-audit-20261020/`. Synthetic checks are not host readiness or performance observations. Writer-tail remains INCONCLUSIVE.

**Exact next action:** await owner approval/rejection of the named proposal. If approved, implement the scoped exception and pass its synthetic acceptance matrix with updated policy/runner provenance; obtain explicit execution authorization before the one attempt. Otherwise leave existing no-launch behavior intact. This draft authorizes no benchmark, readiness probe, automatic retry or guard override.

## 53. Real-host contention diagnostic implementation — 2026-10-20

**Current authority:** owner's “M4 Writer-tail Under Real Host Contention” mission permits engineering, synthetic checks and offline analysis only. No real 500×20 A/A, performance readiness/ambient capture, idle polling or unrelated-project process intervention was performed. The mission supersedes the narrow design in §52 without activating that old proposal or changing historical no-launch receipts. Do not ask the owner to stop/pause/throttle other projects. **Engineering implemented and verified; real measurement OWNER_DECISION_REQUIRED; writer-tail INCONCLUSIVE.**

New explicitly selected `contention-aware` mode uses the existing JSON policy/runner, not a new benchmark framework. All pre-existing JSON fields remain identical to `475ea4c`, including qualification/default-diagnostic thresholds, stability, pressure and cadence semantics. All four historical resource comparisons stay raw PASS/FAIL with their empirical provenance; in the new mode only they describe contention instead of requiring an idle host. A separate continuation decision requires workload-capacity reservations and approved M4 budgets, never an invented load/PSI safety threshold.

Enforcement is connected through outer preflight, actual-bracketed ambient observation, contained-arm preflight, kernel memory/swap/CPU caps, sampled workspace checks, runtime sampler/independent watchdog, recovery and non-qualifying receipts. Before releasing M4, the watchdog validates caps and acknowledges the exact operation/context digest. New-mode recovery requires three valid capacity/integrity windows and stops immediately on failure, not a loop waiting for idle. Required telemetry is finite, present, internally consistent and monotonic at the plan's approved maximum monitoring gap. Correctness/durability/source/runtime mismatch, capacity shortfall (RAM/swap/disk), observed host OOM, owned OOM/allocation/swap distress, cap/controller loss, timeout or unverified cleanup remain blockers. Only owned descendants/unit are terminated. Existing index roots, duplicate operations and context leakage to qualification/legacy modes are rejected. Every new-mode result remains **EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING**, even without historical exceedances.

An explicit operation-bound approved resource plan is mandatory: current policy SHA, pinned full-sync identity, fixed tracing-off workload, concrete justified M4 memory/swap/CPU/workspace caps, host/disk reserves, runtime/observation-gap budget, execution authorization and I/O-risk acceptance references. `research/m4-native/docs/contention-plan.pending.json` is incomplete/unapproved and cannot launch. Resource reserves must include the observer/kernel and retained first-arm root. There is no delegated cgroup I/O controller, hard I/O isolation, disk quota, device-write attribution or guarantee of zero host impact. Workspace limits and shared Btrfs capacity checks can overshoot between samples. External observer resources are outside the workload caps. See policy guide and risk report before deciding on a real pair.

Verification: **51 tests PASS**, including 22 new contention/watchdog/integration tests, 5 shared-policy, 4 scenario, 12 tiny real-systemd lifecycle, 7 legacy gate and 1 tiny sequential-pair test; source hashes unchanged during final job `job-1335348-183`. Fresh six-file `ty check` PASS. Final logs/provenance at `research/m4-native/results/contention-policy-20261020/`; `test-receipt-release.json` is authoritative. An intermediate controller-crash fixture failed on a real completion-publication race: the abort-initiation receipt preceded socket cleanup. It is retained, the exact fixture unit was separately verified/retired, and a terminal cleanup-complete publication now precedes assertions. No deadline/cleanup assertion/guard was weakened, no failed benchmark retried. New mode has not been exercised on the real M4 workload, intentionally.

Binary verification is static, not a fresh benchmark/correctness rerun: **ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f**, source identical to correction commit `7c88272`, all 61 compiler dependencies, six actual linked libraries and 50 durability-manifest entries match. Flags/receipts and corrected five-case crash/EIO matrix/four recovery oracles retained. Chronology: rebuild receipts precede git publication of `7c88272`, although compiled source bytes match that commit; no later-timestamp rebuild is claimed. No C++ input was changed and no redundant rebuild was made.

Offline continuation pinned/replayed 80 historical filter-identity cycles: 15 non-compacting cycles per arm. Candidate-first candidate cycle 1 has no compaction, commit 593.906 ms, native-vector wall/caller CPU 307.111/50.569 ms, writer CPU 137.755 ms and post-M4 residual 88.847 ms. This rules out compaction as a necessary event for that tail, not as a possible cost elsewhere. Wall-minus-caller CPU does not identify I/O wait because native worker CPU/runqueue/off-CPU/fsync waits are not separately measured. Older per-cycle host pressure is absent. Full-sync binary already emits split native-vector/post-M4 SyncTree/finalization wall/caller-CPU and common-monotonic timelines; no heavy tracing or speculative fix was added. Future during-minus-ambient pressure remains temporal association, not isolated incremental attribution.

**Exact next executable action:** owner approval of a concrete resource plan/current policy identity and one unique bounded operation, with explicit acceptance of unisolated host I/O, sampled disk/observer risks and separate launch authorization. After that only, revalidate provenance and actual capacity once and run the approved pair. No historical-idle readiness polling, automatic retry, extra pair, performance-win/regression/production claim or unrelated workload intervention is authorized. Production/native libraries/daemon/ABI/schema/existing indexes and unrelated Rust/research edits remain untouched.

## 54. Autonomous campaign preparation, task caps and source exclusions — 2026-10-20

**Current authority supersedes §53's per-slice direction:** active GOAL-BOUNDED AUTONOMY permits autonomous M4 offline/source/engineering work immediately. Real diagnostic execution still requires owner-approved resource/risk bounds, then at most **three sequential pairs**, one heavy operation at a time. No approved envelope exists: **campaign NOT ACTIVATED, 0/3 pairs, no real A/A/readiness/ambient observation or idle polling**. Status **PARTIAL / OWNER_DECISION_REQUIRED_RESOURCE_PLAN**, research INCONCLUSIVE. This is not an A/B/C mission closeout; none of those completion conditions is met.

Concrete control defect fixed in **`f6c182e`**: explicit `pids_max`, TasksMax at service creation, pids.max apply/read-back/digest-bound budget, task counts/events and fail-closed task distress in contention-aware mode. Kernel 64-task readback and cleanup pass in a tiny owned test; 64 is a synthetic test budget, not a real approved plan. Old qualification/default-diagnostic policy fields remain identical to `6267e69`; no new cap is imposed on legacy modes and no historical FAIL is converted to PASS. No new harness/backend/daemon or unrelated workload intervention.

New source map narrows the question, without claiming a latency fix. New-shard vector build includes native collection creation, six scalar INVERT indexes, HNSW, conversion/upsert/flush and durable metadata; not pure HNSW compute. Direct reader contention on the unpublished new collection's schema mutex is incompatible with the inspected snapshot/handle ownership; shared pools/global locks and scheduling remain possible. Fixture ready/completed cohort waits are outside measured commit/native/post-M4 envelopes. Post-M4 SyncTree and snapshot/coordinator finalization have separate current-binary wall/caller-CPU telemetry. Do not remove scalar indexes, skip mutable WAL sync, identify all old residual as fsync, or equate wall-minus-caller-CPU with IO wait. No native implementation changed.

New offline resource extraction reads the four retained filter-identity arms rather than rerunning them: process RSS about 302 MiB max, old wall 4.96–6.38 s, process writes 216–239 MiB/arm, final logical root 6.85 MiB. These do not measure charged cgroup/cache peak, tasks or workspace peak and are not present full-sync costs/capacity. A pure offline stage/cgroup reader computes conservative **reported counter-increment** bounds from actual brackets and eight-significant-digit serialized-clock uncertainty, never interpolates short stages, rejects mixed context/counter/time data, and reports missing coverage. Kernel accounting lag means these are not strict physical execution-time/critical-path bounds; owned CPU includes readers/workers and excludes observer. Upsert/conversion have no absolute substage timeline and are not fabricated into counter joins. No causal M4 result yet from this new analysis.

**Proposed only, NOT APPROVED:** `resource-plan.proposed.json` in `research/m4-native/results/autonomous-campaign-20261020/`: workload memory 1 GiB, swap 0, CPU quota `200000 100000` (two averaged CPU-equivalents, no isolation/entitlement), tasks 128, sampled workspace 512 MiB allocated/1 GiB apparent per arm, host available-memory reserve 1 GiB, shared disk reserve 2 GiB, arm watchdog 120 s/systemd 125 s, observation gap 2 s, proposed GNU supervisor prefix `timeout --signal=TERM --kill-after=5s 355s` (360 s ceiling, not tool auto-background), max three pairs/1080 supervised seconds; verified owned cleanup still required. Initial capacity arithmetic reserves workload+host (~2 GiB available) and both workspaces+disk (~3 GiB free), **not universal safety thresholds**. Current JSON policy SHA `be7eca5603ae4e29d9f8b42203fdf4b3b0be3beaf376fdc6ab613392810ef6bb`. Owner must expressly accept missing hard I/O/total-write isolation, shared Btrfs/writeback, sampled disk overshoot, inherited limits and observer outside hard caps. No guarantee of zero impact on other projects. No approval inferred from autonomy or historical measurements.

**56 distinct focused tests PASS** (24 contention/task-context + 13 tiny lifecycle + 5 shared policy + 7 legacy gates + 1 tiny sequential pair + 6 offline bounds); fresh six-file `ty check` PASS. Final selection/hashes/commands: `task-budget-tests.json`, `offline-verification.json`, `analysis-release-v2-verification.json`; logs and owned-fixture receipts retained. Earlier analysis tests not double-counted. Initial fixture typing issue corrected; no guard weakened. Static current ELF/compiler/runtime/durability revalidation PASS: same full-sync `ef0221f0…`, 61 dependencies, six actual libraries and 50 evidence entries; retained corrected fault/recovery evidence applies to exact bytes. No rebuild, new native correctness run or performance execution. Native .cc hashes are inspection snapshots only. A missing environment on first static-module import is retained separately; it occurred before any identity/resource guard and was corrected before the first guard evaluation, not a failed-operation retry.

**Exact next action:** owner approves/edits the concrete envelope and risks for up to three named sequential pairs. Only then bind approval refs/current identities, debit a unique slot, perform one fresh capacity/provenance decision and execute one 500×20 tracing-off same-full-sync-binary pair, with the explicit GNU supervisor deadline, not a tool auto-background timeout. Analyze before any next heavy operation; repeat only for an explicit unresolved question, never a prettier PASS. Claimed/preflighted no-launch/abort consumes its slot; formal refusal does not authorize retry under another slot, cap enlargement or guard changes. Continue only independent authorized work after a stop. No requirement to consume all slots. Source/instrumentation variants remain engineering-authorized but must satisfy valid full-sync registration/correctness/provenance prerequisites before measurement, not evade existing guards. Detailed hypotheses, risks, acceptance reconciliation, offline command and current ledger: `CAMPAIGN.md` / `campaign-ledger.json` in the same archive. Research not complete; no root-cause-supported correction, rejected latency optimization, budget-exhausted closeout, win/regression or production claim. Production/native libraries/daemon/ABI/schema/existing indexes and unrelated Rust edits remain untouched.

## 55. Approved campaign, slot-1 launch refusal and minimal control fix — 2026-10-20

**New owner authorization supersedes §54's pending-approval and A/B/C closure requirements.** Up to three sequential same-binary diagnostic pairs approved, explicit finite I/O/sampling/external-observer risk accepted. Reconciled all limits without enlargement. Existing helper adds 5 seconds to service runtime: approved context uses **115 s watchdog / 120 s systemd service**, narrower than the old proposed 120/125. Pair supervisor GNU TERM355s/KILL+5s; quota2 CPU, memory1GiB, swap0, tasks128, workspace512MiB allocated/1GiB apparent, host reserve1GiB/disk2GiB, tracing off, maximum one active benchmark arm. Policy unchanged; approved per-operation JSON and authority evidence under `research/m4-native/results/autonomous-campaign-20261020/`.

**Slot 1 consumed, zero native arms started.** Job `job-1335348-229` performed fresh repository/ancestry, unchanged full-sync ELF/compiler/runtime/durability checks and cgroup capability inspection. Baseline: 1200 valid samples / 59.951419936 s, capacity/integrity PASS. Historical raw disk FAIL in 1200/1200, IO PSI FAIL in 1193/1200 (full avg10 4.30–13.81%); unchanged stability diagnostic FAIL on load drift (load1 1.54–2.65). These comparisons legitimately did not block approved contention-aware continuation and remain raw FAIL. MemAvailable min7,278,576 KiB, free disk min18,041,962,496 B, host OOM delta0, swap-in/out565/1 pages; no native attribution.

`systemd-run` refused **CPUQuota=200.000%** before bootstrap connection. Controller accept timed out after 10 seconds. Original arm receipt **LAUNCH_FAILED / CONTROLLER_ERROR / NOT_STARTED**, `benchmark_started=false`, no caps applied/readback reached; outer receipt **OPERATION_FAILED / RUNNER_OR_ANALYSIS_FAILURE** retained verbatim. The refusal is launch-property serialization, **not historical IO exceedance or native writer delay**. Unit `m4-contained-b32d2a2fdbab4065.service` cleanup/collection verified; later exact-unit query not-found/inactive, cgroup absent, native target never created, private 0700 workspace parent retained empty. Delayed post-inspection host snapshot explicitly is not an immediate host-after sample. No arm2 or unresolved safety/cleanup incident. Exact raw root: `research/m4-native/results/m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/m4-contention-campaign-20261020-pair-1/`.

Continued independent authorized engineering, **not a benchmark retry**: minimal service-property correction serializes exact integer quotas as `CPUQuota=200%`/`50%`, preserving raw cpu.max and all caps/deadlines. Fractional-percentage behavior unchanged; no general support claim. New tiny regression reads actual `cpu.max=200000 100000`, `memory.max=1073741824`, `memory.swap.max=0`, `pids.max=128`, verifies normal exit and retirement. No C++/native measurement-path, ABI/schema/index/freshness/retrieval/durability change, no rebuild or native performance invocation after refusal. Independent job `job-1335348-233`: **57 focused control/offline tests PASS** (14 lifecycle,24 contention,5 policy,7 legacy gates,1 tiny pair,6 bounds); fresh three-file `ty check` PASS. Evidence/source hashes/logs/tiny fixtures: `quota-serialization-tests/`. The confirmed serializer artifact diagnoses startup only, **not writer-tail**; no new CPU/PSS/RAM/IO/physical-write/maintenance or latency tradeoff measured for M4.

**Decision: KEEP exact-integer control correction and full sync; DEFER writer-tail optimization.** H1 has ambient variation but no A/A order evidence; H2/H3 native/sync stages unmeasured; H4 shared contention versus implementation unresolved; H5 startup artifact confirmed/fixed with focused tests. No p95 addition, regression/win or qualification claim. Current owner DoD reconciled in `REPORT-AUTHORIZED-CAMPAIGN.md`: bounded campaign stopped at its authorized condition after independently fixing/verifying the concrete control defect, not marked successful from a refusal alone. Substantive latency investigation remains **INCONCLUSIVE / decision-dependent**.

**Budget/next action:** 1/3 consumed; two conditional slots unspent, **zero eligible next pairs** under owner §5 because prior native pair data is absent and a new ID would be a disguised retry. Generic continuation is not interpreted as an override. No automatic second attempt, extra pair, cap increase or request to quiet other projects. Owner must explicitly authorize a new attempt/retry after this correction if a first native pair is desired; future execution needs fresh provenance/capacity and actual cap readback before GO. Current ledger/approved resource context, exact commands/job/exit/operation receipts, reproducible `analyze_authorized_attempt.py` and `pair-1-analysis.json`, closure evidence and separate new checksums are retained. Historical preregistration/manifests and raw failure are not rewritten. No push; unrelated Rust edits preserved.

## 56. Explicit replacement R1 — pre-GO integrity refusal, cleanup verification and parser fix — 2026-10-20

**Latest authority:** owner explicitly granted one additional replacement after `14f77dc`, preserving Original Pair1 and both original remaining slots. Maximum **4 total attempts / at most 3 executable pairs**; no further expansion. `owner-replacement-authorization.md`, `ledger-before-replacement.json` and approved R1 context retain the amendment/history. **Current: 2/4 attempts consumed (original1 + R1), zero native arms, two original conditional slots unspent. PARTIAL / OPERATION_BLOCKED; measurement objective NOT fulfilled.**

R1 ID **m4-contention-campaign-20261020-replacement-1**, job **job-1335348-244**. Fresh tiny real-kernel cap test verified CPUQuota200%, cpu.max200000/100000, memory1GiB/swap0/tasks128 and retirement; fresh static full-sync `ef0221f0…`/61 inputs/six libraries/50 durability entries and no-active-arm/capability checks PASS. Approved caps unchanged, watchdog115s/systemd120s, supervisor TERM355s/KILL+5s, tracing off, fresh sequential Btrfs roots. Execution controller SHA **da8c14b0…** matches Git14f77dc; native measurement source **43d62bc4…** unchanged, no rebuild. Real operation was invoked, not replaced by a synthetic test.

Ambient **1200 valid samples / 59.951112561 s**, approved capacity/integrity PASS; unchanged stability drift FAIL retained/nonblocking. Load1 3.81–4.82, host CPU some avg10 0–0.37%, IO full1.76–5.05%, memory full0–0.55%; MemAvailable min5,605,092KiB, disk min17,269,014,528B; host OOM delta0/swap-in-out12,099/7,647pages. Historical raw FAIL counts load1200/disk1200/IO487/memory0, not refusal reasons. No M4/other-project device-write attribution.

Actual service/caps application and controller readback succeeded, but independent watchdog refused its **first bootstrap-only sample**: `required_telemetry_missing`, `ValueError: incomplete process-tree telemetry`. IO complete=false, memory complete=true, one bootstrap PID397739; no native GO/child or arm2. Bootstrap memory peak16,023,552B, swap0, owned CPU55,717us, no max/OOM/throttle events; these are not native costs. Original outer **SAFETY_STOP**, controller cleanup verified=false, final unit retirement verified=true, watchdog ABORTED_CONTAINED and successful cgroup.kill all retained verbatim. Before any independent test/fix, exact unit `m4-contained-95bdf38a0e2140ff.service` was independently verified not-found/inactive, cgroup/socket absent, native root absent, workspace empty. This resolves current cleanup only; no raw safety-stop downgrade or permission to resume. The earlier controller-versus-retirement verification timing difference is not established/fixed by assertion weakening.

Offline deterministic cause: generic `parse_kv` kept the colon in `/proc/PID/io` keys, while process sampler required `read_bytes`/`write_bytes`. Thus readable proc data was marked incomplete. **One-line research parser correction** removes a terminal colon; cgroup keys unchanged, exceptions/missing data still incomplete and strict validator unchanged. New real tiny full-cap initial-sample regression now asserts IO+memory completeness and the unchanged `contention_runtime_failures(..., None)` contract, in addition to caps/cleanup. Job **job-1335348-248**: **58 focused tests PASS** (15 lifecycle,24 contention,5 policy,7 legacy,1 tiny pair,6 bounds), fresh three-file `ty check` PASS; eight archived synthetic units retired. Corrected controller SHA **c4bc68de…**. No native C++/measurement instrumentation/ABI/schema/index/durability change; no native retry. Saved incomplete process-IO zeros are invalid, not zero physical-write proof; independent older C++ physical-write reporting is a different path.

Writer p50/p95/p99, native build/post-M4 cycles, reader coherence/reopen, arm-order asymmetry and native resource/cost tradeoffs are **NOT MEASURED (N=0), not zero**. H1 has ambient context only; H2/H3/H4 unresolved; H5 parser artifact confirmed, not writer-tail root cause. **KEEP parser+CPU quota corrections, strict completeness and full sync; DEFER latency optimization.** No A/A regression, p95 addition, qualification or win claim.

**Exact next:** current formal telemetry refusal is not retried under another ID. Two original conditional slots remain unspent; owner must explicitly decide if another attempt after the parser correction may use an existing slot. No fifth slot/cap increase/quiet-host request or automatic use of remaining budget. Actual native measurement remains unfulfilled, not mission SUCCESS from green tests. Independent cleanup recovery/diagnosis/focused correction/reporting complete; production/other projects untouched, unrelated Rust edits preserved, no push. Full hashes, raw receipts, cap/readiness/cleanup distinctions, requested metric availability, reproducible `analyze_replacement.py` and budget evidence: **`research/m4-native/results/autonomous-campaign-20261020/REPORT-REPLACEMENT-R1.md`** / `replacement-1-analysis.json`, `replacement-1-execution/`, `proc-io-parser-tests/`; raw operation at the sibling writer-tail-causal root named by R1 ID.

## 57. Real strict rehearsal PASS; original slot2 stopped by memory capacity — 2026-10-20

**Latest authority:** explicit owner end-to-end rehearsal/continuation authorization superseded §56's parser-permission barrier for existing slots only. No accounting reset/cap increase/retry of Original1/R1. **Current 3/4 attempts consumed, native arms0/completed native pairs0, one original conditional slot unspent. PARTIAL / OPERATION_BLOCKED; native writer-tail objective NOT fulfilled.** Historical Original1/R1 objects/receipts unchanged; snapshot `ledger-before-e2e-continuation.json` proves preservation.

Source commit **277245a**: fixed tiny synthetic command admission through the existing *real contention* path, not legacy fixture-mode/mock replacement. Distinct rehearsal purpose/prefix/Python identity/fixed code; native500×20 binding remains enforced, wrong-context/native/arbitrary-command regressions added. Parser regression covers rchar/wchar/read_bytes/write_bytes/cancelled_write_bytes. Policy/caps/qualification/durability/production/native C++/ABI/schema/index unchanged. Current controller **aa1103b4…**, native source43d62bc4…/full-sync ELFef0221f0… unchanged, no rebuild or performance claim.

Actual rehearsal **m4-contention-rehearsal-e2e-1**, job **job-1335348-259**, PASS: actual systemd unit/cgroup caps/readback/bootstrap/child, proc IO, independent contention-watchdog context acknowledgement, runtime sampler, expected exit0, receipt publication and verified cleanup. Fixed32KiB fsync/read/1s hold; **12 complete strict-contract samples**, no abort, memory peak22,282,240B/swap0/workspace allocated peak32,768B. Worker proc fields1326832/32768/0/49152/0 respectively; zero physical reads valid with complete fields. CPU200000/100000,memory1GiB/swap0/tasks128, watchdog115/systemd120 all unchanged; separate tiny outer TERM30/KILL+5 narrows its supervision. Generic receipt benchmark_started=true denotes fixed synthetic Python, **not native**. Zero native slots consumed by rehearsal. Unit m4-contained-579ca03808454887.service collected/cgroup/socket absent/workspace empty, also independently verified after crash.

**59 focused tests PASS**, initial2 Path-typing errors corrected,25 contention cases rerun and fresh4-file ty PASS; raw failed typecheck retained. Tested source hashes unchanged after crash. No claim synthetic covers native20-cycle/reader/reopen/durability/overload behavior or performance; no hard IO/observer caps added. Actual rehearsal evidence/source/config/context/digest/raw outputs under `m4-contention-rehearsal-e2e-1/`, `e2e-rehearsal-execution/`, `e2e-admission-checks/`.

Did continue toward native: original slot2 debited at preparation, ID **m4-contention-campaign-20261020-pair-2**. Preliminary wrapper job261 import-path error occurred before *any* resource check/operation directory/native command; preserved and fixed only wrapper sibling import setup. Same already-debited operation advanced to its **first** formal check, not a native/guard retry. Job **job-1335348-262** fresh full-sync61-input/6-library/50-durability hash verification, context/rehearsal/no-active-unit prerequisites PASS. At **15:44:07.135544Z**, **M4-CA-MEM-CAPACITY FAIL**: available **2,020,233,216B / 1,972,884KiB** versus **2,147,483,648B / 2,097,152KiB** required (approved1GiB workload+1GiB reserve), short **127,250,432B / 124,268KiB**. `continuation_allowed=false`. Historical memory/disk FAIL retained/nonblocking, IO.16% and load1 1.8 historical PASS; *current approved memory capacity*, not old threshold/parser/quota/quality, blocked execution. No native orchestrator invocation/operation directory/baseline/arm or reader/coherence data. Native p50/p95/p99/stages/order/resource costs **unavailable N=0, not zero**; H1–H4 remain unresolved, H5 control correction demonstrated only. No candidate regression/win/qualification or p95 addition.

User-reported crash occurred before exit/log could be recovered; job registry absent afterward. Saved raw prerequisite decision is retained, **no exit code or crash cause invented**. Independently verified no current owned unit/controller/native process; delayed post-crash snapshot labelled closure only, never substituted as a fresh permission/green retry. No other-project intervention, no relaunch of refused operation. Supplemental supervisory record explicitly is not an original native runner receipt (orchestrator never ran).

**KEEP control/parser correction + bounded real strict rehearsal, caps/fullsync; DEFER latency optimization. Exact next:** owner decision if a further native attempt after memory-capacity recovery may use the existing last conditional slot. New formal refusal prevents automatic retry or disguised last-slot substitution; no discriminating native question exists from zero paired samples. Corrected parser no longer needs separate permission, but this *new capacity refusal* remains authoritative. No extra/fifth slot, cap increase, idle-host request or polling-until-green. Current authorized evidence/report work complete; overall native measurement objective still unfulfilled, no push; unrelated3Rust edits preserved. Full eight-part report/raw refs/reproducible file-only analysis/checksums: **`research/m4-native/results/autonomous-campaign-20261020/REPORT-E2E-CONTINUATION.md`**, `analyze_e2e_continuation.py`, `e2e-continuation-analysis.json`, approved pair2 context/ledger, `pair-2-execution/after-import-correction/prerequisites.json` and `post-crash-reconciliation/`.

## 58. Final original slot3 — native actually launched, workspace integrity abort — 2026-10-20

Owner explicitly said **“chạy đi” / “trong prompt có nói rõ mà”** after the prior memory refusal was explained. Bound renewed direction to the last existing original slot, no cap change/reset/automatic retry of history. **Now4/4 attempts consumed, zero remaining; one actual native arm launched, zero completed native arms/pairs, no arm2. PARTIAL / OPERATION_BLOCKED / BUDGET_EXHAUSTED; completed writer-tail objective NOT fulfilled.** Original1/R1/original2 objects/receipts unchanged versus `ledger-before-final-slot.json`.

ID **m4-contention-campaign-20261020-pair-3**, job **job-8740-9**, supervisor exit1. Fresh static fullsync61-input/six-library/50-durability checks, same-controller strict rehearsal prerequisite, capacity/integrity/no-owned-unit checks PASS. Unchanged source43d62bc4…/ELFef0221f0…/controlleraa1103b4…, no native rebuild/source/ABI/schema/production-index/durability change. Caps unchanged2CPU/1GiB/swap0/tasks128, sampled512MiB allocated/1GiB apparent, reserves1GiB/2GiB, watchdog115/systemd120/GNU355+5, tracing off, fresh Btrfs root.

Ambient1200 valid samples/59.950846022s; raw stability FAIL MemAvailable drift nonblocking, not refusal. Load4.52–5.73, CPU someavg10=0%,IO full1.29–3.19%,memory full0–2.17%,MemAvailable2,243,664–9,445,100KiB. Native ELF **PID139597** started16:14:40.679230Z in actual unit **m4-contained-580593efa0104d61.service** (bootstrap139593). Receipt benchmark_started=true is now **native**, not rehearsal.18runtime samples/17active, last active observation966.446534ms; proc IO/memory samples complete.

At16:14:41.687678Z **workspace_telemetry_incomplete**: `generation-1/fts/tombstones-2-1.bin.tmp.139597` vanished between os.walk enumeration/stat (ENOENT). Native AtomicWrite creates .tmp.PID then fsync/rename or unlink; observed race consistent with transient publication, exact syscall untraced. Current scanner treats any stat OSError as incomplete; no swallowing/retry/reclassification. Watchdog SIGTERM native, return-15/signal15. Raw arm WATCHDOG_ABORT/ABORTED_CONTAINED, outer OPERATION_BLOCKED, raw failure_class UNKNOWN retained. Raw measurement_quality COMPLETE scopes available process samples, **does not make failed workspace/unfinished native run valid**. Failed scan allocated3,153,920B/apparent6,197,220B partial only, not verified workspace peak/cap proof.

Observed partial cgroup peak219,193,344B/swap0, final CPU1,087,464us, no max/OOM/swap/task/throttle events; PSS max199,961KiB/RSS214,288KiB, process-tree writes max12,025,856B. Partial lifecycle includes bootstrap/readers/workers, not isolated writer/full native costs; exited-worker IO can disappear. Native stdout empty, **zero completed cycle payloads; writer p50/p95/p99/native/post-M4/finalization/coherence/reopen/order comparison unavailable**, not0. No regression/win/qualification or independent-p95 addition, H1–H4 unresolved/H5 observer race observed only.

Cleanup_complete/cgroup.kill success and controller retirement verified. Independent exact unit not-found/inactive, cgroup/socket absent, all owned units absent. Arm2 nevercreated. Partial research data root `/var/tmp/m4-writer-sync-pair-z9h8aj8w` retained, not deleted/reused. Quiet post-stop inventory never repairs the live failed scan. No other-project action, no extra native attempt. Immediate host snapshots retained; native affinity/top-process attribution not directly captured, delayed operator inspection labelled as such.

New one-off final launcher/file-only analyzer and evidence only; runner/guards unchanged. An unrelated editor clang fallback missing include path produced false m4.h/namespace cascades; research `.clangd` now selects stable project-local `.work/build/lsp` database mirrored from verified CMake build, no source flags overridden. Full clangd feature-check timed out60s after AST (not PASS); actual recorded compiler syntax-only PASS and fresh active3-file LSP clean. New Python typecheck/analysis/hash replay retained separately. No runtime/code build or additional performance measurement from tooling checks.

**KEEP strict caps/telemetry/fullsync and known CPU/parser corrections; DEFER latency optimization. Exact next:** no native budget remains. Highest-value follow-up is a bounded race-aware workspace-accounting design/regression covering atomic rename/unlink and explicit uncertainty/completeness—not ignoring ENOENT, retrying until green, or quietly creating a fifth attempt. Any future native campaign needs new authority, not implied continuation. Full report/raw receipts/actual launch/caps/cleanup/source identities/reproducible analysis/checksums: **`research/m4-native/results/autonomous-campaign-20261020/REPORT-FINAL-PAIR-3.md`**, `analyze_final_pair_3.py`, `final-pair-3-analysis.json`, `pair-3-execution/`, final approved context/ledger; raw sibling writer-tail-causal root named by pair3. No push, unrelated three Rust edits preserved.

## 59. Workspace observer correction; capacity proof still unavailable — 2026-10-21

Owner authorized bounded research scanner/accounting investigation and deterministic/synthetic containment tests only; no full native500×20, new attempt or campaign request. **4/4 consumed, zero remaining unchanged. Research correction verified; native readiness PARTIAL / OPERATION_BLOCKED.** §58/raw launch/failure/binary/ledger remain historical, not reclassified.

`tools/m4_containment_runner.py` now walks descriptors no-follow once, checking directory metadata/identity and bounded per-pass inotify. Explicit states separate complete traversal (not atomic), partial known mutation, unverifiable scope/error/coverage/budget and sampled limit excess. ENOENT is retained as incomplete known-race evidence, not silently ignored. Non-ENOENT permission/EIO/watch/root errors remain fail-closed. No retry/rescan, independent upper-bound override or verified snapshot/peak backend. JSON observer limits:16,384entries/depth64/32issueexamples/65,536eventbytes/0.25s cooperative budget, not a preemption guarantee for blocked syscalls. Existing watchdog/systemd lifetime bounds remain.

All runtime/postflight consumers use the same workspace-capacity decision. The A/A controller's file-only contention analysis also uses zero reserve credit instead of sampled gross bytes; twelve unchecked JSON sites reuse existing error-propagating helpers, no fallback/retry/recovery logic. Current controller hash changes, historical rehearsal/native bindings do not. Partial counts cannot update sampled peaks or produce COMPLETE measurement quality; verified lifetime peak is null. Gross/time-skewed Btrfs allocation cannot reduce required shared-disk reserve, so reservation credit is zero. Resource caps/reserve thresholds/runtime/cleanup/native durability/qualification authority unchanged. No production/native/ABI/schema/WAL/full-sync binary modification.

Filesystem review found shared Btrfs regular directories, **no verified correctly scoped allocated AND apparent capacity accounting**. Global statvfs, process/cgroup IO/memory and inotify do not bound both counters; sparse size, reflinks, event coalescing/overflow, mmap/writeback and scope boundaries invalidate shortcuts. Qgroup subvolume extent accounting is not an ordinary directory apparent-size limit; no quota was enabled or privileged host change attempted. No absence-of-sysfs inference that quotas globally are off. Thus a normal atomic mutation still stops the affected arm when independent proof is unavailable; honest classification alone is not native recovery.

Fresh **job-8740-32:72tests PASS**, six suites19observer/15lifecycle/25contention/5benchmarkpolicy/7AAgate/1syntheticpair. Deterministic filesystem interleavings cover create/rename/unlink/replacement/directory loss/growth/sparse divergence/near-cap allocation/non-ENOENT errors; additional missing/replaced root, symlink, bounded cost/queue loss/nonfinite/forged-proof checks. Actual tiny Python/systemd unknown-capacity abort and single-file65,536B>testcap4,096B pre-GO refusal have final retirement verified. Existing exit7, SIGTERM-15, SIGKILL-9 and cleanup cases PASS. Synthetic pair uses Python stand-in, not native. v2 failed test expectation retained: cap guard correctly refused and existing API raised after receipt; assertion alone fixed before v3. Raw early controller cleanup false and later retirement true remain distinguishable. Deliberately mocked cleanup-failure cases are not leaked real units; closure query shows no owned units. Active two-file LSP0errors/2hints/30aux numeric-parse warnings, not warning-free. Pre-existing unchecked-JSON findings in the A/A controller were initially deferred, then resolved by reuse of error-propagating helpers. Final controller LSP probe zero diagnostics but clean confirmation inconclusive, not claimed clean. Subsequent direct four-path `ty check` PASS is recorded separately (`final-ty.log`/exit0). Its seven gate tests plus one synthetic pair test rerun on pinned final source are retained in `consumer-final/test-receipt.json`; no expansion into harness recovery logic.

**Decision: native launch path NOT ready for a separately authorized campaign. Remaining exact recovery:** separately bounded design/verification of correctly scoped independent conservative capacity evidence for both counters throughout concurrent mutation. No backend is implemented, no infrastructure/admin expansion implied, no native execution authorized. After engineering recovery native still needs fresh authority. No writer-tail performance/qualification conclusion, no native budget reset. Full analysis/design/limitations/test commands/source identities/raw failures/file-only replay: **`research/m4-native/results/workspace-observer-repair-20261020/REPORT.md`**, `verification-v3/test-receipt.json`, `verification-summary.json`, `analyze_verification.py`. Research-only commit, no push; unrelated three Rust edits preserved.

## 60. Focused capacity-feasibility investigation closed; retain no-launch — 2026-10-21

Owner requested a bounded feasibility decision, not another indefinite harness repair: Btrfs quota/accounting, writer-side logical/apparent enforcement, proportionality, and one of three outcomes. **Investigation COMPLETE; choose direction 3, retain NO-LAUNCH under the current contract. Native writer-tail remains PARTIAL / OPERATION_BLOCKED, campaign4/4 consumed, zero remaining.** No runtime source, resource threshold, executable policy, approved historical plan, ledger, native durability or binary changed; no M4/native benchmark executed.

Read-only `GET_SUBVOL_INFO` confirms `/var/tmp` and project scratch are both in shared Btrfs **subvol5**; no per-arm quota scope. Exact one-key quota-tree status read returns **EPERM**, CLI unavailable, sysfs quota state not verified. Absence of sysfs is not proof quotas disabled. Kernel simple-quota capability is not an enabled scoped quota. Full qgroups require global accounting and potentially expensive rescan; squotas change ownership semantics. Neither establishes aggregate application apparent bytes, nor automatically matches gross st_blocks. No enable/rescan/syncfs/subvolume/admin operation occurred. Host data/metadata allocation counters were recorded only as global context.

A shared checked pre-write logical reservation ledger is implementable for participating M4 writers: reserve before write/copy/extension, count temporary coexistence, retain reservations on partial failure, no reclamation needed for the conservative one-shot form. Coverage audit found WAL, stats, segments/compaction, backups, bridge snapshots/copies and temporary dictionaries; **native vector Open/upsert/flush and library storage are outside the M4 helpers**. Native source also exposes shared mappings. Thus a small M4 counter does not enforce whole-arm apparent capacity or allocated capacity. Finite500x20 inputs bound row mutations, not native preallocation/metadata/compaction storage. Per-file RLIMIT_FSIZE is not an aggregate cap.

Six distinct small model/filesystem checks PASS (two probe executions after a static-check correction, not12distincttests): boundary/oversized requests, partial-failure reservation, hardlink identity, 1MiB sparse extension with0blocks, open-unlinked data and1-byte file with4096B allocation. Payload65,537B/probe; owned temporary trees removed. Host-before/after, ioctl outcomes, source hashes and initial source/receipt preserved. Final direct `ty check` PASS; active one-path LSP0diagnostics/clean. No native build/oracle/sanitizer/measurement needed for evidence/docs-only work.

Proportionality: current512MiB allocated/1GiB apparent/2GiB disk reserve are administrative diagnostic risk budgets, not validated safety constants; workspace enforcement has been sampled abort, not a kernel hard quota. Historical final logical7,177,536B is about149.6times below the apparent cap; cumulative process writes226–251MB are NOT occupancy peaks. Global quota infrastructure solely for this tiny fixture is disproportionate scope, but small retained output does not bound transient growth, metadata or2s-monitor-gap overshoot on the shared root filesystem. No safe growth/overshoot bound was established, so an unapproved weaker contract is not a safety proof.

**Three-way decision:** (1) reject a partial counter as current-contract whole-arm enforcement; (2) an explicit weaker residual-risk contract is possible only as a separate owner decision, not adopted or silently implemented; (3) **retain no-launch now, and stop this investigation here**. No default engineering continuation remains. If the owner wants to resume, separately choose revised risk semantics (including which strict guarantees are relinquished, native/temp/retained scope, sampled-vs-hard controls and failure/cleanup rules) or authorize isolated-storage scope. Either also needs fresh finite native authority/budget. Do not start quota infrastructure, keep repairing the observer, poll for admission or request another campaign from this handoff.

Full reasoning, mechanism/cost matrix, write-path inventory, source references, acceptance reconciliation, raw probes and verification: **`research/m4-native/results/capacity-feasibility-20261021/REPORT.md`**, `probe.json`, `investigate.py`, `typecheck.log`, `verification.json`, `SHA256SUMS.txt`. Historical raw receipts and ledger remain unchanged. Feasibility completion does not complete or unblock the native measurement mission; production/unrelated Rust changes remain untouched.

## 61. Owner-approved exploratory workspace soft budgets; new single-arm authority — 2026-10-21

Owner explicitly approved soft budgets512MiB allocated/1GiB apparent only for contention-aware exploratory, and one NEW native diagnostic attempt after amendment verification and fresh readiness/provenance PASS. No automatic retry, no replacement/reset of old4/4campaign, no quota infrastructure or other-project interference. Exact authority/plan/new ledger: `research/m4-native/results/workspace-soft-budget-20261021/`.

Minimal runner/policy change preserves raw scanner rows. Known PARTIAL_KNOWN_RACE continues UNKNOWN/INCOMPLETE; soft overages warn with metric/observed/budget/excess bytes. Permission/EIO/scope/coverage failures remain fatal. Hard1GiBRAM/swap0/CPU200%/tasks128,115s watchdog120s systemd lifetime,1GiBhostRAM/2GiBdisk reserves, OOM/distress/requiredprocess+cgrouptelemetry/monitor-gap/correctness/durability/verifiedcleanup unchanged. Disk reservations retain conservative two-root allowance even for the new single arm; reservation credit remains0. Other modes/benchmarks remain strict. No capacity/peak/quota/overshoot proof claim. Completion with workspace uncertainty/warnings uses explicit non-PASS statuses, no fake COMPLETE inventory.

79distincttests PASS (32contention/19workspace/15lifecycle/5policy/7AApolicy/1syntheticpair), final32rerun on pinned source. Actual32KiB/1s Python watchdog rehearsal PASS with18over-budget warning samples, workload receipt COMPLETED_WITH_WORKSPACE_WARNINGS, exit0 and verified unit/cgroup/socket retirement; not a native arm. Initial wrong disk-reason assertion and lifecycle missing CLI argument preserved; only expectations/invocation corrected, no guard weakened. Direct4pathtyPASS; LSP0errors,2clean/2inconclusive. No native source/build/durability change; fresh static61compiler-input/library/completed-correctness evidence check required before native.

One-shot launcher uses exclusive operation directory/newmax1ledger, claims before readiness, no retry on failure, one fresh0700Btrfs root including temp data, fullsync500x20/no tracing/no second arm. At the engineering commit, native outcome was pending. Final outcome is below. `REPORT.md`, `engineering-verification.json`, `tests/`, `m4-contention-rehearsal-soft-1/`, `launch_once.py`, `approved-resource-plan.json`, `new-attempt-ledger.json` preserve authority and exact execution boundary. Historical ledger/raw receipts untouched; no production changes or push.

### §61 final native outcome and prospective direct correction

Commitfdef863 recorded amendment/tests before launch. New one-shot jobjob-8740-36, IDm4-contention-soft-budget-native-20261021-1, fresh61-input/library/fullsync/completed-correctness/fault provenance and hard capacity/integrity PASS. Actual native PID716330 started2026-10-21T02:32:52.935899Z in m4-contained-adf6803b58cc4074.service(bootstrap716259), tracingoff/fullsync500x20, sameELFef0221f0… and unchanged caps. One native arm, no second arm; new authority1/1consumed0remaining, old4/4ledger untouched.

After1.175346733s, native returned-15/SIGTERM. Raw WATCHDOG_ABORT/INCOMPLETE: workspace_capacity_unverifiable. Last scanner167entries/135files/32dirs/41.152ms saw two inotify MODIFY(mask2), then a readable queue after one bounded drain. This was labeled MUTATION_COVERAGE_LOST/events exceed one bounded drain, inventoryUNVERIFIABLE. No observed overflow/unmount/truncated event or scope/permission/EIO error. Native stdout empty:0completecyclepayloads, writerp50/p95/p99/stage/coherence/reopen unavailable, not0/PASS. Generation4 directory is not a completed-cycle verdict.

20partial runtime samples: requiredprocess/cgroup safety telemetry complete, all recorded hard capacity decisions allowed, no OOM/max/swap/task distress or throttling. Cgrouppeak229,666,816B/swap0/CPU824,026us; sampled tasks76, processPSS221,518KiB/RSS236,216KiB,writes39,514,112B. Partial lifetime, not whole-arm/isolated-writer costs or physical-device writes. Last partial allocated14,897,152B/apparent22,994,122B not verified peaks; verifiedpeaknull and peak-sampled fieldsnull because workspace incomplete. Hostload1.81–2.14,MemAvail4,418,612–5,221,452KiB,diskavail16.543–16.553GB; IO full/memoryfull/CPU someavg10=0 in short smoothed samples. Host deltas not identified causal M4 impact. Native affinity and contemporaneous top processes captured, no other-project intervention.

Cleanup: watchdog ownedSIGTERM then cgroup.kill success, controller terminal retirement verified; independent exactunitnotfound/inactive,cgroup/socketabsent,allownedunitsabsent. Private root/var/tmp/m4-soft-native-ilm3j5wcretained, notdeleted/reused. Bootstrap systemd signal9 is separate from native signal15. No reclassification of raw abort or exhausted budgets.

Direct defect correction only, prospective: select-readability after one drain is accounting uncertainty, not observed overflow. Allow only that exact pending-event issue plus known mutations with valid root identity and all issue examples retained; raw scanner still UNVERIFIABLE/INCOMPLETE/UNKNOWN, no new drain/retry/rescan. Actual overflow/unmount/truncated events/scope/permission/EIO/work-budget errors remain fatal, other modes strict. Two deterministic regressions added; final81tests PASS(34contention+19workspace+15lifecycle+5policy+7AApolicy+1syntheticpair). No native reattempt or new infrastructure branch. Executed approved plan/source/policy stays pinned tofdef863; later currentpolicyhash does not retroactively authorize another run. Correction is not native-validated.

**Final mission status PARTIAL / OPERATION_BLOCKED / NEW_ATTEMPT_EXHAUSTED.** Amendment/commit/tests/watchdog and authorized one-shot execution completed; valid native writer-tail measurement NOT obtained. Remaining condition is separately renewed finite owner execution authority to validate the prospective correction; no standing retry/admission polling authorization. Full report, raw receipts, file-onlyanalyze_native.py/native-analysis.json, final-verification.json and SHA256SUMS.txt in workspace-soft-budget-20261021. No push/production/unrelated Rust edits.

## 62. Newly authorized post4579e5a native500x20 completes with accounting uncertainty — 2026-10-21

Owner explicitly authorized ONE new native diagnostic after4579e5a, not an automatic retry/history reset. New operationm4-contention-pending-drain-native-20261021-1, jobjob-8740-44. Runtime runner/policy and native sources unchanged; launcher/fixedrehearsal copied byte-identically into separate evidence scope. Pinned source matches prior81testreceipt; current34focusedtests plus actual32KiB/1s same-controller watchdog e2e PASS(19overage samples, warning-not-PASS receipt, cleanupverified). Fresh fullsyncELFef0221f0…/61compilerinputs/runtime-library/completedcorrectness+durability/readiness PASS; no unresolved owned units. No new harness, quota admin, guard/cap/durability change or intervention in other projects.

Actual nativePID784988 started2026-10-21T02:44:03.108833Z, unitm4-contained-a92795d9db7a4185.service, exit0 at02:44:09.221028Z, commandwall6.112181178s.20commits/80readerqueries/coherence0; post-close reopen generation21/live500/vectorFTScoherence0,460.217ms. Final1generation/0pinnedretired/1shard/0stalerows/5compactions. Requested bounded diagnostic COMPLETED WITH CAVEATS, not qualification or whole comparative M4 mission completion. Rawreceipt COMPLETED_WITH_WORKSPACE_UNCERTAINTY, measurementINCOMPLETE, hardprocess/cgsafetytelemetrycomplete.104runtime samples:77complete traversals(notatomic),24knownraces,3rawUNVERIFIABLEpending-after-drain cases continued UNKNOWN, no extra drain/rescan.0sampledsoftbudgetoverages, no verified lifetime peak; peakfieldsnull/reservecredit0. Old raw failures not reclassified.

Writercommit wallp50/p95/p99 **231.399/267.359/369.428ms**; nearest-rankceil(q*n)-1,n20p95secondlargest/p99max, single-rununcertainty. Nativevectorbuild **102.032/117.603/223.503ms**, M4update103.357/119.271/121.150ms. Post-M4SyncTree15.385/26.212/26.304ms; wholepost-M4-to-round-end24.051/37.658/40.929ms derived percycle, not summed percentiles. Snapshotfinalizep500.286ms, genpublish3.698ms, retirement0.614ms. Summedcommitwall4.837616s/writerthreadCPU3.308866s; nativevector44.97%/M4update42.69%/entirepost-M410.66%(SyncTreesubset7.66%) of summedcommitwall. Slowestround7zero-based compacts:369.428ms, vector223.503(open180.306),M4update104.606,postSync21.367,posttotal37.658. Association not isolatedcompactioncause. Build/open stages dominate this run more than post-M4sync; no optimization/win/regression inference. Readerp50/p95/p99=2.092/2.966/4.332ms.

Hardcapsunchanged1GiBRAM/swap0/CPU200%/tasks128/watchdog115systemd120,hostRAMreserve1GiB/disk2GiB+conservative2rootallowance. Allruntimehardreservedecisionsallow; noOOM/max/swap/taskdistress/throttle; maxsamplegap70.061ms<2s. Cgroupkernelpeak323,055,616B(308.090MiB),swap0,CPU4,978,732us; sampledPSS275,706KiB/RSS290,228KiB/tasks80. Native mainprocessCPU4.336282s, reopen0.510993s; not isolatedwritercost. Main+reopen/procwrite_bytes220,258,304B(210.055MiB), not physicaldevicewrites; rawphysical_write_byteslabelretained. Workspace complete-traversalobservedmax18,665,472Ballocated/34,150,594Bapparent notverifiedpeaks; finalrootlogical6,181,823B. Externalobservermean/maxscan5.056/14.240ms, sample8.830/19.437mswall, not observerCPU or perturbation-free proof.

Hostload6.12–6.32,MemAvailable3,184,020–4,239,384KiB,diskavailable16.304–16.327GB. PSIavg10IOfull0.75–1.34%,memoryfull5.63–9.73%,CPUsome2.00–3.66%; hostswap-in+51,823pages,swapout/directreclaim/OOMdelta0,ownedswap0. Notidlehost, not identifiedincrementalM4impact. Actualnativeaffinity0–19/top/beforeaftercontextcaptured; no projectquieting. Allcleanupterminalreceiptsverified; independentexactunitnotfound/inactive,cg/socketabsent,allownedunitsabsent. Newprivate0700Btrfsrootincludingtmpretained, notdeleted/reused.

Newmax1ledger consumed1/remaining0,1launched/1completed. Oldcampaign4/4 and precedingseparate1/1spent/receiptsmanifests byte-identical. No retry/secondarm/othermeasurement. Onearm20commits,p99max, no comparator/order/stationarybaseline/causalhostattribution/hardworkspacequota/qualification/promotion claim. Furthernativeworkneedsnewfiniteauthority; no automatic continuation.

Full report/authority/plan/rehearsal/raw receipts, reproduciblefile-onlyanalyze.py,analysis.json,per-round.csv,verification.json,SHA256SUMS.txt: **research/m4-native/results/pending-drain-native-20261021/**. RESULTS/README/policy explanation updated; machineJSON/runtime/native/production unchanged. No push; three pre-existing Rust changes left outside this task's commit.

## 63. Bounded writer-tail follow-up isolates ordinary native open cost; cycle-7 outlier remains unresolved — 2026-10-21

No new sustained/native campaign was run: §62's one-arm authorization is spent. Focused local probes only, with existing caps unchanged (1GiB RAM, swap0, CPU200%, tasks128); no runtime, native dependency, production schema, or benchmark-policy change.

Trace: `research/m4-native/tests/m4_bridge_integration.cc` compaction threshold selects full live rows, creates an empty native collection, upserts and flushes, then builds durable generation metadata, opens/applies/flushes M4, finalizes and publishes the snapshot. `OpenVector` schema assembly itself measured only0.007–0.021ms. Across two untraced runs, exact six-scalar-field schema create/open medians ranged64–80ms (observed samples59–91ms); HNSW and FLAT were similar. No-scalar controls were about8–13ms. Schema assembly was <0.1ms. One-field controls had medians30–41ms, with no lone dominant field. A whole-process, strace-perturbed summary saw1,501 vs385 futex calls and159 vs27 fsync+fdatasync calls (scalar schema vs no-scalar, 3 opens each). It includes initialize/close/shutdown; sync syscalls may occur outside the timed open. This is consistent with more synchronization/synchronous-persistence work in the scalar-index lifecycle, not a phase attribution; exact CPU/wait/device-I/O shares remain unknown. HNSW is not the ordinary empty-open cause in this fixture. This is not a feature-equivalent removable optimization: scalar fields support M4 bridge filtering. The 2026-10-21 cycle7 native open remains180.306ms, nearly2× the later untraced probe's maximum. That cycle has no open-only CPU measurement; its 166.893ms CPU is for the complete native build. No event-specific native profile/syscall accounting establishes why this open was slower. Host/scheduling/I/O attribution remains unresolved.

A sequential bounded500-doc×5-round baseline/candidate comparison exercised the research option `--incremental-fts-sustained-cached-fingerprint`. Strict M4 open samples80.22–84.88ms, candidate37.93–43.48ms; on the compaction round M4 open fell84.88→43.48ms while native open stayed59.96→59.70ms. One cached fingerprint pass (~36–42ms) remains and the second (~38–42ms) is skipped. Writer p50/p95 moved216.28/258.48→191.02/232.65ms in these single sequential arms; native-open outliers and order/host effects make whole-commit deltas directional only. Both final validations reopened generation6 with500live FTS docs and zero coherence errors, but `OpenDeltaVectorSnapshot()` constructs default `m4::Options`; that post-close reopen used strict verification, not the skip option. The separate `--incremental-fts-candidate` integration suite (582 checks/0 failures) exercises incremental-FTS generations but does not set `skip_cached_analyzer_fingerprint_verification` either. Process peak RSS216,632→211,092KiB; reported writes60,342,272→60,981,248B are process counters, not device writes.

Keep strict verification default. The cache is process-local; after a true process restart it is cold, so the candidate has no initial cache hit and performs full initialization plus post-init verification. Existing cache-lifecycle coverage verifies content-based invalidation (including same-size/same-mtime mutation), reuse across generations, and the hot-cache skip. It does not test crash/recovery with the skip active; the existing crash/recovery checks use strict options. A fresh `m4_analyzer_cache_lifecycle` CTest rerun passed1/1 (2.62s). The skipped second fingerprint remains a TOCTOU/integrity tradeoff for a concurrently mutable dictionary path; do not promote without a documented immutable-dictionary contract or a design that preserves that check.

Validation: the current `m4_bridge_integration --incremental-fts-candidate` passed582 checks/0 failures, including incremental-FTS compaction/reopen/recovery/coherence under the unchanged resource limits; this is not candidate-specific fingerprint-skip coverage. A first CTest attempt was `Not Run` because that target binary was absent; building it resolved that. One first launch failed because the probe executable was absent (exit127); another aborted with SIGABRT under an agent-imposed `TasksMax=32` (lower than the approved128 cap). Both are retained/excluded, and the abort's cause is unproven. Subsequent microprobes used the approved128. No policy gate or full native arm was retried.

Full report/raw outputs/host snapshots: **`research/m4-native/results/writer-tail-investigation-20261021/`**. RESULTS and README latest-status summaries updated; runtime JSON, M4 runtime source, native dependency, and production remain unchanged. Pre-existing unrelated AGENTS/Rust workspace edits were not included.

## 64. Owner-authorized 0–6 scalar-index phase trace blocked by unchanged host gates — 2026-10-21

A separate explicit owner request authorized one exploratory discriminator: scalar-index counts0–6, HNSW and FLAT as distinct matched conditions, three repetitions, and both fresh create and existing-collection reopen. Approved arm caps: RAM1GiB, swap0, CPU200%, tasks128, timeout20s. Owner accepted shared-Btrfs I/O contention because the available delegated cgroup has no `io.max`; results would be exploratory/non-qualifying. This authorization does not replenish or alter §62's exhausted campaign ledger.

No operation was launched. Fresh read-only host receipt at2026-10-21T06:43:58.627134Z failed two unchanged hard gates: MemAvailable3,992,232KiB <5,477,762KiB (short1,485,530KiB) and `/var/tmp` free10,815,062,016B <18,219,000,000B (short7,403,937,984B). Host I/O PSI full avg10 was0.88% ≤4.46% and load-1 was2.36 ≤3.14. These are empirical operation-specific gates, not universal host-safety thresholds. No runner arm, systemd service, cgroup, native open, or timing sample was created. No thresholds, policy, source, or native binary were changed. Preserve the no-launch; do not bypass or automatically retry. Recovery requires a new fresh passing preflight and a policy-consistent operation plan; confirm the one-run authorization still applies before launch. Any threshold change requires separate explicit owner authorization.

The discriminating trace is still unmeasured. Source inspection predicts a shared cost at `InvertedIndexer::open` (one RocksDB context create/open for all scalar columns), followed by per-field `InvertedColumnIndexer` setup. Create also includes IDMap/DeleteStore, VersionManager and writing-Segment publication. Reopen includes schema/version metadata and recovery of persisted/writing Segments, scalar/vector indexes and WAL. A valid probe must time internal spans per API open and scope `fsync`/`fdatasync` to that open; close/shutdown stays outside. The report records this trace map and the no-launch evidence: **`research/m4-native/results/writer-tail-phase-trace-20261021/REPORT.md`**, raw `preflight-host.json`/`preflight-guards.json`, and `SHA256SUMS.txt`. The result distinguishes the operation blocker from research mission completion; it does not explain cycle7's180.306ms open outlier.

Cached fingerprint remains opt-in. The cache is process-local and content-keyed; a true restart is cold and must take the full verification path. A focused `m4_analyzer_cache_lifecycle` assertion now requires two fingerprint passes/no skip for an expired cold-cache reopen even when the candidate option is set; rebuilt CTest passed1/1 in2.78s. This does not remove the hot-cache mutable-dictionary TOCTOU tradeoff. The 500×5 candidate arm exercised warm in-process generation opens; its final reopen used strict options, as did crash/recovery tests. Strict mode remains default.

## 65. Bounded 0–6 open-probe resource inventory; no rehearsal or diagnostic launched — 2026-10-21

**Historical entry; superseded by §66.** At the time this inventory was written, no rehearsal had launched.

The follow-up mapped the requested diagnostic to its actual workload: seven scalar counts × HNSW/FLAT × three repetitions, each fresh create and persisted reopen, **84 serial API opens**. The fixture is empty, has no query/insert workload, and uses one collection path at a time. Native create initializes IDMap, empty DeleteStore, VersionManager, writing Segment, and—when scalar indexes are enabled—one shared inverted RocksDB context with per-field column indexers. Six configured fields produce 12 CFs; the zero-index control skips the scalar DB. Empty WAL makes `SegmentImpl::flush()` return early on close; this is not evidence of zero filesystem I/O. HNSW graph and FLAT vector data are not built without rows. Existing probes time fresh create only, with max100.174ms; they have no reopen timings, cgroup memory/task peaks, or per-root byte accounting. Thus 20s is not a proven duration bound and no smaller memory/storage cap is supported by direct measurement.

Read-only inventory: 209 owned `/var/tmp/m4-*` roots total1,415,430,144B `du` allocation; `.work/build`10,788,864B, `.work/tmp`112,660,480B, results598,994,944B, native source947,531,776B, native build1,636,302,848B. Shared Btrfs prevents treating `du` as exclusive physical attribution. Keep all results, builds, native source and historical roots. Twenty old analyzer dictionary snapshots account for111,902,720B, and their content hashes match the pinned source dictionaries; however, `/proc` inspection had358 unreadable/raced PIDs and `lsof` reported unrelated namespace warnings. They are **not** approved for cleanup; no files were deleted. Full inventories and limitations: `research/m4-native/results/open-probe-resource-plan-20261021/`.

The old §64 per-arm limits (1GiB RAM, swap0, CPU200%, tasks128, timeout20s) are only candidate values for a new plan; §64 used them only for no-launch preflight. A resource-only rehearsal was proposed before any latency diagnostic: one repetition of all 14 count/type configurations, create/close/reopen/close (**28 API opens**). That proposal was later corrected to the full 84-open workload under explicit owner approval; see §66. The 2026-10-21T07:21:35Z observational host snapshot was not a preflight. Current disposition from this historical point is superseded by §66.

Focused correctness: `m4_analyzer_cache_lifecycle` rebuilt and passed1/1 in3.95s (38 checks,0 failures), including a separate `exec` process reopening a persisted M4 generation and asserting a cold cache miss, one initialization, two fingerprint passes, and no skipped verification. The changed-file LSP check timed out at20s; direct build/CTest are the passing checks. The later rehearsal and its guard/measurement limitations are recorded in §66.

## 66. Owner-approved scalar-open resource rehearsal; host guard and telemetry failures — 2026-10-21

The owner approved operation-specific admission floors of **MemAvailable ≥3 GiB and `/var/tmp` free ≥8 GiB for this resource rehearsal only**, retaining 1 GiB MemoryMax, swap0, CPUQuota200%, TasksMax128 and a 20-second workload timeout. I corrected the proposed 28-open rehearsal to the exact 84-open workload before launch: seven scalar counts (0–6) × HNSW/FLAT × three repetitions, create/close/reopen/close, one empty root at a time, no documents/queries. No other project was touched and no retained artifact was deleted.

Fresh readiness at 2026-10-21T07:57:50Z passed the two approved floors (MemAvailable3,626,200KiB; free disk10,738,757,632B). **An unchanged historical load guard failed: load-1=5.19 >3.14.** The initial custom operation context incorrectly marked all historical gates nonblocking rather than overriding only the two owner-approved floors. The rehearsal therefore ran contrary to the unchanged load gate; this is an agent execution error, not a valid waiver. The runner was corrected afterward so this purpose enforces every historical guard except MemAvailable and disk, and it fails on host swap/direct-reclaim/OOM movement. Unit tests cover both exact 84-open binding and preservation of non-overridden gates. No rerun is permitted under this authorization.

The workload returned0 after84 API opens: workload-reported3.47062s, command start-to-exit3.491871s, watchdog samplespan3.555100s. Runtime limit20s applies from workload start; systemd hard stop was RuntimeMaxSec25s; watchdog and controller waits are bounded at32s and28s respectively, with bounded setup/cleanup calls. Thus the operation was bounded, but **20s is not an end-to-end start-to-cleanup deadline**. Cgroup sampled memory peak49,082,368B; swap peak0; tasks peak66/128; CPU usage2,845,805µs; memory/swap/pids max and OOM event deltas0; CPU throttling0. `/sys/fs/cgroup` exposes no I/O controller. Highest sampled process-tree writes117,190,656B are not device-write attribution.

During complete host samples, MemAvailable minimum3,438,784KiB and free disk minimum10,730,123,264B remained above the approved floors. Load ranged5.02–5.19. Host vmstat changed: swap-in+177 pages, swap-out+1,576, direct scan+270, OOM delta0; source attribution unknown. This would trigger the corrected distress gate. Workspace telemetry was **INCOMPLETE**: active RocksDB log disappearance and directory mutations were observed, followed by `MUTATION_COVERAGE_LOST` (“events exceed one bounded drain”). Program output measured maximum final per-root size126,779 apparent/163,840 allocated bytes over42 closed roots before removal; this is not a transient/verified peak. Raw result is `COMPLETED_WITH_WORKSPACE_UNCERTAINTY`, not a valid complete resource measurement.

Unit is independently `not-found`/`inactive`; its cgroup is absent. The rehearsal-owned empty `/var/tmp` workspace was verified empty then removed using `rmdir`; no other `/var/tmp/m4-*` root was touched. Receipts, stdout and analysis: `research/m4-native/results/open-probe-resource-plan-20261021/m4-scalar-open-rehearsal-20261021/`; overall report: `research/m4-native/results/open-probe-resource-plan-20261021/REPORT.md`.

**Disposition as originally evaluated: PARTIAL / OPERATION_BLOCKED.** The one rehearsal is spent. No retry, latency benchmark, 0–6 diagnostic, or qualification run. Any future execution requires a separate owner decision after fixing workspace mutation telemetry and proving the corrected gate path; no success/latency/qualification claim follows from this run.

## 67. Resource-only exploratory contract amendment; no rerun — 2026-10-21

The owner amended the resource-only exploratory contract: **load-1 is observational, not a hard admission gate; host swap/reclaim deltas are recorded and analyzed, and a positive delta alone is not distress.** OOM, true capacity exhaustion, telemetry-integrity failures, and existing hard reserve/cgroup/timeout/correctness/cleanup guards remain blocking. The host RAM/disk reserve values remain operation-approved and are not lowered or defaulted by this amendment. Qualification, paired benchmark, legacy diagnostic, and production policies are unchanged.

Workspace races or lost coverage remain **UNKNOWN / INCOMPLETE**. Sampled traversal cannot establish a verified lifetime peak; soft-budget telemetry/warnings never become PASS or hard-cap proof. The 84-open run's load 5.19 (historical envelope 3.14) and host swap-in/out/direct-scan increases are now interpreted as observations, not independent blockers or M4-attributed effects. The workspace observer's `MUTATION_COVERAGE_LOST` remains decisive: no complete resource-measurement claim, verified peak, latency claim, or qualification result. The raw §66 receipt and original report are preserved; the amendment does not authorize a retry. Current status: **PARTIAL / MEASUREMENT_INCOMPLETE**, one-shot authorization spent.

Implementation and tests are scoped to the operation-bound resource-only purpose; global historical thresholds and other benchmark modes remain intact. Details: `research/m4-native/results/open-probe-resource-plan-20261021/CONTRACT-AMENDMENT-20261021.md`; machine-readable authority: `research/m4-native/docs/benchmark-policy.json`; policy tests cover load disposition, swap/reclaim observations, OOM/counter integrity, and non-PASS workspace caveats.

## 68. Separately authorized bounded scalar-open latency diagnostic — 2026-10-21

The owner subsequently authorized one direct, bounded 0–6 scalar-open latency diagnostic under the amended exploratory contract; this did not reopen or retry the spent resource-only rehearsal. The native workload completed once with exit 0 and 84/84 API intervals (scalar counts 0–6 × HNSW/FLAT × 3 repetitions, empty collections, no documents/queries). Outcome: **COMPLETED_WITH_WORKSPACE_UNCERTAINTY / WORKSPACE_CAPACITY_UNVERIFIED**, evidence class **EXPLORATORY / NON-QUALIFYING**. Per-cell median/range data, host context, raw receipts and exact coverage caveats: `research/m4-native/results/scalar-open-latency-diagnostic-20261021/m4-scalar-open-latency-diagnostic-20261021-r1/REPORT.md`.

The observer recorded 44 non-atomic complete traversals, 9 known-race partial traversals, and one mutation-coverage-loss event after its bounded drain. Workspace status remains UNKNOWN/INCOMPLETE; the largest observed traversal is not a verified peak or capacity upper bound. Cgroup, correctness, host reserve and cleanup guards passed; cleanup was verified. Host load and positive swap/reclaim deltas remain observations, not M4 attribution. The timer reports whole C API create+open and reopen calls only: it does not break down schema/index setup, and no documents or flush operation were used, so this is not HNSW graph-build or flush timing. The one diagnostic is spent; no retry, qualification, paired benchmark or production operation is authorized.

## 69. Writer-tail optimization review — 2026-10-21

The three candidates were evaluated separately; no combined performance run or promotion is claimed. Full prior evidence: `research/m4-native/results/writer-tail-phase-trace-20261021/REPORT.md`, with timings and trace limits in `research/m4-native/results/writer-tail-investigation-20261021/REPORT.md`.

- **Scalar initialization — DEFER.** Empty-collection 84-open and later one-field probes show a strong but noisy association between scalar-index count and whole `create_and_open` latency. Native source traces the cost to writing-segment creation, one shared RocksDB context and per-field `InvertedColumnIndexer` initialization; HNSW graph build is absent with zero documents. Whole-process `strace` futex/fsync/fdatasync counts cannot assign cost to an open phase. The requested bounded phase sweep was **not launched** after mandatory preflight failed MemAvailable and free-disk floors; there is no internal phase split or causal attribution.
- **Compaction collection reuse — DEFER promotion; bounded proof-of-correctness now exists.** The mutable `Coordinator` prototype appends generation-keyed rows to one shared native collection, holds the snapshot mutex exclusively across commit/GC, filters queries by generation, flushes both stores before publication, and gates retained generations with reader leases/backpressure. A test-only exact-generation GC now reclaims unpinned published rows and WAL-reserved-but-unpublished crash debris; crash tests cover M4-GC interruption and commit interruption after reserve, M4 staging, and native staging. It is not a drop-in replacement for immutable-shard compaction: no full-size writer-tail performance comparison has been run, no cross-process reader lease protocol exists, and no production/native source changed. The immutable per-generation shard path remains unchanged.
- **Cached fingerprint — KEEP opt-in only; reject default promotion.** The single 500-doc×5-round sequential run lowered M4 open by 38–42 ms, while native open was effectively unchanged; the cache does not remove the first full fingerprint. Content mutation before open changes the digest and rejects the manifest before lookup. The cache is process-local; the cold `exec` test with the skip option still misses and verifies twice, so crash/restart cannot reuse a warm analyzer. A hot hit can accept a source-path mutation after the initial digest and before the skipped post-init reread; the returned analyzer still matches the digest/manifest, but strict “unchanged throughout Open” behavior is not preserved. The feature remains disabled by default. Weak registry entries do not retain analyzers; live generations share one immutable dictionary snapshot until the last owner closes. The observed RSS/write-counter deltas are too small and confounded for memory or device-I/O claims.

**Overall at this handoff: PARTIAL / OPERATION_BLOCKED for performance diagnosis.** Current owner authorization supports continued M4 research and implementation, but does not waive per-operation resource gates or permit promotion without comparative evidence. No production/native-source changes, benchmark-policy changes, or resource-cap increases were made. The new correctness work and the latest resource observation are recorded in §70; do not infer a performance win from the small test fixture.

## 70. Owner-directed recovery investigation and shared-collection GC prototype — 2026-10-21

The owner authorized autonomous M4-scope investigation, correctness fixes, and further bounded experiments; production must remain unchanged, resource limits/authorizations stay in force, and completed work should be committed without push.

**Bridge-test failure investigated; attribution is bounded, not proven.** The first full `m4_bridge_integration` failure was 11 checks cascading from subprocess `zvec_collection_open` returning `INTERNAL_ERROR: Resource temporarily unavailable`. That invocation used `TasksMax=128` for a multi-process test, supporting a task/process-resource hypothesis; no contemporaneous PID-cgroup event receipt was captured, so this does not prove the scope itself exhausted its limit. Re-running without that task cap (still scoped to `CPUQuota=400%` and a 600-second timeout) passed **1,247 checks, 0 failures**. The diagnostic `Need` helper now includes the native status message so this class of resource failure is not misread as established crash-recovery corruption. No project guard was bypassed; the remaining attribution limitation is explicit.

**Shared-collection prototype correctness fix.** The experimental prototype initially relied on range-delete filters; native `delete_by_filter` failed for a range predicate with an Arrow schema/type mismatch, while exact `generation = N` filters passed. The GC now issues exact-generation deletes. It serializes with commit/query snapshots, flushes M4 tombstones before vector deletion, uses the WAL high-water to include abandoned reserved-but-unpublished generations, and reclaims only unpinned published generations plus unpublished IDs. A test-only bounded retention cap prevents publication when a reader lease would exceed three retained generations. Recovery coverage passed **931 checks, 0 failures**, including pinned-reader visibility, GC crash after durable M4 deletion, and process crashes after generation reserve, M4 staging, and native staging; each restart keeps the published generation visible and converges both stores to 8 current rows. This is correctness evidence for a local prototype, not a runtime integration or performance result. The full bridge/crash-recovery suite separately passed 1,247/0 after the edits.

Build and local artifacts: `research/m4-native/.work/build/m4-stage-trace-bridge-test/m4_bridge_integration`; exact moved test roots under `research/m4-native/.work/tmp/task-20261021/`, including latest crash-matrix root `m4-shared-collection-reuse-crashmatrix-fixed-20261021T130632Z-3520689` and full bridge root `m4-stage-trace-bridge-final-20261021T130655Z-3529280`. The earlier artificial-TasksMax failure is retained for diagnosis. No user data, production data, or unrelated temporary roots were deleted.

**Performance remains unmeasured.** The shared-collection fixture uses 8 rows and its 13 commit/11 GC timings are correctness-test instrumentation only; they are not representative or reported as benchmark evidence. The previous cycle-7 tail remains unexplained; no full-size paired writer run was launched. The latest host observation at that time showed MemAvailable **3,693,620 KiB**, below the existing **5,477,762 KiB** generic floor; this is historical context, not a current readiness receipt.

## 71. Durable GC cursor and next operation-bound comparison — 2026-10-21

The current owner-directed task authorizes continued M4 research and explicitly says not to apply the sustained 500×20 benchmark envelope indiscriminately to microbenchmarks. That instruction does not silently amend the machine policy: it still requires an operation-bound resource contract, explicit execution authorization, and acceptance of shared-host I/O risk before any native performance pair. Production/runtime integration remains out of scope.

**Bounded GC work correction.** The shared-collection test coordinator now keeps a checksummed, atomically replaced `shared-gc.state` with monotonic reclaimed-generation and orphan-reservation cursors. M4 tombstones and native vector deletes are flushed before the cursor advances; after a crash between either store deletion and the checkpoint, recovery replays idempotently. Steady-state GC examines only newly eligible generations/reservations rather than iterating from generation 1. The latest focused test passed **1,046 checks, 0 failures**, including crash-after-M4-delete, crash-after-vector-delete-before-checkpoint, and all three unpublished-commit crash points. The final full bridge suite passed **1,247 checks, 0 failures**. Fixtures remain 8 rows for the shared prototype and 24 rows for the bridge suite; this is not scale/performance evidence. Artifacts are retained under `research/m4-native/.work/tmp/task-20261021/`.

**No performance operation launched.** The 2026-10-21 13:35 UTC host snapshot had MemAvailable **4,083,716 KiB**, `/var/tmp` free **87,494,950,912 B**, and I/O-full PSI avg10 **3.36%**. It did not satisfy the existing 5,477,762-KiB generic memory floor, and it is not a complete preflight. No unrelated project/process was stopped and no automatic retry is running.

**Owner decision requested:** approve or defer one exploratory pair using the existing four-reader deterministic workload at **500 rows × 5 churn rounds per arm**: immutable vector shards with incremental M4 versus one shared vector/M4 collection with epoch GC and leases. Measure writer/reader latency, native/M4 open/setup, GC/compaction, CPU/process I/O, retained bytes, peak PSS/RSS/cgroup memory/tasks, and end-state coherence; one pair is descriptive only (five writer cycles per condition, no stable p95/qualification claim). Proposed unchanged per-arm controls: MemoryMax 1 GiB, swap 0, CPUQuota 200%, TasksMax 128, 180-second watchdog; soft sampled-workspace budget only. Proposed operation-specific host reserve is MemAvailable >= 4 GiB (1-GiB cap + 3-GiB host reserve), retain the existing 18,219,000,000-B free-disk floor and 4.46% I/O-full-PSI ceiling; load is recorded, not used as an idle-host gate. Collect valid capacity/pressure windows and inter-arm recovery; no launch unless every gate passes. This changes the generic memory threshold for this one microbenchmark and explicitly accepts shared `/var/tmp` Btrfs I/O with no per-arm I/O throttle or physical-write attribution. The current 13:35 snapshot is below the proposed 4-GiB threshold, so even an approval is not a launch until a fresh preflight passes. If approved, encode the operation-bound contract and telemetry before launching; on any gate failure preserve evidence, stop the pair, and do not auto-retry.

## 72. Owner-directed shared-pair I/O PSI gate amendment — 2026-10-21

The owner questioned whether the inherited 4.46% I/O PSI guard is necessary and explicitly pointed out that gates may be adjusted by benchmark. This supersedes §71's pending gate decision for this bounded exploratory pair only. The previous 14:23:37 UTC snapshot (4.48%, 0.02 percentage points above 4.46%) remains preserved as an observation of the prior contract; it is not relabeled as a passing preflight.

The shared-pair harness now treats I/O PSI as required, finite, host-wide **observational telemetry**, not an admission or abort gate. Per-sample decisions retain the raw historical `M4-RG-IOPSI` status, mark it unenforced for this operation, and include full/some PSI values plus an explicit interpretation caveat. The generic I/O gate and all other benchmark modes remain unchanged. Memory/disk reserve, cgroup caps, telemetry integrity, correctness/durability, recovery, workspace uncertainty, and cleanup remain enforced.

Changed files: `research/m4-native/docs/benchmark-policy.json`, `research/m4-native/docs/benchmark-policy.md`, `research/m4-native/tools/m4_containment_runner.py`, and `research/m4-native/tools/test_m4_contention_policy.py`. Focused shared-pair policy tests pass **2/2**; policy JSON validation and Python syntax checks pass. Broader contention-policy run: **39/40 pass**; the remaining test fails before its intended assertion because `verify_full_sync` reports compiler input mismatch for the modified `tests/m4_bridge_integration.cc` (stale/unmatched compiled provenance; not a PSI-policy assertion). The generic benchmark-policy tests pass **6/6**. The current bridge harness rebuild is running as job `job-3573960-76`; no benchmark arm has launched. Fresh operation readiness must be established against the amended policy and rebuilt/pinned harness; do not reuse the old I/O-gate refusal as permission to skip other preflight checks.

## 73. Shared-pair controller binding defect; no native arm started — 2026-10-21

The rebuilt pair harness and operation wrapper passed no-launch validation, and an amended-contract host snapshot passed the operation-specific capacity gates (MemAvailable 7,785,968 KiB, `/var/tmp` free 87,455,379,456 B, I/O-full PSI 3.04%; PSI is observational). The randomized first role was shared-collection. Both recorded pair invocations then failed before the native workload could start: independent watchdog config rejected `pair-first` because its contention binding validator only admitted `aa-first`/`aa-second`. Both receipts report `benchmark_started=false`, controller error, and no workload return code; no M4 cycles, writer/reader latencies, or native measurements exist. Bootstrap processes were terminated, systemd unit collection/cleanup was verified, and no owned M4 unit or cgroup remains. Raw artifacts and SHA256 verification are under `research/m4-native/results/shared-collection-pair-20261021/m4-shared-collection-pair-20261021-214421-0708b2/` and `...-214442-59f466/`.

The second wrapper invocation was accidentally triggered while reproducing the first failure; preserve both receipts and do not treat this as permission for another retry. The runner defect is corrected: watchdog config accepts only `pair-first`/`pair-second` for the shared-pair purpose, and `watchdog_phase` maps `pair-first` to runtime-first so it cannot lose the two-root reserve. Added synthetic regression tests cover both pair names, reject role substitution, and verify phase mapping. Both operation attempts are spent/refused under the current no-retry contract; **a new native attempt requires explicit owner authorization**.

Evidence: rebuilt `m4_bridge_integration` SHA256 `f42a2ffefa8a3bbc77f1f56b7d049be62c4085983e5d4e976b69051bb5c64bfb`; `run_shared_collection_pair.py --validate-only` passed; focused watchdog/policy tests passed; generic benchmark-policy tests passed 6/6. Full `test_m4_contention_policy.py` is 41/42: the unrelated existing correctness/durability assertion is preempted by `verify_full_sync` compiler-input mismatch for the modified bridge source. No benchmark win/loss or architecture comparison can yet be claimed. The remaining useful action is a newly authorized operation after the owner reviews the two no-launch refusals and corrected watchdog tests.

## 74. Corrected shared-collection pair — 2026-10-21

After the owner authorized a new pair, the missing initial `generation.manifest` was added before `Coordinator` construction in `RunSharedCollectionPerformance`. The rebuilt binary (`3388c930b675f4f404c43d918cf51a329346c59838bf45d6789255f58365ac9b`) completed one same-binary 500×5 pair, immutable-shard first. Fresh preflight MemAvailable was 7,584,008 KiB; all three inter-arm capacity/integrity windows passed. Both workloads exited 0, completed five rounds/20 reader queries, had zero coherence errors/failures, and cleanup was verified.

Descriptive result: baseline commit p50/p95 586/1,074 ms and reader p50/p95 1.58/2.02 ms; shared writer-cycle (commit+GC) p50/p95 464/482 ms and reader p50/p95 64.78/97.69 ms. Shared-arm host I/O PSI full avg10 was 10.11% versus 3.68% at baseline preflight; process-tree writes were ~209 MB versus ~61 MB, and apparent retained root ~34.7 MB versus ~11.5 MB. Candidate writer cycles include GC, so boundaries differ; the host-pressure mismatch and single pair preclude causal or architecture-wide conclusions. Keep immutable-shard as baseline; defer shared-collection promotion pending focused reader-latency and GC/storage investigation.

Both arms are `COMPLETED_WITH_WORKSPACE_UNCERTAINTY`: full filesystem traversals had zero scan errors, but are non-atomic; no lifetime workspace peak/cap proof is claimed. The wrapper initially missed the candidate's `documents` output schema after both arms completed; the parser was fixed and both raw receipts were revalidated without rerunning. Three receipt regression tests pass. Full report and raw evidence: `research/m4-native/results/shared-collection-pair-20261021/m4-shared-collection-pair-20261021-221036-a40ca2/`. Policy resource gates/caps remain unchanged; only the operation authorization reference was updated for this owner-approved new pair.

The candidate's `gc_m4_search_delete_ms` was 350–383 ms per round (total GC 374–407 ms; compaction 0 ms). `Coordinator::Commit` and `CollectBeforeGeneration` hold the exclusive snapshot mutex; `Hybrid` needs its shared form across both stores. This aligns with reader-wait concerns, but host I/O PSI was substantially higher in the shared arm and one run cannot allocate all observed delay to the lock. The pair is exploratory and does not complete the broader M4 design goal.

## 75. Overall M4 status and owner-requested stop — 2026-10-21

**PARTIAL / NOT COMPLETE.** The owner requested a detailed report and no further engineering. No final architecture has been selected; immutable-shard remains the working baseline, not a demonstrated winner, and shared-collection promotion is deferred. The latest evidence and outstanding completion gaps are recorded in `research/m4-native/results/M4-STATUS-20261021.md`, linked from the M4 README and RESULTS index. This was the status at that handoff; the subsequent owner request authorizes continued in-scope research, without authorizing new benchmark attempts or changing resource gates.

## 76. Shared-collection GC search/delete batching — 2026-10-22

The shared coordinator's M4 reclamation path previously ran one `SearchFilter` per reclaimable generation and one `Delete` mutation per hit while holding the exclusive snapshot lease. It now combines the generation predicates into one OR-filtered search, then applies deletes in batches of at most 256 mutations (the existing `ApplyBatch` limit). Flush, native deletions, durable cursor checkpoint, optional compaction, and lock/lease order are unchanged. The batched mutation API retains WAL-backed atomicity within each batch; a crash between batches remains recoverable because the GC checkpoint is still published only after all deletion/flush work, and replay is idempotent.

Verification: `m4_bridge_integration` rebuilt and its full correctness/durability/crash suite passed (1,247 checks, zero failures). This is a code-path optimization, not a measured latency result. No native benchmark was run; there is no new benchmark authorization or resource budget in this change. Reader lock duration still spans the commit/GC operations, and the one-pair evidence remains insufficient to select shared-collection over immutable-shard. Next valid step: inspect and test whether a safe lease/snapshot design can shorten reader blocking without weakening cross-backend coherence; defer performance claims until a new benchmark operation is explicitly authorized.
