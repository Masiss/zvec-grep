# Crash-Atomic Incremental Generation — W2c

Status: **PASS for W2c acceptance; W3 remains gated on the verifier surface.**

Baseline: `50d53ea` (W2b). Implementation: `d366692`. Date: 2026-11-01.

Scope: `zg-engine` incremental publication only. Native zvec remains pinned to
0.7.2; ZGM2, searchable index format, scheduler, UX V2, policy and thresholds
were not changed. No installed `libzvec_c_api.so` or production binary was
replaced.

## 1. What changed

Before W2c, rebuilds used a staged generation, but ordinary incremental writes
opened the **active** generation read/write in place. A `SIGKILL` mid-write left
that active generation partially mutated. W2b checksums could detect it, but
there was no older generation to read while recovering.

W2c makes every ordinary incremental write staged:

- `workspace/build.rs::prepare_incremental_build()`:
  - reads the current manifest;
  - assigns a new storage generation UUID;
  - writes the build ownership record **before** creating storage;
  - creates the stage directory;
  - clones the active generation into the stage;
  - fsyncs cloned file data and directories.
- `clone_generation()`:
  - uses `FICLONE` (`rustix::fs::ioctl_ficlone`) on Linux when supported;
  - falls back to `fs::copy` plus `sync_all` when reflink is unavailable;
  - never hardlinks mutable files;
  - rejects non-file, non-directory, non-symlink entries;
  - recreates symlinks.
- `pipelines/indexing/service.rs`:
  - captures `active_storage` from the existing manifest;
  - uses `prepare_build()` for explicit rebuilds;
  - uses `prepare_incremental_build()` for ordinary incremental, rename and
    settings-refresh paths when an active generation exists;
  - opens, closes and seals the **stage**, then publishes through the existing
    `publish_build()` manifest path;
  - the clean-scan fast path still bypasses storage only when it can prove there
    are no writes.
- Failure/recovery:
  - any error before publication discards the stage; the active manifest and
    generation remain untouched;
  - a crash before publication leaves the old manifest active and a build record
    for the next writer to discard;
  - a crash after publication leaves the new manifest active and the build
    record for recovery cleanup;
  - cleanup never deletes a generation that the active manifest references.

The workspace write lock is held across clone, stage write and publication, so
the source generation cannot be modified by another writer or removed by a
reader transition during the copy.

## 2. Crash/failure evidence

Matrix: 200 files, custom W2b native library, staged incremental, `SIGKILL` at
six points plus one kill immediately after the manifest generation changed.
Verifier: W2c standalone native verifier. Raw receipt:
`benchmarks/harness-v2/operations/deep-storage-integrity/w2c/results/crash-results.json`.

| Case | Killed | Old manifest kept | Verify active | FTS query | Recovery | Recovery verify |
|---|---:|---:|---|---:|---:|---|
| delay 0.05 s | yes | yes | PASS_COMPLETE | 0 | 0 | PASS_COMPLETE |
| delay 0.15 s | yes | yes | PASS_COMPLETE | 0 | 0 | PASS_COMPLETE |
| delay 0.35 s | yes | yes | PASS_COMPLETE | 0 | 0 | PASS_COMPLETE |
| delay 0.70 s | yes | yes | PASS_COMPLETE | 0 | 0 | PASS_COMPLETE |
| delay 1.20 s | yes | yes | PASS_COMPLETE | 0 | 0 | PASS_COMPLETE |
| delay 1.80 s | yes | yes | PASS_COMPLETE | 0 | 0 | PASS_COMPLETE |
| after publish | yes | no (new manifest) | PASS_COMPLETE | 0 | 0 | PASS_COMPLETE |

For all six pre-publication kills:

- active manifest generation was unchanged;
- the old generation still answered FTS queries;
- a separate 200-file run answered a vector query against the old generation
  after the kill (`results/vector-old-after-crash.txt`);
- `build.json` remained for recovery;
- the next writer removed the abandoned stage and completed successfully.

The after-publish kill produced a new active generation with a complete
verifier result; recovery removed the leftover build record.

These results cover `SIGKILL` consistency. They do **not** claim power-loss
durability; filesystem and storage-controller cache behavior was not faulted.

## 3. Cost and disk amplification

Fixture: 2,001 source files, 881 MiB source, 858 MiB sealed generation, btrfs.
Full receipt: `w2c/results/cost.json`.

| Operation | Wall | Max RSS | File-system output |
|---|---:|---:|---:|
| Initial index | 65.92 s | 2,466,544 KiB | 5,137,872 blocks |
| One-file incremental (staged) | 16.14 s | 1,239,904 KiB | 2,163,184 blocks |
| Recovery incremental after kill | 13.00 s | 1,237,500 KiB | 2,126,800 blocks |
| `cp --reflink=always -a` generation | 0.53 s | 2,288 KiB | 2,912 blocks |
| Byte copy to tmpfs (reference only) | 0.29 s | 2,548 KiB | 0 |

Crash during the staged incremental, before recovery:

| Generation | Total | Exclusive | Shared |
|---|---:|---:|---:|
| Old active (858 MiB logical) | 864.84 MiB | 0.00 MiB | 864.84 MiB |
| Stage (858 MiB logical) | 866.34 MiB | **1.56 MiB** | 864.78 MiB |

Logical peak is approximately two generations (~1.7 GiB), but reflink physical
increment at the crash point was **1.56 MiB**. After successful recovery the old
generation was removed and one generation remained (773.78 MiB total; 692.37 MiB
exclusive).

The staged incremental is slower than the old in-place path because it clones
and reopens a generation; it is not a latency optimization. The clone itself is
small relative to the native open/rewrite/close cost. No performance claim is
made beyond the measured fixture.

## 4. Native checksum residual found during W2c

With the custom W2b writer, some large indexes had a small late `HnswT4S0`
segment (204 bytes) with `segment_crc=0` while the header capability and
`content_crc` were valid. `content_crc` covers the full content region,
including that segment, so the artifact is not silently corrupt. W2c's verifier
therefore defines `PASS_COMPLETE` from capability + non-zero `content_crc` and
reports zero per-segment CRCs separately as `segments_without_crc`.

This is a localization/coverage gap for a future native writer refresh, not a
false PASS. Legacy indexes with `content_crc=0` still return `INCOMPLETE`, as
established by W2b. W3 should recompute per-segment CRCs in the final flush if
per-segment localization is required.

## 5. Verification

- `cargo test -p zg-engine --lib workspace::build::tests` — **18 passed**
- `cargo test -p zg-engine --lib pipelines::indexing::service::tests` — **34 passed**
- `cargo test -p zg-engine --lib` — **560 passed, 3 failed, 39 ignored**; the
  three failures are the pre-existing workspace authorization fixtures
  (`authorization::tests::*`), unrelated to this change.
- `rustfmt --edition 2024 --check` on both changed files — pass.
- W2c crash matrix — 7/7 killed cases; 7/7 active verifier PASS_COMPLETE;
  7/7 FTS queries succeeded; 7/7 recoveries succeeded.
- No benchmark workload was run beyond the W2c cost fixture; no native library,
  scheduler, UX V2, policy or threshold was changed.

## 6. Limits / next gate

- Reflink is filesystem-dependent; the fallback copies bytes and can double
  logical and physical peak disk while the stage exists.
- The single-writer lock means no concurrent reader is served during clone or
  publication; readers after a crash use the old generation.
- W3 still needs the C/Rust verifier surface, `.ipc`/`.bin` and WAL coverage,
  generation identity, and a read-only stable-generation verification operation.
- This work does not reopen Deferred Maintenance #10 or native concurrent
  optimize.
