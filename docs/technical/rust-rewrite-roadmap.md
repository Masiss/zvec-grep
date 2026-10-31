# Rust rewrite: status, evidence, and completion criteria

[Engineering guide](README.md) · [Interfaces and compatibility](interfaces-compatibility.md) · [Rust architecture](../../rust/ARCHITECTURE.md)

## Purpose and current authority

The Rust workspace under `rust/` is a rewrite of the root TypeScript/Node.js implementation. The TypeScript implementation remains the compatibility oracle during the rewrite. Rust is a separate runtime, not a Rust binding around the TypeScript engine. The implementation and crate boundaries are described in [`rust/ARCHITECTURE.md`](../../rust/ARCHITECTURE.md); developer rules are in [`rust/CONTRIBUTING.md`](../../rust/CONTRIBUTING.md).

**The rewrite is in progress; this repository does not currently document a complete, evidence-backed parity matrix or an approved cutover/retirement decision.** Existing Rust code, green tests, or completed milestone documents do not alone mean the rewrite is done. This page consolidates the evidence trail and makes completion claims falsifiable; it does not declare undocumented owner decisions.

## Evidence currently available

| Area | What repository evidence establishes | What it does not establish |
| --- | --- | --- |
| Rust architecture and crate boundaries | A Rust CLI/daemon/MCP/engine implementation exists; see `rust/ARCHITECTURE.md` and crate source. | Complete TypeScript feature/API parity or production cutover readiness. |
| Bounded indexing milestone | `rust/docs/m1-plan.md` records M1 as complete and points to `m1-handoff.md`; the handoff describes implementation evidence and its visibility-barrier/retry limits. | Cross-collection transactional atomicity or all storage-format migration cases. |
| Incremental native index work | `rust/docs/m3.2-incremental-native-index-design.md` and `m3.3-feasibility.md` record design/feasibility evidence and boundaries. | Authorization to apply a research proposal to production or a general parity result. |
| Compatibility apparatus | `rust/compat/`, `zg-testkit`, and workspace tests provide reusable cases and checks. | Exhaustive coverage of every CLI/MCP/API default, error, filesystem edge case, model backend, platform, and lifecycle. |
| M4 incremental native research | Current status and limits are indexed in [Research components](research.md) and `research/m4-native/results/ACTIVE-EXECUTION.md`. | Production integration or qualification; M4 is a separate prototype. |
| Build and test health | Commands and CI exist for the Rust workspace; see [Development](development.md). | Behavioral equivalence, operational performance, or safe migration of existing user indexes. |

The table is intentionally evidence-based. Add specific feature-level status only when the named compatibility cases and source paths have been reviewed; do not replace `unknown` with `pass` because a nearby test happens to pass.

## Workstreams that must be closed

Use these as a coverage inventory, not as a claim that every item is currently incomplete:

1. **Public surfaces:** Node package exports, CLI commands/defaults/output/exit codes, MCP tools and schemas, server lifecycle/configuration, and direct/server routing.
2. **Search behavior:** direct ripgrep, FTS, vector, hybrid fusion, multiple query groups, filters/refill, ordering, previews, and hydration/source ranges.
3. **Index lifecycle:** file selection and extraction, embedding/model resolution, incremental updates, status/freshness, cancellation, background scheduling, watcher recovery, and progress.
4. **Persistence:** Rust index layout and schema, reopen/recovery, interrupted publication, compatibility with indexes users already have, and explicit rebuild/drop/migration behavior.
5. **Models and security:** supported local/remote backends, artifacts/cache, endpoint resolution, authorization/grant binding, error handling, and credential hygiene.
6. **Runtime and operations:** install/package behavior, daemon authentication and loopback boundary, logging/diagnostics, resource/cancellation behavior, platform support, and upgrade/rollback.
7. **Quality and performance:** regression suites, compatibility corpus coverage, representative retrieval evaluation, bounded resource use, startup/steady-state behavior, and benchmark reproducibility.

## Completion criteria for a rewrite claim

A “Rust rewrite complete” claim should be supported by all of the following, with intentional differences documented and accepted:

- **Inventory:** every supported external TypeScript surface and behavior is enumerated, with owner/source/tests and an explicit supported/unsupported/deferred status.
- **Compatibility:** stable fixtures cover normal cases, defaults, ordering, paths, errors, filtering, cancellation, and mode differences; differential results are reviewed and unexplained differences are zero.
- **Storage:** compatibility or migration behavior is explicit for existing indexes/configuration, including failure/recovery, backups or rebuild requirements, and rollback. Do not assume formats are interchangeable.
- **Runtime:** daemon/MCP/CLI integration and supported platforms are verified at the actual process boundary, not only through engine unit tests.
- **Quality:** correctness, security, retrieval-quality, and resource/performance targets are defined and tested on representative workloads; limits and single-run uncertainty are disclosed.
- **Release/cutover:** install/update paths select the intended implementation, version/upgrade behavior is tested, rollback is practical, and the TypeScript retirement decision is explicit.
- **Documentation:** user guides, technical architecture, support/operations instructions, and release notes match the selected runtime and no longer imply unsupported parity.

These criteria do not grant authority to change public APIs, formats, deployment defaults, or cutover strategy. Owner decisions remain necessary wherever those product choices are not already specified.

## How to maintain parity evidence

For each feature, record a row in a future versioned matrix with: observable behavior; TypeScript source/tests; Rust source/tests; compatibility fixture(s); platform/model conditions; differences and disposition; evidence revision/date. Keep cases stable and machine-readable under `rust/compat/` where appropriate. A row must be marked **not assessed** until both implementations and the external behavior have been inspected.

When a milestone is complete, update both its dated handoff and this page's evidence table. Keep historical reports, but link the latest checkpoint prominently and label superseded decisions. Do not edit a historical report to make it look current.

## Navigation

- [Rust implementation map](../../rust/ARCHITECTURE.md)
- [Rust contributing and boundary rules](../../rust/CONTRIBUTING.md)
- [M1 plan and handoff](../../rust/docs/m1-plan.md) · [M1 handoff](../../rust/docs/m1-handoff.md)
- [M3.2 design review](../../rust/docs/m3.2-incremental-native-index-design.md) · [M3.3 feasibility](../../rust/docs/m3.3-feasibility.md)
- [Compatibility fixture guide](../../rust/compat/README.md)
- [Rust audit 2026-10-25](../../rust/docs/audit-20261025.md) (dated audit, not a whole-rewrite parity report)
- [Research components and current M4 boundary](research.md)