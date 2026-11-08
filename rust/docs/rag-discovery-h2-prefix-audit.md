# H2 prefix audit — full-prefix 500 vs bounded 500

Status: **the 19-vs-15 difference is not a stable 4-span capability loss; it is
unordered range/file iteration.**

Date: 2026-11-08. Tool:
`benchmarks/rag-impact/tools/discovery_h2_prefix_audit.py`.
Qualification: `results/discovery-h2-qualification.json`;
bounded: `results/discovery-h2-bounded.json`.

## Finding

- Full H2 pool: 15,683 unique ranges; first 500 by insertion order.
- Bounded pool: H1 units then ranges until 500.
- The two policies differ only in **iteration order** of `{u["path"] for u in h1}`
  and range enumeration; Python set/file/range order is not sorted.
- Running the same full/bounded logic with different `PYTHONHASHSEED` values
  changes per-case coverage:
  - D05 0/1, D06 1/2, D07 3/2, D12 1/0 across seeds.
  - A run with full=19 vs bounded=15 showed per-case losses D05, D06, D07,
    D12; the exact range inside D06/D07/D12 changes with order.
- Therefore the **four lost spans cannot be uniquely attributed** to a fixed
  candidate set. The prefix comparison is not reproducible until ordering is
  deterministic. The 19/25 “coverage@500” is an order artifact, not a deployable
  policy result.

## Consequence

- H2 bounded 500 = 15/25 (60%) is the reproducible representative until a
  deterministic ordering exists.
- H2 full 19/25 requires 15.7k ranges and nondeterministic order; it is not a
  valid bounded policy.
- This motivates H5 (deterministic budget-aware ordering); do not tune H2 by
  choosing a lucky order.
