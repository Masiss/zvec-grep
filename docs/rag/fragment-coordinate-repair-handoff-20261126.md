# Bàn giao sửa tọa độ fragment và kiểm tra evidence thực giao

**Nhiệm vụ research-only.** Sửa lỗi làm mất hoặc cắt sai byte nguồn giữa fragment và verified pack. Sau đó dùng pack đã kiểm chứng để xác định R03 cần sửa ở ranking hay selection. Kết quả của nhiệm vụ là patch trên nhánh thử nghiệm, replay 24 câu dev và quyết định kỹ thuật cho bước sau. Quyết định hiện tại về promotion là **NO-GO**.

## Bắt đầu ở đâu

- Worktree thực thi: `/home/masis/src/zvec-grep-rust-rag-20261126`, branch `work/recover-rag-evidence-20261126`, checkpoint `313e48d`. Main ở `51c9eba`. Kiểm tra status, HEAD và đọc hướng dẫn repo cùng `rust/AGENTS.md` trước khi sửa. [Báo cáo của lượt trước](/home/masis/src/zvec-grep-rust-rag-20261126/docs/rag/selection-recovery-report-20261126.md)
- Receipt gốc: `A=$HOME/.cache/zvec-grep/selection-recovery-20261126`. Trong đó có `frozen/{rust,csharp}-cases.json`, `frozen/{rust,csharp}-corpus`, `traces2/{rust,csharp}/G1K4/`, `packs/{rust,csharp}/G1K4/` và binary `build/zg-g1` có SHA-256 `36017f2749b21ccfbc10a2f29cad377a4a3a554ccba623de8d2985b37bfcee1a`. Dùng R01–R12 và D01–D12. Bước đầu chỉ đọc các file này.
- Tạo thư mục output mới cho run và giữ nguyên receipts cũ. Script cũ `run-g1.sh` ghi vào đường dẫn cố định dưới `$A`; tham khảo flags và tokenizer trong script, đổi đích ghi trước khi dùng lại. Dùng `count_tokens_full`; counter cũ cắt ở 512 token. Build target cũ 3,9 GB đã dọn. Trước build kiểm tra disk/RAM và đặt ngưỡng dừng theo hướng dẫn repo. Giữ index/model hiện có, refresh off. Held-out, evaluator ngoài, deploy và thay đổi production nằm ngoài nhiệm vụ.

## 1. Tái lập lỗi trước khi sửa

Đếm theo **selected item**, kể cả khi một fragment sinh nhiều piece. Với mỗi case, ghép dòng `context_selected` với `context_group_items` bằng path, file SHA-256 và fragment ID. Nếu khóa ghép trả nhiều parent có range khác nhau, ghi nhận là mơ hồ và truy qua route/rank; tránh âm thầm lấy dòng cuối. Dùng `content_range` ở cả hai stage.

Đọc file nguồn dưới dạng bytes. Xác minh hash, thứ tự offset, biên UTF-8 và dòng của cả hai endpoint trên khoảng nửa mở `[start,end)`. Dòng tại offset `p` bằng 1 cộng số LF trước `p`. Khi end nằm ở đầu dòng, dòng chứa end khác với dòng nội dung cuối. Ghi riêng kết quả kiểm tra cột và nội dung byte nếu trace có đủ dữ liệu. Đây là kiểm tra bổ sung vì phép đối soát ban đầu chỉ kiểm hash, biên byte và dòng.

| Đường đi của các item bị verifier loại ở G1K4 | Rust | C# |
|---|---:|---:|
| Range đầu vào hợp lệ, byte range thay đổi, range sau selector sai | 124 | 20 |
| Range đầu vào đã sai, selector giữ nguyên byte range | 0 | 60 |
| Range đầu vào đã sai, selector thay đổi byte range | 0 | 41 |
| Tổng đối chiếu với pack gaps | **124** | **121** |

**Gate tái lập.** Xuất ledger theo item và khớp ba hàng trên với pack thật. Rust có 124 `stale_coordinates`; C# có 113 `stale_coordinates` và 8 `invalid_range`. R10 trước patch mới đã giao đủ 3/3 gold dù pack còn 11 gaps. Nếu số lệch, truy về case/fragment, phiên bản corpus hoặc phép chuyển range trước khi đánh giá patch. [Pack R10](/home/masis/.cache/zvec-grep/selection-recovery-20261126/packs/rust/G1K4/R10.json)

## 2. Sửa clipping trên nhánh thử nghiệm

Trong `clip_fragment_overlap()`, danh sách offset là **đầu mỗi dòng**. Code chặn `local_end_exclusive` ở `lines.len() - 1` rồi kiểm tra nó nhỏ hơn `lines.len()`, khiến nhánh xử lý end giữa dòng không tới được. Ngoài ra, hai đoạn rời nhau trên cùng dòng đang bị xét là overlap. Viết regression chạy đỏ cho hai trường hợp này trước khi thay code. Đây là hai lỗi cụ thể, còn phân bổ nguyên nhân của 144 item phải xác nhận bằng replay. [Selector](/home/masis/src/zvec-grep-rust-rag-20261126/rust/crates/zg-engine/src/pipelines/indexed_search/context.rs:402)

Trừ hợp các khoảng byte đã giao trên cùng `(path, source_sha256)`. Với từng khoảng dư không rỗng, giữ đúng lát byte và tính lại endpoint bằng quy ước nguồn hiện có. Khi không trùng, bảo toàn nguyên fragment. Tính chi phí bằng số byte thực được nhận sau clip. Giữ nguyên chính sách rank, thứ tự chọn và cap của G1K4 cho phép so sánh này.

**Gate clipping.** Trên replay cùng tập đầu vào phải có **0 item đầu vào hợp lệ bị biến thành range sai**. Mọi phần dư trong ngân sách khớp byte nguồn, R10 vẫn giao 3/3. Giải thích thay đổi số piece/bytes theo case. Test qua đường selector → assembler cho fragment kết thúc giữa dòng, EOF thiếu newline, overlap cắt đôi fragment, hai lát cùng dòng, Unicode nhiều byte và cùng path khác SHA. Verifier phải tiếp tục từ chối stale hash và offset không hợp lệ.

## 3. Truy lỗi C# có từ trước selector

Bắt đầu với **60 item có byte range giữ nguyên**, sau đó kiểm tra 41 item còn lại. Nhóm theo dấu hiệu lỗi, lấy một ví dụ tối thiểu cho mỗi nhóm và đối chiếu lần lượt: byte nguồn → entity source origin → fragment offset tương đối → `context_item_target()` → `content_range` tuyệt đối → evidence request. Ghi bước đầu tiên bắt đầu sai, path/hash và byte thực tế so với byte khai báo. Kiểm tra BOM, entity bắt đầu giữa dòng và LF/CRLF/CRCRLF ở các case có dữ liệu tương ứng. [Chuyển từ fragment sang tọa độ nguồn](/home/masis/src/zvec-grep-rust-rag-20261126/rust/crates/zg-engine/src/pipelines/indexed_search/context.rs:928) · [Verifier](/home/masis/src/zvec-grep-rust-rag-20261126/rust/crates/zg-engine/src/api/evidence.rs:416)

**Gate nguồn.** Đối soát đủ 101 mục cũ. Với từng nguyên nhân đã chứng minh, sửa tại phép chuyển sai đầu tiên và kiểm tra qua pack thật. Nếu source/index thực sự stale hoặc việc sửa đòi đổi schema/reindex, giữ fail-closed, báo case bị chặn cùng bằng chứng và hoàn tất các nhóm độc lập. Ghi số gaps còn lại theo nguyên nhân; không đánh dấu hoàn tất sửa tọa độ khi còn từ chối nhầm chưa giải thích.

## 4. Chấm pack thực giao và quyết định về R03

- Chấm trực tiếp `items[].content` của pack sau khi đối chiếu hash và byte nguồn. Chấm CLI preview riêng nếu có claim về preview. Pack thiếu là `UNGRADABLE`; pack `partial` chỉ được credit phần thực giao và phải ghi gaps. K0 lịch sử dùng proxy từ trace; chỉ so trên cùng bề mặt delivery hoặc ghi rõ không so được.
- Với mỗi gold span, ghi exact coverage tại pool, thứ hạng đầu tiên chứa trọn span, thứ hạng/cutoff khi vào selector, quyết định chọn hoặc bỏ, chi phí byte còn lại và exact coverage trong pack. Báo riêng một fragment chứa trọn, hợp các khoảng byte chứa trọn và tỷ lệ câu có đủ mọi span. Nếu chấm greedy dùng gold để ước lượng tiềm năng, giữ cùng pool và cap, ghi rõ đó là ước lượng.
- Replay G1K4 và candidate trên cùng 24 câu. Báo riêng Rust/C# cho exact, range recall, precision theo byte nguồn, token, gaps và latency; một lượt dev chỉ có giá trị chẩn đoán. Chạy focused context/evidence tests, fmt và sáu ca normal-search parity. Phân biệt lỗi môi trường với regression bằng baseline cùng điều kiện. Lưu digest, lệnh tái lập và đường dẫn raw receipts.
- **Điểm dừng.** Kết luận bằng ledger R03. Gold đã xếp thấp trước selector dẫn tới đề xuất thử ranking; gold xếp cao nhưng bị loại vì overlap/chi phí dẫn tới đề xuất thử selection; thiếu gold trong pool dẫn tới chẩn đoán retrieval hoặc representation. Nếu nguyên nhân hỗn hợp, ghi theo từng span. Kết thúc ở quyết định có bằng chứng này. Arm ranking mới và promotion là công việc riêng.

**Bàn giao lại cho owner:** SHA patch và binary, lệnh replay, bảng trước/sau từng gate, một ví dụ byte-level đã sửa cho mỗi nguyên nhân, kết quả R10/R03 và các case còn mở. Giữ verdict chất lượng tách khỏi verdict đúng tọa độ. [Plan qualification dài hạn](selection-recovery-plan-20261126.md)
