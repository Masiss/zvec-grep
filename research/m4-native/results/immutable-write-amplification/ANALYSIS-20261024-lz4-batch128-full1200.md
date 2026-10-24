# Full-size WAL-LZ4 at 128-mutation chunks — 2026-10-24

## Decision

**Retain LZ4 at chunk128 as an exploratory candidate only; no default/qualification change.** Two counterbalanced 1,200-file pairs both saved process writes and improved query p95 at every measured stage. Counterbalanced means: writes −31.35%, CPU −5.58%, wall time −5.86%; mean query p95 improved 15.2–43.6% by stage. The replay-probe durable-ACK p95 mean, however, increased 5.3%, and CPU/wall/ACK directions reversed with arm order. Each condition has only one pair per order, and the result remains non-qualifying. This follow-up counters the unstable 600-file chunk128 signal but does not erase it or justify production defaults.

## Contract and method

Both fresh pairs used 1,200 files, `m4-batch`, mutation chunks128 in both roles, stats coalescing OFF, delta1536, total-run cap12, per-level cap4, 4 MiB stats threshold, LZ4 acceleration1, WAL replay probe enabled, and symmetric per-call mutation-latency tracing. Within each pair, the sole treatment was WAL-LZ4 OFF vs ON; arm order was counterbalanced. The first control-first wrapper exited before invoking the profiler because an ad-hoc process check matched its own shell command. No benchmark/root was created by that attempt; the process check was corrected to inspect actual process names, then the still-unconsumed operation ran successfully. This was not a policy/containment refusal.

Both roots were fresh and had already passed `VALIDATED_NO_LAUNCH`; both arms in each pair returned `PASS` / `HARD_QUOTA_BOUND` under qgroup `0/259`, the unchanged 134,217,728-byte hard cap, and the same cgroup controls. Both pairs had COMPLETE workspace accounting, exact documents/tokens/files, exact snapshot/oracle/reopen/retrieval parity, zero retrieval/top-10 mismatches, and top-10 overlap1.0. Peak RSS was about 413–417 MB, PSS about 405–409 MB, swap zero. Each root was copied outside the quota subvolume, hash-verified (79 source files, each), then that exact temporary root was removed; the quota workspace is empty.

## Per-order result

| Order | Process writes (off → on) | CPU (off → on) | Wall (off → on) | Replay ACK p95 (off → on) |
|---|---:|---:|---:|---:|
| Control-first | 75,952,128 → 54,861,824 B (−27.8%) | 11.305 → 12.017 s (+6.3%) | 13.113 → 13.954 s (+6.4%) | 63.65 → 93.53 ms (+46.9%) |
| Candidate-first | 78,819,328 → 51,388,416 B (−34.8%) | 9.694 → 7.810 s (−19.4%) | 11.377 → 9.102 s (−20.0%) | 67.56 → 44.69 ms (−33.9%) |
| Two-order mean effect | **−31.35%** | **−5.58%** | **−5.86%** | **+5.34%** |

Query p95 (LZ4 on vs off, averaging the two orders) was 3.267→1.842 ms initially (−43.6%), 2.675→1.929 ms in burst1 (−27.9%), 2.985→2.417 ms in burst2 (−19.1%), 3.002→2.423 ms in burst3 (−19.3%), and 2.842→2.410 ms after reopen (−15.2%). Query p95 improved in both orders for all five stages. Durable replay-ACK p95 did not: the two orders reversed direction substantially (one +46.9%, one −33.9%).

The per-frame telemetry provides a mechanism check: each LZ4 arm compressed 182 frames from 71,660,941 input bytes to 24,498,222 output bytes (about 65.8% fewer WAL bytes). Measured LZ4 compression CPU was 115.48 ms in control-first and 101.57 ms in candidate-first, about 108.52 ms mean or roughly 1.1% of mean candidate process CPU. This supports the WAL reduction but is too small to explain the larger order-dependent total CPU changes; those differences remain unexplained and should not be attributed to compression cost.

## Context and limits

The earlier full-size LZ4 OFF/ON studies used chunk256 and are already complete; do not repeat them. Their process writes also fell (−19.8% and −35.6%), but CPU/wall, replay-ACK p95, and most query p95 stages regressed. The new chunk128 pair therefore provides a promising scale×chunk interaction signal. However, those older chunk256 profiles predate the explicit per-call mutation-latency tracing added to this chunk128 profile; do not treat a cross-chunk aggregate as a fully matched factorial estimate. The 600-file counterbalanced study also gave different chunk-dependent CPU/wall directions. Across all sizes, the reliable repeated mechanical result is lower WAL/process writes; broader performance effects remain order/workload-sensitive.

Both 1,200-file chunk128 pairs are marked exploratory/non-qualifying (`performance_eligible=false`). Keep LZ4 research-only and do not alter default chunk size, compression, durability, or production behavior. Full operation reports and raw profiles/receipts/host snapshots are linked below.

## Reproduction artifacts

- [Control-first pair](m4-write-amplification-pair-20261024-lz4-batch128-full1200-control-first-01/REPORT.md)
- [Candidate-first pair](m4-write-amplification-pair-20261024-lz4-batch128-full1200-candidate-first-01/REPORT.md)

Binary SHA-256 `252debff281ef3535df5776ce31669c7997879808f8dec5f76308dccb3960323`; policy SHA-256 `83cae713763fed47b28cfe3fb079097155bfa3e6ca59d19a0cd606cfc5118134`; dictionary-tree SHA-256 `9c05e30b201663f0045db0cd9d810b831b7adba59055b1658353a1bd7ae5fa5c`.

<!-- END -->
