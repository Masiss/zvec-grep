# R4 evidence representation investigation — preregistration (2026-11-19)

Status: **PRE-REGISTERED; no representation experiment run yet.** This is an
experimental worktree branched from `work/r4-deep-opt-20261118` at
`0c9ba7ed26fe7c4ccfff4b06165ea85242dc95ab` (which includes requested starting
commit `7030bd7` plus the later handoff/index commit). No production or held-out
asset is in scope.

## Frozen inputs

- Development cases: `benchmarks/rag-impact/cases/dev.json` (D01–D12, 25 gold
  spans); gold is used only by the offline evaluator, never by the workflow.
- Corpus manifest SHA-256:
  `c706c8dd9b560572a2c1739c2c2bda676982f4c3b5da4cd4b32bcfeb66359cdb`.
- Blind plan: `benchmarks/rag-impact/results/r4b-blind-plan-20261117.json`,
  SHA-256 `e8a33dde61c6b661d87128f70d01999add8fab1ebf33be67815a78754bb1dba7`.
- Isolated corpus/index:
  `/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus`; index v3,
  generation `cea13512-733a-4c9f-96a7-5e131b57d1dc`, local
  `potion-code-16m-v2`, 822 files. Index manifest SHA-256
  `f6f9695a9a4cf2f42ffbd36d13eed5110fe1c61a99be18508e8b7ee77ec05917`.
- Frozen gold and protocol: `benchmarks/rag-impact/cases/dev.json`,
  `benchmarks/rag-impact/REPORT-CONTRACT.md`, and
  `rust/docs/r4-task-correctness-contract-20261118.md`; none will be edited.
- Historical best receipt: `benchmarks/rag-impact/results/r4b-paired-20261116.json`
  (`head=720c1ed…`), with current code revision and the historic case trace at
  `r4-evidence-trace-20261116.json`.

## Gate A findings motivating a measurement correction

Source inspection shows a stage boundary not recorded in the old trace:

1. C# extraction emits separate declaration entities for containers and
   members; each entity owns source-backed fragments. Indexing creates FTS text
   and vector input per fragment. The index-truth audit independently verified
   the method declarations and ranges.
2. Search internally associates fragment hits with an entity ID and returns
   `SearchHit.evidence` containing fragment selectors. Context projection sets
   `ContextItem.range` to the whole entity, while `content_range`/`excerpt_range`
   identify the retrieved fragment. The R4 example adapter currently converts
   only `ContextItem.range` to `SearchUnit`, discarding fragment range, score,
   matched route and query-group rank. R4 therefore ranks parent declarations,
   then sometimes expands a large declaration to fact-parser methods without
   knowing which fragment caused the hit.
3. A read-only FTS CLI probe on the frozen index returned a container hit with
   `Range: 33-1307` and `Matched: 529-566`, alongside a method hit with a
   narrower source range. The adapter's `units()` uses the former `range`, not
   the matched/content range.
4. Consequently the old `pool=19/25` is **not yet established as raw evidence
   reachability**: overlap against a containing class range can count a gold
   span even when the retrieved fragment is elsewhere. The final selected
   ranges are a separate, source-hash-verified measure and remain the historic
   9/25 (macro Recall@10 0.5278) until a corrected run says otherwise.
5. The historical paired harness explicitly uses `max_source_reads=160`, while
   R0/R4 require at most 32 distinct source files per investigation. `State::text`
   caches by path, so `source_reads` counts distinct attempted file reads; old
   traces reach 160 and candidate pools can contain up to 256 files. Thus the
   0.5278 result is a useful historical quality landmark but is **not qualified
   under the 32-file gate**. Corrected baseline must use `max_source_reads=32`.

## Measurement-correction run (not an implementation experiment)

- **Question:** What does the unchanged current R4 policy achieve when both
  fragment-level hit ranges and the R0 32-distinct-file source-read bound are
  measured honestly?
- **Hypothesis:** The selected result may decrease under a 32-file cap; the
  19/25 entity-overlap pool will overstate raw fragment-level reachability.
  No direction or magnitude is assumed for corrected Recall@10.
- **Independent variables:** none in engine selection, route calls, frozen plan,
  index, extractor, embedding model, selector or score weights. Only the
  evaluator's trace will retain returned context range/content range/route
  metadata, and the source-read budget will be set to 32 to match the frozen
  contract.
- **Stop:** abort qualification if any case reads >32 distinct source files,
  any call/round/file/byte bound or hash validation fails, or if resource
  containment cannot be maintained. Preserve the old receipt unchanged. One
  paired run; no rerun for a nicer number.
- **Expected outcomes:** exact raw-fragment candidate attribution, current
  selector output, macro Recall@10, source-read and byte costs for all 12 cases.
  This becomes the paired baseline for representation work; it does not erase
  the historical 0.5278 result.

## Implementation experiment E1 — retrieve-to-evidence projection

- **Hypothesis:** R4 loses relevance and sub-unit precision at the
  retrieval-to-workflow adapter: projecting the already-returned fragment
  `content_range` as a candidate anchor (while retaining the parent declaration
  for provenance/context) will improve selected evidence without changing
  retrieval ranking, call allocation, index granularity, model, plan or selector
  policy.
- **Baseline:** the measurement-corrected run above on the same frozen index and
  plan, source-read cap 32.
- **Expected effect:** reduce false pool coverage from parent-range overlap,
  produce evidence windows anchored to the retrieved fragment, preserve the
  enclosing declaration/header when it fits, and increase selected gold from
  baseline; no claim about correctness before Gate C.
- **Acceptance:** selected ≥12/25 and macro Recall@10 ≥0.60 under the same
  budgets; also no loss of a guard/call/transaction context required by the
  frozen cases, no citation/hash failures, and no resource-gate failure.
  Selected count does not substitute for macro recall.
- **Stop:** if E1 fails acceptance or loses critical source context, revert it
  and record the failure; do not tune scores/calls to rescue it. A separate
  hypothesis would need a new preregistration and must stay within the remaining
  experiment quota.

## Experiment quota and guardrails

- At most three **implementation** experiments with distinct hypotheses; the
  measurement correction above is only a gate repair. E1 is the first
  implementation experiment. Do not reserve or spend later experiments without
  new evidence.
- No persisted index granularity/schema change, rebuild, production operation,
  held-out access, external model/provider, public MCP/CLI change, ranking/call
  allocation change, or gold-conditioned runtime logic.
- Preserve ≤3 rounds, ≤6 calls, ≤32 distinct source files, ≤24 KiB final pack,
  ≤64 KiB delivered tool content; retain source hash/range, typed gaps,
  abstention, cancellation and prompt-injection isolation.
- Resource pre/post captures are required for any run. Disk was 96% used with
  21 GiB available and memory availability was 1.8 GiB after the earlier
  one-query probe; no build or benchmark starts until resources are rechecked.

## Known non-R4 blockers

- `MAX_REFS_PER_ROUND` truncates after sorting and emits no gap. This is a
  correctness/observability defect to fix with an explicit typed gap and a
  regression test; it must not silently remain in the final verdict.
- Frozen task-correctness rubric still needs frozen C3 required-step lists and
  an independent external answerer/grader. No remote provider may receive this
  private corpus. Until an authorized, privacy-valid local setup exists,
  correctness is NOT_MEASURED/BLOCKED, not simulated.
- No dev-split result establishes independent generalization.

## Read-only resource probe (2026-11-19)

One FTS query on the isolated index, direct mode, no refresh, no remote model:
`zg --fts 'EmployeeEditThongTinCaNhan' --limit 5 --preview detail`. Wall time
~0.7 s, maximum RSS 318,788 KiB, zero swaps by the process; host available RAM
was 1.5–1.8 GiB, disk free 21 GiB, and memory/I/O PSI showed background
pressure. This was a route probe, not a paired benchmark or quality result.
No index build/rebuild or production operation was intended.

## Next authorized action

Recheck disk, memory, and process state. Then correct the paired harness to
capture exact fragment ranges and enforce `max_source_reads=32`, run the one
measurement-correction pair, and inspect its raw receipt before implementing E1.
If the resource guard cannot be met, preserve the probe and report the exact
blocker; do not raise caps or rebuild the index.

---

This preregistration is frozen before the corrected paired run. Changes to these
acceptance criteria require a versioned addendum, not silent edits.
