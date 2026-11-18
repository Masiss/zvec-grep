# R4 selection feasibility & controlled improvement — Gates 1–3

Branch `work/r4-selection-20261118` (from `a926a78`). Frozen E3 plan
(`r4b-blind-plan-20261117.json`, sha256 `e8a33dde…`) unchanged; discovery and
pool untouched. 25 workflow tests pass; citation accuracy 1.000 everywhere.

## Gate 1 — selection feasibility (oracle upper bound): **PASS with headroom**

Reproduced E3 exactly: plan arm macro **0.1389**, pool **14/25** distinct gold
spans reachable, selected **4/25**.

Offline oracle (gold used only here, never in runtime): from the frozen E3 pool,
per case, under the contract bounds (10 items, 24 KiB, verified units,
compaction ≤2.5 KiB/unit — a compact window overlapping any part of a gold span
counts as coverage under the frozen overlap rule):

| Case | Gold | Gold in pool | Oracle-selectable |
|---|---:|---:|---:|
| D01 | 2 | 2 | 2 |
| D02 | 6 | 3 | 3 (3 gold live in ONE file) |
| D03 | 1 | 1 | 1 |
| D04 | 1 | 1 | 1 |
| D05 | 1 | 1 | 1 |
| D06 | 3 | 2 | 2 |
| D07 | 3 | 1 | 1 |
| D08 | 2 | 2 | 2 |
| D09 | 1 | 0 | 0 |
| D10 | 2 | 0 | 0 |
| D11 | 1 | 0 | 0 |
| D12 | 2 | 1 | 1 |
| **total** | 25 | **14** | **14/25** |

**Oracle macro upper bound = 0.5833 ≥ GO 0.5722.** So the pack budget is *not*
the binding constraint: with perfect selection, the frozen pool suffices for
the R0 target. The gap is entirely pool→pack conversion.

Loss attribution (from the plan-arm trace): all 10 lost gold candidates were
`unexamined` — the 10-item budget filled before their turn. They are
**entity-range units** (whole-class ranges like lines 14–1038) whose match
covers the entire range, so compaction has no anchor and cannot shrink them;
they also share one route rank, so ordering cannot separate them.

## Gate 2 — controlled selection improvement: **targets not met** (3 experiments)

| Exp | Hypothesis | r4b (no plan) | plan arm | plan selected | Verdict |
|---|---|---:|---:|---:|---|
| baseline | — | 0.3194 | 0.1389 | 4/25 | — |
| E1 | per-file representative = widest unit | 0.1944 | 0.0000 | 0/25 | **falsified** (both arms regress) |
| E2 | split entity ranges into typed declarations (R2/R3 facts), inherit provenance | 0.2778 | 0.1389 | 4/25 | falsified for recall (score ties: ~30 same-rank methods, no signal to pick the gold one) |
| E3 | E2 + plan-term scoring (blind plan's own query/symbol words) | 0.2778 | **0.2083** | 4/25 | real macro gain, target still missed |

Final shipped state: E2 decomposition is retained but **gated on plan presence**
(without a plan there is no signal to choose among sub-units and splitting
measurably degraded the no-plan arm 0.3194→0.2778); E3 plan-term scoring kept.
With the gate, the no-plan arm is restored to **0.3194** and the plan arm is
**0.2083** (+0.07 over E3).

**Why selected stayed at 4/25 (measured):**
1. **Multi-gold files vs the per-file cap.** D02's three pooled gold spans live
   in `EmployeeCommand.cs`; the pack admits at most `2+2` spans of one file, and
   the two-pass breadth ordering fills the remaining items with other files'
   candidates first. Covering D02's 3/6 requires three items from one file —
   a policy the current selector never executes.
2. **No discriminative signal among same-file sub-units.** All methods of one
   entity unit inherit the parent's route rank; without gold knowledge the
   engine cannot tell the gold method from its ~30 siblings. Plan terms help
   (+0.07) but select other Employee* commands instead.
3. The 10 items the pack does carry are dominated by single-span subjects.

**Gate 2 verdict: NO-GO against its intermediate target** (selected ≥10/25 not
reached; best 4/25). Not INFEASIBLE by budget — the oracle proves budget
headroom — but infeasible with the signals available to a blind selector.

## Gate 3 — paired evaluation (locked contract, single run per arm)

| Arm | Macro R@10 | Pool gold | Selected gold |
|---|---:|---:|---:|
| hybrid snippets ≤24 KiB (baseline) | 0.1667 | 2/25 | – |
| R4b, no plan (unchanged) | **0.3194** | 12/25 | 6/25 |
| E3 plan arm, before selection work | 0.1389 | 14/25 | 4/25 |
| E3 plan arm, after E2+E3 selection work | **0.2083** | 14/25 | 4/25 |
| oracle upper bound | 0.5833 | 14/25 | 14/25 |
| GO target | 0.5722 | | |

Resources (unchanged budgets, all arms): ≤6 calls, ≤32 files, ≤160 reads,
pack ≤24 KiB, delivered ≤26.6 KiB, gap delivery coalesced, citation 1.000.
Task correctness **NOT_MEASURED**. Single run per arm; per-case rows and host
state in `benchmarks/rag-impact/results/r4b-paired-20261116.json`; per-candidate
decisions in `r4-evidence-trace-20261116.json`.

## Overall verdict

**NO-GO** (against the mission's intermediate and product criteria). Proven:
- The 24 KiB/10-item budget is **not** the wall; the frozen E3 pool admits an
  oracle 0.5833.
- Selection conversion is blocked by **policy** (per-file cap + breadth
  ordering vs multi-gold files) and **signal** (no blind way to rank same-file
  sub-units), not by bytes.
- Blind plan terms are a real but weak ranking signal.

## One next step (proposal, not started)

A **subject-aware allocation policy**: allow a file to take up to k pack items
when its candidates are top-ranked and the file's sub-units are
distinct declarations (k ≈ 4), replacing the fixed 2+2 cap and global breadth;
evaluate with the same frozen E3 plan and oracle. Success criterion: selected
≥10/25 and plan-arm macro above the no-plan arm 0.3194. If it fails, the
remaining gap needs either a plan with corpus access (planner may search) or an
owner decision on the pack shape — do not iterate further on blind plans.

## Reproduce

```bash
W=/home/masis/.cache/zvec-grep/r4-discovery-20261115
cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
R4B_DECISIONS=1 R4B_CASES=D02 cargo run -q -p zg-engine --example r4b_paired -- $W/corpus
cargo test -p zg-engine --lib -- rag::workflow --test-threads=1
```
