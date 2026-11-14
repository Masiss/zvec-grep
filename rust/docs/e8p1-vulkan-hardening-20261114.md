# E8-P1 — Vulkan Integration Hardening & Promotion Readiness

Date: 2026-11-14  
Base: E8 `b565472` (`work/e8-embedding-acceleration-20261114`)  
Branch: `work/e8p1-vulkan-hardening-20261114`  
Scope: integration hardening only. CPU remains the default; no merge, release,
production index/config/binary/daemon/schema/ranking change was made.

## 1. Verdict

| Hypothesis | Verdict | Meaning |
|---|---|---|
| H1 Vulkan explicit opt-in | **QUALIFIED_GO (review candidate)** | Safe explicit device binding, fail-closed accelerator, guarded incremental writes, and matching hybrid quality. Not promoted. |
| H2 CPU affinity | **NO_GO** | Unchanged from E8. |
| Automatic Vulkan selection | **NO_GO/blocked** | `Auto` cannot create or relabel a non-CUDA index; a refresh may only resolve to an already-saved concrete device. |
| Production default change | **NOT REQUESTED/NOT DONE** | CPU remains default. Merge/promotion is an owner decision. |

`OWNER_DECISION_REQUIRED`: whether to merge the branch, and whether to fund a
full 392-file P1 A/B or CUDA-Auto hardening before promotion. No branch was
merged.

## 2. P0 finding — index execution identity was not fully represented

### 2.1 E8 measurement path used backend defaults, not an explicit query device

The E8 harness selected the model device at index time from
`ZVEC_GREP_E8_DEVICE`, but its query request omitted `ContextOptions.device`.
Consequently a CPU-query-on-Vulkan-index run could execute through the
default/fallback path rather than an explicit CPU request. The index device
guard existed, but the benchmark could not declare the query device it measured.

P1 fixes this at the harness boundary: `query` now sets
`device: Some(e8_device())`, so the declared query device is the requested
device. This is a measurement-contract fix, not an engine ranking change.

### 2.2 Explicit accelerators could silently fall back to CPU

`llama.cpp` load and embed paths had CPU fallback for GPU failures. With an
explicit Vulkan/CUDA/Metal request, a failed GPU initialization could therefore
produce CPU vectors under an accelerator identity. P1 makes explicit
accelerators **fail-closed**:

- GPU model load failure -> typed error, no CPU fallback.
- unavailable explicit GPU backend -> typed error, no CPU fallback.
- mid-run embed failure on an explicit GPU model -> no CPU fallback.
- `Auto` retains legacy fallback for CUDA builds only; it is not used for new
  non-CUDA index writes.

Receipt: `e8p1-20261114/receipts/unavailable-final.log`.

### 2.3 Relocation erased device identity

Workspace relocation cleared both `device` and `cacheDir`. Clearing `cacheDir`
is correct (machine-local path), but clearing `device` allowed a moved CPU or
GPU index to be incremented by a different backend. The existing index guard
then saw `None` and could not reject the write.

P1 preserves `device` across relocation, still clears `cacheDir`, and keeps the
same guard. A relocated index now rejects a device-changing incremental write
and requires `--rebuild=true` for migration. Manifest version is unchanged (no
schema migration).

Evidence:

- `reloc-cross-cs40-cpu-vk.log`: rejected.
- `reloc-cross-cs40-vk-cpu.log`: rejected.
- `guard-live-cross.log`: same-root CPU -> Vulkan rejected.
- `rebuild-vk.log`: explicit rebuild succeeds.
- `manifest.json` after rebuild records `device: vulkan`.

### 2.4 Auto could relabel an index

A first build with `Auto`, or an `Auto` update of an index with a concrete saved
device, could save the wrong identity. P1 now:

- rejects `Auto` for a first build/relabel;
- for `Auto` refresh of a non-CUDA index, resolves to the saved concrete device;
- persists the device that actually acquired the runtime, not the raw `Auto`
  request;
- leaves a legacy saved-`auto` refresh behavior for compatibility, but does not
  use it to relabel to another concrete device.

Evidence: `auto-new-final.log` (rejected), `auto-refresh-final.json` (saved
`cpu`), and the updated model-preferences test.

## 3. Supported and unsupported workflows

| Workflow | Result |
|---|---|
| Explicit CPU index + CPU query | PASS |
| Explicit Vulkan index + Vulkan query | PASS |
| CPU index + Vulkan query (query only) | PASS |
| Vulkan index + CPU query (query only) | PASS |
| Same-device incremental index | PASS; embedding reuse preserved |
| Cross-device incremental index | REJECTED, fail-closed |
| Explicit `--rebuild=true` migration | PASS |
| No-op index | PASS, 0 files re-embedded |
| Published/reopened index query | PASS; manifest hash unchanged by query |
| Copied/relocated index cross-device update | REJECTED (device preserved) |
| Explicit Vulkan unavailable | REJECTED with “refusing CPU fallback” |
| First build with `Auto` | REJECTED |
| Argument/CLI failures | No partial publication |

The device guard is **not** a hardware UUID check. It binds the declared
execution backend to the index; an explicitly requested backend is treated as
the actual backend because unavailable initialization now fails closed. Device
selection is recorded in the index descriptor and receipts.

## 4. Performance (P1 controlled A/B)

Both arms used the same 40-file C# corpus, same model/artifact, same harness
build profile, and explicit device; runs were serialized. Peak RSS is
`/usr/bin/time` maximum RSS.

| Workload | CPU | Vulkan | Ratio |
|---|---:|---:|---:|
| 40 C# cold index wall | 241.38 s | 59.92 s | **4.03x** |
| 40 C# cold user CPU | 926.62 s | 16.29 s | **56.9x less** |
| 40 C# peak RSS | 2,080,176 KB | 700,376 KB | **2.97x less** |
| 1,000-log-line cold wall | 141.05 s | 36.63 s | **3.85x** |
| 1,000-log-line user CPU | 545.52 s | 9.26 s | **58.9x less** |
| 1,000-log-line peak RSS | 2,229,472 KB | 696,340 KB | **3.20x less** |

These are cold-index measurements with one serialized run per arm, not repeated
variance estimates. E8's full-392-file concurrent runs were **not** an A/B and
are not presented as a speed ratio: 89:32 CPU wall / 20,908 s user vs 54:43
Vulkan wall / 752 s user; they support the CPU-time and memory direction only.

## 5. Quality

### P1 matched 40-file subset

Using the same matched layout, vector and hybrid metrics were identical across
all four device combinations (CPU/Vulkan index x CPU/Vulkan query) for R@10,
R@50, MRR, nDCG, solved@10 and no-answer FP. The subset is a device-mechanism
test, not the frozen E6 quality estimate.

### E8 full E6 (392 files, frozen v2 gold)

Reused E8 full receipts; they are the full-quality evidence for this branch.
Hybrid was exactly identical between CPU and Vulkan. Pure-vector showed a
device/index interaction:

| Arm | R@10 | R@50 | nDCG@10 | Solved@10 |
|---|---:|---:|---:|---:|
| CPU index, CPU query | 0.5679 | 0.8673 | 0.5244 | 0.3704 |
| Vulkan index, Vulkan query | 0.6049 | 0.8302 | 0.5481 | 0.4074 |
| Vulkan index, CPU query | 0.6049 | 0.8673 | 0.5481 | 0.4074 |
| CPU index, Vulkan query | 0.5679 | 0.8673 | 0.5248 | 0.3704 |

Hybrid (recommended): both CPU and Vulkan reported R@10 0.6142, R@50 0.8025,
nDCG 0.5387, solved@10 0.4444, no-answer FP@10 0.0. The CPU/Vulkan hybrid
receipts are byte-identical at the metric level.

Caveat: Vulkan **query** on the Vulkan index loses one anchor at K=50 in the
pure-vector route (R@50 0.8302 vs 0.8673 with CPU query on the same Vulkan
index). Hybrid does not show the loss. This is why the candidate is explicit
opt-in, not a default promotion.

## 6. Verification

- Engine default: **764 passed, 0 failed, 28 ignored** (serialized).
- Engine Vulkan: **763 passed, 0 failed, 28 ignored** (serialized).
- Harness: **4 passed, 0 failed**.
- Added fail-closed accelerator test and preserved/rewritten relocation and
  Auto preference tests.
- Independent E8 verifier on raw vectors: 11 inputs, cosine min **0.999404**,
  cosine mean **0.999541**, max abs **1.4589**; 11 x 768 coordinates.
  Negative NaN receipt rejected as expected.
- Query-only manifest hashes were unchanged.
- Receipts include logs for guard rejection, relocation rejection, explicit
  rebuild, unavailable backend, Auto rejection/refresh, no-op and published
  query.

## 7. Limits / owner decisions

1. No production promotion, merge, default change or deployment was performed.
2. CUDA builds retain the legacy `Auto` fallback path; P1 hardened Vulkan and
   explicit accelerators generally, but did not add CUDA device-identity tests.
3. A legacy index whose saved setting is literally `auto` is still allowed to
   refresh without relabeling. New explicit `Auto` builds/relabels are rejected.
4. The P1 40-file subset is not the frozen E6 estimate; full decision quality
   relies on E8's frozen E6 receipts, not on a new full P1 index.
5. No full-392 P1 A/B was run; that is a cost/owner decision if a new
   end-to-end performance point is required.
6. Device identity is the declared execution backend, not a physical GPU UUID.
7. The branch is review-ready but remains unmerged.

## 8. Artifacts

- Receipts: `/home/masis/.cache/zvec-grep/e8p1-20261114/receipts/` (logs,
  summaries, selected manifest snapshots; `SHA256SUMS`).
- E8 full-quality source receipts:
  `/home/masis/.cache/zvec-grep/e8-20261114/receipts/` (historical, preserved).
- Report: this file.
- Temporary workloads, target directories and dependency prefixes were removed
  after the receipts were checksummed. The receipts, not the deleted working
  trees, are the durable evidence.
