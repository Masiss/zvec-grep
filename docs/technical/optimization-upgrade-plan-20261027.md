# Kế hoạch tối ưu và nâng cấp zvec-grep — 2026-10-27

**Trạng thái: P0 hoàn tất; P1-A (offline) hoàn tất; P1 native baseline gate PASS (exploratory, không phải statistical/production qualification).** V7 (#1710) hoàn tất full trace dưới containment: query p95 181.592 ms, 44 samples, không missed slot, max lag 229.325 ms; query SLO và freshness SLO đều PASS (freshness max 385.027 ms). V6 (#1670) query SLO INCONCLUSIVE và được giữ riêng, không gộp với v7. Ledger một-lần #1710 đã tiêu thụ; không có authorization chạy thêm. P2-A/B correctness-only gate PASS: 17 focused test cases đã PASS, gồm recovery/retry, watcher coalescing, PK-delete idempotency, incremental PK-delete reopen, staged replacement và partial-result error propagation; regression cuối còn được xác nhận qua cả Rust fallback lẫn native C-ABI interposer. Một diagnostic P2-B scheduler có đối chứng chạy dưới authorization #2329: writer/executor calls 14→2, giữ đủ 41/41 changes; final-event-to-writer-start 32→750 ms. Đây là mock-executor proxy, không đo CPU/native lifecycle; xem [P2 baseline](optimization-p2-baseline-20261027.md). Follow-up profile hiện-tree (#2448), operation `p2-native-profile-20261027t134637z-d0a1aa13`, đo `index_optimize`/`finalize_writes()` 5.715 s trên 8.683 s tổng wall time các writer job; riêng bốn mutation job là 89.4%. Query p95 260.040 ms nhưng **INCONCLUSIVE** do 1 missed slot/max lag 274.846 ms; freshness max 444.914 ms (PASS trong lượt này). Đây là diagnostic, không phải A/B/qualification. Follow-up substage v1 `p2-native-substage-profile-20261027t142307z-f5acfee1` test PASS nhưng không phát event do `cfg(test)` không bật cho dependency `zg-engine`; corrected v2 `p2-native-substage-v2-20261027t145503z-2a65b72b` PASS và ghi 8 stage events. Trong bốn watcher mutation finalizations, fragment optimize 2.180 s (58.4%), files 0.882 s (23.6%), entities 0.393 s (10.5%), checkpoint 0.274 s (7.3%). Các update/burst còn `fragments_incomplete`; rename/delete có delete flag, nên chưa có candidate an toàn để defer fragment optimize. Query p95 151.674 ms nhưng 1 missed slot/max lag 318.036 ms: INCONCLUSIVE; freshness max 340.236 ms: PASS trong lượt này. Optimizer-thread A/B v1 (`p2-native-optthreads-ab-20261027t151858z-d36a1394`) chỉ chạy control rồi dừng theo stop rule query; authorization v1 đã tiêu thụ. Pair v2 (`p2-native-optthreads-ab2-20261027t153804z-f479eee4`) hoàn tất sau một lần preflight ban đầu bị chặn vì RAM thấp và lần recheck được owner yêu cầu. Trong một pair exploratory, 4 threads giảm tổng finalize của bốn mutation 1.13% (4.569→4.517 s), nhưng query p95 tăng 21.71% (263.321→320.494 ms) và candidate FAIL provisional SLO 300 ms; freshness PASS cả hai. Fragment optimize chậm hơn 6.74%. Không chọn 4 threads; giữ default 2, không đổi runtime/deploy. Xem [native profile](optimization-native-profile-20261027.md). Xem [P0 baseline](optimization-p0-baseline-20261027.md), [P1 baseline](optimization-p1-baseline-20261027.md), [P2 disposition](optimization-p2-baseline-20261027.md) và [native profile](optimization-native-profile-20261027.md).

**Cập nhật mới nhất (2026-10-31):** P3 research đã đóng ở mức `PARTIAL`; production vẫn `NO-GO`. Native FTS tail được attribute cho native schema-lock, không phải FTS search; corpus 421 cũ là `PROVENANCE_BLOCKED`, camera 421 là replacement. Không chạy benchmark mới. Xem [`M4-P3-TRIAGE-20261030.md`](../../research/m4-native/results/M4-P3-TRIAGE-20261030.md) và [`M4-P3-NATIVE-FTS-STAGE-ATTRIBUTION-20261031.md`](../../research/m4-native/results/M4-P3-NATIVE-FTS-STAGE-ATTRIBUTION-20261031.md).

**Cập nhật trước (2026-10-30):** Phase 3 query lock-split đã DONE (research scope) với `QUALIFIED_DIAGNOSTIC`; đây là native-engine slice, không phải P3/M4 production gate. Xem phần “Cập nhật 2026-10-30” bên dưới.

Phạm vi: toàn dự án, gồm bản TypeScript đang được phân phối, Rust rewrite, retrieval, vận hành/release và nghiên cứu M4. Tài liệu này tổng hợp hiện trạng để chọn việc tiếp theo; không thay thế policy thực thi hay cho phép migration, đổi default/schema, chạy lại operation đã dừng hoặc deploy.

Snapshot khi lập kế hoạch: branch `work/zg-optimization`, HEAD `eac1f5672b4f36e26584124f26a6ad5c27954a95`, **working tree có nhiều thay đổi chưa commit**. Các nhận định dưới đây kết hợp tài liệu, code và diff tại thời điểm lập kế hoạch. Không chạy build, test, benchmark, model hoặc truy cập live user index trong lượt lập kế hoạch đó. Kết quả test/đo được dẫn lại khi ấy là bằng chứng lịch sử, không phải xác nhận working tree.

Cập nhật thực thi P0 sau thời điểm lập kế hoạch dùng HEAD hiện tại và dirty diff riêng; xem [baseline manifest](optimization-p0-baseline-20261027.md). Snapshot `eac1f567...` ở trên chỉ là mốc lịch sử khi soạn plan.

> **Trạng thái M4 hiện tại (2026-10-27):** Latest authorized 64-file diagnostic `m4-rust-lifecycle-pair-20261027t052952z-332a873b` hoàn tất M4-first/native-second; hard-safety telemetry và cleanup đều được xác minh. Queue totals: M4 308 full / 0 disconnected / 42 cap omissions; native 130 full / 0 disconnected / 0 cap omissions. Workspace inventory/soft-budget peaks còn unknown và p95 không comparable, nên Gate B vẫn PARTIAL, chưa có performance verdict/qualification hay production approval. Authorization một pair đã dùng hết; không retry hoặc chạy operation khác. Stop `dc8e1120`/50-of-64 ở pair cũ là lịch sử, không phải trạng thái operation hiện tại. Xem [active checkpoint](../../research/m4-native/results/ACTIVE-EXECUTION.md) và [query-drop report](../../research/m4-native/results/M4-GATE-B-QUERY-DROP-CAUSE-20261027.md).

## Cập nhật 2026-10-30 — Phase 3 query lock-split (research-only)

**Trạng thái: DONE (research scope) — `QUALIFIED_DIAGNOSTIC`, không phải production qualification.** Đây là một slice native-engine, **không phải** quyết định kiến trúc M4 (P3) và **không** mở production gate.

- **Root cause đã isolate:** FTS-only query phải chờ `schema_handle_mtx_` exclusive mà `CollectionImpl::flush()` giữ suốt `flush_unsafe()` (gồm FTS RocksDB flush), và optimize giữ lock tương tự trong lúc seal.
- **Prototype:** flush dùng shared schema + shared write + exclusive `vector_access_mtx_`; FTS-only không lấy vector guard. Optimize publish temporary segment snapshot để FTS-only đọc trong lúc seal.
- **Correctness/recovery:** FTS query chạy **bên trong** `RocksDB::Flush` thật (không phải trước `flush_unsafe()`); 8 failure-injection points PASS; full TSAN native+RocksDB **0 report**; 8 real SIGKILL points PASS (flush sau content CF, flush trước/sau vector indexer flush, optimize trước inverted seal, trước FTS seal, trước side-CF drop, DDL drop-index trước/sau commit), exact count + one more write + close/reopen; 3-cycle crash loop trên cùng collection PASS (16/32/48 docs).
- **Multi-field/tokenizer parity PASS (2026-10-30):** schema `content` (standard FTS) + `title` (whitespace FTS) + vector; exact 64 docs each during flush after both FTS CFs, exact 128 during optimize before FTS seal, exact 128 after close/reopen. v20 test-hook library SHA `1dc813475f7d64b97dce9e72ffa9b1f81a1008c120addf50ca79867932dfd865`. Evidence: [`multifield summary`](../../research/m4-native/results/native-query-timeline-20261029/native-query-lock-split-multifield-20261030/summary.json).
- **Performance:** 3 counterbalanced pairs + 1 A/A, 256 samples/phase/arm. A/A noise flush +0.61 ms (CI -3.21..4.83 ms), optimize +14.7 ms (CI -1.73..33.2 ms). Treatment effect per cycle: flush -60.0/-78.0/-64.3 ms, optimize -109.4/-116.0/-118.0 ms; all CIs below zero. Control flush p50 77.5–85.0 ms vs treatment 9.4–11.3 ms; control optimize 94.4–108.8 ms vs treatment 0.73–0.77 ms.
- **Giới hạn:** workload synthetic 512→4.608 docs, một host; multi-field/tokenizer đã cover 2 FTS field, vector-flush crash, DDL crash và crash loop; multi-host/kernel variation **OPERATION_BLOCKED** (chỉ có một host/kernel); chưa integrate native checkout/production.
- **Hệ quả cho roadmap:** đây là candidate **native lock-contention** có thể release độc lập qua P6 native-first, không cần M4. Nó **không** thay đổi kết luận M4 Gate B, **không** thay thế P3 architecture decision, **không** authorize production.

Evidence: [`M4-PHASE3-QUERY-LOCK-SPLIT-PLAN-20261030.md`](../../research/m4-native/results/M4-PHASE3-QUERY-LOCK-SPLIT-PLAN-20261030.md), [`crash summary`](../../research/m4-native/results/native-query-timeline-20261029/native-query-lock-split-crash-20261030/crash-summary.json), [`pair summary`](../../research/m4-native/results/native-query-timeline-20261029/native-query-lock-split-multipair-20261030/pair-summary.json), [`ACTIVE-EXECUTION.md`](../../research/m4-native/results/ACTIVE-EXECUTION.md).

## 1. Hiện trạng đã cập nhật

| Mảng | Hiện trạng / bằng chứng | Hệ quả đối với plan |
|---|---|---|
| Sản phẩm | TypeScript/Node là implementation được phân phối ở root và compatibility oracle. Rust là runtime riêng; chưa có parity matrix đầy đủ hoặc quyết định cutover. | Không gọi Rust rewrite là hoàn tất chỉ vì code/CI đã có. Tách quyết định runtime khỏi quyết định M4. |
| Kiến trúc | CLI/MCP/daemon → engine → scanner/extraction/model/storage; direct grep là route riêng, không cần index. | Tối ưu tại owning layer, không đưa ranking vào transport hoặc biến indexed search thành grep ngầm. |
| Native indexing | `MaintenancePolicy::from_env()` vẫn mặc định eager (`threshold=0`). Current-tree profile thấy `finalize_writes()` chiếm 65.8% tổng writer-job stage time và 89.4% trên mutation jobs. V2 substage diagnostic tách bốn watcher mutation finalizations: fragments 2.180 s (58.4%), files 0.882 s (23.6%), entities 0.393 s (10.5%), checkpoint 0.274 s (7.3%). | Fragment optimize là stage lớn nhất, nhưng update/burst còn incomplete và rename/delete có delete flag; bỏ optimize có thể làm thay đổi search visibility. Không defer optimize hay đổi maintenance policy. Configuration-only A/B 2-vs-4 optimizer threads hoàn tất một pair: finalize primary chỉ nhanh hơn 1.13% với 4 threads, trong khi query p95 320.494 ms vượt target 300 ms (control 263.321 ms). Fragment optimize tăng 6.74%; freshness dưới 1 s cả hai. Single pair không đủ ý nghĩa thống kê; không promote candidate, giữ default 2. Giữ delete-forces-optimize, không coi kết quả là qualification. Xem [native profile](optimization-native-profile-20261027.md). |
| Maintenance đang dở | Store mới vẫn khởi tạo `fragments_debt=0`, `fragments_ack_epoch=0`, `pending_since_ms=0`; reopen chỉ seed tuổi từ thời điểm mở khi còn incomplete/delete marker. Tuổi chỉ được xét khi finalize chạy, không có timer bảo đảm deadline. | Chưa thể gọi deferral là bounded xuyên nhiều job/restart. Sửa tính đúng của state trước khi đổi default. |
| Cải tiến đã có | Watch coalescing 750 ms / tối đa 5 s đã tồn tại và bật ngoài test; diff có telemetry submissions. Xóa fragment theo PK, chunk 512, đã có trong working tree. | Không lên task “implement debounce/PK deletion” từ đầu. Review, test và đo phần đã làm. |
| Model memory | `StaticEmbeddingTable` vẫn là `Vec<f32>`; loader mở rộng tensor F16 sang F32. | Có hướng giảm RAM không cần đổi index schema, nhưng chưa chứng minh đây là nguyên nhân khiến full lifecycle vượt cap. |
| M4 correctness | Gate A PASS **chỉ trong research integration**; whole-pass fence REWORK. Gate 2 selective-visibility/short-commit-fence cases pass trong phạm vi test, candidate vẫn chưa được qualification. Bổ sung 2026-10-30: Phase 3 lock-split chứng minh FTS-only query chạy trong lúc RocksDB flush I/O thật; 8 failure-injection + 8 real SIGKILL recovery (gồm vector flush và DDL) + 3-cycle crash loop + full TSAN 0 report. | Giữ test/research feature; không phục hồi thiết kế khóa toàn pass làm mặc định. Lock-split là bằng chứng correctness/recovery cho native-engine slice, chưa phải qualification kiến trúc M4 hay production. |
| M4 performance | Gate B vẫn PARTIAL / chưa có performance verdict. Latest authorized diagnostic `m4-rust-lifecycle-pair-20261027t052952z-332a873b` completed M4-first/native-second with hard-safety telemetry and cleanup verified. Cause-specific totals: M4 308 queue-full drops, 0 disconnected, 42 cap omissions; native 130 queue-full, 0 disconnected, 0 cap omissions. Workspace inventory/soft-budget peaks remain unknown and p95 is non-comparable. The one-pair authorization is consumed; no retry or further operation. Earlier `dc8e1120`/50-of-64 stalls remain historical, not current-operation status. Bổ sung 2026-10-30: Phase 3 lock-split multi-pair đạt `QUALIFIED_DIAGNOSTIC` (3 counterbalanced + A/A, 256 samples/phase/arm); query latency giảm mạnh và CIs dưới 0. | Keep measurement quality separate from engine correctness/performance. Existing M4 pair is diagnostic only; do not infer speed or qualification. Lock-split result củng cố hướng giảm lock contention ở native, không thay đổi kết luận M4 Gate B. See latest `research/m4-native/results/ACTIVE-EXECUTION.md`. |
| M4 feasibility | Gate 3 stall xảy ra khi memory peak khoảng 348 MiB và qgroup headroom vẫn trên reserve; không có hard guard nào trigger. Các memory/qgroup stop cũ vẫn là evidence riêng. Pilot 64 file không đại diện full corpus 421 file hay toàn sản phẩm. Bổ sung 2026-10-30: root cause query-wait (exclusive schema lock) đã isolate và prototype loại bỏ wait trong workload đo; full-corpus/resource vẫn chưa giải quyết. | Điều tra liveness/search-vs-lock wait và hoàn thiện measurement; không tăng cap để có PASS. Lock-split không thay thế full-corpus qualification. |
| Retrieval | Có harness SWE-QA20, BEIR, DuRetrieval, Quarry. Protocol SWE-QA20 hiện ghi `quality_gate=report-only-until-a-reviewed-baseline-is-frozen`; latency policy không phải controlled speed comparison. | Có hạ tầng đánh giá nhưng chưa được suy ra quality release gate hoặc baseline đã duyệt. Không dùng latency của harness này thay benchmark lifecycle. |
| Product vision | Roadmap vẫn bao gồm nhiều format/multimodal, graph/query planning, GUI và desktop/mobile. | Giữ trong roadmap nhưng mở sau nền tảng correctness, quality và release; không làm đồng thời tất cả. |

### Tài liệu cũ cần đọc với trạng thái mới

- Audit `rust/docs/audit-20261025.md` là baseline lịch sử; §9 có disposition/fixes. M-01/M-03 không được coi là lỗi chưa sửa chỉ vì bảng finding ban đầu còn đó. PK deletion cũng đã có diff mới.
- `rust/docs/native-build-optimization-plan.md` còn ghi task tạo Linux script chưa làm, nhưng `rust/scripts/build-zvec-linux.sh` đã tồn tại và được track. Việc còn thiếu là xác nhận độ an toàn/reproducibility và kết quả đo hiện tại, không phải tạo lại script.
- `rust/compat/README.md` còn mô tả `mcp/` như tương lai, trong khi `rust/compat/mcp/` đã có fixture và README chi tiết. Fixture hiện có không đồng nghĩa thiếu toàn bộ MCP parity, cũng không chứng minh parity đầy đủ.
- Đầu `rust/docs/m4-incremental-native-index.md` chứa checkpoint cũ; khi tiếp tục M4 phải ưu tiên `research/m4-native/results/ACTIVE-EXECUTION.md` và plan recovery ngày 27/10.
- Những câu mô tả M4 như production backend, mmap/bounded-memory tuyệt đối hoặc rewrite hoàn tất phải kiểm tra source/contract trước khi sử dụng. Owner đã chọn **không có absolute total WAL-file cap**: recovery của WAL ngoại lai/non-default quá lớn chưa có absolute RAM bound.

## 2. Chiến lược và cách đọc gate

**Khuyến nghị: hai đường tiến triển độc lập, hội tụ tại release.**

1. Đường thực dụng: ổn định WIP → baseline → native optimization + retrieval + parity.
2. Đường nghiên cứu: baseline → sửa measurement → chứng minh candidate M4 → quyết định có đáng tích hợp hay không.

M4 bị block không được làm dừng những việc độc lập của đường thứ nhất. Ngược lại, một native improvement nhỏ không tự kết thúc câu hỏi kiến trúc M4.

```text
P0: chốt WIP/current state
 ├─ P1: baseline + protocol ─┬─ P2: native/resource optimization ─┐
 │                         ├─ P3: M4 research decision ────────┤ chỉ bắt buộc nếu chọn M4
 │                         └─ P4: retrieval improvement ───────┤
 └─ P5: parity + compatibility + runtime hardening ────────────┤
                                                            P6: release/rollback
                                                             └─ P7: mở rộng sản phẩm
```

Đây là dependency graph, không yêu cầu nhiều người/agent chạy song song. Với một người, làm lần lượt từng lát nhỏ; không chạy các measurement nặng đồng thời.

Mỗi phase có vài hướng; ghi rõ hướng bổ sung nhau hay phương án thay thế. **Không bắt buộc implement hết mọi hướng để pass.** Gate phải kết luận theo evidence:

- **PASS:** đầu ra và acceptance của phase đạt trên phạm vi đã chốt.
- **REWORK:** có defect hoặc candidate chưa đạt; quay về đúng phần lỗi.
- **INCONCLUSIVE:** phép đo hợp lệ nhưng chưa đủ phân biệt; không công bố win/regression.
- **OPERATION_BLOCKED:** một operation không thể chạy an toàn/hợp lệ; giữ evidence và tiếp tục việc độc lập.
- **OWNER_DECISION_REQUIRED:** đổi default/schema/public behavior, migration, cutover hoặc policy cần quyết định rõ.

Một phase có thể hoàn tất bằng quyết định **giữ baseline / loại candidate**, không cần ép mọi thử nghiệm thành cải tiến. “Gate phase PASS” không đồng nghĩa candidate được production GO.

## 3. P0 — Chốt phần đang làm và baseline code có thể review

**Mục tiêu:** biết chính xác cái gì đang có, đã kiểm đến đâu và còn thiếu gì trước khi thêm tối ưu.

### Các hướng xử lý

- **A — Hoàn thiện WIP theo lát nhỏ (khuyến nghị):** inventory riêng scheduler/coalescing, PK deletion, native telemetry/maintenance, selective-visibility/fence, M4 containment, tài liệu. Mỗi lát có source/diff/test/evidence; tách thay đổi behavior khỏi instrumentation và documentation. Commit chỉ khi được phép, không stage cả working tree.
- **B — Cô lập lát còn bất định:** giữ patch/WIP nguyên vẹn nhưng tách candidate chưa đủ evidence khỏi baseline được đo bằng feature/test seam hoặc checkout/worktree riêng. Không reset/revert công việc của người khác.
- **C — Đóng discrepancy tài liệu:** cập nhật navigation/current-status và disposition của finding; không viết lại receipt/report cũ để biến nó thành bằng chứng mới.

A và C bổ sung nhau; B là cách giữ tiến độ nếu không thể hoàn thiện tất cả WIP ngay.

**Gate P0:** có inventory versioned chỉ rõ `implemented / verified-at-revision / unverified / blocked`; baseline gồm HEAD **và diff/source manifest**, không chỉ commit hash; focused tests cho lát định giữ được chạy và ghi kết quả; default build không kéo M4 research vào production. Không còn claim hiện trạng quan trọng mâu thuẫn mà chưa ghi disposition. Test fail trong lát được chọn → REWORK, không chuyển nó thành “known issue” để đo tiếp.

**Đầu ra:** một baseline manifest và checklist WIP ngắn, không thêm hệ thống quản lý evidence mới.

## 4. P1 — Baseline thực tế và acceptance trước khi tối ưu

**Mục tiêu:** trả lời “tốn ở đâu, chất lượng thế nào, tối ưu để đạt điều gì?” bằng workload cố định.

### Các hướng xử lý

- **A — Phân tích offline trước (khuyến nghị):** dùng log/trace hiện có để phân rã open/scan/extract/embed/write/checkpoint/optimize/publish/query-wait. Giữ nhãn historical; không gọi replay mô phỏng là latency mới.
- **B — Một baseline native có containment:** khi được thực thi và preflight hợp lệ, đo trên source fixture operation-owned; trace gồm initial index, small update, burst, rename, delete, idle rồi reopen, repeat rescan và query đồng thời. Tách watcher submission-to-visible khỏi riêng thời gian writer job.
- **C — Baseline retrieval từ harness sẵn có:** ưu tiên replay/report-validation offline, sau đó mới thu phần evidence còn thiếu. Freeze package, corpus/model/query/gold/protocol. Chọn suite đúng workload trước, không chạy toàn bộ bốn suite đắt tiền chỉ để có số.

### Chốt metric và ngưỡng

Đề xuất để review **trước measurement mới**, không phải ngưỡng đã được owner duyệt:

| Loại thay đổi | Mục tiêu lợi ích đề xuất | Guard không được đánh đổi |
|---|---|---|
| Native maintenance/coalescing | Giảm ≥20% CPU maintenance hoặc lifecycle wall time trên trace mục tiêu; chọn một primary trước khi đo | Không lỗi correctness/recovery; query p95 và event-to-visible không xấu quá 10% so baseline, đồng thời đáp ứng absolute SLO đã chốt |
| Memory-only | Giảm ≥20% peak memory của component được đổi; báo riêng mức giảm toàn process | Output giữ contract; CPU/query p95 không xấu quá 10%; toàn workload vẫn phải nằm trong cap hiện hành |
| M4 architecture | Giảm ≥30% end-to-end lifecycle wall time trên churn đại diện so native baseline được chọn | Steady-state parity; query p95/freshness không xấu quá 10% và đạt absolute SLO; recovery/storage/resource đạt envelope |
| Retrieval relevance | Tăng ≥0,02 absolute primary nDCG@10 repository-macro trên code suite cho thay đổi hướng relevance | Không giảm >0,01 ở suite liên quan khác; budget context và latency đã chốt không bị vượt; xem uncertainty ghép cặp |

Các số trên là **screening targets đề xuất**, không sửa protocol đang freeze. Owner/reviewer có thể chọn ngưỡng khác tại P1, trước khi xem candidate result. Absolute SLO về interactive query/freshness phải được chốt theo nhu cầu sử dụng; baseline gần 0 không dùng tỷ lệ phần trăm. Không đủ SLO hoặc uncertainty hợp lý thì chưa được qualification/release claim.

**Gate P1:** workload/trace và primary metric được đóng băng; có baseline hợp lệ hoặc ghi blocker đúng phạm vi; thresholds/SLO, sample design, cost budget và stop conditions được review. Correctness, resource, measurement integrity và relevance có tiêu chí riêng. Retrieval quality gate chỉ nâng từ report-only sau khi baseline được duyệt, không tự sửa gold/protocol để đạt mục tiêu.

**Đầu ra:** protocol ngắn dùng lại harness hiện tại + baseline/report có provenance; tối đa một lần thu cần thiết cho mỗi điều kiện, chỉ lặp khi cần trả lời câu hỏi uncertainty/order effect cụ thể.

## 5. P2 — Tối ưu native và RAM trước, không đổi kiến trúc index

**Điều kiện vào:** P0; measurement claims cần P1. Không phụ thuộc M4 PASS.

**Disposition 2026-10-27:** P2 đã đóng **PASS as a baseline-retained decision**; không có performance candidate nào được qualify. P2-C (model memory) và P2-D (build) **không được chọn**. Chi tiết: [`optimization-p2-baseline-20261027.md`](optimization-p2-baseline-20261027.md). Muốn mở lại P2 phải có evidence mới + protocol/authorization riêng.

### Các hướng xử lý

- **A — Sửa maintenance state trước khi tuning (ưu tiên cao):** reproduce debt/age mất qua close/reopen; thiết kế state bền nhỏ, versioned, giữ epoch/ack, delete marker và retry semantics. Phân biệt “age kiểm tại finalize” với hard deadline; nếu cần deadline thật phải có maintenance owner/timer, cancellation/shutdown và tránh starvation. Kiểm tra duty-cycle trước khi chọn threshold/cooldown; state persistence có thể khiến optimize chạy **nhiều hơn**, không mặc nhiên nhanh hơn.
- **B — Hoàn thiện event/write path đã có (bổ sung A):** verify last-event-wins, bounded coalescing, explicit request không bị Watch trì hoãn, shutdown/cancel/freshness epoch. Đo telemetry submissions/jobs để phân biệt ít job hơn với job rẻ hơn. Review/test PK deletion và per-document native result, missing-ID idempotency, partial failure. **Không trì hoãn finalize của một job đang chạy.**
- **C — Giảm model-memory (candidate độc lập):** giữ tensor F16 và chuyển hàng khi pooling, hoặc mmap/lazy load nếu memory profile chứng minh cần thiết. Giữ output/accumulation semantics, tokenization và model identity. Đây không phải đổi vector index sang F16/int8. Đo cả cold load, warm query và peak của toàn process.
- **D — Tối ưu build chỉ khi nó cản iteration:** review script Linux đã có, compiler cache/Ninja, tổng concurrency ngoài+nested Arrow và identity native library. Không rebuild production-pinned native chỉ để nghiên cứu M4. Không dùng dự báo 9–10 phút của plan cũ như kết quả đã đạt.

A/B là đường chính; C/D chỉ chọn khi baseline cho thấy lợi ích và chi phí hợp lý. Không nhồi mọi tối ưu vào một A/B.

**Gate P2:** reopen/age/debt/epoch/delete/retry tests đạt; watcher/filter/hydration semantics không đổi; thay đổi được chọn đạt mục tiêu P1 với evidence hợp lệ, hoặc bị loại và baseline được giữ. Mọi flag-off path vẫn tương thích. **Eager default và delete-forces-optimize được giữ** trừ owner phê duyệt G-3/G-4; `merge_segments_`/segment sizing cũng không đổi ngầm. Sidecar maintenance state phải có compatibility/failure story trước khi ghi vào user index.

**Fallback:** giữ eager native, ship riêng PK deletion/telemetry/coalescing đã qua gate nếu có lợi ích; không lấy việc deferral chưa đạt làm lý do buộc tích hợp M4.

**P2-A/B correctness-only follow-up (2026-10-27):** source review found that a persisted `maintenance.delete` marker is not independently included in `wants_fragments`; after reopen, a complete (1.0) fragment index can therefore skip the required delete optimize. The working tree includes the minimal scheduling fix in `rust/crates/zg-engine/src/storage/store/mod.rs`, plus ignored Step-2 reopen/insert-debt regression assertions in `rust/crates/zg-engine/src/storage/store/tests.rs`. Existing watcher tests cover bounded trailing coalescing, same-path last-event-wins and explicit-name preservation. A single synthetic P2-B scheduler diagnostic ran under the user's Phase-2 authorization; see the P2 report. It is not a native CPU/lifecycle measurement. Across separate runs, the focused correctness subset has **17 passing test cases**: eight Step-2 maintenance/reopen/delete tests, three scheduler coalescing/explicit-name tests, fragment test `deleting_fragment_ids_by_primary_key_is_idempotent` (missing and repeated keys are harmless), `gate_k_optimize_failure_recoverable` (failure retained debt; retry cleared it and reached completeness 1.0), daemon test `a_new_recovery_epoch_cannot_be_cleared_by_an_older_full_refresh`, current-source regression `step2_reopen_detects_incremental_pk_delete_debt` (durable marker survived reopen and forced optimize), staged-path regression `staged_replacement_streams_batches_and_reopens_consistently`, and `partial_primary_key_delete_failure_is_reported_after_prior_success`. The Gate K failure/retry and recovery-epoch tests ran under a 180 s timeout and 1 GiB cgroup. Logs: `.tmp/p2-correctness-20261027/p2-gate-k-optimize-failure-retry-20261027T115021Z-2cad2cac/test.log` (SHA-256 `f6a994f5615aed278dd04b5b85af3ea3ec21201d561e8b0e7ecc96510122a58f`) and `.tmp/p2-correctness-20261027/p2-recovery-epoch-20261027T115242Z-c819592d/test.log` (SHA-256 `f63599c8088e2f447bd706a419a4193d1ded3b1d2499df23e22ce710f836f2a5). An earlier Gate K wrapper invocation failed before test discovery due to the wrong working directory; no test ran in that invocation, and the corrected run passed. `step2_ab_benchmark` and other ignored Gate K cases were excluded. P2-A/B correctness-only gate is PASS for the focused scope. No performance candidate was selected: the substage profile localizes fragment optimize as the largest mutation cost, but current incomplete-fragment/delete visibility invariants block deferral. A configuration-only optimizer-thread pair (2 vs 4) completed under protocol v2; 4 threads improved four-mutation finalization only 1.13% in one run, while query p95 was 320.494 ms and failed the 300 ms provisional SLO. The result is exploratory, not statistical qualification; retain 2 threads. Phase 2 remains PASS as a baseline-retained decision, not performance or production qualification; defaults and deployment are unchanged.

**P2 follow-up — incremental PK-delete durability (2026-10-27):** review found that `delete_files` persists `maintenance.delete`, but normal and staged replacement paths can delete fragment primary keys (or full-replace an existing file) without persisting that intent. Added `persist_replacement_delete_intent` before manifest/row mutation in both paths and ignored regression `step2_reopen_detects_incremental_pk_delete_debt`. The fragment delete loop checks native result cardinality and each per-document status, treats `NotFound` as idempotent, and errors on other statuses; durable intent precedes the first delete, so partial writes remain recoverable by retry/replacement. Result validation is routed through the shared production helper `validate_delete_result`. The ordinary Rust-only branch directly tests that helper after a real zvec deletion of the first fragment. The Linux interposer branch additionally exercises `Fragments::delete_fragment_ids`, `Collection::delete`, and Rust FFI result decoding, verifies the injected status includes the failed real fragment ID, and checks that only the first real fragment was removed. An initial empty-primary-key probe was preflight-blocked by the 2 GiB test envelope's 4 GiB host reserve (MemAvailable 3,791,409,152 < 6,442,450,944 bytes; no test launched). After the user's later instruction and a passing preflight (MemAvailable 8,862,138,368; disk 30,323,949,568 bytes), one bounded probe ran: zvec returned success for the valid key and `NotFound` for the empty key, so this path is idempotent rather than a non-NotFound failure. Log `.tmp/p2-correctness-20261027/p2-native-empty-pk-probe-20261027T124821Z-1d43186a/test.log` (SHA-256 `de91ede40305755e22c718837e1155417fb075e3728f8f9210321dacb63fb61d`). The remaining boundary gap is now covered with a test-only Linux `LD_PRELOAD` C-ABI interposer: it calls real zvec for the first PK, substitutes a missing placeholder PK for the second (leaving the real second document untouched), then changes that native-owned result code from `NotFound` to `InternalError` before `zvec-rust` decodes it. The production `delete_fragment_ids` path reports the failed real fragment ID and the collection is verified partially mutated. This is explicit C-ABI fault injection, not a claim that the zvec backend naturally emits `InternalError`. The interposer regression passed 1/1 under a 2 GiB cgroup, with the injection marker observed; log `.tmp/p2-correctness-20261027/p2-native-delete-c-abi-20261027T125521Z-e90edeb3/test.log` (SHA-256 `5c9fe2527cdbc84a1d5140852e6a32c6bcea90827ff5bdcbc41f6504d4994f6a`), shim SHA-256 `2cf4324ade5e62811891bf822b447e17f4b7a908e079bc915ebe5cdd73af4702`. The ordinary Rust validator-test path also passed 1/1 without the interposer: `.tmp/p2-correctness-20261027/p2-native-delete-rust-fallback-20261027T125621Z-83e2a640/test.log` (SHA-256 `7539894e803cb30344ef475255ce514b541e44132ec0bd86845d2911150f3a5f`). Reproduction script: `rust/crates/zg-engine/tests/run-native-delete-fault-injection.sh`; C shim: `rust/crates/zg-engine/tests/support/native_delete_fault_interposer.c`. No production code/default changed. Durable-intent/reopen behavior is covered separately. The current-source incremental-PK regression passed 1/1 under a 2 GiB cgroup: `STEP2_PK_REOPEN pending=true pending_after=false optimize_before=0 optimize_after=1`. Log: `.tmp/p2-correctness-20261027/p2-pk-delete-actual-test-20261027T121830Z-c75b2a8d/test.log` (SHA-256 `6a6eaa08f8c8fb42370fc0ba3782a39b9e9b9edd02b7bb092cc76c236580c4bf`); preflight MemAvailable 7,832,043,520 bytes and disk headroom 30,493,605,888 bytes. The staged replacement regression also passed 1/1 under a 1 GiB cgroup: `.tmp/p2-correctness-20261027/p2-staged-replacement-verify-20261027T122406Z-e2f6becc/test.log` (SHA-256 `19d0531cf62c1f106bff01ec8322082b86512e5b8e80d96490aa010015a8c1c1`). The real-partial-mutation regression passed 1/1 under a 2 GiB cgroup: `.tmp/p2-correctness-20261027/p2-delete-actual-partial-20261027T124011Z-97a2b15e/test.log` (SHA-256 `1cd14e58c8f682292c36bb7f2fd7fd1562f80e01e50cfff1cee2d6eab8f8c6a5`); preflight MemAvailable 8,763,744,256 bytes and disk headroom 30,359,089,152 bytes. An earlier validator-only injection also passed 1/1 at `.tmp/p2-correctness-20261027/p2-delete-partial-result-20261027T123118Z-27fada78/test.log` (SHA-256 `e2244090d80d9bd2235b48b5e0fb07c7da6db399539d558ae967d0907e2e0964`). Preserve earlier non-passing attempts: the 1 GiB compile was OOM-killed (`systemd` result `oom-kill`, peak 1,073,741,824 bytes) at `.tmp/p2-correctness-20261027/p2-pk-delete-repro-20261027T120309Z-f0bd69c9/`; a later 2 GiB compile first exposed a missing test-helper argument, which was fixed (`.tmp/p2-correctness-20261027/p2-pk-delete-verify-20261027T121647Z-d1361316/test.log`, SHA-256 `294a1c1889aed4d3446fa200b2a2fea59f061631c5be60381bff3c51699b46f9`). The next invocation compiled but its exact filter selected 0 tests (not counted; `.tmp/p2-correctness-20261027/p2-pk-delete-verify-20261027T121747Z-4a5606a6/test.log`, SHA-256 `403b238be1246adf88aba61fd3978f9547824d4273c94c62eb361c0b749a6ed9`); the corrected filter then ran the test above. An earlier preflight at 4,671,737,856 bytes available blocked before launch; the later owner instruction authorized the successful bounded attempt. No full native lifecycle/CPU benchmark ran; the only P2-B scheduler diagnostic is described in the P2 report. No default or production runtime changed.

## 6. P3 — Đóng câu hỏi M4 bằng evidence, không mở thêm campaign vô hạn

**Trạng thái P3 (2026-10-31): research closure `PARTIAL`; production `NO-GO`.** Native FTS tail đã được decompose: `collection_query` p99 184.7 ms vs `schema_handle_lock` p99 184.0 ms; inner FTS stages <0.5 ms p99; đây là native lock/publish concurrency, không phải FTS cost. Multi-host vẫn `OPERATION_BLOCKED`; exact 421 per-query trace là optional và cần operation mới. Corpus 421 cũ `PROVENANCE_BLOCKED`; camera 421 là replacement. Xem [`M4-P3-NATIVE-FTS-STAGE-ATTRIBUTION-20261031.md`](../../research/m4-native/results/M4-P3-NATIVE-FTS-STAGE-ATTRIBUTION-20261031.md) và [`M4-P3-TRIAGE-20261030.md`](../../research/m4-native/results/M4-P3-TRIAGE-20261030.md).

**Trạng thái P3 trước đó (2026-10-30): camera 300 và 421 chạy xong dưới 768 MiB/1 GiB và qgroup 384 MiB; M4 0 drops ở 421; cả hai cặp không comparable; Stage E vẫn blocked; overall `REWORK`; production NO-GO.** 300: M4 483/484, 1 drop, p95 42.7 ms; native 479/503, 24 drops, p95 225 ms. 421: M4 506/506, 0 drops, p95 34.2 ms; native 492/498, 6 drops, p95 151.5 ms. Parity PASS. Đây là camera subset, không phải Stage E; p95 không qualification. Kết quả: [`camera 300/421`](../../research/m4-native/results/M4-P3-CAMERA-300-421-RESULT-20261030.md).

**Điều kiện vào:** baseline/protocol P1. Đây là research track; chi tiết operation tiếp tục theo [M4 recovery plan](../../research/m4-native/results/M4-RECOVERY-EXECUTION-PLAN-20261027.md).

### Các hướng xử lý

- **A — Khôi phục độ tin cậy measurement (bắt buộc trước pair):** một vòng diagnosis bounded cho observer/capacity và query sampler. Hai phương án capacity thay thế: (1) proof quota chính xác cho purpose/root/UUID/subvolume/qgroup, nếu policy/owner chấp nhận; (2) observer rẻ hơn với mất event/overflow/gap vẫn fail-closed. Không tăng entry/time budget hoặc cho incomplete scan thành PASS. Query measurement phải ghi offered/started/completed/late/missed/drop/queue delay; zero missed starts ở protocol cùng offered load, hoặc bounded queue model đã khai báo. Không so p95 chỉ từ số request sống sót.
- **B — Chứng minh selective visibility + short commit fence (candidate chính):** test read view từ retrieval đến hydration, file-mask suốt pending interval, unchanged-file reads, filtered refill/ranking, overlapping updates, rename/delete/recreate, cancel/shutdown, crash/reopen tại mọi durable transition. Không giữ fence trong thời gian chờ remote embedding. Whole-pass fence chỉ làm historical control, không trở lại làm candidate mặc định.
- **C — So matched full lifecycle rồi quyết định kiến trúc:** sau A/B mới có diagnostic pair 64 file; sau đó giải quyết memory/capacity ở source trước full corpus 421 file và corpus đại diện đã thống nhất. Hai arm đều dùng Rust engine/native baseline; candidate thêm M4 sidecar, gồm chi phí duplicate store, WAL, flush/compaction, recovery và cleanup — không so C++ microbenchmark với production path.
- **D — Giữ native hoặc mở revisioned architecture sau decision:** nếu candidate không đạt, giữ native là fallback hợp lệ. Immutable revisions + active-set manifest chỉ được thiết kế sâu khi evidence chứng minh short fence không đạt SLO; nó có thêm native-versioning/GC/storage/migration cost, không phải bước tiếp theo tự động.

**Native lock-split slice (2026-10-30):** Phase 3 query lock-split đã DONE (research scope) với `QUALIFIED_DIAGNOSTIC`, nhưng đây là một **native-engine optimization slice**, không phải kết luận P3. Nó không thay đổi Gate P3/B, không chứng minh M4 hay production; nếu muốn đưa vào sản phẩm, phải đi qua P6 native-first với integration/migration/rollback riêng. Xem phần Cập nhật 2026-10-30.

**Gate P3:** kết luận `GO-next-research-integration / REWORK / NO-GO-current-candidate / INCONCLUSIVE / OPERATION_BLOCKED` với ba lớp tách biệt:

1. Correctness/recovery: không mixed snapshot, mất durable intent, deadlock hoặc stale hydration ngoài contract; parity steady-state đúng. Process restart test không bị nâng thành power-loss proof.
2. Measurement/resource: cả hai arm hoàn tất, telemetry/postflight/cleanup đạt, query schedule so được, source/binary/trace đồng nhất; giới hạn memory/swap/CPU/tasks/runtime/disk vẫn giữ. Không có `io.stat/io.max` thì không claim cgroup physical-I/O attribution/containment.
3. Value: đạt target P1 trên workload đại diện và uncertainty đủ cho claim. Pair đơn chỉ diagnostic; counterbalance/lặp chỉ trong budget và để giải quyết order effect/uncertainty đã xác định.

**Evidence class:** operation chạy theo contention-aware exploratory policy vẫn là `EXPLORATORY / NON-QUALIFYING`; thêm mẫu hay đạt screening target không tự nâng thành qualification. Claim qualification cần protocol/policy và authorization phù hợp riêng; nếu chưa có thì chỉ kết luận research trong phạm vi được đo.

**Stop rule:** hết một vòng bounded observer diagnosis mà không chứng minh được capacity theo policy thì dừng nhánh benchmark, báo blocker cụ thể; không xây resource framework mới. Không tự retry pair đã dừng. Current request này chỉ lập kế hoạch; launch sau này phải có authorization hợp lệ theo current operation/policy.

**Giới hạn giữ nguyên:** không mở lại codec/framing exploration; không thêm absolute total WAL cap trái quyết định owner. Đánh giá growth/recovery theo default-threshold envelope, ghi rõ oversized external WAL chưa absolutely bounded. P3 PASS không phải quyền production migration.

## 7. P4 — Cải thiện retrieval có kiểm soát

**Điều kiện vào:** P1 quality baseline; có thể làm độc lập P2/P3. Error analysis trước, mỗi lần một hypothesis.

### Các hướng xử lý

- **A — Cải thiện serving/ranking không đổi format (khuyến nghị đầu tiên):** trace miss theo discovery → extraction → lexical/vector candidate → filter/refill → fusion → ranking → preview. Ưu tiên bug/rule có regression fixture: thiếu candidate sau filter, identifier/path matching, duplicate hoặc tie-break, source coordinates và context quá dài. Tuning RRF không phải mặc định chỉ vì dễ làm.
- **B — Tokenizer `standard` + identifier subword (cùng một candidate):** audit proxy gợi ý có tín hiệu nhưng không đủ qualification; `standard` đơn lẻ có thể giảm recall. Thử offline trên isolated index với version/schema và reindex/rollback rõ; giữ original identifier để exact lookup. Đây là G-2, không rollout ngầm lên index hiện có.
- **C — Model/chunk theo workload:** chỉ mở multilingual hoặc contextual embedding khi query/language labels cho thấy lỗi model thật. Giữ extraction/chunk budget cố định khi so model, hoặc tách ablation để không lẫn hai yếu tố. Không đổi default model, bỏ punctuation pooling, quantize index hay tăng chunk chỉ dựa proxy một repo.

A/B/C là các candidate khác nhau; không đổi model + tokenizer + chunk + weights cùng một lần. Phần nào đổi cùng nhau theo thiết kế (như standard+subword) phải được ghi như một compound candidate có control phù hợp.

**Gate P4:** frozen gold/protocol; paired comparison trên code suite và suite liên quan, metric theo repository/query class chứ không chỉ micro-average; relevance đạt target P1 hoặc giữ baseline. Không regress exact-identifier/filter/path/hydration contract; preview thực sự hỗ trợ câu trả lời trong budget context. Model từ xa cần explicit egress consent và chi phí dự kiến, không dùng nhiều remote suite đồng thời. Candidate format-changing còn cần G-2/migration; PASS offline không cho phép reindex user data.

**Đầu ra:** chọn tối đa một candidate đã chứng minh lợi ích cho release gần nhất, hoặc quyết định không đổi relevance. Các hướng còn lại giữ deferred, không kéo dài tuning trên gold public.

## 8. P5 — Chốt parity Rust, compatibility và vận hành

**Điều kiện vào:** inventory P0; bắt đầu matrix được ngay, không chờ M4. Không làm lại những MCP fixture/test đã có.

### Các hướng xử lý

- **A — Differential matrix theo surface (bắt buộc):** CLI defaults/output/exit codes; MCP tools/schema/presentation/errors; direct/server; discovery/extraction/filter/search; model/authorization; index lifecycle/status/freshness/cancellation. Mỗi row có TS source, Rust source, fixture/test, platform/conditions, evidence revision và disposition. Intentional differences phải link các TOML registry hiện có.
- **B — Integration/operations hardening (bổ sung A):** actual CLI/stdio/HTTP daemon boundary, watch overflow/reconcile, concurrent workspace access, late freshness epoch, process restart, interrupt/update recovery, shutdown/model/resource cleanup; platform/package CI và consumer install/uninstall/upgrade. Kiểm tra loopback/auth/origin, workspace/symlink scope và endpoint-bound consent ở đúng boundary; không phải mở một audit toàn repo không giới hạn.
- **C — Product compatibility decision:** Node API ở `src/index.ts` không biến thành Rust API chỉ bằng đổi binary. Owner chọn giữ TS package API, có replacement/binding tương thích, hoặc deprecate có version/migration. Tương tự, TS/Rust index layout không được giả định interchangeable: quyết định rebuild/import/migration và rollback riêng.

**Gate P5:** mọi supported external surface có disposition; zero unexplained differential mismatch trên inventory đã chốt; mọi intentional difference được review, không đánh dấu toàn rewrite PASS từ test count. Supported OS/package matrix và actual process-boundary tests đạt; index/config/model migration hoặc rebuild được diễn tập trên fixture với rollback. Unsupported/deferred behavior chỉ ra khỏi supported inventory bằng quyết định sản phẩm rõ, không âm thầm xóa row để pass.

**Đầu ra:** parity matrix đủ dùng để quyết định release, danh sách intentional differences và runbook compatibility/recovery ngắn.

## 9. P6 — Release có rollback, không gộp runtime cutover với M4

**Điều kiện vào:** P0/P1 và P5 cho release/cutover Rust; P2/P4 đã có disposition rõ. Nếu release dùng native không cần chờ P3. Nếu release muốn chứa M4 thì P3 và milestone production integration được phê duyệt là điều kiện thêm.

### Các hướng xử lý

- **A — Native-first / giữ TS default (rủi ro thấp nhất):** phát hành riêng các improvement đủ evidence; Rust giữ canary/opt-in. M4 tiếp tục research, không thành release blocker.
- **B — Rust cutover, vẫn native storage:** khi parity, packaging, quality/resource targets và owner decision đủ. Diễn tập install/upgrade/failure/rollback, chọn đúng executable/runtime và index namespace; không retire TS API ngầm.
- **C — M4 opt-in rồi mới xét default (giai đoạn sau):** cần integration/migration design riêng, durability/support envelope và fallback/rebuild được thử. Không bật chỉ bằng compile research feature trong package production.

Đây là các cấp rollout, không phải ba việc bắt buộc làm cùng lúc. Khuyến nghị tách **runtime cutover → storage migration** thành hai decision/release độc lập.

**Gate P6:** release candidate từ baseline reproducible; regression/relevance/resource gates đúng phạm vi đạt; artifact/version/provenance/package consumer tests đạt; migration/rebuild và rollback được diễn tập; observability đủ nhận diện stale index, maintenance debt, query wait, recovery failure. Owner phê duyệt runtime/default/migration/rollout cụ thể. Có fail-closed recovery và rollback trigger định trước; không yêu cầu thử trực tiếp trên live index để hoàn tất plan.

**Kết quả:** release được phê duyệt hoặc giữ runtime hiện tại với canary; không kết luận “toàn dự án đã tối ưu xong”.

## 10. P7 — Nâng cấp sản phẩm sau nền tảng

**Điều kiện vào:** P6 cho nền tảng được chọn; từng pilot cần scope/resource acceptance riêng. Các hướng dưới đây là product vision hiện có, không bị bỏ khỏi roadmap nhưng không mở đồng loạt.

### Các hướng xử lý

- **A — Format-aware extraction:** chọn một format có nhu cầu thật, ưu tiên extraction giữ structure/source location trước OCR/vision phức tạp. Parser fixture gồm malformed/large input, cancellation và bounded resources. Không mặc nhiên thêm external conversion/service.
- **B — Structural/graph retrieval + query planning:** trước hết dùng symbol/import/reference đã có để kiểm chứng added value trên task set; chỉ thêm graph store khi cần. So với lexical/vector/structural baseline, tránh “xây graph rồi mới tìm use case”.
- **C — Out-of-box UX và platform:** bắt đầu diagnostics/onboarding/install/model/permissions bằng CLI hoặc GUI desktop theo nhu cầu. Mobile là feasibility pilot riêng cho power/memory/background lifecycle/local execution, không copy nguyên desktop daemon xuống điện thoại.

**Gate P7:** mỗi pilot có user workflow và benchmark/task set riêng; chứng minh thêm giá trị so baseline, giữ local-first/consent, source traceability và resource budget; có owner quyết định promote/defer/stop. Thành công một pilot không tự mở toàn bộ multi-format/graph/GUI/mobile scope.

## 11. Thứ tự triển khai thực dụng và quyết định còn thiếu

### Nếu chỉ chọn ba lát đầu tiên

1. **P0:** review các diff đang có, chạy focused regressions cho PK deletion/coalescing và fence; chốt current-status/disposition. Không viết lại các fix đã có.
2. **P1 + P5-A:** freeze baseline/acceptance và lập parity matrix từ test/fixture hiện có. Bắt đầu bằng offline evidence, chỉ thu measurement còn thiếu.
3. **P2-A/B:** reproduce maintenance reopen/debt/age, sửa correctness sau seam phù hợp rồi đo một trace đã freeze. Đồng thời lên bounded test-only remediation của P3-A nếu tiếp tục nhánh M4 được giao.

P2 đã đóng theo baseline-retained; P2-C/P2-D không được chọn. Chỉ mở lại P2 nếu có memory/build evidence mới và protocol/authorization riêng. P4-A (retrieval) là nhánh sau optimize, không mở trước khi optimize kết luận.

### Timebox, không phải cam kết deadline

- P0 và baseline inventory: nhỏ, hướng tới vài ngày kỹ sư, không mở refactor.
- P2: từng candidate một lát riêng; pass correctness trước khi tiêu budget measurement.
- P3-A: đúng một vòng diagnosis bounded với deliverable và stop condition chốt trước; không kéo thành framework/campaign mới.
- P3 full qualification, P5 platform closure và P6 migration chưa thể ước lượng đáng tin trước khi có baseline/matrix và tài nguyên khả thi. Không hứa ngày cutover từ các test hiện có.

### Owner decisions cần chốt khi đến đúng phase

1. P1: provisional SLO/workload/primary metric chốt ở #576. V6 (#1670) có query SLO INCONCLUSIVE (44 samples, 1 missed slot, max lag 315.569 ms). Sau chẩn đoán, structured owner approval #1710 cho đúng một lượt follow-up: v7 đạt query p95 181.592 ms/44 samples/0 missed slot và freshness max 385.027 ms; cả hai SLO status PASS. Phase gate hoàn tất ở mức exploratory single-run evidence, không statistical/production qualification; không gộp hai operation. Ledger #1710 đã tiêu thụ; không thêm run. P2-A/B correctness-only gate PASS với 17 focused test results qua các lượt riêng; incremental PK-delete marker recovery, staged replacement và injected partial-result handling đều PASS trên source hiện tại. Performance candidates không được chọn và native baseline được giữ lại: P1 provisional SLOs pass trong một lượt exploratory, nhưng chưa có stage attribution hoặc statistical/production qualification. SWE-QA20 vẫn deferred.
2. P2: chỉ khi muốn đổi default maintenance/delete policy hoặc storage contract; không hỏi lại việc debug/test thông thường.
3. P3: recovery condition/authorization cho operation mới sau stop, hoặc policy-compatible capacity route; không xin tăng cap như cách mặc định để pass.
4. P4: schema/tokenizer/reindex, model default và remote data-egress nếu được chọn.
5. P5/P6: supported surface/OS, Node API, index migration/rebuild, runtime cutover/rollback/TS retirement; production M4 là quyết định riêng.

## 12. Nguồn đối chiếu

- [Engineering entry point](README.md), [system map](system-map.md), [runtime](runtime-flows.md), [storage](storage-consistency.md), [retrieval](retrieval.md), [interfaces](interfaces-compatibility.md), [verification](development.md).
- [Rust rewrite completion criteria](rust-rewrite-roadmap.md), [Rust architecture](../../rust/ARCHITECTURE.md), [Rust audit và disposition](../../rust/docs/audit-20261025.md).
- [Current M4 checkpoint](../../research/m4-native/results/ACTIVE-EXECUTION.md), [recovery execution plan](../../research/m4-native/results/M4-RECOVERY-EXECUTION-PLAN-20261027.md), [query-latency evidence](../../research/m4-native/results/M4-GATE-B-QUERY-LATENCY-20261026.md), [benchmark policy](../../research/m4-native/docs/benchmark-policy.md).
- Code xác nhận hiện trạng: `rust/crates/zg-engine/src/storage/store/mod.rs`, `storage/fragments/mod.rs`, `pipelines/indexed_search/{pipeline.rs,storage.rs}`, `models/backends/model2vec/safetensors.rs`, `rust/crates/zg-daemon/src/job_scheduler.rs`, `rust/crates/zg-engine/Cargo.toml`, root `package.json`.
- [Compatibility fixtures](../../rust/compat/README.md), [MCP evidence](../../rust/compat/mcp/README.md), [retrieval benchmark](../../benchmarks/zg-retrieval/README.md), [frozen SWE-QA20 protocol](../../benchmarks/zg-retrieval/configs/protocol.json), [product roadmap](../08-roadmap.md).

**Kết luận:** hoàn thiện phần đã làm, đo đúng baseline, ưu tiên cải tiến native/retrieval có rollback; chỉ đưa M4 hoặc Rust cutover vào đường production khi gate tương ứng có evidence. Phase 3 lock-split (2026-10-30) bổ sung một candidate native lock-contention đã qua correctness/recovery/TSAN và qualified diagnostic, nhưng vẫn là research-only: chưa integrate, chưa qualification production, chưa authorize deploy. Không để nghiên cứu storage biến thành điều kiện tiên quyết cho mọi giá trị của sản phẩm.
