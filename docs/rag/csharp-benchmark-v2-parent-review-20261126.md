# Parent review — C# benchmark v2

Verdict: **PASS for baseline validity; not a quality-promotion approval.**

Independent checks performed by the parent:

- `MANIFEST.sha256`: 843 entries, zero failures.
- Corpus: 822 source files preserved.
- Gold conversion recomputed independently from source bytes.
- Exactly two changes versus v1:
  - D09 `EmployeeBaseViews.sql`: `[0,857) -> [3,857)`;
  - D12 `sp_UpdateOrgTotalEmployee.sql`: `[0,2980) -> [3,2980)`.
- All other 23 byte spans unchanged; removing v2 metadata/byte_spans reproduces
  the v1 case JSON exactly.
- All 12 packs independently validated: status ok, zero gaps, valid
  hash/range/content, <=64 items, <=24,576 bytes.
- All 12 packs byte-identical to the prior SQL-index-v2 C0 replay.
- Independent union-by-path/hash scoring reproduced:
  - all-gold 2/12;
  - exact 3/25;
  - macro precision 0.012356252949315773;
  - mean tokens 6590.416666666667;
  - D09 and D11 exact/all-gold.
- Main and RAG worktrees remained clean.

Accepted limitations:

- One internal Proxima cache file changed on index read; pre-run hashes are
  preserved, the manifest/sources/packs did not change, and no rebuild occurred.
- The copied model artifact marker ctime changed; payload hashes did not change
  and the frozen marker was restored byte-for-byte.
- Timing is a single run under host load/swap pressure and is not a p95 claim.

The v2 baseline is suitable for separately preregistered ranking/recall arms.
No such arm was run or authorized by this review.
