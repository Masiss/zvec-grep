# HANDOFF — zvec-grep RAG R4 → RAG MVP (as of 2026-11-18)

> **Historical handoff — superseded 2026-11-20.** The current entry point is
> [`rust/docs/r4-external-agent-handoff-20261120.md`](rust/docs/r4-external-agent-handoff-20261120.md).
> This document preserves the 2026-11-18 R4 investigation and its receipts;
> its proposed method-level index action and task-correctness prerequisite are
> not current authorization/status. Do not use its “next authorized action”
> without reconciling against the newer handoff.

---

## 1. Current state in one paragraph

The RAG pipeline (`DEFINE → DISCOVER → EXPAND → SELECT/PACK → VERIFY → FINAL`)
exists, is instrumented, and is **fully inside the R0 resource contract**
(≤3 rounds, ≤6 tool calls, ≤32 files, pack ≤24 KiB, delivered tool-content
≤64 KiB), with hash verification, provenance, typed gaps, abstention,
cancellation and prompt-injection isolation intact. Best measured result on the
frozen dev split is **Recall@10 = 0.5278** (pool 19/25, selected 9/25, citation
1.000) against the frozen **GO target 0.5722** on a fair 24 KiB baseline of
**0.2500**. **Task correctness is NOT_MEASURED** — a frozen rubric exists but has
never been executed. The deficit is now proven to be a *representation* gap, not
a tuning gap: the evidence unit is a whole declaration range, so one pack item is
either ~700 lines of noise (high recall, unusable evidence) or ~20 lines of
precision (only ~7 fit in the pack).

**Branch:** `work/r4-deep-opt-20261118` @ `7030bd7` (pushed, not merged).
Base of the whole R4 line: `26a1562` (R1) → `711a4f7` (R4) → `53c3bf3` →
`48130d9` → `cdfb014` → `eb143a1` → `06ae60b` → `a926a78` → `45c3e19` →
`d934f92` → `3ab7b90` → `b6e76df` → `720c1ed` → `7030bd7`.

---

## 2. Environment and artifacts (exact paths)

| Item | Value |
|---|---|
| Frozen dev cases / gold | `benchmarks/rag-impact/cases/dev.json` (D01–D12, 25 gold spans) |
| Dev-independent cases | `benchmarks/rag-impact/cases/dev-independent.json` (E1–E5, NOT held-out) |
| Report contract (budgets, citation rules) | `benchmarks/rag-impact/REPORT-CONTRACT.md` |
| Corpus manifest | `benchmarks/rag-impact/corpus-manifest.json` (822 files) |
| **Baseline isolated corpus + index (old)** | `/home/masis/.cache/zvec-grep/r4-discovery-20261115/corpus` |
| **Current isolated corpus + index (with SQL symbols)** | `/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus` — **use this one** (v3, generation from the rebuild, potion-code-16m-v2, 822 files, 31 s rebuild, 82 MB) |
| Build target dir (do not change) | `/home/masis/.cache/zvec-grep/rust-target-recovered-20261105` (`.cargo/config.toml`) |
| zg binary for CLI probes | `.../rust-target-recovered-20261105/debug/zg` |
| `LD_LIBRARY_PATH` for CLI runs | `.../debug/build/zvec-rust-sys-9d9cb66e2b7fe09f/out/zvec-prebuilt` |
| Paired receipt | `benchmarks/rag-impact/results/r4b-paired-20261116.json` |
| Per-candidate trace | `benchmarks/rag-impact/results/r4-evidence-trace-20261116.json` |
| Frozen blind agent plan | `benchmarks/rag-impact/results/r4b-blind-plan-20261117.json` (sha256 `e8a33dde…`) |
| Index-truth receipt | `benchmarks/rag-impact/results/r4-index-truth-20261118.json` |

Production (v2 index, daemon, binary) was **never touched**. Index v3 (used here)
and production v2 are different environments — do not conflate them.

---

## 3. Reproduce everything (commands)

```bash
cd /home/masis/src/zvec-grep/rust
W=/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118
LD=/home/masis/.cache/zvec-grep/rust-target-recovered-20261105/debug/build/zvec-rust-sys-9d9cb66e2b7fe09f/out/zvec-prebuilt

# all four arms (hybrid raw, hybrid bounded 24 KiB, no-plan, frozen-plan)
env LD_LIBRARY_PATH=$LD TMPDIR=/tmp cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
# + per-candidate trace / per-stage stats
env LD_LIBRARY_PATH=$LD TMPDIR=/tmp R4B_DECISIONS=1 \
  cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
# index truth (source→index→searchable), gold + gold-free sample
env LD_LIBRARY_PATH=$LD TMPDIR=/tmp cargo run -q -p zg-engine --example r4_index_truth -- $W/corpus
# tests (26 workflow + 45 extraction)
cargo test -p zg-engine --lib -- rag::workflow extraction::code
cargo clippy -p zg-engine --all-targets
```

Expected output of the first command (single run, deterministic on this corpus):

```
hybrid=0.5000 hybrid_bounded=0.2500 hybrid_p1=0.2778 r4b=0.3194 plan=0.5278
plan_pool=19/25 plan_sel=9 pool=12/25 selected=6/25 citation=1.000
```

---

## 4. Reconciled metrics (single source of truth)

Metric definitions — **never mix them**:
- **Macro Recall@10** = mean over the 12 cases of (gold spans overlapped by the
  ≤10 selected units / gold spans of that case). This is the R0 definition.
- **Span count** = overlapped gold spans / 25. Diagnostic only.

| Arm | Macro R@10 | Gold spans | Pool | Selected |
|---|---:|---:|---:|---:|
| hybrid raw top-10 (retrieval reference only, no byte bound) | 0.5000 | 9/25 | 9/25 | – |
| **hybrid snippet units under 24 KiB (the fair baseline)** | **0.2500** | 4/25 | 4/25 | – |
| R4 no-plan | 0.3194 | 8/25 | 12/25 | 6/25 |
| **R4 + frozen plan (BEST)** | **0.5278** | 13/25 | 19/25 | 9/25 |
| policy-respecting oracle over the pooled union | 0.8472 | 19/25 | 19/25 | 19/25 |
| **GO target (frozen)** | **0.5722** | | | |

Historical numbers now superseded (kept for the record): 0.3611 (first R4),
0.2778 (R4b), 0.3194 (compaction), 0.4444 (call rebalancing), 0.4861 (typed
acquisition), 0.5278 (SQL-object ref ordering).

---

## 5. 25-span ledger for the best arm (what is left)

| Status | Count | Spans |
|---|---:|---|
| **Delivered** | 9 | D01(EmployeeCommand), D03, D04, D07(EmployeeCommand), D08×2, D09, D11, D12(OrganizationCommand) |
| Pooled but **unexamined** (pack filled before their turn) | 5 | D01 EmployeeCommand 215-285, D05, D07 OrganizationCommand 1057-1074, D07 sp_ 58-95, D10 CoreService 15-22 |
| Pooled only inside a widened entity unit | 5 | D02×2, D06×2 + siblings |
| **Never pooled** | 6 | D02 CoreDBContext 17-20 & 41-55, Employee.cs, EmployeeDetail.cs, D06 OrganizationCommand, D10 CoreDBContext 9-20 |

All 25 spans are **verified present in the index** and searchable
(`r4-index-truth-20261118.json`); the 6 never-pooled ones are class/entity
declarations that only exact symbol lookup reaches, and the DbContext never seeds
itself.

---

## 6. Architecture facts the next session must not re-derive

1. `zg_engine::rag::workflow` implements the whole loop with enforced bounds;
   `WorkflowOutcome` exposes `candidates`, `pool` (with `sources`/`facts`/
   `route_rank`), `traced` (one terminal `CandidateDecision` per pooled
   candidate), `selected` (with `sources`, `facts`, `role`, `content_bytes`),
   `gaps`, `stages`, `stats` (incl. delivered `tool_content_bytes` vs internal
   `ranked_content_bytes`, `gap_events`).
2. Routes available to the workflow: `hybrid(question)` (the deployable best),
   `fts(query)`, `symbol(name)` (exact, index-backed), `search_filtered(...)`
   (backend file filters). Defaults fail closed.
3. `EXPAND` derives typed references from R2 C# facts and R3 SQL facts:
   DbSet element types, invocation targets, types, `SaveChanges` contexts, SQL
   definition/table names, **plus** SQL object names found in **string literals**
   (`sql_call_string`, candidate-only provenance — resolved only if a matching
   source declaration exists, never a deployment fact).
4. **SQL symbols now exist in the index**: `extract_code` has a
   `FileFormat::Sql` branch using the PASS-gated R3 `sql_facts` parser;
   `language: None` so the index schema did not change; whole-file text fallback
   preserved so FTS/vector cannot regress. `_old` variants excluded.
5. R2/R3 facts are **computed at expansion time from source**, not persisted.
6. `MAX_REFS_PER_ROUND` truncation emits **no typed gap** (known blind spot).
7. Evidence unit = declaration range (widened, compacted to ≤2.5 KiB when a
   match anchor exists). Whole-method matches have no anchor and pass through at
   full size (measured 13.4 KiB and 15.9 KiB spans) — this is the core defect.

---

## 7. Falsified hypotheses — DO NOT RETRY

Each of these has a receipt; retrying wastes the session.

| Hypothesis | Evidence |
|---|---|
| Code-token lexical discovery as the primary route | 0.2778 vs hybrid 0.4167 |
| Reserved "hybrid slots" preserve the hybrid ranking | 0.2361 |
| Larger pool via loosening admission | pool 434→953 units, gold unchanged |
| Breadth-first subject rotation as a recall lever | no change |
| Whole-declaration spans as the pack unit | byte-bound, noise-dominated |
| Widest-unit-per-file breadth pass | 0.1944 / 0.0000 (regression) |
| Fair-share byte cap on a subject's *first* span | 0.4028 / 0.2778 (regression) |
| Enforcing the 2.5 KiB cap on whole-method windows | 0.3611 / 0.25 (loses deep gold) |
| AgentPlan route bonus | 0.3611 (displaces FTS/lexical gold) |
| Second typed call (`expand_reserve=2`) | 0.5278, pool −1 |
| Reserved pack slot for exact-symbol evidence | 0.5139 |
| Blind planner identifiers | 0 gold; symbols cost 3/6 calls for nothing |
| Interactive planner with corpus access | **BLOCKED** — reads private source; needs owner permission |

---

## 8. Evaluation contract (locked — do not change)

1. Baseline = hybrid question route with **snippet** units (`ContextItem::content`),
   greedy fit to 24 KiB, measured in `r4b_paired` on the frozen corpus/index.
2. Report three numbers separately: pool coverage, selected coverage, macro
   Recall@10 — with span counts beside each. State which definition is used.
3. Every pooled candidate has exactly one terminal decision; tests enforce
   `pool.len() == traced.len()` and the selected↔pack bijection.
4. Cumulative **delivered** tool content ≤64 KiB (internal rank reads are
   `ranked_content_bytes`, not tool content); final pack ≤24 KiB.
5. Metrics carry unit, stage, denominator, budget, git head, corpus manifest hash
   and index identity. Non-reproducible ⇒ **UNVERIFIED**, never quoted.
6. No generalization claim from D01–D12; held-out untouched and uninspected.

---

## 9. The one next authorized action (owner decision pending)

**Change the evidence unit, not the selector.** Emit **method-level index units
with the declaration header** (the R2 fact parser already yields exact method
ranges; the SQL extractor pattern from `b6e76df` shows how to add an entity kind
without a schema change). Target: one pack item ≈15 lines instead of 700, so a
10-item / 24 KiB pack can carry the 7–9 distinct methods a gold answer needs.

- **Success criterion (pre-register before running):** selected ≥12/25 at 24 KiB
  with ranks and call plan unchanged; macro Recall@10 ≥0.60.
- **Stop condition:** two experiments; if selected stays <12/25, the pack
  contract (10 items / 24 KiB) is the binding limit and needs an owner decision.
- **Why it needs an owner:** it changes index granularity (new entity kinds or
  finer fragments), which the current mission boundaries classify as an
  owner-level change. Everything else (selector, ranking, call allocation) has
  been exhausted with falsified results.

Second, independent of that: **run the frozen task-correctness protocol**
(`r4-task-correctness-contract-20261118.md`) with an external agent + separate
grader session. Until it runs, no MVP claim is available even at 0.5722. The
missing prerequisite is freezing the per-question "required steps" lists for C3.

---

## 10. Report index (in reading order)

| Document | What it establishes |
|---|---|
| `rust/docs/rag-audit-20261116.md` | forensic audit; reconciles every historical number to a commit/stage/metric; defects A1–A3 |
| `rust/docs/rag-measurement-integrity-20261116.md` | trace invariants, snippet baseline, tool-content accounting; measurement gate PASS |
| `rust/docs/r4-query-feasibility-and-selection-audit-20261115.md` | raw question 0/25, vector 5/25, hybrid 8/25; embed/plan/alias feasibility |
| `rust/docs/r4-gateab-20261117.md` | contract-budget compliance; blind plan (Gate B NO-GO) |
| `rust/docs/r4-selection-feasibility-20261118.md` | policy oracle 14/25 → budget is not the wall |
| `rust/docs/r4-deep-investigation-20261118.md` | complementary arms; union pool 19/25; ordering is the loss |
| `rust/docs/r4-interactive-typed-20261118.md` | typed call rebalancing 0.4444 → 0.4861; interactive planner BLOCKED |
| `rust/docs/r4-index-truth-audit-20261118.md` | C# index INDEX_CORRECT (independent extractor + real-route read-back) |
| `rust/docs/r4-sql-symbols-20261118.md` | SQL symbols added without schema change; baseline 0.1667→0.2500, R4 unchanged |
| `rust/docs/r4-index-utilization-20261118.md` | string-literal SQL objects → 0.5278 (current best) |
| `rust/docs/r4-deep-optimization-20261118.md` | **latest**: 2 hypotheses falsified, representation gap proven, correctness rubric |
| `rust/docs/r4-task-correctness-contract-20261118.md` | frozen C1–C6 rubric + protocol (NOT_MEASURED) |

---

## 11. Working rules for the next session

- One run per arm; record host state; never re-measure to get a nicer number.
- Never merge, release, install, or touch the production index/daemon/model.
- Never inspect held-out; never send private source to a remote provider.
- Freeze + hash any plan or answer artefact **before** comparing to gold.
- If a measurement looks inconsistent with a previous report, reconcile the
  definition/stage first — three of the last five "contradictions" were
  reporting artifacts, not engine defects.
