# M1 — Bounded streaming indexing (plan)

## Milestone goal
Heavy, stable indexing under continuous coding-agent edits; avoid whole-file/vector accumulation and unbounded storage.

## Scope
- `pipelines/indexing/pipeline.rs`
- `pipelines/indexing/storage.rs`
- `storage/store/mod.rs`, `storage/staging.rs`
- extractor/storage/native adapter touched as needed

## M1.1 Refactor pipeline ownership
1. Producer emits batches with controlled byte budget.
2. Embedding consumer consumes within existing concurrency budget.
3. Completed outcomes forwarded/freed early.
4. Naive full-file futures not created.
5. Exact ordering/identity even with out-of-order batch completion.
6. Byte budget across pending/in-flight/completed; not just futures.

## M1.2 Storage staging
1. Small-file memory fast path.
2. Large-file bounded staging.
3. Disk-backed staging quota + cleanup.
4. staging not published until atomic publish.
5. Validate snapshot/model/schema.
6. Cancel/failure doesn't publish incomplete.
7. crash recovery/retry idempotent.
8. No write lock during embedding.

## M1.3 Streaming extraction
1. Plain text/log stream chunk when semantics allow.
2. AST/parser fallback respects admission policy; no claiming bounded if full materialize.

## DoD evidence
- No all-file vector accumulation on streaming path.
- Native encode/write batches instead of `Vec<Doc>` accumulation.
- Bounded intermediate memory.
- Regression: large file, concurrency, cancellation, failure, restart, partial staging, FTS/vector/filter.
- Build/tests PASS.

## Status
M1 implementation and correctness gates are complete; evidence and limits are recorded in [`m1-handoff.md`](m1-handoff.md). Native collections still do not offer a cross-collection transaction, so the implementation uses bounded staging, a `NotIndexed` visibility barrier, and idempotent retry rather than claiming atomic publication. The original pre-implementation micro-commit list above is superseded by that handoff.
