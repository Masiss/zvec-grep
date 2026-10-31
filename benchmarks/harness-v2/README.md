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

**P0 — Runtime provenance: implemented.**

- `contracts/golden/runtime-provenance.json` pins every active V2 runtime
  module plus the policy, containment runner, and lifecycle shim by SHA-256.
- `contracts/provenance.py` validates schema, duplicates, file presence, hashes,
  and tracked paths. `test_runtime_provenance.py` fails closed on drift.
- Closed research runners and stale tests are archived; historical receipts are
  unchanged.

**P2 — Host/measurement primitives: implemented.**

- Host probes (`host_snapshot`, process snapshots, cgroup lookup/populated) and
  the measurement artifact helper are owned by `host/` and `measurement/`.
- `m4_containment_runner.py` delegates to those primitives; duplicate active
  implementations were removed. `run_arm`/watchdog remain in the containment
  core as the single launch/watchdog owner by design.
- Measurement artifact tests moved to the V2 module; containment tests were
  ported to patch the V2 host module.
- No threshold, policy, cgroup limit, or RC change.

**P3 — Rust lifecycle split: implemented.**

- `operations/m4/rust_lifecycle/` owns the operation: `config.py` (injected
  policy/containment dependencies), `context.py` (corpus, artifact identity,
  binary discovery, arm context), `preflight.py` (full authorization/resource
  preflight), `executor.py` (arm execution and finalization), `verify.py`
  (query-latency gate), and `receipt.py` (failure, pair, and diagnostic
  receipts).
- `run_rust_lifecycle_pair.py` is now only the CLI compatibility shim; command
  line, operation IDs, receipt names, refusal paths, and public helper names
  are unchanged.
- Legacy monkeypatch-heavy tests were ported to the split modules. They still
  exercise the same refusal ordering and synthetic receipts.
- No benchmark workload was run.

**P4 — Operation families: implemented at the V2 boundary.**

- `operations/m4/registry.py` declares every policy-owned operation and its V2
  disposition. `validate()` fails closed when policy sections or purposes
  drift.
- Active: Rust lifecycle and lock-contention microbench. Research-only:
  shared collection, native query-lock split, observer overhead, qgroup
  reclaim, and synthetic containment.
- Write amplification is `historical_only` with execution refused: no tracked
  runner references its purpose.
- Scalar-open diagnostics are `unregistered`: receipts exist but no top-level
  policy purpose exists, so they cannot launch as V2.
- Receipt adapters normalize lock, write-amplification, and diagnostic receipts
  without ever promoting them to performance evidence.
- `operations/m4/execution.py` is the shared gate: it refuses historical or
  unregistered operations before launch, requires the receipt purpose to match
  the descriptor, and lets adapters own launch/receipt loading. The legacy
  runners remain the CLI execution owners until P6 archiving.

**P5 — Retrieval disposition: upstream canonical adopted.**

- `benchmarks/zg-retrieval` was replaced with the upstream retrieval harness,
  including compact-response parsing, snapshot/report changes, and the
  retrieval-only/PR/comment workflows and actions.
- Retrieval tests: **185 pass / 0 fail**.
- The copied workflow tests now normalize YAML line continuations introduced by
  this repository's line-length constraints; the asserted behavior is unchanged.
- Retrieval has no M4 imports and remains an independent `core/metrics/engines/
  reports` tree.

**P6 — Archive/provenance: implemented.**

- `benchmarks/harness-v2/archive/` now holds the frozen native-lifecycle
  baseline, closed native query-lock split runners, and synthetic prototypes.
  Its README records status and last evidence.
- Policy-referenced diagnostic fixtures/runners and the lifecycle's
  `create_measurement_artifact.py` dependency are tracked under
  `research/m4-native/tools/`; a clean checkout no longer depends on untracked
  runtime modules.
- Historical receipt directories were not moved or rewritten.
- The registry points at the archived native query-lock runner and refuses it
  for execution.

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
python3 research/m4-native/tools/test_run_rust_lifecycle_pair.py
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
