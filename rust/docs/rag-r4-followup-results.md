# R4 follow-up results — multi-call seeds and file packing

Status: **R4-A FAIL; R4-B FAIL (cost-only). No candidate GO.**
Date: 2026-11-08. Pre-registration:
`benchmarks/rag-impact/R4-EXPERIMENT-PREREGISTRATION.md`.
Frozen inputs/targets were not changed; held-out was not inspected.

## Results (12 development cases, pack-level diagnostic)

| Arm | Recall@10 | Evidence files/case | Evidence tokens/case |
|---|---:|---:|---:|
| B0 best-call pack | 0.4722 | 7.75 | 2,845 |
| R4-A multi-call seeds | **0.2917** | 4.08 | 3,189 |
| R4-B file clusters | **0.2917** | **3.00** | **1,420** |

- R4-A vs B0: recall **−18.06pp** (target +10pp) → FAIL; files −47.3%;
  tokens +12.1%.
- R4-B vs B0: recall **−18.06pp** → FAIL; files −61.3%; tokens −50.1%.
- Task-correctness proxy remains below B0; no recall target met.
- The B0 pack-level definition uses the best call's top-10 units. It is a
  diagnostic comparison, not a redefinition of the frozen B0 receipt.

## Per-case movement

- Recovered: D01 (0.5), D11 (1.0).
- Stable: D04, D08 (1.0); D02/D03/D05/D12 remain 0.
- Regressed to 0: D06 (was 0.333), D07 (0.333), D09 (1.0), D10 (0.5).

## Interpretation

- Multi-call retention recovered two seed-set losses, but the per-call quota
  still admitted low-value units and displaced files that the B0 best call
  found. Expansion could not recover D06/D07/D09/D10.
- File-cluster packing delivered the intended cost reduction (3 files, 1,420
  tokens) but did not change recall; it cannot repair a bad selection.
- The dominant failure remains **seed selection**, not packing/dedup. A future
  attempt should rank units by method/entity evidence and exact identifier
  matches, preserve the best evidence from every call, and only then pack by
  file; it must not tune to HRM-specific names.

## Disposition

Both experiments FAIL the frozen benefit targets. Keep the R1 lightweight mode.
No additional R4 run was made. A further attempt requires a new
pre-registration; the B0/candidate receipts and targets remain historical
evidence.
