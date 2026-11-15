# RAG reference-intelligence architecture — 2026-11-15

Status: **experimental candidate, not production.** Branch
`work/rag-refint-20261115`. No engine schema, daemon, binary, installed index or
production Selection/Expansion change.

## 1. Decision

The mission tested whether a lightweight source-reference layer can turn
lexical/semantic candidates into task-relevant evidence with provenance.
The result is **partial**:

- typed property/call/SQL expansion **closes most of the candidate gap**
  (dev 18/25 → 22/25; held-out 20/20);
- the same expansion does **not** produce reliable task-relevant selection
  (dev 10/25; 91/101 selected units non-gold; held-out 59/64 non-gold).

The architectural conclusion is:

> Reference metadata is useful for **candidate discovery and provenance**, but
> structural reachability is not evidence relevance. Selection must be driven by
> query/task semantics and validated false-edge controls; it cannot simply reward
> relation distance.

## 2. Reference model

`RefIndex` is rebuilt from the frozen corpus and stored as a standalone JSON
artifact. It is not a graph database and not persisted into the engine index.

| Entity | Count |
|---|---:|
| Types | 1,370 |
| Methods | 3,382 |
| Properties | 8,602 |
| SQL files | 2 |
| Edges | 30,323 |
| Resolved edges | 8,666 |
| Call edges | 21,720 |
| Ambiguous call names | 3,106 |
| Helper-name call edges | 5,019 |
| Explicit SQL-proc edges | 1 |

Each edge carries:

- `kind`: `property_type`, `call`, `sql_proc`;
- source `path` and inclusive `start/end`;
- target declaration `path` and inclusive `start/end`;
- target count, `resolved`, and helper flag where applicable;
- for SQL, procedure/file name and table names.

Unresolved and ambiguous edges remain visible but are **not promoted** to
evidence. A property type such as `DbSet<Employee>` resolves to the `Employee`
declaration; a property such as `Context : CoreDBContext` resolves to the
`CoreDBContext` declaration.

### What is intentionally absent

- No full type system, overload resolution, namespace alias resolution or
  inheritance closure.
- No SQL table/column graph.
- No graph database, vector store or LLM.
- No persisted engine-side metadata or migration.

These omissions are deliberate: the experiment needed to establish whether
direct typed edges already carry usable evidence before building heavier
infrastructure.

## 3. Runtime pipeline

```
frozen probe candidates
        |
        +--> structural units (class/method/block/prefix/header)
        |
        +--> exact code-symbol seeds
        |
        +--> RefIndex typed expansion (depth <= 2, fan-out bounded)
                 property_type | call | SQL proc | identifier type
        |
        +--> query intent + lexical/action scoring
        |
        +--> diversity + token-budget pack (<= 3716)
```

### Seeds

- strongest initial units by query lexical/body overlap;
- exact code-symbol declarations for query identifiers of length >= 6;
- bounded to 12 initial seeds plus exact symbols.

### Traversal

- depth at most 2; helper calls are skipped unless query-relevant;
- direct calls only, and only when the target is query-relevant or the intent is
  write/flow;
- property types only for `Employees`, `EmployeeDetails`, `Context` and
  query-matching names;
- entity targets restricted to `DBContexts/` and `Services/` paths;
- SQL only for explicit `execute/exec sp_*` strings; whole SQL file added when
  <= 140 lines.

This is intentionally conservative at the edge level. The failure is in the
**relevance/ranking** stage, not in fabricated references.

### Scoring and packing

Features: name/body TF-IDF, exact symbol, write/read/SQL intent action,
relation distance, structural granularity, entity/header bonuses. Selection
preserves exact/initial evidence first, deduplicates overlap, caps three units
per file and four per seed, then trims by the frozen tokenizer.

## 4. Intent model

A small deterministic lexicon maps query terms to:

- `write` (update/save/restore/ghi/cập nhật/sửa/xóa/tạo/ảnh hưởng);
- `read` (view/get/list/đọc/hiển thị);
- `sql` (proc/procedure/SQL/database/table);
- `flow` (call/route/through/gọi/đi qua).

Domain aliases `personal`/`nhân viên`/`hồ sơ` → `employee` are query-only and
do not expose gold paths. Intent affects **action bonuses**, not edge existence.

## 5. Production integration (not implemented)

A production candidate would need:

1. Engine-side extraction of the same typed edges during indexing.
2. A sidecar or additive index section with schema/version and invalidation.
3. Incremental reuse and crash recovery for the sidecar.
4. Query-to-evidence relevance separate from structural reachability.
5. False-edge accounting and a no-answer policy.
6. Migration/rollback plan for the sidecar; no change to the v3 core index is
   required by this prototype.

Do **not** merge this branch or replace the running binary/index. The
experimental JSON sidecar is not a production metadata store.

## 6. Rollback and safety

- No write to `.zvec-grep`, installed binaries, daemon or config.
- The reference index is external and rebuildable; deleting it has no effect on
  retrieval.
- Rollback of this experiment is deletion of the branch/worktree and external
  artifacts; no data migration.
- If production integration is later authorized, use a versioned sidecar with
  rebuild-only activation and keep v3 index rollback intact.

## 7. Decision ledger

| ID | Hypothesis | Evidence | Verdict |
|---|---|---|---|
| H-R0 | Missing candidate coverage is caused partly by absent cross-file/property/SQL expansion | Dev union 18→22/25; held-out 20/20; property/call edges resolve `CoreDBContext`, `Employee`, `EmployeeDetail`, `OrganizationCommand` | **PASS (candidate coverage)** |
| H-R1 | Task-relevant filtering makes reference expansion improve selection | Dev 10/25, 91/101 selected units non-gold; held-out 59/64 non-gold | **FAIL** |
| H-R2 | Reference selection generalizes to held-out | Held-out 8/20, macro recall 0.5625, strict 0.375; better strict than S1b but fewer spans | **INCONCLUSIVE / partial** |
| H-R3 | SQL name binding is sufficient without explicit `exec` strings | Only one explicit SQL-proc edge; D07/D12 SQL absent from candidate union | **OPEN** |
| H-R4 | Reference index is resource-light enough for experimentation | 12 MB JSON, 25 s dev wall, 134 MB RSS | **PASS (experiment only)** |
| H-R5 | Reference metadata can be added without core schema migration | External sidecar only; no engine change | **PASS (prototype)** |
