# R0 tokenizer execution fault

The first R0 token probe called `Tokenizer::from_file` on tokenizer.json, which declares `truncation.max_length = 512`. It then recorded `truncated = token_count > 1,024`, so it could not detect the tokenizer's earlier 512-token truncation. The original token and score artifacts are retained and marked historical.

The corrected probe explicitly calls `with_truncation(None)` before encoding and applies the R4 1,024-token cap itself. Eighteen candidates have 513–592 full tokens; their first 512 IDs match the historical output. No query or candidate reaches the corrected cap. Model weights, queries, source inputs, annotations, and scoring code were unchanged.

Corrected direct scores changed for 18 candidate IDs (maximum absolute cosine delta 0.04406). No frozen pairwise outcome, group-level R0 outcome, or reported annotated-range completion rate changed. Corrected lexical BM25 scores are identical because they use the same full R0 strings, all below 1,024 tokens; RRF was recomputed from corrected vector ranks.
