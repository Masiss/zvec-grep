# M4 capacity enforcement — bounded feasibility decision (2026-10-21)

## Decision and task closure

**Choose direction 3: retain NO-LAUNCH under the current resource contract.**
**This focused feasibility investigation is COMPLETE.** Native writer-tail remains
**PARTIAL / OPERATION_BLOCKED**; the campaign remains 4/4 consumed, zero remaining.
This is a decision, not a request for another harness-repair iteration.

Neither a cheap existing filesystem facility nor a small M4-only writer change
has been shown to enforce BOTH current whole-workspace limits. Do not present a
partial implementation as capacity proof. No quota, guard, resource-plan value,
runner, native source, durability setting or campaign ledger was changed. No M4
executable, benchmark, A/A arm, admission poll or campaign was launched.

The alternative of **an explicitly weaker, owner-approved risk contract** is
technically coherent, but it is not demonstrated hard enforcement and is NOT
adopted by this report. The historical footprint supports discussing that tradeoff;
it does not establish a maximum growth rate, maximum overshoot, or a safe new cap.
There is no default next engineering task. Resume only after a separate owner
choice: accept specified residual shared-filesystem risk in a revised contract,
or authorize separately scoped storage isolation. Either still needs new native
execution authority and a new finite budget. If neither is wanted, stay no-launch.

## Authority, scope, and evidence

Owner request: investigate Btrfs quota/accounting feasibility, writer-side apparent
enforcement, proportionality of existing limits, then choose one of three routes;
do not begin an indefinite harness repair. Work stayed in research evidence and
its documentation. Read-only inspection of the linked native source was used to
identify the dependency boundary; it was not modified.

Receipts: [probe.json](probe.json), [probe.log](probe.log),
[investigate.py](investigate.py), [typecheck.log](typecheck.log),
[verification.json](verification.json), [SHA256SUMS.txt](SHA256SUMS.txt).
The first probe and its exact source are preserved as `probe-initial.json`,
`probe-initial.log`, `investigate-initial.py.txt`: a type annotation/formatting
correction led to one final probe, not an operation retry. Both have six distinct
small model/filesystem checks; this is **six checks, not twelve distinct tests**.
Payload created per probe was 65,537 bytes, plus a sparse 1-MiB logical file;
temporary trees were removed. No capacity/stress/performance experiment occurred.
Host-before/after load, memory/swap, PSI, affinity and process inventory are in each
probe. Snapshots are context, never a fresh native admission decision.

Frozen inputs, including policy, ledger, approved historical plan and inspected
writer sources, have SHA256 identities in the probe. Historical receipts remain
historical; no resource maxima were inferred from the interrupted native arm.

## 1. Btrfs: useful kernel capability, no cheap applicable proof here

### Observations on this host

- Linux `7.2.10-arch1-1`, unprivileged UID 1000; no `btrfs` CLI in PATH.
- `findmnt` reports `/var/tmp` and project scratch on the same root Btrfs mount,
  `/dev/mapper/cryptroot`, `subvolid=5`.
- Read-only `BTRFS_IOC_GET_SUBVOL_INFO` independently returns **treeid 5** for
  BOTH `/var/tmp` and `.work/tmp`. These are directories inside the shared
  top-level subvolume, not separate per-arm subvolumes. A limit on qgroup 0/5
  would have the wrong scope and could affect unrelated data.
- One bounded quota-tree status-item read (4096-byte buffer, one exact key,
  no sync/rescan) returns **EPERM**. No privilege escalation was attempted.
  `/sys/fs/btrfs/<fsid>/qgroups` is absent. Neither observation proves global
  quotas disabled or healthy; quota state, consistency and limits are unverified.
- Kernel sysfs exposes the `simple_quota` capability file (value `0`). This is
  a supported-feature interface, NOT proof squotas are enabled on this filesystem.
  Enabled per-filesystem feature listing does not expose a verified quota backend.
- Sysfs allocation counters are filesystem-global. Data and metadata counters
  are distinct; the snapshot reports metadata `bytes_used=4287397888`,
  `total_bytes=5368709120`, but `disk_used=8574795776` and
  `disk_total=10737418240`. This demonstrates why logical accounting, device
  occupancy and metadata replication must not be silently equated. These are
  concurrent observations, not free-space guarantees or new thresholds.

### Mechanisms and costs

| Mechanism | What it could establish | Why it is not the current cheap solution |
|---|---|---|
| Existing consistent qgroup on an exclusive arm subvolume | Kernel-enforced referenced/exclusive extent quota, independent of filename publication races | No such scope/limit verified; present workspace is in shared subvol5. Cannot impose its cap on the host root. |
| Enable full qgroups and create new per-arm subvolumes | Potential allocated-extent enforcement under a different, explicit quota metric | Filesystem-wide accounting/possible rescan and global extent-processing overhead; root-admin and scope changes. Cost not measured here and cannot be called low. |
| Simple quotas/squotas on new isolated subvolumes | Cheaper allocation-owner accounting, avoiding full back-reference accounting | Still filesystem-level setup; first-allocator attribution differs from current gross `st_blocks` and apparent bytes; sharing/lifetime rules need a distinct contract. Not a directory quota. |
| Global sysfs, statvfs, qgroup query only | Context/observation | Reading counters is not enforcing a scoped limit; query freshness and delayed allocation/reservations matter. No atomic workspace/lifetime bound. |

A configured qgroup can be queried at low userspace cost; **cheap querying is not
cheap activation or correct scope**. Full qgroup enablement changes global extent
processing. Upstream warns about performance and snapshot/subvolume interaction,
and inconsistent qgroups cannot be relied on for limits until repaired. No quota
enable/disable, rescan, syncfs, new subvolume, limit change, package install, loop
mount or privileged helper was executed.

Most importantly, qgroup referenced/exclusive extent bytes are **not** the current
sum of inode `st_size`, nor necessarily the sampled gross `st_blocks * 512` metric.
Sparse logical size, hardlinks/reflinks, compression, metadata and deleted-but-open
objects differ. Even a correctly configured quota would need a documented metric
mapping or contract amendment; it does not prove BOTH limits by itself.

Sources consulted (2026-10-21):
- [btrfs quota](https://btrfs.readthedocs.io/en/latest/btrfs-quota.html):
  "When qgroup mode is activated, it affects all extent processing"; squotas
  "account all extents to the subvolume that first allocated it".
- [btrfs qgroup](https://btrfs.readthedocs.io/en/latest/btrfs-qgroup.html):
  "the leaf qgroups are attached to subvolumes"; "Qgroup limit only works when
  qgroup is in a consistent state"; `show --sync` forces filesystem sync.
- [Quota groups](https://btrfs.readthedocs.io/en/latest/Qgroups.html): sharing,
  referenced/exclusive accounting and squota ownership/lifetime examples.
- Local ABI: `/usr/include/linux/btrfs.h`, `/usr/include/linux/btrfs_tree.h`.
  Probe asserts the x86_64 structure sizes and uses only read-only ioctls.

## 2. Writer-side logical/apparent enforcement: feasible subset, not whole arm

### A minimal *participating writer* invariant is implementable

For a fresh, exclusively owned tree with no external mutation, share one checked,
thread-safe reservation ledger across ALL allocating/growing operations:

```
initial conservative admitted bytes + sum(all pre-admitted growth/copy charges) <= L
```

Charge **before** the operation, reject `n > L - used` without unsigned overflow,
retain the reservation after partial writes/failure, and do not reclaim credits
for unlink/truncate/retirement in the simplest implementation. Charge copied
content and temporary replacement files while old files remain; charge a sparse
extension's `new_size - old_size` even if no payload is written. For a conservative
simple scheme, charge whole write requests even on overwrite, and count hardlink
creation as another full size (overcounts the present inode-deduplicated metric).
Never count rename as a second new payload. A separate finite file/inode budget
is needed; payload bytes alone cannot bound metadata or tiny-file overhead.

This yields a conservative **cumulative admitted logical budget**, not exact live
workspace occupancy. It may refuse a valid workload early, especially after many
updates or clones. Atomic pre-write denial must propagate through existing error
and cleanup paths, without changing publication/fsync ordering or reporting a
partially published generation as successful. A process crash invalidates any
unpersisted ledger; simplest scope is fresh one-shot run, never a resumed root.

The small model checked exact boundary/overflow-style denial and no-refund failure
handling. Filesystem counterexamples were concrete: a 1-MiB `truncate` had **zero
allocated bytes**; one-byte file had **4096 allocated bytes** after syncing only
that file; hardlinks shared an inode; open-unlinked content remained accessible by
FD after namespace removal. These demonstrate missing cases, NOT native enforcement.

### Coverage audit

| Inspected path | Operations a complete logical ledger must cover |
|---|---|
| `src/index.cc:658–826` | WAL append/partial rollback/truncate, atomic tmp write/rename, manifest `.bak` copies |
| `src/index.cc:2787,2853,2878,3236` | Tombstone snapshots, stats append/checkpoint and publication; all errors/cleanup |
| `src/format.cc:66–108` | Segment write helper and atomic temporary segment |
| `src/stream_merge.cc:99–194` | Buffered streaming writes, final header pwrite, cleanup/rename; reserve before buffer admission or physical write with complete accounting |
| `tests/m4_bridge_integration.cc:425,757,769,1459` | Durable snapshot/state writes, generation WAL, hardlinked immutable runs and copied mutable state |
| `src/index.cc:283–311` | Analyzer dictionary snapshot in temporary directory; its root must be included or separately reserved, not silently omitted |
| `tests/m4_bridge_integration.cc:1540–1580` | Native vector Open/upsert/flush: **outside the M4 write helpers**, not covered by an M4-only counter |

The last row is decisive for whole-arm coverage. Native source
`.local/native/zvec-native-profile/src/core/framework/index_mapping.cc:198–233,357–361`
contains shared file mappings and mapped metadata stores. This is source-level
capability evidence, not a trace proving which specific mmap ran in the aborted
arm. It is sufficient to reject a claim that editing M4's `write` helpers covers
all possible storage growth in the native dependency. Native files can also be
preallocated or grown via library abstractions that an M4 byte counter never sees.

The 500x20 generator is finite: 500 live rows, 21 generation builds, initial 500
upserts plus 20 batches of five updates, one delete and one insertion (620 upsert
inputs, 140 total mutation inputs). This bounds application inputs; it does NOT
bound native file preallocation, metadata amplification, compaction coexistence,
or native failure behavior. No audited native storage-size function was found
at this boundary, and proving one across the dependency is not a minimal patch.

**Answer:** yes for M4-owned logical serialization with a focused shared ledger;
**no demonstrated small whole-arm apparent enforcement** for the current linked
writer. `RLIMIT_FSIZE` is only per-file, not an aggregate directory cap; task/memory/
time bounds do not supply the missing disk bound. LD_PRELOAD syscall wrapping,
a mutable-tree watcher, or polling after each stage is not a substitute for
pre-write accounting of every path (mmap/truncate/copies/open-unlinked included).
No implementation is proposed as meeting the current contract.

## 3. Are the two hard limits proportionate?

Historical plan: allocated **536870912 B (512 MiB)**, apparent **1073741824 B
(1 GiB)**, shared-disk reserve **2147483648 B (2 GiB)**, memory 1 GiB/swap 0,
CPU 2 cores, tasks 128, watchdog 115 s, monitor maximum gap 2 s. These were
owner-approved diagnostic risk budgets, NOT validated physical safety constants.
Even before repair, workspace enforcement was sampled-abort-only, not a kernel
hard quota. Requiring a conservative proof now exposes that enforcement gap;
it does not mean the old sampled checks ever guaranteed a hard ceiling.

Four historical 500x20 observations in `retained-resource-evidence.json`:
- retained final logical root: **7,177,536 B = 6.845 MiB**;
- reported process `write_bytes`: **226,091,008–251,011,072 B = 215.617–239.383 MiB**;
- observed whole wall times: **4.963–6.383 s**.

The apparent cap is about **149.6x** final retained logical size. The allocated
cap is **not** comparable to cumulative process writes as a proof: occupancy,
write traffic and physical device writes are three different metrics. Final size
is not live peak, and these older runs are not current full-sync A/A evidence.
The interrupted arm's 3,153,920 allocated / 6,197,220 apparent partial scan cannot
fill the gap. No host-wide peak or per-arm causal disk attribution is asserted.

**Judgment:** two independently exact byte ceilings, including application apparent
size, are not shown necessary by this tiny finite fixture's observed footprint.
Installing filesystem-global quota infrastructure solely to save this measurement
is disproportionate scope/cost. But the shared root filesystem and metadata ENOSPC
risk are real; it is also not justified to replace BOTH safety requirements with
"final tree is small". Apparent size remains useful against accidental sparse
extension/unbounded output; physical allocation needs a different mechanism.

A 2-second maximum observation gap and 115-second lifetime are NOT a storage
overshoot bound: without a verified maximum growth rate, neither `reserve >= rate
* gap` nor "512 MiB will be enough" can be established. Likewise host free bytes
are not an exclusive reservation against concurrent unrelated writers. Current
snapshots never authorize a run later. An acceptable *best-effort* resource plan
must acknowledge these limits, not label them hard enforcement.

## 4. Three-way disposition and finite stop condition

| Route | Disposition |
|---|---|
| 1. Minimal implementable enforcement meeting current contract | **Rejected for this whole arm.** Cheap ledger is partial; no allocated proof and native apparent coverage missing. Do not start implementation under this investigation. |
| 2. Change resource-plan contract on evidence | **Possible only as an explicit new owner risk decision, not adopted.** Evidence supports small expected footprint, not guaranteed bounded overshoot. A proposal must distinguish per-arm sampled abort thresholds from hard containment and specify accepted failure consequences on the shared root filesystem. |
| 3. Retain no-launch | **CHOSEN now.** Current proof cannot be provided by a small, correctly scoped, low-cost change. Investigation closed; do not continue speculative accounting repairs. |

If the owner later chooses route 2, the contract must name at least:
1. Exact whole-writer scope, including native storage, temporary analyzer data and
   retained first arm; immutable binary/input/env identity, 500x20, no tracing.
2. Which guarantees are relinquished (strict aggregate allocated/apparent ceiling,
   deterministic overshoot bound), which are retained (cgroup/time/task/swap,
   owned-only cleanup, correctness/durability, existing disk reserve unless
   explicitly changed), and shared-disk I/O/ENOSPC residual risk acceptance.
3. Separate bounded writer-input admission checks from sampled occupancy/context.
   If mutation races become permitted *partial observations*, that is a deliberate
   contract revision: retain raw race/incomplete labels, never declare complete
   telemetry. Permission/path-replacement/unknown I/O errors must remain fatal.
   Missing independent required controller/process/cgroup/capacity telemetry,
   monitor gaps, cleanup failure, timeouts and OOM remain fatal, without retry.
4. Finite execution budget with explicit fresh authority; no reuse of exhausted
   campaign4/4. A resource risk change alone grants no execution authority.

If instead strict whole-arm hard containment is non-negotiable, authorize a
separate isolated-storage design with its own cost/metric/durability implications,
not quota changes on subvol5. That is a new task, not remaining work in this one.

## Acceptance reconciliation

1. Btrfs feasibility and low-cost claim: **answered** with live read-only scope/
   capability probes and upstream semantics; no verified applicable quota.
2. Writer logical/apparent cap: **answered** with reservation invariant, write-path
   inventory, six tiny checks and explicit native-backend coverage gap.
3. Proportionality: **answered**, separates administrative ceilings, tiny expected
   footprint and nonzero shared-filesystem failure risk; no empirical-to-hard-bound leap.
4. Three-way decision: **answered — no-launch**, no further repair loop authorized.

Verification: six small checks pass in each retained probe; exact script/source
hashes and scratch removal checked; direct `ty check` and active LSP check on final
probe script; documentation links and `git diff --check` verified in
`verification.json`. Initial diagnostic is preserved, not called clean. No native
build/oracle/sanitizer/benchmark was needed because runtime sources were unchanged.

Mission distinction: **feasibility COMPLETE; native measurement NOT complete,
NOT ready, still OPERATION_BLOCKED.** Production and unrelated working-tree changes
are untouched. Native authority and accounting safety remain independent issues.
