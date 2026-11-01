# Index/Finalize Qualification V1 — measured result

Status: **INCONCLUSIVE (formal protocol)**; measured memory/contention is
**GOOD_ENOUGH**. No #10 candidate was demonstrated.

Protocol baseline: `2478776`; preregistration: `671b106`. No engine, scheduler,
policy, threshold, index-format, UX V2, or #10 change was made.

## Execution

- 15/15 cells completed.
- **45/45 measured repetitions** have receipts.
- 0 failed receipts after expected watcher cancellations were classified.
- One real execution incident: the first matrix attempt hit a full filesystem
  (222 MB free, 100% used) during `4_restart_delete`. Completed cell indexes
  were archived/removed and the run resumed; that cell and all 8-workspace
  cells then completed. No threshold was changed.
- Fixture manifest: `57ce6408a8aeacedd439f9173fd3a394ab09ba0af2b3c8a576990b307134804d`.

## Per-cell peaks and timing

| Cell | Reps | cgroup peak max | PSS peak max | RSS peak max | swap max | submit→done median | storage |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1_clean | 3 | 2.27 GiB | 0.68 GiB | 0.70 GiB | 0.0 MiB | 1.54 s | 934.9 MiB |
| 1_dirty_delete | 3 | 1.97 GiB | 0.80 GiB | 0.82 GiB | 0.0 MiB | 0.52 s | 934.9 MiB |
| 1_dirty_insert | 3 | 0.48 GiB | 0.51 GiB | 0.53 GiB | 0.0 MiB | 1.55 s | 934.9 MiB |
| 1_restart_delete | 3 | 2.19 GiB | 0.62 GiB | 0.64 GiB | 0.0 MiB | 1.53 s | 934.9 MiB |
| 1_restart_insert | 3 | 2.31 GiB | 0.73 GiB | 0.74 GiB | 0.0 MiB | 0.53 s | 934.9 MiB |
| 4_clean | 3 | 4.22 GiB | 1.15 GiB | 1.16 GiB | 0.0 MiB | 2.05 s | 3739.6 MiB |
| 4_dirty_delete | 3 | 4.90 GiB | 1.63 GiB | 1.64 GiB | 0.0 MiB | 2.08 s | 3843.5 MiB |
| 4_dirty_insert | 3 | 4.72 GiB | 1.65 GiB | 1.66 GiB | 0.0 MiB | 2.05 s | 3739.6 MiB |
| 4_restart_delete | 3 | 4.26 GiB | 0.93 GiB | 0.97 GiB | 71.2 MiB | 2.06 s | 3739.6 MiB |
| 4_restart_insert | 3 | 4.28 GiB | 1.43 GiB | 1.45 GiB | 0.0 MiB | 2.56 s | 3739.7 MiB |
| 8_clean | 3 | 4.80 GiB | 0.90 GiB | 0.93 GiB | 136.9 MiB | 3.61 s | 7479.2 MiB |
| 8_dirty_delete | 3 | 4.40 GiB | 1.18 GiB | 1.21 GiB | 0.0 MiB | 3.16 s | 7479.2 MiB |
| 8_dirty_insert | 3 | 4.10 GiB | 1.17 GiB | 1.19 GiB | 80.3 MiB | 3.07 s | 7479.2 MiB |
| 8_restart_delete | 3 | 4.28 GiB | 1.34 GiB | 1.35 GiB | 6.1 MiB | 4.11 s | 7479.2 MiB |
| 8_restart_insert | 3 | 4.96 GiB | 1.30 GiB | 1.33 GiB | 0.3 MiB | 3.08 s | 7479.2 MiB |

`cgroup peak` is `memory.peak` for the systemd scope containing daemon + CLI +
engine descendants. PSS/RSS are sampled from the cgroup process set; page cache
is included in cgroup peak but not in process PSS/RSS.

## Envelope comparison (measured subset)

| Metric | Envelope | Measured worst | Result |
|---|---:|---:|---|
| Total peak PSS | ≤ 7 GiB | 1.65 GiB | pass |
| Total peak RSS | ≤ 10 GiB | 1.66 GiB | pass |
| cgroup `memory.peak` | ≤ 8 GiB | 4.96 GiB | pass |
| Swap increase | ≤ 512 MiB | 136.9 MiB | pass |
| Memory PSI full p95 | ≤ 5% | 0.0% | pass |
| I/O PSI full p95 | ≤ 10% | 6.34% | pass |
| Queue-full | 0 | 0 observed | pass |
| Single-writer/root | 0 violations | 0 | pass |
| Job failures | 0 unexpected | 0 | pass |

Host `MemAvailable` per run, diskstats, and cgroup CPU/IO counters were not
stored by the runner in every receipt. The preflight host snapshot passed, but
the per-run host envelope is incomplete.

## Correctness

- FTS retrieval, delete visibility, restart/reopen recovery, and job-failure
  checks passed for all 45 repetitions.
- Single-writer checks found no overlapping running writers per root.
- **Vector-route parity** was validated separately on a fresh representative
  fixture with the deterministic insert/delete/restart checks:
  `results/vector-parity-ws1.json` (all four checks pass). The validation uses
  an 8 KiB inserted file, matching the fixture size, not a tiny probe file.
- The vector check was **not** run for every one of the 45 matrix receipts.
  Cell index state had already been removed after the matrix to recover disk
  space, so a full per-cell vector rerun would require rerunning the matrix.
  Formal protocol verdict therefore remains INCONCLUSIVE.

## Storage

Index storage is approximately linear in workspace count:

- 1 workspace: ~0.94 GiB
- 4 workspaces: ~3.74 GiB
- 8 workspaces: ~7.48 GiB

This is storage, not finalize memory. It is material (roughly 0.94 GiB per
2,000-file workspace) and should be tracked separately from finalize policy.

## Verdict

- **Formal protocol verdict: INCONCLUSIVE**, because vector parity was not
  checked across every matrix cell; targeted vector parity passed.
- **Measured memory/contention: GOOD_ENOUGH.** All measured envelope gates pass;
  dirty insert/delete and clean finalize stay below 5 GiB cgroup peak and
  1.7 GiB process PSS/RSS.
- **Disk-full incident:** only the failed first attempt at `4_restart_delete`
  is invalid. The successful resumed cell and all later receipts were produced
  with >=25 GiB free and are retained; no raw evidence was rewritten.
- **M3 dirty finalize:** no memory/contention evidence was found to reopen it.
- **Deferred Maintenance #10:** not indicated by this baseline. No comparative
  #10 experiment was run, so no improvement is claimed.

## Required follow-up

Run vector-route parity for the same fixture/matrix, then re-issue the formal
verdict. Also record per-run `MemAvailable`, diskstats, and cgroup cpu/io
counters if a full envelope claim is required.
