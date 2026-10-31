# Interfaces, native boundaries, and compatibility

[Engineering guide](README.md) · [System map](system-map.md) · [Rust rewrite roadmap](rust-rewrite-roadmap.md)

The repository has several interfaces with different stability and trust properties. Keep presentation and transport concerns at their adapters; shared search/index behavior belongs in the owning engine.

## Interface inventory

| Boundary | TypeScript source | Rust source | Contract evidence |
| --- | --- | --- | --- |
| Node package API | `src/index.ts`, package `exports` | Not the Rust call surface | Type declarations, package tests, root `package.json` |
| CLI | `src/cli/` | `zg-cli/`, executable assembly in `zg/` | `--help`, command tests, compatibility cases |
| MCP tools and schemas | `src/mcp/` | `zg-transport-mcp/` | schema/tool tests and MCP compatibility fixtures |
| Local daemon HTTP / client | `src/daemon/http-server.ts`, `src/client/daemon-client.ts` | `zg-daemon/` | endpoint/auth/lifecycle tests; Rust daemon protocol tests |
| Rust daemon command/reply protocol | Not shared with TS runtime | `zg-daemon-protocol/` | serialized DTO tests and daemon client/server tests |
| Engine API | `src/engine/service/` | `zg_engine::api`, `ZvecGrep` | engine unit/integration tests and compat runner |
| Filesystem/native dependency | npm package integrations | `zg-host-native`, `zvec-rust` | adapter tests and platform/build checks |
| Remote model authorization | `src/authorization/` | `zg-engine::authorization` | grant/signature/endpoint-binding tests |

MCP schemas and daemon protocol payloads are separate contracts. A similarly named operation in both runtimes does not prove wire compatibility.

## TypeScript package and CLI

Root `package.json` exports `dist/index.js` with declarations and installs the `zg` executable from `dist/cli/index.js`. Build output is produced by `npm run build`; it is generated, not source of truth. The package's declared public exports in `src/index.ts` define the supported Node API.

The CLI is the most discoverable user interface, but exact argument semantics live in source. `zg --help` and subcommand help report the installed build's interface. Managed ripgrep uses an argument parser separate from indexed search and intentionally controls output formatting.

## Daemon and MCP

TypeScript MCP request schemas/normalization, tools, progress, and response formatting are in `src/mcp/`. The daemon serves MCP locally and coordinates long-lived workspace/model runtime. Bearer authentication protects daemon access when configured; loopback binding and origin/host checks are additional transport controls. None of these is equivalent to remote-embedding consent.

Rust has a typed daemon protocol (`zg-daemon-protocol`) and an MCP adapter (`zg-transport-mcp`). The daemon runs in `zg-daemon`. Transport handlers should call the public typed `ZvecGrep` API and leave indexing/search semantics in `zg-engine`. The intended ownership and exceptions are documented in [`rust/CONTRIBUTING.md`](../../rust/CONTRIBUTING.md).

## Native storage and FFI seams

The TypeScript implementation uses native packages through their public JS APIs; Rust uses `zvec-rust` bindings/build support. Keep native-library-specific types and ownership inside their integration crate/module. Validate any behavior that crosses process, FFI, serialization, or native storage boundaries at that boundary (round-trip, errors, cancellation/lifetime, reopen/recovery as relevant). Do not import the standalone `research/m4-native/rust-bridge` into production merely because the directory contains Rust/C++ bridge code.

## Errors and cancellation

Errors should preserve operation context and distinguish shared terminal failures from per-request or per-file failures where the public contract does so. Rust structured `EngineError`/`ErrorReport` and transport-level error mappings are defined in the owning crates. TypeScript error classes and CLI/MCP mappings are in `src/engine/errors.ts`, `src/cli/errors.ts`, and MCP handlers. Do not flatten an error to a string until the boundary requires presentation text.

Long-running operations must propagate cancellation across request handler, scheduler, scanning, model work, and storage. Progress events are observational; they must not be mistaken for publication/commit. On protocol changes, verify both normal completion and cancellation/disconnect behavior.

## Compatibility workflow

During the rewrite, the TypeScript implementation is the behavioral oracle. Stable cases and normalizations live in `rust/compat/`; shared loaders are in `zg-testkit`. Compare observable behavior at the external boundary: outputs, ordering, paths, errors, defaults, cancellation, model/authorization behavior, and persisted index compatibility where that is part of the product contract.

A compatibility fixture passing proves only the case it encodes. Milestone completion, crate test counts, similar architecture, or a successful build do not establish complete API or behavioral parity. Record unsupported behavior, intentional differences, and untested cases explicitly. See [Rust rewrite roadmap](rust-rewrite-roadmap.md) for the repository-level completion criteria.

## Security boundaries

- Keep API keys, bearer tokens, signing keys, and remote responses out of logs, debug output, fixture snapshots, and persisted diagnostic artifacts.
- Bind consent/grants to the actual workspace/model/endpoint as implemented; changing a bound target needs new consent.
- Treat a local daemon endpoint and a remote model provider as separate trust boundaries.
- Path normalization, symlink behavior, and workspace-root validation are security-sensitive input contracts. Test traversal and platform-specific cases at the entry boundary.
- Preserve loopback-only server assumptions unless the authentication, origin, and deployment model are deliberately redesigned.