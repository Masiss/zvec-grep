# Handoff — next session (2026-11-21, semantic evaluator pilot ready)

> **Latest continuation — D12 diagnostic (2026-11-22):** From R4 base HEAD `67ee809a9efb412ce2c3a1790680c947b2a0f2de`, exactly two answerers and one fresh-context reviewer completed the D12 retrieval-vs-reasoning diagnostic. Current conclusion: **INCONCLUSIVE (mixed acquisition/task-ambiguity and answer-completeness/citation signals)**. Read `rust/docs/r4-d12-retrieval-vs-reasoning-20261122.md`. A abstained citing the missing procedure name and did not search; B received source-reference content but the SQL excerpt metadata exceeded the frozen span due CRLF line counting. No further answerers were run. Resume from current branch HEAD. No code, HRM2.0 source, benchmark, or persisted index changed.

> **Previous continuation (2026-11-22):** The native-subagent D01/D06/D12 × A/B/C pilot has been analyzed and independently reviewed against local raw artifacts. Pilot analysis started from base R4 HEAD `44f9640c50494af4bafb5d06f96fd89f96453e44` and is now recorded at the current HEAD of this branch; resume from current HEAD. The latest decision is **BLOCKED** for a budget-auditable paired rerun and **NO_GO** for arm-winner or answer-quality improvement claims. Read `rust/docs/r4-subagent-pilot-results-20261122.md` first for the current, detailed Vietnamese report. This continuation supersedes the older PILOT_READY and owner-gate statuses below. No rerun or code change was made.

Single entry point. Read this, then
`rust/docs/r4-semantic-evaluator-pilot-20261121.md` (report: SE-01/SE-02,
C3 audit, pilot, review) and, for the earlier round,
`rust/docs/r4-semantic-evaluation-20261121.md`.

Status: **PILOT_READY — REAL ANSWER QUALITY NOT_MEASURED.** The evaluator
workflow can now represent and grade "correct but incomplete" and "stale
evidence" distinctly from "correct" and "wrong", but no production evaluator is
registered and no real Arm A/B/C answer quality exists. No `RAG_MVP_GO`, no
architecture winner.

## 1. Repository state right now

| Item | Value |
|---|---|
| Active worktree | `/home/masis/src/zvec-grep-r4-evidence-20261119` |
| Branch | `work/r4-evidence-representation-20261119` |
| HEAD | the handoff commit (parent `5b93300`) — **not pushed**, 6 commits ahead of `origin`: `069f6a8`, `8dd396c`, `bacfa36`, `75563f0`, `5b93300`, then this handoff |
| Other worktree | `/home/masis/src/zvec-grep` on `main` (`2d7d2ac`) |
| Worktrees | exactly two |
| Working tree | clean |
| Disk | 35G free (93%) |
| Registry | `benchmarks/rag-impact/cases/evaluators.json` still **empty** |

Snapshot of the round, if the worktree moves: `~/.cache/zvec-grep/r4-semantic-eval-20261121/` (code state `5b93300`, before this handoff commit; HEAD is only the handoff doc on top).

Rule that still holds: at most two local worktrees; push a branch before any
worktree removal. Nothing in this round was pushed, merged or released.

## 2. What this round finished

1. **SE-01 — correct-but-incomplete verdict.** The evaluation contract gained a
   required `omissions[]`. `PASS` requires `completeness = 2`, no omissions and
   no unsupported claims; `FAIL` is valid with an unsupported claim **or** an
   omission; `completeness = 0` requires an omission. Grader and `accept`
   enforce the same rules, so bypassing `accept` does not lift an incomplete
   answer.
2. **SE-02 — stale evidence fails closed.** A contentless pack item is re-read
   from the corpus only when its hash still matches the frozen pack hash; stale
   text is withheld (`stale_source`, no content) and a forged "current" packet
   is refused. No frozen hash at all → text withheld and marked
   `unverifiable_source`.
3. **C3 completeness audit.** `tools/audit_c3_anchoring.py`: 49 required steps
   over D01–D12, 29 addressable by the frozen token locator, 20 not (15
   no-identifier, 5 outside-gold), corpus bytes verified against the manifest.
   Proven: a D07 answer omitting step 3 still reaches 12/12 under a PASS
   evaluation. Gold/checklist/thresholds unchanged; the grader now reports
   `unmeasured_required_steps` (id + reason only), and the evaluator's
   `completeness` is the compensating control.
4. **Pilot.** `tools/r4_semantic_pilot.py`: five synthetic scenarios through the
   real packet/accept/grader — complete PASS, incomplete FAIL
   (`unsupported = []`, `omissions = ["Name"]`), wrong FAIL, wrong-citation
   FAIL, stale FAIL — asserted on the distinguishing fields, with a
   contrived-PASS counterfactual showing structure alone would accept the
   incomplete answer.
5. **Independent review** (DeepSeek Flash 4.1 High, one pass): no P0; 4 P1 + 6
   P2. All material findings fixed and pinned (PASS `completeness = 2`, C2 no
   longer drops on an omission-only FAIL, no-hash stale branch, pilot
   assertions). P2 residuals are stated in the report §7.

## 3. Environment (unchanged, still important)

```bash
export LD_LIBRARY_PATH=/home/masis/.cache/zvec-grep/rust-target-recovered-20261105/debug/build/zvec-rust-sys-9d9cb66e2b7fe09f/out/zvec-prebuilt
export ZG_ORT_CACHE_DIR=/home/masis/.cache/zvec-grep/rust-target-recovered-20261105/ort-prebuilt
export CARGO_TARGET_DIR=/home/masis/.cache/zvec-grep/r4-evidence-20261119/target
```

**Never delete**: the `zvec-prebuilt/` and `ort-prebuilt/` above, the frozen
corpus `~/.cache/zvec-grep/r4-sql-symbols-20261118/corpus`, and every `r4-*`
receipt/bundle directory. The two large `deps` (9.0G + 8.6G) are kept
deliberately; removing them frees ~17G and costs ~10 min of recompilation.

## 4. Reproduce / verify

```bash
cd /home/masis/src/zvec-grep-r4-evidence-20261119
C=/home/masis/.cache/zvec-grep/r4-semantic-eval-20261121

python3 benchmarks/rag-impact/tools/r4_grade_selftest.py \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus /tmp/g   # 89/89
python3 benchmarks/rag-impact/tools/r4_semantic_evaluator_selftest.py /tmp/se  # 57/57
python3 benchmarks/rag-impact/tools/r4_bench_driver_selftest.py \
  $CARGO_TARGET_DIR/debug/r4-adapter \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus /tmp/h  # 79/79
python3 benchmarks/rag-impact/tools/r4_semantic_pilot.py --work $C/pilot  # 5/5
python3 benchmarks/rag-impact/tools/audit_c3_anchoring.py benchmarks/rag-impact \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus --out $C/c3-audit.json
python3 benchmarks/rag-impact/tools/r4_semantic_e2e.py \
  --corpus /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus --work $C/e2e
```

Expected: 89/89, 57/57, 79/79, pilot 5/5, audit 29/49 with 20 reported, E2E
exit 0 with 36 runs, 0 violations, 12/12 plan chains, 36/36 accepted
evaluations, 7/7 fixtures, no formal PASS without an evaluator.

Adapter tests (unchanged): `cargo test -p zg-r4-adapter` → 2 + 51 passing.

## 5. Owner decisions that unblock a real run

**D1 — register one semantic evaluator.** Candidate configs:
`benchmarks/rag-impact/cases/evaluator-candidates.json`. The prompt digest every
packet records and checks is
`4e9e20413c8ae2172844298ed743ab24c5fb79c0a36defeb957bee0c4f28cd55`
(`r4_semantic_evaluator.INSTRUCTIONS`, 2,974 bytes). Registration shape and the
updated procedure are in `cases/evaluators.json`; add `trusted: true` plus the
fingerprint, model/served identity and kind. The workflow never writes this.

**D2 — authorize private-source inference** if the evaluator is remote:
provider, exact model + served identity, endpoint, and the data classes in the
report §6. The packet never contains gold, the C3 checklist, the answerer
session or corpus outside delivered items/citations.

**D3 — run the real three-arm benchmark**: 12 cases × 3 arms through the driver
with `--backend openai-responses --authorized-inference`, then one grader
process per frozen pair with `--semantic` and the registry. Report structural
and semantic layers separately; a formal PASS needs both. Cost estimate for the
evaluator alone: ~$0.87 for 36 evaluations at the illustrative 2/8 USD per 1M
in/out rate card (~$0.024/evaluation; 331,949 estimated prompt tokens +
25,200 output).

## 6. Do not retry

- Changing gold, the C3 checklist or thresholds to make the 20 unanchored steps
  measurable; that is a rubric change for the owner, not a correction.
- Re-introducing an exclusive `end_line` convention: adapter packs are inclusive,
  and the old `end - 1` normalisation was a reproduced false negative.
- Accepting an evaluation without the five dimensions, or a PASS with
  `completeness < 2`; those were review findings and are pinned by fixtures.
- Treating the pilot judge or the loopback judge as answer-quality evidence.

## 7. Where the evidence lives

| What | Where |
|---|---|
| SE-01/SE-02, C3 audit, pilot, review | `rust/docs/r4-semantic-evaluator-pilot-20261121.md` |
| Earlier round (packet/accept, W1–W13) | `rust/docs/r4-semantic-evaluation-20261121.md` |
| Selftest / pilot / audit / E2E receipts | `~/.cache/zvec-grep/r4-semantic-eval-20261121/` |
| Sanitized delta bundle + zip | `~/.cache/zvec-grep/r4-semantic-eval-20261121/bundle-pilot-final/` / `r4-semantic-pilot-final-20261121.zip` (sha256 `fb3268dad8ef8e736482f85932a7177969a94706f3ac90be10890b465b66df3a`) |
| Evaluator/pilot/audit/E2E tools | `benchmarks/rag-impact/tools/` |
| Frozen corpus (read-only) | `~/.cache/zvec-grep/r4-sql-symbols-20261118/corpus` |

## 8. First thing next session

Do not add infrastructure. Either get D1+D2 from the owner and run D3, or stop
at the authorization gate. The next milestone is **measured Arm A/B/C answer
quality**, not more schema.


## Continuation update — 2026-11-22 (real-agent RAG evaluation)

The evaluator and benchmark remain **PILOT_READY at the harness level; real answer quality NOT_MEASURED**. No private inference was run. The current gate report is rust/docs/r4-real-agent-rag-evaluation-gate-20261122.md; the owner decision form is rust/docs/r4-real-agent-rag-approval-package-20261122.md.

Readiness checks found that the frozen index and local Potion vector route execute on a disposable copy; the original cache corpus is read-only in this sandbox. The unrelated main workspace FTS index still fails to open. A complete Potion artifact is present in an existing local experiment cache, so the older “missing artifact” note is stale for this host, though the exact selected artifact path was not logged.

A mock test reproduced an absolute corpus-root leak in an adapter error sent as a model tool result. The boundary now replaces corpus and session roots before sending tool output; local transcripts retain the original. Fix checkpoint: 360c797. Offline checks after the fix: grader 89/89, evaluator 57/57, driver 79/79, synthetic pilot 5/5 and synthetic E2E exit 0. These remain harness results only.

Fresh-context review found the real remaining blockers: D1 evaluator identity/trust, D2 private-source inference approval, verified retention, and an enforceable all-in cost ceiling. The existing GPT-4.1 evaluator candidate is deprecated. No production evaluator is registered.

**Next action:** wait for the owner to fill/approve the decision form, including an all-in USD cap. Before any private request, implement and verify request/output-token bounds and a pre-call budget guard. Then run only the fixed 3×3 development pilot; proceed to full 12×3 only if the preregistered operational gate passes. Keep H01–H08 sealed. Do not treat synthetic E2E as answer-quality evidence.


## Continuation update — native Codex subagent pilot readiness (2026-11-22)

The current checkpoint remains 135844ed before this handoff-only update. Native Codex delegation was verified with a fresh-context reviewer using sanitized pilot information only. No HRM2.0 case, source, evidence, gold or checklist was sent to a subagent; no answerer/evaluator or Responses API call was run.

Tool availability: native rg, the collaboration spawn interface, R4 adapter CLI, and MCP zvec_grep_search are present. The adapter exposes search/symbol/facts/evidence/verify/workflow/status. The host has no zvec_grep_rg MCP tool; the current workspace FTS failure remains separate from the frozen R4 index.

The prepared exploratory pilot uses D01/D06/D12 × Arms A/B/C, nine fresh answerer contexts and three separate blind Arm-B plans. Evaluators would run only after all answers, one packet at a time, without gold/checklist/peer outputs. The frozen budgets remain targets; do not claim they are enforced uniformly by native subagents.

Key limitation: this session's subagent interface provides no per-agent tool allowlist/filesystem root, and the parent cannot independently see complete per-tool traces, token usage, cost or served-model fingerprint. Fresh context is not filesystem isolation. Arm A shell operations and unmediated Arm C activity are not covered by the R4 adapter ledger. Any pilot under these conditions is exploratory, not formal trusted evaluation. Full sanitized readiness and reviewer findings are in rust/docs/r4-codex-subagent-pilot-readiness-20261122.md.

**Stop here:** no private inputs go to subagents until the owner explicitly approves that data flow. Do not poll approval fields or use Responses API. Preserve H01–H08 and do not claim arm superiority from a three-case exploratory pilot.


## Continuation update — native Codex-subagent pilot results (2026-11-22)

The exploratory D01/D06/D12 × A/B/C pilot has completed using native Codex subagents; no Responses API calls were made. Results, per-case structural scores, pack-budget violations, evaluator judgments, model-identity limits, tool receipts and independent review are in rust/docs/r4-subagent-pilot-results-20261122.md. Raw private artifacts remain in a local temporary directory; the sanitized artifact manifest SHA-256 is 2312c6bfda87489b1d65ae30353853f2b7e1656d36b21b0419f594bcdeed8535.

Decision: **INCONCLUSIVE for comparative RAG answer quality.** Six gradeable structural cells all failed; three cells were ungradable; zero semantic verdicts were trusted. Requested answerer/planner model was gpt-6-luna/high and evaluator request gpt-6.2-sol/high; Codex accepted these requests but did not expose served snapshots, actual reasoning identity, token usage or per-agent latency. No fallback was used. Three semantic judgments were accepted by the local validator but rejected by the empty trusted-evaluator registry. Several search-derived packs exceeded 24 KiB/10 items, and one case/arm had no adapter receipt.

The reviewer found the run useful for process observations only. Shared workspace/tool access means gold/C3 isolation cannot be independently proven; pack reconstruction and unverified receipts further limit formal comparison. No frozen benchmark, source, index, embedding, production default, or API was changed.

**Stop at this owner gate.** Do not claim an arm winner or formal answer-quality result. No further infrastructure work or cases are in scope for this run.

## Continuation — R4 native-subagent pilot failure analysis (2026-11-22)

Đọc `rust/docs/r4-subagent-pilot-results-20261122.md` để xem phân tích tiếng Việt chi tiết và sanitized review bundle. Raw private evidence vẫn chỉ lưu local; report ghi raw manifest SHA-256 `2312c6bfda87489b1d65ae30353853f2b7e1656d36b21b0419f594bcdeed8535`, không chứa source/question/gold text.

**Trạng thái hiện tại: BLOCKED cho paired rerun có so sánh budget; NO_GO cho claim arm winner/answer-quality improvement.** Sáu cell có structural grade đều fail, ba ungradable; không có semantic verdict trusted. D01/A, D01/C, D06/C, D12/A có search-derived pack dựng hậu kỳ; pack cap breach chỉ chắc chắn ở artifact chấm, chưa chứng minh payload answerer đã nhận vượt cap. D12/C ledger ghi 6 calls, 3 rounds, 21 read ops, 16 files, 63,368 bytes, đọc 2/2 gold files nhưng không có pack/final receipt (`terminal=null`).

Root causes đã thấy: D01/B final schema sai kiểu; D01/C evidence request sai address schema và adapter fail-closed; D06/A có 5 lỗi cú pháp CLI và một `--help` (việc thiếu grammar trong prompt chỉ là giả thuyết); D06/B lần đầu không tạo session, nguyên nhân chưa xác minh, rồi retry thành công; D12/B đọc relevant file nhưng pack không chứa gold-file coverage; D12/C adapter trace có search results nhưng không có successful evidence/verify/finalize hay pack. Các B workflow không lưu candidate pool nên chưa phân biệt được acquisition miss với selector omission. Không sửa source/retrieval/ranking; source diff rỗng.

Fresh-context OpenAI reviewer đã kiểm tra raw local artifacts trong phạm vi D01/D06/D12: 127/127 manifest files khớp digest; diff từ pilot checkpoint `bad498e` chỉ gồm hai tài liệu. Reviewer xác nhận phải phân biệt selected content bytes với JSON disk bytes; D12/C có adapter trace 7 envelopes/6 charged calls nhưng không có native transcript, delivery confirmation, pack hoặc final receipt. D12/A citation resolve/hash_ok nhưng `pack_verified=false`; exploratory PASS không xác nhận frozen completeness. Findings chi tiết đã sanitize trong report. Offline negative controls tái lập fail-closed cho D01/B schema, D01/C address, hai D06/A CLI syntax forms và D12/C missing pack; không synthetic PASS, không sửa source. Reviewer không yêu cầu code patch; không có source diff. Paired rerun nhỏ nhất đáng cân nhắc là D01 × A/B/C sau preflight offline, **chỉ** khi cap được enforce tại delivery boundary và exact serialized payload được lưu. Nếu native subagents không hỗ trợ, dừng, không dựng framework mới.

Model settings `gpt-6-luna/high` cho answerers/planners và `gpt-6.2-sol/high` cho evaluators chỉ là requested settings; served identity, usage, cost, latency không được lộ. Không có Responses API, không mở H01–H08, không đổi evaluator registry, không push/merge/release. Báo cáo và handoff được checkpoint tại commit sẽ ghi trong kết quả bàn giao; checkpoint chỉ gồm hai tài liệu.


## Continuation — D12 retrieval vs. reasoning diagnostic (2026-11-22)

Báo cáo tiếng Việt đã sanitize: `rust/docs/r4-d12-retrieval-vs-reasoning-20261122.md`. Kết quả chính: **INCONCLUSIVE**, có tín hiệu mixed. A chỉ gọi status rồi abstain, nêu câu hỏi thiếu proc identifier; điều này gợi task ambiguity nhưng không kiểm tra được năng lực retrieval engine vì không có search query. B nhận hai source excerpts và thận trọng không khẳng định runtime, nhưng bỏ sót `Organizations.TotalEmployee` effect. Oracle SQL excerpt được dựng từ file hash đúng nhưng line extractor tách CRCRLF thành hai dòng; header ghi `1–205` dù frozen span là `1–95`, nên citation B không hợp lệ và có thêm 8 dòng ngoài frozen span. C3 steps 1–2 được bao phủ trong source; step 3 thiếu variant inventory; step 4 cần deployment/catalog/runtime evidence.

Một fresh-context reviewer blind X/Y kết luận `RETRIEVAL_LIMITED` dựa trên chênh lệch evidence. Sau khi đối chiếu line range và việc A không thực sự search, kết luận bàn giao là `INCONCLUSIVE`: chưa đo retrieval engine và có cả omission/citation defect ở oracle answer. Reviewer không gán formal C1–C6. Requested answerer config giống nhau (`gpt-6-luna/high`), served identity/usage/latency không quan sát được. Shared workspace không có ACL per-agent.

Raw questions, source excerpts, answers và receipt vẫn local trong authorized temp storage; report chỉ có sanitized findings/digests. Hai hash source refs khớp; diagnostic index copy không đổi. Không gọi Responses API, không mở case khác, không sửa code/index/benchmark. Không chạy lại answerer theo stopping rule. Không có rollback vì không có code changes.

## Tiếp nối — kiểm tra RAG capture-first D01 (2026-11-23)

**Kết quả: LIMITATION_CONFIRMED cho đường Arm C được thử trên D01; quyết định sản phẩm: HOLD.** Đây là một tác vụ thăm dò, không phải paired benchmark chính thức và không đại diện cho toàn bộ RAG. Adversarial review cuối của GPT-6.1 Sol High trả INSUFFICIENT_EVIDENCE cho hiệu quả tổng quát, khuyến nghị đóng mission với lỗi truy xuất D01. Không chạy thêm answerer, không chạy D06/D12, không gọi thêm truy vấn, không sửa code, không gọi Responses API, không thay index persisted, push, merge hay release.

### Identity và môi trường chạy

- Frozen scope: D01 only; H01–H08 stayed sealed.
- SHA-256 corpus manifest: c706c8dd9b560572a2c1739c2c2bda676982f4c3b5da4cd4b32bcfeb66359cdb (manifest frozen 822 files). SHA-256 index manifest: f6f9695a9a4cf2f42ffbd36d13eed5110fe1c61a99be18508e8b7ee77ec05917; index v3; storage generation cea13512-733a-4c9f-96a7-5e131b57d1dc; Potion Code 16M v2, 256-D, CPU. Lúc chạy đã dùng một bản copy tạm dưới /tmp/zvec-rag-overnight-20261122/index-copy/corpus; copy này hiện không còn. Không rebuild; index v3 vẫn chưa qualified.
- Adapter binary có SHA-256 bdd6f6207e35757a0f6cec5797ca5d6b72739fea59257cc9c41b8e9535f810fd, mtime 2026-11-21 10:38 +07; native library SHA-256 5093c7d722304b0efaa7425f206456ca902dc377d5a0ae8056f1c5c010fe763e. Không xác minh được chính xác source commit đã build ra binary cache này.
- Answerers được yêu cầu dùng gpt-6-luna / reasoning HIGH. Không thấy served model identity, tokens, cost hoặc latency của answerer. Host không cung cấp ACL filesystem/tool theo agent hoặc transcript đầy đủ cho parent; mọi so sánh là exploratory.

### Probe Arm B và evidence Arm C thực tế

Một probe Arm B do controller chạy chọn 10 references, ghi 7 gaps sau 32 source reads; selected-content bytes là 15,648 và delivered envelope là 16,337 bytes. Các selected records không có literal source content. Profile Arm B chỉ cho workflow/verify/status, không cho evidence, nên output này chưa thể cấp source content cho answerer trong cùng session. Không chạy answerer Arm B.

Arm C chạy hai hybrid query chỉ dựa trên D01: “Form 1, update thông tin cá nhân đi qua đâu?” và “Form 1 update thông tin cá nhân”. Mỗi query trả 5 candidates; không query nào có EmployeeController.cs. Kết quả query thứ hai có candidate không liên quan EmployeeChangeRequestCommand.cs. Evidence call trả 2 verified spans — EmployeeChangeRequestCommand.cs:647–679 và WorkLogResource.cs:219–247 — trong pack 4,787 bytes / 2 items. Final answer cite đúng hai range/hash của pack nhưng nói rõ không đủ để xác nhận controller/API của Form 1.

Các digest ghi nhận tại thời điểm chạy: Arm-C receipt 5a68bf995b4519839b2d5dc9e52dfc6244eac0d1bafb201c993c24891e515dd3; trace 97659ba6d543f4bd2d1a2ba9a0c56f6e1b748696f10d8bc18dbf0baaa847c6c8; pack efe06eb807ed70ce34663470aeadee201caf827cefaa2e25232df6f0b021c1d8; answer e3c143c317d0219b49a78d3fae537cd0762409e25f725fc2923f6f47bf79ef17. Đây là digest lịch sử; files gốc không còn. Bundle metadata-only hiện lưu tại .tmp/rag-overnight-20261122/d01-capture-summary.json (SHA-256 abba226f9ced4d412464d68dd46896c57e87b828d09bf11a63093cf4deb33d0b). Các file run từng nằm trong /tmp đã biến mất sau runtime interruption. Không thể replay hoặc rehash raw receipt/trace/answer/pack; không dựng lại private source content từ trí nhớ.

Cộng dồn bảo thủ Arm C qua lần lock thất bại, status-only probe và session cuối: 4 charged calls / 3 rounds / 5 distinct source files / 13 read ops / 36,586 adapter-envelope bytes; pack cuối 2 items / 4,787 bytes. Tổng này nằm trong frozen R0 caps. Đây là số adapter ghi nhận, không chứng minh đầy đủ payload mà model thực sự thấy sau lớp shell wrapper. Arm A không có transcript/resource ledger xác minh được; usage và delivered-source membership không rõ.

### Correctness và failure attribution

Một Arm-A native Codex subagent fresh-context được yêu cầu cùng gpt-6-luna/high. Theo tự báo cáo, agent dùng rg và đọc source, mô tả flow EmployeeChangeRequest phê duyệt khác, trích controller/service liên quan nhưng không nêu Form 1 EmployeeEdit dispatch. Không có transcript hoặc usage để xác minh.

Sau khi answer được freeze, đánh giá C1–C6/C3 trên D01 xác định source route là EmployeeController.EmployeeEdit: Form == "1" dispatch tới EmployeeEditThongTinCaNhan, tách biệt create branch; method đích tra employee qua Context.Employees. Cả Arm A và Arm C đều không nêu đủ các bước bắt buộc. Automated projection báo C1=0/3 required steps có trong Arm-C pack. Projection cấu trúc của prose Arm C (chuẩn hóa sau khi nhận answer; JSON không do answerer phát ra) đo C1=0, C3=0, C4=2, C5=1, C6=2; C2 chỉ STRUCTURE_ONLY vì 2/2 citation khớp pack verified, nên tổng vẫn NOT_MEASURED. Một Sol evaluator chưa đăng ký chấm Arm C 7/12 exploratory FAIL: C1=0, C2=1, C3=0, C4=2, C5=2, C6=2; C6 có caveat về granularity. Evaluator Arm A chấm C3=0/2; C1/C6 ungradable do không có delivery capture; citation support partial. Không evaluator nào là trusted/formal.

Root cause: ACQUISITION — hai hybrid candidate sets đều bỏ sót controller cần thiết; SELECTION/DELIVERY — pack verified nhưng chứa flow EmployeeChangeRequest không trả lời Form 1; REASONING — Arm C biết abstain phần chính nhưng thêm liên hệ yếu, Arm A kể flow chi tiết hơn nhưng vẫn sai task. Đây là lỗi của D01 path đã thử, không phải bằng chứng mọi kiến trúc RAG thất bại.

**Sự cố evaluator isolation:** evaluator Arm C báo rằng lookup làm lộ checklist rows kế cận D02/D04 (và D12); evaluator nói không dùng chúng. Evaluator Arm A không báo trạng thái lộ rows kế cận nên vẫn UNKNOWN. Không đọc source D02/D04 hoặc H01–H08. Coi hai điểm số là exploratory/non-blind. Lần đánh giá sau cần packet evaluator chỉ chứa D01, không tìm trong file checklist nhiều case.

### Quyết định tiếp

Giữ R4 ở HOLD; không tốn thêm lượt D01 và không mở D06/D12 vì can thiệp đã thử không lấy được evidence dispatch cần thiết và chưa có value signal so với rg. Session D01 đã finalized; không reset R0 bằng session mới. Nếu owner muốn tiếp tục sau mission này, hãy preregister riêng giả thuyết literal-anchor routing, giới hạn truncation/candidate coverage và evaluator materials chỉ gồm D01 trước khi chạy answerer. Không có code/API/default/index thay đổi; dirty/untracked state của source-discovery worktree ban đầu được giữ nguyên.

**Hành động ngay:** đóng mission này ở trạng thái HOLD; không chạy thêm probe hoặc answerer. Thí nghiệm literal-anchor là quyết định mới của owner về sau, không được handoff này tự động kích hoạt.
