# Daemon/CLI UX V2 — manual index priority and queue visibility

Status: **proposed / not implemented**. This is a post-RC track. Do not change
the frozen `0.3.0-rc.2` branch for this work.

## Problem

The resident daemon and CLI currently have a confusing interaction for manual
indexing:

- `zg --index` may be routed to the daemon without making that routing obvious.
- The daemon scheduler defaults to **2 concurrent index jobs** and a **64-entry
  queue**.
- A second request for the **same workspace** becomes a follow-up behind the
  active job; `--rebuild` cannot start until that job completes.
- A manual rebuild can therefore wait behind a long watcher/full-scan job while
  the CLI shows no queue position or phase. The client process may sit at 0%
  CPU, which looks like a hang.
- Manual and watcher jobs have no priority distinction, and there is no
  operator-facing job list or cancel path.

Source evidence: `rust/crates/zg-daemon/src/job_scheduler.rs`
(`SchedulerConfig { concurrency: 2, queue_capacity: 64 }`,
`active_by_root`/`followup_by_root`, `reserve_slot`), and the CLI-to-daemon
index request path.

## Goals

1. Make routing explicit: direct vs daemon, with the chosen target shown before
   a long-running operation.
2. Give manual index/rebuild requests priority over watcher jobs for the same
   workspace; define a safe cancellation/supersede boundary.
3. Expose job id, root, reason, priority, state, phase, queue position, elapsed
   time, and progress in CLI/daemon status output.
4. Keep one writer per workspace and atomic rebuild/rollback semantics.
5. Make a third independent project queue explicitly, not appear as a lock.
6. Add bounded cancellation/timeout and stale-job handling.
7. Prevent watcher jobs from silently indexing huge generated/results trees
   unless the workspace scan scope explicitly includes them.

## Non-goals

- No scheduler rewrite on the frozen RC branch.
- No bypass of workspace locks, containment, or rebuild atomicity.
- No change to index format, defaults, or production authorization.
- No new benchmark campaign.

## Proposed design

### Job model

Expose a read-only `JobView`:

```text
id, root, reason (manual|watch), priority, state (queued|running|waiting),
phase (scan|embed|commit|optimize|finalize), queue_position, elapsed,
progress, cancelable
```

`zg --server jobs` and `--server status --json` should return these views.
`zg --index` should print the target (`direct` or daemon job id) and then stream
phase changes.

### Scheduling

- Manual jobs get a higher priority class than watcher jobs.
- Same-root manual rebuild:
  - request cancellation of a running watcher job at a safe checkpoint;
  - if cancellation is not safe, wait and report the exact blocking job/phase;
  - create one follow-up rebuild, not an unbounded queue of follow-ups.
- Different roots: retain a global resource budget, but make the queue visible.
- Keep per-root single-writer exclusion; no concurrent writers on one index.

### Routing

- Management commands (`--index`, `--rebuild`, `--drop`) should be direct by
  default, or require an explicit `--server`/config choice.
- If routed to daemon, print `routed to daemon job <id>` before waiting.
- Add an explicit `--no-wait`/`--detach` for operators who want a job id.

### Watcher scope

- Watcher jobs should honor the saved scan scope and default ignores.
- Do not auto-index large generated/results trees unless explicitly selected.
- Surface skipped/ignored scope in status when a scan is unexpectedly large.

## Migration phases

| Phase | Work | Acceptance |
|---|---|---|
| UX0 | JobView + `--server jobs` + phase/queue rendering | An operator can see running/queued jobs and why a client waits |
| UX1 | Explicit routing + job id output + `--no-wait` | No silent daemon routing for management commands |
| UX2 | Manual priority + same-root watcher supersede at safe checkpoint | Manual rebuild starts promptly or reports the exact blocking job |
| UX3 | Per-root queue + global resource budget + cancellation/timeout | Third project queues explicitly; no orphan/stale running job |
| UX4 | Watcher scope/default-ignore hardening | Large results/generated trees are not indexed by accident |

## Acceptance criteria

- A manual `--rebuild` on a workspace with an active watcher job either starts
  after a safe checkpoint or prints the blocking job id/phase; it never appears
  as an unexplained 0%-CPU hang.
- `zg --server jobs` shows every running and queued job with root, reason,
  priority, phase, and queue position.
- Three different roots produce explicit `running`/`queued` states; no global
  "lock" message.
- Same-root index requests never run two writers concurrently.
- Rebuild cancellation/rollback preserves the previous generation.
- Queue-full and cancellation failures are explicit, non-silent errors.
- No RC behavior change is made in this track without a new authorization.

## Open questions

- Which native optimize stages are safe cancellation checkpoints?
- Should manual priority be the default, or opt-in for a transition period?
- What global resource budget replaces the fixed concurrency 2 on small hosts?
- Should watcher auto-index a workspace at all when its scan scope is huge?

## Workaround until implemented

For a manual rebuild, stop the daemon first, then run direct:

```bash
zg --server off
ZVEC_GREP_MODE=direct zg --index --rebuild
```

For a different project, direct mode avoids the daemon queue, subject to host
CPU/RAM limits.
