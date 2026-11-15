# 08 — Decisions and Open Questions

## 1. Decision ledger

Statuses: `ACCEPTED`, `REJECTED`, `DEFERRED`, `OWNER_DECISION_REQUIRED`,
`HISTORICAL_ONLY`.

| ID | Decision / finding | Status | Evidence |
|---|---|---|---|
| D-ARCH-1 | Rust workspace is the maintained release line in this fork; Node/TS remains a separate product implementation | ACCEPTED | `rust/ARCHITECTURE.md`, `README.md:48`, recovery docs |
| D-ARCH-2 | CPU is default; accelerator is explicit opt-in, not silent fallback | ACCEPTED | E8/E8-P1 docs, catalog/default code |
| D-ARCH-3 | TypeScript/Node and Rust are not interchangeable identities | ACCEPTED | `package.json` v0.2.1 vs Rust 0.3.0-rc.2; README |
| D-STORE-1 | No in-place index migration; incompatible versions require explicit rebuild | ACCEPTED | `rust/docs/rag-r1-release-gates.md:20-25`, release notes |
| D-STORE-2 | Index v3 is conditional on FTS/scope fixes; deferred format/schema work not approved | ACCEPTED with limits | `index-v3-promotion-qualification.md:150-160` |
| D-STORE-3 | E1 bounded maintenance is experimental, not merged | ACCEPTED | E1 branch doc |
| D-E2-1 | E2 embedding reuse is ready for promotion review and included in E8-P2 | ACCEPTED | `e2-integration-20261112.md:117-131` |
| D-E7-1 | E7 thread/batch/multi-context optimizations are NO_GO | REJECTED | `e7-indexing-scalability-20261114.md:175-180` |
| D-E8-1 | Vulkan/CUDA/Metal query/write behavior is explicit; accelerator writes rebuild-only | ACCEPTED | `e8p2-integration-20261115.md:13-20,53-55` |
| D-BENCH-1 | E4 FTS model-dependence claim is retracted; old route tables are invalid | ACCEPTED | E5/E6 docs |
| D-BENCH-2 | E3-02B NO-GO rationale requires owner review because attribution is invalid | OWNER_DECISION_REQUIRED | E6 validity matrix |
| D-RAG-1 | R0 scope/cases/budgets are frozen for the pilot | ACCEPTED | `benchmarks/rag-impact/PREREGISTRATION.md` |
| D-RAG-2 | R1 correctness slice is safe as library/internal only | ACCEPTED with limits | `rag-r1-release-gates.md:1-45` |
| D-RAG-3 | R4 bounded expander/candidate is NO-GO; keep R1 fallback | REJECTED for promotion | `rag-r4-candidate-pilot.md:61-70` |
| D-RAG-4 | Full R4 multi-turn loop/public integration was not implemented | OPEN | plan vs R4 docs |
| D-RAG-5 | R5 paired qualification and R6 opt-in release are not done | OPEN / not authorized | plan phases 7–8 |
| D-RAG-6 | Selection/Expansion remain frozen | ACCEPTED | S1/S1b/RefInt handoffs |
| D-RAG-7 | Post-R4 S1/S1b/RefInt/Evidence/GQ/GQR/GS are research branches, not plan phases | ACCEPTED | branch docs |
| D-GQ-1 | GQ candidate `3821168` is NO_GO due commented-code false binding and safety failures | REJECTED | GQ handoff |
| D-GQR-1 | Gate A liveness/binding repair PASS; GQ4 quality NO_GO for v5 | ACCEPTED | GQR handoffs |
| D-GS-1 | `typed-grounding-v1` is ready for independent qualification, not production | ACCEPTED with limits | GS verification |
| D-GS-2 | GQ5 has not been run | OPEN / owner authorization required | GS docs |
| D-OPS-1 | Installed v2/Potion is not replaced by source v3/E8-P2 | ACCEPTED / no action | runtime state |
| D-OPS-2 | Rollback pair must be verified before any replacement | ACCEPTED | recovery/release docs |
| D-GOV-1 | `docs/rag` still says PROPOSED and has not been reconciled with post-R4 work | OPEN | `docs/rag/README.md:3`; plan `:5` |

## 2. Owner-only gates

The following require explicit owner authorization; this documentation branch
cannot decide them:

1. Change production index schema/version, storage engine or migration.
2. Add a large graph/parser/LSP infrastructure or heavy dependency.
3. Change the public `VERIFIED` contract in a breaking way.
4. Relax correctness/safety gates.
5. Change production ranking, default model, or unfreeze Selection/Expansion.
6. Merge/promote/release or replace installed binary/daemon/index.
7. Open formal GQ5 or a new product-domain campaign.
8. Publish a public RAG/evidence surface.

## 3. Open blockers

### B1 — No external-agent RAG MVP

R1 evidence is library-only; no CLI/MCP/daemon consumer, no versioned public
response, no unsupported behavior policy. R5/R6 absent.

### B2 — No independent qualification of GS

`typed-grounding-v1` is frozen, but GQ5 was not run. GQ4 is observed and tiny.
No human-independent adjudication.

### B3 — R4 failure remains unresolved

Bounded expander failed; perfect selector over old six calls cannot reach target;
new retrieval/discovery needs owner approval. R1 remains fallback.

### B4 — Historical measurements mixed

E5/E6 invalidated old route tables. Old R4 tables use oracle B0 (0.4722), while
fixed deployable FTS is 0.3750. Any future report must name the baseline and
measurement contract.

### B5 — Source/installed drift

Source is v3/E8-P2; installed is v2/Potion/older binary. No replacement
authorization. Release/rollback rehearsal required.

### B6 — Plan/governance drift

`docs/rag` says PROPOSED and does not include S1/S1b/RefInt/Evidence/GQ/GQR/GS.
The project is running post-plan research without an updated canonical plan.

### B7 — Native concurrency/lifecycle limits

Native zvec 0.8 optimize/lifecycle constraints restrict maintenance outside the
state lock; E1 deferred policy and #10 remain unimplemented.

### B8 — Product/release definition

RC release is merge-ready but production rollout is NOT READY. No owner
decision to release RAG or to expose R1 publicly.

## 4. Non-goals / pitfall register

- Do not treat `api::evidence` as a product CLI.
- Do not treat `typed-grounding-v1` readiness as qualification.
- Do not use GQ4 as independent held-out data after seeing it.
- Do not reuse old GQ/GQ2/GQ3 negative-basis receipts as negative-safety proof.
- Do not quote E4 FTS/vector route numbers as pure routes.
- Do not compare B0 oracle with fixed deployable FTS without labeling.
- Do not compare pack-only tokens with search+read tokens.
- Do not treat structural reachability as task relevance.
- Do not infer index v3 from source when installed manifest says v2.
- Do not infer active Gemma from a cached GGUF.
- Do not infer `origin/main` from `work/zg-recovered`.
- Do not merge a research branch because it is “latest”.
- Do not run a rebuild on a production index to test compatibility.
- Do not treat a passing unit suite as production readiness.

## 5. Recommended next owner decisions

| Priority | Decision |
|---:|---|
| P0 | Keep installed v2/Potion unchanged until a release/rollback rehearsal is authorized. |
| P0 | Decide whether `docs/rag` remains the canonical R0–R6 plan or is amended with a post-R4 grounding track. |
| P1 | Authorize or decline GQ5 for `typed-grounding-v1`; do not merge before it. |
| P1 | Decide R1 product path: library-only, public CLI/MCP evidence, or no RAG integration. |
| P2 | If R4 is revisited, write a new preregistration for retrieval/discovery, not another packing/selection tweak. |
| P2 | Close E3-02B owner review; do not reopen based on invalid attribution. |
| P3 | Reconcile RC release notes with E8-P2/source v3 before any release claim. |

## 6. Decision authority map

| Area | Authority |
|---|---|
| source code/docs | repository owner / mission authorization |
| production binary/daemon/index | owner only |
| index schema/migration | owner only |
| public API/release | owner only |
| benchmark targets/preregistration | owner/frozen target before measurement |
| post-R4 research branches | mission owner; no automatic promotion |
| this KB | documentation only; no execution authority |

## 7. Evidence anchors

- `docs/rag/implementation-plan.md:5-16,100-140`.
- `docs/rag/README.md:3`.
- `rust/docs/rag-r1-release-gates.md:1-45`.
- `rust/docs/rag-r4-*` (candidate, follow-up, bottleneck, D1/D2).
- `rust/docs/e5-*`, `e6-*`, `e7-*`, `e8-*`.
- `rust/docs/rag-gq-handoff-20261115.md` @ `0843ae5c`.
- `rust/docs/rag-gqr-*` @ `39e75d48`.
- `rust/docs/rag-gs-*` @ `102f214`.
- `docs/release/RELEASE-READINESS.md:1-25`.
