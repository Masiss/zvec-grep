# WAL replay memory profile — 2026-10-25

## Scope and outcome

This is an exploratory measurement of the existing replay path, not a qualification or production change. The probe records encoded-file bytes, decoded payload bytes/capacities, record-vector capacity and growth, replay wall/CPU time, process I/O, process RSS/PSS/Private_Dirty, and residency of only the probe files. The tracked-live estimate excludes allocator metadata and unrelated objects; RSS/PSS are process-wide snapshots, not replay-exclusive attribution.

Two policy-bound profiles were created: a 64-document small case and a 1,200-document many-record case. Both use replay batches of 16 and append a 7-byte incomplete suffix after durable ACKs. In the candidate probe, the first half is written with legacy frames and the second half with LZ4; the control probe is all legacy. Main-operation options and probe-frame options are recorded separately in each `config.json`.

## Evidence

| Profile / arm | Operation status | WAL bytes | Records / docs | Decoded payload B | Tracked peak upper bound B | Replay wall / CPU ms | Process read B | RSS delta / PSS delta B |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| small / control, legacy | complete pair | 855,257 | 16 / 256 | 854,930 | 1,710,830 | 60.2 / 59.0 | 856,064 | +66,000 / +2,000 |
| small / candidate, mixed legacy+LZ4 | complete pair | 578,077 | 16 / 256 | 854,930 | 1,433,650 | 104.0 / 101.6 | 577,536 | +8,000 / +9,000 |
| many-records / control, legacy | first arm only; watchdog aborted after replay | 16,079,597 | 300 / 4,800 | 16,073,600 | 32,173,700 | 1,410.7 / 1,395.7 | 16,080,900 | +27,639,000 / +27,570,000 |

Small-pair values are from `...small.../REPORT.md`; raw per-arm profiles and process/host snapshots remain under the operation root. The candidate small probe had 1/145 target pages resident after `POSIX_FADV_DONTNEED`; therefore it is not labeled fully cold. Control was 0/212. The system page cache was not globally evicted.

The many-record control's executable emitted replay measurements, recovered all 4,800 durable documents, and truncated the torn 7-byte suffix. Its 300 decoded records yielded roughly 16.08 MB encoded + 16.07 MB decoded payload live at once, plus vector capacity. The WAL open used about 1.41 s wall and 1.40 s CPU and incurred about 16.08 MB process reads. This supports linear growth of the current full-buffer-plus-decoded-records design. The cgroup peak was 451,944,448 bytes, with no OOM/limit events and zero swap for the arm.

### Large-operation integrity stop

The large operation is **not a complete paired run** and is **not performance-eligible**. After the control command exited successfully, the watchdog's final sample found `process_memory_sample_complete=false` for the process tree. It preserved `watchdog-abort.json` with `reason=required_telemetry_missing` / `ValueError: incomplete process-tree telemetry`, marked `measurement_quality=INCOMPLETE`, and blocked the candidate arm. `unit-cleanup.json` records verified cleanup; the watchdog used `cgroup.kill`. Raw stdout, runtime samples, receipts, and the partial index/WAL are retained unchanged. No retry was made or is authorized by this artifact.

Host snapshots are preserved by the profile. Around the small run, MemAvailable was about 3.85 GB before and 3.70 GB after; around the large attempt it was about 4.30 GB before and 4.76 GB after. Shared host I/O pressure was elevated and remains observational context only. These single pairs are descriptive, not causal.

## Assessment

At the measured 16.0808-MB legacy WAL and 10.8725-MB mixed-LZ4 WAL, decoded payload stayed 16.0736 MB while tracked peak fell from 32.1749 MB to 26.9666 MB (−5.2083 MB, 16.2%), exactly reflecting the encoded-buffer reduction. Replay-open wall/CPU was 1,251.9/1,237.5 ms for candidate versus 1,230.0/1,214.4 ms for control (+1.8%/+1.9%). Process-wide RSS/PSS deltas were about 5 MB lower for candidate, while its whole-arm cgroup peak was 23.2 MB higher; these measures are not replay-exclusive and do not jointly establish an application-memory improvement. This single candidate-first pair is exploratory/non-qualifying. **Decision: retain the current replay architecture for this slice; do not claim unbounded WALs are safe.** The probe supports the encoded-plus-decoded memory model, but no target production WAL maximum or replay-memory budget exists to justify a streaming rewrite.

The recorded data does not change WAL ordering, valid-prefix behavior, batch atomic visibility, per-Upsert durability, or recovery semantics. The 7-byte torn-tail probe and the separately passing normal/TSan correctness suites remain the correctness evidence.

### Telemetry, capacity, and completed large profile (2026-10-25)

The process-tree sampler's bounded retry for cgroup membership churn remains fail-closed for persistent churn or unreadable live members; its focused regressions pass 2/2. This repairs sampler behavior without altering the original incomplete operation receipt.

**Capacity attempt history:** operation `...many-records-20261025-03` was refused before launch because filesystem free space (17,481,572,352 B) was 737,427,648 B below the unchanged 18,219,000,000-B reserve. For operation `...many-records-20261025-04`, read-only validation passed with qgroup free 90,906,624 B (67,108,864 B required), but the actual arm-launch gate `M4-CA-DISK-CAPACITY` required 20,366,483,648 B and observed only 18,600,906,752 B (short 1,765,576,896 B). The standard 18,219,000,000-B disk reserve itself passed; the stricter operation-specific hard gate did not. The receipt says `benchmark_started=false`; the wrapper exited 1 with `PREFLIGHT_BLOCKED`, not a build/test failure. Operation `...many-records-20261025-05` later started its candidate arm but the watchdog stopped it after 7.54 s at `M4-CA-DISK-CAPACITY` (20,365,680,640 B observed versus 20,366,483,648 B required); it produced no completed pair and its cgroup cleanup was verified. None of IDs 03/04/05 was retried.

After explicit owner authorization, the retained operation-01 partial, small-pair, and operation-05 roots were copied to `wal-replay-memory-raw-archives-20261025/` with buffered non-reflink I/O, independently hash-verified, and only those exact workspace roots removed. `ARCHIVE-MANIFEST.json` SHA-256: `908d32cca935f0da5e90a0f83f2ef137a507c8fa88828692aaa4aa024a650385`.

**Operation `m4-write-amplification-pair-wal-replay-memory-many-records-20261025-07`:** the fresh no-launch preflight passed with current binary SHA-256 `da6b57999bcc7c613463381d504c08e9796a043a9e1c5492be96f1fcbebe2fd2`; live qgroup `0/259` had 270,336 B referenced, 133,947,392 B available, and 67,108,864 B required. Both candidate-first arms exited 0 with containment PASS, `measurement_quality=COMPLETE`, no swap, snapshot parity, zero retrieval mismatches, and 4,800 recovered docs each. The probe replayed 300 records, found zero target pages resident after per-index advice (rest of host/page cache remained warm), truncated the injected 7-byte torn suffix, and recovered the valid prefix. Candidate mixed legacy/LZ4 WAL was 10,872,500 B versus 16,080,800 B legacy control; decoded payload was identical at 16,073,600 B. Tracked peak was 26,966,600 B versus 32,174,900 B. Replay-open was 1,251.850/1,237.5 ms wall/CPU versus 1,230.010/1,214.4 ms. The report correctly remains `EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING`, performance-eligible=false; one pair is descriptive, not qualification. Its host-before snapshot recorded MemAvailable 4,472,804 KiB against the telemetry-only historical envelope 5,477,762 KiB; blocking failures were empty. The cgroup I/O controller was unavailable, so no cgroup I/O or syscall sync attribution is claimed.

The complete raw operation is archived at `m4-write-amplification-pair-wal-replay-memory-many-records-20261025-07/`; `ARCHIVE-MANIFEST.json` SHA-256: `7201bced90c789f62591e666f1cba73d0795bf4fd442795776ad943b2f10cd84`. The exact source root was removed only after independent source/archive verification. No production, default, WAL ordering, durability, or resource-policy change was made. The production WAL/recovery-memory budget remains undefined; retain current replay architecture and make no unbounded-WAL safety claim.

## Archived raw operation roots

- Prior small pair, telemetry-incomplete operation 01, and postflight-stopped operation 05: `research/m4-native/results/immutable-write-amplification/wal-replay-memory-raw-archives-20261025/` (verified combined manifest above).
- Completed exploratory pair 07: `research/m4-native/results/immutable-write-amplification/m4-write-amplification-pair-wal-replay-memory-many-records-20261025-07/` (verified manifest above).

All referenced source-workspace roots were cleaned only after buffered copies and independent hash verification; no unrelated operation roots were removed.
