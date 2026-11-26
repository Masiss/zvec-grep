# Entity-anchored span replay report — 2026-11-26

## Verdict

**DONE for the authorized offline replay; NO-GO for port or promotion.**

Both preregistered policies preserved pack integrity and the C0 all-gold cases,
but neither delivered evidence for one additional all-gold question. Arm B
improved macro byte precision in both languages without increasing exact-span
or question-level coverage. No resolver, selector, ranker, index, model, default,
binary, daemon, corpus, gold label or held-out data was changed.

The next bottleneck is **anchor scheduling under the fixed byte budget**, not
source coordinates or declaration boundaries. Most missing gold spans already
lie inside entities present in the frozen pool, but their fragment anchors do
not become eligible before the pack fills or before the four-entity resolver
quota is exhausted.

## Scope and frozen identity

Executed plan: `docs/rag/entity-anchored-span-replay-plan-20261126.md`.

- Worktree/branch: `/home/masis/src/zvec-grep-rust-rag-20261126`,
  `work/recover-rag-evidence-20261126` @ `e0193d0`; coordinate code `b336cbb`.
- Run receipts: `~/.cache/zvec-grep/entity-anchored-span-replay-20261126/`.
- Cases: Rust `c6e1a7e7…`, C# `27626109…`.
- Model SHA-256: `75cf7a6c…`; tokenizer `107bbdcb…`.
- C0 binary SHA-256: `48855c5b…`.
- Resolver SHA-256: `32665259…`; tree-sitter `0.27.0`, Rust grammar `0.24.2`,
  C# grammar `0.23.5`.
- Full input hashes: `MANIFEST.sha256`; locked policy: `PREREGISTRATION.md`.

The resolver received only source path/hash, entity/fragment byte ranges and
runtime structural metadata. It never received case ID, gold path/span or an
expected answer. Gold was read only by the separate scorer/diagnosis scripts.

## P0 — control parity and metric reconciliation

The control replay reproduces the real selector's order, fragment dedupe,
half-open byte clipping, remainder handling and item accounting.

| Check | Rust | C# |
|---|---:|---:|
| Ordered case parity | 12/12 | 12/12 |
| Replay items = C0 items | 400 = 400 | 305 = 305 |
| Replay bytes = C0 bytes | 294,834 = 294,834 | 294,768 = 294,768 |
| Tuple `(path, hash, start, end, content digest)` mismatches | 0 | 0 |
| Verifier gaps | 0 | 0 |
| R10 all gold | 3/3 | n/a |

A pre-existing scoring inconsistency was found and preserved rather than hidden:

- The union-replay receipt's **single-item** exact metric is **5/30 Rust** and
  **2/25 C#**.
- Correct union-of-pack-byte exact is **7/30 Rust** and **2/25 C#**. R11 has two
  gold spans covered by adjacent pack items, not by one item.
- Question-level all-gold counts are unchanged: **2/12 Rust, 1/12 C#**.

Both metrics are reported below, as allowed by the plan. Precision and all-gold
gates use unique source-byte unions.

## Locked policies

Both arms used the frozen pool, original rank order and C0 selector mechanics,
with a 24,576-byte / 64-item cap. Replacement happened atomically at the current
candidate; invalid or over-budget proposals fell back to the original fragment.

- Resolver attempt only when all clipped original pieces fit the remaining cap.
- At most four distinct entities and four distinct files per question; one
  proposal per entity; at most 4,096 bytes per proposed span.
- At most 1 MiB cold source bytes read/parsed per question; warm file-cache reuse
  was recorded separately.
- Rust: `function_item`. C#: method, constructor, operator, conversion,
  destructor and local-function declarations with a body.
- **A:** smallest declaration containing the entire fragment.
- **B:** declaration with the highest locked question/name/signature term score;
  tie-break containing fragment, source offset, kind, name.

## Paired quality results

Macro precision is the mean per-case unique-gold-byte / unique-delivered-byte
ratio. Tokens are full tokenizer counts on the serialized item content.

| Split / arm | All-gold cases | Gained | Lost | Exact union | Legacy single-item | Macro precision | Mean tokens | Total bytes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Rust C0 | 2/12 | — | — | 7/30 | 5/30 | 0.0353558146 | 7,873.1667 | 294,834 |
| Rust A | 2/12 | 0 | 0 | 7/30 | 5/30 | 0.0353558146 | 7,869.8333 | 294,834 |
| Rust B | 2/12 | 0 | 0 | 7/30 | 5/30 | 0.0379650438 | 7,860.3333 | 294,822 |
| C# C0 | 1/12 | — | — | 2/25 | 2/25 | 0.0094574071 | 6,596.5000 | 294,768 |
| C# A | 1/12 | 0 | 0 | 2/25 | 2/25 | 0.0094574071 | 6,601.5000 | 294,798 |
| C# B | 1/12 | 0 | 0 | 2/25 | 2/25 | 0.0109267178 | 6,591.8333 | 294,786 |

### Per-case paired evidence

`AG` is all-gold; `EX` is union-byte exact spans; `P` is precision; `T` is full
tokens. Values are C0 / A / B.

| Case | AG | EX | P | T |
|---|---|---|---|---|
| R01 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 7736/7736/7671 |
| R02 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 8072/8072/8054 |
| R03 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 8017/8017/8030 |
| R04 | 0/0/0 | 0/0/0 | .065638/.065638/.065638 | 7140/7100/7100 |
| R05 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 7696/7696/7696 |
| R06 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 8126/8126/8123 |
| R07 | 0/0/0 | 0/0/0 | 0/0/.031130 | 8053/8053/8067 |
| R08 | 0/0/0 | 1/1/1 | .014812/.014812/.014815 | 7762/7762/7743 |
| R09 | 1/1/1 | 1/1/1 | .033447/.033447/.033473 | 7932/7932/7986 |
| R10 | 1/1/1 | 3/3/3 | .041758/.041758/.041757 | 8741/8741/8661 |
| R11 | 0/0/0 | 2/2/2 | .150887/.150887/.151041 | 7640/7640/7630 |
| R12 | 0/0/0 | 0/0/0 | .117726/.117726/.117726 | 7563/7563/7563 |
| D01 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 6589/6719/6589 |
| D02 | 0/0/0 | 0/0/0 | .057033/.057033/.057033 | 6452/6452/6452 |
| D03 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 7185/7185/7185 |
| D04 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 6303/6291/6236 |
| D05 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 6195/6137/6262 |
| D06 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 7160/7160/7160 |
| D07 | 0/0/0 | 0/0/0 | 0/0/.017632 | 6703/6703/6647 |
| D08 | 0/0/0 | 1/1/1 | .028402/.028402/.028402 | 6824/6824/6824 |
| D09 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 7365/7365/7365 |
| D10 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 5636/5636/5636 |
| D11 | 1/1/1 | 1/1/1 | .028054/.028054/.028054 | 6395/6395/6395 |
| D12 | 0/0/0 | 0/0/0 | 0 / 0 / 0 | 6351/6351/6351 |

## Integrity and cost gates

| Gate | A | B | Result |
|---|---:|---:|---|
| Case receipts | 24/24 | 24/24 | PASS |
| Hash/offset/UTF-8/content errors | 0 | 0 | PASS |
| Proposal bytes outside entity | 0 | 0 | PASS |
| Pack byte/item cap violations | 0 | 0 | PASS |
| Resolver entity/file/byte quota violations | 0 | 0 | PASS |
| R10 all gold | 3/3 | 3/3 | PASS |
| New all-gold case | 0 | 0 | **FAIL port signal** |
| C0 all-gold cases lost | 0 | 0 | PASS |
| Macro precision >= C0 | equal | higher both | PASS |
| Mean tokens <= 1.25 x C0 | yes | yes | PASS |

Resolver/replay cost (offline, not binary p95):

| Split / arm | Calls | Cold files | Cold bytes read/parsed | Warm bytes reused | Parse ms total | Replay mean / p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| Rust A | 48 | 31 | 1,715,042 | 1,016,695 | 145.549 | 20.665 / 34.168 |
| Rust B | 48 | 32 | 1,718,618 | 895,560 | 158.199 | 22.586 / 41.314 |
| C# A | 48 | 40 | 1,232,276 / 1,232,243 | 243,479 | 295.424 | 44.119 / 57.268 |
| C# B | 48 | 40 | 1,232,276 / 1,232,243 | 243,479 | 298.236 | 44.694 / 56.469 |

Maximum cold bytes in one case were 489,525 Rust and 177,871 C#; maximum
entities attempted per case was four. No I/O quota skip occurred.

One Rust resolver attempt in each arm (`R03`, `tests/builder/help.rs`) fell back
because the frozen grammar reported a parse error. Hash verification passed and
the original fragment was retained. This is recorded as a parser-coverage
fallback, not hidden as a successful boundary resolution.

## Failure attribution

Counts below are gold spans, not unique files. A span is `delivered` when the
union of actual pack bytes covers it completely.

| First blocking stage | Rust A | Rust B | C# A | C# B |
|---|---:|---:|---:|---:|
| Delivered | 7 | 7 | 2 | 2 |
| Entity absent from pool | 3 | 3 | 9 | 9 |
| Entity in pool, no anchor eligible before budget | 15 | 14 | 5 | 6 |
| Resolver entity/quota limit | 3 | 4 | 7 | 6 |
| No containing declaration (A) | 2 | — | 2 | — |
| No query signal (B) | — | 1 | — | 0 |
| Wrong declaration selected (B) | — | 1 | — | 2 |
| Correct span blocked by cap | 0 | 0 | 0 | 0 |

`original_not_fit` dominates the raw candidate ledger because the selector walks
hundreds of pool candidates after the byte cap is effectively full. For
gold-bearing entities specifically, the decisive failures are late/no anchor
eligibility and the four-entity resolver quota.

### Byte-evidence examples

- **R03 remains 0/4.** Arm A accepted a valid expansion in
  `tests/builder/hidden_args.rs` to `hidden_arg_with_possible_value_with_help`
  at bytes `6986..7765`, but the gold spans are in `parser.rs` `64675..64836`,
  `builder/command.rs` `33686..33920`, `output/help.rs` `211..1233` and
  `error/mod.rs` `10872..11214`. The expansion was structurally valid but did
  not advance any gold byte.
- **D04 remains 0/1.** Arm A expanded to C# `EditEmployeeSecurity` at
  `EmployeeCommand.cs` `24535..26485`; the gold span is `24119..24525`, ending
  10 bytes before the proposal. This is an adjacent-but-wrong declaration, not
  a coordinate failure.
- **D08 remains 0/2.** Arm B selected `DeleteEmployeeByIds` at
  `EmployeeCommand.cs` `23177..23666`, exactly covering the service gold span.
  The controller gold span at `EmployeeController.cs` `27513..27942` remained
  missing, so the question still did not become all-gold.
- **B precision without case gain:** R07 precision rose from `0` to `0.031130`
  and D07 from `0` to `0.017632`; neither gained an exact gold span or an
  all-gold question.

## Gate decision

- **P0 integrity: PASS.**
- **Arm A integrity: PASS; port signal: FAIL (0 new all-gold cases).**
- **Arm B integrity: PASS; port signal: FAIL (0 new all-gold cases).**
- **Port: NO-GO. Promotion: NO-GO.**

Because neither arm produced a newly all-gold question, the plan explicitly
classifies precision-only or span-only movement as NO-GO. No opt-in code path
was ported and no binary p95 campaign was started.

## Exactly one next step supported by the data

Run **one offline, gold-aware upper-bound probe of entity-anchor scheduling on
the same frozen pool**: move anchors whose entity contains a gold span ahead of
other anchors, keep the locked resolver, fragment set, selector and 24 KiB cap,
then count newly all-gold questions and paired losses. Gold may order this
oracle probe only after the policies are locked; it must not become a runtime
feature.

This probe directly tests the dominant measured blocker: 15 Rust + 5/6 C#
gold spans have a matching entity in the pool but no eligible anchor before
budget, with another 3/4 Rust + 7/6 C# stopped by the resolver quota. If the
oracle does not create a new all-gold question, close the anchor-order line and
do not spend effort on a new ranker or resolver variant.

### Follow-up outcome

The authorized probe was executed and recorded in
[`oracle-anchor-order-report-20261126.md`](oracle-anchor-order-report-20261126.md).
It added five Rust and two C# all-gold questions with no C0 losses, so the
anchor-order line remains open. The oracle uses gold and is not deployable; a
separate owner-authorized gold-blind scheduler task is the next action.

## Reproduction

All commands use external targets/receipts; no repository index or production
artifact is rebuilt.

```bash
NEW=/home/masis/.cache/zvec-grep/entity-anchored-span-replay-20261126
ROOT=/home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/final

PYTHONPATH="$ROOT/tools:$NEW/tools" \
  python3 "$NEW/tools/replay_control.py" --root "$ROOT" --out "$NEW/p0"

cd "$NEW/resolver"
CARGO_TARGET_DIR="$NEW/resolver-target" cargo build --release --offline

PYTHONPATH="$ROOT/tools:$NEW/tools" \
  python3 "$NEW/tools/replay_arms.py" --root "$ROOT" --out "$NEW/arms/A" --arm A
PYTHONPATH="$ROOT/tools:$ROOT/ranking-decision/union-replay/tools:$NEW/tools" \
  python3 "$NEW/tools/score_arms.py" --root "$ROOT" --run "$NEW/arms/A" --arm A

PYTHONPATH="$ROOT/tools:$NEW/tools" \
  python3 "$NEW/tools/replay_arms.py" --root "$ROOT" --out "$NEW/arms/B" --arm B
PYTHONPATH="$ROOT/tools:$ROOT/ranking-decision/union-replay/tools:$NEW/tools" \
  python3 "$NEW/tools/score_arms.py" --root "$ROOT" --run "$NEW/arms/B" --arm B
PYTHONPATH="$ROOT/tools:$ROOT/ranking-decision/union-replay/tools:$NEW/tools" \
  python3 "$NEW/tools/diagnose.py" --root "$ROOT" --run "$NEW/arms/A" --arm A
PYTHONPATH="$ROOT/tools:$ROOT/ranking-decision/union-replay/tools:$NEW/tools" \
  python3 "$NEW/tools/diagnose.py" --root "$ROOT" --run "$NEW/arms/B" --arm B
```

Machine-readable results are in `p0/`, `arms/A/scores/`, `arms/B/scores/`,
`arms/*/diagnosis/`, `replay-stats-*.json`, ledgers and logs under the run ID.
