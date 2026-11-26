# RAG implementation and qualification plan — 2026-11-26

## Current checkpoint

- Local binary/daemon: clean `6b6f294`; default RAG behavior unchanged.
- Canonical repo: `main`, clean; RAG research branch retained.
- C# benchmark v2: SQL-inclusive, explicit BOM-aware gold; baseline 2/12
  all-gold, 3/25 exact.
- Direct exact-file/header v1: offline PASS, only D10 changed; C# reached 3/12
  all-gold and 5/25 exact with higher precision and lower tokens.
- Rust: best broad ranking experiment remains multi-route v1 at 3/12 all-gold and
  9/30 exact, NO-GO; R11 remains unresolved.
- No engine port, held-out evaluation or runtime p95 has been performed for the
  direct-file route.

## Goal

Convert the one successful offline policy into a measurable, opt-in engine
candidate without regressing current behavior, then qualify it on held-out data
before considering promotion. Treat remaining benchmark/query defects separately
from implementation.

## Non-goals

- No new weighted ranker or broad multi-route tuning.
- No resolver-slot or declaration-cap increase without new evidence.
- No default-on flag, production reindex or daemon switch in the experimental
  phases.
- No silent comparison between benchmark versions with different coverage/gold.
- No gold-aware features in any candidate policy.

## Phase 1 — Opt-in engine port of direct-file/header route

**Priority: highest. This is the only current policy with a frozen offline PASS.**

Implementation:

1. Add an opt-in flag, tentatively `ZVEC_RAG_DIRECT_FILE_HEADERS=1`.
2. Reuse existing source reading, UTF-8/BOM handling, evidence clipping and pack
   serialization utilities; do not create a parallel pack format.
3. Implement the locked policy:
   - explicit identifier to exact file-stem matching;
   - at most two files/windows, <=1024 bytes each;
   - namespace/class header start and UTF-8-safe end;
   - one referenced-type file selected by identifier-prefix affinity;
   - direct windows before C0 fill, with C0 remainders clipped against them;
   - unchanged 24 KiB/64-item caps.
4. Keep flag-off behavior byte-for-byte unchanged.

Required tests:

- no identifier/no file match;
- BOM and non-BOM files;
- namespace/class header boundaries;
- UTF-8 boundary adjustment;
- dependency chooses `CoreDBContext`, not inheritance `BaseService`;
- duplicate/already-covered windows leave the pack unchanged;
- byte/item caps and remainder clipping;
- Rust and C# fixtures;
- D10 integration parity with the offline receipt.

Phase exit gates:

- Flag off: 24/24 existing parity packs unchanged.
- C# v2 flag on: reproduces direct-file v1 scores and only D10 changes.
- Zero verifier gaps; no source/index/model mutation.
- Any failure stops the port; no policy tuning inside the phase.

## Phase 2 — Measure the same port on Rust

Run the identical opt-in policy on the frozen Rust benchmark; do not port the
C#-specific namespace heuristic blindly without a Rust declaration analogue.

Phase exit gates:

- R10 remains 3/3.
- No C0 all-gold loss, including R09.
- Exact/precision do not regress; tokens <=1.10x C0.
- Report whether any Rust case improves. If none, keep the feature C#-only or
  reject it for Rust; do not broaden the heuristic merely to create a gain.

## Phase 3 — Repair query/benchmark context separately

D07 and D12 refer to omitted context (`Proc tên này`, side effects without the
proc name). They cannot fairly test retrieval when the identifying name is not
in the question.

Deliverable:

- benchmark v3 with explicit query context or a documented `context_required`
  field;
- freeze before running candidates;
- preserve v1/v2 and publish a versioned erratum;
- rerun C0 and direct-file baseline on v3.

This phase does not change engine code and must not be mixed with Phase 1/2
quality claims.

## Phase 4 — One arm per remaining hard blocker

Only after Phases 1–3:

1. **D04:** test the minimal stored-metadata exact-symbol signal that already
   rescued D04 in hybrid v1. Do not reintroduce the full multi-route scheduler.
2. **R11:** test a targeted same-entity coherence rule that keeps related
   fragments together, with R10/D11 as mandatory regressions. Do not reuse the
   broad scheduler v1 that lost R10.
3. Each arm gets its own preregistration, fixed pool/index/model, and stop rule.
   Do not combine multiple new policies in one replay.

## Phase 5 — Held-out qualification

Create a held-out set before viewing candidate results:

- both Rust and C#;
- explicit file identifiers and implicit questions;
- BOM/non-BOM, SQL/non-SQL, small/large declarations;
- no overlap with the 24 frozen development cases.

Compare flag-off C0 against the opt-in candidate. Required gates:

- no all-gold regression;
- exact and macro precision improve or remain neutral per split;
- tokens <=1.10x C0;
- zero integrity gaps;
- real-binary p95 within the preregistered latency budget;
- report per-case wins/losses and failure taxonomy.

## Phase 6 — Promotion decision

Promotion requires all of:

- Phase 1/2 engine parity and regression tests;
- held-out gates in Phase 5;
- clean release build and self-contained `bin/lib` packaging;
- controlled daemon deployment with rollback;
- explicit owner approval.

Until then the flag remains off and the current production ranker/selector stays
unchanged.

## Immediate next action

Implement Phase 1 only: opt-in direct-file/header port with tests and frozen
parity. Stop at the Phase 1 exit gates before running Rust or held-out work.
