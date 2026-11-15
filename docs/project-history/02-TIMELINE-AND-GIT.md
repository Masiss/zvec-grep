# 02 — Timeline and Git History

This is a chronological map, not a commit dump. Dates are commit dates
(`%ad`, author date) and may differ from merge/publication time. Every named
SHA is a real object in this checkout unless marked otherwise.

## 1. Three Git “main” lines

| Line | Current SHA | Date | Meaning |
|---|---|---|---|
| local `main` | `f95fc49ef793ee47e697ded6ac5fb6d7ecbf4d0f` | 2026-10-13 | comparison branch, not maintained |
| `origin/main` | `2d7d2ac220d1baf840694d937fea7054d091e33d` | 2026-10-15 | remote main snapshot |
| `upstream/main` | `e1f93d257800f8626eb877eaf45573518076ab65` | 2026-10-30 | upstream Rust main |
| `work/zg-recovered` | `27b81c0e0472644820955fd69d1a080fb76b16ca` | 2026-11-15 | maintained recovery/release-candidate line |
| `origin/work/zg-recovered` | same | 2026-11-15 | published maintained branch |

`work/zg-recovered` is **206 commits ahead of `origin/main`** and **213 ahead
of local `main`**. A history answer that says “main” without naming one of these
is ambiguous.

## 2. Phase A — Node/TypeScript origin (July–September 2026)

| Date | Milestone | Evidence |
|---|---|---|
| 2026-07-10 | Initial `zvec-grep` import | `d3e2a47` |
| 2026-07-14 | MCP server integration, shared MCP contracts, HTTP server, async indexing, watcher, daemon lifecycle | `b802a61`, `5b4eec4`, `3d19924`, `05ddbf3`, `f3f4bc8`, `eca332a` |
| 2026-07-15 | Remote Qwen embeddings, local GPU config, initial benchmarks | `6c1359e`, `c42943a`, `8482226`, `cfb5a85` |
| 2026-07-16 | `zg` CLI redesign, server-mode integration, v0.1.5 tag | `f50acf8`, `d4e1aeb`, tag `v0.1.5` |
| 2026-07-21–23 | More local embeddings, GPU acceleration, MCP parity, authorization | `e03cf36`, `38e2823`, `69e67e5`, `e336955` |
| 2026-08-07 | Default local embedding `local/potion-code-16m-v2` | `0f15ff9` |
| 2026-08-20 | v0.1.8 / v0.1.9 | tags |
| 2026-08-26 | v0.2.0 | tag |
| 2026-09-01 | v0.2.1 | tag |
| 2026-09-07 | v0.2.2 | tag |

The TypeScript package remains in `src/` and `package.json`; the README
explicitly documents it separately from Rust.

## 3. Phase B — Rust workspace alongside TypeScript (September–October)

| Date | Milestone | Evidence |
|---|---|---|
| 2026-09-20 | Rust workspace integrated alongside TypeScript | `b1d0ce9` |
| 2026-09-21–24 | Rust CI, MCP/daemon parity, installer, encoding, agent targets | `a2feef0`, `e2f4c49`, `ddeb04c`, `5b4b510` |
| 2026-09-23 | Search becomes default `zg` interface | `73b84cd` |
| 2026-09-28 | Rust MCP transport/public tool behavior alignment | `5a5bc81` |
| 2026-10-12–14 | Rust file-type filters, native npm release, index/status fixes | `3f97886`, `152d048`, `c2f5e3f` |
| 2026-10-15 | `origin/main` snapshot; Pi agent support | `2d7d2ac` |
| 2026-10-19–22 | query merge, code signatures, managed rg, SQLite graph persistence, zvec 0.8 | `1bf179b`, `f2256bd`, `ae1ac57`, `99cf43f`, `7e71e67` |
| 2026-10-23 | CUDA backends as runtime plugins | `0203984` |
| 2026-10-29 | FP16 vectors, RC1 packaging | `11ea06e`, tag `rust-v0.3.0-rc.1` |
| 2026-10-30 | upstream Rust RC2 line | `e1f93d2` |
| 2026-10-31 | RC2 package / release code | tag `rust-v0.3.0-rc.2` at `c2347d5`; release code `289674b` |

The current installed `zg 0.0.1` is not the CLI version of this Rust workspace
package; see [PROJECT-STATE.md](PROJECT-STATE.md).

## 4. Phase C — Recovery and consolidation (November 4–9)

| Date | Milestone | Evidence |
|---|---|---|
| 2026-11-04 | Archive V1/native work and consolidate recovery source | `13a3555`, tags `archive/zg-v1-before-recovery-20261104`, `archive/zg-v2-release-20261104` |
| 2026-11-04 | R1 bounded source evidence ported to V2 | `a8048a0` |
| 2026-11-05 | Recovery verification: full workspace suite, isolated native pair, rollback | `rust/docs/recovery-verification-20261105.md` |
| 2026-11-08 | R1 V2 dev evidence pilot; RAG R0/R1 handoff; R4 candidate NO-GO | `2695216`, `rust/docs/rag-r1-release-gates.md`, `rust/docs/rag-r4-candidate-pilot.md` |
| 2026-11-09 | Index v3 deep audit/hardening | `rust/docs/index-v3-deep-audit.md`, `work/index-v3-hardening-20261109` |

Recovery notes state the canonical branch is `work/zg-recovered`; production
rollout remains outside that completed recovery task.

## 5. Phase D — Foundation E1–E8 (November 10–15)

| Track | Branch / tip | Outcome |
|---|---|---|
| Foundation G1–G4/G5 | `work/foundation-opt-20261110` @ `d64e54e` | microbench wins; no end-to-end win; G5 streaming hash slower/rolled back |
| E1 bounded maintenance | `work/e1-bounded-maintenance-20261110` @ `6012ef4` | experimental; not merged; deferred maintenance showed bounded gains but needs policy |
| No-op fast path | `work/noop-fastpath-integration-20261111` @ `24af3e6` | integrated into later foundation line; ~89–92% no-op wall reduction |
| E2 content-addressed reuse | `work/e2-content-addressed-20261111` @ `8ee9790`; integration `work/e2-integration-20261112` @ `96d543c` | READY_FOR_PROMOTION_REVIEW; integrated into E8-P2 |
| E3 retrieval benchmark | `work/e3-01-retrieval-corpus-20261112` @ `ae3aab2` | corpus/gold/benchmark; later measurements contaminated |
| E3-02A attribution | `work/e3-02a-attribution-20261112` @ `95fd0f3` | gold v2 qualified; route table later invalidated |
| E3-02B vector policy | `work/e3-02b-r0-vector-policy-20261114` | NO-GO rationale depends on invalid attribution; owner review required |
| E4 model selection | `work/e4-embedding-selection-20261113` | inference/RAM measurements valid; retrieval table later corrected |
| E5 FTS reproducibility | `work/e5-fts-reproducibility-20261114` | root cause = harness primary query + route; E4 FTS claim retracted |
| E6 integrity | `work/e6-benchmark-integrity-20261114` | fail-closed route contract; validity matrix; requalification |
| E7 scalability | `work/e7-indexing-scalability-20261114` | bottleneck attributed; both optimizations NO_GO |
| E8 acceleration | `work/e8-embedding-acceleration-20261114` | Vulkan explicit opt-in; CPU default; benchmark parity qualified |
| E8-P1 Vulkan hardening | `work/e8p1-vulkan-hardening-20261114` | device identity/fail-closed; CPU/Vulkan results |
| E8-P2 integration | `work/e8p2-integration-20261115`; merge `58582d2` | merged into `work/zg-recovered`; index v3 etc. |
| Post-merge stabilization | `27b81c0` | E8-P2 merge recorded; no installed binary replacement |

E8-P2 merge result: main branch tree at `58582d2` matches the integration tree;
docs follow at `27b81c0`. Rollback tag
`rollback/pre-e8p2-merge-20261115` = `6d5b523`.

## 6. Phase E — RAG R0–R4 (November 8, 2026)

| RAG phase | Actual artifact | Status |
|---|---|---|
| R0 | `benchmarks/rag-impact/` scope, **16 frozen dev + 8 held-out** cases, budgets, B0 receipt | frozen pilot exists; B0 measured 12 real-source dev cases |
| R1 | `rust/crates/zg-engine/src/api/evidence.rs`; `r1-dev` pilot | correctness PASS; library-only |
| R2 | `extraction/csharp_facts.rs` | implemented source-visible C# facts |
| R3 | `extraction/sql_facts.rs`; `rag-r3-sql-facts.md` | implemented selected SQL facts |
| R4 | `extraction/candidate.rs` + `rag-r4-*` | expander candidate FAIL; full multi-turn loop not implemented |
| R5 | no implementation | not started |
| R6 | no implementation | not started |

Canonical meaning is in [05-RAG-ORIGINAL-R0-R6.md](05-RAG-ORIGINAL-R0-R6.md).

## 7. Phase F — Post-R4 research (November 15, 2026)

Exact branch tips and bases:

| Branch | Tip / base | Outcome |
|---|---|---|
| `work/rag-requal-20261115` | published `f93d0499…`; local ref `1d8f57a…` / `27b81c0` | Discovery not qualified; candidate union 18/25 |
| `work/rag-s1-20261115` | `1d8f57a3…` / `f93d0499` | Selection NO_GO; exact 6/25 |
| `work/rag-s1b-20261115` | `ef2a7667…` / `1d8f57a3` | signal NO_GO; 10/25, held-out strict regression |
| `work/rag-refint-20261115` | `89c182e7…` / `ef2a7667` | MISSION_PARTIAL; candidate union 22/25, selection 10/25 |
| `work/rag-evidence-20261115` | `3821168…` / `89c182e7` | MISSION_PARTIAL; SQL binding/grounding prototype |
| `work/rag-gq-20261115` | `0843ae5c…` / `3821168` | NO_GO; commented-code false binding exposed |
| `work/rag-gqr-20261115` | `39e75d48…` / `0843ae5c` | Gate A PASS; GQ4 NO_GO |
| `work/rag-gs-20261115` | `102f214c…` / `39e75d48` | typed grounding; readiness only; GQ5 not run |

`work/rag-s1-20261115` points at the same post-S1 docs tip as
`work/rag-requal-20261115` (`1d8f57a`); the S1 commit `7d912c1` is in that
history. This is a branch-label artifact, not a separate product branch.

The post-R4 chain is **not** R5/R6 and is not merged into `work/zg-recovered`.

## 8. What was actually merged vs experimental

- `origin/main` is old; `work/zg-recovered` is the maintained line.
- E8-P2 is merged into `work/zg-recovered` (merge `58582d2`).
- E1 bounded maintenance, E7, E3/E4/E5/E6 experiment branches and the entire
  post-R4 RAG chain are **not merged**.
- R1 evidence API and R2/R3 facts are in the maintained source tree, but R1 is
  library-only and R2/R3 are source-visible facts, not a deployed RAG answer
  system.
- Installed binary/index is older than the maintained source; see
  [07-OPERATIONS-AND-RELEASE-STATE.md](07-OPERATIONS-AND-RELEASE-STATE.md).

## 9. Historical branch and tag caveats

- Tags `archive/zg-v1-before-recovery-20261104` and
  `archive/zg-v2-release-20261104` preserve old lines; they are not releases of
  the current RAG work.
- `rollback/pre-e8p2-merge-20261115` is a rollback tag, not a current runtime.
- A branch name containing a date is not proof that its work was merged or
  installed.
- Local `main`, `origin/main`, `upstream/main` and `work/zg-recovered` are
  different identities; use SHAs.

## 10. Evidence anchors

- `git log` at `27b81c0`; specific commits as above.
- `rust/docs/recovery-verification-20261105.md`, `rust/docs/branch-recovery-status.md`.
- `rust/docs/e5-fts-reproducibility-20261114.md:1-80`.
- `rust/docs/e6-benchmark-integrity-20261114.md:1-100`.
- `rust/docs/e8p2-integration-20261115.md:13-40`.
- `docs/rag/implementation-plan.md:5-16`.
- Post-R4 handoffs in `work/rag-*` branches at their documented tips.
