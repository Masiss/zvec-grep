# Corrected R0 tokenization receipt

The corrected probe loads the pinned Potion tokenizer, disables its built-in 512-token truncation, encodes with special tokens disabled, retains at most 1,024 IDs, then filters the pinned unknown-token ID as the scorer does.

- Queries: 120; candidates: 1,591; maximum full query length: 35 tokens; maximum candidate length: 592 tokens.
- Truncated queries/candidates at the R4 1,024 cap: 0/0.
- Eighteen candidates exceed 512 tokens; their first 512 token IDs match the historical tokenizer output exactly. They were silently cut before the end in the old receipt.
- Corrected R0 tokenization JSONL SHA-256: `d97753383605212e56be6faa7fb83a9562ba3b49a6ad8a212da0e68116f8c32e`.
- Corrected R0–R5 tokenization manifest SHA-256: `3792ccceef064f0ec814be72b8acf3bb3015a5295e3bdb0d13ee8b912993363f`.
- Corrected R0 score SHA-256: `c05b547a1ab4563ebcf96d54329ba3e1691d8823a9d96a4d0370b5de70a5a91b`.

The hidden tail changed 18 candidate vectors. The corrected run reproduced the same aggregate hard-pair outcomes and required-range completion values as the historical run; use the corrected score file and manifest for reproduction.
