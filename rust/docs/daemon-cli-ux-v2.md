# Daemon/CLI UX V2

## Routing and wire compatibility

The default client mode is `auto`: a ready compatible daemon handles the operation; if no daemon is ready, indexing uses direct mode. `--daemon` and `--mode server` explicitly require the daemon. `--mode direct` deliberately bypasses it. A readiness/submit race or daemon protocol mismatch is returned as an error; the CLI never retries the same operation in direct mode. Daemon protocol capability 2 is required for this UX wire contract. Missing, older, and newer capability versions fail closed with guidance to use a compatible client or explicitly stop the daemon before restarting. Starting a client does not replace an incompatible daemon.

## Job control

- `zg --server jobs` lists queued/running jobs; `--all` includes retained terminal jobs; `--json` emits structured job data.
- `zg --server cancel <job-id>` requests cancellation for that job. A terminal or unknown ID is not cancellable.
- `zg --index --daemon --no-wait [ROOT]` submits a detached manual job and reports its ID. It does not wait or cancel the daemon job when the CLI exits.
- `zg --index --mode server --wait-timeout-ms N [ROOT]` submits a manual job and waits for at most N milliseconds. Timeout is a nonzero, actionable result; the job continues and can be inspected/cancelled with the commands above.

Job views report canonical root, manual/watch reason, queued/running/terminal state, observed file progress, elapsed wait, and an observed same-root blocker ID. Queue-full submission is rejected before a job is created. Retained history is bounded.

## Scheduling and persistence

Manual and watcher work use separate bounded concurrency lanes with a shared total scheduler capacity and bounded queue. Manual work has priority over a trailing watcher debounce. A same-root manual request supersedes a running watcher at the engine cancellation checkpoint; watcher changes are merged into the manual follow-up and watcher reconciliation resumes after completion. Per-user settings are bounded and validated:

```json
{
  "version": 1,
  "daemon": {
    "scheduler": {
      "manual_concurrency": 1,
      "watch_concurrency": 1,
      "queue_capacity": 64
    }
  }
}
```

One-operation scan settings, rebuild/reset controls, credentials, and remote consent are not replayed into future watcher jobs. The completed index summary reports full skipped counts and at most five sampled skipped paths. `results/` directories are excluded by the default scan policy. This UX change adds no storage/index format behavior.

Wait timeouts apply only to the CLI's wait, never to daemon work. Ordinary streaming foreground requests still propagate client cancellation to exclusive jobs; shared/coalesced work is not cancelled out from under other callers.

These changes do not alter workspace format version 2, native zvec version 0.8.0, FP16 behavior, or storage/engine pipelines.