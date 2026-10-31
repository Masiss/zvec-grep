# Development, builds, and verification

[Engineering guide](README.md) · [System map](system-map.md) · [Interfaces](interfaces-compatibility.md)

Use the verification command owned by the implementation being changed. A TypeScript build does not verify Rust, and Rust workspace tests do not verify npm packaging or the TypeScript runtime.

## Toolchain and packages

- Root package: Node.js `>=22`, TypeScript/ESM, scripts and dependencies in root `package.json`, compiler settings in `tsconfig.json`.
- Rust: pinned toolchain in `rust/rust-toolchain.toml`, workspace manifest in `rust/Cargo.toml`, Rust-specific guidance in `rust/CONTRIBUTING.md`.
- M4 research: separate CMake/C++ and Rust bridge build under `research/m4-native/`; read its `AGENTS.md`, benchmark policy, and current execution checkpoint before running any workload. It is not a routine application build.

## TypeScript checks

From the repository root:

```bash
npm ci
npm run build
npm run typecheck
npm run lint
npm run format:check
npm run test:run:unit
npm run test:run:integration
npm run test:run:e2e
npm run test:package
```

`npm run check` is the package's consolidated check and includes lint, formatting, typecheck, coverage-backed full tests, and package tests. `npm run test:coverage` builds first and uses the coverage thresholds declared in `package.json`. Use focused test scripts while iterating, then the appropriate full check before claiming broad validation.

## Rust checks

From `rust/`:

```bash
cargo fmt --all --check
cargo check --workspace --all-targets
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
RUSTDOCFLAGS="-D warnings" cargo doc -p zg-engine --no-deps
```

The repository script `rust/scripts/check.sh` runs the workspace checks package-by-package, clippy, tests, metadata validation, and npm-local packaging tests. See `rust/CONTRIBUTING.md` for crate-boundary rules. Use narrower package/test invocations during development, but do not report them as equivalent to the full workspace check.

## Choose tests by change boundary

| Change | First evidence | Broader evidence |
| --- | --- | --- |
| CLI args/rendering | owning CLI unit tests and help output | package/e2e tests; Rust compat cases if rewriting |
| MCP schema/presentation | schema and formatting fixtures | client/server conformance and transport integration |
| Daemon scheduling/watchers | deterministic scheduler/runtime tests | lifecycle integration, freshness/recovery, platform checks |
| Extraction/file selection | format fixtures and admission edge cases | indexing integration and TS↔Rust compat corpus |
| Model/artifact/authorization | isolated resolver/backend/grant tests | end-to-end with local/mock provider; never leak credentials |
| Storage/publication | staging, reopen, interruption and recovery tests | engine integration, snapshot/query visibility, migration checks |
| Retrieval/ranking | route-specific recall/rank/filter fixtures | benchmark/evaluation with pinned data, model and protocol |
| Native build/FFI | bridge round-trip/error/lifetime tests | clean build, package test, platform CI |
| Research benchmark | policy/unit/no-launch validation first | only a fresh policy-valid contained run; report actual limitations |

## CI map

`.github/workflows/` separates TypeScript CI, Rust CI/models/package builds, publish, security, retrieval comparison, and benchmark/offline evaluation. When changing a workflow, inspect its trigger, conditions, matrix, artifacts, and exact checked-in command; do not assume a similarly named workflow covers the entire repository. Release/publish workflows are not local verification commands.

## Benchmark hygiene

Performance measurements need a fixed workload and binary identity, host/process/resource snapshots immediately before and after, and a record of concurrency, affinity, pressure, and per-process CPU/memory where available. Use the project's existing harness and policy rather than launching an ad hoc measurement. Keep repeated runs proportional to the decision and state single-run uncertainty. A benchmark PASS says only that its defined gates passed; it does not establish correctness, production readiness, or broad performance unless those properties were measured.

## Safe change sequence

1. Identify implementation and public boundary from [System map](system-map.md).
2. Read the applicable repository/nested `AGENTS.md`, implementation, and neighboring tests.
3. Make the smallest owning-layer change; update fixtures/docs/contracts together.
4. Run focused tests and format/type/lint checks for changed files.
5. Run broader suite/build when needed for risk and stated acceptance criteria.
6. Inspect final diff, generated/untracked artifacts, and report exactly what did and did not run.

Avoid using generated `dist/`, `target/`, benchmark workspaces, or cached native artifacts as source of truth. Preserve unrelated existing worktree changes.