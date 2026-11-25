# RAG and Indexed Semantic Retrieval upgrade plan

## Goal

Find whether R4 fails because the answer agent cannot reason from its evidence, because retrieval omits or withholds required source, or because hybrid retrieval adds no value over a strong lexical baseline. Optimize for correct, complete, source-cited answers. Keep retrieval and answer quality as separate measurements.

## Phase 1 — Build a paired answer-quality evaluation

- Use the existing SWE-QA benchmark runner, independent judge, trial accounting, reference isolation, and artifact validation.
- Author and freeze 24 public-source questions, split 12 development and 12 validation. Cover direct lookup, multi-step or cross-file behavior, vocabulary-divergent questions, and explicit abstention. Freeze required behavior steps and accepted source ranges before running agents.
- Run the same agent/model/prompt with three evidence conditions: lexical `rg`, R4 hybrid, and a human-verified sufficient evidence pack. The third condition diagnoses answer-agent limits and is not a product retrieval baseline.
- Keep the answer agent blind to references and gold. Keep gold and rubric visible only to an independent grader. Blind condition names during grading.
- Score answer correctness, required-step coverage, citation support, source/deployment discipline, honest uncertainty, and unsupported claims per task. Report evidence sufficiency independently. Record agent and judge token use, calls, wall time, retrieval latency, and peak RSS.
- Reuse the benchmark contract limits of 10 items, 24 KiB final evidence, 3 rounds, 6 calls, 32 opened files, and 64 KiB cumulative source. Apply identical caps and navigation tools to all evidence conditions.

## Phase 2 — Diagnose and select one change

- On development tasks, trace each failed answer to evidence absence, retrieval acquisition, evidence selection, navigation, citation, or answer reasoning.
- Select one intervention by the most common supported failure: claim-to-source checking for answer reasoning, retaining useful retrieved fragments for selection loss, or bounded follow-up search for acquisition/navigation loss.
- Keep corpus revision, agent, model, prompt, route, and budgets fixed while comparing the intervention to both frozen baselines.
- Do not tune against validation results. Run validation once after the intervention and evaluation protocol are sealed.

## Phase 3 — Decision gates

- A validation gain requires at least two more fully correct tasks than both R4 and `rg`, preservation of all baseline-correct tasks, and no increase in unsupported claims or failed abstentions.
- Promote hybrid retrieval only when the paired answer results show a repeatable gain over lexical retrieval that justifies its measured latency and memory cost.
- If sufficient packs still produce incorrect answers, prioritize answer verification. If R4 answers fail only when required source is missing or omitted, prioritize retrieval or bounded navigation. If R4 ties `rg` on answer quality at higher cost, keep R4 opt-in or hold investment.

## First implementation slice

Add a frozen task-contract format and validation path to `benchmarks/swe-qa-bench/zg_bench/swe_qa/` that binds each task ID to the prompt, source revision, required behavior steps, accepted source ranges, and hashes. Keep the gold fields inaccessible to the answer runner. Validate asset isolation and protocol limits before enabling any model-backed trial. Then add the three evidence conditions through the existing pair runner and judge, preserving existing baseline and zvec-grep profiles.

## Acceptance

- Frozen assets validate and the answer runner cannot load gold or reference answers.
- All three conditions receive identical tasks, agent settings, and budget limits.
- The grader produces per-task scores with citation-to-source verification and separate retrieval/evidence sufficiency results.
- Development and validation aggregates, per-task failures, cost metrics, run identities, and hashes are reproducible from saved artifacts.
- Product changes remain opt-in until the validation gate passes.

## Execution checkpoint — 2026-11-23

Implemented in `benchmarks/swe-qa-bench/`:

- Frozen 24-task prompt, gold, and reference assets with source-lock-backed line ranges and SHA-256 manifest. The assets separate answer-visible prompts from grader-only gold and references.
- Contract validation for task split/type balance, abstention controls, source revision, file hashes, source line bounds, accepted-range mappings, and prompt/reference isolation.
- RAG evidence-pack validation, source-backed citation checks, required-step evidence coverage, and navigation-budget checks.
- A separate RAG answer judge for correctness, required-step coverage, citation support, source/deployment discipline, honest gaps, and unsupported claims. Its intervention gate requires at least two more fully correct validation tasks than both lexical and R4 baselines, preserves both baselines' correct tasks, and rejects increased failed abstentions or unsupported-claim cases.
- RAG-aware pair collection for `lexical-rg`, `r4-hybrid`, and `sufficient-pack`, including source-verified evidence sidecars, navigation usage, retrieval latency, and peak RSS.
- Normalization of exact lexical and R4 evidence ranges, including byte-offset snippets; source-backed sufficient packs; separate evidence-sufficiency reporting; and matched gold-blind Harbor datasets.
- A Harbor run path applying the same baseline agent/model settings to all three evidence datasets, with an optional fourth candidate profile for Phase 2/3; it checks assets and budgets before execution, collects multi-task jobs, and records reproducibility manifests.

Checkpoint verification:

- Task assets regenerated: 24 prompts and 24 gold records.
- `validate-rag-contract` passed against the frozen source lock and `/tmp/zvec-public-source-20261123`: 24 tasks, 12 development / 12 validation, grader-only gold separation.
- Retrieval ran on all 12 development prompts with the frozen lexical and R4 scripts. Gold-span evidence coverage was 3/10 complete answerable tasks for lexical `rg` (mean required-step coverage 0.30) and 4/10 for R4 hybrid (0.50). Gold-derived sufficient packs covered 10/10 answerable tasks and were empty for both abstention controls. These are evidence-coverage results, not answer-quality scores; see [`DEVELOPMENT.md`](../../research/rag-r4-answer-quality-20261123/DEVELOPMENT.md) and its raw manifests.
- Matched Harbor datasets were prepared for all 12 development tasks. The common prompt prefix is hash-checked across conditions; evidence is the only task-instruction difference. The dataset audit found no gold fields, references, or profile labels in agent-visible task files.
- Focused contract, pack-preparation, Harbor-dataset, judge, and pair-collector tests passed (11, 1, 1, 2, and 6 tests respectively); `git diff --check` passed.
- `git diff --check` passed.

## Phase 2 checkpoint — 2026-11-23

- Initial paired answer trials subsequently ran through the authenticated host Codex CLI because Codex in Harbor could not connect to the model service. The frozen answers use `gpt-6-luna`; the blinded batched grader uses `gpt-6.1-sol`. Codex subscription usage exposes tokens, not USD cost. Baseline answers used 849,700 input and 14,056 output tokens across the three conditions. The first grade used 12 judge calls, 386,393 input and 6,240 output tokens. See `research/rag-r4-answer-quality-20261123/DEVELOPMENT.md` and `development-answer-report-v3.json`.
- Failure diagnosis found R4 fragment-selection/retention loss on 4 tasks and route-file-hit/fragment-miss on 4 tasks. The lexical baseline had 9 selection losses and 2 acquisition misses. Sufficient-pack answers exposed 3 answer-side required-step misses. The evidence therefore supported one retrieval-selection experiment before an answer-side intervention.
- Implemented a dev-only candidate that alternates rank-ordered R4 snippets and lexical candidate windows, deduplicates overlapping ranges, and keeps the shared limits of 10 items and 24,576 bytes. Its source-sufficiency result was 4/10 complete answerable tasks (mean step coverage 0.50), equal to R4 and above lexical's 3/10, with 1.433 s measured combined retrieval time. It did not add evidence coverage over R4.
- To control model usage, only the 12 candidate answers were generated; the 36 existing baseline answers were reused after checking common instruction hashes. Answers used `gpt-6-luna` (candidate totals: 301,221 input and 4,764 output tokens). Candidate and baselines were graded together in 12 blinded calls using `gpt-6.1-sol` (442,007 input and 8,017 output tokens; USD cost unavailable from Codex OAuth).
- In that same batched grade, candidate was fully correct on 3/12 tasks, equal to lexical (3/12) and below sufficient-pack (9/12); it did not meet the dev screen of at least two additional fully correct tasks over both product baselines. It introduced one unsupported-claim case (`d05`) while lexical and sufficient-pack had none. R4 had two unsupported-claim cases and one failed abstention in this batch. Candidate evidence coverage remained 4/10 complete answerable tasks. This candidate is rejected; do not run validation for it or promote it.
- Candidate pack manifest SHA-256: `2430eb8d33710acdfb5d93cb9217550c2d618f14d250d2baab7f60061e2a9c3e` (`development-packs-candidate-v2`; records strategy/version and per-task raw candidate input hashes). Paired answer manifest SHA-256: `cacc929a27681cfebc8a5afce9924a2450a89b33e1784c5636805d374ad7a0bf`. Graded, deterministic-citation-refreshed report SHA-256: `b62b91afbb89091443875e086e2c48e7aa99eed5180168728cdee30210b254e5`.
- The Phase 2 candidate generator is an experiment harness only; no product retrieval defaults were changed. The plan stops at the dev gate: a new candidate needs a better selection/acquisition hypothesis and a sealed dev comparison before the one-time validation gate is appropriate.

## Indexed Semantic Retrieval — bổ sung vào kế hoạch

### Mục tiêu và trạng thái hiện tại

Xác định liệu Indexed Semantic Retrieval có tạo lợi ích riêng so với lexical `rg` ở khả năng đưa người dùng tới method hoặc đoạn mã giải thích đúng hành vi, và liệu bằng chứng bị mất khi tìm ứng viên, hợp nhất kết quả hay chọn fragment.

Các thử nghiệm trước đã kết luận `HOLD`: trên tập đã đo, R4 và `rg` cùng đạt 8/9 câu có evidence hữu ích và 3/9 câu đủ hỗ trợ. Bounded neighbor candidate không cải thiện điểm tác vụ, trong khi R4 tốn thời gian và RSS cao hơn. Phép đo hạng của evidence hữu ích đầu tiên chưa phân định được lợi thế S1. Tập cũ đã lộ kết quả, vì vậy không dùng lại nó để tuning hoặc tuyên bố validation mới. Chỉ mở một study độc lập nếu có nhu cầu sản phẩm riêng và owner chấp thuận phạm vi đó.

### Bước 1 — phân rã lỗi bằng artifacts hiện có

- Dùng trace nội bộ đã lưu và dữ liệu của RAG dev hiện có. Không rebuild index, đổi embedding, gọi model, hoặc chạy lại retrieval.
- Với mỗi câu và required step, ghi vị trí evidence đầu tiên trong các lớp đã được đo: fragment giao cho agent, fragment khác trong entity hybrid đã thu hồi, entity hoặc file khác trong kết quả hybrid, rồi các route FTS/vector thô nếu trace có sẵn.
- Phân loại đóng góp lexical-only, vector-only, cả hai route cùng tìm thấy, mất khi hợp nhất, mất khi chọn fragment, và thiếu ở mọi route. Không suy luận route nội bộ từ pool top-50 proxy. Ghi “không có trace” khi chưa biết.
- Tính số task và số step riêng. Đo giao nhau theo dòng lẫn đầy đủ vùng nguồn được chấp nhận, thử mọi source range thay thế hợp lệ dưới cap 10 items và 24.576 bytes. Đây là trần gold-guided để chẩn đoán, không phải kết quả retrieval hoặc chất lượng câu trả lời.
- Xuất bảng từng task/step, hạng đầu tiên có evidence, route tạo ra evidence, byte cần đọc, trạng thái đủ/thiếu và nguồn dữ liệu dùng. Nếu không đủ dữ liệu để xác định thứ tự hạng hoặc byte, giữ trạng thái chưa xác định.

### Bước 2 — cổng chọn hướng kỹ thuật

Chỉ chọn một candidate nếu phép phân rã cho thấy nó có thể khôi phục đầy đủ nguồn cho ít nhất hai task dev còn thiếu và không làm mất nguồn của task R4 đã đủ. Chọn cơ chế ở tầng hẹp nhất có bằng chứng:

| Bằng chứng trong trace | Candidate duy nhất được phép xem xét |
|---|---|
| Entity đã xếp hạng có sibling fragment đủ nguồn nhưng fragment giao ra thiếu | Chọn fragment tốt hơn trong các fragment cùng entity đã thu hồi. Ưu tiên method/khai báo khớp tên symbol, sau đó route-rank hợp nhất ổn định; không dùng vector score riêng nếu chưa có hợp đồng thứ tự được xác minh. |
| Route FTS/vector có method đủ nguồn nhưng hybrid hợp nhất làm mất nó | Giữ metadata hạng theo route và thử rank fusion ở cấp method trong isolated harness. Không thay index hoặc embedding. |
| Không có đủ nguồn trong kết quả route hiện có | Không thử re-ranking hay neighbor expansion. Đánh dấu cần benchmark độc lập để đánh giá acquisition. |
| Không candidate nào đạt ngưỡng khôi phục hai task hoặc không thể xác định từ trace | Kết thúc tại `HOLD`, không phát sinh model call. |

Không ghép fragment reselection, rank fusion và neighbor expansion trong cùng một candidate. Giữ nguyên kết quả baseline, API công khai, schema index, model, prompt và nguồn. Candidate phải chịu cùng cap pack và navigation. Nếu candidate chỉ có thể đánh giá trên tập đã lộ, báo đó là phép đo chẩn đoán, không gọi là validation.

### Bước 3 — đánh giá có điều kiện và chi phí

- Trước khi gọi model, kiểm tra offline rằng candidate khôi phục ít nhất hai task đủ evidence, giữ task R4 đã đủ, không vượt cap và không làm tăng evidence gây hiểu nhầm theo adjudication độc lập.
- Nếu qua cổng offline và còn phù hợp với giới hạn của benchmark, sinh 12 answer trials candidate bằng `gpt-6-luna`, tái sử dụng baseline chỉ khi task, prompt, model và instruction hashes khớp. Chấm cùng batch bằng `gpt-6.1-sol`, một judge call cho mỗi task. Ghi token và call count; USD cost từ Codex OAuth hiện không đo được.
- Candidate chỉ qua dev khi fully-correct tăng ít nhất hai task so với cả lexical và R4, giữ toàn bộ task baseline đã đúng, và không tăng unsupported claims hoặc failed abstentions. Candidate trượt thì dừng, không chạy validation.
- Chỉ khi có split độc lập chưa lộ, protocol được đóng băng và candidate qua dev mới chạy validation đúng một lần với cùng giới hạn. Ngưỡng validation vẫn theo Phase 3 ở trên. Kết quả trên split Indexed cũ không được tính như validation mới.

### Đầu ra và kiểm tra

- Ghi diagnostic JSON/Markdown có input hashes, trace coverage, per-task/per-step attribution, caps, confidence và giới hạn suy luận. Không tạo hoặc rebuild persistent index.
- Kiểm tra trường hợp nhiều step trong một task, nhiều range thay thế, fragment chồng lấn, entity/file có nhiều symbol và evidence chỉ chạm một dòng nhưng thiếu context.
- Cập nhật quyết định cuối theo một trong ba trạng thái: `HOLD` nếu không có giả thuyết phân biệt được, `DEV_CANDIDATE_REJECTED` nếu candidate trượt dev, hoặc `READY_FOR_INDEPENDENT_VALIDATION` nếu đủ mọi điều kiện mở split độc lập.

### Kết quả thực thi offline — 2026-11-24

- Đã tạo [`indexed-semantic-rag-crosswalk-v1.json`](../../research/rag-r4-answer-quality-20261123/indexed-semantic-rag-crosswalk-v1.json), ghép 10 task Indexed với 18 required source ranges, route-rank proxy, delivered range rank, byte-through-rank, hybrid internal target entities/fragments khi trace có dữ liệu, adjudication và 32 required-step rows từ RAG dev. Artifact lưu SHA-256 của từng nguồn.
- Indexed set ghi nhận R4 và `rg` đều useful trên 8/9 task answerable, complete trên 3/9. R4 tới target file sớm hơn ở val-03/04/06/07/10; `rg` sớm hơn ở val-01/09. Target-file rank không được diễn giải thành actionable-entry-point rank. Hai route pool là top-50 proxy, không phải adaptive candidate map.
- Có direct internal evidence cho delivery-selection loss ở val-03: hai fragment sibling trong entity hybrid đã phủ hai required ranges nhưng fragment được giao không phủ chúng. Val-04 và val-10 còn thiếu nhiều nhánh với một số non-final joins không đầy đủ. Val-08 không có target file trong hai direct route pools, nhưng helper routes hiện diện và gold scope có uncertainty. Vì vậy chưa có bằng chứng đủ để chọn thay đổi acquisition hoặc fusion tổng quát.
- Các trace này đến từ split đã lộ. Kết quả product-reality review trước đó ghi `NO_INCREMENTAL_VALUE_OBSERVED`/`HOLD`; bounded-neighbor C không cải thiện task-level usefulness/completeness trên validation. Giữ quyết định `HOLD`, không mở thêm candidate hoặc validation trên split cũ. Indexed search trên workspace vẫn lỗi `open_fts_indexers`; không rebuild persistent index trong bước này.
- Phần RAG dev trong crosswalk giữ nguyên 32 hàng evidence theo step từ diagnosis hiện có. R4 có 4 `hybrid_fragment_selection_loss` và 4 `route_file_hit_fragment_miss`; lexical có 9 `lexical_selection_loss` và 2 `lexical_acquisition_miss`. Candidate interleave bị loại ở checkpoint Phase 2 phía trên. Không có model call hoặc retrieval rerun trong phép crosswalk này.
- Crosswalk SHA-256: `6064609d1489807e5d15924e2c21d66c8e2c64a3597c4794bc1c2f1028ffd894`. Kết quả thực thi phần offline kết thúc ở `HOLD`; chỉ xem xét study độc lập nếu có nhu cầu sản phẩm riêng và sampling được đóng băng trước khi mở gold.
