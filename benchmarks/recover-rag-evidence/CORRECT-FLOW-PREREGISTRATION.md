# Correct Fragment Ranking & Selection preregistration

Continuation from `work/recover-rag-evidence-20261126@6eef417`.

## Contracts to fix

1. **Fusion**: each fragment keeps its own retrieval rank and score per route.
   `fuse_fragment_candidates()` must use `InternalEvidence.rank` and
   `InternalEvidence.hit.score`, not entity-level `Candidate.recall`.
2. **Ranking**: route scores come from runtime signals carried on `SearchHit`,
   not from optional `SearchHitTrace`. Results must be identical with
   observability on/off.
3. **Selection**: source identity is `(relative_path, file_sha256)`; overlap is
   computed on fragment `content_range`, never entity range. Only genuinely
   duplicated source lines are clipped/removed; a partially overlapping
   fragment keeps its non-overlapping remainder.
4. **Budget**: byte budget is the primary limit; item count is a resource
   safeguard (default 64 in fragment mode), not the 10-item delivery cutoff.

Stage dumps and benchmark analyzer must record/use fragment ranges.

## Qualification

After unit tests and lineage audit pass, run K arms:

| Arm | Meaning |
|---|---|
| K0 | baseline `5a4c8201…` |
| K1 | previous fragment candidate `6d05ed17…` |
| K2 | corrected flow, mixed relevance |
| K3 | corrected flow, RRF-dominant relevance |

Fixed byte budget 24 KiB, item safeguard 64, 12 Rust + 12 C# dev cases.
Gate: beat K0 and K1 on exact coverage in both languages, no precision
regression, <=25% token growth and <=2x p95 vs K1; task correctness reported.
No held-out, no promotion, no new architecture.
