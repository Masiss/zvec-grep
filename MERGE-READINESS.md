# Merge-readiness report — integration/upstream-rust

Date: 2026-10-31. Branch: `integration/upstream-rust`. Upstream base:
`e1f93d2`. Head at audit time: `4929ffc` (28 commits ahead before this report's
commit). No push, merge, deploy, default change, or new benchmark was performed
for this report except the separately authorized confirmation run described in
§7.

## 1. Verdict

- **Merge-readiness: READY for review** at the branch scope. All targeted suites
  are green, the net diff has no accidental debug artifacts, and every
  production-relevant fork item is integrated or has an explicit disposition.
- **Production-readiness: NOT READY / OWNER_DECISIONS_REQUIRED.** Three behavior
  changes need explicit owner acceptance, the M2 sidecar needs a storage-format
  decision, and the finalization optimization is blocked by the pinned native
  library. These are product/release decisions, not merge defects.

## 2. Scope and net diff

`git diff e1f93d2..HEAD`: 25 files, +4,146/−81 before this report. Rust changes
are confined to:

| Area | Files | Disposition |
|---|---|---|
| Storage finalization / M3-A | `storage/store/mod.rs`, table modules | dirty mask, fragment optimize outside state lock, generation/debt marker, telemetry |
| M2 reuse sidecar | `storage/manifest.rs`, `storage/fragments/mod.rs`, `pipelines/indexing/pipeline.rs`, `workspace/index.rs` | additive reconciliation sidecar, FP16 vector fingerprint, rebuild preflight |
| Watcher coalescing | `zg-daemon/src/job_scheduler.rs` | 750 ms trailing debounce, 5 s cap, manual priority |
| Extraction/input | `pipelines/indexing/input_budget.rs` | streaming token-density detector |
| Model runtime | `models/runtime/*`, `models/spi.rs`, Qwen backend | concurrency cap/overrides, stable-identity guard |
| Tests/docs | storage/pipeline tests, repo handoffs | evidence and regressions |

No dependency lock change, no zvec schema/table change, no CLI/MCP source
change.

## 3. Fork disposition audit

`work/zg-optimization` HEAD is `fdf5c90`, one docs commit after `abaf7bf`;
there is no post-`abaf7bf` production code to port. `FEATURE-MATRIX.md` rows
#1–#22 are the disposition record. Confirmed during this audit:

- #1, #2, #3, #4, #6, #7, #8, #12, #13, #14: **INTEGRATED** with focused tests.
- #5 (streaming/staged indexing), #17 (scanner `read_stream`): **REWORK /
  NOT INTEGRATED** — upstream has no streaming scanner surface. The token-density
  detector is the portable part and is integrated.
- #9 (full lock isolation): **BLOCKED** — fragment isolation is integrated;
  scalar outside-lock maintenance failed the zvec 0.8.0 native gate (see §6).
- #10 (deferred maintenance): **NOT INTEGRATED** — needs an owner policy and a
  benchmark; the durable marker + generation subset is integrated.
- #19 (extraction): completed semantic diff.
  - Token-density streaming detector: **INTEGRATED**.
  - `TextStreamChunker`: **REWORK**, depends on #17.
  - Table-cell projection for embedding content: **REWORK / behavior decision**;
    changes embedding input for tables and has no upstream evaluation.
  - Python decorator / Rust attribute signatures: **SKIP**; upstream
    deliberately excludes them and has tests asserting that contract.
  - `extract_doc` simplification and `ExtractionOutput`/graph removal: **SKIP**;
    fork-M4 architecture, upstream keeps richer docs and graph output.
- #11, #15, #16, #18, #20, #21, #22: **COVERED/SKIP** for the reasons in the
  matrix.

## 4. Behavior changes vs upstream

These are intentional and currently active:

1. **Model compute thread default cap = 8** when
   `ZVEC_GREP_MODEL_COMPUTE_THREADS` is unset. On hosts with >8 cores this is a
   real default change; the env var overrides it. New tests pin the parse/bounds.
2. **Watcher coalescing**: watcher-triggered jobs may start up to 750 ms later
   (5 s hard cap); manual jobs bypass the trailing wait. Covered by daemon tests.
3. **M2 embedding reuse** after a modified file when fragment/model/input/FP16
   vector fingerprints still match. Query output parity is tested; reuse can be
   disabled with `ZVEC_GREP_DISABLE_EMBEDDING_REUSE=1`.
4. **Rebuild disk preflight**: an atomic rebuild now refuses when free space is
   below 2× active index + 4 GiB. This is a new conservative failure mode.
5. **Selective finalization**: only dirty collections are optimized; fragment
   optimize runs outside the state lock.
6. **Diagnostics**: new `index_optimize_*`/`index_reconcile_*` timing entries
   appear in `--debug` index output.

Model cache env overrides change nothing unless set (idle ≤ 86400 s, capacity
≤ 16). No other defaults changed.

## 5. Correctness and compatibility

- **M2 manifest**: version 2, `deny_unknown_fields`, ≤4 MiB; missing/corrupt/
  oversized/version-1 sidecars are ignored. Published after native rows commit;
  delete/failed replacement remove stale sidecars. Tests:
  `storage::manifest` (3), `reconciliation` (5), `m2_reuse` (2), plus pipeline and
  storage lifecycle tests.
- **M3-A**: dirty mask, writer-generation debt, durable `optimize.pending`,
  close/reopen, injected failure retry. Tests: store suite (50) including
  `fragment_optimize_*`, `close_waits_*`, `dirty_optimize_*`.
- **Storage compatibility**: `tests/index_version.rs` (2) and
  `tests/storage_crash.rs` (2) pass; no migration. The sidecar is additive; a
  downgrade ignores it. Rollback: delete `reconciliation/` (reuse disabled) or
  restore the previous generation.
- **M3-B handle-based outside-lock path**: implemented and exercised, then
  rolled back because the pinned zvec 0.8.0 rejects iterators/writes during
  `optimize()` and can SIGABRT. Five outside-lock tests are `#[ignore]` with the
  blocker; the safe implementation remains. Evidence:
  `M3B-HANDLE-MAINTENANCE-FEASIBILITY.md`,
  `/home/masis/bench/m3b-native-gate/run.log`.

## 6. Verification evidence

All commands run from `rust/` with
`TMPDIR=/home/masis/src/zvec-grep-integration/.tmp` (absolute, no `..`; a
`..` in TMPDIR makes MCP path fixtures fail for environmental reasons).

| Suite | Result |
|---|---|
| `cargo test -p zg-engine --lib` | 696 passed, 0 failed, 15 ignored |
| `cargo test -p zg-engine` (integration targets) | 39 passed: concurrent_queries 8, engine_storage 16, file_selection 8, index_version 2, scan_failures 3, storage_crash 2 |
| `cargo test -p zg-daemon` | 124 passed |
| `cargo test -p zg-cli` | 180 passed (175 + 5) |
| `cargo test -p zg-transport-mcp` | 60 passed |
| `cargo test -p zg-daemon-protocol` | 5 passed |
| `cargo test -p zg-zvec-fp16` | 2 passed |
| `cargo test -p zg-host-native` | 44 passed (25 + 10 + 9) |
| clippy (`zg-engine`, `zg-daemon`, `zg-cli`, `zg-transport-mcp`, all targets, `-D warnings`) | PASS |
| `cargo fmt --all -- --check` | PASS |
| `cargo build -p zg-cli --release` | PASS (after removing stale build artifacts left by the temporary benchmark worktree) |

**Total: 1,150 tests passed, 0 failed, 15 ignored (blocked handle-based
regressions).**

## 7. Authorized confirmation benchmark (64-file)

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
cgroup peak ≤ 512 MiB (all runs hit exactly the 512 MiB cap with no OOM;
per-process VmHWM was 557–561 MiB, so the envelope was tight). However, the pre-registered `during_samples >= 20` gate
failed for upstream (9–11 samples; blocking queries reduce the sample count), and
upstream repeat 2 was a 2.8 s outlier with heavy disk reads under the tight
cgroup. Formal conclusion: **INCONCLUSIVE (exploratory)**. Descriptively, there
is no regression signal on mutation wall (+4.2%, inside the 5% bar), while query
p95/freshness direction strongly favors the integration; this is consistent with
M3-A fragment isolation, not proof of a qualified win.

Artifacts: `/home/masis/bench/m3b-confirm/results-20261031T100004Z/`
(protocol/manifest/preflight/per-run JSON/time/host snapshots). Full write-up:
`BENCHMARK-RESULTS.md`.

## 8. Rollback

- Revert the branch commits or merge-revert the branch; no storage migration is
  involved.
- M2 sidecar: delete `reconciliation/` or restore the previous index generation;
  queries remain correct, reuse is disabled.
- Behavior changes are code-local: compute cap (env override), watcher debounce,
  preflight refusal, selective finalization.
- The blocked handle-based code is not active; only test-only hooks/ignored
  regressions remain.

## 9. Unresolved risks / owner decisions

1. **Sidecar storage-format decision**: additive `reconciliation/` is not a zvec
   format change, but if the owner treats any new on-disk sidecar as a format
   change, decide before release.
2. **Behavior acceptance**: compute cap 8, watcher 750 ms debounce, rebuild
   preflight refusal. These need product/owner sign-off.
3. **Native finalization blocker**: zvec 0.8.0 forbids query/write during
   optimize; outside-lock scalar maintenance is NO-GO. Reopen only with a native
   version/API that passes the native gate.
4. **Deferred maintenance / streaming staging**: not integrated; need policy and
   scanner API decisions.
5. **Benchmark evidence remains exploratory**: 64-file pilot, one host, tight
   512 MiB cgroup, upstream sample-count/outlier limitations; no 421-file corpus.
6. **Docs placement**: `HANDOFF*.md`, `BENCHMARK-*.md`, `FEATURE-MATRIX.md`,
   and this report are branch artifacts added at repo root; decide whether they
   belong in the merged upstream repository or should move to an internal
   evidence location.

## 10. Recommended merge steps

1. Owner accepts or rejects the behavior changes in §4 and the sidecar decision.
2. Decide the placement of the branch evidence docs.
3. Re-run the summary suites/CI with the absolute TMPDIR convention.
4. Merge only after those decisions; do not merge the blocked M3-B optimization.
