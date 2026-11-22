# Phân tích thất bại pilot R4 native subagents — 2026-11-22

## Kết luận điều hành

Pilot D01/D06/D12 × A/B/C đã chạy được như một phép thử quy trình, nhưng **không tạo bằng chứng đáng tin để xếp hạng answer quality giữa các arm**. Sáu cell đều trượt structural rubric; trong sáu base reports chỉ D06/B và D12/B có overall formal FAIL, bốn report kia là NOT_MEASURED. Ba cell còn lại thiếu artifact hợp lệ và ungradable. Ở ba augmented reports có evaluator packet, semantic status là INVALID và overall UNGRADABLE; production registry rỗng. Ba nhận xét semantic từ evaluator exploratory gồm hai FAIL và một PASS, nhưng grader chính thức đánh dấu cả ba là không tin cậy vì evaluator registry vẫn trống. Vì vậy số semantic verdict chính thức là **0**.

Quyết định: **BLOCKED** cho paired rerun có thể so sánh theo budget. Chưa chứng minh được cách giới hạn và lưu đúng bytes/items thực sự tới answerer trong native subagent workflow, cũng chưa có telemetry xác minh model snapshot, token usage hay isolation. **NO_GO** cho mọi tuyên bố arm winner hoặc cải thiện chất lượng từ pilot này. Một paired rerun hẹp D01 × A/B/C chỉ đáng làm sau khi delivery cap và exact serialized outputs trở nên kiểm toán được; nếu không làm được bằng công cụ sẵn có thì dừng hướng so sánh native-subagent có kiểm soát.

Không sửa source code, retrieval/ranking, index, embedding, production default, frozen case, gold hay rubric. Không gọi Responses API, không mở H01–H08, không push/merge/release. Raw private artifacts vẫn ở local; tài liệu này chỉ chứa số liệu đã làm sạch và digest.

## Phạm vi và model identity

- Answerers và Arm-B planners được spawn với yêu cầu `gpt-6-luna`, reasoning `high`; evaluator subagents được yêu cầu `gpt-6.2-sol`, `high`.
- Spawn interface chấp nhận yêu cầu trên; không dùng fallback. Runtime chỉ lộ nhãn chung “Codex based on GPT-6”, không có served snapshot hoặc xác nhận reasoning thực tế. Đây là **requested configuration**, không phải model identity đã xác minh.
- Native Codex không cung cấp per-agent token/cost hoặc timing đáng tin. Latency, token usage, cost và memory theo từng agent: **không đo được**.
- Không có Responses API call. Evaluator registry production tiếp tục trống. Digest hướng dẫn evaluator exploratory: `4e9e20413c8ae2172844298ed743ab24c5fb79c0a36defeb957bee0c4f28cd55`.

## Bảng 9 cell

`Đọc` là số thao tác đọc qua adapter ledger; `ledger bytes` là tổng bytes kết quả được ledger ghi. Với Arm B, `selected_bytes` là accounting của selector, còn kích thước JSON trên đĩa được ghi riêng; không đại lượng nào tự chứng minh native answerer đã nhận payload chính xác đó. “Giao gold interval” nghĩa content range giao source span đã freeze; không đồng nghĩa span đầy đủ được đưa vào một item. Pack A/C hậu kỳ được dựng từ nhiều search envelopes sau execution. Confidence là mức chắc chắn của phân loại nguyên nhân, không phải xác suất thống kê.

| Case/arm | Kết quả và artifact | Evidence tiếp cận gold | Root cause chính | Tin cậy |
|---|---|---|---|---|
| D01/A | 4 adapter ops, 22 read ops, 13 files, 65,347 bytes. Có abstain và một claim. C1–C6 `0/0/0/2/0/2` = 4/12. Pack hậu kỳ: 37,855 bytes trên đĩa, 27,299 UTF-8 content bytes/19 items; vượt byte và item cap trong artifact chấm, nhưng không có bằng chứng đó là payload đã giao nguyên khối. | Search output chạm 1/2 gold files nhưng giao gold interval 0/2; excerpt ranges không phủ source spans. | **Acquisition/range miss** đã thấy trước reasoning; thiếu completeness. Pack hậu kỳ quá lớn làm score không còn budget-compliant. | Cao về span miss và pack size; trung bình về ảnh hưởng lên answerer vì pack là hậu kỳ. |
| D01/B | Workflow/status/verify; 5 internal calls, 36 read ops, 26 files, 14,515 bytes. `abstain` trong final là string thay vì JSON boolean; ungradable. Arm-B selector: 24,431 selected_bytes, 10 items; JSON pack 9,656 bytes trên đĩa. | Đọc 2/2 gold files; pack chạm 1/2 gold files nhưng giao gold interval 0/2. Candidate pool trung gian không được lưu. | **Execution/schema**: output sai kiểu. Với evidence, chưa tách được engine acquisition miss khỏi selector omission. | Cao về schema failure; thấp về pha evidence gây mất span. |
| D01/C | 5 successful ops, 13 read ops, 8 files, 44,377 bytes. Abstain. Pack hậu kỳ: 23,117 bytes trên đĩa, 17,523 UTF-8 content bytes/10 items, unverified; không vượt các cap tính theo artifact này. | Search/read chạm 1/2 gold files nhưng giao gold interval 0/2. Hai evidence requests bị từ chối: `unknown address mode`, `each request needs an address`. | **Tool-call construction/interface** gây adapter fail-closed; retrieval vẫn chạy nhưng không đưa được request evidence hợp lệ. | Cao. |
| D06/A | Agent trace ghi 6 CLI invocations: 5 lỗi cú pháp và một lần `--help`; không có adapter session receipt, ledger hay pack. Ungradable. | Không có adapter evidence để xác nhận đã đọc gì. | **Invocation failure** đã được xác nhận; thiếu CLI grammar trong prompt là giả thuyết, không phải nguyên nhân duy nhất đã chứng minh. D12/A chạy được sau khi nhận cú pháp cụ thể chỉ là quan sát ở cell khác. | Cao về invocation; vừa phải về prompt là nguyên nhân. |
| D06/B | Lần đầu không tạo session; một retry chạy xong. 5 internal calls, 20 read ops, 14 files, 9,914 ledger bytes. Abstain; C1–C6 `0/0/0/2/2/2` = 6/12, formal FAIL. Arm-B selector: 24,564 selected_bytes/6 items; JSON pack 6,839 bytes trên đĩa. | Đọc 1/2 gold files; pack chạm 1/2 files nhưng giao gold interval 0/3. Candidate pool không được lưu nên chưa biết span bị acquisition hay selection bỏ. | **Launch failure chưa rõ nguyên nhân**: lần đầu không có session, retry có receipt; sau đó evidence coverage thiếu. Không quy lần đầu cho lỗi prompt hay adapter khi chưa có receipt. Answer cuối được tạo ở follow-up không có thêm source calls. | Cao về retry và pack; thấp về acquisition-vs-selection. |
| D06/C | 6 ops, 17 read ops, 11 files, 42,267 bytes. Một claim, không abstain. C1–C6 `0/0/0/2/1/2` = 5/12. Pack hậu kỳ: 19,330 bytes trên đĩa, 12,620 UTF-8 content bytes/13 items; vượt item cap trong artifact chấm. | Search/read chạm 1/2 gold files nhưng giao gold interval 0/3. | **Acquisition/range miss** cùng với **answer completeness** thấp. Evaluator exploratory xác định một claim không được hỗ trợ và một omission. | Cao về recorded overlap, vừa phải về phân loại claim/omission vì evaluator chưa trusted. |
| D12/A | Status + 3 search; 11 read ops/files, 26,169 ledger bytes. Abstain và có một claim. C1–C6 `0/0/0/0/0/2` = 2/12. Pack hậu kỳ: 16,793 bytes trên đĩa, 11,288 UTF-8 content bytes/11 items; vượt item cap trong artifact chấm; citation source resolve/hash_ok nhưng `pack_verified=false`, 0 citation hợp lệ/1 không hợp lệ. | Search/read và pack có content range giao 1/2 gold intervals; 0/2 span được khớp exact hoặc được một item bao trọn. Span còn lại không tới pack. | Chỉ giao 1/95 dòng của interval thứ nhất và không giao dòng nào trong interval thứ hai; chưa có required span nào được bao trọn. Đây là cell có exploratory semantic PASS nhưng structural evidence chỉ 2/12 và pack quá item cap; kết quả bất đồng không được giải bằng cách ưu tiên semantic PASS. | Cao về số đo; thấp về độ tin cậy của semantic PASS. |
| D12/B | Workflow/status/verify; 5 internal calls, 33 read ops, 28 files, 12,427 bytes. Abstain; C1–C6 `0/0/0/2/2/2` = 6/12, formal FAIL. Arm-B selector: 24,562 selected_bytes/5 items; JSON pack 8,562 bytes trên đĩa. | Đọc 1/2 gold files; pack chạm 0/2 files, exact spans 0/2. | Relevant file đã được đọc nhưng không xuất hiện trong pack: **delivery/selection loss** sau read. Không có candidate trace để phân biệt selector với bước xây pack. | Cao về read/pack chênh lệch; vừa phải về vị trí chính xác lỗi. |
| D12/C | Ledger: 6 charged calls, 3 rounds, 21 read ops, 16 files, 63,368 bytes. Adapter trace: 7 envelopes (4 search success/29 content items, 1 invalid search, 1 symbol, 1 status); `packs=[]`, `terminal=null`, thiếu final receipt. Ungradable. | Search results có ở cả hai gold files; ranges giao 1/2 intervals, nhưng không span nào được bao trọn. Không native transcript/delivery confirmation để biết answerer nhận gì. | **Evidence selection/finalization failure**: không có successful evidence/verify, không tạo pack, không finalize. | Cao về trạng thái ledger/trace; thấp về native delivery. |

## Nhận xét semantic exploratory và trạng thái grader

| Cell | Exploratory evaluator | Nhận xét chính | Trạng thái formal |
|---|---|---|---|
| D01/A | FAIL | Claim correctness 2, citation support 2, completeness 0; 1 omission, không có unsupported claim. | Semantic response qua local schema validation nhưng grader trả INVALID/untrusted; overall augmented report UNGRADABLE. |
| D06/C | FAIL | Claim correctness 0, citation support 0, completeness 1; 1 unsupported claim và 1 omission. | Semantic response qua local schema validation nhưng grader trả INVALID/untrusted; overall augmented report UNGRADABLE. |
| D12/A | PASS | Năm dimensions đều 2; evaluator ghi 1 supported claim, 0 omissions. Packet có source text/citation nhưng không có gold/checklist để xác nhận frozen completeness. | Semantic response qua local schema validation nhưng grader trả INVALID/untrusted; overall augmented report UNGRADABLE. |

Hai exploratory FAIL và một PASS là mô tả judgement trên packet, không phải formal verdict. D12/A còn có 2/4 reference steps unaddressable trong frozen span mapping; evaluator không được đưa checklist/gold nên không thể giải quyết chênh lệch đó.

## Ranh giới delivery và diễn giải budget

Pack của D01/A, D01/C, D06/C và D12/A là bản dựng hậu kỳ từ nhiều search envelopes. Chúng có lần lượt 19/10/13/11 items và tổng UTF-8 source content 27,299/17,523/12,620/11,288 bytes; kích thước JSON trên đĩa lần lượt 37,855/23,117/19,330/16,793 bytes. D01/A vượt 24 KiB cả khi chỉ đếm source content; D06/C và D12/A vượt giới hạn 10 items; D01/C nằm đúng 10 items và dưới 24 KiB. Helper gom unique spans nhưng không giới hạn số items, vì vậy không thể suy ra answerer nhận một blob duy nhất có đúng byte/item count đã báo. Các số này mô tả artifact hậu kỳ, chưa chứng minh payload delivery.

Ba Arm-B grading packs khớp byte-for-byte với session packs. JSON trên đĩa lần lượt là 9,656/6,839/8,562 bytes, còn tổng `selected[].content_bytes` lần lượt 24,431/24,564/24,562; item count 10/6/5. Cả ba content-byte totals dưới 24 KiB; các selector stats vẫn ghi gap categories liên quan byte/item/reference scan/tool-call budgets. Selected records không có trường literal `content`, nên đây không phải xác nhận exact serialized payload native answerer nhận. Workflow không lưu candidate pool đầy đủ; không xác định được span nào từng có trong pool rồi bị loại.

D12/C là trường hợp riêng: ledger ghi 63,368 bytes, dưới outer 64 KiB nhưng sát giới hạn; trace có 7 adapter envelopes, song không có pack/finalize receipt hoặc native transcript xác nhận delivery. Không có cơ sở nói answerer đã nhận pack hợp lệ hay đã vượt limit. Do đó không cộng D12/C thành “budget violation” đã chứng minh.

## Causal chain: question → search → evidence → answer → grading

- **Acquisition/range:** D01/A, D01/C, D06/C chạm ít nhất một gold file nhưng pack/search ranges giao gold intervals lần lượt 0/2, 0/2, 0/3; D12/A và D12/C giao 1/2 intervals nhưng phủ tổng cộng chỉ 1/95 dòng của interval thứ nhất và 0/18 của interval thứ hai. Không có interval nào được bao trọn. Đây là miss ở span-level acquisition/range trước khi có thể quy cho reasoning.
- **Selection/delivery:** D01/B và D06/B đọc gold file nhưng pack không giao gold intervals; không có candidate pool nên pha cụ thể chính xác còn bỏ ngỏ. D12/B là bằng chứng rõ hơn: file liên quan được đọc, nhưng pack không đụng gold files. D12/C search trả kết quả từ cả hai gold files nhưng chỉ excerpts giao 1/2 intervals, không tạo pack hay kết thúc session.
- **Answer/reasoning:** D01/A exploratory evaluator thấy một supported claim và một omission; D06/C thấy một unsupported claim và một omission. D12/A evaluator chấm exploratory PASS, nhưng packet không có gold/checklist và không chứng minh đủ source-span coverage. Search/pack content range chỉ giao 1/2 gold intervals; không span nào khớp exact hoặc được một item bao trọn. Structural 2/12: C1–C5=0; C4 heuristic bắt hai cụm mà packet giải thích là phủ định/giới hạn (nguy cơ false positive), C5=0 dù answerer ghi hai gaps vì answer abstained và pack không ghi engine gap; C6=2 vì không có pack-supported step nào bị bỏ sót. Citation source resolve/hash_ok nhưng `pack_verified=false`, 0 valid/1 invalid citation. Đây là bất đồng giữa exploratory evaluator và structural/reference checks, không phải bằng chứng semantic PASS đúng. Các evaluator không trusted nên đây chỉ là dấu hiệu thăm dò, không phải kết quả chính thức.
- **Grading:** Sáu cell có structural scores đều fail; ba artifact không đủ để chấm. Các semantic packets đều qua local schema validator nhưng bị grader đánh `INVALID`/`UNGRADABLE` do evaluator registry rỗng. Không đổi validator, registry hay rubric để ép ra verdict.

Không đưa ra phần trăm execution/acquisition/selection/reasoning: các nhóm nguyên nhân chồng lấn, ba cell ungradable, và candidate spans trung gian bị thiếu nên không có mẫu số phân loại loại trừ lẫn nhau. Có thể đếm dấu hiệu trực tiếp nhưng không dùng chúng làm tỷ lệ: lỗi invocation/schema/finalization thấy ở D01/B, D01/C, D06/A, D12/C; D06/B có lần khởi chạy đầu không tạo session nhưng nguyên nhân chưa rõ; span-range miss được ghi ở D01/A, D01/C, D06/B/C, D12/A/C; selection-vs-acquisition vẫn chưa tách ở D01/B và D06/B. Answer-reasoning evidence chỉ exploratory ở D01/A và D06/C; D12/A bất đồng evaluator/rubric không cho phép quy lỗi cho reasoning.

## Lỗi đã tái lập, sửa và chưa xác minh

**Tái lập được từ receipt/diagnostic:** D01/C evidence request sai schema bị adapter từ chối; D06/A gọi CLI sai; D01/B `abstain` sai kiểu; D12/C không tạo pack/final receipt dù adapter trace có search results; D12/B mất toàn bộ gold-file coverage sau khi file đã được đọc; các pack hậu kỳ vượt 10 items (D01/A, D06/C, D12/A).

**Đã sửa trong code:** Không có; source diff rỗng. Các lỗi đã fail-closed ngay trong pilot: evidence request sai địa chỉ bị từ chối; final schema sai kiểu và cell thiếu receipt/pack không được chấm như câu trả lời hợp lệ. Đây là negative outcomes thực tế, không phải synthetic PASS. Vì không có code correction nên không có before/after code regression. Không thêm test/framework.

**Chưa xác minh được:** serialized bytes/items chính xác tại delivery boundary; B-arm candidate spans trước selection; D12/C native delivery; exact served model/reasoning; token, cost, latency; cách chứng minh isolation khi subagents dùng chung filesystem/tool access. Không gán các khoảng trống này cho acquisition hay reasoning.

## Reproductions offline và negative controls

Các kiểm tra dưới đây chỉ xác nhận parser/adapter/grader từ chối đầu vào không hợp lệ; không phải synthetic PASS hay đo answer quality. Chúng chạy local, không gọi API, không đọc thêm case ngoài D01/D06/D12 và không sửa corpus/index.

- **D01/B schema:** lấy đúng trường `abstain` từ final artifact đã lưu và gọi contract validator `ANSWER_SPECS["abstain"].validate`. Kết quả: từ chối, type là `str`, diagnostic yêu cầu boolean.
- **D01/C evidence:** chạy controller schema validator với hai malformed address shapes tương ứng các lỗi trace ghi nhận. Kết quả: mode ngoài `lines`/`bytes` bị từ chối; request thiếu `address` cũng bị từ chối. Đây là minimal negative-control payloads dựa trên diagnostics, không phải replay nguyên văn tool calls.
- **D06/A CLI:** phát lại hai cú pháp CLI sai trong trace; chỉ thay `--root` thành `/tmp` để parser không truy cập corpus. Cả hai trả exit code `2` và dừng ở usage/parser error. Không gọi search hoặc đụng index.
- **D12/C missing pack:** gọi official grader pack loader với artifact directory thật và không cấp fallback pack directory. Kết quả: từ chối với `no frozen pack found for D12/arm-c`.

Các control xác nhận failure modes fail-closed và giúp tách chúng khỏi quality scores. Không có code correction nên không có before/after regression; không chạy toàn bộ selftest suite vì suite dùng case ngoài phạm vi pilot đã được duyệt.

## Reviewer độc lập

Một reviewer fresh-context OpenAI được yêu cầu kiểm tra raw receipts/artifacts trong đúng phạm vi D01/D06/D12, delivery/budget interpretation, causal claims và diff. Requested model/reasoning: `gpt-6.2-astra/high`; served identity không được runtime xác minh. Reviewer đã đối chiếu 3 frozen source_refs, 8 ledgers/traces và 7 grading packs.

### Findings reviewer mới

1. **D12/C:** adapter trace tồn tại: 7 envelopes, 4 successful search envelopes có 29 content result items, 1 invalid search input, 1 symbol, 1 status; 6 charged calls. Search output có cả hai gold files nhưng chỉ giao 1/2 reference intervals. Thiếu native transcript/delivery confirmation, pack và final receipt; mô tả trước đó “không có raw trace” là sai và đã sửa.
2. **Pack accounting:** các Arm-B selector ghi `selected_bytes` lần lượt 24,431 (D01/B), 24,564 (D06/B), 24,562 (D12/B); kích thước JSON trên đĩa tương ứng 9,656, 6,839, 8,562 bytes. Cả ba selected_bytes dưới 24 KiB, số items lần lượt 10/6/5. Đây không phải xác nhận exact serialized payload native answerer nhận.
3. **Span coverage:** D12/A và D12/C có content ranges giao 1/2 gold intervals, nhưng không có span nào exact-equal hoặc được một item bao trọn. Các pack cell khác không giao gold intervals; D01/B có một gold file nhưng không có interval overlap.
4. **D12/A discrepancy:** citation source resolve/hash_ok nhưng pack verification false; grading ghi 0 valid/1 invalid citation. C4 heuristic bắt hai cụm trong câu phủ định/giới hạn, có rủi ro false positive. C5=0 dù answerer nêu hai gaps vì answer abstained và pack không ghi engine gap; C6=2 vì không có pack-supported required step nào bị bỏ sót. Evaluator packet không có gold/checklist nên PASS không kiểm định được full reference coverage.
5. **Provenance/diff:** raw manifest có đủ 127/127 file bytes/hash khớp digest; corpus copy 1,812 files không mismatch. So với checkpoint pilot `bad498e`, diff hiện tại chỉ có hai tài liệu. Reviewer không yêu cầu code correction; không thấy cơ sở cho patch chỉ xử lý symptom. Các lỗi execution đã được fail-closed.

Reviewer kết luận causal summary cần giữ các ranh giới trên; không có cơ sở claim arm winner. Rerun D01×A/B/C chỉ khi exact delivery cap/payload được kiểm soát và lưu; nếu không, dừng.

## Rerun gate và tối thiểu cần đổi

Không sửa retrieval/ranking theo trực giác: kết quả hiện tại chỉ chứng minh span misses/selection gaps trên các traces đã lưu, không chứng minh ranking algorithm là nguyên nhân. Không sửa registry/rubric để biến các verdict exploratory thành formal.

Nếu tiếp tục, paired rerun nhỏ nhất có ích là **D01 × A/B/C**, ba answerers, sau một preflight offline chỉ kiểm chứng capture/cap enforcement. Giả thuyết kiểm tra được: với prompt ghi đúng invocation/schema, mỗi arm có thể tạo evidence pack nằm trong cap ngay tại delivery boundary và giữ exact serialized outputs; trước đó cả ba cell D01 chạm gold file nhưng pack không giao gold intervals. Đây chỉ kiểm tra khả năng acquisition/delivery trên một case, không đo chất lượng tổng quát.

Điều kiện trước rerun:

1. Prompt cố định, ghi chính xác CLI grammar, evidence request schema (`address` bắt buộc; mode `lines` hoặc `bytes`) và final JSON (`abstain` là boolean).
2. Budget cap được enforce trước khi bytes/items được đưa tới answerer; lưu nguyên serialized payload cho từng call, không dựng pack hậu kỳ để thay thế.
3. Cell thiếu `finalize`, receipt hoặc pack bị đánh `UNGRADABLE`, không chuyển thành abstention/zero.
4. Ghi candidate trace nếu adapter hiện có làm được; nếu không, đánh dấu acquisition-vs-selection là chưa tách được.
5. Không gọi kết quả exploratory semantic là formal nếu evaluator chưa được owner đăng ký; không claim model identity/cost nếu runtime không xác minh.

Nếu native subagents không cho enforce và audit điều kiện 2, **không chạy paired comparison**. Không mở rộng thành framework benchmark.

## Artifact, digest, rollback

- Raw manifest SHA-256: `2312c6bfda87489b1d65ae30353853f2b7e1656d36b21b0419f594bcdeed8535`; raw source/evidence/answers không nằm trong sanitized review document.
- Corpus copy được so với source inventory: 1,812 files, 0 hash mismatches.
- Reviewer fresh-context đã hoàn tất; sanitized findings nằm ở mục “Findings reviewer mới”.
- Source diff: none. Thay đổi dự kiến chỉ là báo cáo và canonical handoff; rollback bằng revert documentation commit, không chạm các checkpoint code trước.
