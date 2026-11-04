# Phase 3 native schema-lock attribution — authorized plan and execution record (2026-10-30)

## Objective and current evidence

Close the remaining **native query lock-owner attribution** gap, not qualify performance. The last native-only trace reproduced five direct collection-query calls over 100 ms: four were dominated by waiting to acquire `schema_handle_mtx_` (111.697–1,243.589 ms), while a fifth retained a 151.856 ms gap before `query_validate`. FTS filter/search/fetch spans were each below 1 ms. The old trace did not identify the exclusive lock owner and some unrelated trace lines were interleaved in stderr. No causal claim about flush is established.

The owner authorized **up to three fresh native-only diagnostic runs**, sequentially, with investigation and retained evidence after each. This is a run budget, not a requirement to spend all three. Stop as soon as the attribution question is answered; do not infer that this makes Phase 3 performance-qualified or production-ready.

## Exact authorization and scope

The owner originally authorized these one-use IDs in `docs/benchmark-policy.json`:

1. `m4-rust-lifecycle-pair-20261030t011938z-nquery-locktrace-r1` — attempted as a fresh no-launch preflight and **consumed by artifact-provenance refusal**.
2. `m4-rust-lifecycle-pair-20261030t011938z-nquery-locktrace-r2` — not used.
3. `m4-rust-lifecycle-pair-20261030t011938z-nquery-locktrace-r3` — not used.

The approved stop-on-gate condition closes the batch after the ID-1 refusal; there are now no active IDs. See [the retained refusal report](M4-PHASE3-NATIVE-SCHEMA-LOCK-TRACE-PREFLIGHT-REFUSAL-20261030.md). A new owner instruction is required before any further trace. No retry, reserve reduction, guessed cleanup, or cap increase. The originally approved scope was only the existing 64-file **native arm** under the existing containment/resource policy. No M4 arm, pair, p95 comparison, performance claim, quota/cap change, artifact deletion/movement, native checkout modification, ABI/schema change, or production action was authorized.

## Instrumentation and artifact plan

- Build a native-library overlay from copies of `.local/native/zvec-native-profile/src/db/collection.cc` and `src/db/common/native_timeline.h`. The source checkout and existing CMake build tree are read-only inputs. The builder and focused tests are `tools/build_native_schema_lock_trace.py` and `tools/test_build_native_schema_lock_trace.py`.
- Add wait and held spans for all 13 shared and 13 exclusive `schema_handle_mtx_` sites, preserving each existing lock type and scope. Retain the existing flush-specific spans. Split `close_internal`'s condition-variable wait from its actual lock-held portions.
- Cover the SearchQuery path from lock acquisition through state checks and `query_unsafe` setup to `query_validate`, so the former pre-validation gap is measurable.
- Write each native profile event in one `write(2)` to a fresh `O_EXCL` file, `pair-first/containment/native-profile.jsonl`, instead of mixing native records with Rust stderr. The runner binds that path to the exact operation receipt directory, validates the file and retains byte count, valid/malformed line counts, and SHA-256. A tiny logger smoke test runs during the offline build; it does not execute the lifecycle workload.
- Compile copied `collection.cc` and every translation unit that includes `native_timeline.h` (currently `collection.cc` and `db/sqlengine/planner/fts_recall_node.cc`) against the same overlay header. Replace both corresponding members in a copied `libzvec.a`, then link a new `libzvec_c_api.so` into the ignored research build target. A library is unusable unless the final linked image contains the trace-path, lock-owner, and query-gap markers. Do not run CMake in a way that rewrites `.local/native/zvec-native-build` or its source checkout.
- Pin source/overlay/object/archive/library hashes and the exact lock-site inventory in `results/native-query-timeline-20261029/native-schema-lock-trace-build-v4.json` before any operation preflight.

## Per-run procedure

### Run 1 — complete owner timeline

1. Recheck the live workspace qgroup/attestation, disk reserve, and host/resource state immediately before the preflight. Refresh only the root attestation if stale; do not change the live qgroup cap. Preserve the snapshot.
2. Run `--native-only-diagnostic --validate-only` once with ID 1 and explicit binary/library paths plus expected SHA-256 values. Do not launch unless it passes every artifact, qgroup, disk, host, context, and containment check.
3. If it passes, launch the single native workload using the instrumented library. Capture existing containment telemetry plus before/after host and process state.
4. Preserve the operation root, trace, stdout/stderr, runner receipt, post-cleanup qgroup evidence, and hashes. Stop after this one run; join each query's existing direct-query span to shared-lock wait/hold events and overlapping exclusive-owner spans by collection/object ID and monotonic time. Inspect every >100 ms call and the full pre-validation interval. Check trace completeness, host pressure, workspace-accounting caveats, process tree, and safety/cleanup separately.
5. Write a run-1 evidence report and update `ACTIVE-EXECUTION.md` before any decision to use ID 2.

### Run 2 — evidence-directed follow-up (only if still needed)

Choose exactly one bounded follow-up based on run-1 evidence: either repeat the same instrumentation to confirm a specific owner/outlier, or rebuild a narrower overlay adding nested spans only inside the identified owner path / remaining untraced query setup. Keep the workload, arm count, resource controls and hard gates unchanged. Pin the new library hash and run the same fresh-preflight/one-use-ID procedure. Preserve a complete report before ID 3 is considered.

### Run 3 — final attribution check (only if still needed)

Use the remaining ID only for the narrow question left after run 2: repeat a targeted owner trace, or validate a small research-only mitigation **only if** the evidence identifies a correctness-preserving candidate and focused tests pass first. Do not make speculative locking changes. Preserve the same evidence and hard-gate procedure. If attribution remains ambiguous after three runs, report it as inconclusive and stop; do not extend the budget implicitly.

## Evidence acceptance and stop conditions

Attribution is considered resolved only if the long query intervals are reproduced and joined to intact lock-owner wait/hold records or an explicitly identified non-lock substage, with the pre-validation interval covered and trace line integrity verified. A lock overlap alone is correlation; name a causal owner only when its exclusive/shared ownership interval accounts for the query's blocked interval and the caller/thread/object identity agrees. Otherwise state the narrower observed fact and keep the root cause inconclusive.

Any qgroup/disk/host/containment refusal stops that operation; preserve the original refusal and do not bypass it. Missing/malformed trace, incomplete hard telemetry, unverified cleanup, or workspace-accounting uncertainty must be reported as such, not converted to PASS. Even a successful attribution run is not an M4-vs-native performance result and does not change the separate Phase 3 or production decision.

## Execution status and refusal record

- The v4 isolated library build completed. It recompiles both translation units that include the timeline header and verifies the final shared object. Manifest: `native-query-timeline-20261029/native-schema-lock-trace-build-v4.json`; library SHA-256: `65817813bacc710b29ea01d273ca604c0e1177b1e5aabf618604fe45c701a971`. Its subsequent one authorized lifecycle use and findings are documented in [the result report](M4-PHASE3-NATIVE-SCHEMA-LOCK-TRACE-RESULT-20261030.md).
- A first CLI invocation failed argument parsing (`--expected-binary-sha256` requires `--binary-path`); its stdout capture is empty and no preflight ran. The corrected `--validate-only` attempt then refused the retained `zg_engine-552d...-nodebug` binary because its normalized ELF headers/program headers and all four PT_LOAD segments differ from the current original. The measurement copy predates the original by several hours and is stale. Exact hashes, timestamps, refusal output, and no-workload evidence are in [the refusal report](M4-PHASE3-NATIVE-SCHEMA-LOCK-TRACE-PREFLIGHT-REFUSAL-20261030.md). No operation root, artifact rebuild/copy, or workload was created.
- Immediately before the refusal, live qgroup `0/259` headroom was 113,934,336 B and disk available was 18,443,689,984 B; the root attestation was refreshed. These values were not adjudicated by the preflight because artifact identity failed first. No quota or cap was changed.
- Focused verification completed before this refusal: builder 3/3, lifecycle runner 24/24, benchmark policy 34/34, focused containment authorization/environment 3/3; Python compilation and JSON parsing passed. A standalone header smoke produced one intact 192-byte event. The separate broad contention-policy test has one unavailable pinned-binary fixture error and two cadence assertion failures outside this change; no unrelated fixture or test was changed.
- Build history: v1 failed before native object compilation because its smoke fixture emitted escaped line breaks. V2 passed the trace-file smoke but stopped on a build-directory collision. V3 compiled/linked but its final library lacked the logger path because an archive member with the inline header could win ODR/COMDAT selection; it is withheld. V4 corrected this by rebuilding both header-including translation units. All prior work roots/logs remain retained; no qgroup cleanup occurred.

**Status at closure of the original batch:** OPERATION_BLOCKED / ID-1 PREFLIGHT REFUSED / NO LIFECYCLE WORKLOAD. A later fresh owner instruction authorized and consumed exactly one new attempt; see [the reauthorization addendum](M4-PHASE3-NATIVE-SCHEMA-LOCK-TRACE-REAUTH-20261030.md), [the result report](M4-PHASE3-NATIVE-SCHEMA-LOCK-TRACE-RESULT-20261030.md), and the active checkpoint/policy for current status. Phase 3 remains PARTIAL.
