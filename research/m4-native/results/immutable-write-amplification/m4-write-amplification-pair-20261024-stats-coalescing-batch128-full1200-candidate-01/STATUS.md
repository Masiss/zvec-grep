# Operation status — partial, no paired result

Operation ID: `m4-write-amplification-pair-20261024-stats-coalescing-batch128-full1200-candidate-01`

Profile: 1,200 files, ApplyBatch chunk 128, LZ4 off in both arms, stats-event coalescing off/control vs on/candidate; candidate-first order. Both bindings passed `VALIDATED_NO_LAUNCH` under attested qgroup `0/259` with the existing 128 MiB cap.

- Candidate arm (coalescing on) exited 0; containment receipt `PASS` and cleanup verified.
- Control arm (coalescing off) exited 2 during burst-3 flush; containment receipt `NONZERO_EXIT`, cleanup verified. Captured stderr says only `FATAL M4 burst flush`; the harness checked `m4index->Flush().ok` without recording `Status.message`, so the underlying cause is unknown.
- No quota/resource refusal or correctness mismatch is recorded in the runner evidence. Workspace capacity stayed within the attested hard bound; this does not explain the native flush error.
- The counterorder is incomplete. Do not pool it with the separately completed control-first root or infer a paired performance result. No retry: this operation identity has been consumed, and the owner instruction forbids retrying consumed operations.

The raw partial root and all arm receipts/logs are preserved here; `ARCHIVE-MANIFEST.sha256` verifies the archive. This is failure evidence, not a completed benchmark report. The flush-status diagnostic gap is a harness limitation; it does not establish the M4 failure's root cause.
