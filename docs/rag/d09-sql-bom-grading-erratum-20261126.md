# D09 SQL BOM exact-grading erratum — 2026-11-26

Applies to frozen C# case D09 and future SQL-inclusive benchmark versions.

## Finding

`DB/Views/EmployeeBaseViews.sql` starts with UTF-8 BOM bytes `EF BB BF`.
The SQL index and delivered pack use the real source content range `3..857`.
The frozen line-based gold conversion uses `0..857`, counting the BOM as gold
content. Therefore the full view is delivered but union-exact reports zero.

## Preservation

Frozen v1 cases/gold/traces/packs are unchanged.

## Required v2 handling

For text evidence spans, exclude a leading UTF-8 BOM from the content range, or
apply the same source-coordinate normalization used for other BOM-bearing
formats. Record the corrected byte span in the v2 manifest/errata. Do not
silently compare corrected gold against uncorrected v1 scores.
