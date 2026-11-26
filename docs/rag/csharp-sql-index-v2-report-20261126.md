# C# SQL index v2 — index-only NO-GO

Date: 2026-11-26. Isolated reindex; frozen v1 and local production index unchanged.

## Build

- Formats: `csharp,sql`.
- Files: 818 scanned, 818 added, 0 failed.
- Entities: 15,039.
- Generation: `d9bc5400-05b9-404e-a0cc-0fbf167872e9`.
- Duration: 14.47 s wall; max RSS 729,228 KiB.
- Index size: about 100 MiB.
- Source/model/binary hashes unchanged.

## Unchanged C0 results

| Metric | Frozen C# v1 | C# SQL v2 |
|---|---:|---:|
| All-gold cases | 1/12 | 1/12 |
| Exact spans | 2/25 | 2/25 |
| Macro precision | .009457 | .012356 |
| Mean tokens | 6,596.50 | 6,590.42 |

All packs passed integrity, gap, item and byte checks. Strong gates still fail:
no new all-gold case, exact <4, D04 not all-gold. D11 remains all-gold.

## What changed

- D09's `EmployeeBaseViews.sql` is now recalled at rank 22, selected and delivered.
- D10's `CoreService.cs` and `CoreDBContext.cs` enter the pool, but at ranks
  145/179 and 488/567; they do not reach the 24 KiB pack.
- D07/D12's `sp_UpdateOrgTotalEmployee.sql` is indexed but not recalled by the
  current questions.

## D09 BOM grading erratum

The SQL file has a UTF-8 BOM. The pack correctly delivers source bytes
`3..857` (854 content bytes). Frozen gold line-to-byte conversion uses `0..857`,
including the three BOM bytes, so exact grading reports 0 despite the full SQL
view content being present.

This is a format/gold coordinate mismatch. Frozen v1 gold is not edited. A v2
grader must exclude the BOM from content spans or record a versioned gold
errata. With that correction, D09 would be one exact/all-gold case.

## Verdict and next step

**Reindexing is necessary coverage repair, but not sufficient quality repair.**
Under frozen v1 gold/gates the index-only arm is NO-GO. It establishes the
correct foundation and exposes the next two distinct issues:

1. versioned SQL/BOM benchmark v2;
2. ranking/scheduling for pool-present D10, and query context/recall for D07/D12.

No query expansion, reranker, second index or production reindex was run.
