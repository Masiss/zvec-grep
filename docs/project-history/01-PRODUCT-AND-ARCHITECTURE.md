# 01 — Product and Architecture

## 1. Product goal

`zvec-grep` / `zg` is an **agent-friendly local workspace search** system:
index a workspace once, then return ranked source evidence through a CLI, MCP
tools and a resident daemon. The product goal is **less searching and less
context**, with local-first data and permissioned remote embeddings.

The README describes two maintained language stacks:

- the TypeScript/Node.js implementation (`src/`, `package.json`);
- the Rust workspace (`rust/`), introduced alongside in September 2026.

The recovery branch is the Rust/release line. The TypeScript implementation is
still present and is not “deleted history”; its README and package are the
Node-facing product.

## 2. External contract

```
Agent / human
   │
   ├── CLI (`zg`)
   ├── MCP tools (`zvec_grep_search`, focused/context tools)
   └── daemon HTTP / MCP (`127.0.0.1:7999` in the current runtime)
          │
          ▼
   zg-transport-mcp / zg-daemon-protocol
          │
          ▼
   ZvecGrep engine API
   context / files / paths / lookup_path / index / info / index_setup / drop
          │
          ├── lexical service (embedded grep; no external rg required)
          ├── indexed retrieval (FTS / vector / hybrid)
          ├── scanner + source extraction
          ├── embedding model runtimes
          └── native zvec storage
```

`rust/ARCHITECTURE.md` defines the engine as the public application boundary
(`rust/ARCHITECTURE.md:5-24`). Transport DTOs and MCP routing stay outside the
engine. Workspace roots are request state; one engine instance can serve many
roots, and the resident daemon owns a process-level `Arc<ZvecGrep>`.

## 3. Rust crate boundaries

| Crate | Responsibility |
|---|---|
| `zg-engine` | typed API; retrieval, indexing, extraction, models, storage, workspace |
| `zg-cli` / `zg` | argument parsing, terminal rendering, binary lifecycle |
| `zg-host-native` | native scanner/watcher |
| `zg-llama-native` | llama/ggml backend boundary |
| `zg-daemon` | resident process, HTTP endpoints, jobs, stdio bootstrap |
| `zg-daemon-protocol` | transport-only command/reply/progress DTOs |
| `zg-transport-mcp` | MCP schemas, validation, result presentation, consent |
| `zg-testkit` | fixtures/helpers |
| `zg-zvec-fp16` | FP16 vector support |

No global engine singleton; public methods call private services directly. The
daemon has one process-level engine, while CLI direct mode keeps resources for
one command.

## 4. Scanner, workspace and project scope

- Scanner and watcher are native (`zg-host-native`); workspace scan rules are
  persisted in the workspace manifest.
- Query filters narrow retrieval without rewriting scan rules.
- The workspace layer owns manifests, portable paths, credentials, locks and
  recoverable index publication under `.zvec-grep/`.
- Indexing coordinates scanner, extraction, model runtimes and zvec storage.
- Text and code share one embedding model per workspace; images are currently
  skipped.
- Workspace discovery supports nested workspaces and explicit roots; current
  Rust behavior is documented in the engine/workspace READMEs.

**Confirmed vs proposed:** the engine and workspace behavior above are in code
and docs. “Whole-repository semantic understanding” is not a product guarantee;
the retrieval layer is still chunk/symbol-level search plus optional evidence
assembly.

## 5. CLI, MCP, daemon and UX

### CLI

Current installed help exposes:

- bare query search;
- `--rg` managed lexical search;
- `--index`, `--status`, `--verify`, `--config`, `--auth`;
- `--server`, `--install`, `--uninstall`;
- search modes and model/file-type help.

The Rust CLI is the maintained release line; Node CLI remains in `src/` for the
TypeScript package.

### MCP and adapters

The product installs agent integrations for multiple hosts. Confirmed targets
include OpenCode, GitHub Copilot, VS Code, Pi and other MCP clients; the READMEs
also document Claude/Codex-style evaluation usage. MCP is the normal agent
seam. The current installed daemon reports MCP toolset `agent` and URL
`http://127.0.0.1:7999/mcp`. Installation is per-user and version-sensitive;
`--install --upgrade-installed` migrates existing launchers rather than
selecting new agents.

MCP tool routing is outside the engine; `zg-transport-mcp` owns schemas,
validation, presentation and consent. MCP tools do **not** imply the RAG
evidence API is exposed: R1 `api::evidence` is currently a library API only.

### Daemon / protocol / UX v2

`rust/docs/daemon-cli-ux-v2.md` defines:

- default mode `auto`: a ready compatible daemon handles the operation, direct
  mode is fallback for indexing;
- `--mode direct` bypasses the daemon; daemon protocol capability 2 required;
- explicit job submission, wait timeouts, cancellation and retained terminal
  jobs;
- separate manual/watch concurrency lanes with bounded queues;
- same-root manual work supersedes watcher work at a cancellation checkpoint;
- daemon work survives CLI wait timeout;
- no replayed one-operation scan/credentials/consent into future watcher jobs.

The UX change explicitly does **not** alter workspace format version 2, native
zvec 0.8, FP16 behavior, storage/engine pipelines or index format.

## 6. Retention / UX v2 job queue

The daemon owns:

- queued/running/terminal state;
- observed file progress;
- same-root blocker IDs;
- submitted job IDs;
- bounded retained history;
- manual vs watch reason;
- cancellation.

The CLI can list jobs, cancel, detach (`--no-wait`) and wait with a bounded
timeout. A timeout is a CLI-level result; the daemon job continues. Shared or
coalesced work is not cancelled out from under other callers.

## 6b. Build, packaging and release workflow

- Rust workspace builds from `rust/` with a pinned native zvec 0.8 and pinned
  llama.cpp revision; the default member is `zg`.
- Native npm packaging is generated under `dist/npm/`; the local install stays
  linked to `dist/npm/zvec-grep-local` until rebuilt.
- CUDA plugins are runtime plugins, not mandatory dependencies; accelerator
  detection/download is explicit (`ZG_INSTALL_ACCELERATOR`).
- Release candidates are tagged (`rust-v0.3.0-rc.1`, `rust-v0.3.0-rc.2`);
  archive/rollback tags preserve older lines.
- A release is not the same as a production rollout: RC notes can say
  “merge/release review ready” while production rollout remains blocked.

## 7. Release / compatibility philosophy

- Engine API is typed and additive where practical.
- Daemon protocol and workspace schema are versioned; incompatible versions
  fail closed rather than silently reusing data.
- Index compatibility is explicit. The current source has
  `CURRENT_INDEX_VERSION = 3`; the installed index is v2. V2 does not become v3
  by opening it; the release/rollback path must be deliberate.
- Native backend identity is part of embedding reuse keys; CPU and accelerator
  vectors are not interchangeable without a verified identity.
- Rollback tags and a rollback pair exist; they are not automatically installed
  by this documentation work.

## 8. What is *not* the product

- A general web search engine.
- A source-code compiler or semantic LSP.
- A business-DB query executor.
- A system that treats structural reachability as task relevance.
- A guaranteed RAG answer engine.
- A production-qualified evidence API just because the engine has
  `api::evidence`.
- The R1 evidence pack, R4 expander, or post-R4 GS candidate being exposed in
  CLI/MCP.

## 9. Main architecture risks

1. **Dual-stack drift:** Node and Rust products share a name but have different
   versions, schemas and release lines.
2. **Installed/source drift:** source is index v3/E8-P2; installed is
   v2/Potion.
3. **Transport/API gap:** R1 evidence is library-only; adding CLI/MCP is a
   product decision, not a small wiring change.
4. **Benchmark contamination:** route composition and measurement units have
   already produced invalid historical conclusions (E5/E6).
5. **Research fragmentation:** post-R4 branches explore typed grounding but are
   outside the canonical R0–R6 plan.
6. **Native concurrency:** native optimize/operations under state lock constrain
   incremental maintenance and distributed assumptions.

## 10. Evidence anchors

- `rust/ARCHITECTURE.md:5-24` — public engine boundary.
- `rust/ARCHITECTURE.md:26-60` — retrieval/indexing/resource ownership.
- `rust/ARCHITECTURE.md:62-86` — crate boundaries and dependency rules.
- `rust/docs/daemon-cli-ux-v2.md:1-45` — routing, job control, scheduler and
  compatibility limits.
- `rust/crates/zg-engine/src/lib.rs` — `ZvecGrep` entry point.
- `rust/crates/zg-engine/src/api/mod.rs` — public API groups.
- `README.md:48` — README describes the TypeScript implementation; Rust is
  separate.
- `package.json:2-10` — Node package identity/version.
- `Cargo.toml` (rust workspace) — package version `0.3.0-rc.2`.
- `docs/project-history/PROJECT-STATE.md` — installed/source distinction.
