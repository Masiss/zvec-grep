# Native cross-process collection lock contract — 2026-10-21

## Result

`PASS` for the narrow native lock contract exercised by the research fixture:

```text
readonly_open_rejected code=INTERNAL_ERROR detail=Can't lock read-only collection: /home/masis/src/zvec-grep/research/m4-native/.work/tmp/m4-cross-process-lock-contract-20261021T165747Z/LOCK
native_cross_process_lock_contract=PASS read_only_while_writer_open=REJECTED read_only_after_writer_close=OPENED
```

The fixture creates a fresh native collection and holds a read-write handle in the parent process. A separate `exec`'d child process attempts `zvec_collection_open` with `read_only=true`; it is rejected while the writer is open. After the parent closes the writer and shuts down the native runtime, a fresh child successfully opens and closes the same collection read-only.

Reproduce with the research bridge target:

```sh
research/m4-native/.work/build/m4-stage-trace-bridge-test/m4_bridge_integration \
  "$PWD/research/m4-native/.work/tmp/<fresh-nonexistent-root>" \
  "$PWD/.local/native/zvec-native-profile/thirdparty/cppjieba/cppjieba-5.6.7/dict" \
  --native-readonly-lock-contract
```

Fixture source: `research/m4-native/tests/m4_bridge_integration.cc`, mode `--native-readonly-lock-contract`. Native lock implementation: `.local/native/zvec-native-profile/src/db/collection.cc`, `CollectionImpl::acquire_file_lock` (read-only uses a shared lock; writable collection uses an exclusive lock).

## Scope and architectural consequence

This is direct evidence that the current native API refuses a separate read-only process on the same collection path while a writer owns it; it is not a multi-reader stress test, a performance result, or proof of M4 lease safety. M4 `Index::Open` has no read-only option and currently creates/updates files on open (including the stats log and WAL) and runs garbage collection. Thus a process-local M4 snapshot lease cannot make the current mutable M4/native collection roots safe for independent reader processes.

The implemented application topology points to the safer fit: `rust/ARCHITECTURE.md` documents one resident daemon owning a reusable `Arc<ZvecGrep>` and serving CLI/MCP clients over loopback. `rust/crates/zg-engine/src/storage/read_session.rs` caches and leases native read handles inside that engine process; `rust/crates/zg-engine/src/workspace/lock.rs` coordinates cross-process workspace readers/writers with shared/exclusive file locks and drains remote read-cache residency before writer access. These are existing application mechanisms, not proof that the standalone M4 prototype is already integrated with them.

**Provisional architecture recommendation:** pursue a brokered shared M4/native collection inside that existing resident process, with process-local pinned snapshots/read leases and the application's outer cross-process workspace lock as the only storage-access boundary. Do not have client processes open the M4/native collection directly. This fits the implemented process model and avoids inventing an independent read-only M4 storage API; the completed pair also showed much lower shared writer commit time, but its reader tail was higher and the result is only one confounded sample. Keep immutable-shard as the fallback if stage-attributed shared-reader latency remains unacceptable. Separate immutable generations with true read-only opens plus cross-process reclamation leases remain an alternative only if direct process-level index access becomes a real requirement. No production integration or final architecture selection is made by this research-only checkpoint.

The first fixture attempt was an ordinary test setup defect: the test created the collection directory before calling the native create API, which requires a nonexistent target. The fixture was corrected to let the native API create the target; the final test above passed. No native-library or production source was modified.

## Verification limitation

Final focused Python regressions passed: benchmark policy (6 tests), pair receipt (4 tests), contention policy (42 tests); `py_compile` passed. This artifact otherwise records only the native fixture and its scope.

The full M4 bridge correctness/durability/crash suite passed 4,633 checks after reader-stage instrumentation and before this dedicated test mode was added. The added test mode was separately rebuilt and executed successfully. Its two owned temporary collection roots are removed after the fixture. No real A/B pair with the new per-reader stage metrics ran: its preflight was blocked before launch by `M4-CA-MEM-CAPACITY` (observed 3,495,800,832 B; required 4,294,967,296 B). Do not retry until a fresh preflight passes the unchanged hard gate.
