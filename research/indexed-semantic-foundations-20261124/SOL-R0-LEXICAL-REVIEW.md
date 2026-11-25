# Fresh-context GPT-6.1 Sol High review — R0 lexical controls

> **Historical review:** v2 used the original vector ranks with hidden 512-token truncation. BM25 scores are unchanged, while RRF was recomputed from corrected R0 in v3. See `R0-LEXICAL-CONTROL-RECEIPT-v3.json` and the final handoff review.

**Disposition: READY for corrected v2 measurements, with the limits below.**

Sol independently reviewed the v2 scripts, raw rankings, frozen range and pairwise labels, and output hashes. The raw and analysis hashes match their receipts. It checked all 600 query/pool records for frozen membership, candidate counts, unique IDs, and deterministic ordering; all 1,800 range-completion rows and all 135 pairwise rows reproduce from raw rankings and frozen labels. BM25 spot checks agree within floating-point rounding; RRF spot checks agree exactly.

The review confirmed that v2 fixes the v1 tokenizer's extra letter/digit splitting and uses exact lexical tie comparisons. The scoring script does not read relevance labels or target selectors. Historical score-blinding is supported by the workflow and receipts, though it cannot be independently proven from artifacts alone.

## Interpretation limits

- Supporting-versus-nonsupporting pair labels cover only 11 groups and 15 candidate-input pairs. The 45 paraphrase comparisons are dependent; group averaging is implemented correctly.
- Lexical settings were fixed after vector scores existed. These controls are exploratory, not a preregistered lexical comparison.
- The 40-group ranking measure is completion of frozen annotated source ranges. It is not exhaustive semantic recall.
- Candidate pools are curated selected-file slices. The measurements do not establish ANN acquisition or production retrieval quality.
- Symbol coverage means some identifier-component match exists in the pool; it does not measure target-name coverage or successful discrimination.

## Reviewer conclusion

There is no demonstrated vector advantage over lexical on the held-out pairwise groups and no vector-plus-lexical complementarity-gate pass. Held-out macro group outcomes are cosine 0.7222, BM25 0.8889, and RRF 0.8472 across two repositories. Curated-corpus required-range Completion@10 is 45.8% for cosine, 53.3% for BM25, and 61.7% for RRF. The RRF range-completion gain does not override its lower held-out pairwise result than BM25, nor does it identify production retrieval gains.
