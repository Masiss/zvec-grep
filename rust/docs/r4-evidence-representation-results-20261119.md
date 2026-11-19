# R4 Evidence Representation & Granularity — final investigation (2026-11-19)

## Verdict

**PARTIAL / NO-GO for representation changes under the frozen R4 gate.** The
source/index path is now attributed more precisely, one class-to-method fan-out
bug was fixed and regression-tested, but none of the three preregistered
implementation experiments met acceptance or improved the corrected selected
result. The best historical **0.5278** remains below GO **0.5722** and was not
qualified under today's 32-source-read gate. Task correctness remains
**NOT_MEASURED**.

No index/corpus/model/plan/production change was made. No fourth experiment,
call-budget increase, external source transfer, or correctness simulation was
performed.

## Source → retrieval → evidence map

1. **Extraction and persistence.** The frozen v3 index was rebuilt with the
   R3 SQL-definition extractor. Its manifest is index v3, generation
   `cea13512-733a-4c9f-96a7-5e131b57d1dc`, `local/potion-code-16m-v2` (256-D,
   CPU). The independent index audit and SQL-symbol audit verify all **25/25**
   gold declarations persisted with source ranges; the exact symbol route can
   return C# methods and SQL procedure/view declarations. R2/R3 fact graphs are
   parsed at workflow time, not persisted as a separate relationship graph.
   See `r4-index-truth-audit-20261118.md` and its reconciliation in
   `r4-sql-symbols-20261118.md`.
2. **Search representation.** FTS/vector search is over indexed fragment
   content. A hit carries fragment selectors; context projection can also
   return its containing entity range. That parent range can be a whole class.
   The index truth is not the problem: it is the adapter/workflow's choice of
   candidate range and the later retrieval/selection path. Exact symbol lookup
   is a separate route and consumes the same constrained call budget.
3. **Projection and candidate pool.** The corrected baseline's parent-range
   overlap was **19/25**, but independent matched-fragment reachability was only
   **13/25**. Treating parent overlap as a retrieved relevant unit therefore
   overstates raw reachability by six spans. E1/E2 fragment-anchored pools were
   **12/25**, close to raw-fragment reachability; the gap is primarily before
   ranking, not a reason to add method vectors blindly.
4. **Ranking/widening.** E2 traces exposed a real implementation defect:
   `EmployeeCommand.cs:200–238` was a candidate with enclosing class
   `:14–1038`; rank expansion admitted every parsed method in the class under
   that fragment's provenance. The correction now expands a contextual C# or
   SQL fragment only to a parsed method/procedure that contains it, even when
   the parent range is short. Broad candidates without a separate context keep
   the previous behavior. This preserves precise anchor/provenance while
   avoiding sibling declarations.
5. **Selection and verified evidence.** The fixed E2 pack still selected
   **8/25** (same as corrected baseline). D01 selected the full controller C1
   method `EmployeeController.cs:459–622`, preserving the `Form == "1"` and
   `Perform` branch; required C2 `EmployeeCommand.cs:215–285` remained absent.
   D07 selected the `RestoreEmployeeById` method (including its SQL update and
   `UpdateOrgTotalEmployee()` call) but not the linked SQL definition needed to
   establish the downstream organization update. D12 selected neither gold
   span. Thus “whole method” alone is not a sufficient evidence policy.
6. **Typed expansion/call allocation.** The frozen six-call limit was exhausted
   before a useful second typed lookup. E3 derived method-scoped references but
   did not increase selected evidence; in D07 its symbol lookup changed from
   exact `sp_UpdateOrgTotalEmployee` to generic `CoreService`. Do not interpret
   missing SQL evidence as index corruption: SQL symbols exist in this index,
   but correct query choice and call allocation remain limiting.

## Frozen experiment results

All runs used D01–D12, the same corpus/index/model and blind plan. The frozen
acceptance is selected ≥12/25 **and** macro ≥0.60, with the R0 caps.

| Run | Validity | Plan macro | Pool | Selected | Key finding |
|---|---|---:|---:|---:|---|
| Corrected baseline32 | valid baseline | 0.4861 | 19/25 parent overlap | 8/25 | Parent overlap is not raw-fragment reachability (13/25). |
| E1 fragment-only | valid, rejected | 0.4028 | 12/25 | 7/25 | D01 selected `:524–544`, missing the branch-entry context; C2 absent. |
| E2 attempt 01 | invalid diagnostic; preserved | 0.4028 | 12/25 | 7/25 | Rank fanned one class-scoped candidate out to sibling methods. |
| E2 attempt 02 | valid, rejected | 0.4861 | 12/25 | 8/25 | Containment fix passes; no gain over corrected selected result; C2/D07 SQL/D12 remain incomplete. |
| E3 method-scoped refs | run complete; no-go under fixed cap | 0.4861 | 12/25 | 8/25 | Same selected ranges as E2; D07 exact SQL symbol query was replaced by generic `CoreService`. |

E2 attempt 02 and E3 stayed within the frozen caps: ≤6 calls, ≤3 rounds,
≤32 distinct source reads, ≤24,576 selected-pack bytes, and ≤65,536 per-case
source-content-plus-gap estimate. E2 max selected bytes **24,535 B** and max
workflow estimate **27,240 B**; E3 selected plan spans were hash-verified.
Top-level `citation=1.000` is the no-plan arm and is not a task-correctness
score. The external delivery wrapper and independent answer correctness were
not measured.

E3's per-case trace shows five discovery calls plus a sixth lookup, then
`round=2 refs=0 calls_left=0`. It derived 48, 50, and 48 typed-reference trace
entries in D01, D07, and D12 respectively. The sixth lookup displaced D07's
specific SQL-procedure symbol with generic `CoreService`; the remaining derived
names were not dispatched. No rerun or call-budget change is authorized by
this mission.

## Code and verification

The retained code change is in `rust/crates/zg-engine/src/rag/workflow.rs`:
contextual candidates expand only to the containing parsed C# method or SQL
definition. Regressions in `rust/crates/zg-engine/src/rag/workflow/tests.rs`
cover a multi-method C# class and a multi-procedure SQL file. E3-only
method-scoped typed-reference changes were removed after the one run failed to
improve the plan result and displaced the specific D07 SQL lookup. Its
executable, source hashes, receipt, and test/build logs are preserved externally;
the unaccepted E3 source snapshot itself was not retained.

Verified on the final E2-only working tree:

- `cargo fmt --all` and `git diff --check` pass.
- `cargo test --offline --locked -p zg-engine rag::workflow::tests`: **32 passed**.
- `cargo build --offline --locked -p zg-engine --example r4b_paired`: pass.

Existing unrelated compiler warnings remain (`unused_mut` in workflow and an
unused import in `extraction/candidate.rs`).

## Reproducibility and remaining blockers

- Branch: `work/r4-evidence-representation-20261119`, HEAD/base
  `0c9ba7ed26fe7c4ccfff4b06165ea85242dc95ab`; uncommitted, not pushed/merged.
- Frozen corpus manifest SHA-256:
  `c706c8dd9b560572a2c1739c2c2bda676982f4c3b5da4cd4b32bcfeb66359cdb`.
- Blind plan SHA-256:
  `e8a33dde61c6b661d87128f70d01999add8fab1ebf33be67815a78754bb1dba7`.
- Index manifest SHA-256:
  `f6f9695a9a4cf2f42ffbd36d13eed5110fe1c61a99be18508e8b7ee77ec05917`.
- Raw receipts, per-case traces, time/resource snapshots and artifact hashes:
  `/home/masis/.cache/zvec-grep/r4-evidence-20261119/` (`receipt-baseline32`,
  `r4-e1-20261119`, `r4-e2-20261119`, `r4-e3-20261119`). External build target
  remains at `/home/masis/.cache/zvec-grep/r4-evidence-20261119/target`.
- Task-correctness rubric is frozen, but C3 required-step lists plus a
  privacy-valid independent answerer and separate grader were unavailable.
  Private source was not sent to a remote provider. Correctness therefore
  remains **BLOCKED / NOT_MEASURED**, as specified in
  `r4-task-correctness-contract-20261118.md`.

## Recommendation

Do **not** rebuild the index to add more method-level vectors: exact C#/SQL
symbol ranges already persist and are searchable. Do not claim R4 GO. The
remaining actionable boundary is a separately authorized design decision on
how to spend the six calls between plan discovery and exact-symbol/SQL lookup,
plus how to carry candidate relevance anchors into the final selected pack
without counting broad parent overlap as retrieval. Any work beyond the three
experiments requires a new scope/preregistration; this investigation stops
here.
