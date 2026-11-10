# E1 — bounded deferred maintenance experiment (2026-11-10)

Status: **experiment branch only**. Nothing merged, pushed or released; the
foundation branch is untouched, main stays `9444eed`.

| Item | Value |
|---|---|
| Worktree | `/home/masis/src/worktrees/zvec-grep/e1-bounded-20261110` |
| Branch | `work/e1-bounded-maintenance-20261110` |
| Base | `d64e54e` (foundation-opt HEAD) |
| HEAD | `47cb38b` |
| Commits | `cc567c4` debt record + policies · `f152526` harness query repeats · `0a09b71` restore debt across sessions · `90de9d6` crash/concurrency tests · `47cb38b` clippy cleanup |

Tooling: external `CARGO_TARGET_DIR`, temp and receipts under
`~/.cache/zvec-grep/out-e1`; probes and data outside the repository; no
protected index, daemon, receipt, RAG/Selection/Expansion or held-out data
touched.

---

## 1. Verified root cause

A one-file edit (dense corpus, 1.7 KB C# file, 43 entities/fragments) costs:

| Stage | Time |
|---|---:|
| prepare | 1.3 ms |
| embedding (43 inputs) | 3.5 ms |
| commit (delete + reinsert 43 rows) | 13.7 ms |
| **optimize** | **1 187.9 ms** |
| — fragments | 864.6 ms |
| — files | 320.6 ms (concurrent with entities 234.1, directories 0.2) |

plus **207 104 process filesystem-output blocks ≈ 106 MB** (not physical SSD
writes). Optimize rewrites whole collections; its cost tracks collection size,
not the delta. `DIRTY_ALL` is accurate here (file row, entities and fragments all
change), so precise dirty masks are not the missing lever.

## 2. What differs from the architecture review

1. **The floor is much higher than the review's estimate.** The review estimated
   ~0.3 s/edit at K=4 by amortizing 1.19 s. Measured: a no-op pass over the same
   workspace is **0.79 s**, and a fully deferred edit is **0.88 s** (B16,
   no optimize at all). Process start, full-tree scan/classify, prepare, commit,
   open/close dominate. Therefore maintainance deferral can remove at most the
   ~0.64–0.7 s optimize component: **the wall ceiling is ≈ −42…−50%, not −75%**.
2. **Reopening must not force a catch-up.** The first prototype treated any
   debt marker as "retry now"; because every edit is a separate process, that
   turned every bounded arm into "optimize on every other edit" regardless of K.
   The record now distinguishes *clean deferral* (`retry=false`) from *in-flight
   or failed maintenance* (`retry=true`); only the latter (and legacy/unknown
   markers) forces a catch-up.
3. **Segment growth is real but modest for this corpus.** Probe with 485–970
   document batches showed 5 MB/segment; the engine's 43-document batches grow
   ~1.24 MB per deferred edit (55.6 → 65.5 MB over 8 edits). Extrapolating the
   probe would have overestimated the disk penalty by ~4×.

## 3. Prototype mechanism

- `maintenance.rs`: `MaintenancePolicy` — `immediate` (default), `scalar-deferred:K`,
  `bounded:K`, selected by `ZVEC_GREP_MAINTENANCE_POLICY`; `DebtRecord`
  `{version, dirty, pending, retry}` persisted in `optimize.pending`.
- Planning (`plan_maintenance`) decides per pass: immediate always runs dirty
  collections; deferred runs everything once `pending_writes ≥ K` or a force is
  set. Deferral writes the debt record and publishes as usual.
- Settlement (`settle_debt_after_pass`) clears only what the pass optimized; a
  write during native work keeps its full debt (same conservative rule as
  before). The record is written before native work (`retry=true`) and rewritten
  after (`retry=false`), so a crash resumes maintenance.
- Legacy zero-byte markers read as "all dirty, retry now" (fail-safe).

Full builds always run under `immediate`, because the build itself is the
maintenance opportunity; only incremental passes defer.

## 4. Measurements

Corpus: dense 300 C# files (12 600 entities/fragments), mock 1024-d embeddings
(separate process, CPU excluded), 8 chained trailing edits per arm, fresh index
per arm. Host was shared and noisy (load average 3–8).

### 4.1 Per-edit means (8 edits)

Run 1 (query repeats 50):

| Arm | wall s | user s | FS blocks | deferred peak MB | growth× | catch-up s | catch-up blocks |
|---|---:|---:|---:|---:|---:|---:|---:|
| control | 1.61 | 0.97 | 208 355 | 56.51 | 1.000 | 0.67 | 7 190 |
| A4 scalar-deferred:4 | 1.66 | 1.02 | 171 653 | 56.85 | 1.000 | 0.69 | 5 208 |
| B2 bounded:2 | 1.31 | 0.76 | 122 270 | 57.76 | 1.022 | 0.64 | 5 632 |
| B4 bounded:4 | 0.97 | 0.57 | 78 248 | 60.56 | 1.072 | 0.58 | 7 056 |
| B8 bounded:8 | 0.81 | 0.46 | 56 137 | 64.12 | 1.134 | 0.53 | 7 448 |

Run 2 (query repeats 150, second deferred query sample; B8/B16 re-measured):

| Arm | wall s | user s | FS blocks | deferred peak MB | growth× | catch-up s | catch-up blocks |
|---|---:|---:|---:|---:|---:|---:|---:|
| control | 1.58 | 0.94 | 208 038 | 56.51 | 1.000 | 0.64 | 6 160 |
| B8 bounded:8 | 0.91 | 0.52 | 56 989 | 64.12 | 1.134 | 0.63 | 5 184 |
| B16 bounded:16 | 0.88 | 0.48 | 32 792 | 65.47 | 1.159 | 2.29 | 186 448 |

Per-edit sequences (run 1) show the amortization clearly:

```
control  wall 1.6 1.66 1.61 1.58 1.64 1.57 1.60 1.61   fs 203-214k each edit
B4       wall 0.72 0.77 0.72 1.67 0.62 0.75 0.74 1.81   fs 21 36 35 222 21 34 34 223 k
B8       wall 0.69 0.72 0.68 0.68 0.71 0.70 0.72 1.59   fs 22 35 35 38 36 35 30 218 k
```

### 4.2 Memory

Median per-edit PSS / RSS: control 342 / 354 MiB; A4 336 / 347; B2 292 / 303;
B4 238 / 249; B8 239 / 250; B16 239 / 250. Optimize's transient native buffers
are what deferral removes; the steady-state index footprint is unchanged.

### 4.3 Query latency (deferred vs optimized, warm, µs)

Run 2, 150 repeats, two independent deferred samples to bound noise:

| Arm | mode | p50 deferred-1 / deferred-2 / optimized | p95 deferred-1 / deferred-2 / optimized |
|---|---|---|---|
| control | fts | 130 356 / 166 400 / 176 630 | 147 992 / 207 157 / 211 042 |
| control | vector | 186 440 / 195 380 / 165 068 | 231 127 / 240 528 / 198 752 |
| control | hybrid | 184 792 / 179 976 / 197 691 | 227 873 / 266 172 / 239 175 |
| B8 | vector | 192 361 / 191 407 / 175 841 | 292 003 / 238 312 / 191 600 |
| B8 | hybrid | 212 732 / 188 298 / 206 454 | 279 910 / 224 541 / 242 216 |
| B16 | vector | 247 980 / 250 506 / 176 174 | 290 358 / 344 022 / 226 951 |
| B16 | hybrid | 258 178 / 237 372 / 200 713 | 311 388 / 261 570 / 228 564 |

The control arm measures the *same* optimized state twice against itself and
still moves by ±20% (vector p50 1.13×/1.18×, p95 1.16×/1.21×), which is this
host's noise floor. Against that floor:

- B8 vector p50 1.09× in both samples (stable), p95 1.52× and 1.24× (straddles
  the 1.25 gate) → p50 PASS, p95 **INCONCLUSIVE**.
- B16 vector p50 1.41×/1.42× consistently → **FAIL**.
- A4, B2, B4 p50 within 1.15×; B4 hybrid p95 1.38× in a single sample →
  INCONCLUSIVE at p95.

### 4.4 Correctness while deferred

FTS paths identical between deferred and optimized states for every arm; vector
and hybrid top-10 sets identical for every arm except B16 (8 deferred segments),
where approximate-search results differ — consistent with ANN variance under a
different graph, not with stale rows (row counts and FTS results are unchanged).

## 5. Debt / recovery / crash contract

Implemented and tested:

- **Durable debt**: `optimize.pending` holds `{version, dirty, pending, retry}`;
  empty/unknown/foreign payload → all-dirty fail-safe catch-up.
- **Cross-session bound**: reopening restores the mask and counter, so K keeps
  applying (test `deferred_debt_survives_reopen_without_forcing_maintenance`).
- **Failed maintenance**: an injected native optimize failure leaves the debt
  and `retry=true`; the next session retries immediately
  (`deferred_catch_up_failure_keeps_debt_and_retries_after_reopen`).
- **Concurrent write during catch-up**: the pass keeps the new write's debt for
  every table and the next bounded pass settles it
  (`write_during_deferred_optimize_keeps_the_new_debt`).
- **Legacy marker**: a zero-byte marker forces exactly one catch-up
  (`legacy_empty_marker_forces_one_full_catch_up_under_deferral`).
- **Deferred state serves reads**: FTS and vector queries return the written
  rows while fragments are unoptimized.

SIGKILL fault injection through the real binary (dense corpus, `bounded:1` so the
kill lands around maintenance):

| Kill delay | Marker after kill | Query after restart | Next pass |
|---|---|---|---|
| 0.15 s | none (before any write) | 10/10/10 items | catch-up no-op, marker clear |
| 0.35 s | none | 10/10/10 items | catch-up no-op, marker clear |
| 0.60 s | `dirty=15 pending=1 retry=true` | 10/10/10 items | catch-up ok, marker clear |

Rollback contract: revert the commits; a leftover `optimize.pending` written by
this prototype is parsed as JSON by the new code only — the previous code treats
any marker as "all dirty" and would simply run one full maintenance pass, which
is safe. No schema or index-version change was made, so no rebuild is required.

## 6. Gates and verdicts

Gate thresholds are the experiment's own (not historical thresholds): wall and
FS output **≥ 50% lower**, deferred p50/p95 **≤ 1.25×**, growth **≤ 1.15×** at
the allowed edits, catch-up **≤ 1.02×**, recovery/correctness PASS.

| Arm | wall ≥50% | FS ≥50% | latency ≤1.25× | growth ≤1.15× | catch-up ≤1.02× | recovery | Verdict |
|---|---|---|---|---|---|---|---|
| control | reference | reference | — | 1.000 | 1.000 | PASS | reference |
| A4 scalar-deferred | −3% ✗ | −18% ✗ | PASS | 1.000 ✓ | 1.000 ✓ | PASS | **FAIL** |
| B2 bounded:2 | −17% ✗ | −41% ✗ | PASS | 1.022 ✓ | 1.000 ✓ | PASS | **FAIL** |
| B4 bounded:4 | −38% ✗ | −62% ✓ | p50 PASS, p95 INCONCLUSIVE | 1.072 ✓ | 1.000 ✓ | PASS | **FAIL** (wall) |
| B8 bounded:8 | −42…−50% ✗/borderline | −73% ✓ | p50 PASS, p95 INCONCLUSIVE | 1.134 ✓ (thin) | 1.000 ✓ | PASS | **INCONCLUSIVE** |
| B16 bounded:16 | −44% ✗ | −84% ✓ | **FAIL** (p50 1.41×) | **1.159 ✗** | 1.000 ✓ | PASS | **FAIL** |

**Overall: NO-GO for the specified combined gate.** No arm reduces wall time by
≥ 50%; the achievable ceiling on this corpus is ≈ −42…−50%, because ~0.85–0.9 s
of every edit is work deferral cannot remove. The write-amplification half of the
goal is met from K = 4 onward (−62% to −84% process FS blocks).

## 7. Measurement limits and unresolved risks

- One corpus (dense 300), mock embeddings. With a real model the embedding stage
  grows and the relative wall share of maintenance shrinks further, so the wall
  gate would be harder, not easier. Embedding wall share was ~3.5 ms/edit here.
- Host noise: control-vs-control p95 spread ±20%; p95 gate results are
  INCONCLUSIVE, not established. A quiet host and more repetitions are required
  to settle them.
- Process filesystem blocks are not physical writes; no device-level counters
  were read.
- Deferred peak disk is measured per edit (the peak of the chain), not sampled
  during native work; immediate-mode peak during a full build was 84.1 MB vs
  54.4 MB final (1.55×) in the earlier review.
- Segment count is not exposed by the backend (`stats` only reports index
  `completeness`), so the bound is expressed in writes, not segments.
- No age/time trigger exists (needs a scheduler); a workspace that stops writing
  keeps its debt until the next write or an explicit maintenance pass.
- Recovery was exercised by SIGKILL at three points and by injected native
  failures, not by exhaustive crash-point enumeration inside native compaction.

## 8. Minimal E1 worth implementing

**Adopt bounded deferral for write amplification, not as a wall-time fix.**

- Keep `bounded:K` with **K = 4** (or 8 if the 1.13× peak is acceptable), applied
  to *incremental* passes only; full builds stay immediate. Measures: −62% FS
  output and −38% wall at K = 4 (K = 8: −73% FS, −42…−50% wall), growth 1.07×
  (1.13× at K = 8), p50 within noise, recovery PASS.
- Ship the debt record with a `retry` flag and the fail-safe legacy-marker path;
  add an explicit maintenance entry point (e.g. `zg --index --optimize`) so a
  workspace with persistent debt can be settled without an extra write.
- Do **not** ship `scalar-deferred` alone (no wall gain, −18% FS).
- Do not raise K beyond 8 on this corpus: growth crosses the 1.15× gate and
  vector p50 leaves the 1.25× band at K = 16.

If a ≥ 50% wall reduction is required, the remaining lever is the fixed per-edit
cost (process start plus full-tree scan/classify plus open/close ≈ 0.79 s of
1.58 s). That is a separate scoped-scan/incremental-discovery experiment, not
maintenance tuning; tuning K further cannot reach it.
