# Harness V2 refactor plan — M4 containment, lifecycle, retrieval

Date: 2026-10-31. Scope: audit only. No benchmark was run, no harness code was
rewritten, and neither `integration/upstream-rust` nor `work/zg-optimization`
was modified.

## 1. Inputs and baseline

| Input | State |
|---|---|
| RC baseline | `integration/upstream-rust` at `c2347d5`; code baseline `289674b`; tag `rust-v0.3.0-rc.2` |
| Research worktree | `/home/masis/src/zvec-grep`, branch `work/zg-optimization`, HEAD `fdf5c90`, **dirty** |
| Upstream baseline | `/home/masis/src/zvec-grep-upstream-baseline`, HEAD `e1f93d2` |
| M4 evidence | 5,576 JSON files under `research/m4-native/results/` |
| Retrieval harness | upstream `benchmarks/zg-retrieval` (newer than the fork copy) |

The research worktree has uncommitted/untracked harness files. In particular,
`create_measurement_artifact.py`, the whole `benchmarks/native-lifecycle/`
directory, and several `run_native_query_lock_split*` scripts are untracked;
`run_rust_lifecycle_pair.py` imports `create_measurement_artifact`. A clean
checkout therefore cannot reproduce the current lifecycle runner. Freeze
provenance before any refactor.

## 2. Inventory and disposition

| Component | Evidence | Disposition |
|---|---|---|
| `research/m4-native/tools/m4_containment_runner.py` | 3,313 lines, 82 functions; policy/cgroup/host/watchdog core | **REFACTOR into core + operations**; keep behavior and receipts |
| `research/m4-native/docs/benchmark-policy.json` + `.md` | policy schema v1, many per-operation contracts | **FREEZE v1**, read declaratively; no threshold edits |
| `research/m4-native/tools/run_rust_lifecycle_pair.py` | 969 lines; preflight 290–518 (229), context 159–265, pair arms 710–891 | **REFACTOR** into context/preflight/executor/receipt; keep CLI shim |
| `research/m4-native/tools/create_measurement_artifact.py` | 125 lines, untracked, imports containment; ELF load-image checks | **TRACK + REFACTOR** into measurement primitives |
| `research/m4-native/tools/run_lock_contention_microbench.py` | 893 lines; 246 receipts | **REFACTOR as operation adapter** |
| `research/m4-native/tools/run_shared_collection_pair.py` | 364 lines; hardcoded build/dictionary/results paths; 49 receipts, latest 2026-10-23 | **REFACTOR or ARCHIVE after provenance review**; not a core primitive |
| `research/m4-native/tools/run_native_query_lock_split_pair.py` + `run_native_query_lock_split_*` | untracked; P3 is closed/partial; hardcoded `/proc`, `df`, worker paths | **ARCHIVE as research-only**; preserve evidence |
| `research/m4-native/tools/build_native_schema_lock_trace.py` | 1,970 lines; CMake/source tracing | **ARCHIVE/isolate**; research-only |
| `contained_loader_prototype.py`, `sealed_unit_prototype.py` | 191/229 lines, prototypes | **ARCHIVE** |
| `observer_overhead_*`, `qgroup_*`, `provision_btrfs_*`, `refresh_workspace_quota_attestation.py` | policy-referenced diagnostics and host setup | **KEEP as diagnostic/operations adapters**, not core |
| `benchmarks/native-lifecycle/run_baseline.py` + protocols | 469 lines, untracked; frozen p1 protocol; duplicates containment primitives | **ARCHIVE as frozen historical baseline**; not V2 entrypoint |
| `benchmarks/zg-retrieval` (upstream) | core/metrics/engines/reports split; golden tests | **KEEP as canonical retrieval harness** |
| `benchmarks/zg-retrieval/expansion` | optional BEIR/DuRetrieval data expansion, separate requirements | **KEEP isolated research input**; not a core dependency |
| Fork `benchmarks/zg-retrieval` | 697 lines behind upstream compact-response/parser changes | **DO NOT PORT**; adopt upstream copy or rebase |
| `research/m4-native/docs/harness-review-scenarios.md` + contract tests | scenario/gate contract, authority rules | **KEEP as contract tests** |

### Receipt-volume evidence

`purpose` counts in `research/m4-native/results/`:

- 549 `immutable-write-amplification-pair`
- 315 `m4-rust-full-lifecycle-pair`
- 246 `m4-lock-contention-microbench`
- 49 `m4-shared-collection-pair`
- 17 `synthetic-containment-rehearsal`
- 5 `m4-scalar-open-latency-diagnostic`
- 4 `m4-scalar-open-resource-rehearsal`
- 3 `m4-observer-overhead-diagnostic`

These are historical evidence, not disposable test output. Schema/field
compatibility is a P0 constraint.

**Provenance gap:** write-amplification receipts exist, but no tracked tool file
currently references `immutable-write-amplification-pair` except the containment
policy/tests. Reconstruct or archive the runner before treating that family as
V2-supported.

## 2.1 Priority order

| Priority | Work | Reason |
|---|---|---|
| P0 | Freeze provenance, track/archive untracked modules, golden receipts | Current runner cannot be reproduced from a clean checkout |
| P1 | Contract layer + v1 receipt reader | Protects 1,000+ historical receipts before code moves |
| P2 | Containment/host/measurement primitives | Removes duplicated safety-critical logic |
| P3 | Lifecycle adapter split + compatibility shim | Highest-volume current operation (315 receipts) |
| P4 | Other operation families (lock microbench, write amplification, diagnostics) | Reuse the same boundary; establish missing provenance first |
| P5 | Retrieval disposition (upstream canonical) | Avoid stale fork copy; retrieval is independently healthy |
| P6 | Archive prototypes/closed P3 runners and document | Reduce surface after migration |

## 3. Findings

### 3.1 God functions and policy branching

- `m4_containment_runner.py`
  - `run_arm` lines 2534–3221: **688 lines**; creates/validates cgroup,
    applies budgets, launches systemd, watches, samples, cleans up, writes
    receipts.
  - `watchdog_main` lines 2091–2431: **341 lines**.
  - `validate_contention_context` lines 591–856: **266 lines** of
    per-operation contract checks.
  - 84 references to operation-purpose constants; 9 purpose constants at
    lines 350–360. Adding an operation edits core code and policy.
- `run_rust_lifecycle_pair.py`
  - `preflight` lines 290–518: **229 lines**; repeats authorization, interval,
    arm-order, and policy checks.
  - `make_context` lines 159–265: **107 lines**.
  - `_run_pair_arms` lines 710–891: **182 lines**.
- `benchmarks/native-lifecycle/run_baseline.py`
  - `main` lines 239–461: **223 lines**.
- Retrieval (upstream, lower priority)
  - `engines/zg/run.mjs` `runRepository` lines 235–665: **431 lines**.
  - `reports/validation.mjs` `validateZgReport` lines 243–437: **195 lines**.
  These are large but cohesive; do not rewrite retrieval as part of M4 work.

### 3.2 Duplication

Duplicate function names across files (AST evidence):

- `frozen_manifest`, `find_test_binary`, `extract_corpus`, `git`:
  `run_rust_lifecycle_pair.py` (47–78, 88–139, 521–548) and
  `benchmarks/native-lifecycle/run_baseline.py` (86–106, 109–133, 136–162).
- `host_snapshot`: containment runner (187–205), native-lifecycle (65–83),
  native-query pair (38–61).
- `read_meminfo`: containment runner (149–155), native-lifecycle (39–46).
- `write_json`/`sha256`: containment runner, shared pair, lock microbench.
- `run_arm`: containment runner (2534–3221) and native-query pair (147–207).
- `main`: seven harness entrypoints each reimplementing CLI/plan/receipt flow.

### 3.3 Coupling and portability

- `m4_containment_runner.py` imports and validates a 3,000+ line policy at
  import time; constants and branches hardcode Linux `/proc`, `/sys/fs/cgroup`,
  btrfs qgroups, `systemd-run`, `CLOCK_BOOTTIME`, and `systemctl`.
- `run_shared_collection_pair.py` hardcodes `BASE`, `BUILD`, `BINARY`,
  `DICTIONARY`, `RESULTS` and a result-date path; it cannot run from a clean
  checkout without local `.work` artifacts.
- `run_native_query_lock_split_pair.py` hardcodes `/proc`, `df`, and a worker
  path; it is untracked and tied to the closed P3 investigation.
- `run_rust_lifecycle_pair.py` resolves test binaries by scanning
  `rust/target/debug/deps`; binary/library identity is captured in the receipt,
  but discovery is host-layout-dependent.
- Retrieval is already well-bounded: `core/` (suite, corpus, I/O, response),
  `metrics/`, `engines/zg/`, `reports/`; wrappers are thin. Keep it independent
  of M4 containment.

## 4. Target module boundaries

Proposed location: `benchmarks/harness-v2/` (shared) with M4 adapters under
`benchmarks/harness-v2/operations/m4/`; canonical retrieval remains upstream
`benchmarks/zg-retrieval/`.

```text
benchmarks/harness-v2/
  contracts/        # v1 policy reader, receipt/protocol schemas, golden fixtures
  host/             # host probe, cgroup v2, systemd driver, watchdog, cleanup
  measurement/      # clocks, samplers, artifact creation, receipt writer, stats
  operations/m4/    # rust_lifecycle, write_amplification, lock_microbench, diagnostics
  cli/              # thin entrypoints; one operation per command
```

Invariants:

1. **One containment path.** Every launch goes through `host/`; no runner calls
   `systemd-run` or writes cgroup files directly.
2. **One policy interpreter.** Operations declare required fields and allowed
   outcomes; core validates once. No per-operation `if purpose == ...` in core.
3. **Receipts are append-only contracts.** V2 writes v1-compatible fields plus
   an explicit `harness_version`; historical receipts are read-only.
4. **Refusal before launch.** Guard/telemetry/authorization failure produces a
   no-launch receipt and never creates a workload unit.
5. **Reproducibility identity.** Policy SHA, corpus manifest, binary/library SHA,
   host snapshot, and cgroup identity are mandatory receipt fields.
6. **Retrieval is separate.** No M4 policy/cgroup imports in `zg-retrieval`; no
   retrieval scoring in M4 operations.

## 5. Migration steps

### P0 — Freeze provenance (no behavior change)

- Snapshot SHA-256 for every harness file currently imported by a runner.
- Track `create_measurement_artifact.py` or move it to a tracked `legacy/`
  archive; do not leave a tracked runner importing an untracked module.
- Decide whether `benchmarks/native-lifecycle/` and P3 `run_native_query_lock_split*`
  are tracked history or archived. Do not delete receipts.
- Record one representative receipt per purpose as a golden fixture; never
  rewrite the originals.

### P1 — Contract layer and compatibility readers (implemented 2026-10-31)

- Implemented under `benchmarks/harness-v2/contracts/`; see
  `benchmarks/harness-v2/README.md` and `contracts/golden/manifest.json`.
- Extract policy v1 and receipt/protocol schemas into `contracts/`.
- Add a v1 receipt reader that accepts every golden fixture and returns
  normalized fields without changing files.
- Port `test_m4_benchmark_policy.py`, `test_m4_contention_policy.py`, and
  `test_m4_harness_scenarios.py` to the new contract layer.
- Gate: old receipt reader tests and policy/hash tests pass.

### P2 — Containment primitives (primitives extracted 2026-10-31; adoption partial)

- Implemented primitives under `benchmarks/harness-v2/host/` and
  `benchmarks/harness-v2/measurement/`; see `benchmarks/harness-v2/README.md`.
- Launch, cgroup path/cleanup, and the watchdog loop remain local until their
  monkeypatch-based legacy tests are ported in P3.
- Move host snapshot, cgroup discovery/validation, systemd launch, watchdog,
  quota attestation, and owned-cgroup cleanup into `host/`.
- Move `create_measurement_artifact` and clock/sampling/receipt helpers into
  `measurement/`.
- Replace duplicated functions in `run_baseline.py`, `run_shared_collection_pair.py`,
  and `run_native_query_lock_split_pair.py` with imports or archive those files.
- Gate: synthetic no-launch, guard-refusal, cleanup, watchdog, and
  telemetry-integrity tests pass; no orphan units; no limit changes.

### P3 — Lifecycle operation adapter (implemented 2026-10-31)

- Implemented under `benchmarks/harness-v2/operations/m4/rust_lifecycle/`:
  `config.py` (injected policy/containment dependencies), `context.py`
  (corpus, artifact identity, binary discovery, arm context), `preflight.py`
  (full authorization/resource preflight), `executor.py` (arm execution and
  finalization), `verify.py` (query-latency gate), and `receipt.py` (failure,
  pair, and native-diagnostic receipts).
- `run_rust_lifecycle_pair.py` is a CLI compatibility shim. Old command lines,
  operation IDs, receipt names, refusal paths, and public helper names remain
  valid.
- The monkeypatch-heavy legacy tests were ported to the split modules. The
  launch/watchdog boundary still belongs to the containment runner.
- Gate met: synthetic pair receipts expose the same semantic fields; the
  legacy and split paths agree on clean and refusal decisions; no benchmark
  run was needed.

### P4 — Operation families (registry, adapters, execution gate 2026-10-31)

- Implemented `operations/m4/registry.py` with a descriptor for every
  policy-owned section plus explicit `historical_only`/`unregistered`
  dispositions. Coverage validation fails closed on policy drift.
- Implemented receipt adapters for lock contention, write amplification, and
  diagnostics. They normalize status/evidence but never promote a receipt to
  performance evidence.
- `operations/m4/execution.py` is the reusable execution gate. It refuses
  historical/unregistered operations before launch and validates the receipt
  purpose against the descriptor.
- `lock_contention_microbench` remains active; the legacy runner stays the CLI
  execution owner until P6 archive, while V2 owns descriptor/receipt/gate.
- `immutable_write_amplification_pair` is `historical_only` and refuses
  execution until a tracked runner/provenance exists.
- Scalar-open receipts are `unregistered` because no top-level policy purpose
  exists; they cannot launch as V2.
- Remaining: P6 archive/retirement of legacy CLI entrypoints; containment
  execution remains owned by the legacy runner.

### P5 — Retrieval disposition (implemented 2026-10-31)

- Replaced `benchmarks/zg-retrieval` with the upstream copy, including
  compact-response parsing and the retrieval-only/PR/comment workflows and
  actions.
- Kept the `core/metrics/engines/reports` boundaries; no M4 imports.
- Gate: **185 retrieval tests pass / 0 fail**. Workflow tests normalize YAML
  line continuations introduced by repository formatting; semantic assertions
  are unchanged. Report/compare/CI entrypoints remain on frozen fixtures.

### P6 — Archive and document

- Move superseded prototypes and closed-research runners to
  `benchmarks/harness-v2/archive/` or a research archive, with a `README`
  stating status and last receipt.
- Update `research/m4-native/docs/benchmark-policy.md` to point to V2 modules;
  leave historical receipt directories untouched.
- No production defaults, thresholds, or authorization changes.

## 6. Acceptance criteria

### Measurement validity

- Every operation receipt has policy SHA, corpus manifest, binary/library SHA,
  host snapshot before/after, cgroup identity, telemetry completeness, and
  explicit evidence class.
- Refusal and incomplete-telemetry paths produce no-launch/incomplete receipts;
  they never produce a performance result.
- A v1 golden-fixture suite covers every historical purpose and passes with
  semantic field equality.

### Resource safety

- All launches pass through one containment module; no direct `systemd-run` or
  cgroup writes in operation adapters.
- Synthetic tests cover guard refusal, missing telemetry, cgroup ownership
  mismatch, watchdog timeout, cleanup failure, and no orphan user units.
- No threshold, cgroup limit, or authorization file is changed by the refactor.

### Reproducibility

- A clean checkout contains every imported module (`git ls-files`) and has no
  untracked runtime dependency.
- The same pinned corpus/binary/library/policy yields the same operation
  identity fields; host-dependent fields are explicitly marked.
- Retrieval reports are reproducible from frozen fixtures and golden tests.

### Structure

- No operation-purpose branching in containment core; each operation adapter is
  under ~250 lines and the shared `run_arm` equivalent is under ~200 lines.
- No duplicate `host_snapshot`, `frozen_manifest`, `find_test_binary`, or
  `extract_corpus` implementations outside `host/`/`contracts/`.
- Retrieval and M4 harness have no cross-imports.

## 7. Explicit non-goals

- No new benchmark, threshold change, cgroup change, or production rollout.
- No rewrite of retrieval scoring, metrics, or reports.
- No deletion of historical receipts or golden data.
- No change to the frozen RC branch.
- No reopening of M3-B, #10, or P3 optimization research.

## 8. Source evidence index

- `research/m4-native/tools/m4_containment_runner.py`: docstring; policy load
  lines 59–73; constants 350–360; `run_arm` 2534–3221; `watchdog_main`
  2091–2431; `validate_contention_context` 591–856.
- `research/m4-native/tools/run_rust_lifecycle_pair.py`: imports 17–18;
  `preflight` 290–518; `make_context` 159–265; `_run_pair_arms` 710–891.
- `research/m4-native/tools/create_measurement_artifact.py`: 1–125; untracked.
- `benchmarks/native-lifecycle/run_baseline.py`: 469 lines; `main` 239–461;
  duplicate helpers 39–162; directory untracked.
- `research/m4-native/tools/run_shared_collection_pair.py`: hardcoded paths
  17–23; `main` 228–360.
- `research/m4-native/tools/run_native_query_lock_split_pair.py`: untracked;
  `host_snapshot` 38–61; bootstrap 119–137.
- `research/m4-native/docs/benchmark-policy.json`: schema v1, per-operation
  contracts; `research/m4-native/docs/benchmark-policy.md`.
- `benchmarks/zg-retrieval/README.md`: code structure; upstream
  `engines/zg/run.mjs` `runRepository` 235–665; `reports/validation.mjs`
  `validateZgReport` 243–437.
- Fork/upstream retrieval diff: `git diff --stat e1f93d2..HEAD --
  benchmarks/zg-retrieval` shows the fork is 697 lines behind upstream.
