# RAG reference-intelligence failure matrix — 2026-11-15

## 1. Failure modes

| ID | Failure mode | Evidence | Root cause | Reference candidate response | Residual |
|---|---|---|---|---|---|
| F1 | Cross-file candidate missing | Initial union 18/25; `CoreDBContext`, `Employee`, SQL absent from S1 pool | No property/type/call/SQL edge layer | Typed `property_type`/`call` expansion adds 4 dev spans: union **22/25**; held-out **20/20** | D07/D12 SQL still missed; method-name→proc binding not implemented |
| F2 | Container dominates selection | S1 whole-class containers; S1b 10 spans but high context | Ranking rewards broad lexical overlap | Header/block/prefix ranged units and relation edges | Dev selection still only 10/25 |
| F3 | Structural edge ≠ evidence relevance | S1b false-edge 75% dev / 86% held-out; refint selected non-gold **91/101** dev, **59/64** held-out | Direct calls point to shared helpers, sibling operations, generic entities | Helper filter, intent gate, fan-out 2, per-file/seed caps | Still fails; relation relevance unresolved |
| F4 | Query-to-evidence semantic gap | D02/D05/D06 remain low despite edges inside files | Query wording (“personal update”, “detail”, “tổng nhân sự”) does not map deterministically to method names | Domain aliases `personal→employee`; intent write/read | Only partial; no semantic resolver |
| F5 | SQL binding incomplete | 2 SQL files, only **1** explicit `sql_proc` edge; D07/D12 SQL absent from candidate union | Most methods call a wrapper by method name, not `execute sp_*` | Explicit `exec` strings and whole-file add | Method-name → procedure binding is a clear next unit |
| F6 | Held-out contamination | S1b held-out was already observed | S1/S1b evaluation history | None | This run is evaluation, not a clean independent lock |
| F7 | Resource fan-out | 30,323 edges; 462 dev files / 3.85 MB read; 545 held-out files / 4.38 MB | Full corpus parse and bounded seed expansion | Max depth 2, 12 seeds, per-path caps | No production indexing or incremental reuse yet |
| F8 | Entity over-promotion | `Employee*` entities dominate selected units | Property-type expansion rewarded entity type without task necessity | Entity names restricted; non-gold penalty | Insufficient; 59/64 held-out non-gold |

## 2. Why the candidate succeeds but selection fails

The reference layer accurately resolves **real** relationships:

- `CoreService.Context : CoreDBContext` → `CoreDBContext` declaration;
- `CoreDBContext.Employees : DbSet<Employee>` → `Employee` declaration;
- `CoreDBContext.EmployeeDetails : DbSet<EmployeeDetail>` → `EmployeeDetail`;
- `RestoreEmployeeById()` calls `UpdateOrgTotalEmployee()`;
- `UpdateOrgTotalEmployee()` is declared in `OrganizationCommand`.

These are source-provenance edges, not fabricated evidence. The candidate union
reaches 22/25 dev and 20/20 held-out. But a resolved edge does not state that the
target is **required** by the question. Selecting an entity/property because it
is reachable produces a large amount of plausible-but-irrelevant context.

The selection failure is therefore **relevance**, not reference resolution.

## 3. D02 example

The reference index can reach:

- `EmployeeCommand.EmployeeEditThongTinCaNhan` (215–285, 287–425);
- `CoreDBContext` (17–20, 41–55);
- `Employee` and `EmployeeDetail` declarations.

Yet the final dev selector chose mostly `Employee*` headers/entities and only
1/6 gold spans. The query “Personal update ghi vào những nơi nào?” does not
lexically identify `EmployeeEditThongTinCaNhan`; the graph knows the file but not
that this method is the requested write path.

## 4. D07/D12 SQL example

`sp_UpdateOrgTotalEmployee.sql` is present in the corpus. The reference index
contains its procedure name and tables, but only one method has an explicit
`execute/exec sp_*` edge. D07/D12 methods call a wrapper named
`UpdateOrgTotalEmployee`; without a method-name→procedure binding, the SQL
candidate is not proposed. This is a concrete, bounded missing capability, not
a reason to build a full SQL graph.

## 5. Rejected approaches

- **Graph database / large framework**: unnecessary; a 12 MB sidecar covers the
  observed gap and is easier to verify.
- **LLM/embedding reranker**: out of scope; no evidence that ranking is the only
  bottleneck when the required relation is absent.
- **More heuristic edge weights on the same metadata**: S1b already showed
  overfitting and false edges; repeating it would not reduce uncertainty.
- **Unfreezing production Selection/Expansion**: unsupported; all selection
  gates fail.

## 6. Decision

- Keep the reference index as an **experimental discovery/provenance layer**.
- Do not wire it into production retrieval.
- Do not claim task-relevant selection.
- If continued, the next unit is **method-name→SQL-procedure binding plus a
  query/task relevance test** with a fresh held-out fixture, not more scoring
  over the same metadata.
