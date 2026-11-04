# WAL record bounds, recovery envelope, and integration readiness — 2026-10-26

## Scope and disposition

Source review and deterministic correctness only. No benchmark or production/integration run was launched. The 24-hour benchmark attestation has expired; the registered resource/containment policy was not changed or bypassed. This closes the **per-record** unbounded Upsert path but does not establish an absolute WAL-file/recovery-memory budget. The latter remains an owner-level resource-budget choice, described below.

## Input limits and write/ACK path

`zg-engine` does not currently integrate M4 (see `research/m4-native/AGENTS.md`). Its source scanner's default limit for code files is 1 MiB; data files default to 16 MiB, images to 10 MiB, and other formats to 256 MiB. Whole-source fallback parsers are restricted to 8 MiB, while UTF-8 text may use streaming. `workspace.scan.max_file_size_bytes` can override these defaults. These are scanner policies, not a guarantee that an M4 watcher supplies one document per file or honors any particular maximum.

For the M4 API, the path is:

1. `Index::Upsert` / `ApplyBatch` / writer-group input is checked before document normalization/copy and before writer-state mutation.
2. `NormalizeDocument` copies the text/scalars and expands array-u32 values to tagged scalar values; the preflight estimate includes primary key, text, field/value strings, array presence markers, expanded values, count/length varints, and fixed DocIds. Array duplicates are conservatively counted before normalization.
3. The exact serialized mutation is checked again before WAL append. A single mutation is limited to 2 MiB; an atomic transaction frame is limited to 8 MiB (and 256 mutations). WAL framing has a 32-bit format length, but the application now refuses frames above 8 MiB.
4. WAL append writes the frame and, when `fsync_wal` is enabled, fsyncs it before the mutation becomes query-visible/receives a success ACK. The ordinary Upsert/Delete sequence number is now advanced only after successful append. ApplyBatch remains one durable WAL frame and validates all replay children before applying any child.
5. Query-visible insertion retains the document text/metadata and tokenized postings in the active delta. Automatic flush is triggered by the configured delta thresholds; successful publication orders segment, stats, tombstone, manifest, then WAL reset. Compaction itself does not independently checkpoint the WAL.

The 2 MiB default is grounded in the scanner's 1 MiB default code-file ceiling with 100% record/metadata/normalization headroom; all serialized metadata and normalized arrays consume that same bound. It is a standalone M4 research API limit, not a production watcher guarantee. An explicit scanner override above 1 MiB, full-file document ingestion, or a future integration must reconcile its document-fragment size with this cap before rollout.

## Implemented safety behavior

- Reject an over-limit single Upsert/Delete before normalization, WAL append, DocId allocation, sequence consumption, or query-visible mutation.
- Reject an oversized child or oversized atomic batch before normalization/WAL append; replay keeps child views into the bounded frame instead of copying the entire batch's child records for validation.
- Fail closed on valid legacy raw/compressed frames above the 8 MiB frame limit and on legacy mutation/child records above 2 MiB. Return an explicit recovery error and leave the WAL bytes intact; do not silently truncate or skip durable data. Ordinary old frames within the new limits keep their old format and replay path.
- Reject overflowing 64-bit varints instead of allowing the tenth byte to wrap the decoded length/count.
- Guard the WAL file-size-to-`size_t` conversion, and reject sequence exhaustion rather than wrapping the next sequence.
- Existing torn-tail repair remains limited to incomplete suffixes. Complete malformed/oversized records fail closed. A failed WAL append does not consume the direct Upsert/Delete sequence; append/fsync uncertainty still requires reopen as before.

The deterministic regression covers the exact 2 MiB serialized boundary, one byte over, no mutation/no WAL growth on rejection, a later valid write with contiguous reopen, rejection of an oversized child in an atomic batch with no prefix visible, an overflowing legacy varint, and a valid-checksum oversized legacy record whose bytes remain unchanged after failed open. Existing full correctness coverage also exercises WAL crash/reopen, torn suffixes, legacy replay, batch atomicity, and flush publication failure/recovery.

## Recovery allocation and WAL growth envelope

The current replay implementation reads the whole WAL file into one `std::string`, then retains decoded payload strings in a record vector. The archived profiler provides direct evidence for the amplification: a 16,080,800-byte WAL decoded 16,073,600 payload bytes and reported 32,174,900 bytes tracked peak live capacity, before all allocator/index-state costs. The corresponding contained operation observed a 451,944,448-byte cgroup peak under its 536,870,912-byte memory limit and zero swap. Those values are exploratory, process/cgroup-wide, and not a replay-only or production qualification.

With the **default** `max_delta_bytes=8 MiB`, the mutation serializers count raw text and metadata in the active delta; a single record can overshoot by at most 2 MiB. ApplyBatch can add at most 8 MiB before its auto-flush attempt, so the ordinary default path has an expected pre-checkpoint WAL envelope below about 16 MiB plus frame headers. Per-Upsert group commit is capped at 6 MiB and also checks active delta thresholds. A failed threshold flush blocks subsequent ordinary mutations; group commit marks reopen-required after a durable/flush failure. Successful flush resets the WAL only after the manifest is durable.

This is a **conditional operational envelope, not a hard file limit**. `max_delta_bytes` is configurable and has been deliberately raised to 1 GiB in existing research fixtures; a manually accumulated or externally supplied valid WAL can also exceed the default envelope. There is no maximum WAL-file size today. Consequently, `Wal::Replay` can still attempt a whole-file allocation proportional to an arbitrarily large WAL before it examines the bounded frame length. Recovery memory therefore has no unconditional upper bound. The 2 MiB record cap closes the single-Upsert/normalization hazard but does not make arbitrary WAL-file replay bounded.

### Owner budget choices for an absolute recovery cap

1. **32 MiB WAL file cap (recommended starting point for M4 discussion):** twice the largest archived 16.08 MB replay fixture; at the observed roughly 2× tracked replay amplification, budget about 64 MiB for encoded+decoded WAL capacity before delta/index/allocator overhead. This is a hard compatibility boundary: writes must stop or require a successful checkpoint before crossing it, and an older file above the cap must fail closed with its bytes preserved. It must not be implemented as truncation.
2. **64 MiB WAL file cap:** more tolerance for raised delta thresholds/legacy data, but roughly 128 MiB tracked WAL capacity under the same linear model and correspondingly less headroom under the policy's 512 MiB cgroup ceiling. Same fail-closed migration behavior.
3. **No absolute file cap:** keep the current default-threshold envelope and the new per-frame/per-mutation limits, explicitly restrict supported configurations to bounded delta settings, and accept that manually supplied/oversized WAL files have no safe memory upper bound. This avoids a new compatibility cliff but does not satisfy an unconditional recovery-memory bound.

A hard cap needs an append-side projected-size check as well as a pre-allocation replay check. On capacity exhaustion, the implementation must either safely checkpoint the already-applied state before the next append or return an error requiring an explicit flush; it cannot ACK a new mutation that cannot later be replayed. Choose this budget before claiming bounded recovery for arbitrary options/files. No such total-file cap was added without that owner budget.

## Integration and full-lifecycle comparison readiness

M4 remains standalone; no watcher-to-M4 path exists here to test end-to-end batching, snapshot handoff, or rollback. The integration contract to qualify is:

- Convert each final scanner snapshot into a deterministic mutation trace (per-path Upsert/Delete, repeated-event coalescing, rename as delete+upsert), with source path, document size, metadata and tokenizer identity recorded. Never truncate a >2 MiB M4 mutation; surface it as an explicit unsupported/too-large item or split it using a separately specified document model.
- Use atomic `ApplyBatch` only for watcher batches within 256 mutations/8 MiB; reject and split/retry the *unacknowledged* suffix without replaying already-ACKed work. Keep per-Upsert writer-group mode out of snapshot publication because readers can observe sequential prefixes.
- For every WAL-enabled successful ACK require `fsync_wal=true`; after process death/reopen, compare final path→document versions, delete liveness, scalar filters and retrieval results against the scanner snapshot. Treat a flush error after durable append as an uncertain outcome and reconcile on reopen before retrying.
- Rollback must leave the previous production index untouched: M4 currently has no in-place production rollback contract. Any future adapter needs a separately owned M4 data root, manifest-generation/snapshot switch, offline parity check, and an explicit abort/fallback path.

Prepare the native-vs-M4 lifecycle run from one immutable mutation trace and identical initial corpus, metadata, tokenizer and query oracle. Include initial ingest, the same updates/deletes/renames, durable ACK boundaries, native `optimize` at its actual schedule, and M4 auto/manual `Flush` plus `CompactNow` at its actual schedule. Compare only after equivalent logical snapshots; record retrieval/filter parity, final live-document set, WAL size/replay peak, maintenance bytes, total physical/logical writes, CPU/wall time and peak memory. Existing native/M4 paired code explicitly does not call native `optimize` or M4 `CompactNow`, so it is not evidence for this question. Do no lifecycle benchmark until a fresh approved containment/attestation profile exists; no consumed operation was retried.

## Verification

- `m4_correctness`: **21,094 checks, 0 failures**, including the new WAL record-bound regression.
- Build scratch and test data are under `research/m4-native/.work/`; no benchmark was launched.
- LSP diagnostics confirmed the changed header and utility file clean; the large `index.cc` and correctness test exceeded the LSP server's 5,000-line limit and were not LSP-checked. C++ build/test compiled and exercised them.
- No codec/framing experiment, production edit, global harness/policy change, index rewrite, or push was made.

## Owner decision and next action

Owner decision (2026-10-26): **do not add an absolute WAL-file cap**. Keep the new per-record/frame safeguards and accept only the conditional/default-threshold operational envelope. This avoids a hard compatibility boundary for existing files, but arbitrary oversized or externally supplied WAL recovery remains unbounded because replay reads the whole file. Do not describe recovery as absolutely bounded or qualified under non-default limits.

Next non-benchmark work is the integration adapter contract/test seam. A matched native-optimize vs M4 flush/compaction run remains unlaunched until a fresh approved containment/attestation profile exists.
