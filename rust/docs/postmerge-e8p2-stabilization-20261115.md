# E8-P2 post-merge stabilization — 2026-11-15

Status: **MERGED_AND_STABILIZED** (controlled merge into `work/zg-recovered`;
production release and installed artifacts untouched).

Owner authorization covered: review, pre-merge qualification, controlled merge,
post-merge regression, independent verification and cleanup. It did **not**
authorize a release, production promotion, installed-binary/index/daemon change,
or making an accelerator the default.

## 1. SHAs and state

| Item | Value |
|---|---|
| Pre-merge main | `6d5b523369c7b064d5c7ac15c6fa91cfcf3f1c8f` (`work/zg-recovered`) |
| Integration branch tip | `5bd3f260e2367ceb1cab4076e4c7fa85fcd244b2` |
| Code integration commit | `e9ba76ce0f98ee4397044bfeff88724bf863d532` |
| Merge commit | `58582d2857013fcf189a454cd781746468ab07a9` (parents `6d5b523`, `5bd3f26`) |
| Rollback reference | lightweight tag `rollback/pre-e8p2-merge-20261115` → `6d5b523` |
| Main tree after merge | identical to integration tree (`git diff 5bd3f26 58582d2` empty) |
| Merge shape | `git merge --no-ff`, no rewrite, no force-push |

Remote `origin/work/zg-recovered` was at `6d5b523` at preflight and unchanged
before the merge. Push state is recorded in the session handoff; this document
does not itself authorize a release.

## 2. Integration provenance / diff audit

`111 files changed, 30171 insertions(+), 260 deletions(-)` versus main.
No `Cargo.lock`, CLI, daemon, daemon-protocol or transport changes; the only
manifest change is `zg-engine/Cargo.toml` (`e4-experiment` feature and `sha2`
build dependency). Source-level audit confirmed:

- Index v3, no-op fast path, E2 dedup/reuse and E8/E8-P1 device guards are
  present with their required commits (`verify_e8p2.py` ancestry PASS).
- `ZVEC_GREP_E7_*` is absent from production source (grep hits are docs only).
- E4 catalog entries and encoder-path changes are behind non-default
  `e4-experiment`; the default catalog oracle test enforces 14 entries.
- No deleted files. Refactors with deletions are the no-op lazy-open path and
  content-addressed reuse/readback.
- New engine API surface is additive (`ZvecGrep::symbols`, `SymbolsOptions`);
  no CLI/management contract changed.

### Compatibility correction (important)

The E8-P2 handoff/report statement “no index schema change” is **branch-relative**
(P2 did not change v3). Relative to main, this merge changes
`CURRENT_INDEX_VERSION` from 2 to 3 through Index v3. Therefore:

- existing v2 indexes are rejected by merged code with
  `ZG.ENGINE.INDEX_INCOMPATIBLE` and explicit `--rebuild` guidance;
- no automatic migration runs and the v2 manifest is not mutated (verified);
- source files are untouched, so the rebuild path is safe;
- rollback to `6d5b523` is safe but a v3 index would then require a rebuild.

This is an explicit, qualified version bump, not an in-place migration.

## 3. Pre-merge gate matrix

| Gate | Evidence | Result |
|---|---|---|
| No unresolved safety/correctness regression | Source audit; full suite single-thread; one parallel-only `RESOURCE_BUSY` config-test flake reproduced on unchanged main source | PASS (flake pre-existing, not candidate) |
| No missing/foreign dependency or research-only code | `Cargo.lock` unchanged; E7 grep; E4 feature gate + default catalog test | PASS |
| Index compatibility/recovery proven | `index_version` covers unsupported 0/1/2/4/5/None: rebuild error, manifest unchanged; v3 reopen/query; crash/recovery suites | PASS |
| Vulkan/device fail-closed | Code review (`fail_closed`, explicit device checks); unavailable/failover tests; device guards | PASS |
| E6 measurement contract | `verify_e6.py` on P2 FTS/Vector/Hybrid/Hybrid-symbol: 4× contract PASS; `test_e6_negative.py` PASS | PASS |
| Safe rollback | Tag above; no schema migration; source-preserving rebuild path | PASS |

Pre-merge test/verification on `5bd3f26` (clean worktree, empty external target):

- engine default lib `765 passed / 0 failed / 28 ignored` (single-thread);
- engine Vulkan lib `764 passed / 0 failed / 28 ignored`;
- focused integration `17 passed`;
- harness `4 passed`;
- feature-off checks (`zg --release`, `zg-engine --release --no-default-features`) PASS;
- `verify_e8p2.py` PASS and negative mutations rejected;
- E6 verifier/negative PASS.

Flake note: `config::tests::updates_preserve_other_settings_and_do_not_erase_model_fields`
can fail with `RESOURCE_BUSY` only when the whole lib suite runs in parallel; it
passes alone, in the config subset and single-threaded, and the same failure
reproduced from a pristine `6d5b523` source export. It is a pre-existing
test-isolation/lock-contention issue in unchanged code, recorded as a
limitation, not a merge blocker.

## 4. Merge and post-merge verification

Post-merge verification ran on a **clean merged-worktree checkout of `58582d2`**
(no local untracked cargo config). Running the suite from the main worktree is
invalid on this host because its untracked `rust/.cargo/config.toml` forces
`TMPDIR` inside the repository, which now contains a v2 index; tests then resolve
that index and fail with the expected incompatibility error.

Merged HEAD results:

| Check | Result |
|---|---|
| Engine default, all targets | **765 lib + 59 integration passed, 0 failed, 28 ignored** |
| Engine Vulkan lib | **764 passed, 0 failed, 28 ignored** |
| Focused C#/FTS/no-op integration | 17 passed |
| Retrieval harness | 4 passed |
| Feature-off checks (`zg`, `zg-engine --no-default-features`) | PASS |
| E6 verifier (4 routes) / negative tests | PASS |
| `verify_e8p2.py` / `--negative` | PASS / 3 mutations rejected |

The 764 vs 765 difference is explicit and benign:
`explicit_accelerator_unavailable_is_fail_closed` is `cfg(not(feature="vulkan"))`.

## 5. CLI smoke on merged HEAD

Built from merged sources: CPU `zg` (release) and Vulkan-enabled `zg`
(`--features zg-engine/vulkan`, release), external target dir, model cache
`~/.zvec-grep/models`, isolated CLI home.

| Scenario | Result |
|---|---|
| CPU cold index (14 files) | 14 added, 196 entities, v3, saved `device=cpu` |
| FTS / vector / hybrid queries | 1 / 5 / 5 items, expected declarations |
| CPU repeated no-op (same config) | 22–28 ms, “already up to date”, manifest/tree unchanged |
| CPU incremental (2 changed files) | 2 modified, 34 entities; new symbol searchable |
| Cross-device write (`cpu` index, `--device vulkan`) | rejected: different embedding device; manifest unchanged |
| Legacy `auto` descriptor | query OK; write rejected (`legacy auto-device…`); manifest unchanged |
| `--rebuild --device cpu` | v3, `device=cpu`, 198 entities |
| Workspace relocation | query OK; incremental 1 modified; device preserved |
| v2 index | query/index rejected with rebuild guidance, manifest unchanged; explicit rebuild → v3 |
| Crash (kill -9 during first build) | only locks/temp left; rerun indexed 62/62; query OK |
| Vulkan cold index (6 files) | 6 added, 99 entities, v3, saved `device=vulkan` |
| Vulkan index queries | FTS/CPU-device/Vulkan-device all return items |
| Vulkan incremental write | rejected (rebuild-only); explicit rebuild OK |
| Vulkan repeated no-op (same explicit config) | 23–30 ms, manifest unchanged |
| Vulkan index + `--device cpu` write | rejected, manifest unchanged |

Vulkan no-op **without** repeating `--device`/`--model-cache` fails closed with
“incremental writes to accelerator-backed indexes are not supported” because the
configuration no longer matches the published manifest, so the read-only
no-op preflight is skipped and the conservative write guard applies. This is
fail-closed, not data corruption; it is a UX limitation, not a partial write.

## 6. Retrieval and performance parity

- Frozen E6 hybrid metrics (E8 receipts, verifier re-checked on merged HEAD):
  R@10 **0.6142**, R@50 **0.8025**, nDCG@10 **0.5387**, solved@10 **0.4444**;
  CPU and Vulkan hybrids remain identical.
- E2 counters/receipts reused and re-verified: cold 653 requests → 643 unique;
  one-file incremental 1 computed / 9 reused; line shift 43 fetched / 1 computed.
- The CLI smoke uses `local/potion-code-16m-v2` (model2vec, CPU execution): CPU
  6-file cold 1.34 s / 385 MB peak RSS vs Vulkan-labelled 1.41 s / 391 MB. This
  is **parity**, not an acceleration measurement; E8's Vulkan gain applies to
  llama.cpp models and is preserved from the frozen E8 receipts
  (40 C# files: 282.76 s CPU vs 77.41 s Vulkan in P2).
- No full-392 P2 re-index was performed; E8 frozen quality receipts were reused,
  as authorized.

## 7. Defaults / device / safety status

- CPU remains the default and is the only backend with incremental writes.
- Vulkan/CUDA/Metal are explicit opt-in and rebuild-only for writes.
- Legacy saved `auto` is query-only; writes fail closed.
- Cross-device writes are rejected before mutation.
- Workspace relocation preserves the concrete device and clears cache paths.
- Explicit unavailable accelerators fail closed (no silent CPU fallback).
- E7 production hooks absent; E4 remains a non-default research feature.
- No installed binary, service, production index or daemon was changed or
  restarted.

## 8. Remaining limitations / release blockers

1. Accelerator incremental writes remain unsupported by contract.
2. No full-392 P2 A/B; E8 quality/performance receipts are reused.
3. CUDA `Auto`/CUDA identity remains outside scope.
4. CPU backend-build identity is per-fragment reuse, not an index-level token.
5. Parallel-only config-lock test flake (pre-existing) is not fixed here.
6. Accelerator no-op requires the same explicit device/model-cache invocation;
   otherwise the pass fails closed rather than reporting a no-op.
7. `rust/docs/e8p2-integration-20261115.md` §9 and the handoff phrase “no index
   schema change” must be read branch-relative; see §2 here for the
   v2→v3 compatibility impact on main.

None of these blocks the controlled merge; all are release-note / owner-review
items.

## 9. Artifacts and cleanup

- Receipts/logs: `/home/masis/.cache/zvec-grep/final-merge-20261115/`
  (`logs/` pre/post test logs, verifier JSON, E6 outputs;
  `smoke/` CLI logs and manifests; `vulkan-sdk/` local SPIRV-Headers package).
- Historical receipts preserved:
  `/home/masis/.cache/zvec-grep/e8p2-20261115/receipts/` (104-file SHA256SUMS
  verified) and `/home/masis/.cache/zvec-grep/e8-20261114/`.
- Rollback tag: `rollback/pre-e8p2-merge-20261115`.
- Build targets are external to the repository and were removed after
  verification; smoke workspaces contain only generated fixtures and indexes.
- Main worktree `work/zg-recovered` @ `58582d2`; integration worktree and the
  temporary merged worktree were removed. Local branch
  `work/e8p2-integration-20261115` remains as provenance.

## 10. Owner decisions still required

1. Decide whether to keep the merge on `work/zg-recovered` and release it.
2. Accept/communicate the v2→v3 rebuild requirement for existing indexes.
3. Accept the rebuild-only accelerator write contract (or authorize a later
   compatibility effort).
4. Decide whether a full-392 P2 A/B is required before release.
5. Decide whether to fix the pre-existing parallel test-lock flake separately.
