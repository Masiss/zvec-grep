# PROJECT-STATE — verified snapshot

**as_of:** 2026-11-15T20:16+07:00  
**Nature:** read-only archaeology; no production mutation.

## 1. Git identities

| Identity | SHA | Meaning |
|---|---|---|
| This source checkout | `27b81c0e0472644820955fd69d1a080fb76b16ca` | `work/zg-recovered`, maintained recovery branch |
| `origin/work/zg-recovered` | `27b81c0…` | published recovery branch |
| `origin/main` | `2d7d2ac220d1baf840694d937fea7054d091e33d` | remote main snapshot (2026-10-15) |
| `main` (local) | `f95fc49ef793ee47e697ded6ac5fb6d7ecbf4d0f` | comparison branch (2026-10-13), not current product |
| `upstream/main` | `e1f93d257800f8626eb877eaf45573518076ab65` | upstream Rust main (2026-10-30) |
| RC release code | `289674bc127f664fea0993381c6d109302af95ae` | 0.3.0-rc.2 packaging |
| RC tag commit | `c2347d5` | `rust-v0.3.0-rc.2` |
| E8-P2 pre-merge rollback | `6d5b523369c7b064d5c7ac15c6fa91cfcf3f1c8f` | tag `rollback/pre-e8p2-merge-20261115` |
| Latest post-R4 GS branch | `102f214c71884712ce8d4f7ee2dd341257ec3fb2` | `work/rag-gs-20261115`; docs-only context, not merged |

`work/zg-recovered` is **206 commits ahead** of `origin/main` and **213 ahead**
of local `main`; it is not the same tree as `origin/main`. The maintained branch
is the recovery/release candidate line, not a clean `main`.

## 2. Installed CLI and native library

| Item | Observed value |
|---|---|
| Executable | `$HOME/.local/bin/zg` |
| Binary SHA-256 | `6db0f67e05e61ad6d50e8e9ea7b3e4593cfb07c99ac0af9f5064f48398db7ed4` |
| CLI version | `0.0.1` |
| Native library | `$HOME/.local/lib/libzvec_c_api.so` |
| Native SHA-256 | `532f16dd91574cc692ba5f36f855dd187f04a40afe29dde48fa47a35f8c13c38` |
| Binary build ID | `78ee4a26d0a81265061f22f8cfa781e2422af7d6` |
| Rollback pair | `$HOME/.local/share/zvec-grep/rollback-20261101T130300Z/` |
| Rollback binary | `ad1d9c2c1aca53d1b1c113ca84f5b1006289592df9e8e85b9f18d1629fba7d0a` |
| Rollback lib | `5093c7d722304b0efaa7425f206456ca902dc377d5a0ae8056f1c5c010fe763e` |

The installed binary is **not** a build of source HEAD `27b81c0`. The CLI
version string `0.0.1` is not the Rust workspace package version
`0.3.0-rc.2`.

## 3. Running daemon / server

| Item | Observed value |
|---|---|
| PID | `161009` |
| Command | `zg --server run --mcp-toolset agent --listen 127.0.0.1:7999 --home $HOME/.zvec-grep` |
| Runtime | `~2d 10h` at snapshot, started before this audit |
| Loaded native lib | `532f16dd…` via `/proc/161009/maps` |
| Server state | `Server: ready`; URL `http://127.0.0.1:7999/mcp` |

The daemon was **not** restarted, replaced or signalled. Its loaded library is
the current installed library above.

## 4. Active workspace index

Workspace: `/home/masis/src/zvec-grep`

| Item | Observed value |
|---|---|
| Manifest | `.zvec-grep/manifest.json` |
| Manifest SHA-256 | `0539af2c0e55b2a1515a466537bdcaecd509466e3ddddf4f0af560aa1ff32e47` |
| Index version | **2** |
| Active model | `local/potion-code-16m-v2` |
| Dimension/metric | 256 / cosine |
| FTS | `jieba`, lowercase filter |
| Generation | `6e3c3a4d-4690-4868-8f9e-1023bbb7577c` |
| Storage generation size | ~216 MB observed (`du -sh`) |
| Legacy `index/` directory | ~2.4 GB observed; not equivalent to the active generation |
| `zg --status` | “Workspace index status is unknown” |
| `zg --verify` | `PASS_COMPLETE`, native ABI 1 |

Do **not** infer freshness from `--status` alone. `--verify` was read-only and
PASS_COMPLETE for this generation; no query/indexing mutation was performed by
this audit.

## 5. Model/runtime inventory

- Active index embedding: **Potion Code 16M v2**, 256d, cosine, local.
- Gemma GGUF artifact is present under `$HOME/.zvec-grep/models/` (333,590,944
  bytes) but is **not the active model** of the current workspace index.
- No `ZVEC_GREP_*` embedding/model override was present in the audit shell.
- The active config is therefore the persisted manifest, not the catalog
  default and not the presence of a cached Gemma file.
- Installed indexes are **v2**; `work/zg-recovered` source has
  `CURRENT_INDEX_VERSION = 3` after E8-P2. V2 is not silently migrated.

## 6. Product / RAG state

| Layer | State |
|---|---|
| Search product | Rust CLI/MCP/daemon line; native zvec 0.8; FTS/vector/hybrid |
| Source branch | E8-P2 merged into `work/zg-recovered`; index v3, no-op fast path, E2 reuse, explicit Vulkan |
| Installed | CLI `0.0.1`, native lib `532f…`, index v2, Potion; not E8-P2 |
| RAG R0 | Frozen scope/cases/budgets and B0 receipts present |
| RAG R1 | Engine/library evidence API; dev pilot PASS; no CLI/MCP/daemon consumer; public release blocked |
| RAG R2/R3 | C# and SQL source-visible facts implemented; no compiler/DB authority |
| RAG R4 | Bounded expander candidate and follow-up **FAIL**; full agent loop not implemented |
| RAG R5/R6 | **Not implemented** |
| Post-R4 | Separate experimental branches; latest GS `typed-grounding-v1` frozen for independent qualification; GQ5 **not run** |
| Public RAG exposure | None found in CLI/MCP |

## 7. Current active phase

The **source** branch is at E8-P2 post-merge stabilization
(`27b81c0`) and the latest RAG research branch is GS readiness
(`102f214`). The **installed/runtime** system is still the older v2/Potion
line. These are separate operational states.

## 8. Accepted decisions (short)

- CPU is the default embedding backend; Vulkan/CUDA/Metal are explicit opt-in
  and write/rebuild-only where the contract says so.
- E5/E6 corrected benchmark interpretation: FTS is model-independent; the old
  E4 “FTS+Vector” contamination is invalid and must not be quoted as pure-route
  numbers.
- R1 lightweight internal evidence is the safe fallback; R4 expansion is NO-GO.
- Selection/Expansion remain frozen; post-R4 candidates were not promoted.
- No production schema, model default, ranking or installed binary changed by
  the RAG research chain.
- Documentation/readiness is not qualification.

## 9. Open blockers / next owner decision

1. Decide whether to amend `docs/rag` to include the post-R4 grounding track or
   formally keep it as a research spike.
2. Authorize or decline an independent GQ5 campaign for `typed-grounding-v1`.
3. Decide the product path: R1 library-only, public CLI/MCP evidence API, or no
   RAG integration for now.
4. If pursuing release, close R1 integration/versioning/cache/security/
   performance/release gates; R5/R6 are not done.
5. Do not replace the installed v2/Potion binary/index until a release and
   rollback plan is separately authorized.

See [08-DECISIONS-AND-OPEN-QUESTIONS.md](08-DECISIONS-AND-OPEN-QUESTIONS.md).
