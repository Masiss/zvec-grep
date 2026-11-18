# RAG measurement integrity & evaluation lock — results

Branch `work/rag-audit-20261116` (continues from `45648a9`). No retrieval,
ranking or selection algorithm was changed; the R4b recall of 0.3194 at
`eb143a1` reproduces unchanged after the fixes, which is itself the strongest
regression signal. 25 workflow tests pass (4 new invariants).

## Gate 1 — defects fixed

### 1. Candidate trace integrity
- Trace entries are now keyed by the **pooled** unit; the trimmed evidence unit
  is recorded only as `TracedCandidate::selected`. The join defect (audit A1) is
  closed: pool ↔ trace ↔ selected are consistent by construction.
- `CandidateDecision::Unexamined` is a new, honest terminal state for candidates
  ranked but never given a turn (item budget ended, or pooled but not sent to
  selection). **No rejection is backfilled.** `finish()` only fills genuinely
  absent entries with `Unexamined`, never over a real decision.
- New invariant tests:
  `trace_covers_every_pooled_candidate_exactly_once` (pool.len == traced.len, no
  duplicate/missing),
  `every_selected_span_has_a_selected_decision_and_vice_versa` (bijective
  selected↔pack), and
  `unexamined_is_reported_instead_of_a_fake_rejection` (regression for audit A2).

### 2. Snippet-unit baseline
- The `hybrid_byte_bounded` arm now sizes **`ContextItem::content`** bytes
  (the deployable snippet) against the same 24 KiB budget, in the same harness,
  on the same index, instead of entity ranges. The old 0.0000 arm is gone.
- Measured: **0.1667 macro** — matches the independent CLI probe from the audit
  (F1). A harness bug that silently dropped the per-case recall push was found
  and fixed during this work (it reported 0.0 despite covered items); the fixed
  arm is the locked baseline.
- Raw reference (0.4167, unbounded) is reported as a separate arm and labelled
  as retrieval reference only.

### 3. Cumulative tool-content budget
- New `WorkflowStats::tool_content_bytes`: every unit of source content the
  workflow materializes for ranking (which is what an agent would see) plus the
  final pack; contract bound 64 KiB, pack bound 24 KiB inside it.
- Regression test `cumulative_tool_content_stays_within_the_contract_budget`
  on a small fixture, and per-case accounting in the receipt.
- **Finding (not tuned here): the R4b workflow violates the 64 KiB cumulative
  budget on the real corpus** — 0.7–1.36 MB per case (max 1,364,680 bytes,
  total 11.8 MB over 12 cases). The pack itself is 24 KiB, but ranking reads
  hundreds of candidates per case. Any future R4 mission must either bring the
  examined-content volume under the contract or get an explicit owner waiver;
  by the current contract this alone disqualifies the workflow as-is.

## Gate 2 — independent reproduction (frozen corpus/index, single run)

| Metric | Historical | This run | Verdict |
|---|---|---|---|
| Hybrid raw top-10 (macro, reference) | 0.4167 | 0.4167 | reproduced |
| Hybrid snippet-unit 24 KiB (macro, **baseline**) | 0.1667 | 0.1667 | reproduced in-harness |
| R4b best (macro, verified pack) | 0.3194 | 0.3194 | reproduced unchanged |
| Pool / selected gold | 12/25, 6/25 | 12/25, 6/25 | reproduced |
| Citation accuracy | 1.000 | 1.000 | reproduced |
| Trace decisions | (invalid) | selected 107, unexamined 6,414, no fabricated labels | new, consistent |

Per-case rows, host state and cumulative accounting:
`benchmarks/rag-impact/results/r4b-paired-20261116.json`; per-candidate trace:
`r4-evidence-trace-20261116.json`. NOT_MEASURED: per-arm CPU time, task
correctness (no grader exists).

## Gate 3 — evaluation contract (locked)

1. Baseline = hybrid question route, snippet units (`ContextItem::content`),
   greedy fit to 24 KiB, measured in `r4b_paired` on the frozen corpus/index.
2. Arms report separately: raw retrieval reference, byte-bounded baseline,
   verified packed result; each with pool coverage, selected gold, macro
   Recall@10 and span counts side by side.
3. Every pooled candidate carries exactly one terminal decision; invariants
   `pool.len() == traced.len()` and selected↔pack bijection are enforced by
   tests that fail on any recurrence of audit defects A1/A2.
4. Cumulative tool content is reported per case against the 64 KiB contract;
   final pack bytes against 24 KiB.
5. Metrics state unit, stage, denominator, budget, code revision (git head in
   the receipt), corpus manifest hash (in the receipt) and index identity.
6. Non-reproducible numbers are UNVERIFIED and must not be quoted.

## Verdict

**PASS** for the Measurement Integrity Gate: all three defects are fixed,
regression-guarded, and the historical landmarks reproduce exactly with the
corrected instrumentation. Two open items are recorded, not blockers of this
gate: (a) the cumulative tool-content contract violation by the R4b workflow is
a *content-policy* finding for the next mission to address or waive; (b) task
correctness remains NOT_MEASURED.

## Reproduce

```bash
W=/home/masis/.cache/zvec-grep/r4-discovery-20261115
cargo run -q -p zg-engine --example r4b_paired -- $W/corpus        # all arms
R4B_DECISIONS=1 cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
cargo test -p zg-engine --lib -- rag::workflow --test-threads=1   # 25 pass
```
