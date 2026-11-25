# Fresh-context reviewer findings

Reviewer: `/root/real_csharp_reviewer` (read-only; no code edits or long runs).

- Verified all frozen SHA-256 values against `freeze.json`.
- Confirmed the four Roslyn gold sets total 168 bound call sites: 61 `UpdatePseudoClasses`, 13 `SnapshotUndoRedo`, 7 `CreateBitmapsAndColorMap`, and 87 `SetAt`. Candidate emits zero edges. Unresolved records match all 168 gold sites by path suffix, line, and callee name; there are no extra unresolved records for the queried callee names. There are no false emitted edges; edge precision is undefined for an empty prediction set.
- Confirmed the scan-wide guard in `src/csharp.rs` clears definite edges when any file is skipped or the scan is truncated. The selected corpus has 44 parse failures, so this guard suppresses otherwise resolved edges across the corpus.
- Confirmed declaration misses: one output path each for partial types whose frozen gold has 2, 7, and 2 paths (3/11 locations).
- Confirmed five `SnapshotUndoRedo` calls are valid without an argument because the method’s Boolean parameter is optional; candidate marks them `no_matching_arity`. This demonstrates missing optional-argument handling.
- Assessed Roslyn as useful for these concrete bound call sites, with explicit limits: no official project properties, package refs, generators, or target-specific compilation setup. Generalization beyond the selected Avalonia corpus is INCONCLUSIVE.
- Independent recommendation: `NO_GO` for adopting the current C# edge/declaration output as a useful finder.

## Supplemental partial-method suite

- Verified frozen input hashes and candidate output arrays against their recorded artifacts.
- Method identity scoring is 3/24; exact raw Roslyn display strings need type qualification normalization. The selected targets have no same-type overload collisions under `(containing type, exact method name)`.
- Candidate emits 0/106 edges. Only 22/106 gold sites appear as unresolved, split 7/14, 13/14, and 2/78 across `CreateBitmap`, `SnapshotUndo`, and `UpdatePseudo`.
- Three other unresolved records (`Snapshot`, `Set`, `Set`) are outbound calls from matched methods. They are outside the narrow target-callee gold and should be reported as unresolved output noise, not false edges.
- Independent conclusion remains NO_GO for the current prototype; generalization beyond this corpus is INCONCLUSIVE.
