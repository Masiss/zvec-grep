# W2c — Crash-Atomic Incremental Generation

Status: **PASS for W2c acceptance; W3 gated on verifier surface.**

Baseline: `50d53ea` (W2b). Implementation: `d366692`. Full design and results:
[`rust/docs/deep-storage-integrity-w2c.md`](../../../../../rust/docs/deep-storage-integrity-w2c.md).

## Evidence

- `results/crash-results.json` — 7-case SIGKILL matrix: old manifest preserved,
  active verifier complete, FTS query works, recovery works.
- `results/cost.json` — 2,001-file staged incremental, reflink/copy, RSS and
  file-system output.
- `results/disk-amplification.json` — old + stage physical/logical footprint at
  crash.
- `results/vector-old-after-crash.txt` — vector query against the old generation
  after a crash.
- `results/fts-old-after-crash.txt` — FTS query against the same generation.
- `w2_verify.cc` — standalone verifier used for the W2c receipts. It defines
  completeness from the header capability plus `content_crc`; zero
  per-segment CRCs are reported separately.

## Scope

Engine staging only. Native zvec 0.7.2, ZGM2, searchable index format, scheduler,
UX V2, policy and thresholds were not changed. The installed
`libzvec_c_api.so` and production binary were not replaced.

Raw crash fixtures and the runnable harness were scratch artifacts under
`.tmp/w2c/` and are not committed; the receipts above are the retained evidence.
