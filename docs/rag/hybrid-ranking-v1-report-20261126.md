# Hybrid-ranking-v1 offline replay report

Run ID: `entity-anchored-span-replay-20261126/hybrid-ranking-v1`.
Repository branch/HEAD remained `work/recover-rag-evidence-20261126` @
`bb6821487cc07ee4375e6907d05b1eedc8acb8ee`; the worktree is clean and has no
staged files. Nothing in the repository, production runtime, index, model,
corpus, gold, or held-out data was changed.

## Verdict

**GO for the preregistered offline replay gates only.** No C0 all-gold case was
lost, R10 remains 3/3, C# D04 is newly all-gold, macro precision improved in
both splits, mean full tokens decreased in both splits, and integrity passed.
This is **not** production/deployment approval and does not establish a runtime
p95 result. No second configuration was run.

## Parent independent review

**PASS for the frozen offline replay gates only.** The parent independently:

- verified `INPUTS.sha256`, `BASELINES.sha256` and `MANIFEST.sha256` with zero
  failures;
- confirmed frozen index content hashes, sizes and mtimes were identical
  before/after/final;
- reran all four synthetic policy tests and the 24-case integrity/policy audit
  (630 items, 27 proposals, 24 dynamic event sequences) with zero errors;
- recomputed D04 gold coverage directly from source bytes: C0 has no covering
  interval, hybrid covers `EmployeeCommand.cs` `24119..24525` exactly;
- confirmed the repository worktree remained clean.

Review limitations: one new all-gold case (C# D04), no new Rust all-gold case,
R11 remains 2/3, and there is no held-out, production integration or binary p95
evidence. The policy is therefore accepted as a research candidate, not a
production ranker.

## Policy and metadata access

The single policy used fragment relevance as the anchor: per-entity maximum
score, plus `0.15 * second + 0.05 * third`; metadata bonus capped at 0.40 from
exact case-insensitive symbol-name match, symbol-token coverage and signature
coverage. Static priority was `anchor + support + bonus`. Before every
candidate, the remaining entity with highest `static_priority *
saturation_factor` was chosen, then its best remaining fragment by original
rank. Saturation was 1.0 below 4,096 accepted entity bytes, 0.5 at 4,096–8,191,
and 0.25 at 8,192+, with no hard entity cap. The exact tokenizer, tie-breaks,
null handling and all weights are frozen in `PREREGISTRATION.md` (SHA-256
`434f156fac9d764f064b7bc406b21f2cbfedf715c045cf660a04f0024c9075e8`).

Stored metadata was read directly from the frozen indexes with a read-only
`zvec-rust 0.8.0` helper; no source outline, reindex or parse fallback was used.
All 3,492 distinct pool entities were fetched: 3,440 had stored code metadata
and 52 had stored null metadata, which received zero bonus. The export took
629.045 ms, used 0.58 s user + 0.04 s system CPU, peaked at 59,012 KiB RSS, and
contained 1,106,393 serialized metadata bytes. Every index file's content hash,
size and mtime matched before/after export and again after the full run.

Arm A resolver selection, clipping, dedupe, fallback, 24,576-byte/64-item cap,
4-entity/4-file/one-proposal-per-entity limits, 4,096-byte proposal cap and
1 MiB cold-I/O quota were unchanged. The reranker saw no gold, case ID,
expected answer or oracle order.

## P0 and integrity

- Unmodified arm A control: **24/24 ordered parity**, zero mismatch kinds.
  Rust: 400 items / 294,834 bytes; C#: 305 items / 294,768 bytes.
- Frozen C0 R10: **3/3 exact, all-gold**; parity makes the unmodified replay
  equivalent to that C0 pack.
- Hybrid receipts: **24/24 packs**, 630 items, 27 accepted proposals, zero
  source-hash/bounds/UTF-8/content errors, zero gaps, zero proposal containment
  or proposal-cap errors.
- Independent audit recomputed every static feature and all 24 dynamic event
  sequences from the frozen pool/metadata and actual accepted pack bytes:
  **0 errors**. It also checked ledger candidate coverage, byte accounting,
  item/byte caps, entity/file resolver quotas and cold-I/O quotas.
- Resolver totals: Rust 48 calls / 48 entities, 2,103,409 cold bytes,
  1,267,276 warm bytes, maximum 437,381 cold bytes in one case; C# 48 calls /
  48 entities, 1,276,647 cold bytes, 308,753 warm bytes, maximum 226,467 cold
  bytes in one case. Zero I/O quota skips; at most four entities per case.
- Maximum accepted bytes from one entity: 7,549 Rust and 5,984 C#. This is a
  measured outcome, not a hard cap.
- `INPUTS.sha256`: 1,538 checks, zero failures. Frozen index hashes/stat snapshots
  remained identical. `BASELINES.sha256` freezes all 24 C0 packs and prior
  oracle/failed-v1 comparison summaries.

## Paired results

| Split | Metric | C0 | Hybrid | Oracle upper bound |
|---|---|---:|---:|---:|
| Rust | all-gold cases | 2/12 | 2/12 | 7/12 |
| Rust | gained / lost | — | none / none | R03,R07,R08,R11,R12 / none |
| Rust | union exact spans | 7/30 | 7/30 | 21/30 |
| Rust | legacy single-item exact | 5/30 | 6/30 | 15/30 |
| Rust | macro precision | 0.035355815 | 0.036737992 | 0.083980758 |
| Rust | mean full tokens | 7,873.17 | 7,777.33 | 7,302.00 |
| Rust | bytes / items | 294,834 / 400 | 294,837 / 351 | 294,820 / — |
| C# | all-gold cases | 1/12 | 2/12 | 3/12 |
| C# | gained / lost | — | D04 / none | D03,D04 / none |
| C# | union exact spans | 2/25 | 3/25 | 5/25 |
| C# | legacy single-item exact | 2/25 | 3/25 | 4/25 |
| C# | macro precision | 0.009457407 | 0.022258772 | 0.080554822 |
| C# | mean full tokens | 6,596.50 | 6,462.08 | 6,644.67 |
| C# | bytes / items | 294,768 / 305 | 294,715 / 279 | 294,749 / — |

Token ratios versus C0 are 0.987828 Rust and 0.979623 C#, both below 1.25.
Precision deltas are +0.001382177 Rust and +0.012801365 C#. The hybrid captures
one of the two C# oracle gains and none of the five Rust oracle gains. The
oracle is gold-aware and is only an upper bound, not a deployable policy.
The failed gold-blind RRF+path v1 had lost R10 and D11; this policy loses
neither.

D04's gain is concrete: the exact-metadata `EmployeeUpdateInfo` entity
(original fragment rank 119, static priority 0.620746) was admitted after soft
saturation lowered earlier broad entities. Its original 406-byte fragment at
24,119–24,525 covers the gold span exactly. The resolver fell back with
`entity_quota`; no structural expansion was involved. C0 delivered 0/1; hybrid
delivered 1/1. This corrects the subagent's original mechanism description;
the measured metrics and hashes are unchanged.

## Target diagnosis

### R10 — preserved 3/3, broad-entity starvation removed

All three C0 target fragments were reached with ample budget:

| C0 rank | processing position | used before | remaining | original cost | outcome |
|---:|---:|---:|---:|---:|---|
| 1 | 1 | 0 | 24,576 | 1,817 | original fallback `no_boundary`; gold delivered |
| 12 | 4 | 5,376 | 19,200 | 1,786 | original fallback `no_boundary`; gold delivered |
| 28 | 7 | 10,232 | 14,344 | 1,765 | original fallback `no_boundary`; gold delivered |

Before rank 28, broad entity `0000002f609…` had consumed 5,376 bytes and
`0000002fc35c…` 4,856 bytes, not the failed v1's 24,148 bytes. The broad entity's
static priority was 1.513533 (including 0.375 metadata bonus); after accepted
bytes crossed 4,096 its factor dropped to 0.5, allowing other entities before
it without a hard cap. Final broad-entity bytes were 7,549.

### D11 — target reached first, prior starvation removed

The `UpdateEmployeeAttr` rank-1 target was processing position 1: used before 0,
remaining 24,576, original cost 689. Stored metadata gave an exact symbol match
and static priority 1.135279. Arm A accepted the containing method at bytes
35,152–35,841 (689 bytes); no entity consumed bytes before it. Hybrid remains
all-gold 1/1, whereas failed v1 arrived with only 97 bytes remaining.

### R11 — coherence improved, but one gold span still starved

The C0-delivered target at rank 10 was reached at processing position 9 with
used before 6,883, remaining 17,693 and cost 1,818. Its original was accepted
via `entity_already_attempted` fallback; the same entity had already contributed
1,976 bytes. No hard entity cap was hit (final same-entity bytes 4,969).

However, the fragment that would complete the remaining gold span 624–2114 is
rank 66 (range 451–2271, clipped cost 1,536). It was reached only after the pack
was full: used before 24,575, remaining 1, `original_not_fit`. By then the two
validator entities had 4,969 + 4,713 bytes, while all other entities consumed
14,893 bytes, led by `debug_asserts.rs` 4,525, `_concepts.rs` 1,887,
`_tutorial.rs` 1,879, `_derive/_tutorial.rs` 1,862 + 1,488. Thus R11 stays 2/3
exact and not all-gold, although legacy single-item exact improved from 0 to 1.
The oracle's R11 gain was not reproduced.

## Exact commands

```bash
BASE=/home/masis/.cache/zvec-grep/entity-anchored-span-replay-20261126/hybrid-ranking-v1
FINAL=/home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/final
OLD=/home/masis/.cache/zvec-grep/entity-anchored-span-replay-20261126
DEPS=/home/masis/.cache/zvec-grep/rust-rag-20261126/build/target/debug/deps
RUSTC=/home/masis/.cache/zvec-grep/selection-recovery-20261126/test-home/.rustup/toolchains/1.98.0-x86_64-unknown-linux-gnu/bin/rustc
export PYTHONPATH="$OLD/tools:$FINAL/tools:$FINAL/ranking-decision/union-replay/tools"
export LD_LIBRARY_PATH=/home/masis/.local/lib

"$RUSTC" --edition=2021 -C opt-level=2 \
  --extern zvec_rust="$DEPS/libzvec_rust-172bc00cbdfa1982.rlib" \
  --extern serde_json="$DEPS/libserde_json-55ebe675c32b9bd6.rlib" \
  -L dependency="$DEPS" -L native=/home/masis/.local/lib \
  -o "$BASE/metadata-export" "$BASE/metadata_export.rs"
/usr/bin/time -v "$BASE/metadata-export" "$FINAL" "$BASE/stored-metadata.jsonl"

python3 "$OLD/tools/replay_control.py" --root "$FINAL" --out "$BASE/p0"
python3 "$BASE/replay_hybrid.py" --root "$FINAL" --out "$BASE/hybrid" \
  --arm A --metadata "$BASE/stored-metadata.jsonl"
python3 "$OLD/tools/score_arms.py" --root "$FINAL" --run "$BASE/hybrid" --arm A
python3 "$OLD/tools/diagnose.py" --root "$FINAL" --run "$BASE/hybrid" --arm A
python3 "$BASE/analyze_targets.py"
python3 "$BASE/audit_hybrid.py"
python3 "$BASE/summarize_results.py"
(cd "$BASE" && sha256sum -c INPUTS.sha256 && sha256sum -c BASELINES.sha256)
```

The scorer's hardcoded `count_tokens_full` binary and Potion tokenizer paths
were unchanged. The replay process took 2.23 s wall, 2.03 s user + 0.07 s system
CPU, peak RSS 58,432 KiB; scoring took 10.52 s wall and peak RSS 100,732 KiB.
These are local replay measurements, not a binary p95 campaign. Host load after
validation was 2.20/2.05/1.85 with 4.2 GiB available memory and 22 GiB free disk;
full after-state is in `logs-resource-after.txt`. An immediate pre-run host
snapshot was not preserved as a receipt, so no host-controlled causal claim is
made.

## Artifacts and limits

Key evidence: `PREREGISTRATION.md`, `INPUTS.sha256`, `BASELINES.sha256`,
`stored-metadata.jsonl`, `hybrid/replay-stats-A.json`, both hybrid ledgers,
`hybrid/rankings/*.json`, 24 packs under `hybrid/packs`, `hybrid/scores`,
`hybrid/diagnosis`, `hybrid/ledger-forensics`, `AUDIT.json`, `GATES.json`,
`PAIRED-COMPARISON.json`, command logs and `MANIFEST.sha256`.

No blockers remain for interpreting this offline run. Residual limits: one
frozen 24-case replay, no repeated-run uncertainty estimate, no production
integration, no held-out evaluation, and no binary p95 campaign. R11 remains a
known failure. Stop here as instructed; do not tune or launch another variant.

**Exact next recommendation:** obtain owner authorization for a separate,
independently reviewed preregistration before any production port. That effort
should preserve this policy verbatim, add ranker/selector regression tests, and
only then run a paired runtime/latency validation including a real binary p95
campaign. Do not tune weights or use R11/gold labels to modify this frozen
configuration.
