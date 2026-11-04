# Phase 3 native schema-lock trace — Run 1 preflight refusal (2026-10-30)

## Outcome

**OPERATION_BLOCKED before workload; no native lifecycle run occurred.** The one-use operation ID `m4-rust-lifecycle-pair-20261030t011938z-nquery-locktrace-r1` is consumed by the artifact-provenance refusal. The approved plan says stop on any gate refusal, so the remaining IDs are not exercised and this authorization batch is closed. Phase 3 remains **PARTIAL**; no lock-owner attribution was collected.

## What happened

1. The first `--validate-only` invocation supplied `--expected-binary-sha256` without `--binary-path`. The CLI rejected the argument combination (`--expected-binary-sha256 requires --binary-path and 64 lowercase hexadecimal characters`) before preflight. Its stdout capture is the zero-byte `native-query-timeline-20261029/m4-rust-lifecycle-pair-20261030t011938z-nquery-locktrace-r1-preflight.json`; no operation root was created.
2. A corrected no-launch preflight supplied an existing measurement executable and the pinned v4 native library. It stopped in `find_test_binary()` with `ValueError: measurement binary differs from its original ELF load image`. The captured stdout file `native-query-timeline-20261029/m4-rust-lifecycle-pair-20261030t011938z-nquery-locktrace-r1-preflight-final.json` is zero bytes because the exception was written to stderr. This happened during artifact resolution, before qgroup/disk/host qualification and before any operation workspace or test workload was created.

The selected measurement executable was `research/m4-native/.work/build/rust-bridge-target/debug/deps/.m4-measurement/zg_engine-552d5229013b8e71-nodebug` (116,162,592 bytes; SHA-256 `c17e234f9ec300f5f066ce7713790c0e2c2bbecdc375d03efea221ad77d88c27`). Its required original at `debug/deps/zg_engine-552d5229013b8e71` is now 344,621,128 bytes with SHA-256 `020f8397af778b38082611c2178757e0052f6386c37091aad2b7197e73c9899b`. The normalized ELF/program-header comparison and all four PT_LOAD segments differ; the current Build IDs are `0f4ed429b592192c6f88ddd78e43c69e3c0ce223` (original) versus `f982f56f63761e0d61bbc4d61b5377e34d8b9080` (measurement copy). The measurement copy timestamp is 2026-10-28 15:46:47 +0700; the original is 2026-10-29 00:24:14 +0700. This establishes that the retained copy is stale relative to the current original; the exact rebuild that replaced the original is not known.

No binary was rebuilt or copied in response. The explicit override was rejected rather than bypassed. The first invocation's argparse error was a command-usage defect; the second invocation is the decisive artifact-integrity refusal.

## Gate state observed immediately before the refusal

These are fresh observations, not PASS results from the refused preflight:

- At 2026-10-30T01:38:33Z, Btrfs on UUID `6c15377e-2602-4870-96ba-50e58605309c` had quotas enabled in `squota` mode, consistent, with no override. Qgroup `0/259` referenced/exclusive was 53,837,824 B, cap 167,772,160 B, headroom 113,934,336 B. The unchanged workspace allowance plus reserve required 100,663,296 B, so observed headroom exceeded that by 13,271,040 B. Separate qgroup `0/258` was 128,782,336 / 134,217,728 B; it was not changed.
- At 2026-10-30T01:38:42Z, disk available was 18,443,689,984 B on `/`. The command's `df -B1 -T --output=...` form was incompatible and returned an option error; the immediately corrected `df -B1 --output=...` and `df -B1 -T` calls supplied the value above.
- The root-owned attestation was refreshed from live state: `/var/tmp/m4-native-workspace-quota-pwd.attestation.json`, referenced 53,837,824 B and headroom 113,934,336 B. No qgroup cap, reserve, cleanup, or artifact location changed.

Because artifact validation failed first, the preflight did **not** adjudicate those resource values, and no host-before/after measurement snapshot exists for a workload.

## Prepared artifact and integrity

The isolated v4 native schema-lock trace library is built and independently finalized from the two archive members that include the inline timeline header. Its shared-library SHA-256 is `65817813bacc710b29ea01d273ca604c0e1177b1e5aabf618604fe45c701a971`; required trace-path, schema-lock-owner, query-setup, and FTS markers and the C query symbol were present. Native source files were not modified by the overlay build. Full source/object/archive/library hashes and lock-site inventory are in `native-query-timeline-20261029/native-schema-lock-trace-build-v4.json`.

The most recent successful diagnostic used `zg_engine-28bb1915fe9f2135`, SHA-256 `c6b1b11effe9026c0a85fc0b262bc3056ab2b84252fe95350d17a44c41ff8f19`. It remains in the default target directory, but this attempt did not run it. The CLI currently requires an explicit `--binary-path` whenever an expected binary SHA is supplied, while the default discovery path can select and hash the newest valid binary. No CLI or policy modification was made after the refusal.

## Required recovery / authorization boundary

Do not retry ID 1 and do not proceed with IDs 2 or 3 under the closed batch. No qgroup increase is indicated by the latest snapshot. If the owner wants to continue, a fresh instruction should authorize a new one-use ID and confirm a recovery route. The least disruptive candidate is to allow expected-SHA pinning with default binary discovery (the preflight function already checks the selected SHA), then perform a new no-launch preflight using the exact expected binary hash and v4 library hash. Alternatively, a fresh matching measurement copy could be produced, but its disk cost and reserve must be re-evaluated first. Launch only after a complete fresh preflight passes.

This was solely a provenance/preflight failure. It supplies no evidence about `schema_handle_mtx_` owners, native query latency, or performance.