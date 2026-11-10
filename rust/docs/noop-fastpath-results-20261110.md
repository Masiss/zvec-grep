# No-op indexing fast path — experiment results (2026-11-10)

> Superseded in part by `noop-fastpath-final-qualification-20261110.md`: the
> publication skip described in section 2 ("C") was reduced to the read-only
> preflight after qualification found that storage newer than the manifest must
> still publish (`cb71a01`). Numbers below still hold; see that document and
> `noop-fastpath-handoff-20261111.md` for the current state.

Status: experiment branch only. Nothing merged, pushed or released; the
foundation branch and main are untouched.

| Item | Value |
|---|---|
| Worktree | `/home/masis/src/worktrees/zvec-grep/noop-fastpath-20261110` |
| Branch | `work/noop-fastpath-20261110` |
| Base | `d64e54e` |
| Commits | `ddb591b` lazy collections + no-write flush skip · `42407a0` read-only preflight + state-equality publication · `ddcc13d` completion event + configuration-change test · this document |
| Measurement inputs | `~/.cache/zvec-grep/out-fastpath` (external target, receipts, index copies) |

## 1. Verified cost and what changed

Before: a pass with no changed file still opened all four native collections
read-write (~356 ms cold, 329 ms of it the vector/FTS collection), flushed every
collection on close (touching a 34 MB vector index and rotating RocksDB
manifests), and rewrote the workspace manifest (~3.2 MB process FS output,
0.48 s wall in the earlier audit; 0.53/0.48 s in today's paired run).

After: the same pass costs **0.06 s**, writes **no index file**, and does not
rewrite the manifest.

## 2. Design (three minimal changes)

**B — lazy retrieval collections** (`storage/entities/mod.rs`,
`storage/fragments/mod.rs`, `storage/store/mod.rs`).
`Entities` and `Fragments` remember their path/schema and open the native
collection on first use (`OnceLock`). New storage still creates every collection
eagerly so the published layout is complete; a read-only open still fails fast
when a collection directory is missing. Fragment `flush`/`optimize`/`stats`
skip unopened collections, and `close` skips the flush entirely when the session
accepted no write and owes no maintenance (`state.writes == 0 && dirty == 0 &&
!needs_optimize`).

**A — read-only no-op preflight** (`pipelines/indexing/service.rs`).
`run_index_pass`'s scan/classify/diff is extracted as `plan_pass`
(`pipelines/indexing/pipeline.rs`) and reused. Before any read-write open, when
* all of: not rebuilding, clean-scan not eligible, no `optimize.pending` debt
  under the active index directory, no interrupted build (`needs_recovery`),
* and the manifest state matches (index version, storage generation, scan rules,
  source root, default model + routes + FTS config, embedding configs with API
  keys compared by decrypted plaintext),
the service opens storage **read-only**, plans the pass, and returns without
opening anything read-write if the plan has no added/modified/deleted candidates
and no unfinished/failed files. IDs are not allocated during the probe.

**C — no publication without a state transition**.
When a full pass reports zero added/modified/deleted/failed/pending files and
zero created entities, owes no maintenance and the workspace state already
matches, the manifest rewrite (and `updatedTime` bump) is skipped. The fast path
reports a `Done` progress event with "index is already up to date".

`plan_pass` is the same code the real pass runs, so "no work" is not re-derived
from mtimes or a cheaper heuristic: it is the pass's own diff.

## 3. Source evidence for every proof obligation

| Obligation | Mechanism |
|---|---|
| no add/modify/delete/rename | `plan_pass` → `compute_diff` (`pipeline.rs`), `files_added/modified/deleted` all zero required, `DiffPlan::candidates`/`deleted` empty |
| no unfinished/failed/pending | `compute_diff` maps any non-indexed existing file to `Pending`; candidates must be empty; `files_pending`/`files_failed` must be zero |
| no maintenance debt | marker read from `workspace.layout().index_dir()/optimize.pending`; a legacy zero-byte marker forces the full path (test) |
| no recovery owed | `crate::workspace::index::needs_recovery(&layout)?` |
| version/model/generation/selection valid | `manifest_state_matches`: version, generation, scan rules, source root, model identity/routes/fts, configs with plaintext credentials |
| durability barriers | fast path opens read-only and writes nothing; all writes still flush; the marker protocol is unchanged (`store/mod.rs`) |
| writer exclusivity | the fast path runs after the same exclusive workspace lock acquisition as every pass |
| readers | unchanged: read sessions still retire on generation change; the fast path never changes the generation |

## 4. Paired measurements

Identical index copies built by the baseline binary, then five cold-process
no-op passes per variant (alternating order was used for the change control):

| Corpus | Variant | wall (median, range) | user | sys | RSS | process FS output | index bytes over 5 passes |
|---|---|---:|---:|---:|---:|---:|---|
| dense 300 | baseline | **0.530 s** (0.46–0.56) | 0.29 s | 0.19 s | 280 MiB | 7.7 k blocks | 54 202 368 (grew from 54 149 120) |
| dense 300 | fast path | **0.060 s** (0.06–0.08) | 0.02 s | 0.04 s | **62 MiB** | **< 1 k (0.0)** | 54 149 120 (unchanged) |
| mixed 525 | baseline | **0.480 s** (0.46–0.51) | 0.28 s | 0.17 s | 279 MiB | 7.2 k blocks | 17 428 480 (grew from 17 375 232) |
| mixed 525 | fast path | **0.060 s** (0.06–0.06) | 0.02 s | 0.04 s | **63 MiB** | **< 1 k (0.0)** | 17 375 232 (unchanged) |

Warm in-process no-op (`index()` repeated on the same engine):

| Corpus | baseline index call | fast path index call |
|---|---:|---:|
| dense | 237 / 238 / 392 ms | 31 / 23 / 24 ms |
| mixed | 253 / 254 / 279 ms | 49 / 39 / 40 ms |

`info(include_status)` warm drops from 152–195 ms to 25–41 ms for the same
reason.

Changed-file control (fresh identical copies, three repetitions, alternating
order):

| Corpus | baseline change pass | fast-path change pass |
|---|---:|---:|
| dense | 2.56 s median (2.54–2.66) | 2.42 s median (2.41–2.66) |
| mixed | 1.55 s median (1.49–1.56) | 1.56 s median (1.54–2.38) |

Within host noise: the preflight adds one read-only open plus one scan
(~40–60 ms) before the real pass, and the real pass is unchanged.

**Stage-attribution caveat:** on a changed pass the fast-path build reports
`index_commit` ≈ 430–710 ms instead of ≈ 8–19 ms, because the native retrieval
collections now open inside the commit rather than during `IndexStore::open`.
`index_optimize` is unchanged within noise. Total wall is unchanged; do not
compare those stage numbers across builds without this context.

Query parity after no-ops: FTS result paths identical, vector and hybrid result
sets identical between variants; item counts identical. Symbol cursors issued
before a no-op remain valid (no `updatedTime` bump); a real commit still
invalidates them (test).

## 5. Verification

New tests:

- `tests/noop_fast_path.rs` (8): three repeated no-ops leave manifest bytes,
  `updatedTime`, every index file, stored file rows and entity documents
  untouched and never call the embedding provider; add/edit/rename/delete take
  the full path and publish; a saved configuration change publishes; a legacy
  `optimize.pending` marker forces maintenance and is cleared; a persisted
  failed file is retried; same-size/same-mtime keeps the documented skip; a
  no-op preserves query results and the symbol cursor while a commit
  invalidates it; two concurrent index calls are serialized and both end in a
  consistent no-op.
- `storage/store/tests.rs`: a read-write session that only lists files does not
  open the entity or fragment collections and records no write; a real write
  opens both.

Suites: `zg-engine` lib **753 passed / 26 ignored**; integration binaries
**808 passed, 0 failed**; `zg-cli` 185, `zg-daemon-protocol` 7,
`zg-transport-mcp` 60, `zg-host-native` 44 — all pass. `cargo fmt --check`
clean; `cargo clippy -p zg-engine --all-targets` reports exactly the same 22
pre-existing warnings as the baseline.

Fault check: a change pass killed with `SIGKILL` at 0.25 s left the file
pending; the next pass detected `modified: 1` and repaired it (1.85 s), and the
following pass was a no-op (39 ms). No false fast path.

The two tests above caught real defects during development: the debt marker was
first looked up in the storage directory instead of the index directory (the
fast path skipped a required maintenance pass), and the manifest comparison
initially failed every time because API keys are re-encrypted with a fresh nonce
on each load.

## 6. Remaining costs and limits

- Remaining ~60 ms per no-op: process start (~40 ms) plus a read-only open of the
  file/directory collections and one source scan. That is the floor for "prove
  the tree did not change".
- Workspaces where the clean-scan cache is eligible keep their existing early
  path; the fast path deliberately does not weaken clean-scan integrity.
- Local (non-remote) model workspaces still run model acquisition/compatibility
  checks on a no-op; its cost (artifact verification) was **not measured** here.
- `updatedTime` no longer advances on a pure no-op. This keeps symbol cursors
  valid; anything that used the bump as a "refresh happened" signal would need
  review (**UNKNOWN** whether any caller depends on it).
- The probe still reads the whole file table and scans the tree, so the cost is
  O(files) in I/O terms; measured scans are 3–6 ms at 300–525 files.
- No daemon/long-lived-session measurement; no local-model workspace benchmark.

## 7. Gate results and verdict

| Gate | Result |
|---|---|
| no-op wall reduction ≥ 50 % | **PASS**: −87…−89 % (0.53/0.48 → 0.06 s) |
| FS output near zero with no work | **PASS**: 7.2–7.7 k → < 1 k blocks (0.0 k median); index bytes unchanged over five passes |
| changed-file indexing not significantly slower | **PASS**: 2.42 vs 2.56 s dense, 1.56 vs 1.55 s mixed |
| no extra stale exposure or missed source | **PASS**: the decision is the pass's own diff; unfinished/failed files, markers, recovery and configuration changes all force the full path (tests) |
| no schema/version change, rollback/recovery intact | **PASS**: index version unchanged, all existing crash/recovery suites pass |
| snapshot/cursor correctness | **PASS**: no-op keeps cursors valid, commits invalidate them |

**Verdict: GO.** The fast path is provably no-op: it triggers only on the pass's
own empty diff plus an unchanged manifest state, opens storage read-only, writes
nothing, and keeps every existing durability, recovery and freshness barrier.
The remaining 60 ms is process and scan cost, not avoidable work.
