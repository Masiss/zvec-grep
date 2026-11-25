# GPT-6.1 Sol review of R0 results

> **Historical review:** this review checked arithmetic for the original score output, whose tokenizer had hidden 512-token truncation. The corrected R0 score was independently recomputed; see the final handoff review for current artifacts.

**Disposition:** measurements READY after one wording correction. Fresh-context GPT-6.1 Sol High reviewed the analysis JSON and raw score lineage. No model scoring was rerun during review.

Verified findings:

- All 45 pairwise comparison outcomes reproduce: 33 wins, 12 losses, no ties; macro group score 0.772727; 8 group wins and 3 losses.
- All 600 range completion rows and 200 pool size/hash receipts match the raw scores and frozen range memberships.
- Completion aggregation is correct: minimum eligible-candidate rank for each range, maximum across jointly required ranges, then minimum across sufficient sets.
- Full-pool completion rates and conventional median rank 14 match. The repository-pool median is 9.5, using the middle two values for 120 outcomes.
- The held-out cluster bootstrap reproduces: 10,000 draws, seed `20261124`, equal-repository mean 0.7916667 and 95% interval `[0.5833333, 1.0]`.
- Frozen weights and tokenizer hashes match. No scorer rerun was needed.

The reviewer noted that the report should name three groups with paraphrase-dependent scores, including two group-level losses. `R0-RESULTS-v1.md` was updated to identify `dev-c4-02`, `dev-c5-01`, and `held-newton-c5` while preserving the original scores.

The conclusions remain limited to curated candidate pools, 11 pairwise groups, and required-source-range completion. They do not establish exhaustive semantic recall, ANN acquisition, product quality, or answer quality.
