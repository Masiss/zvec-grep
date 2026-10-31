# Merge-readiness report — integration/upstream-rust

Date: 2026-10-31. Branch: `integration/upstream-rust`. Upstream base:
`e1f93d2`. Head: the commit containing this report. No push, merge, deploy,
default change beyond the owner decisions below, or new benchmark was performed.

Release gates, upgrade/downgrade, sidecar recovery, #10 audit, and rollback are
in [`RELEASE-READINESS.md`](RELEASE-READINESS.md). This report covers the net
diff, dispositions, behavior changes, compatibility, and merge verdict.

## 1. Verdict

- **Merge-readiness: READY for review** at branch scope. Targeted suites are
  green, the net diff has no accidental debug artifacts, and every
  production-relevant fork item is integrated or has an explicit disposition.
- **Production-readiness: NOT READY.** The 64-file confirmation is exploratory/
  INCONCLUSIVE, no 421-file/production benchmark was authorized, M3-B scalar
  outside-lock maintenance is blocked by zvec 0.8.0, and #10 is not implemented.
- **No release gate is BLOCKED.** The M3-B limitation applies to the unshipped
  outside-lock path; the safe sequential path is what this branch ships.

## 2. Scope and net diff

`git diff e1f93d2..HEAD`: 25 files, +4,146/−81 before the release-readiness docs.
Rust changes are confined to:

| Area | Files | Disposition |
|---|---|---|
| Storage finalization / M3-A | `storage/store/mod.rs`, table modules | dirty mask, fragment optimize outside state lock, generation/debt marker, telemetry |
| M2 reuse sidecar | `storage/manifest.rs`, `storage/fragments/mod.rs`, `pipelines/indexing/pipeline.rs`, `workspace/index.rs` | additive reconciliation sidecar, FP16 vector fingerprint, rebuild preflight, symlink/special-file hardening |
| Watcher coalescing | `zg-daemon/src/job_scheduler.rs` | 750 ms trailing debounce, 5 s cap, manual priority |
| Extraction/input | `pipelines/indexing/input_budget.rs` | streaming token-density detector |
| Model runtime | `models/runtime/*`, `models/spi.rs`, Qwen backend | concurrency cap/overrides, stable-identity guard |
| Tests/docs | storage/pipeline/workspace tests, release reports | evidence and regressions |

No dependency lock change, zvec schema/table change, or CLI/MCP source change.

## 3. Fork disposition audit

`work/zg-optimization` HEAD is `fdf5c90`, one docs commit after `abaf7bf`; there
is no post-`abaf7bf` production code to port. `FEATURE-MATRIX.md` rows #1–#22 are
the disposition record. Confirmed:

- #1, #2, #3, #4, #6, #7, #8, #12, #13, #14: **INTEGRATED** with focused tests.
- #5 (streaming/staged indexing), #17 (scanner `read_stream`): **REWORK / NOT
  INTEGRATED** — upstream has no streaming scanner surface. The token-density
  detector is the portable part and is integrated.
- #9 (full lock isolation): **BLOCKED** — fragment isolation is integrated;
  scalar outside-lock maintenance failed the zvec 0.8.0 native gate.
- #10 (deferred maintenance): **NOT INTEGRATED** — needs an owner policy and a
  benchmark; the durable marker + generation subset is integrated. Evidence and
  risks: `RELEASE-READINESS.md` §8.
- #19 (extraction): completed semantic diff.
  - Token-density streaming detector: **INTEGRATED**.
  - `TextStreamChunker`: **REWORK**, depends on #17.
  - Table-cell projection for embedding content: **REWORK / behavior decision**.
  - Python decorator / Rust attribute signatures: **SKIP**; upstream deliberately
    excludes them and has tests asserting that contract.
  - `extract_doc` simplification and `ExtractionOutput`/graph removal: **SKIP**;
    fork-M4 architecture; upstream keeps richer docs and graph output.
- #11, #15, #16, #18, #20, #21, #22: **COVERED/SKIP** for the reasons in the
  matrix.

## 4. Owner-accepted behavior changes

1. **Model compute thread default cap = 8** when
   `ZVEC_GREP_MODEL_COMPUTE_THREADS` is unset. On hosts with >8 cores this is a
   real default change; the env var overrides it. New tests pin parse/bounds.
2. **Watcher coalescing**: watcher-triggered jobs may start up to 750 ms later
   (5 s hard cap); manual jobs bypass the trailing wait. Daemon tests cover it.
3. **M2 embedding reuse** after a modified file when fragment/model/input/FP16
   vector fingerprints still match. `ZVEC_GREP_DISABLE_EMBEDDING_REUSE=1` disables
   reuse. The sidecar is auxiliary; see §5.
4. **Rebuild preflight**: hard floor = one active-index copy; conservative target
   = `2 × active + 4 GiB`; a warning band in between proceeds because a shrinking
   rebuild may fit. A failed build retains the active index. Error help gives
   exact bytes and remediation.
5. **Selective finalization**: only dirty collections are optimized; fragment
   optimize runs outside the state lock; scalar stages remain under it.
6. **Diagnostics**: new `index_optimize_*`/`index_reconcile_*` timing entries
   appear in `--debug` index output.

Model cache env overrides change nothing unless set (idle ≤ 86400 s, capacity
≤ 16). No other defaults changed.

## 5. Correctness and compatibility

- **M2 manifest**: version 2, `deny_unknown_fields`, ≤4 MiB; missing/corrupt/
  oversized/torn/symlinked/non-regular sidecars are ignored. Published atomically
  after native rows commit; delete/failed replacement remove stale sidecars.
  Vector fingerprint binds the exact FP16 stored vector. Tests:
  `storage::manifest` (6), `reconciliation` (5), `m2_reuse` (2), plus pipeline,
  store lifecycle, and the new retrieval-parity integration test.
- **M3-A**: dirty mask, writer-generation debt, durable `optimize.pending`,
  close/reopen, injected failure retry. Store suite (51) includes
  `fragment_optimize_*`, `close_waits_*`, `dirty_optimize_*`, and pre/post-optimize
  visibility.
- **Storage compatibility**: `tests/index_version.rs` (2) and `tests/storage_crash.rs`
  (2) pass; no migration. Incompatible index versions require explicit rebuild.
  The sidecar is additive; a downgrade ignores it. Rollback: delete
  `reconciliation/` (reuse disabled) or restore the previous generation.
- **M3-B handle-based outside-lock path**: implemented and exercised, then
  rolled back because pinned zvec 0.8.0 rejects iterators/writes during
  `optimize()` and can SIGABRT. Five outside-lock/native tests are `#[ignore]`
  with the blocker; the safe implementation remains. Evidence:
  `M3B-HANDLE-MAINTENANCE-FEASIBILITY.md`,
  `/home/masis/bench/m3b-native-gate/run.log`.

## 6. Verification evidence

All commands from `rust/` with
`TMPDIR=/home/masis/src/zvec-grep-integration/.tmp` (absolute; a `..` component
breaks MCP path fixtures for environmental reasons).

| Suite | Result |
|---|---|
| `cargo test -p zg-engine --lib` | 702 passed, 0 failed, 15 ignored |
| `cargo test -p zg-engine` integration | 40 passed: concurrent_queries 8, engine_storage 17, file_selection 8, index_version 2, scan_failures 3, storage_crash 2 |
| `cargo test -p zg-daemon` | 124 passed |
| `cargo test -p zg-cli` | 180 passed (175 + 5) |
| `cargo test -p zg-transport-mcp` | 60 passed |
| `cargo test -p zg-daemon-protocol` | 5 passed |
| `cargo test -p zg-zvec-fp16` | 2 passed |
| `cargo test -p zg-host-native` | 44 passed (25 + 10 + 9) |
| clippy (`zg-engine`, `zg-daemon`, `zg-cli`, `zg-transport-mcp`, all targets, `-D warnings`) | PASS |
| `cargo fmt --all -- --check` | PASS |
| `cargo build -p zg-cli --release` | PASS |

**Total: 1,157 passed, 0 failed, 15 ignored.** The ignored tests are five
handle-based/native-maintenance gates plus ten local-model/benchmark/oracle
tests. All retain their reasons.

CLI/MCP release smoke (isolated `ZVEC_GREP_HOME`, release `0.3.0-rc.2`):
`--version` and `--help` pass; MCP `initialize` + `tools/list` returns one agent
tool (`zvec_grep_search`) and six full-toolset tools, no protocol error. Artifacts:
`/home/masis/bench/release-readiness-20261031/`.

## 7. Authorized 64-file confirmation benchmark

Owner authorization: exactly one confirmation run, recorded in
`/home/masis/bench/m3b-confirm/authorization.json`; policy
`max_authorized_pair_operations` remains 0 and was not edited. 3 repeats/arm,
counterbalanced, `taskset -c 0,1`, release, 512 MiB user cgroup, 4 concurrent
query workers, same corpus/model. No 421-file run.

Results (median of 3, integration vs upstream `e1f93d2`):

| Metric | Integration | Upstream |
|---|---:|---:|
| Mutation wall | 981.2 ms | 941.2 ms |
| `index_optimize` | 756.5 ms | 666.6 ms |
| Query p50 during mutation | 3.7 ms | 496.8 ms |
| Query p95 during mutation | 244.0 ms | 872.8 ms |
| Query p99 during mutation | 444.9 ms | 872.8 ms |
| Freshness lag | 400.7 ms | 1,600.7 ms |
| CPU user / system | 740 / 230 ms | 500 / 330 ms |
| Peak RSS | 560.6 MiB | 557.5 MiB |
| I/O write / read | 64.7 / 0.23 MB | 60.8 / 8.0 MB |

Validity: all 6 runs exited 0, zero query errors, parity 10/10, freshness found,
cgroup peak ≤ 512 MiB (all runs hit exactly the 512 MiB cap with no OOM; VmHWM
557–561 MiB, so the envelope was tight). However, the pre-registered
`during_samples >= 20` gate failed for upstream (9–11 samples; blocking queries
reduce the sample count), and upstream repeat 2 was a 2.8 s outlier with heavy
disk reads under the tight cgroup. Formal conclusion: **INCONCLUSIVE
(exploratory)**. Descriptively, mutation wall is at parity (+4.2%, inside the 5%
bar), while query p95/freshness direction favors the integration; this is
consistent with M3-A fragment isolation, not proof of a qualified win.

Artifacts: `/home/masis/bench/m3b-confirm/results-20261031T100004Z/`; full
write-up: `BENCHMARK-RESULTS.md`.

## 8. Rollback

- Revert the branch commits or merge-revert the branch; no storage migration is
  involved.
- M2 sidecar: delete `reconciliation/` or restore the previous index generation;
  queries remain correct, reuse is disabled.
- Rebuild: reopen for automatic recovery; preserve an unowned backup. Do not
  delete the active index manually.
- Behavior changes are code-local: compute cap (env override), watcher debounce,
  preflight warning band, selective finalization.
- The blocked handle-based code is not active; only test-only hooks/ignored
  regressions remain.

## 9. Remaining risks / owner decisions

1. **Production qualification:** 64-file only, one host, tight cgroup; no
   421-file run and no qualified performance win. Do not treat the branch as
   production-qualified.
2. **M3-B native blocker:** zvec 0.8.0 forbids query/write during scalar
   `optimize()`; outside-lock maintenance is NO-GO. Reopen only with a native
   version/API that passes the native gate.
3. **#10 deferred maintenance:** not implemented; needs policy, storage-growth
   bounds, and benchmark. Visibility evidence exists but recall/latency at scale
   does not.
4. **Preflight:** extreme shrink below one active copy still rejects; error help
   and the warning band are the remediation. The conservative margin is
   intentional.
5. **Sidecar:** accepted as auxiliary persisted state; it adds a directory and
   I/O. Stale hints are safe but may cause unnecessary re-embedding.
6. **Docs placement:** resolved — release/evidence documents live in
   `docs/release/`; operational handoffs remain at the repository root.

## 10. Recommended merge steps

1. Review `docs/release/README.md`, release notes, manifest, and PR description.
2. Confirm the accepted behavior changes are in the release notes.
3. Re-run the summary suites/CI with the absolute TMPDIR convention if the merge
   target differs.
4. Merge the safe sequential implementation; do not enable the blocked M3-B
   path or claim production qualification.
