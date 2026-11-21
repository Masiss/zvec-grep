# Handoff — R4 benchmark-validity corrections (2026-11-21)

Status: **READY_FOR_AUTHORIZED_INFERENCE — SEMANTIC_EVALUATION_PENDING**.
Answer quality **NOT_MEASURED**. Not `QUALIFIED`, not `RAG_MVP_GO`, no
architecture winner.

## 1. Where things are

| Item | Value |
|---|---|
| Repo | `/home/masis/src/zvec-grep-r4-evidence-20261119` |
| Branch | `work/r4-evidence-representation-20261119` |
| Base checkpoint | `43565f5` |
| HEAD | tip of the branch (commits listed by `git log 43565f5..HEAD`; **not pushed**) |
| Worktree | clean |
| Report | `rust/docs/r4-benchmark-validity-corrections-20261121.md` |
| Amendment | `rust/docs/r4-three-arm-preregistration-20261120.md` §6d |
| Doc index | `rust/docs/README-rag-r4.md` |
| Review bundle | `/home/masis/.cache/zvec-grep/r4-validity-corrections-20261121/r4-validity-corrections-20261121.zip` |
| Bundle integrity | `bundle/MANIFEST.sha256` (content-addressed; the ZIP is rebuilt from it) |
| Synthetic receipts | `/home/masis/.cache/zvec-grep/r4-longrun-20261121/three-arm-v4/` |
| Runner | `/home/masis/.cache/zvec-grep/r4-longrun-20261121/run_three_arm_v4.py`, `reconcile_v4.py` |

## 2. Frozen identities (unchanged)

| Artifact | Identity |
|---|---|
| Corpus | `/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus` (822 files) |
| Cases | `cases/dev.json` sha256 `27626109fe4d2bf02beec474809424bbd58dd5a2b709263c6dde0926a4a9c311` |
| Rubric | `r4-task-correctness-contract-20261118.md` sha256 `330c796d…` |
| Evaluator materials | `cases/c3-steps.json` sha256 `7a84ab6945bc975788b44f1e3e7f40ce73cbece52e622283fd745688c44ac386` |
| Embedding / index | potion-code-16m-v2, unchanged; no rebuild |
| Adapter | built from this HEAD; `LD_LIBRARY_PATH` = pinned `zvec-prebuilt`; target `/home/masis/.cache/zvec-grep/r4-evidence-20261119/target` |

## 3. Verified

- 45/45 adapter contract tests (7 new boundary tests for read accounting).
- 76/76 harness acceptance checks, 32/32 grader fixture checks.
- 36/36 synthetic three-arm runs: 0 violations, 0 egress findings, 36/36
  answers `OK`, 36/36 packs bound, 36/36 receipts reconcile per-call deltas.
- 0 formal PASS without a semantic evaluation (asserted, not observed).

## 4. What a real run still needs

1. **A preregistered semantic evaluator**: identity, prompt, what it receives
   (frozen answer + frozen pack + evaluator-only materials), verdict contract
   (`PASS|FAIL` + rationale + `unsupported_claims`). Without it every case is
   `NOT_MEASURED` — by design.
2. **Owner authorization** for private-source inference: provider, exact
   model/served identity, endpoint, and the answerer's data flow (question,
   tool schemas, every delivered span + hash).

## 5. Exact next authorized action

With (1) and (2) in place: run D01–D12 × 3 arms via
`benchmarks/rag-impact/tools/r4_bench_driver.py --backend openai-responses
--authorized-inference --grade`, then grade each frozen pair in its own process
with `r4_grade.py --semantic <evaluator.json>`. Report structural and semantic
layers separately; a formal PASS requires both.

Not authorized here: ranking/embedding/index changes, held-out H01–H08,
GQ5/R5/R6, push, merge, release.
