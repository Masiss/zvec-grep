# Discovery stage profile and rchar

Date: 2026-11-08. Tool:
`benchmarks/rag-impact/tools/discovery_stage_profile.py`.
Receipt: `results/discovery-stage-profile.json`.

## Per-stage (12 dev cases)

| Stage | Measured |
|---|---:|
| Search output bytes | **6.93 MB** |
| Source bytes read | **19.64 MB** |
| Search wall | **43.5 s** total (~3.6 s/case) |
| Source read wall | **37 ms** total (page cache warm) |
| H1 pool units | 88–336/case |

## rchar explanation

- H2 qualification reported rchar **736.8 MB** while source bytes were ~19.6 MB
  and search stdout ~6.9 MB. The gap is **not source-corpus I/O**.
- The qualification PSS sampler reads `/proc/self/smaps_rollup` every 10 ms for
  ~40 s; that file is large enough to dominate `rchar` (hundreds of MB).
- Therefore the earlier 723/736 MB rchar is a **measurement artifact of PSS
  sampling**, not evidence of reading the repository hundreds of times.
- Correct comparisons should use source bytes (19.6 MB), search payload
  (6.9 MB) and stage wall/CPU separately.

## Resources (H2 full vs bounded)

- Full H2: p95 wall 5,314 ms/case, p95 CPU 1.00 s, peak PSS 13,367 KiB.
- Bounded H2: p95 wall 4,052 ms/case, p95 CPU 0.44 s, peak PSS 11,955 KiB.
- Latency units are **milliseconds**; documents previously written as “5.314 ms”
  meant **5,314 ms**. This is corrected here.
- H4: 144 queries, 97.3 s wall; CPU/PSS not separately sampled.
