# M4 Gate B — full-lifecycle preflight (2026-10-26)

## Status

**Gate B is open; no full-lifecycle measurement was launched and no performance conclusion is supported.** Gate A remains PASS for the opt-in research path only. Existing native-vs-M4 reports are not substitutes: their paired write-amplification workload does not call native `IndexStore::finalize_writes`/optimize and M4 `CompactNow`, and it does not exercise the current Rust FFI pipeline end-to-end.

This checkpoint preserves the current dirty working tree and uses only the committed source snapshot for workload planning. No benchmark, build, cleanup, policy edit, production change, or push was performed in this preflight.

## Source-backed comparison design

The appropriate comparison is the real `zg-engine` indexing and search path, not the standalone C++ benchmark:

- **Native arm:** `IndexStore` with the production `IndexStorage`/`SearchStorage` implementations, `NativeScanner`, and the same deterministic embedding runtime as the candidate.
- **M4 arm:** current `M4ResearchStorage`, retaining the same native `IndexStore` for vectors, metadata and hydration, while adding the sidecar, file-state manifests, durable pass intent, Rust FFI and M4 FTS search. This measures the incremental subsystem cost rather than replacing native components that M4 does not own.
- **Equivalent trace:** materialize an immutable corpus from committed `HEAD` blobs (not dirty worktree files); apply the same deterministic initial scan, updates, deletes/rename and repeated passes to each isolated arm. Run the same query/filter/hydration workload, including readers scheduled while mutations are in flight. Record pass/query/reopen/recovery latency, CPU, peak cgroup memory/PSS, process I/O bytes, artifact size/retention, correctness and retrieval parity.
- The current sidecar adapter initially lacked an FFI method for `Index::CompactNow`. A research-only `m4_rust_file_compact_now` C ABI/Rust `Adapter::compact_now` call and a deterministic publish→compact→reopen/search test have now been added. **They are source edits only: not compiled or executed** because the hard disk-reserve gate fails. Automatic flush may occur before explicit compaction; count the entire maintenance call and do not omit native data retained alongside the sidecar.
- The current test workspace driver holds a Tokio `Mutex` across indexing and queries, and the FFI adapter serializes calls through a C++ mutex. A workload must use the actual `M4ResearchStorage`/pipeline path and record the existing writer/query fence and reader serialization; it must not silently replace these with a faster test-only lock. Query tail/wait remains an architectural outcome.

### Frozen candidate corpus

From `git ls-tree -rl HEAD` at baseline commit `34d75aa14c760d302e497831c7501e1a31400f2b`:

- 421 tracked code files (`.rs`, `.cc`, `.cpp`, `.h`, `.hpp`, `.c`, `.py`), 7,070,189 bytes total.
- Extension totals: Rust 222 / 3,791,298 bytes; Python 179 / 2,125,781; C++ `.cc` 14 / 1,106,078; headers 6 / 47,032.
- Largest file: 386,650 bytes. Sorted path/blob-ID/size manifest SHA-256: `a4d53604bd03386ce0745e902d4099b39def0102753e584ff5312bfaab2d338b`.

The repository is a relevant real-code corpus, but one repository snapshot is not a broad corpus qualification. Freeze this exact manifest for a bounded first evaluation; do not include dirty worktree edits or generated `.work`, `.tmp`, `target`, vendored or third-party files. Existing scanner fragment limits and index output must be checked before any operation.

## Containment and launch decision

The current operation-policy hard reserve is 18,219,000,000 free bytes on `/`. Latest read-only `df -B1` reported 17,648,779,264 free: **short by 570,220,736 bytes**. Do not launch a measurement or consume this reserve by cleaning or relocating unrelated data.

The configured workspace attestation `/var/tmp/m4-native-workspace-quota-pwd.attestation.json` is root-owned and **148,295 seconds old** (maximum 86,400); it identifies filesystem UUID `6c15377e-2602-4870-96ba-50e58605309c`, subvolume 259 / qgroup `0/259`, 128 MiB cap, simple-quota accounting. Live `btrfs qgroup show` is denied with `Operation not permitted`, so current accounting/capacity cannot be independently verified. The authorized `.work/m4-native-workspace-quota` contains prior operation directories and is not empty. The supplied `provision_btrfs_simple_quota_workspace.sh --apply` refuses to alter a non-empty workspace; **do not run it or rewrite the attestation by hand**. No supported safe refresh path for this populated workspace was found.

Read-only `m4_containment_runner.py probe` confirms cgroup v2 and writable CPU/memory/swap/pids controls via the delegated user service, but this host exposes neither cgroup `io.stat` nor `io.max`; process `/proc` I/O, filesystem accounting and host I/O pressure can be recorded, but cgroup I/O attribution/isolation cannot be claimed. Current `MemAvailable` is below the historical threshold, but the working-tree policy/docs classify that threshold as **observational only**, not a launch gate. Do not misstate it as a blocker.

**No alternative is valid for Gate B right now:** (1) the policy-backed quota workspace would be the correct route, after its disk reserve and quota proof are restored; (2) the existing `/var/tmp` shared-collection profile is explicitly exploratory/non-qualifying and uses a different workload; (3) the existing C++ paired profiler does not cover the Rust pipeline, sidecar FFI, native retained state or equivalent full lifecycle. Do not switch roots or borrow another profile.

## Required recovery and next authorized steps

One root/admin intervention is needed before the execution can be made runnable: restore `/` free space to at least 18,219,000,000 bytes without agent deletion of unrelated data; verify the existing subvolume/qgroup, filesystem identity, simple-quota status and accounting; then provide a fresh root-owned attestation through a supported safe verifier for the **already populated** workspace. The current provisioning script is not that verifier. If no supported refresh route exists, the workspace owner must provide one without weakening any cap.

After that condition is met: compile and run the new `CompactNow` FFI regression, then implement/build the dedicated real Rust-pipeline lifecycle harness; run deterministic parity/recovery tests and the policy runner's no-launch validation. Only then launch a unique, quota-contained operation. Preserve the current workload, CPU/memory/swap/pids/runtime ceilings and all durable-ACK/failure guards; maximum three evidence rounds, stopping earlier if results settle. Every attempted operation consumes its identity, and a guard refusal is not retried. Record start/end host state, cgroup memory/CPU/process I/O telemetry, query distributions, disk retention, correctness/retrieval parity and cleanup receipts. A missing cgroup I/O controller is an explicit evidence limitation, not a reason to bypass containment.

## Decision

**Current Gate B status: BLOCKED before execution, not PASS/FAIL.** The already accepted research architecture remains **REWORK overall / not GO for production** until a representative matched lifecycle result and all remaining correctness/resource contracts are evaluated. No new owner-level architecture decision is needed yet; after measurements, compare CPU/work/latency/retention and availability tradeoffs rather than selecting a winner from isolated index timing.

Evidence checked: current policy (`benchmark-policy.json` and its working-tree edit), `m4_containment_runner.py probe`, live `df`/`/proc/meminfo`, attestation metadata, denied live qgroup query, tracked-HEAD inventory, `IndexStore::finalize_writes`, `M4ResearchStorage`, and the Rust bridge C ABI. No output from the prior codec branch or synthetic pair is upgraded to qualification evidence by this report.
