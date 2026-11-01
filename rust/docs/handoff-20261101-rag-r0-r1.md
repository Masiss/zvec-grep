# Handoff — RAG R0/R1 (2026-11-01)

Next session starts here. Storage integrity is closed; do **not** reopen it or
open a new optimization track. The active work is RAG R0 freeze, then R1 pilot
validation.

## 1. Current state

| Item | State |
|---|---|
| Storage integrity W4 | **PASS; closed; installed locally** (`ed8d23d`) |
| RAG R1 evidence contract | **PASS on synthetic fixtures** (`d1114d1`) |
| RAG R0 pilot freeze | **OPERATION_BLOCKED: checkout absent** |
| RAG pilot validation | **Not started** |

Baselines/commits:

- W3 `5b218ae` — native verifier.
- W4 `ed8d23d` — `zg --verify`, light seal check, local rollout.
- R1 `d1114d1` — `api::evidence`, R0 artifacts.

Installed pair (unchanged by R1; R1 is internal engine API only):

- `/home/masis/.local/bin/zg` — SHA-256 `6db0f67e05e61ad6d50e8e9ea7b3e4593cfb07c99ac0af9f5064f48398db7ed4` (0.0.1)
- `/home/masis/.local/lib/libzvec_c_api.so` — SHA-256 `532f16dd91574cc692ba5f36f855dd187f04a40afe29dde48fa47a35f8c13c38`
- Daemon `127.0.0.1:7999` was ready after rollout; re-check with
  `zg --server status --check-ready`.
- Rollback: `/home/masis/.local/share/zvec-grep/rollback-20261101T130300Z/`
  (`zg` `ad1d9c2c…`, library `5093c7d7…`). Copy both back and restart.

Do not upgrade zvec 0.8.0, push/deploy, or edit/delete live indexes.

## 2. R0 blocker — restore the pilot first

Declared pilot: `/home/masis/itc/Ricons/HRM2.0`
(checkout root `/home/masis/itc/Ricons/`).

On 2026-11-01 neither path existed. Therefore no source hash, line range or
reviewer holdout was frozen. The artifacts deliberately contain `null` hashes
and `frozen:false`.

R0 artifacts:

- `benchmarks/rag-impact/scope-manifest.json` — selected projects/anchors,
  exclusions, blocker, `null` hashes.
- `benchmarks/rag-impact/cases/dev.json` — 16 proposed development cases,
  unfrozen.
- `benchmarks/rag-impact/cases/heldout.json` — H01–H08 placeholders; a reviewer
  independent of tuning must author them.
- `benchmarks/rag-impact/schema/evidence-pack.schema.json`.
- `benchmarks/rag-impact/REPORT-CONTRACT.md`.

Read first:

1. `docs/rag/README.md`
2. `docs/rag/implementation-plan.md` (§2 R0, §3 R1, §9 budgets)
3. `docs/rag/hrm2-pilot.md`
4. `benchmarks/rag-impact/README.md`
5. `rust/docs/rag-r0-r1.md`

## 3. R1 implementation

`rust/crates/zg-engine/src/api/evidence.rs` (`pub mod api::evidence`).

- `EvidenceRequest`: project, relative path, whole-file SHA-256, half-open UTF-8
  byte range, optional expected start/end line.
- `EvidenceBudget`: `max_bytes`, `max_items`.
- `assemble(root, requests, budget) -> Result<EvidencePack, EvidenceError>`.
- `EvidencePack`: `ok` / `partial` / `empty` / `failed`, validated `EvidenceItem`s,
  typed `EvidenceGap`s, dedup count.
- `revalidate(root, pack)` before a final answer.

Fail-closed cases: traversal, absolute path, symlink/out-of-root, missing,
stale hash, invalid range/UTF-8, stale coordinates, snippet/item budget, and
post-assembly source changes. Same relative name in different projects stays
separate. Empty request list is `empty`; zero budget is an input error.

No search/MCP/CLI response shape changed. No C#/SQL parser yet.

Tests:

```sh
cd /home/masis/src/zvec-grep/rust
cargo test -p zg-engine --lib api::evidence
```

Result: **8 passed / 0 failed**. Final full engine lib run:
**571 passed / 0 failed / 39 ignored**. CLI **95**, wrapper **4**.

## 4. Next steps, in order

1. **Restore the read-only checkout** at the declared path (or record a new
   approved path and update every doc/manifest reference).
2. Verify the checkout HEAD and cleanliness. Declared HEAD:
   `d5e8fbcdd49b48c208887096993074464c793cd7`. If it moved, record the reviewed
   HEAD and re-anchor all paths; do not silently reuse old line numbers.
3. Fill every `sha256` and exact span in `scope-manifest.json` (P1–P3, C1–C7,
   S1–S2). Keep `_old` SQL variants excluded; never infer live deployment.
4. Revalidate the 16 development cases and record facts/spans/forbidden claims.
   Cases not supported by the checkout become gaps, not invented gold.
5. Have a reviewer independent of candidate tuning author and freeze H01–H08.
   Do not copy D01–D16 and rename IDs.
6. Accept or adjust the numeric targets in `implementation-plan.md` §9
   **before** any candidate measurement.
7. Run the first executable slice: D02 (personal update writes), D07 (restore
   side effects), plus negative twins D04/D06/D12, through the R1 evidence pack.
8. Only after R0/R1 pilot validation, start R2 (scoped C# facts). Do not add a
   graph store, background LLM, DB connection, build/restore or code execution.

R0 gate is not PASS until steps 1–6 are complete. If the corpus remains
unavailable, report `OPERATION_BLOCKED`; do not manufacture hashes or holdout
cases.

## 5. Working-tree caveat

The worktree already contained unrelated modifications before W4/R1 (M4
research, profiling features, etc.). The W4/R1 commits stage only their own
files/hunks. `rust/crates/zg-engine/Cargo.toml` and `src/lib.rs` intentionally
retain unstaged pre-existing hunks (`native-profile`, `rustix` dev-dep,
`NativeLifecycleTestWorkspace`). Preserve them; do not sweep unrelated changes
into a RAG commit.

R0/R1 evidence:

- `benchmarks/rag-impact/results/r1-contract.json`
- `benchmarks/harness-v2/operations/deep-storage-integrity/w4/` (W4 receipts)
