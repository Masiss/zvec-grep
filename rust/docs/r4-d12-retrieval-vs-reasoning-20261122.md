# R4 D12 — retrieval vs. reasoning diagnosis

## Kết luận

**`INCONCLUSIVE` — có dấu hiệu hỗn hợp, chưa thể quy thất bại D12 riêng cho retrieval hoặc reasoning.** A không search; phản hồi viện dẫn việc câu hỏi thiếu tên procedure. Đây là tín hiệu task ambiguity, nhưng không chứng minh retrieval engine có thể hay không thể tìm nguồn nếu có truy vấn. B nhận source evidence và nêu đúng rằng repo không chứng minh được definition đang deploy, nhưng bỏ sót một required effect và citation SQL không theo đúng frozen span. Oracle excerpts đủ cho hai bước source-supported chính, nhưng không đủ để xác định các biến thể repo hay trạng thái runtime.

Fresh-context reviewer xếp kết quả là `RETRIEVAL_LIMITED` dựa trên chênh lệch evidence X/Y. Sau khi kiểm tra artifact đầu vào, kết luận tổng thể được hạ thành `INCONCLUSIVE`: A không thực sự thử retrieval, câu hỏi thiếu referent, và gói SQL B vượt ra ngoài frozen span do lỗi line counting. Không có formal C1–C6 verdict.

## Thực nghiệm và evidence delivery

Đã chạy đúng hai answerers độc lập với cùng cấu hình **được yêu cầu** `gpt-6-luna/high`; served model/reasoning identity không quan sát được. Sau đó chạy một OpenAI fresh-context reviewer với answer labels X/Y bị ẩn. Không gọi Responses API, không mở case khác, không sửa source/index/benchmark.

| Nhánh | Điều kiện đã nhận | Kết quả quan sát được |
|---|---|---|
| A — independent discovery | Câu hỏi frozen và corpus copy qua R4 adapter; giới hạn adapter giữ nguyên: 6 calls, 3 rounds, 32 files, 64 reads, 64 KiB delivery, pack 24 KiB/10 items. | Một `status` interaction, 552 delivered bytes; 0 search/evidence calls, 0 source reads, 0 pack. A abstain, nêu thiếu tên proc; receipt xác nhận chưa truy xuất corpus. |
| B — oracle evidence | Cùng câu hỏi và hai excerpts; 3,533 source-content bytes, 2 items, dưới pack cap. Hash của cả hai source-reference files khớp frozen D12. | B nêu source call và một SQL definition, nhưng không nêu `TotalEmployee` effect; abstain về definition đang chạy. Citation của C# nằm trong frozen span; citation SQL ghi 1–205. |

**Sai lệch oracle cần lưu ý:** frozen SQL span kết thúc ở dòng 95, còn excerpt B được gắn range 1–205 do `splitlines()` tách riêng CR và LF trong file CRCRLF. File nguồn có 103 logical lines theo LF-based indexing; excerpt nhận thêm các dòng sau frozen span. Procedure definition và hai `TotalEmployee` update nằm trong phần frozen 1–95, nhưng citation 1–205 không hợp lệ và phép thử không giao đúng span boundary. C# excerpt 1057–1072 nằm trong frozen span 1057–1074.

C3 steps 1–2 có source coverage trong frozen excerpts. Step 3 đòi phân biệt repository variants nhưng oracle không nhận variant inventory; step 4 yêu cầu giới hạn về runtime, điều mà source không thể xác minh. Không coi việc thiếu step 3 là reasoning failure trong điều kiện oracle này.

## Per-step review

| Required step | A | B | Phân loại |
|---|---|---|---|
| Gọi tên source call | Không có search hay citation. | Claim được hỗ trợ; citation C# nằm trong frozen span. | Acquisition chưa diễn ra ở A; B xử lý được source call. |
| Definition và `Organizations.TotalEmployee` effect | Không có evidence. | Nhận diện một definition nhưng bỏ sót effect dù các update nằm trong excerpt; SQL citation range sai. | Dấu hiệu answer-completeness/citation issue ở B, nhưng không formal. |
| Selected repository variant, loại `_old`/`_old1` | Không tìm kiếm variants. | Không có variant inventory trong oracle input. | Chưa phân biệt được retrieval với evidence-delivery limitation. |
| Repo source so với definition đang chạy | Không trả lời; lý do nêu là thiếu tên proc. | Đúng khi nói repo excerpt không chứng minh runtime/deployment. | Runtime/deployment evidence không có trong thí nghiệm. |

Reviewer xác nhận B chỉ có excerpt được cung cấp; A chỉ có status, không có retrieval. Reviewer cho rằng chênh lệch cho thấy retrieval limitation, nhưng không thể suy rộng thành lỗi của retrieval engine vì A không chạy search. Reviewer cũng xác nhận runtime không thể kết luận từ repo và đánh dấu B thiếu effect. Labels có thể suy ra từ evidence conditions dù đã ẩn X/Y.

## Nguyên nhân và engineering decision

- **Độ tin cậy cao:** câu hỏi không nêu identifier; A dừng trước search. Đây là task-disambiguation/acquisition gap đã quan sát, không phải phép thử retrieval thành công/thất bại.
- **Độ tin cậy vừa:** B bỏ sót effect có trong source excerpt và nêu citation range sai. Line-range packaging error là do harness dựng oracle, không phải lỗi model retrieval.
- **Không thể xác minh:** definition nào đang deploy/chạy; repository không có database catalog hoặc deployment/runtime evidence trong scope này.

Không sửa retrieval, ranking, resolver hay benchmark. Engineering follow-up tối thiểu: bảo đảm câu hỏi thực tế nêu procedure identifier khi cần discovery; nếu cần xác nhận “đang chạy”, phải có nguồn deployment/catalog được phép. Trước bất kỳ diagnostic tương lai nào, oracle extractor phải dùng frozen `spans` và LF line semantics; không chạy lại answerers trong nhiệm vụ này.

## Giới hạn, provenance và checkpoint

- Shared workspace không có per-agent filesystem ACL; chỉ dẫn D12-only được áp dụng nhưng không thể chứng minh cách ly tuyệt đối. Answerers dùng fresh context và không trao đổi output.
- Token usage, latency, cost và served model identity không có telemetry. Không có retrieval receipt cho B; A có R4 receipt kết thúc, không có search/read/pack.
- Diagnostic index copy không đổi; tree digest trước/sau khớp. Raw question, source excerpts và answers chỉ lưu trong local authorized temp storage; report này chỉ có aggregates/digests.
- Question SHA-256: `dca05e9c8a786b2f6f7c1bb457c6f2a0e610a0b832dede2bc865a872690bb882`; oracle-input SHA-256: `31466de816fbd717f4ecb677c6f8fe57598405a1c37c21873aee3d80f7667721`.
- Raw answer digests: A `f257df1bfb19b30fc7384e522b73bbf8d9b9a574f3e85870737d2fad81f0922a`; B `91f8e57e4045a59b826f4cf65cdd51119a0a3a18eda896a73c7f1ccb49ae86cc`. A adapter receipt SHA-256: `c68504717c4f5e6e86d532ccd246ada6cb968372a347b23f6b958d7a1750deb3`.
- Code changes: none; rollback not applicable. This report and canonical handoff are the only repository changes planned; no push/merge/release.
