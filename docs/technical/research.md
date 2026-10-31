# Research and evaluation components

[Engineering guide](README.md) · [Retrieval](retrieval.md) · [Rust rewrite roadmap](rust-rewrite-roadmap.md)

Research and benchmark code lives beside application source but is not part of the normal search runtime unless a package/build dependency says otherwise. Treat each experiment as a bounded evidence artifact tied to its own data, binary, protocol, and date.

## Research areas

| Area | Location | Purpose and evidence boundary |
| --- | --- | --- |
| M4 incremental native index | `research/m4-native/` | Standalone C++17 prototype, bridge, tests, policy-controlled workloads, WAL/segment/compaction and publication research. Not production storage or a runtime dependency. |
| Retrieval evaluation | `benchmarks/zg-retrieval/`, `docs/zg-retrieval-metric-review.md` | Dataset/gold cases, retrieval engines, metrics, and reports for search relevance. Results apply to the recorded corpus, query set, model, configuration, and metric. |
| SWE-QA evaluation | `benchmarks/swe-qa-bench/`, `docs/zg-retrieval-only-sweqa20-design.md` | Dataset suites, benchmark runners/agents, metrics, tests, and reports for repository question answering. Not a general user-visible quality guarantee. |
| BrowseComp+ | `benchmarks/browse-comp-plus/` | Separate benchmark suite, prompts, and runner; results are scoped to its tasks and protocol. |
| Rust milestone research | `rust/docs/` | Design reviews, feasibility, audit, handoffs, and M1/M3/M4 documents. Dated/experimental documents preserve their stated scope and status. |

## M4 status and navigation

M4's top-level README and execution checkpoint contain a long research history. For current state, start with `research/m4-native/results/ACTIVE-EXECUTION.md`, then follow the report and operation artifacts named by that checkpoint. As of the 2026-10-27 Gate 3 diagnostic, Gate A remains PASS only in research scope; the whole-pass fence remains REWORK, and selective visibility/short commit fencing is a tested but unqualified research candidate. The one newly authorized 64-file pair (`m4-rust-lifecycle-pair-20261027-dc8e1120`) stalled in its native arm at 50/64 and was stopped; the M4 arm never ran, so there is no matched result or query-latency conclusion. Cgroup and qgroup hard limits did not trigger, but watchdog cadence drift and observed CPU led to additional observer telemetry. Source tracing found a plausible shared `IndexStore` mutex contention path, not a proven cause. No further pair is authorized, numeric qualification thresholds remain unapproved, and production integration is not approved. See [`M4-RECOVERY-GATE-3-DIAGNOSTIC-20261027.md`](../../research/m4-native/results/M4-RECOVERY-GATE-3-DIAGNOSTIC-20261027.md), [`M4-RECOVERY-GATE-1-2-20261027.md`](../../research/m4-native/results/M4-RECOVERY-GATE-1-2-20261027.md), and the historical [`M4-GATE-B-QUERY-LATENCY-20261026.md`](../../research/m4-native/results/M4-GATE-B-QUERY-LATENCY-20261026.md) for evidence and limits.

This is a dated status, not a permanent characterization. On a later M4 task, refresh from the newest active checkpoint before updating this section. Do not use old reports, benchmark artifacts, or test counts as current authorization.

Important M4 references:

- `research/m4-native/AGENTS.md` — scope, safety and research invariants.
- `research/m4-native/docs/benchmark-policy.md` and `.json` — operation and containment policy.
- `research/m4-native/CMakeLists.txt`, `src/`, `include/`, `tests/` — standalone native implementation/tests.
- `research/m4-native/rust-bridge/` — research bridge only.
- `research/m4-native/tools/` — policy validation, containment, benchmark and report tooling.
- `research/m4-native/results/ACTIVE-EXECUTION.md` — active checkpoint and evidence pointers.

Research-only does not mean safe to run without controls. Follow nested guidance and the currently active policy for any measurement; never bypass a hard resource or containment guard to obtain a result.

## How to read a benchmark result

Before carrying a result into an engineering or release decision, verify:

1. Dataset/repository revision, split, query set, and exclusions.
2. Executable/source revision and build configuration.
3. Model/provider, endpoint, index parameters, candidate limits, and warm/cold state.
4. Metric definition, aggregation, paired design, sample count, and uncertainty.
5. Host/process load, memory/swap, CPU affinity/quota, disk and task limits, and whether guard/monitor gaps occurred.
6. Whether the report distinguishes correctness, relevance, latency, resource use, and operational qualification.

A single successful run proves only the exact measured condition. A missing/blocked arm means there is no comparison; an incomplete sampling schedule means the corresponding percentile is not comparable. Preserve failed/blocked evidence and its limits rather than converting it into a pass/fail product claim.