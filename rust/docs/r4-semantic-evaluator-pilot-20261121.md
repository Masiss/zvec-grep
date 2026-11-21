# R4 semantic evaluator — final corrections & pilot readiness (2026-11-21)

Base `bacfa36` (branch `work/r4-evidence-representation-20261119`, unpushed).
This round closes the two defects the independent review reproduced (SE-01,
SE-02), audits the C3 completeness measurement over D01–D12, and runs a
controlled five-scenario evaluator pilot against the real grader.

Status: **PILOT_READY — REAL ANSWER QUALITY NOT_MEASURED.** No production
evaluator is registered; `cases/evaluators.json` is still empty.

## 1. SE-01 — correct but incomplete had no valid verdict

**Before** (reproduced): an evaluation with every claim judged `supported`,
`completeness = 0`, and a FAIL verdict was rejected by `accept`
(`FAIL must mark at least one claim unsupported`) and by the grader. The only
way to express "all true, some required part missing" was to invent an
unsupported claim, which corrupts the claim-level evidence.

**Fix**: an explicit `omissions` list in the response contract and in the
evidence document.

- `omissions` is required (possibly empty), a list of non-empty unique strings.
- `PASS` requires no omissions, `completeness = 2`, `citation_support = 2`,
  `claim_correctness = 2`, and `uncertainty/abstention ≥ 1`.
- `FAIL` is valid when it has an unsupported claim **or** at least one omission.
- `completeness = 0` requires at least one omission; `completeness = 2` forbids
  omissions.
- The grader enforces the same rules independently, so bypassing `accept` does
  not lift an incomplete answer to `PASS`.

**After** (fixtures): `correct-but-incomplete FAIL is accepted` (all claims
supported, `omissions = ["…"]`, completeness 0), while completeness 0 without
omissions, PASS with omissions, malformed/duplicate omissions, and
completeness 2 with omissions are all rejected. Grader-side: the same
correct-but-incomplete document is a **formal FAIL** (`r4_grade.py` echoes the
omissions), and the malformed variants are `UNGRADABLE`.

Frozen rubric, thresholds, gold and checklist are unchanged; only the evidence
representation gained a field.

## 2. SE-02 — stale source could enter the packet

**Before** (reproduced): when a pack item stores no content (workflow pack),
`build_packet` read the cited span from the corpus and included it even when the
corpus hash no longer matched the frozen pack hash (`hash_ok: false` but the
stale text was present). The evaluator was therefore reading whatever the file
said now, not frozen evidence.

**Fix**: fail closed.

- If the read source hash differs from the pack item's frozen hash, the span is
  reported `resolved: false`, `content: null`, `stale_source: true`, with the
  reason `source changed since freeze; stale text withheld`. No stale bytes
  enter the packet.
- If the pack item carries **no frozen hash at all**, there is nothing to verify
  against, so the source text is withheld the same way and the span is marked
  `unverifiable_source: true` (review W3).
- `accept` re-derives the span. If the source moved, it requires the packet to
  mark the span stale with no content; a packet that pretends stale text is
  current is refused.
- A stale span therefore produces a normal semantic `FAIL` (no evidence can
  support the claims), not `NOT_MEASURED`.

**After** (fixtures): stale text withheld; stale evidence yields a valid
semantic FAIL; a forged non-stale packet is refused.

## 3. C3 completeness audit (D01–D12)

Tool: `benchmarks/rag-impact/tools/audit_c3_anchoring.py` (read-only). It
re-runs the grader's own locator over the frozen metadata.

| Metric | Value |
|---|---|
| Cases | 12 |
| Required steps | 49 |
| Addressable by the frozen token locator (measured by C1/C3/C6) | 29 |
| Not addressable by that locator | 20 |
| — no backticked identifier (cannot anchor by construction) | 15 |
| — identifiers exist but none inside that case's gold | 5 |

Per-case unaddressable: D01 0, D02 1, D03 0, D04 2, D05 3, D06 2, D07 2,
D08 2, D09 3, D10 2, D11 1, D12 2.

The 20 excluded rows are not proof of impossibility in every case: some are
negative claims that a different deterministic rule could check by absence (D08
step 3), and some name an anchor in prose (D05 step 2). The claim here is the
narrow one: **the current frozen locator does not measure them**. The audit also
verifies every case's corpus bytes against the per-anchor `sha256` in the
manifest (`corpus_mismatch_cases: []` this run), so a drifted corpus is reported
rather than silently reclassifying steps.

**Is there a case where a missing required step still scores maximum? Yes.**
D07 step 3 — "Cite the helper's named procedure call and cache invalidations." —
has no backticked identifier, so the pre-registered anchoring policy excludes it
from C1/C3/C6. A D07 answer that cites only the addressable steps (1, 2, 4) and
omits step 3 grades **12/12, structural complete, formal PASS** under a PASS
evaluation (`c3-gap-d07.json`). The same mechanism applies to D05 step 2, D09
step 3 and the other `no_identifiers` rows.

This is not fixed by changing gold, checklist or thresholds (forbidden, and it
would be guessing anchors). Two changes were made instead:

1. the grader now reports `unmeasured_required_steps` with the step id, text and
   reason (`no_identifiers` / `outside_gold`), so a 12/12 no longer silently
   implies full checklist coverage;
2. the semantic `completeness` dimension plus SE-01 is the compensating control:
   the evaluator can fail an answer for a missing asked part without inventing
   an unsupported claim. `PASS` now requires `completeness = 2`, so a partially
   complete answer cannot pass through a permissive `completeness = 1`.

An omission-only FAIL does **not** lower C2: C2 now depends on unsupported
claims and structurally invalid citations, not on the verdict word. The earlier
draft dropped C2 from 2 to 1 for a fully cited but incomplete answer, which
contradicted its own rationale (review W2).

The audit is reproduced in `c3-audit.json`; the policy quote remains as written
in `r4-benchmark-readiness-20261121.md` ("steps that cannot be anchored are
reported UNADDRESSABLE and excluded — never guessed").

## 4. Evaluator pilot (5 scenarios)

Tool: `benchmarks/rag-impact/tools/r4_semantic_pilot.py`. A small synthetic
frozen world (one source file, one question asking three explicitly named
elements, two C3 steps — deliberately omitting the third element, mirroring the
D07 gap). Answers are known independently of the evaluator. The **real grader**
and the **real packet/accept workflow** are used unchanged.

| Scenario | Grounded semantic | Distinguishing evidence | Formal verdict | Structural-only (contrived PASS) |
|---|---|---|---|---|
| complete | PASS | no unsupported, no omissions, 2/2 spans with content | **PASS** | PASS |
| incomplete (all claims true, one asked element missing) | FAIL | `unsupported = []`, `omissions = ["Name"]` — an omission-only FAIL, not a fake contradiction | **FAIL** | **PASS** (12/12) |
| wrong (claim contradicts the cited span) | FAIL | `unsupported = [0]`, `omissions = ["Name","SaveChanges"]` | FAIL | PARTIAL |
| wrong-citation (valid hash, span says otherwise) | FAIL | `unsupported = [0]`, `omissions = ["Rename","Name"]` | FAIL | PARTIAL |
| stale (pack item without content, source moved) | FAIL | `unsupported = [0,1]`, all cited spans withheld (`stale_source`), no content | FAIL | PASS |

Each row is asserted on the distinguishing evidence, not only on the verdict
(review W4): a one-token change that turned the incomplete claim unsupported
would fail the `unsupported = []` / `omissions = ["Name"]` assertions.

The `structural-only` column is the counterfactual with an always-PASS
evaluator: it shows that structure alone accepts the incomplete answer, and the
grounded judge is what distinguishes it. The pilot judge is a scripted stand-in
(`pilot-scripted-v1`, rule: asserted identifiers must occur in the resolved,
hash-matching cited text; required elements come from the question's backticked
names). It is **not** a production evaluator and its verdicts are not answer
quality.

Receipts: `pilot/receipts/pilot-summary.json`; the synthetic world is kept under
`pilot/world/` (no private source).

## 5. Regression / end-to-end totals

| Suite | Result |
|---|---|
| `r4_grade_selftest.py` | 89/89 |
| `r4_semantic_evaluator_selftest.py` | 57/57 |
| `r4_bench_driver_selftest.py` | 79/79 |
| `r4_semantic_e2e.py` | 36 arm runs, 0 violations, 12/12 plan chains, 36/36 evaluations accepted (omissions recorded per run), no formal PASS without evaluator; 7/7 fixtures; 6 contrived flips |
| `r4_semantic_pilot.py` | 5/5 scenarios as expected |
| `audit_c3_anchoring.py` | 29/49 measured, 20 unmeasured reported |

## 6. Evaluator proposal (owner decision D1/D2)

`cases/evaluator-candidates.json` is unchanged in shape; the instructions digest
must be the current one:

- `instructions_sha256`:
  `4e9e20413c8ae2172844298ed743ab24c5fb79c0a36defeb957bee0c4f28cd55`
  (2,974 bytes, includes the SE-01 omission rules and `completeness = 2` for PASS)
- the policy text it applies is `cases/evaluator-candidates.json`
  `packet_policy` (no separate digest is claimed; the instructions digest is
  the value every packet records and checks)

Candidates: local open-weights (no egress), remote frontier (requires D2), named
human. Cost measured over 36 frozen answers: ~$0.87 for the full three-arm set
at the illustrative 2/8 USD per 1M in/out rate card ;
the remote candidate is the only one that sends private cited source off-machine.
The packet never contains gold, the checklist, the answerer session or corpus
outside delivered items/citations.

## 7. Residual risks

1. The pilot judge is scripted; it proves the workflow distinguishes the five
   situations, not that a real model would.
2. Twenty of 49 required checklist steps are not addressable by the frozen
   token locator; a structurally perfect score can still omit them. The
   evaluator's completeness judgement is the only control, and it must infer
   requirements the checklist states without anchors. Some of those rows could
   be checked by other deterministic rules, but that would be a rubric/tooling
   change, not a correction.
6. An omission is evaluator prose, not tied to a claim index or the question
   text; a fabricated omission can fail a complete answer (review W5). This is
   the same trust boundary as the evaluator identity: owner review of the
   evaluation document is the control. Pinning omissions to question elements
   would be a rubric change.
3. No production evaluator is registered; no real answer quality exists.
4. The packet digest is an integrity checksum, not authentication; owner receipt
   review remains the root of trust.
5. The stale-source policy depends on re-reading the frozen corpus; a deployment
   without the frozen corpus cannot verify contentless pack items and `accept`
   refuses those spans (fail-closed). A pack item with no frozen hash cannot be
   verified at all: its text is withheld and marked `unverifiable_source`.

## 8. Owner decisions

1. **D1** — approve and register the production evaluator identity (candidate,
   prompt digest above, artifacts it may read).
2. **D2** — authorize private-source inference for a remote evaluator, naming
   provider/model/served identity, endpoint and data classes.
3. **D3** — authorize the real three-arm benchmark run (12 cases × 3 arms), now
   that incomplete answers and stale evidence have valid, distinguishable
   verdicts.

## 9. Reproduce

```bash
cd /home/masis/src/zvec-grep-r4-evidence-20261119
export LD_LIBRARY_PATH=/home/masis/.cache/zvec-grep/rust-target-recovered-20261105/debug/build/zvec-rust-sys-9d9cb66e2b7fe09f/out/zvec-prebuilt
export ZG_ORT_CACHE_DIR=/home/masis/.cache/zvec-grep/rust-target-recovered-20261105/ort-prebuilt
C=/home/masis/.cache/zvec-grep/r4-semantic-eval-20261121

python3 benchmarks/rag-impact/tools/r4_grade_selftest.py \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus /tmp/g
python3 benchmarks/rag-impact/tools/r4_semantic_evaluator_selftest.py /tmp/se
python3 benchmarks/rag-impact/tools/r4_semantic_pilot.py --work $C/pilot
python3 benchmarks/rag-impact/tools/audit_c3_anchoring.py benchmarks/rag-impact \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus --out $C/c3-audit.json
python3 benchmarks/rag-impact/tools/r4_semantic_e2e.py \
  --corpus /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus --work $C/e2e
```

Expected: 89/89, 57/57, pilot 5/5, audit 29/49 with 20 reported, E2E exit 0.


## 10. Independent review (DeepSeek Flash 4.1 High, thinking=high)

One pass over the sanitized delta bundle and the report. **No P0.** Four P1 and
six P2 findings; all material findings fixed and pinned, the rest stated.

| Finding | Severity | Resolution |
|---|---|---|
| W1 an incomplete answer could still PASS via `completeness = 1`, since PASS only required ≥ 1 | P1 | PASS now requires `completeness = 2` (accept **and** grader), and the instructions say so; new fixtures for `completeness = 1` PASS |
| W2 an omission-only FAIL lowered C2 to 1, contradicting its own rationale | P1 | C2 now depends on unsupported claims and structurally invalid citations, not the verdict word; regression asserts C2 = 2 and total 12 for an omission-only FAIL |
| W3 SE-02 was open for a pack item with neither content nor frozen hash | P1 | text withheld and marked `unverifiable_source`; `accept` refuses a packet carrying text for such an item; two regressions |
| W4 the pilot asserted verdict equality only; a one-token mutation kept 5/5 | P1 | every scenario now asserts distinguishing evidence (`unsupported`, `omissions`, structural total, contrived verdict, stale flags, spans with content); mutation now fails |
| W5 omissions are unbound and unfalsifiable | P2 | stated in §7; pinning them to the question is a rubric change for the owner |
| W6 the audit's "outside deterministic measurement" overstated the bucket | P2 | reworded to "not addressable by the frozen token locator"; corpus hashes verified against the manifest; negative-claim caveat recorded |
| W7 `unmeasured_required_steps` embedded checklist text into results | P2 | result carries step id and reason only |
| W8 omission-FAILs were indistinguishable in arm receipts | P2 | `omissions` recorded in every E2E row |
| W9 the registration procedure in `evaluators.json` was stale | P2 | procedure now documents `omissions` and the five dimensions |
| W10 `packet_policy_sha256` was not reproducible | P2 | removed; the instructions digest is the recorded/checked value |

Counts after the fixes: grade 89/89, evaluator 57/57, driver 79/79, E2E 36/36
accepted with 7/7 fixtures, pilot 5/5 with per-field assertions, audit 29/49.
