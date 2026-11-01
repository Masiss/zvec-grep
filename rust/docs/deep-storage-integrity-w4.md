# Deep Storage Integrity — W4 Operational Verification and Local Rollout

Status: **PASS.** `zg --verify` and the W4-native seal check are installed on
this host. The storage integrity track is closed.

Baseline: W3 commit `5b218ae`. Date: 2026-11-01.
Native candidate patch (supersedes the W3 patch):
`benchmarks/harness-v2/operations/deep-storage-integrity/w4/patches/w4-integrity-verifier.patch`.

## 1. Decisions implemented

- **Light check at seal; deep verification on request.** Every publish path runs
  a light seal check (required layout + Proxima structure) after the stage is
  closed and before the manifest can select it. Deep verification is the
  explicit `zg --verify` operation; there is no background scheduler.
- **Fail closed on corruption.** `CORRUPT` and generation-identity mismatch exit
  `1`. The verifier never deletes, repairs, rebuilds or switches a generation.
- **No new UX/protocol mode.** `zg --verify` is direct-only and reuses the
  existing CLI/engine/manifest/native layers. `zg --status --verify` keeps its
  pre-existing "include status" meaning. No daemon protocol or scheduler lane
  was added.
- **Local rollout on PASS.** The new `zg` and native library were installed
  after the qualification matrix passed; the pre-W4 pair remains backed up for
  rollback.

## 2. Integration

### CLI

```sh
zg --verify [root] [--json] [--require-complete]
```

Exit codes: `0` PASS_COMPLETE, `1` CORRUPT/identity mismatch, `2`
INCOMPLETE/INCOMPLETE_LEGACY/uninitialized/unavailable. Human output lists
coverage, missing coverage, limitations and component errors; `--json` emits the
full typed report.

### Engine

`zg-engine` exposes `VerifyOptions`/`VerifyResult` and `ZvecGrep::verify`.
The result carries the native report, expected generation, native ABI, explicit
limitations and a fail-closed outcome. The engine never opens a writable
collection for verification.

The active manifest's `storageGeneration` is compared with the UUID reported by
the native verifier. A mismatch or a missing selected generation is `CORRUPT`
(exit 1), not merely unverified.

### Seal check

`WorkspaceIndexService::run_index` calls the native verifier with
`PROXIMA_STRUCTURE | SKIP_STABILITY | LIGHT` on `<generation>/storage` after
`close()` and before `publish_build`. The light path checks the required
`files`/`directories`/`entities`/`fragments_*`/`schema.json` layout and Proxima
header/footer/segment-table structure; it does not open RocksDB, IPC or WAL. A
failed check aborts publication and leaves the active generation untouched.
If the loaded library predates the verifier, the seal check logs a warning and
continues, preserving compatibility with older pairs.

### Native ABI

The C API now exposes `zvec_integrity_abi_version()` (ABI `1`). The Rust wrapper
rejects a mismatched ABI. The pre-W4 native library is **not** a supported pair
for the new binary: it lacks base symbols such as
`zvec_config_data_set_jieba_dict_dir` and fails at dynamic link time. Binary and
library must be upgraded together.

## 3. Coverage

| Category | Check |
|---|---|
| Proxima | header/footer/segment-table CRCs, bounds, content CRC, per-segment CRC |
| RocksDB | read-only open, paranoid checks, `VerifyChecksum` over SST/MANIFEST/CF metadata |
| IPC | Arrow footer, schema and every record batch — structure only |
| sidecars | ZGM2 magic/version/identity/counts/lengths/trailing bytes — structure only |
| WAL | zero-length sealed logs covered; non-empty WAL unsupported → incomplete |
| layout | required generation collections; missing collection → CORRUPT |

`.ipc` and `.bin` have no content checksum. Their payload limitations are listed
in `limitations` (`ipc.content_checksum`, `sidecars.content_checksum`) and are
never silently reported as full payload verification. Structural corruption is
still detected.

## 4. Qualification

Raw receipts:
`benchmarks/harness-v2/operations/deep-storage-integrity/w4/results/`.

- `cli-cases.json` — **25/25 passed**: clean and `--require-complete`; Proxima
  header/content/meta/truncate; RocksDB SST/MANIFEST/CURRENT; IPC
  footer/truncate; sidecar magic/truncate/count/trailing; missing collection;
  missing selected generation; manifest identity mismatch; active writer;
  non-empty WAL; legacy zero-CRC; old-library pairing incompatibility;
  uninitialized workspace; help; `--status --verify` compatibility.
- `crash.json` — **5/5 passed**: SIGKILL at 0.05/0.20/0.60/1.20 s and after
  publication; old manifest preserved before publish, new manifest complete
  after publish; active verify and FTS work; recovery clears the stage.
- `concurrency.json` — 8 parallel verifies + 8 parallel FTS searches passed;
  verifies during a writer returned `PASS_COMPLETE` or `INCOMPLETE`, never
  `CORRUPT`; FTS stayed available.
- `restart.json` — scratch daemon on 7998 restarted; installed daemon on 7999
  stopped/started and reported ready; verify after restart passed.
- `disk-full.json` — RLIMIT_FSIZE write-failure proxy passed: failed write left
  the active generation `PASS_COMPLETE`, verify worked under the limit, and
  recovery reindexed successfully. True ENOSPC is `OPERATION_BLOCKED`: this host
  has no isolated mount privilege.
- `regression.json` — FTS and hybrid outputs are byte-identical before and after
  a deep verify (39-line FTS, 19-line hybrid, rc 0).
- `native-abi.json` — verifier ABI 1; new library exports the verifier; old
  library does not and is not a supported pairing.
- `measurements.json`:
  - deep verify, 201 files: 0.17 s wall, 30 MiB max PSS, no writes;
  - legacy verify: 0.11 s wall, rc 2;
  - light seal probe: 41 ms wall, 8.7 MiB max PSS;
  - one-file incremental (new binary, direct): 7.72 s wall including model load,
    1.11 s CLI-reported indexing duration, 365 MiB max PSS;
  - clean no-op (new binary, direct): 21 ms wall, 6 ms internal duration.
- Rust: `zg-engine` lib **563 passed / 0 failed / 39 ignored**;
  `zg-cli` **95 passed**; `zg-integrity-native` **4 passed**. Focused
  `pipelines::indexing::service::tests` **34 passed** with the custom library.
- `cargo fmt` on changed crates passes. New-code clippy warnings were fixed;
  workspace-wide `clippy -D warnings` still trips pre-existing warnings in
  `zg-host-native` and unrelated engine/CLI code.

## 5. Local rollout

- Backup:
  `/home/masis/.local/share/zvec-grep/rollback-20261101T130300Z/`
  - `zg` SHA-256 `ad1d9c2c…` (0.3.0-rc.2)
  - `libzvec_c_api.so` SHA-256 `5093c7d7…`
- Installed:
  - `/home/masis/.local/bin/zg` SHA-256 `6db0f67e…` (0.0.1)
  - `/home/masis/.local/lib/libzvec_c_api.so` SHA-256 `532f16dd…`
- The old daemon on 7999 was stopped gracefully (no active jobs), the pair was
  installed, and the daemon was restarted. `--server status --check-ready`
  reported ready.
- Smoke: `--version`, `--verify` (`PASS_COMPLETE`, ABI 1), `--status`, FTS,
  hybrid search and server-mode FTS all returned success.
- Rollback was confirmed by running the backed-up `zg` with the backed-up
  library: version `0.3.0-rc.2`, old-workspace status and FTS succeeded. Rollback
  is copying the two backed-up files back and restarting the daemon.

## 6. Compatibility finding and residual risks

- The pre-W4 rc.2 binary writes a different workspace manifest schema
  (`.zvec-grep/index`) than the current development engine. The new binary
  reports `rebuild-required` and does not rewrite or delete such a workspace.
  No registered live workspace exists outside scratch fixtures; users with an
  old-rc workspace must rebuild explicitly.
- Mixed binary/library pairs are unsupported; install them together.
- `.ipc`/`.bin` payload checksums do not exist; only structure is verified.
- True disk-full (ENOSPC) was not exercised; the bounded write-failure proxy is
  the evidence.
- No power-loss durability claim; crash evidence is `SIGKILL` only.

## 7. Track closure

Storage integrity W2/W2b/W2c/W3/W4 are complete and the track is closed. The next
track is RAG R0/R1 under `docs/rag/`. Do not open a new optimization track.
