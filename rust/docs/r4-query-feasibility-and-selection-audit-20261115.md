# R4 query-to-entity feasibility and selection audit

Status: **research/audit only — no production, contract or index change.**
Branch `work/r4-workflow-20261115` (from `53c3bf3`). Inputs: canonical RAG R0–R6
plan, `r4-discovery-{attribution,contract,results}-20261115.md`, the R4 paired
receipt, and one new isolated measurement.

## 0. Errata to the previous report

`r4-discovery-results-20261115.md` §3 attributed the D04 loss to "a `missing`
gap during pack assembly (stale read/hash mismatch)". The audit shows the `missing`
gap recorded for D04 is `required code token absent from selected evidence:
EmployeeUpdateInfo` — an abstention diagnostic, **not** a verification rejection.
D04's gold span was pooled by the FTS route with the exact gold range and then
lost to the item cap. No stale/hash rejection occurred in any of the four cases.
The selection attribution below supersedes that sentence.

## 1. The semantic query gap, measured

All arms run on the same isolated 822-file corpus index (Potion Code 16M v2,
index v3), window 10 unless stated. Cases are the dev and dev-independent splits;
none of this is a qualification (held-out untouched).

| Arm (per-case gold-span reach) | D01–D12 (25 spans) | E1–E5 (5 spans) |
|---|---:|---:|
| Raw Vietnamese question through `rg` (no code tokens) | **0/25 (0.0000)** | – |
| Existing vector route (question text only) | **5/25 (0.2639)** | 1/5 |
| Existing default hybrid route (question text) | **8/25 (0.4167)** | 4/5 |
| External-agent plan, frozen blind (rg queries + symbols, 50/20 windows) | n/a (no plan frozen for D) | **3/5 (0.6000)** |
| `fixed_fts` on code tokens (R4 paired baseline) | 0.3333 | – |
| R4 workflow (selected pack) | 0.2778 | – |

Receipts: `r4-query-feasibility-devi-20261115.json`,
`r4-query-feasibility-dev-20261115.json`, frozen plan
`r4-query-plan-freeze-20261115.json` (+ sha256 in the receipt).

Three facts follow directly:

1. **The Vietnamese question text carries no lexical handle in the D cases**
   (0/25 by raw `rg`). This is the mechanism behind the 14/25 "no lexical handle"
   attribution, now confirmed by direct measurement rather than inference.
2. **The question text does carry signal in the E cases** (hybrid 4/5, blind plan
   3/5) because the corpus contains Vietnamese comments, UI strings and messages.
   So the gap is not "Vietnamese vs English" as such; it is that the D questions
   describe *workflows* whose spans contain no natural-language echo.
3. **The engine's own hybrid route already beats both R4 arms** on the D split
   (0.4167 vs 0.3333 FTS and 0.2778 workflow), with a 10-item window and one call
   per case. The R4 discovery design competed with, and lost to, a route that was
   already available: searching *derived code tokens* discards the question's
   semantics, which the hybrid route keeps.

## 2. Feasibility of the three directions

### A. External-agent query planning
- **Measured:** blind frozen plan on E1–E5 reaches 3/5 gold spans using 8 rg
  queries + 8 symbol lookups (4 queries + 4 symbols per case); per-query hits are
  in the receipt (`plan_query_hits`, `plan_symbol_hits`).
- **Strength:** the only direction that produced a *typed* plan (queries shaped
  for controllers, commands, executors, mappers) rather than a bag of words, and
  the only one that can express intent ("đi qua method nào" → call-chain target).
- **Weakness:** on D-class questions the same capability is what the plan cannot
  substitute for: 0/25 from question text alone means the agent must *know* the
  domain nouns (Employee, Command, Executor) before it can plan. Bias risk is real
  and must be controlled by a per-case plan freeze + hash before measurement.
- **Cost:** 8–16 tool calls per case, no local model, no rebuild, no privacy
  exposure if the agent runs host-side on local files.
- **Risk:** planning quality is not reproducible by the engine; it must be
  measured as a protocol (plan file + hash), never tuned against gold.

### B. Semantic embeddings
- **Measured:** the *existing* index vectors reach 5/25 (0.2639) on the D split —
  below the code-token FTS baseline and far below hybrid. On E1–E5, 1/5.
- **Interpretation:** a 16M-parameter code model is weak on Vietnamese workflow
  questions. E4's larger-model runs are **not usable** here (contamination and
  reproducibility defects recorded in E6); re-running them needs a clean
  protocol, artifacts and owner authorization, which this mission does not have.
- **Cost/risk:** a new embedding model means a full index rebuild (~51 s measured
  here for 822 files, but multi-GB for larger models), a query-embedding runtime,
  a model-identity change in artifacts, and a fresh paired baseline. Not justified
  by the current evidence: the *non-embedding* hybrid route already outperforms
  every embedding arm measured so far.
- **Verdict:** embeddings are a **supporting** signal (they are already part of the
  hybrid route), not the primary solution.

### C. Domain alias / entity mapping
- **What is available:** the corpus itself contains Vietnamese↔identifier
  evidence (comments, XML docs, message strings, table/column names), which is why
  the hybrid route scores 0.4167. A deterministic lexicon mined from those
  artefacts would be derived, not hardcoded, and could be provenance-tagged
  (`alias_source: comment|doc|schema`).
- **Not measured here:** mining and evaluating such a lexicon would be a new
  system, and any tuning on D01–D12 would overfit HRM2.0 — exactly the risk the
  mission names. A feasibility claim without a non-HRM corpus cannot be made.
- **Cost:** low to build, high to *validate*; the validation corpus does not exist
  in this repository.
- **Verdict:** bounded, useful as a **bounded lexicon layer with provenance**,
  conditional on a second corpus; not the primary direction now.

## 3. Selection loss audit (9 pooled → 5 selected)

Source: `r4-selection-audit-20261115.json` (fresh run, same code/corpus/budget).

| Case | Gold span | Pool | Selected | Cause |
|---|---|---|---:|---|
| D01 | `Webs/HRM/Controllers/Employee/EmployeeController.cs:459-620` | 5 line units inside gold, none widened into the pack | 0 | **Ranking + byte budget**: 9/10 items selected, `budget_bytes` gaps; the 24 KiB pack was consumed by large spans first |
| D01 | `Services/.../EmployeeCommand.cs:215-285` | 0 | 0 | Not discovered (no handle) |
| D03 | `Services/.../EmployeeCommand.cs:215-285` | 3 line units | 0 | **Per-file cap**: 2 spans of that file already selected, neither overlapping gold |
| D04 | `Services/.../EmployeeCommand.cs:562-573` | 2 (one is the exact gold range via `Fts`) | 0 | **Item cap / ranking**: 10/10 selected; gap is the abstention token `EmployeeUpdateInfo`, not a verification failure |
| D10 | `Services/.../CoreService.cs:15-22` | 1 line unit | 0 | **Per-file cap**: 2 spans of that file already selected |

Summary: **2 per-file cap, 2 item/byte-budget ranking, 0 verification rejections,
0 discovery-side range errors among the pooled ones.** None of the four was
correctly refused; all four are capacity/allocation losses of valid evidence.

Two consequences:

1. **Selection must be fixed before discovery coverage is raised.** With a 9/25
   pool the pack already discards ~44% of the valid pooled evidence; a better pool
   would be discarded at the same rate. Ordering discovery work first would produce
   an unmeasurable improvement.
2. The dominant mechanisms are concrete and addressable: a byte budget that
   prefers a few large spans, a per-file cap of 2 that blocks a second gold method
   in the same file, and a ranking with no notion of "this span came from the same
   subject as the question". These are structural, not tuning constants.

## 4. Recommendation: one priority direction

**Put query understanding in the external agent, and make the engine's discovery
base the existing hybrid route instead of derived code-token lexical search.**

Rationale, in evidence order:

1. The engine's own hybrid route already achieves 0.4167 on the D split — above
   every R4 arm (0.3333, 0.2778) and above existing vector-only (0.2639). R4 lost
   by replacing a semantically richer route with a narrower derived one.
2. The remaining gap is intent, not similarity: 3 of 25 spans need call-chain or
   write-path reasoning ("đi qua method nào", "xử lý gì", "ở đâu") that no local
   scoring function expresses, while an agent can state it as a typed plan.
3. Embeddings are already inside the winning route and cannot be improved locally
   with currently authorized artifacts; a domain lexicon cannot be validated
   without a second corpus.

Concretely, R4 becomes an **agent-driven evidence protocol**: the agent proposes a
typed plan (natural-language queries, code queries, candidate symbols, expected
evidence role), the engine executes it through the protected hybrid + symbol +
fact routes with per-candidate provenance, and a selection policy that respects
the audit findings assembles the verified pack. The engine's contribution is
typed evidence and fail-closed verification, not language understanding.

## 5. Execution contract and preregistration (next mission, R4b)

Scope: implement the agent-plan protocol *and* the selection fixes, measured
together against the hybrid baseline. Bounds per case: ≤8 plan items, ≤12 tool
calls, ≤96 files, ≤160 reads, 10 items / 24 KiB pack, 2 spans per file unless the
policy change is the hypothesis under test.

Pre-registered changes (≤2 iterations, each with hypothesis + measurement):

- **P1 (selection, hypothesis: capacity allocation, not scoring):** make the pack
  byte budget fair-share across distinct subjects instead of greedy first-come
  (e.g. per-subject byte cap), and raise the per-file span cap only when the extra
  span overlaps a *distinct* declaration. Predicted effect: 9→5 becomes 9→8 on the
  audit set.
- **P2 (discovery base, hypothesis: hybrid beats derived tokens):** add the hybrid
  question route as a first-class discovery route with reserved slots (like the FTS
  quota), keeping code-token lexical as a secondary. Predicted effect: pool
  9/25 → ≥14/25.
- **P3 (agent plan):** accept an external plan (frozen per case, hashed before
  measurement) as the seed for queries/symbols/roles, with provenance tag
  `AgentPlan {role}`.

Paired arms: `hybrid_question` (new baseline, measured on the same index),
`r4b`. Metrics unchanged: selected Recall@10 (primary), candidate-union coverage,
citation accuracy, verification status, reads, tool calls, content bytes, wall
time, peak PSS. Task correctness stays out of scope (no grader exists).

Acceptance: GO ≥ 0.5722 (frozen R0 target) with full verification;
PARTIAL ≥ 0.5000 (must beat both the hybrid baseline 0.4167 and the FTS baseline);
NO-GO otherwise or on any unverified evidence claim.

Stop conditions: two iterations used with no change; any arm below the measured
hybrid baseline; any measurement requiring a production change, a model change,
held-out inspection, or a rebuild of the production index; any plan contaminated
by gold.

## 6. Not done / not claimed

No production change, merge, release, install, default model/ranking/index change,
no production index rebuild, no GQ5/R5/R6, no public MCP/CLI contract change, no
held-out inspection, no remote provider call, no private source exfiltration.
E1–E5 are dev-independent, **not** held-out, and are not a qualification signal.
Single run per arm; latencies are in the receipts; no token count was produced by
a tokenizer (content-bytes/4 estimate only).

## 7. Reproduce

```bash
W=/home/masis/.cache/zvec-grep/r4-discovery-20261115
cargo run -q -p zg-engine --example r4_query_feasibility -- $W/corpus dev
cargo run -q -p zg-engine --example r4_query_feasibility -- $W/corpus devi
R4_AUDIT=1 cargo run -q -p zg-engine --example r4_discovery_paired -- $W/corpus D01,D03,D04,D10
```
