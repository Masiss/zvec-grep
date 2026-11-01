# UX V2 performance baseline (bounded)

Date: 2026-11-01
Scope: UX V2 control plane only. No production/production-corpus claim.

## Environment

- Host: `masus`, Linux 7.2.11-arch1-1, 20 logical CPUs, 15 GiB RAM.
- Binary: release build of `zg` at commit `d61c37b` (UX V2 feature-complete).
- Fixture: 300 small Rust files under one workspace, local model cached.
- State: isolated `HOME` and `ZVEC_GREP_HOME`; no user daemon involved.
- Host load before: `9.43, 10.12, 6.16`; after: `8.21, 9.83, 6.11`.
  The host was busy, so absolute times are descriptive, not a clean certification.
- Measurements: wall time and max RSS from `/usr/bin/time`; 5 runs for
  direct/auto, 10 runs for `--server jobs`. Single fixture and single host.

## Results

| Path | Runs (s) | Median | Max RSS |
|---|---:|---:|---:|
| `zg --index --mode direct` (warm no-op) | 0.55, 0.52, 0.58, 0.50, 0.65 | ~0.55 s | ~287 MB |
| `zg --index` default auto → warm daemon (no-op) | 0.86, 0.23, 0.23, 0.30, 0.27 | ~0.27 s | ~36 MB |
| `zg --server jobs --json` | 0.00–0.01 | ~0.00 s | ~35 MB |
| `zg --index --no-wait` (auto daemon submit) | 0.00–0.01 | ~0.01 s | ~35 MB |

The first default-auto run (0.86 s) includes watcher activation/reconciliation
after the daemon had been idle; steady-state default routing is ~0.23–0.30 s.

## Interpretation

- UX V2 control-plane overhead is negligible: job listing and no-wait submit
  are both ~10 ms or less.
- Warm daemon index work dominates the default auto path (~0.23–0.30 s).
- Direct index is slower (~0.55 s) and much heavier in RSS (~287 MB) because
  each invocation initializes the engine/model in a short-lived process.
- The measured bottleneck is therefore **process/engine initialization on the
  direct path**, not routing, job visibility, queue rendering, or timeout
  bookkeeping.

## Optimization decision

No UX V2 code optimization is justified by this baseline. The control-plane
paths are already below measurement noise relative to engine work. The only
evidence-backed optimization target would be direct-process engine/model
startup, which is outside this UX track and needs its own authorization and
benchmark. No threshold, policy, RC, or production behavior was changed.

## Limitations

- One host, one fixture, high background load; no counterbalanced pair.
- No production corpus, no 421-file benchmark, no end-to-end watcher latency
  series.
- Times describe the release binary at `d61c37b`; later changes require rerun.
