# zvec-grep engineering guide

This is the repository-wide technical entry point for engineers and coding agents. It maps the implemented TypeScript and Rust systems, explains their boundaries and invariants, and points to source and verification paths. It complements the user-facing guides in [`docs/README.md`](../README.md); it does not replace source code as the authority for current behavior.

## Start with the system map

1. [System map and module ownership](system-map.md) — repository layout, implementation boundaries, dependency direction, and source map.
2. [Runtime and data flows](runtime-flows.md) — process startup, CLI/MCP requests, indexing, watcher updates, search, and result hydration.
3. [Storage and consistency](storage-consistency.md) — persisted state, identities, publication, failure/recovery, and native storage boundaries.
4. [Retrieval and extraction](retrieval.md) — discovery, extraction, embedding, lexical/vector recall, filtering, fusion, ranking, and direct grep.
5. [Interfaces and compatibility](interfaces-compatibility.md) — CLI, MCP/daemon contracts, engine/native seams, error handling, and the Rust rewrite oracle.
6. [Development and verification](development.md) — build/test entry points, CI map, and how to verify changes at the owning boundary.
7. [Rust rewrite roadmap](rust-rewrite-roadmap.md) — current authority, evidence model, and completion criteria for replacing the TypeScript implementation.
8. [Research components](research.md) — what is exploratory, where its evidence lives, and what is not production behavior.
9. [Optimization and upgrade plan — 2026-10-27](optimization-upgrade-plan-20261027.md) — proposed phases, alternatives, evidence gates, and dependencies across native optimization, M4, retrieval, Rust parity, and release (Vietnamese).
10. [P0 baseline and WIP checklist — 2026-10-27](optimization-p0-baseline-20261027.md) — working-tree revision, diff manifest, focused verification, known blockers, and current evidence disposition.
11. [P1 baseline and acceptance — 2026-10-27](optimization-p1-baseline-20261027.md) — offline lifecycle evidence, seven native attempts; v7 is the valid exploratory baseline (query/freshness SLO PASS), v6 remains separately INCONCLUSIVE; includes the test-only reopen fix and 17 distinct focused P2 correctness cases, with the partial-delete regression additionally verified through both the ordinary Rust path and a test-only Linux C-ABI interposer.
12. [P2 baseline disposition — 2026-10-27](optimization-p2-baseline-20261027.md) — correctness gate, scheduler diagnostic, completed exploratory optimizer-thread A/B, SLO outcome, and baseline-retained decision.
13. [Current-tree native lifecycle profile — 2026-10-27](optimization-native-profile-20261027.md) — contained per-finalization timings and optimizer-thread A/B; 4 threads gave only a small finalize gain and failed query p95 SLO in the measured pair.

14. [RAG evidence upgrade plan — 2026-10-28](../rag/README.md) — proposed local-CPU evidence architecture, bounded external-agent self-check loop, phases/gates, and source-backed HRM2.0 pilot (Vietnamese; not implemented behavior).

## Source-of-truth rules

- **Implementation claims:** verify against the current source and tests named in each page. A module name or old design document is not evidence that a feature is active.
- **Intent and historical decisions:** use dated plans, handoffs, ADRs, and research reports as evidence of why a choice was made, not as proof that it remains implemented.
- **Public behavior:** verify CLI help, exported API types, daemon/MCP schemas, compatibility fixtures, and tests. User guides explain intended usage but can lag source.
- **Rewrite status:** [`rust-rewrite-roadmap.md`](rust-rewrite-roadmap.md) is the navigation and completion-criteria page; per-feature parity claims remain evidence-linked and must not be inferred from milestone labels alone.
- **Research:** experiments under `research/` are not production dependencies unless a source manifest/build path proves otherwise. In particular, `research/m4-native/` is a standalone prototype.

If documentation conflicts with source, record the discrepancy and correct the documentation or source deliberately; do not silently promote a proposal into an invariant.

## Fast source navigation

| Concern | TypeScript implementation | Rust implementation |
| --- | --- | --- |
| Public package / process entry | `src/index.ts`, `src/cli/index.ts` | `rust/crates/zg/src/main.rs`, `rust/crates/zg-cli/src/lib.rs` |
| Engine facade | `src/engine/service/zvec-grep.ts` | `rust/crates/zg-engine/src/lib.rs`, `service.rs`, `api/` |
| CLI routing | `src/cli/`, `src/client/` | `rust/crates/zg-cli/`, `rust/crates/zg/src/main.rs` |
| Daemon and watchers | `src/daemon/`, `src/mcp/http-transport.ts` | `rust/crates/zg-daemon/`, `rust/crates/zg-host-native/` |
| MCP boundary | `src/mcp/` | `rust/crates/zg-transport-mcp/` |
| Indexing and extraction | `src/engine/pipeline/indexing/`, `src/engine/extraction/` | `rust/crates/zg-engine/src/pipelines/indexing/`, `extraction/` |
| Indexed search | `src/engine/pipeline/search/`, `src/engine/service/lexical.ts` | `rust/crates/zg-engine/src/pipelines/indexed_search/` |
| Persistence | `src/engine/storage/`, `src/engine/manifest.ts` | `rust/crates/zg-engine/src/storage/`, `workspace/` |
| Models and authorization | `src/engine/models/`, `src/authorization/` | `rust/crates/zg-engine/src/models/`, `authorization/` |
| Tests / parity | `test/` | `rust/crates/*/tests`, `rust/compat/`, crate unit tests |

## Read by task

- Change user-visible command behavior: read the relevant page in `docs/`, then trace CLI → client/daemon → engine and update compatibility tests.
- Change Rust behavior: start at [`rust/ARCHITECTURE.md`](../../rust/ARCHITECTURE.md), then the owning crate/source and tests. Preserve the crate boundaries in [`rust/CONTRIBUTING.md`](../../rust/CONTRIBUTING.md).
- Change persisted data or publication: read [Storage and consistency](storage-consistency.md) and both implementations' recovery tests before editing.
- Change ranking, candidate generation, or filtering: read [Retrieval and extraction](retrieval.md), then compare route-specific tests and fixtures.
- Change experimental native-index research: read [Research components](research.md) and `research/m4-native/AGENTS.md`; do not treat prototype evidence as production qualification.

## Scope and maintenance

This guide covers the TypeScript package at repository root, the Rust workspace under `rust/`, native dependencies/bindings, tests and verification, and research components. It describes present architecture and known evidence limits. It is not a promise that the two implementations are already feature-equivalent. Update the nearest topic page and source map when a crate/module boundary or cross-component flow changes.
