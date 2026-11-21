# R4 semantic evaluation & real-benchmark preparation (2026-11-21)

Base `cb8dfc3` (branch `work/r4-evidence-representation-20261119`, already
pushed before this round). This round closes the two validation defects the
independent review left open, adds the smallest independent-evaluator workflow
that can produce a gradeable semantic evaluation, and proves the whole path
end-to-end with synthetic fixtures and a loopback transport.

Status: **READY_FOR_OWNER_AUTHORIZATION — SEMANTIC_EVALUATION_PENDING.**
Answer quality is still **NOT_MEASURED**. No `RAG_MVP_GO`, no architecture
winner, no formal benchmark curriculum allocated.

## 1. The two open validation defects are closed

Both were reproduced first, then fixed at the cause, then pinned by fixtures.

| Defect | Reproduction before the fix | Fix |
|---|---|---|
| `rationale` of the wrong type crashed the grader | `rationale: 123` → `AttributeError: 'int' object has no attribute 'strip'` | types are checked before use; every malformed evaluation becomes a structured `INVALID` (→ `UNGRADABLE`) |
| `trusted` was tested for truthiness | `"trusted": "false"` (or `1`, `[]`) granted authority → formal `PASS` | authority requires the literal JSON `true` (`registered.get("trusted") is not True` → INVALID) |

Directly adjacent type-confusion defects found while fixing them, all now
`UNGRADABLE` instead of a crash or a silent pass:

- `evaluator_id` that is not a string (`["x"]`, `7`) — an unhashable id used to
  raise `TypeError` in the registry lookup;
- a registry whose top level is not an object (`[]`), or whose `evaluators`
  member is not an object, or whose registration entry is not an object;
- `verdict` / `inputs` of the wrong type;
- `rationale` that is a list, dict or bool.

Evidence: `r4_grade_selftest.py` grew from 56 to **72 checks**; 16 of them are
the new regression fixtures (8 malformed-evaluation shapes, 7 registry shapes
that must not grant authority, plus the literal-`true` control that still
grades). The `invalid()` path no longer calls any method on an unvalidated
value, so a malformed evaluation cannot raise inside the grader.

## 2. Independent evaluator workflow (packet → response → accept)

Design constraint: the grader cannot grade meaning, and an evaluator must not
be able to write its own identity or binding digests. The workflow therefore
splits into two files, in two processes, with a strictly one-way authority
flow.

```
frozen answer + pack + frozen question + cited-span text
        │  r4_semantic_evaluator.py packet     (evaluator-side, no answerer session)
        ▼
   packet.json  ──► transport ──► response.json        (model or human)
        │                              │
        │        r4_semantic_evaluator.py accept  (strict schema + consistency)
        ▼                              ▼
   packet_sha256                 evaluation.json  ──► r4_grade.py --semantic
```

**What the packet contains** (built from frozen artefacts, hash-checked, pack
binding re-verified): question text and digest, answer text/claims/citations/
declared gaps, the pack's items (paths, ranges, hashes, verification state and
content when the pack carries it), the exact source text of the spans the
answer itself cited, the public C1–C6 rubric and `REPORT-CONTRACT.md`.

**What it never contains**: gold spans, the scope manifest, the per-case C3
checklist (only the materials file's SHA-256 is used as a binding digest), the
answerer's session/transcript/ledger, any grader verdict or previous
evaluation, or corpus files outside the cited spans and pack items. Cited-span
paths are resolved inside the frozen corpus with traversal refusal; a citation
outside it fails the packet build.

**Authority**: `accept` takes `evaluator_id`, `inputs{case, answer_sha256,
pack_sha256, question_sha256, rubric_sha256}` and `packet_sha256` from the
packet, never from the response. A response carrying an extra key (including
`evaluator_id`) is rejected; an `accept --identity` that disagrees with the
packet is rejected before any response is read. The packet's own digest is
recomputed, so a packet edited after it was built is rejected.

**Response contract** (strict, `additionalProperties: false`): `verdict`
(PASS|FAIL), non-empty `rationale`, `claims[]` judging every claim index exactly
once as `supported|unsupported` with a non-empty reason, and five dimensions
(`claim_correctness`, `citation_support`, `completeness`, `uncertainty`,
`abstention`) each scored 0–2 with a rationale. `bool` is rejected as an index,
scores must be integer `0|1|2`, duplicates and gaps are rejected.

**Consistency enforced before the grader sees it**: `PASS` cannot carry an
unsupported claim, requires `claim_correctness = 2`, `citation_support = 2`, and
`completeness/uncertainty/abstention ≥ 1`; an answer with no claims may only
`PASS` with `abstention = 2`; `FAIL` must mark at least one claim unsupported.
The grader then independently re-checks the claim lists, so an inconsistent
evaluation is `UNGRADABLE` even if `accept` were bypassed.

The tool is transport-agnostic and contains **no network client**: a remote
model and the loopback judge exchange the same two JSON files. Sending private
source to a remote endpoint remains an owner decision (D2).

## 3. End-to-end proof (synthetic, loopback)

Command (see §6) produced `r4-semantic-eval-20261121/e2e/receipts/`.

### 3a. Three arms through the new pipeline (36 runs)

| Metric | Result |
|---|---|
| Runs (12 authored cases × 3 arms) | 36 |
| Protocol violations | 0 |
| Answers `OK` / packs bound | 36 / 36 |
| Read-delta reconciliation | 36 / 36 |
| Arm B plan chain closed | 12 / 12 |
| Packets built | 36 |
| Evaluations accepted → grader `EVALUATED` | 36 / 36 |
| Formal verdict without an evaluator | 36 × `NOT_MEASURED`, 0 PASS |
| Formal verdict with the loopback judge | 24 FAIL, 12 NOT_MEASURED (arm A, provenance blocked) |
| Formal PASS with a real independent evaluator | **0 — still not measurable** |

The mock transports' answers are pointer-style claims ("excerpt … lines …"),
not real answers, and the loopback judge correctly refuses them; that is a
statement about the fixtures, not about model quality. What the run proves is
mechanical: binding, accounting, plan chain, packet construction, accepted
evaluations, and the impossibility of a formal PASS without an evaluator.

### 3b. Crafted fixtures on frozen case D11 (real source, synthetic answers)

Each fixture is built from the frozen span and graded in a separate process.

| Fixture | Expected semantic | Observed semantic | Expected formal | Observed formal |
|---|---|---|---|---|
| correct (grounded claim, verified pack) | PASS | PASS | PASS | **PASS** |
| contradicted (valid hash, claim names `Email` which the span lacks) | FAIL | FAIL | FAIL | FAIL |
| runtime-assertion ("deployed in production…") | FAIL | FAIL | FAIL | FAIL (C4 = 0) |
| missing-evidence (claim with no citation) | FAIL | FAIL | FAIL | FAIL (C2 = 0) |
| unsupported-citation (valid hash, cited span says otherwise) | FAIL | FAIL | FAIL | FAIL |

The first row is the only way to reach a formal `PASS`; every negative row
stays non-PASS even though the citation hash and line range can be valid. That
is the property the semantic layer exists for.

## 4. Proposed evaluator configuration (owner decision D1)

`cases/evaluator-candidates.json` lists three candidates with the exact data
flow and privacy class; **nothing is registered**. `cases/evaluators.json`
remains empty, so every real case is `NOT_MEASURED` by construction.

| Candidate | Endpoint / identity | Data flow | Cost for 36 evaluations |
|---|---|---|---|
| local open-weights | local Responses-compatible server; record server name, weights digest, quantization | none off-machine; D2 not required | electricity/time only |
| remote frontier | `https://api.openai.com/v1/responses`, model pinned and served identity recorded per evaluation | question + answer + pack metadata + cited span text leave the machine | **$0.87** at the illustrative 2.00/8.00 USD per 1M input/output tokens |
| named human | owner-named reviewer, no session/gold access | none | reviewer time per evaluation |

Cost basis: measured over the 36 frozen answers in this round — 334,375
estimated prompt tokens (UTF-8 bytes / 4) + 25,200 assumed output tokens
(700/evaluation) = 359,575 tokens ≈ **$0.024 per evaluation**, **$0.87 for the
full three-arm set**. The rate card is a labelled assumption, not a quote; token
counts are estimates. A repeat costs the same; four arm-profile repeats would
be ≈ $3.5.

Privacy: the remote candidate is exactly the egress the frozen benchmark was
designed to keep local. It needs D2 (provider, endpoint, data classes) *and*
D1 (identity + prompt). Gold spans, the C3 checklist, the answerer session and
the corpus outside cited spans never enter a packet, so even an authorized
remote evaluator sees only what the answerer already received.

## 5. What is still not measured / not claimed

1. **No real answer-quality measurement exists.** Both decisions (D1, D2) are
   owner-level; until then the harness reports `NOT_MEASURED`.
2. The loopback judge is a deterministic stand-in. Its verdicts in §3a are
   synthetic and must never be cited as benchmark results.
3. Arm A cannot produce a formal verdict at all (`provenance_blocked`), because
   its pack has no verified evidence. That is a capability statement.
4. The frozen-question identity is a self-declaration inside the artifact; a
   local attacker owning the artifact and its sidecar can still rewrite both
   consistently (the known W2 gap from the previous round). Owner-side receipt
   review is the control; the production registry stays empty.
5. The loopback judge's grounding rule (identifier containment in the cited
   span) is a coarse proxy: it can mark a correct paraphrase unsupported. That
   is why it is a pipeline proof, not a measurement.

## 6. Reproduce

```bash
cd /home/masis/src/zvec-grep-r4-evidence-20261119
export LD_LIBRARY_PATH=/home/masis/.cache/zvec-grep/rust-target-recovered-20261105/debug/build/zvec-rust-sys-9d9cb66e2b7fe09f/out/zvec-prebuilt
export ZG_ORT_CACHE_DIR=/home/masis/.cache/zvec-grep/rust-target-recovered-20261105/ort-prebuilt
export CARGO_TARGET_DIR=/home/masis/.cache/zvec-grep/r4-evidence-20261119/target

# 1. validation regressions (72 checks) + evaluator contract (31 checks)
python3 benchmarks/rag-impact/tools/r4_grade_selftest.py \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus /tmp/g
python3 benchmarks/rag-impact/tools/r4_semantic_evaluator_selftest.py /tmp/se

# 2. driver contract (79 checks)
python3 benchmarks/rag-impact/tools/r4_bench_driver_selftest.py \
  $CARGO_TARGET_DIR/debug/r4-adapter \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus /tmp/h

# 3. end-to-end: 5 crafted fixtures + 36 synthetic arm runs
python3 benchmarks/rag-impact/tools/r4_semantic_e2e.py \
  --corpus /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus \
  --work /home/masis/.cache/zvec-grep/r4-semantic-eval-20261121/e2e

# 4. cost estimate over the frozen answers
python3 benchmarks/rag-impact/tools/r4_semantic_evaluator.py estimate \
  --answers /home/masis/.cache/zvec-grep/r4-semantic-eval-20261121/e2e/answers \
  --labels benchmarks/rag-impact \
  --corpus /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus \
  --candidates benchmarks/rag-impact/cases/evaluator-candidates.json \
  --out /home/masis/.cache/zvec-grep/r4-semantic-eval-20261121/cost-estimate.json
```

Expected: 72/72, 31/31, 79/79, E2E exit 0 with `all_as_expected: true`,
36/36 accepted evaluations and no formal PASS without an evaluator.

## 7. Owner decisions still required

1. **D1 — register one semantic evaluator**: choose a candidate, approve the
   packet instructions (their SHA-256 is recorded in every packet), and add
   `{trusted: true, fingerprint, kind, model/served identity or human name}` to
   `cases/evaluators.json`. The tool never does this.
2. **D2 — authorize private-source inference** if the chosen evaluator is
   remote: name provider, exact model and served identity, endpoint, and the
   data classes in §4.
3. **D3 (optional) — run the real benchmark**: driver with
   `--backend openai-responses --authorized-inference`, 12 cases × 3 arms,
   then one grader process per frozen pair with `--semantic` and the registry.
   Structural and semantic layers are reported separately; a formal PASS needs
   both.
