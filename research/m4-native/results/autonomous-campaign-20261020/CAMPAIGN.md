# M4 writer-tail autonomous campaign — 2026-10-20

**Mission: PARTIAL / OWNER_DECISION_REQUIRED_RESOURCE_PLAN. Research INCONCLUSIVE, not complete.**

The active GOAL-BOUNDED AUTONOMY mission authorizes immediate offline/source/engineering work and autonomous choices within M4 research. It supersedes §53's per-slice owner-direction model, but explicitly conditions real diagnostic execution on an approved resource envelope. No such approval exists. **Campaign not activated: 0/3 pairs consumed; zero real A/A, readiness/ambient capture, idle polling or native performance executable launched.** Synthetic/tiny owned-unit tests are not campaign measurements. No request to quiet other projects.

## Completed engineering and offline work

### Concrete containment gap: task/thread limits

The previous new-mode plan required CPU/memory/swap caps but did not explicitly bound native tasks/threads. Added `pids_max` to the existing budget/context, `TasksMax` at service creation, `pids.max` write/read-back before release and runtime read-back. Record `pids.current` and `pids.events`; missing task telemetry or any `pids.events.max` increase fails closed in contention-aware mode. Positive-integer validation rejects boolean/zero/negative/missing task caps. A tiny owned service reads an actual kernel cap of **64 tasks**, exits normally and verifies cleanup; synthetic checks cover changed caps and task-limit distress. This is a test value, not authorization for a real resource plan.

No new harness, daemon, controller, backend or trace facility. Qualification/default-diagnostic JSON is byte-value-identical outside the new `contention_aware` section compared with `6267e69`; legacy modes do not receive a newly chosen TasksMax. Historical raw FAILs remain FAIL. No old guard threshold, assertion or timeout was relaxed. Extra task telemetry is observer work and is not a latency improvement.

### Source findings that narrow the next question

Inspected pinned bridge `research/m4-native/tests/m4_bridge_integration.cc` and local native `src/binding/c/c_api.cc`, `src/db/collection.cc`, `src/db/index/segment/segment.cc`:

| Hypothesis | Current evidence / disposition | Discriminating observation |
|---|---|---|
| H1: “native-vector-build” is HNSW compute | **Not established.** `BuildDeltaVectorSnapshot` creates a new shard; `OpenVector(..., true)` constructs HNSW plus six scalar INVERT indexes, creates native collection storage and a bridge manifest. The measured vector envelope also includes conversion, upsert, native flush, metadata and durable writes. | Split vector-open/upsert/flush wall and caller CPU, owned CPU/pressure/throttle-counter increments, compaction flags. High aggregate group CPU does not isolate HNSW or its critical path. |
| H2: post-M4 synchronization/finalization causes the tail | **Plausible, unmeasured with current full-sync bytes.** `SyncTree(generation_root)`, new-shard SyncTree and root SyncDir are separate from row-copy/coordinator finalization. Old post-M4 residual combines work and cannot all be named fsync. | Existing split `post_m4_sync_tree` versus `snapshot_finalize` arrays and common-monotonic timelines. IO/CPU pressure is supporting context, not syscall attribution. |
| H3: readers block the new shard's collection schema mutex | **Direct same-object explanation rejected by source ownership.** Native flush takes its collection's exclusive schema mutex, but this new collection is private until publication; cohort readers pin the prior immutable snapshot. | Shared native pools, allocator/global locks and host scheduling remain possible; rejection does not eliminate those. |
| H4: fixture reader barrier is included in the native/post-M4 stage | **Direct explanation rejected by source placement.** Cohort `ready` wait precedes `commit_start`; completed-reader wait follows collection of commit/stage arrays. This does not remove ordinary scheduling contention from those stages. | Preserve the same cohort/reader contract; do not “improve” latency by dropping readers/barriers. |
| H5: compaction is necessary for the tail | Already rejected by retained 80-cycle review (§53); **not rerun**. | Keep flags and separate compaction/non-compaction cycles; no new claim that compaction is free. |

No latency optimization selected or native source changed. In particular, do not remove six scalar indexes without retrieval/filter evidence, elide fsync broadly, skip mutable `fts/wal.log`, or treat wall-minus-caller-CPU as IO wait. A possible immutable-reuse sync optimization would first need measured material cost and a precise already-durable-file/parent-directory proof plus fault/oracle coverage; it is **not implemented or approved by this note**.

### New offline evidence, not another benchmark

`inspect_retained_resources.py` extracts resource fields from the four already-retained 500×20 filter-identity arms. It selects the sustained payload rather than accidentally selecting the final post-close/reopen JSON object. Inputs and SHA-256 identities are in `retained-resource-evidence.json`.

- Largest sampled/reported process RSS: about **302 MiB**; sampled process swap zero.
- Historical arm wall: **4.96–6.38 s**; process writes **216–239 MiB**; retained final logical root **6.85 MiB**.
- **Not captured:** charged cgroup memory/file-cache peak, task/thread peak, workspace allocated/apparent peak, device-write ownership. Process RSS and final root size are not substitutes for these quantities.
- These are old filter-identity comparisons, not current full-sync results or predictions of present capacity/safety.

`stage_counter_bounds.py` is a pure offline analysis reader, not a sampler/runner. It joins emitted stage clocks to existing cgroup telemetry with actual read brackets and the pinned bridge's eight-significant-digit clock rounding error: full counter spans inside a shrunken stage provide lower bounds on **reported counter increments**, spans intersecting an expanded stage upper bounds only with complete boundary coverage. No interpolation; incomplete coverage is UNBOUNDED, absent old stage fields NOT_EMITTED. Identity/mode/context digest, round count/order, clock brackets, counter monotonicity and monitoring gaps are checked. CPU/IO/memory PSI and throttle counters are not added to each other or to wall/caller CPU. Kernel accounting can batch/lag execution: these are **not strict bounds on physical execution time or critical-path waits**. Zero IO PSI does not exclude unmarked fsync/lock waits. Owned CPU includes readers/workers/bootstrap; observer CPU is outside. Native upsert/conversion have per-cycle wall/caller-CPU arrays but no emitted absolute substage clocks; the helper does **not** invent a group-counter join for those substages. Synthetic tests validate this method; **no real M4 causal result has been derived from it**.

## Resource proposal — owner decision required

Exact machine-readable **PENDING** proposal: `resource-plan.proposed.json`. Active incomplete template remains unapproved. The proposal is pinned to policy SHA `be7eca5603ae4e29d9f8b42203fdf4b3b0be3beaf376fdc6ab613392810ef6bb` and full-sync ELF `ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f`.

| Proposed parameter | Enforcement / meaning |
|---|---|
| M4 memory 1 GiB; swap 0 | Owned `memory.max` / `memory.swap.max`; no swap allowance. Not charged-cache peak evidence. |
| CPU `200000 100000` | Two CPU-equivalents averaged over a 100 ms quota period, not affinity/exclusive cores or a two-core entitlement. Inherited limits can be stricter. |
| Tasks 128 | Kernel pids.max/TasksMax counts threads as well as processes. Peak is unknown; no automatic increase on distress. |
| Workspace allocated 512 MiB; apparent 1 GiB per arm | Sampled owned-root ceilings, **not filesystem quotas or total-write-byte caps**. Retain first root/reserve second root. |
| Available host memory reserve 1 GiB | Separate reserve for observer/kernel/unrelated work; first decision also reserves workload MemoryMax (about 2 GiB total available required). Not a universal safety threshold or guaranteed future headroom. |
| Shared filesystem free reserve 2 GiB | Plus both incoming 512 MiB workspace reservations initially (about 3 GiB required); existing runner accounts retained first root in later decisions. No cleanup of unrelated files to make room. |
| Arm runtime 120 s | Existing independent watchdog; systemd RuntimeMaxSec 125 s includes bootstrap. No timeout enlargement/retry. |
| Monitoring-gap allowance 2 s | Nominal existing sampling ~50 ms; actual brackets/gaps retained. Gap violation aborts, not a reason to relax resolution. Broad short-stage bounds stay uncertain. |
| Pair supervisor 360 s; maximum 3 sequential pairs | Explicit GNU `timeout --signal=TERM --kill-after=5s 355s` command prefix: TERM at 355 s, KILL grace ends at 360 s. Tool auto-background is **not** a hard deadline. Up to 1080 s supervised measurement time; existing watchdog/systemd control owned work and verified cleanup remains required. No promise of immediate kernel/IO cleanup at the deadline; no next operation while cleanup is unverified. |
| I/O | **No hard bandwidth/IOPS/total-write cap, no delegated io.max, no device-write ownership.** Monitor owned/process IO, cgroup/host PSI and shared disk capacity; not isolation. |

Choice rationale: 1 GiB is about 3.4× recorded process RSS, but not proof of fit. The alternative 512 MiB/one CPU/64 tasks spends less host budget but has less headroom for unmeasured cache/tasks and more self-throttling; advisory/agent reasoning retained in `resource-choice.json`. All these numbers are **administrative proposals**, not measured safety limits. Owner can edit before approving. No resource approval reference is synthesized from engineering autonomy.

**Risk acceptance must expressly include** unisolated IO/shared Btrfs, potentially delayed writeback after the process exits, sampled disk/workspace overshoot, pressure/latency effects on other projects, inherited/shared limits, and external observer resources without their own hard CPU/RAM/IO caps. Observed reserve/identity/telemetry/OOM/owned-distress/timeout/cleanup failures still stop the operation. No guarantee of zero host impact; do not stop/throttle other projects to force a pass.

## Bounded first experiment and subsequent decisions

1. **Only after owner approval:** bind real approval/execution/IO-risk references and unique per-slot context; keep numerical caps unchanged. Apply the explicit 355 s TERM + 5 s KILL supervisor prefix, not merely a tool auto-background timeout. Claim the unique operation once, debit its slot, then perform one fresh provenance/capacity decision. Do not poll for idle or interpret an old snapshot as a fresh capacity decision.
2. First pair: same pinned full-sync binary, `--incremental-fts-sustained 500 20`, tracing off, four readers/80 queries per arm, fresh roots, existing verified-cache path (not cached-fingerprint-skipping variant). Existing before/during/after host/process/cgroup evidence, correctness/coherence/reopen checks and verified cleanup remain mandatory.
3. Before any next heavy operation, analyze vector-open/upsert/flush versus post-M4 sync/finalization, caller CPU, counter-read uncertainty, self-throttling, reader tails, memory/task peaks and logical/process-physical writes. Retain historical raw comparisons and explicit diagnostic continuation reasons. Output **EXPLORATORY / CONTENTION-AWARE / NON-QUALIFYING**, never qualification or production.
4. Slots 2/3 are not scheduled now. Use one only for a stated unresolved discriminating question, not to obtain a prettier PASS. Repeating same-binary data needs an explicit new reproducibility/order/uncertainty question. Source/instrumentation/candidate engineering remains autonomous within M4, but a changed binary may not evade the existing full-sync provenance guard: it needs correctly registered/verified full-sync prerequisites before measurement. No unsupported new binary, heavy tracing or automatic cap/timeout/guard changes.
5. Each claimed/preflighted operation consumes its slot even on no-launch/abort. A failed formal gate is **not** permission to retry under the next slot. Preserve observations and stop unsafe dependents; continue only independent already-authorized offline work. Cap enlargement, extra pair or retry of a refused operation needs the corresponding owner decision/recovery condition. Record remaining uncertainty after each completed analysis. Do not spend all three slots merely because they exist.

A counter-bound analysis command after a valid arm (not executed now):

```sh
python3 research/m4-native/results/autonomous-campaign-20261020/stage_counter_bounds.py \
  --stdout "$PAIR/aa-first/stdout.jsonl" \
  --runtime "$PAIR/aa-first/containment/runtime.jsonl" \
  --config "$PAIR/aa-first/containment/watchdog-config.json" \
  --output "$PAIR/aa-first/stage-counter-bounds.json"
```

This reads evidence only; its output cannot waive an operation guard or establish a win.

## Verification, provenance and remaining acceptance

**56 distinct focused tests PASS:** 24 contention/task-context, 13 tiny containment/lifecycle, 5 shared-policy, 7 unchanged legacy gate, 1 tiny sequential-pair and 6 offline bound tests. Current six-file `ty check` PASS. Selection/commands/hashes: `task-budget-tests.json`, `offline-verification.json`, `analysis-release-v2-verification.json`; corresponding logs and owned-unit receipts retained. Earlier analysis receipts remain historical, not counted twice. Clock-serialization inspection found a concrete boundary precision gap in the offline method; v2 expands/shrinks the bounds and adds a regression rather than pretending serialized clocks are exact. `owned-fixture-retirement-audit.json` confirms all seven archived launched fixture units retired; its initial ad-hoc empty selector was corrected using actual nested receipt fields without rerunning a fixture. Initial LSP test-fixture union inference was fixed with explicit list narrowing; final fresh CLI type check is authoritative. Blanket auxiliary unchecked-I/O findings are not a claim of LSP cleanliness; CLI input/IO failures intentionally fail closed.

`current-static-provenance.json`: actual current ELF, all **61 compiler dependencies**, **six runtime libraries** and **50 durability-manifest entries** checked; retained corrected correctness/five fault cases/four recovery oracles still apply to exact bytes. No redundant rebuild or fresh fault/benchmark run. Build receipts precede publication of `7c88272`, with identical compiled source; no later rebuild timestamp claimed. Native .cc hashes are inspection snapshots, not new proof of native-library source provenance. A first import lacked required environment, **before any guard ran**; retained in `static-check-import-error.json`. Correcting import configuration performed the first static guard evaluation, not a retry of a failed guard or operation.

No production/native library, daemon, ABI/schema, existing index or unrelated Rust edits. No latency/cost win or accepted minimal optimization. `campaign-ledger.json` is current authority tracking, not a measurement or owner approval.

| Mission criterion | Status |
|---|---|
| Relevant offline work / concrete control defect | Completed; source hypotheses narrowed, task cap gap fixed/tested. |
| Approved resource/risk envelope | **NOT MET — owner decision required.** |
| Real bounded diagnostic campaign | **NOT STARTED, 0/3.** |
| Root-cause-supported correction (A) | Not met. |
| Evidence-based latency optimization rejection (B) | Not met; source exclusions above are narrower than rejecting an optimization. |
| Budget-exhausted evidence-limited closeout (C) | Not met; campaign budget unactivated/unspent. |

**Exact next action:** owner approves or edits the proposed resource vector and unisolated-IO/observer risks for up to three sequential named pairs under the stated no-retry/timeout envelope. Then execute one fresh approved operation and analyze before choosing another. Until approval, no benchmark, readiness/ambient capture or implicit campaign-completion claim.
