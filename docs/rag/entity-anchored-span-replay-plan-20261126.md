# Bàn giao thí nghiệm RAG: entity làm điểm neo, fragment định tuyến, span nguồn làm evidence

**Quyết định cần đưa ra:** Với ranker, index và cap 24 KiB hiện có, liệu dùng cấu trúc bên trong entity đã tìm thấy có giúp agent nhận đủ evidence cho thêm câu hỏi? Làm replay offline trước. Chỉ triển khai nhánh thử nghiệm nếu một chính sách chạy được ngoài benchmark vượt gate đã khóa. Trạng thái ban đầu là **research-only, NO-GO cho promotion**.

## 1. Hai giả thuyết cần tách

| Giả thuyết | Ví dụ lỗi | Chính sách cần thử |
|---|---|---|
| **A — fragment đúng chỗ nhưng thiếu ngữ cảnh liền kề.** | Hit nằm trong method cần tìm nhưng bị cắt giữa signature và phần thân. | Giữ hit và thứ hạng, mở rộng tới khai báo có thân nhỏ nhất bao trọn hit, trong cùng entity. |
| **B — entity đúng nhưng fragment trỏ nhầm chỗ.** | Hit nằm ở một method khác trong cùng class hoặc `impl`; mở rộng quanh hit chỉ giao thêm code không liên quan. | Giữ entity và thứ tự anchor, dùng câu hỏi để chọn một declaration con trong entity rồi giao span nguồn của declaration đó. |

Trong cả hai giả thuyết, **entity giới hạn vùng tìm; fragment quyết định anchor nào được xét trước; chỉ byte nguồn có path, SHA-256 và khoảng nửa mở đã xác minh mới được ghi vào pack**. A có thể lấy byte liền kề ngoài fragment. B còn có thể lấy byte ở một declaration khác cùng entity. Union replay trước đó chỉ dùng các byte fragment đã mang vào pool.

### Căn cứ và giới hạn

- Entity flow F1 (tắt diversity) đạt macro exact 0,333 Rust và 0,083 C#. K1 fragment cũ đạt 0,153 và 0,167. Các số lịch sử dùng cách chấm khác nhau giữa trace và pack; chúng giải thích vì sao cần thử hai vai trò, không phải baseline để tuyên bố thắng. Mô phỏng giao nguyên entity theo hạng fragment đã có; không lặp lại arm đó. [Fragment-flow report](/home/masis/src/zvec-grep-rust-rag-20261126/benchmarks/recover-rag-evidence/FRAGMENT-FLOW-REPORT.md) · [Mô phỏng entity cũ](/home/masis/.cache/zvec-grep/selection-recovery-20261126/tools/simulate_entity_expand.py)
- Baseline byte-graded cuối cùng có **2/12 Rust và 1/12 C#** đủ mọi gold span, **5/30 và 2/25** span exact. Union replay đạt 7/30 và 2/25, cứu thêm **0 câu**. Reorder trên pool cũ cũng chỉ có ít ca có thể cứu hoàn toàn. R03 là ca mất vì rank/budget, không phải lý do để viết ranker mới trong nhiệm vụ này. [Union replay](</home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/final/ranking-decision/UNION-REPLAY-DECISION.md>)
- Trong **13 Rust + 20 C#** lượt span được gắn nhãn `retrieval_or_representation`, kiểm tra theo từng câu trên toàn bộ pool cho thấy **10 Rust + 11 C#** nằm trọn trong ít nhất một entity đã trúng. Có đúng file nhưng thiếu entity chứa trọn ở **1 + 4** lượt; file vắng ở **2 + 5** lượt. Một số entity chứa trọn dài hơn 53 KB. Đây là số lượt span, có source lặp ở C#. Nó chưa cho biết entity nào được xét trước khi ngân sách đầy, method nào chọn được khi giấu gold, hay câu nào có thể cứu đủ mọi hop. [Ledger và trace](/home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/final)

## 2. P0 — Khóa baseline và quy tắc trước khi xem kết quả candidate

**Điểm xuất phát.** Worktree `/home/masis/src/zvec-grep-rust-rag-20261126`, branch `work/recover-rag-evidence-20261126`, checkpoint tài liệu `e0193d0`, code đã sửa tọa độ `b336cbb`. Main tại thời điểm bàn giao là `51c9eba`. Đọc hướng dẫn repo/worktree trước khi sửa. Dữ liệu đọc tại `~/.cache/zvec-grep/fragment-coordinate-repair-20261126/final/`, gồm `frozen/`, `traces2/{rust,csharp}/G1K4/`, `packs/{rust,csharp}/G1K4/` và `ranking-decision/union-replay/`. Ghi script, manifest và receipt mới dưới run ID riêng; corpus HRM2 ở local.

1. **Đối chứng C0 là pack thật của code đã sửa tọa độ.** Chạy cùng bộ chấm byte trên cả 24 ca dev. Dựng replay với resolver tắt bằng đúng thứ tự, dedupe, clipping theo byte, cách nhận từng piece và cách tính item của selector gốc. So danh sách có thứ tự `(path, hash, start, end, digest nội dung)` với C0 từng câu. Ghi sai khác đầu tiên. Khôi phục parity trước khi đo candidate. Đọc implementation hiện tại; script union cũ có đường đếm item theo danh sách quyết định, gồm cả lần bỏ, nên không dùng nguyên script đó làm selector đối chứng. [Selector](</home/masis/src/zvec-grep-rust-rag-20261126/rust/crates/zg-engine/src/pipelines/indexed_search/context.rs:545>) · [Replay cũ](</home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/final/ranking-decision/union-replay/tools/replay_union.py:76>)
2. **Tách resolver khỏi scorer.** Resolver chỉ nhận câu hỏi, thứ hạng/hit và metadata mà runtime có thật, cùng source đã neo. Scorer riêng mới được đọc gold. Nếu tận dụng parser/outline trên source frozen để bổ sung boundary, ghi rõ API, ngôn ngữ hỗ trợ và chi phí xây outline. Output của oracle dùng để phân tích sau khi policy đã khóa. Cấm truyền gold path/span, case ID như feature hoặc expected answer vào resolver.
3. **Preregister một cấu hình cho mỗi arm.** Ghi commit, hashes, corpus/index/tokenizer, quy ước gold-byte, flags, tie-break, loại declaration được xét, giới hạn source đọc/parse, số anchor có thể mở, kích thước span, gates và thứ tự A rồi B. Điểm khởi đầu đề xuất là tối đa **4 entity khác nhau/câu**, tối đa **1 đề xuất cấu trúc/entity**, **4 KiB/span đề xuất**, cap pack **24.576 byte/64 item**. Đây là giới hạn nghiên cứu đề xuất, không phải thông số đã tối ưu. Khóa trần I/O riêng theo cách parser thực sự hoạt động trước khi chấm; nếu phải đọc cả file, tính chi phí cả file. Ghi cả bytes đọc lạnh và phần dùng lại từ cache. Cùng quy tắc cho hai ngôn ngữ, ngoài khác biệt cú pháp.

**Gate P0:** cùng payload C0, 0 verifier gap, R10 đủ 3/3 và tái lập được các số byte-graded ở trên. Nếu định nghĩa exact khác báo cáo lịch sử, ghi song song hai metric. Không sửa gold/corpus để khớp kết quả.

## 3. P1 — Replay hai chính sách trên cùng luồng fragment

**Quy tắc chung:** duyệt fragment đúng thứ tự C0. Khi một hit đủ điều kiện nhận fragment gốc theo ngân sách còn lại, dùng nó để thử resolver nếu còn quota. Thay thế xảy ra **ngay tại lượt xét hit**, nhờ vậy expansion có thể đẩy một hit sau ra khỏi pack và phép đo sẽ ghi nhận phần mất. Cùng source identity `(path, file_sha256)`, giới hạn trong `entity_range`, clip phần byte trùng theo contract đã sửa, kiểm tra số byte và số piece trước khi nhận. Nếu proposal không hợp lệ, vượt quota hoặc không đủ chỗ cho toàn bộ proposal sau clip, dùng lựa chọn gốc. Ghi fallback, kể cả parse thiếu boundary. Replay output phải mang nội dung byte thật, hash và range kiểm chứng được.

| Arm | Quy tắc chạy được khi giấu gold | Tác dụng cần phân biệt |
|---|---|---|
| **A. Mở rộng quanh hit** | Tìm function/method/constructor hoặc declaration có thân nhỏ nhất chứa toàn fragment. Giao span từ phần khai báo đến hết thân nếu vừa cap. Nếu parent trực tiếp là class/`impl` lớn, hit cắt qua nhiều declaration, hoặc boundary không xác minh được, fallback. | Có cứu được câu chỉ bằng lấy thêm source quanh một fragment đã định vị đúng? |
| **B. Chọn declaration trong entity đã neo** | Chạy độc lập từ C0 với cùng anchor eligibility, thứ tự và quota. Trong entity, xét các declaration con vừa cap; ưu tiên match identifier trong tên và signature với từ của câu hỏi; chốt tokenizer/camel-case và tie-break trước khi chạy. Ví dụ tie-break: giao với fragment anchor trước, rồi source offset tăng dần. Không có tín hiệu thì fallback. Có thể chọn sibling method nằm ngoài fragment. | Chênh lệch với A cho biết chọn lại vị trí trong cùng entity có đáng chi phí đọc/parse hay không. |

Chạy A trước. Nếu A vượt gate và B không cần thiết để quyết định triển khai, kết thúc replay ở A. Nếu A không đạt, chỉ chạy B đã preregister khi có entity đủ điều kiện với các declaration con có thể phân biệt. Không chỉnh cap hoặc thêm biến thể sau khi đọc điểm. Nếu B không thể chạy vì thiếu metadata hoặc tín hiệu, ghi số case/anchor bị ảnh hưởng. **Hai arm đều không đổi ranker, không mở rộng recall, không sinh index.**

## 4. Chấm và quyết định

**Đơn vị quyết định là câu hỏi.** Chỉ credit byte payload agent thực nhận. Gold theo cùng hàm và source hash của union replay. Báo theo từng ngôn ngữ và từng case:

- Câu mới đủ **mọi** span, câu từng đủ nay bị mất, tổng paired so với C0. Kèm số span exact theo hợp byte cùng source, precision trên byte nguồn duy nhất và token đầy đủ bằng `count_tokens_full`.
- Ledger từ entity trong toàn pool → anchor đủ điều kiện → boundary tìm được → proposal được nhận/fallback → bytes giao. Ghi source bytes mới, bằng chứng bị thay thế do hết cap, số file/entity đọc, bytes đọc/parse và latency. Tách **không tới lượt anchor**, **đã tới nhưng chọn sai span**, **đúng span nhưng cap chặn**. R03 là đối chứng giới hạn của thứ tự hiện tại, R10 là regression bắt buộc.

| Gate | Quyết định |
|---|---|
| **Toàn vẹn** | 24/24 có receipt, 0 gap, 0 byte ngoài source/parent cho proposal, mọi hash/offset đúng, tuân thủ cap pack và quota I/O đã đăng ký, R10 vẫn 3/3. Có vi phạm thì sửa lỗi replay và chạy lại cùng cấu hình trước khi xét chất lượng. |
| **Tín hiệu để port** | Đề xuất khóa ngưỡng: tăng ít nhất một câu đủ mọi span ở ít nhất một split, giữ mọi câu C0 đã đủ ở cả hai split. Macro byte precision từng split ≥ C0 trên cùng scorer; mean evidence tokens từng split ≤ 1,25 × C0. Báo giá trị chưa làm tròn. Tăng số span nhưng không tăng số câu, hoặc vi phạm guardrail, thì arm **NO-GO**. |
| **Port có điều kiện** | Chỉ arm qua cả hai gate mới được port sau cờ opt-in trên nhánh research. Dùng cùng resolver, chạy lại 24 câu qua selector → verifier → serialized pack thật. Đòi parity theo case với replay, test BOM/Unicode/CRLF/EOF/stale hash và parity normal search 6/6 khi tắt cờ. Đo p95 paired trên binary thật với baseline cùng cache/trace; khóa trần latency trước bước này. Lệch replay hoặc vượt ngưỡng thì giữ research-only. |

Gate trên chỉ quyết định có đáng port. Gate promotion cũ về K0/K1, hai ngôn ngữ, chi phí, chất lượng câu trả lời và dữ liệu độc lập vẫn áp dụng riêng. Một câu dev thắng chưa đủ cơ sở thay default.

## 5. Nếu expansion không đạt

Đọc trace hiện có để phân loại case còn thiếu. Với **đúng file nhưng sai entity**, kiểm tra outline có declaration đủ nhỏ và tín hiệu nào trong query có thể tìm được nó. Với **file vắng** hoặc span chưa thấy ở raw, hỏi lần lượt: source đã được index chưa, index hiện có unit nào phủ span, unit có xuất hiện trên từng raw route trước cutoff không? Chỉ gán nguyên nhân khi có bằng chứng ở ranh giới tương ứng. Trace chỉ ghi tới một độ sâu thì đánh dấu phần sâu hơn là **chưa quan sát**.

| Khi thấy bằng chứng này | Hướng đề xuất ở handoff tiếp theo |
|---|---|
| Unit phù hợp tồn tại nhưng nằm ngoài độ sâu quan sát | Đề xuất probe recall sâu hơn trên index hiện tại; nếu cần decomposition, dùng một identifier hoặc reference có nguồn từ hit. Đánh giá số câu cứu thêm và chi phí/call. |
| Entity flow lịch sử có anchor bổ sung mà fragment flow không tìm thấy | Đề xuất dual recall giới hạn. So đóng góp riêng của từng route tại cùng ngân sách; dedupe theo source identity. |
| Source có trong index nhưng các unit hiện có cắt qua evidence, kể cả sau khi xét đầy đủ ranh giới trên source | Đề xuất thử method/block hoặc multi-scale windows trên corpus nhỏ, với dự toán cardinality, disk, RAM, thời gian index và p95. Việc đổi schema/reindex cần quyết định riêng của owner. |

Các hướng trên là **nhánh quyết định**, không phải các arm tự động chạy nối tiếp. Kết thúc nhiệm vụ với manifest và lệnh tái lập, bảng paired, ví dụ thắng/thua có byte evidence, chi phí, verdict từng gate và đúng một bước tiếp theo được dữ liệu ủng hộ. Giữ held-out đóng; phạm vi không gồm đổi model/default, chạy evaluator ngoài, promote hoặc deploy.
