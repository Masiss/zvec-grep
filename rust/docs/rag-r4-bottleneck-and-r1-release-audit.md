# R4 bottleneck audit and R1 v1.0 release-gate assessment

Status: **READ-ONLY AUDIT. R4-A/B remain NO-GO. No new R4 implementation.**
Date: 2026-11-08. Frozen inputs only: B0 receipt, candidate/R4 receipts, dev
cases, corpus manifest. No corpus, gold, target, receipt or held-out changes.

Raw per-case data: `benchmarks/rag-impact/results/retrieval-selection-ceiling.json`.
Tool: `benchmarks/rag-impact/tools/audit_retrieval_selection.py`.

## 1. Is B0 best-call a deployable policy?

**No. B0's `best_call` is an oracle selector.** The harness computes each call's
recall against gold, then keeps the highest-recall call per case:

- `benchmarks/rag-impact/tools/b0_official.py:328-341` — `best_call` is assigned
  only when `recall > best`; the case recall is `best`.
- `case["best_call"]` therefore requires the gold spans and cannot be chosen by
  an agent before answering.

| Policy | Mean Recall@10 (12 dev cases) | Deployable? |
|---|---:|---|
| Per-case B0 oracle best-of-6 | **0.4722** | No — uses gold |
| Fixed `fts_code_tokens` for all cases | **0.3750** | Yes |
| Fixed `rg_code_tokens` | 0.3194 | Yes |
| Fixed `fts_first_phrase` | 0.1111 | Yes |
| Hybrid / FTS question / `rg_last_phrase` | 0.0000 | Yes |

The best fixed deployable call is `fts_code_tokens` at 0.3750. The B0 baseline is
9.72pp higher because it selects a different call per case using gold. Any
candidate target stated as “B0 + 10pp” must say whether B0 means the oracle or a
deployable fixed/selector policy.

## 2. Retrievable and selectable ceilings

For each case, the **retrievable ceiling** is the gold covered by the union of
all six search calls. The **selectable Recall@10 ceiling** is the best coverage
of any 10-item subset of that union (optimal subset, same item budget).

| Case | Gold | Union retrievable | Selectable@10 | B0 oracle | Fixed `fts_code` | Old candidate |
|---|---:|---:|---:|---:|---:|---:|
| D01 | 2 | 1 | 1 | 1 | 1 | 0 |
| D02 | 6 | 0 | 0 | 0 | 0 | 0 |
| D03 | 1 | 0 | 0 | 0 | 0 | 0 |
| D04 | 1 | 1 | 1 | 1 | 1 | 1 |
| D05 | 1 | 0 | 0 | 0 | 0 | 0 |
| D06 | 3 | 1 | 1 | 1 | 0 | 0 |
| D07 | 3 | 1 | 1 | 1 | 0 | 0 |
| D08 | 2 | 2 | 2 | 2 | 2 | 2 |
| D09 | 1 | 1 | 1 | 1 | 1 | 1 |
| D10 | 2 | 1 | 1 | 1 | 0 | 1 |
| D11 | 1 | 1 | 1 | 1 | 1 | 0 |
| D12 | 2 | 0 | 0 | 0 | 0 | 0 |
| **Total** | **25** | **9 / 25 (36%)** | **9 / 25** | **9 / 25** | **6 / 25** | **5 / 25** |
| Macro mean | | **0.4722** | **0.4722** | **0.4722** | **0.3750** | **0.2917** |

The selectable@10 ceiling equals the union ceiling in every case: a 10-item
budget is not the limiting factor for the retrievable gold. B0 oracle already
attains the retrieval ceiling; no ranking policy can exceed 9/25 on these six
search calls. The original target `B0 + 10pp` = 0.5722 macro is **above the
0.4722 retrieval ceiling** and is therefore impossible without improving
retrieval/discovery. R4-A/R4-B expand beyond the union, but still reach only
5/25.

## 3. Discovery, ranking and range misses

Using the union as the reference:

| Policy | Packed | Discovery | Ranking | Range | Total |
|---|---:|---:|---:|---:|---:|
| B0 oracle | 9 | 16 | 0 | 0 | 25 |
| Fixed `fts_code_tokens` | 6 | 16 | 3 | 0 | 25 |
| Old candidate | 5 | 16 | 4 | 0 | 25 |
| R4-A | 5 | 15 | 4 | 1 | 25 |
| R4-B | 5 | 15 | 4 | 1 | 25 |

- **Discovery miss = 16/25 (64%)**: gold is not in any of the six search calls.
  D02 has 6; D03/D05 one each; D06/D07 two each; D10 one; D12 two; D01 one.
  This is the primary bottleneck and cannot be fixed by ranking a fixed
  candidate set.
- **Ranking miss** for the fixed deployable policy is 3 spans (D06, D07, D10):
  gold is retrievable, but the fixed call's top-10 does not select it.
- **Ranking miss** for the old candidate is 4 spans (D01, D06, D07, D11);
  R4-A/B also lose 4 retrievable spans (D01/D06/D07/D09) and have 1 range miss
  (D10) while recovering one non-union span via expansion.
- **Range miss** under the union definition is rare (R4-A/B D10 only). The
  earlier attribution's “range selection” labels for D03/D05/D06/D07 were
  relative to the B0 best-call pack, not to the full search union. Relative to
  the union, D03 and D05 are discovery misses: no search call retrieved the gold
  span, even though the same file appeared in a pack.

## 4. Does files/case measure additional agent reads?

**No.** The frozen numbers are not comparable:

- B0's `additional_source_reads` is a simulated post-search read: it walks the
  union of search units, opens distinct files until 64 KiB, and counts them
  (`b0_official.py:342-363`). It is still a simulation, not an agent trace.
- The old candidate's `additional_source_reads` is the number of distinct
  **paths represented in the final pack** (`candidate.rs` quality pilot:
  `read_paths` is built from `items`, lines 1139-1160). It does not simulate an
  agent reading files after the pack.
- R4-A/R4-B `files` is simply `file_count(pack)` — distinct files in the pack
  (`candidate.rs:850-852`).

Therefore candidate/R4 files/case measures **pack file diversity**, not
additional agent reads. The reported read comparisons (e.g. 5.75 vs 2.75, or
3.00 vs 7.75) cannot support a read-reduction claim. A correct metric must run
the agent workflow after the tool response and count files opened, or define
both arms as pack file count. Tokens have the same asymmetry: B0 counts search
payload + read content; candidate/R4 counts final item content only.

## 5. Bottleneck conclusion

**Primary bottleneck: retrieval/discovery (64% of gold is never retrieved).**
**Secondary bottleneck: selection (deployable fixed policy loses 3 retrievable
spans; candidate loses 4).**

- Retrieval ceiling 9/25; selectable ceiling 9/25; B0 oracle 9/25.
- Fixed deployable policy 6/25; old candidate/R4 5/25.
- Even a perfect selector over the current six calls cannot reach the +10pp
  target. R4 selection changes cannot fix the target; they can only close the
  gap from 5/25 to at most 9/25.
- A new retrieval/discovery experiment is required before any further selection
  tuning. That requires owner approval; this audit does not authorize it.

## 6. R1 lightweight — v1.0 release-gate assessment

R1 is the internal/public engine evidence API in
`rust/crates/zg-engine/src/api/evidence.rs` plus
`benchmarks/rag-impact/REPORT-CONTRACT.md`.

### PASS (correctness slice)

- Current focused suite: **10 passed, 0 failed** (`api::evidence`), covering
  Unicode/codepoint, CRLF/EOF, traversal/symlink, stale/renamed, same-name
  different project, budget/dedup, empty vs invalid query, changed source and
  forged coordinates.
- Fail-closed gaps, source hash/range revalidation and the three conclusion
  classes are implemented/documented.
- `api::evidence` is a public engine module (`api/mod.rs`, `lib.rs`).

### MISSING for v1.0 release

1. **Release-branch real-source pilot provenance.** The retained five-pack R1
   pilot receipt has `source_head` but no engine commit/branch. The V2 handoff
   says the V2 port was API-only and did not rerun the real-source pilot. A
   release candidate needs a V2 real-source run with engine identity and receipt.
2. **Public integration and versioning.** No CLI/MCP/daemon consumer uses
   `EvidenceRequest`/`assemble`/`revalidate`; there is no public response shape,
   unsupported-behavior contract or auth/scope decision. Release v1.0 must either
   be explicitly library-only or add a versioned integration.
3. **Cache/version key and invalidation.** R1 docs state no cache/version key is
   persisted; callers revalidate per final answer. A release needs a documented
   no-cache contract or a hash + extraction/rule version key with invalidation.
4. **Performance/footprint gate.** No p95/PSS/concurrency/index-overhead
   measurement for evidence assembly/revalidation; the §9 budgets are not
   measured for R1. At minimum, bounded pack assembly latency and process PSS
   must be recorded on the frozen workload.
5. **Broader evidence validation.** The pilot covers D02/D07 plus negative twins
   (5 packs, 14 items), not all 24 cases or held-out. R1 needs either full dev +
   held-out evidence validation or an explicit release limitation.
6. **Security/privacy review for the exposed surface.** Traversal/symlink and
   UTF-8 are tested; secret/PII classification, root admission, auth/audit and
   data-retention behavior are not defined for a public consumer.
7. **Release mechanics.** No feature flag/rollback, versioned release note or
   deprecation/unsupported behavior; needed before promoting beyond internal.

**Verdict:** R1 correctness slice is PASS, but R1 lightweight is **not ready for
a public v1.0 release**. It can be shipped only as an internal/library capability
with documented limits, or after the integration, provenance, performance,
validation and security gates above. No R4 implementation is authorized by this
audit.
