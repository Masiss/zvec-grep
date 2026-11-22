# Handoff — offline source discovery experiment

Status: **prototype verified on synthetic corpora; product adoption
INCONCLUSIVE**. Keep the tool experimental and opt-in. No default, index,
embedding, or public API changed.

## Source and checkpoints

- Repo: `/home/masis/src/zvec-grep`
- Branch: `work/source-discovery-autonomous-20261122`
- Starting checkpoint: `fb880c267a41595e4d67d9ab24bf57cb4ecd2e6b`
- Frozen development data: commit `6308350`
- Frozen independent validation: commit `e13c35f`
- Final implementation/results checkpoint: `cd95929`
- Main artifacts: this directory; detailed measurements are in `RESULTS.md`
  and `results-20261122.json`.

The originally named `rust/docs/r4-next-session-handoff-20261121b.md` is absent
from this checkout. Earlier R4 records remain on
`work/r4-evidence-representation-20261119`; they prohibit unrelated private
inference and index changes. This offline prototype did not need them.

## Decision and evidence

- `rg -n -F` missed all tested partial CamelCase/prefix queries in the frozen
  corpora. The prototype returned declarations and caller links for 4/4 valid
  development partial cases and the account/widget validation cases.
- Development: 15/15 declaration TP, 0 FP/FN; caller-name TP/FP/FN = 12/0/0
  across 11 valid tasks. One frozen case expects a missing `worker.py`; it is
  excluded.
- Independent validation: declaration TP/FP/FN = 7/1/1; caller-name TP/FP/FN =
  7/0/0 across six tasks. `invoiceNo` fails to map `No` to `number` and instead
  includes `send_invoice_notification`.
- Median process latency was 3.00 ms prototype vs 6.02 ms `rg` on valid dev
  tasks and 5.79 ms vs 8.15 ms on validation. Median RSS was 4,160 vs 6,472 KiB
  and 5,614 vs 6,472 KiB, respectively. These are tiny fixture measurements.
- The initial `baseline-rg.json` files contain `rg` exit 2 from an incorrect
  doubled relative path. They are preserved as provenance. The evaluator runs
  `rg` on the correct path and its actual outputs are recorded in
  `results-20261122.json`.
- One fresh-context reviewer identified caller name-collision, incomplete type
  resolution, unbounded directory scanning, and thin validation. Review was
  single-pass; no hardening loop followed.
- The reviewer also confirmed the corrected result metrics and flagged that
  caller scoring flattens names rather than verifying caller-to-callee edges;
  7/7 caller-name TP is not a 7/7 relationship-accuracy claim.

## Implementation

`src/main.rs` is an offline Rust CLI using Tree-sitter Rust, Python, and
TypeScript grammars. It extracts function/type declarations and syntactic calls,
matches contiguous prefixes over camelCase/underscore tokens, and serializes
declarations with caller links. `evaluate.py` verifies frozen corpus hashes and
compares actual candidate and ripgrep outputs, bytes, latency, and peak RSS.

Build and run:

```bash
CARGO_TARGET_DIR=/tmp/zvec-source-discovery-target \
  cargo build --offline --manifest-path rust/experiments/source-discovery/Cargo.toml
python3 rust/experiments/source-discovery/evaluate.py \
  /tmp/zvec-source-discovery-target/debug/zg-source-discovery-prototype \
  > rust/experiments/source-discovery/results-20261122.json
```

The `Cargo.lock` is local to the isolated prototype crate. No source/index was
sent externally. One initial semantic workspace search failed while opening
the persisted FTS index; it was not used by the prototype or evaluation.

## Unresolved issues and next work

Do not integrate into production defaults. If the owner chooses to continue,
the smallest useful next experiment is to scope traversal to source directories
and exclude vendor/generated/build paths, then create a separately frozen,
larger corpus with duplicate symbol names and abbreviation pairs. Preserve the
current validation set unchanged. Caller links should be resolved by scoped
symbol identity before any broader adoption claim.

No index repair/rebuild, model download/change, live database access, private
source upload, push, merge, or release was performed. Revert the implementation
and results checkpoint with `git revert cd95929`; remove the frozen benchmark
commits as well by reverting `e13c35f` and then `6308350`.

## C# continuation at `e1f3d9d`

The C# continuation is an offline-only branch of the same standalone prototype.
Current measurements are in `csharp/RESULTS.md`; diagnostic reproductions are
in `csharp/repros/`. Frozen C# data is at `9ee18f8`; earlier implementation
checkpoints are `7723fdc` and `55f8aa2`; final reviewer fixes will be
checkpointed with this handoff. Preserve all frozen cases. The independent
validation case `overload-edges-by-parameter-type` has a source/gold mismatch
(`dynamic` in source, `object` in expected metadata); do not change it.

The final prototype measured 20/20 development and 10/10 independent
caller-to-callee symbol edges with zero false edges. Review reproductions now
fail closed on generic identity, dynamic/unknown arguments, property types,
numeric literal ranges, shadowed type names, and local-function scope. Decision:
Decision: INCONCLUSIVE for product adoption and generalization. The synthetic
suites do not establish real HRM2.0 gains or full C# binding behavior.
