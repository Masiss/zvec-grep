# RAG next-step research — 2026-11-26

## Verdict

**Ranking has improved materially but has not solved evidence loss.** None of the
gold-blind ranking configurations passes the strong both-language gates. The
next useful experiment is not another weighted ranker or more resolver slots;
it is a bounded **raw-discovery file/facet expansion** using the same index and
model.

## What ranking has achieved

| Arm | Rust all-gold | Rust exact | C# all-gold | C# exact | Verdict |
|---|---:|---:|---:|---:|---|
| C0 | 2/12 | 7/30 | 1/12 | 2/25 | Baseline |
| Hybrid metadata v1 | 2/12 | 7/30 | 2/12 | 3/25 | Offline candidate only |
| Multi-route slate v1 | 3/12 | 9/30 | 2/12 | 3/25 | NO-GO |
| Gold-aware oracle | 7/12 | 21/30 | 3/12 | 5/25 | Upper bound only |

Multi-route v1 rescued R12 and preserved D04/D11/R10, but missed the required
Rust >=4 all-gold including R11, Rust >=11 exact, C# >=3 all-gold and C# >=4
exact. Precision and token cost improved, but quality gates still fail.

## Current first-blocker attribution

Multi-route span diagnosis reports:

| First blocker | Rust | C# |
|---|---:|---:|
| No entity in pool | 3 | 9 |
| Entity not anchored before budget | 9 | 3 |
| Resolver slot unavailable | 6 | 10 |
| No containing declaration | 3 | 0 |

These are first blockers, not independent root causes. In particular,
`resolver_slot_unavailable` can hide a representation failure behind it.

## New resolver-slot upper-bound probe

A read-only probe invoked the existing resolver at the first eligible event for
every span whose first blocker was `resolver_slot_unavailable`. It did not rerun
scheduling or change a pack.

Findings:

- **None** of those events had a containing declaration within the current
  4 KiB proposal cap.
- Some had a larger containing declaration: R02 `push_attrs` was 22,000 bytes,
  R07 `parse_short_arg` was 5,759 bytes, and D03
  `EmployeeEditThongTinCaNhan` was 11,795 bytes. Several had no containing
  declaration at all.
- At the recorded schedule position, the large proposals did not fit the
  remaining global/route budget without reordering.

Therefore:

1. Adding resolver slots alone is unlikely to rescue exact spans.
2. Raising the declaration cap alone would pay 6–22 KiB for a single method and
   worsen budget starvation.
3. The useful representation direction, if needed later, is **bounded
   sub-declaration windows**, not whole large declarations.

Receipt:
`/home/masis/.cache/zvec-grep/entity-anchored-span-replay-20261126/multi-route-slate-v1/replay/diagnosis/RESOLVER-SLOT-UPPER-BOUND.json`.

## Why raw discovery is the next bottleneck

C# has 9/25 gold spans with no entity in the candidate pool. Ranking and pack
replay cannot recover bytes that were never recalled. Examples include:

- D09: question names `EmployeeBaseViews`; the SQL view is absent from the pool.
- D10: question names `CoreService`; both `CoreService.cs` and
  `CoreDBContext.cs` gold evidence are absent.
- D02: broad personal-update write-path question; four EF/SQL spans are absent.
- D07/D12: SQL stored-procedure evidence is absent.

D09 and D10 are especially strong signals for a gold-blind file/identifier
route: the user query contains the file/type name, but entity-only reranking
cannot introduce a file that raw discovery omitted.

## Recommended next single experiment

### Raw-discovery file/facet expansion, offline first

Keep the index, embedding model, corpus, byte cap and source hashes unchanged.
Compare C0 against one expanded candidate pool:

1. Start with the exact C0 pool.
2. Extract explicit query identifiers (existing camelCase/snake_case/backtick
   logic).
3. Add a small file-level route:
   - file stem/path identifier matches;
   - indexed file/FTS hits for those identifiers.
4. For remaining non-identifier facets, union a bounded number of per-facet
   FTS/vector hits.
5. Preregister a hard expansion limit, suggested **+4 files / +16 fragments per
   case**, then dedupe by entity/fragment.
6. Feed the expanded pool through the unchanged C0 ranker/selector, resolver
   quota and 24 KiB cap. Do not combine this with a new scheduler.

This isolates recall gain. If it works, the expanded pool can later be paired
with the best offline ranker in a separate preregistered arm.

### Gates

- Zero verifier gaps; R10 remains 3/3; D11 and every C0 all-gold case preserved.
- At least one new C# all-gold case, preferably D09 or D10, without Rust
  all-gold loss.
- Exact-span and macro precision must not regress in either split.
- Mean tokens <=1.10x C0; report per-case p95 only if a real binary arm is run.
- If explicit file recall does not add a C# all-gold case, stop rather than
  broadening the route indefinitely.

## Deferred direction

If discovery expansion passes but large-method spans remain missing, test a
separate **sub-declaration window resolver**:

- when the smallest containing declaration exceeds 4 KiB, return its
  signature/header plus at most two query-scored 2 KiB windows around matched
  terms/fragment boundaries;
- never return the whole 6–22 KiB declaration by default;
- retain path/hash byte-union grading and the same 24 KiB cap.

Do not increase resolver slot count, declaration cap, index representation or
model until the corresponding blocker is isolated by a preregistered replay.
