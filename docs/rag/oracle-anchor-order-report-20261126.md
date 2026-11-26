# Oracle entity-anchor scheduling report — 2026-11-26

## Verdict

**DONE for the single authorized upper-bound probe. Positive signal for
anchor-order research; NOT deployable and NOT a port candidate.**

Moving fragments from entities that contain gold ahead of other fragments,
while keeping the locked arm A resolver, selector and 24 KiB cap, changed:

- Rust all-gold questions: **2/12 → 7/12** (+5, 0 lost).
- C# all-gold questions: **1/12 → 3/12** (+2, 0 lost).
- Rust union exact spans: **7/30 → 21/30**.
- C# union exact spans: **2/25 → 5/25**.

The ordering uses gold and is therefore an oracle upper bound only. It must not
be shipped or treated as evidence that a production ranker has already passed.
No production code, ranker, selector, index, model, corpus, binary, daemon or
held-out data changed.

## Frozen setup and the single change

Follow-up to `docs/rag/entity-anchored-span-replay-report-20261126.md`.

- Branch: `work/recover-rag-evidence-20261126`; base `5760bdf`.
- Run: `~/.cache/zvec-grep/entity-anchored-span-replay-20261126/oracle-anchor-A/`.
- Preregistration: `ORACLE-ANCHOR-PREREGISTRATION.md`.
- Resolver SHA-256: `32665259…`; cases/model/tokenizer hashes unchanged.

Exactly one policy changed from arm A: candidates belonging to an entity whose
frozen entity range contains at least one gold byte span were processed before
all other candidates. Original rank and pool order were preserved inside each
priority class. Fragment set, source identity, clipping, dedupe, item accounting,
24,576-byte / 64-item pack cap, resolver quotas, declaration rules and fallbacks
were unchanged.

Arm A was preregistered for this probe to isolate scheduling from arm B's
query/declaration-selection behavior.

## Paired results

Macro precision is mean per-case unique gold bytes / unique delivered bytes.
Tokens are full tokenizer counts on serialized item content.

| Split / policy | All-gold | Gained | Lost | Exact union | Legacy single-item | Macro precision | Mean tokens | Total bytes | Total items |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Rust C0 | 2/12 | — | — | 7/30 | 5/30 | 0.0353558146 | 7,873.1667 | 294,834 | 400 |
| Rust oracle | 7/12 | 5 | 0 | 21/30 | 15/30 | 0.0839807583 | 7,302.0000 | 294,820 | 317 |
| C# C0 | 1/12 | — | — | 2/25 | 2/25 | 0.0094574071 | 6,596.5000 | 294,768 | 305 |
| C# oracle | 3/12 | 2 | 0 | 5/25 | 4/25 | 0.0805548217 | 6,644.6667 | 294,749 | 262 |

Newly all-gold cases:

- Rust: **R03, R07, R08, R11, R12**.
- C#: **D03, D04**.

No C0 all-gold case was lost. R09/R10 and D11 remained all-gold. Mean tokens
changed by approximately **−7.25% Rust** and **+0.73% C#**, both inside the
1.25× guardrail. Total payload bytes were effectively unchanged while item
count fell, explaining much of the token reduction.

## Per-case paired evidence

`AG` is all-gold; `EX` is union-byte exact spans; `P` is precision; `T` is full
tokens. Values are C0 → oracle.

| Case | AG | EX | P | T |
|---|---|---|---|---|
| R01 | 0→0 | 0→2 | 0 → .082825 | 7736 → 6340 |
| R02 | 0→0 | 0→0 | 0 → 0 | 8072 → 7714 |
| R03 | 0→1 | 0→4 | 0 → .071597 | 8017 → 7816 |
| R04 | 0→0 | 0→0 | .065638 → .065721 | 7140 → 6000 |
| R05 | 0→0 | 0→1 | 0 → .118988 | 7696 → 7431 |
| R06 | 0→0 | 0→1 | 0 → .041607 | 8126 → 7896 |
| R07 | 0→1 | 0→2 | 0 → .133765 | 8053 → 6065 |
| R08 | 0→1 | 1→2 | .014812 → .043791 | 7762 → 7738 |
| R09 | 1→1 | 1→1 | .033447 → .033447 | 7932 → 7932 |
| R10 | 1→1 | 3→3 | .041758 → .041768 | 8741 → 8268 |
| R11 | 0→1 | 2→3 | .150887 → .206324 | 7640 → 7641 |
| R12 | 0→1 | 0→2 | .117726 → .167935 | 7563 → 6783 |
| D01 | 0→0 | 0→0 | 0 → .139906 | 6589 → 6606 |
| D02 | 0→0 | 0→0 | .057033 → .263633 | 6452 → 6873 |
| D03 | 0→1 | 0→1 | 0 → .138205 | 7185 → 7028 |
| D04 | 0→1 | 0→1 | 0 → .016530 | 6303 → 6240 |
| D05 | 0→0 | 0→0 | 0 → .235931 | 6195 → 6624 |
| D06 | 0→0 | 0→0 | 0 → .078029 | 7160 → 7209 |
| D07 | 0→0 | 0→1 | 0 → .037959 | 6703 → 6508 |
| D08 | 0→0 | 1→1 | .028402 → .028411 | 6824 → 6730 |
| D09 | 0→0 | 0→0 | 0 → 0 | 7365 → 7365 |
| D10 | 0→0 | 0→0 | 0 → 0 | 5636 → 5636 |
| D11 | 1→1 | 1→1 | .028054 → .028054 | 6395 → 6749 |
| D12 | 0→0 | 0→0 | 0 → 0 | 6351 → 6168 |

## Integrity and cost

| Gate | Rust | C# | Result |
|---|---:|---:|---|
| Case receipts | 12/12 | 12/12 | PASS |
| Hash/offset/UTF-8/content errors | 0 | 0 | PASS |
| Proposal bytes outside entity | 0 | 0 | PASS |
| Pack byte/item cap violations | 0 | 0 | PASS |
| Resolver entity/file/byte quota violations | 0 | 0 | PASS |
| R10 all gold | 3/3 | n/a | PASS |
| C0 all-gold losses | 0 | 0 | PASS |
| Precision >= C0 | yes | yes | PASS |
| Mean tokens <= 1.25× C0 | yes | yes | PASS |

Offline resolver/replay cost, not binary p95:

| Split | Calls | Cold files | Cold bytes read/parsed | Warm bytes reused | Parse ms total | Replay mean / p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| Rust | 48 | 35 | 2,061,675 | 780,272 | 199.826 | 29.698 / 39.961 |
| C# | 47 | 34 | 1,055,074 / 1,055,047 | 546,438 | 291.134 | 52.428 / 70.691 |

Maximum cold bytes in one case were 323,013 Rust and 147,959 C#. Maximum
entities attempted per case was four. No I/O quota skip occurred. These are
local replay costs; a real-binary paired p95 remains required only if a
gold-blind candidate is later authorized and passes replay gates.

## Failure attribution after oracle scheduling

The diagnosis scorer was corrected to evaluate delivery as a **union of pack
bytes**, matching the exact metric. With gold-bearing entities scheduled first,
the previous anchor-order/quota blockers disappear from the missing-span
ledger:

| First blocking stage | Rust | C# |
|---|---:|---:|
| Delivered | 21 | 5 |
| Entity absent from pool | 3 | 9 |
| No containing declaration <=4 KiB (arm A) | 6 | 11 |
| Entity not anchored before budget | 0 | 0 |
| Resolver quota | 0 | 0 |
| Wrong declaration selected | 0 | 0 |

This isolates the next representation problem: after fixing scheduling, the
remaining misses are dominated by entities absent from the pool or by arm A's
4 KiB containing-declaration rule, not by selector byte clipping.

## Byte evidence for rescued questions

The following are actual oracle pack intervals covering the gold bytes:

- **R03, 4/4:** `parser.rs` `64675..64836`; `builder/command.rs`
  `33686..33920` covered by pack `32777..34587`; `output/help.rs` `211..1233`;
  `error/mod.rs` `10872..11214` covered by pack `8203..11543`.
- **R07, 2/2:** `clap_lex/src/lib.rs` `13037..13983` covered by
  `11996..14005`; `parser/parser.rs` `36025..38366` covered by
  `34092..41967`.
- **R08, 2/2:** `aot/generator/mod.rs` `8145..8355`; `aot/shells/bash.rs`
  `238..1104` covered by `238..2421`.
- **R11, 3/3:** all three `parser/validator.rs` gold spans are covered by the
  pack interval `451..15735`.
- **R12, 2/2:** `clap_derive/src/derives/args.rs` `6405..10243` covered by
  `6405..10576`; `clap_derive/src/item.rs` `41632..41920` covered by
  `38982..43943`.
- **D03, 1/1:** `EmployeeCommand.cs` `8337..11732` covered by `7856..18931`.
- **D04, 1/1:** `EmployeeCommand.cs` `24119..24525` covered exactly by
  `24119..24525`; the earlier arm A run selected the adjacent declaration at
  `24535..26485` and missed it.

## Decision and next authorized action

The probe **does not close the anchor-order line**. It shows substantial
question-level upside from making the right entity anchors eligible early,
with no paired all-gold losses and improved precision/token cost.

It also **does not authorize a ranker implementation**: the scheduler knows
gold. The next action is a separate owner-authorized task to preregister and
measure **one gold-blind entity-anchor scheduler** using only runtime-available
query, fragment/entity metadata and source structure. It must be evaluated on
the same frozen pool/cap, preserve every C0 all-gold case, and pass the existing
precision/token/integrity gates before any opt-in port discussion.

No oracle ordering may enter production code.

## Reproduction

```bash
NEW=/home/masis/.cache/zvec-grep/entity-anchored-span-replay-20261126
ROOT=/home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/final
PYTHONPATH="$ROOT/tools:$ROOT/ranking-decision/union-replay/tools:$NEW/tools" \
  python3 "$NEW/tools/replay_oracle_anchor.py" --root "$ROOT" \
  --out "$NEW/oracle-anchor-A" --arm A
PYTHONPATH="$ROOT/tools:$ROOT/ranking-decision/union-replay/tools:$NEW/tools" \
  python3 "$NEW/tools/score_arms.py" --root "$ROOT" \
  --run "$NEW/oracle-anchor-A" --arm A
PYTHONPATH="$ROOT/tools:$ROOT/ranking-decision/union-replay/tools:$NEW/tools" \
  python3 "$NEW/tools/diagnose.py" --root "$ROOT" \
  --run "$NEW/oracle-anchor-A" --arm A
```

Receipts include the preregistration, replay ledgers/stats, 24 packs, per-case
scores, span diagnosis and logs under `oracle-anchor-A/`.
