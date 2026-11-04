# Index RAM growth investigation — 2026-11-04

## Question and scope

Owner observed index RAM grow above 3 GB on an approximately 9k-file run.
This investigation reads the released V2 indexing path and retained local index
metadata, with a no-index default-options probe of the preserved RC native
library. It does not rerun 9k files, open native collections, rebuild an index,
modify the installed binary/daemon, or claim a benchmark/profile result.

The historical run's exact executable/library/model, RSS/PSS/cgroup metric and
phase timeline are not available. Those missing observations prevent assigning
an exact fraction of the reported 3 GB to each owner or proving a leak.

## Observed facts

1. V2 `storage/zvec.rs::initialize` calls `zvec_rust::initialize(None)` and
   `open_collection` sets read-only but no max-buffer or memory configuration.
   The app does not impose a total process-RAM budget for indexing.
2. A temporary C-API **options-object-only** probe of the RC native library
   (SHA-256 `5093c7d722304b0efaa7425f206456ca902dc377d5a0ae8056f1c5c010fe763e`)
   returned: memory setting 13,149,093,888 bytes (~12.25 GiB), collection buffer
   67,108,864 bytes (64 MiB), mmap enabled, max 10,000,000 docs per segment.
   The probe did not call `zvec_initialize` or create/open any collection. This
   is configuration metadata, not a memory-use measurement. A native memory
   setting is not a hard bound on Rust + tokenizer + model + Arrow + all RSS.
3. V2 `pipeline.rs::index_candidates_inner` bounds the number of scheduled
   embedding tasks and batches by fragment count/model batch size, not by
   prepared bytes. `prepare_candidate` reads/decodes and extracts an entire
   changed file before scheduling it. `PreparedFile` retains canonical entities,
   fragment input content, lexical text and optional reused f32 vectors.
4. `embed_prepared_files` clones embedding input content. For a large file,
   `embed_file` creates futures for every fragment batch, retains completed
   vector batches until the whole file completes, and commits only afterward.
   Concurrency limits therefore do not prove a small working-set byte bound.
5. `commit_file` creates native `Doc` representations for canonical entities and
   all fragment projections before replacing a file. Native accepts the data
   into four collections: directories, files, entities, fragments. Per-file
   success does not close those collections or prove buffers/caches were freed.
   Finalize/optimize occurs after all candidates and the retry pass. Optimize
   can add a separate temporary peak for building/compacting native indexes.
6. `FuturesUnordered` is drained and file outcomes are consumed/committed. No
   obvious Rust vector holding every processed file's embedding input forever
   was found. File-ID/directory maps scale with file/path count, but cannot by
   themselves explain gigabytes for roughly 9k paths.
7. Read-only inventory of retained `.zvec-grep/index/reconciliation/*.json`
   found 9,639 parseable JSON hints, 388,775 fragment entries, 106,539,303 hint
   bytes, 52 files with >1,024 fragments, and a largest file with 15,011 entries.
   Retained `index/schema.json` says dimension=256, metric=cosine. Those hinted
   vectors alone would be ~189.83 MiB at FP16, or ~379.66 MiB at f32 before
   container/graph overhead. Thus 3 GB cannot be explained by raw vectors alone.
8. The same **retained old `index/` directory** has approximately 642 MiB of
   entities, 1.7 GiB of fragments, 15 MiB of files and 128 MiB allocated to the
   reconciliation directory (`du` observations, NOT RAM). Whole `.zvec-grep`
   is ~2.6 GiB including a separate generation. The current manifest selects
   generation `ab545958-6dc1-4eb3-bd86-dcfc55aa9642`, so the old-directory counts
   are NOT asserted to describe the currently selected generation or the exact
   historical 9k run. Hints are auxiliary and not a canonical row census.
9. V2's built-in ignored directories do not include `results`. This repository
   has thousands of tracked research result JSON/text files, including large
   numerical reports; these can multiply fragment counts if the saved selection
   admits them. Actual per-fragment path attribution was not reconstructed, so
   do not claim all 388,775 entries came from results. No private source contents
   or credentials were printed.

## Native ownership evidence and caveat

The locally available 0.7.2-era native source, including the W4 patches, retains
Arrow record batches in `MemForwardStore::batches_` after writing them; simple
flush advances `flushed_batches_`, while close clears the batches. However,
`is_full` checks total bytes against the block buffer threshold and
`SegmentImpl::finish_memory_components` closes that memory store and switches
to persisted stores. Therefore it is **incorrect** to conclude that this one
Arrow batch vector is unbounded across the entire corpus merely from `push_back`.

Persisted block/index handles, mmap residency, vector graph construction, FTS
and scalar indexes, allocator high-water retention and native caches are
separate owners. The available native source explains plausible mechanisms but
is not proof that the preserved .8 native binary has an identical implementation.
Its actual public defaults were verified separately by the options-only probe.

## Diagnosis

**Confirmed design risks:** indexing has no application-level total byte budget;
large-file preparation and embedding staging can amplify transient memory; native
storage stays open and grows while indexing; full-text/canonical metadata and
index structures are substantial in addition to vectors; optimize is a separate
peak. The observed corpus is hundreds of thousands of fragments, not merely 9k
small records.

**Likely explanation of a gradual rise:** native index/cache/mmap residency plus
allocator high-water growth as more fragments are accepted, with additional
working-set amplification on large files. A large final spike would point more
toward optimize. This is not yet a measured allocation breakdown and does not
establish either a leak or that every byte is unavoidable.

The sidecar compaction and no-op startup work in the recovery lane do NOT by
themselves bound RAM for a first full 9k-file index. Do not present those ports as
a fix for this issue. The old V1 tree's separate byte-budget/streaming machinery
must not be assumed present in V2.

## Proportionate next verification / fix direction

- First clarify the original metric: process RES/RSS versus PSS, a whole
  daemon/cgroup, or overall system RAM; identify selected model and phase.
- On a separately approved bounded diagnostic (or a future owner-driven index),
  sample anonymous/file-backed RSS/PSS and cgroup anon/file/cache alongside
  files, entities/fragments, prepared/pending bytes, native collection state,
  model runtime count and finalize start/end. Record host state before/after.
  Do not start another full 9k run merely to get this information.
- Prioritize explicit byte-based backpressure and streaming/staged large-file
  handling, not only a concurrency count. Validate file/fragment parity and
  failure/cancel cleanup before promotion.
- Investigate native buffer/cache/index memory with the exact .8 library.
  Lowering a native limit or calling flush more often is not automatically a
  safe total-RAM fix; verify which allocations it controls and preserve native
  maintenance/single-writer/durability gates.
- Use source-focused saved selection to avoid accidental benchmark/build/result
  indexing when those artifacts are not part of the desired search corpus.
  Any exclusion/default change or explicit reindex remains a separate action;
  no live index or scan rules were changed by this investigation.

## Code anchors (V2 baseline c2347d5)

- `rust/crates/zg-engine/src/storage/zvec.rs:18,28,128` — default init/open/write.
- `rust/crates/zg-engine/src/pipelines/indexing/pipeline.rs:630,707,958,1070,1462,1592`
  — memory-bearing data, scheduling, preparation, clones and file commit.
- `rust/crates/zg-engine/src/storage/store/mod.rs:127,608,838` — collection
  lifetime, finalize and replacement.
- `rust/crates/zg-engine/src/storage/entities/mod.rs:191` and
  `storage/fragments/mod.rs:101` — canonical payload and lexical/vector docs.
- `rust/crates/zg-engine/src/file_selection/policy.rs:527` — baseline exclusions.

No source optimization, benchmark, package install, process restart or index
mutation was performed for this investigation. Recovery implementation continues
in its isolated writer lanes.
