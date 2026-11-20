# R4 external-agent adapter — implementation & qualification (2026-11-20)

**Status: adapter implemented and contract-qualified on synthetic fixtures;
end-to-end smoke verified on the frozen isolated corpus for all three arms.
Gate B remains OPERATION_BLOCKED for real answerer runs (private-source egress
authorization outstanding); Gate C remains NOT_MEASURED.** This mission
implemented the private experimental integration; it did not run any
answer-quality experiment and did not change any default engine contract.

## 1. What was built

New private experimental crate **`rust/crates/zg-r4-adapter`** (binary
`r4-adapter`, not part of default-members, `publish = false`):

- **Session model.** `session new` pins corpus root, profile and case; the
  authoritative ledger (`ledger.json`, atomic replace) plus an append-only
  trace (`trace.jsonl`) enforce the frozen R0 cumulative budget across
  separate process invocations. No command resets the ledger; terminal
  (`finalize`/`cancel`) refuses further investigation calls.
- **Frozen budget (non-configurable):** ≤6 calls, ≤3 rounds, ≤32 distinct
  delivered files, ≤64 KiB cumulative delivered envelope bytes, ≤24 KiB / ≤10
  items per evidence pack. Every admitted call consumes a slot even on
  error; gate rejections (terminal, no_calls_left, profile_forbidden,
  invalid_round) refuse without consuming a slot but still account their
  error envelope bytes. The exact final serialized envelope is what is
  accounted (an earlier compact-vs-pretty measurement bug was found in smoke
  and fixed; a contract test now asserts the invariant).
- **Tool surface per profile:**
  - `arm-a` (product control): `search` only (hybrid/fts/vector/rg).
  - `arm-b` (engine-managed R4): `workflow` (deterministic
    `rag::workflow::run` under the baseline32 budget) + `verify`; internal
    operations are surfaced explicitly via `WorkflowStats` and an
    `internal_accounting` block (internal reads/ranking are not delivered
    tool content and are reported, never hidden).
  - `arm-c` (agent-driven): `search`, `symbol` (exact indexed declarations),
    `facts` (R2 C# / R3 SQL typed facts with method filtering and
    excluded-variant authority marking), `evidence` (R1 hash-checked spans,
    verified vs observed semantics), `verify` (source revalidation), `status`.
- **Fail-closed path handling:** workspace-relative paths only; absolute,
  traversal, symlink and out-of-root targets are rejected or produce typed
  gaps. A pack that cannot fit the remaining delivered budget is refused
  *before* being stored (no phantom pack artifacts).
- **Round semantics (declared interpretation, not a relaxation):** the R0
  contract defines rounds for engine-internal workflow stages; in an
  interactive session a round is one agent investigation iteration,
  caller-declared via `--round` (1..=3, non-decreasing). The ambiguity is
  reported here rather than resolved by relaxing any bound.

## 2. Reused vs newly implemented capabilities

Reused unchanged: indexed search (`api::context`), exact symbols
(`api::symbols`), R1 evidence surface (`api::evidence::surface`
`assemble_spans`/`revalidate_spans`), R2/R3 fact parsers
(`extraction::{csharp_facts, sql_facts}`), deterministic R4 workflow
(`rag::workflow::run`, baseline32 budget identical to the retained E2
checkpoint).

New: session/ledger enforcement, the three profiles, facts presentation with
excluded-variant authority (`_old`, `_old1`, `NewSolution` markers) and a
`--method` filter, budget-aware degradation paths, and the CLI.

Engine surface change is minimal and gated: a non-default
`r4-experimental-facts` feature adds `pub mod r4_experimental` re-exporting
`ProjectIdentity`, `parse_csharp_file`, `parse_sql_file`. Default builds and
the public API are unchanged; the full `zg-engine` lib suite passes
(803 passed / 0 failed) with the changes in place.

## 3. Contract & safety qualification (synthetic fixtures)

`rust/crates/zg-r4-adapter/tests/contract.rs`: **24 tests, all passing.**
Coverage: search accounting and hashing; symbol/facts error accounting;
profile gating without slot consumption; call-budget exhaustion; round bounds
and non-decrease; C# facts (methods/saves/assignments/invocations, method
filter); SQL facts selected vs excluded variants; fail-closed traversal /
absolute / outside / missing / unsupported paths; verified vs observed
evidence; stale-hash gaps; source-change revalidation; item/dedup/missing /
out-of-root pack gaps; delivered-budget degradation (exact-byte invariant);
distinct-file budget with explicit dropped markers; malicious source content
delivered as JSON data only; arm-b abstention, internal accounting and
verified-span output; terminal refusal and single finalize; receipt hashes;
ledger persistence across processes and non-reset; CLI round-trip
(session/search/finalize/post-terminal refusal/evidence via stdin/status/
verify).

## 4. End-to-end smoke on the frozen corpus (read-only, local)

Corpus: `/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus`
(822 files, existing v3 index, potion-code-16m-v2). Receipts preserved under
`/home/masis/.cache/zvec-grep/r4-external-agent-20261120/smoke/`
(`armc-final`, `arm-b`, `arm-a`, plus earlier debug sessions).

- **arm-c** (case D02): search → symbol → facts → evidence → verify; the
  ledger refused an evidence call that no longer fit the remaining budget
  after a large facts call, and `verify` fail-closed on the absent pack.
  Final receipt: 4 calls, 1 round, 5 distinct files, 61,929 delivered bytes
  (≤64 KiB enforced), per-call walls 2,374 ms / 49 ms / 6 ms / 0 ms;
  max RSS per call 45–421 MB (index warm-up dominates the first search).
- **arm-b** (case D02): one `workflow` call, wall 15.0 s, delivered 11,285
  bytes; 10 verified spans (21.6 KiB selected, 24,218 tool-content bytes);
  internal accounting explicit: 6 calls, 32 source reads, 117 files seen,
  513 KiB ranked internally. Engine-managed internal reads stay internal and
  are reported, matching the retained E2 containment contract.
- **arm-a** (case D02): ordinary hybrid search, wall 2.96 s; gold file
  `EmployeeCommand.cs` in top-10, controller file outside top-10.

Smoke also surfaced two real adapter defects that were fixed and pinned by
tests: (a) delivered-bytes measured on compact serialization while
delivering pretty serialization (budget could be exceeded by indentation);
(b) evidence packs were stored before the delivered-budget fit check,
leaving phantom artifacts on degraded calls.

Single-run smoke; no variance measurement was performed (per the workspace
measurement preference, walls/RSS above are one observation each, and the
resource counters were captured with `/usr/bin/time` per call).

## 5. Inference environment investigation (Gate B)

- The existing coding agent (this harness) uses a **remote third-party
  provider**: default provider `openai`, API `openai-responses`,
  base URL `https://api.openai.com/v1`, default model `gpt-5.5`
  (from `~/.pi/agent/settings.json` / `models-store.json`; other configured
  identities include `openai-codex/gpt-6.1-sol`). No local generative model
  is installed, and no additional model was downloaded, per the mission's
  hard constraints.
- **What can be verified:** provider identity, endpoint category and model
  identifiers from configuration; fresh isolated answerer sessions are
  mechanically feasible (separate contexts without gold, checklists, plans
  or handoffs); a separate grader context is feasible; all arms would use
  the same provider/model identity.
- **What cannot be verified from inside:** the exact served model
  version/revision and inference configuration behind the endpoint, and
  formal data-handling/retention terms for the submitted corpus content.
- **Isolation limitation (disclosed):** answerer sessions of this harness
  have general shell access; arbitrary filesystem reads cannot be fully
  prevented. Mitigation for a future benchmark run: answerers receive only
  the question and the adapter path (not the corpus path), are instructed
  that any direct source access is a contract violation, and all receipts
  are audited. This is a residual bypass risk that must be reported with any
  future Gate C result; it does not affect the adapter's own accounting.
- **Privacy gate (unchanged):** private HRM2.0 source may be submitted to
  the identified `openai` endpoint only after explicit owner authorization
  covering that provider/model and the benchmark data-processing scope.
  This mission did not grant it; no source, pack, prompt or trace left the
  local machine.

## 6. Gate matrix

| Gate | Status |
|---|---|
| Gate A (architecture/model inventory) | PASS (prior audit, unchanged by this work) |
| Gate B (private-source-compatible inference) | **OPERATION_BLOCKED** — provider identified (`openai`, remote); owner egress authorization outstanding |
| Gate C (answer quality C1–C6/C3) | **NOT_MEASURED** — no answerer/grader run |
| Adapter contract qualification | PASS — 24/24 synthetic tests |
| Three-arm smoke (retrieval capability only) | PASS — all three arms execute end-to-end under the frozen budget on the isolated corpus |

## 7. Next authorized action (requires owner decision)

Preregister and run the three-arm paired comparison (same model identity
across arms, fresh blind answerer sessions, frozen D01–D12, separate grader
context, C1–C6 + C3 addendum, budget receipts from this adapter) **only
after** the owner explicitly authorizes private-source submission to the
`openai` endpoint (model identity to be fixed in the preregistration), or
provides another approved, private-source-compatible inference environment.
Adapter-side work needed for that run is complete up to the execution gate.
