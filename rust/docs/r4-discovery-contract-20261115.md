# R4 discovery contract — strategy and pre-registration

Status: **DESIGN ONLY. Not implemented, not authorized for execution.**
Branch `work/r4-workflow-20261115` @ `711a4f7`. Follows
`r4-discovery-attribution-20261115.md` and the frozen R0 contract.

## 1. Objective and non-goals

Objective: make an external agent able to **reach** the evidence it needs inside
the frozen R0 budgets, then verify and answer. Discovery is the measured
bottleneck (pool coverage 7/25; 14/25 gold spans have no lexical handle at all).

Non-goals: no recall target change, no new retrieval heuristic without a stated
hypothesis, no held-out, no GQ5, no MCP/CLI exposure, no release, no default
model/index/ranking change, no production index or daemon.

## 2. Evidence the contract rests on

| Fact | Source |
|---|---|
| 6/25 selected, 14/25 no lexical handle, 3/25 starved by result limit, 1/25 span-level miss, 1/25 item-cap loss | `r4-discovery-attribution-20261115.md` |
| All gold files are in the frozen manifest, admitted and present → not an indexing/scope failure | forensics receipt |
| Declaration/stem/in-span names reach 17/25 spans in top-10 (18/25 files); oracle macro ceiling **0.6944** | `r4-symbol-reachability-20261115.json` |
| Deployable FTS 0.3750, B0 oracle 0.4722, fixed rg 0.3194, R0 target 0.5722 (frozen) | existing receipts / PREREGISTRATION |
| `ZvecGrep::symbols` is exact-name, index-backed, declarations only, with path/range and cursor | `api/symbols.rs` |
| R2 facts: project/type/method, DbSet, invocation targets, `SaveChanges`, guards; R3: selected proc/view, table reads/writes, literal SQL | R2/R3 gates re-verified 13/13 |
| R1 surface: hash-verified spans, typed gaps, 24 KiB/32 items | R1 prototype |

## 3. Proposed Discovery contract

Five routes, one router, one bounded acquisition layer, one corrective check.

### Route L — lexical (question handles)

Workspace or scope-restricted literal/regex search, with the file filter applied
**by the backend** (globs, file types, max file size, ignored-dir policy), not
after the result set. This removes the measured starvation where generated dumps
consume the 250-result window (D03 rank 3312).

### Route F — typed fact expansion (R2/R3)

From each accepted seed span, extract typed names with provenance:

- enclosing declaration and its project/type scope (R2 `MethodFact`, `TypeFact`);
- `DbSet<T>` element types used in the seed (R2 `DbSetFact`);
- invocation targets and their resolution status (R2 `InvocationFact.target`,
  `Resolved` vs `Candidate`; candidates are expanded but labelled);
- `SaveChanges` context types (commit boundaries, not atomic transactions);
- SQL: selected proc/view names, tables read/written, and whether the definition
  is selected (R3 `SqlCatalog`, `SqlBinding`).

Each derived name is emitted with `(seed path, seed range, fact kind, status)`.
Provenance is part of the candidate record; a `Candidate` never becomes
`Resolved` by expansion.

### Route S — declaration/symbol lookup (index v3)

Exact-name lookup through `symbols(name)` returning declaration ranges and
files. This is the decisive route for the 14 spans with no lexical handle
(`EmployeeEditThongTinCaNhan`, `CoreDBContext`, `Employee`, `EmployeeDetail`,
`UpdateOrgTotalEmployee`, `CoreService`). Exact-name lookup cannot be starved by
content volume and returns the declaration span directly.

Fallback when no index is available: name-targeted lexical search
(`-F <name>` with a path/scope hint), flagged as `lexical_fallback` so the
receipt never implies symbol precision.

### Route P — proc/view definition binding (R3)

Resolve `sp_*`/view names to selected definitions; keep `_old` variants and
dynamic targets as explicit candidates with reasons. Distinguishes
source-visible facts from deployment authority (never claimed).

### Route C — corrective check (R1)

After selection, verify every selected span by hash (R1 surface). If a required
claim anchor — a code token from the question, or a fact-derived name that the
answer depends on — has no supporting selected span, run **one** corrective
round: Route S on the missing name, then Route L within the file, then re-select.
Otherwise abstain with the missing names listed.

### Router rules (query-aware, deterministic)

| Condition | Order |
|---|---|
| question has identifier-shaped tokens | L (those tokens, scoped) → S (their declarations) → F |
| question has no identifier tokens | L (phrases/anchors) → F from the best seeds → S on derived names |
| question has SQL/persistence intent | P first when a proc/view name appears; otherwise L → F(SQL) → P |
| corrective round | S on the missing name, then L restricted to the file |

Budget split per case (frozen in the preregistration, not tuned mid-run):
40 % lexical, 35 % symbol/name lookup, 20 % fact expansion, 5 % corrective.
Explicit caps: calls, distinct files, spans per file, source reads, bytes,
per-route call caps, cancellation. Every refused operation becomes a typed gap
with the route that requested it.

### Two invariants

1. Nothing enters the pack without a hash-verified read (R1 surface).
2. Expansion changes *where to look*, never *what is true*: a candidate edge or
   mapping stays a candidate unless the source shows it.

## 4. Metric contract (frozen, unchanged from R0)

| Term | Definition |
|---|---|
| candidate union coverage | gold spans overlapped by any candidate unit; **diagnostic only** |
| selected spans | units in the final pack (≤10 items, ≤24 KiB) |
| **Recall@10** | macro mean over cases of gold spans overlapped by the ≤10 selected units (B0 definition) |
| citation accuracy | selected spans that hash-verify / selected spans (must be 1.0) |
| secondary | source bytes read, tool calls, latency, peak PSS |

Reporting must always show candidate union, selected spans and Recall@10
separately. `0.6944` from the probe is an **oracle symbol-query ceiling**, not a
deployable result, and must be labelled as such wherever it appears.

## 5. Baselines (frozen)

| Role | Baseline | Value |
|---|---|---|
| **primary paired** | fixed deployable FTS (`fts_code_tokens`) | 0.3750 |
| reference, not paired | B0 oracle best-call (uses gold) | 0.4722 |
| reference, not paired | fixed `rg_code_tokens` | 0.3194 |
| oracle ceiling | symbol-query ceiling (this analysis) | 0.6944 |

The primary comparison is the new arm vs **FTS 0.3750 on the same cases,
metric, and budget**. B0's oracle may be quoted only as a labelled reference.
GO remains the frozen owner target: **Recall@10 ≥ 0.5722** (B0 + 10 pp).

## 6. Pre-registered predictions

| # | Prediction | Basis |
|---|---|---|
| P1 | candidate union coverage ≥ 18/25 (0.72) | symbol route 17/25 with oracle names; typed-fact derivation should add the proc chain |
| P2 | Recall@10 ≥ 0.4750 (i.e. ≥ FTS + 10 pp) | union + declaration-level spans + ≤10 items |
| P3 | Recall@10 < 0.5722 is the **likely** outcome | 10-item cap over ≤18 reachable spans; ceiling 0.6944 assumes oracle queries |
| P4 | citation accuracy = 1.0, zero unverified or stale content | R1 surface already enforces it |
| P5 | bounds and abstention hold: no-evidence, missing anchors, budget, cancellation, duplicate queries | existing tests |
| P6 | source bytes read ≤ 90 MB for the 12-case run (≈3× the last measured 30.2 MB) | index-backed routes replace full-file widening |

A prediction that fails is reported as failed. Predictions are not edited after
the run.

## 7. Expected cost

| Item | Estimate | Basis |
|---|---|---|
| one frozen-corpus index build (isolated `ZVEC_GREP_HOME`, no daemon, no production index) | **~70 min wall CPU**, ~9.2k fragments, ~1–2 GB disk | E4 measured 9 223 fragments in 1:10:36 |
| abort threshold | > 90 min or > 3 GB | this preregistration |
| implementation + tests | ~1 working session (fixtures, no corpus needed) | R2/R3/R4 prototype size |
| paired measurement (both arms, 12 cases) | ~3–6 min once the index exists | workflow ≈5 s/case, FTS sub-second |
| adversarial scenarios | < 1 min | existing fixture set |

The index is a **measurement artifact** in an external directory. It is not the
production index, not installed, and not shared with the running daemon.

## 8. Acceptance and stop conditions

**GO**: all of {P1, P4, P5} hold **and** Recall@10 ≥ 0.5722 on dev, with the
frozen metric, budget and case set, and FTS 0.3750 as the paired baseline.

**PARTIAL**: Recall@10 ∈ [0.4750, 0.5722) with P1/P4/P5 holding. Report the miss
against the frozen target; do not re-baseline.

**NO-GO**: Recall@10 < 0.4750, or any unverified/stale selected span, or
candidate union < 15/25 after two preregistered iterations.

**Stop rules**

1. At most two implementation iterations after M1. Each iteration declares in
   advance: the hypothesis, the metric it should move, and the expected
   direction.
2. No tuning of limits, weights or route order without a stated hypothesis and a
   preregistered expectation.
3. Stop immediately if the index build exceeds its cost cap; fall back to the
   non-paired reference baseline and mark the result `PARTIAL / NOT_PAIRED`.
4. Never inspect held-out, never change targets, never merge or release.
5. If discovery is fixed but selection is then the loss, report it and stop —
   do not start a third family of experiments.

## 9. Implementation mission (for approval)

**M1 — discovery correctness (no index, no new dependency)**
1. Push the file filter into backend queries (globs/file types/size) for Route L.
2. Declaration-aware widening (enclosing declaration via the fact parser, not a
   line window).
3. Route F with per-candidate provenance `(seed, fact kind, status)` and bounded
   two-pass expansion.
4. Tests: provenance, candidate-vs-resolved, budget refusal, declaration
   widening, starvation regression fixture.
*Acceptance*: candidate union ≥ 15/25 on dev; citation 1.0; no budget regressions.

**M2 — symbol route**
5. Route S via `symbols(name)` with a lexical fallback and an explicit
   `source: symbol|lexical_fallback` field.
6. Route P for proc/view definitions, keeping unselected variants as candidates.
*Acceptance*: symbol hit rate reported; union ≥ 18/25; every candidate carries
route provenance.

**M3 — selection and corrective CHECK**
7. Claim-anchored ranking (declaration proximity, route priority, required code
   tokens, diversity), unchanged 10-item/24 KiB caps.
8. One corrective round keyed to a missing required name; abstention otherwise.
*Acceptance*: P4/P5 green; Recall@10 recorded against FTS.

**M4 — paired qualification**
9. One run per arm on D01–D12 with the frozen metric, plus the adversarial
   scenarios; receipt and failure analysis.
10. Verdict GO/PARTIAL/NO-GO per §8.

**Explicitly excluded**: held-out, GQ5, MCP/CLI surface, release, install,
production index/daemon/model/ranking/default changes, any candidate-expander or
cosine-reranking revival.

## 10. Owner decisions required before execution

1. Authorize **one isolated corpus index build** for measurement (~70 min CPU,
   ≤3 GB, external directory), with no daemon and no production index touched.
2. Approve Route S using **exact-name symbol lookup over the whole workspace**
   (declarations only) as part of discovery, or restrict it to scoped roots.
3. Confirm the frozen GO bar stays **Recall@10 ≥ 0.5722** and that P3 (likely
   PARTIAL) is acceptable as an outcome.
4. Confirm that the primary paired baseline is **FTS 0.3750** (requires the index
   in decision 1); if the build is not authorized, accept a `PARTIAL / NOT_PAIRED`
   verdict instead of a paired claim.

No code, index, corpus or production artifact was changed by this design.
