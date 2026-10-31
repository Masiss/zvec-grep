# System map and module ownership

[Engineering guide](README.md) · [Runtime flows](runtime-flows.md) · [Storage](storage-consistency.md)

## Product and implementation boundaries

The repository contains two product implementations plus research and evaluation code:

```text
TypeScript package (repository root)                 Rust rewrite (`rust/`)
CLI / public JS API                                   CLI (`zg`, `zg-cli`)
  └─ client mode router                                 ├─ daemon/MCP (`zg-daemon`)
      ├─ local daemon / MCP                             ├─ protocol (`zg-daemon-protocol`)
      └─ direct engine                                  ├─ MCP adapter (`zg-transport-mcp`)
           ├─ indexing/search                            └─ engine (`zg-engine`)
           ├─ extraction/models                                ├─ host adapters (`zg-host-native`)
           └─ zvec storage/native binding                       ├─ storage/models/pipelines
                                                                   └─ zvec-rust native API

Standalone native research: `research/m4-native/` (not a production runtime backend); Rust has an explicit research/test integration feature
Evaluation/fixtures: `test/`, `rust/compat/`, crate tests, benchmark tooling
```

The root package is the shipped TypeScript implementation today and the compatibility oracle for the rewrite. Rust is a separate Cargo workspace and has its own CLI/daemon/engine/native dependency graph; it does not call into the TypeScript runtime. See [`rust/ARCHITECTURE.md`](../../rust/ARCHITECTURE.md) for the detailed Rust design and [`rust/README.md`](../../rust/README.md) for its local build and usage instructions.

`research/m4-native/` is a standalone C++17 research prototype with its own CMake build and containment/benchmark harness. It is not wired as a production runtime backend. Rust contains an explicit `m4-research-integration` test/research path (see `zg-engine` feature configuration and `m4_integration_tests`); that path does not make the prototype a production dependency. Integration, migration, or production qualification requires explicit evidence and should not be inferred from research status or test count.

## TypeScript package (`src/`)

| Area | Responsibility / boundary | Entry points to inspect |
| --- | --- | --- |
| `src/index.ts` | Public package exports; keep public API additions explicit | `src/index.ts` |
| `src/cli/` | Argument parsing, command orchestration, rendering, managed ripgrep, installation | `src/cli/index.ts`, `args.ts`, `commands.ts` |
| `src/client/` | Selects server/direct execution and applies client-side search policy | `mode-router.ts`, `daemon-client.ts`, `search-policy.ts` |
| `src/daemon/` | Local HTTP/MCP hosting, runtime lifecycle, workspace coordination, scheduling, watching, logging | `http-server.ts`, `runtime-manager.ts`, `runtime.ts`, `index-coordinator.ts`, `job-scheduler.ts`, `watch-manager.ts` |
| `src/mcp/` | MCP schemas/tools, request state, transport, progress, result presentation | `toolset.ts`, `tools.ts`, `schemas.ts`, `http-transport.ts` |
| `src/engine/service/` | Engine-level operations and coordination | `zvec-grep.ts`, `workspace-index.ts`, `lexical.ts` |
| `src/engine/pipeline/` | Workspace file discovery/indexing and indexed search pipelines | `indexing/index.ts`, `indexing/scanner/`, `search/index.ts` |
| `src/engine/extraction/` | Format-specific extraction and chunk/vector-content preparation | `index.ts`, `code/`, `markdown/`, `text/`, `image/` |
| `src/engine/models/` | Model catalog/resolution, local/remote embedding and ranking backends, artifact and concurrency management | `catalog.ts`, `resolution.ts`, `embeddings.ts`, `ranking.ts`, `backends/` |
| `src/engine/storage/` | Index layout and zvec collection operations | `index.ts`, `layout.ts`, `zvec.ts`; also `src/engine/manifest.ts` |
| `src/authorization/` | Consent planning, prompt, grants, target binding, persistence | `manager.ts`, `planner.ts`, `store.ts` |
| `src/observability/` | Trace context carried through operations | `trace-context.ts` |

Runtime dependencies are declared in root `package.json`. Notable boundaries include `@zvec/zvec` for vector/lexical native storage, managed ripgrep via `@vscode/ripgrep`, Tree-sitter grammars/runtime for structural extraction, and local embedding runtimes. Optional `node-llama-cpp` is not a required dependency. Confirm exact versions and feature flags in the manifest rather than copying them into long-lived prose.

## Rust workspace crates

`rust/Cargo.toml` defines the workspace; `rust/crates/*/Cargo.toml` is the source of dependency truth. The intended dependency direction is from adapters toward the engine, not from engine toward CLI/daemon transports.

| Crate | Responsibility | Main source |
| --- | --- | --- |
| `zg` | Executable and process/runtime assembly | `crates/zg/src/main.rs` |
| `zg-cli` | CLI types, argument handling, authorization/install helpers, rendering | `crates/zg-cli/src/lib.rs` and sibling modules |
| `zg-engine` | Typed engine API, domain model, extraction, models, indexing/search, storage, workspace | `crates/zg-engine/src/lib.rs`, `api/`, `service.rs`, subsystem directories |
| `zg-daemon` | HTTP/MCP daemon lifecycle, auth, runtime/workspace scheduling and jobs | `crates/zg-daemon/src/lib.rs`, `controller.rs`, `runtime.rs`, `workspace_runtime/` |
| `zg-daemon-protocol` | Typed daemon command/reply framing and wire DTOs | `crates/zg-daemon-protocol/src/lib.rs` |
| `zg-host-native` | Host filesystem scanner/watcher abstractions | `crates/zg-host-native/src/{scanner,watcher,api}.rs` |
| `zg-transport-mcp` | MCP tools/schemas and presentation adapters | `crates/zg-transport-mcp/src/lib.rs` |
| `zg-memory` | Shared process memory/pressure observation primitives | `crates/zg-memory/src/lib.rs` |
| `zg-testkit` | Shared compatibility fixture loading for tests | `crates/zg-testkit/src/lib.rs` |
| `zg-engine/macros` | Engine-local procedural macros | `crates/zg-engine/macros/` |

The engine keeps implementation modules private where possible and exposes typed API through `zg_engine::api` and `ZvecGrep`. Daemon protocol and MCP types belong to their owning crates. Rust workspace lint policy forbids unsafe Rust and denies `unwrap`, `todo!`, and `unimplemented!`; native C/C++ dependency internals remain outside that Rust lint boundary.

## Native and build boundaries

- **TypeScript native storage/model stack:** declared by root `package.json`; the JS engine calls packages and backend libraries through their published APIs.
- **Rust native stack:** `zvec-rust` / `zvec-rust-build` are workspace-pinned dependencies; `zg-engine` owns the native storage integration. Inspect `build.rs` and the owning crate manifest when changing linking/build behavior.
- **M4 experiment:** `research/m4-native/CMakeLists.txt`, `research/m4-native/include`, `src`, `rust-bridge`, and tools build an independent prototype.
- **CI:** `.github/workflows/ci.yml`, `rust-ci.yml`, `rust-models-ci.yml`, `rust-package-build.yml`, publish/security workflows and retrieval/benchmark workflows encode separate validation and release boundaries. A workflow's existence does not imply a production deployment path.

## Tests and evidence map

- TypeScript: `test/*.test.mjs`, `test/unit/`, `test/integration/`, `test/e2e/`, and `test/package/`; scripts are declared in root `package.json`.
- Rust: unit tests inside crates, integration tests under `rust/crates/*/tests/`, cross-crate fixtures in `rust/compat/`, and workspace commands in `rust/scripts/check.sh`.
- Native M4: C++ and bridge tests under `research/m4-native/tests/` and `rust-bridge/tests/`; harness and policy under `research/m4-native/tools/` and `docs/`.
- Benchmarks and retrieval evaluations are evidence about only the exact workload, binaries, and protocol recorded in their reports. They do not establish general parity or production readiness.

When moving a responsibility across a boundary, update this table, the detailed architecture page, and the owning crate/module docs. Confirm the graph from manifests and imports; do not use this hand-authored map as a substitute for dependency checks.