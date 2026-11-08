# H7 E1R — anchor-only facts + compact declaration index

Status: **resource-refactor experiment pre-registered before implementation.**
Date: 2026-11-08. Base `e7eac77`; worktree `h7-resource-20261108`.
Historical H7 E1 gate/receipt (`discovery-h7-e1.json`) and E1 forensics remain
immutable. H7 E1 stays historical NO-GO. E2 is not authorized.

## 1. Scope

Replace the full `ProjectFacts` parse/merge used by the H7 E1 measurement with:

- **anchor-only full facts**: methods/assignments/invocations/saves only for
  files containing the frozen anchor spans;
- **compact project-scoped declaration index**: types, fields, DbSets and
  selected SQL definitions for all compile files, with cross-file partial
  classes and `(namespace, type)` scope resolution.

No persistent index, new parser, LSP, model, or identifier hardcode. SQL remains
the existing `SqlCatalog` selected-variant path. Typed closure/provenance and
`resolved`/`candidate`/`unresolved(reason)` semantics are unchanged.

## 2. Pre-change profile (required before refactor)

Before changing product code, profile the current full path:

- P1/P2/P3 parse time and retained/peak PSS/RSS;
- fact counts by type: types, methods, fields, db_sets, assignments,
  invocations, saves;
- merge/clone time and PSS delta;
- per-case refs-off/on closure cost.

Profile receipt is separate; no product/parser change in that step.

## 3. Coverage and budget semantics (unchanged)

- cap 500 distinct candidate ranges/case;
- coverage at 10/50/100/250/500;
- same path + inclusive line-overlap span coverage; one gold span counted once;
- anchors from the frozen B0/H1 calls (6 calls/case, upstream); no additional
  broad search; exact project-fact lookups are not search calls;
- refs-off is `expand_with_closure(..., false)`; refs-on is true.

## 4. Gate (fixed before measurement)

| Gate | Threshold |
|---|---|
| Coverage @500 dev12 | **≥23/25** |
| Coverage @250 / @100 | report; no new lower threshold |
| Independent easy | 5/5 |
| Independent hard | **≥4/5** (same historical independent gate) |
| Determinism | identical candidate hash/coverage in two fresh runs |
| Provenance | 100% of closure items have anchor/expression/declaration/target/rule/status |
| No hardcode | no `CoreDBContext`/HRM identifier special case in product diff |
| Peak PSS/RSS | **≤64 MiB (65,536 KiB)** |
| Latency | warm p95 per case ≤5 s; cold/warm reported separately |
| I/O | build source bytes vs rchar reconciled; per-case I/O reported |

If resource passes but coverage/independent fails, report NO-GO. If resource
passes and coverage passes but pool churn remains, only analyze an
anchor-bounded closure option; do not tune caps against gold.

## 5. Predictions

- Refactor removes most of the full method/assignment/invocation graph and the
  full ProjectFacts merge; expected peak PSS ≤64 MiB if compact declarations are
  retained instead of full `FileFacts`.
- Coverage risk: anchor-only full facts may lose query-matching methods outside
  anchor files. If @500 <23, report the exact lost spans; do not add a broad
  search or gold feature.
- Cross-file partial/DbSet resolution must stay resolved; if not, report
  `unresolved(type_decl_missing|dbset_decl_missing)` rather than name fallback.

## 6. Stop conditions

- No persistent cache/index, no parser rewrite, no new language adapter.
- No gate change after measurement.
- No E2, Selection/Expansion, push, release, or default promotion.
- Held-out, corpus, gold, targets unchanged.
