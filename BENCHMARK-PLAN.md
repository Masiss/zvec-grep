# Three-way benchmark plan — fork / upstream / integration

Status: **PREPARED, NOT EXECUTED.** No benchmark operation ID has been issued and
`docs/benchmark-policy.json` still authorizes zero pair operations. This plan
does not change safety gates, cgroup/qgroup limits, or production defaults.

## Versions

| Arm | Revision | Worktree |
|---|---|---|
| Fork | `abaf7bf` (`origin/work/zg-optimization`) | existing fork worktree, read-only for measurement |
| Upstream | `e1f93d2` (`upstream/main`) | clean baseline worktree |
| Integration | `integration/upstream-rust` HEAD | this worktree |

Build all three with the same profile, feature flags, toolchain, and
`TMPDIR="$PWD/.tmp"`. Record binary/library SHA-256 and `cargo metadata` lockfile
hash per arm.

## Corpus and workload

Primary corpus:

- Source: `~/itc/camera/AttendanceSafetyCamera/**/*.cs`
- Selection: first 421 lexicographic files, 4,693,920 bytes
- Manifest: `9bff98d3…9716` (record full SHA in the run receipt)
- Pilot: first 64 files for smoke/ordering checks

Same corpus bytes, same ignore rules, same embedding model and revision for all
arms. Do not let one arm use a different tokenizer/model cache.

Workload sequence per arm (one fresh workspace per repeat):

1. Initial index.
2. No-op rescan (unchanged files).
3. Modify one file with one changed entity; verify only the changed fragment is
   re-embedded on the integration arm.
4. Rename one file.
5. Delete one file.
6. Reopen the index and run retrieval queries.
7. Optional crash/recovery check separately; it is correctness, not a timing
   sample.

## Resource envelope

Use the same envelope for all arms; do not tune one arm:

- 2 CPU, zero swap, cgroup memory 1 GiB for the 421-file corpus.
- 512 MiB for the 64-file pilot.
- Workspace qgroup 384 MiB (or the already-approved policy value); record actual
  referenced/limit.
- `TMPDIR` under the worktree; no `/tmp` measurement artifacts.

If an arm cannot start under the envelope, record `OPERATION_BLOCKED`; do not
raise the cap to obtain a pass.

## Measurements

Per repeat and arm:

- Wall time for each phase; index throughput (files/s, bytes/s).
- p50/p95 query latency for the same query set.
- Peak RSS/PSS and anonymous memory; swap must remain zero.
- Embedding inputs, vectors produced, and **vectors reused** (integration arm);
  manifest hit/miss count.
- Finalization duration, state-lock wait count/total/max (where exposed).
- Reopen time and retrieval parity (top-k IDs/paths/scores).
- Counts: files indexed/unchanged/failed, entities, fragments, drops/errors.

## Ordering and repeats

- Counterbalanced arm order across repeats (ABC, BCA, CAB) to reduce thermal/cache
  bias.
- Three repeats per arm for the pilot; at least three for the 421 corpus if the
  resource policy allows.
- Record host load, memory/swap, pressure, affinity, and top processes immediately
  before/after each run.
- One run per condition unless a repeat is needed to answer a decision; flag
  single-run uncertainty.

## Acceptance / interpretation

Correctness gates before timing:

- Retrieval parity for the fixed query set.
- No lost/duplicated documents after rename/delete/reopen.
- Integration M2: unchanged fragment reuse is exact; changed fragment is
  re-embedded; no stale vector after fingerprint/model change.
- Integration M3-B: marker survives interruption; no lost optimization debt.

Performance gates (pre-register numbers before running):

- Integration p95 must not regress versus upstream by more than the agreed bar.
- M2 must reduce embedding inputs on the single-file-edit workload.
- M3-B must show lower state-lock wait or finalization stall without increasing
  query p95 beyond the bar.

If any arm is not comparable because of drops, queue saturation, or a different
accepted load, report **INCONCLUSIVE / NOT COMPARABLE**, not a win.

## Artifacts

Store under a new results directory, never overwrite research artifacts:

- `benchmark-manifest.json` (revisions, hashes, corpus manifest, envelope).
- Per-arm `preflight.json`, `run.json`, `stdout.log`, `stderr.log`.
- `summary.json` with paired deltas and raw counters.
- Host state before/after snapshots.

## Blockers / authorization

- `max_authorized_pair_operations: 0`; operation IDs are required.
- Full 421-file corpus may need the already-approved 1 GiB / 384 MiB envelope;
  record it as a resource-policy choice, not a silent change.
- No M4 benchmark is included; M4 remains research-only.
- This plan must be reviewed before any measurement is launched.
