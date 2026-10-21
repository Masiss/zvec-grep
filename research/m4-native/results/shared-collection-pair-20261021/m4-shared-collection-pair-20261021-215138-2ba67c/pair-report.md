# Shared-collection pair outcome

Status: **OPERATION_BLOCKED / PARTIAL**. This was the already-authorized pair; the immutable-shard baseline ran once. The shared-collection candidate did not start.

## Baseline arm

- Workload: 500 docs, 5 rounds, 4 reader threads, 20 reader queries; arm `base-delta-vector-incremental-fts-no-analyzer-cache`.
- Writer commit p50/p95/p99/max: 596.23 / 655.97 / 655.97 / 655.97 ms.
- Reader latency p50/p95/p99/max: 2.185 / 3.601 / 3.879 / 3.879 ms.
- Base build: 629.71 ms; native vector build: 116.76 ms; M4 build: 480.87 ms.
- Whole process CPU/wall: 3179.67 / 3746.18 ms; physical writes 58,933,248 bytes; peak RSS 551,252 KiB.
- Post-close reopen: 494.01 ms; 500 FTS live docs; vector/FTS coherence errors: 0.
- Workload exit 0 and cleanup verified. Workspace accounting is marked `COMPLETED_WITH_WORKSPACE_UNCERTAINTY`; measurement quality is incomplete.

These are one baseline arm only—not a completed pair, candidate comparison, causal result, or performance win.

## Why continuation stopped

The runner initially failed while validating the saved arm: it selected the last JSON record (post-close reopen) instead of the first sustained-workload record, and assumed non-null `controller_cleanup` although `unit_cleanup.verified` was true. The raw output was preserved; the local parser/cleanup validation fix was checked against the saved record, without rerunning the baseline.

A fresh inter-arm preflight then blocked continuation: `MemAvailable` was 2,964,193,280 bytes versus the unchanged 4,294,967,296-byte requirement. The candidate arm was never launched. No retry or gate bypass was made. A future attempt requires a fresh recovery snapshot meeting the unchanged policy; this receipt does not establish when or why host capacity fell.

## Follow-up after memory recovery

The owner noted that current RAM availability was about 7 GB. A fresh candidate preflight then recorded **8,443,480 KiB** MemAvailable, and all three 10-second inter-arm recovery windows passed (minimums **8,431,836 / 8,330,216 / 8,375,312 KiB**). The existing pair continued without repeating the baseline.

The candidate process started once but exited with code **14** during setup, before any measurement rounds. `stdout.log` is empty; `stderr.log` reports `SHARED COLLECTION PERFORMANCE: read manifest`. Source inspection identifies the defect: `RunSharedCollectionPerformance` constructs `Coordinator` without first publishing its required `generation.manifest`; `Coordinator` calls `Recover`, which reads that manifest. The candidate workspace has no `generation.manifest`. Containment cleanup is verified. This is a harness/setup defect; **no candidate timing or comparison exists**, and no retry was made.

A corrected benchmark would need a rebuilt binary and a new matched pair; this run's baseline is not comparable to a future rebuilt candidate.

## Harness fix

Fixed `RunSharedCollectionPerformance` to publish `generation.manifest` at generation 0 before constructing `Coordinator`. The M4 bridge target rebuilt successfully; six focused shared-pair policy/recovery tests passed, Python compilation and `git diff --check` passed, and the saved baseline output still validates with the corrected receipt parser. The candidate was not rerun. Rebuilding replaced the executable at the original path, so its current hash differs from the hash recorded for the failed operation; the old operation's raw receipt remains unchanged.
