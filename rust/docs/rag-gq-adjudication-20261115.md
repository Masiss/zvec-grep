# GQ independent adjudication report — 2026-11-15

Status: **machine-assisted source-first adjudication, not human-independent
gold.** The candidate and gates were frozen before execution.

## 1. Frozen candidate

| Item | Value |
|---|---|
| Commit | `382116824c62d56d9b1908d79d8e0d9d1c2703bd` |
| Grounding policy hash | `6aa30d7b…ec3a60` |
| Binding policy hash | `e1ccc705…4f9c4` |
| Frozen SQL bindings | `4862a694…89643` |
| Corpus manifest | `d7052d3f…fc4a` |
| Candidate freeze manifest | `c4ecc8e8…fa322` |

The candidate was not modified after the GQ dataset was authored.

## 2. Dataset

| Split | Cases | Purpose |
|---|---:|---|
| Dev | 6 | candidate development/qualification visibility |
| Held-out | 4 | post-freeze evaluation |

Files:

| File | SHA-256 |
|---|---|
| `benchmarks/rag-impact/cases/gq-20261115/dev-gq.json` | `821efc10…0a1bc` |
| `benchmarks/rag-impact/cases/gq-20261115/heldout-gq.json` | `9807749e…5a07bc` |
| Gates | `133163b0…a20c` |
| Qualification manifest | `67edc4fd…f31395` |

Coverage:

- C# cross-file method/wrapper/procedure/view;
- property/type (`DbSet` mapping);
- inline SQL and negative invocation;
- ambiguous same-name method;
- commented-out code (hard negative);
- unknown procedure / near-name;
- Vietnamese and English;
- one **UNKNOWN** no-answer case for a Rust query. The frozen corpus has no
  Rust source; GQ-09 only tests abstention at a corpus boundary, **not**
  non-C# retrieval capability.

## 3. Adjudication contract

Roles: `REQUIRED`, `SUPPORTING`, `OPTIONAL`, `IRRELEVANT`, `MISLEADING`,
`UNKNOWN`.

- A required-group hit requires ≥50% coverage of one declared alternative.
- A claim is `SUPPORTED`, `REFUTED`, `INSUFFICIENT`, `CONFLICTING` or `UNKNOWN`.
- Negative claims must be backed by the live method body; selecting a procedure
  that is not invoked makes the negative claim unsafe.
- Commented-out declarations and calls are **not executable evidence**. The
  adjudicator checked the source text, not candidate output.
- Label status is `VERIFIED` only where source inspection is unambiguous.
  GQ-09 is `UNKNOWN`; no item is marked `DISPUTED` because the source cases were
  unambiguous, but this is a limitation of a small set, not proof of label
  infallibility.

## 4. Adjudication limitations

- No independent human annotator.
- Ten cases total; held-out four.
- All positive source cases are C#/SQL; no representative non-C# source.
- No claim generator, so claim-to-evidence support is evaluated as evidence
  adequacy, not generated answer acceptance.
- No conflicting live/deployed SQL authority case.
- The evaluator and verifier are authored in the same branch; the verifier
  independently re-reads source/ranges, but semantic adjudication remains
  machine-assisted.

These limits prevent a `QUALIFIED_CANDIDATE` claim regardless of the
candidate's fixture performance.
