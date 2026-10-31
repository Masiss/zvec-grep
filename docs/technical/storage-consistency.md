# Storage, identity, consistency, and recovery

[Engineering guide](README.md) · [Runtime flows](runtime-flows.md) · [Interfaces](interfaces-compatibility.md)

The TypeScript and Rust implementations have separate on-disk layouts and must not be assumed to read or migrate one another's indexes. Always identify which executable created an index before proposing a format change or recovery procedure.

## TypeScript index

The TypeScript workspace index is rooted at `<workspace>/.zvec-grep/`. The current user guide identifies `manifest.json`, `files.zvec`, and `index.zvec` as its principal files. `src/engine/manifest.ts` owns manifest handling; `src/engine/storage/layout.ts` owns path/layout decisions; `src/engine/storage/zvec.ts` implements collection operations. Index settings, including embedding identity and selection configuration, affect whether an existing index can be reused. Consult `docs/04-pipeline.md` and `docs/07-embedding.md` before changing those compatibility rules.

The file and fragment records are the bridge from search hits back to workspace paths and source content. Updates must keep their identifiers, metadata, and stored index contents coherent. Atomic-file and lock helpers in `src/engine/utils/` are part of persistence safety; use the existing utility rather than introducing an ad hoc write path.

Global configuration and daemon state are separate from per-workspace index data. User-visible defaults and environment overrides are described in `docs/06-server.md` and `docs/07-embedding.md`; paths and serialization are defined by the corresponding `src/engine/config.ts`, `src/daemon/config.ts`, and authorization store implementation.

## Rust index

Rust has its own storage implementation under `rust/crates/zg-engine/src/storage/` and workspace coordination under `workspace/`. The storage tree separates directories, file metadata, fragments, collection/store access, and read sessions. The schema and authoritative call paths are in those modules and the owning types—not in the TypeScript manifest.

Current architecture uses a publication/visibility barrier: newly staged index contents are not searchable until publication metadata makes them visible. Staging, generation/snapshot handling, and recovery/retry are correctness-critical; inspect `storage/staging.rs`, `storage/read_session/`, `storage/store/`, `workspace/`, and the recovery tests before altering them. The Rust architecture explicitly does **not** claim a cross-collection native transaction. It relies on staging, visibility control, and idempotent recovery where publication spans independently persisted records. See `rust/docs/m1-handoff.md` for the milestone evidence and limitations.

Identifiers and generation/revision values are not interchangeable: a document/file identity, fragment identity, workspace revision, and read snapshot serve different purposes. Preserve the mapping from candidate fragment to file/source range through hydration, and do not reuse identities unless the owning type/storage contract explicitly permits it.

## M4 research format is separate

`research/m4-native/` has its own experimental segment, manifest, tombstone/statistics, and WAL design. It is a standalone research tree and is not the TypeScript or Rust production index format. Read `research/m4-native/README.md`, `research/m4-native/AGENTS.md`, and the dated report/checkpoint before handling its artifacts. Its write-ahead log and compaction evidence must not be projected onto either application storage implementation.

## Consistency and failure questions to answer before edits

For any storage change, establish these answers from source/tests:

1. **Identity:** What is the stable key for a file, fragment, model/configuration, or generation? Which values are derived and may change?
2. **Visibility:** At what exact operation does a write become searchable? Can reads observe staged, partial, or mixed-generation data?
3. **Ordering:** What must be durable before the manifest/publication marker points at it? What happens if the process stops at each boundary?
4. **Recovery:** Is replay idempotent? Are orphaned/staged records retried, discarded, or hidden? What is the failure scope?
5. **Snapshot:** Do candidate lookup and result hydration share a read session or equivalent consistency boundary?
6. **Compatibility:** Can old indexes be opened? Is schema evolution supported, or must an incompatible change rebuild/drop explicitly?
7. **Cleanup:** Does cancellation/error clean only operation-owned staging, and can concurrent readers/writers still access authoritative state?

A passing unit test is evidence for its exercised interruption point; it does not establish power-loss durability, multi-process safety, or transactional atomicity unless the test actually covers that property.

## Change and verification map

| Change | Start at | Required adjacent inspection |
| --- | --- | --- |
| TS manifest/layout | `src/engine/manifest.ts`, `src/engine/storage/layout.ts` | `src/engine/storage/zvec.ts`, config compatibility and tests |
| TS file/fragment persistence | `src/engine/storage/` | indexing pipeline, search hydration, atomic-file/lock helpers, reopen tests |
| Rust schema/publication | `rust/crates/zg-engine/src/storage/` | `workspace/`, indexed pipeline, generation/read-session and recovery tests |
| Rust watcher freshness | `rust/crates/zg-daemon/src/workspace_runtime/` | scheduler, protocol/status events, runtime lifecycle tests |
| M4 WAL/segments | `research/m4-native/src/`, `include/m4/` | M4 policy, fault injection, recovery and bounded-operation evidence |

Never delete or rewrite user index files as part of a documentation or test operation. Format migration and destructive recovery need an explicitly defined compatibility and backup story.