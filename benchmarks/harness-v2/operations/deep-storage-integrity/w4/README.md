# W4 — Operational Integrity Verification and Local Rollout

Status: **PASS; installed locally.**

Design and results:
[`rust/docs/deep-storage-integrity-w4.md`](../../../../../rust/docs/deep-storage-integrity-w4.md).

## Contents

- `run_cli_cases.py` — 25-case `zg --verify` fault matrix (clean, corruption,
  identity, active writer, WAL, legacy, unavailable/ABI pairing, CLI help and
  status compatibility).
- `run_crash.py` — 5-case SIGKILL crash/recovery matrix.
- `run_concurrency.py` — parallel verify/search and verify-during-index.
- `run_disk_full.py` — RLIMIT_FSIZE write-failure proxy (true ENOSPC blocked).
- `measure.py`, `light_probe.py` — CPU/PSS/I/O and light seal measurements.
- `patches/w4-integrity-verifier.patch` — full W4 native candidate patch
  (supersedes the W3 patch; includes the verifier ABI and light-seal flags).
- `results/` — CLI matrix, crash, concurrency, disk proxy, restart, regression,
  native ABI, measurements, rollout and provenance receipts.

## Run

```sh
cd /home/masis/src/zvec-grep

cmake --build .local/native/zvec-native-build --target zvec_c_api -j 8

python3 benchmarks/harness-v2/operations/deep-storage-integrity/w4/run_cli_cases.py \
  --binary /home/masis/.local/bin/zg \
  --old-binary <new-zg-with-old-library> \
  --clean-workspace <clean> --legacy-workspace <legacy> \
  --out benchmarks/harness-v2/operations/deep-storage-integrity/w4/results/cli-cases.json
```

`zg --verify` is direct-only; no daemon protocol or scheduler lane was added.
`.ipc` and `.bin` are structural checks only and are reported as limitations,
never as full payload verification.
