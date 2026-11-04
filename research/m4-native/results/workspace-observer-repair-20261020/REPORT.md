# Workspace observer correction — 2026-10-21

**Research correction verified; native readiness remains PARTIAL / OPERATION_BLOCKED.** No native A/A was run. Campaign remains **4/4 attempts consumed, zero remaining**. No campaign request, production/native changes, quota enablement, other-project action or push.

## Root cause and evidence

Canonical handoff §58 and `../autonomous-campaign-20261020/REPORT-FINAL-PAIR-3.md` retain the original observation: `fts/tombstones-2-1.bin.tmp.139597` disappeared between enumeration and stat, approximately one second after native GO. `AtomicWrite` in `src/index.cc` creates, fsyncs and renames/unlinks temporary files. This is consistent with normal publication; the exact actor/syscall was not traced. Historical raw failure, process-oriented COMPLETE label, partial counters, ledger and binary provenance remain unchanged. They do not become valid workspace measurements retrospectively.

The old `os.walk` scanner treated every stat error alike. It also could miss creates, growth or replacement without any ENOENT. A successful recursive walk is not an atomic inventory. Summing different instants can miss new files or count both sides of a rename; even a sampled aggregate breach need not prove simultaneous usage. Quiet post-stop traversal cannot repair the live observation.

## Accounting decision and filesystem investigation

The affected roots (`/var/tmp` and project-local scratch) are on shared Btrfs, not a dedicated per-workload filesystem. No correctly scoped quota/accounting backend for both approved allocated and apparent-byte limits has been established. The `btrfs` CLI is unavailable; observed sysfs exposes global allocation, not a verified per-workspace bound. Absence of an exposed qgroups directory is **not** proof that quotas are globally disabled. No privileged filesystem change was made.

| Evidence | What it establishes / does not establish |
| --- | --- |
| `st_size` | Per-inode apparent length at stat time, not total concurrent/lifetime apparent usage. Sparse files can have huge length and little allocation. |
| `st_blocks*512` | Sampled gross allocated bytes, hardlinks deduplicated. Reflink/shared Btrfs extents can be counted multiple times; not exclusive physical allocation. |
| `statvfs` / host free disk | Global headroom, not workload attribution, logical size or either workspace bound. |
| cgroup memory / process IO / cgroup IO | Different scopes/semantics; writes may lag or be overwritten, sparse truncate need not write, exited processes disappear. Not workspace-capacity upper bounds. |
| Btrfs qgroups | Subvolume referenced/exclusive extent accounting, not an ordinary directory's logical `st_size` limit. Would require verified scope, accounting health and separate apparent-size evidence; not deployed here. |
| inotify | Bounded mutation warning channel only. Nonrecursive watches, queue loss/coalescing, mmap/writeback limitations and cross-pass gaps prevent capacity certification. |

Consequently there is **no stable conservative upper bound for both counters available here without additional independently validated accounting**. No synthetic headroom formula, external JSON assertion, IO delta or event count is accepted as proof. A quota/backend design is remaining work, not silently implemented or authorized filesystem administration.

References checked: [Linux inotify(7)](https://man7.org/linux/man-pages/man7/inotify.7.html), especially limitations on mmap, recursive coverage and queue overflow; [Btrfs qgroups](https://btrfs.readthedocs.io/en/latest/Qgroups.html), referenced/exclusive subvolume accounting. These explain limitations, not evidence that a host quota is configured.

## Implemented correction

`tools/m4_containment_runner.py` now uses a single descriptor-relative, no-follow streaming walk. Directory identity/metadata checks plus bounded per-pass inotify detect known creates, rename/unlink, replacement, directory loss and ordinary growth during observation. Real errno details remain in bounded issue examples. EIO, permission, scope loss, missing root, watch failure and queue loss are not relabelled benign races.

Executable observer limits are in `docs/benchmark-policy.json`: one pass, 16,384 entries, depth 64, 32 issue examples, one 65,536-byte event drain and 0.25s cooperative deadline. There is **no retry or rescan**. Memory/watch/FD work is bounded by entries/depth. The cooperative deadline cannot preempt an individual blocked kernel filesystem syscall; independent watchdog/systemd containment remains necessary. No benchmark of scanner overhead or native cadence is claimed.

States are explicit:

- `COMPLETE_TRAVERSAL_NOT_ATOMIC`: observed traversal only, **never** `complete_snapshot=true` or independent proof. Existing sampled-abort policy may continue as `SAMPLED_NOT_EXCEEDED`; this does not certify true capacity.
- `PARTIAL_KNOWN_RACE`: incomplete observation with a known mutation; does not imply a filesystem fault or safe capacity.
- `UNVERIFIABLE` / `CAPACITY_UNVERIFIABLE`: incomplete/scope/error/budget/coverage loss. With no independent proof backend, stop the affected arm.
- `OBSERVED_LIMIT_EXCEEDED`: retain counter, threshold and reason; abort. Label deliberately does not overclaim a simultaneous aggregate proof. The real single-file over-cap fixture establishes an actual violation and is refused before GO.
- Conservative upper bound and verified lifetime peak remain **null/unavailable**, distinct from all sampled states. There is no fabricated PROVEN state or implemented complete-snapshot backend.

All runtime and postflight consumers use `workspace_capacity_decision`; incomplete workspace sampling makes new receipts' measurement quality INCOMPLETE. The retained A/A controller's file-only contention analysis also uses the same zero reserve-credit helper instead of passing raw gross allocated bytes. Existing error-propagating JSON helpers are reused at twelve unchecked parse sites to address diagnostics without adding fallback, retry or recovery logic. These current source changes do not rebind historical rehearsal/native receipts. Partial totals never update sampled workspace peaks. A later partial scan invalidates the whole workspace sampling series for completeness; earlier sampled values remain only explicitly sampled information. `workspace_verified_peak` stays null. Historical receipts are not rewritten.

`workspace_reservation_credit` is always zero: gross/time-skewed samples cannot subtract physical workspace allocation from the approved shared-disk reserve requirement. This strengthens uncertainty handling without changing any cap or reserve threshold. CPU/memory/swap/tasks, native durability/WAL/full-sync binary, independent watchdog, runtime/cleanup and qualification authority remain unchanged. Auxiliary JSON helpers propagate malformed-input errors without fallback; capacity counters reject nonfinite values, booleans and invalid types. Effective-access probing uses one shared helper. No new capacity-proof override exists.

## Verification

Fresh retained execution: **job-8740-32**, `verification-v3/test-receipt.json`, all six checks PASS, source hashes unchanged during checks:

| Suite | Tests |
| --- | ---: |
| New workspace observer fixtures | 19 |
| Existing containment/lifecycle | 15 |
| Existing contention policy | 25 |
| Existing benchmark policy | 5 |
| Existing A/A gate policy | 7 |
| Existing synthetic contained pair integration | 1 |
| **Total** | **72** |

Deterministic real-filesystem interleavings exercise atomic create/rename, temp unlink, replacement without ENOENT, directory removal, post-enumeration create, file growth, sparse/apparent divergence, concurrent near-cap allocation, EIO and permission errors. Additional cases cover missing/replaced roots, symlink non-following, entry/depth/deadline exhaustion, event overflow/truncation, malformed/nonfinite counters, malformed JSON and forged upper-bound assertions. Interleavings are controlled fixtures, not randomized native stress or performance evidence.

Actual tiny Python/systemd fixtures show permission-induced unverifiable capacity abort with INCOMPLETE measurement and null peaks, plus a 65,536-byte file against unchanged test-local 4,096-byte caps refused before GO. Final unit retirement is verified. Existing lifecycle fixtures retain **exit 7, SIGTERM -15/signal15, SIGKILL -9/signal9**, normal exits, watchdog timeout and crash cleanup checks. Existing pair test launches a synthetic Python stand-in, not native M4; its recovery windows are synthetic. Tests explicitly disable host admission only in existing fixture mode; this is not native guard admission or capacity proof.

`verification-v2` is retained as a failed **test expectation**, not a safety failure to bypass: the pre-GO cap guard correctly aborted/cleaned up, but `run_arm` raises after writing LAUNCH_FAILED receipt and the test expected a return. Only the assertion was corrected before v3. The raw early controller cleanup flag can be false before later verified unit retirement; neither receipt field is rewritten or hidden. Deliberately mocked negative lifecycle fixtures include CLEANUP_FAILED and must not be mistaken for real leaked units. Closure query shows no owned `m4-contained-*` units.

Active two-path LSP: **0 errors**, 2 unreachable-code hints, 30 auxiliary numeric-parse warnings; not a warning-free claim. New file-only analyzer also checked after diagnostic fixes. The A/A consumer edit exposed pre-existing unchecked-JSON diagnostics; initial six findings were deferred, then all twelve exposed sites were aligned with existing error-propagating JSON helpers, without catch-and-continue fallback or expanded harness recovery. Final active controller probe returned zero diagnostics but **inconclusive clean confirmation**, not a verified-clean claim. A subsequent direct `ty check` over all four changed Python files (runner, observer tests, A/A controller and file-only analyzer) **PASS**, with exit0 and `final-ty.log` retained independently. Its existing seven A/A gate tests and one synthetic contained-pair test were rerun on the final pinned source; exact commands, unchanged source hash and results are in `consumer-final/test-receipt.json`. Intermediate `post-consumer-*` checks are retained separately; repeated tests do not increase the 72 distinct tests. `verification-summary.json` records tests, raw receipt dispositions, host filesystem observation, closure and ledger hash. Replay: `PYTHONDONTWRITEBYTECODE=1 python3 research/m4-native/results/workspace-observer-repair-20261020/analyze_verification.py`. Exact test commands are in the v3 receipt. Earlier focused/v2 logs are retained; v3 alone is the fresh final six-suite run.

## Acceptance and next action

Completed: root-cause/consumer/accounting investigation, bounded research source correction, deterministic and real tiny containment coverage, retained raw failures, explicit uncertainty/capacity decisions, policy explanation, RESULTS/handoff and research-only commit. No native attempt or push.

**Native launch path is NOT ready for a separately authorized campaign.** The original race is now classified honestly, not made safe: normal publication still fails closed when no independent capacity proof exists. The remaining recovery condition is a separately scoped/validated accounting design that conservatively covers **both** approved capacity counters throughout the race, then focused regression verification. No retry-until-green, recursive rescanning, quota deployment or new campaign is implied. Even after that engineering blocker is resolved, native execution would require fresh explicit authority; current budget remains exhausted. No writer-tail latency, peak-capacity, optimization or qualification conclusion is available.
