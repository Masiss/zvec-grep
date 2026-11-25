# R0 tokenizer and range-survival receipt

> **Superseded:** this receipt did not detect tokenizer.json's built-in 512-token truncation. The corrected probe disables embedded truncation and applies the R4 1,024-token cap manually; see `corrected-r0-v2/tokenization-manifest.json` and `R0-TOKEN-RECEIPT-v2.md`.

**Status:** tokenization measured; no embeddings or similarity scores computed.
**Annotation freeze:** `frozen-r0-v1/manifest.json`
**Token receipt:** `frozen-r0-v1/tokenization-r0-v1/manifest.json`
**Token receipt SHA-256:** `0ca09c5ffb37150f0dfed0f63152b4c606dc2b97aa8b72820b8622f6e5b1fe6b`

The probe used Rust `tokenizers` 0.23.1 against the pinned Potion tokenizer, matching R4's `encode(text, false)` behavior. Offsets are UTF-8 byte offsets, confirmed with a multibyte string. The R4 limit is 1,024 tokens; query and document prefixes are both absent.

- 120 queries: no truncation; maximum 35 tokens.
- 1,591 R0 candidate inputs: no truncation; maximum 512 tokens.
- 228 candidate/range instances across 119 annotated range segments: all source spans mapped to tokenizer offsets, all range tokens survived the 1,024-token limit, and none of the decisive range tokens used the filtered unknown ID.
- All 15 pairwise candidate inputs were present and untruncated.

The complete token IDs, offsets, unknown-token filtering, candidate input hashes and range mappings are in `frozen-r0-v1/tokenization-r0-v1/`. This cleared the R0 token-cap gate. R0 cosine scores were later computed and are reported in `R0-RESULTS-v1.md`; R1–R5 still require their own transformed-range survival and common-mask receipts.
