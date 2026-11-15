# RAG-GS — Typed Grounding Stabilization Architecture & Contract

Status: **experimental capability contract**, candidate frozen for independent
qualification. No production change.

## 1. Scope

The stabilizing layer answers one question at a time from a frozen source
snapshot and an independent verified-binding artifact. It does not generate
natural-language answers and does not read labels, gold paths, required groups,
case IDs or evaluation answer keys.

The design is intentionally an adapter over the existing `RefIndex`/`Source`
prototypes, not a new compiler, database or graph engine.

## 2. Typed entity model

| Entity kind | Resolved object |
|---|---|
| `TYPE` | class/struct/interface/record/enum declaration |
| `PROPERTY` | exact property/field; `is_dbset=True` for `DbSet<T>` mapping |
| `METHOD` | live method declaration/body |
| `SQL_BODY` | stored procedure/body object |
| `VIEW` | `CREATE VIEW` object |
| `DATA` | source/data target attached to a binding |
| `UNKNOWN` | unresolved or out-of-snapshot reference |

Every selected unit carries:

```json
{
  "path": "...",
  "start_line": 42,
  "end_line": 42,
  "role": "PROPERTY",
  "roles": ["PROPERTY", "TYPE"],
  "entity_kind": "PROPERTY",
  "resolved": {"name": "Employees", "type": "Employee", "parent": "..."},
  "provenance": [{"edge": "dbset_type", "type": "Employee", "property": "Employees"}],
  "liveness": "LIVE",
  "ambiguity": []
}
```

`roles` is a union; no processing order is allowed to overwrite a secondary
role. `role` is the intent-priority primary role.

## 3. Typed query resolution

`typed_resolution_20261115.py` classifies query intent and resolves exact
identifiers only.

### Intent classes

- property/DbSet/type mapping;
- type declaration;
- method/control flow;
- SQL/procedure invocation;
- SQL view;
- database-object kind;
- negative claim;
- generic.

### Resolution rules

1. Type, property, method and view names match **exact, case-insensitive**
   identifiers.
2. `DbSet<T>` mapping is resolved by `PROPERTY.type == TYPE.name`, not by name
   similarity.
3. SQL procedure names are exact after optional `sp_`/case normalization.
4. A view name is never a substring match, e.g. `View` is not `Views`.
5. Unknown `sp_*` names remain unknown and force abstention.
6. A method whose name equals a type/property is not admitted as task evidence;
   constructor-like members are not property evidence.
7. A primary method declaration is selected per name: verified-binding source
   and service implementation outrank thin controller distractors.
8. Alias normalization is deliberately limited to case/punctuation and the
   optional SQL `sp_` prefix. There is no broad fuzzy alias equivalence.
9. Ambiguity is recorded; it is not promoted to a verified fact.

There are no case-specific entity names or GQ IDs in the resolver.

## 4. Evidence-role contract

| Role | Meaning |
|---|---|
| `CONTROL` | live method/control unit selected for the query |
| `WRAPPER` | verified chain member between caller and SQL target |
| `TYPE` | entity/type declaration |
| `PROPERTY` | exact property or `DbSet<T>` mapping |
| `SQL` | SQL body/procedure target |
| `VIEW` | SQL view definition |
| `SUPPORTING` | adjacent evidence explicitly required by the task |
| `UNKNOWN` | unresolved/out-of-snapshot unit; never fabricated |

Role rules:

- structural reachability alone is not task relevance;
- non-gold is `UNKNOWN`, never automatically `MISLEADING`;
- `WRONG_ENTITY_KIND` is a separate label/metric;
- multi-role units preserve every role and provenance;
- a unit outside the snapshot cannot be selected.

## 5. Safety invariants

- Commented or inactive code cannot create a live binding or executable
  evidence.
- Unknown compilation context fails closed.
- Name-only and ambiguous edges cannot become `VERIFIED`.
- Transitive bindings require every edge to be verified.
- Static reachability is not runtime execution.
- Negative refutation is accepted only for an explicit closed-world/local
  method-body basis; open-world absence is not proof.
- Source drift or stale ranges are rejected by provenance checks.
- Runtime code does not read gold, labels or evaluation needles.
- Candidate and corpus identities are frozen by SHA-256.

## 6. Evaluation contract v2

`evaluate_gq_v2_20261115.py` separates:

- required group coverage and strict required-unit completeness;
- candidate coverage;
- role accuracy over known selections;
- unknown rate;
- wrong-entity-kind admission;
- misleading/unsupported admission;
- correct/false abstention;
- local negative-claim safety;
- token/resource cost.

Defaults:

- group coverage threshold: `0.5`;
- strict completeness threshold: `0.9`;
- required-group completeness: all required groups;
- non-gold selection: `UNKNOWN`, not `MISLEADING`;
- negative claims: explicit basis required; `method_body_complete` or
  `closed_world`.

Historical v1 receipts and labels remain unchanged. v2 results are not
directly comparable metric-for-metric where denominators or negative-basis
semantics differ.

## 7. Regression foundation

`typed_resolution_regression_20261115.py` covers 19 checks:

- exact property/DbSet mapping vs view substring;
- exact view resolution;
- unknown SQL near-name abstention;
- method exact identity;
- negative query intent with no SQL admission;
- synthetic rename preserving entity kind;
- irrelevant sibling noise;
- comment/uncomment liveness transition;
- source drift changing ranges;
- multi-role union/provenance merge;
- corpus integration cases.

These fixtures are **not** held-out qualification data.

## 8. Capability matrix

| Capability | State |
|---|---|
| Exact C# type/property/method identity | supported |
| `DbSet<T>` property/type mapping | supported |
| SQL procedure/view exact identity | supported |
| Comment/string/preprocessor liveness | supported |
| Verified direct/wrapper binding | supported |
| Local method-body negative basis | supported with explicit scope |
| Multi-role/provenance representation | supported |
| Dynamic SQL execution authority | unsupported |
| Global absence proof | unsupported |
| Non-C# semantic parsing | not claimed |
| Production qualification | not claimed |
| Independent GQ5 qualification | not run in this mission |

## 9. Known limits

- Evaluation sets remain tiny and machine-adjudicated.
- SQL is the only non-C# positive tested.
- GQ/GQ2/GQ3/GQ4 have been observed and are regression/development evidence,
  not independent generalization.
- The resolver is intentionally shallow; it does not resolve all C# semantics.
- No claim generator exists; claim support is evidence adequacy.
- `UNKNOWN`/`SUPPORTING` retention is bounded by the four-unit selection cap.

## 10. Safety and owner gates

No production schema, storage, ranking, model, default, binary, daemon or
installed-index change was made. A formal GQ5 campaign is not opened here; it
requires owner authorization.
