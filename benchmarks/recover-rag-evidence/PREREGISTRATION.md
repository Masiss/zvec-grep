# Recover Downstream RAG Evidence — preregistration

Frozen before implementation. Base: `work/discovery-selection-20261126@b05960d`.

## Fixed baseline and constraints

- Potion, tokenizer, extraction, chunking, index schema, storage, FTS/vector
  discovery and RRF fusion are unchanged.
- Normal search behavior must remain byte-identical when RAG env flags are off.
- No production change, no binary promotion, no held-out, no graph/query/model
  work.
- Rust clap dev cases and C# HRM D01–D12 dev cases only.

## Isolated changes

| Arm | Change | Env switch |
|---|---|---|
| A0 | Baseline from `b05960d` | none |
| A1 | Remove file diversity penalty in RAG only | `ZVEC_RAG_NO_DIVERSITY=1` |
| A2 | Delay rank cutoff to 200 while discovery stays unchanged | `ZVEC_RAG_RANK_LIMIT=200` |
| A3 | Budget-aware selection with source-range redundancy | `ZVEC_RAG_BUDGET_SELECT=1` |
| A4 | R1 Verified Evidence Pack assembly/delivery | `ZVEC_RAG_EVIDENCE_PACK_FILE=...` |
| A5 | Combine only changes that pass alone | all passing switches |

## Metrics and gates

- Primary: exact evidence coverage and evidence precision (delivered bytes
  overlapping gold / delivered bytes) at the same 24 KiB / 10-item budget.
- Secondary: task correctness with a tool-free external agent, Potion context
  tokens, p95 latency, CPU, peak RSS, oracle ceiling over the same pool.
- An isolated change passes only if exact coverage improves by >=0.05 macro
  without increasing context tokens >25% or p95 latency >2x baseline.
- Any change that degrades task correctness or normal-search parity is rejected.
- Combination is qualified only after isolated results are recorded.
