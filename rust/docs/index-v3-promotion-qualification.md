# Index v3 promotion qualification — retrieval and efficiency

Base `928f49f`. Isolated worktree, external target, mock embedding server.
This note freezes the corpora, gates and results used to decide whether the
C# structural index is promotable. No held-out data, RAG track, Selection or
Expansion was touched.

Receipts: `~/.cache/zvec-grep/out-csharp/index-v3/receipts/`
(`quality-v3*`, `mixed-v3final2*`, `dense-v3final2*`, `duplicate-v3final2*`,
`cjk-*`, `mixed-noscalar-*`, `*.time`).

## 1. Corpora and ground truth

- **mixed**: 300 C# + 75 Rust + 75 Python + 75 Markdown, unique type/property
  names; lexical and symbol navigation.
- **dense**: 300 C# files × (30 properties + 10 methods), high symbol density.
- **duplicate**: 500 C# files with the same `Widget`/`Run`/`Value` declarations.
- **non-C#**: Rust/Python/Markdown only.
- **quality**: 10 topics × (8 C# classes + 2 Markdown + 1 Rust + 1 Python).
  A topic query's relevant set is the 12 files generated for that topic;
  `quality_truth.json` is written by the harness. This is synthetic and uses a
  mock embedding server; it measures relative retrieval behavior, not semantic
  model quality.
- **CJK/adversarial**: Chinese notes for tokenizer checks; CRLF/Unicode/generic/
  operator/indexer fixtures in unit tests.

Metrics: Recall@10/50 against the synthetic relevant set, MRR@10, binary
nDCG@10, relevant-file coverage, duplicate-context@K (items minus distinct
paths), no-answer false positives. Resource metrics: full/incremental wall,
user CPU, peak RSS/PSS, allocated disk, FS outputs.

## 2. Gates

1. No-answer multi-term query returns no documents (not an arbitrary top-k).
2. A query mixing one known and one unknown term still recalls the known term.
3. Quality R@10/R@50/MRR/nDCG must not fall below baseline by more than 2 pp;
   relevant-file coverage must not fall.
4. Exact-symbol behavior, source ranges, freshness and pagination unchanged.
5. C# scope removal (if applied): namespace/class queries still return the
   relevant files; mixed top-k same-file crowding falls; index does not grow.
6. Non-C# corpus behavior and bytes unchanged except the global query fix.
7. Resource changes must be measured; no index-format change without a separate
   design.

## 3. Root cause: multi-term FTS returned arbitrary documents

`Fragments::search_fts` used zvec `Fts::set_match_string`. With two or more
terms and no real match, zvec returns top-k documents anyway. Reproductions on
the quality index:

| Query | old match_string | new explicit OR |
|---|---:|---:|
| `zzzz qqqq` | 50 docs / 42 files | 0 |
| `Type0 zzzz` (mixed) | 10 arbitrary | Type0 files |
| `access control` (quality) | 50 / 42 | 10 / 10 topic files |
| `发票对账` (CJK) | 1 | 1 |
| `不存在的词语` (CJK) | 0 | 0 |

`match_string` + OR params preserves the fallback; `match_string` + AND gives
precision but loses OR recall (`access control` → 0 on a document set where both
terms occur separately). The fix builds a quoted `A OR B` **query_string** from
alphanumeric identifier terms, splits punctuation (colon/slash/hyphen), keeps
`_`, `#`, `+`, filters boolean stopwords `and/or/not`, caps at 64 terms, and
escapes quotes/backslashes. This is a query-only change: no index rebuild is
required.

## 4. Quality root cause: C# scope metadata crowded top-k

FTS and vector text prepend `scope` to every declaration. A topic query matched
all members of a class because each member carried `scope: Namespace::Class`.
On the mixed `Type0` query, v3 returned 12 items from one file at @50; v2
returned 1 chunk/file; scope-free C# metadata returns 6 items from that file
(class + initializer-bearing property) and still all 4 relevant files.

Applied: omit `scope` from **C#** FTS and vector metadata only. `CodeMetadata`
still carries scope for output and exact-symbol filters; class/namespace
entities retain their own symbol names/scopes. Non-C# languages are unchanged.

Measured quality (10 topics × 3 runs):
- R@10 0.833 = the maximum possible for 12 relevant files in 10 slots;
- R@50 1.0 FTS/hybrid, 0.983 vector;
- MRR@10 1.0, nDCG@10 1.0;
- mixed `Type0` @50: 12 items/8 same-file → 6 items/2 same-file, 4 files;
- mixed index 15.51 → 15.26 MB apparent (−1.6%);
- dense index 55.78 → 54.42 MB (−2.4%);
- namespace/class queries still hit the relevant files (8/8 for the quality
  namespace query).

Trade-off: a C# scope string no longer matches member entities directly; the
class/namespace declaration still represents that scope. This is a deliberate
retrieval semantic change, not a silent recall loss.

## 5. Efficiency analysis (no unsafe promotion)

Full dense index: `index_embedding` 31.8 s summed over batches (5.6 s wall),
`index_optimize` 1.93 s. One-file incremental mixed: optimize 0.99 s of 1.54 s.
Vector-per-declaration accounts for 25.8 MB of 54.4 MB dense allocated disk;
container class vectors are only ~1.1% (dense) to ~8.5% (mixed). Skipping
container embeddings is therefore not a meaningful lightweight change.

Prototype: skip scalar `directories/entities/files` optimize on incremental,
keep fragment optimize. Incremental mixed 1.66 → 1.39 s (−16%), FS outputs
128,280 → 85,552 (−33%), query latency unchanged within noise. This leaves
scalar optimize debt/fragmentation and needs a periodic-maintenance policy, so
it is **DEFERRED**, not committed.

Selective/leaf-only embeddings or a nullable-vector schema would materially cut
dense disk/CPU but require an index-format/schema change and a recall
qualification; **DEFERRED design**.

Batching already fills the model `maxBatchSize` (10 for the mock/Qwen config)
across files with concurrency; no safe batching gain was found.

## 6. Final results

| Corpus | v2 allocated | v3 final allocated | v2 full | v3 final full | v3 peak RSS |
|---|---:|---:|---:|---:|---:|
| mixed | 8.93 MB | 17.37 MB | 2.45 s | 2.82 s | 344 MB |
| dense | 8.56 MB | 54.15 MB | 2.09 s | 6.59 s | 447 MB |
| duplicate | 8.21 MB | 12.56 MB | 2.27 s | 3.11 s | 328 MB |
| quality | n/a | 5.75 MB | n/a | 1.59 s | 315 MB |
| non-C# | 5.479 MB | 5.479 MB | 1.72 s | 1.87 s | 311 MB |

Query latency (warm p50): mixed FTS 129 ms / vector 138 / hybrid 140;
dense FTS 187 / vector 160 / hybrid 133; quality FTS ~135–220 / vector
~130–205 / hybrid ~134–248. The FTS query change did not add measurable
latency. Exact-symbol cursor/paging results are unchanged from the deep audit.

No-answer tests: `zzzz qqqq` → 0, `---` → 0, `不存在的词语` → 0; partial
`Type0 zzzz` → Type0 files; CJK `发票对账` → the Chinese note. `prefer-symbol`
expected-path probes remain hit.

## 6b. Verification

- `cargo test -p zg-engine --tests`: lib 745 passed / 24 ignored; integration
  binaries 8+1+4+17+8+1+2+2+3+2 passed, 0 failed (includes FTS and C# coverage).
- `zg-cli`, `zg-daemon-protocol`, `zg-transport-mcp`: 180+5+7+60 passed.
- `cargo check --workspace --all-targets`, release `zg` build and
  `cargo fmt --check` pass. New code has no clippy warnings; the workspace
  `-D warnings` run still has pre-existing `extraction/candidate.rs` lints.
- FTS regression tests include no-answer multi-term, partial known/unknown,
  punctuation, CJK, boolean stopwords and scope-aware C# metadata.

## 7. Dispositions and verdict

- Multi-term FTS fallback: **FIXED** (query-only, no reindex).
- C# scope crowding: **FIXED** (C# FTS/vector metadata; rebuild for clean
  index, no correctness dependency).
- Scalar optimize deferral: **DEFERRED** (needs maintenance policy).
- Selective/leaf embeddings: **DEFERRED** (format/schema + recall qualification).
- Exact-symbol O(candidates)/coverage scan: **ACCEPTED LIMITATION** (bounded,
  complete, cursor-safe).
- Dense disk/CPU: **ACCEPTED TRADE-OFF** for structural per-declaration vectors;
  not promotable as “lightweight” without a separate index-format track.
- CLI/MCP/daemon symbols surface: **DEFERRED**.

**CONDITIONAL GO** for Index v3 with the FTS/scope fixes, provided the owner
accepts dense disk/CPU and the deferred interface/efficiency tracks. The FTS
fix is a blocker-grade correctness improvement; it should be promoted regardless
of the C# scope decision. The scope change is supported by quality/resource
gates and should be reindexed before use.
