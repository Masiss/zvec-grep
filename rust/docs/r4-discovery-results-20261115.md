# R4 discovery implementation — M1–M4 results

Status: **NO-GO for the R4 discovery arm. Stop condition reached.**
Branch `work/r4-workflow-20261115`. Design input:
`r4-discovery-attribution-20261115.md`, `r4-discovery-contract-20261115.md`.

## 1. What was implemented

**M1 — discovery correctness**
- File filtering moved into the backend query (`globs`, so `*.cs`, `*.sql`, …):
  the result window now contains admitted files instead of generated dumps.
- Declaration-aware widening: the enclosing method/type comes from the R2 C#
  fact parser and the R3 SQL definition parser (bounded to ≤200 lines), with the
  line-window widening as fallback.
- File/size admission, file cap, per-file span cap and byte/item caps all report
  typed boundary gaps (`budget_*`).

**M2 — query-aware discovery**
- New backend routes with defaults that fail closed: `search_filtered`
  (backend-filtered lexical), `symbol` (exact declaration lookup, index v3) and
  `fts` (indexed baseline route).
- `Fts` seed route and exact `Symbol` route; typed fact expansion
  (`DbSet`, invocation, type, `SaveChanges`, SQL definition/table) stays as
  `CandidateSource::FactExpansion` with the fact name attached; lexical fallback
  is labelled `LexicalFallback`.
- Every pooled candidate carries `sources` and `facts`; selected spans carry
  `sources`, `facts` and an `EvidenceRole`
  (`SeedHit | Declaration | Reference | ProcDefinition | TableAccess`).
- Overlapping ranges reached by several routes merge provenance instead of
  duplicating the span. Unresolved/dynamic facts stay candidates.

**M3 — selection and CHECK**
- Reserved FTS slots (`fts_quota = 3`): expansion cannot displace indexed FTS
  evidence.
- Multi-route corroboration bonus in ranking; size penalty retained.
- R1 hash-verified pack, typed gaps, abstention and one corrective round
  unchanged; cancellation, injection isolation and bounds unchanged.

17 workflow tests pass, including new ones for FTS protection, symbol
provenance, plan stability under hostile source content, and backend filtering.

## 2. Measurement (M4)

Isolated corpus copy of the frozen 822-file manifest (all 822 hashes verified;
one live working-tree file had drifted by 1 byte and was restored from the frozen
git commit), indexed once in an external directory (51 s wall, 775 MB peak RSS,
index v3, Potion Code 16M v2, CPU). No production index, daemon or binary was
touched.

Both arms: same corpus, same index, frozen D01–D12, same budget.

| Arm | Recall@10 (macro) | Selected | Verified |
|---|---:|---:|---:|
| `fixed_fts` (paired baseline, measured now) | **0.3333** | 71 | n/a |
| `workflow` | **0.2778** | 120 | 120 |

Δ = **−5.56 pp**. Candidate pool coverage 9/25; final selected coverage 5/25;
citation accuracy 1.0. Resource: FTS 3.5 s total, workflow 126.9 s, 285 tool
calls (63 symbol), 15.1 MB source reads, 232 KB selected content, peak PSS
357 MB. Host load 8.5 → 7.8 during the run (single run per arm).

The measured FTS baseline is **0.3333**, not the historical 0.3750; the receipt
reports the measured value as instructed.

## 3. Per-case results

| case | gold | FTS | pool | selected |
|---|---:|---|---:|---:|
| D01 | 2 | 0 | 1 | 0 |
| D02 | 6 | 0 | 0 | 0 |
| D03 | 1 | 0 | 1 | 0 |
| D04 | 1 | 1 | 1 | 0 |
| D05 | 1 | 0 | 0 | 0 |
| D06 | 3 | 0 | 0 | 0 |
| D07 | 3 | 0 | 1 | 1 |
| D08 | 2 | 2 | 2 | 2 |
| D09 | 1 | 1 | 1 | 1 |
| D10 | 2 | 0 | 1 | 0 |
| D11 | 1 | 1 | 1 | 1 |
| D12 | 2 | 0 | 0 | 0 |

Pool 9 → selected 5: four pooled gold spans were not selected (D01, D03, D04,
D10). D04's pool span was lost to a `missing` gap during pack assembly (stale
read/hash mismatch), the others to ranking plus the 10-item/24 KiB caps.

## 4. Attribution of the improvement and the regression

| Mechanism | Measured effect |
|---|---|
| Backend filtering (M1) | Removed the generated-dump starvation defect: no gold file is filtered out, and the pool now contains admitted source only. Pool coverage rose 7/25 → 9/25. |
| Declaration widening (M1) | Selected spans are method/type ranges instead of single lines, but this did not add gold coverage in this measurement. |
| Exact symbol route (M2) | 63 lookups, 0 gold spans gained: exact names for question words (`Form`, `Personal`, `Proc`, `Thiếu`) resolve to unrelated declarations, and fact-derived names stay inside the already-wrong seed neighbourhood. |
| Typed acquisition (M2) | No gold coverage gained; expansion amplifies seed quality. |
| Corrective CHECK (M3) | Held behaviour (`missing_code_tokens` abstention, one round), no coverage gain. |
| FTS protection (M3) | FTS-sourced spans survived expansion, but they are the same spans FTS already returns, so no added coverage. |

Selection loss (pool 9 → selected 5) is the second, independent regression: the
pack cap and ranking prefer code-token-dense, large spans, and one span was
dropped by a stale/hash gap.

## 5. Verdict and stop condition

Frozen acceptance: GO ≥ 0.5722; PARTIAL ≥ 0.4750; NO-GO < 0.4750 or unverified
evidence. Measured 0.2778 with full verification, **below the 0.4750 floor and
below the paired FTS baseline 0.3333**.

Two implementation iterations were used, as pre-registered:

1. fact-only symbol lookups + multi-route corroboration bonus → 0.2778 (no change).
2. bounded declaration ranges (≤200 lines) → 0.2778 (no change).

Per the stop rules, the direction stops here. No further knob tuning.

## 6. Remaining bottleneck (evidence, not speculation)

1. **No lexical handle → no entity resolution.** 14/25 gold spans share no token
   with the question. The implemented symbol route needs a *name*; question words
   are not names, and fact expansion only re-derives names from whatever the
   first seeds found. Without a question→entity resolver (domain alias table,
   embeddings, or an LLM step outside this budget), the pool cannot reach the
   target. This is the same gap the pre-implementation analysis predicted; the
   implementation confirms it empirically rather than by assumption.
2. **Selection is now a measurable second loss** (9 → 5) once the pool improves.
   It must be addressed before any further retrieval work, otherwise gains do
   not reach the answer.
3. Cost: 285 calls and 127 s per 12 cases is 36× the FTS baseline's wall time,
   with no quality gain — not viable as a default.

## 7. Not done / not claimed

No held-out, no GQ5, no R5/R6, no merge, release, install, production index,
daemon, model, ranking or public surface change. No MVP claim.

## 8. Reproduce

```bash
# corpus copy + index (already done in the isolated directory)
cargo run -q -p zg-engine --example r4_discovery_paired -- \
  /home/masis/.cache/zvec-grep/r4-discovery-20261115/corpus
```

Receipt: `benchmarks/rag-impact/results/r4-discovery-paired-20261115.json`.
