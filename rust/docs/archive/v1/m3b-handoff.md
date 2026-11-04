# M3-B Hand-off — Lock isolation + bounded deferred native maintenance

Updated: 2026-10-17. **Step 1 (lock isolation) and Step 2 (bounded deferred
maintenance) are implemented, correctness-verified, and deployed to the local
daemon; a HIS_EMR-scale canary observation is still pending.** No workspace
schema migration, generation-architecture change, or search-semantics change was
made. Production business sources were never touched.

This document is the operating + engineering hand-off for the storage
maintenance work. It complements `ARCHITECTURE.md` (system layout) and
`docs/m1-handoff.md` (M1/M2/M3-A). Read this before changing
`crates/zg-engine/src/storage/`.

---

## 1. Problem statement

On HIS_EMR a single fragment insertion can trigger `fragments.optimize()`
taking **67–98 s**. Investigation established:

- The dominant foreground impact is **not CPU priority**; it is the Rust
  `Mutex<StoreState>` being held across the whole native optimize, which blocks
  **all searches** (and writers) for the optimize duration.
- Native zvec (0.7.2) runs optimize in three phases; **phase 2 is lock-free and
  safe alongside concurrent query and mutation** — verified in source and by
  Gate H/I. The freeze came from the Rust-side mutex, not native synchronization.
- Eagerly optimizing on every job also repeats work: total CPU/wall/I/O scale
  with the number of optimize runs.

Two independent deliverables:

- **Step 1 — Lock isolation:** run native optimize outside `Mutex<StoreState>`
  so search/writers stay available; keep checkpoint/publish semantics and eager
  policy unchanged.
- **Step 2 — Bounded deferred maintenance:** stop optimizing on every job;
  start maintenance when fragment debt crosses a threshold, an age deadline
  passes, or a deletion forces it. Opt-in, default eager.

---

## 2. Current deployed state (2026-10-17)

| Item | Value |
|---|---|
| Daemon args | `zg --server run --mcp-toolset agent --listen 127.0.0.1:7999` |
| `ZVEC_GREP_HOME` | `/home/masis/.zvec-grep` |
| `ZVEC_GREP_MAINTENANCE_THRESHOLD` | `50` (Step 2 experimental value) |
| Running binary (audit-fixed) | `sha256 f71a0a72…` |
| Rollback — Step 2 pre-audit | `rust/target/rollback/zg.step2_pre_audit.bin` (`fd541182…`) |
| Rollback — Step 1 | `rust/target/rollback/zg.step1.bin` (`3cb2249a…`) |
| Logs | `/home/masis/.zvec-grep/daemon/logs/server.log` |
| Runner stdout | `/home/masis/.zvec-grep/daemon/logs/deploy.stdout.log` |

Runtime lock-isolation on the live daemon is **NOT OBSERVED** yet (no natural
optimize during the observation window). Do not claim runtime concurrency PASS
until a real optimize is observed with concurrent search.

---

## 3. Architecture and state ownership

### Locks (lock order: `maintenance` → `state`; never reversed)

- `IndexStore.shared: Mutex<Option<Arc<SharedStore>>>` — per-store lease; `shared()`
  clones the `Arc` and releases immediately.
- `SharedStore.state: Mutex<StoreState>` — the single global state lock that
  search (`read()`) and writers (`write()`) use.
- `SharedStore.maintenance: Mutex<()>` — maintenance lease. Held only across the
  native optimize phase, and by `close()`.

Invariants (audited, see §7):
- No path holds `state` then acquires `maintenance`.
- `finalize_writes` never holds `state` while running native optimize.
- `close()` acquires `maintenance` **after** releasing the lease so it does not
  hold `shared` while waiting.

### Collections

Each of `files/ entities/ directories/` holds `Arc<Collection>`; `fragments/`
holds `BTreeMap<String, Arc<Collection>>`. The `Arc` lets `finalize_writes`
check out handles and run optimize without the state lock; it also keeps the
native handle alive across concurrent `close()` (no use-after-free). `Arc` does
**not** protect on-disk generation files — that safety comes from the
service ordering `retire → close → publish` plus the registry refusing a
read-only session while a writer is open.

### Maintenance debt model (`StoreState`)

- `finalize: FinalizeState` — bit flags per collection (`DIRECTORIES`,
  `ENTITIES`, `FILES`, `FRAGMENTS`).
- `mutation_epoch: u64` — bumped on every `mark_finalize` call; used for the
  "nothing mutated during optimize" acknowledgement guard.
- `fragments_debt: u64` — bumped **only** when the `FRAGMENTS` bit is marked
  (fragment-specific; metadata-only changes do not count).
- `fragments_ack_epoch: u64` — `fragments_debt` value acknowledged by the last
  completed maintenance run; policy debt = `fragments_debt − fragments_ack_epoch`.
- `pending_since_ms: u64` — wall-clock start of the current pending debt
  (0 = none).
- `fragments_delete_pending: bool` — a deletion awaits native optimize even if
  completeness is 1.0.
- `closed: bool`.

### Durable deletion marker

`<workspace>/storage/maintenance.delete` — persisted intent so pending delete
maintenance survives restart. Written with the same fsync-file + fsync-directory
publish as the workspace manifest (`utils::atomic_write`), **before** any
tombstone is flushed; cleared only on an acknowledged maintenance run. Recovery
on reopen: marker present ⇒ `fragments_delete_pending = true` + seed
`pending_since_ms`; else if `fragments.needs_optimize()` (completeness < 1) ⇒
seed `pending_since_ms` (insert-debt recovery).

---

## 4. Step 1 — Lock isolation

### Design

`finalize_writes` runs in three phases:

- **Phase A (state lock held):** checkpoint, probe `needs_optimize()` and
  completeness, copy `FinalizeState`, capture `mutation_epoch` /
  `fragments_debt`, and **check out `Arc<Collection>` handles** for the
  collections to optimize. Release the state lock.
- **Phase B (state lock released, `maintenance` lease held):** run native
  `optimize()` on the checked-out handles. Search and writers proceed
  concurrently (native phase 2 is lock-free; Gate H/I).
- **Phase C (state lock reacquired):** acknowledge only the exact debt that was
  optimized (§5), reset `mutation_stats`, log.

### Files

- `crates/zg-engine/src/storage/store/mod.rs` — `finalize_writes`, `close`,
  `mark_finalize`, `FinalizeState`, `StoreState`, `SharedStore`.
- `crates/zg-engine/src/storage/{files,entities,directories}/mod.rs` — `Arc<Collection>`, `handle()`.
- `crates/zg-engine/src/storage/fragments/mod.rs` — `Arc<Collection>`, `handles()`, `handles_completeness()`.
- `crates/zg-engine/src/storage/store/tests.rs` — Gate K acceptance.

### What must stay true

- Optimize runs **outside** `Mutex<StoreState>`.
- `close()` waits on the `maintenance` lease before setting `closed`/flushing.
- `Arc` keeps native handles alive; do not downgrade collections back to owning
  `Collection` values.

---

## 5. Step 2 — Bounded deferred maintenance

### Policy

`MaintenancePolicy { threshold, max_age_ms }` read per finalize from env:

- `ZVEC_GREP_MAINTENANCE_THRESHOLD` (default **0 = eager**; K=50 is the
  experimental canary value, never a universal default).
- `ZVEC_GREP_MAINTENANCE_MAX_AGE_MS` (default 600000).

Decision for `fragments` (in Phase A):

```
wants_fragments   = finalize.has(FRAGMENTS) || fragments_incomplete
debt              = fragments_debt - fragments_ack_epoch
aged              = pending_since_ms != 0 && now - pending_since_ms >= max_age_ms
run_fragments     = wants_fragments
                    && (fragments_delete_pending
                        || threshold == 0
                        || debt >= threshold
                        || aged)
deferred          = wants_fragments && !run_fragments
```

- **Delete-forced:** `fragments_delete_pending` always forces optimize (tombstones
  leave the HNSW search path only after optimize, even at completeness 1.0).
- **Deferral:** keeps `finalize` flags, starts `pending_since_ms`, performs no
  optimize at all this round.
- **Deadline is best-effort, not a real-time timer:** `aged` is evaluated only
  inside `finalize_writes`. With no further job after a project closes, debt can
  persist beyond `max_age_ms` and runs at the next job/open. A true hard
  deadline needs a daemon-scope maintenance owner (not implemented; see §9).

### Acknowledgement (Phase C)

`acknowledged = optimize_succeeded && mutation_epoch unchanged && fragments_clean`.
When acknowledged: clear `finalize`, set `fragments_ack_epoch = snapshot debt`,
reset `pending_since_ms`, clear `fragments_delete_pending`, remove the marker.
If not acknowledged (new mutation during optimize, or optimize failed/incomplete):
keep the debt flags for the next round; a new deletion marker is written only by
a later checkpoint.

- **Failure recovery:** a failed native optimize keeps the debt and returns the
  error; the next `finalize_writes` retries (no retry storm — retries are driven
  by jobs, not a loop).
- **Close during maintenance:** `close()` waits on the `maintenance` lease, so
  native optimize always completes before close flushes/closes. If `close()`
  wins the race to set `closed` before Phase C, Phase C skips acknowledgement
  (guard) so no debt/marker is mutated after close; the debt is recovered on
  reopen.

### Environment knobs (native, unchanged defaults)

- `ZVEC_GREP_NATIVE_THREADS` (default 2) — query+optimize thread pool.
- `ZVEC_GREP_OPTIMIZE_THREADS` — research knob to override the optimize pool
  only. **Not** used in production; Gate H2 showed threads=1 doubles wall time
  without improving foreground responsiveness.

---

## 6. Correctness + performance gates (what each proved)

| Gate | Proved | Where |
|---|---|---|
| A | checkpoint-before-optimize is searchable + recoverable | `store/tests.rs::gate_a_*` |
| B | 100 pending commits searchable before optimize | `gate_b_*` |
| C | repeated checkpoints + one optimize converge | `gate_c_*` |
| E | eager vs deferred A/B on fixture (wall/CPU) | `gate_e_*` (ignored) |
| F | bounded-debt sweep K=1…1000: K≈25–50 sweet spot, per-optimize amplitude ~constant | `gate_e_a_b_*` (ignored) |
| G | foreground latency under optimize; affinity/nice weak, `ionice` ineffective on `none` scheduler | scripts in session logs |
| H | native concurrency SAFE (query during optimize); Rust mutex froze search (= optimize wall); optimize threads 1 vs 2 | `gate_h_*` (ignored) |
| I | concurrent insert/update/delete during optimize correct; native writers pass phase 2 | `gate_h_i_*` (ignored) |
| J | test-only lock-split + epoch/ack prototype | `gate_j_*` (ignored) |
| K | production `IndexStore` lock split: search served during optimize, writer debt preserved, conditional ack, close waits, optimize failure recoverable, crash/reopen, memory retention | `gate_k_*` (ignored) |
| Step 2 | defer/threshold/deadline/delete-forced/reopen/metadata-only/marker-immediate/**fail-safe**/**restart-recovery** | `step2::*` (ignored) |
| Audit | F2 witness (not a real bug), F6 witness + close-race recovery | `audit::*` (ignored) |

Performance A/B (fragment-specific debt, 300 mutations / 60 batches):
eager `optimizes=60 wall≈54 s` vs deferred-K50 `optimizes=6 wall≈20 s`,
`VmHWM ≈450→423 MB`, search p95 `75µs` (eager) vs `228µs` (deferred, still
sub-ms). Storage suite: **114 passed / 0 failed**.

---

## 7. Engineering audit findings (post-M3-B)

Confirmed and **fixed**:
- **F3 — `hard_bound` dead logic:** `hard_bound = threshold*10 > threshold`, so
  `debt >= hard_bound` was unreachable. Removed.
- **F6 — Phase C can run after `close()`:** proven by witness; added a guard
  (`if state.closed { skip acknowledgement }`) and a close-race recovery test.
- **F8 — dead store-level `optimize()`** on files/entities/directories/fragments:
  removed.
- **F9 — duplicate time helper:** removed `now_epoch_ms`, use `wall_clock_ms`.
- Test-only hook: override sentinel now `u64::MAX` = unset, so `threshold=0`
  means eager in tests.

Investigated, **not changed** (kept as trade-offs):
- **F2** (metadata-only mutation blocking ack ⇒ redundant fragment re-optimize):
  could not be reproduced with a real path — a concurrent `replace_file` genuinely
  leaves the fragment index incomplete, so the extra optimize is required.
  Conservative ack by `mutation_epoch` retained.
- **F1/F4** (multiple debt fields / delete bool vs bitflag): not simplified just
  for state count.
- **F5** (durable `maintenance.delete` marker): kept, required for delete recovery.
- `index_job` tracing span: kept (diagnostic value for spikes).

Cosmetic, pending (low value): **F10** dedupe finalize logging branches,
**F11** stringly-typed reason fields. Benign release warning:
`DEFAULT_MAINTENANCE_THRESHOLD` is only used in the `cfg(test)` override.

---

## 8. Operations

### Build + targeted tests

```sh
cd rust
TMPDIR=$PWD/target cargo test -p zg-engine --lib storage::      # 114 pass
cargo build --release --bin zg
```

### Deploy (controlled)

```sh
export ZVEC_GREP_HOME=/home/masis/.zvec-grep
# 1. verify quiescent: cpu tick delta ~0 and server.log idle
OLD=$(pgrep -f 'zg --server run' | head -1)
# 2. save rollback (real file copy, NOT cp -a of /proc/pid/exe which symlinks)
cp -L /proc/$OLD/exe rust/target/rollback/zg.<label>.bin
# 3. graceful stop
rust/target/release/zg --server off        # wait for exit; SIGTERM/SIGKILL only if it lingers
# 4. start new (same args/env, add threshold for Step 2)
ZVEC_GREP_MAINTENANCE_THRESHOLD=50 setsid nohup \
  rust/target/release/zg --server run --mcp-toolset agent --listen 127.0.0.1:7999 \
  >> ~/.zvec-grep/daemon/logs/deploy.stdout.log 2>&1 &
# 5. smoke: ss -ltn | grep 7999 ; zg <query> in an indexed workspace
```

### Rollback

```sh
cp rust/target/rollback/zg.<label>.bin rust/target/release/zg
# restart with the same args/env; never delete or rebuild an index to roll back
```

`--server off` triggers a graceful stop; if the process lingers without
listening, `SIGTERM` then `SIGKILL` (it is quiescent). Do not rely on `kill -0`
alone — a lingering process may already have released the port.

### Observability

- `~/.zvec-grep/daemon/logs/server.log` (JSON tracing).
- Every index job is wrapped in an `index_job` span with `job_id` + `root`;
  `store/mod.rs` logs `index storage finalization` /
  `... deferred native optimize` / `... skipped native optimize` with
  `acknowledged`, `epoch_unchanged`, `debt`, `threshold`, `max_age_ms`,
  `defer_reason`, `fragments_completeness_before/after`.
- Pending deletion marker: `<workspace>/storage/maintenance.delete`.

---

## 9. Known limitations / open items

1. **Deadline is best-effort**, not a real-time timer. Closing a project with
   pending debt defers maintenance to the next job/open. Implement a
   daemon-scope maintenance owner (separate approval) if a hard guarantee is
   required; it must avoid idle-time spikes.
2. **Insert-debt restart recovery** relies on native `completeness < 1` plus the
   seeded age deadline; only delete-debt is marker-persisted.
3. **Runtime lock-isolation on the live daemon NOT OBSERVED**; needs a real
   optimize with concurrent search in the window.
4. **F2** unproven; conservative ack retained.
5. **HIS_EMR-scale canary** not run; thresholds/wall on HIS_EMR are unmeasured.
6. F10/F11 cosmetic; benign release warning.

---

## 10. Must-keep mechanisms (do not remove as "complexity")

- Lock split via `Arc<Collection>` + optimize outside `Mutex<StoreState>`.
- Maintenance lease + `close()` waiting for maintenance.
- Epoch/ack: `mutation_epoch` (guard) + `fragments_debt/fragments_ack` (policy).
- `fragments_delete_pending` + durable fsync'd marker (delete/tombstone recovery).
- Delete-forced optimization; opt-in deferral (default eager).
- Gate A/B/C/K invariants (checkpoint-before-optimize, reopen, crash, close).

---

## 11. Suggested cleanup plan (each commit independently rollback-able)

1. **No-behavior:** remove dead metrics/reasons, dedupe finalize logging (F10),
   enum reasons (F11), drop the unused `DEFAULT_MAINTENANCE_THRESHOLD` warning.
   Guard: `storage::` 114 + `step2` + Gate K.
2. **Tests:** move Gate E–K / step2 harnesses to a feature-gated or integration
   module; consolidate test hooks into one `#[cfg(test)] TestHooks` struct.
   Guard: compile + explicit `--ignored` runs unchanged.
3. **Debt model (only if justified):** unify debt fields into one `FragmentDebt`
   and fold `fragments_delete_pending` into a `FinalizeState` bit. Guard:
   `step2` + Gate K + audit witnesses.

---

## 12. Session artifacts not committed

The working tree carries all M2/M3-A/M3-B changes uncommitted. Gate harnesses
(`gate_e…gate_k`, `step2`, `audit`) are `#[ignore]` and require
`LD_LIBRARY_PATH` to the debug `libzvec_c_api.so` and a large-filesystem
`TMPDIR` when run; heavy ones need an external `timeout` watchdog.
