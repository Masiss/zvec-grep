# M4 research prototype guidance

## Scope

M4 is a standalone research prototype under `research/m4-native/`. It is not integrated with `zvec`, `zg-engine`, the daemon, production index formats, or production APIs.

- Keep M4 work inside `research/m4-native/` unless the owner explicitly expands scope. Do not modify production Rust/Zvec code, production schema/index data, deployment, migration, or backfill as part of M4 research.
- This file contains durable design and workflow constraints only. Current benchmark readiness, dated next actions, host snapshots, and experiment outcomes belong in the canonical handoff (`rust/docs/m4-incremental-native-index.md`) and durable result artifacts, not here.
- The machine-readable operation policy is `docs/benchmark-policy.json`; it is the sole source of executable resource-guard values and classifications. `docs/benchmark-policy.md` explains the policy but is not parsed by enforcement code. Runners and orchestrators must load the JSON source rather than duplicate threshold literals. Changes require updating the explanation, regression tests, result record, and canonical handoff as appropriate; threshold changes require explicit owner approval.

## Data and ranking invariants

- FTS and INVERT share one `DocId`/tombstone catalog. `DocId`s are never reused; update means tombstone old and append new.
- Runs are immutable. Flush writes format v1; compaction writes format v2. Readers accept both. Any incompatible layout change requires a version bump and independent reader coverage.
- Store raw `max_tf`/`min_dl` per FTS block. Compute IDF, `N_live`, `df_live`, and `avg_dl` at query time; never persist an IDF-weighted bound.
- Preserve BM25 semantics, OR/AND/phrase FTS, scalar EQ/range, hybrid ranking, result ordering, and the public search API.

## Compaction and durability

- Compaction is a streaming k-way merge; do not materialize merged postings, values, or docs in RAM.
- Enforce `compaction_memory_limit_bytes`, charging posting buffers, block metadata, dictionaries, and fixed I/O buffers. On budget failure, abort before manifest publish; old runs remain searchable.
- v2 compaction writes directly into its final file; do not reintroduce temporary payload/meta/docs sections or a second output copy.
- Commit order: segment (fsync + rename) → stats → tombstones → atomic manifest → WAL truncate. Garbage-collect old files only after the manifest is durable.
- Segment checksum covers data from the lowest section offset to EOF. v2 section order is FTS, docs, INVERT; do not assume docs-first.
- Temporary output must be RAII-cleaned on failure. `M4_FAULT` hooks are research-only and must not be set in normal runs.

## Workflow and verification

- Keep agent-controlled temporary, build, sanitizer, and disposable scratch artifacts under `research/m4-native/.work/{tmp,build,sanitizer,scratch}/` (the M4 project working directory). Set `TMPDIR`, `TMP`, and `TEMP` per command/job to `.work/tmp/` where compatible; direct build outputs to `.work/build/` or `.work/sanitizer/`. Do not change system-wide temp configuration.
- Distinguish disposable outputs from retained evidence. Put benchmark receipts, raw evidence, provenance, and binaries needed to reproduce or substantiate a result under `results/` with checksums/manifests. Clean task-owned disposable artifacts when no longer needed; do not leave piles of binaries/build trees in `/tmp`.
- Do not touch unrelated application/process data, unknown files, or artifacts without verified ownership. Preserve special filesystem semantics required by a test/benchmark; project-local TMPDIR must not silently change benchmark storage semantics.
- Read `README.md`, relevant format writer/reader and oracle before changing layout. Add/update oracle or regression coverage first for behavior changes; update format documentation when layouts change.
- Run the focused correctness checks first, then required sanitizers/builds, then only explicitly authorized benchmarks. Do not treat benchmark readiness or a historical handoff next-action as authorization.
- Do not bypass or change a guard, auto-retry a blocked operation, launch a dependent arm after failed recovery/telemetry/cleanup, or delete unreviewed files to create resources. See `docs/benchmark-policy.md` for interpretation and JSON policy for executable values.
- Keep result artifacts reproducible: exact command, source/binary identity where relevant, receipts, and evidence class. Distinguish logical from physical writes and measurement-quality limitations.
- Update the canonical handoff with evidence after material changes. A runner receipt is operation-level; it must leave whole-mission status unevaluated. Mission status is determined by the active user DoD, not a runner or a single green check.

## Known implementation traps

- FTS v2 dictionary field order is `payload_offset` then `meta_offset`; v1 uses `first_block`/`block_count`.
- v2 section order is FTS → docs → INVERT so docs can use final term ids; checksum starts at the FTS section.
- Save `stats->fts_terms` before clearing the in-memory FTS dictionary.
- `fts_dict`/`term_to_id` are O(vocabulary) metadata and must remain charged to the compaction budget.
- `M4_FAULT=after_rename` deliberately leaves an orphan final file simulating a crash after rename. It must be ignored by the old manifest; do not delete the published file to “fix” it.
- Debt policy runs only after `Flush`, not per mutation; preserve one-merge-per-flush, cooldown, minimum-runs, and input-cap guards. Default remains OFF for the baseline.

Build and benchmark commands and current evidence are maintained in `README.md`, `results/RESULTS.md`, and the canonical handoff; do not copy dated host state or current gate outcomes into this durable instruction file.
