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

Completed at checkpoint: **9/15 cells, 27/45 measured repetitions**, all passing
receipts. Cell `4_restart_delete` hit a real disk-full failure (460G volume
100%); completed cell indexes were removed after archiving raw telemetry, and
the runner resumed from that cell. Raw telemetry is archived under
`results/runs/<cell>/telemetry-rep-*.jsonl`.

Known runner limitation: current repetitions check FTS but not the vector route;
the vector parity guard remains outstanding. No PASS/FAIL or #10 verdict is
claimed.
