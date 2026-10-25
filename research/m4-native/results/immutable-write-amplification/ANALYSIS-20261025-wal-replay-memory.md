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

At the measured 16 MB WAL, the tracked replay peak was about 32 MB; the 0.85 MB case was about 1.4–1.7 MB. The present implementation therefore has a real approximately encoded-plus-decoded memory cost, but this bounded evidence does not justify the complexity and correctness risk of a streaming-replay rewrite now. No target production WAL maximum or replay-memory budget was established, and the large mixed-frame candidate was blocked before it ran. **Decision: retain the current replay architecture for this slice; do not claim unbounded WALs are safe.** Reconsider only with an owner-defined maximum WAL/recovery-memory budget and a separately authorized, fresh operation after the process-tree telemetry issue is resolved; never retry the consumed operation.

The recorded data does not change WAL ordering, valid-prefix behavior, batch atomic visibility, per-Upsert durability, or recovery semantics. The 7-byte torn-tail probe and the separately passing normal/TSan correctness suites remain the correctness evidence.

## Raw operation roots

- Complete small pair: `research/m4-native/.work/m4-native-workspace-quota/m4-write-amplification-pair-wal-replay-memory-small-20261025-01/`
- Large control arm; telemetry-aborted before candidate: `research/m4-native/.work/m4-native-workspace-quota/m4-write-amplification-pair-wal-replay-memory-many-records-20261025-01/`

These roots are intentionally retained as raw evidence; no unrelated data was cleaned.
