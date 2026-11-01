# Direct-path startup evidence

Baseline `3be680b`; one workspace, 2,000 × 8 KiB files, already indexed.

- `results/baseline.json` — direct CLI and warm no-op before.
- `results/after.json` — final warm no-op repeated measurement.
- `results/summary.json` — profile, incremental/rebuild/daemon context.

The fast path skips the 407 ms zvec read/write open and does not acquire a model.
Dirty/incremental/rebuild paths remain on the normal index pipeline and refresh
the sidecar only after success. Raw indexes are temporary and not committed.
