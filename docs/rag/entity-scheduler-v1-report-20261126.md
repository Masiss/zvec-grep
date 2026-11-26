# Gold-blind entity-anchor scheduler v1 report — 2026-11-26

## Verdict

**DONE for the single authorized scheduler; NO-GO.**

The locked gold-blind scheduler failed the mandatory regression and paired
quality gates:

- It rescued **R11** but lost **R10**, the mandatory 3/3 regression case.
- It rescued no C# question and lost **D11**.
- Macro byte precision fell in both languages.
- Integrity of the replay itself passed; the failure is scheduler quality,
not pack corruption or coordinate handling.

No formula tuning or second variant was run. No scheduler was ported. No
production code, ranker, selector, index, model, corpus, binary, daemon or
held-out data changed.

## Frozen setup

Follow-up to `docs/rag/oracle-anchor-order-report-20261126.md`.

- Branch: `work/recover-rag-evidence-20261126`; base `b4dcb16`.
- Run: `~/.cache/zvec-grep/entity-anchored-span-replay-20261126/entity-scheduler-v1/`.
- Preregistration: `ENTITY-SCHEDULER-PREREGISTRATION.md`.
- Same pool, arm A resolver, selector mechanics, caps and quotas as prior runs.

The scheduler groups unique fragments by entity and computes only
runtime-available features:

- `rrf = sum(1 / (60 + fragment_rank))`.
- Distinct query-group count, capped at four.
- Exact question/path token intersections, capped at two; tokenizer splits
  camelCase/snake_case and keeps parts of length >=3.
- `priority = rrf + 0.01 * min(route_count, 4) + 0.02 * min(path_matches, 2)`.

Entities are sorted by descending priority, then best rank and entity ID;
fragments within an entity retain original rank/pool order. The scheduler does
not read gold or parse additional source.

## Paired results

| Split / policy | All-gold | Gained | Lost | Exact union | Legacy single-item | Macro precision | Mean tokens | Total bytes | Total items |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Rust C0 | 2/12 | — | — | 7/30 | 5/30 | 0.0353558146 | 7,873.1667 | 294,834 | 400 |
| Rust scheduler v1 | 2/12 | R11 | R10 | 7/30 | 3/30 | 0.0343102968 | 7,689.0000 | 294,847 | 241 |
| Rust oracle upper bound | 7/12 | 5 | 0 | 21/30 | 15/30 | 0.0839807583 | 7,302.0000 | 294,820 | 317 |
| C# C0 | 1/12 | — | — | 2/25 | 2/25 | 0.0094574071 | 6,596.5000 | 294,768 | 305 |
| C# scheduler v1 | 0/12 | 0 | D11 | 0/25 | 0/25 | 0.0019420113 | 6,666.7500 | 294,696 | 209 |
| C# oracle upper bound | 3/12 | 2 | 0 | 5/25 | 4/25 | 0.0805548217 | 6,644.6667 | 294,749 | 262 |

Mean tokens remained inside the 1.25× guardrail, but token cost does not offset
the coverage and precision failures.

## Per-case paired evidence

`AG` is all-gold; `EX` is union-byte exact spans; `P` is precision; `T` is full
tokens. Values are C0 → scheduler v1.

| Case | AG | EX | P | T |
|---|---|---|---|---|
| R01 | 0→0 | 0→0 | 0 → 0 | 7736 → 8548 |
| R02 | 0→0 | 0→0 | 0 → 0 | 8072 → 7455 |
| R03 | 0→0 | 0→0 | 0 → 0 | 8017 → 7890 |
| R04 | 0→0 | 0→1 | .065638 → .070522 | 7140 → 5940 |
| R05 | 0→0 | 0→0 | 0 → 0 | 7696 → 7941 |
| R06 | 0→0 | 0→0 | 0 → 0 | 8126 → 8668 |
| R07 | 0→0 | 0→1 | 0 → .095283 | 8053 → 5836 |
| R08 | 0→0 | 1→0 | .014812 → 0 | 7762 → 7747 |
| R09 | 1→1 | 1→1 | .033447 → .033455 | 7932 → 8361 |
| R10 | 1→0 | 3→1 | .041758 → .006022 | 8741 → 8687 |
| R11 | 0→1 | 2→3 | .150887 → .206442 | 7640 → 7326 |
| R12 | 0→0 | 0→0 | .117726 → 0 | 7563 → 7869 |
| D01 | 0→0 | 0→0 | 0 → 0 | 6589 → 7196 |
| D02 | 0→0 | 0→0 | .057033 → 0 | 6452 → 4856 |
| D03 | 0→0 | 0→0 | 0 → 0 | 7185 → 7178 |
| D04 | 0→0 | 0→0 | 0 → 0 | 6303 → 5908 |
| D05 | 0→0 | 0→0 | 0 → 0 | 6195 → 7185 |
| D06 | 0→0 | 0→0 | 0 → 0 | 7160 → 7106 |
| D07 | 0→0 | 0→0 | 0 → 0 | 6703 → 7139 |
| D08 | 0→0 | 1→0 | .028402 → 0 | 6824 → 6869 |
| D09 | 0→0 | 0→0 | 0 → 0 | 7365 → 7266 |
| D10 | 0→0 | 0→0 | 0 → 0 | 5636 → 5681 |
| D11 | 1→0 | 1→0 | .028054 → .023304 | 6395 → 6595 |
| D12 | 0→0 | 0→0 | 0 → 0 | 6351 → 7022 |

## Gate results

| Gate | Rust | C# | Result |
|---|---:|---:|---|
| Case receipts | 12/12 | 12/12 | PASS |
| Hash/offset/UTF-8/content errors | 0 | 0 | PASS |
| Proposal bytes outside entity | 0 | 0 | PASS |
| Pack byte/item cap violations | 0 | 0 | PASS |
| Resolver entity/file/byte quota violations | 0 | 0 | PASS |
| Mandatory R10 3/3 | **1/3** | n/a | **FAIL** |
| C0 all-gold losses | R10 | D11 | **FAIL** |
| Newly all-gold cases | R11 | 0 | insufficient |
| Macro precision >= C0 | no | no | **FAIL** |
| Mean tokens <=1.25× C0 | yes | yes | PASS |

Because R10 is a mandatory regression and C0 all-gold losses are forbidden,
this scheduler cannot merit an opt-in port discussion.

## Cost

Offline resolver/replay cost, not binary p95:

| Split | Calls | Cold files | Cold bytes read/parsed | Warm bytes reused | Parse ms total | Replay mean / p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| Rust | 48 | 38 | 3,015,224 | 1,207,460 | 254.848 | 39.502 / 46.389 |
| C# | 40 | 30 | 790,682 / 790,655 | 378,436 | 218.530 | 62.789 / 86.775 |

Maximum cold bytes in one case were 437,381 Rust and 140,960 C#; no I/O quota
skip occurred. The scheduler added no source reads, but different entity order
changed which files the resolver opened.

## Why the gold-blind signal was insufficient

A separate gold-aware diagnostic measured how many of the scheduler's first
four entities contained at least one gold span:

| Split | C0 top-4 overlap | Scheduler top-4 overlap | Gold entities total |
|---|---:|---:|---:|
| Rust | 8 | 12 | 33 |
| C# | 4 | 7 | 23 |

The scheduler therefore improved entity-level candidate recall, but that did
not translate into complete question coverage:

- **R10:** three of six gold entities appeared in the scheduler top four, yet
  delivered exact spans fell from 3/3 to 1/3. Broad/related entities consumed
  the fixed budget without covering every gold declaration.
- **R11:** both gold entities appeared in the top four and the question became
  all-gold, producing the single Rust rescue.
- **D11:** one of two gold entities appeared in the top four, but the question
  lost its only exact span.
- C# scheduler top-four overlap occurred in seven cases, but no case became
  newly all-gold.

The failure attribution after scheduling was:

| First blocking stage | Rust | C# |
|---|---:|---:|
| Delivered | 7 | 0 |
| Entity absent from pool | 3 | 9 |
| Entity in pool, no anchor eligible before budget | 19 | 14 |
| No containing declaration <=4 KiB | 1 | 2 |

The metadata prior improved top-four entity recall but still scheduled too many
non-gold or incomplete entities ahead of the full evidence set. This is a
quality failure of the locked signal, not a reason to tune weights after seeing
the score.

## Decision

**NO-GO for scheduler v1. Do not port.**

The oracle upper bound remains valid evidence that anchor order matters, but
this gold-blind RRF/path prior does not reproduce it and violates mandatory
regressions. No second formula, threshold, feature or variant was run.

Any continuation with a different information source or policy requires a new
owner-authorized task and a new preregistration. The current result does not
authorize a ranker change, resolver change, reindex or production experiment.

## Reproduction

```bash
NEW=/home/masis/.cache/zvec-grep/entity-anchored-span-replay-20261126
ROOT=/home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/final
PYTHONPATH="$ROOT/tools:$ROOT/ranking-decision/union-replay/tools:$NEW/tools" \
  python3 "$NEW/tools/replay_entity_scheduler.py" --root "$ROOT" \
  --out "$NEW/entity-scheduler-v1" --arm A
PYTHONPATH="$ROOT/tools:$ROOT/ranking-decision/union-replay/tools:$NEW/tools" \
  python3 "$NEW/tools/score_arms.py" --root "$ROOT" \
  --run "$NEW/entity-scheduler-v1" --arm A
PYTHONPATH="$ROOT/tools:$ROOT/ranking-decision/union-replay/tools:$NEW/tools" \
  python3 "$NEW/tools/diagnose.py" --root "$ROOT" \
  --run "$NEW/entity-scheduler-v1" --arm A
```

Receipts include the preregistration, ledgers/stats, 24 packs, scores,
diagnosis, top-four entity recall and logs under `entity-scheduler-v1/`.
