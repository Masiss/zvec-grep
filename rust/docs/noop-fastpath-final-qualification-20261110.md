# No-op fast path — final integration qualification (2026-11-10)

Scope: audit only, plus one minimal correctness fix found during the audit.
Nothing merged, pushed or released; main stays `9444eed` and the foundation branch
stays `d64e54e`.

| Item | Value |
|---|---|
| Worktree | `/home/masis/src/worktrees/zvec-grep/noop-fastpath-20261110` |
| Branch | `work/noop-fastpath-20261110` |
| HEAD | `0620189` |
| Fast-path commits | `ddb591b` lazy collections · `42407a0` read-only preflight · `ddcc13d` completion event · `cb71a01` **fix: never fast-path storage newer than the manifest** · `0620189` real-cursor test |
| Dev-only commits | `f6001e4` first results doc (partly superseded) · `52b6fca` local-model decomposition harness |
| Measurement root | `~/.cache/zvec-grep/out-fastpath`, `~/.cache/zvec-grep/out-qual` |

## 1. Audit A — timestamps and snapshot contracts

Every consumer of `updatedTime`, the storage generation and the symbol cursor:

| Consumer | Uses | Effect of not bumping on a no-op |
|---|---|---|
| Symbol cursor (`pipelines/symbol_lookup.rs:100,205-240`) | binds `generation` + `updated_ms` + query hash | cursor stays valid, which is correct: the snapshot did not change |
| `InfoResult.updated_epoch_ms` → MCP `index_status.updated_time` (`api/info.rs:168,238`, `zg-transport-mcp/src/lib.rs:2157`) | display | shows the last real publication instead of "now"; no behavioural consumer |
| Read-session cache (`storage/read_session.rs:148`) | keys on `storage_generation` only | no-op does not change the generation → unchanged |
| Freshness (`indexed_search/context.rs:583-598`) | file size/mtime + content hash | independent of the manifest → unchanged |
| Daemon (`zg-daemon/src/*`) | its own `updated_at` record | no manifest consumer |
| CLI status fixture (`zg-cli/src/status.rs:273`) | placeholder | none |

Verified behaviourally: a genuine `next_cursor` taken from a two-file declaration
continues across a no-op pass and is rejected after a commit
(`real_symbol_cursor_survives_a_noop_and_dies_on_commit`); repeated no-ops leave
the manifest bytes and `updatedTime` identical and keep FTS/vector/hybrid results
unchanged.

**F6 (same-millisecond token collision)** — pre-existing, unchanged: the cursor
check is equality on (`generation`, `updated_ms`); two commits inside one
millisecond would produce the same token. The fast path *reduces* how often the
token changes but does not touch commit semantics. Clock rollback makes
`updatedTime` non-monotonic; a cursor from the newer time is then still rejected
(equality), so the failure mode is fail-closed. Both remain **UNKNOWN** in
production terms and are out of scope here.

**Introduced hazard found and fixed (`cb71a01`).** A pass can commit file rows and
die before `publish_index_state`. Those rows carry `indexed_epoch_ms` later than
the still-published `updatedTime`. The previous fast path only compared the
workspace *configuration*, so the next pass saw no file work and skipped
publication, leaving a manifest older than the stored content while cursors bound
to the old snapshot stayed valid.

Fix: `plan_pass` now reports `published_state_current` — every stored
`Indexed` row's timestamp must be at or before the manifest time that is *still
published* (the new workspace carries this pass's timestamp, so it cannot be used
as the reference). The preflight refuses the fast path when that fails, and every
non-preflight pass publishes unconditionally again, so the baseline semantics are
restored for any full pass. Tested by restoring an older manifest over newer rows
and asserting the next pass republishes (`storage_newer_than_the_manifest_republishes_instead_of_noop`).

**Verdict A: PASS**, with the fix. No caller treats `updatedTime` as a last-check
time; F6/clock-rollback limitations are pre-existing and unchanged.

## 2. Audit B — concurrency and recovery

| Mechanism | Evidence |
|---|---|
| writer exclusion | the fast path runs after the same exclusive workspace lock as every pass; concurrent in-process and cross-process passes serialize |
| reader safety | the preflight opens storage **read-only**, which takes a *shared* `storage.lock`; readers are unaffected and no-op passes do not change the generation |
| native registry | a read-only open reuses an existing in-process read store or opens its own; a writer is never open at preflight time (lock) and the probe closes before any write path |
| source edits racing the pass | the plan is a snapshot; a file edited during the pass is caught by the next one — tested (`edit_racing_a_noop_pass_is_never_lost`, content hash of the stored row matches the edited file) |
| debts | `optimize.pending` under the active index directory forces the full path and is settled by it |
| unfinished/failed files | `compute_diff` maps any non-indexed row to `Pending`; candidates must be empty → full path (tested with a persisted failed row) |
| interrupted build/publication | `needs_recovery` forces the full path; publication is atomic and the new row-time check covers the pre-publication window |
| configuration change | manifest-state equality (scan rules, version, generation, source root, model identity, plaintext credentials) → full path (tested) |
| any probe error | storage open error or plan error logs and returns `None` → full path |

Multi-process evidence: 10 concurrent no-op passes (5 pairs) with the local model
on one workspace — 0 failures, manifest bytes byte-identical afterwards, and the
index tree (path, size, mtime) unchanged.

**Verdict B: PASS.**

## 3. Audit C — local-model no-op

Model `local/embeddinggemma-300m` (GGUF Q8_0, 333 590 944 B, sha256 `b5ce9d77…`,
768-d), verified offline against the catalog artifact list; private cache via a
hardlink, `ZVEC_GREP_HOME` pointed at the scratch directory, `allow_remote=false`.
8 real C# files from the vendored Arrow sources; index built once (797 s-class
build was the 60-file corpus earlier; this one took 47.6 s for 106 entities).

| Variant | no-op wall | user | RSS | FS blocks |
|---|---:|---:|---:|---:|
| baseline | 0.75 s (0.71–0.76) | 0.44–0.49 s | 280–281 MiB | 6.7–7.1 k |
| fast path | 0.06–0.08 s | 0.02–0.03 s | 59–60 MiB | 8 blocks |

Decomposition on the fast-path build (same process):

| Stage | Time |
|---|---:|
| `info(include_status=true)` — read-only open + list + scan, **no model acquisition** | 26–32 ms |
| `index()` — model acquisition + manifest comparison + the same probe | 28–46 ms |

So model acquisition/verification for a cached local model costs **≈0–15 ms** and
the artifact is not re-hashed per pass (a 333 MB rehash would take ~1 s). Mock and
local no-ops are within 10 ms of each other (0.06 s dense-300 mock vs 0.07 s
8-file local), so there is **no significant local-model initialization cost left**
in the no-op path. The remaining ~40–60 ms is process start plus the read-only
open and source scan. Model *loading* only happens when embeddings are needed; a
no-op never prepares the model.

Unmeasured: an uncached or partially downloaded local model (acquisition would
download), and long-lived daemon sessions.

**Verdict C: PASS** for a cached local model.

## 4. Resource comparison (paired, same index copies)

| Corpus | Variant | no-op wall (median) | FS blocks | RSS |
|---|---|---:|---:|---:|
| dense 300 | baseline | 0.50 s | 7.8 k | 280 MiB |
| dense 300 | fast path | **0.06 s** | **~0** | **62 MiB** |
| mixed 525 | baseline | 0.54 s | 7.0 k | 279 MiB |
| mixed 525 | fast path | **0.06 s** | **~0** | **63 MiB** |

Changed-file control (fresh identical copies, three passes): dense 1.53 s
baseline vs 1.61 s fast path; mixed 1.39 s vs 1.40 s — within host noise.

## 5. Integration verification

**Ancestry and dependency.** The branch is linear on `d64e54e`:
`d64e54e → ddb591b → 42407a0 → ddcc13d → f6001e4 (doc) → cb71a01 (fix) → 0620189 (test) → 52b6fca (dev harness)`.
The fast-path code does not call any G1–G4 or Index v3 code path: it touches
`storage/{entities,fragments,store}`, `pipelines/indexing/{pipeline,service}` and
adds tests. Because the branch base already contains v3/G1–G4/diagnostics,
merging the branch wholesale would carry those; integrate by cherry-picking
`ddb591b`, `42407a0`, `ddcc13d`, `cb71a01`, `0620189` onto the intended base
(rebased), and drop the dev-only `52b6fca` (plus `examples/e3_spike.rs` and the
`index_bench` profile phase if harness changes are not wanted).

**Format, migration, rollback.** No schema, index-version, or `optimize.pending`
format change; `indexVersion` stays 3. An index built by the fast-path binary is
read by the baseline binary (no-op 0.44 s, queries return 10/10/10) and the
reverse (no-op 0.03 s, queries 10/10/10). Rollback is a plain revert: the baseline
re-reads the same manifests and markers.

**Query parity.** FTS paths identical and vector/hybrid result sets identical
between variants after no-ops and after a change pass.

**Suites.** `zg-engine` lib **753 passed / 26 ignored**; integration **811
passed, 0 failed** (including 11 fast-path tests); `zg-cli` 185,
`zg-daemon-protocol` 7, `zg-transport-mcp` 60, `zg-host-native` 44; `cargo fmt
--check` clean; clippy reports exactly the 22 pre-existing warnings.

## 6. Open issues

| Issue | Class |
|---|---|
| F6 same-millisecond cursor token | **pre-existing**, unchanged, out of scope |
| clock rollback makes `updatedTime` non-monotonic | **pre-existing**, fail-closed |
| `updatedTime` no longer advances on a pure no-op | **intended change**; verified no consumer depends on it; cursors stay valid |
| uncached local-model artifact acquisition on a no-op | **unmeasured** (needs download; would be a full-path cost anyway) |
| daemon long-lived sessions and reader-cache pressure | **unmeasured** |
| remaining ~40–60 ms per no-op (process start + scan + read-only open) | expected floor |

No introduced regression remains open; the one introduced hazard found during
this audit was fixed and covered by a test.

## 7. Verdict

**READY_TO_INTEGRATE**, on the condition that integration cherry-picks the five
fast-path commits listed above onto the intended base (not a branch merge that
would also promote Index v3 / G1–G4), and that the pre-existing F6 same-millisecond
cursor limitation is accepted as-is or tracked separately. All three audits pass:
timestamps/snapshots (A), concurrency/recovery (B) and local-model cost (C).
