# Indexed semantic retrieval qualification — 2026-11-22

## Kết luận

**INCONCLUSIVE.** Đã tìm thấy một R4 index v3 hoạt động khi mở qua bản sao local, nhưng verifier đọc-only duy nhất hiện có dừng với `INCOMPLETE`: nó chỉ hỗ trợ index v2 (`unsupported index version 3; expected 2`). Vì điều kiện của Goal yêu cầu đánh giá trên index đã qua integrity verification, các phép đo bên dưới chỉ là exploratory evidence, chưa phải kết quả chất lượng đã được qualification.

Trong exploratory slice ba task không có exact identifier, vector/hybrid đưa được một số **target file** vào top 10 nhưng không có route indexed nào chồng lấp frozen gold spans. Ripgrep top 10 chồng lấp 1/9 span đã được xác nhận searchable; một lexical IDF ranking sensitivity cũng chồng lấp 1/9. Ripgrep không giới hạn tìm thấy 3/9 span nhưng median output thô là 1.43 MB/task. Kết quả không chứng minh semantic retrieval có giá trị bổ sung; cũng không đủ để kết luận toàn bộ semantic retrieval không có giá trị.

## Fork, source và runtime provenance

| Thành phần | Đã xác minh |
|---|---|
| Worktree bàn giao hiện tại | `/home/masis/src/zvec-grep`, branch `work/source-discovery-autonomous-20261122`, HEAD `663566546f03768b9b021c65eb3accc4aa0720a1`. Đây là branch nghiên cứu hiện tại, không phải runtime được dùng để truy vấn. |
| R4 source checkpoint | Branch `work/r4-evidence-representation-20261119`, HEAD `2bf2f1ef13e71d321668eef89f1de75f37978033`; commit là phần tiếp nối R4 experimental branch. `upstream/main` là `e1f93d257800f8626eb877eaf45573518076ab65`; `origin/main` và local `main` đang ở `2d7d2ac220d1baf840694d937fea7054d091e33d`. R4 branch có 217 commit sau `upstream/main`; branch nghiên cứu hiện tại phân kỳ 71 commit sau và thiếu 11 commit so với upstream tip. Không sync/rebase/merge. |
| Maintained Rust branch | Checkout này không chỉ ra branch riêng nào có thể xác nhận là maintained Rust release branch. R4 branch được xem là nhánh thử nghiệm; không suy ra production status từ tuổi branch hoặc số commit. |
| Query engine | Cached Rust `r4-adapter`, SHA-256 `bdd6f6207e35757a0f6cec5797ca5d6b72739fea59257cc9c41b8e9535f810fd`; cached Rust `zg` dùng để kiểm tra status, SHA-256 `144fa20e48dba9f908f606bee8db68cd3d2db891e762ba409ba4b9c911729c48`. Binary-to-source commit/build configuration chưa được chứng minh. Installed `zg 0.0.1` chỉ được dùng thử verifier; không dùng để lấy route results. |
| Lexical runtime | Native ripgrep 15.2.0, SHA-256 `e62198eb19b136b88c330af83647b5a962cb99b6b1f066758568f12de1974849`. |
| Embedding artifact | `local/potion-code-16m-v2`, 256 chiều, CPU. Local artifact inventory SHA-256 `ed75b120cf7f96b1826bee93931e25e1f7bf04be4ae2b5692d1300ee5b7c98ec`; weights SHA-256 `75cf7a6c2171b230ad19b1e7d8e0b1aee86da5a02af8e7cacedd9921d227623c`. Queries chạy offline; không tải model hay gửi source ra ngoài. Binary cache không ghi lại build provenance của index vectors. |

Đây là R4 Rust engine/index experiment, không phải TypeScript implementation hay installed runtime. Workspace index generation `6e3c3a4d-4690-4868-8f9e-1023bbb7577c` báo StorageFailure và không được dùng trong các phép đo này.

## Corpus và integrity gate

Nguồn là R4 frozen corpus snapshot `r4-sql-symbols-20261118`, index version 3, generation `cea13512-733a-4c9f-96a7-5e131b57d1dc`. Source inventory digest SHA-256 `2b761e66fa43f47d9b5ee9b0cc3a799f05cf8b92c2f8794304aa297efab874a3`; pristine index-tree digest `c36ca81d7e794506866694199ec03ff981864b2c0532a32cc75ee812efacb28d`. Corpus có 822 source files; `--status` trên disposable copy báo 818/818 coverage và `file-ids.json` ghi 818. Chênh lệch bốn file chưa giải thích được.

R4 index-truth artifact có 25 checks: 21 `OK_SEARCHABLE`, 3 `NOT_PERSISTED`, 1 `NOT_SEARCHABLE`. Điều này mâu thuẫn với prose cũ trong R4 SQL-symbol report nói cả 25 spans đã persisted; phép đo này dựa vào artifact theo từng check. Riêng task set ở đây, 9/10 frozen spans được audit đánh dấu persisted/searchable; một D07 span `NOT_PERSISTED` bị loại khỏi retrieval denominator. Tám source-reference hashes được đối chiếu với corpus, 8/8 khớp.

Verifier `/home/masis/.local/bin/zg --verify --json` chạy read-only, không đổi digest của copy, nhưng trả exit 2 / `INCOMPLETE`: `unsupported index version 3; expected 2`. Đây là giới hạn tương thích của verifier, không phải kết quả `CORRUPT`. `--status` và các route FTS/vector/hybrid chạy thành công trên các disposable copies. Do chưa có deep integrity verifier tương thích v3, không coi chúng là integrity-qualified index. Mỗi route indexed bắt đầu từ cùng pristine index-tree digest; FTS/vector/hybrid làm thay đổi index tree của disposable copy trong lúc mở/truy vấn. Bản gốc không thay đổi.

## Thiết kế và frozen tasks

Trước khi chạy validation routes, đã freeze D02, D05, D07 trên cùng corpus snapshot. Các câu hỏi truy vấn là concept paraphrases không chứa exact identifier; raw text và gold không được đưa vào report. D01/D06 chỉ là development diagnostics và không tính vào số liệu. D12 bị loại do referent trong câu hỏi chưa xác định.

Routes: native `rg` OR-term lexical query; indexed FTS; indexed vector; indexed hybrid. Mỗi route có giới hạn tối đa 10 items và 24,576 source-text bytes. Frozen gold chỉ được mở ở bước scoring sau khi tất cả route outputs đã hoàn tất. Native `rg` OR-pattern dùng `--max-count 1` mỗi file; pool không giới hạn theo số file nhưng chỉ giữ context quanh match đầu tiên của mỗi file, nên **không phải** lexical recall ceiling. Sensitivity IDF chạy từng term riêng rồi gộp first-hit contexts; vì thế pool đó không phải subset của OR first-hit pool. Trên D07, một context từ truy vấn term riêng (range `[547, 553]`) chạm frozen span `[539, 560]`, trong khi context OR first-hit trên file đó không chạm span. Sensitivity này được chạy sau khi xem route outputs và điểm span-overlap ban đầu; đây là post-hoc diagnostic, không phải primary preregistered score.

**Erratum về binary identity:** cả hai freeze giữ nguyên `engine_binary_sha256=144fa…`, đây là cached Rust `zg` chỉ dùng cho status; route searches thực tế chạy bằng cached `r4-adapter` SHA-256 `bdd6f6207e35757a0f6cec5797ca5d6b72739fea59257cc9c41b8e9535f810fd`. Không sửa freeze sau run; phân biệt vai trò binary trong execution supplement của `results.json`. Provenance commit/build của cả hai binary vẫn chưa được chứng minh.

Freeze SHA-256: `58c38def408abdbbde38673bae76671126c41f42030b37b6a3e223ae93a01068`. Development freeze D01/D06 SHA-256: `f254aadf624fc92f073e704b4b3a5f7d0bce106cc3c6f61bff575b8ad8cadec6`. Bản freeze trong repo chỉ chứa digests/counts, không chứa private question/source/gold text.

## Per-task retrieval evidence

`target files` chỉ đếm file có frozen gold spans đã xác nhận searchable. `span overlap` nghĩa returned source range giao frozen gold span; không đồng nghĩa câu trả lời đúng. `rg OR first-hit pool` là pool chưa giới hạn số file nhưng giới hạn một match/file, không cùng context budget với top 10 và không bao hàm mọi context của các truy vấn term riêng.

| Task | Searchable gold | Native rg top 10 | IDF-rg top 10† | rg OR first-hit pool | FTS top 10 | Vector top 10 | Hybrid top 10 |
|---|---:|---:|---:|---:|---:|---:|---:|
| D02 | 6 spans / 4 files | 1 file / 1 span | 0 / 0 | 4 files / 3 spans | 0 / 0 | 2 / 0 | 2 / 0 |
| D05 | 1 span / 1 file | 0 / 0 | 0 / 0 | 1 file / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| D07 | 2 spans / 2 files; 1 span excluded as `NOT_PERSISTED` | 0 / 0 | 1 / 1 | 2 files / 0 | 0 / 0 | 0 / 0 | 1 / 0 |
| **Tổng** | **9 spans / 7 files** | **1 file / 1 span** | **1 / 1** | **7 files / 3 spans** | **0 / 0** | **2 / 0** | **3 / 0** |

Không route nào bao phủ toàn bộ một gold span. Top 10 mỗi route đều dưới 24 KiB. Median delivered source text: `rg` 2.8 KB; post-hoc IDF-rg 8.8 KB; FTS 7.2 KB; vector 2.5 KB; hybrid 8.7 KB. Với OR first-hit `rg` pool không giới hạn theo số file, median raw JSON output là 1.43 MB, 563 candidate files và 144.6 KB source context/task; pool giao 3/9 spans searchable. Đây là evidence rằng lexical acquisition có thể tìm thêm spans khi nới giới hạn file/item, nhưng việc chọn nội dung trong budget còn yếu.

| Route | Median wall/query | Median peak RSS | Phạm vi đo |
|---|---:|---:|---|
| Native rg | 24 ms | 4.6 MiB | Một process/query, OR terms, output thô được capture rồi giới hạn top 10; RSS đo process native. |
| R4 FTS | 391 ms | 315 MiB | Một cached adapter process/query, includes process/runtime startup. |
| R4 vector | 1.04 s | 412 MiB | Includes local model/runtime load; not warm-service latency. |
| R4 hybrid | 1.06 s | 417 MiB | Includes local model/runtime load; not warm-service latency. |

Một query cho mỗi task/route; các medians là giữa ba task, không phải repeated-run confidence interval. Các top-10 items không giao gold span được gọi là **gold-nonoverlap items**, không bị gán là false positives. Không đo answer quality, citation quality, completeness hay user usefulness.

## Diễn giải và giới hạn

Trong slice này, vector/hybrid retrieval chạm lần lượt hai và ba target files, nhưng không đưa được gold span nào. Capped native rg chạm 1/9 spans; post-hoc IDF-rg cũng chạm 1/9, nhiều hơn vector/hybrid nhưng chạm ít target files hơn (1 so với 2/3). OR first-hit pool giao 3/9 spans searchable. Hai lexical pools khác nhau về context construction và không lồng nhau: IDF chạy mỗi term riêng, rồi gộp context; OR pool chỉ giữ match đầu tiên mỗi file. Vì IDF là post-hoc trên ba task và index chưa qua integrity gate, không dùng chênh lệch nhỏ này để xếp hạng sản phẩm. Kết quả hiện tại chưa chứng minh indexed semantic retrieval bổ sung source evidence có ý nghĩa trong điều kiện đã thử.

Chưa thể chuyển nhận xét đó thành kết luận sản phẩm vì index v3 chưa qua verifier-compatible deep integrity gate, corpus source/index count lệch bốn file, chỉ có ba task, một gold span không được persisted, IDF ranking là sensitivity post-hoc, index query thay đổi disposable copy, và binary-to-source provenance còn thiếu. Gold spans cũng không đại diện đầy đủ mọi evidence có thể hữu ích; retrieval overlap không phải answer quality.

**Quyết định:** chưa có cơ sở đầu tư thêm vào ranking/model hoặc tuyên bố kiến trúc thắng. Điều kiện tối thiểu để tiếp tục là xác minh v3 bằng integrity verifier tương thích mà không rebuild index, giải thích source/index file-count delta, ghi provenance build của Rust binaries, rồi chỉ chạy lại freeze này với capped lexical baseline có ranking cạnh tranh. Không mở H01–H08; không dùng Responses API/subagents; không thay đổi production, index gốc, embedding, frozen benchmark hoặc public API.

## Reviewer findings

GPT-6.1 Sol High được yêu cầu independent design review trong fresh context và chỉ nhận thông tin đã sanitize. Reviewer lưu ý path-sorted rg top 10 yếu, index 822/818 chưa giải thích, binary build provenance thiếu, và không-overlap không đồng nghĩa false positive. Theo đề xuất, đã chạy một lexical IDF ranking sensitivity có cùng 10-item/24-KiB cap. Nó chạm 1/9 span, nhiều hơn vector/hybrid (0/9) nhưng chạm ít target files (1 so với 2/3); do post-hoc và n=3, đây không phải bằng chứng thắng. Reviewer cuối GPT-6.1 Sol High xác nhận `INCONCLUSIVE` đúng với verifier v2 không đọc được v3, kiểm tra số liệu, và yêu cầu sửa comparison IDF, identity binary, quan hệ giữa hai lexical pools, cùng đơn vị RSS. Các điểm đã sửa. Served model snapshot không được host hiển thị để xác minh độc lập.

## Artifacts, changes and rollback

- Sanitized freeze: [`validation-freeze.json`](../../research/indexed-semantic-qualification-20261122/validation-freeze.json)
- Sanitized per-task metrics: [`results.json`](../../research/indexed-semantic-qualification-20261122/results.json)
- `results.json` SHA-256: `788647afd6ed5ec1d3489657a1f2f27f20ab195b0152bd9d62669c6d4a4da7bb`.
- Raw query/output/receipts/source excerpts chỉ local tại `/tmp/indexed-semantic-qualification-20261122/`; 178 local files / 11,748,247 bytes, manifest SHA-256 `22453bca2da0c60e9c2681e1e52cfcbe849a0a60663f5fc183e836995f3dddd9`. Không có raw private source hoặc gold trong repository.
- Runtime/index/embedding/default/API/frozen benchmark: không đổi. Không gọi remote inference hay API.
- Tạm dừng một lần do quota `/tmp` khi tạo nhiều bản sao riêng; các bản sao do lượt đó tạo đã được xóa. Rerun cuối dùng một disposable copy được reset từ pristine index trước từng route. Source/index gốc được kiểm tra lại và không thay đổi.
- Chỉ thêm research artifacts và handoff docs; rollback bằng cách bỏ các file tài liệu mới và gỡ continuation được thêm vào handoff hiện tại. Không có source-code rollback.
