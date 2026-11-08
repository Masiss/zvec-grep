# RAG discovery research — 16 non-retrievable gold spans

Status: **DISCOVERY RESEARCH ONLY. R4-A/B remain NO-GO. No candidate change,
no new experiment execution, no held-out access.**
Date: 2026-11-08. Baselines remain historical: B0 oracle **0.4722**, deployable
fixed `fts_code_tokens` **0.3750**. Targets are unchanged.

Inputs: frozen B0/candidate receipts, `cases/dev.json`, corpus manifest and the
read-only HRM2.0 source. Raw audit:
`benchmarks/rag-impact/results/discovery-layer-audit.json`.
Tool: `benchmarks/rag-impact/tools/audit_discovery_layers.py`.

## 1. Layer result

The union of all six search calls contains 9/25 gold spans. The 16 discovery
misses split as follows:

| Layer | Count | Cases |
|---|---:|---|
| Corpus/index coverage | **0** | all 16 paths are present in the frozen corpus and reachable by a path-filtered exact/body FTS probe |
| Query formulation | **9** | D02 (6), D07 C7, D10 C4, D12 S1 |
| Candidate retrieval/ranking depth | **7** | D01 C2, D03, D05, D06 C2 427–457, D06 C7, D07 C2, D12 C7 |
| File hit but missing source range | **7** | the same 7 ranking-depth cases: file appears in B0 top-10, but the selected ranges are other methods/entities |

The last two rows are the same evidence at different levels: ranking depth
manifests as a file hit with the wrong range. No case was found where the file
was absent from the index. No case required a new LSP/model/index to reach the
source; path-filtered FTS with `--limit 50` reaches every audited span.

## 2. Per-case evidence

| Case | Missing spans | B0 file hit | Exact/body probe | Layer |
|---|---:|---|---|---|
| D01 | C2 215–285 | yes | `EmployeeEditThongTinCaNhan` | file hit/range |
| D02 | C2 215–285, 287–425; C4 17–20, 41–55; C5 1–65; C6 1–100 | no | `EmployeeEditThongTinCaNhan`, `AddFromAssembly`, `EmployeeDetails`, `Employee`, `EmployeeDetail` | query formulation (6) |
| D03 | C2 215–285 | yes | `Keyword` path-filtered returns C2 237–276 | file hit/range |
| D05 | C2 215–425 | yes | `SaveChanges` path-filtered returns 271–304/391–434 | file hit/range |
| D06 | C2 427–457; C7 1057–1074 | yes | `UpdateOrgTotalEmployee` returns C2 428–472 and C7 1037–1099 | file hit/range (2) |
| D07 | C2 539–560; C7 1057–1074 | C2 yes, C7 no | `DeleteEmployeeByIds` returns C2 503–553; `UpdateOrgTotalEmployee` reaches C7 | file hit/range + query formulation |
| D10 | C4 9–20 | no | `CoreDBContext` returns C4 1–35 | query formulation |
| D12 | C7 1057–1074; S1 1–95 | C7 yes, S1 no | `UpdateOrgTotalEmployee` reaches C7; `sp_UpdateOrgTotalEmployee` returns S1 1–70 | file hit/range + query formulation |

### Priority cases

- **D02 (6/25):** all six are query formulation. The B0 questions are
  natural-language and never include `EmployeeEditThongTinCaNhan`,
  `CoreDBContext`, `Employee`, or `EmployeeDetail`. Exact/path-filtered probes
  retrieve every span. This is the largest general-purpose lever.
- **D03 (1/25):** file hit but range miss. `Keyword` is in the question and
  path-filtered FTS returns C2 237–276; global top-10 selects other ranges/files.
  This is ranking/range depth, not corpus coverage.
- **D05 (1/25):** file hit but range miss. `SaveChanges` path-filtered returns
  C2 271–304/391–434; the six queries do not carry that body term/identifier.
  Query/range depth.
- **D12 (2/25):** C7 is file-hit/range-miss; S1 is query formulation. The proc
  name is the decisive identifier; exact `sp_UpdateOrgTotalEmployee` returns S1.
- D01/D06/D07/D10 are the same two patterns at smaller counts.

## 3. What this means

- **Retrieval/discovery remains the bottleneck**: 16/25 gold is outside the six
  search calls. The 9 query-formulation spans are reachable with identifiers
  already present in the source; the 7 file-hit spans are reachable with body
  terms already present in the file.
- **Ranking/range is secondary but real**: the file is often in top-10 and the
  gold range is another method/entity in that file.
- **No corpus/index coverage failure** was found for these 16 spans. Do not add
  an LSP, LLM, new index or new corpus without evidence from a targeted
  experiment.

## 4. Proposed next experiments (pre-registration required; not run)

### R4-D1 — Identifier/relationship query expansion (discovery)

- From the top seed files/entities, extract generic method/type/procedure names
  and run at most 2 bounded exact/lexical probes, restricted to the same file or
  referenced entity.
- Rank by exact identifier overlap and method/entity containment, not path token
  frequency. Dynamic/unknown targets stay candidates.
- Deployable policy: no gold, fixed call/file budget, deterministic fallback;
  compare against fixed `fts_code_tokens` 0.3750 and historical oracle 0.4722.
- Expected to address the 9 query-formulation spans, especially D02 and D12 S1.
- Pre-registration must freeze queries, budget, corpus and metrics before a run.

### R4-D2 — File-hit range-aware packing (ranking/range)

- When a file is in the seed set, add all source ranges whose method/body
  matches query terms or the file's identifier; merge/dedup ranges and cap the
  number of files.
- Deployable policy: no gold; use method containment, exact identifier/body
  overlap and a fixed byte/file budget.
- Measure Recall@10 plus correctly defined additional reads (files opened after
  the pack, not files represented in the pack).
- Expected to address the 7 file-hit/range spans (D03/D05/D06/D07/D12) and to
  prevent the earlier false read/token comparisons.

Both experiments are general-purpose and must not encode HRM-specific file or
method names. No implementation or run is authorized by this research note;
owner approval and a fresh pre-registration are required.

## 5. Baseline discipline

- B0 oracle **0.4722** is historical reference only; it uses gold to select the
  best call.
- Fixed `fts_code_tokens` **0.3750** is the deployable comparison.
- Targets are unchanged. Any future result that cannot separate the oracle from
  a deployable selector is not a target result.
