# HANDOFF — R4 external-agent architecture validation

**As of 2026-11-20. Read this first before continuing R4.** This handoff
supersedes the active-action section of `HANDOFF-20261118-NEXT.md`. That file
remains historical evidence; its suggestion to add method-level index units is
not current authorization. Current status is **PARTIAL**: Gate A completed;
Gate B blocked; Gate C not measured.

## 1. Exact checkpoint

| Item | Current value |
|---|---|
| Work branch | `work/r4-evidence-representation-20261119` |
| Worktree | `/home/masis/src/zvec-grep-r4-evidence-20261119` |
| Source HEAD before this handoff/document update | `207bdd2538e91724b55b45eb5f44f1a807a2520f` (`docs(rag): cite containment verification receipt`); this is the last previously verified code/document checkpoint. |
| Pre-handoff commit sequence | `8646382` containment checkpoint → `f50242c` architecture audit/C3 freeze → `70475cb` README references → `207bdd2` test-receipt citation |
| Base / remote | `7030bd7` is an ancestor; remote `origin/work/r4-deep-opt-20261118` remains `0c9ba7ed26fe7c4ccfff4b06165ea85242dc95ab`. |
| Push state | No push. At the source snapshot, the work branch was 4 commits ahead; this handoff's doc-only commit(s) are descendants. Run `git rev-parse HEAD` and `git rev-list --left-right --count origin/work/r4-deep-opt-20261118...HEAD` on resume for the final tip/delta. |
| Worktree | `/home/masis/src/zvec-grep-r4-evidence-20261119`; after this documentation batch is committed, expected clean. Main worktree `/home/masis/src/zvec-grep` remains on `work/r4-deep-opt-20261118` at `0c9ba7e`; no branch switch or merge. |
| Current scope | Architecture/model inventory, documentation and frozen C3 preparation only. No inference, source egress, new retrieval benchmark, model/index change, or public API/MCP change. |

The initial containment implementation is commit `86463826717ccdf11454755d99fa2acd57ce3c1c`.
It preserves a ranked fragment's containment: widening can reach only the
method/procedure that contains that fragment. The E3-only reference-scope
change was reverted. C#/SQL regressions were added. This is the only code
checkpoint from the previous R4 representation work.

## 2. Mission and gate verdict

The active mission switched from engine-managed R4 tuning to validating whether
an external agent should own reasoning, planning and evidence selection. The
latest user instruction is to prepare this detailed handoff and update related
documentation. Do not resume the earlier method-level-index proposal by default.

- **Gate A — architecture/model inventory: PASS.** See
  `rust/docs/r4-external-agent-architecture-validation-20261120.md`.
- **Gate B — paired external-agent architecture validation: OPERATION_BLOCKED.**
  No permitted inference environment with identifiable model/version/config
  was found. There was no local inference process/listener in the bounded
  snapshot. The current Pi model identity and its privacy/egress configuration
  are not available through the inspected project/runtime tools. A local CLI
  alone would not prove local inference.
- **Gate C — task correctness: NOT_MEASURED.** No answerer or independent
  grader was run. The C3 rubric preparation is complete, but is not a test
  result.
- **Overall:** PARTIAL; no architecture winner or RAG MVP GO claim.

No corpus, case pack, or answer content was sent to a remote provider. Do not
use a remote model for the private HRM source without explicit approval covering
that endpoint and source class.

## 3. Architecture facts to carry forward

### Product boundary

Canonical design remains: zvec-grep owns deterministic source search, static
facts, provenance, validation and bounded retrieval; an external agent owns
question interpretation, investigation choices, semantic checking and answer
composition. The Rust engine contains **no generative LLM**. Its model layer is
an embedding-model layer, not an answer-generation or neural-reranking service.

### Tool/API boundary

- MCP `agent_direct` exposes only `zvec_grep_search` (hybrid/lexical/vector
  search with symbol preference).
- The **full** MCP server also exposes `zvec_grep_evidence`, but the default
  agent profile does not. Neither MCP profile has a direct `symbols` or typed
  `relations` tool.
- Rust `ZvecGrep` has context and exact-symbol APIs; evidence assembly and
  validation exist in the engine. C#/SQL extraction modules are crate-private.
- `rag::workflow::run_with_plan` is a deterministic Rust workflow. It accepts
  an external `AgentPlan`, then the engine still derives anchors, discovers and
  expands, ranks, selects, compacts and verifies the pack. It has no model call
  and is not wired to the default agent MCP profile.
- A direct-agent prototype could wrap these existing APIs and internal facts
  in a **non-public in-process adapter**. Do not add public tools/contracts,
  default models, or a new service based on this audit alone.
- R0's investigation target is 6 calls / 32 source reads / 24 KiB pack / 64 KiB
  delivered content. `WorkflowBudget::default()` is 12 calls / 64 source reads;
  the R4 benchmarks supplied explicit tighter limits. Search MCP requests can
  be repeated, so direct orchestration needs a cumulative host/session budget
  ledger (calls, reads, bytes, rounds and elapsed time), not just per-request
  limits.

### Model inventory and identity

- Frozen R4 index manifest: `/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus/.zvec-grep/manifest.json`
  SHA-256 `f6f9695a9a4cf2f42ffbd36d13eed5110fe1c61a99be18508e8b7ee77ec05917`.
  It records index v3, `local/potion-code-16m-v2`, CPU, dimension 256, text+code,
  max input 1024 tokens, and empty `embeddingRoutes`.
- Frozen Potion artifact SHA-256
  `75cf7a6c2171b230ad19b1e7d8e0b1aee86da5a02af8e7cacedd9921d227623c`;
  32,490,072 bytes. Matching copies exist in `/home/masis/.zvec-grep/models/`
  and the isolated E3 model cache. Its tokenizer JSON is 1,024,340 bytes.
- A cached EmbeddingGemma Q8 GGUF exists at
  `/home/masis/.zvec-grep/models/hf_ggml-org_embeddinggemma-300M-Q8_0.gguf`
  (333,590,944 bytes, SHA-256
  `b5ce9d77a3fc4b3b39ccb5643c36777911cc4eb46a66962eadfa3f5f60490d63`). It is
  cache evidence only, not evidence it was loaded in the R4 runs.
- The default catalog has 14 embedding choices; six E4 experiment variants
  remain behind the non-default `e4-experiment` feature. E4 is research-only.
  Vulkan is an optional compute backend, not a second model. Hosted Qwen
  catalog entries are embedding APIs and would require explicit egress consent.
- A process/listener snapshot found no recognized local model server or active
  `zg` process. There was no engine instance to query `runtime_snapshot()`.
  Therefore report **no active model observed**, not “no model can exist.” The
  current external answerer identity/version/config remains unknown.
- Keep the one-embedding-model-per-workspace invariant. The R4 manifest has
  one model; indexing rejects more than one unique runtime model even though
  descriptor metadata can represent content-kind routes.

## 4. Frozen dataset, index and task-correctness contract

- Frozen source corpus manifest:
  `benchmarks/rag-impact/corpus-manifest.json`, SHA-256
  `c706c8dd9b560572a2c1739c2c2bda676982f4c3b5da4cd4b32bcfeb66359cdb`.
- Source root recorded by the manifest:
  `/home/masis/itc/Ricons/HRM2.0`; source Git HEAD
  `68a53a6865b81d05745c4aa47967204e8e128996`. SQL anchors S1 and S2 are selected
  repository variants, **not deployment evidence**.
- Development case file `benchmarks/rag-impact/cases/dev.json`, schema v1,
  SHA-256 `27626109fe4d2bf02beec474809424bbd58dd5a2b709263c6dde0926a4a9c311`.
  It still carries the old `ALL_24_CASES_FROZEN_B0_PENDING` status field;
  preserve the frozen file and use current reports for phase status. C3 applies
  to D01–D12; D13–D16 are synthetic and are not answer-quality cases. No
  held-out case was opened.
- Base task correctness contract
  `rust/docs/r4-task-correctness-contract-20261118.md`, SHA-256
  `330c796d0e51ed5e02d8ca02f34a763dac06f6abd055eb4eee3ecc8488fe5e15`.
- C3 checklist addendum
  `rust/docs/r4-task-correctness-c3-checklist-20261120.md`, SHA-256
  `2bd4c63dff8b6638cad7db72a37ceeaaa6fd1031acf5f0cd15e3ed92ae8751bb`.
  Freeze is complete; original contract/gold and frozen dev data were not
  edited. The checklist/gold must not be given to the answerer. Freeze each
  answer and evidence pack before giving it to an independent grader in a
  separate context. No grading has occurred.

## 5. Corrected R4 result — definitions matter

Latest valid representation baseline/result under the corrected 32-distinct-
source-read contract:

| Arm | Macro R@10 | Gold candidate pool | Selected gold | Interpretation |
|---|---:|---:|---:|---|
| Fair hybrid baseline, 24 KiB | 0.2500 | 4/25 | n/a | Bounded baseline; not the raw/unbounded arm. |
| R4 no-plan | 0.2778 | 12/25 | 6/25 | Retrieval-only. |
| Corrected R4 plan baseline | 0.4861 | 19/25 parent-range overlap | 8/25 | Parent overlap overstates raw matched-fragment reachability (13/25). |
| E1 fragment-only | 0.4028 | 12/25 | 7/25 | Rejected; lost needed enclosing context. |
| E2 containment, valid attempt 02 | 0.4861 | 12/25 under the corrected contained pool | 8/25 | Kept fix; no gain in selected count, but sibling-method over-widening prevented. |
| E3 reference-scope | 0.4861 | 12/25 | 8/25 | No gain; exact SQL symbol path regressed to generic `CoreService`. E3 code reverted. |
| GO threshold | **0.5722** | — | — | Not reached. Correctness remains NOT_MEASURED. |

The **historical 0.5278 / 19 pooled / 9 selected** result is not qualified under
the corrected ≤32-source-read gate. Do not merge macro scores, pool overlap,
raw-fragment reachability and selected evidence into one metric. Full attribution
is in `rust/docs/r4-evidence-representation-results-20261119.md` plus its
amendments.

No additional heuristic optimization is active. The prior recommendation to
change index/evidence granularity is a historical proposal only; the current
mission explicitly shifted to external-agent architecture validation and
requires a new scoped decision before implementation.

## 6. Experiment reproduction limits and retained artifacts

All previous representation runs are development-split retrieval-only,
single-run measurements. They had no external answerer/grader, no repeat-based
variance estimate and no per-model CPU/RSS/PSS attribution. Receipts are under
`/home/masis/.cache/zvec-grep/r4-evidence-20261119/` and have not been cleaned.

| Run | Retained material | Rebuild/reproduction limitation |
|---|---|---|
| Baseline32 | `/receipt-baseline32/`: summary, JSON and detailed traces, pre/mid/post resource snapshots, timings and SHA files. | No executable in this receipt; multiple attempts/files must not be confused. |
| E1 | `/r4-e1-20261119/attempt-01/`: output JSON, trace, time, stdout/stderr, pre/post snapshots and hash receipt; hashes verified. | No executable and no exact source patch/tree. Source hash alone is insufficient to reconstruct the build. |
| E2 attempt 01 | Raw diagnostics retained in `/r4-e2-20261119/attempt-01/`. | Invalid diagnostic run; not a qualified result. |
| E2 attempt 02 | `/r4-e2-20261119/attempt-02/`: full receipt and executable SHA-256 `229e13f1706fd37de67f730085d562f8a911789d996b0aa6ba8de281307d3e3a`. | Containment source and tests are in commit `8646382`; binary receipt verifies. Still one retrieval-only dev run. |
| E3 | `/r4-e3-20261119/attempt-01/`: full receipt and executable SHA-256 `75411d331ddb58b5afcb5e4288b9359ffd33bc0b79a8861d3f1c2652edc5e941`. | Exact E3 source tree/patch was not preserved; binary execution is repeatable, source rebuild is not. E3-only changes were reverted. |

Hash verification passed for E1, E2 attempt 02 and E3 attempt 01. Build/test
artifacts remain in `/home/masis/.cache/zvec-grep/r4-evidence-20261119/e2-build-tests/`.
`containment-fix-tests-build.log` records 32 workflow tests passed; this is
prior checkpoint evidence, **not** a test run from the documentation/handoff
pass. The prior checks included formatting and example compilation; no current
code build was run.

## 7. Changes and documentation map

| Commit | Change |
|---|---|
| `8646382` | Preserved containment fix and C#/SQL regression tests; E3-only code reverted. |
| `f50242c` | Added architecture audit, frozen C3 required-step addendum and implementation-status overlay. |
| `70475cb` | Removed two stale RAG README links that pointed to absent files. |
| `207bdd2` | Linked the architecture report to the exact prior 32-test receipt. |

Current pointers:

- This handoff: `rust/docs/r4-external-agent-handoff-20261120.md`.
- Architecture/model/tool audit and Gate B blocker:
  `rust/docs/r4-external-agent-architecture-validation-20261120.md`.
- C3 list and freeze protocol:
  `rust/docs/r4-task-correctness-c3-checklist-20261120.md`.
- Representation results and detailed causal attribution:
  `rust/docs/r4-evidence-representation-results-20261119.md` and the linked
  amendments.
- R4 document index: `rust/docs/README-rag-r4.md`.
- Product-level target design and current implementation overlay:
  `docs/rag/README.md`.
- `HANDOFF-20261118-NEXT.md` is now historical; do not treat its next action as
  current authorization.

No binaries, receipts, model caches, corpus/index artifacts or build targets
were deleted. No benchmark job remains active from this pass. Do not clean the
shared or historical caches as part of routine handoff.

## 8. Exact next action and guardrails

**Current authorized action: stop at the inference gate.** Wait for an explicitly
permitted, private-source-compatible inference environment with model identity,
version and configuration available for recording. A local CLI label is not
sufficient: verify where inference executes and the egress path. If the only
available model is remote and private-source approval is absent, report BLOCKED
and do not send corpus, questions containing source identifiers, evidence
packs or answers to it.

After that condition is met, resume the existing mission by preregistering a
paired comparison using the same model/version/config and prompt policy for all
three arms:

1. external agent + ordinary hybrid search;
2. external agent + current deterministic R4 workflow;
3. external agent directly orchestrating search/symbols/typed facts/verified
evidence through a non-public adapter.

Use D01–D12 only, freeze/hash every prompt, plan, answer and evidence pack at the
appropriate blind stage, and use a separate independent grader context with the
frozen C1–C6 contract and C3 addendum. Enforce a cumulative host/session budget
for calls, source reads, bytes, rounds and elapsed time. Observe privacy and
resource gates before starting. This paragraph is a plan for resumption, **not
an automatic authorization** for a new inference/benchmark operation or a
public API/tool change.

Until a new explicit scope says otherwise:

- keep the one-embedding-model-per-workspace rule and current index unchanged;
- do not add a generative, second embedding, reranker or specialized model;
- do not expose new MCP/public APIs or alter the transport profile;
- do not restart engine-managed heuristic tuning or method-level index work;
- do not edit frozen case files, base correctness contract, corpus manifest or
  historical receipts;
- do not run database code, query live HRM data, merge, push, deploy or clean
  historical artifacts.

## 9. Reading order for the next session

1. This handoff (current state, blockers and exact safe next step).
2. `rust/docs/r4-external-agent-architecture-validation-20261120.md` (source
   evidence for model/tool boundaries and Option 1–3 assessment).
3. `rust/docs/r4-task-correctness-c3-checklist-20261120.md` and the base
   `rust/docs/r4-task-correctness-contract-20261118.md` (frozen grading protocol;
   still no result).
4. `rust/docs/README-rag-r4.md` (index, current metrics and historical results).
5. `rust/docs/r4-evidence-representation-results-20261119.md` and amendments
   only when detailed retrieval attribution is needed.
6. `docs/rag/README.md` and `docs/rag/implementation-plan.md` for target product
   architecture and R0–R6 gates.

Do not repeat the architecture inventory, C3 checklist drafting, hash checks or
receipt integrity checks without a specific new discrepancy; their current
results are recorded above and in the linked reports.


---

## Amendment 2026-11-20 (same day, after the external-agent mission)

The authorized mission that followed this handoff implemented the private
experimental adapter: new crate `rust/crates/zg-r4-adapter` (CLI
`r4-adapter`; sessions, authoritative cumulative ledger, frozen R0 budget,
profiles arm-a/arm-b/arm-c) plus a non-default `r4-experimental-facts`
feature in `zg-engine` re-exporting the R2/R3 fact parsers. Deterministic
contract/safety qualification passed (24/24 synthetic tests), and all three
arms were smoke-verified end-to-end on the frozen isolated corpus. See
[`r4-external-agent-adapter-20261120.md`](r4-external-agent-adapter-20261120.md)
for the report, receipts and two fixed defects. Gate B status is refined, not
resolved: the harness's inference provider is identified (`openai`,
`openai-responses`, `https://api.openai.com/v1`, default model `gpt-5.5`), so
the outstanding decision for the owner is now explicit: authorize
private-source submission to that identified provider/model for the frozen
D01–D12 benchmark runs, or provide another approved environment. No source
left the machine; no answer-quality run occurred; Gate C stays NOT_MEASURED.


## Amendment 2 — technical audit (2026-11-20, later same day)

The authorized audit mission verified the adapter against benchmark
requirements and fixed two proven defects: a CRITICAL concurrent-budget race
(reproduced at 5/8 racers admitted; now an flock transaction with
reload-under-lock; post-fix control: 1/12) and a HIGH verify-reservation gap
(verify now consumes no investigation slot so final verification always
fits the frozen budget). Exclusion markers became session scope
configuration; search routes pin RefreshPolicy::Off. A controller-mediated
answerer driver (`benchmarks/rag-impact/tools/r4_bench_driver.py`) provides
the isolation boundary (answerer = strings only; 7 adversarial negative
controls rejected with zero state change; gold-free arm-c policy completes
a verified pack in 3 calls / 16.8 KiB). Crash sweeps confirm all-or-nothing
ledger accounting. Audit HEAD `ac7610d` (fix commit `3f1d87e`); 29/29
adapter contract tests; default engine suite unchanged (two pre-existing
parallel-lock flakes in `config::tests` documented). Owner-side review
bundle: `/home/masis/.cache/zvec-grep/r4-external-agent-20261120/
r4-adapter-review-20261120.zip` (46 files, 253,800 bytes, SHA-256
1816d4e3314d12739ff8294794b8d00c6566d6321a3f315a4b561f39702819bd).
Status: **READY_FOR_BENCHMARK** for the harness and accounting; the real
three-arm run still awaits the private-source inference authorization
(identified `openai` endpoint, model identity to be fixed in
preregistration) and push authorization remains outstanding.
