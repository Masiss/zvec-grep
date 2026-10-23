# M4 — Incremental native index (research prototype)

## Active research status — resumed exploratory mission

**IN PROGRESS — architecture remains OPEN.** The owner explicitly resumed the complete M4 exploratory mission, authorizing implementation, correctness work, and repeated bounded measurements without per-milestone approvals. This supersedes the historical pause in [M4 status and completion gaps](results/M4-STATUS-20261021.md), but does not waive resource, containment, correctness, durability, or cleanup gates. Immutable-shard remains a working baseline, not a proven winner; shared-collection is still unselected.

Memory-cause follow-up: source tracing found cppjieba's process-lifetime parsed-dictionary cache is keyed by path, while M4 previously created a new temporary dictionary snapshot path on each reopen. The prototype now reuses a content-addressed snapshot path under reference-counted leases. One fresh 1,024-document-per-arm four-arm verification stayed below the unchanged 512 MiB cap (observed cgroup `memory.peak` 189,665,280 B; no OOM/swap events), corroborating the diagnosis. It remains `COMPLETED_WITH_WORKSPACE_UNCERTAINTY` / measurement-incomplete, not a PASS or performance result; the prior 4,096-document OOM is still a consumed failure, not rerun. Native lifecycle/correctness and policy tests pass. The source audit also found 228 stale legacy snapshot directories under `/tmp` (1,275,691,008 B allocated); all 456 files were verified against the pinned dictionary and only those inactive generated copies were removed, restoring the same amount of `/tmp` capacity. The cleanup receipt is preserved under `results/lock-contention-microbench/20261022/ORPHANED-ANALYZER-SNAPSHOT-CLEANUP-20261022.json`. The contention runner now gives each contained operation a private `TMPDIR` on the same temp filesystem and removes only that operation's root after verified cgroup/unit cleanup or a receipt proving no unit launched; if cleanup is unverified, it preserves the root and marks the operation incomplete. A separate 2,048-document-per-arm post-fix trace then completed under unchanged caps at 194,039,808 B cgroup `memory.peak` (no OOM/swap); this is memory-scaling evidence only, not a performance result or proof of 4,096-document capacity. The lifecycle CTest now also covers four concurrent uncached opens/readers and last-lease snapshot cleanup. At that point, a residual remained: cppjieba's function-static path-keyed dictionary map retained every parsed content version; removing M4 snapshot files did not free those native entries. The follow-up isolated eviction prototype and evidence below now address that retention mechanism in a research-only build. A one-shot two-version churn probe (`m4-lock-contention-microbench-memory-churn-two-versions-20261022-170148-b72ef4`) completed under the unchanged caps: process PSS rose 57,212 KiB then another 43,624 KiB for distinct identities and did not fall after either analyzer release; cgroup peak was 334,364,672 B, with no OOM/swap and verified cleanup. This matches cppjieba's non-evicting path-keyed static dictionary cache. That uncapped-cache probe is now historical baseline evidence, not the current implementation state. Its limitations remain: one run, no long-run slope, and no allocator-exact byte attribution. [Detailed analysis](results/lock-contention-microbench/ANALYSIS-20261022.md) · [active checkpoint](results/ACTIVE-EXECUTION.md).

Latest cache-lifecycle follow-up (2026-10-23): added a reproducible research-only cppjieba overlay with one retained parsed generation, weak slots for active old versions, same-key initialization coalescing, and out-of-lock parse work. The lifecycle test verifies concurrent same-key sharing, concurrent distinct-key opens with non-aliased terms, same-version reopen without reparse, active old/new versions, last-lease release, and correct older-version reopen. It does not force overlap inside the native parse routine; source review verifies expensive parsing occurs outside the cache mutex. One bounded two-version operation completed under the same 512 MiB cap: process PSS increased 13,592 KiB on the first version and did not increase on the second; cgroup `memory.peak` was 337,891,328 B, slightly above the uncapped comparison, so no whole-workload peak reduction or causal win is claimed. PSS did not fall after release (allocator retention); cgroup I/O telemetry was unavailable; this is not latency/performance evidence. The newest cache/open trade-off and next steps are in [analysis](results/lock-contention-microbench/ANALYSIS-20261022.md) and the [active checkpoint](results/ACTIVE-EXECUTION.md). The prototype is research-only; no production integration or push is authorized by this work. This cross-version cache finding has not been shown to cause the earlier 4,096-document/512 MiB abort; that workload's dominant memory allocation remains unknown. After the memory trace, retained-path allocation was moved before cache publication so an allocation failure wakes same-key waiters; the hardened overlay and M4 lifecycle/correctness targets pass 3/3 CTests. The registered profile now pins this safe build, but no second memory operation was run. The measured pre-hardening diff is preserved in `patches/cppjieba-dict-cache-lifecycle-measured-20261022.patch`; the current exception-safe revision is `patches/cppjieba-dict-cache-lifecycle.patch`.

Memory-attribution follow-up (2026-10-23): cgroup-v2 `memory.stat` sampling localized most sampled usage to anon, but left a ~50 MiB unsampled peak. Source review found cppjieba `BuildDictCacheEntry` sorting a full `DictUnit` copy and deep-copying parsed units into the cache while both were live. An isolated research patch now selects min/max/upper-median from scalar weights and moves parsed units into the cache; exact weight/lifecycle tests pass. Its one same-cap 1,024-doc cache-churn trace passed with no failures, OOM, swap, throttle, or cleanup error. Compared with the earlier eviction run, `memory.peak` was 268.08 vs 324.32 MB (−17.3%), max sampled current 202.01 vs 271.63 MB (−25.6%), and max anon 189.05 vs 256.23 MB (−26.2%). Two init samples were also lower (193.9/189.8 vs 311.0/298.7 ms), but post-release PSS increment stayed +13,588 KiB; the high-water/sample gap widened to 66.06 MB, so an unsampled transient remains. This is one directional comparison with different arm order/host conditions, not causal proof, a stable latency/memory claim, or capacity evidence; the prior 4,096-document abort remains unattributed. Source review also found `DictTrie::CreateTrie` copied all keys into temporary `vector<Unicode>` and pointer arrays (~33.5 MB of array payload at 348,982 rows). A second one-run candidate now streams direct inserts into a local trie. It passed the same 1,024-doc/two-version workload and all correctness checks, with `memory.peak` 260.73 MB and only 3.67 MB between peak and max sampled current; but max sampled current/anon were 27–30% higher than the preceding run, arm order/host differed, and two init samples were slower. Treat the smaller unsampled gap as a hypothesis-consistent observation, not causal proof; peak reduction was only 2.7%, no I/O controller telemetry is available, and performance is ineligible. A bounded dictionary-topology count found 157,162/201,008 internal trie nodes unary, motivating inline-first-child storage. Its one-run test passed under the same cap: `memory.peak` 250.11 MB and max sampled current 227.30 MB, but the unsampled peak gap widened to 22.81 MB and per-analyzer PSS increments were larger than the previous trace. A later concurrent distinct-fingerprint open diagnostic saw PSS +131,884 KiB with both analyzers live, with +46,744 KiB remaining after both releases; this is consistent with retained native cache entries but PSS cannot assign exact ownership. The concurrent run's cgroup peak was 368.14 MB, with no OOM/swap, but a workspace inotify race made measurement quality incomplete. These are exploratory lifecycle/memory signals, not a causal saving or performance claim; cgroup I/O telemetry is unavailable. Full details and checksummed receipts: [analysis](results/lock-contention-microbench/ANALYSIS-20261022.md).

Timing-gap follow-up: one specifically authorized fixed synthetic containment rehearsal (`m4-contention-rehearsal-20261022T113503Z-clock-gap-probe-01`) passed with 11 samples, no watchdog abort, verified cleanup, and maximum monotonic/boottime gaps of 0.111149375/0.111148095 s. It did not reproduce the earlier 15.323 s gap and is not an M4 performance result; the cause remains unknown. The outer controller had a post-result stray-marker error, preserved and corrected without rerunning the contained operation. Full native CTest passed 8/8, TSan 2/2, and Python suites 48/11/4. Details: [analysis](results/lock-contention-microbench/ANALYSIS-20261022.md) · [verification](results/lock-contention-microbench/verification-20261022/REPORT.md).

Latest **completed** three-arm contention operation `m4-lock-contention-microbench-20261022-131543-f71669` completed `PASS`, with zero writer/reader/durability/coherence errors and verified cleanup. In that raw run, the WAL-preserving `snapshot_rebase` arm measured writer p95 16.359 ms, state-wait p95 15.578 ms, compaction state-lock hold 2.684 ms, and pending flush 0.0029 ms. A prior run measured 32.254/31.096/17.729/14.855 ms, respectively. Different binaries and host conditions make this directional evidence only. The result is not performance-eligible because workspace capacity accounting has no verified lifetime peak. Additionally, the policy semantics still described flush-on-publication when this candidate ran; raw binary identity is preserved, but classify the run as descriptive prototype evidence, not a policy-matched A/B. Policy text/tests now reflect the WAL-preserving behavior; the operation was not rerun. The separate `102855` watchdog abort remains an unreproduced 15.323-second sample gap with cause UNKNOWN; no retry or watchdog weakening. See [analysis](results/lock-contention-microbench/ANALYSIS-20261022.md), [latest completed operation](results/lock-contention-microbench/20261022/m4-lock-contention-microbench-20261022-131543-f71669/operation-plan.json), and [historical abort](results/lock-contention-microbench/20261022/m4-lock-contention-microbench-20261022-102855-992cef/operation-plan.json).

A writer-queue follow-up now instruments `ApplyBatch` and adds a four-arm comparison: the new arm uses four atomic eight-mutation batches for the same 32 logical writes, with matching inputs prepared before the start barrier. `ApplyBatch` input normalization was moved outside the state mutex; per-call diagnostics are opt-in. This is explicit client batching, not an internal group-commit queue, and its four batch-call samples are not equivalent to the 32 individual-Upsert samples. The one fresh operation `m4-lock-contention-microbench-20261022-135347-96ce27` was **PREFLIGHT_BLOCKED** before launch: `M4-CA-MEM-CAPACITY` observed 2,950,447,104 B against the unchanged 3,758,096,384 B requirement. It was not retried, and the new arm has no performance result. The empty temporary workspace was removed and the raw operation manifest verifies. Code verification passed native CTest 8/8, focused correctness 19,020/0, TSan 1/1, and Python suites 48/17/4. See [ApplyBatch analysis and evidence](results/lock-contention-microbench/ANALYSIS-20261022.md), [verification hashes](results/lock-contention-microbench/verification-20261022/applybatch-verification-SHA256SUMS.txt), and [blocked operation plan](results/lock-contention-microbench/20261022/m4-lock-contention-microbench-20261022-135347-96ce27/operation-plan.json).

The current harness adds explicit post-writer `Flush()` stage telemetry (timing schema 5), splits each instrumented writer's WAL append wall time into `write()` and `fsync()` durations (timing schema 6), and emits the tracked compaction scratch-memory peak (timing schema 7). Nested flush-stage schema v2 adds exclusive query-lock hold; legacy timing schemas 2–6 remain accepted. The compaction peak is internal `MemoryTracker` accounting, not process RSS/cgroup peak or total index memory. This is diagnostic-only and has not been performance-measured. Earlier operation `135347-96ce27` was no-launch blocked; fresh operation `145824-f0e7c1` hit a cgroup OOM at the unchanged 512 MiB cap and watchdog-aborted with incomplete telemetry. Neither operation may be retried or used as a performance result. The `wal_write_ms`/`wal_fsync_ms` sub-stages are bounded by total WAL append time in the verifier. A separately policy-bound `memory_probe` profile seeds 4,096 rather than 8,192 documents while retaining the same four arms and hard resource caps. Its sole operation `m4-lock-contention-microbench-memory-probe-20261022-152438-8f6561` passed host preflight but the watchdog stopped it after cgroup `memory.events.max` increased; no performance result is valid and this profile is not to be retried. The harness now emits stderr phase markers, added after that abort, for future phase attribution. See [policy semantics](docs/benchmark-policy.md) and the [analysis](results/lock-contention-microbench/ANALYSIS-20261022.md).

Latest fully completed matched pair `m4-shared-collection-pair-20261022-013401-a450e2` is exploratory, workspace-uncertain, and not performance-eligible. Shared readers had M4 query-lock wait p50/p95 4.255/7.888 ms while coordinator snapshot-lock wait p95 was 0.001 ms; details and limitations are in [the derived analysis](results/shared-collection-pair-20261022/analysis-shared-query-lock-20261022.md).

Earlier, to avoid stalling lock research on the 500×5 pair, a single-arm 64×10 diagnostic ran under the existing containment/watchdog with a 512 MiB memory cap and unchanged 3 GiB host reserve. It completed 10 cycles/40 concurrent-reader samples with zero coherence errors. Descriptive p50/p95: M4 query-lock wait 0.001/0.009 ms, reader total 0.849/1.867 ms, GC 12.145/19.356 ms, writer commit 37.405/43.807 ms, writer cycle 50.072/57.459 ms. Status remains `COMPLETED_WITH_WORKSPACE_UNCERTAINTY`, measurement `INCOMPLETE`, `performance_eligible=false`; no A/B or causal claim. The 4 GiB pair admission sum is its 1 GiB workload cap plus 3 GiB operation reserve, not a universal safety threshold. Full gate provenance, the safe 256 MiB watchdog stop, receipts, stage breakdown, and limitations: [microbenchmark analysis](results/lock-contention-microbench/ANALYSIS-20261022.md).

The subsequent changed-binary operation `m4-shared-collection-pair-20261022-013803-1f9ce2` completed only its shared arm. Inter-arm recovery stopped before the immutable arm when host MemAvailable fell 37–73 MiB below the unchanged 4-GiB pair reservation. A fresh pair `m4-shared-collection-pair-20261022-040857-4ee12c` was also refused before launch when 3,794,853,888 B was below 4,294,967,296 B; neither operation was retried. The current source has an unpromoted writer-preferred shared-reader lock and WAL/flush lock-window changes. Correctness/durability/crash regression passes 4,658 checks, but the new microbenchmark is a single small workload and remains non-qualifying. The current goal, operation evidence and next action are in [the active execution checkpoint](results/ACTIVE-EXECUTION.md).

A separate research-only subprocess test confirms native read-only opens are rejected while the same collection is open read-write by another process, then succeed after writer close; M4 `Index::Open` also mutates its directory and has no read-only option. The implemented application uses one resident `zg-daemon` broker, an in-process read-session cache, and outer cross-process workspace locks; brokered shared M4 remains a provisional architecture fit, not a final performance or production selection. Clients should not open M4/native roots directly.

## Earlier shared-pair result — 2026-10-21

A newly authorized corrected matched pair completed both 500×5 arms (immutable-shard then shared-collection; four readers). Correctness/coherence checks passed and cleanup was verified, but both receipts retain workspace-accounting uncertainty (non-atomic scans; no verified lifetime peak). Descriptively, writer p50/p95 was 586/1074 ms for baseline `commit` and 464/482 ms for shared `writer_cycle` (includes GC); reader p50/p95 was 1.58/2.02 ms versus 64.78/97.69 ms. Shared-arm host I/O PSI was much higher (10.11% vs 3.68%), and its process-tree writes/retained apparent root were larger. This single, contention-mismatched run does **not** establish a winner. Per-cycle shared M4 GC search/delete took 350–383 ms (of 374–407 ms total GC; compaction was 0 ms); source inspection shows exclusive snapshot locking spans commit and GC while queries require a shared lock. This is a concrete follow-up hypothesis, not a causal attribution of all reader wait. Keep immutable-shard as baseline; defer promoting shared until reader latency and GC/storage costs are investigated. Full report and receipts: [pair report](results/shared-collection-pair-20261021/m4-shared-collection-pair-20261021-221036-a40ca2/pair-report.md). The wrapper initially missed the candidate `documents` field; both saved outputs were re-parsed and verified without rerunning. Regression tests now cover both output schemas and cleanup representations.

## Previous shared-pair no-launch attempts — 2026-10-21

The owner-directed per-benchmark amendment makes host I/O PSI observational (not an admission gate) for the one descriptive shared-pair comparison only; the generic 4.46% historical gate is unchanged elsewhere. The 500×5 four-reader pair did **not** start. Two recorded wrapper invocations were refused by the independent watchdog before the benchmark binary launched: watchdog arm-name validation accepted `aa-first`/`aa-second`, not `pair-first`/`pair-second`. Both receipts show `benchmark_started=false`; bootstrap cleanup and absence of owned units/cgroups were verified. The second invocation was accidental during failure reproduction, is preserved, and must not be treated as an authorized retry.

The mismatch and a related first-vs-second runtime phase mapping were fixed with focused regression coverage. Rebuilt harness SHA256: `f42a2ffefa8a3bbc77f1f56b7d049be62c4085983e5d4e976b69051bb5c64bfb`; no-launch validation passed. `test_m4_contention_policy.py`: 41/42 (one legacy full-sync assertion is preempted by compiler-input provenance mismatch); benchmark-policy tests 6/6. **No performance comparison exists. The operation is blocked under the no-retry rule; a new native attempt needs explicit owner authorization.** Raw receipts: [pair operation results](results/shared-collection-pair-20261021/); canonical [handoff §72–§73](../../rust/docs/m4-incremental-native-index.md).

Status: **research only.** Nothing here is linked into `zvec`, `zg-engine`, the
daemon, or the production index format. HNSW, the native build, and every
production schema are untouched.

## Latest follow-up — durable shared-GC cursor and crash replay (2026-10-21)

The shared-collection prototype now persists a checksummed, atomically replaced GC watermark. Normal collection examines only newly eligible generations and newly reserved orphan IDs, rather than rescanning the entire historical generation range. The durable cursor is advanced only after M4 and native deletes are flushed; crashes after either store deletion replay idempotently. Test-only verification now passes **1,046 checks, 0 failures**, including crashes after M4 GC, after native GC but before cursor persistence, and after each unpublished commit stage. The full bridge/crash-recovery suite also passes **1,247 checks, 0 failures**. This is correctness evidence on a tiny fixture, not a performance result or cross-process reader-lease proof.

No immutable-shard/shared-collection performance pair was run. The latest resource snapshot (2026-10-21 13:35 UTC) had MemAvailable 4,083,716 KiB; the existing generic floor is 5,477,762 KiB. This operation therefore has no launch authorization under the current machine policy. A microbenchmark-specific resource contract and explicit shared-Btrfs I/O risk acceptance are presented for owner decision; no waiting or automatic retry is in progress.

## Previous follow-up — writer-tail optimization review (2026-10-21)

Three directions were evaluated separately. **Scalar-index initialization — DEFER:** earlier empty-collection measurements associate most create+open growth with scalar-index count, not HNSW graph construction; per-field timings are noisy/non-additive, and the requested internal phase split was blocked by its failed preflight. **Compaction collection reuse — DEFER promotion; bounded correctness prototype now exists:** exact-generation GC reclaims unpinned published rows and WAL-reserved-but-unpublished crash debris; reader leases/backpressure, commit/GC serialization, and crash recovery are covered by test-only proof (931 checks, 0 failures at that stage). No full-size writer-tail comparison or cross-process reader lease protocol exists, and the immutable-shard path remains the baseline. **Cached fingerprint — KEEP opt-in only; reject default promotion:** the 500×5 exploratory run saved ~38–42 ms per M4 open by skipping a second source hash, with native open unchanged. Same-size/same-mtime mutation before open invalidates the digest; a source change after the first hash can be accepted by the opt-in path, while cold-process reopen performs both passes. Strict verification remains default. Detailed evidence, limits, and follow-ups: [phase-trace and direction review](results/writer-tail-phase-trace-20261021/REPORT.md) and [timing report](results/writer-tail-investigation-20261021/REPORT.md).

## Previous follow-up — bounded scalar-open latency diagnostic (2026-10-21)

The owner separately authorized one direct scalar-open latency diagnostic under the amended exploratory contract. The bounded workload completed once: scalar counts 0–6 × HNSW/FLAT × three repetitions, **84 API opens**, zero documents/queries, 5.721 s contained runtime, exit 0. Per-call create+open and reopen timings are recorded in the [full report](results/scalar-open-latency-diagnostic-20261021/m4-scalar-open-latency-diagnostic-20261021-r1/REPORT.md) and raw [latency analysis](results/scalar-open-latency-diagnostic-20261021/m4-scalar-open-latency-diagnostic-20261021-r1/latency-analysis.json).

Outcome: **COMPLETED_WITH_WORKSPACE_UNCERTAINTY / WORKSPACE_CAPACITY_UNVERIFIED**. The workload completed, but sampled workspace scans are not atomic and mutation coverage was lost in one bounded drain; no verified peak or capacity PASS is claimed. Cleanup was independently verified. This is **EXPLORATORY / NON-QUALIFYING**, not an internal stage trace, qualification, stable-tail estimate, or causal comparison. That one-run diagnostic is spent. Current owner-directed research authorizes further M4 investigation, but any native experiment still needs a fresh operation-bound plan and all unchanged resource gates.

## Earlier follow-up — resource-only exploratory contract amendment (2026-10-21)

One owner-approved 84-open rehearsal ran: 7 scalar counts × HNSW/FLAT × 3 repetitions, create/close/reopen/close, no documents or queries. Workload completed in 3.49 s under MemoryMax 1 GiB / swap 0 / CPU 200% / tasks 128 / 20 s workload watchdog; sampled cgroup peak 49,082,368 B, swap 0, tasks 66. The approved operation-specific 3 GiB MemAvailable / 8 GiB free-disk floors passed. **Contract amendment:** in resource-only exploratory runs, load-1 is observation-only, and positive host swap/reclaim deltas are recorded/analyzed rather than automatic distress. Neither alone blocks. The observed load (5.19 vs historical 3.14), swap-in/out and direct-scan deltas remain raw context, not M4-attributed impact.

The workspace observer lost inotify coverage, so accounting remains **UNKNOWN / INCOMPLETE**: no verified peak storage or complete resource measurement, and no PASS/qualification claim. The workload and cleanup completed; no retry or new run is authorized. Cgroup caps, timeout, correctness, cleanup and host reserve guards remain. Full reclassification and immutable raw receipt references: [contract amendment](results/open-probe-resource-plan-20261021/CONTRACT-AMENDMENT-20261021.md), [resource-plan report](results/open-probe-resource-plan-20261021/REPORT.md), [machine-readable policy](docs/benchmark-policy.json).

Read-only inventory remains unchanged: 209 `/var/tmp/m4-*` roots, retained builds/results/source preserved. Twenty analyzer snapshots (111.9 MB allocated) are reproducible, but live-consumer inspection was incomplete; no unrelated cleanup. Focused cache lifecycle test build/CTest passed 1/1 (3.95 s, 38 checks). Current state: **PARTIAL / MEASUREMENT_INCOMPLETE**.

## Earlier handoff — 2026-10-21 owner-authorized phase trace blocked at preflight (historical)

**The owner authorized one bounded 0–6 scalar-index phase trace** (three repetitions, HNSW and FLAT in separate matched conditions, fresh create and reopen) and accepted the shared-Btrfs I/O risk. It was **not launched**: fresh mandatory preflight failed MemAvailable (**3,992,232 KiB < 5,477,762 KiB**) and `/var/tmp` free space (**10,815,062,016 B < 18,219,000,000 B**). I/O PSI and load passed. No native source/binary changes, systemd unit, cgroup, or timing samples were created. Do not bypass these unchanged hard gates; no automatic retry. Details and raw receipts: [phase-trace report](results/writer-tail-phase-trace-20261021/REPORT.md); canonical [handoff §64](../../rust/docs/m4-incremental-native-index.md).

The existing bounded probes still show that empty native collection open is dominated by scalar-index setup (six-field HNSW/FLAT medians ~64–80 ms versus ~8–13 ms with no scalar indexes), but the internal stage split and 0–6 slope remain **unmeasured**; cycle 7’s 180.306 ms outlier remains unexplained. Source trace maps one shared RocksDB create/open followed by per-field column-indexer setup. The cached-fingerprint mode remains opt-in: same-content identity/generation reuse and cold-cache full verification are tested, but hot-cache concurrent dictionary mutation remains a correctness tradeoff. A new cold-cache regression assertion passed in `m4_analyzer_cache_lifecycle` (1/1, 2.78 s); final reopen/crash evidence from prior runs used strict options. No runtime algorithm, production schema, or benchmark policy changed.

The prior 2026-10-21 500×20 arm remains the latest completed full native measurement. The new phase-trace authorization was exercised only for read-only preflight; any future launch requires fresh passing gates, an operation plan consistent with the then-current policy, and confirmation that the one-run authorization still applies. The earlier 500×20 campaign budget remains exhausted.

### Previous native diagnostic — 2026-10-21

**After correction4579e5a, the separately authorized single500x20arm completed:20commits/80readerqueries, exit0, coherence/reopen errors0. Writer p50/p95/p99 =231.399/267.359/369.428ms.** Native-vector stage p50=102.032ms; post-M4SyncTree p50=15.385ms. Kernel cgroup RAMpeak308.090MiB/swap0/CPU4.979s; no observed hard distress or reserve failure, cleanup verified. Raw receipt remains **COMPLETED_WITH_WORKSPACE_UNCERTAINTY / INCOMPLETE**:27/104workspace samples UNKNOWN, including3pending-drain cases correctly continued without fake PASS. One run/20samples, p99=max, not qualification or a stable performance comparison. See [completed-arm report](results/pending-drain-native-20261021/REPORT.md), [analysis](results/pending-drain-native-20261021/analysis.json) and [handoff §62](../../rust/docs/m4-incremental-native-index.md). New1/1spent, no further run authorized; old4/4campaign and preceding1/1abort unchanged. Production/migration unapproved.

### Previous amendment and aborted attempt (historical)

**Research only. Owner APPROVED contention-aware exploratory workspace soft budgets; engineering verified with79tests plus actual watchdog rehearsal.** Hard cgroup/time/RAM/disk reserves/distress/correctness/durability/cleanup remain unchanged. Known workspace races stay UNKNOWN/INCOMPLETE, budget breaches warn; neither becomes fake PASS. Qualification/other benchmarks remain strict. One NEW single-arm full-sync500x20 diagnostic is authorized after fresh readiness/provenance PASS, no retry on refusal. The old4/4campaign stays exhausted. See [amendment report](results/workspace-soft-budget-20261021/REPORT.md) and [new attempt ledger](results/workspace-soft-budget-20261021/new-attempt-ledger.json); **Latest outcome: readiness PASS, native PID716330 launched once then workspace-accounting abort after1.175s; zero complete cycle payloads or writer-tail results. New1/1attempt spent, no retry.** Pending-after-drain classification corrected prospectively with81finaltests PASS, not natively revalidated. Exact raw abort/cleanup remain unchanged; follow the report for resource impact and remaining uncertainty.

### Prior feasibility conclusion (superseded only by the explicit soft-budget authority)

**Historical strict-contract result: PARTIAL / OPERATION_BLOCKED.**

- Observer correction committed as **`6439ed2`**: bounded single-pass observation, explicit race/uncertainty states, no retry/rescan and no partial inventory promoted to a verified peak.
- **72 distinct tests PASS**; the final A/A controller's eight relevant synthetic tests were rerun on its pinned source. Direct typecheck of four changed Python files PASS. These are software checks, not native measurements or fresh host admission.
- Campaign budget: **4/4 attempts consumed, zero remaining**. Historical outcome: one actual native arm launched, zero completed arms/pairs. No native execution occurred during observer repair.
- **Native launch path is NOT ready.** Shared Btrfs has no independently verified, correctly scoped capacity bound for both allocated and apparent bytes. Known publication races therefore still fail closed; inotify, sampled sizes and IO counters are not substitutes for that proof.
- **Focused capacity-feasibility investigation COMPLETE; decision: retain NO-LAUNCH.** Read-only ioctl confirms shared Btrfs subvol5; quota status is EPERM/unverified. M4-only logical accounting misses native-vector writes. No low-cost whole-arm proof was established; do not continue an indefinite repair loop. A revised residual-risk contract or isolated-storage scope requires a separate owner decision; later native execution also needs fresh authority/budget. Neither change is adopted here. Production migration remains unapproved.

Read the [feasibility decision](results/capacity-feasibility-20261021/REPORT.md) and [canonical handoff](../../rust/docs/m4-incremental-native-index.md), especially **§60** (closed investigation), **§58** (original abort) and **§59** (observer correction), then the [repair report](results/workspace-observer-repair-20261020/REPORT.md) and [benchmark policy](docs/benchmark-policy.md). Exact test commands/source identities are in `results/workspace-observer-repair-20261020/verification-v3/test-receipt.json` and `consumer-final/test-receipt.json`.

The repair evidence/checksum manifest is pinned to commit `6439ed2`; verify it against that revision, not against subsequently edited documentation. Historical receipts and campaign accounting are not rewritten by this handoff update.

## Local artifact workspace

Run repository commands from the repository root and use the M4 project-local
workspace for agent-controlled temporary/build outputs:

```sh
mkdir -p research/m4-native/.work/{tmp,build,sanitizer,scratch}
export TMPDIR="$PWD/research/m4-native/.work/tmp" TMP="$PWD/research/m4-native/.work/tmp" TEMP="$PWD/research/m4-native/.work/tmp"
```

Build directories belong under `research/m4-native/.work/build/` (or
`.work/sanitizer/`); receipts and evidence that must survive cleanup belong in
`research/m4-native/results/`. Benchmark data roots must still use the
filesystem specified by that experiment's protocol (for example `/var/tmp`
when Btrfs semantics are required); do not redirect them to tmpfs or change
their storage semantics via `TMPDIR`.

## Why this exists

M2/M3 showed that a single-file write still costs 20–30 s on large
collections. The cost is not HNSW (that path is already base-reuse + tail
append). It is the two whole-collection reducers:

- `ReduceScalar` / `ReduceScalarIndex` re-emit every forward row and rebuild
  the sealed INVERT RocksDB;
- `ReduceFts` re-merges every FTS posting and rewrites the stat CF.

M3.3 evaluated the narrow fix (make the BM25 block bound stat-independent) and
rejected it as incomplete: the FTS RocksDB still had to be rebuilt, and the
scalar INVERT had the same sealed-format problem. M4 therefore changes the
**storage/update architecture for both index kinds together** rather than
patching one bound.

## Scope

Kept:

- HNSW and its base-reuse semantics (not touched);
- the BM25 formula, top-k semantics, boolean/phrase FTS, scalar EQ/range
  filters;
- the hybrid-merge contract (vector scores injected by the caller);
- exact result correctness, validated against a full-scan oracle.

Changed:

- how FTS postings and scalar inverted postings are stored and updated;
- how deletes/updates are represented (one shared tombstone/version catalog);
- how BM25 block bounds are stored (raw `max_tf`/`min_dl`, no IDF in the
  format);
- how compaction reclaims tombstones and bounds run count.

Deliberately **not** kept: compatibility with the old sealed FTS/INVERT
format. The research format is new and re-indexed from source. This is the
constraint that prevented an incremental design in M3.

## Architecture

### One document catalog for both index kinds

Every logical write receives a new monotonically increasing global `DocId`
(never reused). An update is:

```
tombstone(old DocId) + append(new DocId)
```

The tombstone set is shared by FTS and INVERT. No posting list is rewritten on
delete or update. A query filters candidates through the same live set, so the
two indexes cannot disagree about a document's liveness.

### Log-structured runs

```
active delta (bounded RAM)  --flush-->  level-0 run
                                           |
                         tiered compaction (R runs / level)
                                           v
                                      higher levels
```

A run contains:

- docs: `doc_id`, `dl`, primary key, unique term ids;
- FTS: sorted dictionary, per-term postings, per-block `max_tf` / `min_dl`,
  positions;
- INVERT: per-field sorted values, doc-id postings.

Runs are immutable. A flush writes only the changed documents; a delete writes
only a tombstone. Compaction merges adjacent runs, drops tombstoned docs, and
recomputes the merged run's raw block metadata.

### Streaming compaction (bounded peak memory, direct writes)

Compaction is a k-way merge by sorted term (FTS) and sorted value (INVERT).
It never materializes a merged run in RAM:

1. Merge each term's postings across input runs with one decoded 128-doc block
   per input; emit fixed-size payload blocks **directly into the final file**.
2. Append that term's fixed block metadata immediately after its payload, then
   write the final term dictionary once, after all terms. No temp payload or
   temp metadata file is created.
3. Stream docs in doc-id order after the FTS dictionary is known, mapping
   source term ids through the new dictionary (O(vocabulary) map, not
   O(collection)).
4. Append the INVERT section the same way: per-value payload + metadata, then
   the field dictionary and directory.
5. Patch the 120-byte header with final section offsets and checksum, fsync,
   then atomic-rename. The checksum is accumulated over each byte as it is
   written; there is no second read of the output.

The enforced working-set budget (`compaction_memory_limit_bytes`, default
128 MiB) charges posting buffers, fixed block-metadata vectors, the new
dictionary, and fixed IO buffers. If it cannot fit, compaction aborts before
the manifest is published and the old runs remain active. A temp-output RAII
guard removes an unpublished `.tmp.<pid>` file on failure.

Compaction output uses research **format v2**; flush still writes v1. The
reader accepts both and dispatches on the header version, so existing runs do
not need conversion. Measurements and A/B evidence are in
`results/RESULTS.md`.

### Snapshot compaction prototype (explicit opt-in, research only)

`Index::CompactNowSnapshotPrototype()` is a separate experimental path; the
ordinary `CompactNow()` and automatic compaction policy remain unchanged. It
snapshots the immutable input runs and only the tombstones belonging to those
runs, reserves an output run ID, then releases both the writer-state and query
locks while the streaming merge runs. At publication it reacquires locks in
writer-state-then-query order and validates the mutation generation, WAL
sequence, run IDs/files, and segment identities. Any concurrent mutation or
flush causes the new output to be discarded and an error returned; no
compaction manifest is published, and the current state (including committed
writer changes) remains authoritative. A competing compaction is rejected
before its build begins. The method does not retry automatically, so sustained
writes can repeatedly supersede compaction.

The per-index build slot prevents simultaneous compaction builds. Automatic
maintenance is deferred while the prototype owns that slot. Active output and
temp filenames are protected from concurrent flush garbage collection;
unreferenced temp files remain eligible for cleanup after process restart. The
input-run metadata and group tombstone snapshot are charged conservatively
against `compaction_memory_limit_bytes`; the charge is an estimate, not
allocator-level accounting. Regression tests pause after an FTS term has been
merged, apply a concurrent delete, verify writer/read progress, and require
stale publication to abort. A concurrent flush also verifies run-ID
reservation, GC protection for in-flight output, stale-output cleanup, and
forced-rebuild deferral. Coverage includes stable publication/reopen,
pre/post-manifest fault recovery, and early memory-budget refusal. This is
correctness evidence only: no performance or production-readiness claim is
supported, and default APIs remain unchanged.

### Query-time BM25 bound (no stale IDF)

Each block stores its own `max_tf` and `min_dl`. At query time:

```
idf(t)      = ln((N_live - df_live(t) + 0.5) / (df_live(t) + 0.5) + 1)
block_bound = idf(t) * max_tf * (k1+1)
              / (max_tf + k1 * (1 - b + b * min_dl / avg_dl_live))
```

`df_live`, `N_live`, and `avg_dl_live` are current live-corpus statistics.
Corpus growth can therefore never invalidate a persisted bound. WAND uses
these bounds over the union of run iterators; tombstoned candidates do not
reduce recall (the scan simply continues).

### Query diagnostics (research)

Passing a `QueryDiagnostics` pointer to `SearchFts` or `SearchFtsFiltered`
collects query-lock wait, filter construction, query parse, cursor/setup,
posting execution and final top-k wall/reader-thread CPU timings, alongside
run/posting/liveness/candidate counters. Query parse is a subset of setup.
CPU timings use `CLOCK_THREAD_CPUTIME_ID`; they attribute CPU to the calling
thread, not to other worker threads. Timing clocks are only sampled when the
caller requests diagnostics. These counters do not change search semantics or
ranking and do not expose native HNSW internals.

### Statistics without O(N) rewrite

- `N`, `total_dl`, and per-term `df` are updated on write, in O(tokens of the
  changed document).
- The update log is appended per flush and checkpointed only when it exceeds a
  size threshold, so checkpoint cost is amortized.
- Compaction does not change live statistics (dead rows were already
  excluded), so it never touches the stats map.

### Durability and recovery

- WAL record per mutation; fsync optional (on by default).
- Flush order: segment file → stats checkpoint/log → tombstone snapshot →
  atomic manifest replace → WAL truncate.
- Crash before the manifest leaves orphan files and replays the WAL. Crash
  after the manifest skips WAL records already covered by the manifest
  sequence.
- Manifest writes keep the previous file as `manifest.bak`; an invalid
  manifest falls back to it.
- Old run/tombstone/stats files are garbage-collected only after the new
  manifest is durable.

### Bounds

- **Query fan-out**: `max_total_runs` (default 12); tiered compaction keeps
  `runs_per_level ≤ R` and forces a merge when the cap is exceeded.
- **RAM**: active delta is capped by docs and bytes; segment dictionaries are
  mmap-packed; postings decode one 128-doc block at a time. Streaming
  compaction holds one decoded block per input per active term/value, one
  output block, and the new term dictionary. The dictionary is metadata-scale
  and is charged against the compaction memory budget.
- **Disk**: tombstones are the only retained garbage; compaction triggers at a
  configurable ratio (default 0.30) and after bounded run growth. Old runs are
  removed after commit.

## Format (research, not compatible)

Little-endian, fixed header (120 bytes):

```
magic/version/block_size
run_id/level/first_doc/last_doc/doc_count/total_tokens
docs_offset/size, fts_offset/size, invert_offset/size, checksum
```

- `docs`: varint deltas for `doc_id`, `dl`, pk, unique term ids.
- `fts`: field name, term dictionary, fixed 32-byte block metadata
  (`last_doc, max_tf, min_dl, payload offset/size`), then varint postings
  (`doc delta, tf, dl, positions`).
- `invert`: field count; per field sorted value dictionary and fixed 24-byte
  block metadata (`last_doc, payload offset/size`), then doc-id delta
  postings.

**v1 (flush)** stores one global postings table and global metadata table; term
entries carry `first_block`/`block_count`. **v2 (compaction)** stores each term
and value as a self-contained payload chunk plus metadata chunk, with offsets
relative to its section. Its section layout is:

```
fts:    field name, [term payload][term metas]..., term dictionary, footer
docs:   count, records
invert: per-field [value payload][value metas]..., value dictionary, directory,
        footer
```

v2 dictionaries are sorted and written after their chunks, so one streaming
pass can assign final term ids before the docs section. Both versions use the
same fixed metadata structs and checksum contract. The segment checksum covers
the contiguous data from the lowest section offset to EOF, so it works for the
v2 section order as well as the v1 docs-first order.

Fault-injection hooks (`M4_FAULT=after_rename|before_manifest|after_manifest`)
exercise failure before and after the atomic publish in
`tests/m4_correctness.cc`; the old runs stay searchable, or a reopened index
sees the durable new run, as appropriate.

Manifest, stats checkpoint, tombstones, and WAL are separate files with their
own checksums.

## Correctness oracle

`tests/m4_correctness.cc` keeps an independent full-scan reference with the
same tokenizer and BM25 formula. It validates:

- FTS top-k scores and membership for OR/AND/phrase, including after corpus
  growth (the stale-bound regression);
- scalar EQ/LT/LE/GT/GE sets and typed Uint32 EQ/finite-IN sets, including
  empty IN, duplicates, boundaries, missing fields, update/delete, and reopen;
- typed Boolean filter compositions (AND/OR/NOT) over string EQ and
  filename prefix/suffix, Uint32 EQ/IN, and ArrayUint32 membership, with
  three-valued missing-field semantics and pre-top-k candidate filtering;
- randomized updates/deletes (1,200 base docs + 500 mutations);
- flush, compaction, close/reopen;
- WAL replay after destruction without a flush;
- bounded run count and query correctness under a small run cap;
- hybrid ranking, a forced compaction-memory-budget failure that must leave
  the old runs searchable, and injected faults at rename/manifest boundaries;
- delete-heavy churn (updates, deletes, replacement inserts, reinserted
  deleted keys) with exact live-doc/token/FTS/INVERT checks after repeated
  compactions, WAL replay after churn, and final tombstone GC.

The M4-P2.4 oracle adds filename prefix/suffix, exact/inequality, empty and
missing filename cases, and validates update/delete/reinsert, WAL replay,
flush/compact/reopen, and filtered top-k. Current result: 18,095 checks,
0 failures; warning-clean and ASan + UBSan + leak detection clean.

## Native FTS differential harness (research only)

`tests/native_fts_differential.cc` compares single-field M4 FTS with the
checked-out native Zvec C API using separate temporary collection/index
folders. Set `M4_ZVEC_ROOT` to enable it; this also compiles the checked-out
native `standard` and `jieba` tokenizer implementations plus the native
`lowercase` filter into the M4 research target. The Jieba compatibility target
requires an explicit dictionary path. It does not modify native sources, the
production ABI, or any production index.

```sh
cmake -S research/m4-native -B research/m4-native/.work/build/p1 \
  -DCMAKE_BUILD_TYPE=Release \
  -DM4_ZVEC_ROOT="$PWD/.local/native/zvec-native-profile"
cmake --build research/m4-native/.work/build/p1 --target m4_correctness m4_native_fts_differential m4_native_bm25_isolation -j4
research/m4-native/.work/build/p1/m4_correctness
LD_LIBRARY_PATH="$PWD/.local/native/zvec-native-build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  research/m4-native/.work/build/p1/m4_native_bm25_isolation
# Expected to exit nonzero while native/M4 ranking remains non-parity:
LD_LIBRARY_PATH="$PWD/.local/native/zvec-native-build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  research/m4-native/.work/build/p1/m4_native_fts_differential
```

The fixture pins `standard` + `lowercase`, empty extra parameters, native BM25
defaults (`k1=1.2`, `b=0.75`), and a score tolerance of `1e-4` absolute plus
`1e-4` relative. M4's runtime token carries native byte offsets for direct analyzer
differentials; the experimental segment format continues to store terms and
positions only. The existing harness returns nonzero for semantic differences
(`checks=438 failures=57`). The new minimal
probe emits native-source and M4-live BM25 components at insert/update/delete/
optimize/reopen and passes its lifecycle/model checks. P1.1 attributes the
remaining score differences to native's tombstone/compaction statistics
lifecycle versus M4's live-corpus statistics; it does not claim ranking parity
or authorize an algorithm change. See `results/native-fts-differential-20261017.md`,
`results/native-bm25-breakdown-20261017.log`, and
`results/native-bm25-isolation-20261017.log`. These are correctness experiments,
not performance benchmarks.

## Native typed Uint32 differential (research only)

`tests/native_uint32_differential.cc` exercises `file_id: Uint32` against the
checked-out native C API. The native field is non-nullable, matching the
`zg-engine` schema; the fixture records that a missing-value write is rejected.
M4 intentionally supports absent fields and verifies they match neither EQ nor
IN. The harness checks logical-PK equality across boundary values, duplicate
IN, update/delete, flush, optimize/compact, and reopen. A high-scoring
non-matching FTS hit plus a lower-scoring matching hit verifies candidate
ordering: the harness retrieves all FTS matches, intersects the typed scalar
set, then applies top-k. M4 still has no integrated FTS+scalar-filter API; this
slice adds no planner.

Native `file_id IN ()` is rejected with `INVALID_ARGUMENT`; M4 `IN []` returns
an empty set. The harness records this compatibility difference explicitly.
It passes with `checks=528 failures=0`. This is correctness evidence only; no
performance benchmark was run. `SearchInvertUint32In` deduplicates input
values, collects exact-value postings, then sorts/uniques candidate DocIds;
extra memory is O(U + P) for U unique inputs and P matched postings.

```sh
cmake --build research/m4-native/.work/build/p1 --target m4_native_uint32_differential -j4
LD_LIBRARY_PATH="$PWD/.local/native/zvec-native-build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" research/m4-native/.work/build/p1/m4_native_uint32_differential
```

## Native ArrayUint32 membership differential (research only)

`tests/native_array_uint32_differential.cc` validates the actual `zg-engine`
`ancestor_directory_ids: ArrayUint32` usage against native Zvec by logical PK.
It covers `CONTAIN_ANY` and `NOT CONTAIN_ANY`, including empty arrays and empty
operand lists, duplicate elements/operands, missing fields, 0 and
`UINT32_MAX`, update/delete/reinsert, WAL replay, flush, optimize/compaction,
and reopen. Native rejects a missing value under the production-shaped
non-nullable schema; M4 permits missing arrays but excludes them from both
membership and the negative non-null universe. Native empty-list behavior is
`CONTAIN_ANY ()` → false and `NOT CONTAIN_ANY ()` → `IS NOT NULL`. Both engines
reject more than 32 operands. The latest differential is `checks=954
failures=0`; the M4 oracle passes at `checks=17323 failures=0` under warning-
clean and ASan+UBSan builds. The harness also reports fixture-level NOT
candidate materialization: 6 live non-null docs, 2 matching docs, 4 returned,
8 examined DocIds, 64 bytes of logical universe+match DocId payload (excluding
hit strings and temporary vector capacity). General AND/OR/NOT composition was subsequently approved and implemented in
M4-P2.3; production integration and performance benchmarks remain out of scope.

```sh
cmake --build research/m4-native/.work/build/p1 --target m4_native_array_uint32_differential -j4
LD_LIBRARY_PATH="$PWD/.local/native/zvec-native-build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" research/m4-native/.work/build/p1/m4_native_array_uint32_differential
```

## Native Boolean filter differential (research only)

`tests/native_boolean_filter_differential.cc` covers the usage-backed filter
subset from `zg-engine`: nested AND/OR/NOT over string EQ/NE, filename prefix/
suffix, Uint32 EQ/IN, and ArrayUint32 membership. It checks nullable-field
semantics, missing and empty filenames, escaped `%`/`_`/backslash/quote,
ASCII/Unicode and case-sensitive matches, updates/deletes/reinserts,
flush/compact/WAL recovery/reopen, and native + M4 filtered FTS top-1 before
truncation. Native `file_id IN ()` remains rejected while M4 returns empty;
`NOT LIKE` is rejected by native parsing and M4. Empty filename prefix/suffix
produce `LIKE '%'`, matching all present values (including empty strings) but
excluding missing fields. Rust declares `file_name` through
`wildcard_string(schema, "file_name", false)`, which sets native
`IndexParams::invert(false, true)` (non-nullable, range optimization off,
extended wildcard on); the differential uses that index configuration but
makes the field nullable to exercise missing-value semantics. The native binder
accepts at most one unescaped `%`, rejects unescaped `_` for invert execution,
and allows leading `%` only with extended wildcard enabled. The LIKE evaluator
unescapes escaped characters, treats `%` as the sole wildcard in this path,
uses exact byte-sensitive term keys, and maps bare `%` to `IS NOT NULL`. Rust's
`like_literal` escapes backslash, `%`, and `_`; SQL quoting escapes apostrophes.
The simple APIs cannot express raw SQL patterns or invalid multi-wildcard input.

Current M4-P2.4 result is recorded in
`results/m4-p2.4-filename-like-compatibility-20261017.md`; no performance
benchmark was run. The fixture reports candidates, distinct field-value
dictionary entries scanned across runs/delta, the LIKE candidate DocId-vector
capacity, and an analytic logical DocId-payload bound. LIKE scans every
`file_name` value in every run and the active delta; there is no dedicated
prefix/suffix index, so work is O(distinct indexed values + matched postings).
The capacity metric excludes Boolean merge buffers, `Hit`/PK strings, vector
capacity slack outside the reported leaf, and allocator overhead; the analytic
bound excludes `Hit`/PK storage and vector capacity slack.

```sh
cmake --build research/m4-native/.work/build/p1 --target m4_native_boolean_filter_differential -j4
LD_LIBRARY_PATH="$PWD/.local/native/zvec-native-build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  research/m4-native/.work/build/p1/m4_native_boolean_filter_differential
```

## zg-engine-shaped end-to-end workload (research only)

`tests/native_zg_workload_differential.cc` derives its fixture schema and
queries from `zg-engine` fragment projections: file/entity/document identity,
ancestor-directory IDs, filename, hashed symbol name/type, and FTS text. It
checks root/nested files, path and symbol filters, positive filename prefix/
suffix LIKE, same-PK upsert, whole-file deletion, duplicate suppression,
filtered top-k, compaction, and forced-exit WAL recovery against isolated
native and M4 directories, normalizing identity by fragment primary key. The
original P3 harness omitted native Zvec's process-wide Jieba dictionary
registration, so collection creation returned `INVALID_ARGUMENT`; this was a
harness configuration error, not lack of native Jieba support. Rust
`zg-engine` auto-registers the bundled dictionary during initialization and
uses `jieba` + `lowercase` with no per-field extra params. Its tokenizer
factory has no standard-tokenizer fallback. The original E2E fixture remains
standard+lowercase and ASCII-only, so it is not production analyzer parity.

P3.2 adds a research-only M4 analyzer adapter that directly reuses the pinned
native `JiebaTokenizer` and `LowercaseTokenFilter` sources. M4 requires an
explicit `jieba_dict_dir`, preflights the two required dictionary files, and
returns initialization errors rather than falling back to `standard` or an
ambient dictionary. Indexing, WAL replay, and query parsing share the same
adapter; quoted phrases are tokenized as one phrase string so Jieba sees the
same context as native query analysis. The compatibility differential compares
terms, sequence positions, and byte offsets for ASCII, punctuation, Chinese,
mixed-language, and Unicode fixtures, then checks query hit sets across
phrases, update/delete, compaction, reopen, and WAL recovery. The segment format still stores only terms/positions. Manifest v2 persists a
deterministic analyzer fingerprint (configuration plus SHA-256 of the Jieba
dictionary/model contents); same-content dictionaries at different paths are
equivalent, while mismatched identities are rejected before WAL replay. Legacy
manifests without identity require rebuild in a new directory. BM25 remains M4
live-statistics; score parity is not claimed. See the P3.2/P3.3 reports and logs, alongside
`results/m4-p3-zg-workload-validation-20261017.md` and
`results/m4-p3.1-effective-analyzer-20261017.md`.

The opt-in research option `Options::reuse_analyzer_cache` reuses an immutable
in-memory analyzer implementation across live indexes in the same process,
keyed by the already-verified analyzer fingerprint. The registry stores weak
references only; each `Index` retains shared ownership for its lifetime, so
pinned generations keep their analyzer alive without extending run/file
ownership. Fingerprint checks remain enabled on cache hits; a cache entry never
replaces manifest identity validation or is persisted to disk. The cache is
OFF by default. It does not cache segment handles: the P4 open-stage profile
measured segment loading below 0.5 ms while Jieba analyzer initialization
accounted for about 46 ms. This is a standalone M4 research optimization, not
an HNSW or production change; see `results/m4-m4-open-attribution-cache-20261018.md`.

## M4-P4 paired native vs M4 workload

`tests/native_m4_paired_benchmark.cc` plus
`tests/profile_native_m4_paired.py` run one isolated index/process per arm
(`native`, per-document `m4`, or durable `m4-batch`), using a generated
zg-engine-style fragment workload, logical-PK oracle, resource sampling, and
paired order. This is FTS/INVERT only and does not use native optimize or M4
`CompactNow()` as a baseline. Use a real filesystem and a fresh result root.
P4 evidence is in `results/m4-p4-native-vs-m4-20261017.md`. P4.1 attribution
and traces are in `results/m4-p4.1-write-amplification-20261018.md` and
`results/artifacts/m4-p4.1-writeamp-60-20261018.tar.gz`.

P4.2 adds a research-only `m4-batch` arm backed by `ApplyBatch` (max 256
mutations / 8 MiB, durable WAL transaction when enabled); existing
per-document APIs and default policy remain unchanged. Phase A/E native
comparisons and raw syscall/per-query artifacts are in
`results/m4-phase-a-native-vs-m4-batch-20261018.md`,
`results/m4-phase-e-final-native-ab-20261018.md`, and
`results/artifacts/m4-native-m4batch-phase-a-e-20261018.tar.gz`. The profiler
retains and classifies the known `json parse` native-only extra hits rather
than suppressing them. `--trace-fsync` enables detached `strace` syscall
counting (adds tracing overhead); `--m4-debt-policy` enables the accepted S5
policy for the M4 candidate only after the base flush/reopen. Default remains
OFF. The policy-on A/B, sustained-churn evidence, and complete limitations are
in `results/m4-phase-b-d-churn-debt-20261018.md`. Results are synthetic,
research-only; durability boundaries differ, and production integration is
unapproved.

### Cross-component bridge snapshot prototype (research only)

`tests/m4_bridge_integration.cc` models a separate M4 FTS/INVERT store beside
native Zvec vector/HNSW. Coordinator reads hold a shared snapshot lease across
FTS+vector hybrid queries. Commit stages M4 rows while readers may continue
querying the prior published generation; it excludes readers only during each
native vector upsert call, then releases between batches. It reacquires the
exclusive lease for final vector flush and manifest publication. Component
compaction/optimize and generation GC still hold the exclusive lease. A
concurrency regression verifies readers finish during M4 staging and return the
exact prior-generation hit set. This prevents native vector IDs from racing the
forward-filter row bitmap during mutable segment writes while avoiding reader
blocking during M4 staging and between completed native upsert calls. The
shared-collection prototype also has a research-only leased-generation GC path
in `tests/m4_bridge_integration.cc`; it searches one reclaimable generation
at a time and issues deletion batches capped at 256 mutations and a conservative
7 MiB encoded-size budget. This avoids a second full-sized copy of all stale
primary keys in memory. M4
reclamation is limited to generations older than all registered reader leases,
so current-generation readers may continue during M4 search/delete/flush. The
exclusive snapshot lease is acquired around each native deletion call and
reacquired for native flush, optional compaction, and checkpoint publication.
Current readers may proceed between completed delete calls; queries for reclaimed
old generations must hold a registered lease. Correctness/crash evidence and the comparative
reader-stall/GC measurements are in `results/M4-STATUS-20261021.md` and the
linked operation report. The one measured pair found high reader latency and
material GC cost. The GC batching and narrowed commit lock are correctness-
verified research changes, not yet performance-evaluated; neither justifies
shared-collection promotion or any production claim.

```sh
cmake --build research/m4-native/.work/build/p4 --target m4_bridge_integration -j2
research/m4-native/.work/build/p4/m4_bridge_integration research/m4-native/.work/tmp/m4-bridge-fresh "$PWD/.local/native/zvec-native-profile/thirdparty/cppjieba/cppjieba-5.6.7/dict"
M4_BRIDGE_TRACE=1 research/m4-native/.work/build/p4/m4_bridge_integration research/m4-native/.work/tmp/m4-bridge-trace "$PWD/.local/native/zvec-native-profile/thirdparty/cppjieba/cppjieba-5.6.7/dict"
research/m4-native/.work/build/p4/m4_bridge_integration /var/tmp/m4-bridge-perf "$PWD/.local/native/zvec-native-profile/thirdparty/cppjieba/cppjieba-5.6.7/dict" --perf

# Controlled AB/BA reader-vs-existing-vector-compaction crossover; fresh root required
research/m4-native/.work/build/p4/m4_bridge_integration /var/tmp/m4-reader-compaction-crossover "$PWD/.local/native/zvec-native-profile/thirdparty/cppjieba/cppjieba-5.6.7/dict" --reader-compaction-crossover
```

```sh
python3 tests/profile_native_m4_paired.py \\
  --binary research/m4-native/.work/build/p4/native_m4_paired_benchmark \\
  --dictionary "$PWD/.local/native/zvec-native-profile/thirdparty/cppjieba/cppjieba-5.6.7/dict" \\
  --root /var/tmp/m4-p4-new --files 1200 --runs 3 \\
  --candidate-arm m4-batch
```

```sh
cmake --build research/m4-native/.work/build/p1 --target m4_native_zg_workload_differential -j4
LD_LIBRARY_PATH="$PWD/.local/native/zvec-native-build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  research/m4-native/.work/build/p1/m4_native_zg_workload_differential

# Effective zg analyzer and research-only Jieba parity/lifecycle differential
cmake --build research/m4-native/.work/build/p1 --target m4_native_jieba_compatibility -j4
LD_LIBRARY_PATH="$PWD/.local/native/zvec-native-build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  ctest --test-dir research/m4-native/.work/build/p1 --output-on-failure \
  -R '^m4_native_jieba_compatibility$'
```

## Run

Benchmark and harness gate semantics are governed by
[`docs/benchmark-policy.md`](docs/benchmark-policy.md) and its executable
source [`docs/benchmark-policy.json`](docs/benchmark-policy.json). Historical
resource-envelope refusals block only the affected measurement operation; they
do not authorize threshold changes, automatic retries, or mission completion.
The active user mission/DoD takes precedence over an older handoff next action.

```sh
# Correctness (temporary data uses TMPDIR; see Local artifact workspace)
cmake -S research/m4-native -B research/m4-native/.work/build/default
cmake --build research/m4-native/.work/build/default -j
research/m4-native/.work/build/default/m4_correctness

# Fixed-snapshot M4 Bridge search diagnostics (resource-gated; research only)
# --reader-search-control disables diagnostics; --reader-search-counters enables
# counters only; --reader-search-timing enables phase timers only;
# --reader-search-diagnostics enables both. QueryDiagnostics defaults both ON
# for a non-null pointer; set the input flags independently for overhead A/B.
# Build the harness to match zg's default 256-D normalized cosine embedding shape.
cmake -S research/m4-native -B research/m4-native/.work/build/bridge-search256 \
  -DCMAKE_BUILD_TYPE=Release \
  -DM4_ZVEC_ROOT="$PWD/.local/native/zvec-native-profile" \
  -DCMAKE_CXX_FLAGS=-DM4_BRIDGE_DIMENSION=256
cmake --build research/m4-native/.work/build/bridge-search256 --target m4_bridge_integration -j1
LD_LIBRARY_PATH="$PWD/.local/native/zvec-native-build/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  research/m4-native/.work/build/bridge-search256/m4_bridge_integration \
  /var/tmp/m4-reader-search-diagnostics-20261018 \
  "$PWD/.local/native/zvec-native-profile/thirdparty/cppjieba/cppjieba-5.6.7/dict" \
  --reader-search-diagnostics

# Search-only overhead arms use identical fixed-snapshot queries, fresh roots,
# and the same binary; capture per-process wall/CPU/PSS plus host state.
# The isolated native HNSW worktree can additionally emit per-query counters
# when M4_NATIVE_SEARCH_COUNTERS=1; see the dated results record for the
# isolated link and provenance commands. Do not use instrumented timings as a
# production-like baseline without measuring their overhead.

# Benchmark (all cases)
./build/m4_benchmark

# One size
./build/m4_benchmark 50000

# Delete-heavy churn: base docs, cycles, ops/cycle
M4_BENCH_ROOT=/var/tmp/m4-churn-500k \
  ./build/m4_benchmark --churn 500000 8 6000

# S5 structural debt policy (default OFF)
M4_BENCH_ROOT=/var/tmp/m4-churn-policy M4_DEBT_POLICY=1 \
  M4_DEBT_RATIO=0.01 M4_DEBT_CAP=250000 \
  ./build/m4_benchmark --churn 1000000 8 6000
```

The benchmark's `rebuild_on_mutation` mode is **not** production zvec. It is
the same prototype with `CompactNow()` after every flush, used as a controlled
O(collection) baseline. `--churn` keeps live cardinality approximately stable
while deleting and reinserting docs, then updates existing docs; it reports
per-cycle tombstone/run/disk/query-p95/p99 series and the final post-compaction
state. Use a real filesystem (`M4_BENCH_ROOT=/var/tmp/...`) and
`/proc/self/io` for physical-write claims; `/tmp` is tmpfs.

## Non-goals / next steps

- No production migration, no old-format reader, no reindex of user data.
- The M4 index core does not provide general concurrent mutation/read safety.
  The bridge test adds a coordinator lock around complete cross-component
  snapshots; broader concurrent use still needs a read snapshot / writer
  handoff.
- Direct v2 compaction removes the temp-section copy. Its remaining costs are
  the O(vocabulary) term/value dictionary held until the end of each section
  and the single-output-file fsync; both are charged against the budget.
- M4-P2.3/P2.4 add a small typed filter expression over string EQ/NE,
  filename prefix/suffix, typed Uint32 EQ/IN, and ArrayUint32 membership;
  filtered FTS candidates are gated before top-k. Prefix/suffix use a full scan
  of scalar `file_name` dictionary values; no new index or format was added.
  This is not generic SQL LIKE: M4 rejects negative LIKE, matching native's
  current parser rejection. The format has dictionaries per field and is ready
  for multiple fields.
- The optional structural debt policy (default off) cuts 1M mean p95
  186.0→73.8 ms (max 236.6→89.6) and leaves runs at 2, with +12.7% churn
  writes and lower CPU; 500k is 51.8→31.3 ms at +20% writes. It is a GO for
  research, not a production policy.
- The single-field BM25 mutation mismatch is now attributed to different
  statistics lifecycles, but ranking parity is still **not** established. A
  production rollout would also need broader tokenizer/analyzer parity,
  multiple fields, additional numeric/array scalar encodings, background compaction
  scheduling, and a migration/backfill plan. None of that is approved here.
