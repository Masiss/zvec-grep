# Bounded Laya index-memory diagnostic — preregistration

Owner authorized trying a larger source index to investigate `zg` process RAM,
and offered Laya or NhiDong. Use Laya only; no HIS/EMR access is required.

## Frozen inputs and identities

- Source root `/home/masis/app/laya-mini`, Git HEAD
  `b4f2dfe78d162d1e8b573d9e3678207f967042bc` (dirty source bytes independently hashed).
- The root is ~99 GB including weights/artifacts. Do NOT index/copy all of it.
  Select only non-hidden `.py/.rs/.ts/.tsx/.js/.cs/.md` source, excluding
  data/model/artifact/report/archive/test/heldout/locked/official/production/R6/R7
  scopes, symlinks, embedded third-party trees and files over 1 MiB.
- Deterministic pathname-hash order; at most 4,096 files and 64 MiB total.
  Prepared bounded corpus: **2,714 files / 67,108,845 source bytes**.
  Separate size-stratified **64-file pilot**. Each source copy is hash-checked.
- Immutable manifests/copies under private
  `/home/masis/.local/share/zvec-grep/index-memory-diagnostics/laya-20261104/`.
  No input source or test/heldout contents are published to this repository.
- Preserved RC CLI SHA `ad1d9c2c1aca53d1b1c113ca84f5b1006289592df9e8e85b9f18d1629fba7d0a`;
  native SHA `5093c7d722304b0efaa7425f206456ca902dc377d5a0ae8056f1c5c010fe763e`.
  Copies are paired in the private run directory; no installed file is changed.
- Explicit direct CPU indexing, local `potion-code-16m-v2`, revision
  `e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b`, embedding concurrency 2.
  Cached model/tokenizer copied privately. Clean HOME/config/state/cache,
  proxy blackhole and AF_UNIX-only service prevent remote model/embedding access.

## Envelope and launch conditions

One attempt per admitted condition, unique user-systemd unit identity.

| Condition | MemoryMax (whole unit) | SwapMax | CPU | RuntimeMax |
|---|---:|---:|---:|---:|
| Pilot | 1 GiB | 0 | 2 CPUs / quota 200% | 180 s |
| Bounded corpus | 2 GiB | 0 | same | 600 s |

- Verify user-systemd containment properties with `/usr/bin/true` before indexing.
- Before each measurement: record load, memory/swap, CPU/memory/I/O PSI, affinity,
  disk space, top processes. Require MemAvailable >= MemoryMax + 2 GiB and at
  least 8 GiB disk free. Do not launch during compiler/linker work; recovery
  writers temporarily hold new Cargo validation, but may continue source edits.
- Stop if disk free falls below 6 GiB; unit controls memory/swap/runtime. Do not
  increase caps or retry a failed/limit-hit attempt. Preserve failures verbatim.
- Admit bounded corpus only if pilot succeeds and its conservative file/byte
  scaling ETA is <=480 s. This is a feasibility gate, not a linear-memory model.
- No daemon request, source-root `.zvec-grep` write, live index open/rebuild,
  original source mutation, installed process restart or model download.

## Evidence and interpretation

The private helper `/home/masis/.pi/agent/reports/profile_zg_index_memory_20261104.py`
records timestamped progress output; once-per-second `zg` RSS/PSS, anonymous/
file-backed PSS, swap, CPU/time/I/O and affinity; cgroup memory/peak/stat/events;
and peak smaps. `/usr/bin/time -v` records exact leaf-process maximum RSS even
if polling misses a brief peak. Record the host state immediately after each run.

No heaptrack/valgrind/perf profiler is installed. This run can distinguish file
residency from anonymous memory and correlate peaks with phases; it cannot assign
every anonymous allocation to Rust vs C++ or prove a leak. Cgroup page cache is
not equivalent to process RSS. The cgroup also changes the native default memory
budget; do not present a cap-limited peak as an unconstrained 9k-file reproduction.

Single host, one necessary pilot and one conditional bounded run, no timing
comparison or performance promotion. A capped/OOM result is censored failure,
not a measured unconstrained peak and not permission to run the 99 GB folder.
