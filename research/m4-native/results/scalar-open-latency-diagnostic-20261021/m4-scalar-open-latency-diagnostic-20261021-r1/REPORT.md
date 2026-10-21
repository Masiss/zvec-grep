# M4 scalar-open latency diagnostic — 2026-10-21

**Outcome:** `COMPLETED_WITH_WORKSPACE_UNCERTAINTY` — the workload completed successfully, but workspace capacity remains **`WORKSPACE_CAPACITY_UNVERIFIED`**. Evidence class: **EXPLORATORY / NON-QUALIFYING**. This is not a qualification result, benchmark win, stable-tail estimate, or causal comparison.

## Authorization and scope

This was the single owner-authorized direct 0–6 scalar-open latency diagnostic under the amended exploratory contract. It was not a rerun of the resource-only rehearsal. No retry was made or authorized.

The one-process workload ran seven scalar counts (0–6) × HNSW/FLAT × three repetitions. Each case created and opened one empty collection, closed it, reopened it, closed it, then removed the case root. There were **84 C API opens**, zero documents, zero queries, and at most one collection root at a time. Each of the 84 individual API call intervals was recorded. `create_and_open` timing starts after schema construction; `reopen` timing is separate. Close, schema construction, initialization/shutdown, and cleanup are outside those per-call intervals. The native loop reported 5,689.557 ms; the contained command ran for 5.721 s. Exit status was 0 and all 84 samples plus the summary validated.

## API latency results

Values are **median [min, max] milliseconds over n=3**, per scalar count and vector type. The three repetitions are too few for stable tail claims.

| Scalar fields | HNSW create+open | HNSW reopen | FLAT create+open | FLAT reopen |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 16.065 [15.828, 17.713] | 10.008 [9.396, 11.197] | 15.832 [12.649, 17.232] | 9.194 [8.914, 10.750] |
| 1 | 43.742 [40.777, 48.413] | 27.460 [24.068, 31.351] | 38.881 [34.235, 46.829] | 21.558 [20.308, 26.482] |
| 2 | 64.444 [53.567, 67.323] | 32.745 [32.579, 38.028] | 47.300 [42.882, 54.953] | 29.863 [25.136, 46.255] |
| 3 | 114.267 [111.987, 120.400] | 49.947 [41.004, 54.945] | 113.154 [107.179, 122.228] | 44.009 [39.586, 49.981] |
| 4 | 149.026 [145.792, 149.182] | 53.183 [41.790, 61.039] | 167.324 [163.048, 172.533] | 44.586 [42.960, 52.513] |
| 5 | 126.489 [126.392, 152.867] | 44.753 [40.541, 47.056] | 130.347 [124.940, 130.410] | 47.747 [44.715, 58.333] |
| 6 | 152.468 [146.813, 152.510] | 53.432 [40.412, 53.672] | 138.218 [134.407, 154.019] | 49.181 [45.989, 52.601] |

All 84 raw calls and analysis are preserved in `aa-first/containment/stdout.log` and `latency-analysis.json`.

## What the measurement covers—and does not

The create+open API interval includes whatever schema interpretation, collection setup, and index initialization the native API performs internally; the harness has no internal phase instrumentation, so their separate costs cannot be inferred from this run. The reopen interval similarly covers the API call as a whole. HNSW was selected as an empty collection's vector-index type, but **no documents were inserted**, so this did not measure data-driven HNSW graph construction. The harness invokes no flush API, so it provides no flush timing. A separate instrumented trace would be needed to attribute time to collection/index setup, HNSW internals, or flush.

## Host context and containment

Fresh readiness passed the operation-specific hard floors: MemAvailable was 4,715,932 KiB against 3 GiB; `/var/tmp` free space was 28,964,388,864 bytes against 8 GiB. During the 54 watchdog samples, MemAvailable ranged 4,222,936–4,752,392 KiB, load-1 ranged 3.93–5.05, I/O PSI `full avg10` ranged 0.97–1.16%, memory PSI `full avg10` stayed 0, and CPU PSI `some avg10` peaked at 0.32%. Load exceeded the historical 3.14 envelope, but load was explicitly observational in this mode, not an admission/abort gate. A concurrent host process (`tsgolint`) was observed using 655% CPU before and 526% after; other busy processes were also present. This was not an idle or stationary host sample.

Host vmstat deltas over the observation window: swap-in +183 pages, swap-out +4,534 pages, direct scan +2,809, direct steal +1,426, movable allocstall +19, OOM-kill +0. Per the approved contract these are temporal host observations, not evidence attributable to M4 or standalone distress. `/var/tmp` free space ended at 28,963,115,008 bytes.

The owned cgroup limits were memory 1 GiB, swap 0, CPU 200%, tasks 128, and a 20 s runtime cap. Observed cgroup memory peak was 49,414,144 bytes (47.1 MiB), swap peak 0, peak task count 65, CPU usage 4,423,405 μs, throttled periods 0, and memory/swap/OOM events 0. Cgroup I/O accounting was unavailable, so no per-operation physical-write attribution is claimed.

## Workspace uncertainty and cleanup

The watchdog captured 54 workspace observations: 44 `COMPLETE_TRAVERSAL_NOT_ATOMIC`, 9 `PARTIAL_KNOWN_RACE`, and 1 `UNVERIFIABLE`. Across them it recorded 32 issue events: 22 directory-activity, 8 directory-changed, 1 entry-disappeared, and 1 `MUTATION_COVERAGE_LOST` (`events exceed one bounded drain`). One observed transient RocksDB temporary-file `ENOENT` and metadata-change reports occurred while collection files/directories were being published. Those observations were preserved; no retries or error suppression were used. The traversals all lacked atomic-snapshot semantics, so their `complete_snapshot` flag was false. There were zero soft-budget warning samples.

The largest *observed complete traversal* was 1,249,280 allocated bytes and 165,236 apparent bytes. These are observations, **not** a verified lifetime peak or capacity upper bound. Thus the runner correctly reported `measurement_quality: INCOMPLETE`, `workspace_accounting_status: UNKNOWN`, and `workspace_capacity_status: WORKSPACE_CAPACITY_UNVERIFIED`; it did not convert successful workload completion into capacity PASS. Overall status is caveated completion, not PASS.

The native run's owned collection roots were removed on normal completion. The empty, private operation workspace directory was removed with `rmdir` only; cleanup verification passed, the transient systemd unit was collected, and containment cleanup was verified. Raw receipts remain intact under this artifact directory.

## Reproduction identities and artifacts

- Operation ID: `m4-scalar-open-latency-diagnostic-20261021-r1`
- Policy-context SHA-256: `ad5e35feee8762122e9ae1d6406752ee4fae01abe1946612b4e7c5adccf55c3e`
- Diagnostic binary SHA-256: `b37310fa1749daa5ea8e8d5dd5b21de736c863b696d8ffd0baa0402c76c3316d`
- Exact launch plan and source identities: `operation-plan.json`
- Host-before/after context: `host-before.json`, `host-after.json`
- Full runner receipt, process/cgroup samples, watchdog config/summary, stdout/stderr, command start/end, and cleanup evidence: `aa-first/containment/`
- Per-call analysis: `latency-analysis.json`
- Workspace layout and cleanup receipts: `workspace-layout.json`, `workspace-cleanup.json`
- Integrity manifest: `SHA256SUMS.txt`

No qualification, paired benchmark, resource-only rehearsal, production change, or follow-up native attempt is authorized by this report.

## Verification

Only the `m4_native_scalar_open_latency_diagnostic` target was built (single build job); it did not run during compilation. The focused policy/contention suites passed **44/44** before launch. The actual native diagnostic ran exactly once and emitted/validated 84/84 API samples. The workspace caveat above remains part of the result.
