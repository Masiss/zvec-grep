# R4b — hybrid discovery + evidence selection (results)

Status: **NO-GO.** Neither preregistered target was met, and both implementation
iterations are used. Branch `work/r4b-hybrid-20261116` (from `587123d`).

## 1. What was implemented

**P1 — selection (preregistered target: 9 → ≥8 selected on the old pool)**
- Reserved-slot ordering: hybrid slots first, then the FTS fallback, then derived
  candidates (`hybrid_quota`, `fts_quota`).
- Conditional per-file cap: beyond `max_spans_per_file`, a further span is admitted
  only when it covers a **distinct declaration**, bounded by
  `max_spans_per_file_extra`.
- Fair-share byte budget per evidence subject (file): `max(max_bytes/3, 8 KiB)`,
  so one large file cannot consume the pack (`budget_subject_bytes` gap).
- Route rank is now carried on every pooled candidate
  (`PoolEntry::route_rank`) and dominates scoring: hybrid `10000-10·rank`,
  FTS `5000-5·rank`, derived evidence capped at 200 for tie-breaking.

**P2 — hybrid question route as discovery base**
- New `EvidenceSource::hybrid`, `CandidateSource::Hybrid`, `hybrid_route` and
  `WorkflowStats::hybrid_calls`; discovery now runs hybrid first with the
  **original question text**, then the FTS fallback, then derived lexical
  (all switchable through `use_hybrid/use_fts/use_lexical/use_facts`).
- Typed R2/R3 facts, exact symbols and SQL bindings stay as *supplementary*
  evidence; provenance, dedup, bounds, correctness and R1 hash verification
  unchanged.

**P3 — external-agent plan protocol: NOT_MEASURED.** No blind plan exists for
D01–D12. A plan written now would follow gold inspection, and the E1–E5 plan is on
a different split and must not be extrapolated (explicitly out of bounds). The
protocol is therefore implemented in shape (plan file + hash + `AgentPlan{role}`
provenance slot) but no quality claim is made.

21 workflow tests pass, including four new regressions shaped by the audit cases:
distinct-declaration extension beyond the per-file cap, fair-share byte isolation
for a large subject, hybrid provenance reaching the pack, and bounded abstention
on an empty pool.

## 2. Paired measurement

Same frozen 822-file corpus copy, same index, same budget, single run per arm.

| Arm | Recall@10 (macro) | Selected | Verified | Notes |
|---|---:|---:|---:|---|
| `hybrid_baseline` (one hybrid question route, 10 results) | **0.4167** | 10/case | 0 | no byte bound |
| `hybrid_p1` (same candidates, P1 pack) | 0.1944 → 0.2778 | ~8/case | all | 24 KiB verified pack |
| `r4b` (P1 + P2 + FTS + lexical + facts) | **0.2778** | 5/25 pool | all | pool 12/25 |
| target (frozen) | 0.5722 GO / 0.5000 PARTIAL | | | |

Per-case (selected gold spans, R4b / P1 / hybrid baseline):
D01 0/0/0, D02 0/0/0, D03 0/0/1, D04 0/0/1, D05 0/0/0, D06 0/0/2, D07 1/1/1,
D08 2/2/2, D09 1/0/0, D10 0/0/0, D11 1/1/1, D12 0/0/0.

Candidate pool coverage rose 9/25 → **12/25** (P2 worked as predicted in the
discovery sense), but final selection stayed at **5/25** and citation accuracy
remained 1.000. The improvement did **not** transfer to final evidence quality.

Iteration 2 (route-rank preservation) changed the selection ordering and took
`hybrid_p1` from 0.1944 to 0.2778, still far below the baseline.

**P1 on the legacy pool (isolating the selection change, cases D01/D03/D04/D10):**
the four previously lost gold spans were selected **0/4** — the preregistered
9 → ≥8 target **failed**; the pack dropped them for the same structural reasons.

## 3. Attribution

| Mechanism | Effect on final Recall@10 |
|---|---|
| Hybrid as discovery base (P2) | +3 pooled gold spans (9→12) — real, measured |
| Reserved hybrid/FTS slots (P1) | Prevents displacement, but the reserved items are the same ones the baseline already returns |
| Fair-share byte budget (P1) | Stops a single large file from eating the pack, yet total pack bytes remain the binding constraint |
| Conditional per-file cap (P1) | Admits a second declaration in a file, but that span then competes for the same scarce bytes |
| Route-rank preservation (iteration 2) | Recovers ordering among reserved items; no net gain |
| Typed facts / symbols (P2 supplementary) | No measurable gain on this split |

**Root cause of the failure (new bottleneck, measured):** the comparison is
byte-asymmetric. The hybrid baseline returns 10 unverified spans with no byte
bound; the R4b pack must fit 10 **hash-verified** spans into 24 KiB. Method-level
declarations in this corpus are 1–9 KiB each, so verification alone caps the pack
at ~4–6 spans (measured: 4–10 items/case, D04 = 5, D06 = 4). Pool growth cannot
survive that ceiling.

Consequently: the 12/25 pool contains roughly half again as much valid evidence,
but the pack can only carry what the 24 KiB budget allows, and the spans it
carries are not more relevant than the ones the baseline already ranks first.

## 4. Verdict and stop conditions

- Target P1 (9 → ≥8): **FAILED** (0/4 recovered on the audit pool).
- Target P2 (pool ≥14/25): **FAILED** (12/25) though directionally positive.
- Primary acceptance: 0.2778 < baseline 0.4167 → **NO-GO**.
- Both preregistered iterations used → **stop**. No further micro-tuning.

PARTIAL/GO were never reachable: recall went *down*, not up. No evidence was
claimed as verified that was not; citation accuracy 1.000 with all selected spans
hash-checked.

Resource note: R4b needs 12–24 tool calls and 0.1–8 s per case versus one hybrid
call and ~0.3 s, with no quality gain — not viable as a default. Exact per-arm
numbers are in `benchmarks/rag-impact/results/r4b-paired-20261116.json` and
`r4b-p1-legacy-pool-20261116.json`; no tokenizer count was produced
(content-bytes/4 estimate only). Single run per arm; PSS/RSS/load are recorded in
the receipt header.

## 5. What would have to change (owner-level decision, not implemented)

1. **Compare like with like:** either an unverified-snippet baseline arm under the
   same byte bound, or a verified arm with a byte budget sized for 10
   declaration-level spans (≈48–64 KiB). Without that, "verified recall" and
   "snippet recall" are not the same metric, and every R4 variant will look worse
   than the baseline for reasons unrelated to ranking quality.
2. **Expose a relevance score** from route results (hybrid/FTS rank is the only
   signal the backend has) so the selector can allocate bytes *by score*, not by
   arrival order.
3. **Selection must be able to say "this subject deserves 3 spans"** — the current
   fair-share and per-file rules are content-blind.

Until (1) is decided, further R4 discovery work is unmeasurable. No alias layer,
no model change, no production change was made.

## 6. Reproduce

```bash
W=/home/masis/.cache/zvec-grep/r4-discovery-20261115
cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
R4B_CASES=D01,D03,D04,D10 R4B_NO_HYBRID=1 cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
```
