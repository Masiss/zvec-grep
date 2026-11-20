# R4 adapter technical audit — findings, corrections and benchmark readiness (2026-11-20)

**Base: `e62963a` → audit HEAD `3f1d87e`** (branch `work/r4-evidence-representation-20261119`, local, unpushed). Scope: verify the `zg-r4-adapter` implementation is correct, safe, lightweight and trustworthy enough for the three-arm external-agent benchmark; fix defects proven by reproduction; produce an owner-side review bundle. No retrieval research was reopened; no frozen case, gold, checklist, threshold or acceptance target was changed; no private source left the machine.

## 1. Findings

| # | Severity | Finding | Disposition |
|---|---|---|---|
| F1 | **CRITICAL** | Concurrent CLI processes admitted calls against a stale ledger and could exceed the cumulative budget. Reproduced before the fix: 8 processes racing the final call slot → **5 admitted** (≈5× budget bypass) plus 3 raced writes. | **Fixed + pinned.** All mutating operations now hold an OS-level `flock` (advisory; kernel-released at process death) across reload→admit→execute→commit with a 120 s fail-closed bound. Post-fix controls: 12 racing processes → exactly **1 admitted, 11 refused**, ledger intact; 10-process mixed load never exceeds the cap. Rust contract tests `concurrent_processes_cannot_exceed_the_last_call_slot`, `concurrent_processes_keep_ledger_within_budget_under_mixed_load`. |
| F2 | **HIGH** | Final evidence verification could be blocked by an exhausted call budget, violating the R0 requirement that final verification stay possible within frozen limits. | **Fixed + pinned.** `verify` now consumes no investigation slot (its envelope bytes are still fully accounted). Test: `final_verification_is_always_possible_after_budget_exhaustion` — after all six slots are spent, verify still revalidates the pack inside the delivered budget. |
| F3 | **MEDIUM** | Excluded-variant markers (`_old`, `_old1`, `NewSolution`) were hard-coded in the adapter — corpus-specific behavior presented as generic. | **Fixed + pinned.** Markers are session configuration (default = frozen scope-manifest list; `--exclude` CLI override). Test: `session_exclusion_markers_are_configuration_not_engine_behavior`. |
| F4 | **MEDIUM (prior, re-verified)** | The two defects fixed in the previous mission (compact-vs-pretty byte measurement; phantom pack on degraded evidence) were re-verified with tests independent of implementation internals: `delivered_budget_overflow_degrades_to_explicit_error_envelope` now asserts `delivered_bytes <= 64 KiB` exactly, and `evidence_refused_for_budget_stores_no_phantom_pack` asserts no pack record survives a refused call. | Confirmed fixed. |
| F5 | **MEDIUM (documented interpretation)** | `--round` is agent-declared. The adapter enforces bounds (1..=3) and non-decrease, but cannot know the agent's true iteration structure; this is an *interpretation* of the R0 workflow-stage notion, declared before any benchmark result. | Documented; the mediated harness may pin a round policy in preregistration. No post-hoc redefinition. |
| F6 | **MEDIUM (documented, no code change)** | The facts tool is not self-bounding: a method-filtered facts call on `EmployeeCommand.cs`/`EmployeeEditThongTinCaNhan` delivers ~54 KiB (84 % of the delivered budget). Every envelope exposes the remaining budget, and the gold-free control completes comfortably, but a naïve agent can burn most of its budget on one call. | Recorded with recommendation (optional field-level filtering), not implemented — no evidence of a validity impact. |
| F7 | **MEDIUM (resource, recorded as measured)** | Process-per-call cost: indexed FTS ≈ 0.57 s, hybrid/vector ≈ 1.35 s per call (index + query-embedding model load per process), per-process RSS ≈ 305–416 MB (freed after each call); arm-b workflow warm median 5.6 s (single cold observation 15.0 s). Bounded local ops are fast (status/verify/evidence 0.01 s, rg 0.03 s, facts 0.04 s). The plan's warm p95 ≤1 s gate holds for bounded ops and FTS but is exceeded by hybrid/vector under the process-per-call model. | Reported as measured; no acceptance target adjusted. R0 benchmark gates are call/file/byte-based; latency will be reported per arm alongside inference time. A persistent-engine variant is a possible future limited correction, not needed for benchmark validity. |
| F8 | **LOW** | `zg-r4-adapter` is a workspace member, so `--workspace` builds compile it; default product builds (`default-members`) are unaffected and the full default suite passes (803/803 with two pre-existing parallel-lock flakes in `config::tests`, unrelated to this worktree — they pass in isolation and in most full runs). | Documented. |
| F9 | **LOW** | `trace.jsonl` is appended after the ledger commit; a crash in that window leaves the trace behind the ledger. | Documented: the ledger is authoritative, the trace is an audit artifact, and the receipt hashes the trace as-is. Crash controls confirm the ledger itself is always all-or-nothing. |

## 2. Verified non-defects (suspicion rejected with evidence)

- **"117 files violates the ≤32 rule"** — no. `WorkflowStats.files_seen` counts pool *visibility* under an internal `max_files×8` candidate bound; the R0 ≤32 bound applies to delivered files, enforced at selection (smoke delivered 7 distinct files) and by the adapter's own file budget. `source_reads` (32, capped) is the read bound and is reported separately.
- **Ledger authority vs untrusted answerer** — the ledger is authoritative *within the trusted adapter*; against an untrusted answerer the security boundary is now the mediated driver (below), not instructions.
- **CLI-level rejections** (clap/unknown-session errors) occur before admission and outside the ledger; the mediated driver treats any non-envelope output as a contract error and halts the session, so they cannot become an accounting bypass.
- **Search refresh semantics** — `auto_update=false` with `refresh=None` does not auto-refresh (engine `service.rs` admits only on `refresh.map_or(auto_update)`); all adapter routes now pin `Some(Off)` explicitly to remove the legacy ambiguity.

## 3. Exact R0 accounting semantics (reconciled)

- **Calls**: one per admitted investigation tool invocation (`search`, `symbol`, `facts`, `evidence`, `workflow`), consumed even on execution error; gate rejections (terminal / no-calls-left / profile / invalid-round) refuse without consuming a slot but their error envelope is byte-accounted. `status` and `verify` consume no slot; their bytes are accounted. Nested engine calls (arm-b workflow internals) are *not* external calls: they are reported explicitly via `WorkflowStats` + an `internal_accounting` block.
- **Rounds**: caller-declared, bounded 1..=3, non-decreasing (F5).
- **Files**: distinct files whose *content is delivered* (search excerpts, facts, evidence items) cap at 32; internal reads (hashing, ranking) are not delivered content.
- **Delivered bytes**: the exact final serialized envelope (pretty JSON written to stdout), including wrappers, metadata, gaps and errors; cumulative cap 64 KiB enforced by measuring the exact bytes delivered (F4 regression tests assert the invariant).
- **Evidence pack**: ≤24 KiB / ≤10 items, stored as the real `SpanPack` only after the delivered-budget fit check; `verified` only on hash match; revalidation gates `verified` on zero gaps.

## 4. Answerer isolation (mission §6)

Implemented as **controller-mediated execution** (`benchmarks/rag-impact/tools/r4_bench_driver.py` + selftest): the answerer is a pluggable conversation backend whose only outputs are tool-call *strings*; the driver is the sole executor, validating every request against the frozen tool contract (six verbs, typed argument schemas, path grammar, no shell metacharacters) before spawning anything. The answerer never receives the corpus or session paths, gold, checklists or handoffs; there is no code path from answerer output to a shell or file read. The pi-harness subagent-with-shell path is **rejected** as a valid benchmark answerer (its shell access cannot be contained); the provider tool-calling loop through this driver is the supported path once inference is authorized.

Negative controls (all pass, zero session state change): unknown tool `shell`; `mode` injection `rg; rm -rf /`; traversal `../../etc/passwd`; absolute path; unknown argument; out-of-range round; 11 evidence items. Gold-free usability control on the real corpus: scripted arm-c policy completes a **verified pack in 3 calls / 16,836 delivered bytes / 2 rounds**, well inside budget. Cross-profile control: the same policy under arm-a gains no hidden capability (profile gate, no packs).

Residual limitation: real-model isolation quality (prompt-injection resistance of the model itself) is an inference-time property that can only be measured when the private-source inference gate opens; the *mechanical* isolation is verified here.

## 5. Crash/recovery verification

Kill -9 during arm-b workflow execution at 1/3/6 s → ledger parses, budget unchanged, nothing delivered (fail-closed); at 10 s → fully committed all-or-nothing. Sessions remain usable after any crash (lock released by the kernel). Rust control `killed_process_leaves_ledger_intact_and_budget_never_loosens` sweeps 15 kill points asserting: ledger always parses; call accounting is all-or-nothing; delivered bytes never regress; the session stays callable.

## 6. Resource measurements (single host, warm, n=5 each)

status/verify/evidence 0.01 s; rg 0.03 s; facts (small file) 0.04 s; indexed FTS median 0.57 s (max 0.60); hybrid/vector median 1.35 s (max 1.37); arm-b workflow median 5.63 s (max 5.85); per-process RSS 305–416 MB on index-touching calls, 33–45 MB otherwise. Earlier single-run smoke figures (search 2.4 s, workflow 15 s) were cold observations; both cold and warm numbers are reported, none promoted to a p95 claim. Host state recorded per the workspace measurement preference at receipt time.

## 7. Verdicts

| Area | Verdict |
|---|---|
| Adapter contract integrity | **PASS** (29/29 synthetic contract tests, real-binary boundary) |
| Cumulative budget integrity | **PASS after F1/F2 fixes** (concurrency + crash controls) |
| Concurrent/crash behavior | **PASS after fix** (flock transaction; all-or-nothing commit verified) |
| Answerer isolation | **READY (mechanical)** via mediated driver + negative controls; model-level isolation NOT_MEASURED pending inference |
| Evidence correctness | **PASS** (R1 semantics preserved; verified/observed; stale gaps) |
| Resource qualification | **PARTIAL**: bounded ops + FTS within gates; hybrid/vector exceed the ≤1 s warm target under process-per-call (F7) |
| Three-arm harness readiness | **READY_FOR_BENCHMARK** (all three arms execute under the frozen budget on the isolated corpus) |
| Actual inference authorization | **BLOCKED** (owner decision outstanding: openai egress for private source) |
| Answer quality (C1–C6/C3) | **NOT_MEASURED** |

`READY_FOR_BENCHMARK` means the harness and accounting are fit to run the comparison once the owner authorizes a specific inference environment; it is not a product qualification and not `PRODUCTION_READY` (experimental crate, non-default feature, unpushed experimental branch).

## 8. What the owner must still decide

1. Authorize private-source submission for frozen D01–D12 to the identified `openai` endpoint (model identity to be fixed in the preregistration, with response model/version captured from API metadata — never keys), **or** designate another approved inference environment. The mediated driver is the answerer boundary; gold/checklists are released only to the separate grader context.
2. Push authorization for the 7-commit experimental branch remains outstanding (nothing pushed by this mission).
