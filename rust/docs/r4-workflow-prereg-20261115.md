# R4 workflow — pre-registration (R2+R3 integration, prototype)

Status: **FROZEN BEFORE MEASUREMENT.** Branch `work/r4-workflow-20261115`.
Not merged, not released, no production change.

## 1. Scope

Integrate the existing R2 C# facts and R3 SQL facts into one bounded workflow
module (`zg_engine::rag::workflow`) and measure it end to end against a paired
baseline on the frozen development cases.

Stages: DEFINE → DISCOVER → EXPAND → CHECK → FINAL, with a single corrective
round, explicit gaps, abstention, and enforced caps (rounds, tool calls, distinct
files, source reads, items, bytes, cancellation).

Not in scope: new retrieval heuristics that repeat a NO-GO (candidate expansion,
cosine re-ranking, selection micro-tuning), held-out, GQ5, MCP/CLI surface,
release, merge.

## 2. Frozen inputs

- corpus: `benchmarks/rag-impact/corpus-manifest.json` (822 files),
  `source_root` read-only;
- cases: `cases/dev.json` D01–D12 (25 gold spans, 9 files); held-out untouched;
- budget: ≤24 KiB / ≤10 items selected, ≤32 files, ≤12 tool calls, ≤2 rounds,
  ≤64 source reads;
- metric: any-overlap span coverage against gold spans, exactly as B0.

## 3. Arms (frozen)

| Arm | Behaviour |
|---|---|
| `fixed_rg` | Today's deployable baseline: search the question's code tokens with rg, take the first 10 matched ranges, no widening, no expansion, no verification |
| `workflow` | DEFINE/DISCOVER/EXPAND/CHECK/FINAL as implemented, with widening, typed expansion, hash verification and abstention |

Reference points from frozen receipts are reported separately and are **not**
re-measured or treated as paired: B0 oracle 0.4722, fixed deployable FTS 0.3750,
fixed rg 0.3194, reference-union coverage 0.875 (H6).

## 4. Predictions (frozen)

| # | Prediction |
|---|---|
| P1 | `workflow` span recall@10 exceeds paired `fixed_rg` by ≥ 10 percentage points |
| P2 | Citation accuracy = 1.0: every selected span is hash-verified; zero stale content is selected |
| P3 | `workflow` reads at least as many source bytes as `fixed_rg` (it reads to rank); recall is bought with I/O, not free |
| P4 | Adversarial behaviours hold: no-evidence question abstains with 0 selected; `max_tool_calls=1` reports a budget gap and ≤1 call; cancellation abstains; duplicate queries are counted and suppressed |
| P5 | Missing required question code tokens produce `missing_code_tokens` abstention rather than a confident answer |

## 5. Acceptance (frozen; not edited after measurement)

- **GO**: P1–P5 hold AND workflow recall ≥ 0.55 (i.e. ≥ B0 oracle 0.4722 plus
  10 pp would be 0.5722; 0.55 is the minimum useful threshold below the target,
  and any result between is reported explicitly as below the frozen R0 target).
- **PARTIAL**: P2, P4, P5 hold but P1 misses the +10 pp margin.
- **NO-GO**: citation accuracy < 1.0, stale content selected, or no recall
  improvement over `fixed_rg`.

The R0 owner target (Evidence Recall@10 ≥ B0 + 10 pp) is unchanged. If the
workflow lands below it, the result is reported as a target miss, not a
re-baselined pass.

## 6. Measurement hygiene

One run per arm/condition; host load, memory availability and PSS are recorded
before and after. Latency is reported as observed, not as p95. No numeric
claim is made for recall on any split other than D01–D12.
