# E5 — FTS reproducibility: root cause and harness fix

**Status:** DONE (investigation only; no production index, ranking, model policy,
daemon or binary changed)
**Branch:** `work/e5-fts-reproducibility-20261114`
**Base:** `work/e4-embedding-selection-20261113` @ `ca63ebb`
**Main:** `work/zg-recovered` @ `6d5b523` (untouched)
**Worktree:** `/home/masis/src/zvec-grep-e5`
**Receipts:** `/home/masis/.cache/zvec-grep/e5-20261114/receipts/`

## 0. Verdict

| Question | Answer |
|---|---|
| Is native FTS dependent on collection name, document IDs, insertion order, rebuild, batching or optimize state? | **No.** Native FTS is deterministic and model-independent when the fragment table is identical. |
| What caused the E4 FTS spread? | **Benchmark request composition.** The harness set both `ContextOptions.query` and an explicit route. A bare `query` is a *primary* query that always searches **FTS + Vector**; the explicit route is *supplemental*. Every E4 "FTS" request therefore contained an implicit vector route whose candidates and scores depended on the embedding model. |
| Is this an engine defect? | **No.** `normalize_context_request` is explicit and tested: primary `query`/`queries` groups are FTS+Vector; `routes` are searched as given. |
| Fix | **Harness fix** (`query: None` when explicit routes are the request), plus an engine regression test and API documentation. No ranking/storage change. |
| Final verdict | **EXPECTED_BEHAVIOR_WITH_CONTRACT** (engine) + **QUALIFIED_FIX** (benchmark harness). The E4 FTS claim is retracted. |

## 1. Preflight and controlled corpus

Host: 12th Gen Intel i7-12700H, 20 logical CPUs, 15 GiB RAM. Disk kept
above 50 GiB free. Native backend: `zvec-rust`/`zvec-rust-sys` 0.8.0,
`libzvec_c_api.so` SHA-256 `5093c7d722304b0efaa7425f206456ca902dc377d5a0ae8056f1c5c010fe763e`.

To isolate variables the corpus had to produce **byte-identical fragment
tables**: 24 small C# files (70,873 bytes) from the frozen E3-01 corpus,
281 entities, **286 fragments** at 2,048-token chunking, 32 frozen E3 queries.
The following arms were indexed into separate roots:

| Arm | Model reference | Artifact | Context | max batch |
|---|---|---|---:|---:|
| `a` | `local/jina-...-ctx2048` | Jina Q5_K_M | 2048 | 8 |
| `b` | `local/jina-...-alias` | same artifact | 2048 | 8 |
| `a2` | `local/jina-...-ctx2048` | same artifact, rebuilt, files created in reverse order | 2048 | 8 |
| `g16` | `local/embeddinggemma-300m` | Gemma Q8 | 2048 | 16 |
| `g8` | `local/embeddinggemma-300m-b8` | same artifact | 2048 | 8 |
| `np` | `local/embeddinggemma-300m-noprefix` | same artifact, `format: none` (different vectors only) | 2048 | 8 |
| `cr` | `local/coderankembed-q8` | CodeRank Q8 | 2048 | 8 |
| `jn` | `local/jina-...-ctx2048` | Jina Q5_K_M | 2048 | 8 |

Every arm's fragment table was checked column-by-column (`document_id`,
`_zvec_uid_`, `entity_id`, `file_id`, `text`, `symbol_name`, ...). All 10
columns were identical for the arms used in each comparison (`a`/`b`,
`a`/`a2`, `g16`/`g8`, `g8`/`np`, `cr`/`jn`). This was a precondition, not an
assumption.

## 2. Reproduction matrix

| Experiment | Variable | Result |
|---|---|---|
| A. Collection rename | `a` vs `b`: same artifact/layout/vectors, different collection name (`fragments_2dc4` vs `fragments_b1ca`) | FTS **32/32 identical** |
| B. Rebuild + creation order | `a` vs `a2`: same model, fresh root, files created in reverse order; fragment table identical | FTS **32/32 identical** |
| C. Insertion batching | `g16` vs `g8`: same Gemma vectors, max batch 16 vs 8 | FTS **32/32 identical** |
| D. Vector values only | `g8` vs `np`: same text/doc IDs, `format: none` changes only vectors | Corrected pure FTS **32/32 identical**; legacy request **0/32** |
| E. Native FTS | Probe using only `zvec-rust` (no engine code) on all four collections | Exact engine expression and path filter: top-50 **identical**; filtered IDs 0/1/3/4 **identical** |
| F. Vector/FP16 probe | Synthetic schema, 200 docs, 24 upserts, FP32 and FP16, two vector sets | FTS scores **identical** |
| G. Corrected routes | Pure FTS, pure vector, explicit hybrid on all four indexes | FTS **32/32 identical** (top-10 320/320); vector and hybrid differ, as expected |

The E4 divergence was reproduced exactly by running the old harness behavior
with the new fixed engine: `legacy-primary=true` gives `g8` vs `np` **0/32**
identical FTS results.

## 3. Root cause

`ContextOptions` has two distinct inputs:

- `query` / `queries`: **primary** query groups. `normalize_context_request`
  builds each primary group with **two routes** — `Fts` and `Vector`
  (`pipelines/indexed_search/context.rs`).
- `routes`: **supplemental** groups, searched exactly as given.

The E4 benchmark harness passed **both**:

```rust
ContextOptions {
    query: Some(text.to_owned()),   // -> primary FTS+Vector group
    routes,                         // -> supplemental FTS (or Vector) route
    ..
}
```

The engine therefore executed:

- a primary **FTS + Vector** group, plus
- the requested supplemental route.

Trace evidence (temporary `ZG_E5_TRACE` instrumentation, removed before commit):

```
legacy: E5_NORM query=Some("...") routes=[(Fts, "...")]
        E5_RANK routes=2 maximum_rrf=0.032786885
        E5_ROUTE mode=Fts ...
        E5_ROUTE mode=Vector ...
        E5_RANK routes=1 maximum_rrf=0.016393443
fixed:  E5_NORM query=None routes=[(Fts, "...")]
        E5_RANK routes=1 maximum_rrf=0.016393443
        E5_ROUTE mode=Fts ...
```

Classification: **fusion/request composition**, not scoring, ties, candidate
retrieval, lifecycle or identity. The raw native FTS hits (same `document_id`,
same BM25 score, same order) were identical in every arm; the divergence
appeared only after the implicit vector route contributed candidates and the
ranking stage fused them.

The engine behavior is intentional and already covered by
`normalizes_primary_and_supplemental_query_groups_like_main`; the ambiguity was
in the benchmark caller, not the storage engine.

## 4. Fix

### Benchmark harness

`benchmarks/retrieval-evidence/harness/src/main.rs`:

```rust
query: args.legacy_primary.then(|| text.to_owned()),
```

Explicit routes are now the whole request. `--legacy-primary=true` reproduces
the E4 defect for audit; the default is correct.

### Engine

- Documented `ContextOptions.query`/`queries` (primary FTS+Vector) and
  `routes` (supplemental, exact) in `api/context.rs`.
- Added
  `explicit_fts_route_without_query_does_not_add_a_vector_route` in
  `pipelines/indexed_search/context.rs`: an explicit FTS route with
  `query: None` yields one supplemental FTS group and **no** vector route.
- Added `e5_fts_is_independent_of_embedding_values` in
  `storage/fragments/tests.rs`: same text, two different vector sets, FTS
  scores identical before and after `optimize()`.

No production ranking, storage, index schema, model default, selection policy
or binary behavior was changed.

## 5. Verification

```
default features, serial:   761 passed; 0 failed; 27 ignored
e4-experiment, serial:      760 passed; 0 failed; 27 ignored
```

The parallel run has one pre-existing isolation flake
(`config::tests::updates_preserve_other_settings_and_do_not_erase_model_fields`,
`RESOURCE_BUSY` on the global config lock). It passes alone and in the serial
suite; serial runs are the authoritative result.

Independent checks:

- Native `zvec-rust` probe, no engine code: exact expression, top-50 and
  filtered results identical across `g8`, `np`, `cr`, `jn`
  (`receipts/fts/native-*.txt`, `filter-*.txt`).
- Corrected pure FTS: `g8` vs `np`/`cr`/`jn` **32/32 full results identical**,
  top-10 **320/320**.
- Corrected pure vector top-10 overlap: `g8` vs `np` 19/320, vs `cr` 13/320,
  vs `jn` 12/320 — model differences remain, as expected.
- Same-index repeats and same-model rebuilds are bitwise identical.

## 6. E4 impact

The E4 report is **corrected by an erratum** on this branch:

| E4 row | Was it pure? | Status |
|---|---|---|
| FTS R@10 / FTS MRR | **No** — primary FTS+Vector + supplemental FTS | **Retracted.** Native/pure FTS is model-independent. |
| Vector R@10 / MRR / nDCG | **No** — primary FTS+Vector + supplemental Vector | Absolute numbers invalid; the shared FTS component dilutes vector differences, so the direction of the model gaps is preserved but the magnitudes are not pure vector. |
| Hybrid | Closest, but the primary group duplicated the supplemental hybrid routes | Directional only. |

E4 model verdicts (CodeRank/Jina/Potion/BGE **NO_GO**, Gemma provisional
reference) remain **provisional and not promotion-ready**. They were never a
promotion decision, and the corrected FTS result does not reverse them: pure
FTS is common to all models, so the observed quality gaps are still driven by
the vector route. Exact E4 metrics must be re-run with the corrected harness
before being quoted again. No E4 gold, query set, relevance rules or verdict
labels were edited.

## 7. Unresolved risks and contract proposal

1. `ContextOptions` allows `query` and `routes` together. It is intentional,
   but easy to misuse. A future non-breaking guard could reject a request whose
   primary and supplemental route sets overlap, or add a `routes_only` helper.
   Not implemented here.
2. Other benchmark/consumer code may set both fields. Grep for
   `ContextOptions {` with a non-empty `query` and `routes` before reusing the
   E4 numbers.
3. The E4 full-corpus re-run was not repeated: re-indexing six models costs
   hours and E5's mandate is bounded. The E4 quantitative rows are marked
   invalid, not silently replaced.
4. The parallel config-lock test flake is unrelated to E5 and remains a test
   isolation issue.

## 8. Git / artifacts / cleanup

- Branch: `work/e5-fts-reproducibility-20261114`.
- Fix and tests are committed; `cargo fmt --check` passes.
- Receipts: `$E5/receipts/` (`SHA256SUMS`, fixed/legacy matrices, native probe
  outputs, trace logs, probe source).
- External Cargo target, model cache and work roots are removed after the
  receipts and hashes are recorded; the worktree is removed only after the
  branch is pushed and the remote SHA is verified.
- Main worktree and all production indexes/binaries are untouched.

**No model or routing default was promoted.**
