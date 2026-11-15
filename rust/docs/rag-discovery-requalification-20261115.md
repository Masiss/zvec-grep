# RAG Discovery requalification on the E8 Foundation — 2026-11-15

Status: **DISCOVERY NOT QUALIFIED (NO-GO).** The candidate pool improved, but
deployable top-10 evidence did not reach the gate. No Selection, Expansion,
delivery, release or production change was made.

Worktree: `work/rag-requal-20261115` at `27b81c0`.
External root: `/home/masis/.cache/zvec-grep/rag-requal-20261115/`.
Primary receipt: `<external>/receipts/requal-foundation.json`
(`17ae8f32…8ce8e0`), raw units in `<external>/receipts/raw-probe.json`
(`84b7593a…9188e27`).

## 1. Protocol (pre-registered)

- Frozen dev set: `benchmarks/rag-impact/cases/dev.json`, first **12 cases**,
  **25 gold spans**, hash `27626109…a4a9c311`.
- Frozen corpus: `benchmarks/rag-impact/corpus-manifest.json`, 822 files /
  5,400,637 bytes; indexed 818 files; index v3; hash
  `c706c8dd…6359cdb`.
- Held-out `H01–H08` remained sealed and was not executed.
- Same six-call policy as B0: `fts_question`, `fts_first_phrase`,
  `rg_last_phrase`, `fts_code_tokens`, `rg_code_tokens`, plus
  `hybrid_question`; `vector_question` was added as an explicit parity route.
- Each engine call returned **top-50**; **Recall@10** is computed on the first
  10 units. `rg` calls are line hits and are also capped at 10 for the top-10
  metric.
- Primary metric: **span recall**, macro-averaged across the 12 cases.
- Secondary: **strict evidence** (unit covers ≥50% of gold, unit length
  ≤ max(100, 3×gold length)), union/selectable ceiling, lines and characters
  per pack.
- Routes use the effective execution plan: one query group, explicit route,
  `query=None`, `auto_update=false`; no implicit path fallback.
- Binary: experiment `0.3.0-rc.2`, SHA `f6e0a22a…85a8d1`. Index and corpus are
  external; no production index was touched.

## 2. Identity and validity matrix

| Dimension | Required property | Observed |
|---|---|---|
| Gold set | frozen, no tuning | 12 dev cases / 25 spans; hash matches |
| Corpus | immutable, hash-verified | 822 manifest files; tree hash matches |
| Index | v3, complete | 818 files, 15,039 entities, 0 failed/pending |
| Model | Potion 256d, cosine | `potion-code-16m-v2`, active index metadata |
| Binary | experiment only | sha `f6e0a22a…`; installed `0.0.1` untouched |
| Route contract | requested = effective | 84/84 route rows matched |
| Scoring unit | top-10 evidence units | line spans; container ranges allowed |
| Denominator | all 25 spans | 12-case macro |
| Held-out | sealed | not executed |
| Production | unmodified | no binary/index/daemon/config change |

The measurement defect found in the historical reports is corrected here:
“9/25 retrievable” was a **union** result, while “0.4722” was a per-case
best-call Recall@10. They are not the same quantity and are now reported
separately.

## 3. Results

### 3.1 Routes

| Route | Macro Recall@10 |
|---|---:|
| `fts_question` | 0.3750 |
| `vector_question` | 0.2639 |
| `hybrid_question` | 0.4167 |

### 3.2 Policy

| Metric | Current (v3) | Historical B0 (v2) |
|---|---:|---:|
| Best-of-six Recall@10 (oracle, uses gold) | **0.5972** | 0.4722 |
| Fixed `fts_code_tokens` Recall@10 (deployable) | **0.3333** | 0.3750 |
| Strict recall of the best-recall call | **0.1250** | not reported |
| Max strict recall over calls | 0.2917 | not reported |
| Union, span total | **19/25** | 9/25 |
| Union, case-macro | 0.8472 | not reported |
| Selectable@10, span total (containers) | 19/25 (0.76) | 9/25 |
| Top-10 lines per call (mean) | **1,875** | not measured |
| Top-10 characters per call (mean) | **7,177** | pack 3,716 tokens/case |
| Cold index wall | 14.57 s | 6.31 s median |

The union/selectable numbers are **container-inflated**: a single class range
can cover several gold spans. Splitting returned containers into their indexed
declarations gives:

| Granularity | Candidate union | Selectable@10 |
|---|---:|---:|
| Raw returned units | 18/25 | 18/25 |
| Containers split into declarations | **18/25** | **18/25** |
| Raw units ≤200 lines only | 8/25 | not computed |
| Raw units ≤100 lines only | 7/25 | not computed |

The precise candidate pool is genuine: methods/properties alone cover 18/25.
It is **not** a container artifact. But it still misses the 80% (20/25) gate.

### 3.3 Per-case failure attribution

`S` = selection/ranking, `G` = container granularity, `D` = discovery
(cross-file/SQL), `R` = same-file range miss.

| Case | Best Recall@10 | Max strict | Union | Precise union | Attribution |
|---|---:|---:|---:|---:|---|
| D01 | 0.000 | 0.000 | 2/2 | 2/2 | S/G |
| D02 | 0.167 | 0.000 | 3/6 | 3/6 | D/R (DbContext, Employee) |
| D03 | 1.000 | 0.000 | 1/1 | 1/1 | G |
| D04 | 1.000 | 1.000 | 1/1 | 1/1 | G (precise via exact symbol) |
| D05 | 0.000 | 0.000 | 1/1 | 1/1 | S |
| D06 | 0.667 | 0.000 | 3/3 | 3/3 | S/G |
| D07 | 0.333 | 0.000 | 2/3 | 1/3 | S/D/R (SQL, method) |
| D08 | 1.000 | 0.500 | 2/2 | 2/2 | partial (one precise) |
| D09 | 1.000 | 1.000 | 1/1 | 1/1 | success |
| D10 | 0.500 | 0.000 | 1/2 | 1/2 | D/G (CoreDBContext) |
| D11 | 1.000 | 1.000 | 1/1 | 1/1 | success |
| D12 | 0.500 | 0.000 | 1/2 | 1/2 | D (SQL procedure) |

Remaining **7 precise spans** are cross-file/SQL:
D02 `CoreDBContext.cs` 17–20 and 41–55, D02 `Employee.cs` 1–65,
D07 `OrganizationCommand.cs` 1057–1074, D07 SQL 58–95,
D10 `CoreDBContext.cs` 9–20, D12 SQL 1–95.

## 4. Historical decision ledger

Hashes were re-checked against the receipts; statuses are historical evidence,
not current authorization.

| Artifact | Historical verdict | Reuse on E8 Foundation |
|---|---|---|
| `b0-official.json` `e57c11e2…` | `B0_FROZEN`; 0.4722 / 0.3750 / 9-of-25 | Valid v2 baseline only; not comparable to v3 |
| `discovery-h2-qualification.json` | 19/25 pool, 15,683 ranges, 5.3 s p95, 736.8 MB rchar; below 80% gate | Candidate idea reusable; resource path NO-GO |
| `discovery-selection-probe.json` | pool ceiling 14/25, best deployable 6/25; NO-GO | Pool changed under v3; **must requalify** |
| `discovery-h5-ordering.json` | 19/25 ceiling, high cost; NO-GO | Not promoted |
| `discovery-h6-reference.json` | 20/25 but resource/generalization NO-GO | No production reference graph |
| `discovery-h7-e1.json` | coverage/independent PASS, resource FAIL; E1 NO-GO | E1 feature is not in production |
| `h7-e1r-compact.json` | 23/25 but 67.8/72.3 MiB > 64 MiB; NO-GO | Compact index idea only |
| `candidate-pilot.json` | `CANDIDATE_QUALITY_PILOT_FAIL` | Not a baseline |
| `r4-failure-attribution.json` | R4-A/B NO-GO; D1/D2 policy runs failed | Failure map reusable |
| `r1-v2-dev-pilot.json` | R1 v2 correctness pilot | R1 contract re-tested here |

Current R1 checks on merged HEAD:

- `cargo test -p zg-engine --lib api::evidence`: **10 passed**.
- `cargo test -p zg-engine --test r1_public_api`: **2 passed**.
- No Performance/durability/daemon-parity claim is made from these tests.

## 5. Verdict

**Discovery: NO-GO (not qualified).**

Reasons, in order:

1. **Candidate coverage 18/25 (72%)** is below the 80% gate. It is a large
   improvement over B0 (9/25) and close to H2’s 19/25, but does not qualify.
2. **Deployable top-10 is worse than the candidate pool**: fixed FTS 0.333,
   hybrid 0.417, best-of-six oracle 0.597. Selection/ranking, not just missing
   candidates, is the dominant loss.
3. **Evidence granularity is poor**: best-recall-call strict recall is 0.125;
   top-10 packs average 1,875 lines. Returned units are often whole classes.
4. **The remaining 7 spans are cross-file/SQL**, not recoverable by the current
   index-only search channels without references, SQL binding or an agent loop.
5. Selection and Expansion remain frozen; no implementation was opened.

The correct next unit is **Selection qualification on the v3 candidate pool**,
not another Discovery search tweak. That requires owner authorization because
Selection was previously declared NO-GO and remains frozen.
