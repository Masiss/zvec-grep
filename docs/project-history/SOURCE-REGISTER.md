# SOURCE REGISTER

This register maps material claims in this knowledge base to verifiable
sources. Pointers use `path:line @ SHA` for source-tree files and branch docs;
runtime observations are marked with the read-only command/snapshot.

## 1. Audit scope and limitations

**Inspected:**

- repository worktree and Git objects at `27b81c0`;
- local and remote branches, tags, worktrees, `origin/main`, `upstream/main`;
- `docs/rag/`, `docs/release/`, `rust/docs/`, `rust/ARCHITECTURE.md`, engine
  storage/workspace/API sources;
- benchmark docs and manifests under `benchmarks/rag-impact/` and
  `benchmarks/retrieval-evidence/`;
- post-R4 branch docs at their tips;
- read-only runtime process/binary/library/manifest/status/verify;
- the external ChatGPT context package
  `/home/masis/downloads/zvec-grep-project-context-20261115/` (13 files).

**Not fully inspected / unavailable:**

- every external receipt under `~/.cache/zvec-grep/*` was not re-hashed
  exhaustively; key hashes are cited where documented;
- HRM2.0 source snapshot is external and referenced by relative anchors/hashes,
  not copied;
- no live app database, deployed SQL, MSBuild or production HRM runtime was
  queried;
- no human adjudication of RAG labels;
- no GQ5 qualification;
- no exhaustive Node/TypeScript audit beyond history/release docs;
- private credentials/secrets were not read or copied.

`NOT_FOUND_IN_SCOPE` means the scanned repository/evidence did not contain it;
it is not proof of absence from every historical/private location.

## 1b. External historical supplement — provenance

The user supplied `zvec-grep-project-context-20261115`, generated from prior
ChatGPT conversations. It is a **historical supplement**, not a higher-priority
source than Git/source/receipts.

| Item | Value / use |
|---|---|
| Package manifest | `MANIFEST-SHA256.txt` sha256 `17d6fd04…89252d` |
| Master handoff | `history/MASTER-HANDOFF-20261113.md` sha256 `8a3fe79c…e8ec3d` |
| RAG README copy | sha256 `ceea9057…39fd6e`; **byte-identical** to `docs/rag/README.md` |
| RAG plan copy | sha256 `28d8d210…72c0`; **byte-identical** to `docs/rag/implementation-plan.md` |
| HRM2 pilot copy | sha256 `4c72b317…45c6`; **byte-identical** to `docs/rag/hrm2-pilot.md` |

The three original RAG documents were therefore reconciled at byte level, not
just by title. Numbers/claims that appear only in the supplement are marked
`HISTORICAL_USER_REPORTED` in the canonical docs; they do not override direct
receipts.

## 2. Product and architecture

| Claim | Evidence | Confidence |
|---|---|---|
| `ZvecGrep` is the public engine boundary with typed operations | `rust/ARCHITECTURE.md:5-24 @ 27b81c0`; `rust/crates/zg-engine/src/lib.rs` | VERIFIED |
| Transport/MCP stays outside the engine | `rust/ARCHITECTURE.md:26-60 @ 27b81c0` | VERIFIED |
| Crates separate engine/CLI/daemon/protocol/MCP/native | `rust/ARCHITECTURE.md:62-86 @ 27b81c0`; `rust/Cargo.toml` | VERIFIED |
| Node/TypeScript is a separate implementation | `README.md:48 @ 27b81c0`; `package.json:2-10` | VERIFIED |
| Rust package version is 0.3.0-rc.2 | `rust/Cargo.toml:6-11 @ 27b81c0` | VERIFIED |
| Daemon protocol/job semantics | `rust/docs/daemon-cli-ux-v2.md:1-45 @ 27b81c0` | VERIFIED |
| Daemon is resident, one engine Arc | `rust/ARCHITECTURE.md:46-60 @ 27b81c0` | VERIFIED |
| Current installed daemon command and PID | `ps`/`/proc/161009/cmdline` read-only, 2026-11-15T20:16+07 | VERIFIED_READ_ONLY |
| Current installed binary SHA | `sha256sum $HOME/.local/bin/zg` | VERIFIED_READ_ONLY |
| Current installed native lib SHA | `sha256sum $HOME/.local/lib/libzvec_c_api.so` | VERIFIED_READ_ONLY |
| Current server ready on 127.0.0.1:7999 | `zg --server status` read-only | VERIFIED_READ_ONLY |

## 3. Timeline and Git

| Claim | Evidence | Confidence |
|---|---|---|
| Initial import 2026-07-10 | commit `d3e2a47` | VERIFIED |
| MCP/daemon/async index origin July 14 | commits `b802a61`,`5b4eec4`,`3d19924`,`05ddbf3`,`f3f4bc8`,`eca332a` | VERIFIED |
| v0.1.5 tag | tag `v0.1.5` = `cf62cbd` | VERIFIED |
| v0.1.8/v0.1.9/v0.2.0/v0.2.1/v0.2.2 | tags; dates from `git log -1` | VERIFIED |
| Rust workspace integrated alongside TS | commit `b1d0ce9` (2026-09-20) | VERIFIED |
| Rust search becomes default interface | commit `73b84cd` (2026-09-23) | VERIFIED |
| RC1/RC2 | tags `rust-v0.3.0-rc.1`, `rust-v0.3.0-rc.2`; `c2347d5`, `289674b` | VERIFIED |
| Archive/recovery tags | `archive/zg-v1-before-recovery-20261104`, `archive/zg-v2-release-20261104` | VERIFIED |
| Canonical recovery branch | `rust/docs/branch-recovery-status.md:5-20 @ 27b81c0` | VERIFIED |
| `work/zg-recovered` is 206 ahead of origin/main | `git rev-list --left-right --count origin/main...work/zg-recovered` | VERIFIED_READ_ONLY |
| E8-P2 merge | merge `58582d2`; docs `27b81c0`; `rust/docs/e8p2-integration-20261115.md` | VERIFIED |
| Rollback tag | `rollback/pre-e8p2-merge-20261115 = 6d5b523` | VERIFIED |
| Post-R4 branch tips/bases | table in `02-TIMELINE-AND-GIT.md`; `git log`/`merge-base` | VERIFIED |

## 4. Storage/index

| Claim | Evidence | Confidence |
|---|---|---|
| Native zvec 0.8 and FP16 | `rust/Cargo.toml`; `rust/ARCHITECTURE.md:26-48`; `e8p2-integration` | VERIFIED |
| FTS + FP16 vector collections | `rust/crates/zg-engine/src/storage/fragments/mod.rs:1-5 @ 27b81c0` | VERIFIED |
| Workspace generation/manifest/reconciliation layout | `rust/crates/zg-engine/src/workspace/README.md:1-50` | VERIFIED |
| Source version has index v3 | `rust/crates/zg-engine/src/workspace/mod.rs:13 @ 27b81c0` | VERIFIED |
| V2→v3 FTS/scope changes | `rust/docs/index-v3-promotion-qualification.md:50-120` | VERIFIED |
| No auto-migration; rebuild required | `rust/crates/zg-engine/src/workspace/manifest.rs:88-140`; release notes | VERIFIED |
| E2 sidecar identity/crash behavior | `rust/docs/e2-integration-20261112.md:20-75` | VERIFIED |
| E1 deferred maintenance not merged | `work/e1-bounded-maintenance-20261110:rust/docs/e1-bounded-maintenance-results-20261110.md:1-12` | VERIFIED at branch |
| Active installed index v2/Potion | `.zvec-grep/manifest.json`; `zg --status` read-only | VERIFIED_READ_ONLY |
| Installed verify PASS_COMPLETE | `zg --verify` read-only | VERIFIED_READ_ONLY |
| Legacy `index/` size vs generation | `du -sh` read-only | VERIFIED_READ_ONLY |
| Native optimize under state lock | `rust/ARCHITECTURE.md:46-52`; E1 docs | VERIFIED |

## 5. Benchmarks and embeddings

| Claim | Evidence | Confidence |
|---|---|---|
| Potion 16M is catalog default | `rust/crates/zg-engine/src/models/catalog/resolution.rs:80-95` | VERIFIED |
| E4 model compatibility table | `rust/docs/e4-embedding-selection-20261113.md:54-75` | VERIFIED |
| E4 inference/RAM table | `rust/docs/e4-embedding-selection-20261113.md:127-155` | VERIFIED |
| EmbeddingGemma Q8 reference 768d/2048 | same, lines 62/118 | VERIFIED |
| QAT Q4/Q8 blocked by missing projection head | same, lines 63-65/77-96 | VERIFIED |
| E5 harness primary+route contamination | `rust/docs/e5-fts-reproducibility-20261114.md:35-130` | VERIFIED |
| Pure FTS model-independent | E5 §2-3, same file | VERIFIED |
| E4 FTS claim retracted | `e5...:1-30`; E6 | VERIFIED |
| E6 route contract/invalidity matrix | `rust/docs/e6-benchmark-integrity-20261114.md:20-100` | VERIFIED |
| E7 both optimizations NO_GO | `rust/docs/e7-indexing-scalability-20261114.md:173-180` | VERIFIED |
| E8 40-file CPU/Vulkan and RSS | `rust/docs/e8-embedding-acceleration-20261114.md` | VERIFIED |
| E8-P2 282.76/77.41 s, 3.65× | `rust/docs/e8p2-integration-20261115.md:114-124` | VERIFIED |
| Hybrid CPU/Vulkan parity 0.6142 | same, lines 99-106 | VERIFIED |
| Vulkan/accelerator rebuild-only | same, lines 13-20,53-55 | VERIFIED |
| E8-P1 3.20× RSS reduction | `rust/docs/e8p1-vulkan-hardening-20261114.md:120-135` | VERIFIED |
| No-op fast path 0.591→0.047/0.575→0.062, RSS 281→62 MiB | supplement `history/MASTER-HANDOFF-20261113.md` §7; `24af3e6` branch | HISTORICAL_USER_REPORTED (branch exists; exact receipts not re-run) |
| G1–G4 microbench/no end-to-end; G5 slower/rolled back | supplement §5.1; `work/foundation-opt-20261110` @ `d64e54e` | HISTORICAL_USER_REPORTED |
| E1 arm table/FS blocks | supplement §6; E1 branch/results | HISTORICAL_USER_REPORTED; branch doc supports qualitative verdict |
| E2 44× line-shift variant | supplement §8.4; E2 branch docs | HISTORICAL_USER_REPORTED; integration 26.07→9.75 is the canonical receipt |
| Distillation design only (no student/training) | supplement §13; no repo artifact | HISTORICAL_USER_REPORTED / DESIGN_ONLY |

## 6. RAG original plan

| Claim | Evidence | Confidence |
|---|---|---|
| Plan status PROPOSED 2026-10-28 | `docs/rag/README.md:3 @ 27b81c0`; `implementation-plan.md:5` | VERIFIED |
| R0–R6 canonical order | `docs/rag/implementation-plan.md:7-16` | VERIFIED |
| R4 is multi-turn + public integration | `implementation-plan.md:100-115` | VERIFIED |
| R5 is evaluation/footprint | `implementation-plan.md:116-129` | VERIFIED |
| R6 is opt-in release/rollback | `implementation-plan.md:130-139` | VERIFIED |
| MVP definition | `implementation-plan.md:187` | VERIFIED |
| R0 frozen 16 dev + 8 held-out; B0 measured 12 real-source dev | `benchmarks/rag-impact/PREREGISTRATION.md:81-84`; `cases/dev.json` (16); `cases/heldout.json` (8); B0 receipt | VERIFIED |
| B0 best-call 0.4722 | `benchmarks/rag-impact/results/b0-official.json`; R4 docs | VERIFIED |
| Fixed deployable FTS 0.3750 | `rust/docs/rag-r4-d1-results.md:1-16`; D2 | VERIFIED |
| R1 library correctness/pilot | `rust/docs/rag-r1-release-gates.md:1-45` | VERIFIED |
| R1 missing CLI/MCP/daemon | same, line 34 | VERIFIED |
| R2 bounded C# facts PASS | `rust/docs/rag-r2-csharp-facts.md:1-35` | VERIFIED |
| R3 SQL facts PASS; full expansion absent | `rust/docs/rag-r3-sql-facts.md:45-60` | VERIFIED |
| R4 expander FAIL | `rust/docs/rag-r4-candidate-pilot.md:25-70` | VERIFIED |
| R4-A/B/D1/D2 fail | `rag-r4-followup-results.md`, `rag-r4-d1/d2-results.md` | VERIFIED |
| R4 perfect selector ceiling ~9/25 | `rust/docs/rag-r4-bottleneck-and-r1-release-audit.md:112-126` | VERIFIED |
| R5/R6 absent | no files/implementation found in scanned source; plan remains proposed | VERIFIED absence in scope; not universal |

## 7. Post-R4 chain

| Claim | Evidence | Confidence |
|---|---|---|
| Requal Discovery not qualified | `work/rag-requal-20261115:rust/docs/rag-discovery-handoff-20261115.md:1-20` @ published `f93d0499` (local ref `1d8f57a`) | VERIFIED |
| Requal candidate union 18/25 | same handoff, ceiling section | VERIFIED |
| S1 NO_GO, 6/25 | `rag-selection-s1-handoff-20261115.md:1-30` @ `1d8f57a` | VERIFIED |
| S1b NO_GO, 10/25, false edges 75/86% | `rag-selection-s1b-handoff-20261115.md:1-60` @ `ef2a7667` | VERIFIED |
| RefInt `MISSION_PARTIAL`, union 22/25, selection 10/25 | `rag-refint-handoff-20261115.md:1-55` @ `89c182e7` | VERIFIED |
| Evidence binding 15 VERIFIED / 2 ambiguous | `rag-evidence-handoff-20261115.md:1-55` @ `3821168` | VERIFIED |
| GQ NO_GO commented false binding | `rag-gq-handoff-20261115.md:1-30` @ `0843ae5c` | VERIFIED |
| GQR Gate A PASS/GQ4 NO_GO | `rag-gqr-gate-a-20261115.md`; `rag-gqr-gate-b-gq4-20261115.md` @ `39e75d48` | VERIFIED |
| GS readiness only, GQ5 absent | `rag-gs-verification-20261115.md:1-20`; `rag-gs-handoff-20261115.md` @ `102f214` | VERIFIED |
| GS 19 regression / 124 verifier / GQ4 v2 1.0 | `rag-gs-verification-20261115.md`; external receipts | VERIFIED at branch |
| GS freeze `8a26c91e…`, tools `bafa862e…` | `rag-gs-handoff-20261115.md:20-45` | VERIFIED at branch |

## 8. Runtime/installed

| Claim | Evidence | Confidence |
|---|---|---|
| Installed binary SHA/version | `sha256sum`, `zg --version` | VERIFIED_READ_ONLY |
| Installed lib SHA/loaded by daemon | `sha256sum`, `/proc/161009/maps` | VERIFIED_READ_ONLY |
| Daemon PID/start time | `ps -o etime`, `/proc` | VERIFIED_READ_ONLY |
| Index v2/Potion/generation | `.zvec-grep/manifest.json`; schema | VERIFIED_READ_ONLY |
| `--status` unknown, `--verify` PASS_COMPLETE | read-only commands | VERIFIED_READ_ONLY |
| Gemma artifact cached but not active | `find`/model cache; manifest Potion | VERIFIED_READ_ONLY |
| No env override in audit shell | `env | rg ZVEC` empty | VERIFIED_READ_ONLY |
| Rollback pair hashes | `SHA256SUMS` in rollback dir | VERIFIED_READ_ONLY |

## 9. Conflicts and questionable claims

| Conflict | Resolution |
|---|---|
| R4 = full agent loop/public integration vs R4 = bounded expander | Keep canonical meaning; treat expander as a subset/experiment. No full R4 completion. |
| R4 baseline 0.4722 vs fixed 0.3750 | 0.4722 is B0 oracle/best-call; 0.3750 is fixed deployable FTS. Always label. |
| E3/E4 route tables vs E5/E6 | Old FTS/vector numbers contaminated; use E6 contract; E4 inference/RAM still valid. |
| E3-02B NO-GO vs invalid attribution | Rationale depends on invalid table; `OWNER_DECISION_REQUIRED`, not automatic reopen. |
| `docs/rag` PROPOSED vs work done | Plan stale; actual execution R0–R4 partial; post-R4 separate. |
| R1 “public engine API” vs no public consumer | Public as Rust engine library; not public CLI/MCP/daemon. |
| Source index v3 vs installed v2 | Both true; different identities. No silent migration. |
| GS `READY_FOR_INDEPENDENT_QUALIFICATION` vs “qualified” | Readiness only; GQ5 not run; no production claim. |
| `work/rag-s1` tip = requal tip | Branch-label artifact; S1 commit `7d912c1` is in history. |
| CLI `0.0.1` vs package `0.3.0-rc.2` | Different version dimensions; installed binary is older. |
| Token counts absent in GS GQ4 | No token counter binary; not measured, not estimated. |
| FROZEN R0 = 16 dev cases vs “12 dev” | 16 frozen (D01–D16); B0 official receipt measured 12 real-source cases. Both facts retained. |
| S1b supplement says 56 selected / 5 gold; receipt says 57 units / 10 selected spans | Use the executable receipt (`signal-dev.json`: 57 units, 10 selected spans, 47 non-gold); supplement number is `HISTORICAL_USER_REPORTED` and not used. |
| No-op 0.591→0.047 vs 0.53→0.06 | Different measurement runs; same capability. Keep both as historical variants; do not average. |
| E2 26.07→9.75 vs 142.7→4.11 vs 135.95→3.07 | Different workload sizes/qualification/integration runs; not interchangeable multipliers. |
| E8-P1 4.03× vs E8-P2 3.65× | Different runs/builds; E8-P2 integration receipt is canonical for merged claim. |
| E6/engine test counts differ between reports | Report scope/build differs; use the named receipt, do not merge counts. `UNRESOLVED` if a single total is needed. |
| Supplement says GQ5 “not run” vs readiness wording | GQ5 not run is verified; readiness is not qualification. |
| Supplement S1b says false-edge 75/86% while other tables use different denominators | Preserve per-table denominator; never merge true/false counts across sets. |

## 10. Explicit unverified / missing

- Human-independent RAG gold and adjudication: **not found**.
- GQ5 formal qualification: **not run**.
- Full R4 agent loop/public integration: **not found**.
- R5/R6 implementation: **not found**.
- Distillation implementation/student artifact: **not found**; the supplement
  contains a design discussion only, marked `HISTORICAL_USER_REPORTED /
  DESIGN_ONLY`, not a capability.
- Live production HRM2 DB/deployed SQL authority: **not inspected/claimed**.
- Production rollout of RC/E8-P2: **not authorized/verified**.
- Exact native HNSW parameters: delegated to zvec backend; not independently
  established here.
- Exhaustive external cache receipt hash verification: **partial**.

## 11. Refresh protocol

When the repository/runtime changes:

1. Record new `git rev-parse HEAD`, active branch, `origin/main`,
   `origin/work/zg-recovered`.
2. Recompute installed binary/native hashes, daemon PID/maps, manifest,
   `--status`, `--verify`.
3. Check for new branches/tags and new `rust/docs/rag-*` files.
4. Update `PROJECT-STATE.md` and this register; never silently overwrite the
   historical note.
5. If a claim conflicts, preserve both and mark `CONFLICT_UNRESOLVED` until
   direct evidence resolves it.
