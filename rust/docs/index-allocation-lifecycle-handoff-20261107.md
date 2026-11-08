# Handoff — Rust index RAM — 2026-11-07

## 1. Trạng thái và phạm vi

- **DONE:** chốt bằng chứng điều tra, báo cáo và đề xuất; không mở thêm lượt index.
- **PARTIAL:** chưa có giải pháp được chấp nhận cho RSS ~2 GB với large log/text.
- **OWNER_DECISION_REQUIRED:** quyền remote của index repo trước sự cố test chưa khôi phục
  được; xem cảnh báo bên dưới. Không chặn việc đọc code/tài liệu hoặc dùng model local.

Đọc trước: [báo cáo và đề xuất](index-allocation-lifecycle-investigation-20261107.md).
Current request là viết báo cáo/handoff, **không phải** authorization cho benchmark campaign,
cài binary, triển khai staging hay đổi defaults mới. Owner đã phàn nàn chẩn đoán quá lâu;
đã cam kết dừng các lượt index mới. Các job chẩn đoán/canonical cuối đã hoàn tất.

## 2. Working tree: giữ nguyên candidate, không nhầm với bản đã phát hành

```text
Repo:   /home/masis/src/zvec-grep
Branch: work/zg-recovered
HEAD:   92719bc (docs recovery; runtime nền release 4807eaa)
Dirty:  rust/crates/zg-engine/src/pipelines/indexing/pipeline.rs
        + báo cáo/handoff này và navigation note trong báo cáo cũ
```

`pipeline.rs` có **36 insertions/1 deletion**: reserve separator trong `append_contents`,
hai regression capacity/byte-preservation. Đây là candidate **chưa commit/chưa cài/chưa
chấp nhận RAM hoặc speed**. Không revert/discard nhầm. Không commit/push/deploy tự động.
Native pin 0.8.0, format 2, FP16, defaults/concurrency/cache/model semantics không thay đổi.
Installed binary, owner daemon và source ứng dụng không thay đổi.

## 3. Những gì đã biết — không cần chạy lại để nhớ

| Workload | Kết quả | Diễn giải |
|---|---|---|
| HIS mutable ban đầu, ~8.057 file | RSS gần 3,99 GiB; 3 lỗi, đã sửa incremental | Corpus lẫn env/vendor/log/telemetry; không là controlled baseline |
| Source đã sửa, 5.328 text files | A no-op **1,762 GiB**; B phase trim **1,482 GiB** | Đạt process RSS mục tiêu trong lần đo này, không phải bound cgroup |
| 5 file/81.060.690 bytes gồm log 40 MB | Baseline **2,461 GiB**, FTS candidate **2,408 GiB** | Vẫn vượt 2 GiB; wall **121,03 → 147,58s**, CPU **219,44 → 264,28s** |

Source A/B có reclaim within-hook thật **492,80/569,41 MiB**, allocated-arena delta 0,
file-backed delta 0. Không cộng hai mức giảm; chỉ một ordered pair. M2 cả 5.328 file bằng
nhau, **chưa full canonical proof cho corpus source**.

Large case cùng Rust **1.98.0**: 123.596 entity + 124.098 fragment-vector;
**247.694 canonical row hashes/raw FP16 đều bằng baseline**, ba M2 bằng; hai file lớn
vượt 4 MiB cap M2 nên không có hint. Candidate 1.98.1 trước đó là confounded comparison,
giữ receipt nhưng không dùng quy kết. Không dùng một cặp để khẳng định % slowdown cố định.

Partial heaptrace window: Native upsert ~679 MiB, Native Doc construction ~255 MiB,
Rust FTS ~198 MiB vs entity strings ~99 MiB, embedding futures/clones ~53 MiB.
Native bị stripped; không tự đặt tên internal cache. Full heaptrack 600s và full reader
300s đã timeout, **không retry/nâng cap**. Recovered window không phải full profile.

Probe Native nhẹ cuối: 5/5 file, 0 lỗi, 149,68s; 274 cặp API đúng;
peak 124.383 Rust-owned Native Docs, estimate 202,22 MiB; create=destroy=247.718, exit 0,
untracked add/destroy 0. Quanh fragment optimize, malloc-accounted live
817,18 → 378,21 MiB; exit còn ~2,10 GiB free arena. Estimate không là exact malloc/RSS.
**Probe canonical proof cũng đủ 247.694 rows bằng baseline.** Instrumented timing không là
performance acceptance.

## 4. Đề xuất tiếp theo — cần scope mới trước khi thực thi

1. **Source workload:** giữ policy corpus đã sửa; ưu tiên qualify **opt-in Linux/glibc
   phase-boundary reclaim**, vì đã có RSS reclaim thực. Không cần viết lại pipeline để
   đạt số source corpus hiện tại. Chưa bật production default hoặc bảo đảm overhead.
2. **Nếu owner yêu cầu 2 GB cả log/text lớn:** thiết kế bounded per-file projection
   materialization/staging ở `commit_file → IndexStorage → store::replace_file`.
   Encode/validate trước delete; replay bounded batches; giữ fragment/vector/FTS bytes,
   NotIndexed/Indexed và error/cancel/recovery invariants. Không chỉ chia `Vec<Doc>` sau
   khi đã materialize cả file. Chưa triển khai spool hay chọn threshold.
3. Với bất kỳ implementation nào: qualification failure-path trước, full canonical proof,
   rồi mới xin/chuẩn bị phép đo giới hạn trên cùng snapshot/toolchain/native/model.
   Chốt budget/đơn vị RSS và overhead owner chấp nhận trước rollout. Không tự đổi cache,
   concurrency, Native pin, format hoặc deferred optimize policy.

Exact next safe action nếu tiếp nhận sau handoff: đọc báo cáo, source diff và artefact
bên dưới, rồi chốt với owner phạm vi **source-only reclaim** hay **large-input lifecycle**.
Không tự suy handoff là lệnh chạy một lượt index mới.

## 5. Kiểm chứng đã có

- Fmt PASS; pipeline tests **34 passed**; engine all-target clippy `-D warnings` PASS.
- Full engine lib trong sandbox đã qualify: **727 passed, 15 ignored**, assertions không đổi.
  Không phải full workspace test của candidate. Log đầu lỗi vẫn giữ.
- Private release build offline/locked + smoke PASS; compiler-matched 1.98.0 có manifest/SHA.
- Cặp baseline/candidate và Native probe đều có full canonical equivalence.

## 6. Cảnh báo quan trọng: test isolation / authorization còn mở

Test ban đầu dùng Cargo-forced TMPDIR dưới indexed repo; fixture đi ngược lên workspace
cha và rewrite **repo** `.zvec-grep/authorization.json` bằng grant giả. Không phải HIS index.
Kết quả ban đầu **717 passed/10 failed/15 ignored** được giữ nguyên.

**Không có bytes trước test/backup authorization**, không thể loại trừ owner grants bị mất.
Đã quarantine duy nhất grant giả dưới authorization lock; hiện authorization **absent,
fail-closed**. Không tự tạo grant, giả consent, chạy `grant`, hoặc nói đã khôi phục quyền cũ.
Nếu remote consent từng dùng, owner cần backup gốc hoặc tự cấp lại chính xác quyền cần thiết.
Canonical index data/vectors không bị sửa bởi sự cố. Chi tiết: `containment/incident.json`.

`rust/AGENTS.md` hiện còn ví dụ TMPDIR dưới repo: **không dùng ví dụ đó để chạy full suite**.
Đường tuyệt đối chưa đủ để cô lập workspace discovery. Recovery đã qualify dùng bwrap:
host/repo read-only, chỉ private `$OUT` writable, temp/CWD ngoài indexed parents.

Lệnh lịch sử đã PASS dưới đây là **reference**, không yêu cầu chạy lại. Test binary/cache
có thể bị dọn sau này; nếu rebuild thì cần qualify lại containment trước suite.

```bash
OUT=$HOME/.local/share/zvec-grep/index-allocation-diagnostics/20261105-lifecycle
bwrap --die-with-parent --new-session \
  --ro-bind / / --dev-bind /dev /dev --proc /proc \
  --bind "$OUT" "$OUT" --chdir "$OUT/test-working" \
  --setenv TMPDIR "$OUT/test-tmp" \
  --setenv ZVEC_GREP_HOME "$OUT/test-home" \
  --setenv LD_LIBRARY_PATH "$HOME/.local/share/zvec-grep/rollback-20261101T130300Z" \
  "$HOME/.cache/zvec-grep/rust-target-recovered-20261105/debug/deps/zg_engine-2c851e226cfdd1b9" \
  --test-threads=2
```

Không dùng `cargo test` uncontained, xóa/đổi owner index để test qua, hoặc làm grant mẫu
trên index thật. Sự cố cần owner quyết định không đồng nghĩa mọi local work bị safety-stop.

## 7. Artefact và source locator

```text
OUT=$HOME/.local/share/zvec-grep/index-allocation-diagnostics/20261105-lifecycle
CORPUS=$HOME/itc/HIS_EMR/NhiDong/docs/agent-runs/2026-11-05/zg-reclaim
HELPERS=$HOME/.pi/agent/reports
```

- `$CORPUS/REPORT.md`, `corpus-policy.json`, `A/`, `B/`, `comparison.json`: source selection + trim proof.
- `$OUT/comparison.json`, `matched/qualification.json`, `matched/canonical-sha256.json`:
  current compiler-matched comparison/correctness; `comparison-UNMATCHED-COMPILER.json` là lịch sử.
- `$OUT/baseline/qualification-v2.json`, `baseline/canonical-sha256.json`: baseline canonical;
  không nhầm `memory-comparison-PENDING-CANONICAL.json` là verdict cuối.
- `$OUT/profile/{prefix-recovery,peak-window,window-peak-groups,selected-peak-stacks}.json`:
  recovered window/method limits; raw timeout receipts không xóa.
- `$OUT/boundary/{qualification,native-boundary-summary,canonical-sha256}.json`,
  `native-boundaries.jsonl`, `native-boundary-qualified.json`: Native probe qualification.
- `$OUT/containment/{incident,sandbox-qualified}.json`, `engine-lib-tests*.log`:
  failure + contained verification; không công khai grant backup hay raw source/input.
- `$OUT/fixed-1.98.0-bin/{manifest.json,source.diff}`, build/clippy/pipeline logs:
  candidate provenance. Full source diff vẫn là truth tại repo, không dựa riêng vào memory notes.
- Helpers: `zg_allocation_lifecycle_20261105.py`, `zg_canonical_fingerprint_20261105.py`,
  `zg_heaptrack_peak_window_20261105.py`; chúng có modes chạy experiment, **không gọi lại
  run modes chỉ để xem trạng thái**.

Source chính: `rust/crates/zg-engine/src/pipelines/indexing/{service,pipeline,input_budget}.rs`,
`storage/{store/mod,zvec,manifest}.rs`, `storage/{entities,fragments}/`.
Invariant/regression và thiết kế đề xuất có locator trong báo cáo, không cần rediscover toàn repo.

Các lệnh kiểm tra an toàn, chỉ đọc:

```bash
git status --short
git diff -- rust/crates/zg-engine/src/pipelines/indexing/pipeline.rs
git diff --check
python -m json.tool "$OUT/comparison.json"
python -m json.tool "$OUT/boundary/qualification.json"
```

Không commit/push/install, dọn backup/partial trace, restore authorization hay chạy benchmark
mới như một phần tự động của việc nhận handoff.
