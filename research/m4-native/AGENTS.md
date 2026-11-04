# M4 Research Prototype Guidance

## 1. Mission and scope

M4 is a standalone research prototype under `research/m4-native/`. It is not integrated with production `zvec`, `zg-engine`, the daemon, index formats, or public APIs.

The research program evaluates correct, durable, resource-efficient incremental FTS/INVERT for background code search. The **current user request** determines whether the agent is executing a bounded M4 task or an autonomous research mission.

Optimize for practical outcomes:

- Retrieval correctness and ranking fidelity.
- Durability, atomic publication, and crash recovery.
- Low CPU, memory, I/O, and storage overhead.
- Predictable writer latency and bounded maintenance work.
- Stable operation under realistic host contention.
- Reproducible evidence supporting engineering decisions.

**Research progress is measured by what was learned or improved, not by the number of tests, receipts, commits, or benchmark attempts completed.**

Keep changes within `research/m4-native/` unless the owner explicitly expands scope. Do not modify production implementations, deployment, schemas, or existing indexes.

Repository root `AGENTS.md` supplies project-wide behavior, **not global user/agent policy**. This file adds M4-specific constraints. Active owner instructions take precedence over historical next actions, subject to repository safety and correctness requirements.

## 2. Sources of truth

Keep responsibilities separate:

| Source                                     | Responsibility                           |
| ------------------------------------------ | ---------------------------------------- |
| Root `AGENTS.md`                           | Repository-wide engineering guidance     |
| This file                                  | Durable M4 invariants and workflow       |
| `README.md`                                | Build, run, and development instructions |
| `docs/benchmark-policy.json`               | Executable benchmark policy              |
| `docs/benchmark-policy.md`                 | Policy explanation                       |
| `results/RESULTS.md`                       | Research outcomes and evidence           |
| `rust/docs/m4-incremental-native-index.md` | Canonical mission handoff                |

Do not duplicate resource thresholds here.

Runners must load executable values from `benchmark-policy.json`, not hardcode copies.

Changes to policy implementation require matching tests and documentation. Changes to approved resource limits, evidence eligibility, or safety semantics require owner authorization.

Historical receipts, benchmark failures, and handoff recommendations are evidence, **not fresh execution authorization**.

## 3. Data and ranking invariants

- FTS and INVERT share one `DocId`/tombstone catalog.
- `DocId`s are never reused. An update tombstones the old document and appends a new one.
- Runs are immutable.
- Flush writes format v1; compaction writes format v2.
- Readers support both formats.
- Incompatible layout changes require an explicit version bump and independent reader tests.
- Store raw `max_tf`/`min_dl` per FTS block.
- Compute IDF, `N_live`, `df_live`, and `avg_dl` at query time.
- Never persist IDF-weighted block bounds.
- Preserve BM25 semantics, OR/AND/phrase FTS, scalar EQ/range, hybrid ranking, deterministic result ordering, and the existing search API.

Correctness must not be traded for performance.

## 4. Compaction and durability

Compaction is a streaming k-way merge. Do not materialize merged postings, scalar values, or documents in RAM.

Enforce `compaction_memory_limit_bytes`, charging posting buffers, block metadata, dictionaries, and fixed I/O buffers.

On budget failure, abort before manifest publication; existing runs remain searchable.

Format v2 compaction writes directly to its final file. Do not reintroduce temporary payload/meta/docs sections or a second output copy.

Preserve the established durability protocol:

1. Segment write, fsync, and rename.
2. Stats publication.
3. Tombstone publication.
4. Atomic manifest publication.
5. WAL truncation.
6. Garbage collection only after the manifest is durable.

Preserve required file and directory synchronization across the full publication lifecycle. Do not remove FTS/WAL fsync operations merely because an earlier `Flush()` succeeded.

A performance optimization affecting persistence ordering requires explicit durability analysis and crash-recovery verification.

Segment checksums cover the lowest section offset through EOF.

The v2 section order is FTS → docs → INVERT.

Temporary output must be RAII-cleaned on failure.

`M4_FAULT` is exclusively for isolated research fault injection and must not leak into normal execution.

## 5. Scope of autonomy for M4

The active user request determines the scope:

- **Bounded M4 task:** implement/investigate only the requested slice, with the necessary debugging, tests and evidence. Passing the task's real acceptance criteria is sufficient; do not independently start another M4 research topic.
- **Delegated M4 research mission:** independently investigate source and evidence, choose experiments and prototypes, test competing designs, and continue across engineering slices until the mission's decision criteria are met or no safe authorized work remains. Neither a commit nor a benchmark receipt ends the mission by itself.

Routine in-scope fixes, instrumentation, focused verification and research documentation need no additional owner prompting. Architectural prototype experiments are allowed when the current mission delegates them and established M4 format/durability contracts remain intact. Seek an owner decision for changes outside the delegated scope, incompatible storage/public contracts, production integration, destructive changes lacking ownership, or modifications to mandatory safety/resource limits.

A task authorization is not standing permission for endless research; a delegated research goal is not reduced to a single slice by a historical handoff.

## 6. Resume and execution state

Start from the real working tree and `results/ACTIVE-EXECUTION.md` when present. Consult the canonical handoff and results for background and provenance; **do not replace active execution state with historical next actions, stops, or budgets**. Reconcile the checkpoint with git diff, receipts and the current user request, especially after compaction.

For the currently authorized work, trace implementation failures to their cause, repair within scope, run proportionate checks and resume the unfinished task/mission. A formal operation refusal stops only that operation; preserve its evidence and pursue other **already-authorized** work where useful. Never automatically replay a refused operation or silently weaken its gates.

Keep `ACTIVE-EXECUTION.md` concise: active goal and scope, HEAD/dirty work, last verified evidence, blocked operation (if any), and one executable next action. Archive detailed history in the canonical handoff or results, not the active checkpoint. Updating this file is continuity support, never a substitute for engineering progress.

Long jobs should remain bounded and nonblocking where tooling supports that. The user determines whether a new mission begins, whether broad autonomy is delegated and whether a task is done; do not infer new goals from old research notes.

## 7. Benchmark and measurement discipline

Separate three concerns:

**Operational safety:** whether a workload can be executed within approved containment, resource, correctness, durability, and cleanup requirements.

**Measurement integrity:** whether the collected data is complete, correctly attributed, and reproducible.

**Measurement quality:** whether environmental conditions permit the desired statistical or causal conclusion.

Do not merge these classifications into a single generic PASS/FAIL explanation.

Qualification and contention-aware exploratory measurements have different purposes. Apply their current executable policy without changing thresholds or reclassifying results implicitly.

M4 is intended to operate while other projects, coding agents, IDEs, and browsers are active.

Do not require the owner to stop unrelated work merely to recreate an idle host. Use approved contention-aware diagnostic procedures when appropriate.

Host contention must be measured and disclosed, not silently treated as M4 resource consumption.

Every real performance experiment must specify:

- Hypothesis and expected discriminating observation.
- Workload and experiment identity.
- Source, binary, and configuration provenance.
- Approved resource limits and execution budget.
- Stop conditions.
- Required telemetry.
- Evidence classification.
- Analysis criteria.

### Benchmark readiness before asking the owner

If a benchmark is needed but the owner must authorize its execution or change an environmental condition, finish preparation **before** asking. Have the current-source binary built and identified; correctness and command validation complete; arms, workload, operation ID/fresh output root, resource policy and stop conditions fixed; required dictionaries/data verified; and monitoring, output paths, expected duration, and result-analysis steps ready. Run only no-launch validation during preparation. The request must state the concrete blocker and exactly what decision/action is needed; do not tell the owner that the benchmark is unprepared and defer preparation until after they respond or end the session.

Preparation is not an extra permission gate: when the active task already authorizes benchmarking, run the prepared operation as soon as the unchanged policy permits. If a formal gate refuses it, preserve the evidence and stop that operation; do not weaken or bypass the gate or auto-retry. Ask for an owner decision only when a new operation requires authorization or a policy change. A policy change must be explicitly authorized and made through the documented policy, implementation, test, and handoff path before the new operation.

**Standing owner authorization (2026-10-24):** the owner explicitly permits bounded benchmark execution within this M4 research project without asking for per-operation confirmation. This authorizes only experiments already within the current task/policy and approved containment; it does not authorize changing resource limits, weakening correctness/durability/cleanup safeguards, production activity, unbounded runs, or retrying an operation refused or stopped by a safety gate. Preserve a fresh operation identity and all required telemetry/evidence for every run.

A same-binary A/A measures execution variability; it does not independently establish a candidate regression.

Do not infer stage causality by adding or subtracting independent latency percentiles.

Distinguish wall time, process CPU time, I/O wait, logical writes, physical writes, and observer overhead.

Never claim a performance win without the relevant completed measurements.

## 8. Observer, telemetry, and watchdog behavior

Research instrumentation must not be confused with the implementation being measured.

Classify failures separately:

- Native implementation failure.
- Correctness or durability failure.
- Containment or resource failure.
- Telemetry collection failure.
- Observer integration defect.
- Invalid or insufficient measurement.
- Environmental interference.

An observer failure is not automatically an engine defect.

However, an observer failure must not be ignored when it removes evidence required for safe continuation.

For mutable filesystem observation:

- Account for concurrent creation, rename, replacement, and unlink.
- Do not assume recursive directory scans are atomic snapshots.
- Preserve incomplete or uncertain observations explicitly.
- Never promote partial counts into verified peaks.
- Do not treat an ENOENT race alone as proof of engine corruption.
- Apply the approved capacity and telemetry policy rather than inventing a new enforcement rule.

Keep monitoring overhead bounded.

Favor kernel-enforced limits where available. Document limitations of sampled or best-effort accounting.

Independent watchdogs must enforce the approved policy context consistently with the controller.

Raw failures remain immutable even if later analysis identifies an observer defect.

Cleanup must be verified before retiring owned containment units.

Never disable watchdog safety solely to obtain benchmark results.

## 9. Avoid benchmark-infrastructure rabbit holes

Benchmark infrastructure exists to support research; it is not the research objective.

Before changing a runner or observer, identify the direct failure preventing the intended experiment.

Prefer minimal corrections and focused end-to-end checks.

Do not create new frameworks, resource controllers, quota subsystems, policy layers, or large test matrices without evidence that they are necessary.

If repeated attempts are blocked by different control-plane defects:

1. Stop treating each defect as an isolated incident.
2. Review the relevant end-to-end execution path.
3. Identify shared assumptions or design problems.
4. Test those assumptions using bounded synthetic workloads.
5. Reassess the measurement approach and expected information value.
6. Avoid consuming additional native attempts merely to discover predictable integration defects.

A synthetic rehearsal proves only the behavior it actually exercised. It is not native performance evidence.

If an existing resource contract cannot be enforced with available mechanisms, report the limitation and seek an explicit owner policy decision rather than continually adding infrastructure.

Do not weaken correctness, durability, or operational safety in order to obtain numbers.

## 10. Resource management and long-running work

Keep agent-controlled temporary, build, sanitizer, and disposable scratch outputs under:

`research/m4-native/.work/{tmp,build,sanitizer,scratch}/`

Set `TMPDIR`, `TMP`, and `TEMP` per job where compatible.

Do not change system-wide temporary configuration.

Preserve required filesystem semantics for benchmarks; project-local temporary paths must not silently change measurement conditions.

Store reproducible raw evidence, manifests, receipts, and checksums under `results/` as appropriate.

Separate retained evidence from disposable working artifacts.

Do not modify unknown files or unrelated project data.

Long-running jobs must be bounded, logged, and observable without blocking the interactive agent session.

Use background execution with nonblocking status/log inspection where supported.

Never kill or throttle unrelated processes to satisfy M4 readiness.

Do not automatically retry a formal safety or qualification refusal.

Within an authorized campaign, choose subsequent experiments based on information value, not merely unused budget.

A consumed attempt remains in the ledger even when it produces no valid measurement.

## 11. Verification and evidence

For behavioral changes:

- Read the relevant oracle and format implementation.
- Add or update focused regression coverage.
- Verify correctness and recovery when applicable.
- Run appropriate sanitizer/build checks according to the change.
- Measure performance only when authorized and technically meaningful.

For a benchmark result, preserve:

- Exact executable and command.
- Source and binary identities.
- Environment and resource context.
- Raw receipts and sampled telemetry.
- Completed workload counts.
- Correctness/coherence outcomes.
- Measurement uncertainty and evidence class.
- Failure and cleanup details, if applicable.

A successful launch is not a completed measurement.

An aborted workload must not produce fabricated latency statistics.

Tests passing does not mean the research hypothesis is validated.

Keep the latest canonical handoff synchronized with material decisions.

Runner receipts report operation-level outcomes. Whole-mission status is determined by the active user-defined Definition of Done.

## 12. Known implementation traps

- FTS v2 dictionary field order is `payload_offset`, then `meta_offset`; v1 uses `first_block`/`block_count`.
- v2 section order is FTS → docs → INVERT, allowing docs to use final term IDs.
- Checksums begin at the FTS section.
- Save `stats->fts_terms` before clearing the in-memory FTS dictionary.
- `fts_dict` and `term_to_id` are O(vocabulary) metadata and must be charged to the compaction budget.
- `M4_FAULT=after_rename` intentionally leaves an orphan final file representing a crash after rename. The old manifest must ignore it.
- Do not delete a legitimately published file to repair an orphan-file test.
- Debt policy executes after `Flush`, not after each mutation.
- Preserve one-merge-per-flush, cooldown, minimum-runs, and input-cap guards.
- Debt policy defaults to OFF for the baseline.
- Native vector-stage elapsed time can include collection/index setup, flush, and metadata operations; do not equate the entire interval with HNSW computation.
- Temporary atomic-publication files can disappear between directory enumeration and stat.
- Inotify pending events require correct coverage/drain semantics; pending does not necessarily mean permanent coverage loss.
- `CPUQuota` formatting and `/proc/PID/io` parsing are control-plane concerns; verify the actual kernel and parser behavior, not just mock expectations.

## 13. Completion and handoff

For a **bounded task**, finish when its requested result has been delivered and proportionately verified. For an **autonomous mission**, finish only when its stated research/decision objective has been evaluated adequately, meaningful in-scope alternatives are exhausted, or a genuine authorization/safety decision blocks all relevant continuation.

Describe completed work, evidence class, uncertainty and any remaining blocker accurately. `PARTIAL`, `OPERATION_BLOCKED`, `SAFETY_STOP`, and `OWNER_DECISION_REQUIRED` describe their actual scopes; none by itself creates a new authorization or proves whole-mission completion. Keep checkpoint state factual and resumable when a session ends.

Current measurements and action state belong in `results/ACTIVE-EXECUTION.md`, `results/RESULTS.md` and the canonical handoff—not in this durable guidance file.
