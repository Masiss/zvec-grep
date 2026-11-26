# Fragment coordinate repair — verified pack replay, 2026-11-26

Status: **DONE for coordinate repair; quality remains NO-GO for promotion**.
All work stayed on the experimental branch with env-gated research code. No
held-out data, external evaluator, index rebuild, model change, production
default, or deployment was touched.

## Scope and environment

- Plan executed: `docs/rag/fragment-coordinate-repair-handoff-20261126.md`.
- Branch/worktree: `work/recover-rag-evidence-20261126` /
  `/home/masis/src/zvec-grep-rust-rag-20261126`, start `313e48d`.
- Main worktree remains `51c9eba`; installed CLI remains SHA-256
  `940180c7c74a199c5effbaec604bb69af1a9e9bdfb46881beb6231a838a9be2d`.
- Old receipts preserved under
  `/home/masis/.cache/zvec-grep/selection-recovery-20261126`. New run root is
  `/home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126`, with
  per-stage `final/` receipts. Build target was external to the repo;
  `ORT_DYLIB_PATH=/home/masis/.local/lib/libonnxruntime.so.1` avoided the
  upstream ONNX Runtime 404. Disk/RAM were checked before build (22 GB free,
  ~7 GB available; abort thresholds 8 GB / 4 GB).
- Final experimental binary: SHA-256
  `48855c5ba673d0801331f3ec06f41d8079da3e0f906c4ca31d5ed70f7d4eab8f`.

## Gate 1 — reproduce the defect before patching

Ledger is per selected item, including multiple pieces from one fragment.
Parents were joined by `(path, file_sha256, fragment_id)` using `content_range`
from both stages; there were **no ambiguous parent joins**. Source bytes were
read with hash, offset order, UTF-8 boundary and half-open endpoint checks;
columns were recorded separately. Assembler deduplication was simulated so the
ledger matches pack gaps exactly.

| Path through G1K4 | Rust | C# |
|---|---:|---:|
| Valid input, byte range changed, bad post-selector range | 124 | 20 |
| Invalid input, selector kept byte range | 0 | 60 |
| Invalid input, selector changed byte range | 0 | 41 |
| Pack gaps | 124 stale | 113 stale + 8 invalid |

R10 pack verified directly: 3/3 gold spans delivered with 24 items and 11
gaps. The reproduction gate matched the handoff table exactly. Ledger:
`ledger/baseline-{rust,csharp}.jsonl`, summary `results/GATE-1-REPRODUCTION.json`.

## Gate 2 — clipping repair

Three regression tests were added first and confirmed red (mid-line EOF
endpoint, two slices on the same line, UTF-8 boundary). Root causes:

- `local_end_exclusive` was clamped to `lines.len() - 1`, so the mid-line-end
  branch could never be taken.
- Overlap was computed on whole line numbers, so two disjoint byte slices on
  one line were treated as one blocked line.

`clip_fragment_overlap()` now subtracts the union of already delivered byte
intervals on the same `(path, source_sha256)`, keeps every non-empty remainder
with recomputed half-open source coordinates, preserves the whole fragment when
there is no valid overlap, and charges only the bytes actually delivered.
Ranking order, rank policy and the 24 KiB cap are unchanged.

Byte-level Rust example: in R10 a clipped remainder ending at source byte 3696
previously declared `end_line = next line` while the byte still lay mid-line;
the verifier emitted `stale_coordinates`. It now declares the line containing
the endpoint and the exact byte column.

| G1K4 replay | Rust before | Rust after clip |
|---|---:|---:|
| Pack gaps | 124 stale | **0** |
| Delivered items | 268 | 400 |
| Delivered bytes | 19,773.5 | 25,264.1 |
| Exact / range | 0.208 / 0.417 | 0.208 / 0.417 |
| Precision | 0.0468 | 0.0364 |

R10 still delivers 3/3. Rust clipping gate passed.

## Gate 3 — C# pre-existing source-coordinate defect

After clipping, 86 gaps remained. All 86 were UTF-8 BOM files with LF endings;
every one had declared `start_byte_offset` exactly **3 bytes smaller** than the
offset implied by its declared line/column. Other BOM encodings and
CRLF/CRCRLF cases were absent from the failing set.

Byte-level C# example: `EmployeeAuthOne.cs` entity origin recorded
`byte_offset 4382, line 118, byte_column 8`. Raw source byte 4382 is line 118,
column 5; source byte 4385 is column 8. Index extraction used the
BOM-stripped decoded text for byte offsets but kept source line/column, so all
post-BOM offsets were short by the 3-byte UTF-8 BOM.

The first wrong step in principle is extraction/storage; changing stored
offsets would require reindex, which this task does not authorize. Instead,
the query-time conversion in `context_item_target()` now reads the source BOM
and shifts entity byte offsets by 3 for UTF-8 BOM files. It also shifts the
column when the endpoint is on line 1 to preserve `TextRange` invariants.
Other BOM types are left unchanged so the verifier fails closed.

| G1K4 replay | C# before clip | after clip | after BOM fix |
|---|---:|---:|---:|
| Pack gaps | 121 | 86 | **0** |
| Delivered items | 179 | 219 | 305 |
| Delivered bytes | 13,637.3 | 15,414.1 | 26,132.1 |
| Exact / range | 0.083 / 0.194 | 0.083 / 0.194 | 0.083 / 0.194 |
| Precision | 0.0260 | 0.0209 | 0.0093 |

All 305 selected C# items and all 400 Rust items now pass the byte verifier;
remaining gaps by cause: **0**. Quality (coverage/precision) is a separate
verdict from coordinate correctness.

## Gate 4 — verified-pack grading and R03 decision

Packs were graded directly from `items[].content` after hash/byte checks; the
historical K0 trace proxy is not treated as the same surface. Direct-pack
final macro results: Rust exact 0.2083 / range 0.4167 / precision 0.0375 /
24,569.5 bytes; C# exact 0.0833 / range 0.1944 / precision 0.0099 / 24,564.0
bytes. The same-surface trace analyzer gives Rust precision 0.0364 and C#
0.0093. Cases with all gold spans covered: in pool 4 Rust / 2 C#; by one
fragment 1 Rust / 2 C#; in delivered pack 2 Rust / 1 C#.

R03 span ledger (all four spans present in the ranked pool):

| Gold span | First full rank | Selector decision | Bytes used before | Pack exact |
|---|---:|---|---:|---|
| `parser.rs:1621-1624` | 111 | `dropped_budget` (cost 161) | 24,574 | no |
| `command.rs:1004-1012` | 264 | `dropped_budget` (cost 1,810) | 24,574 | no |
| `help.rs:9-39` | 111 | `dropped_budget` (cost 1,022) | 24,574 | no |
| `error/mod.rs:362-370` | 320 | `dropped_budget` (cost 1,800) | 24,574 | no |

Decision: **ranking**, not selection. Gold-bearing fragments enter the ranked
pool but only at ranks 111–320; the rank-order byte-budget selector has already
spent 24,574 of 24,576 bytes before reaching them. There is no overlap/clipping
cause for R03. A new ranking arm (not a selection arm) is the evidence-backed
next experiment. This decision is diagnostic on dev data only; no new arm or
promotion is authorized by this report.

## Rank-loss measurement and decision (follow-up)

A bounded aggregate over all 24 dev cases was computed from the final frozen
receipts (`final/ranking-decision/`). For each gold span it records the first
ranked fragment fully containing the span, the bytes already delivered before
that rank, and whether the span was only partially represented or absent from
the pool.

| Metric | Rust | C# |
|---|---:|---:|
| Gold spans | 30 | 25 |
| Full-containing fragment in pool | 16 | 4 |
| Partial-only in pool | 8 | 13 |
| Absent entirely from pool | 6 | 8 |
| Present in pool but not exact in pack | 11 | 3 |
| First full rank, mean / max of missing spans | 125.7 / 320 | 167.7 / 262 |
| Bytes delivered before that rank, mean | 23,230 | 24,545 |
| Cases fully ranking-rescuable | 2 (R03, R08) | 1 (D04) |
| Perfect-rerank exact ceiling | 0.528 | 0.222 |

R03 is a real ranking-by-budget case (ranks 111/264/111/320, 24,574 bytes
already spent), but only 2 Rust and 1 C# cases are fully rescuable by
reordering. Since 22/30 Rust and 21/25 C# spans have no full-containing
fragment in the pool, the dominant bottleneck is representation/discovery, not
priority. Decision: **do not authorize the proposed signature/body ranking
arm now**. A representation change may require index/schema work and needs a
separate owner decision. Quality remains NO-GO; the 0-gap and R10 3/3
regressions are unchanged.

## Offline union-byte pack replay (follow-up)

With index, model, ranker and the 24 KiB cap fixed, the ranked pool was replayed
offline with pack assembly replaced by a byte union per `(path, file_sha256)`.
Exact was graded on that same byte surface. Cases with every gold span
DELIVERED stayed at **2/12 Rust and 1/12 C#**; newly rescued cases: **0 in both
languages**. Span-level exact rose from 5 to 7 in Rust and stayed at 2 in C#,
but no case crossed the all-gold boundary. Decision: **do not change pack
assembly to join fragments**.

Tracing spans still missing bytes: 13 Rust and 20 C# spans are lost at raw
retrieval/representation, 10 Rust and 3 C# at pack union budget, and none at
fusion/load/rank cutoff. R03's four missing spans are all `pack_union_budget`.
The next investigation, if authorized, is raw discovery/cutoff before any
reindex discussion. Raw receipts:
`final/ranking-decision/union-replay/results/`.

## Verification and reproduction

- Focused context tests: **16 passed, 0 failed**; evidence tests: **11 passed,
  0 failed**; `cargo fmt --all -- --check` clean.
- Normal-search parity: **6/6 sampled Rust/C# cases byte-identical** against the
  K0 baseline with all RAG env switches unset.
- No task-correctness/blind-evaluator run and no held-out access.
- Full engine suite was not rerun in this task; the previously recorded 10
  failures remain environment failures caused by `~/.zvec-grep` being a symlink.

```bash
W=/home/masis/src/zvec-grep-rust-rag-20261126
R=/home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126
cd "$W/rust"
ORT_DYLIB_PATH=/home/masis/.local/lib/libonnxruntime.so.1 \
CARGO_TARGET_DIR="$R/target" TMPDIR="$W/.tmp" cargo build -p zg --release
"$R/final/run-final.sh"
PYTHONPATH="$R/final/tools" python3 "$R/final/tools/grade_packs.py" \
  --root "$R/final" --out "$R/final/grades"
```
