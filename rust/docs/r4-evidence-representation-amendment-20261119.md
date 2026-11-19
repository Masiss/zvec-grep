# R4 representation preregistration — factual amendment 1 (2026-11-19)

This amendment records execution evidence and a newly found code defect. It
**does not change** the frozen E1 hypothesis, thresholds, inputs, or experiment
quota in `r4-evidence-representation-prereg-20261119.md`. The corrected baseline
is preliminary and is not yet a qualification result.

## Corrected-budget baseline, first successful execution

Raw artifacts (external, immutable for this run):

- Directory: `/home/masis/.cache/zvec-grep/r4-evidence-20261119/receipt-baseline32/attempt-02/`
- Receipt SHA-256:
  `b06a0fb0b231fd59a88806b2dffaaed5db7b1250dc74aa21902b4488381f257d`
- Trace SHA-256:
  `a640758133630dc72ac3e626e7acb6a0bba95856a429672bd8d38dff28fb99f0`
- `/usr/bin/time -v`: wall 2m44.86s, user 162.62s, system 8.17s, peak RSS
  734,564 KiB, process swaps 0. Host before: 19 GiB disk available, 5.4 GiB
  `MemAvailable`; mid-run R4 PSS 632,338 KiB and 4.1 GiB available; after:
  17 GiB disk available, 3.8 GiB `MemAvailable`. Run was read-only against the
  frozen index; no reindex/download/model call.
- Run code head: `0c9ba7ed26fe7c4ccfff4b06165ea85242dc95ab`; index manifest,
  corpus manifest and blind-plan hashes are in `attempt-02/preflight.txt`.

Observed (D01–D12, 25 gold spans): fixed-question hybrid macro 0.5000;
byte-bounded hybrid 0.2500; P1 0.2778; current unplanned R4b 0.3194; current
planned R4 macro **0.4861**, selected **8/25**. All cases obeyed the newly set
`source_reads ≤32` cap (11 cases read 32 distinct candidate files; D08 read 23),
≤6 calls, ≤3 rounds, ≤24,576 claimed pack bytes except for the defect below,
and ≤65,536 claimed tool-content bytes. Citation hash accuracy was 1.000 in
the unplanned R4b arm; planned-arm provenance/byte reconciliation is
unqualified below. The historic 0.5278 was not reproduced under this stricter
source-read cap; it remains a historical result, not the corrected baseline.

The old pool metric counted parent `SearchUnit` overlap: **19/25**. Recomputing
against each *admitted* `ContextItem.content_range` (falling back to exact
`match_ranges` when present) yields only **13/25** fragment-window reachability.
Six parent-only false pool positives are in D02 (1), D05 (1), D06 (2), D07 (1),
and D12 (1). Eight spans were selected by the planned arm. This confirms that
19/25 did not mean 19 gold spans had retrieval evidence; the adapter's parent
range inflated reachability by six. `content_range` is the stored fragment
window, not an exact lexical match position for indexed FTS/vector routes, so
13/25 is explicitly a fragment-window metric, not token-level relevance.

## Newly found selection reconciliation defect — baseline not yet qualified

In `workflow.rs::State::select`, an assembled evidence item is paired with its
`chosen` candidate using only `unit.path == item.relative_path`. When several
selected spans come from one file, later pack items can inherit the first span's
`content_bytes`, anchors, source routes, and facts. The returned line range and
source hash remain independently assembled, but provenance/byte attribution can
be wrong. The D11 trace reports 27,452 selected bytes against the 24,576-byte
cap even though selection's pre-assembly `used_bytes` is bounded; the trace has
three `WorkLogCommand.cs` spans whose `content_bytes` repeat the first selected
candidate. This is consistent with path-only reconciliation and must be fixed
before using the receipt to qualify pack-byte or provenance gates.

The workflow outcome carries selected references/ranges, not source content.
Thus `tool_content_bytes = selected_bytes + gap descriptions` is currently a
**planned-output estimate**, not bytes proven delivered to an external AI agent.
The separate final evidence-returning adapter is not exercised by this paired
harness; the R0 64 KiB *delivered* content gate remains unverified here. Do not
claim external-agent answer quality from these coverage metrics.

## Build and launch setup failures (preserved; no evaluation data)

- Initial build command used Cargo `--offline` but omitted the build-script
  environment `CARGO_NET_OFFLINE=true`; the build script attempted GitHub's
  ONNX Runtime URL and received HTTP 404. Log and SHA are in
  `receipt-baseline32/build-failure.log` and `build-failure.sha256`. No corpus
  evaluation ran.
- The 7.3 MiB cached ONNX archive matched the pinned SHA-256
  `7485c7e7aac6501b27e353dcbe068e45c61ab51fbaf598d13970dfae669d20bf`. The
  failed 1.4 GiB in-worktree Cargo target was stopped and moved, intact, to the
  task-specific external `.../target/`; subsequent builds set `CARGO_TARGET_DIR`,
  `CARGO_NET_OFFLINE=true`, and `ZG_ORT_CACHE_DIR`. The first offline compile
  then found a harness enum mismatch; fixed and rebuilt successfully. The build
  logs and their hashes are preserved.
- The first binary launch had exit 127 because `LD_LIBRARY_PATH` omitted the
  already-built `libzvec_c_api.so`; elapsed time was 0 seconds, no evaluation
  started. Log/time and hash are in `attempt-01/`. `ldd` confirmed the local
  task-target library resolved before the successful run.

## Fixes after the preliminary baseline (not an E1 experiment)

The two code defects identified above are now addressed in the experiment
worktree:

- `State::select` reconciles each assembled evidence item by relative path and
  exact start/end line, reports an `InvalidRange` gap rather than borrowing
  another same-file candidate's metadata, counts `item.content.len()`, and
  guards the assembled pack against `budget.max_bytes`.
- `reference_names` now emits `budget_reference_names` with the number of
  prioritized typed references omitted by the per-round cap, and
  `budget_reference_scan` with the remaining seed spans when its scan cap
  stops examining candidates. No reference is silently dropped.

Regression evidence: the same-file test failed before the mapping change on the
second span's missing FTS provenance; afterward that test passed, and the
workflow suite passed **28/28**. The reference-cap test demonstrated the absent
gap before the fix and passes after it; it checks both 4 omitted names from a
12-name pool and an unexamined seed when a one-round scan reaches its cap. Logs
are in `/home/masis/.cache/zvec-grep/r4-evidence-20261119/regression-mapping/`.
The engine example must be rebuilt with these final fixes, and the bounded
baseline must be reproduced once. The prior baseline receipt remains immutable
and preliminary; only the post-fix receipt can qualify E1.

## Corrected post-fix baseline (measurement correction; not E1)

Receipt and raw trace are preserved at
`/home/masis/.cache/zvec-grep/r4-evidence-20261119/receipt-baseline32/attempt-03/`.
The frozen plan/index/query hashes match the preregistration. This was one
post-fix reproduction after the two code repairs; the earlier receipts remain
unchanged. Results:

- Legacy entity-parent overlap: **19/25**; raw matched-fragment reachability:
  **13/25**; selected: **8/25**, macro **0.4861**, citation verification
  **1.000**. Thus parent overlap overstated actual fragment reachability by
  6 spans; the representation experiment starts from the corrected 8/25, not
  historical 9/25 or the 19/25 pool number.
- All 12 cases passed measured caps: max source reads **32** (11 cases hit
  32; D08=23), calls **6**, rounds **3**, selected pack **24,571 B** (≤24,576),
  workflow source-content-plus-gap estimate **27,417 B** (≤65,536); zero
  violations. This tool-content statistic excludes JSON/tool-envelope bytes
  and any real external-agent delivery, neither of which this harness measures.
- `/usr/bin/time -v`: wall **179.98 s**, user **177.58 s**, system **9.14 s**,
  max RSS **737,776 KiB**, zero process swaps, filesystem inputs **213,704**,
  outputs **9,888**. Pre/post resource snapshots are saved beside the receipt.
- The baseline trace predates explicit serialization of gap maps. The final code
  emits `budget_reference_names` and `budget_reference_scan`; the regression
  verifies both. The paired harness has now been updated to serialize per-case
  `plan_gaps`/`r4b_gaps` for the next run, without rewriting attempt-03.

## Next authorized action: E1

Rebuild the harness, run the pre-registered E1 adapter projection on the same
frozen plan/index/caps by setting `R4B_FRAGMENT_ANCHOR=1`, and preserve the
baseline arm and per-case gap maps. E1 changes only candidate projection from
entity range to returned `content_range`; ranking/search routes, call/round,
file/byte bounds and index remain fixed. Enclosing method/procedure headers are
recovered from the exact fragment anchor; raw trace retains original entity
range/entity ID for provenance. Check all limits and manually audit guard/call
context before comparing to the frozen acceptance: selected ≥12/25 and macro
Recall@10 ≥0.60. If it fails or critical context is lost, stop/revert per the
preregistration; no score/call tuning. No persisted index change or public
MCP/CLI change is in scope.

---

The first launch attempts and the 32-file baseline are retained; no raw evidence
was rewritten or deleted.
