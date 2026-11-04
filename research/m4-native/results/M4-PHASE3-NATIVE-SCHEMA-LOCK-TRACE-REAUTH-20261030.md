# Fresh one-run authorization — native schema-lock trace (2026-10-30)

## Authorization

After reviewing the ID-1 artifact-provenance refusal, the owner authorized one new native-only trace. Exact one-use ID: `m4-rust-lifecycle-pair-20261030t015522z-nquery-locktrace-fresh1`. This is a new authorization; the earlier three-ID batch remains closed. Any refusal consumes this ID and ends the attempt. No M4 arm, pair, performance comparison, quota/cap change, cleanup, artifact movement, production action, or retry is included.

## Artifact binding and recovery

- Use default binary discovery, then require the exact expected SHA-256. Current intended selection is `research/m4-native/.work/build/rust-bridge-target/debug/deps/zg_engine-28bb1915fe9f2135`, SHA-256 `c6b1b11effe9026c0a85fc0b262bc3056ab2b84252fe95350d17a44c41ff8f19`.
- Use the isolated v4 native library at `research/m4-native/.work/build/rust-bridge-target/debug/build/m4-native-schema-lock-trace-v4/out/zvec-prebuilt`, SHA-256 `65817813bacc710b29ea01d273ca604c0e1177b1e5aabf618604fe45c701a971`.
- The CLI previously required `--binary-path` whenever `--expected-binary-sha256` was supplied, although `preflight()` already verifies the expected hash after default discovery. The narrow change removes only that CLI restriction and adds regression coverage; it does not weaken artifact verification.
- Do not create a new debug-reduced binary or change the quota. Refresh the root qgroup attestation only after verifying live `0/259` and disk headroom. Preserve the preflight output.

## Procedure and stop rule

1. Run one fresh `--native-only-diagnostic --validate-only` preflight with this ID, the expected binary SHA, and the expected v4 library SHA. Confirm that the returned plan records the exact expected executable/library paths and hashes and that every qgroup, disk, host, policy, and containment check passes. If not, preserve the refusal and stop—no launch and no retry.
2. Immediately before launch, capture host/process and live qgroup/disk state. Launch the single authorized native arm once with the same pinned hashes. Retain stdout/stderr, receipt, native JSONL trace, host/process before/after, and post-cleanup qgroup evidence.
3. Analyze each >100 ms query against all lock wait/hold spans and the full query setup-to-validation interval. Verify trace integrity and distinguish attribution from correlation. Write the evidence report and update the active checkpoint. No further run is authorized by this instruction.

**Execution result:** the fresh no-launch preflight passed with the approved binary/library hashes and live resource proof. The one native arm ran once, returned 0, and produced 13,886 valid trace records. Three of four >100 ms query waits matched same-object `optimize_seal` exclusive holds; one shared-lock wait remains unresolved. The runner completed with workspace-accounting caveats and performance eligibility false. The one-use ID is consumed; see [the full result report](M4-PHASE3-NATIVE-SCHEMA-LOCK-TRACE-RESULT-20261030.md).