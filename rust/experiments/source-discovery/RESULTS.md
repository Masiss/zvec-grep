# Offline source discovery prototype — results

Date: 2026-11-22  
Base: `fb880c267a41595e4d67d9ab24bf57cb4ecd2e6b`  
Frozen development checkpoint: `6308350`  
Frozen independent validation checkpoint: `e13c35f`

## Decision

**INCONCLUSIVE for product adoption; retain as an opt-in experimental prototype.**
The prototype demonstrates a real offline capability: on partial identifiers
that plain `rg` does not find, it can return parsed declarations and caller
links. Development cases scored 15/15 declarations and 12/12 caller links over
11 valid tasks. The independent set scored 7/8 declarations (one false positive
and one miss) and 7/7 caller links. This is enough to verify the mechanism, but
not enough to support a production-quality claim.

No production default, persisted index, embedding path, or public API changed.
The standalone command is opt-in and supports Rust, Python, TypeScript, and TSX.

## Method and data integrity

- The development corpus and 12 tasks were committed before implementation at
  `6308350`. One task expects `src/worker.py::create_request_context`, but the
  frozen corpus contains only `src/session.rs`; the scorer flags and excludes
  this task from the valid development aggregate. The frozen task was not
  edited after results were viewed.
- The independent corpus and six cases were committed at `e13c35f` before the
  candidate was run against that corpus. This is separate from development,
  though the case author knew the prototype design; it is not a blinded study.
- The first `baseline-rg.json` captures a command-path error: it invokes a
  doubled relative corpus path and records `rg` exit code 2. That file remains
  as provenance and is not used as baseline evidence. `evaluate.py` invokes
  `rg` against the correct corpus path and records those actual outputs in
  `results-20261122.json`.
- One initial semantic workspace search failed while opening the persisted
  FTS index (`open_fts_indexers: open failed`). It was not used by the prototype
  or benchmark, and was not repaired or rebuilt. No model or private source
  was used.

## Approaches compared

- **Literal `rg`:** proves useful for a distinctive substring, but returns
  unclassified matching lines. It missed the frozen partial queries.
- **Identifier token expansion over source text:** can bridge casing and
  separators, but text-only matches still need declaration/call-site
  classification and caller association. The prototype uses token-prefix
  matching only to acquire symbol candidates.
- **Tree-sitter declarations plus syntactic call links:** selected because
  these grammars are already used in the checkout and the approach returns
  definitions with caller context offline. It costs more code and its current
  name-only linking has collision risk.

Fuzzy matching in persistent FTS was not evaluated: the local index failed to
open and the prior readiness record reports a missing local embedding
artifact. No repair or model download was attempted.

## Results

Three subprocess repetitions per query; latency includes process startup and
the `/usr/bin/time` wrapper. RSS is maximum resident set size per subprocess.
These small local corpora are smoke measurements, not representative repository
benchmarks.

| Suite | Tasks | Declaration TP / FP / FN | Caller-name TP / FP / FN | Median latency: `rg` / prototype | Median RSS: `rg` / prototype | Median output bytes: `rg` / prototype |
|---|---:|---:|---:|---:|---:|---:|
| Development, valid | 11 | 15 / 0 / 0 | 12 / 0 / 0 | 6.02 / 3.00 ms | 6,472 / 4,160 KiB | 255 / 249 B |
| Independent validation | 6 | 7 / 1 / 1 | 7 / 0 / 0 | 8.15 / 5.79 ms | 6,472 / 5,614 KiB | 0 / 298 B |

On the four valid development partial-identifier cases, plain `rg -F` returned
zero lines; the prototype found the expected declaration and caller for all
four. On independent partial cases, `rg -F` also returned zero lines. The
prototype found the expected declaration(s) and caller(s) for the account and
widget cases. For `invoiceNo`, it returned `invoice_note`,
`invoice_notification`, and `send_invoice_notification`, missing the expected
`normalize_invoice_number`. This exposes the limits of token-prefix matching:
`No` does not currently expand to `number`, while it prefixes `notification`.

On exact and ambiguous queries, plain `rg` found literal lines (for example six
lines for the `parse` and `cache` development queries) but did not group them by
declaration or caller. The prototype emitted structured entry points and
relationships; its output was smaller for those two ambiguous development
queries. These measures compare CLI text bytes with JSON bytes and are not a
token-cost estimate.

Caller scoring is a smoke metric only: `evaluate.py` flattens caller names
across matched declarations. It does not verify each caller-to-callee edge
against its specific declaration, so 7/7 does not establish 7/7 correct
relationships. The reviewer highlighted this limitation; it keeps the adoption
decision INCONCLUSIVE.

Full per-task outputs, scores, timings, and RSS are in
`results-20261122.json`. Reproduce from the repository root:

```bash
CARGO_TARGET_DIR=/tmp/zvec-source-discovery-target \
  cargo build --offline --manifest-path rust/experiments/source-discovery/Cargo.toml
python3 rust/experiments/source-discovery/evaluate.py \
  /tmp/zvec-source-discovery-target/debug/zg-source-discovery-prototype \
  > rust/experiments/source-discovery/results-20261122.json
```

## Implementation and limits

The standalone Rust CLI scans supported source files, parses declarations and
calls with Tree-sitter, splits camelCase and underscore-separated identifiers,
and returns matching declarations with call sites and simple return-type
producer links. It has no embedding or persistent index dependency. The source
and evaluator total 558 lines; this is materially more code than invoking `rg`.

The reviewer found concrete limits that keep this experimental:

- Caller links are keyed by callee name, not resolved symbol identity. Duplicate
  names in modules/classes can inherit unrelated callers.
- Type producer links compare literal type strings and do not resolve aliases,
  generics, or qualified types.
- The recursive scan does not exclude vendor, generated, or build directories;
  short query tokens can return broad result sets without a cap or ranking.
- The held-out suite has only six tasks and exposed one declaration false
  positive and one false negative. Timing and memory evidence comes from tiny
  synthetic corpora on one host.

The independent reviewer recommended **INCONCLUSIVE** and advised against a GO
claim on the initial artifacts. The review occurred before the corrected
`results-20261122.json` was saved; its source-level caller-resolution and
coverage concerns still apply. There was no second review pass.

## Rollback

This is a standalone experiment; no product route depends on it. To revert the
implementation, evaluator, and results commit, use `git revert <final-commit>`.
To remove the frozen benchmark artifacts as well, revert `e13c35f` and then
`6308350`. Do not reset or discard unrelated pre-existing worktree files.
