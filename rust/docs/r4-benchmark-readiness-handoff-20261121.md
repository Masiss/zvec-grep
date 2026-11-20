# Handoff — R4 benchmark readiness corrections (2026-11-21)

Status: **READY_FOR_AUTHORIZED_INFERENCE** (harness) with real private-source
inference **OPERATION_BLOCKED** on owner authorization. Answer quality
**NOT_MEASURED**. This is not `QUALIFIED` and not `RAG_MVP_GO`.

## 1. Where things are

| Item | Value |
|---|---|
| Repo | `/home/masis/src/zvec-grep-r4-evidence-20261119` |
| Branch | `work/r4-evidence-representation-20261119` |
| Base checkpoint | `eda88b1` |
| HEAD | tip of `work/r4-evidence-representation-20261119` at handoff (commits listed by `git log eda88b1..HEAD`; **not pushed**, not merged) |
| Remote SHA | none — no push performed |
| Worktree | clean |
| Report | `rust/docs/r4-benchmark-readiness-20261121.md` |
| Amendment | `rust/docs/r4-three-arm-preregistration-20261120.md` §6c |
| Doc index | `rust/docs/README-rag-r4.md` |
| Review bundle | `/home/masis/.cache/zvec-grep/r4-benchmark-readiness-20261121/r4-benchmark-readiness-20261121.zip` (ZIP SHA-256 reported in the mission message; it changes whenever the manifest is regenerated, so the manifest is the stable identity) |
| Bundle integrity | `bundle/MANIFEST.sha256` (content-addressed, 21 files, 21/21 OK); the ZIP is rebuilt from that manifest |
| Synthetic receipts | `/home/masis/.cache/zvec-grep/r4-longrun-20261121/three-arm-v3/` (`three-arm-summary.json`, `RECONCILIATION.json`, 36 session dirs) |
| Runner used | `/home/masis/.cache/zvec-grep/r4-longrun-20261121/run_three_arm_v3.py`, `reconcile_v3.py` |

## 2. Frozen identities (unchanged by this mission)

| Artifact | Identity |
|---|---|
| Corpus | `/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus` (822 manifest files, 5.4 MB) |
| Cases | `benchmarks/rag-impact/cases/dev.json` sha256 `27626109fe4d2bf02beec474809424bbd58dd5a2b709263c6dde0926a4a9c311` |
| Rubric | `rust/docs/r4-task-correctness-contract-20261118.md` sha256 `330c796d…` |
| C3 checklist | `rust/docs/r4-task-correctness-c3-checklist-20261120.md` (hashed in materials) |
| Evaluator materials | `benchmarks/rag-impact/cases/c3-steps.json` sha256 `7a84ab6945bc975788b44f1e3e7f40ce73cbece52e622283fd745688c44ac386` (generated verbatim from the two frozen docs) |
| Embedding | potion-code-16m-v2 (dim 256, CPU) — unchanged |
| Adapter | `r4-adapter` built from this HEAD; `LD_LIBRARY_PATH` = pinned `zvec-prebuilt`; target dir `/home/masis/.cache/zvec-grep/r4-evidence-20261119/target` |

## 3. Verified in this mission

- 38/38 adapter contract tests (incl. read-budget boundary and single-open).
- 804/804 `zg-engine` lib tests; clippy clean of errors.
- 38/38 harness acceptance checks (`r4_bench_driver_selftest.py`).
- 15/15 grader fixture checks (`r4_grade_selftest.py`).
- 36/36 synthetic three-arm runs (12 authored cases × 3 arms): 0 protocol
  violations, 0 plan-egress findings, arm-b/arm-c 12/12 verified packs,
  arm-a policy explicit (`no_verification_tool`).
- Cold latency reported as measured: bounded ops p95 ≤ 13 ms (PASS), indexed
  search cold p95 4.66 s (FAIL vs 1 s warm gate), workflow cold p50 11.8 s
  (FAIL vs 3 s gate; warm reference 3.1–4.7 s).

## 4. Cleanup state

Nothing cleaned; all receipts, plans, packs and answers are retained under
`/home/masis/.cache/zvec-grep/r4-longrun-20261121/three-arm-v3/`. No index
rebuild, no model change, no repo-local large artifacts.

## 5. Exact next authorized action

Owner must decide and preregister: provider, exact model + served identity,
endpoint, and the answerer's data flow (question, tool schemas, and every
delivered evidence span plus its hash). With that authorization:

1. Run D01–D12 for all three arms via
   `benchmarks/rag-impact/tools/r4_bench_driver.py --backend openai-responses
   --authorized-inference --grade` (arm-b plan generation runs first per case).
2. Grade each frozen `(answer, pack)` pair in a separate process with
   `r4_grade.py`; keep `RECONCILIATION.json` semantics for the numbers.
3. Report C1–C6/C3 per arm with the C2-semantics / C4-intent caveat, and stop
   before any private-source payload that exceeds the approved scope.

Nothing else is authorized by this handoff: no ranking/embedding/index work, no
held-out H01–H08, no GQ5/R5/R6, no push, no public API change.
