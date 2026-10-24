# Mutation chunk 128 vs256 at 1,200 files — blocked before workload

## Intended comparison

The new policy profile held both WAL-LZ4 and stats-event coalescing off, used the same durable `m4-batch` arm, delta and run-cap settings, and enabled symmetric per-call mutation tracing. Only the ApplyBatch chunk differed (control 128, candidate 256). Both `control-first-01` and `candidate-first-01` passed `VALIDATED_NO_LAUNCH` under the existing 128 MiB cap for Btrfs qgroup `0/259`; the initial binary was SHA-256 `8501350eb9c4f10a60873d1138c1a784bc70795a520fe2cf5d10b63c9c9a0730` and policy was `c914a8053dd16b1cb727ca02d98b4fd18d07a7abaa7f2b741799151f031f0f38`.

## Failure and root cause

The control-first operation failed in its first arm during native index initialization, before workload ingestion: `JiebaTokenizer could not write immutable dictionary snapshot` (exit 2). No candidate arm ran and there is no performance result. The raw root, including host snapshots, watchdog/containment receipts and logs, is preserved in [the operation status directory](m4-write-amplification-pair-20261024-mutation-batch-128-vs-256-full1200-control-01/STATUS.md); its archive manifest verifies.

The original benchmark process omitted the OS error from the native `Status`, so `AcquireDictionarySnapshot` was changed to report the attempted path and OS error. A focused `m4_native_jieba_compatibility` test then reproduced the failure when `TMPDIR` was placed inside qgroup `0/259`: the write to `.../m4-analyzer-snapshot-cache-.../jieba.dict.utf8` returned **`Disk quota exceeded`**. The same rebuilt test passes with machine-default `/tmp` (1/1). `m4_correctness` also passes with TMPDIR inside the quota workspace (1/1); it does not replace the focused Jieba diagnostic. This isolates the failure to insufficient write headroom in the quota-backed temp workspace, not the tokenizer algorithm. The hard cap remains 134,217,728 bytes; live qgroup usage could not be read unprivileged, so the exact remaining bytes are unknown. Unprivileged `btrfs subvolume list -s /` and `-d /` both fail with `Operation not permitted`; snapshot and deleted-subvolume state is therefore unknown and untouched.

## Decision and recovery boundary

**OPERATION_BLOCKED / no paired measurement.** The original control-first identity is consumed and not retried; the candidate-first identity was not launched. Do not move benchmark scratch to uncapped PWD, raise the qgroup cap, or remove snapshots to create space. Any future benchmark requires headroom restored under the same authorized cap or a separately provisioned capped workspace, followed by fresh policy validation and new operation identities. No default/production change.

## Verification

- `m4_research`, `m4_correctness`, and `native_m4_paired_benchmark` build after the diagnostic change.
- `m4_correctness` CTest passes 1/1 with TMPDIR under qgroup `0/259`.
- `m4_native_jieba_compatibility` passes 1/1 with default `/tmp`; its run with TMPDIR under qgroup `0/259` fails with the captured `Disk quota exceeded` error.
- Paired-profile tests 14/14; benchmark-policy tests 30/30; Pyright 0/0/0; Ty passes; live LSP workspace diagnostics are empty for both changed Python files.
