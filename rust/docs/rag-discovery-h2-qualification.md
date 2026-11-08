# H2 pool qualification

Date: 2026-11-08. Tool: `benchmarks/rag-impact/tools/discovery_h2_qualification.py`.
Receipt: `benchmarks/rag-impact/results/discovery-h2-qualification.json`.

## Pool

- **15,683 unique ranges** total; **13,525** added over H1.
- Provenance: search 2,158; C# method 11,632; C# type 1,893; SQL 0.
- **0 duplicate ranges** after dedup.
- 1,266 files read; ~19.6 MB source bytes; `rchar` 736.8 MB (per-case
  re-reads/cache), `syscr` 207,739.
- Coverage: **19/25 (76%)**, macro 0.8611.

## Coverage@budget (insertion order)

At 100 ranges/case coverage is 9/25; at **500 ranges/case it is 19/25** and
does not improve through 15,683. The 13.5k expansion is over-expansion relative
to the 76% coverage it buys.

## Resources

- p95 per-case pool wall: **5,314 ms**.
- p95 per-case CPU: **1.00 s**; total CPU user+sys ~9.58 s.
- Peak process PSS during the 12-case run: **13,367 KiB** (~13 MiB).
- I/O rchar 736.8 MB due to rereading pool files per case; write 40 KiB.
- No Selection/top-10 in this pool.

## Interpretation

H2 is monotonic and deduplicated, but its 15.7k-range pool is not a retrieval-
relevance claim. A 500-range/case budget retains the same 19/25 coverage; the
extra ranges are not justified by coverage and increase cost. H2 alone is below
the 80% Discovery gate.
