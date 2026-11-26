# Discovery & Selection Bottleneck Investigation

Status: `DONE / research-only`. No production change, no model change, no graph
extension, no held-out opened. Instrumentation is env-gated and compiled only
into an isolated binary. Branch: `work/discovery-selection-20261126`; commits
`49b22c2`, `bda5a1e`, plus final report commit. Artifacts:
`/home/masis/.cache/zvec-grep/discovery-selection-20261126`.

## 1. Method

- Rust corpus: frozen `clap-rs/clap@4201754a`, 12 dev cases from the prior
  mission (`cases.json` SHA-256 `c6e1a7e…`).
- C# corpus: frozen 822-file HRM2.0 corpus manifest copied into the artifact
  (0 hash mismatches), indexed with 816 files / 15,036 entities. D01–D12 dev
  cases only; old held-out not opened.
- Model: existing Potion `minishlab--potion-code-16M-v2`, unchanged.
- Instrumented binary SHA-256:
  `5a4c8201276cbebdafa7b618ab4699759d92bed13c6439b6e16ce6516b5c357b`.

Instrumentation is gated by `ZVEC_DISCOVERY_TRACE_FILE`, inserted at:

- `pipelines/indexed_search/pipeline.rs:36-40` recall depth constants,
  `220-240` stage sequence, `591+` fusion, `ranking/mod.rs:20-48,136-205`
  policy/diversity/cutoff, `context.rs:318-378` coverage priority/global fill.
- Dumped stages: `raw`, `fused`, `loaded`, `ranked`, `context_group_items`,
  `context_selected`.

Metrics distinguish:

- file recall: gold file appears anywhere in staged rows;
- entity recall: gold-span entity id appears;
- range recall: any staged range intersects a gold span;
- exact coverage: gold span fully contained in a staged range;
- noise ratio: delivered line bytes not overlapping any gold span.

## 2. Stage loss breakdown

Macro over 12 cases, `--limit 50`, current engine ranker.

| Stage | Rust file | Rust entity | Rust range | Rust exact | C# file | C# entity | C# range | C# exact |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| raw discovery | 0.944 | 0.944 | 0.944 | 0.917 | 0.820 | 0.820 | 0.820 | 0.708 |
| fused | 0.944 | 0.944 | 0.944 | 0.917 | 0.820 | 0.820 | 0.820 | 0.708 |
| loaded | 0.944 | 0.944 | 0.944 | 0.917 | 0.820 | 0.820 | 0.820 | 0.708 |
| ranked top-50 | 0.917 | 0.729 | 0.729 | 0.687 | 0.778 | 0.694 | 0.694 | 0.597 |
| context selected (50) | 0.917 | 0.729 | 0.729 | 0.687 | 0.778 | 0.694 | 0.694 | 0.597 |
| final top-10 rank | 0.674 | 0.361 | 0.361 | 0.361 | 0.583 | 0.417 | 0.417 | 0.333 |
| delivered pack, 10 items / 24 KiB | — | — | 0.250 | 0.250 | — | — | 0.181 | 0.083 |

Average raw evidence rows: Rust 400 → fused 293 → loaded 293 → ranked 50.
C# 675 → fused 360 → loaded 360 → ranked 50.

### Where evidence is lost

| Transition | Rust exact loss | C# exact loss | Cause |
|---|---:|---:|---|
| raw → fused/loaded | 0.000 | 0.000 | Entity aggregation/RRF did **not** remove gold in these dev cases. |
| loaded → ranked top-50 | −0.230 | −0.111 | Rule ranking + 50-item cutoff. |
| top-50 → top-10 rank | −0.326 | −0.264 | Final rank cutoff is the largest single ranking-side loss. |
| top-10 → 24 KiB pack | −0.111 | −0.250 | Byte packing drops or truncates already-selected gold-bearing entities; severe in C#. |
| raw → final delivered | −0.667 | −0.625 | Total downstream loss. |

Answer to the key question: **discovery is not the main bottleneck in Rust**
(94.4% raw range recall, 91.7% raw exact coverage) and is only partially a
bottleneck in C# (82.0% raw range, 70.8% exact). In both languages, ranking and
pruning discard most evidence before “discovery recall” is effectively observed.
Any evaluation that measures only the final top-10 pack will misattribute this
to discovery.

## 3. Ranking ablations on the same loaded pool

Top-10 macro recall at limit 50:

| Policy | Rust range/exact | C# range | C# exact | Mean pack bytes Rust/C# |
|---|---:|---:|---:|---:|
| pure cosine (Potion vector score only) | 0.000 / 0.000 | 0.014 | 0.000 | — |
| pure RRF fusion score | 0.403 / 0.403 | 0.500 | 0.417 | — |
| current rule ranking + diversity | 0.361 / 0.361 | 0.417 | 0.333 | 12,329 / 17,475 |
| rule ranking, diversity disabled | **0.486 / 0.486** | **0.500** | **0.417** | 12,637 / 18,112 |

Consequences:

- Pure cosine is unusable as a standalone ranker on these cases despite being
  the semantic route; it promotes large noisy entities.
- Pure RRF already beats the current rule ranker, especially in C#.
- `FILE_DECAY` diversity penalty costs **−0.125 Rust range recall** and
  **−0.083 C# range recall** for only −2.5%/−3.6% mean pack bytes. Removing it
  satisfies the preregistered “no >25% byte increase” condition.
- The current rule bonuses are not uniformly harmful; pure RRF is close but
  weaker than no-diversity rule ranking in Rust. The harmful component is the
  per-file diversity penalty, not rule scoring as a whole.

## 4. Cutoff and budget effects

### Candidate cutoff (`--limit`)

| Limit | Rust ranked range | Rust top-10 range | C# ranked range | C# top-10 range |
|---:|---:|---:|---:|---:|
| 10 | 0.361 | 0.361 | 0.417 | 0.417 |
| 25 | 0.590 | 0.361 | 0.625 | 0.417 |
| 50 | 0.729 | 0.361 | 0.694 | 0.417 |
| 100 | 0.799 | 0.361 | 0.694 | 0.417 |

Raising the recall cutoff enlarges the diagnostic pool but **does not change
top-10 delivered evidence** with the current ranker. More discovery without a
better selector is not useful here.

### Item and byte budgets (limit 50)

| Config | Rust range/exact | Rust mean bytes/tokens | C# range/exact | C# mean bytes/tokens |
|---|---:|---:|---:|---:|
| 5 items / 8 KiB | 0.180 / 0.180 | 5,274 / 1,635 | 0.167 / 0.083 | 6,250 / 1,592 |
| 10 items / 8 KiB | 0.180 / 0.180 | 7,553 / 2,414 | 0.167 / 0.083 | 6,957 / 1,802 |
| 10 items / 16 KiB | 0.250 / 0.250 | 12,336 / 3,878 | 0.181 / 0.083 | 14,342 / 3,165 |
| 10 items / 24 KiB | 0.250 / 0.250 | 15,543 / 4,579 | 0.181 / 0.083 | 22,167 / 5,583 |
| 10 items / 32 KiB | 0.222 / 0.222 | 19,916 / 6,095 | 0.181 / 0.083 | 30,244 / 7,397 |
| 20 items / 24 KiB | **0.361 / 0.319** | 22,254 / 6,731 | 0.181 / 0.083 | 22,663 / 5,790 |
| 20 items / 32 KiB | 0.292 / 0.222 | 27,988 / 8,606 | 0.181 / 0.083 | 30,796 / 7,604 |

- Rust benefits from more items but not from more bytes alone: 20 items/24 KiB
  recovers the rank-top-10 range recall, still within the byte cap.
- C# saturates: larger budgets mostly add noise because selected entity ranges
  are coarse. The byte budget itself is not the only issue; the range size is.
- Final 24 KiB packs contain **95.1% Rust** and **99.4% C#** delivered line
  bytes that do not intersect gold spans. This is the concrete noise cost of
  entity-range evidence units.

## 5. Resource measurements

| Split | Calls (12×4 limits) | Wall sum | p95 wall | CPU user+sys | Peak RSS |
|---|---:|---:|---:|---:|---:|
| Rust | 48 | 83.81 s | 2.09 s | 86.08 s | 414,820 KiB |
| C# D01–D12 | 48 | 99.54 s | 2.86 s | 101.94 s | 456,928 KiB |

C# index build in isolation: 816 files indexed, 15,036 entities, 32.90 s wall,
62.99 s user, 1.32 s sys, peak RSS 767,196 KiB. These are debug-build research
measurements, not production performance claims.

## 6. Evidence-backed conclusions

1. **Fusion/entity aggregation is clean.** Raw, fused and loaded stages have
   identical gold coverage in both languages.
2. **Ranking/cutoff is the first major loss.** Top-50 already loses 23 pp Rust
   exact and 11 pp C# exact; top-10 loses much more.
3. **Diversity penalty is a proven lossy component.** Removing it improves
   recall with negligible byte increase.
4. **Top-K alone is not the fix.** Increasing limit from 10 to 100 improves the
   pool but leaves top-10 unchanged.
5. **Context packing is a second major loss, especially C#.** Byte caps drop
   gold-bearing selected entities; the delivered pack has extreme noise.
6. **Discovery recall measured at final pack is misleading.** It conflates
   retrieval coverage with ranker and packer losses. Stage lineage should be
   the primary diagnostic.

## 7. Removal options and trade-offs

| Option | Supported by evidence | Trade-off |
|---|---|---|
| Disable/reduce `FILE_DECAY` | Yes: +0.125 Rust, +0.083 C# range recall | Less per-file diversity; more repeated-file evidence. |
| Replace current ranker with pure RRF | Partially: beats current C#, close Rust | Loses rule bonuses; still below no-diversity rule. |
| Keep rule ranking but remove diversity penalty | Best supported ranker change | Needs regression on corpus diversity outside dev cases. |
| Raise item budget 10→20 | Yes for Rust (+0.111 range at 24 KiB) | More tokens/wall; C# does not benefit. |
| Raise byte budget alone | No | More noise, no C# recall gain. |
| Change evidence unit to smaller ranges | Strongly implied by 95–99% noise and C# packing loss | Requires representation/index change, out of this mission. |
| Increase `--limit` | No top-10 gain | More recall work and trace volume only. |

## 8. Limits

- One run per condition; no variance estimate.
- 12 development cases per language; no held-out.
- Stage overlap is deterministic source-line evidence, not semantic answer
  correctness.
- Instrumentation adds JSONL writes only when env var is set; production path
  unchanged.
- C# frozen corpus had 822 manifest files but 816 indexable files; the 6 were
  not code files in this index run.
- No model, graph, production index, ranking source on `main`, or runtime binary
  was changed.

## 9. Reproduce

```bash
W=/home/masis/src/zvec-grep-rust-rag-20261126
A=/home/masis/.cache/zvec-grep/discovery-selection-20261126
python3 "$W/benchmarks/discovery-selection/tools/run_stage_traces.py" \
  --binary "$A/build/target/debug/zg" --cases "$A/rust-cases.json" \
  --corpus "$A/../rust-rag-20261126/corpus/clap" --artifacts "$A" \
  --split rust --limits 10,25,50,100 \
  --model-cache /home/masis/.cache/zvec-grep/rust-rag-20261126/model-cache
python3 "$W/benchmarks/discovery-selection/tools/analyze_stages.py" \
  --artifacts "$A" --cases "$A/rust-cases.json" \
  --corpus "$A/../rust-rag-20261126/corpus/clap" --split rust --limit 50
```
