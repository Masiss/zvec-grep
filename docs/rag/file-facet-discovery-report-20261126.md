# File/facet discovery expansion v1 — NO-GO

Date: 2026-11-26. One preregistered offline arm; no tuning after results.

## Verdict

**NO-GO. Do not port this query-expansion configuration.**

It rescued D04 but lost mandatory R10, did not rescue D09/D10, and reduced C#
macro precision. More importantly, it exposed a frozen-index coverage mismatch:
the C# index includes only `csharp`, while the gold set contains `.sql` files.

## Configuration

- Binary: clean `zg-6b6f294`.
- Same frozen corpora, model cache, G1K4 ranker/selector and 24 KiB/64-item cap.
- C0 control reproduced all 24 frozen packs byte-for-byte.
- Arm A added at most four FTS query groups:
  - up to two explicit identifiers matching a file stem;
  - rare and broad IDF-ordered query-term facets.
- No new vector groups, pool merge, reranker, resolver change or reindex.

## Paired results

| Split | All-gold C0 -> A | Exact C0 -> A | Macro precision C0 -> A | Mean tokens C0 -> A |
|---|---:|---:|---:|---:|
| Rust | 2 -> 1 | 7 -> 7 | .03536 -> .03861 | 7,873 -> 7,957 |
| C# | 1 -> 2 | 2 -> 3 | .00946 -> .00608 | 6,597 -> 6,828 |

- Gained: C# D04.
- Lost: Rust R10, exact 3/3 -> 1/3.
- D09 and D10 remained 0 exact / not all-gold.
- Zero pack integrity errors; all hashes and frozen sources unchanged.

## Discovery expansion was much broader than intended

| Split | Mean new fragments | Max new fragments | Mean new files | Max new files |
|---|---:|---:|---:|---:|
| Rust | 125.25 | 180 | 20.25 | 48 |
| C# | 216.75 | 483 | 89.67 | 214 |

The engine applies the 50-hit limit per query group before fusion. Four extra
groups therefore expanded the candidate pool far beyond the suggested
+16-fragment/+4-file diagnostic target. The 24 KiB delivered cap still held, but
fusion noise regressed mandatory cases.

## Root-cause finding: SQL is not in the C# index

The copied frozen C# manifest records:

```json
"included_formats": ["csharp"]
```

The corpus contains two `.sql` files, and gold includes spans from them. They
were never indexed, so no amount of entity/fragment ranking can recall them.

Concrete evidence:

- D09 query contains `EmployeeBaseViews`, but the identifier FTS route recalls
  `CoreDBContext.cs`; `DB/Views/EmployeeBaseViews.sql` is absent from every trace
  stage.
- D12's selected stored-procedure SQL is absent for the same reason.
- D10's `CoreService.cs` is present, but the gold class header and
  `CoreDBContext.cs` are not delivered; the candidate pack contains only a
  different CoreService entity.

This is a coverage/index-representation limitation, not a weighting bug.

## Erratum and next decision

Frozen v1 is not edited. A versioned benchmark/index errata is required before
comparing future C# arms: either

1. build a frozen **index v2 with `csharp,sql`**, preserving the same corpus/model
   and recording a new manifest; or
2. add a separate **direct exact-file source route** for file-stem matches, which
   can cover D09/D10 without reindex but is a different pack/ranking policy.

The direct exact-file route is the smaller next experiment for D09/D10. Broad
SQL recall (D07/D12) likely requires index v2. Neither is authorized or run by
this report.

## Artifacts

- `PREREGISTRATION.md`, `PREREGISTERED-HASHES.sha256`
- `RUN-C0.json`, `RUN-A.json`
- `audit/P0-PARITY.json`, `audit/DISCOVERY-DELTA.json`
- `scores/SUMMARY-A.json`, `GATES.json`
- `packs/`, `traces/`, `logs/`
- `INPUTS.sha256`, `RUNTIME-INPUTS.sha256`, source hash manifests
