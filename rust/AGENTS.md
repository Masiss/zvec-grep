# Rust workspace guidance

Read [`../AGENTS.md`](../AGENTS.md) first. This file adds Rust-specific
invariants only; it does not grant authority, expand the current task, or
override the root repository policy.

## Scope

- This guidance applies to `rust/` and its crates.
- The frozen release candidate is `integration/upstream-rust` at
  `rust-v0.3.0-rc.2` (code baseline `289674b`). Do not change the RC for
  post-RC tracks such as Harness V2 or Daemon/CLI UX V2 unless the active user
  request authorizes a new RC.
- Do not change production defaults, storage format, zvec pin, dependency lock,
  benchmark authorization, or release policy without explicit authorization.

## Build and test

Use an absolute `TMPDIR` with no `..` component; a relative or `..` path breaks
MCP path fixtures for environmental reasons:

```bash
export TMPDIR=/home/masis/src/zvec-grep/.tmp
# or another absolute scratch directory inside the active worktree
```

Focused/full verification commands:

```bash
cargo test -p zg-engine --lib
cargo test -p zg-engine
cargo test -p zg-daemon
cargo test -p zg-cli
cargo test -p zg-transport-mcp
cargo test -p zg-daemon-protocol
cargo test -p zg-zvec-fp16
cargo test -p zg-host-native
cargo clippy -p zg-engine -p zg-daemon -p zg-cli -p zg-transport-mcp --all-targets -- -D warnings
cargo fmt --all -- --check
cargo build -p zg-cli --release
```

- Prefer focused tests first, then the relevant suites. Report exact commands,
  counts, and failures; do not claim verification that did not run.
- Do not weaken a test to obtain PASS. Treat a known `#[ignore]` reason as
  evidence, not as permission to remove the gate.
- The release CLI needs the native `libzvec_c_api.so` next to the binary or under
  its `$ORIGIN/../lib` runpath. Do not symlink an installed local `zg` directly
  to a disposable `target/` directory; `cargo clean` will break it.

## Native and storage invariants

- zvec 0.8.0 rejects readers/writers during native `optimize()`; the M3-B
  handle-based outside-lock scalar maintenance path is **blocked**. Do not
  re-enable it or treat 0.7.2 Gate H/I as evidence for 0.8.0.
- Preserve the safe finalization design: fragment optimize outside the store
  state lock; directory/entity/file optimize under the lock; durable
  `optimize.pending` and writer-generation debt.
- Index format is version 2. Incompatible versions require an explicit rebuild;
  do not add in-place migration without an owner decision and compatibility
  tests.
- The M2 `reconciliation/` sidecar is additive and optional. Missing, corrupt,
  torn, oversized, incompatible, symlinked, or non-regular sidecars must remain
  non-fatal. Never make sidecar repair a prerequisite for canonical queries.
- #10 deferred maintenance is not implemented. Do not add a background deferral
  policy without owner authorization, storage-growth bounds, and a benchmark.

## Benchmarks and evidence

- Do not run a new benchmark, reuse an operation ID, raise a resource limit, or
  change a benchmark policy without explicit authorization.
- Preserve historical receipts and policy v1 fields. New harness code must use a
  versioned reader for old receipts; never rewrite evidence in place.
- Harness plans and development tracks live in `rust/docs/`, not repository-root
  `docs/` (that tree is upstream product documentation) and not in a per-crate
  `MEMORY.md`.
- M4-specific policy and evidence stay under `research/m4-native/`; follow its
  nested `AGENTS.md` there.

## Documentation placement

- Implementation/refactor plans: `rust/docs/`.
- Track index: `rust/docs/development-tracks.md`.
- M4 policy/evidence: `research/m4-native/docs/` and
  `research/m4-native/results/`.
- Release gates/evidence: the frozen RC worktree's `docs/release/`.
- Do not create new root-level handoff or plan files for this workstream.

## Reporting

When finishing a Rust change, report:

- what changed (crates/files);
- exact tests, clippy/fmt, and release build results;
- any ignored tests and why;
- what remains blocked or unverified;
- whether an RC, benchmark policy, default, or storage format was touched.

If a fixable test/build defect is within the authorized work, repair it and
rerun the relevant checks rather than reassigning it. If a safety or
authorization gate rejects an operation, preserve the refusal and do not retry
it automatically.
