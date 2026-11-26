# Fragment-flow preregistration

Continuation from `work/recover-rag-evidence-20261126@5eab0b5`.

## New pipeline under test

`Fragment Discovery → Fragment Fusion → Fragment Ranking → Fragment Selection → Evidence Pack`

- Discovery, chunking, Potion, tokenizer, index schema, storage, FTS/vector
  routes and RRF constants are unchanged.
- Entity supplies metadata and structural context only.
- Multiple fragments from the same entity/file may appear in the final pack;
  dedupe is by fragment id and source-range overlap, not by entity or file.
- RAG-only env `ZVEC_RAG_FRAGMENT_FLOW=1`; normal search remains untouched.

## Arms

| Arm | Binary | Switches |
|---|---|---|
| F0 | baseline `b05960d` | none |
| F1 | previous candidate `5eab0b5` | `ZVEC_RAG_NO_DIVERSITY=1` (previous best Rust arm) |
| F2 | fragment candidate | `ZVEC_RAG_FRAGMENT_FLOW=1`, `ZVEC_RAG_BUDGET_SELECT=1`, R1 pack |
| F3 | fragment candidate | F2 + `ZVEC_RAG_RANK_LIMIT=200` |

## Gates

At fixed 24 KiB / 10 items, fragment flow must improve exact evidence coverage
by >=0.05 over F0 on both Rust and C# without >25% token growth or >2x p95
latency. Evidence precision, task correctness, CPU/RAM and oracle ceiling are
reported. No held-out, no production promotion.

## Amendment F — fragment-only arm

F2 combined fragment flow with budget selection and R1 delivery. To isolate the
unit change, F4 tests `ZVEC_RAG_FRAGMENT_FLOW=1` only. Thresholds are unchanged;
F4 is added before qualification.
