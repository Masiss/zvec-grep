# M4 native open phase trace — preflight blocked

**Status: PARTIAL / OPERATION_BLOCKED. No native measurement was launched.**

## Authorization and scope

The owner requested a discriminating 0–6 scalar-index sweep and then selected the bounded-run approval: three repetitions at each scalar count, HNSW and FLAT as separate matched conditions, measuring both fresh create and existing-collection reopen. Approved execution limits were memory 1 GiB, swap 0, CPU 200%, tasks 128, and a 20-second runtime. The owner accepted the shared-storage I/O risk: the delegated cgroup has no `io.max`, so the probe could contend on shared Btrfs; results would be exploratory/non-qualifying. No policy threshold change or I/O isolation was authorized.

## Source trace found

Native API path in `.local/native/zvec-native-profile`:

- Fresh create: `zvec_collection_create_and_open` → `Collection::CreateAndOpen` → `CollectionImpl::open` → `create`.
- Create stages: path/lock; IDMap and empty DeleteStore snapshot; VersionManager; writing Segment; initial Version publication/flush.
- Writing Segment creation calls `SegmentImpl::create` → `load_scalar_index_blocks(true)`. It passes all indexed scalar fields to one `InvertedIndexer::CreateAndOpen`. `InvertedIndexer::open` constructs the RocksDB column-family list, creates/opens one shared `RocksdbContext`, then initializes each `InvertedColumnIndexer` in a per-field loop. This predicts a possible shared first-index cost plus per-field setup; prior one-field timings could not separate those terms.
- Existing-collection reopen: `zvec_collection_open` → `Collection::Open` → `CollectionImpl::open` → `recovery`. Recovery loads Version/schema metadata, IDMap/DeleteStore, persisted and writing Segments, scalar/FTS/vector indexes, then writing-segment recovery/WAL. These must be reported separately from fresh creation.
- In the empty fixture, HNSW graph construction is not performed during collection creation; FLAT only records vector-field metadata. Schema-builder time is outside the native API interval, while C-API schema conversion is inside it.

A phase probe must time these spans inside each individual API call and separately capture `fsync`/`fdatasync` only while that call is active. Close, shutdown, and cleanup must remain outside the open trace. No native source instrumentation or binary rebuild has yet been made; existing uncommitted native-source changes from prior research were left untouched.

## Fresh preflight — no launch

Host snapshot: **2026-10-21T06:43:58.627134Z**. `/var/tmp` is Btrfs. The unchanged mandatory host gates returned:

| Gate | Observed | Required | Result |
|---|---:|---:|---|
| MemAvailable | 3,992,232 KiB | ≥5,477,762 KiB | **FAIL** (short 1,485,530 KiB) |
| `/var/tmp` free | 10,815,062,016 B | ≥18,219,000,000 B | **FAIL** (short 7,403,937,984 B) |
| I/O PSI full avg10 | 0.88% | ≤4.46% | PASS |
| load-1 | 2.36 | ≤3.14 | PASS |

These are operation gates based on the unchanged historical envelope, not a claim that the host is universally unsafe. The memory and disk failures independently require no-launch. No runner arm, systemd unit, cgroup, native open, or benchmark sample was created; there are no latency numbers. The read-only capability probe confirmed CPU/memory/pids controllers are available and `io.max` is not.

Raw read-only preflight: `preflight-host.json`, `preflight-guards.json`.

## Cached-fingerprint correctness follow-up

The cache remains opt-in. The cached identity is content-based; the tests cover same-size/same-mtime dictionary mutation rejection before cache lookup and reuse across generation directories. The candidate skips only the second fingerprint on an initial warm-cache hit. A true process restart has no process-local warm cache, so it takes the miss path and performs full post-init verification.

Clarified prior evidence: the 582-check `--incremental-fts-candidate` suite tests incremental FTS, not the cached-fingerprint skip. The 500×5 cached-fingerprint arm did exercise the skip during in-process generation opens, but its final `OpenDeltaVectorSnapshot()` uses default strict options. Existing crash/recovery tests also use strict options. A focused lifecycle assertion was added for an expired/cold cache with the skip option: it must not report skipped verification and must perform two fingerprint passes. `m4_analyzer_cache_lifecycle` rebuilt and passed **1/1** (2.78 s). The active LSP check for that test source timed out and is inconclusive—not a clean diagnostic result; the direct CMake build and CTest passed. This does not remove the hot-cache concurrent-dictionary-mutation tradeoff; keep strict mode default.

## Three-direction conclusions from existing evidence

No new native measurement is claimed in this continuation. It consumes the completed timing/probe artifacts and the existing focused correctness reports; the failed phase-probe preflight remains a hard operation-specific blocker.

### 1. Scalar-index initialization — DEFER further optimization pending phase split

The 84-open diagnostic (empty collections, no documents/queries) measured whole API calls only. In the later one-field probe, 0-scalar HNSW create+open median was 12.736 ms and six-scalar HNSW was 79.600 ms; FLAT six-scalar median was 78.572 ms. Individual scalar fields alone were 30–41 ms, with a 100.174 ms logical-PK sample. The one-field costs are not additive and one field alone does not identify the six-field cost, so neither a field ranking nor a linear per-index model is justified. The earlier 84-open table had a higher six-scalar median (152.468 ms), confirming substantial run-to-run variability.

The source path is concrete: `Collection::CreateAndOpen` → `CollectionImpl::open` → `create` → writing-segment creation → `SegmentImpl::load_scalar_index_blocks(true)` → one shared `InvertedIndexer::open`/`RocksdbContext::create` → per-field `InvertedColumnIndexer::CreateAndOpen`. Fresh create also writes IDMap/DeleteStore and VersionManager metadata; C-API schema conversion is inside the call. HNSW graph training/build is not present because these fixtures contain zero documents. Reopen follows a separate recovery path and must not be conflated with create.

The whole-process strace comparison (three opens plus initialize/close/shutdown) saw 1,501 vs 385 futex calls, 96 vs 12 `fsync`, and 63 vs 15 `fdatasync` for scalar vs zero-scalar cases, but those counts are not phase-scoped and strace heavily perturbs execution. They cannot establish whether the added wall time is RocksDB metadata, per-field setup, synchronization, or filesystem persistence; no device I/O attribution exists. Thus the evidence implicates scalar-index lifecycle broadly, not HNSW or one field, but does not isolate a dominant internal subphase. The planned instrumented 0–6 sweep remains unrun because the unchanged preflight failed both MemAvailable and free-space floors. Do not infer internal phase numbers from whole-call timings.

### 2. Compaction collection reuse — DEFER as a drop-in replacement; retain immutable-shard design

The immutable `DeltaVectorSnapshot` path creates a fresh vector collection for each new shard; compaction writes all live rows into a new shard. Old `DeltaVectorShard` objects own their native handles and directories through `shared_ptr`, so pinned readers continue on the old collection while the new generation is staged and published. This deliberately buys snapshot isolation by recreating native schema/index state, including the six scalar INVERT indexes.

There is a research-only alternative already in the integration harness: `Coordinator` owns one mutable vector collection; writes use generation-prefixed primary keys and a `generation` filter, and `Commit` holds the snapshot mutex exclusively while staging FTS and vector rows. It flushes both stores before the publish WAL record and atomic manifest. Readers hold the shared lock across hybrid queries. `CompactComponents` drains readers and calls M4 `CompactNow` plus native `zvec_collection_optimize` in place. The existing `--vector-delta-candidate` report records 1,401 checks/0 failures, including crash cuts, native agreement and reader pinning (`durability-restore-20261020/REPORT.md`). This is evidence that **reuse can be safe while all historical versions remain addressable**, not that it is a safe substitute for immutable compaction.

The reused collection retains old generation documents: optimize has no generation-aware garbage-collection contract, and old readers/committed manifests may still depend on them. Each complete-snapshot commit also appends/upserts the current live set, so scalar postings and vector storage grow with generations. Deleting obsolete rows during compaction would need a reader-epoch/lease protocol, durable delete/publication ordering across M4 and native stores, and crash/reopen tests at every boundary. Without that design, reuse merely trades index recreation for unbounded retained-version index growth. No new prototype is warranted under current evidence; preserve the immutable path.

### 3. Cached fingerprint — KEEP only as explicit opt-in; reject default promotion

The single sequential 500-doc×5-round comparison in `writer-tail-investigation-20261021/REPORT.md` observed M4-open samples 80.22–84.88 ms strict vs 37.93–43.48 ms candidate (about 38–42 ms saved per open); native open on the compaction round was 59.96 vs 59.70 ms. The candidate still performs the first full content fingerprint and skips only the second post-init read on a warm cache hit. The reported update/commit deltas are directional only: one arm per condition, sequential order, and no stationary control. Process peak RSS was 216,632 vs 211,092 KiB and `/proc` write bytes 60,342,272 vs 60,981,248; neither small difference is evidence of a memory or physical-device-I/O improvement. The weak registry does not strongly retain closed analyzers; strong Index owners share one immutable Jieba snapshot, which is removed after the last owner releases it. The fingerprint's dictionary bytes are still materialized/read on every open.

Existing cache lifecycle evidence covers same-size/same-mtime content mutation **before** open (digest mismatch, rejection before cache lookup), cross-generation sharing, expired weak entries, and a cold `exec` reopen with the skip option. That cold process has no warm process-local cache: the test confirms two fingerprint passes and no skipped verification. Therefore a true process crash/restart cannot carry the candidate skip path into recovery. Clarification: a warm cache hit has a narrow interval after the first fingerprint and before the omitted second fingerprint in which a source-path change can be accepted. The reused analyzer remains tied to the pre-change content digest/snapshot, so persisted index semantics remain aligned with the manifest; however, the candidate no longer enforces the strict policy that the source path remain unchanged through the whole `Open`. Keep the option false by default unless that snapshot-linearized mutation contract is explicitly accepted. The focused `m4_analyzer_cache_lifecycle` test passed 1/1 after the cold-cache assertion; no additional test or benchmark ran in this continuation.

## Next action and stop condition

Do not bypass or weaken either failed gate. A future launch requires a fresh read-only preflight with both `MemAvailable ≥5,477,762 KiB` and `/var/tmp` free space `≥18,219,000,000 B`, plus all other unchanged checks. Do not poll or retry automatically. If capacity later recovers, confirm the one-run authorization still applies, then obtain/validate an operation-bound plan consistent with the then-current runner contract before executing the 0–6 phase sweep. Any threshold or policy change requires a separate explicit owner decision.

This was a preflight refusal, not a completed diagnostic and not evidence for or against the scalar-count hypothesis. No native runtime, production, schema, durability, benchmark-policy, or cgroup-guard changes were made.

## Evidence checksums

See `SHA256SUMS.txt` for the report and raw preflight files.

---

*Research-only M4; no promotion, qualification, or production-readiness claim.*
