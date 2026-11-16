# R4 autonomous investigation — Gate 1–3 results

Status: **NO-GO for MVP**, with a measured +15 pp gain over the *fair* baseline.
Branch `work/r4-autonomous-20261116` (from `48130d9`). Frozen R0 target
(Recall@10 ≥ 0.5722) unchanged; dev split only; no held-out, no production change.

## 1. Gate 1 — the previous NO-GO was partly a metric artifact

The R4b verdict compared two different objects:

| Arm | Byte bound | Verified | Macro Recall@10 |
|---|---|---|---:|
| `hybrid` raw top-10 (what R4b was compared to) | none | no | 0.4167 |
| `hybrid` top-10 under the **same 24 KiB pack budget** | 24 KiB | no | **0.1667** (2/12; CLI-measured snippet spans) |
| R4b verified pack (before this mission) | 24 KiB | yes | 0.2778 |

`benchmarks/rag-impact/REPORT-CONTRACT.md:38` fixes "final source pack ≤24 KiB",
so the budget is part of the contract; the *baseline* was simply never subjected
to it. Measured hybrid spans are enormous (D01: 9 spans totalling 178 KB; single
spans of 30–56 KB), so under 24 KiB the deployable route keeps 2–7 spans and its
recall collapses to 0.12–0.17. That is the fair comparison, and by it R4b was
already ahead.

The harness now carries both baselines: `hybrid_baseline` (raw, unbounded) and
`hybrid_byte_bounded` (same budget, no verification). The snippet-sizing CLI
measurement is reproducible with the command in §6 and yields 0.1667.

## 2. Gate 2 — three experiments

**E1 — compact verified evidence (hypothesis: the pack wastes bytes on whole
declarations; trimming keeps recall because gold overlap is preserved).**
Implemented `compact_range`: declarations larger than 2.5 KiB are trimmed to the
declaration header (when it still fits) plus a window of 6 lines before / 14 after
the matched region; the range always stays inside the declaration and contains the
match, so overlap semantics are unchanged and the hash still verifies the exact
bytes sent. Result: **0.2778 → 0.3194** (selected 5 → 6/25), bytes per span down
from 30–56 KB to ≈2.5 KiB. Accepted.

**E2 — breadth-first selection (hypothesis: gold is spread across files, so one
span per subject first converts pool coverage better).** Implemented a two-pass
selector (distinct subjects, then depth) with the per-file cap and fair-share byte
budget unchanged. Result: **0.3194 → 0.3194** (no change; the pack was already
one span per file). Hypothesis falsified, code kept because it is harmless and
documents the intent.

**E3 — hybrid backbone with supplementary fill + near-identical dedup (hypothesis:
reserving most slots for the hybrid route reproduces its recall while verification
and typed evidence fill the rest).** Implemented `hybrid_quota = 10`,
`fts_quota = 0`, and replaced blanket overlap-merge with a near-identical (≥75 %
IoU) duplicate rule so two different matches inside one declaration are not
silently collapsed. Result: **0.2361** (worse). Hybrid units are line-level
candidates whose provenance is frequently superseded by the scoped-lexical/FTS
entries for the same region, so reserving "hybrid slots" does not actually
reserve the hybrid ranking. Hypothesis falsified; the dedup fix was kept (it
prevents dropping distinct matched regions).

Experiment budget (3) is now spent.

## 3. Gate 3 — paired results (frozen corpus, same index, single run/arm)

| Arm | Macro Recall@10 | Selected (spans / 25) | Verified | Notes |
|---|---:|---:|---:|---|
| hybrid raw top-10 | 0.4167 | 8 | no | no byte bound |
| hybrid under the same 24 KiB | 0.1667 | 2 | no | fair deployable baseline |
| R4b before this mission | 0.2778 | 5 | yes | |
| **R4b + E1 + E2 + E3 dedup** | **0.3194** | 6 | yes | best of this mission |
| target | 0.5722 GO / 0.5000 PARTIAL | | | |

Candidate pool 12/25; citation accuracy 1.000; all selected spans hash-verified.
Per case (best arm): D07 1/3, D08 2/2, D09 1/1, D10 1/2, D11 1/1; all other cases 0.

Cost of the best arm versus one hybrid call: 8–14 tool calls, 0.2–3 s per case,
≈2.5 KiB per selected span, peak PSS recorded in the receipt header.

## 4. Verdict

**NO-GO.** 0.3194 < 0.5722 (GO) and < 0.5000 (PARTIAL); it is also below raw
hybrid 0.4167, so the workflow does not yet add retrieval value over the
unbounded route. It *does* beat the fair deployable baseline (0.1667) by
+15.3 pp, which is the first defensible statement of R4's value so far: the
verified, compacted pack delivers at least as much relevant evidence as the
snippet route can deliver inside the same byte contract.

No claim is made about generalization: D01–D12 is a heavily reused dev split.

## 5. Remaining bottleneck and the single next step

Measured, not inferred:

1. **Pool ceiling, not selection.** 12/25 gold spans ever reach the pool; the pack
   converts 6 of 12. D02 (6 gold) and D12 (2 gold) have zero pooled gold, D01/D05/
   D06 gold lives in files whose pooled units never overlap the gold range.
2. **Provenance supersession.** A hybrid hit is pooled, but the same region later
   arrives from scoped lexical search with a higher score; reserved-slot accounting
   is therefore route-*label* based while the ranking is region based. This is the
   concrete defect behind E3's failure and the reason "hybrid backbone" could not be
   reproduced inside the workflow.
3. **Compaction is at 2.5 KiB/span**; at 10 spans that is 25 KiB — still byte-bound,
   and further trimming trades agent readability for count.

Next single step (proposal, not started): make reservation and scoring
**region-based** — a canonical region key (file + enclosing declaration) shared by
every route, with the best route rank retained per region — and measure whether the
pack then reproduces the hybrid list exactly (expected 0.4167) before attempting any
further discovery work. If parity is reached, the next lever is pool coverage for
the zero-pool cases (D02, D12), which no selection change can fix.

## 6. Reproduce

```bash
W=/home/masis/.cache/zvec-grep/r4-discovery-20261115
cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
cargo test -p zg-engine --lib -- rag::workflow --test-threads=1
```

Fair-baseline snippet sizing (CLI, parse `File:`/`Range:` from
`--preview detail`, greedy fit to 24 KiB) — script retained in the session log;
receipt `benchmarks/rag-impact/results/r4b-paired-20261116.json`.
