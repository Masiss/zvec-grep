# Zvec-grep — Indexing Engine v2 — Original Plan (user-provided)

## M0 — Chuẩn bị và bảo toàn trạng thái

- Kiểm tra working tree local và các thay đổi chưa commit.
- Bảo toàn toàn bộ thay đổi hiện có, bao gồm Watch coalescing và các fix đã kiểm chứng.
- Chia implementation thành các thay đổi độc lập có thể review/rollback.
- Dùng fixture và index thử nghiệm riêng. Không sửa, reset hoặc rebuild HIS_EMR/laya-mini production.
- Giữ single-index search architecture, model/schema compatibility và hiện trạng daemon đang chạy.

**Hoàn thành:** có baseline source rõ ràng, test environment độc lập, không mất local changes.

## M1 — Bounded streaming indexing

### M1.1. Refactor pipeline ownership

Tập trung tại:

- `pipelines/indexing/pipeline.rs`
- `pipelines/indexing/storage.rs`
- các interfaces extractor, embedding runtime và storage liên quan.

Thay luồng giữ toàn bộ `PreparedFile` → toàn bộ embedding outcomes → toàn bộ `IndexedFragment` bằng luồng batch có backpressure.

- Producer phát batches với lượng bytes được kiểm soát.
- Embedding consumer xử lý theo concurrency budget hiện có.
- Kết quả hoàn tất được chuyển tiếp và giải phóng sớm.
- Không tạo toàn bộ futures hoặc giữ toàn bộ vector outcomes của một file trong RAM.
- Giữ ordering và identity chính xác, kể cả khi batches hoàn thành khác thứ tự.

Giới hạn bằng **byte budget xuyên suốt pipeline**, không chỉ giới hạn số futures.

### M1.2. Storage staging

Thiết kế staged replacement để storage nhận các batches mà không cần assemble toàn bộ trở lại thành `Vec<Doc>`.

- File nhỏ có thể đi qua memory fast path.
- File lớn dùng bounded staging; nếu cần disk-backed staging, phải có quota và tự cleanup.
- Staging chưa hoàn chỉnh không được xuất hiện như index đã cập nhật.
- Validate source snapshot/hash, model và schema trước khi commit.
- Cancellation hoặc lỗi embedding không publish dữ liệu thiếu.
- Nếu crash xảy ra trong apply, phải có recovery hoặc retry idempotent.
- Không giữ write lock qua thời gian embedding.

Phân biệt rõ **staging hoàn tất** và **atomic publication**: staging xuống disk không tự nó tạo transaction giữa các native collections.

### M1.3. Streaming extraction

Với plain text/log, hỗ trợ đọc và chunk theo luồng khi chunker semantics cho phép.

Với parser cần toàn bộ AST/source, giữ fallback đúng và sử dụng admission policy minh bạch; không tuyên bố mọi loại file đã bounded nếu vẫn có đường materialize toàn bộ.

### M1 — Definition of Done

- Không còn all-file vector accumulation trên streaming path.
- Native documents được encode/write theo batches thay vì một `Vec<Doc>` lớn.
- Bộ nhớ trung gian được giới hạn bằng budget thực tế, gồm dữ liệu đang chờ, đang xử lý và đã hoàn thành.
- Regression tests: large file, concurrency, cancellation, failure, restart, partial staging, FTS/vector/filter.
- Không làm thay đổi kết quả retrieval hoặc finalization semantics.
- Build và test PASS.

**M1 giải quyết RAM indexing; chưa được tuyên bố giải quyết native finishing.**

## M2 — Incremental fragment reconciliation

### M2.1. Phân loại thay đổi

Tạo cơ chế xác định:

- Unchanged fragments.
- New fragments.
- Removed fragments.
- Changed fragments.
- Metadata-only changes.

Tách **embedding fingerprint** khỏi storage identity.

Embedding fingerprint phải bao gồm toàn bộ input thực tế ảnh hưởng tới embedding cùng model/version/configuration.

Storage identity vẫn phải phản ánh file ownership, source ranges và metadata cần cho FTS/filter. Không giữ nguyên ID cũ khi metadata thay đổi không tương thích.

### M2.2. Reuse embeddings

- Không gọi embedding lại cho fragments có input tương đương.
- Hỗ trợ append-only fast path khi xác nhận prefix cũ không đổi và chunker có ranh giới ổn định.
- Xử lý đúng phần tail bị ảnh hưởng bởi overlap.
- Với insert/delete giữa file, đối chiếu lại chunk identity và source ranges.
- Nếu không chứng minh được reuse an toàn, fallback whole-file.

Ưu tiên tận dụng dữ liệu đã được lưu trước khi thêm một persistent cache mới.

### M2.3. Partial storage mutation

Mở rộng `replace_file()` thành API hỗ trợ reconciliation:

- Chỉ delete fragments thực sự bị loại.
- Chỉ insert/update fragments thay đổi.
- Giữ những rows còn hợp lệ.
- Giữ entity/fragment ownership và FTS metadata nhất quán.
- Không kích hoạt native optimize đối với no-op hoặc metadata-only changes không cần finalization.
- Bảo đảm retry/reopen không tạo duplicate hoặc stale rows.

Không thay retrieval bằng base+delta hoặc multi-index fan-out.

### M2 — Definition of Done

- Append-only không phải embed lại toàn file khi fast path hợp lệ.
- Update nhỏ không phải rewrite toàn bộ fragments nếu storage hỗ trợ partial mutation.
- Các trường hợp append, replace, insert, delete, rename, restart và crash recovery PASS.
- Nếu partial mutation không thể làm atomic với native API hiện tại, phải nêu blocker cụ thể; không giả lập atomicity.
- Fallback whole-file vẫn hoạt động đúng.

**M2 giảm công việc ghi và embedding; native optimize có thể vẫn đắt.**

## M3 — Native index maintenance

Đây là milestone riêng, nhắm trực tiếp vào finishing 200–682 giây.

### M3.1. Loại bỏ finalization dư thừa

Tại `storage/store/mod.rs`, `fragments/mod.rs` và native adapter:

- Tách dirty state theo collection và loại mutation.
- Phân biệt vector additions, deletion, metadata-only và no-op.
- Không optimize collections không cần thiết.
- Với delete-only, chỉ bỏ optimize nếu query visibility, persistence và reopen đã được kiểm chứng.
- Không thay `finalize_writes()` thành checkpoint-only khi vector index chưa complete.

### M3.2. Sửa native compaction path

Nếu thêm một lượng vectors nhỏ vẫn buộc full HNSW merge:

- Làm việc trên native Zvec ở nhánh độc lập.
- Ưu tiên reuse cấu trúc đã tồn tại, tránh materialize/copy dữ liệu không cần thiết.
- Thiết kế compaction/merge với working set bounded.
- Tối ưu CPU và memory amplification cùng lúc.
- Không mặc định dùng segment-only: prototype trước có regression lớn về memory, storage và query latency.
- Giữ tính đúng đắn của deletion, tombstones, query filters và search completeness.

Không tự tạo HNSW engine mới hoặc đổi ranking architecture khi chưa có quyết định kiến trúc.

### M3 — Definition of Done

- No-op/metadata-only không gây full fragments optimize.
- Delete-only được xử lý đúng nếu fast path được bật.
- Với vector mutation, có thay đổi thuật toán native thực tế làm giảm chi phí merge hoặc peak memory so với cùng workload baseline.
- Vector/FTS/filter query, recall, restart và storage recovery không regression.
- Không chấp nhận đổi update latency lấy memory/storage growth không kiểm soát.

Nếu native implementation cần thay đổi lớn ngoài phạm vi đã thống nhất, dừng tại ranh giới đó và xin duyệt thiết kế.

## M4 — Integration và rollout

- Ghép M1/M2/M3 thành đường indexing hoàn chỉnh.
- Kiểm tra Watch bursts, file lớn và cập nhật lặp trên fixture cô lập.
- Chạy full engine regression suite, targeted integration và release build.
- So sánh ngắn peak PSS, CPU time, write amplification, finishing và search correctness với baseline đã có.
- Chỉ dùng smoke/performance verification cần thiết để nghiệm thu, không mở lại một chiến dịch benchmark HNSW.
- Có feature flag hoặc rollback độc lập cho thay đổi storage/native rủi ro cao.
- Chưa deploy hoặc migrate index production nếu chưa được phê duyệt.

## Quyền hạn và quy tắc thực hiện

- Được refactor API/ownership qua các module của indexing pipeline và storage để đạt M1/M2.
- Được tự diagnose và sửa lỗi build/test trong phạm vi đã duyệt.
- Không thay đổi sản phẩm, query semantics hoặc cấu trúc native index production một cách âm thầm.
- Không sửa đồng thời mọi milestone rồi mới kiểm thử.
- Không tuyên bố DONE khi chỉ đổi constants hoặc hoàn thành một phần.
- Chạy các job dài ở background, giữ phiên để nhận steering.
- Nếu một gate kiểm chứng chính thức FAIL, dừng ở gate đó, báo evidence và phương án sửa trước khi tiếp tục.

## Báo cáo sau mỗi milestone

1. Source files và API đã thay đổi.
2. Dataflow trước/sau.
3. Những allocations, embeddings hoặc storage writes đã thực sự loại bỏ.
4. Correctness tests PASS/FAIL.
5. Phần còn BLOCKED và nguyên nhân.
6. Đánh giá production readiness.

**Thứ tự thực hiện: M0 → M1 → M2 → M3 → M4. Hoàn tất M1 rồi mới mở phạm vi code M2; không chờ triển khai production từng milestone.**
