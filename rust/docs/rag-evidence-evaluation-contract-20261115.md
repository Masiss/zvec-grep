# RAG evidence relevance — evaluation and annotation contract

Status: **experimental, small source-grounded set.** Not a production
generalization benchmark. Frozen before the policy was run (held-out), but the
labels and questions were authored by the same implementer who built the
prototype; the independent verifier checks source/range/provenance, not the
semantic correctness of every manual label.

## 1. Dataset

| Split | Cases | Required groups | Purpose |
|---|---:|---:|---|
| Dev | 6 | 9 | policy development |
| Held-out | 6 | 9 | post-freeze evaluation |
| Adversarial | 3 | 0 | unknown near-name abstention, run after freeze |

Files and hashes:

| File | SHA-256 |
|---|---|
| `benchmarks/rag-impact/cases/evidence-20261115/dev-evidence.json` | `f1b6815e…54073` |
| `benchmarks/rag-impact/cases/evidence-20261115/heldout-evidence.json` | `bc8adafd…e866f` |
| `benchmarks/rag-impact/cases/evidence-20261115/adversarial-evidence.json` | `c31237e0…ac025` |
| Corpus manifest | `d7052d3f…fc4a` |

The set deliberately covers:

- C# cross-file method → wrapper → SQL;
- direct and indirect procedure use;
- SQL view versus procedure;
- both Vietnamese and English wording;
- sibling/helper negatives and a direct-invocation negative;
- unknown method/procedure near-names;
- no-answer/abstention.

It does **not** cover non-C#, conflicting live/deployed SQL, dynamic SQL, or a
representative sample of unexpected query styles. Those remain gaps.

## 2. Roles

Each labeled source range has exactly one role:

| Role | Meaning |
|---|---|
| `REQUIRED` | Necessary to answer the question or to establish the negative claim. |
| `SUPPORTING` | Useful context; not sufficient alone. |
| `OPTIONAL` | Relevant but not needed. |
| `IRRELEVANT` | No task relevance. |
| `MISLEADING` | Plausible but would support an incorrect answer. |
| `UNKNOWN` | Not adjudicated; excluded from required-group denominators. |

No non-gold candidate is automatically called irrelevant. A candidate is
`MISLEADING` only when its source content supports a forbidden/incorrect claim.

## 3. Required groups

A required group is a set of alternative source ranges that can satisfy one
necessary evidence slot. Group hit requires **≥50% coverage** of at least one
alternative range, not merely any overlap. This avoids counting a tiny block as
complete evidence.

All required groups must be hit for task-level evidence completeness. A missing
group is a required-evidence failure; tokens/precision cannot compensate for it.

## 4. Answerability and abstention

- `expected_abstain=true`: no required groups; a non-empty answer is a false
  answer.
- `expected_abstain=false`: required groups exist; empty output is false
  abstention.
- `RE-H06`/`RE-D06` test a **negative answer** (no procedure invocation). A
  method-only evidence answer is correct; adding a SQL candidate would be
  misleading admission.
- Unknown method/procedure near-names are adversarial no-answer probes.

## 5. Evaluation metrics

- required-group recall (primary completeness);
- required-unit recall at ≥50% coverage;
- selected role precision;
- misleading admissions (selected units labeled `MISLEADING`);
- false answer / false abstention;
- token cost, latency, RSS;
- SQL-binding resolution counts;
- per-case trace.

No claim-generation or claim-to-evidence support metric is reported: this
prototype emits evidence and an abstention decision, not generated claims.

## 6. Adjudication and independence

- Labels were reviewed against the frozen source; ranges and source existence
  are checked by `verify_evidence_20261115.py`.
- The verifier also checks that the runtime receipt is unchanged when labels
  and required groups are removed (sanitized input).
- It does not independently re-adjudicate semantic labels. Treat the quality
  numbers as **fixture-level evidence**, not human-annotated generalization.
- One implementer performed source review; there is no independent human
  annotator. This is a limitation of the qualification.

## 7. Measurement correctness note

The SQL store file uses `CR CR LF` line endings. Python `read_text()` universal
newline handling made the procedure appear to have 205 lines instead of 102.
The experimental source reader now decodes bytes and collapses `\r+\n` before
line splitting. This preserves editor/gold line numbers and was verified against
the fixture ranges. It is an experimental harness fix, not an engine change.
