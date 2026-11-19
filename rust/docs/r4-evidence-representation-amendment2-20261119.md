# R4 evidence representation — factual amendment 2 (2026-11-19)

This versioned amendment supersedes amendment 1's pending-E1 next action. It
preserves the original E1 preregistration and the E1 raw receipt. E1 is rejected;
E2 and its pre-run amendment are recorded separately in
`r4-e2-prereg-20261119.md` and `r4-e2-prereg-amendment1-20261119.md`.

## E1 — fragment-only candidate projection (rejected)

Run ID `E1-fragment-anchor-20261119`; branch `work/r4-evidence-representation-20261119`,
base HEAD `0c9ba7ed26fe7c4ccfff4b06165ea85242dc95ab`; corpus/index, frozen plan,
query, model and cap hashes are in the external preflight. Raw artifacts are
preserved at
`/home/masis/.cache/zvec-grep/r4-evidence-20261119/r4-e1-20261119/attempt-01/`.
`artifacts.sha256` covers the receipt, raw trace, stdout/stderr and `time.txt`;
`summary.json` records per-case attribution and query drift. The immutable
measurement-corrected baseline remains at
`/home/masis/.cache/zvec-grep/r4-evidence-20261119/receipt-baseline32/attempt-03/`.

On the same D01–D12 frozen set, baseline → E1:

- Parent-hit reachability **19/25 → 18/25**; raw matched-fragment reachability
  **13/25 → 12/25**; workflow candidate-pool overlap **19/25 → 12/25**;
  selected **8/25 → 7/25**; planned macro **0.4861 → 0.4028**.
- Frozen E1 acceptance (selected ≥12/25, macro ≥0.60) failed. E1 is rejected;
  no threshold or input was changed.
- All caps were met: max **6 calls**, **3 rounds**, **32 distinct source reads**
  in every case, **24,122 B** selected (≤24,576), and **25,472 B** workflow
  source-content-plus-gap estimate (≤65,536); zero violations. The estimate
  excludes serialized tool framing and actual external-agent delivery.
- E1 changed one or more typed-reference query strings in **5/12** cases
  (D04/D07/D08/D09/D12); D07/D12 substituted `CoreService` for the baseline
  `sp_UpdateOrgTotalEmployee` lookup. Thus it was not an isolated evidence-range
  projection after expansion. Gap trace has 24 omitted-reference details, 20
  reference-scan truncation details, 96 source-read truncation details, and
  three `invalidrange` gaps across the 12 cases.

Manual context audit found a decisive failure: D01 asks which Form 1 personal
update branch is taken. Its selected controller span `:524–544` contains a
later lock check, but omits the `Form == "1"`/`Perform` branch at the beginning
of gold C1 `:459–620`; required C2 `EmployeeEditThongTinCaNhan` evidence
`EmployeeCommand.cs:215–285` was also absent. Any-overlap recall counted the
partial controller excerpt as C1 coverage, but it cannot establish the frozen
claim. E1 therefore fails both score and critical-context acceptance. Its
E1-only `R4B_FRAGMENT_ANCHOR` mode has been removed; receipts remain unchanged.

Run measurement: `/usr/bin/time -v` wall **5:25.70**, user **311.79 s**, system
**17.67 s**, peak RSS **748,652 KiB**, process swaps **0**, filesystem inputs
**82,704**, outputs **10,632**. Mid-run host load was elevated (10-second CPU
pressure **32.56%** while two unrelated `flutter_tester` processes each used
about one CPU); E1 itself used about one CPU and peaked at **679,059 KiB PSS**.
This is a single run, not a performance claim. Pre/mid/post resource snapshots
and hashes are beside the receipt.

E1 did not serialize a per-span verification bit/hash for its planned arm; the
receipt's citation score 1.000 refers to the unplanned R4b arm, not E1. Do not
reinterpret it as a plan-citation result. The E2 harness adds per-selected-span
hash and verification fields.

## E2 status

The initial E2 preregistration proposed parent-scope reference extraction while
keeping the fragment as final evidence. The E1 D01 audit showed that this alone
would preserve the very header/branch loss that caused the critical-context
failure. Before any E2 run, versioned preregistration amendment 1 therefore
expands the one hypothesis to **separate candidate anchor from enclosing
structured context**: retain fragment provenance, resolve the smallest
containing indexed entity for reference extraction and declaration-level
selection, and serialize both ranges. The frozen E2 thresholds and resource
caps are unchanged.

The E2 implementation has focused workflow coverage; its single frozen
D01–D12 run is pending. Task-correctness remains NOT_MEASURED/BLOCKED: no
privacy-valid independent answerer and separate grader were available, and no
external provider was used.

No index, corpus, model, public MCP/CLI, or frozen evaluation input was changed.
---

The older amendment and raw receipts are historical evidence; this file is the
current factual checkpoint for E1 and the pre-run E2 amendment.
