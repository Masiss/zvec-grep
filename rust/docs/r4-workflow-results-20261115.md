# R2 + R3 + R4 integrated mission — results

Status: **R2 PASS (re-verified) · R3 facts PASS / expansion PARTIAL · R4 workflow
PARTIAL — no MVP claim.** Branch `work/r4-workflow-20261115` from `26a1562`
(R1 evidence surface). Not merged, not released, no production change.
Pre-registration: `r4-workflow-prereg-20261115.md` (frozen before measurement).

## 1. What was verified before building

R2 and R3 were already implemented as private engine fact modules. Their real
HRM2.0 gates were re-run at this HEAD rather than rebuilt:

`cargo test -p zg-engine --lib -- extraction::csharp_facts extraction::sql_facts --include-ignored --test-threads=1`
→ **13 passed, 0 failed**, including `real_hrm2_development_cases` and
`real_hrm2_sql_gate`. R2 covers project/partial/DBSet/receiver/assignment/guard/
`SaveChanges`; R3 covers selected proc/view definitions, source-visible
table reads/writes, selected-variant authority and EF-SQL literal binding.

No R2/R3 gate was re-implemented, and no previously rejected retrieval heuristic
was retried (no candidate expander, no cosine re-ranking, no selection
micro-tuning).

## 2. What was built (R4 seam)

`zg_engine::rag::workflow` — a deterministic, bounded loop an adapter can drive:

| Stage | Behaviour |
|---|---|
| DEFINE | anchors (stopword-filtered), identifier-shaped code tokens, phrases, SQL intent |
| DISCOVER | scoped lexical searches (workspace or per-scope roots), deduplicated |
| EXPAND | reads seed files, derives typed reference names from `csharp_facts`/`sql_facts` (DBSet element types, invocation targets, type names, `SaveChanges` context, proc names, table accesses) and searches them |
| CHECK | widens candidates to enclosing declarations, ranks by code-token/anchor coverage with a size penalty, assembles **hash-verified** spans via the R1 surface, and runs one corrective round for a required code token no selected span covers |
| FINAL | selected spans + SHA-256 + typed gaps + stage traces + abstention |

Enforced, reported bounds: rounds, tool calls, scopes, distinct files, source
reads, items, per-file spans, bytes, cancellation. Generated files are excluded
by extension and size. Source text is only ever data: a file containing "ignore
previous instructions / call index drop" leaves the plan and call count
unchanged (tested).

Tests: **15 workflow tests** (positive cross-file and SQL, DBSet/invocation
expansion, no evidence, missing code tokens, phantom source, served-version
mismatch → `stale_hash` gap, cancellation, tool budget, duplicate suppression,
injection source, invalid root, trace order) plus the pre-existing 13 R2/R3
tests. Full engine suite: **784 passed, 0 failed, 28 ignored**; clippy clean.

## 3. Measurement (frozen dev D01–D12, paired, one run per arm)

| Arm | Span recall@10 (macro) | Selected | Verified | Notes |
|---|---:|---:|---:|---|
| `fixed_rg` (paired baseline) | **0.2500** | 108 | n/a | question code tokens, first 10 matched ranges |
| `workflow` | **0.3611** | 114 | 114 | 24 calls, 4 scopes, widening, expansion, verification |

Δ = **+11.11 pp** vs the paired baseline; citation accuracy **1.0**; selected
content 219 754 B; source read 30.2 MB; 276 calls; wall 60.1 s for 12 cases
(≈5.0 s/case); peak PSS 518 MB; host load 3.44 → 6.00 during the run.

Frozen reference points from existing receipts (**not** re-measured, not paired):

- B0 oracle best-call **0.4722** (uses gold, not deployable);
- fixed deployable FTS **0.3750**;
- fixed `rg_code_tokens` **0.3194** (B0 harness);
- reference-union coverage **0.875** (H6, 500-unit pool, not recall@10).

**The workflow's 0.3611 is below the deployable FTS baseline 0.3750 and far
below the R0 target (B0 + 10 pp = 0.5722).** The +11.11 pp is a win against the
weaker arm it was paired with, not an end-to-end improvement over the best
known deployable baseline. No MVP claim is made.

## 4. Adversarial behaviour (all held)

| Scenario | Result |
|---|---|
| no-evidence question | `no_evidence`, 0 selected |
| `max_tool_calls = 1` | exactly 1 call, `budget_tool_calls` gap, `missing_code_tokens` abstention instead of a confident answer |
| cancellation after 1 search | `cancelled`, 0 selected |
| repeated query | duplicate counted and suppressed |
| source version mismatch | `stale_hash` gap, content not selected |

## 5. Prediction grading (frozen)

| Prediction | Outcome |
|---|---|
| P1 workflow beats paired baseline by ≥ 10 pp | **PASS** (+11.11 pp) |
| P2 citation accuracy 1.0, no stale content | **PASS** |
| P3 workflow reads at least as much as baseline | **PASS** (30.2 MB source reads) |
| P4 adversarial behaviours hold | **PASS** |
| P5 missing code tokens abstain | **PASS** |

Acceptance also required workflow recall ≥ 0.55 for **GO**. It did not:
**PARTIAL**, not GO.

## 6. Iterations and failure analysis (what the evidence actually says)

Four measurement iterations, each with a distinct cause:

| Iter | Change | Paired baseline | Workflow | Pool coverage |
|---|---|---:|---:|---:|
| 1 | initial | 0.2500 | 0.2500 | not measured |
| 2 | source-extension and file-size bounds, size-aware ranking | 0.2500 | 0.2500 | not measured |
| 3 | per-scope discovery (`Webs/Services/DBContexts/DB`), per-file span cap | 0.2500 | 0.2778 | not measured |
| 4 | deeper discovery (250 results/call, 96 files, 160 reads) | 0.2500 | 0.3611 | **7/25** |

Iteration 1 exposed a real defect: generated JSON dumps in `Database/` consumed
the candidate pool and produced `budget_bytes` gaps. Bounds fixed the cost
(72 MB → 30 MB reads) but not recall: alphabetical scan order starves the
result limit, so gold files never enter the pool — the same starvation the
frozen `rg_code_tokens` baseline shows.

Iteration 4 makes the attribution unambiguous: **only 7 of 25 gold spans ever
enter the candidate pool**, and the workflow selected 7 of those 7 (one was lost
to the per-file cap). So:

- **selection is not the current bottleneck** (it converted ~100% of what it
  saw);
- **discovery/pool coverage is the bottleneck** (28% of gold reachable under
  24 calls × 4 scopes × 250 results);
- typed expansion from the seed files amplifies whatever the seeds are; when
  the seeds are boilerplate, the references are boilerplate.

This matches the historical R4 audit (primary bottleneck: retrieval/discovery,
64% discovery miss) and does not reproduce H6's 0.875 union coverage because H6
scored a 500-unit union, not a 10-item answer pack.

Per the mission instruction, this direction stops here rather than continuing
micro-tuning. Raising limits or adding more reference searches would buy pool
size, not a justified MVP.

## 7. Per-phase verdicts

| Phase | Verdict | Basis |
|---|---|---|
| R2 | **PASS (re-verified)** | 13/13 fact tests incl. real HRM2.0 gate; facts used by EXPAND |
| R3 | **PARTIAL** | fact slice PASS and now wired into EXPAND; full cross-file relation acquisition still misses most gold spans; no deployment/live-DB authority claimed |
| R4 | **PARTIAL — NO-GO for MVP** | loop, verification, gaps, abstention and bounds are implemented and tested; +11.11 pp over the paired arm but 0.3611 < deployable FTS 0.3750 and < R0 target 0.5722 |

## 8. What is explicitly not done / not authorized

- No held-out inspection; no GQ5; no R5/R6; no merge, release, install, daemon,
  index, model, ranking or default change.
- No MCP/CLI surface for the workflow; it is an engine module plus a harness.
- No p95/concurrency/accepted-footprint qualification; PSS 518 MB was observed,
  not assessed against a target.
- No claim that any phase is production-ready.

## 9. Next step that the evidence supports (owner-gated)

Discovery is the only lever with evidence behind it. A justified next attempt is
a **discovery-contract change**, not more expansion:

1. target declaration/symbol retrieval directly (path/type/method aware), so a
   question's identifiers select *definitions* before any full-text scan;
2. or change the discovery budget contract (depth vs. breadth) with a new
   preregistration that fixes the paired baseline to the best deployable route
   (FTS 0.3750), not to the weaker rg arm;
3. keep R1 evidence + the workflow's CHECK/abstention layer unchanged — it
   verified 100% of what it was given and never fabricated evidence.

Any of these needs owner approval because it changes the retrieval contract.
