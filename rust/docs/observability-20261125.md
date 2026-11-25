# zvec-grep observability (2026-11-25, finalization)

Local-first, opt-in event capture for runtime diagnosis and retrieval research.
The recorder never changes retrieval, ranking, embedding, indexing or RAG
behavior. The installed runtime binary and live daemon are not promoted.

## Modes, capture and compatibility

All capture is off unless `ZVEC_GREP_OBSERVABILITY=1` is set.

| Mode | Purpose |
|---|---|
| `runtime` | Runtime stream only: jobs, watcher, refresh admission, indexing, resources |
| `dogfood` | Runtime + retrieval metadata; candidate lineage fields are stripped |
| `research` | Full candidate lineage, per-group candidates, evidence IDs, importers |

Capture controls value sensitivity, not lineage breadth:

| Capture | Free text | Query text | Answer text | Thinking |
|---|---|---|---|---|
| `metadata` (default) | dropped | hashed | dropped | dropped |
| `redacted` | hashed | hashed | hashed | dropped |
| `full` | retained locally | hashed | retained locally | dropped |

Common variables:

- `ZVEC_GREP_OBSERVABILITY=1`; `ZVEC_GREP_OBSERVABILITY_MODE=runtime|dogfood|research`; `ZVEC_GREP_OBSERVABILITY_CAPTURE=metadata|redacted|full`; `ZVEC_GREP_OBSERVABILITY_HOME`.
- `ZVEC_PI_OBSERVABILITY=1` enables the Pi extension and makes it inject the same variables plus `ZVEC_GREP_TRACE_ID`, `REQUEST_ID`, `SESSION_ID`, `TURN_ID`, `TOOL_CALL_ID` into `zg` via `env(1)`.
- Invalid or partial variables are a hard error (`ZG.OBSERVABILITY.CONFIG`), never a silent default.

`zg-observability::Config` (capability version 1: mode, capture, home path) is
advertised in the daemon instance record and in `DaemonStatus`. A server-mode
CLI compares its own config against the daemon and fails with
`ZG.OBSERVABILITY.CONFIG_MISMATCH` on any disagreement, including one-sided
enablement. Explicit `--mode direct` bypasses daemon negotiation. MCP search
results expose the daemon config in result `_meta.zvecGrepObservability` so a
client can verify what produced its retrieval event.

Inspect local events with `zg --observability` or `zg --observability --json`.

## Streams and retention

Rust events live under
`observability/{runtime,retrieval,investigation,research}/YYYY-MM-DD.jsonl` with
a per-process `seq`; Pi/Codex importer records have no `seq` and are ordered by
timestamp. Both Rust and Node writers rotate at 10 MiB × 5 files and prune
files older than 14 days. Stream directories are created `0700`, files `0600`;
pre-existing paths are never chmodded. `zg --observability` prunes all streams.

## Correlation model

```
Pi/Codex session id / turn id / tool call id
  -> trace_id (one tool invocation)
  -> request_id (one search/index/status call)
  -> daemon job_id (index jobs)
  -> root_id + index_generation + evidence_id + span_id
```

Pi passes native IDs through CLI env into `zg`; manual daemon index jobs carry
the trace in runtime-only `IndexOptions.trace_id`. Coalesced/reused manual jobs
emit `runtime.job_attached` and adopt the caller trace. Watcher jobs clear the
trace in both template and batch construction. Codex rollouts do not expose
those IDs: `scripts/ingest-codex-session.mjs` joins by query hash + root id +
timestamp (≤15 min), records `join_kind: query_time_root`, and never claims
native trace propagation.

`root_id` hashes the canonical root; `index_generation` is the manifest
`storageGeneration`; `span_id` hashes path + line range; `evidence_id` hashes
root, relative path, range, content hash and generation. The same source span
gets a new evidence identity in a new index generation.

## Event inventory

Runtime:

- `runtime.job_submit`, `runtime.job_attached`, `runtime.job_running`, `runtime.job_finish`: job id, root, reason, state, phase, waited ms, submission-time blocker, progress, error code, resource sample, trace id.
- `runtime.watcher_batch`: changed-file count, rescan flag, scheduled job id, root id (watcher loop coalescing).
- `runtime.refresh_admission`: refresh policy, auto-update flag, waited ms, `admitted|timeout` (search refresh/lock contention).
- `runtime.index_request`: counters, duration, resource, trace id.
- `runtime.resource_sample`: periodic 5-second daemon sample plus phase-boundary samples.

Retrieval:

- `retrieval.delivery`: query hash/length, root/generation, source, coverage, freshness, refresh policy, groups, routes, acquisition hits, empty reason, ranked items with score/matched-by/selection-reason/evidence-id/span-id and research-only `lineage`, per-group candidate sets, stage timings, resource.
- Runtime/Dogfood strip `groups`, `routes`, `acquisition`, `fusion`, `ranking`, `selection`, `group_results`, `timings`.

Investigation:

- `investigation.question`, `tool_call`, `tool_result`, `answer`, `usage`, `source_read`.
- `scripts/ingest-pi-session.mjs` reuses live IDs and joins evidence by `request_id`. Evidence joins require `research` capture mode; in Runtime/Dogfood a matched retrieval has deliberately empty `evidence_ids` because `ranking` is stripped. Importers report a labelled join even when evidence is empty.
- `scripts/ingest-codex-session.mjs` parses rollout messages, custom tool calls/outputs, `item_completed` timings and token usage; it skips reasoning items and hashes nested tool invocations. Both importers are idempotent per input hash + capture and prune markers.

## Resource diagnostics

Linux uses direct `/proc` reads at phase boundaries and on a bounded 5-second
daemon interval, with deltas since the previous sample:

- process RSS, process CPU %, host CPU %, available memory, thread count;
- top eight threads by CPU-tick delta with hashed TID, state and safe thread name (`comm`);
- sample interval to distinguish fresh samples from interval deltas.

Non-Linux uses a coarse `sysinfo` fallback. No per-file, per-candidate or
per-token sampling; overhead is one small `/proc` read at phase boundaries and
one every five seconds while a daemon with observability enabled is alive.

## Privacy

- Query text is hashed in every capture mode; thinking is never stored.
- Metadata drops free-text fields; redacted hashes them; full retains only locally and by explicit opt-in.
- Secret-like keys (`token`, `api_key`, `authorization`, `password`, `secret`) are always dropped outside full capture. Long strings truncate on UTF-8 boundaries with a hash suffix.
- Imported final answers are labelled `agent_observed`; agent behavior is not gold evidence.

## Verification performed

- `cargo fmt --all -- --check`, `cargo check --workspace --all-targets`, and `cargo clippy --workspace --all-targets -- -D warnings` pass.
- Rust focused suites (observability, daemon, CLI, MCP transport) pass; the four former baseline failures were root-caused to fixtures created under a forced `TMPDIR` inside the indexed worktree — nearest-workspace discovery found the parent index. Fixtures now live outside indexed ancestors; tests were not weakened.
- Pi fork `npm test` passes, including bounded rotation/retention/permissions and Codex importer tests.
- Compatibility E2E: daemon with config A rejected CLI config B and a CLI with observability disabled; both failed with `ZG.OBSERVABILITY.CONFIG_MISMATCH`. Matching config succeeded. Invalid/partial variables now fail with `ZG.OBSERVABILITY.CONFIG` even for build-info/help; `--home` selects the event root; a stopped daemon no longer produces a false mismatch.
- Pi real E2E with isolated home/port/binary: status → index → search → missing-index error; native trace appeared on all manual job events, watcher trace was null, lineage and three evidence IDs present, no raw question.
- Codex real E2E via isolated `CODEX_HOME` and MCP tool: MCP search completed; rollout import produced question/tool call/answer/usage plus a labelled `query_time_root` join to the retrieval trace and its three evidence IDs; no reasoning or raw prompt stored. Query identity includes `query`, `queries` and route terms so fts/vector-only calls are joinable.
- Diagnostics E2E: 12+ periodic resource samples with host/process CPU, RSS, thread count and top threads; `runtime.watcher_batch` and `runtime.refresh_admission` emitted.
- Single-run overhead probe (not a benchmark): direct query off 2.96 s / 2.51 s user / 393236 KB RSS; on 2.24 s / 1.90 s user / 396900 KB RSS; one 2964-byte retrieval event. Difference is within cache noise; no repeated-run claim is made.
- Earlier DeepSeek V4.1 Flash High review (`ses-obs-review-20261125`) ended GO after its findings were fixed.

## Known limits

- Observability still requires a matched CLI/daemon build; an older daemon with `deny_unknown_fields` rejects `IndexOptions.trace_id`. No promotion happened here, so this is a deployment constraint.
- `runtime.job_submit` is scheduled under the scheduler state lock but its file I/O runs on a writer thread; running/finish/attach likewise defer I/O and sampling.
- Codex correlation is a labelled heuristic; MCP `_meta` config is available but Codex rollouts do not preserve it.
- Host CPU and thread deltas are sampled, not profiled; no stack sampling.
- Pi harness pin in repo is older than live Pi 1.6.0; live question flushing is verified by E2E, not `npm test`.
