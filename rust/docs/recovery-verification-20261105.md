# Recovered fork — source verification (2026-11-05)

## Outcome and scope

The recovered V2-based source is verified for publication. This is **not production
rollout, performance qualification, or a fix for unbounded indexing RAM**.

Verified code commit: `4807eaaa44e2dbcfaf6acfd9e58cb41a05a7dd45`.
Subsequent recovery-status/publication records change documentation only.

| Check | Actual result |
|---|---|
| `cargo fmt --all -- --check` | PASS |
| `cargo clippy --offline --locked --workspace --all-targets -- -D warnings` | PASS |
| `cargo test --offline --locked --workspace -- --test-threads=2` | **1,437 passed, 0 failed, 16 ignored** across 47 summaries, including doctests |
| `cargo build --offline --locked -p zg --release` | PASS; actual executable, not library-only build |
| Isolated release `--version`, `--help`, `--help index`, `--help server` | PASS; version `0.3.0-rc.2` |
| Release dynamic loader / pinned native pair | PASS; all dependencies resolve; native C API resolves to verified 0.8 library |

The 16 ignored tests retain their existing explicit conditions: external model
assets or plugins, manual fixture/benchmark/replay work, and unsupported concurrent
native scalar maintenance. No tests were ignored or assertions removed to obtain
these results. GPU inference and cross-target builds were not verified.

## Reproducible identities

- Compiler: `rustc 1.98.0 (88d9e12ae 2026-08-18)`.
- Release executable: 58,528,776 bytes;
  SHA-256 `61aae5683f36c44eb98736295536c8a13e253fa247e07ea88309d4a5f5c27674`.
- Native C API SHA-256:
  `5093c7d722304b0efaa7425f206456ca902dc377d5a0ae8056f1c5c010fe763e`.
- Private evidence filenames: `fmt-20261105T123021070682024.log`,
  `clippy-20261105T123022234026394.log`,
  `tests-20261105T123219601385836.log`,
  `release-20261105T124109459426335.log`, `release-smoke-20261105.json`.
- Raw logs and the candidate executable are retained privately, not committed or
  installed. Existing binaries, daemon processes and live indexes were not replaced.

Builds used a shared cache, two build jobs, no incremental compilation, offline /
locked dependencies, an isolated app home and absolute temporary directory. Every
new build required at least 4 GiB free. Refused preflights launched no manual build;
unused test outputs were removed under an exclusive Cargo lock and process-reference
checks before explicitly releasing further work. Earlier failed logs remain intact.

## Runtime versus preserved history

The current runtime preserves zvec 0.8, FP16, portable workspace/index format 2,
fail-closed protocol compatibility and scalar finalization under the store lock.
Recovered changes include daemon job/control UX, optional compact reconciliation
hints, conservative bounded/cancellable clean-scan verification, and the source
RAG evidence API. Lane-exclusive patches were checked against canonical with
`git cherry`; all were covered.

Legacy native 0.7, V1 format work, M4 and benchmark/research artifacts remain archival
or research evidence, **not newly qualified runtime behavior**. Deferred maintenance
#10, production M4 integration and RAG R0/R1 quality work remain incomplete.

The separate single-run Laya diagnostic completed 2,714 files with zero failed files
and about 1.25 GiB peak RSS for `zg`. It did not reproduce the reported >3 GiB run,
prove a leak, or establish a full-index memory cap. No large index rerun was performed.
