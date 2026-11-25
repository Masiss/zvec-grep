# Repository consolidation — 2026-11-25

## Result

`main` is the canonical development branch locally and on origin. It contains
the qualified RC3 implementation and the RC3 handoff. Runtime provenance remains
`rust-v0.3.0-rc.3` @ `6cd4bdeddef4e8490ca390113edfc7250c56979c`; later `main`
commits are documentation-only.

No force-push, upstream synchronization, engine behavior change, binary/native
library/daemon/index/model change, or RAG implementation merge was performed.

## Ancestry and source equivalence

- Before: `main` = `origin/main` = `2d7d2ac`.
- `main` was a direct ancestor of `work/zg-recovered` (`c200a51`), divergence
  0/211; fast-forward was possible and no merge commit was created.
- `work/core-acceptance-20261124` (`6cd4bde`) was already an ancestor of
  `work/zg-recovered`.
- Final acceptance requires full SHA equality: local `main` = live
  `origin/refs/heads/main` = detached dogfood worktree `HEAD` = final commit `F`.
- The complete `6cd4bde..F` diff is restricted to the documentation allowlist:
  `README.md`, `HANDOFF.md`, `HANDOFF-20261031-NEXT.md`, `rust/AGENTS.md`,
  `rust/docs/branch-recovery-status.md`,
  `rust/docs/continuation-20261125-rc3-promotion.md`, and this file. The root
  README banner was corrected from the retired `work/zg-recovered` branch to
  canonical `main`.

## Worktrees

Exactly two local worktrees remain:

1. `/home/masis/src/zvec-grep` — main worktree on `main`.
2. `/home/masis/src/zvec-grep-core-20261124` — dogfood worktree detached at
   canonical `main`; its v3 index remains usable.

The clean R4 evidence worktree was removed after its ignored index/state was
archived externally.

## Active-branch disposition

| Branch | Before | Disposition | Preservation |
|---|---|---|---|
| `work/zg-recovered` | `c200a51` | Promoted into `main`, then retired | Tag `archive/zg-recovered-c200a51` |
| `work/core-acceptance-20261124` | `6cd4bde` | RC3 qualification ancestor, retired | Tag `rust-v0.3.0-rc.3`, `archive/core-acceptance-rc3-6cd4bde` |
| `work/source-discovery-autonomous-20261122` | local `6635665` + dirty; origin `d6e5609` | Research-only, retired | Tags `archive/source-discovery-remote-d6e5609`, `archive/source-discovery-checkpoint-0dc8baf`; external dirty archive |
| `work/r4-evidence-representation-20261119` | `e7d0a89` | Research-only, retired | Tag `archive/r4-evidence-e7d0a89`; external ignored-state archive |
| `work/rag-requal-20261115` | local two commits ahead | Published, then local ref retired | Origin branch and local-ahead commits preserved |

Historical remote `work/*` research branches remain as read-only archive refs.
Their local refs were removed after verifying equality with origin or publishing
local-ahead commits. They are not maintained development branches.

## Research value preserved

### Source discovery

- Offline Rust/Python/TypeScript/C# prototype and frozen fixtures.
- Partial-identifier/caller-link evaluation and agent-guidance change.
- Public RAG task assets and later harness/research summaries.
- Adoption remains INCONCLUSIVE; no implementation entered `main`.

### R4 evidence

- R4 adapter/harness, semantic evaluator workflow, read accounting and
  evidence-integrity fixes.
- Public evidence contract replay, D01/D12 diagnostics and native-pilot hold
  decision.
- No formal answer-quality winner; implementation remains research-only.

## Dirty-work preservation

External receipts live under
`/home/masis/.cache/zvec-grep/repo-consolidation-20261125/`.

- `archive/source-discovery-untracked.tar.zst` — every untracked non-ignored
  source-discovery/research file, verified against the original file list.
- `archive/source-discovery-tracked.patch` — binary diff of tracked changes.
- `archive/source-discovery-untracked-manifest.json` — per-file SHA-256 receipt.
- `archive/r4-evidence-ignored.tar.zst` — ignored R4 worktree state, including
  its model/index, before worktree removal.

The published research checkpoint contains only the exact 74-file allowlist.
The staged-tree credential scan found no real credential; its only regex hit in
research history was the deterministic test fixture `direct-status-secret`.
Raw/build/model artifacts were not published.

## Runtime and rollback

The installed RC3 binary, native libraries, daemon, runtime home, Potion model,
project indexes and rollback checkpoints were not changed by consolidation.
Operational details remain in
[`continuation-20261125-rc3-promotion.md`](continuation-20261125-rc3-promotion.md).

## Verification limits

- Python benchmark tests were not run because `pytest` is unavailable in the
  system Python; this is recorded in the external receipts.
- Node contract/install tests for the research checkpoint passed 25/25 and
  80/80.
- No new RAG or performance research was run.
