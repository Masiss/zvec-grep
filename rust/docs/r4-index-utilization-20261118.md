# R4 index utilization & call allocation

Branch `work/r4-utilization-20261118` (from `b6e76df`). Same isolated
SQL-symbol index (`r4-sql-symbols-20261118`, v3, 822 files, 31 s rebuild), same
frozen E3 plan, locked evaluation contract. 26 workflow tests pass; clippy
clean; citation 1.000.

## Gate 1 — why the better index did not reach R4

Per-arm replication on the SQL-symbol index: hybrid raw 0.5000, hybrid bounded
0.2500, R4 no-plan 0.3194, R4 plan 0.4861, GO 0.5722.

1. **The index gain is exactly one span, D09** (`EmployeeBaseViews`, hybrid
   0→1). R4 *already* selected D09 from the lexical route, so the baseline
   improved and R4 did not move — the gain was already inside R4's reach.
2. **D07/D12 need a name no component derived.** The procedure is invoked only
   as a *string literal*: `Context.Database.ExecuteSqlCommand("execute
   sp_UpdateOrgTotalEmployee")` at `OrganizationCommand.cs:1062`. `csharp_facts`
   deliberately treats such targets as `Candidate` / "dynamic/external SQL
   target" (a string literal is not an executed call — correct by the frozen
   contract), so typed traversal never produced the name. The blind plan never
   contained it either (planner had no corpus access).
3. **Expand never ran at all on the constrained cases.** Stage traces showed
   `expand: round=1 refs=0 calls_left=0` for D12: the derived-lexical round
   consumed all remaining calls, so the typed stage that *can* resolve SQL
   objects got zero budget.
4. **The one typed call was spent on unrelated names.** Even after reserving a
   call, D07 used it on a C# invocation while the SQL object name sat in a
   later-pooled file and was truncated by `MAX_REFS_PER_ROUND` — reference
   collection followed pool order, not specificity.

Verified with read-only probes (stage traces + per-call counters + the string
literal in source), reproduced from `r4-evidence-trace-20261116.json`
(`plan_stages`, `plan_stats`, `plan_pool`).

## Gate 2 — three experiments

| Exp | Hypothesis | Plan arm | Pool | Selected |
|---|---|---:|---:|---:|
| base | (SQL-symbol index) | 0.4861 | 18/25 | 8/25 |
| E1 | string-literal SQL object names enter typed refs as `sql_call_string` candidates, priority within a unit | 0.4861 | 18/25 | 8/25 — inert (truncation happens across units) |
| E2 | `expand_reserve = 1` call so the typed stage always runs | 0.4861 | 18/25 | 8/25 — inert (refs still pool-ordered) |
| **E3** | rank `sql_call_string` refs **across all seeds** before truncation | **0.5278** | **19/25** | **9/25** |

E3 accepted: +4.2 pp macro, +1 selected span (D12's procedure is now pooled and
selected). Also verified the derived name path is provenance-safe: a string
literal is recorded as a *candidate* (`sql_call_string`), resolved only if a
matching declaration exists in source; it is never promoted to a verified fact
about deployment.

Guardrails: R4 no-plan arm unchanged (0.3194) — isolation preserved; C# evidence
untouched; single variable changed in E3 (ref ordering) on top of E2's reserve.

## Gate 3 — paired evaluation (same index, contract locked)

| Arm | Macro R@10 | Pool | Selected | Notes |
|---|---:|---:|---:|---|
| hybrid snippet ≤24 KiB | 0.2500 | 1/25 | – | improved by SQL symbols (D09) |
| R4 no-plan | 0.3194 | 12/25 | 6/25 | unchanged |
| R4 plan (previous best) | 0.4861 | 18/25 | 8/25 | baseline |
| **R4 plan + E3** | **0.5278** | 19/25 | **9/25** | new best |
| GO | 0.5722 | | | not met |

Per-case selected (best arm): D01 1/2, D02 0/6, D03 1/1, D04 1/1, D05 0/1,
D06 0/3, D07 1/3, D08 2/2, D09 1/1, D10 0/2, D11 1/1, D12 1/2.

Resources unchanged: ≤6 calls, ≤32 files, pack ≤24 KiB, delivered ≤64 KiB,
hash-verified, typed gaps/abstention/cancellation/injection isolation intact.
Task correctness **NOT_MEASURED** (no grader). Dev-split development evidence
only.

## Verdict

**PARTIAL** — product improvement runs **met** (0.5278 > 0.4861 under the same
contract, attribution isolated to E3), RAG MVP **GO not met** (0.5278 < 0.5722),
and correctness remains unmeasured.

## Remaining gap (measured)

The 4 spans still out of reach are not an index problem (all verified
persisted): D02 4/6 (one file's three methods + two DbContext declarations),
D05, D06 (3), D07's other 2, D10 (2) — these need either a second typed call
(beyond ≤6 without cannibalizing proven gold) or method-level semantic ranking,
both owner-level decisions already recorded in earlier reports.

## Next step (single recommendation, not started)

Make the typed stage iterative rather than single-shot within the same 6 calls:
allow the 3 plan-hybrid queries to be *replaced* by a second typed round when
the first typed round yields a pool hit that is unselected (message passing
between stages, no extra calls). Success criterion: selected ≥11/25 with calls
unchanged. This is the only remaining lever inside the contract that does not
require a new signal source.

## Reproduce

```bash
W=/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118
cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
R4B_DECISIONS=1 cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
cargo test -p zg-engine --lib -- rag::workflow
```
