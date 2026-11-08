# R4 failure attribution — frozen candidate pilot

Status: **READ-ONLY AUDIT. R4 remains NO-GO; frozen B0/candidate results and
targets are unchanged. No code, corpus, target or held-out data was modified.**
Date: 2026-11-08. Inputs: `benchmarks/rag-impact/results/b0-official.json`,
`benchmarks/rag-impact/results/candidate-pilot.json`,
`benchmarks/rag-impact/cases/dev.json`, the frozen corpus manifest and the
read-only HRM2.0 checkout.

Raw per-span audit: `benchmarks/rag-impact/results/r4-failure-attribution.json`.
Tool: `benchmarks/rag-impact/tools/audit_r4_failure.py`.

## 1. Result

25 gold spans across 12 development cases. The candidate packed **5/25 (20%)**;
20/25 were lost. The dominant cause is not a single ranking formula: the
candidate selected the wrong B0 seed call, then packed 8 fixed seeds and only 2
expansion items.

| Cause | Spans | Share | Meaning |
|---|---:|---:|---|
| Packed | 5 | 20% | Final 10-item pack covers the gold span |
| Seed-set selection | 6 | 24% | Gold was in a B0 seed/call, but the candidate chose another call or an empty pack |
| Range selection | 4 | 16% | The correct file was packed, but a different method/range was selected |
| Discovery | 10 | 40% | No chosen seed/file reached the gold span |
| Dedup loss | 0 | 0% | No exact-duplicate removal lost a distinct gold span |
| Context-packing loss | 0 | 0% | No 24 KiB/token truncation lost a selected span |

Ranking/budget is a cross-cutting mechanism, not a separate bucket: 8 of 10
slots were reserved for one call's seeds (scores 1000–993), leaving 2 expansion
slots (scores 320–560). Target methods usually scored below the cutoff. Dedup
and context truncation do **not** explain the recall loss.

## 2. Per-case evidence

### D01 — seed-set selection + discovery
- Chosen call: `rg_code_tokens`; B0 best call: `fts_code_tokens`.
- Final pack: 8 single-line seeds from `AuthComponent.cs`/`BuilderTable.cs`,
  plus 2 `AuthComponent` spans. C1/C2 are absent.
- Gold C1 `EmployeeController.cs:459–620` was a B0 seed but was in the unchosen
  call → **seed-set selection**.
- Gold C2 `EmployeeCommand.cs:215–285` was in no chosen seed file → **discovery**.
- Candidate recall 0.0 vs B0 0.5.

### D02 — discovery
- All 6 gold spans missing: `EmployeeCommand.cs:215–285`, `287–425`,
  `CoreDBContext.cs:17–20`, `41–55`, `Employee.cs:1–65`,
  `EmployeeDetail.cs:1–100`.
- No chosen seed overlapped them; the candidate had no expansion path to those
  files. B0 was also 0.0. → **discovery**, not packing.

### D03 — range selection
- Gold `EmployeeCommand.cs:215–285`.
- Final pack contained the same file but `545–599` and `562–573`; the target
  method was not selected. Cutoff 410; the target's estimated committed score
  is ~60 (seed file + `Keyword` assignment match). → **range selection plus
  ranking/budget**.

### D05 — range selection
- Gold `EmployeeCommand.cs:215–425`.
- Final pack contained the same file but `587–636`. Cutoff 450; target estimate
  ~60 (seed file + `EmployeeDetails` match). → **range selection plus
  ranking/budget**.

### D06 — seed-set selection + range selection
- Chosen `hybrid_question`; B0 best `fts_first_phrase`.
- Gold `EmployeeCommand.cs:215–285` was a B0 seed in the unchosen call →
  **seed-set selection**.
- Gold `EmployeeCommand.cs:427–457` was not reached → **discovery**.
- Gold `OrganizationCommand.cs:1057–1074`: the file was packed, but the final
  range was `1127–1158` → **range selection**.
- Candidate recall 0.0 vs B0 0.333.

### D07 — seed-set selection + range selection + discovery
- Chosen `fts_first_phrase`; B0 best `rg_code_tokens`.
- Gold `sp_UpdateOrgTotalEmployee.sql:58–95` was a B0 seed in the unchosen call
  → **seed-set selection**.
- Gold `EmployeeCommand.cs:539–560`: final same-file range was `215–459`
  → **range selection**.
- Gold `OrganizationCommand.cs:1057–1074` was not seeded → **discovery**.
- Candidate recall 0.0 vs B0 0.333.

### D11 — seed-set selection
- Chosen `hybrid_question`; B0 best `fts_code_tokens`.
- Gold `EmployeeCommand.cs:866–887` was a B0 seed in the unchosen call and is
  absent from the final pack. → **seed-set selection**.
- Candidate recall 0.0 vs B0 1.0. This is the largest single-case regression.

### D12 — empty-pack selection
- Chosen call produced an **empty final pack** (0 items, 0 reads, 0 tokens)
  despite B0 having 10 hybrid units and 3 file reads.
- Gold `OrganizationCommand.cs:1057–1074` and
  `sp_UpdateOrgTotalEmployee.sql:1–95` both missing. → **seed-set/empty-pack
  selection**, not a content-ranking issue.
- The empty pack also makes any read/token comparison for this case meaningless.

### D04, D08, D09, D10 — packed or discovery
- D04, D08 (2), D09, D10 (one span) are packed. D10's other gold span
  (`CoreDBContext.cs:9–20`) is a discovery loss.
- These cases show the expander can work when the chosen seeds already include
  the right file/entity.

## 3. Ranking, budget, dedup and packing

### Ranking and budget
- The final pack is structurally 8 best-call seeds + 2 expansion items in 11/12
  cases; D12 is empty.
- Seed scores are forced to 1000–993. Expansion scores are 320–560. Target
  methods in D03/D05/D06/D07 have estimated scores ~60–220 under the committed
  scoring rules. They cannot enter the top 10 even when the right file is a
  seed file.
- This is a **priority/budget allocation** problem, not a small score
  miscalibration: 80% of the pack is reserved before expansion.
- The chosen-call heuristic is the main selector. It differs from the B0 best
  call in D01, D06, D07 and D11; those cases lost 6 gold spans. The heuristic
  rewards path/method token hits and a code-label bonus, but does not value
  method containment, span richness or call diversity.

### Dedup
- Final items are unique by `(path, start_line, end_line)`. No distinct gold
  span was removed by exact dedup. Contribution to recall loss: **0**.
- Dedup only collapsed duplicate seed ranges; it did not create a wrong range.

### Context packing
- Candidate pack tokens: min 0, max **4,670**, mean 2,433; the frozen pack budget
  is 24 KiB, so there was **no token/byte truncation**. Context-packing recall
  loss is **0**.
- The packing strategy is still costly: the pack spans **5.75 files/case** and
  mostly single-line seeds. It creates file spread without adding recall.
- **Measurement caveat:** the frozen candidate pilot counted “reads” as distinct
  files represented in the pack, while B0 counted files read after search. It
  also counted candidate tokens from final item content only, while B0 counted
  search payloads plus read content. The reported reads (+109%) and tokens
  (−34.5%) are therefore **not like-for-like** and must not be used as candidate
  evidence. Only the recall/task-proxy regression is robust in this pilot.

## 4. Discovery vs selection vs packing

| Class | Count | Evidence |
|---|---:|---|
| Discovery | 10 | D02 all 6; D01 C2; D06 C2 427–457; D07 C7; D10 C4 |
| Selection — seed set | 6 | D01 C1; D06 C2 215–285; D07 S1; D11 C2; D12 C7 + S1 (empty pack) |
| Selection — range | 4 | D03, D05, D06 C7, D07 C2 (same file, wrong range) |
| Packing — content truncation | 0 | Max pack 4,670 tokens << 24 KiB; no selected span was truncated |
| Dedup | 0 | Unique final ranges; no distinct gold removed |

The largest lever is **discovery (40%)**, which cannot be fixed by reordering the
same seeds. The second is **selection (40% combined)**, dominated by choosing one
call and reserving 8 slots for it. Packing and dedup are not recall bottlenecks;
they are cost/measurement issues.

## 5. Proposed next R4 experiments (maximum two)

Both are general-purpose: method containment, exact identifier overlap, call
diversity and file clustering. No HRM-specific file/method names, no new model,
no index-format change, no target change.

### R4-A — Multi-call seed retention + method-level ranking (recall-first)
- Do not pick one call. Keep a per-call quota (e.g. top 1–2 units per call) or
  retain the union, and rank units by method/entity containment, exact
  identifier/token overlap, span density and call diversity.
- Prefer method/entity ranges over single-line hits; keep at least one seed from
  each non-empty call.
- Reserve at least 5/10 slots for expansion; cap fixed seeds at 5.
- Add a generic identifier expansion step: from top seeds, search method/type
  names and selected SQL definitions; unresolved/dynamic targets remain gaps.
- Success metric: Recall@10 first, then the corrected reads metric. Expected to
  recover D01/D06/D07/D11 seed-set losses and D03/D05 range losses without new
  resources.

### R4-B — File-cluster packing + corrected measurement (reads/tokens)
- Pack by file clusters: choose at most 3 files by evidence score, merge
  overlapping/adjacent spans, apply a per-item byte cap, and include SQL/type
  definitions only inside the chosen cluster.
- Re-define both arms consistently: additional reads = files opened after the
  tool response; context tokens = search payload + evidence payload. Do not
  compare pack-only tokens to B0 search+read tokens.
- Success metric: files/case and tokens/case with no Recall@10 regression; no
  increase in PSS/concurrency resources. This addresses the 5.75-file spread and
  prevents another false token PASS.

Do not run either experiment until a new pre-registration is written. The old
B0/candidate receipts remain historical evidence; targets stay frozen.
