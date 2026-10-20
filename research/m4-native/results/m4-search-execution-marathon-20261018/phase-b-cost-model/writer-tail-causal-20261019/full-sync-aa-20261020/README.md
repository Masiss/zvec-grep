# Authorized full-sync same-binary A/A — 2026-10-20

**Operation: OPERATION_BLOCKED at the single baseline resource preflight.** No ambient baseline, arm, workload root or performance result. No retry/poll, guard change, process termination or cleanup to create headroom.

## Prerequisites

The executable retained at `research/m4-native/results/durability-restore-20261020/m4_bridge_integration.full-sync` hashes to `ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f`. All 61 actual compiler dependencies, current `ldd` resolution/six shared libraries, and all 50 retained build/test manifest entries match. No rebuild/source substitution. Full outer FTS/WAL sync and focused crash verification remain valid; physical power-loss qualification is not claimed.

User systemd was **running**, no failed units or active M4 tests/benchmarks were observed, cgroup/telemetry prerequisites were present, and the containment runner source matched the source-matched 12/12 lifecycle regression receipt. No synthetic containment suite was rerun immediately before measurement. Live arm cgroup handshake/cleanup, baseline cadence/integrity and pressure derivation were NOT EXERCISED because the resource preflight stopped first. `/var/tmp` is Btrfs; planned roots were fresh private per-arm directories there, not TMPDIR-derived data roots.

The existing orchestrator still attached old fsync-elision prose and an old partial-build archive. A metadata-only correction allows `M4_AA_BUILD_RECEIPT` and takes the durability description from the existing configuration. This attempt's actual `pair.json` was checked to contain the full-sync receipt/hash and description. No enforcement, timer, workload, query mix, telemetry, runtime limit or recovery protocol changed. Existing synthetic policy regression: 7/7 PASS.

## Sole resource evaluation

At **2026-10-20 12:05:35 +07**, canonical JSON policy returned:

| Guard | Observed | Unchanged requirement | Outcome |
| --- | ---: | ---: | --- |
| M4-RG-MEM | 4,134,692 KiB | >= 5,477,762 KiB | FAIL; short 1,343,070 KiB |
| M4-RG-LOAD | 6.56 | <= 3.14 | FAIL; excess 3.42 |
| M4-RG-DISK | 22,096,510,976 B | >= 18,219,000,000 B | PASS |
| M4-RG-IOPSI | 1.06% | <= 4.46% | PASS |

Job `job-1335348-140` exited without ambient capture or any arm. Pair receipts: `../host-carryover-aa-pair-20261020T050534Z/`, including operation status and baseline preflight/host/no-launch JSON. `attempt-summary.json` records commands, environment, source/build receipt and exact outcomes; `prerequisites.json` records the earlier non-resource prerequisite check. The no-launch artifact seals and log are beside this file. The prerequisite check did not perform a resource readiness evaluation.

## Analysis and limits

Durability is no longer the measurement prerequisite blocker for this corrected executable. The blocker here is the two unchanged resource guards, not systemd, storage semantics or binary mismatch. The host snapshot records background activity (including high tsgolint CPU/RSS), but one snapshot cannot assign cause to this host condition, much less historical writer tails. No unrelated process was signaled.

There are no new cycles, vector substeps, post-M4 finalization timings, runtime PSI windows, process I/O or PSS to compare. A/A repeatability/order effects therefore remain unmeasured. The existing 80 historical cycles/320 readers and their source-level boundaries remain useful only at their established resolution; this no-launch cannot establish or refute H1–H5. Old elision binary measurements must not be relabeled as full-sync current performance. No independent analysis with new discriminating evidence remains in this attempt; repeating old analysis would not fill absent contemporaneous data.

**Next action:** preserve these receipts and stop this operation. A future attempt requires new explicit authorization plus all unchanged resource, baseline-pressure, integrity and containment requirements. Do not retry/poll under this authorization, tune thresholds or start an additional campaign. Writer-tail causal status remains INCONCLUSIVE; production is untouched.
