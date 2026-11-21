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

Evidence: `r4_grade_selftest.py` grew from 56 to **81 checks**; the new ones
cover 8 malformed-evaluation shapes, 7 registry shapes that must not grant
authority, the literal-`true` control that still grades, a PASS that omits or
weakens the scored dimensions, a citation wider than its pack item, the last
delivered line vs one line past it, and three malformed answer-artifact shapes
(review W9). The
`invalid()` path no longer calls any method on an unvalidated value, so a
malformed evaluation cannot raise inside the grader.

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
declared gaps, the pack's items (paths, inclusive ranges, hashes, verification
state, and the full item content when the pack stores it), the exact text of
the parts of those items the answer cited, the public C1–C6 rubric and
`REPORT-CONTRACT.md`. Cited text is capped at 64 KiB per span and 512 KiB in
total; oversized evidence is refused, not truncated.

**Delivered-evidence boundary (review W1).** A citation may be narrower than a
pack item but never wider: the region shown to the evaluator is the
intersection, and a citation that is not fully inside one delivered pack item
is reported as `in_pack: false` with no content and is **never read from the
corpus**. The grader enforces the same rule structurally (`contains`, not
`overlaps`), and an invalid citation scores C2 = 0 without needing an evaluator.
Pack ranges are inclusive at both ends — the convention the adapter actually
writes; the previous exclusive-end normalisation dropped the delivered last line
and is removed (second-pass P1-1). Overlap is not delivery.

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
recomputed, the instructions digest is checked, and the response must echo
`packet_sha256`, so a response carrying a different packet digest is rejected
(review W5/W7). `accept` additionally rebuilds every cited span from the
delivered pack items and refuses any content mismatch, so rewriting
`cited_spans[].content` and recomputing the digest does not change what was
judged (second-pass P1-2). The packet digest is an integrity checksum, **not**
authentication: the owner-side receipt review remains the root of trust, and a
hand-written evaluation that bypasses `accept` is only as trustworthy as the
registry decision.

**Response contract** (strict, `additionalProperties: false`): the packet digest,
`verdict` (PASS|FAIL), non-empty `rationale`, `claims[]` judging every claim
index exactly once as `supported|unsupported` with a non-empty reason, and five
dimensions (`claim_correctness`, `citation_support`, `completeness`,
`uncertainty`, `abstention`) each scored 0–2 with a rationale. `bool` is
rejected as an index, scores must be integer `0|1|2`, duplicates and gaps are
rejected. An answer with **no claims** is refused: it cannot be semantically
evaluated and the structural layer governs (review W3).

**Consistency enforced in both places (review W2)**: `PASS` cannot carry an
unsupported claim, requires `claim_correctness = 2`, `citation_support = 2`, and
`completeness/uncertainty/abstention ≥ 1`; `FAIL` must mark at least one claim
unsupported. The grader independently re-checks the claim lists **and the five
dimension scores** (mandatory on PASS), so bypassing `accept` cannot produce a
PASS that `accept` would have refused. A PASS without dimensions, or with a
score below a floor, is `UNGRADABLE`. A malformed `FAIL` is likewise
`UNGRADABLE`: an evaluation whose own fields do not satisfy the contract cannot
be trusted to say the answer failed.

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
| Runs a contrived (always-PASS) evaluator would have lifted | 6 of 24 grounded FAILs (5 → PASS, 1 → PARTIAL) |
| Formal PASS with a real independent evaluator | **0 — still not measurable** |

The mock transports' answers are pointer-style claims ("excerpt … lines …"),
not real answers, and the loopback judge correctly refuses them; that is a
statement about the fixtures, not about model quality. What the run proves is
mechanical: binding, accounting, plan chain, packet construction, accepted
evaluations, and the impossibility of a formal PASS without an evaluator.

### 3b. Crafted fixtures on frozen case D11 (real source, synthetic answers)

Each fixture is built from the frozen span and graded in a separate process.

| Fixture | Grounded semantic | Grounded formal | Contrived-PASS formal |
|---|---|---|---|
| correct (grounded claim, verified pack) | PASS | **PASS** | PASS |
| contradicted (valid hash, claim names a field the span lacks) | FAIL | FAIL | **PASS** |
| runtime-assertion ("deployed in production…") | FAIL | FAIL | FAIL (C4 = 0) |
| missing-evidence (claim with no citation) | FAIL | FAIL | FAIL (C2 = 0) |
| unsupported-citation (valid hash, cited span says otherwise) | FAIL | FAIL | FAIL (C3/C6) |
| citation-wider-than-pack (starts inside, ends outside) | FAIL | FAIL | FAIL (C2 = 0) |
| claim-free answer with a declared gap (pack is partial) | REFUSED | FAIL | n/a (no claims) |

Within this fixture set only the grounded row reaches a formal `PASS`. The
contrived column is the reviewer's W12 counterfactual: grade the same frozen
artifacts with an evaluator that PASSes every claim. `contradicted` then becomes
`PASS` while the grounded evaluation says `FAIL`, which is the only
demonstration that the semantic layer is *decisive* rather than redundant — the
structural layer and a permissive evaluator would have accepted it.

The "wider than pack" row is the review's W1 attack: overlap used to be enough
for the structural layer, so widening the end line could hide an unsupported
claim behind bytes that were never delivered. It is now a measured C2 failure
and the evaluator never sees the undelivered part. The last row records the
abstention policy: an honest abstention with a declared gap scores full C5 but a
claim-free answer never receives a formal PASS (W3).

The positive row is a property of this fixture set, not of the semantic layer:
the loopback's own rule (identifier containment) is what calls it supported, and
a real evaluator may disagree. That is why §5 keeps answer quality
`NOT_MEASURED`.

## 4. Proposed evaluator configuration (owner decision D1)

`cases/evaluator-candidates.json` lists three candidates with the exact data
flow and privacy class; **nothing is registered**. `cases/evaluators.json`
remains empty, so every real case is `NOT_MEASURED` by construction.

| Candidate | Endpoint / identity | Data flow | Cost for 36 evaluations |
|---|---|---|---|
| local open-weights | local Responses-compatible server; record server name, weights digest, quantization | none off-machine; D2 not required | electricity/time only |
| remote frontier | `https://api.openai.com/v1/responses`, model pinned and served identity recorded per evaluation | question + answer + pack metadata + cited span text leave the machine | **$0.85** at the illustrative 2.00/8.00 USD per 1M input/output tokens |
| named human | owner-named reviewer, no session/gold access | none | reviewer time per evaluation |

Cost basis: measured over the 36 frozen answers in this round — 323,895
estimated prompt tokens (UTF-8 bytes / 4) + 25,200 assumed output tokens
(700/evaluation) = 349,095 tokens ≈ **$0.0236 per evaluation**, **$0.85 for the
full three-arm set**. The rate card is a labelled assumption, not a quote; token
counts are estimates. A repeat costs the same; four arm-profile repeats would
be ≈ $3.4.

Exact registration shape (the tool never writes this file; the owner does):

```json
{"evaluators": {
  "<approved-id>": {
    "trusted": true,
    "kind": "local-model" | "remote-model" | "human",
    "model": "<exact model/version>",
    "served_identity": "<provider-returned snapshot or human name>",
    "endpoint": "<or null for a human>",
    "instructions_sha256": "4ce060dc2bc189051e2d03489cbee92d6c93a7a841b2c5ee952a23ed7ce8958c",
    "packet_policy_sha256": "573c68907360d1a48684bd3bd996b578f96d0122d0e97c4b7a552751b06ab585",
    "fingerprint": "<owner-recorded digest of the approval note>"
  }
}}
```

`instructions_sha256` is the digest of the packet instructions
(`r4_semantic_evaluator.INSTRUCTIONS`, 2,969 bytes), and every packet records
it; an evaluator that changes its prompt must be re-approved and re-registered.

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

# 1. validation regressions (81 checks) + evaluator contract (42 checks)
python3 benchmarks/rag-impact/tools/r4_grade_selftest.py \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus /tmp/g
python3 benchmarks/rag-impact/tools/r4_semantic_evaluator_selftest.py /tmp/se

# 2. driver contract (79 checks)
python3 benchmarks/rag-impact/tools/r4_bench_driver_selftest.py \
  $CARGO_TARGET_DIR/debug/r4-adapter \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus /tmp/h

# 3. end-to-end: 7 crafted fixtures + 36 synthetic arm runs
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

Expected: 81/81, 42/42, 79/79, E2E exit 0 with `all_as_expected: true`,
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

## 8. Independent review and resolution

Reviewer: DeepSeek Flash 4.1 High (`opencode-go/deepseek-v4.1-flash`,
thinking=high), sanitized bundle only — never the corpus, packs, answers, gold
spans or the answerer session. First pass: **FIX-FIRST** with one P0, two P1 and
the rest P2. Every material finding was reproduced, fixed at the cause, and
pinned by a fixture; the second pass re-checked the corrected bundle.

| Finding | Severity | Resolution |
|---|---|---|
| W1 the evaluator's evidence boundary was chosen by the answer; the structural gate accepted a citation that only *overlapped* a pack item, so widening the end line could hide an unsupported claim | P0 | the packet shows only the intersection with a delivered pack item and never reads outside it (`in_pack=false`); the grader uses `contains`, not `overlaps`, and an invalid citation scores C2 = 0 without an evaluator; two new fixtures (wider-than-pack at packet and grader level) |
| W2 dimension floors lived only in `accept`, so a dimension-less evaluation could still be a formal PASS | P1 | the grader now requires the five dimensions on PASS, validates integer 0–2 scores, and enforces `claim_correctness = 2`, `citation_support = 2`, `completeness/uncertainty/abstention ≥ 1`; two new grader fixtures |
| W3 claim-free answers had no coherent grade; the prompt asked for a verdict the grader discards | P1 | claim-free answers are refused by `accept` (structural verdict governs) and a PASS with zero claims is `INVALID` in the grader; abstention fixture added; the prompt says so |
| W4 the 36-run arm half could not be verified from the bundle | P1 | the bundle now carries `r4_bench_driver.py`, its selftest, `receipts/arms.json` (per-run digests, verdicts, plan chain, ledger reconciliation) and `e2e-summary.json`; packets/answers/session receipts stay out because they contain private source |
| W5 the packet digest was described as tamper-proofing | P2 | `verify_packet` checks `instructions_sha256`; the doc calls the digest an integrity checksum, not authentication |
| W6 the audit chain was not recorded | P2 | the grader records `semantic_sha256`, `packet_sha256`; `accept --served-identity` records the transport-reported identity, explicitly not trusted |
| W7 a response was not bound to the packet it answered | P2 | the response must echo `packet_sha256`, checked in `validate_response`; the schema requires it |
| W8 no cited-span size cap; a NUL path raised instead of refusing | P2 | per-span and total cited-byte caps; all path resolution wrapped into `EvaluatorError`; NUL-path fixture |
| W9 the same type-confusion class was open on the answer side | P2 | `load_answer` validates claim/citation shapes and raises `GradingError` → `UNGRADABLE`; three new fixtures |
| W10 leak checks were substring smoke tests; one fixture's pack range did not match its content | P2 | an uncited corpus sentinel and an item-content equality check replace them; the pack fixture uses the `end_line = end + 1` convention |
| W11 vacuous metrics and dead assertions | P2 | the dead top-level `claims` check is gone; `packets_built` requires a 64-hex digest; `estimated_per_evaluation_usd` is named for what it divides by; fixture rows no longer carry a hard-coded empty `violations` |
| W12 the suite never showed the semantic layer being decisive | P2 | every fixture (and arm run) is also graded with a contrived evaluator that PASSes all claims; `contradicted` flips `FAIL → PASS` only under that contrivance, recorded as `semantic_decisive` |
| W13 the positive fixture is circular | P2 | stated explicitly: the PASS is the loopback's rule, not an independent judgment; answer quality remains `NOT_MEASURED` |

No finding was left "acknowledged but unfixed" except the root-of-trust limits
that no local code can close: the frozen-question identity and the evaluator
identity are owner-approved declarations, checked by digests and receipt review,
not by a signature. That is recorded in §5.

## 9. Residual limitations after the corrections

1. No independent evaluator is registered; every real case is `NOT_MEASURED`.
2. The arm half of the E2E proves mechanics, not answer quality; the mock
   transports' pointer-style claims are refused by design.
3. The contract is signature-free: a local attacker owning artifacts and
   receipts can rewrite them consistently. Owner-side review is the control.
4. The loopback judge's rule is coarse; the contrived-PASS counterfactual shows
   the *layer* is decisive, not that a real evaluator would agree on any single
   answer.
5. The remote candidate's rate card is illustrative; the real budget needs the
   provider's current price sheet.

### 8b. Second review pass (bounded, corrections only)

The same reviewer re-checked only the corrections, with execution where
possible. It reproduced the 79/40/79 self-tests and the E2E summary, and
created its own probes for the dimension floors, citation containment, replay
and shape validation. **No P0.** Three P1 and seven P2 issues; all are fixed
here except the two root-of-trust limits stated in §9.

| Finding | Severity | Resolution |
|---|---|---|
| P1-1 the `end - 1` normalisation dropped the delivered last line on adapter-shaped packs, so a correct citation of that line was rejected — and the new fixture was built to the wrong convention | P1 | `pack_spans` and the packet now treat `end_line` as inclusive, matching both real pack shapes (checked against the adapter's packs: search-derived and verified are inclusive; a trailing newline explains the earlier miscount); all fixtures use the same convention; new fixtures cover "citation exactly at the delivered end" (valid) and "one line past" (C2 = 0) |
| P1-2 the evaluation did not bind the evidence text; a rewritten `cited_spans[].content` plus a recomputed packet digest survived to a formal PASS | P1 | `accept` rebuilds every cited span from the delivered pack items (or the frozen corpus when an item stores no content, via `--corpus`) and refuses any mismatch, missing span, or uncited extra span; two new regression fixtures |
| P1-3 the bundle could not run its own driver evidence (`r4_tool_schema` missing) | P1 | the tool is in the bundle and the patch; the README states the bundle is read-only and that self-test logs come from the repo at HEAD, because the bundled selftest needs the repo's labels and corpus |
| P2-1 the NUL-path fixture could not fail | P2 | the check now requires `in_pack: false` (or a structured refusal) |
| P2-2 `receipts/fixtures.json` had been post-processed by hand | P2 | the E2E now writes the sanitized receipt itself; it is exactly the shipped code's output |
| P2-3 "5 crafted fixtures" survived in the doc and tool docstring | P2 | both say 7 |
| P2-4 the abstention fixture was labelled "honest" while its artefacts said the pack was complete | P2 | the pack is now partial and the row is described as a policy fixture: declared gap, no claims, refused a formal PASS |
| P2-5 a malformed FAIL became `UNGRADABLE` instead of `FAIL` | P2 | stated in §2: an evaluation that does not satisfy its own contract is `UNGRADABLE`, whatever verdict it claims |
| P2-6 packet scope under-described item content and the caps | P2 | §2 names the full item content and the 64 KiB / 512 KiB caps |
| P2-7 `served_identity` had no provenance marker on the grading side | P2 | the grader records it only when `served_identity_source == "transport"` |

No further review round is opened: the remaining limits (signature-free
artifacts; evaluator identity is an owner decision) are structural, not code
defects, and are recorded rather than patched.
