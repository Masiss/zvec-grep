# M3-B handle-based maintenance — feasibility assessment

Status: **GO for the Rust-side design, with mandatory native gates.** Not an
implementation, not a benchmark, not a production/default change. This document
decides whether directories/entities/files `optimize()` can run outside
`IndexStore`'s state lock using stable collection handles, and lists the focused
regression needed before implementing it.

Evidence reused (no bottleneck re-research):

- P2 stage attribution and the rejected 4-thread experiment:
  `work/zg-optimization docs/technical/optimization-native-profile-20261027.md`.
- Phase 3 native lock-split research (research-only):
  `work/zg-optimization docs/technical/optimization-upgrade-plan-20261027.md`
  §“Cập nhật 2026-10-30”.
- Fork M3-B handoff, Gate H/I/K and the production lock split:
  `work/zg-optimization rust/docs/m3b-handoff.md`.
- Rejected overlap measurement: `BENCHMARK-RESULTS.md`, “M3-B overlap
  candidate”.

The M4 P3 architecture decision remains **REWORK / production NO-GO**
(`work/zg-optimization research/m4-native/results/M4-P3-ARCHITECTURE-DECISION-20261030.md`).
This assessment does not reopen it and does not use it as a production basis;
only the Phase 3 native lock-split diagnostic is used as evidence that native
lock windows can exist inside optimize.

## 1. Why this direction

- P2: fragment optimize is 58.4% of finalization, file 23.6%, entity 10.5%,
  checkpoint 7.3%. The 4-thread A/B was rejected (query p95 320 ms). The
  bottleneck is finalization, not M2.
- M3-B overlap (rejected): running scalar optimize **under the state lock** while
  fragments optimize outside shortened mutation wall (~1,083 → 894 ms) but
  stretched the state-lock window from ~271 to ~362 ms and raised concurrent
  query p95 from ~275 to ~360 ms. The reader-visible cost is the lock window,
  not the total optimize.
- Therefore the only useful next step is to remove the **scalar** native
  optimize from under the state lock, not to overlap more work under it.

## 2. Can stable handles be taken under the state lock?

**Yes, but only with shared ownership, not raw handles.**

| Fact | Evidence |
|---|---|
| `Collection` owns a raw `*mut zvec_collection_t`; `Drop` calls `zvec_collection_close`; there is no `Clone` impl. | `zvec-rust-0.8.0/src/collection.rs:117`, `:680`. |
| `Collection` is `unsafe impl Send/Sync`; the C API documents internally thread-safe collection operations. | `zvec-rust-0.8.0/src/collection.rs:698-699`. |
| `as_raw()` is explicitly unsafe to use after the owning `Collection` is dropped. | `zvec-rust-0.8.0/src/collection.rs:126-130`. |
| Integration tables currently own `Collection` directly. | `storage/files/mod.rs:28-31`, `entities/mod.rs:18-20`, `directories/mod.rs:23-30`. |
| Integration fragments are already behind `Arc<Fragments>` and are cloned out of the state lock. | `storage/store/mod.rs:81-112`, `finalize_writes_detailed` `:553`. |
| The fork implemented exactly this with `Arc<Collection>` + `handle()`. | `abaf7bf storage/{files,directories,entities}/mod.rs` (`Arc<Collection>`, `handle()`), `fragments/mod.rs:39,305`. |
| Gate K verified the production `IndexStore` lock split: search served during optimize, debt preserved, close waits, failure recoverable, crash/reopen. | `work/zg-optimization rust/docs/m3b-handoff.md` §5–§6. |

A `Collection` handle can only be checked out safely as `Arc<Collection>`:
the `Arc` keeps the native handle alive after the state lock is released and
across a concurrent `close()`/`SharedStore` drop. Keeping a raw pointer or
reopening the same collection path is not acceptable.

Minimal ownership change: `Files.collection`, `Directories.collection`,
`Entities.collection` become `Arc<Collection>` with `handle() -> Arc<Collection>`.
`Files.ids` and `Directories.ids` are not used by `optimize()`; they stay under
the state lock and are unchanged. Fragments can stay as `Arc<Fragments>` (already
stable) or move to per-collection `Arc` handles for uniformity.

## 3. Minimal design

Lock order (must be invariant): **maintenance → state**, never the reverse. The
per-store `IndexStore.shared` lease is separate and must not be held while
waiting for maintenance.

### Phase A — state lock held

1. Reject `closed`.
2. Keep the existing debt test (`needs_optimize || fragments_pending || dirty`).
3. Set `needs_optimize = true`, `optimizing = true`; persist
   `optimize.pending` before any native work.
4. Snapshot `dirty`, `write_generation`, and clone the required handles:
   `Arc<Collection>` for directories/entities/files, `Arc<Fragments>` (or its
   handles) for fragments.
5. Release the state lock.

### Phase B — no state lock, maintenance lease held

1. Take `SharedStore.maintenance` (new `Mutex<()>`).
2. Run native `optimize()` on the checked-out handles. Preserve the current
   stage order to isolate this change: fragments first, then the existing
   parallel directories/entities/files stage. Do **not** add thread-policy or
   overlap changes in the same candidate.
3. Search and writers proceed concurrently. This relies on the native
   optimize/query/write concurrency guarantee (see §5).
4. Record per-stage durations (the `index_optimize_*` telemetry already exists)
   and the first failure.

### Phase C — state lock reacquired

1. If `closed`, do not mutate debt or the marker; return (recovery happens on
   reopen).
2. If optimize failed or `write_generation` changed, keep the marker and the
   dirty/`needs_optimize` debt for the next finalize.
3. Only if the generation is unchanged and all stages succeeded: remove
   `optimize.pending`, clear `needs_optimize`/`dirty`.
4. Clear `optimizing`.

### Close

`close()` must acquire the `maintenance` lease before setting `closed` and
flushing, so a native optimize and a flush/close cannot overlap. The existing
wait loop on `optimizing` is not sufficient by itself: it releases the state
lock before taking the store lease, so a finalize can start in that window
(`store/mod.rs:646-668`). If close wins the lease before Phase B, Phase C's
`closed` guard preserves the marker and recovery remains safe.

## 4. Generation, debt, marker, crash recovery

- The marker is written under the state lock before Phase B and removed only in
  Phase C after a successful, generation-unchanged run. A crash between those
  points leaves the marker; `open` already seeds `needs_optimize`/`dirty` from
  it (`store/mod.rs:228-267`), so the next finalize retries.
- `write_generation` is bumped by accepted replacements/deletions
  (`store/mod.rs:516,780`). A write during Phase B changes it, so Phase C must
  not acknowledge; the new write keeps `dirty` and the marker for the next run.
- A failed stage returns the error, keeps the marker and debt, and resets
  `optimizing`; the next finalize retries. No retry loop is introduced.
- No schema/storage-format change; this is lifecycle-only.

## 5. Concurrent read/write/close and native limits

- **Reads/writes:** with the state lock free during Phase B, readers/writers take
  the state lock and call native query/write on the same collection. This is safe
  only if the pinned native library allows optimize alongside query/write.
- **Evidence:** fork Gate H/I proved query and insert/update/delete during
  optimize correct on **zvec 0.7.2** (`m3b-handoff.md:24-26,127,220-221`).
  Integration pins **zvec 0.8.0** (`rust/Cargo.toml:63`); the 0.8.0 Rust wrapper
  asserts internal thread safety, but the 0.7.2 source-level Gate H/I evidence
  is not automatically a 0.8.0 qualification.
- **Native seal window:** Phase 3 research isolated a real native window where
  flush/optimize hold an exclusive schema lock during the seal, blocking FTS-only
  queries until the lock-split prototype (`optimization-upgrade-plan-20261027.md`
  §“Cập nhật 2026-10-30”). That work is **research-only / QUALIFIED_DIAGNOSTIC**
  and does not qualify the prebuilt 0.8.0 library. Removing the Rust state lock
  may therefore leave a shorter native seal stall; this must be re-measured, not
  assumed away (see §6).
- **Close:** the maintenance lease is the only correct serialization; without
  it a flush/close can race a native optimize. `Arc` keeps handles alive, but it
  does not serialize operations.
- **Deadlock:** maintain `maintenance → state`; `optimize_dirty` must not take
  the maintenance lease, and Phase C must run after the lease is released.

## 6. Mandatory gates before adopting

1. **Native concurrency on pinned zvec 0.8.0:** a focused gate equivalent to
   Gate H/I on this branch (query + insert/update/delete during optimize;
   crash/close during optimize). If 0.8.0 differs from 0.7.2, stop and decide
   whether a native dependency change is allowed.
2. **Native seal latency:** measure reader p95 during optimize on 0.8.0. If the
   native seal still blocks readers for the target window, the Rust-side change
   alone is insufficient; do not claim the 300 ms target.
3. **Focused Rust regressions** (below) pass.

This is why the verdict is GO for the design, not GO to ship without those gates.

## 7. Focused regression required

Existing coverage that stays: `fragment_optimize_releases_the_store_state_lock_for_readers`,
`fragment_optimize_allows_concurrent_writes_and_queries`,
`close_waits_for_in_flight_fragment_optimization`,
`fragment_optimize_failure_preserves_marker_and_retries_after_reopen`,
`dirty_optimize_failure_preserves_marker_and_retries_after_reopen`,
`finalize_reports_per_stage_optimize_telemetry`.

New tests (test hooks must be thread-safe/global because maintenance runs on
scoped workers):

1. `scalar_optimize_releases_state_lock_for_readers` — delay a scalar
   `optimize()`; `list_files()` and a FTS query complete while it is active.
2. `scalar_optimize_allows_concurrent_writes_and_queries` — accept a write and
   query during scalar optimize; the write survives and its debt is preserved.
3. `scalar_optimize_generation_change_preserves_marker` — write during optimize;
   Phase C must not remove `optimize.pending`; a second finalize clears it.
4. `close_waits_for_in_flight_scalar_optimization` — maintenance lease held;
   close blocks, then flushes; optimize never overlaps flush/close.
5. `close_race_before_maintenance_lease_preserves_recovery` — Phase A finishes,
   close wins the lease, Phase C sees `closed`; marker remains and reopen retries.
6. `scalar_optimize_failure_retries_after_reopen` — injected failure keeps the
   marker; reopen + successful finalize clears it and queries remain correct.
7. `handle_keeps_collection_alive_across_close` — checkout handles, close, then
   drop; no use-after-free, and the store's OS lock is not released early.
8. `crash_marker_between_phase_a_and_c_reopens` — marker present at open seeds
   debt, and the next finalize succeeds.
9. `no_deadlock_finalize_reads_writes_close` — bounded stress: finalizer, reader,
   writer and close in parallel; all complete, no timeout.
10. Ignored native gate (not a normal unit test): concurrent query/write during
    optimize on the pinned library, with close/reopen and crash-marker checks.

## 8. Verdict

**GO** for implementing handle-based maintenance with `Arc<Collection>` plus a
maintenance lease, using the Phase A/B/C design above.

**Not** GO to merge or benchmark-claim until the pinned zvec 0.8.0 concurrency
and native seal window are verified. If those fail, the fallback is **REWORK**
(a native dependency/lock-split decision), not returning to the rejected
fragment/scalar overlap.

Do not: increase optimizer threads, overlap native maintenance with scalar
optimize under the state lock, cherry-pick `b4b8686` wholesale, change
production defaults, or run the 421-file campaign from this assessment.
