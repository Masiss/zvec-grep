# Correct Fragment Ranking & Selection — final report

Status: `DONE` for the four contract fixes and verification; `NO-GO` for
promotion because no candidate passes the both-language quality gate.

Branch: `work/recover-rag-evidence-20261126` pushed through `e088b10`.
Start point `6eef417`. Artifacts:
`/home/masis/.cache/zvec-grep/correct-flow-20261126`.

## 1. Contract fixes

### Fusion

`fuse_fragment_candidates()` in `pipelines/indexed_search/pipeline.rs` now uses
`InternalEvidence.rank` and `InternalEvidence.hit.score` for every fragment and
route. Entity-level `Candidate.recall` is used only to recover the route query
string. Each fragment carries a `SearchRecallTrace` with its own rank/score and
`FragmentSignals { fts, vector }`.

Unit test: `fragment_fusion_keeps_fragment_route_rank_and_score` asserts that
an entity trace with rank 99/score 999 does not leak into a fragment with rank
3/score 12.5.

### Ranking

`SearchHit.signals` carries runtime FTS/vector scores. `route_score()` in
`pipelines/indexed_search/ranking/mod.rs` reads those signals, not optional
`SearchHitTrace`. Unit test `fragment_ranking_ignores_observability_trace`
asserts identical scores and ranks with trace on/off.

### Selection

`context.rs` selection now:

- identifies source by `(relative_path, file_sha256)`;
- computes overlap on `content_range`, never `range` (entity range);
- clips only overlapping source lines via `clip_fragment_overlap()`, keeping
  the non-overlapping remainder of a fragment;
- skips a fragment only when it is entirely duplicated.

Unit tests cover partial clipping, different file hashes, and budget defaults.

### Budget

`fragment_budgets()` uses byte budget 24 KiB as primary and item count 64 as a
resource safeguard. Stage traces now carry `fragment_id`, `entity_range`,
`fragment_range`, `content_range` and `file_sha256`; the benchmark analyzer
computes oracle from the ranked fragment pool using fragment ranges.

## 2. Pre-benchmark verification

- Focused engine tests: **54 passed, 0 failed, 1 ignored**.
- Lineage audit (`tools/audit_fragment_lineage.py`) on real CLI stage traces:
  - Rust R01: 400 raw route fragments, 400/400 fragment rank/score lineage
    checks passed.
  - Rust R02: 400/400 checks passed.
  - C# D01: 800/800 checks passed.
  - C# D02: 400/400 checks passed.
  - Every ranked row has fragment id/ranges; every selected row has fragment
    id, content range and file hash.

Final candidate binary SHA-256 `cbf7a004…`; K0 `5a4c8201…`; K1 `6d05ed17…`.

## 3. Qualification at 24 KiB / item safeguard 64

| Arm | Description |
|---|---|
| K0 | baseline entity flow |
| K1 | previous fragment flow |
| K2 | corrected mixed relevance + utility selection |
| K3 | corrected RRF-dominant relevance + utility selection |
| K4 | corrected RRF-dominant + rank-order selection |
| K5 | corrected mixed relevance + rank-order selection |

| Arm | Rust exact | Rust precision | Rust tokens | C# exact | C# precision | C# tokens |
|---|---:|---:|---:|---:|---:|---:|
| K0 | **0.250** | 0.049 | 4,213 | 0.083 | 0.007 | 4,624 |
| K1 | 0.153 | 0.042 | 1,302 | **0.167** | **0.030** | **946** |
| K2 | 0.042 | 0.005 | 3,555 | 0.083 | 0.008 | 2,884 |
| K3 | 0.042 | 0.006 | 4,087 | 0.083 | 0.008 | 3,113 |
| K4 | 0.125 | 0.011 | 4,143 | 0.083 | 0.009 | 3,021 |
| K5 | 0.042 | 0.006 | 3,605 | 0.083 | 0.009 | 2,673 |

Runtime p95:

| Arm | Rust | C# |
|---|---:|---:|
| K0 | 1.79 s | 2.34 s |
| K1 | 2.13 s | 3.01 s |
| K2 | 3.45 s | 9.47 s |
| K3 | 3.30 s | 7.90 s |
| K4 | 2.50 s | 7.77 s |
| K5 | 2.77 s | 7.18 s |

Gate: beat K0 and K1 exact coverage on both languages, no precision
regression, <=25% token growth vs K1, <=2x p95 vs K1.

- K4 is the best corrected arm, but Rust exact `0.125` is still below K1
  `0.153` and K0 `0.250`; C# exact `0.083` is below K1 `0.167`.
- Precision is below K1 on both languages.
- Latency is materially higher, especially C# (`~7–9 s` vs `3.0 s`).

**No arm passes the gate. NO-GO for promotion.**

## 4. Task correctness

| Split | K0 | K1 | K4 |
|---|---:|---:|---:|
| Rust correct /12 | **3** | 0 | 2 |
| C# correct /12 | 0 | 0 | 0 |
| Rust total tokens | 50,815 | 20,621 | 50,251 |
| C# total tokens | 66,670 | 18,467 | 44,646 |

K4 improves over K1 on Rust answers but not over baseline, at a much higher
context cost. C# grader remains weak/high-abstention.

## 5. Regression

- Normal-search parity: **6/6 sampled Rust/C# cases byte-identical** between K0
  baseline and final corrected binary with all RAG env switches unset.
- Focused engine suite after final change: **54 passed, 0 failed, 1 ignored**.
- No production binary, daemon, index, config, model or held-out changed.

## 6. Failure taxonomy and limits

- **Contract failures fixed**: entity-level fusion ranks, trace-dependent route
  scores, entity-range overlap, hard 10-item cutoff.
- **Remaining quality failure**: corrected fragment relevance still ranks the
  gold fragment too low in many cases; the oracle in ranked pools is much
  higher than delivered coverage.
- **Clipping side effect**: byte-primary budget with many small clipped pieces
  increases item count and noise; rank-order selection (K4) is better than
  utility selection but still not competitive with K1.
- **Runtime regression**: wider ranked pools plus clipping/selection add CPU and
  latency.
- **Measurement limits**: one run per arm, 12 dev cases per language, heuristic
  oracle parsing, weak C# grader.

## 7. Decision

The four requested implementation defects are fixed and independently
verified by unit tests plus real trace lineage checks. The corrected flow does
not improve end-to-end evidence quality over the previous fragment candidate;
do not promote. The limiting stage is now fragment relevance and source-range
selection under budget, not lineage preservation or entity-range bookkeeping.
