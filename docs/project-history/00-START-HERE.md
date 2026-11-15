# zvec-grep project history — START HERE

**Project:** zvec-grep / `zg`
**Single entrypoint:** upload/read this file first; it links every canonical
topic and the current snapshot.
**Knowledge-base consolidation:** 2026-11-15T20:41+07:00
**Runtime snapshot:** 2026-11-15T20:16+07:00
**Repository revision:** `27b81c0e0472644820955fd69d1a080fb76b16ca` on
`work/zg-recovered` (see [PROJECT-STATE.md](PROJECT-STATE.md))
**Audience:** ChatGPT Project / engineers who need verifiable project context,
not a new implementation plan.

## 1. What this knowledge base is

This directory reconstructs the project from Git history, code, design docs,
benchmark receipts and read-only runtime inspection. It is deliberately
**evidence-first**:

- “planned” is not “implemented”;
- “experimental” is not “merged”;
- “merged” is not “installed”;
- “installed” is not “qualified”;
- “qualified on a dev fixture” is not “production-ready”.

The current verified snapshot and conflicts are collected in
[PROJECT-STATE.md](PROJECT-STATE.md). The source-to-claim map is
[SOURCE-REGISTER.md](SOURCE-REGISTER.md).

## 2. Reading order

| Order | Document | Why |
|---:|---|---|
| 1 | [PROJECT-STATE.md](PROJECT-STATE.md) | One-screen truth: HEADs, installed/runtime identity, active track, decisions |
| 2 | [01-PRODUCT-AND-ARCHITECTURE.md](01-PRODUCT-AND-ARCHITECTURE.md) | What the product is, module boundaries, external-agent seam |
| 3 | [02-TIMELINE-AND-GIT.md](02-TIMELINE-AND-GIT.md) | Node → Rust, release, recovery, E-series and RAG chronology |
| 4 | [03-NATIVE-DB-INDEX-AND-STORAGE.md](03-NATIVE-DB-INDEX-AND-STORAGE.md) | Native DB, schema/version, FTS/vector, recovery/freshness |
| 5 | [04-EMBEDDINGS-AND-BENCHMARKS.md](04-EMBEDDINGS-AND-BENCHMARKS.md) | Models, E1–E8, corrected metrics, CPU/Vulkan |
| 6 | [05-RAG-ORIGINAL-R0-R6.md](05-RAG-ORIGINAL-R0-R6.md) | Canonical R0–R6 meaning and actual status |
| 7 | [06-RAG-POST-R4-RESEARCH.md](06-RAG-POST-R4-RESEARCH.md) | Requal → S1/S1b → RefInt → Evidence → GQ/GQR/GS |
| 8 | [07-OPERATIONS-AND-RELEASE-STATE.md](07-OPERATIONS-AND-RELEASE-STATE.md) | Installed vs source, daemon, compatibility, read-only commands |
| 9 | [08-DECISIONS-AND-OPEN-QUESTIONS.md](08-DECISIONS-AND-OPEN-QUESTIONS.md) | Owner decisions, blockers, authority gates, non-goals |
| 10 | [09-RAG-EXECUTION-DELTA.md](09-RAG-EXECUTION-DELTA.md) | Canonical R0–R6 vs verified reality; shortest justified MVP path |
| 11 | [10-REVIEW-AND-VERIFICATION.md](10-REVIEW-AND-VERIFICATION.md) | Independent review, negative controls, residual gaps |
| 12 | [CHANGELOG.md](CHANGELOG.md) | Knowledge-base change log; not engine history |
| 13 | [HANDOFF-20261115.md](HANDOFF-20261115.md) | Resumable checkpoint and next-session constraints |

## 3. Canonical sources (priority order)

1. **Git commit/tree + source code** at a named SHA.
2. **Executable receipts/manifests** with hashes (`benchmarks/rag-impact/results/`,
   `benchmarks/retrieval-evidence/`, external `~/.cache/zvec-grep/*/receipts/`).
3. **Design/handoff docs** at the matching revision (`docs/rag/`,
   `rust/docs/`, `docs/release/`).
4. **Read-only runtime observations** (binary/library hashes, process, manifest,
   `zg --status`/`--verify` output). These describe *now*, not source history.
5. **Chat/history seed and summaries** — `OWNER_REPORTED_UNVERIFIED` unless
   corroborated by 1–4.

Do not promote a lower layer over a higher one. A later report may correct an
earlier measurement; both remain historical evidence.

## 4. Current one-line status

- The project started as a Node/TypeScript search/MCP product and gained a Rust
  workspace in September 2026; `work/zg-recovered` is the maintained branch,
  not `origin/main`.
- Native index/storage, FTS, FP16 vectors, E1–E8 and RAG R0–R4 have extensive
  receipts. E5/E6 invalidated some earlier benchmark interpretations.
- **R1** is an internal/library evidence API with a dev pilot, not a CLI/MCP
  product. **R4** candidate expansion failed and is NO-GO. **R5/R6** were not
  implemented.
- Post-R4 S1/S1b/RefInt/Evidence/GQ/GQR/GS are separate experimental branches;
  `typed-grounding-v1` is frozen only for an independent qualification that has
  **not** been run.
- Current installed `zg 0.0.1`, native lib and daemon use **index v2** and
  `local/potion-code-16m-v2`; source `work/zg-recovered` is index **v3** after
  E8-P2. Do not treat installed and source as the same identity.

## 5. Uncertainty policy

Use these labels in new analysis:

- `VERIFIED`: direct source/receipt/runtime evidence.
- `VERIFIED_READ_ONLY`: runtime observation; snapshot-sensitive.
- `HISTORICAL_USER_REPORTED`: a user/chat report from a named date, not
  independently repo-fetched; may be accurate but is not a receipt.
- `INFERRED`: conclusion derived from stated evidence; state the inference step.
- `SUPERSEDED`: replaced by a later, better-scoped result; retained as history.
- `HISTORICAL_ONLY`: superseded measurement or branch, retained for history.
- `CONFLICT_UNRESOLVED` / `UNRESOLVED`: two credible sources disagree or no
  sufficient source exists; do not force a PASS.
- `OWNER_REPORTED_UNVERIFIED`: only a chat/owner summary supports it.
- `NOT_FOUND_IN_SCOPE`: scan scope did not find it; **not** proof of absence.

### 5b. Capability status vocabulary

These describe **capability layers**, not interchangeable praise:

| Status | Meaning |
|---|---|
| `PROPOSED` | documented intent only; no execution authority |
| `IMPLEMENTED_EXPERIMENTAL` | branch/fixture code; not main |
| `MERGED_INTERNAL` | code in `work/zg-recovered`; no public guarantee |
| `TESTED_PASS` | measured inside a named protocol |
| `QUALIFIED` | independent qualification inside a named scope |
| `INSTALLED` | executable/native artifact on disk checked |
| `ACTIVE_RUNTIME` | running process/model/index checked |
| `PUBLIC_EXPOSED` | tested CLI/MCP/daemon contract |
| `NO_GO` / `BLOCKED` / `UNKNOWN` | named gate failed, blocked, or not measured |

Never infer a higher layer from a lower one. `MERGED_INTERNAL` ≠ `INSTALLED`;
`TESTED_PASS` ≠ `QUALIFIED`; `QUALIFIED` ≠ `PUBLIC_EXPOSED`.

## 6. Coverage matrix

| Domain | Document |
|---|---|
| Product/architecture, Node→Rust, CLI/MCP/daemon | 01, 02 |
| Scanner, workspace, native schema, FTS/vector, versions/recovery | 03 |
| Models, quantization, CPU/Vulkan/CUDA/Metal, E1–E8, corrected metrics | 04 |
| Canonical RAG R0–R6 | 05 |
| Post-R4 research chain | 06 |
| Installed/runtime/release identity | 07 |
| Decisions, blockers, authority | 08 |
| Execution delta / MVP gap | 09 |
| Claim-to-source map | SOURCE-REGISTER |
| Independent review / negative controls | 10 |
| ChatGPT Project instructions | PROJECT-CHAT-INSTRUCTIONS |
| Documentation change log | CHANGELOG |
| Resumable handoff | HANDOFF-20261115 |

## 7. Explicit non-goals of this documentation branch

- No feature implementation, new benchmark, GQ5, index write, migration,
  daemon restart or installed-binary replacement.
- No merge to main or release.
- No reinterpretation of owner-approved targets as new targets.
- No secrets, credentials or private source snapshots in portable docs.
