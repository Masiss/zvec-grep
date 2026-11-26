# RAG fragment-flow status — 2026-11-26

This is the current research checkpoint for the active branch
`work/recover-rag-evidence-20261126`. It does not change the promoted RC3
runtime described in [`continuation-20261125-rc3-promotion.md`](continuation-20261125-rc3-promotion.md).

## Bottom line

- **Implementation**: `DONE` for the corrected fragment-centric flow and all
  four contract fixes.
- **Qualification**: `NO-GO` for promotion. No corrected arm beats both the
  entity-flow baseline and the previous fragment candidate on Rust and C# at
  the same time.
- **Production**: unchanged. No binary promotion, daemon/index/config change,
  model change, held-out access, or new architecture.

## Branch and artifacts

- Active branch: `work/recover-rag-evidence-20261126` @ `bef0252`
- Base for this research line: `main` @ `51c9eba`
- Worktree: `/home/masis/src/zvec-grep-rust-rag-20261126`
- Installed runtime binary remains SHA-256
  `940180c7c74a199c5effbaec604bb69af1a9e9bdfb46881beb6231a838a9be2d`.
- Artifact roots:
  - `~/.cache/zvec-grep/rust-rag-20261126/`
  - `~/.cache/zvec-grep/potion-capability-20261126/`
  - `~/.cache/zvec-grep/discovery-selection-20261126/`
  - `~/.cache/zvec-grep/recover-rag-evidence-20261126/`
  - `~/.cache/zvec-grep/fragment-flow-20261126/`
  - `~/.cache/zvec-grep/fragment-ranking-20261126/`
  - `~/.cache/zvec-grep/correct-flow-20261126/`

## Research progression

| Stage | Branch tip | Result |
|---|---|---|
| Rust RAG investigation | `c20257f` | Independent `clap` corpus; selection dominates; no universal arm |
| Potion capability | `2e932ae` | Static token embedding; no headroom for rerank/late interaction |
| Discovery/selection | `b05960d` | Fusion clean; rank/cutoff and packing lose evidence |
| Recover RAG evidence | `5eab0b5` | RAG-only lineage switches; no universal arm |
| Fragment-centric flow | `0ca0090` | Fragment identity preserved; no universal arm |
| Fragment relevance/selection | `6eef417` | Ranking and budget flow implemented; quality/latency regression |
| Corrected flow | `bef0252` | Four contracts fixed and verified; still NO-GO |

## Current implementation contract

Flow:

`Fragment Discovery → Fragment Fusion → Fragment Ranking → Budget-driven Selection → Verified Evidence Pack`

1. Fusion uses each fragment's own route rank and raw score; entity recall
   contributes only route query text.
2. Ranking reads `SearchHit.signals` (runtime FTS/Potion), never optional
   observability trace.
3. Selection source identity is `(relative_path, file_sha256)`; overlap is
   computed on `content_range`; only duplicate source lines are clipped.
4. Byte budget (24 KiB) is primary; item count (64) is a safeguard.

Env gating keeps normal search unchanged:
`ZVEC_RAG_FRAGMENT_FLOW`, `ZVEC_RAG_FRAGMENT_RELEVANCE`,
`ZVEC_RAG_BUDGET_SELECT`, `ZVEC_RAG_RANK_RRF_ONLY`,
`ZVEC_RAG_FRAGMENT_SELECT_RANK_ONLY`,
`ZVEC_RAG_BYTE_BUDGET`, `ZVEC_RAG_ITEM_BUDGET`,
`ZVEC_RAG_EVIDENCE_PACK_FILE`.

## Verification

- Focused engine suite: **54 passed, 0 failed, 1 ignored**.
- Real trace lineage audit: Rust `400/400`, `400/400`; C# `800/800`, `400/400`
  fragment route rank/score checks.
- Normal-search parity: **6/6 sampled Rust/C# cases byte-identical**.
- Corrected-flow qualification at 24 KiB:
  - K0 baseline exact: Rust `0.250`, C# `0.083`
  - K1 previous fragment exact: Rust `0.153`, C# `0.167`
  - K4 best corrected exact: Rust `0.125`, C# `0.083`; p95 C# `7.77 s`
  - No arm passes the preregistered both-language gate.
- Task correctness Rust: K0 `3/12`, K1 `0/12`, K4 `2/12`; C# `0/12` for all.
  C# keyword grading remains weak/high-abstention.

## Reports and receipts

- `benchmarks/rust-rag/REPORT.md`
- `benchmarks/potion-capability/REPORT.md`
- `benchmarks/discovery-selection/REPORT.md`
- `benchmarks/recover-rag-evidence/REPORT.md`
- `benchmarks/recover-rag-evidence/FRAGMENT-FLOW-REPORT.md`
- `benchmarks/recover-rag-evidence/FRAGMENT-RANKING-REPORT.md`
- `benchmarks/recover-rag-evidence/CORRECT-FLOW-REPORT.md`
- Preregistrations and tools live alongside those reports.

## Selection recovery continuation (2026-11-26)

The owner-authorized execution of `docs/rag/selection-recovery-plan-20261126.md`
is recorded in [`../../docs/rag/selection-recovery-report-20261126.md`](../../docs/rag/selection-recovery-report-20261126.md).
The G1 coordinate adapter was fixed (exclusive evidence endpoint and all
clipped remainders preserved) and an env-gated coverage selector was tested and
rejected. Rust G1K4 exact coverage rose from `0.125` to `0.208`, but C# exact
`0.083` remains below K1 `0.167`; promotion verdict remains **NO-GO**. No
production binary, daemon, index, model, default or held-out data changed.

## Remaining bottleneck and next authorized action

Discovery, fusion and fragment identity preservation are no longer the limiting
stages. The remaining problem is fragment-level relevance under a fixed source
budget: ranked pools still contain gold evidence that selection does not
deliver. No further work is authorized by this status note. Any continuation
must be a new owner-authorized task; production promotion requires a candidate
that passes both-language coverage/precision/latency gates and task
correctness.
