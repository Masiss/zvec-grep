# R0 lexical and structural control protocol

This is a deterministic secondary baseline over the frozen R0 score-blind inputs. It does not alter the frozen queries, source ranges, candidate pool, or R0 vector scores. No score labels are read while computing BM25, symbol/scope alignment, or RRF. The implementation details below were fixed after the R0 vector output had been produced, but before calculating these lexical-control results; treat the lexical comparison as a secondary exploratory control, not as a preregistered primary result.

## Input and tokenization

- Queries are the 120 frozen raw query strings. Documents are the 1,591 R4 v3 `r0_input` strings in the frozen candidate dump.
- Candidate text is the exact R0 input before Potion tokenization. The tokenizer receipt confirms every candidate is under the R4 1,024-token cap, so the retained R0 string is unchanged.
- Lexical tokens are produced by splitting acronym-to-word boundaries with `([A-Z]+)([A-Z][a-z])`, splitting lowercase/digit-to-uppercase boundaries with `([a-z0-9])([A-Z])`, splitting on non-alphanumeric characters, then lowercasing. Keep digits and stop words; do not stem or tune per task.

## BM25

Use `k1=1.2`, `b=0.75`, and `idf(t)=ln(1+(N-df(t)+0.5)/(df(t)+0.5))`. Compute document frequencies and average document length independently inside each frozen candidate pool (local, primary file, repository slice, split slice, and the 1,591-candidate curated corpus). Query term frequency contributes once per occurrence. Sort scores descending; exact ties break by candidate ID. The candidate pools and source-path membership are frozen in `candidate-pool-definitions.json`.

Report BM25 on the R0 text. Since the R0 tokenizer receipt found no truncation, a full-text secondary BM25 run would be identical and is omitted.

## Symbol/scope control

Without consulting target selectors, score each candidate by the count of unique lowercase query tokens that exactly match identifier components from its extracted `entity` and `scope`. Split identifier components with the same camelCase/acronym/snake_case rules. Report how many queries have at least one identifier-component match. This is a limited control because queries intentionally avoid exact target method names.

## Fixed reciprocal-rank fusion

For each pool, combine R0 cosine rank and BM25 rank with equal weights: `1/(60+rank_vector) + 1/(60+rank_BM25)`. No weights are tuned. Use the same deterministic candidate-ID tie break.

Compute all baselines over score-blind query/candidate inputs first. Join the frozen 119 range labels and the 11-group/15-pair roster only after raw baseline outputs and hashes are written. Report required-range completion ranks; do not describe unjudged candidates as negatives or claim exhaustive semantic recall.
