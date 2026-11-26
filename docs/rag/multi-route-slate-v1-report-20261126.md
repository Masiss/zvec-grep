# Multi-route entity-slate v1 offline replay report

## Parent independent review

**PASS for integrity and policy conformance; NO-GO accepted.** The parent
independently verified all input/output hashes, reran eight synthetic tests and
the full audit: 24 cases, 7,840 entities, 10,772 events, 632 items, 19 proposals
and 834 not-reached candidates, with zero errors. The repository remained clean.

The implementation matches the locked route wallets, fair rounds, saturation,
primary-route ownership and resolver-slot allocation. One documentation
discrepancy was found: the proposal mentions a 24-query-term cap while the
preregistration/reference feature study does not apply it. Every actual case
has at most 13 filtered query terms, so results are unaffected.

The strong gate correctly rejects the candidate: Rust 3/12 all-gold and 9/30
exact, C# 2/12 and 3/25, with R11 still 2/3. No port or tuning is authorized.

## Verdict

**DONE for the single authorized configuration; NO-GO.**

The preregistered multi-route entity-slate replay completed on all 24 frozen
cases. Integrity, P0, R10, no-C0-loss, precision, and token gates passed. Four
required quality gates failed: Rust all-gold/R11, C# all-gold count, Rust union
exact, and C# union exact. No tuning, second completed policy, production edit,
reindex, held-out run, or binary p95 campaign was performed.

The candidate improved over C0 and hybrid-ranking-v1 but did not meet the
preregistered port-discussion threshold:

- Rust all-gold: **2/12 -> 3/12**, gaining R12 and losing none; R11 remained 2/3.
- C# all-gold: **1/12 -> 2/12**, gaining D04 and preserving D11.
- Rust union exact: **7/30 -> 9/30** (required >=11).
- C# union exact: **2/25 -> 3/25** (required >=4).
- Macro precision improved in both splits; mean tokens fell in both splits.

The gold-aware oracle upper bound remains 7/12 Rust and 3/12 C# all-gold, with
21/30 and 5/25 union exact. It was used only for comparison after scoring.

## Frozen setup and change discipline

- Authoritative proposal: `docs/rag/ranking-fix-proposal-20261126.md` at
  `0ef8ad3ec22c450e6a9e224f528b48e90a4666b1`, SHA-256
  `087f0a074da354852c6044fd67d8615457e7a62b5ea7ef1a26cb8837a720ec5c`.
- Run root: `/home/masis/.cache/zvec-grep/entity-anchored-span-replay-20261126/multi-route-slate-v1/`.
- Frozen root: `/home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/final`.
- Stored metadata SHA-256 verified:
  `6dca6bdd8c206386ee9cdd03158fb2451d4f43ac9bf130b3450fd598b2e7b2fd`.
- Resolver SHA-256 verified:
  `32665259f9b43447f9b024a0df918203a99a1ea17571c916f88a71e6ca706395`.
- `INPUTS.sha256` covers 3,218 frozen/prior input files. All 3,218 hashes passed
  before P0 and after the run. Repository `git status --short` and staged-file
  lists are empty at HEAD `0ef8ad3ec22c450e6a9e224f528b48e90a4666b1`.

Exactly one completed multi-route replay was run. An initial invocation failed
before completing a case because JSON-exported facet term lists were used as
sets and RRF ranks were initialized too late. The failure, original script, and
logs are preserved under `failed-startup/`. A synthetic regression test caught
the follow-on RRF issue. The corrected replay script
(`119e27c42ae5da8facd000bce89ba4ab2a43b087a9beb3804a18dec0deebcd9e`) was
frozen before the completed replay and was not edited afterward. Audit/gate
implementation corrections are also documented in `IMPLEMENTATION-ERRATUM.md`;
they did not alter packs, scores, policy, or thresholds.

## Locked policy realized

The implementation follows the task's exact policy:

- unchanged first-occurrence fragment dedupe; entity grouping with original
  fragment rank/pool order retained;
- base top-3/8,192-byte/two-slot reserve using max + 0.15 second + 0.05 third;
- full entity-content TF-IDF top-4/8,192-byte/one-slot reserve, with the feature
  study's tokenizer, stopwords, DF/IDF, and normalization;
- stored metadata top-2/4,096-byte/one-slot reserve, with exact bonuses only for
  CamelCase, snake_case, or backticked/name-like identifiers;
- greedy uncovered-IDF facet top-2/4,096-byte/zero-slot reserve, normalized gain
  used for dynamic scheduling;
- earliest-route primary ownership for slate duplicates;
- independent route wallets, fair rounds, route-score times soft 4/8 KiB
  saturation, then static-priority spillover;
- atomic original/proposal fit against global, item, and active wallet budgets;
- arm A resolver selection/fallback and global four-entity/four-file/1 MiB
  cold-source limits; route slots are never reassigned;
- 24,576-byte/64-item pack cap and zero gaps.

No case ID, gold, expected answer, or oracle ordering entered the blind policy.
Gold was read only by the separate scorer and diagnosis after replay.

## P0 and integrity

P0 passed before the candidate replay:

| Check | Result |
|---|---:|
| Ordered C0 parity | 24/24 |
| Gap cases | 0 |
| Pack status/item/byte failures | 0 |
| R10 exact/all-gold | 3/3, PASS |

Independent audit results:

| Check | Result |
|---|---:|
| Case receipts | 24/24 |
| Case-entity static feature sets audited | 7,840 |
| Scheduling events audited | 10,772 |
| Delivered items audited | 632 |
| Resolver proposals audited | 19 |
| Not-reached candidates audited | 834 |
| Hash/offset/line-column/UTF-8/content/order errors | 0 |
| Proposal containment/cap errors | 0 |
| Wallet/slot/saturation/fair-round errors | 0 |
| Pack/resolver/item/byte/I/O quota errors | 0 |
| Terminal candidate accounting errors | 0 |

`AUDIT.json` and `ROUTE-WALLET-AUDIT.json` are at the run root, with byte-for-byte
copies under `replay/`.

## Paired aggregate results

| Split / arm | All-gold | Union exact | Macro precision | Mean tokens | Bytes | Items |
|---|---:|---:|---:|---:|---:|---:|
| Rust C0 | 2/12 | 7/30 | 0.0353558146 | 7,873.17 | 294,834 | 400 |
| Rust hybrid-v1 | 2/12 | 7/30 | 0.0367379918 | 7,777.33 | 294,837 | — |
| Rust multi-route v1 | **3/12** | **9/30** | **0.0405978826** | **7,737.58** | 294,808 | 333 |
| Rust oracle upper bound | 7/12 | 21/30 | 0.0839807583 | 7,302.00 | 294,820 | 317 |
| C# C0 | 1/12 | 2/25 | 0.0094574071 | 6,596.50 | 294,768 | 305 |
| C# hybrid-v1 | 2/12 | 3/25 | 0.0222587723 | 6,462.08 | 294,715 | — |
| C# multi-route v1 | **2/12** | **3/25** | **0.0325215077** | **6,435.17** | 294,710 | 299 |
| C# oracle upper bound | 3/12 | 5/25 | 0.0805548217 | 6,644.67 | 294,749 | 262 |

Candidate token ratios versus C0 are 0.982779 Rust and 0.975543 C#, both below
the 1.10 ceiling. Candidate all-gold gains are R12 and D04; there are no C0
all-gold losses. R09/R10 and D11 remain all-gold.

## Per-case comparison

`AG/EX` is all-gold / union exact. Values are C0 -> candidate; oracle values are
shown for context only.

| Case | C0 AG/EX | Candidate AG/EX | Oracle AG/EX |
|---|---:|---:|---:|
| R01 | 0/0 | 0/0 | 0/2 |
| R02 | 0/0 | 0/0 | 0/0 |
| R03 | 0/0 | 0/0 | 1/4 |
| R04 | 0/0 | 0/0 | 0/0 |
| R05 | 0/0 | 0/0 | 0/1 |
| R06 | 0/0 | 0/0 | 0/1 |
| R07 | 0/0 | 0/0 | 1/2 |
| R08 | 0/1 | 0/1 | 1/2 |
| R09 | 1/1 | 1/1 | 1/1 |
| R10 | 1/3 | 1/3 | 1/3 |
| R11 | 0/2 | 0/2 | 1/3 |
| R12 | 0/0 | **1/2** | 1/2 |
| D01 | 0/0 | 0/0 | 0/0 |
| D02 | 0/0 | 0/0 | 0/0 |
| D03 | 0/0 | 0/0 | 1/1 |
| D04 | 0/0 | **1/1** | 1/1 |
| D05 | 0/0 | 0/0 | 0/0 |
| D06 | 0/0 | 0/0 | 0/0 |
| D07 | 0/0 | 0/0 | 0/1 |
| D08 | 0/1 | 0/1 | 1/1 |
| D09 | 0/0 | 0/0 | 0/0 |
| D10 | 0/0 | 0/0 | 0/0 |
| D11 | 1/1 | 1/1 | 1/1 |
| D12 | 0/0 | 0/0 | 0/0 |

Full per-case precision, tokens, bytes, items, legacy single-item exact, and all
four arm comparisons are in `COMPARISON.json` and `replay/scores/`.

## Strong gate results

| Gate | Result | Evidence |
|---|---|---|
| P0 control | PASS | 24/24 parity, 0 gaps, R10 3/3 |
| 24/24 integrity | PASS | 0 audit errors |
| R10 3/3 | PASS | 3/3 exact, all-gold |
| No C0 all-gold loss | PASS | Rust lost `[]`; C# lost `[]` |
| Rust all-gold >=4 including R11 | **FAIL** | 3/12; R11 not all-gold |
| C# all-gold >=3 including D04/D11 | **FAIL** | 2/12; D04 and D11 both all-gold |
| Rust union exact >=11 | **FAIL** | 9/30 |
| C# union exact >=4 | **FAIL** | 3/25 |
| Rust macro precision >=C0 | PASS | 0.0405978826 >= 0.0353558146 |
| C# macro precision >=C0 | PASS | 0.0325215077 >= 0.0094574071 |
| Rust tokens <=1.10x C0 | PASS | ratio 0.982779 |
| C# tokens <=1.10x C0 | PASS | ratio 0.975543 |

Any failed gate mandates NO-GO. `GATES.json` is the machine-readable decision.

## Required target diagnosis

The table summarizes exact target delivery and scheduling pressure. Wallet use
is base/content/metadata/facet bytes; resolver slots are base/content/metadata/
facet. Detailed route ranks/scores, wallet before/after at every target event,
slot consumption, saturation, and prior consumers are in
`replay/diagnosis/TARGET-DIAGNOSIS.json`.

| Case | Exact gold | Reserved targets / pool targets | Prior bytes before first target event | Wallet use B/C/M/F | Slots B/C/M/F |
|---|---:|---:|---:|---|---|
| R03 | 0/4 | 1/4 | 7,757 | 7,757 / 7,148 / 332 / 0 | 2/1/1/0 |
| R07 | 0/2 | 1/3 | 7,012 | 3,355 / 7,275 / 241 / 1,888 | 2/1/1/0 |
| R08 | 1/2 | 0/2 | 14,997 | 1,948 / 7,852 / 86 / 3,603 | 2/1/1/0 |
| R10 | 3/3 | 3/6 | 0 | 7,428 / 7,259 / 3,381 / 0 | 2/1/1/0 |
| R11 | 2/3 | 2/2 | 0 | 6,883 / 7,130 / 233 / 0 | 2/1/1/0 |
| R12 | 2/2 | 2/2 | 686 | 7,969 / 7,057 / 1,341 / 3,532 | 2/1/1/0 |
| D03 | 0/1 | 1/2 | 9,197 | 7,382 / 7,696 / 72 / 3,765 | 2/1/1/0 |
| D04 | 1/1 | 2/2 | 6,798 | 6,798 / 7,803 / 641 / 47 | 2/1/1/0 |
| D11 | 1/1 | 2/2 | 0 | 7,619 / 6,937 / 46 / 48 | 2/1/1/0 |

Key mechanisms:

- **R10 preserved:** three target entities were base-reserved and no foreign
  bytes preceded the first target event; all three exact spans were delivered.
- **R11 not rescued:** both target entities were base-reserved and no foreign
  bytes preceded them. The two base resolver slots went to `validate_required`
  and another base entity, so `Validator<'cmd>` received originals only. Its
  rank-10 fragment cost 1,818 bytes, was deferred at base wallet 1,309, then fit
  in spillover at global used 14,246. That raised the entity to 4,414 bytes and
  saturation 0.5. Rank 66, which covers the remaining 624..2114 gold span, reached
  spillover at global used 24,565 with only 11 bytes left and could not fit.
  This is residual same-entity/global starvation plus exhausted base slots, not
  a C0 all-gold regression (C0 also delivered 2/3).
- **R12 rescued:** both target entities were reserved; 686 prior bytes preceded
  them and both exact spans were delivered.
- **D04 preserved from hybrid-v1:** both target entities were reserved and the
  exact `EmployeeUpdateInfo` span was delivered despite 6,798 prior bytes.
- **D11 preserved:** both target entities were reserved with zero prior target
  bytes; the `UpdateEmployeeAttr` span remained exact.
- **R03/R07/R08/D03:** route diversity did not yield enough exact target spans.
  R08 had no target entity in the reserved slate and 14,997 prior bytes; R03 and
  D03 each reserved only one of their target entities; R07 reserved one of three
  and delivered no exact span.

The separate frozen span diagnoser classified first blockers as:

| Split | Delivered | No pool entity | Not anchored before budget | Resolver slot unavailable | No containing declaration |
|---|---:|---:|---:|---:|---:|
| Rust | 9 | 3 | 9 | 6 | 3 |
| C# | 3 | 9 | 3 | 10 | 0 |

## Offline I/O and resources

Content reconstruction used the already-frozen entity byte ranges and a shared
per-file cache. It did not parse source. Preregistered caps were 128 MiB unique
source bytes per case and 256 MiB total; actual total unique content bytes were
6,238,720 (719 files), far below cap. Pack serialization added zero unique reads
because all pack files were already cached.

| Split | Resolver calls/entities | Cold files | Cold bytes read/parsed | Warm bytes | Max cold/case | Proposals accepted |
|---|---:|---:|---:|---:|---:|---:|
| Rust | 48/48 | 42 | 2,828,547 / 2,828,547 | 456,235 | 482,363 | 10 |
| C# | 46/46 | 41 | 1,106,857 / 1,106,812 | 185,382 | 172,234 | 9 |

No resolver I/O quota skip occurred. Every case stayed within four entities,
four files, and 1 MiB cold bytes. Route slot totals were Rust 24/12/12/0 and C#
24/12/10/0 for base/content/metadata/facet.

| Split | Content unique bytes | Content cache-hit bytes | Pack cache-hit bytes | Replay mean / p95 |
|---|---:|---:|---:|---:|
| Rust | 2,429,102 | 217,867,440 | 22,231,067 | 46.33 / 58.09 ms |
| C# | 3,809,618 | 85,800,076 | 9,143,981 | 66.94 / 88.64 ms |

These are offline replay timings, not binary p95. GNU time measured 7.63 s wall,
7.37 s user, 0.16 s system, and 83,060 KiB maximum RSS for the completed replay.
Python's child receipt recorded resolver children at 0.498 s user, 0.051 s
system, and 83,060 KiB maximum RSS. Per-process PSS/swap was not captured and is
not estimated.

Host context immediately before the completed replay: load average
`1.94 2.02 1.88`, 5,092,612 KiB available memory, 143,688 KiB free swap. After all
work: load `2.51 2.21 1.96`, 4,204,988 KiB available memory, 144,400 KiB free
swap. CPU pressure was zero; memory pressure `some avg10` was 0.00 before and
after. Full snapshots are under `logs/resources-*.json`.

## Decision and stop

**NO-GO. Do not port this configuration.** It is a coherent improvement over C0
on precision/token cost and rescues R12/D04, but it fails four mandatory quality
gates and does not rescue R11 or reach the exact-span thresholds. The result
does not authorize tuning, a second variant, production changes, reindexing, or
a runtime p95 campaign.

The authorized task stops here. Any further ranking experiment requires a new
owner decision and preregistration; no next experiment was run.

## Artifact map

- `PREREGISTRATION.md`, `PREREGISTRATION.sha256`,
  `PREREGISTERED-HASHES.sha256`, `INPUTS.sha256`, `FINAL-POLICY.sha256`.
- `IMPLEMENTATION-ERRATUM.md`, `failed-startup/` (preserved startup/audit/gate
  implementation failures; no completed candidate result).
- `scripts/` (replay, independent audit, P0 gate, target diagnosis, gate
  evaluator, resource capture, manifests, eight synthetic tests).
- `p0/` (parity summaries and P0 gate).
- `replay/packs/{rust,csharp}/A/` (24 packs).
- `replay/ledger-{rust,csharp}-A.jsonl`, `replay/rankings/` (all static routes,
  slate owners, policy events, final wallets/slots), `replay/replay-stats-A.json`.
- `replay/scores/`, `COMPARISON.json`, `AUDIT.json`, `ROUTE-WALLET-AUDIT.json`,
  `GATES.json`.
- `replay/diagnosis/` (span and required target diagnoses).
- `logs/` (command stdout/stderr, GNU time, resources, input checks, git state).
- `OUTPUT-MANIFEST.sha256` and its hash receipt.

## Exact reproduction commands

```bash
BASE=/home/masis/.cache/zvec-grep/entity-anchored-span-replay-20261126/multi-route-slate-v1
OLD=/home/masis/.cache/zvec-grep/entity-anchored-span-replay-20261126
ROOT=/home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/final
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$BASE/scripts:$OLD/tools:$ROOT/tools:$ROOT/ranking-decision/union-replay/tools"
export LD_LIBRARY_PATH=/home/masis/.local/lib

python3 "$BASE/scripts/test_policy.py" -v
python3 "$OLD/tools/replay_control.py" --root "$ROOT" --out "$BASE/p0"
python3 "$BASE/scripts/p0_gate.py" --root "$ROOT" --out "$BASE/p0"
python3 "$BASE/scripts/replay_multi_route.py" --root "$ROOT" --out "$BASE/replay" \
  --metadata "$OLD/hybrid-ranking-v1/stored-metadata.jsonl" \
  --hybrid-rankings "$OLD/hybrid-ranking-v1/hybrid/rankings"
python3 "$OLD/tools/score_arms.py" --root "$ROOT" --run "$BASE/replay" --arm A
python3 "$BASE/scripts/audit_multi_route.py" --root "$ROOT" --run "$BASE/replay" \
  --metadata "$OLD/hybrid-ranking-v1/stored-metadata.jsonl"
cp "$BASE/replay/AUDIT.json" "$BASE/AUDIT.json"
cp "$BASE/replay/ROUTE-WALLET-AUDIT.json" "$BASE/ROUTE-WALLET-AUDIT.json"
python3 "$OLD/tools/diagnose.py" --root "$ROOT" --run "$BASE/replay" --arm A
python3 "$BASE/scripts/diagnose_targets.py" --root "$ROOT" --run "$BASE/replay"
python3 "$BASE/scripts/evaluate_gates.py" --run "$BASE"  # expected exit 2: NO-GO
sha256sum -c "$BASE/INPUTS.sha256"
```

The reproduction commands intentionally describe the same single configuration;
they are not authorization to tune or run a variant.
