# R4 real-agent RAG evaluation — readiness gate (2026-11-22)

## Status

**No real answer-quality run was made.** The frozen three-arm harness is substantially ready for an authorized pilot, but the experiment remains at the owner gate. D1 (trusted independent evaluator) and D2 (private-source inference/retention scope) are not approved. The real API credentials expected by the driver were absent in this session. The production evaluator registry remains empty.

The synthetic pilot and synthetic 36-run E2E are pipeline evidence only, not answer-quality evidence.

## Checkpoints and scope

- R4 worktree: branch work/r4-evidence-representation-20261119, base HEAD 93ca36b1b54015cc21c6d7d6486695a243760819.
- Current evaluation fix: 360c797 (fix(rag-harness): redact local roots from model tool output).
- The other worktree remains on work/source-discovery-autonomous-20261122; its pre-existing untracked files were not changed.
- Frozen benchmark inputs were not changed: D01–D12 manifest hash 27626109fe4d2bf02beec474809424bbd58dd5a2b709263c6dde0926a4a9c311; C1–C6 contract hash 330c796d0e51ed5e02d8ca02f34a763dac06f6abd055eb4eee3ecc8488fe5e15; C3 checklist hash 2bd4c63dff8b6638cad7db72a37ceeaaa6fd1031acf5f0cd15e3ed92ae8751bb; evaluator materials hash 7a84ab6945bc975788b44f1e3e7f40ce73cbece52e622283fd745688c44ac386.
- No gold, checklist, rubric, benchmark protocol, model, index, embedding, production default, or API was changed.

## Readiness evidence

| Component | Current evidence | Assessment |
|---|---|---|
| Adapter executable | Existing r4-adapter loads with the pinned native library path; read-only status succeeds against the frozen corpus index. | Present. |
| Frozen index | Version 3, Potion Code 16M, 256 dimensions, CPU. Direct search on the original cache corpus cannot create its temporary write-intent lock under this read-only sandbox. | Environment permission blocks direct use here; not evidence of retrieval failure. |
| Disposable index copy | A temporary local copy (deleted at command exit) returned one result for each FTS, vector and hybrid query. The source/index copy was not rebuilt. | Retrieval routes and local embedding path execute on this host. Capability smoke only, not task quality. |
| Potion artifact | A complete local artifact marker and model weights are present in an existing experiment cache. The vector query succeeded. | Earlier “artifact missing” note is stale for this host; the exact runtime-selected artifact path was not independently logged. |
| Main workspace index | zvec semantic search fails opening the current workspace FTS RocksDB fragment with ZG.ENGINE.STORAGE_FAILURE. | Separate environment issue; the benchmark uses the frozen R4 corpus index, not this workspace index. No repair/rebuild attempted. |
| API backend | Driver supports openai-responses; requested model defaults to gpt-5.5, and served identity/fingerprint/token/latency fields are recorded. R4_INFERENCE_ENDPOINT and R4_INFERENCE_TOKEN were absent; no request was made. | Backend code exists; real provider behavior and identity are unverified. |
| Evaluator | Packet/response/accept tooling is present; production registry is empty. Evaluator selftest passed 57/57. The frozen remote candidate gpt-4.1 is marked deprecated in OpenAI’s current API pricing catalog. | D1 remains open; do not register the stale candidate. |
| Frozen assets | D01–D12, C1–C6, C3 and evaluator materials are present; their fingerprints match the frozen values above. | Available; H01–H08 remain unopened. |

### Offline validation actually run

Using a disposable local copy of the frozen corpus/index and synthetic transports only:

- grader fixtures: **89/89**;
- semantic evaluator contract: **57/57**;
- driver harness, including its loopback-only planner fixture: **79/79**;
- synthetic semantic pilot: **5/5**;
- synthetic three-arm E2E: **exit 0** (36 arm runs in the frozen script).

The loopback test required a sandbox escalation because network access is restricted by default; its only listener was on 127.0.0.1. No external inference occurred.

A local reproduction found that an adapter error response could place the absolute corpus root into a model-facing function_call_output. Commit 360c797 now removes the local corpus and session roots at the model boundary. Full transcripts/receipts still retain the original envelope locally. The existing root-leak check passed as part of 79/79. Rollback: git revert 360c797. No benchmark test framework was added.

## Proposed controlled execution

### Pilot, after approvals

1. Before viewing any answer, freeze model identities, served-identity drift rule, evaluator identity/prompt, storage/retention setting, and a total spend ceiling.
2. Use three preselected development cases (D01, D06, D12 by index-spaced selection) × all three arms: nine answer sessions, plus three blind Arm-B plan calls and nine independent evaluations. Use fresh isolated sessions and frozen per-session budgets.
3. The pilot is a feasibility gate only. Continue to 12×3 only if all nine answers are structurally acceptable, calls and reads reconcile to the frozen budget, the evaluator returns a schema-valid result for every packet, served identities match the freeze, and no privacy/protocol violation occurs. Pilot outputs do not count toward the full score; the 12×3 run uses fresh sessions and reruns all pilot cases.
4. Do not tune settings from pilot scores. If a setting must change, record the pilot as calibration, preregister the changed configuration, and rerun the complete 12×3; do not combine runs across configurations. Never open H01–H08.
5. Full run: all 12 frozen cases × three arms, same approved model/config and budgets; 36 separate semantic evaluations. Report answer quality C1–C6/C3 independently from retrieval overlap, plus per-arm calls, source reads, pack bytes, latency, tokens, memory and cost.

### Owner approval package

See r4-real-agent-rag-approval-package-20261122.md.

- **D1 — evaluator:** owner must name and trust an independent evaluator. The prior remote candidate gpt-4.1 is deprecated. Choose a currently supported, distinct OpenAI model or a named human; record exact requested and served identity, system fingerprint when present, prompt digest 4e9e20413c8ae2172844298ed743ab24c5fb79c0a36defeb957bee0c4f28cd55, and permitted packet fields. Do not register until approved and calibrated on nonprivate adjudicated examples.
- **D2 — data egress:** approve the OpenAI Responses endpoint and precise data classes for both answerer and evaluator. Answerer tool outputs may contain private source text. The evaluator receives the question, answer, delivered pack and cited spans only; no gold, C3 checklist, transcript or source outside delivered/cited evidence.
- **Retention:** the client does not set store:false; use the current account's verified setting. OpenAI documents 30-day Responses application-state retention by default and up to 30-day abuse-monitoring logs unless eligible organization controls change this. Do not assume ZDR/MAM.
- **Cost:** current driver has no output-token ceiling or aggregate cost guard, so total spend is **not bounded by this code**. The old evaluator-only estimate was 331,949 estimated prompt tokens + 25,200 output tokens for 36 evaluations. At currently published GPT-5.5 standard rates ($5/M input, $30/M output), that portion is about **$2.42**, still only an estimate and excluding all answerer/planner inference. Require an owner-approved all-in ceiling and a pre-call reservation guard (bounded request/input size, max_output_tokens, no automatic retries, stop before a request could exceed remaining budget) before any real call.
- **Runtime/index:** no rebuild or repair is proposed. Use the frozen v3 index and existing Potion Code 16M embedding. If the approved runtime requires a persistent change, stop and request separate authorization.

Current OpenAI references checked 2026-11-22: [GPT-5.5 model and snapshot](https://developers.openai.com/api/docs/models/gpt-5.5), [API pricing/models](https://developers.openai.com/api/docs/pricing), [API data controls](https://developers.openai.com/api/docs/guides/your-data). The catalog marks GPT-4.1 deprecated.

## Fresh-context independent review

Reviewer: OpenAI GPT-6.2 Astra, fresh context, one pass over sanitized facts only; no workspace or private-source access.

- **Blocking:** both answerer and evaluator need explicit recipient/endpoint/model/scope/retention approval. Empty trusted-evaluator registry means structural grading alone cannot establish answer quality.
- **Blocking:** current driver has no enforceable total spend ceiling; bound input/output and account for planner, answerer, evaluator and retries.
- **Blocking:** freeze exact model identity and evaluator config. The old evaluator candidate is deprecated. Calibrate the chosen judge on nonprivate adjudicated supported/unsupported/incomplete/citation cases.
- **Resolved for disposable-copy smoke:** FTS/vector/hybrid each returned a result on a temporary copy of the frozen index; the original cache corpus remains read-only in this sandbox because search needs a temporary write-intent lock. The local-path leak is fixed at 360c797. Running against the original writable cache path remains unverified here.
- **Major:** planner inference is additional work; report it and total spend by arm. A pack-limited evaluator cannot independently judge facts never delivered; keep completeness claims bounded and report structural/source-span reachability separately.
- **Major:** pre-register pilot selection and rerun policy; do not tune after pilot and present the same outputs as confirmatory. Normalize arm presentation to the evaluator where possible and disclose remaining cues.
- Reviewer agrees that synthetic 36/36 is pipeline evidence only and that stopping at the authorization gate is appropriate.

## Rollback and next action

- Code-only rollback: git revert 360c797.
- Documentation-only rollback: revert the handoff/report commit recording this gate; no benchmark inputs need reverting.
- Nothing was pushed, merged or released. No real private inference was made.
- **Stop until D1, D2, retention and an enforceable spend cap are approved.** Then run only the fixed 3×3 development pilot; proceed to 12×3 only if the preregistered operational gate passes.
