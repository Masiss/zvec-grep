# Daemon/CLI UX V2 closure

Status: **feature-complete on `work/zg-optimization`**. This is a post-RC
track; the frozen `rust-v0.3.0-rc.2` branch is unchanged.

## Closed features

- UX0: daemon index job visibility (`zg --server jobs`, queued/running state).
- UX1: default auto routing to a ready daemon, visible `route:` output,
  explicit `--daemon`/`--mode direct`, and `--no-wait`.
- UX2: same-root manual work supersedes an active watcher at a safe checkpoint;
  watcher batches are preserved/coalesced and resume after manual success,
  failure, or cancellation.
- UX3: configurable manual/watch lanes, queue position/priority/cancelability,
  explicit queue-full failure, and `--wait-timeout-ms` that stops only the
  client wait.
- UX4: saved scan policy is not replayed from one-operation overrides; skipped
  counts and bounded samples are reported in the index summary; `results/` is
  a default ignored directory.

## Regression limitations

These are known gaps, not hidden failures:

1. **Daemon readiness/submit race is not directly regression-tested.** A
   daemon can disappear after `server_status` reports ready but before
   `submit_index_job`. Current behavior is fail-closed: the submit error is
   returned and the CLI does **not** retry direct, so it cannot silently create
   a second writer. A deterministic test needs a proxy/test seam between status
   and submit.
2. **Wait timeout is client-side.** `--wait-timeout-ms` drops the client wait
   only; the daemon job continues. There is no daemon-side deadline or
   automatic cancellation by design.
3. **Queue-full is reject-only.** It returns `resource_busy` before creating a
   job; there is no retry/backoff.
4. **Saved watcher scope is tested at the boundary, not end-to-end filtering.**
   The regression asserts watcher jobs do not replay transient scan overrides;
   actual file filtering remains the engine/manifest policy's responsibility.
5. **Real-watcher integration is best-effort.** The integration test starts a
   real daemon/watcher, writes before/after files and runs a default manual
   rebuild, but does not deterministically hold a watcher in the running state
   to observe the cancellation transition.
6. **Cancellation checkpoints are engine-owned.** Scheduler tests use an
   executor that honors the cancellation signal; the exact safe-point set for
   every native optimize stage is not enumerated.
7. **Direct-vs-daemon exclusion is proven at the home-lock boundary.** The test
   shows both paths contend on the same `locks/home` write lock; it is not a
   two-process soak test.
8. **No production qualification.** No 421-file/production benchmark, no
   performance claim, and no RC change is implied by this closure.

## Performance baseline and bottleneck

Bounded release baseline is recorded in `ux-v2-performance-baseline.md`:
warm default-auto daemon indexing ~0.27 s, direct indexing ~0.55 s (287 MB RSS),
`--server jobs` and `--no-wait` ~10 ms or less. The control plane is not the
bottleneck; direct process/engine initialization is. No UX V2 optimization was
made because the evidence does not justify one.

## Next step

Feature work is closed. Any further work must target the measured direct-path
engine/model startup with its own authorization and benchmark. Reopen UX V2 only
for a concrete regression or production qualification request.
