# RAG-S1b baseline and ceiling reconciliation — 2026-11-15

Status: **FIXED-POOL SIGNAL EXPERIMENT.** Production Selection/Expansion and
Discovery remain frozen. No index, model, daemon, binary or production data was
changed.

Worktree: `work/rag-s1b-20261115` from `work/rag-s1-20261115` @ `1d8f57a3`
(base `f93d0499`). External root:
`/home/masis/.cache/zvec-grep/rag-s1b-20261115/`.

## 1. Inputs

| Asset | Hash / value |
|---|---|
| Dev D01–D12 | `27626109…a4a9c311`; 25 spans |
| Held-out H01–H08 | `2d81048e…e38f6d`; 20 spans |
| Corpus manifest | `c706c8dd…6359cdb`; 822 files / 5,400,637 B |
| Index manifest | `d7052d3f…473fc4a`; v3, 818 files, 0 failed/pending |
| Dev probe | `84b7593a…9188e27` |
| Held-out probe | `74a83698…255c4c51` (5 calls × 50; no rg) |
| Requal receipt | `17ae8f32…8ce8e0` |
| Tokenizer | `107bbd…f3d45` |
| S1b dev receipt | `c1dcd9b6…c4f5f1` |
| S1b held-out receipt | `a54ec030…583c6a` |

## 2. Ceiling reconciliation (do not conflate)

| Quantity | Value | Meaning |
|---|---:|---|
| Requal precise candidate union | **18/25** | engine-derived declarations/units overlapping gold, no gold-aware filtering |
| S1 Selection pool ceiling | **16/25** | S1 structural pool after excluding 1-line values, rg-only guesses and non-locatable spans |
| S1 oracle with 3,716-token budget | **15/25** | evaluation-only greedy selection under the real token budget |
| S1 runtime selected | **6/25** | exact/structural prototypes |
| S1b runtime selected (dev) | **10/25** | signal prototype |
| S1b candidate ceiling | **16/25** | same fixed pool; relationships only re-rank existing ranges |

The 18/25 figure is not Recall@10 and is not a runtime selector. The 16/25
ceiling is the maximum the S1 pool can cover with ≤10 units; the budgeted
oracle is 15/25. S1b does not add candidates outside the frozen pools, so its
ceiling remains 16/25.

## 3. Measurement contract

- **Recall@10**: first 10 selected units, span overlap, macro over cases.
- **Strict**: one unit covers ≥50% of gold and is ≤ `max(100, 3 × gold length)`.
- **Selected spans**: total overlapping gold spans, not macro.
- **Tokens**: frozen tokenizer count of packed path headers + source; truncation
  disabled; per-case budget 3,716.
- **Ceiling**: exact gold-mask DP over the frozen pool, evaluation-only.
- **Gold leakage**: policy must produce identical units after all gold/label
  fields are removed.

## 4. Baseline comparison

| Policy | Dev spans | Recall@10 | Strict | Mean tokens | Max tokens |
|---|---:|---:|---:|---:|---:|
| S1 fixed FTS | 5/25 | 0.3333 | 0.2917 | 8,048.8 | 28,426 |
| S1 exact | 6/25 | 0.3611 | 0.3194 | 766.3 | 3,389 |
| S1 structural | 6/25 | 0.3611 | 0.3194 | 2,691.0 | 3,674 |
| S1 oracle | 15/25 | 0.7361 | 0.4722 | 1,472.8 | 3,501 |
| **S1b signal** | **10/25** | **0.5694** | **0.4861** | **3,151.7** | **3,711** |

S1b improves dev spans by +4 (6→10), strict by +3 (5→8) and Recall@10 by
+0.208, at +460 tokens and +20 files/case over S1 structural. It still fails
the Recall@10 0.60 gate and the 16/25 selected-span gate.

Held-out, frozen before the run:

| Policy | Held-out spans | Recall@10 | Strict | Mean tokens | Max tokens |
|---|---:|---:|---:|---:|---:|
| S1 structural | 9/20 | 0.6250 | 0.5000 | 2,954.1 | 3,675 |
| **S1b signal** | **9/20** | **0.5000** | **0.2500** | **3,228.4** | **3,588** |

S1b gains H06 (4 spans) but loses H02 and H08 (4 spans); strict falls from 7 to
3. It does not generalize.

## 5. Signal inventory and provenance

All signals are derived from the frozen pool's source ranges:

| Signal | Provenance | Use |
|---|---|---|
| Direct call identifiers | regex over candidate method body; target must be a frozen method candidate | one-hop boost |
| SQL strings | `execute/exec sp_*` in candidate body | map to frozen SQL candidate |
| Identifier aliases | capitalized identifiers in candidate body matching query terms | candidate boost |
| Class header/property group | within a frozen class range, before first method | D10-style class/context evidence |
| Member/block/prefix ranges | within a frozen class/method range | fine-grained evidence |
| Engine rank/lexical scores | frozen probe | prior only |

Only direct edges whose target is already a frozen candidate are followed.
Unresolved calls/SQL names are **not** promoted to evidence. The prototype
does not scan outside candidate files: 249 files / 2.995 MB dev, 301 files /
3.498 MB held-out.

## 6. Resource and integrity

- Dev selection wall: **45.5 s** total (≈3.8 s/case), user CPU 45.1 s, peak RSS
  **112,636 KiB**.
- Source reads include bytes already resident from the probe; no unrestricted
  traversal.
- Determinism: dev rerun hash identical (`c1dcd9b6…`).
- Gold leakage: sanitized dev units identical to the normal run.
- Token budget: dev max 3,711; held-out max 3,588; zero over-budget packs.
- Independent verifier and negative controls: PASS.
