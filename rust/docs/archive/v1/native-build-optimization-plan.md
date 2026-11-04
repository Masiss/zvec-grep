# Native build optimization plan (`zvec_c_api`)

Updated: 2026-10-16. Status: **tạm dừng chờ implement** (T1–T2 đã sửa trong
checkout native; T3–T5 chưa làm; chưa build lại để đo). Không có build nào đang chạy.

Mục tiêu: giảm thời gian build target `zvec_c_api` từ checkout native trong
`.local/native/zvec-native-profile`, không đổi hành vi runtime, không đổi
on-disk format, không đổi ABI.

## 1. Bằng chứng đo được (baseline)

Lần build gần nhất:
`cmake --build .local/native/zvec-native-build --target zvec_c_api -j20`.

- Tổng wall time: **23 phút 12 giây** (00:00:53 → 00:24:05, 2026-10-16).
- Sản phẩm: `lib/libzvec_c_api.so` (42 MB) lúc 00:24.

Timeline object, build ngoài (trừ Arrow lồng nhau):

| Thành phần | Số TU | Thời gian | Ghi chú |
|---|---|---|---|
| antlr4 | 148 | 00:00:57 → 00:04:07 | |
| snowball | 56 | 00:00:53 → 00:01:35 | |
| rocksdb | 330 | 00:01:14 → 00:08:59 | |
| src/ailego | 101 | 00:01:00 → 00:03:15 | |
| src/turbo | 101 | 00:03:17 → 00:06:36 | |
| src/core | 134 | 00:06:53 → 00:11:30 | |
| **arrow (lồng)** | **488** | **00:01:51 → 00:21:19** | **~19.5 phút, critical path** |
| src/db | 116 | 00:21:20 → 00:23:48 | chỉ chạy được sau khi Arrow xong |
| link `libzvec_c_api.so` | – | 00:24:05 | |

Phát hiện quyết định: **có khoảng trống 9.8 phút** (00:11:30 → 00:21:20) mà
build ngoài `-j20` không sinh thêm object nào ⇒ ~18/20 core idle, chỉ chờ Arrow
build với 2 job song song.

Nguyên nhân: `.local/native/zvec-native-profile/thirdparty/arrow/CMakeLists.txt`
(nhánh `else()` cho Linux/macOS chung) hardcode:

```cmake
BUILD_COMMAND "${CMAKE_COMMAND}" --build . --target all -- -j 2
```

Đây là sửa đổi **local, không phải upstream** (upstream dùng `-j ${NPROC}`),
được thêm với comment "Research build: keep nested Arrow compilation bounded on
this host".

Yếu tố phụ:

- Không có ccache: `CMakeCache.txt` → `CCACHE_FOUND:FILEPATH=CCACHE_FOUND-NOTFOUND`.
  (Dự án đã thiết kế sẵn cho ccache/sccache: root `CMakeLists.txt` có comment
  `/Z7 ... to enable sccache/ccache`, và Arrow CMake đã propagate
  `CMAKE_*_COMPILER_LAUNCHER` vào ExternalProject.)
- Generator: GNU Make (`CMAKE_GENERATOR=Unix Makefiles`), không phải Ninja.
- Máy: 20 core, 15 GiB RAM, swap đã dùng nhiều (đây là lý do `-j 2` từng được
  thêm để chặn OOM).
- Đã tối ưu sẵn: tests tắt, examples OFF, patch `arrow.slim_compute` đã áp dụng,
  LTO OFF.

## 2. Quyết định đã chốt với người dùng

1. Làm biến cấu hình cho số job Arrow (thay hardcode).
2. Chuyển sang Ninja.
3. Viết script build Linux (hiện chỉ có `build-zvec-macos.sh`).
4. Giải đáp: có cách sửa mà **không phải sửa inline** về sau hay không.

## 3. Cách tiếp cận

### 3.1 Arrow jobs → biến CMake (không còn hardcode)

Thay literal `-j 2` bằng cache variable `ZVEC_ARROW_JOBS`, default = tự tính
(`NPROC/2`, kẹp `[2, 8]`). Sau thay đổi này, muốn chỉnh chỉ cần truyền
`-DZVEC_ARROW_JOBS=<n>` khi configure — **không cần sửa file lần nữa**. Đây là
câu trả lời cho yêu cầu "không sửa inline".

Giới hạn: bản thân việc *định nghĩa* biến vẫn phải sửa file checkout native một
lần. Không có cách nào để giá trị từ ngoài override một literal `-j 2` nằm cứng
trong `BUILD_COMMAND` mà không sửa dòng đó. Các phương án "không sửa file" khác
(không khuyến nghị):

- Bọc `cmake` bằng shim để chèn job count — giòn, khó debug.
- Patch file lúc build (script `sed`) — vẫn phải giữ patch khớp với upstream.

### 3.2 Ninja

Ninja phải dùng **build dir mới** (không thể đổi generator trên cùng một binary
dir). Lợi ích: scheduling tốt hơn cho 1454 TU + nhiều target, ít overhead hơn
Make. Không đổi kết quả build.

### 3.3 Script build Linux

Script chuẩn hoá: chọn generator Ninja, tính job theo RAM, đặt
`ZVEC_ARROW_JOBS`, tự bật ccache nếu có trong `PATH` (không tự cài), build target
`zvec_c_api`, và in vị trí library.

## 4. Task list

- [x] **T1** — Thêm biến `ZVEC_ARROW_JOBS` vào `thirdparty/arrow/CMakeLists.txt`
      (đã sửa; default auto `NPROC/2` kẹp `[2,8]`).
- [x] **T2** — Đổi `-j 2` → `-j ${ZVEC_ARROW_JOBS}` trong nhánh `else()`
      của `thirdparty/arrow/CMakeLists.txt` (đã sửa). Nhánh Android/iOS/MSVC
      vẫn giữ `-j ${NPROC}` (không đụng tới).
- [ ] **T3** — Tạo `rust/scripts/build-zvec-linux.sh` (Ninja + job theo RAM +
      `ZVEC_ARROW_JOBS` + ccache tuỳ chọn), `chmod +x`.
- [ ] **T4** — Configure build dir Ninja mới, chạy build đầy đủ, đo lại wall time
      và so sánh với baseline 23m12s.
- [ ] **T5** — Ghi kết quả "after" + cập nhật plan/handoff.

## 5. Tiêu chí nghiệm thu

- Build lại thành công target `zvec_c_api`.
- Wall time giảm rõ rệt so với 23m12s (kỳ vọng ~9–10 phút, Arrow ~5 phút thay vì
  19.5 phút), **không** tái diễn khoảng trống idle dài.
- Không OOM, không swap-thrash kéo dài.
- `libzvec_c_api.so` vẫn được tạo đúng.

## 6. Rủi ro / lưu ý

- **RAM**: đẩy Arrow jobs lên cao có thể OOM trên máy 15 GiB khi chạy song song
  với build ngoài. Cần giới hạn tổng job (ngoài + Arrow ≤ ~nproc) và theo dõi
  swap.
- **Đĩa**: `/` là btrfs, chỉ còn ~19 GB (96%). Build dir mới thêm ~1.5 GB.
- **Kết quả đo**: lần build baseline là full rebuild do relocate path, không phải
  build tăng dần điển hình. Cần ghi rõ điều này khi so sánh.
- Thay đổi nằm trong checkout **chưa track** `.local/native/...`; không ảnh hưởng
  repo zvec-grep ngoài script mới.

## 7. Baseline vs kỳ vọng

| | Baseline | Sau tối ưu (kỳ vọng) |
|---|---|---|
| Tổng wall time | 23m12s | ~9–10 phút |
| Arrow | 19.5 phút (`-j2`) | ~5 phút (`-j8`) |
| Khoảng idle | 9.8 phút | ~0 |
| ccache | không | có (nếu cài) → rebuild sau này nhanh hơn nhiều |
