# Session retrospective — source discovery validation

Date: 2026-11-22
Checkpoint: `fb880c2` on `work/source-discovery-autonomous-20261122`
Decision: **INCONCLUSIVE** for the guidance patch's behavioral effect. The paired A/B was not run because the current indexed-search environment fails its read-only integrity check and the configured model artifact is unavailable locally. A compatible native library can be selected for an isolated build, but that does not make the index/model evaluation path ready.

This report contains sanitized metadata only. I read timestamps, item types,
statuses, tool-action categories and aggregate error markers from the local
Codex databases and rollout. I did not export message text, command arguments,
tool output, or source content.

## Session timing

The previous source-discovery goal began at **2026-11-21 17:02:18.773 UTC**.
Its completion timestamp (**2026-11-22 01:26:27 UTC**, second precision) comes
from the prior goal-tool response; the rollout records active/pause/resume
transitions but no explicit completion event. The resulting wall interval is
**about 30,248 s (about 8 h 24 m 8 s)**. The goal tracker reported **30,175 s
(8 h 22 m 55 s)**, about 73 s less than that wall interval. The logs do not
identify the reason for that discrepancy; the fractional-second display is not
meaningful given the completion timestamp's precision.

| Measure | Recorded value | What it means |
|---|---:|---|
| Goal tracker time | 30,175 s | Goal-level elapsed usage; not CPU or model inference time |
| Wall interval | about 30,248 s | Goal creation to completion timestamp (completion timestamp has second precision) |
| Thread-turn overlap with goal | about 30,210 s | Turn wall intervals intersecting the goal window; they include tool waits |
| Gaps between turn intervals | about 38 s | Recorded gaps, including the 13.173 s paused/resumed interval; actual idle time is **UNKNOWN** |
| Command execution items | 153: 123 completed, 30 failed | UI tool items; failures are not necessarily unique task failures or retries |
| Command-item elapsed sum | 28,211.421 s | Tool-item start-to-completion time, not verified process CPU time |
| Inference/active reasoning time | **UNKNOWN** | No reliable per-request model-latency or human-active-time field was present |

The first long goal turn ran with `approval_policy=on-request` and
`approvals_reviewer=user`. It was paused at 2026-11-22 01:22:10 UTC and resumed
14 seconds later. Subsequent turn contexts changed to `approvals_reviewer=auto_review`.
Recorded model context across the goal turns was **gpt-6-luna, high reasoning**.
No paired A/B model identity exists because the A/B did not run.

| Timeline segment (UTC) | Reviewer setting | Recorded duration |
|---|---|---:|
| 2026-11-21 17:02:18–2026-11-22 01:22:10 | User review, on-request | 8 h 19 m 52 s; turn ended interrupted/paused |
| 2026-11-22 01:22:10–01:22:24 | Goal paused | 13.2 s |
| 2026-11-22 01:22:24–01:26:27 | Auto-review, on-request policy | About 4 m 03 s to the goal-tracker completion timestamp |

The final segment records `auto_review` as the reviewer setting while the
approval policy remained `on-request`. This setting change alone does not prove
that every approval was automatically granted; the event logs lack per-request
decision timestamps.

The six long command-item intervals during the manual-review segment were:

| UTC interval | Sanitized tool family | Item status | Item elapsed |
|---|---|---|---:|
| 17:05:45–21:39:28 | Git | Completed | 16,422.516 s |
| 21:46:49–23:12:49 | npm | Failed | 5,159.838 s |
| 23:20:28–00:40:36 | Node | Completed | 4,808.027 s |
| 00:46:56–00:51:16 | Environment-wrapped | Failed | 260.329 s |
| 00:53:41–00:57:25 | Environment-wrapped | Failed | 223.423 s |
| 00:59:55–01:20:35 | Git | Completed | 1,239.803 s |

These are command-item intervals, not measured approval prompts. Since all six
occurred under the manual reviewer and no corresponding request/grant event
timestamps exist, the approval portion of each interval remains **UNKNOWN**.
The goal timer overlaps these tool-item wall intervals, so some approval wait
was likely counted while the goal remained active; logs cannot establish how
much, and the 7 h 48 m 34 s total must not be subtracted as if it were all
approval time.

Six command-execution items each lasted at least one minute. Their recorded
elapsed fields totalled **28,113.936 s (about 7 h 48 m 34 s)** and all fell in
the manual-review turn. Their recorded tool families were Git (2), npm (1),
Node (1), and environment-wrapped commands (2); three items completed and
three failed. This coincidence makes approval
waiting a plausible part of those spans, but the event data has no approval
request/grant timestamps. Therefore **approval-wait duration is UNKNOWN**; the
28,114 seconds must not be reported as approval time or active execution.

The logs also contain 68 keyword-matching feedback rows, but no structured
approval event type or decision timestamps. Exact failure-to-retry pairing is
also **UNKNOWN**; only item counts and failure classes below are supportable.

## Environment readiness at checkpoint `fb880c2`

| Component | Evidence | Assessment |
|---|---|---|
| Source/worktree | Branch `work/source-discovery-autonomous-20261122`, HEAD `fb880c2`; at audit time, the pre-existing untracked `research/` tree and this new report were outside the commit | Correct checkpoint; no source/index mutation during this retrospective |
| Installed CLI | `~/.local/bin/zg`, version `0.0.1`, SHA-256 `6db0f67e05e61ad6d50e8e9ea7b3e4593cfb07c99ac0af9f5064f48398db7ed4`; npm package reports `0.2.1` | Distinct Rust and TypeScript surfaces; exact installed-binary-to-HEAD provenance is **UNKNOWN** |
| Workspace index | `zg --status` says status unknown; generation `6e3c3a4d-4690-4868-8f9e-1023bbb7577c` | Do not use as an A/B baseline |
| Read-only integrity check | `zg --verify --json` exited 1, `CORRUPT`, stable/no active writer; `IndexUnpacker rejected header/footer/segment table/content CRC` | Structural/content-integrity failure confirmed; underlying cause is **UNKNOWN**; no repair/rebuild attempted |
| Actual partial FTS query | `zg --fts searchWork ...` failed with checksum/storage errors | No valid FTS result for the A/B task can be measured |
| Native library | Three local copies were found: two lack `zvec_config_data_set_jieba_dict_dir`, `zvec_get_default_jieba_dict_dir`, and `zvec_set_default_jieba_dict_dir`; one preinstalled source-recovery copy exports all three | The default/older library mismatch was reproduced. With the existing candidate selected via `ZVEC_LIB_DIR` and `LD_LIBRARY_PATH`, offline `cargo test --no-run --locked -p zg` linked successfully into `/tmp`, and that binary ran `--version`. No tests ran; functional indexed queries against a valid fixture remain unverified. |
| Local embedding artifact | A disposable index attempt for configured `local/potion-code-16m-v2` found no local artifact and its Hugging Face/ModelScope requests failed | Missing local artifact confirmed; reason for remote request failures is **UNKNOWN**; no model was downloaded and no source was indexed or sent |

The A/B readiness gate therefore fails for the actual indexed-search path: the
existing workspace index is demonstrably corrupt, and the disposable fixture
path lacks a local model artifact. A compatible library candidate is already
available for an isolated build when selected explicitly, although the default
configured runtime path still points to an older copy. The updated instructions
are present in the tested MCP and CLI installer surfaces, but that contract
result cannot substitute for a paired agent run.

No remote inference over private HRM2.0 data, index repair, index rebuild,
embedding change, or held-out access occurred.

## Evaluation outcome

**No paired behavioral A/B was run.** No 12–20 task set or expected-entry-point
list was frozen, so there is no accuracy, false-positive, call-count, latency,
or token-cost comparison to report. Running a purported A/B against this
corrupt index would not be valid, and using scripted or simulated FTS output as
agent behavior would violate the task's measurement requirement. The `zg`
binary and model were not changed to force readiness.

The earlier public-source observation remains limited to one task: full FTS
identifier `searchWorkspaceIndex` found the entry point, partial `searchWork`
did not, and local `rg -F` found the definition and callers. That establishes
the fallback's capability for that query, not that the new guidance makes an
agent choose it more often.

**Decision: INCONCLUSIVE** for the guidance patch's behavioral effect. Keep the
patch as a low-complexity routing hypothesis until a valid paired run can be
performed; do not describe it as an established model-quality improvement.

## Smallest path to a valid paired run

At an authorization gate, request an already-approved local copy of the
configured Potion model artifact. The compatible library candidate already
present can be selected explicitly for an isolated build; do not change the
default environment as part of this retrospective. Then build only a disposable
public/synthetic fixture index under `/tmp`, verify that index read-only, and
freeze 12–20 tasks, expected entry points, a correctness/false-positive rubric,
and run protocol before starting agents. Use fresh isolated contexts, vary only
the guidance, counterbalance condition order, and keep expected entry points
out of prompts. Report paired per-task outcomes and uncertainty. The existing
workspace index does not need repair or replacement. Do not download artifacts
or retry indexing under the current network/authorization policy.

## Failures, retries and review

Sanitized failure classes confirmed in the run records:

- missing TypeScript dependency, followed by offline cache miss;
- `sharp` install lifecycle failure, followed by an offline `--ignore-scripts`
  install and a successful build;
- installer tests initially blocked by sandbox `listen EPERM` (55/80), then
  passed outside the sandbox (80/80);
- Rust test link failure first from missing `-lzvec_c_api`, then from undefined
  symbols in the default/older library; a later offline link-smoke succeeded
  with the preinstalled symbol-compatible library explicitly selected;
- missing local Potion artifact and failed artifact requests;
- current indexed search failing checksum verification.

The earlier independent review found no P0/P1; its P2s were unmeasured agent
behavior, narrow cost/false-positive evidence and limited prior-work
verification. A focused follow-up confirmed MCP/CLI guidance parity and fixed a
P3 rollback-doc omission. A fresh-context OpenAI reviewer checked this
retrospective's timeline interpretation, leakage, readiness conclusion and
stopping decision against the sanitized report, without inspecting raw session
content or running an A/B. The reviewer confirmed the INCONCLUSIVE decision and
no-run rationale. On the unexecuted A/B design, the reviewer found the pairing
controls are a useful start, but scoring adequacy is not yet reviewable because
no task set, expected-entry-point list, scoring rubric, or run protocol was
frozen. Findings and disposition:

- **P2, worktree snapshot clarity:** resolved by stating that both the
  pre-existing untracked `research/` tree and this report were outside the
  commit at audit time.
- **P3, displayed time precision:** resolved by marking the wall interval and
  long-item human-readable durations approximate where displayed timestamps
  were rounded; underlying event elapsed fields remain as recorded.
- **Experiment design/scoring limitation:** before any future run, freeze a
  correctness and false-positive rubric, isolate fresh contexts, counterbalance
  condition order, and keep expected entry points out of agent prompts. With
  only 12–20 tasks, report paired per-task outcomes and uncertainty instead of
  treating a small aggregate difference as conclusive. These recommendations
  do not change this run's INCONCLUSIVE result.
- The reviewer could not independently verify underlying environment claims
  or actual model identity from the sanitized report alone. Those remain
  bounded by the primary read-only checks summarized above; no model identity
  is claimed for an A/B that did not run.

Raw local session files stay outside the repository and review bundle.
