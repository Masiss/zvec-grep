# GQ-R / GQ4 independent qualification report — 2026-11-15

**Final verdict: `GQ2_NO_GO`.**

Gate A correctness repair is PASS: comment/string/preprocessor liveness and
SQL binding no longer reproduce the GQ false-chain defect. Gate B/GQ4 still
fails the pre-registered quality gates on the new qualification set: required
group recall is 0.5 on dev, role precision 0.667, and one unsupported
answer/evidence is admitted. The held-out half is perfect but only three cases.

The candidate is **not** an integration candidate.

## 1. Candidate identity

| Item | Value |
|---|---|
| Candidate | `liveness-binding-grounding-v5` |
| Freeze | `82cdccd6…52472` |
| Grounding hash | `a30397c5…4711e3` |
| Liveness hash | `cc456eaf…0ca0c` |
| Reference index hash | `d2da4719…22aec4` |
| Binding hash | `e1ccc705…4f9c4` |
| Frozen bindings | `d8b40b0b…a8203` |
| Corpus manifest | `d7052d3f…fc4a` |

The old `3821168` candidate and old GQ dataset remain untouched.

## 2. Evaluation

| Split | Cases | Required groups |
|---|---:|---:|
| Dev | 3 | 4 |
| Held-out | 3 | 1 |

Coverage: `DbSet` property/type, live write method, wrapper SQL, direct SQL,
negative no-proc, commented declaration, unknown procedure, SQL view. SQL is the
only non-C# positive. Labels are machine-assisted source-first, not independent
human gold.

GQ2/GQ3 were invalidated as final qualification because candidate code changed
after those freezes during repair. GQ4 is the fresh set authored after the v5
freeze. Results from GQ2/GQ3 are development evidence only.

## 3. Three-arm result

| Arm | Dev groups | Dev role precision | Dev unknown | Dev unsupported | Held-out groups | Held-out role precision |
|---|---:|---:|---:|---:|---:|---:|
| Exact-symbol baseline | 2/4 (0.50) | 1.000 | 0 | 0 | 1/1 | 1.000 |
| Reference-aware, no grounding | 2/4 (0.50) | 0.333 | 4 | 0 | 1/1 | 1.000 |
| **Candidate v5** | **2/4 (0.50)** | **0.667** | **1** | **1** | **1/1** | **1.000** |

Candidate dev claims: **2/3 sufficient**, 0 unsafe, 1 unsupported. Held-out:
3/3 sufficient.

The candidate does **not** improve required coverage over the baseline on GQ4.
Gate B quality fails.

## 4. Gate result (dev)

| Gate | Target | Candidate | Result |
|---|---:|---:|---|
| False VERIFIED SQL bindings | 0 | 0 | PASS |
| Non-live evidence admitted | 0 | 0 | PASS |
| Unsafe negative claim | 0 | 0 | PASS |
| Required-group recall | ≥0.80 | 0.50 | **FAIL** |
| Role precision | ≥0.85 | 0.667 | **FAIL** |
| Misleading admissions | ≤1 | 0 (1 unknown) | PASS |
| Correct abstention | — | 3/3 | PASS |
| False abstention | ≤1 | 0 | PASS |
| Tokens/case | ≤3000 | 178 mean | PASS |
| Resources | ≤5 s / ≤200 MiB | 1.65 s / 50.1 MiB | PASS |

Held-out gates pass (1/1 group, role 1.0), but only three cases.

## 5. Concrete failure

### GQ4-01 — property query selected a view

Query: “Which DbSet property maps EmployeeBaseView, and where is
EmployeeBaseView declared?”

Candidate selected:

```
DB/Views/EmployeeBaseViews.sql 1–10   role VIEW
```

Required:

- `CoreDBContext.cs:42` DbSet property;
- `Employee.cs:64–81` entity declaration.

The candidate's `views` detection matched `EmployeeBaseView` as a substring of
the SQL view name `EmployeeBaseViews`; the property filter did not take priority
over the view branch. Required groups 0/2, selected unit unknown (the fixture did
not label the view as MISLEADING, which is why the evaluator reports unknown
rather than misleading). This is a **query-intent/property-selection failure**,
not a source/binding correctness failure.

### GQ4-02/GQ4-03

Live `UpdateEmployeeAttr` selected correctly; negative `sp_Update` claim is
refuted by the live method with no SQL. These pass.

### GQ4-04/GQ4-05

Commented `CbEmployeeMany` and unknown procedure both abstain correctly. This
confirms the liveness repair.

### GQ4-06

View query returns `EmployeeBaseViews.sql`; pass.

## 6. Independent verification

| Split | Checks | Result |
|---|---:|---|
| Dev | 103 | **FAIL** — role precision and required-group gates |
| Held-out | 85 | PASS |
| Gate A | 96 | **PASS** |

Verifier checks:

- candidate/tool/binding/dataset hashes;
- source/range/label provenance;
- no false comment-chain binding;
- selected range identity;
- metrics/denominators/claims;
- sanitized runtime identity;
- gate arithmetic.

No fabricated path, false binding, non-live control, or label leakage was found.
The GQ4 failure is a **quality/selection** failure, not a liveness safety
failure.

## 7. Limitations

- Dev three cases, held-out three cases; far too small for production
  generalization.
- Machine-assisted labels; no independent human adjudication.
- SQL is the only non-C# positive; no representative Rust/config/doc positive.
- GQ4-01 fixture omitted the view as an explicit misleading label; this is an
  evaluation annotation gap. The required-group miss still fails.
- No claim generator; claim support is evidence adequacy, not generated answer
  acceptance.
- GQ2/GQ3 are invalidated as final evidence because repair changes followed
  their freezes.

## 8. Decision / remediation

- **Gate A: PASS.**
- **Gate B/GQ4: `GQ2_NO_GO`.**
- No merge, no sidecar activation, no production change.

Targeted remediation, if another candidate is authorized:

1. Give property/DbSet intent priority over SQL/view when the query asks for a
   property mapping.
2. Match view names exactly, not by substring (`EmployeeBaseView` vs
   `EmployeeBaseViews`).
3. Re-run on a **new** frozen GQ5 set; do not reuse GQ4 as final evidence.
4. Keep the liveness contract and Gate A regression suite unchanged.
