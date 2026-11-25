# R1–R5 preliminary run — superseded execution fault

The first local representation run was not an execution of the frozen `CONTRACT-v1.md` and is not valid evidence for selecting a representation. Its score artifacts remain under `.tmp/indexed-semantic-foundations-20261124/representation-v3/` for audit only.

Fresh-context GPT-6.1 Sol High identified these faults before any result was accepted:

- `tokenizer.json` carries a built-in 512-token truncation. The first probe did not disable it before applying the R4 1,024-token cap, and the first survival checker could accept a partially retained range.
- The builder changed candidate units for R1–R4 instead of preserving the frozen fragment anchors; it retained R0 candidates as fallbacks where the contract says not applicable, and its R5 units were not the specified method chunks.
- R1–R3 did not apply the frozen comment removal and JSON serialization rules. R4 added owner signatures and omitted fully qualified scopes because of a per-file map lifetime bug.
- The common mask was derived after scores and counted repeated annotation rows as unique ranges.
- The physical-byte budget measure did not incorporate token survival and is retained only as an unaudited exploratory computation.

The tokenizer defect also affected the original R0 cosine scores: 18 R0 inputs contain more than 512 but fewer than 1,024 tokens. The historical R0 vector and RRF outputs are superseded and must be replaced. The raw-text BM25 control is unchanged because it used the complete candidate text, all R0 strings are below the specified 1,024-token cap, and the lexical tokenizer does not use the Potion tokenizer. A corrected R0 and R1–R5 run is being rebuilt from the frozen contract, with token and source-range survival frozen before scoring.
