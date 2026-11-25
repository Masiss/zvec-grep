# Real-world C# discovery validation

Date: 2026-11-22

## Decision

**NO_GO for experimental integration of this Tree-sitter resolver on multi-project repositories.** It finds partial type identities with useful compact output, but the concrete run fails every tested caller edge, reports every gold edge call site as unresolved, takes seconds per query, and truncates the full `src` tree. `rg` is tens of milliseconds and Roslyn is already available locally for semantic binding. Retain only as a narrow offline experiment; use `rg` for lexical discovery and Roslyn/project-aware tooling for caller relationships.

## Frozen evaluation

Public Avalonia source at commit `debc815ba9dff30a283135b1764e5fdfb609bb91`, copied to `/tmp/avalonia-csharp-eval`; four complete projects, 1,959 `.cs` files / 11.19 MB. SHA-256 source manifest matched all files. Repo `src` contains 2,965 C# files, exceeding the prototype 2,500-file limit. Tasks, expected outputs, Roslyn oracle, and literal case-insensitive `rg` outputs were frozen at HEAD `53372b18ba035ac83fa76d0fdad395bf2c0cb2fd` before candidate runs; `freeze.json` stores SHA-256 hashes because `.git` is read-only in this execution sandbox. These public-corpus tasks are the independent validation set, separate from the earlier synthetic development diagnostics; the synthetic results were not used to tune this candidate evaluation.

The validation has 3 partial-type identity tasks and 4 edge tasks. The type gold spans 11 source declarations across 3 types (AutoCompleteBox 2 files, Dispatcher 7, CalendarDatePicker 2). Roslyn bound 168 exact call sites across `UpdatePseudoClasses` (61), `SnapshotUndoRedo` (13), `CreateBitmapsAndColorMap` (7), and `SetAt` (87).

## Per-task actual outputs

| Task | Prototype ms | `rg` median ms | Prototype bytes | `rg` bytes | Prototype declarations / edges / unresolved | Roslyn gold edges TP/FP/FN |
|---|---:|---:|---:|---:|---:|---:|
| partial_autocomplete | 5719 | 14.84 | 923 | 32,314 | 3 / 0 / 0 | type IDs: 1 found, 2 extra |
| partial_dispatcher | 5805 | 13.83 | 498 | 118,917 | 1 / 0 / 0 | type IDs: 1 found |
| partial_calendar_picker | 6985 | 13.53 | 543 | 15,249 | 1 / 0 / 0 | type IDs: 1 found |
| edges_update_pseudo | 8945 | 14.47 | 136,329 | 11,766 | 20 / 0 / 124 | 0/0/61 |
| edges_snapshot_undo | 7916 | 13.41 | 4,243 | 1,659 | 1 / 0 / 14 | 0/0/13 |
| edges_color_map | 11504 | 13.85 | 100,148 | 1,269 | 1 / 0 / 31 | 0/0/7 |
| edges_set_at | 19076 | 14.21 | 24,063 | 11,482 | 5 / 0 / 87 | 0/0/87 |

Edge scoring uses exact Roslyn-bound `(path, line)` call sites for the queried method names. The candidate emitted **0 edges**: TP 0, FP 0, FN 168; precision is undefined (no predicted edges), recall 0%. It returned the same 168 gold call sites as unresolved (100% unresolved on gold edge sites). The outputs contain 88 other unresolved records from matched caller contexts; there are no extra unresolved records with the queried callee names. Reasons include unknown receiver/argument types, unsupported local-function scope, and incomplete scan. On the selected slice, 44 of 1,959 files were skipped for parse errors; the implementation suppresses all definite edges when files were skipped. This is conservative but makes its relationship output unusable on this repository.

For partial types, the prototype returned the targeted type identity for all 3 tasks (identity recall 3/3). The `AutoComplete` query also returned two valid prefix-matching identities outside that task’s target. It reported one representative source path per merged type, covering only 3 of the 11 expected partial declaration locations (27%). `rg -i` produced 86–811 lines per type query; the prototype returned 1–3 declaration records and substantially smaller output. This is its demonstrated structured benefit.

## Supplemental partial-method identifier suite

To cover method discovery from partial identifiers, I froze a separate suite before running these three candidate queries. Its targets are deterministic prefixes from the earlier frozen edge-task names (`CreateBitmap`, `SnapshotUndo`, `UpdatePseudo`); the Roslyn declaration identities and bound caller sites were frozen first. Exact task, oracle, baseline, hashes, and outputs are in the `partial-methods-*` artifacts.

| Prefix query | Roslyn method identities | Candidate methods TP/FP/FN | Roslyn-bound call sites | Candidate edges TP/FP/FN | Gold sites unresolved | Prototype / `rg` median ms |
|---|---:|---:|---:|---:|---:|---:|
| CreateBitmap | 2 | 1/0/1 | 14 | 0/0/14 | 7/14 | 14869 / 11.59 |
| SnapshotUndo | 1 | 1/0/0 | 14 | 0/0/14 | 13/14 | 11598 / 10.14 |
| UpdatePseudo | 21 | 1/0/20 | 78 | 0/0/78 | 2/78 | 11916 / 11.37 |

Across the three prefixes, the candidate found 3/24 target method identities (100% precision, 12.5% recall). It emitted 0/106 definite caller edges; only 22/106 expected call sites appeared as unresolved (20.8%). `UpdatePseudo` returned only `TextBox.UpdatePseudoclasses` from 21 expected method identities. The prefix queries took 11.6–14.9 seconds versus 10.1–11.6 ms for `rg`; peak RSS was 94.9–95.9 MiB versus 7.1–7.6 MiB for `rg`. Structured output was 3.1–4.8 KB versus 1.7–11.8 KB for `rg`. This directly confirms weak method discovery from partial identifiers and incomplete caller coverage. Three additional unresolved records (`Snapshot` at `TextBox.cs:2674` and two `Set` calls at lines 2648–49) are outbound calls from matched methods, not calls to the queried targets; they are output noise, not false definite edges.
## Resources and comparison

- Prototype selected-slice query time: 5719–19076 ms. `rg -i` medians: 13.41–14.84 ms (about 400–1,400× faster for these tasks). Prototype output for some edge queries was 2–79× larger than `rg` because of unresolved records.
- Full-repository `src` scan: 19.63 s, 143,176 KiB peak RSS; 2,500 files found, 2,446 parsed, 54 parse errors, `truncated=true`. It returned no edges.
- `rg -i SetAt`: 0.02 s, 7,428 KiB peak RSS, 95 lines.
- Roslyn oracle loaded/bound 33,711 invocation sites, 30,888 bound (91.6% overall), and emitted 1,868 error diagnostics in the reduced project context without project properties, package references, analyzers, or generators. The 168 edge gold sites were all concretely bound. This is a limited but valid reference for those exact call sites, not a full Avalonia build or general compiler accuracy claim.

## Reviewer, source diff, limits

Fresh-context reviewer: **NO_GO** for adopting the current output as a useful finder; broader generalization beyond this Avalonia corpus remains INCONCLUSIVE. The reviewer independently verified freeze hashes and edge-site scoring. It confirmed 168/168 gold call sites were reported unresolved, with no extra unresolved records for the queried callee names and no false emitted edges (edge precision is undefined because no edges were emitted). It also confirmed the 3/11 partial-location coverage and identified five valid `SnapshotUndoRedo` calls with an omitted optional Boolean argument mislabeled `no_matching_arity`. This supports the measured NO_GO decision.

Prototype code did not change during this evaluation. Since frozen C# test checkpoint `9ee18f8`, the existing prototype diff adds 1,180 lines in `src/csharp.rs`, plus 1 line in `Cargo.toml` and 11 in `Cargo.lock` (1,192 lines total). No additional framework or persistent index was introduced.

Validity/generalization limits: one public repository, four selected projects, one host, one query per task for prototype timing; `rg` latency repeated three times. The selected source slice is below the scan cap but 44 parse failures remain. Roslyn uses direct source compilations instead of official `.csproj` builds. These limits do not alter the observed 0/168 definite-edge result, but broader repositories/tasks could change declaration utility or the mix of unresolved reasons. No evidence justifies carrying the resolver complexity into product integration.

## Artifacts and rollback

- Frozen tasks/gold: `tasks-frozen.json`; source revision/hashes: `corpus-manifest.json`, `freeze.json`.
- Actual `rg` baseline and latency: `baseline-rg.json`, `rg-latency.json`.
- Roslyn reference output: `roslyn-oracle.json`.
- Candidate outputs and measurements: `candidate-outputs/*.json`, `candidate-measurements.json`; supplemental method-prefix outputs and scores: `partial-method-candidate-outputs/*.json`, `partial-method-measurements.json`, `partial-method-rg-resources.json`, `partial-method-scoring.json`.
- Prototype source diff: `prototype-source-diff.patch` and `prototype-source-diff-stat.txt` (the exact diff from `9ee18f8` to `53372b1`).
- Rollback checkpoints: revert `9cf8eea`, `55f8aa2`, `7723fdc`, and `9ee18f8` in that order (revert documentation-only handoff `53372b1` first if rolling back the whole continuation). The C# implementation is isolated from production defaults/API/index/RAG benchmarks.

No repository source was changed for this validation; no private source, remote inference, persistent index, push, merge, or release was used.
