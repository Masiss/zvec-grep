# Discovery H7 — fact-layer typed reference closure

Status: **E1 pre-registered before implementation/measurement.**
Date: 2026-11-08. Base: `work/zg-recovered` at `88b91d7`.
Worktree: `/home/masis/src/worktrees/zvec-grep/h7-e1-20261108`.
Historical H7/H6 text and receipts are not rewritten; this is a new
pre-registration.

## 1. Why

H5 reaches 19/25 only by reordering the H2 pool; H6 reaches 20/25 by
frequency-ranked observed-name search (incidental D02 span, 10.97 s p95). The
remaining spans are definition locations reachable through typed references:
D02 C4 17–20/41–55, D02 C5, D10 C4, D12 S1.

E1 tests the existing product fact layer, not a new Python parser.

## 2. Vehicle and scope

- Reuse `rust/crates/zg-engine/src/extraction/{csharp_facts,sql_facts,candidate}.rs`.
- Remove the `CoreDBContext` special cases in `candidate.rs` and
  `csharp_facts.rs`; replace with project-scoped receiver/declaration
  resolution (`ProjectScope.fields_by_type`, type facts, `DbSetFact`).
- Add `line_start`/`line_end` to `DbSetFact`, populated from the declaration.
- Closure is limited to:
  1. `SaveFact` (context type / save target),
  2. `AssignmentFact` (`receiver_type`, field/expression),
  3. `DbSetFact.element_type` (receiver property access),
  4. selected static SQL binding (`BindingStatus::Resolved` + selected
     definition).
- No new index, LSP, model, graph store, or persisted cache.
- No Rust/TS/docs adapters in E1.

## 3. Provenance and status

Every candidate added by closure must carry:

```text
anchor: path:start-end
expression: exact observed expression
declaration: path:start-end or project-scope fact
target: path:start-end
rule_id: save | assignment | dbset-element | sql-selected
status: resolved | candidate | unresolved(reason)
```

- `resolved`: exact declaration/definition found, no ambiguity.
- `candidate`: multiple plausible declarations or name-only fallback; never
  counted as resolved.
- `unresolved(reason)`: no path; not emitted as a closure candidate. Reason
  codes are counted in the receipt.
- No relation is invented when the path is absent.

## 4. Budget and search-call semantics

- `budget` = maximum number of distinct candidate ranges in the pool after
  path/line dedup; cap 500. Rankings are deterministic (score desc, path,
  start, end).
- Coverage is evaluated at pool prefixes **10, 50, 100, 250, 500**.
- A gold span is covered when one candidate has the same path and inclusive
  line overlap (`candidate.start <= gold.end && gold.start <= candidate.end`).
  A larger definition range covers a contained gold span; each gold span is
  counted once. `range precision` = covered gold spans / emitted candidate
  ranges (report; diagnostic, not a gate).
- `search calls` = every invocation of the existing search/FTS/rg used to
  obtain anchors or exact definitions, counted per case. E1 uses the frozen H1
  anchor calls (up to 6) and **zero additional broad search**; any exact
  definition lookup through project facts/catalog is counted as `0 search
  calls` and separately as `exact_lookup`. E1 fails if broad search calls
  exceed 6/case.

## 5. Predictions (before measurement)

| Prediction | Value |
|---|---:|
| E1 @500 | **23/25** (minimum pass 22) |
| E1 @250 | **20/25** (minimum pass 19) |
| E1 @100 | **13/25** (minimum pass 13) |
| Refs-off ablation @500 | **19/25** (must equal H5) |
| D10 C4 9–20 | hit via field/receiver type |
| D02 17–20 and 41–55 | hit via DbSet/entity declaration |
| D02 C5 1–65 | hit via `DbSet<Employee>` element type |
| D12 S1 1–95 | hit via selected static SQL binding |

Missing spans remain allowed; they must have a reason code.

## 6. Negative controls / determinism

- Refs-off ablation: same anchors and ranking, closure disabled; must reproduce
  H5 19/25 @500.
- No identifier literal in the diff for `CoreDBContext`, `Employee`,
  `EmployeeDetail`, or `sp_UpdateOrgTotalEmployee` as a special case; mechanical
  grep gate.
- Two independent runs (fresh process) must produce the same candidate hash and
  coverage. Source traversal is sorted/deterministic.
- Ablation with `DbSetFact` line spans missing must be reported; it must not be
  used to claim the final result.
- Negative control: a query/anchor with no closure path must emit
  `unresolved(no_path)` and must not add a lookalike.

## 7. Independent dev sets

- 12 frozen dev cases.
- Easy independent 5 cases (`cases/dev-independent.json`).
- Hard independent 5 cases (`cases/dev-independent-hard.json`).
- Held-out untouched. No corpus/gold/target change.
- E1 passes only if hard independent ≥4/5 and no dev regression at any
  reported budget. If the independent harness cannot use the same anchors
  without a new corpus/index, report the limitation; do not substitute a new
  evaluation silently.

## 8. Resource gates

- Warm p95 ≤5 s/case; cold parse cost reported separately.
- Peak PSS ≤64 MiB; RSS/VmHWM reported.
- Searches ≤6/case (broad); exact lookups reported separately.
- I/O reconciled: source bytes, search output bytes, exact-lookup bytes, and
  process rchar are reported; rchar is not used as source-I/O attribution when
  it does not reconcile.
- CPU user+sys, wall p50/p95/p-max, candidate bytes/range precision reported.

## 9. E2 condition

E2 is **not authorized**. It may be pre-registered only if E1:
1. has a concrete `unresolved(reason_code)` histogram,
2. misses the E1 gate at a specific budget,
3. has a rule-level fix that does not add an adapter, and
4. stays within the same two-experiment cap.

E1 FAIL => NO-GO, stop; no Selection/Expansion work.
E1 PASS => present coverage/provenance/unresolved/resource evidence and let the
owner decide E2 or freeze Discovery. E1 PASS does not declare RAG v1.0 GO.
