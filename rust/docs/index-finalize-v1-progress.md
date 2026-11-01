# Index/Finalize Qualification V1 — progress checkpoint

Status: **RUNNING / partial**, not a verdict.

- Protocol: `671b106`, unchanged.
- Preflight gaps closed: `smaps_rollup`, model-cache SHA-256, cgroup scope,
  `memory.peak` reset.
- Runner: `benchmarks/harness-v2/operations/index-finalize/run_qualification.py`
  (non-blocking, resume per cell/repetition).
- Fixture: committed manifest SHA-256
  `57ce6408a8aeacedd439f9173fd3a394ab09ba0af2b3c8a576990b307134804d`.
- Results path: `benchmarks/harness-v2/operations/index-finalize/results/runs/`.

Completed at checkpoint: **2/15 cells, 6/45 measured repetitions**, all pass
(FTS correctness, delete visibility, job failures excluding expected watcher
cancellation). Raw telemetry lives under `.tmp/ifq/cells/`.

Known runner limitation: current repetitions check FTS but not the vector route;
the vector parity guard remains outstanding. No PASS/FAIL or #10 verdict is
claimed.
