# Kiểm chứng thực tế chất lượng Search & RAG của zvec-grep (2026-11-21)

Mission: xác minh bằng actual queries xem zvec-grep trả về đúng source/symbol/
context/evidence hay không. Base `fb47e1e` → HEAD điều tra `7ff962c`.

Identities: corpus `/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus`
(822 files, index manifest SHA-256 `f6f9695a…`), embedding potion-code-16m-v2
(dim 256), adapter binary rebuild từ `7ff962c`, adapter profiles arm-a/b/c,
LD_LIBRARY_PATH = zvec-prebuilt đã pin. Không rebuild/thay đổi index hay model.
Mọi số dưới đây đo trên chính binary/index đó; relevance labels là
**diagnostic labeling của implementation agent** (không phải independent
formal grading) và được ghi rõ từng loại.

## 1. Executive verdict

- **TIN ĐƯỢC (sau 2 correction mới)**: mọiexcerpt/context deliver giờ khớp
  *byte-chính-xác* với source tại tọa độ khai báo (119/120 item; item còn lại
  chỉ lệch vì BOM+CRLF của chính file, engine content byte-faithful); symbol
  lookup chính xác (exact 1-declaration, ambiguous liệt kê đủ 3 declarations
  kèm type, SQL symbol resolve, nonexistent → 0 sạch); FTS known-item trả đúng
  declaration ở rank 1; rg exact-line hit chuẩn (ví dụ `case "1":` → đúng dòng
  dispatch 466); evidence/verify chain hoạt động đúng (verified=true thật, nội
  dung = source hiện tại); chuỗi C#→SQL truy vết được trọn vẹn trong budget
  (search → rg call-sites → facts → `execute sp_UpdateOrgTotalEmployee`).
- **KHÔNG ĐẠT cho câu hỏi nghiệp vụ nguyên văn (verbal questions)**: hybrid
  top-10 với câu hỏi D01–D12 *nguyên văn* chỉ phủ **8/25 gold spans**; 6/12
  case (D01, D02, D05, D10, D12, và gần hết D03) zero hit. Vector route
  không nối được thuật ngữ nghiệp vụ tiếng Việt → identifier C#; nó match
  nhãn tiếng Việt trong *label-dictionary* (WorkLogResource.cs đứng #1 với
  score 0.99 ở D01) và comment chung ("ghi"). Đây là **quality limitation
  chủ đạo**, không phải defect engine: câu hỏi có identifier (D04/D08/D09/D11)
  đều hit rank 1–3.
- **Defect nghiêm trọng chưa từng được phát hiện: 2 defect excerpt-presentation
  đã CONFIRMED và FIXED** trong mission này (commit `7ff962c`):
  1. Engine excerpt cắt giữa dòng/giữa token theo byte-window của fragment
     trong khi `content_range` khai báo nguyên dòng — consumer kiểm source
     thấy text khác tại vị trí khai báo (false precision). Trước fix: 6/6
     sample kiểm tra có mismatch; sau fix 120/120 khớp.
  2. Adapter envelope bỏ byte-columns → excerpt bắt đầu sau indentation trông
     như full line. Đã thêm `content_range_columns`; 5/5 sample khôi phục
     chính xác từ source tại (line, column).
  Cả hai có reproduction trước/sau trong bundle. Không đổi index/model/
  ranking/selection: chỉ excerpt text/ranges và trường tọa độ.

## 2. Query-level kết quả D01–D12 (hybrid, question nguyên văn, top-10)

Phương pháp: mỗi case một session arm-c tươi, search `--limit 10`, đối chiếu
gold spans (scope-manifest) làm **oracle diagnostic** (chỉ để chẩn đoán, không
tính là deployable improvement). Label noise: label-dict = WorkLogResource
label dictionary; wrong-method = đúng file nhưng sai method.

| Case | Câu hỏi (tóm tắt) | Gold hit@10 (hybrid/fts) | Đặc điểm top-10 | Gán nhãn |
|---|---|---|---|---|
| D01 | Form 1 update thông tin cá nhân đi qua đâu? | 0 / 0 | #1 label-dict 0.99; EmployeeCommand #2 là method [575,625] *khác* gold; gold EmployeeController [459,620] + EmployeeCommand [215,285] vắng | Question→identifier gap; noise |
| D02 | Personal update ghi vào những nơi nào? | 0 / 0 | WorkLogQueue/LogExceptionFilter (comment "ghi") + label-dict; duplicate overlapping pairs ImportController #3/#8 | Gap; duplicates |
| D03 | Đổi FullName qua personal update ảnh hưởng Keyword? | 1@7 / 0 | Gold EmployeeCommand [215,285] chỉ vào rank 7 | Gap (đạt được muộn) |
| D04 | EmployeeUpdateInfo có cập nhật Keyword không? | 1@2 / 0 | Đúng method [562,573] rank 2 | Identifier → tốt |
| D05 | Thiếu detail record → atomic? | 0 / 0 | Label-dict + wrong-method [575,625] | Gap |
| D06 | Mọi lần sửa tên gọi cập nhật tổng nhân sự? | 1@6 / 1@1 | FTS rank 1 đúng helper UpdateOrgTotalEmployee | Identifier tốt, verbal chậm |
| D07 | Restore nhân viên ảnh hưởng gì ngoài Employees? | 1@2 / 1@5 | Hỗn hợp nhưng có gold | Trung bình |
| D08 | DeleteEmployeeByIds xóa hàng vật lý? | 2@1,2 / 2@1,2 | Đúng cả 2 gold spans đầu bảng | Tốt |
| D09 | EmployeeBaseViews ghi hay đọc FullName? | 1@3 / 1@1 | Đúng; SQL file có BOM+`\r\r\n` (so sánh phải decode BOM) | Tốt |
| D10 | Cùng CoreService ba contexts, SaveChanges ghi DB nào? | 0 / 0 | Concept "contexts" không có anchor lexical | Gap |
| D11 | UpdateEmployeeAttr thay trường nào? | 1@1 / 1@1 | Đúng method rank 1 | Tốt |
| D12 | Proc nhiều definition, cái nào chạy? | 0 / 1@6 | Câu hỏi chứa placeholder "tên này" — không có identifier | Gap (câu hỏi dạng này cần tên proc) |

Aggregate: gold-span coverage verbatim-question hybrid@10 = **8/25**;
per-case zero-hit = 6/12. Top-10 noise pattern (120 items hybrid):
**47% là fragment >300 dòng** (49 items >500 dòng — representation
declaration-range của class), **8 cặp duplicate overlapping cùng file chiếm
slot**, **13 items label-dictionary**. FTS sạch hơn (35% huge, 1 duplicate pair).

## 3. Diagnostic ngoài frozen cases (gold-free)

- known-item FTS `EmployeeEditThongTinCaNhan` → #1 đúng declaration [215,459],
  score 2.17, chính xác. 5/5 các known-item query đều rank-1 đúng.
- partial identifier `EmployeeEditThongTin` (FTS) → **0 results, không gợi ý**
  — agent gõ thiếu tên không nhận được gì. Quality limitation (không prefix
  matching); cần owner quyết nếu muốn thêm (ranked prefix/diacritic-insensitive
  lookup là thay đổi ranking/index — ngoài quyền mission này).
- `EditEmployee` (không tồn tại nguyên văn) → 0 results. Tương tự trên.
- nonexistent `ZebraUnicornProcessor` (hybrid) → 5 vector-noise items; envelope
  cho thấy `matched_by=Vector` (không có FTS hit) — consumer có thể tự phán
  "chưa chắc tồn tại"; không có false VERIFIED nào.
- negative control "gui email khi doi ten nhan vien" (chức năng không tồn tại)
  → chỉ noise yếu; không evidence giả.
- English semantic "where is employee personal information updated" →
  EmployeeCommand #1 (đúng file, fragment class 1038 dòng — broad nhưng đúng
  vùng). Vector bridge tiếng Anh tốt hơn tiếng Việt.
- SQL call-string `sp_UpdateOrgTotalEmployee` → #1 file .sql + #2 C# helper:
  chuỗi cross-language hoạt động.
- exact symbol: `EmployeeEditThongTinCaNhan` 1 decl đúng; `sp_UpdateOrgTotalEmployee`
  1 decl; `Employee` 3 decls (class/value/function) kèm type — đủ để agent
  disambiguate; `NoSuchMethodHere` → total 0 sạch, không fabricated result.
- rg: `case "1":` → 1 dòng duy nhất đúng vị trí dispatch [466,466] ✓.
  **Cảnh báo semantics**: rg-mode coi query là *regex* — `switch (model.Form)`
  → 0 hit vì `()` là group. Tool contract nên ghi rõ (integration limitation,
  đã ghi nhận ở report, không đổi engine).
- facts C#: `--method EmployeeEditThongTinCaNhan` → 2 SaveChanges (285, 425) +
  152 assignments (FullName@252, Email@256…) — chính xác và đủ để trả lời
  D03/D11 mà không cần đọc toàn method. Whole-file facts trên file Command lớn
  → honest summary (25 methods/250 invocations) vì vượt budget delivered —
  đúng contract, có gap `facts_budget_exceeded` + hint retry với filter.
- facts SQL (`DB/Store/sp_UpdateOrgTotalEmployee.sql`): definitions parse đúng
  (writes organizations update, reads employees/employeeorganizations…), nhưng
  **reads có noise non-table**: `splitstringtointarray`, `appsettings`,
  `db_cursor` bị tính là table reads. Quality limitation (SQL symbol classification),
  không sai về evidence vì table thật vẫn đúng.
- Corpus isolation: **không có `_old` variant nào** trong corpus frozen —
  D12 dạng "nhiều definition" không thể trả lời từ source này; hệ thống đúng
  khi chỉ hiển thị 1 definition, và không bịa kết luận nào.
- Fidelity toàn diện post-fix: 120/120 hybrid top-10 của D01–D12 khớp source
  tại (lines, columns) — kiểm bằng so byte trực tiếp với file (1 item "lệch"
  là do comparator không strip BOM; engine content đúng byte).

## 4. Failure attribution theo tầng

| Tầng | Kết luận |
|---|---|
| Index representation | Không phát hiện sai/malformed. NHẰM LỚN: đơn vị declaration-range khiến class fragment 1000+ dòng chiếm 47% slot top-10 — đã biết từ R4, giờ có số liệu consumer-level. |
| Query processing | FTS parse đúng, Unicode/UTF-8 BOM ok. Hạn chế: không prefix/partial identifier matching (0-result im lặng). |
| Retrieval | Đúng route, đúng mode (không route contamination: rg/fts/vector/hybrid match `matched_by` envelope). Gold spans có representation nhưng không reachable bằng câu hỏi nguyên văn (đã biết: reachability cần identifier). |
| Ranking | Không sai thứ tự theo contract; nhưng vector với câu tiếng Việt ưu tiên label-dictionaries (0.99) — limitation của semantic matching, không phải bug ranking. Không sửa (ràng buộc mission). |
| Context construction | **2 defect thực (đã sửa)**: byte-clipped excerpt vs line-coordinates; adapter bỏ byte-columns. Post-fix: exact. |
| Structural facts | C# facts chính xác per-method; SQL facts đúng bảng chính nhưng lẫn non-table vào reads. Typed invocations đúng (status=candidate khi unresolved — trung thực). |
| Evidence presentation | verified=true chỉ khi content khớp source hiện tại (đã kiểm: mutate test có sẵn từ correction round); sha256 chỉ gắn fresh + admitted. Không false VERIFIED nào phát hiện. |
| R4 selection | No-plan (question-only) D02 chọn 10 spans toàn noise (WorkLogQueue, 4× KpiPersonalController — duplicate same-file slots trong selection), xác nhận no-plan là diagnostic yếu; plan-based là identity đúng (như correction round). Line-alignment fix không đổi selection/score. |

## 5. Phân loại vấn đề

**Defect correctness (đã sửa, có regression test):**
- C1 excerpt mid-line/mid-token (engine) — fixed `7ff962c`.
- C2 adapter bỏ byte-columns (false full-line impression) — fixed `7ff962c`.

**Quality limitation (không tự sửa, cần owner decision):**
- Q1 câu hỏi nghiệp vụ nguyên văn không reach gold (8/25); cần identifier.
- Q2 class-fragment 47% slot; label-dict #1. (Root đã biết: persisted
  granularity — thay đổi là owner-level.)
- Q3 partial identifier → 0-result im lặng (prefix matching).
- Q4 SQL facts lẫn non-table vào reads.
- Q5 duplicate overlapping results trong top-k (8/120) và same-file trùng
  trong workflow selection.
- Q6 vector yếu với câu tiếng Việt (labels/comments match mạnh hơn code).

**Integration limitation:**
- I1 rg-mode = regex semantics, tool contract không nói rõ (`switch (model.Form)` → 0).
- I2 corpus frozen không chứa `_old` variants → loại câu hỏi "cái nào đang
  chạy" không đánh giá được trên corpus này (không phải lỗi engine).

**Nghi vấn ĐÃ BÁC BỎ:**
- "rg `Form == "1"` trả 0" → pattern sai (source dùng switch/case), engine đúng.
- "facts trả rỗng trên file lớn" → thực chất là honest budget summary, đúng contract.
- "content không khớp source" (6/6 sample) → 1 phần do C1/C2 (đã sửa), phần
  còn lại do comparator không strip BOM/CRLF — engine byte-faithful.
- "exact-symbol có thể trả sai declaration" → 4/4 test exact/ambiguous/SQL/
  nonexistent đều đúng.
- "route bị gọi nhầm" → không thấy; `matched_by` nhất quán mode yêu cầu.

## 6. Trả lời 10 câu hỏi của §12 (tóm tắt)

1. Trả đúng cho query **có identifier/anchor chính xác** (known-item, symbol,
   rg literal, facts per-method, evidence lines, SQL call-string). Không đủ
   cho **câu hỏi nghiệp vụ nguyên văn** (gap 8/25).
2. Hoạt động tốt: FTS known-item, exact symbol lookup (C#+SQL), rg literal,
   facts per-method, evidence+verify, C#→SQL chain, English semantic.
3. Thất bại: câu verbal tiếng Việt không identifier; partial identifier;
   câu hỏi concept không anchor (D10); "nào đang chạy" khi repo không có
   hai biến thể (I2).
4. Sai sót chủ yếu: (a) **presentation/excerpt** (2 defect — đã sửa);
   (b) **representation granularity + question→identifier gap** (đã biết,
   giờ có số liệu consumer); facts-SQL noise nhỏ. KHÔNG tìm thấy defect
   ở route selection, hash/provenance, verify semantics, symbol resolution.
5. Có: C1/C2 (excerpt false precision) — chưa từng được các mission trước
   phát hiện, vì các audit trước kiểm accounting/budget chứ không đối chiếu
   byte content với source. Đã sửa + test.
6. Có — với điều kiện agent biết (hoặc plan cung cấp) identifier: chuỗi
   nhiều bước D01/D06 hoàn thành trong budget (status 0.01s → symbol 0.03s →
   facts 0.05s → evidence 0.01s → verify 0.01s; hybrid 1.2s).
7. Có: facts invocations (UpdateOrgTotalEmployee → sp_…, ExecuteSqlCommand)
   và rg call-sites khôi phục được evidence mà search nguyên văn bỏ sót.
8. Evidence ủng hộ agent-driven (arm-c) **khi có identifier discovery step**;
   không ủng hộ việc bỏ plan-based workflow cho question-only. Nghiên cứu
   tiếp về ranking/granularity vẫn owner-level.
9. Không còn root-cause foundation nào chặn integration đã sửa xong C1/C2.
   Q2 (granularity) vẫn là quyết định kiến trúc ảnh hưởng lớn nhất.
10. Hành động giá trị cao nhất: (a) lấy authorization inference để chạy
    3-arm thật (đã ready từ correction round; C1/C2 làm evidence trung thực
    hơn cho grading); (b) owner decision về persisted granularity (Q2) và
    prefix matching (Q3).

## 7. Implications cho external-agent RAG

- Arm A (ordinary search) đủ điểm bắt đầu investigation nếu agent được hướng
  dẫn dùng identifier-first; câu verbal thuần cần một bước discovery (vd. ask
  user / plan / tree-sitter outline) — đúng giả thuyết preregistration.
- Grading 3-arm nên dùng binary post-`7ff962c` (content tại tọa độ khai báo
  là trung thực; trước đó grader đối chiếu source có thể bị false negative).
- Không thay đổi frozen metrics/thresholds nào.

## 8. Bundles

- Raw (local-only, chứa private HRM2.0 source):
  `/home/masis/.cache/zvec-grep/r4-external-agent-20261120/quality/`
  (d01-d12-raw.json pre/post-fix, diagnostics-raw.json, các session dirs,
  reproduction: các lệnh trong report này đều replay được từ quality/).
- Sanitized review bundle (strip source content, giữ question/path/range/
  score/matched_by/gaps/receipts + diffs):
  `/home/masis/.cache/zvec-grep/r4-external-agent-20261120/r4-quality-review-20261121.zip`
  (manifest + SHA-256 trong README bundle).

## 9. Giới hạn của kết luận

- Relevance labels do implementation agent (diagnostic), không phải
  independent grader; gold dùng làm oracle diagnostic cho phân tích lỗi,
  không tính vào metric deployable nào.
- Single-run per query (n=5 chỉ cho perf đã có từ trước); không đo variance
  ranking giữa các lần chạy (index deterministic, low risk).
- Không đo held-out H01–H08; không chạy remote inference; không live DB.
- Adapter residual findings tại `fb47e1e` (owner authorization gates) KHÔNG
  được coi là đã đóng bởi mission này.

## 9. Errata (2026-11-21, long-run mission reconciliation)

Two figures in this report were mis-derived and are superseded by
[`r4-longrun-quality-20261121.md`](r4-longrun-quality-20261121.md) §1:

- "8/25 gold coverage" counted matching *items* instead of matched *gold
  spans*. Re-derived: **9/25 entity-range overlap** and **4/25
  delivered-excerpt overlap** for the single verbatim hybrid call (the
  canonical R0 Recall@10 is a macro best-of-six metric and is not this).
- "47% of top-10 are fragments >300 lines" measured **entity/declaration**
  ranges, not delivered excerpts. Delivered excerpts are p50 29 lines /
  1.9 KB, max 65 lines / 2.1 KB.

Everything else in this report (defect C1/C2, capability matrix, failure
attribution) stands; the additional defects C3–C8 are listed in the long-run
report.
