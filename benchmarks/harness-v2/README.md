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

**P2 — containment and measurement primitives: extracted, adoption partial.**

- `measurement/primitives.py` owns strict JSON, UTC/boottime clocks, bounded
  file/tree hashes, and receipt JSON writing.
- `host/primitives.py` owns host probes, pressure/meminfo/diskstats parsing,
  cgroup membership and populated checks, verified process termination,
  systemd service properties, receipt-directory creation, quota-attestation
  validation, and watchdog cadence/observer helpers.
- `m4_containment_runner.py` now uses the extracted identity, host-probe,
  service-property, quota, and watchdog helpers. Launch, cgroup path/cleanup,
  and the watchdog loop remain local because their legacy tests monkeypatch
  those module-level symbols; moving them is a P3 test-port task.
- `run_shared_collection_pair.py` now uses the measurement hash/receipt helpers.
- `benchmarks/native-lifecycle/` and the P3 `run_native_query_lock_split*`
  runners remain archive candidates; they are not V2 entrypoints.

**P3 — Rust lifecycle split: first slice implemented.**

- `operations/m4/rust_lifecycle/` holds pure context/artifact identity,
  preflight headroom predicates, executor continuation rules, query-latency
  verification, and failure/pair/diagnostic receipt construction.
- `run_rust_lifecycle_pair.py` remains the CLI compatibility shim and imports
  those helpers; command line, operation IDs, receipt names, and refusal paths
  are unchanged.
- `make_context`, full `preflight`, `run_pair`, and `_run_pair_arms` still live
  in the shim; moving them requires porting their monkeypatch-heavy tests.
- Equivalence tests compare the new helpers against the legacy shim for clean
  and refusal paths; a synthetic pair freezes the semantic receipt fields. No
  benchmark workload was run.

The implementation adds no benchmark workload, production launch, threshold,
policy, cgroup-limit, or RC change. Synthetic containment verification launches
its own transient user units and cleans them up; it runs no production workload.

## Run

```bash
python3 -m unittest discover -s benchmarks/harness-v2/contracts/tests -v
python3 -m unittest discover -s benchmarks/harness-v2/host/tests -v
python3 -m unittest discover -s benchmarks/harness-v2/measurement/tests -v
python3 -m unittest discover -s benchmarks/harness-v2/operations/m4/rust_lifecycle/tests -v
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
