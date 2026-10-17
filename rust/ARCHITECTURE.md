# zvec-grep Rust architecture

This document describes the implemented Rust workspace under `rust/`. The
TypeScript implementation at the repository root remains the compatibility
oracle; it is not part of the Rust runtime dependency graph.

## System boundary

The Rust workspace provides one local search product through three entry
surfaces:

```text
CLI (`crates/zg`) ───────────────┐
MCP over loopback (`zg-daemon`) ─┼─> `zg-engine::ZvecGrep`
In-process callers ──────────────┘
```

The engine owns search, indexing, workspace metadata, model leases, storage
coordination, and typed errors. CLI parsing/rendering, daemon framing, and MCP
schemas stay outside the engine.

The resident daemon owns one reusable `Arc<ZvecGrep>` and serves loopback
HTTP. The public MCP endpoint is `/mcp`; CLI administration uses the daemon's
local `/admin/execute` endpoint. The daemon also exposes health and controlled
shutdown endpoints.

## Crate map

| Crate | Responsibility | Main entry points |
| --- | --- | --- |
| `zg` | Binary entry point and Tokio runtime setup | `crates/zg/src/main.rs` |
| `zg-cli` | CLI grammar, plans, rendering, install/config management | `crates/zg-cli/src/lib.rs` |
| `zg-engine` | Typed application API and private search/indexing implementation | `crates/zg-engine/src/lib.rs` |
| `zg-daemon` | Resident process, loopback HTTP, job scheduler, watcher runtimes | `crates/zg-daemon/src/lib.rs`, `runtime.rs` |
| `zg-daemon-protocol` | Versioned daemon request/response/event DTOs | `crates/zg-daemon-protocol/src/lib.rs` |
| `zg-transport-mcp` | MCP tool schemas, request translation, result formatting, consent boundary | `crates/zg-transport-mcp/src/lib.rs` |
| `zg-host-native` | Filesystem scanner and watcher ports/implementations | `crates/zg-host-native/src/lib.rs` |
| `zg-memory` | Process allocator reclamation after memory-heavy operations | `crates/zg-memory/src/lib.rs` |
| `zg-testkit` | Compatibility fixtures shared by workspace tests | `crates/zg-testkit/src/lib.rs` |

The workspace is declared in `rust/Cargo.toml`; `crates/zg` is the default
member. Native zvec bindings are owned by `zg-engine` and are not exposed as a
cross-crate application abstraction.

## Engine boundary

`zg-engine/src/lib.rs` exposes `ZvecGrep` and typed request/reply modules under
`api/`. The current public methods are:

- `context` and `context_after_refresh`: indexed retrieval or direct embedded
  ripgrep when `ContextOptions::rg` is enabled;
- `index`: create or reconcile a workspace index;
- `info`: read workspace metadata and, when requested, verified status;
- `watch_workspace`: create a watcher session for an already indexed workspace;
- `drop_index`: remove the persisted workspace index;
- `enable_read_session_cache`, `runtime_snapshot`, and `close`.

`EngineService` in `crates/zg-engine/src/service.rs` owns the private services:

- `DirectSearchService` for exhaustive filesystem search;
- `WorkspaceIndexService` for workspace lifecycle and indexing;
- indexed-search services for FTS/vector retrieval and deterministic ranking;
- `ModelRuntimeManager` for shared model instances and leases;
- `ReadSessionCache` for reusable read-only native storage handles.

There is deliberately no generic command bus, operation envelope, or transport
executor inside the engine. Each public method validates its own typed request,
selects the relevant private service, and returns `EngineError` directly.

## Retrieval flows

### Direct search

```text
ZvecGrep::context
  -> EngineService::context_with_refresh
  -> DirectSearchService
  -> embedded grep matcher/searcher + ignore traversal
  -> deterministic file-oriented ContextResult
```

This path reads the workspace directly and does not require an embedding model
or a persisted index.

### Indexed search

```text
ZvecGrep::context
  -> indexed_search::service::context
  -> read session / IndexStore
  -> FTS and vector recall
  -> path/format filtering
  -> ranking and result formatting
```

Indexed search uses the storage read interfaces in
`crates/zg-engine/src/pipelines/indexed_search/storage.rs` and the ranking
pipeline under `pipelines/indexed_search/ranking/`. A read session is retired
before a workspace writer opens the same index.

The refresh policy is a coordination policy, not a second search
implementation. Daemon callers can use `context_after_refresh` after the
workspace runtime has drained watcher events and completed the required index
job.

## Indexing flow

The write path is split between lifecycle orchestration and the data pipeline:

```text
IndexOptions
  -> WorkspaceIndexService::index
  -> workspace manifest / lock / generation selection
  -> NativeScanner discovery
  -> file-selection classification and DiffPlan
  -> extraction and chunk preparation
  -> bounded embedding scheduler
  -> IndexStorage replacement/update/delete operations
  -> finalize writes and publish generation metadata
```

Important implementations:

- `pipelines/indexing/service.rs` opens the workspace, validates model/storage
  compatibility, resolves the scan policy, and owns the lifecycle around the
  operation.
- `pipelines/indexing/pipeline.rs` classifies added/modified/pending/unchanged
  files, extracts entities and fragments, batches embeddings, commits results,
  and records per-file failures.
- `pipelines/indexing/storage.rs` defines the small `IndexStorage` seam used by
  the pipeline and tests; `IndexStore` is the production implementation.
- `extraction/` converts supported source formats into entities, metadata and
  fragments. `file_selection/` applies roots, globs, categories and size
  policy before expensive work.

### Backpressure and failure behavior

- Prepared files are accumulated by both fragment count and an approximately
  64 MiB per-batch memory budget. The budget accounts for source, entity,
  embedding and FTS payload sizes.
- In-flight embedding work is bounded by the adaptive
  `EmbeddingScheduler`. The model runtime remains the owner of model
  concurrency; the pipeline does not create an independent process-global
  scheduler.
- Only structured retryable model/host failures enter the one-pass file retry.
  Deterministic failures are persisted on the file and are not retried merely
  because the outer indexing pass completed.
- Stale-file deletion is chunked and marks file records as `Deleting` before
  removing entities, fragments and file identities. The marker allows the next
  reconciliation to finish interrupted deletion.
- Cancellation is checked during discovery, preparation, deletion and
  embedding. Fail-fast shared model failures drain in-flight futures before
  returning.
- `finalize_writes` runs in three phases: it checkpoints accepted native writes,
  checks out the affected collections and releases the store state mutex, runs
  native `optimize()` **outside** the state lock (search and writers stay
  available), then acknowledges only the exact debt that was optimized. It does
  not imply a full source re-index. See `docs/m3b-handoff.md` for the
  lock-isolation and bounded-deferred maintenance design.

## Storage and workspace lifecycle

`crates/zg-engine/src/storage/` coordinates the native zvec collections:

- `directories/` stores directory identities and membership;
- `files/` stores source file records, snapshots, status and path ownership;
- `entities/` stores extracted entities and metadata;
- `fragments/` stores model-specific searchable projections, FTS text and
  vectors;
- `read_session.rs` manages leased read handles and writer retirement;
- `store/mod.rs` coordinates replacement, failure, deletion and finalization;
- `record.rs`, `types.rs`, `path.rs` and `zvec.rs` define storage encoding and
  native helpers.

A file replacement validates the complete logical file before changing native
projections. Same-content rewrites can update only the persisted source
snapshot; they do not re-extract or re-embed content. File IDs are local to an
index and are not reused while a live record still owns the path.

Native maintenance is bounded and deferred. Every fragment-affecting mutation
advances a fragment-specific debt counter; `finalize_writes` starts native
optimization when the debt reaches a threshold, an age deadline passes, or a
deletion forces it (deletions leave tombstones that native `optimize()` removes
even when completeness already reads 1.0). Deferral is opt-in via
`ZVEC_GREP_MAINTENANCE_THRESHOLD` (default eager). A durable
`storage/maintenance.delete` marker (fsync file + directory) makes pending
deletion maintenance survive restart. Native optimize runs outside the
`Mutex<StoreState>`, and a maintenance lease makes `close()` wait for in-flight
maintenance. Details and gates: `docs/m3b-handoff.md`.

Workspace generations and manifests are handled under
`crates/zg-engine/src/workspace/`. `workspace/build.rs` stages a fresh
storage generation and publishes it through an atomic manifest update. Rebuild
preflight checks active-generation size and available filesystem space before
model acquisition/staging when the platform supports the measurement.

The normal workspace state is under `<workspace>/.zvec-grep/`; global daemon
state is under `~/.zvec-grep/`. The active manifest, not an in-memory process
singleton, identifies the generation used by later readers.

## Models and resource ownership

The private `zg-engine::models` module is layered as follows:

```text
catalog -> artifacts/cache -> backend implementation -> runtime manager
                                                  -> model lease
```

`ModelRuntimeManager` keys reusable runtimes by model and resource-affecting
configuration. A lease owns access to one loaded runtime; it does not load a
second copy for every request. Preparation is explicit, cancellable and
idempotent. Backends implement the backend-neutral SPI in `models/spi.rs`;
backend selection belongs in `models/backends/factory.rs`.

The runtime manager also owns eviction and shared compute limits. Pipeline code
uses a lease/SPI rather than concrete backend types. `zg-memory::reclaim` is
called after indexing, verified status, and index-drop operations to return
allocator-retained pages in the resident daemon; it is intentionally not on the
normal query path.

## Daemon and workspace runtimes

`zg-daemon/src/runtime.rs` owns the loopback host and translates daemon
requests into engine calls. `zg-daemon-protocol` keeps the wire contract
versioned (`CURRENT_DAEMON_PROTOCOL_VERSION`) and transport-only.

`WorkspaceRuntimeManager` in `workspace_runtime.rs` maintains one runtime per
canonical workspace root. A runtime contains:

- a freshness/status cache with invalidation epochs;
- a watcher session from `zg-host-native`;
- dirty/indexed revisions;
- a serialized refresh gate;
- pending watcher configuration;
- an `IndexJobScheduler` and its bounded job state.

The scheduler serializes conflicting writes per root, keeps job snapshots for
late waiters, forwards progress, and maps scheduler/watcher failures back to
`EngineError`. The watcher reports changes; it does not write the index itself.
A scheduled index job is the only component that owns the engine write call.

`zg-transport-mcp` presents the selected agent or full MCP toolset. It translates
inputs to `ZvecGrep` requests or resident workspace operations and formats
results; it must not become a second engine layer.

## Invariants for changes

1. **Boundary:** CLI, daemon protocol and MCP DTOs must not leak into private
   engine implementation modules.
2. **Ownership:** model concurrency belongs to the model runtime; storage locks
   and read-session retirement belong to storage/workspace lifecycle; root job
   serialization belongs to the daemon scheduler.
3. **Generation safety:** readers use the active manifest; a staged generation
   is not visible until its metadata is atomically published.
4. **Failure scope:** shared terminal model/storage failures stop the operation;
   request/file-specific failures may be isolated only when the structured error
   contract permits it.
5. **Freshness:** watcher revisions and status epochs prevent an old scan or
   completion callback from restoring a newer invalidated snapshot.
6. **Determinism:** output ordering, error codes, path normalization and
   compatibility behavior remain stable across direct and server modes.
7. **Cancellation:** every long-running scan, model preparation, embedding and
   daemon job must preserve cancellation and avoid orphaned work.
8. **Security:** credentials stay out of logs, persisted records, hashes and
   debug output; remote embedding requires explicit authorization.

## Where to change common behavior

| Change | Start here | Also inspect |
| --- | --- | --- |
| New CLI flag or output | `crates/zg-cli/src/lib.rs`, `render.rs` | `crates/zg/src/main.rs`, compatibility tests |
| New engine operation | `crates/zg-engine/src/api/` and `lib.rs` | `service.rs`, daemon protocol, CLI/MCP adapters |
| Indexing or retry policy | `pipelines/indexing/pipeline.rs` | `service.rs`, model SPI/runtime, pipeline tests |
| Extraction or file support | `extraction/`, `file_selection/` | domain metadata, fixture tests |
| Storage schema or lifecycle | `storage/`, `workspace/` | generation/manifest code, reopen/recovery tests |
| Search ranking/filtering | `pipelines/indexed_search/` | storage read interfaces and ranking tests |
| Daemon scheduling/watching | `zg-daemon/src/job_scheduler.rs` or `workspace_runtime.rs` | protocol events, lifecycle/freshness tests |
| Model backend/cache | `models/catalog`, `artifacts`, `backends`, `runtime` | `models/spi.rs`, isolated artifact/runtime tests |
| MCP schema/format | `zg-transport-mcp/` | daemon runtime and MCP compatibility fixtures |

## Verification

From `rust/`:

```sh
cargo fmt --all --check
cargo check --workspace --all-targets
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
RUSTDOCFLAGS="-D warnings" cargo doc -p zg-engine --no-deps
```

For codebase navigation, this repository also has a local RepoWise index. Its
state is intentionally ignored by Git. Rebuild or update it with the global
`repowise` command, then use `repowise search`, `repowise context`, or
`repowise export` before making broad architectural changes.

This document is current-state documentation. If a proposed design differs
from the implemented paths above, record the proposal separately as an ADR
before presenting it as an invariant.
