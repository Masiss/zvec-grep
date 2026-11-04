# Storage Footprint Optimization evidence

Baseline: `4b3bded`. Fixture: 2,000 deterministic 8 KiB files, one workspace.
No searchable index format, policy, threshold, scheduler, or UX V2 change.

- `results/summary.json` — before/after disk, timing, RSS, breakdown.
- `results/search-latency-after.json` — post-change FTS/vector latency.
- `results/growth-after.json` — insert/delete growth on the compact index.
- `results/vector-parity-after.json` — initial/insert/delete/restart parity.

Run notes:

- Before total: 980,261,619 bytes; after: 899,519,613 bytes.
- Saved: 80,742,006 bytes (8.24%).
- Reconciliation sidecars: 134,488,000 → 50,172,880 bytes (62.7%).
- Index + finalize: 203.13 s → 138.19 s (single run; not a controlled speedup).
- Peak RSS: 2.087 GiB → 2.068 GiB.
- Search: FTS mean 633 ms, vector mean 717 ms after the change.
- Vector parity: 4/4 checks pass.

Limitations: pre-change search latency was not retained when the old index was
rebuilt; peak PSS was not sampled in this run. Timing is single-run per
condition. Raw fixture/index data remains under `.tmp/` and is not committed.
