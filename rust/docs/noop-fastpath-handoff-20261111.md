# No-op indexing fast path — handoff (2026-11-11)

## Current checkpoint

- Canonical source remains `work/zg-recovered` at `9444eed`. **Nothing is merged,
  pushed or released.**
- Foundation baseline: `work/foundation-opt-20261110` at `d64e54e` — untouched.
- Worktree: `/home/masis/src/worktrees/zvec-grep/noop-fastpath-20261110`
- Branch: `work/noop-fastpath-20261110`
- **HEAD: this document's commit** (`git rev-parse HEAD`), clean working tree.
  Code tip: `0620189`; `e8f2ce2` is the qualification report before this handoff.
- Protected `rust/target`, main tree, RAG/Selection/Expansion, held-out data,
  historical receipts and any installed binary/index/daemon were not modified.
- Build/measurement caches: `~/.cache/zvec-grep/rust-target-recovered-20261105`
  (external target), `~/.cache/zvec-grep/out-fastpath`,
  `~/.cache/zvec-grep/out-qual`. Index copies were deleted; receipts retained
  except where noted.

Read in this order before resuming:

1. [`noop-fastpath-final-qualification-20261110.md`](noop-fastpath-final-qualification-20261110.md)
   — audits A/B/C, the fix found during qualification, integration plan, verdict.
2. [`noop-fastpath-results-20261110.md`](noop-fastpath-results-20261110.md)
   — design, proof obligations, measurements (its publication-skip section is
   partly superseded by the qualification).
3. [`e3-e1-architecture-feasibility-20261110.md`](e3-e1-architecture-feasibility-20261110.md)
   and [`foundation-opt-phase-ab-20261110.md`](foundation-opt-phase-ab-20261110.md)
   — why the no-op cost mattered and how the earlier numbers were corrected.
4. This handoff — continuation state and the exact next authorized action.

Do not treat any older handoff or stop decision as authorization. The active task
must come from the current owner request.

## Verdict

**READY_TO_INTEGRATE** (cherry-pick, not branch merge). Audit A
(timestamps/snapshots), B (concurrency/recovery) and C (local-model cost) all
pass after the one fix found during qualification.

| Metric (no-op pass) | Baseline | Fast path |
|---|---:|---:|
| dense 300, wall | 0.50 s | **0.06 s** |
| dense 300, FS blocks / RSS | 7.8 k / 280 MiB | **~0 / 62 MiB** |
| mixed 525, wall | 0.54 s | **0.06 s** |
| local `embeddinggemma-300m`, wall | 0.75 s | **0.06–0.08 s** |

Changed-file passes are unchanged within host noise (dense 1.53 vs 1.61 s,
mixed 1.39 vs 1.40 s).

## What this branch contains

**Code (integration candidates, in order):**

| Commit | Change |
|---|---|
| `ddb591b` | entity/fragment native collections open lazily; `close` skips the flush when the session wrote nothing and owes no maintenance; new storage still creates the full layout eagerly; read-only opens still fail fast |
| `42407a0` | `plan_pass` extraction; read-only no-op preflight before any read-write open; manifest-state equality check; tests |
| `ddcc13d` | `Done` progress event for the fast path; configuration-change test |
| `cb71a01` | **fix**: never fast-path storage newer than the manifest; every non-preflight pass publishes again |
| `0620189` | real symbol-cursor continuity test |

**Dev-only (do not integrate):** `52b6fca` (local-model decomposition harness),
`examples/e3_spike.rs`, and the `index_bench` profile phase added in `42407a0`.
Documentation commits (`f6001e4`, the qualification, this handoff) travel with
the workstream, not with the code.

**Contracts established — do not weaken:**

1. The fast path runs only after the exclusive workspace lock, only when
   * not rebuilding / not clean-scan eligible / no `optimize.pending` debt /
     no interrupted build,
   * the manifest state matches (version, generation, scan rules, source root,
     model identity, FTS config, embedding configs with plaintext credentials),
   * `plan_pass` (the pass's own diff) finds no candidates, no deleted files and
     no unfinished/failed rows,
   * every stored row's `indexed_epoch_ms` is at or before the **still-published**
     manifest `updatedTime`.
2. The fast path opens storage read-only, writes nothing, and does not change the
   storage generation.
3. Every non-fast-path pass publishes the manifest unconditionally.
4. Any probe error falls back to the full path.

## Integration procedure

The commits were written on `d64e54e`; cherry-pick them onto the target branch in
order, after confirming the target has the same storage/pipeline shapes:

```bash
git worktree add -b <integration-branch> <path> <target-base>
git cherry-pick ddb591b 42407a0 ddcc13d cb71a01 0620189
# drop dev-only files if the harness change is not wanted:
#   rust/crates/zg-engine/examples/e3_spike.rs
#   the index_bench profile phase from 42407a0
```

Do **not** merge `work/noop-fastpath-20261110` wholesale: its history contains
Index v3, foundation G1–G4 and the diagnostics module, and a merge would promote
them. The fast-path code does not call any of that code.

Rollback: revert the cherry-picks. No schema, index-version or marker-format
change is involved (`indexVersion` stays 3), and both binaries read each other's
indexes.

Verification after integration: `cargo fmt --check`; clippy at the baseline
warning set; `zg-engine` lib + integration suites; the 11 tests in
`tests/noop_fast_path.rs`; paired no-op benchmark (dense + mixed) and one
changed-file control; cross-binary read check.

## Dependencies

- **Runtime/code:** none beyond the target base. No new dependency, no format or
  version change, no `unsafe`.
- **Behavioural:** reuses `compute_diff`/`classify_files`; the durable
  `optimize.pending` marker keeps its existing meaning.
- **Not required:** G1–G4 fetch/format optimizations, the C# v3 adapter,
  diagnostics, or E1 deferred maintenance.

## Evidence snapshot

- Paired no-op and changed-file numbers: qualification doc §4.
- Multi-process: 10 concurrent no-op passes (5 pairs) with the local model,
  0 failures, manifest bytes and index tree (path/size/mtime) unchanged.
- Interrupted publication (older manifest over newer rows) republishes instead of
  skipping (`storage_newer_than_the_manifest_republishes_instead_of_noop`).
- Edit racing a pass is never lost (stored content hash matches the source).
- Local model: acquisition adds ≈0–15 ms (no 333 MB rehash); no model load on a
  no-op; RSS 280 → 60 MiB.
- Suites: lib 753 passed / 26 ignored; integration 811 passed, 0 failed;
  `zg-cli` 185, `zg-daemon-protocol` 7, `zg-transport-mcp` 60,
  `zg-host-native` 44; clippy = 22 pre-existing warnings; fmt clean.

## Open items and known limitations

| Item | Class |
|---|---|
| F6 same-millisecond cursor token (equality on generation+time) | **pre-existing**, unchanged, out of scope |
| clock rollback makes `updatedTime` non-monotonic | **pre-existing**, fail-closed |
| `updatedTime` no longer advances on a pure no-op | **intended**; no consumer depends on it; cursors stay valid longer |
| uncached local model on a no-op (artifact download) | **UNKNOWN / unmeasured** |
| daemon long-lived sessions and read-cache pressure | **UNKNOWN / unmeasured** |
| ~40–60 ms floor per no-op (process + scan + read-only open) | expected |
| `rust/docs/development-tracks.md` referenced by `rust/AGENTS.md` is absent from this lineage | documented discrepancy, not created here |

Related workstreams (reference only, do not integrate from them):

- No-op cost audit + E3 pre-spike: `work/foundation-investigation-20261110` @
  `ff5ca06` (`rust/docs/foundation-noop-and-e3-prespike-20261110.md`).
- E1 bounded maintenance: `work/e1-bounded-maintenance-20261110` @ `6012ef4`
  (`rust/docs/e1-bounded-maintenance-results-20261110.md`) — NO-GO on its wall
  gate; not part of this integration.

## Exact next authorized action

No further work is authorized by this handoff alone. If the owner proceeds, pick
exactly one:

1. **Integrate** the five commits per the procedure above, then rerun the named
   verification and report the measured no-op/change numbers on the target.
2. **Hold** and leave the experiment branch as-is.
3. **Measure the remaining unknowns** (uncached local model, daemon session) as a
   separate bounded investigation; do not "improve" the fast path further without
   a new request.

Before any action: verify HEAD/worktree, re-read the qualification doc, and keep
`rust/target`, installed indexes and historical receipts untouched.
