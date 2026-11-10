# Architecture feasibility review — E3 representation decoupling, E1 maintenance

Status: **review and design only**. No implementation, no schema or policy change,
nothing merged or pushed. Main stays `9444eed`; this document is committed on
`work/foundation-opt-20261110` (HEAD before this doc: `4d8caf0`).

Correction to the earlier summary: the foundation branch HEAD is `4d8caf0`, not
`a9f643a` (`a9f643a` is the driver-script commit).

Evidence is source plus bounded zvec probes run in an isolated scratch crate
(`~/.cache/zvec-grep/out-foundation-opt/probe`, external target, no repo
changes). Probe data used the raw zvec API with synthetic corpora, not the
engine, and FP32 where the engine stores FP16; conclusions that depend on the
engine itself are marked UNKNOWN.

---

## 1. Source evidence

### 1.1 E3 — one document per fragment carries text, scalars and a vector

| Fact | Location |
|---|---|
| One collection per embedding model holds FTS text, scalars, FP16 vector + HNSW | `storage/fragments/mod.rs:59,358,382,395-423` |
| `text` and `embedding` are both non-nullable | `storage/fragments/mod.rs:398,411` |
| Every canonical fragment must have exactly one projection; one model per entity | `storage/fragments/mod.rs:728-770` |
| Prepared fragment carries `fts_text` **and** `embedding_content` together | `pipelines/indexing/pipeline.rs:630-650,1299` |
| Vector count must equal pending fragment count | `pipelines/indexing/pipeline.rs:961,1006` |
| Exact-symbol lookup filters `symbol_name` in the **fragment** collections | `storage/fragments/mod.rs:300-315`, `storage/store/resolution.rs:110` |
| FTS and vector search read the same collection | `storage/fragments/mod.rs:255,318` |
| Hit hydration is already id-based (`entity_id`/`file_id` → canonical rows) | `storage/store/mod.rs:901` |
| Index version gate fails closed; rebuild required on change | `workspace/index.rs:151`, `workspace/manifest.rs:120` |
| Per-model collection naming (schema is per model, not per policy) | `storage/fragments/mod.rs:358` |

Consequence: today **structural unit = lexical unit = vector unit**. Any policy
that embeds fewer units requires either a nullable vector or a second collection.

### 1.2 E1 — maintenance dominates a one-file edit

| Fact | Location |
|---|---|
| A modified file replaces all of its rows: delete file rows, rewrite every entity and fragment | `storage/store/mod.rs:838-877` |
| Deletion does the same | `storage/store/mod.rs:576-600` |
| Every write sets `DIRTY_ALL` | `storage/store/mod.rs:141-144,581,854` |
| Finalization writes a marker, optimizes fragments outside the state lock, then scalar collections under it | `storage/store/mod.rs:612-670` |
| Any index with `completeness < 1.0` forces optimize on the next pass | `storage/fragments/mod.rs:216` |
| Crash recovery treats the marker as "all dirty" | `storage/store/mod.rs:275,311` |
| Writer is retired and storage closed before publication | `pipelines/indexing/service.rs:268-269` |
| Read sessions retire on generation change | `storage/read_session.rs:148` |
| Rebuild preflight requires `2× active` + headroom | `workspace/index.rs:19-23,62` |

Measured with the foundation diagnostics (M3 `body`: one 1.7 KB C# file, 43
entities/fragments, dense corpus):

| Stage | Time |
|---|---:|
| prepare | 1.3 ms |
| embedding (43 inputs) | 3.5 ms |
| commit (delete + reinsert 43 rows, 2 `files` writes) | 13.7 ms |
| **optimize total** | **1 187.9 ms** |
| — fragments (FTS + HNSW, outside lock) | 864.6 ms |
| — files (scalar) | 320.6 ms (runs concurrently with entities/directories) |
| — entities | 234.1 ms |
| — directories | 0.2 ms |

Process filesystem output: **207 104 blocks ≈ 106 MB**; index grows
54.15 → 56.09 MB. `none` (nothing changed): 9.9 ms, no optimize. `delete`:
fragments optimize only 12 ms, scalar `files` optimize 331 ms.

So ~98.6% of the incremental wall and essentially all write amplification is
native maintenance that rewrites whole collections, not the delta. The intrinsic
replacement cost is ~18 ms. `DIRTY_ALL` is roughly accurate here (the file row,
entities and fragments all change); the waste is that optimize cost is
proportional to collection size, not to the change.

---

## 2. Probes (isolated, zvec 0.8.0)

### 2.1 Nullable vector + HNSW is unsafe — E3 option A rejected

Probe: one collection, `text` (FTS) + `embedding` (`VectorFp32`, nullable,
HNSW), one document with a vector, one without.

- Schema creation, insert/update and FTS coexisted; `fetch` with and without
  vectors worked; `update` without a vector preserved the stored vector.
- **Order-dependent corruption after optimize**: when the null-vector document
  is written before a vector-bearing document, a vector query returns the
  null-vector document too (`s3: before=["has-vector"] after=["has-vector",
  "no-vector"]`; `s4: after=["a","n","b"]`). The native layer logs
  `Failed to fetch vector` / `Read vector failed` / `vector indexer fetch failed`.
  With the vector-bearing document written first, results stayed correct, which
  makes the failure silent and data-order dependent.
- Writes of null-vector documents emit a warning per document
  (`Field embedding not found or is null for doc ...`).

Conclusion: **a nullable vector field inside the existing collection is not a
viable E3 mechanism** on this zvec build. Any policy-driven subset must use a
separate vector collection.

### 2.2 Unoptimized collections stay readable, but cost grows

Probe: 12 600 documents, 1024-d, written in equal batches, `optimize` never
called (raw zvec; FP32).

| Segments | Disk | Vector query p50 |
|---:|---:|---:|
| 13 | 67.7 MB | 2.41 ms |
| 26 | 134.8 MB | 2.77 ms |
| 52 | 268.9 MB | 3.15 ms |
| 104 | 537.2 MB | 4.33 ms |
| 208 | 1 068.6 MB | 8.26 ms |
| 1 (after optimize) | 59.7 MB | 0.21 ms |

- Unoptimized collections remain queryable, including after a read-only reopen
  with `completeness = 0.0`.
- Disk grows roughly linearly with the number of segments; query p50 degrades
  ~12× at 13 segments and ~40× at 208 segments versus optimized.
- **But this does not transfer directly to the engine**: an engine full build
  (300 files, 43-doc batches) peaks at **84.1 MB** and settles at 54.4 MB
  (1.55×), so the engine's segment accumulation is far cheaper than the probe's
  485–970-document batches. The engine's segment/latency curve for *deferred*
  optimize is **UNKNOWN** and must be measured in an E1 prototype.

---

## 3. E3 root-cause map

1. **Cardinality coupling** — `validate_projections` + non-nullable vector + one
   collection make vectors mandatory per fragment (`fragments/mod.rs:728`).
2. **Cost driver** — one 1 024-d FP16 vector per declaration: 12 600 vectors =
   25.8 MB logical of a 54.1 MB dense index (forensics), plus HNSW entries
   (unattributed) and one embedding per declaration.
3. **Lookup coupling** — exact-symbol and FTS both read the same collection, so
   the collection must exist for all declarations even if vectors are subset
   (`fragments/mod.rs:300`).
4. **Policy is invisible to storage** — collection identity depends only on
   model provider/name/dimension/metric (`fragments/mod.rs:358`), so changing
   which units are embedded would silently keep stale vectors.

Dense composition (forensics, final build): 12 600 code entities — 300 class,
300 module, 3 000 function, 9 000 value, plus 1 text gap.

## 4. E3 options

| Option | Mechanism | Verdict |
|---|---|---|
| **A. Nullable vector in place** | keep one collection, omit vectors | **REJECTED** — probe shows order-dependent wrong vector hits and native read errors |
| **B. Split lexical and vector collections** | `fragments` (identity, membership, symbol scalars, FTS text, all fragments) + `vectors_<model>` (pk, entity_id, file_id, FP16 vector, HNSW; subset) | **Recommended mechanism** |
| C. Vector collection with duplicate zero vectors for excluded units | keeps one schema | Rejected — pollutes HNSW and creates false matches |
| D. Drop documents for excluded declarations | fewer rows | Rejected — violates source/declaration coverage and exact-symbol |

### 4.1 Option B details

- Lexical collection keeps every fragment (coverage unchanged) and remains the
  source for FTS, `symbol_name`/`symbol_type` filters, exact-symbol enumeration
  and ownership.
- Vector collection holds `pk=fragment_id`, `entity_id`, `file_id`, embedding;
  only policy-selected fragments. Vector search returns the same hit shape, and
  existing hydration (`load_search_hits`) is unchanged.
- Writes: `replace_file` deletes and writes both collections; `delete_file`
  cascades. Optimize runs per collection (fragments outside the lock, as today).
- Policy identity must be persisted: encode it in the index version (policy
  change = version bump = rebuild, fail-closed) rather than silently reusing
  vectors from a different policy. Alternative: descriptor field + manifest
  schema bump; more surface for the same outcome.
- Invariant changes: "every fragment has exactly one projection" becomes "every
  fragment has exactly one lexical projection; vector projections are a subset
  with one model per entity".
- Reconciliation: manifest entries stay keyed by fragment id + model + input
  fingerprint; a fragment that is no longer embedded simply has no vector row,
  so its vectors must be deleted on replace (they are, by file id).

### 4.2 Estimated savings (dense, current mock build)

| Policy | Vectors | Vector bytes | Estimated allocated index |
|---|---:|---:|---|
| P0 all (mechanism only) | 12 600 | 25.8 MB | ≈ 54 MB (split adds ~0.5 MB ids, saves nothing) |
| P1 exclude containers (class/module) | 12 000 | 24.6 MB | −1.2 MB (−2%) |
| P2 exclude `value` | 3 600 | 7.4 MB | ≈ 33–36 MB (**−33…−39%**, band excludes unattributed HNSW) |
| P3 only class/module/interface | 600 | 1.2 MB | ≈ 28–31 MB (**−43…−48%**) |

Full-build embedding work scales with vector count: P2 removes 71% of the
embedding inputs. On the duplicate corpus (2 001 fragments over 500 identical
files) a content-hash dedup policy could cut vectors by ~99%.

**These are theoretical upper bounds from cardinality.** Native vector-index
bytes are not attributed in forensics, and quality cost is unmeasured.

---

## 5. E1 root-cause map and options

Attribution above shows the cost is **whole-collection optimize**, not the delta
write. `DIRTY_ALL` is mostly justified; precise dirty masks would help only in
narrow cases (a write that leaves a collection untouched), because optimize
rewrites the collection regardless of how many rows changed.

| Option | Mechanism | Expected benefit | Risk |
|---|---|---|---|
| **A. Bounded deferred maintenance** | persist a debt record; optimize when `pending_writes ≥ K` (start K=4) or age ≥ T, on full builds/rebuild, on recovery, or on an explicit maintenance call; hard cap | amortizes 1.19 s → ~0.3 s per edit at K=4; write volume down proportionally | segment growth degrades queries/space (probe: engine curve UNKNOWN); needs bounds and catch-up |
| B. Defer only scalar optimize | keep fragment optimize every pass, skip `files`/`entities`/`directories` until a bound | measured ceiling −320 ms per edit (~27%) and ~10–20% of writes; independently shippable | scalar table fragmentation; simpler to bound |
| C. Precise fragment replacement | delete/insert only changed fragments | ~13.7 ms commit improves, total wall unchanged | touches ID/ownership/invalidation invariants; **DEFER** |
| D. Background maintenance | optimize on idle | best UX | new lifecycle/daemon policy; not authorized now; **DEFER** |

Marker design for A: replace the zero-byte `optimize.pending` with a small
versioned record `{version, dirty_mask, pending_writes, last_optimized_ms}`;
a legacy empty file must read as `DIRTY_ALL` (backward safe). Recovery must
resume optimization before serving writes.

Bounds to enforce and test:

- index growth after K deferred edits ≤ 1.15× the optimized size;
- a triggered optimize returns to ≤ 1.02× (reclamation works);
- query p50/p95 in the deferred state ≤ 1.25× the optimized state for FTS,
  vector and hybrid;
- crash/restart mid-debt resumes and never loses committed rows.

---

## 6. Prerequisites and measurement corrections

1. **Same-size/same-mtime staleness** (`pipelines/indexing/pipeline.rs:589`,
   `indexed_search/context.rs:542`) is a *deliberate metadata fast path*, and
   the M3 matrix confirms it: `samesize` yields 0 modified files. No test covers
   it today. Proposal (separate from E1/E3): add a regression that asserts the
   current behavior explicitly, then an owner decision between (a) documented
   limitation, (b) optional strict content verification mode. Do not change it
   silently inside either track.
2. **ANN nondeterminism**: vector/hybrid result sets differ between any two
   independent builds of the same corpus, reproduced with the baseline binary
   alone. Quality must be judged with corpus-level metrics (R@10/50, MRR,
   nDCG) over several builds, with tolerance bands and paired seeds. Exact
   set-parity is valid only for FTS and for the lexical/exact-symbol paths.
3. **Cost separation**: mock model runs as a separate process (CPU excluded from
   `/usr/bin/time`), but engine vs native CPU is still unattributed;
   `index_commit`/`index_optimize` are wall measurements that include native
   work. Process FS blocks are not physical writes. Final index size is not peak
   capacity: measured peak 84.1 MB vs final 54.4 MB (1.55×) for a full build,
   before the 2× rebuild preflight.
4. **Missing evidence**: no real embedding model in benchmarks; vector reuse is
   never exercised (Qwen declares an unstable identity → every fragment reports
   `identity_unavailable`); no segment-count metric exposed by the API; no
   engine-level latency curve in the deferred state.

---

## 7. Gates

### E1 first experiment

Benchmark: dense 300 + mixed 525 corpora, mock model (fast, deterministic
cardinality), reports every 1, 4, 8 deferred edits plus catch-up.

Pass:
1. all existing suites plus new debt/recovery/crash tests;
2. deferred state query p50/p95 within 1.25× of optimized, FTS/vector/hybrid;
3. growth bound and reclamation as above;
4. restart/crash resume; no lost or duplicated rows; reader generation
   retirement unchanged;
5. FS blocks and wall per edit reduced by ≥ 50% at K=4 (unless the measured
   engine segment curve forbids it — then K is lowered and the result reported).

Fail (abort and revert): any query regression beyond the bound, unreclaimed
growth, or any recovery/lifecycle test failure.

### E3 mechanism experiment

Pass (policy = all fragments, mechanism only):
1. source/declaration/gap coverage identical; exact-symbol and FTS results
   identical (exact parity);
2. vector recall@10 vs the current index ≥ 0.99 on dense and mixed (rebuild
   tolerance), with the same model and corpus;
3. no-answer behavior unchanged; index version bump forces rebuild and
   rollback-by-rebuild works;
4. disk overhead ≤ +3% and full/incremental wall within noise.

E3 policy experiment (P2/P3) additionally requires a **real embedding model**
and a judge corpus, plus an owner decision on the acceptance tolerance
(suggest: R@10 within 2 pp, no drop at R@50, nDCG within 2 pp).

Cheap pre-spike before any E3 code: measure how often `value` (property/field)
declarations are retrieved by vector but not by FTS on a real-model corpus; if
their unique vector contribution is negligible, P2 is safe and E3 becomes
worth its cost; if not, E3's disk win is a quality trade the owner must accept.

---

## 8. Minimal proposals, dependencies, rollback

**E1** (recommended first): bounded deferred maintenance (option A), implemented
as (a) precise debt record + policy, (b) scalar-only deferral first if the
fragment curve proves too costly. Dependency: none on E3. Rollback: revert the
commit; a leftover debt marker from the new format must be readable by the old
code (unknown fields are ignored / empty-file semantics) or the storage must be
rebuilt — to be verified before shipping.

**E3**: split collections (option B) with policy = all, then a gated policy
change. Dependencies: E1's cheaper incrementals help the quality matrix but are
not required; a real model and quality corpus are required. Rollback: index
version bump; old binaries refuse and require rebuild.

## 9. Unverified and risks

- Engine segment/latency behavior under deferred optimize (probe is not the engine).
- Whether zvec `optimize` can be interrupted safely mid-debt (marker semantics today assume a whole-pass retry).
- Native HNSW/FTS byte attribution; probe used FP32 raw zvec.
- Real-model retrieval impact of any vector subset (P2/P3) — completely unmeasured.
- Reuse remains unexercised (`identity_unavailable`), which also limits any
  E3 policy experiment that relies on manifest reuse.
- Log volume from nullable-vector warning if option A were ever revisited.

## 10. Recommendation

**Run E1 first, then E3.**

E1 owns the measured dominant cost: 98.6% of a one-file edit's wall time and
essentially all of its ~106 MB of process writes, with attribution already in
hand and a qualification path that needs no quality oracle. E3's mechanism alone
saves ~0 bytes; its real benefit (33–48% dense disk, ~71–95% fewer vectors)
depends on a vector policy whose quality cannot be judged with the current mock
model, so E3 must wait for a real-model quality track — and E1 makes that track
cheaper, because every quality iteration is an incremental build.

E3 is the larger prize if quality allows; it should start only after the
vector-contribution pre-spike above shows that excluding `value` declarations is
retrieval-neutral, and its mechanism (split collections) should then be built
once, with policy = all, before any policy number is claimed.
