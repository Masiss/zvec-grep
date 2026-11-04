# Resource-only exploratory contract amendment — 2026-10-21

This amendment updates interpretation for explicitly operation-bound M4 scalar-open **resource-only exploratory** work. It does not authorize another run and does not change qualification, paired benchmark, legacy diagnostic, or production contracts. The machine-readable authority is `research/m4-native/docs/benchmark-policy.json` under `resource_only_exploratory_contract`.

## Revised interpretation

- **Load-1:** retain baseline/runtime values and historical threshold comparisons as observations. Load-1 is not a hard admission or abort gate in this scoped mode.
- **Host swap/reclaim:** record and analyze baseline-to-runtime swap-in/out, direct scan/steal, and allocstall deltas. Positive deltas alone are not distress; host-wide changes do not establish M4 attribution. OOM-kill increments, counter regressions, and missing required telemetry remain blocking.
- **Hard controls retained:** operation-approved host RAM/disk reserves and any approved swap-capacity check, the existing host I/O PSI guard, finite cgroup CPU/memory/swap/task caps and limit/OOM distress, watchdog/timeout, workload correctness, required safety telemetry, and verified cleanup/containment remain enforced. This amendment does not lower or invent reserve values.
- **Workspace:** races or lost coverage remain `UNKNOWN`/`INCOMPLETE`; sampled traversal is not a lifetime peak or capacity proof. Soft-budget observations/warnings are never PASS or hard-cap evidence. Existing fatal scope, permission, I/O, overflow, and required safety-telemetry failures still block.
- **No retroactive rewrite/retry:** raw receipts and run-time records remain immutable. This amendment changes their policy interpretation only; it is not authorization to retry.

## Effect on the 84-open rehearsal

The run completed 84 API opens and cleanup was independently verified. Under the amended scoped contract, load-1 5.19 (historical envelope 3.14) and host deltas of swap-in +177 pages, swap-out +1,576 pages, direct scan +270 are recorded context, not independent blockers; source attribution is unknown. The approved 3 GiB MemAvailable and 8 GiB free-disk floors passed. The workspace observer nevertheless recorded `MUTATION_COVERAGE_LOST` (`events exceed one bounded drain`), so workspace accounting remains `UNKNOWN`/`INCOMPLETE`, the sampled final per-root sizes are not a verified peak, and this is not a complete resource measurement or PASS. No retry, latency benchmark, diagnostic, or qualification run is authorized by this amendment.

The original report and receipt preserve what was recorded under the prior interpretation; see [REPORT.md](REPORT.md) and the immutable [rehearsal receipts](m4-scalar-open-rehearsal-20261021/). The current summary and implementation are in [benchmark policy](../../docs/benchmark-policy.md), [README](../../README.md), and [canonical handoff §67](../../../../rust/docs/m4-incremental-native-index.md).

## Verification scope

The policy and runner changes are software-contract changes only. Focused policy tests validate that load remains visible but nonblocking in this scope, host counter deltas are recorded while OOM/counter-integrity problems block, and workspace UNKNOWN/INCOMPLETE semantics remain intact. No native workload was rerun.

*Research only; no production or qualification claim.*
