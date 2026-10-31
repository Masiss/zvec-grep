# Harness V2 archive

Frozen research material. Nothing here is a V2 entrypoint, is imported by
`benchmarks/harness-v2`, or is covered by production CI. Historical paths and
root calculations are preserved with the original files; do not run these from
the archive without reviewing those paths.

## native-lifecycle

- Source: former `benchmarks/native-lifecycle/` (untracked before P6).
- Status: frozen historical baseline, superseded by the Rust lifecycle V2
  adapter and `research/m4-native/tools/run_rust_lifecycle_pair.py`.
- Last evidence: the protocol JSON files in this directory and the lifecycle
  receipts under `research/m4-native/results/`; no new run is authorized.
- Reason: duplicated containment primitives and a p1-only protocol.

## native-query-lock-split

- Source: former untracked `research/m4-native/tools/run_native_query_lock_split_*`.
- Status: closed research-only P3 runners. Never an M4 production/qualification
  entrypoint.
- Last evidence: `research/m4-native/results/native-query-timeline-20261029/`
  (`native-query-lock-split-ddl-crash-20261030`,
  `native-query-lock-split-crash-loop-20261030`,
  `native-query-lock-split-multihost-20261030`).
- The shared helper `native_query_lock_split.py`, its test, and the schema-lock
  trace builder remain in `research/m4-native/tools/` as frozen support code.

## prototypes

- `contained_loader_prototype.py`, `sealed_unit_prototype.py`, and their tests.
- Status: synthetic experiments only; no receipts, no launcher, no identity
  guard. Not part of the policy or V2 execution boundary.
- Reason: superseded by the containment/host primitives and P4 execution gate.

## What stayed active

`create_measurement_artifact.py`, observer/qgroup fixtures, the scalar-open
diagnostic, quota-attestation refresh, and their tests remain tracked under
`research/m4-native/tools/`. They are policy-referenced or lifecycle
dependencies; they are not archived.
