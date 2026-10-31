# Harness V2

Post-RC refactor track for the M4 research harness. The frozen release
candidate is not changed by this tree.

## Status

**P1 — contract layer and compatibility readers: implemented.**

- `contracts/policy_v1.py` reads the frozen policy JSON, verifies schema v1 and
  an optional SHA-256, and resolves named sections or operation purposes.
- `contracts/receipt_v1.py` reads historical containment receipts and operation
  plans, validates schema v1, and returns a normalized identity/status view.
  Unknown schema versions, unknown document shapes, and wrong field types fail
  closed.
- `contracts/golden/manifest.json` freezes the policy hash, fixture hashes, and
  expected normalized fields for every historical purpose represented by a
  tracked or preserved receipt.
- Fixtures under `contracts/golden/fixtures/` are verbatim copies. Original
  receipts are never rewritten.

P1 does not move containment code, change thresholds, change the policy,
re-run a benchmark, or modify the frozen RC.

## Run

```bash
python3 -m unittest discover -s benchmarks/harness-v2/contracts/tests -v
```

Legacy gate tests remain in `research/m4-native/tools/`:

```bash
python3 research/m4-native/tools/test_m4_benchmark_policy.py
python3 research/m4-native/tools/test_m4_contention_policy.py
python3 research/m4-native/tools/test_m4_harness_scenarios.py
```

The contention suite currently has three pre-existing failures in the dirty
research worktree; see the track report. They are runner-behavior tests, not
contract-reader failures.

## Provenance note

The `m4-rust-full-lifecycle-pair` golden receipt is a preserved copy of an
untracked historical receipt. The manifest records both the fixture SHA-256 and
the original source path/SHA-256. P0 still owns broader provenance freezing and
archive decisions for untracked runner modules.
