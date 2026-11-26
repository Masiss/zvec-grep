# Frozen C# benchmark/index coverage erratum — 2026-11-26

Applies to: `fragment-coordinate-repair-20261126/final/frozen/csharp-corpus`.

## Finding

The frozen index manifest has `included_formats: ["csharp"]`. The corpus and
gold set include SQL files, including:

- `DB/Views/EmployeeBaseViews.sql`
- `DB/Store/sp_UpdateOrgTotalEmployee.sql`

Those files are not present in raw discovery or any downstream candidate stage.
C# exact-recall ceilings measured on this index therefore exclude valid gold
evidence by construction.

## Preservation

Frozen v1, its hashes, cases, packs and traces remain unchanged. This erratum
does not rewrite v1 or authorize a reindex.

## Required future versioning

Any corrected benchmark must be issued as a new version with:

- explicit `csharp,sql` scan formats;
- a new index generation and manifest hash;
- unchanged source/case/gold hashes, or a separately documented gold errata;
- a fresh C0 parity baseline;
- no silent comparison of v2 results against v1 as if coverage were identical.
