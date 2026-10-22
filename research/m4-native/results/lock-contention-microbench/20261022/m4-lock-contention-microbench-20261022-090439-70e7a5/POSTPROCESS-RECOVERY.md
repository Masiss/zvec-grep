# Offline post-processing recovery — 2026-10-22

Operation: `m4-lock-contention-microbench-20261022-090439-70e7a5`.

## Integrity and recovery

The contained workload **did run once** and exited 0. Its immutable `receipt.json` is `PASS`; the workload reported 0 writer, reader, durability, and coherence failures. The wrapper nevertheless recorded `OPERATION_INCOMPLETE`: its post-run parser expected JSON to fit on one line, while this executable emits a valid multi-line JSON document. The first recorded failure is preserved in `operation-plan.json`; no benchmark retry was made.

The parser was corrected to parse the complete JSON document. Offline reprocessing of the saved `stdout.log`, using the same contract checks as the wrapper, passed. All 17 checksum entries present before this recovery matched their files. No raw stdout, receipt, command-exit, operation plan, or existing checksum entry was rewritten. The wrapper's outer `OPERATION_INCOMPLETE` status remains authoritative for the original process; this file records the separate successful offline validation, not a retroactive edit to that status.

## Operation and containment

The one contained process ran 8,192 Jieba-tokenized seed documents per arm, 4 concurrent WAL-durable writers × 8 Upserts, 2 search readers × 32 queries, one compaction, Flush, and close/reopen search verification. Internal order was `default`, then `snapshot`. Binary SHA-256: `275c355882396d8b66861048b8706e018ee21081b9b89701b8aeccccba707052`; dictionary-tree SHA-256: `9c05e30b201663f0045db0cd9d810b831b7adba59055b1658353a1bd7ae5fa5c`.

The contained receipt reports status `PASS`, return code 0, watchdog completed without abort, unit cleanup verified, 18 samples, cgroup memory peak 373,723,136 B (356.4 MiB), CPU 1,748,558 µs, swap peak 0, `memory.events.max=oom=oom_kill=0`, no CPU throttling, and no cgroup I/O controller. Workspace observation is still sampled/non-atomic: observed traversal maxima were about 7.22 MB allocated and 7.17 MB apparent, but no verified lifetime peak or independent capacity upper bound exists. Therefore the receipt is not performance-eligible (`performance_eligible=false`) and is not qualification evidence.

At operation preflight, MemAvailable was 5,088,980 KiB, below the unchanged historical 5,477,762 KiB envelope. That historical comparison is retained as a measurement limitation; it was not waived or rewritten. The separately authorized exploratory capacity reservation (3 GiB host reserve + 512 MiB cgroup cap) passed. `/var/tmp` had 77,252,374,528 bytes free. Host I/O PSI was 1.09% full avg10 and observational only.

## Descriptive results from saved stdout

| Mode | Compaction | Writer p50 / p95 / max (ms) | State-mutex wait p95 (ms) | WAL append p95 (ms) | Query-mutex wait p95 (ms) | Reader p50 / p95 (ms) | Reader-mutex wait p95 (ms) | Runs after Flush |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Default | 20.588 ms, `ok` | 0.592 / 30.869 / 32.371 | 30.156 | 0.752 | 0.0010 | 0.037 / 0.043 | 0.00043 | 2 |
| Snapshot prototype | 22.210 ms, superseded by concurrent state change | 0.817 / 22.850 / 27.299 | 22.162 | 0.754 | 0.962 | 0.750 / 1.563 | 0.781 | 6 |

One operation, 32 writer samples per mode and 64 reader samples per mode: these are descriptive small-sample tails, not stable p95 estimates or a causal architecture result. In default mode, the writer tail is predominantly state-mutex wait, not WAL append or query-mutex wait. The snapshot attempt does not yet solve the writer tail: it still has a large state-mutex wait and shifts noticeable blocking to readers/query admission; it also aborts publication, leaving more runs after Flush. Shared-host scheduling and the below-historical-envelope MemAvailable state further limit interpretation.

## Source finding and next test

Source inspection after the measurement found that snapshot setup counted tombstones by scanning every document in the selected input segments, then scanned those documents a second time to populate the snapshot tombstone set, while both state and exclusive query admission were held. This is O(input documents) critical-section work. It is a plausible contributor to the remaining snapshot-mode writer wait, but the run did not time snapshot-capture separately and cannot prove that attribution.

A follow-up source change now counts/copies tombstone IDs by selected segment ID ranges instead of scanning all input documents. It has not yet been benchmarked; focused correctness/build verification is required before considering a new, separately preflighted operation. The prior operation must not be retried to validate the parser fix.

This artifact is supplemental to, and does not replace, the raw operation plan or receipt.
