# 05 — RAG Original Plan R0–R6

**Canonical source:** `docs/rag/README.md`, `docs/rag/implementation-plan.md`,
`docs/rag/hrm2-pilot.md`, all dated **2026-10-28** and marked **PROPOSED**.
This document preserves the plan semantics; it is not a new roadmap.

## 1. What the plan is for

The plan adds **evidence/local context** to ordinary zvec-grep search for a
selected legacy HRM2.0 C#/EF6/SQL source scope. It is explicitly **not** a new
search wrapper or a graph demo.

Definition of MVP from the plan:

> An external agent using local evidence capability can handle three classes of
> questions on the selected HRM2.0 scope, self-check within budget, return a
> final answer with sources, and distinguish all three conclusion classes.

The intended design is a light evidence layer over existing retrieval, not a
local reasoning service.

## 1b. Business DB RAG is separate from native vector DB

The RAG track targets a selected HRM2.0 legacy application source tree, not the
zvec native index:

- project/assembly identity comes from `.csproj` records (AssemblyName,
  RootNamespace); the selected catalog is caller-supplied;
- C# facts are source-visible: namespace/type/method, `DbSet<T>`, properties,
  assignments, receiver inference, invocations, SaveChanges boundaries and
  branch guards;
- SQL facts are source-visible procedure/view definitions and selected
  read/write bindings; deployment/live DB authority is **not** claimed;
- dynamic SQL/reflection/unresolved mappings remain candidates, not safe facts;
- `_old`/variant procedures are not promoted automatically;
- no MSBuild, compiler, SQL execution or application DB connection is used to
  create these facts.

This is why “R2/R3 PASS” must not be read as “the business database is connected
or the proc is deployed”. The native zvec DB/index is a different storage layer
documented in [03-NATIVE-DB-INDEX-AND-STORAGE.md](03-NATIVE-DB-INDEX-AND-STORAGE.md).

## 2. Canonical phase/gate matrix

| Phase | Canonical meaning | Required gates / deliverables |
|---|---|---|
| **R0** | Freeze scope, source authority, questions, budgets | frozen corpus revision, source anchors, dev/held-out cases, B0 protocol, no target changes after measurement |
| **R1** | Evidence/source pack on existing retrieval | source/project identity, exact spans, hashes, snapshot, gaps, revalidation, budget/dedup, three conclusion classes |
| **R2** | C# facts without running a compiler | project/assembly identity, namespace/type/method, DbSet/properties, assignments, calls, SaveChanges boundaries, guards, unresolved/dynamic reasons |
| **R3** | EF6/SQL and cross-file expansion | selected SQL/proc/view facts, read/write distinction, source variant authority, dynamic candidate handling, bounded relation lookup |
| **R4** | Multi-turn agent loop and public integration | DEFINE/DISCOVER/EXPAND/CHECK/FINAL, missing/stale/budget/cancellation gates, scripted-agent end-to-end, public protocol/limits |
| **R5** | Paired quality and footprint evaluation on the pilot | frozen control/candidate, dev+held-out, source spans/relations/claims, source bytes/tokens/time, CPU/RSS/PSS, invalidation |
| **R6** | Opt-in release and rollback | explicit capability/feature, packaging/process-boundary tests, unsupported behavior, upgrade/reopen/cancel/rollback, owner approval |

### Original budgets/acceptance

`docs/rag/implementation-plan.md:140-162` proposes:

- parser worker, bounded AST/fact cache;
- total incremental evidence memory ≤64 MiB over baseline;
- warm operation p95 ≤1 s, total local-tool wall ≤3 s/investigation excluding
  external LLM;
- correctness/quality gates on frozen dev and held-out;
- no target change after seeing results;
- no unbounded cache to buy latency.

These are proposed budgets until R0 freezes them.

## 3. What “R4” actually became

The canonical R4 is **multi-turn agent loop + public integration**. The
experiment reports named `R4` implement a much narrower thing:

- `crates/zg-engine/src/extraction/candidate.rs` — bounded evidence expander;
- `benchmarks/rag-impact` — scripted B0/candidate pack comparison;
- R4-A/B/D1/D2 — selection/packing experiments;
- no full DEFINE/DISCOVER/EXPAND/CHECK/FINAL loop;
- no public MCP/CLI evidence contract.

Therefore:

- `R4 candidate pilot` is **R4-expander evidence**, not a complete R4 gate.
- R4 follow-up `/ R4-A / R4-B / D1 / D2` are experiments within the gap, not
  additional plan phases.
- Any later report calling a post-R4 branch “R5” or “R6” is using a different
  naming convention; see [06-RAG-POST-R4-RESEARCH.md](06-RAG-POST-R4-RESEARCH.md).

## 4. Actual implementation status

### R0 — frozen baseline

Status: **evidence exists**.

- `benchmarks/rag-impact/` contains scope manifest, corpus manifest, 12 dev
  cases, 8 held-out cases, preregistration, B0 receipts and report contract.
- 12 development cases / 25 requested spans; held-out not inspected during
  implementation claims.
- B0 best-call pack: Recall@10 **0.4722**; fixed deployable FTS baseline
  **0.3750**; targets are owner-frozen.
- The frozen target is +10pp over B0, fewer source reads/tokens, correctness
  proxy ≥ B0.

### R1 — evidence API

Status: **correctness slice PASS; public release blocked; library-only**.

Confirmed:

- `rust/crates/zg-engine/src/api/evidence.rs` is public engine API;
- schema v1, budget, dedup, hashes, exact ranges, revalidation, gaps;
- 10/10 focused tests; external-crate public API contract;
- 12/12 dev packs, 25/25 items, 0 gaps in the V2 dev pilot;
- four concurrent isolated calls pass.

Missing for public v1.0:

- CLI/MCP/daemon consumer;
- versioning/unsupported behavior policy;
- cache/version key;
- p95/PSS/concurrency gate;
- security/privacy review;
- release/rollback/feature flag;
- held-out/broader validation.

So R1 is an **internal library capability**, not the product seam.

### R2 — C# facts

Status: **R2 real-case gate PASS for the bounded subset**.

Implemented `extraction/csharp_facts.rs` using `tree-sitter-c-sharp`; extracts
project identity, namespaces/types/methods, DbSet/properties, types, assignments,
invocations, SaveChanges, guards, unresolved/dynamic reasons. It does **not**
run MSBuild, a compiler or an app DB, and it did not change ordinary lexical
indexing/chunking.

Limits: unresolved mappings/dynamic/reflection remain candidates; no whole-repo
completeness claim.

### R3 — EF6/SQL facts

Status: **SQL FACT SLICE PASS on synthetic + selected real anchors; full
cross-file expansion NOT IMPLEMENTED**.

Confirmed:

- selected CREATE/ALTER PROCEDURE/VIEW facts;
- source-visible writes/reads;
- selected-variant authority; `_old` variants excluded, never promoted;
- raw/dynamic SQL remains candidate;
- `EmployeeBaseViews` read vs table write;
- no SQL execution/DB connection/deployment authority.

Limits: bounded scanner, not a T-SQL parser; dynamic/control-flow gaps; selected
catalog supplied by caller.

### R4 — expander and full loop

Status: **expander candidate FAIL; full loop not implemented**.

Expander pilot results on 12 dev cases:

| Metric | B0 | Candidate | Target | Result |
|---|---:|---:|---:|---|
| Evidence Recall@10 | 0.4722 | **0.2917** | B0 + 10pp | FAIL (−18.06pp) |
| Additional source reads | 2.75 | 5.75 | ↓ ≥30% | FAIL (+109.1%) |
| Additional context tokens | 3,716 | 2,433 | ↓ ≥20% | PASS (−34.5%) |
| Task-correctness proxy | 0.3333 | 0.2500 | ≥ B0 | FAIL |
| Citation/source accuracy | n/m | n/m | 100% | NOT MEASURED |
| Search p95/PSS/indexing/concurrency | B0 only | n/m | relative | NOT MEASURED |

Why: ranking by path/method overlap displaced B0 evidence; expansion consumed
the item budget; file diversity increased; no corrective CHECK loop. The
follow-up R4-A/B also failed; D1 underperformed fixed FTS (`0.3472` vs `0.3750`);
D2 tied fixed FTS (`0.3750`) and lost D09.

R4 audit: candidate/selector cannot reach the target; even a perfect selector
over the six current calls reaches only ~9/25; a new retrieval/discovery
experiment requires owner approval. R1 lightweight remains fallback.

### R5 — paired qualification

Status: **not implemented**. No frozen control/candidate paired agent run on dev
and held-out, no source-byte/time/PSS gate, no human/LLM adjudication.

### R6 — release/rollback

Status: **not implemented**. No opt-in RAG feature, packaging, public
capability/unsupported contract, or RAG sidecar rollback release.

## 5. Measurement corrections that affect RAG

- E5: “FTS” harness requests were primary FTS+Vector + supplemental route.
  Pure FTS is model-independent; E4 FTS claim retracted.
- E6: E3/E4 route tables and E3-02A attribution are invalid as pure-route
  evidence; gold v2, hashes, index cost and resource measurements remain valid.
- E3-02B NO-GO rationale depends on invalid attribution; owner review required.
- R4 B0 comparison is an **oracle best-call** diagnostic, not the fixed
  deployable baseline. The fixed deployable FTS baseline is 0.3750.
- Do not compare token counts from different definitions (pack-only vs
  search+read); a later audit explicitly warns about this.

## 6. Canonical-versus-execution summary

| Phase | Plan | Verified execution |
|---|---|---|
| R0 | freeze all | frozen evidence exists |
| R1 | evidence pack | engine API + dev pilot; no public integration |
| R2 | C# facts | bounded side parser PASS |
| R3 | EF6/SQL expansion | source facts PASS; full expansion absent |
| R4 | multi-turn loop + public integration | expander FAIL; no loop/public |
| R5 | paired quality/footprint | absent |
| R6 | opt-in release/rollback | absent |

**Conclusion:** the project followed the RAG plan through R1–R3 and a narrow
R4 expander, then R4 failed and the plan stalled. Post-R4 work is a separate
research track, not completion of R5/R6.

## 7. Discrepancies preserved

- `docs/rag/implementation-plan.md` calls R4 “Multi-turn agent loop and public
  integration”; `rust/docs/rag-r4-candidate-pilot.md` calls a bounded expander
  “R4”. Both are real; the latter is a subset.
- `R4 followup` calls R4-A/B experiments; they are not plan phases.
- Some later handoffs refer to “R5” generically; no implementation or paired
  qualification exists. Mark such claims `OWNER_REPORTED_UNVERIFIED` unless a
  receipt appears.
- The plan still says **PROPOSED**; it was never rewritten to reflect R1–R4
  execution or post-R4 research.

## 8. Evidence anchors

- `docs/rag/implementation-plan.md:5-16` — status and phase order.
- `docs/rag/implementation-plan.md:23-53` — R0/R1.
- `docs/rag/implementation-plan.md:53-100` — R2/R3.
- `docs/rag/implementation-plan.md:100-140` — R4/R5/R6.
- `docs/rag/implementation-plan.md:187` — MVP definition.
- `docs/rag/README.md:3` — PROPOSED/2026-10-28.
- `docs/rag/hrm2-pilot.md` — source anchors/working set.
- `benchmarks/rag-impact/PREREGISTRATION.md`, `results/b0-official.json`.
- `rust/docs/rag-r0-r1.md`, `rust/docs/rag-r1-release-gates.md:1-45`.
- `rust/docs/rag-r2-csharp-facts.md:1-35`.
- `rust/docs/rag-r3-sql-facts.md:1-60`.
- `rust/docs/rag-r4-candidate-pilot.md:1-70`.
- `rust/docs/rag-r4-followup-results.md:1-50`.
- `rust/docs/rag-r4-bottleneck-and-r1-release-audit.md:1-170`.
- `rust/docs/e5-fts-reproducibility-20261114.md`, `e6-benchmark-integrity-20261114.md`.
