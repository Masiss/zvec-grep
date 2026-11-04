# Direct-path startup evidence

Baseline `3be680b`; one workspace, 2,000 × 8 KiB files, already indexed.

- `results/baseline.json` — direct CLI and warm no-op before.
- `results/after.json` — V4 warm no-op repeated measurement.
- `results/after-v6.json` — V6 metadata-trusted warm no-op (15 runs).
- `results/summary.json` — profile, incremental/rebuild/daemon context.

The fast path skips the 407 ms zvec read/write open and does not acquire a model.
V6 also skips the content fingerprint when ctime/inode metadata is trusted, and
supports large workspaces whose content exceeds the 256 MiB hash limit.
Dirty/incremental/rebuild paths remain on the normal index pipeline and refresh
the sidecar only after success. Raw indexes are temporary and not committed.
