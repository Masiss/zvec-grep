# Clean-scan Freshness — W0 Audit and W1/V5–V6 Hardening

Status: **implemented and verified**. Native zvec stays at 0.7.2; ZGM2,
searchable index format, scheduler, UX V2, policy and thresholds are unchanged.

## W0 — generation consistency

Source evidence:

- `workspace/build.rs` prepares a staging build, validates it, then publishes
  via `publish_build`; the active manifest is replaced atomically.
- `workspace/manifest.rs` stores the active `storage_generation`, index version
  and descriptor.
- The clean-scan fast path never publishes storage and never opens the zvec
  store. It only accepts a generation when the manifest generation and index
  version equal the sidecar values and the generation storage metadata seal
  matches.
- A writer lock is held for the normal index path. The sidecar is removed
  before any normal attempt and rewritten only after a successful index with
  unchanged pre/post fingerprints and storage seal.
- Consequence: a generation is verifiable read-only after publication; an
  in-flight generation is never used by the fast path. There is no need for a
  new lock or a second generation-commit mechanism.

## W0 — native integrity

Audited in `rust/docs/deep-storage-integrity-audit.md` and commit `eedd27c`:

- zvec 0.7.2 exposes no collection checksum/verify/scrub/repair API. Open,
  flush, optimize and iter are not verification.
- Native Proxima has CRC32c support, but the Rust-facing path does not expose a
  full collection verification contract, and RocksDB checksum options are not
  surfaced as a stable API.
- Therefore the startup path uses a cheap external metadata seal. It detects
  missing, truncated, replaced or re-identified generation files. It cannot
  detect arbitrary bit rot inside an otherwise unchanged data page.
- Deep verification remains an explicit opt-in storage-open/scrub concern; the
  fast path does not claim it.

## W0 — filesystem freshness

- Metadata fingerprint includes path, size, mtime, ctime (seconds + nanoseconds)
  and inode for every discovered file, plus the serialized scan rules.
- A trusted metadata fast path requires a usable identity and
  `newest_ctime < created_epoch_ms`. `created_epoch_ms` is captured before the
  post-index scan, so any later source change has `ctime >= created` and forces
  a content recheck.
- The trust predicate is strict. Equal timestamps, missing ctime, inode zero or
  unsupported identity fall back to content verification.
- Coarse or non-monotonic filesystem timestamps are therefore a fallback, not a
  silent no-op.
- Content verification remains for untrusted metadata. It is bounded to
  256 MiB; if the content cannot be hashed, the sidecar may still be published
  for non-empty workspaces only when metadata identity is trusted. In that case
  the stored content fingerprint is `null`.

## W1 — V5/V6 changes

- Sidecar version is now **6**; `content_fingerprint` is optional.
- Metadata-only no-op is allowed when the ctime/inode identity is trusted and
  the generation storage seal matches. This removes the unconditional content
  read from the warm path.
- Large workspaces are no longer permanently excluded by the 256 MiB content
  limit. On a successful index, a `null` content fingerprint is published when
  the metadata identity is trusted; later runs use metadata trust. If metadata
  is not trusted, a `null` fingerprint cannot produce a no-op.
- Empty workspaces are trusted without a ctime, because there is no file to
  change.
- Sidecar creation timestamp is taken before the post-index scan; publication
  therefore refuses the snapshot if any file changed during that window.
- Missing/corrupt/legacy sidecars, skipped files, explicit changes, rebuilds,
  scan-rule changes, generation/version mismatch or storage-seal mismatch still
  force normal indexing.
- Added a regression test that a trusted sidecar with a null content
  fingerprint yields `startup_noop`, while the same sidecar with an untrusted
  timestamp falls back to normal indexing. The trust boundary is unit-tested,
  including equal ctime and empty workspaces.
- Added a concurrent direct + watcher-style explicit-change test. The test
  verifies serialized writers, FTS/vector freshness, and that a direct run
  restores the fast path after the watcher job intentionally drops the
  full-workspace sidecar.
- Added a publication-failure test: a failed index removes the sidecar, the
  next successful run republishes it, and the following run is a fast no-op.
- Sidecar publication is refused when any file failed indexing.

## Measured

Fixture: one workspace, 2,000 files, 16,122,000 bytes, already indexed; direct
CLI; same host; 15 repeated runs.

| Metric | V4 baseline | V6 |
|---|---:|---:|
| Warm no-op p50 | 69.48 ms | **49.98 ms** |
| Warm no-op p95 | 72.49 ms | **53.91 ms** |
| Peak RSS | ~36 MB | **36,860 KiB** |

The release binary was rebuilt from the V6 tree. The fast path still acquires no
model and does not open the zvec store. This is a single-host, single-fixture,
warm-page-cache result; it is not a 45-run qualification or a multi-workspace
measurement.

## Limitations

- Deep data-page corruption is still not detected by the fast path.
- A same-size/same-mtime change that fully reverts during one index attempt is a
  residual TOCTOU window; ctime/inode and pre/post checks reduce it but do not
  prove it impossible.
- Filesystems without usable ctime/inode fall back to content verification; with
  content above the limit they cannot use the metadata fast path.
- No multi-workspace or cold-page-cache measurement was added.
- A watcher job carries explicit changes, so it does not publish the
  full-workspace sidecar; the next direct run must rebuild it before the fast
  path returns.

Evidence: `benchmarks/harness-v2/operations/startup-direct/results/after-v6.json`.
