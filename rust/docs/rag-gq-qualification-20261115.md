# GQ qualification report — 2026-11-15

**Verdict: `NO_GO`.** The frozen binding-aware candidate has a concrete
correctness/safety failure on independent GQ data: it treats commented-out
declarations/calls as live SQL wrappers and selects them as evidence. It also
fails dev role precision, misleading-admission, abstention and negative-claim
safety gates. The evaluation is machine-assisted and small; even setting that
limitation aside, the false binding is a source-level failure.

## 1. Freeze and isolation

| Item | Value |
|---|---|
| Candidate | `382116824c62d56d9b1908d79d8e0d9d1c2703bd` |
| Candidate grounding hash | `6aa30d7b…ec3a60` |
| Frozen binding artifact | `4862a694…89643` |
| Gates | `133163b0…a20c` |
| Dataset | `821efc10…0a1bc` / `9807749e…5a07bc` |
| Candidate modified after freeze | **No** |

Sanitized inputs (roles, groups, claims removed) produced identical selected
units on dev and held-out; no label leakage was observed.

## 2. Three arms

Same source snapshot, token budget and query set. The candidate has an
additional frozen SQL-binding artifact; its pod is otherwise the same source
tree.

| Arm | Dev groups | Dev role precision | Dev misleading | Dev neg. safety | Held-out groups | Held-out role precision |
|---|---:|---:|---:|---:|---:|---:|
| Exact-symbol baseline | 4/10 (0.40) | 0.800 | 1 | 1/1 | 3/3 | 0.600 |
| Reference-aware, no grounding | 4/10 (0.40) | 0.286 | 2 | 0/1 | 3/3 | 0.375 |
| **Frozen binding-aware candidate** | **8/10 (0.80)** | **0.533** | **6** | **0/1** | **3/3** | **1.000** |

Candidate dev claim evidence: **3/6 correct**, **2 unsafe**, **1 insufficient**.
Held-out: **4/4 correct**.

The candidate has a higher dev required-group recall than both arms, but it also
admits six misleading units and makes a negative claim unsafe. The held-out set
is only four cases and cannot offset the dev safety failure.

## 3. Gate result (dev)

| Gate | Target | Candidate | Result |
|---|---:|---:|---|
| Required-group recall | ≥0.80 | 0.80 | PASS |
| Role precision | ≥0.85 | 0.533 | **FAIL** |
| Misleading admissions | ≤1 | 6 | **FAIL** |
| Correct abstention | ≥0.80 | 4/6 (0.667) | **FAIL** |
| Negative-claim safety | 1.00 | 0.00 | **FAIL** |
| False verified SQL bindings | 0 | **2 chains** | **FAIL** |
| Wall / RSS | ≤5 s / ≤200 MiB | 0.73 s / 50.6 MiB | PASS |
| Tokens/case | ≤3000 | mean 1539, max 2971 | PASS |

Held-out gates: group recall 1.0, role precision 1.0, misleading 0, negative
safety 1.0 — but the same false binding is present and only four cases were
evaluated.

## 4. Concrete failures

### GQ-03 — false verified binding through commented code

Candidate selected:

- SQL `sp_UpdateOrgTotalEmployee` 1–103;
- wrapper `OrganizationCommand` 1057–1073;
- `WorkProcessCommand` 164–274;
- `EmployeeCommand.EditEmployeeJobAndGroup` 461–520.

The source shows:

- `WorkProcessCommand.ChangeWorkProcess` declaration at line 164 is entirely
  commented out;
- the `ChangeWorkProcess(...)` call inside `EditEmployeeJobAndGroup` at line 472
  is commented out;
- the live method body at 461–520 has only `SaveChanges` and cache deletes.

The binding artifact nevertheless records `VERIFIED` chains:

```
EditEmployeeJobAndGroup → ChangeWorkProcess → UpdateOrgTotalEmployee
ChangeWorkProcess → UpdateOrgTotalEmployee
```

This is a **false SQL binding**, causing an unsafe `REFUTED` negative claim and
two misleading admissions. The verifier flags two commented-chain bindings.

### GQ-04 — commented method admitted as control evidence

Candidate selected four blocks from the commented `ChangeWorkProcess` source
range 164–274 and did **not** abstain. The claim is `INSUFFICIENT`; expected
behavior is abstention, not executable evidence.

### GQ-01 — property/type query missed

The query asks for the `DbSet<EmployeeChangeRequestDetail>` property and entity
declaration. Candidate returned no units and abstained; required groups
`property` and `type` were missed. This is a discovery/query-intent gap, not a
binding failure.

### Role confusion

GQ-02/GQ-05 selected method ranges as `WRAPPER` rather than `CONTROL`. Required
coverage still succeeds, but the provenance role is imprecise.

## 5. Claim-level assessment

| Claim status | Candidate dev |
|---|---:|
| SUPPORTED claims with sufficient evidence | 3/6 |
| REFUTED claim handled safely | 0/1 |
| Insufficient claim correctly abstained | 0/1 (GQ-04 emitted misleading evidence) |
| Unsupported acceptance | 0 — no claim generator exists |

No claim generation or answer acceptance was performed; the assessment is
evidence adequacy. Negative-claim safety is nevertheless failed by GQ-03.

## 6. Independent verification

`verify_gq_20261115.py` independently checked:

- candidate freeze and binding hashes;
- dataset source/range/claim provenance;
- selected evidence ranges;
- binding states and commented-chain provenance;
- metrics/denominators and abstention;
- sanitized runtime identity.

Results:

| Split | Checks | Failures |
|---|---:|---|
| Dev | 174 | false commented bindings; abstention; role/misleading/negative gates |
| Held-out | 114 | false commented bindings |

The verifier is **not** a label oracle. Its finding is source-level: the
candidate's binding artifact is inconsistent with the live source. It also
confirms no fabricated selected paths and no label leakage.

## 7. Failure attribution

| Failure | Class |
|---|---|
| GQ-01 missing property/type evidence | Discovery / query intent |
| GQ-03 false wrapper chain | Reference/SQL binding miss + incorrect resolution (comment blindness) |
| GQ-03 unsafe negative claim | Negative-claim failure |
| GQ-04 commented method admitted; no abstention | Query intent + incorrect abstention |
| GQ-02/05 role naming | Evidence-role confusion (not required-group failure) |
| Dev role precision 0.533 | Misleading admission / role confusion |
| Held-out passes | small-sample signal, not generalization |

## 8. Candidate erratum

Frozen candidate `3821168` is **not** eligible for integration. The candidate
extractor does not distinguish comments from executable code. This is a
correctness defect, not a tuning issue. Do not patch it and rerun the same GQ
data as if it were independent; a fix requires a new candidate identity and a
fresh qualification dataset.

## 9. Bounded remediation plan

1. Strip `//` and `/* ... */` comments before declaration/call extraction;
   exclude commented method declarations/calls.
2. Require a live method body for `CONTROL` evidence; if a method is
   comment-only, abstain or return `UNKNOWN`.
3. Add property/member (`DbSet<T>`) queries to exact-symbol grounding.
4. Preserve role distinction `CONTROL` versus `WRAPPER`.
5. Re-run the independent verifier on a **new** frozen evaluation set; do not
   reuse GQ as final evidence for the fixed candidate.

## 10. Decision

- **Candidate verdict: `NO_GO`.**
- **Evaluation quality: limited, machine-assisted** (`PROVISIONAL`-grade at
  best); this alone would prevent `QUALIFIED_CANDIDATE`, but the concrete false
  binding independently justifies `NO_GO`.
- No production integration, no sidecar activation, no merge.
