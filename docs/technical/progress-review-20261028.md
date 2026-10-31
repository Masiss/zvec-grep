# Progress review — 2026-10-28

**Scope:** review tiến độ và phần cần sửa/làm lại; không triển khai fix, launch diagnostic, benchmark, refresh attestation hoặc retry operation. Snapshot HEAD `d3b2a95bb238bce47e2dcb87034e5600d9de56e0` cùng working tree có nhiều thay đổi. Các tài liệu baseline P0/P1/P2 được kiểm tra thêm bên cạnh checkpoint M4, không chỉ dựa vào plan ban đầu.

## 1. Tiến độ thực tế

| Phase | Disposition hiện có | Nhận định review |
|---|---|---|
| P0 | DONE cho inventory/baseline/focused checklist ngày 27/10 | Giữ kết quả theo snapshot. Có WIP mới thì refresh manifest của lát sắp đo, không coi toàn working tree hiện tại đã được verify hoặc chạy lại toàn phase. |
| P1 | PASS cho exploratory native baseline v7 | Raw report `p1-native-lifecycle-20261027t114352z-181846d9`: query p95 181.592 ms, failures rỗng, freshness PASS. Đây là fixture 64 file, FTS, fixed embedding; không phải full-corpus/product qualification. |
| P2 | PASS bằng baseline-retained fallback, không có performance candidate được qualify | Có correctness/recovery/PK-delete coverage; coalescing diagnostic dùng mock executor nên không phải đo optimize. Current-tree profile đã xác định finalization là stage lớn. Cặp 2-vs-4 optimizer threads chỉ cải thiện mutation finalize 1.13%; candidate p95 320.494 ms vượt provisional SLO 300 ms. Giữ 2 threads là disposition hợp lý, chưa phải chứng minh thống kê 4 threads luôn chậm hơn. |
| P3 | PARTIAL / OPERATION_BLOCKED | Native-first pair `m4-rust-lifecycle-pair-20261028t025328z-p3` chạy một arm rồi safety stop trước M4: available 89,272,320 B < required 100,663,296 B. Native report có 46 dropped/missed starts, nên dù có arm hai cũng chưa đủ latency eligibility. |
| P3 quota follow-up | Diagnostic độc lập đã được owner duyệt, đang chuẩn bị, chưa launch | Checkpoint mới yêu cầu dedicated purpose/command/watchdog/quota binding cho contract 60 s, ≤1 sample/s tổng cộng, fixture ≤16 MiB. Không được giả dạng lifecycle operation hoặc mở fixture override vào qgroup. |
| P4–P7 | Chưa thấy evidence đóng phase trong các tài liệu đã review | Không lấy việc P2 giữ baseline hay correctness M4 PASS làm substitute cho retrieval quality, parity matrix, release/migration gates. |

Historical Rust log `m4-integration-tests-p3b.log`: **13 passed, 0 failed, 1 intentionally ignored**. Đã đọc log, chưa chạy lại suite này trong review. Gate A/Gate 2 giữ được scoped research correctness evidence, không cần bỏ đi vì benchmark bị block.

## 2. Findings cần sửa trước measurement tiếp theo

### R1 — Chưa khóa cùng một artifact xuyên suốt pair

**Ưu tiên: cao trước pair mới; evidence integrity, không phải đã chứng minh safety bypass hoặc historical result sai.**

- `research/m4-native/tools/run_rust_lifecycle_pair.py:64-86`: chọn binary mới nhất theo mtime; native library chọn theo danh sách path, chưa hash vào identity.
- `:160` preflight chọn binary; `:411` lại gọi `find_test_binary()` trong mỗi arm.
- `:125-126` tạo cả `binary_sha256` và `approved_binary_sha256` từ file đang thấy ở thời điểm tạo context.
- `m4_containment_runner.py:309-364` kiểm từng context với file hiện tại, nhưng không bảo đảm hash arm hai bằng hash đã freeze lúc preflight/arm một.

**Tái hiện không chạy workload:** dùng file binary synthetic trong `.tmp`, mock quota verifier, tạo context native rồi thay bytes ở cùng path và tạo context M4. Cả hai context đều được validator chấp nhận, trong khi SHA-256 khác nhau. Không binary nào được thực thi, không tạo root trong qgroup.

**Sửa tối thiểu:** freeze binary path/hash và native-library path/hash một lần trong plan; mọi arm dùng chính identity đó; verify lại trước launch và từ chối nếu drift, không auto-reselect. Add regression cho binary mới xuất hiện, binary tại cùng path bị đổi, library drift và context hash mismatch. Không cần đổi allowed root/resource limit hay copy binary để lách binding.

**Ảnh hưởng evidence cũ:** chưa chứng minh artifact từng drift trong pair đã chạy. Không tự hủy hoặc sửa report cũ; so retained hashes nếu cần kết luận về một operation cụ thể.

### R2 — Query comparison gate còn fail-open với report thiếu/không hợp lệ

**Ưu tiên: cao trước khi tin `comparable=true`; không thay workload/cadence/cap.**

`run_rust_lifecycle_pair.py:321-383` kiểm offer counters khá tốt, nhưng:

- So protocol bằng `native.get(key) == m4.get(key)` nên trường thiếu cả hai phía trở thành `None == None`.
- Không yêu cầu p95 tồn tại và là số hợp lệ.
- `reader_query_interval_ms=0` vẫn có thể được chấp nhận nếu lag bằng 0.

**Tái hiện bằng report synthetic in-memory:** cả ba trường hợp sau trả `comparable=true`, `reasons=[]`:

1. Xóa `reader_query_queue_capacity`, `reader_query_max_samples_per_pass`, `query_route`, `query_filter` ở cả hai arm.
2. Xóa `concurrent_query_p95_us` ở cả hai arm — output p95 là `null`.
3. Đặt interval và max lag cùng bằng 0.

**Sửa tối thiểu:** validate schema/type/range và required fields trước so sánh; interval/capacity/sample cap phải dương, percentile phải hữu hạn/không âm và hiện diện; đối chiếu protocol với frozen contract, không chỉ giữa hai report. Add missing/null/bool/negative/zero/mismatched-role regression. Giữ nguyên rejection đối với drop/cap omission/missed start/query failure.

**Ảnh hưởng evidence cũ:** các report đã kiểm vẫn bị reject đúng do dropped/missed slots. Không có cơ sở nâng chúng thành eligible hoặc gọi đây là M4 regression.

### R3 — Freshness observer có thể coi query lỗi là delete đã visible

**Ưu tiên: trung bình, sửa focused trước lần dùng lại native baseline harness.**

- `rust/crates/zg-daemon/src/workspace_runtime/tests/freshness.rs:934-978`: query `Err` được chuyển thành `Vec::new()`; metrics giữ failure nhưng observation gửi sang freshness waiter không giữ success/error status.
- `:1074-1093` chỉ kiểm completion timestamp và path predicate.
- `:1321-1324` delete predicate là `!paths.contains(&rename_target)`, vì vậy observation rỗng do query lỗi cũng thỏa điều kiện.
- Freshness SLO được tính riêng từ bốn elapsed values; query SLO có thể INCONCLUSIVE nhưng freshness vẫn ghi PASS.

**Sửa tối thiểu:** observation mang `Result` hoặc success flag; chỉ query thành công được chứng minh visibility. Query failure không thỏa absence predicate. Add deterministic regression: query lỗi sau delete không resolve waiter; successful empty result sau đó mới resolve. Ghi rõ query start/completion và watcher event/revision để dễ kiểm tra freshness semantics.

**Ảnh hưởng evidence cũ:** đã đọc v6/v7 native baseline và sáu native-profile reports; tất cả đều có `query.failures=[]`. Vì vậy chưa thấy finding này làm sai các số freshness đã nêu; không yêu cầu đo lại toàn bộ historical baseline chỉ vì sửa failure path.

## 3. Phần chưa phải lỗi nhưng cần điều chỉnh cách tiếp tục

### Không lấy quota diagnostic làm điều kiện đủ cho pair

Diagnostic chỉ kiểm chứng timing/accounting sau create/write/close/unlink. Delayed Btrfs accounting vẫn là giả thuyết; concurrent usage chưa bị loại trừ. Contract mới chỉ cần bổ sung đúng các binding của runner/controller/watchdog/observer hiện có, không mở resource framework mới. Once-per-second ceiling phải tính tất cả quota reads theo contract, không cộng thêm sampler độc lập.

Ngay cả khi diagnostic thành công, native đã dropped 46 offers ở 25 ms. Do đó pair mới không nên được chạy chỉ để hy vọng quota hết lỗi rồi có comparable p95. Cần một hypothesis source-backed về service time/lock wait và focused test trước; không giảm offered rate, tăng queue/cap hoặc đổi percentile eligibility để tạo PASS.

### Pooled p95 chưa đủ để quyết định kiến trúc

Phase mix và số query phụ thuộc duration từng pass. Qualification sau này cần per-pass/phase distributions và aggregation/uncertainty preregistered, không suy causal win từ một pooled percentile của pair fixed-order. Giữ nhãn exploratory hiện có.

### Không sửa marker/default maintenance chỉ vì P2 đã “PASS”

P2 pass bằng fallback giữ baseline, không có nghĩa cross-reopen debt/age đã được nâng cấp. Source vẫn khởi tạo debt/ack/age mới tại open. Những giới hạn này phải tiếp tục được ghi rõ trước khi bật bounded deferral làm default. Sửa đổi correctness trong production source khác với production deployment; wording báo cáo cần phân biệt hai việc đó.

### Refresh navigation, không viết lại lịch sử

`HANDOFF-20261028.md` dừng ở stale-attestation refusal, trong khi checkpoint đã có root-binding decision, native-first safety stop và diagnostic authorization mới. Giữ handoff cũ như snapshot, thêm chỉ dẫn superseded/current status nếu cập nhật tài liệu; không tiếp tục xin xử lý lại blocker đã được vượt qua. Các baseline P0/P1/P2 phải được link vào progress summary để không báo nhầm là chưa làm.

## 4. Trình tự đề xuất

1. Sửa R1/R2 bằng focused synthetic tests, R3 bằng deterministic observer test. Không cần benchmark để verify các fix này.
2. Hoàn thiện dedicated quota-diagnostic binding và fail-closed tests đúng contract đã duyệt; no-launch preflight; chỉ thực hiện diagnostic duy nhất nếu policy hợp lệ. Dừng trên refusal, không auto pair.
3. Phân tích lại quota result và native lock/service telemetry hiện có. Nếu chưa phân biệt được thì kết luận INCONCLUSIVE, không tự thêm campaign.
4. Chỉ đề xuất operation mới sau safety stop khi có hypothesis, frozen artifacts, query-measurement gates đúng và authorization phù hợp. Full-corpus/M4 production vẫn gated.
5. Đường dự án độc lập: dùng P1/P2 hiện có để sang P4 error analysis và P5 parity inventory khi được giao, thay vì để mọi tiến độ phụ thuộc qgroup.

## 5. Verification thực hiện trong lượt review

- Focused Python lifecycle-wrapper tests: **7/7 PASS** (`python3 -m unittest discover ... -p test_run_rust_lifecycle_pair.py -v`). Chỉ synthetic/mocked checks; TMPDIR ở `.tmp/progress-review-20261028`.
- R1 artifact-drift repro: hai context hợp lệ với hash khác nhau, quota mocked; không native executable hoặc qgroup operation.
- R2 malformed-report repro: ba trường hợp đều bị nhận nhầm comparable.
- Đọc raw receipt của latest safety stop và tám native baseline/profile reports; đối chiếu p95/failures/disposition.
- Không chạy Rust build/test, native/M4 arm, diagnostic, preflight mới, cleanup workspace cũ, sudo, hay thay implementation/policy. Chỉ thêm tài liệu review này.

**Kết luận: không cần làm lại P0–P2 hoặc correctness M4 từ đầu. Cần vá ba điểm evidence/measurement nêu trên, rồi đóng diagnostic quota có giới hạn; Phase 3 vẫn PARTIAL, không có performance hoặc production GO.**
