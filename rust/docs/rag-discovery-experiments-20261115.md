# RAG Discovery bounded experiments — 2026-11-15

Status: **two bounded experiments run; both NO-GO for a Discovery change. One
structural hypothesis deferred.** No engine, index, gold, held-out or
production change. Selection/Expansion remain frozen.

Inputs: `requal-foundation` receipt and raw probe; exact-symbol probe receipt
`<external>/receipts/symbol-channel.json`; refinement receipt
`<external>/receipts/refine-experiment.json` (`d1fdda15…`).

## 1. Hypotheses and pre-registered gates

| ID | Hypothesis | Gate (fixed before run) | Result |
|---|---|---|---|
| H-A | Refining >200-line containers to their best matching declaration preserves recall while raising strict evidence and cutting pack size | Recall loss ≤2 pp; strict ≥0.30; lines −30%; ranges valid; no gold | **FAIL** |
| H-B | Exact-symbol declarations for question identifiers add discovery coverage or precise top-10 evidence | +≥2 gold spans in candidate union, or strict +≥0.10 | **FAIL** |
| H-C | Cross-file/SQL structural closure recovers the remaining DbContext/SQL spans | bounded reads; ≤64 MiB; candidate gain; deterministic | **DEFERRED** |

H-A and H-B used no gold in the policy. Gold was used only for scoring.

## 2. H-A — declaration granularity refinement

Policy:

- start from the raw top-10 of each engine call;
- for a unit longer than 200 lines, parse **only that file/range**, list
  declarations, and replace the container with the best query-token-matching
  declaration (density-normalized, methods/properties preferred);
- prepend up to two exact-symbol declarations for question code tokens when
  ≤200 lines;
- dedupe, cap 10.

### Result

| Metric | Raw top-10 | Refined top-10 |
|---|---:|---:|
| Best Recall@10 (engine-call subset) | 0.5556 | **0.3750** |
| Max strict recall | 0.2917 | 0.2917 |
| Mean lines per call | 2,542.5 | **995.5** (−61%) |
| Source reads (audit) | 0 | 76 files / 2.17 MB |

The policy cuts lines sharply, but **loses 18 recall points** and does not raise
strict recall. It is a cost-only transformation, not a quality improvement.
The cause is selection inside the container: density and query-token overlap do
not reliably pick the declaration containing the gold span.

**H-A: FAIL / NO-GO.** Keep the measurement (declaration splitting exposes the
precise pool); do not ship the replacement policy.

## 3. H-B — exact-symbol channel

Read-only `ZvecGrep::symbols` queries for up to four code-like tokens per
question. The probe is `rust/crates/zg-engine/examples/rag_symbols_probe.rs`;
it does not refresh the index.

### Result

- Symbol items covered **5/25** gold spans: D04, D08 (2), D10 (1), D11.
- **Marginal candidate coverage over the search union: 0 spans.** All five were
  already retrievable by the search calls.
- It gives precise ranges for D04, D08, D11, and one D10 span, but the channel
  alone does not qualify top-10 evidence and does not recover the missing
  cross-file/SQL spans.
- Some exact names return large result sets (D03/D09: 100 and 51 items), so the
  channel needs a selector before it is useful at the top-10 boundary.

**H-B: FAIL as a discovery channel.** It is a useful precision signal for a
future Selection layer, not a standalone qualification.

## 4. H-C — cross-file/SQL structural closure (deferred)

The remaining 7 precise spans are:

- D02 `CoreDBContext.cs` 17–20 and 41–55; `Employee.cs` 1–65;
- D07 `OrganizationCommand.cs` 1057–1074; SQL 58–95;
- D10 `CoreDBContext.cs` 9–20;
- D12 SQL 1–95.

A bounded implementation would need a production reference graph or SQL
procedure binding. Neither is in the current index: the E1 typed-reference
feature is non-default/experimental and was excluded from production; H6/H7
resource gates failed (E1R 67.8/72.3 MiB > 64 MiB). Implementing it now would
cross into Selection/Expansion, which are frozen.

**H-C: DEFERRED, not run.** No resource measurement is claimed.

## 5. Independent verification

`benchmarks/rag-impact/tools/verify_requal_20261115.py` recomputes route
contracts, Recall@10, strict evidence, union/selectable ceilings and identity
hashes without importing the experiment tools.

- Positive verification: **PASS** for requal + refinement receipts.
- Negative controls, all rejected:
  1. extra `vector` route injected into an FTS call → contract failure;
  2. installed/binary hash mutated → identity failure;
  3. corpus file hash mutated → corpus failure;
  4. receipt metric changed → recomputation mismatch.
- R1 contract on merged HEAD:
  `api::evidence` **10/10 PASS**; `r1_public_api` **2/2 PASS**.
- Single-run limitation: the requalification was run once; no repeat-run
  variance or confidence interval is claimed. Determinism is supported by the
  recorded raw probe and recomputation, not by repeated measurement.

## 6. What changed in understanding

1. The historical “9/25 retrievable” was understated for the new Foundation:
   the precise candidate pool is **18/25**, produced by the index itself, not by
   H2-style source parsing.
2. The historical “0.4722” was a best-call Recall@10, not a union. It is not
   evidence that 47% of spans were retrievable.
3. The bottleneck moved: candidate discovery is much better, but **top-10
   Selection and granularity** are now the limiting layer. Refining containers
   without a selector loses recall.
4. An exact-symbol channel is cheap and precise for named identifiers, but adds
   no discovery coverage on this dev set.

## 7. Disposition

- No Discovery implementation change is proposed.
- No Selection/Expansion implementation was opened.
- The only reusable artifacts are the measurement tools, the declaration
  granularity audit, and the exact-symbol probe.
- The next gate is a separately authorized **Selection qualification** on the
  frozen v3 candidate pool.
