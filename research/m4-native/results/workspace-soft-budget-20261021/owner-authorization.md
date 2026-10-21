# Owner authority — 2026-10-21 exploratory workspace amendment

Active user explicitly approves, only for contention-aware exploratory mode:
- Treat 512 MiB allocated / 1 GiB apparent workspace thresholds as monitored soft budgets.
- Retain raw counters and INCOMPLETE/UNKNOWN on scanner publication races; never fake PASS.
- Warn with observed/budget/excess bytes on sampled workspace breach. Hard RAM1GiB,
  swap0, CPU200%, tasks128, timeout, host RAM/disk reserves, OOM/distress,
  correctness/durability and verified owned-only cleanup remain unchanged.
- Qualification, other benchmarks and unrelated running projects remain untouched.
- Minimal policy/runner changes, focused synthetic tests, watchdog verification,
  commit without push. No quota infrastructure or indefinite repair loop.
- Once verified, one NEW native diagnostic attempt is explicitly authorized,
  not an automatic retry. Perform one fresh readiness/provenance check; launch
  only if PASS, stop without retry on formal safety refusal.
- Historical campaign4/4 exhausted and raw receipts remain unchanged.

This record transcribes the current instruction, not new authority inferred from
history. Conservative execution interpretation: exactly ONE full-sync 500x20
native arm (not a second-arm comparison/pair). Separate one-attempt ledger,
max_attempts=1, fresh unique operation and workspace. No candidate/treatment,
no tracing, same pinned full-sync binary; no production source/index changes.

The owner accepts the lack of a hard aggregate workspace bound and deterministic
overshoot guarantee in this exploratory mode. Shared disk reserves/distress are
still hard; no filesystem quota or exclusive disk reservation is claimed.
Known publication races alone may continue as partial accounting; unknown scope,
permission/EIO, scanner coverage/budget failure and missing independent hard
safety telemetry still abort. Nothing permits ignoring a hard guard failure.
