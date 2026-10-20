# Owner-approved campaign: slot 1 launch refusal and independent control fix

**Bounded campaign stopped at an authorized stop condition. Writer-tail decision: DEFER / INCONCLUSIVE.** One of three slots consumed; **zero native M4 arms started**, no paired/order/stage results. Two slots remain unspent but are not eligible as disguised retries under owner §5. Resource approval is granted, not pending; the blocker is the refused launch and absent prior native-pair evidence. No new readiness observation or performance attempt after the refusal.

## Authority, reconciliation and prerequisites

Current owner authorization summarized in `owner-authorization.md` supersedes the old per-pair approval requirement and old A/B/C closure formulation. Repository `/home/masis/src/zvec-grep`, branch `work/zg-optimization`, execution revision `ac48652`; 6267e69/f6c182e/ac48652 ancestry verified. Unrelated Rust edits preserved. No push.

Approved numeric bounds reconciled exactly: 1 GiB M4 memory, swap 0, CPU two equivalents (`200000 100000`), 128 tasks, per-arm workspace 512 MiB allocated/1 GiB apparent, 1 GiB host RAM/2 GiB disk reserves; max three sequential pairs and one active arm. **Narrower arm watchdog 115 seconds** is intentional: existing helper adds five seconds for service/bootstrap, yielding `RuntimeMaxSec=120`, not the old proposed 125. No source/gate or cap enlargement. GNU pair supervisor TERM at 355 seconds, KILL grace five seconds, nonblocking campaign lock. Observation gap remains 2 seconds. Explicit owner acceptance covers missing hard IO ceiling, sampled workspace/disk, external observer and nonzero host impact.

`pair-1-prerequisites.json` records fresh static verification of full-sync ELF **ef0221f0e6c6b9f19e337e3e04a935a9af7069b2e72f8799cbf3fe37a1212e7f**, all 61 compiler dependencies, six actual libraries, 50 durability evidence entries and retained corrected correctness/fault/recovery prerequisites. Delegated CPU/memory/pids capability probe passed; actual owned-service cap readback remained mandatory before GO. No native measurement implementation/C++ input changed, no rebuild required for this unchanged native measurement executable. Later source correction is Python launch serialization only, not native measurement instrumentation or an index/durability change.

## Exact attempt and outcome

- Job **job-1335348-229**. Exact command, supervisor, timestamps, plan digest, `runner.log` and exit=1 retained in `pair-1-execution/`; `pair-1-job.json` indexes the operation root.
- Raw root: `../m4-search-execution-marathon-20261018/phase-b-cost-model/writer-tail-causal-20261019/m4-contention-campaign-20261020-pair-1/`.
- A valid 60-second ambient observation passed approved capacity/integrity; arm-first preflight likewise allowed exploratory continuation. Historical raw disk/IO FAILs remain FAIL and were **not** the refusal reason.
- `systemd-run` rejected **`CPUQuota=200.000%`**: `Failed to parse CPUQuota= value '200.000%': Invalid argument`.
- Bootstrap never connected; controller's fixed 10-second socket accept timed out. The timeout is a secondary symptom, **not evidence of native worker delay, insufficient memory or host IO causing writer latency**.
- Per-arm original receipt: **LAUNCH_FAILED / CONTROLLER_ERROR / NOT_STARTED / OPERATION_BLOCKED**, `benchmark_started=false`, no cgroup identity, no caps applied. Kernel readback was **not reached** and must not be called PASS for the native attempt. Original outer status remains **OPERATION_FAILED / RUNNER_OR_ANALYSIS_FAILURE**; supplemental diagnosis does not rewrite it.
- Owned unit `m4-contained-b32d2a2fdbab4065.service` retirement/collection verified. Read-only follow-up confirms `LoadState=not-found`, inactive, cgroup absent. Native target `/var/tmp/m4-writer-sync-pair-uflgu82c/root` was never created; its private 0700 parent is retained empty, not deleted. No arm two, no watchdog/native GO, no native workload to kill. No unresolved containment/safety incident.

`pair-1-post-inspection-closure.json` includes a host snapshot explicitly labeled **delayed post-inspection**, not an immediate post-arm measurement or another readiness decision. The exception branch did not emit its normal host-after file; this limitation is retained rather than backfilled with a falsely immediate sample.

## What the new raw evidence actually says

Reproducible reader: `analyze_authorized_attempt.py`; result/input hashes: `pair-1-analysis.json`.

- **1200 valid samples**, actual first-to-last duration **59.951419936 s**, nominal 50 ms.
- Load1 **1.54–2.65**. Unchanged stability diagnostic reported load drift; that raw **STABILITY_GATE_FAIL remains FAIL**, legitimately nonblocking for this approved contention-aware mode. The console warning has an old generic `mode=diagnostic` string; actual pair/context/operation JSON binds `contention-aware`, not a mode override.
- IO PSI full avg10 **4.30–13.81%**. Historical IO threshold 4.46% exceeded in **1193/1200** samples; historical disk reserve 18,219,000,000 B failed in **1200/1200**. Minimum actual free disk **18,041,962,496 B**, above the approved workload+reserve requirement. Historical memory/load comparisons had zero failed samples.
- MemAvailable **7,278,576–7,727,404 KiB**; host OOM-kill delta **0**; host swap-in/out **565/1 pages**. These host counters do not establish M4 resource usage or causality.
- No native timing, writer/reader CPU/PSS/RAM, IO/write amplification, maintenance, freshness, retrieval or snapshot coherence result from this attempt. No p95 comparison, sum of independent p95s, regression or improvement claim.

| Hypothesis | New confidence / decision |
|---|---|
| H1 host/order variability | Ambient variation demonstrated; **DEFER same-binary writer-order/asymmetry** because no native pair exists. |
| H2 native vector | **Unmeasured**. Existing source decomposition remains: setup/index/operations/flush/metadata, not pure HNSW. |
| H3 post-M4 sync/finalization | **Unmeasured**. Full sync/WAL behavior unchanged; no fsync-elision proposal. |
| H4 implementation versus shared contention | **Unresolved**. Host/observer IO varied without native M4; cannot isolate observer effect or assign device traffic to another project. |
| H5 measurement artifacts | **Confirmed startup serializer defect**, with direct stderr and positive post-fix kernel readback below. This is not a diagnosis of historical writer-tail. Clock/stage conclusions are unavailable, not zero-cost. |

First offline analysis used the profile wrapper instead of nested `baseline_result`; the resulting KeyError was corrected before writing the final result. Raw evidence was unchanged and no live capture/guard/benchmark was rerun. This is an offline reader-schema error, not a new campaign attempt or a reclassified guard.

## Independent minimal correction — KEEP control fix, not a latency optimization

`m4_containment_runner.py::service_properties` now emits exact whole-number percentages as **`CPUQuota=200%`** (50% similarly), instead of unsupported `200.000%`. Five added lines/one replaced line; approved quota remains exactly two CPU-equivalents. Non-integral percentage formatting behavior is unchanged and is not claimed generally supported by this correction. No timeout/deadline extended, controller handshake weakened, pressure gate changed or whole harness rewritten.

One new focused regression in the existing tiny-unit suite checks serialized 200%/50%, then uses only a small synthetic Python command with CPU/memory/swap/task/workspace bounds. It reads actual kernel files:

```
cpu.max          200000 100000
memory.max       1073741824
memory.swap.max  0
pids.max         128
```

The fixture exits zero and verifies its owned unit retirement. This proves this control-property form and cap readback, **not** native M4 success, safety fit or performance. The fixture arm is five seconds / service ten seconds, below owner maxima. No performance slot is consumed by these explicitly labeled focused tests; no native benchmark executable ran.

Independent test job **job-1335348-233**: **57 focused tests PASS** (14 tiny containment/lifecycle + 24 contention + 5 shared policy + 7 legacy gate + 1 tiny sequential pair + 6 offline bounds), plus fresh three-file `ty check` PASS. Exact commands/frozen source hashes/logs: `quota-serialization-tests/{test-receipt.json,typecheck-receipt.json}` and raw tiny fixtures. Proactive LSP found no primary type errors; auxiliary blanket unchecked-I/O findings are not advertised as a globally clean audit. No native C++/ABI/schema/index/data/retrieval/durability path changed. No performance/cost reduction claimed; native CPU/RAM/PSS/IO/write/disk/maintenance tradeoffs remain unmeasured.

## Stop/decision and current owner DoD

**KEEP** the exact-integer launch serialization correction and durability-preserving full-sync behavior. **DEFER** writer-tail optimization/root-cause attribution. Do not relaunch slot 1 or manufacture slot 2 as its replacement. Owner §5 requires valid preceding exploratory pair data and a discriminating next question; a new ID solely to get the first intended M4 run would violate that rule. Fixing the control defect does not itself grant retry authority. Maximum budget remains three, consumed one, two conditional/unspent, eligible next pairs zero.

| Current owner DoD | Reconciliation |
|---|---|
| Up to budget or correct stop | Stopped at the first refused launch; no automatic retry, extra arm or fourth pair. |
| Each attempt has receipt | Original operation/per-arm/exit/prerequisite/approval/closure records retained, plus supplemental diagnosis. |
| No unresolved cleanup/safety | Exact failed unit not-found/cgroup absent; synthetic units independently retired. No unrelated workload intervention. |
| Hypotheses evaluated using new evidence | Above table distinguishes valid ambient, confirmed startup artifact and unmeasured native/order effects. |
| Engineering decision | KEEP narrow control fix/full sync; DEFER latency optimization. |
| Research changes verified | 57 focused tests and fresh type check; unchanged native prerequisite identities. |
| No production/other-project modification | Only M4 research/control/tests/results and canonical handoff; unrelated Rust untouched; no push. |
| Next action / owner decision | Explicit new retry/attempt authorization if owner wants a first native pair after this correction; unchanged caps, fresh provenance/capacity and actual cap readback required. No request for increased caps or quieting projects. |

Thus the **bounded campaign is closed at its authorized stop with DEFER**, not a writer-tail success. Substantive latency investigation remains unresolved and decision-dependent. Historical preregistration/checksums and unmodified raw failure remain available; do not regenerate old manifests to disguise changed source/ledger. New attempt/fix receipts are separately checksummed and tied to their source states.
