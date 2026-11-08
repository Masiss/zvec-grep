# Điều tra RAM khi index Rust: allocation lifecycle — 2026-11-07

## Kết luận và quyết định

**Đã tái hiện và xác định các tầng làm phình bộ nhớ. Corpus source đã sửa đạt dưới 2 GB
trong phép đo; workload có log lớn vẫn chưa đạt.** Không coi việc bỏ `.txt/.log/.md`
khỏi corpus là sửa lỗi RAM tổng quát: chúng là input được hỗ trợ.

Kết quả quan trọng nhất là **materialization theo toàn bộ file kết hợp với Native write và
allocator retention**, không phải một hàng đợi vô hạn chứa mọi file đã scan:

1. File lớn giữ đồng thời entity text, nội dung embedding, FTS, vector và các Native `Doc`.
2. Allocation đang sống ở đỉnh được ghi nhận chủ yếu trong đường ghi fragment Native,
   tạo Native document và các projection Rust. Chưa xác định được toàn bộ cấu trúc C++ bên trong.
3. Sau khi các buffer được thả, glibc vẫn giữ nhiều trang RAM: `free arena` không phải RAM
   chắc chắn trả ngay được, cũng không phải bằng chứng memory leak.
4. Có một lỗi capacity cụ thể ở FTS builder. Candidate sửa được lỗi này nhưng chỉ giảm
   peak RSS quan sát **2,461 → 2,408 GiB**; thời gian chạy tăng **121,03 → 147,58 giây**.
   **Chưa chấp nhận candidate như giải pháp RAM hoặc performance; chưa commit/cài đặt.**

**Hướng đề xuất phù hợp nhất:** giữ corpus source đã chọn đúng và qualify **opt-in
Linux/glibc reclaim tại phase boundary** trước; đây là bước nhỏ đã có bằng chứng reclaim
RSS thực. Không tái cấu trúc pipeline cho corpus source hiện đã đạt mục tiêu. Nếu yêu cầu
2 GB bao gồm cả log hàng chục MB, scope tiếp theo mới là bounded materialization/staging
của projection theo file. Không bóp concurrency/cache hoặc đổi fragment/vector.
Lộ trình và tradeoff ở [§6](#6-hướng-xử-lý-đề-xuất).

**Sự cố cần owner quyết định:** test chưa được cô lập đã ghi đè file authorization của
index repo. Grant giả đã được quarantine, nhưng không có bytes trước test để khôi phục
quyền cũ. Trạng thái hiện tại là không có grant, fail-closed. Xem [§8](#8-sự-cố-cô-lập-test-và-trạng-thái-khôi-phục).

## 1. Phạm vi, phiên bản và bằng chứng

- Source nền `92719bc`, nhánh `work/zg-recovered`; runtime từ release code `4807eaa` đến
  HEAD nền không thay đổi (diff trong `rust/` chỉ là tài liệu).
- zvec pin **0.8.0**, index format **2**, vector lưu **FP16**, model local
  `local/potion-code-16m-v2`, dimension 256; model cache/revision giữ nguyên.
- Các lượt frozen snapshot/candidate/Native probe không cài binary, restart daemon,
  đổi defaults, cache budget, concurrency, codec, thuật toán fragment/vector hoặc rebuild
  index HIS thật. Đợt HIS gốc đã rebuild theo yêu cầu owner, sau đó sửa selection incremental
  như §3.1; không lặp thao tác đó để thử candidate.
- Candidate source chỉ sửa `append_contents` và thêm hai regression trong
  [`pipeline.rs`](../crates/zg-engine/src/pipelines/indexing/pipeline.rs).
- Chốt điều tra bằng đọc artefact và kiểm chứng canonical read-only; không mở thêm lượt index.

Artefact riêng trên máy điều tra, không chứa trong Git:

```text
OUT=$HOME/.local/share/zvec-grep/index-allocation-diagnostics/20261105-lifecycle
HELPERS=$HOME/.pi/agent/reports
```

Không đưa raw log/source ứng dụng, credentials hoặc index binary vào tài liệu/Git.
Ngày trong tên artefact là ngày khởi tạo chiến dịch, không phải ngày hoàn tất báo cáo.
Báo cáo tĩnh trước đó: [2026-11-04](index-memory-investigation-20261104.md).

## 2. Luồng index và ownership hiện tại

Các symbol dưới đây là locator chính; line có thể dịch khi sửa file.

| Chặng | Vị trí | Bộ nhớ/lifetime đáng chú ý |
|---|---|---|
| Chuẩn bị workspace | `indexing/service.rs::WorkspaceIndexService::index/run_index` | Exclusive workspace lock; kiểm tra format/scan trước mutation; writer/model leases và staged build |
| Scan/diff/schedule | `pipeline.rs::index_candidates_inner` | Chuẩn bị file tuần tự; giới hạn task/batch, không giới hạn tổng byte; flush batch có thể push nhiều future trước kiểm tra ngưỡng |
| Đọc/extract | `prepare_candidate`, `extraction/text.rs` | Đọc/giải mã cả file; tạo toàn bộ entity; source bytes/text tồn tại trong prepare rồi được thả khi return |
| Projection Rust | `prepare_fragments`, `lexical_text` | Giữ FTS text, embedding content/fingerprint, entity/range/fragment identity |
| Embedding | `embed_file`, `embed_fragment_batch`, `embed_with_retry` | Tạo future cho mọi batch của file; clone content trước khi chờ permit; tích lũy kết quả và giữ ordinal trước commit |
| Commit | `commit_file` | Chuyển vectors/FTS thành `IndexedFragment`, tạo manifest; các representation của file vẫn cùng sống |
| Native document | `storage/store/mod.rs::replace_file` | Encode toàn bộ entity docs **và** fragment docs trước khi xóa dữ liệu cũ; arrays cùng sống với entities/entries |
| Native write | `storage/zvec.rs::write_docs`, `Fragments::write` | Upsert theo batch 1024; Native có thêm buffer/representation riêng; ghi xong batch không đồng nghĩa trả RSS |
| Finalize/close | `IndexStore::finalize_writes_detailed/close`, service `run_index` | Optimize sau drain; đóng/flush storage trước khi publish generation hoàn tất |

Source liên quan:
[service](../crates/zg-engine/src/pipelines/indexing/service.rs),
[pipeline](../crates/zg-engine/src/pipelines/indexing/pipeline.rs),
[text extraction](../crates/zg-engine/src/extraction/text.rs),
[store](../crates/zg-engine/src/storage/store/mod.rs),
[fragments](../crates/zg-engine/src/storage/fragments/mod.rs),
[native bridge](../crates/zg-engine/src/storage/zvec.rs).

### Invariants không được làm yếu khi tối ưu

- Encode/validate mọi projection trước destructive replacement: lỗi encode phải không xóa
  dữ liệu cũ. Trong write protocol, `NotIndexed` đi trước thay thế, `Indexed` là marker cuối.
  Đây **không phải** một transaction rollback đa collection; giữ nguyên recovery khi write lỗi.
- Không đổi fragment boundary/ordinal/ID, embedding input/model identity hoặc stored vector bytes.
- Mọi embedding future đã khởi chạy phải drain cả khi lỗi/cancel trước khi thả writer/model leases.
- zvec 0.8.0 không cho readers/writers trong optimize. Giữ fragment optimize ngoài state lock,
  scalar optimize trong lock, `optimize.pending` và write-generation debt.
- M2 là hint tùy chọn, không phải nguồn canonical hay điều kiện query thành công.
- Không biến giảm corpus thành minh chứng runtime đã xử lý được mọi input hợp lệ.

Các regression liên quan trong `pipeline.rs::tests`: `prepared_content_and_fts_stay_with_their_fragment_vectors_at_commit`,
`failed_replacement_does_not_publish_a_reconciliation_manifest`,
`fail_fast_drains_other_index_batches_before_returning`,
`fail_fast_drains_other_fragment_batches_before_returning`,
`snapshot_failure_drains_other_index_batches_before_returning`.

## 3. Workload và kết quả RSS thực

### 3.1 Không trộn ba phạm vi corpus

- **HIS ban đầu:** khoảng 8.057 file, peak RSS gần 3,99 GiB; include whitelist
  `NhiDong/**` ưu tiên hơn ignore, đưa env/vendor/runtime log và cả telemetry của lượt đo
  vào index. Run có 3 lỗi (một private key không đọc được và hai log tự sinh). Đã giữ
  receipt lỗi và sửa incremental về 0 lỗi; không dùng run mutable này làm controlled baseline.
- **Corpus source đã sửa:** exclusion có lý do rõ ràng, không đổi global ignore semantics.
  Actual HIS index cập nhật incremental: 5.328 unchanged, 2.715 entries deleted,
  0 added/modified/failed, không tạo embedding mới; không xóa source hay rebuild.
- **Mixed-format file lớn:** chủ ý giữ log hợp lệ để kiểm tra runtime ngoài corpus source.
  Đây là workload khác, không được so phần trăm RSS trực tiếp với hai corpus trên.

Corpus source snapshot 7.253 file/407.370.377 bytes, engine index 5.328 text files theo
selection hiện tại. Cùng root/bytes/model/concurrency/cache/CPU/cap, hai cold arm:

| Corpus source | A: observer no-op | B: trim tại hai phase |
|---|---:|---:|
| Peak process RSS | **1,762 GiB** | **1,482 GiB** |
| Wall mô tả | 193,49s | 195,37s |
| File lỗi | 0 | 0 |

Trong B, RSS/anonymous PSS giảm thực **492,80 MiB** trước optimize (22,08ms) và
**569,41 MiB** sau optimize/trước save (30,84ms). Allocated-arena byte delta 0,
file-backed PSS delta 0; A no-op không giảm tại hook. Đây là evidence within-probe
cho glibc reclaim, không phải suy từ `malloc_trim` return hoặc free arena. Không cộng
hai mức giảm ở hai thời điểm thành một mức tiết kiệm đồng thời. Peak B vẫn trước hook đầu.

Cả hai arm: 139.752 entities/159.085 embeddings; **5.328 M2 byte-identical**. Đây là
proof sidecar/fingerprint, chưa phải full canonical export cho corpus source. Một ordered
pair, không purge OS page cache: không quy toàn bộ chênh peak/timing giữa arm cho trim.
Cgroup peak A/B khoảng 2,27/2,45 GiB, khác process RSS và không đạt bound cgroup 2 GiB.

Bằng chứng trước: `$HOME/itc/HIS_EMR/NhiDong/docs/agent-runs/2026-11-05/zg-reclaim/`
(`REPORT.md`, `corpus-policy.json`, `comparison.json`, `A/`, `B/`). Không sửa lại receipt này.

### 3.2 Case file lớn đã đóng băng

Snapshot cố định gồm **5 file, 81.060.690 bytes**: hai log `.txt` khoảng 41,38/37,22 MB,
một `.log` khoảng 1,64 MB, một business Markdown khoảng 0,57 MB và C# khoảng 0,25 MB.
Giữ bytes, relative path và cùng snapshot root giữa các cold index; SHA-256 được kiểm
trước/sau. File `.log` có NUL trong input; engine hiện tại vẫn xử lý cả 5 file, không skip/fail.
Không suy từ extension rằng toàn bộ file luôn là văn bản sạch.

Mỗi run: model/device/cache giữ nguyên, embedding concurrency 2, affinity 2 CPU,
CPU quota 200%, cap 4 GiB, swap 0, deadline 600s, AF_UNIX-only/offline. Admission ít nhất
6 GiB host available và 5 GiB disk; không tăng cap. Host load/PSI/process state trước/sau
được lưu. Không có `memory.max`, OOM hoặc OOM kill ở cặp dưới đây.

| Cùng Rust 1.98.0 | Baseline | Candidate FTS |
|---|---:|---:|
| Peak RSS chính xác | 2.580.756 KiB = **2,461 GiB** | 2.524.608 KiB = **2,408 GiB** |
| Peak sampled malloc-accounted live | 1.947,73 MiB | 1.833,68 MiB |
| Wall | 121,03s | 147,58s |
| Cgroup CPU | 219,44s | 264,28s |
| File lỗi | 0 | 0 |
| Entity / fragment-vector | 123.596 / 124.098 | giống hệt |

Mức giảm RSS quan sát là **54,83 MiB**, chưa đạt 2 GiB. Bảng dùng GiB/MiB, không lẫn
GB thập phân. Cần chốt đơn vị, hard cap hay mục tiêu thường gặp trước gate rollout.
Một run mỗi điều kiện, thứ tự baseline → candidate, OS page cache không purge: không
kết luận một tỷ lệ slowdown cố định, nhưng **không được bỏ qua thời gian/CPU tăng**.

Một candidate đầu dùng Rust 1.98.1 trong khi baseline dùng 1.98.0. Đã phát hiện qua ELF
`.comment`, lưu riêng `comparison-UNMATCHED-COMPILER.json`, không dùng nó để quy RAM/tốc độ
cho patch. Candidate sau được build bằng toolchain 1.98.0 đã có trên máy; không đổi default.

**Correctness:** so sánh toàn bộ **247.694 canonical row hashes**, gồm entity content,
metadata, FTS/projection fields và raw FP16 vector bytes; tất cả bằng baseline. Ba M2
được xuất cũng bằng nhau. Hai file lớn vượt cap M2 **4 MiB/file** nên không có hint:
điều kiện ban đầu “phải có đủ 5 M2” là oracle sai, receipt lỗi được giữ nguyên và thay
bằng proof canonical, không index lại để che lỗi.

Artefact: `frozen-inputs.json`, `input-shapes.json`, `baseline/qualification-v2.json`,
`baseline/canonical-sha256.json`, `matched/{result,qualification,canonical-sha256}.json`,
`comparison.json`. Canonical reader đã qua negative control đổi một bit FP16 và kiểm
hash không phụ thuộc thứ tự insert field.

## 4. Allocation stack và Native lifecycle

### 4.1 Stack ở đỉnh đã quan sát

Full heaptrack bị deadline 600s; full reader bị deadline 300s. Không retry hai operation
đó, không tăng cap. Trace gốc truncated được giữ nguyên. Chỉ phục hồi complete-record
prefix thành artefact **PARTIAL**, rồi query cửa sổ 0–330s chứa đỉnh đã quan sát khoảng
302,7s. Đây không phải full-run profile hay bằng chứng memory leak.

Tổng live allocation cost tại đỉnh cửa sổ: **1.917,84 MiB**. Phân nhóm độc quyền theo
allocation-origin stack; không cộng các peak riêng ở những thời điểm khác nhau:

| Origin ở đỉnh cửa sổ | MiB |
|---|---:|
| Native upsert; các stack lớn đi qua `Fragments::write → write_docs → C API` | 678,91 |
| Tạo Native document từ canonical entities/search projections | 255,48 |
| FTS text Rust | 198,13 |
| Plain-text entity strings | 99,06 |
| Nội dung/future embedding | 52,68 |
| Các phần khác, model/vectors và unresolved worker stacks | phần còn lại |

FTS cost đúng gấp đôi plain-text strings trong cửa sổ này. Các clone chờ embedding có
thật nhưng không phải phần lớn nhất. Native `.so` đã stripped, BuildID
`f10631c8d5d960cd9fb3978040def8abb908e240`: không gán tên cache/Arrow/RocksDB cụ thể cho
các frame chưa resolve. Chi phí heap ở run instrumented không phải phần trăm của peak RSS baseline.

Artefact: `profile/{prefix-recovery,peak-window,window-peak-groups,selected-peak-stacks}.json`,
`profile/window-peak-stacks.folded`, các report/timeouts gốc.

### 4.2 Probe Native nhẹ — kết thúc đủ 5 file

Để không lặp full malloc tracing, probe chỉ quan sát API tạo/thả Doc và before/after
upsert/optimize, không đổi argument/result, không trim. Fixed pointer table mmap 16 MiB
là overhead đo lường riêng. Fixture kiểm create/destroy, text/vector fingerprint,
upsert/optimize/reopen trước khi chạy workload thật.

- 550 records, đủ **274 cặp** API before/after, tất cả outer status 0.
- Peak **124.383 Native Doc đang sống do Rust tạo**; `Doc::memory_usage` aggregate ước lượng
  **202,22 MiB**. Đây không phải malloc bytes thực, không bao gồm Native copies nội bộ.
- Tạo/thả **247.718 / 247.718**, cuối run không còn Doc được probe theo dõi;
  không có add/destroy không được theo dõi trong run này. Không suy ra mọi allocation đều leak-free.
- Ghi fragment: 123 upsert calls, tổng call wall khoảng 84,51s; ghi entities 123 calls,
  khoảng 1,69s. Không cộng chúng với thời gian futures như các phase CPU độc lập.
- Cặp before/after fragment upsert có malloc-accounted increase lớn nhất **108,52 MiB**.
  Đây là delta toàn tiến trình quanh lời gọi, có thể gồm hoạt động Native worker đồng thời.
- Trước/sau fragment optimize: malloc-accounted live **817,18 → 378,21 MiB**, lúc đó
  Doc estimate bằng 0. Có bộ nhớ khác ngoài các Doc Rust-owned tiếp tục sống sau commit.
- Tại probe exit: Doc count 0 nhưng glibc còn khoảng **2,10 GiB free arena**.
  Không dùng free-arena như số RSS có thể reclaim ngay hoặc như bằng chứng leak.

Probe hoàn tất 149,68s, 5 file/0 lỗi; **toàn bộ 247.694 canonical row hashes cũng bằng baseline**.
Không dùng RSS/timing instrumented làm số acceptance cho candidate.
Artefact: `boundary/{native-boundaries,native-boundary-summary,qualification,canonical-sha256}.json*`.

### 4.3 Cách diễn giải đúng

Giả thuyết “có dữ liệu còn sống trong khi chờ/đang ghi” đúng ở mức **per-file materialization**.
Không thấy một container giữ tất cả file đã xử lý vô hạn. Native đang ghi từng batch,
nhưng các representation của cả file và file đang chuẩn bị khác vẫn có thể cùng sống.
Sau khi chúng được free, allocator high-water tiếp tục giữ RSS. Cần xử lý cả hai chặng,
không chỉ trim cuối cùng và cũng không chỉ lọc corpus.

## 5. Candidate hiện tại và kiểm chứng

`append_contents`: reserve cả `text.len() + 1` trước `push_str`, tránh append newline vào
buffer vừa exact-fit rồi tăng capacity gấp đôi. Không thay bytes FTS hoặc embedding input.

- Regression RED trên code cũ: **len 1.025, capacity 2.048**.
- Sau sửa: regression ASCII/Unicode và bảo toàn prefix, NUL, newline/separator qua.
- `cargo fmt --all -- --check`: PASS.
- `cargo test --offline --locked -p zg-engine --lib pipelines::indexing::pipeline::tests -- --test-threads=2`:
  **34 passed**.
- `cargo clippy --offline --locked -p zg-engine --all-targets -- -D warnings`: PASS.
- Full engine lib: **727 passed, 15 ignored**, với đúng test binary nhưng chạy trong
  sandbox đã qualify; không đổi assertion. Không phải full workspace test.
- Private release build offline/locked + version smoke: PASS, cả build compiler-matched 1.98.0.
- Canonical equivalence đầy đủ như §3. Candidate vẫn **chưa chấp nhận RAM/speed, chưa cài/commit/push**.

## 6. Hướng xử lý đề xuất

### Ưu tiên 1 — corpus đúng và opt-in phase-boundary reclaim

Đối với source corpus hiện tại, A không trim đã dưới 2 GB; không có lý do bắt đầu bằng
spool hay giảm concurrency/cache. Giữ policy đã sửa. Nếu cần thu hồi retention khi index,
qualify một thay đổi nhỏ Linux/glibc opt-in ở ranh giới sau drain/trước optimize và sau
optimize/trước save. Không trim mỗi file/batch; có guard nền tảng, telemetry, giới hạn tần suất
và đo latency. Không coi B probe là implementation production đã hoàn tất.

Tradeoff: có thể thêm allocator/syscall cost và page fault/cấp phát lại khi index tiếp;
chỉ có số đo within-hook hàng chục ms ở workload này, chưa chứng minh overhead cho mọi corpus.
Không gọi tùy tiện trong Native write hoặc khi giữ critical lock; không bật default.
Trước acceptance tích hợp cần full canonical proof cho A/B, không chỉ 5.328 M2.

### Ưu tiên 2, có điều kiện — bounded materialization cho file lớn

Chỉ mở scope này nếu owner cần budget 2 GB **bao gồm cả large log/text** hoặc workload
source mới vẫn vượt vì live allocation. Phase trim cuối index không bảo đảm được transient
peak trước commit; candidate FTS cũng chưa đủ. Đây là **đề xuất, chưa triển khai/qualify**.
Thay đổi tập trung ở seam
`commit_file → IndexStorage → store::replace_file`, không đổi fragmenter/model/index format.
Ưu tiên làm giảm lượng representation cùng sống, thay vì hạ concurrency/cache để né workload.

Thiết kế cần thử trong một scope tiếp theo được owner duyệt:

1. Với file lớn, encode/validate đầy đủ canonical và search projections vào **staging/spool
   riêng của operation** trước destructive replacement. Không giữ đồng thời mọi Native `Doc`.
2. Validate round-trip, checksum và khả năng đọc staging trước khi bắt đầu xóa dữ liệu cũ.
   Chọn cách biểu diễn tạm sau fixture, không tự coi Native serialization là format ổn định.
3. Replay theo batch với working set hữu hạn, giữ nguyên vector bytes/ranges/order và
   protocol `NotIndexed → writes → Indexed`. M2 vẫn tùy chọn. Đây không tạo transaction rollback mới.
4. Cleanup staging khi thành công/lỗi/cancel; xử lý ENOSPC, hỏng/truncated spool, crash/restart
   và writer ownership. Trước mutation phải bảo toàn dữ liệu cũ khi encode/validate thất bại;
   sau mutation giữ nguyên trạng thái lỗi/recovery, không báo Indexed giả.
5. Sau khi buffer per-file thật sự được drop, thử reclaim có ngưỡng/cooldown trên Linux/glibc
   trong chế độ opt-in. Không trim mỗi batch, không gọi trong Native write/critical lock,
   không bật production default từ kết quả probe.

Không ấn định threshold/số batch mới ở tài liệu này. Phải chọn theo byte evidence và
mức I/O/latency owner chấp nhận. Giữ fast path cho file nhỏ nếu được qualification chứng minh.
Có thể giảm thêm eager FTS projection trong cùng hướng lifecycle, nhưng không thay FTS bytes.

**Tradeoff:** thêm I/O/disk tạm, serialization và recovery complexity; có thể chậm hơn.
Bù lại không cắt phạm vi tìm kiếm, không giảm chất lượng/vector, không buộc giảm concurrency/cache.
Staging không tự khống chế toàn bộ Native internal working set; nếu phần Native còn sống
vẫn vượt budget sau đó, phải điều tra lifecycle Native 0.8 với symbol/probe phù hợp trước khi đổi budget.
Không cam kết thiết kế này chắc chắn đạt 2 GB trước phép đo.

### Các lựa chọn không dùng để che giới hạn

| Cách | Vì sao chưa chọn |
|---|---|
| Lọc bỏ mọi log/text/vendor để đạt số RAM đẹp | Thay bài toán; text/log hợp lệ cần được kiểm chứng riêng |
| Chỉ sửa capacity FTS | Bug có thật nhưng RSS giảm ít và speed chưa được chấp nhận |
| Dùng trim phase làm lời hứa 2 GB cho mọi file lớn | Có bằng chứng reclaim trên source corpus, không bảo đảm đỉnh live trước commit của large log |
| Giảm concurrency/cache hoặc đổi fragment/vector | Ngoài ràng buộc owner hiện tại, có tradeoff khác |
| Optimize thường xuyên/ngoài lock để giải phóng RAM | Không tự đổi maintenance policy; nguy cơ vi phạm 0.8 exclusivity/recovery |
| Viết lại toàn pipeline hoặc chữa Native cache theo phỏng đoán | Bằng chứng chưa đủ, phạm vi/rủi ro không tương xứng |

### Acceptance cho scope triển khai tiếp theo

- Cùng frozen mixed-format workload, đủ file, không fail/skip bất ngờ; thêm corpus lớn chỉ
  sau qualification riêng. Không loại input khó để lấy PASS.
- Cùng compiler/flags, Native/model SHA/revision, device, concurrency/cache, affinity và cap;
  lưu host CPU/time/RSS/PSS/swap/PSI trước/sau. Một run không phải performance qualification phổ quát.
- So sánh **mọi canonical entity/projection/raw FP16 row**, không chỉ count, query mẫu hoặc M2.
- Peak RSS đạt budget đã chốt trên workload đã chốt; báo anon/file-backed riêng, không dùng
  cgroup total thay process RSS. Không hứa bound cho file/corpus kích thước tùy ý.
- Chốt trước overhead thời gian/CPU/I/O/disk chấp nhận được; không giấu slowdown như noise.
- Kiểm cancellation, retries/drain, encode failure trước delete, Native write failure,
  ENOSPC/corrupt spool/crash recovery và stale writer generation. Không giảm assertion.
- Không đo lại các operation bị resource gate từ chối hoặc tự nâng cap; cần admission mới đúng scope.

## 7. Giới hạn và phần chưa xong

- Chưa xác định chính xác mọi owner C++ trong ~679 MiB Native-upsert origin và worker frames.
- Chưa đo reclaim ngay tại large-file commit trên workload 5 file này. Kết quả trim ở
  corpus sạch trước đây không chứng minh nó giải quyết đỉnh live hiện tại.
- Source corpus đã đạt ~2 GB trong lần đo; bound cho large log/text, overhead tích hợp trim
  và một thiết kế staging an toàn chưa được qualify.
- Không có production change ngoài candidate source chưa commit; installed binary/daemon không đổi.
- Không diễn giải allocation sống lúc profile bị terminate thành leak.

## 8. Sự cố cô lập test và trạng thái khôi phục

Lượt `cargo test -p zg-engine --lib` đầu dùng Cargo-forced `TMPDIR` dưới repo. Một số
fixture tìm workspace cha: 8 path-search tests từ chối manifest Node legacy; 2 authorization
child fixtures dùng username test ghi vào authorization của repo thật. Kết quả đầu:
**717 passed, 10 failed, 15 ignored**, log được giữ nguyên.

Ảnh hưởng: `.zvec-grep/authorization.json` của **repo zvec-grep**, dùng chung bởi Node/Rust.
Không phải index HIS. File chỉ còn một grant giả `authorization-test`/`provider.test`;
đã quarantine đúng record đó dưới authorization lock. **Bytes trước test không được chụp;
bản source-recovery không chứa ignored index; không tìm thấy backup authorization.**
Không loại trừ quyền owner trước đó đã bị mất. Không tạo grant mới hoặc giả vờ khôi phục.
Hiện authorization **absent/fail-closed**; nếu repo từng dùng remote consent, owner cần
backup gốc hoặc tự xác nhận/cấp lại đúng quyền. Đây là `OWNER_DECISION_REQUIRED` còn mở.

Điều kiện phục hồi kiểm chứng đã qualify: bwrap mount host/repo read-only, chỉ `$OUT`
writable; `TMPDIR` và CWD ở ngoài indexed parents; thử ghi real index bị từ chối. Cùng
binary/assertions chạy lại **727 passed, 15 ignored**. Không xóa/đổi owner index để lấy PASS.
Không sửa production authorization code trong công việc này.

Bài học: hướng dẫn TMPDIR tuyệt đối là chưa đủ nếu nó vẫn nằm dưới indexed workspace.
Không lặp full-suite uncontained. Lệnh sandbox cụ thể có trong handoff.
Artefact: `containment/{incident,sandbox-qualified}.json`, grant giả đã quarantine và hai log suite.

## 9. Tiếp nhận công việc

[Handoff có thể tiếp tục an toàn](index-allocation-lifecycle-handoff-20261107.md) ghi current
state, artefact, command kiểm tra và giới hạn quyền. Không coi tài liệu này là authorization
cho một campaign benchmark hoặc thay đổi production mới.
