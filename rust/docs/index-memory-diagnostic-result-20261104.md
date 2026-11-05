# Laya index-memory diagnostic — result

Runs executed 2026-11-04 UTC; analyzed after the usage interruption on
2026-11-05. Protocol: [preregistration](index-memory-diagnostic-plan-20261104.md).
This is a memory diagnostic, **not a performance qualification or memory fix**.

## Result

Both admitted conditions finished successfully, with no failed files. No retry,
cap increase, installed binary change, daemon operation or live-index write.

| | Pilot | Bounded source corpus |
|---|---:|---:|
| Files | 64 | 2,714 |
| Source bytes | 1,887,988 | 67,108,845 |
| Entities created | 1,129 | 24,884 |
| Embeddings computed | 1,602 | 43,140 |
| Exact process max RSS, KiB (`time -v`) | 485,056 | 1,310,944 |
| Exact process max RSS, GiB | 0.463 | 1.250 |
| Sampled max anonymous PSS, KiB | 434,948 | 1,177,300 |
| Sampled max file-backed PSS, KiB | 43,144 | 104,803 |
| Sampled cgroup memory peak, bytes | 595,161,088 | 1,580,982,272 |
| Observed process swap, KiB | 0 | 0 |
| Observed cgroup max/oom/oom_kill events | 0/0/0 | 0/0/0 |
| Wall seconds, descriptive only | 3.29 | 57.36 |

PSS component maxima are independently sampled, not necessarily simultaneous.
Cgroup memory includes charged page cache and the wrapper, unlike process RSS.
Cgroup peak/events are observations while the unit existed, not a final atomic
post-exit measurement. The exact leaf-process RSS peak comes from `time -v`.

The frozen source HEAD is `b4f2dfe78d162d1e8b573d9e3678207f967042bc`;
per-file hashes also pin dirty source bytes. The CLI hash matches the published
RC release manifest (release code commit `289674bc127f664fea0993381c6d109302af95ae`).
This run did **not** exercise the still-unfinished recovered UX/engine ports or
establish that the owner's original >3 GB run used this exact artifact/mode.

## Where memory grew

Bounded-run progress output and 1-second samples show:

- Around 1 s: RSS 278,272 KiB, near model preparation/start of indexing.
- Around 44.8 s, before final optimization: RSS **1,188,212 KiB**.
- Optimization starts around 45.9 s; sampled RSS reaches **1,286,568 KiB**
  during that phase. Saving/finishing occur around 57.0–57.2 s.
- At the retained peak-smaps observation, the main heap accounts for about
  **825 MiB RSS**, other anonymous mappings **323 MiB**, native index-file
  mappings **62 MiB**, shared objects **29 MiB**, other mappings **18 MiB**.
  Smaps and rollup reads are not atomic and can differ slightly.

**Most observed growth is anonymous heap/memory, not merely native mmap pages.**
It develops while indexing, with a further optimization-phase increase. The
raw FP16 vector payload for 43,140 × 256 dimensions is only **21.1 MiB**;
that payload alone cannot explain the process footprint. This does not include
HNSW, lexical indexes, metadata, caches, model memory or transient buffers.

## What this establishes — and does not

The run corroborates the source investigation's concern about growing retained
and transient memory, but **does not reproduce >3 GB or prove a leak**. Anonymous
memory includes Rust and native allocations, model/runtime state and allocator
high-water retention; smaps cannot assign ownership among them. No heap profiler
was installed. One-shot direct CLI runs also do not test long-lived daemon
retention after repeated operations.

Do not extrapolate RAM linearly from file count, compare pilot/full timings as
performance arms, or treat these two different corpora as a controlled scaling
study. Native automatic memory budgeting may differ under the cgroup limit;
the effective native default was not separately probed inside these units.

## Recommended next engineering direction

1. Bound **in-flight bytes**, not only task/embedding counts; account for source,
   extracted content, lexical copies and retained embedding batches. Define a
   non-deadlocking oversized-file path and preserve atomic file publication.
2. Reduce simultaneous per-file copies and stage large files in bounded pieces
   where existing durability/index-format contracts permit it.
3. Investigate native heap/cache/buffer ownership with allocation-level evidence
   before changing global budgets or maintenance cadence. A native memory option
   must not be advertised as a hard RSS limit. Do not revive unsafe outside-lock
   scalar optimize or add frequent optimize calls without evidence.
4. For immediate use, keep generated artifacts/datasets/large serialized outputs
   outside the intended source corpus. Concurrency reduction alone does not bound
   persistent native state. No scan defaults were changed by this diagnostic.

These are proposals, **not implemented RAM fixes**. No additional index run is
needed to finish this diagnostic; a new profiling experiment needs a new plan.

## Evidence

Private artifacts remain under
`~/.local/share/zvec-grep/index-memory-diagnostics/laya-20261104/`.
No copied source, native index, raw smaps or source-path manifest is published.
Key SHA-256 anchors:

| Relative artifact | SHA-256 |
|---|---|
| `pilot/result.json` | `0453f265e40365b0e1ba18f09331c6197f07e95175bbda8ab5147a4dff5fbb6f` |
| `bounded/input-manifest.json` | `2c371c66ec23469fb9613bb59c6cd2a8e258f106ccb8f98e3bdd50df3f7c88c8` |
| `bounded/result.json` | `3c41276be455eea11318202429cded13eeef1e2f695f35d07ef985e54a144599` |
| `bounded/timeline.jsonl` | `51669a0a1e24ed5616249795b601aadb514dbbcdf50227e852b22da61f8abdd7` |
| `bounded/peak.smaps` | `03acbb75057e87f7474f52ea9c2455a67754827a1451f9b9de632721c7d597aa` |
| `bounded/leaf-time.txt` | `560e7026b3f77ec8a7a158fe9cf9fbbaeb091d2dd433028979b8119e9ed61fab` |

Host before/after snapshots include load, memory/swap, pressure, affinity,
disk space and top processes. Memory PSI avg10 stayed zero at both boundaries;
MemAvailable was about 5.0 GiB. Existing system swap use was recorded, not
attributed to the measured `zg` (whose observed swap stayed zero).
