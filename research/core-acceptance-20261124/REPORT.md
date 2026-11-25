# zvec-grep core acceptance — report

Date: 2026-11-25. Budget: 4 h. Source: maintained `work/zg-recovered` @
`d9a9b1a` (release line `0.3.0-rc.2`), acceptance branch
`work/core-acceptance-20261124`. No merge, push, release, or production index
mutation. Owner authorized replacing the installed binary.

## 1. Capability matrix

| Area | Implemented | Exposed | Actually works (measured) |
|---|---|---|---|
| CLI hybrid/lexical/vector search | yes | `zg <query>`, `--hybrid`, `--fts`, `--vector` | yes (`--fts IndexResult`, hybrid, vector on a fresh index) |
| Managed ripgrep | yes | `zg --rg` (`-F`, regex, `--files`, globs) | yes; invalid regex exits 1 with a clear engine error |
| Index lifecycle | yes | `--index` create/update/rebuild/drop, `-g` scan globs | yes: 47/63/186-file workspaces; no-op 38 ms; one-file update 1.0 s; add/delete removes stale hits |
| Index integrity | v3 format, checksum on open | `--status`, `--status --check-ready` | yes after fix: corrupt storage reported, `--check-ready` exits 1 |
| Index compatibility | v3 only, explicit rebuild | status/索引 error | v2 index → `rebuild required`, `--check-ready` exits 1 |
| Embedding | Potion Code 16M v2, local FP16 | `--embedding`, `--device cpu` | yes; model load dominates first index; reuse/no-op fast |
| Daemon + MCP | yes | `--server run/on/off/status`; HTTP MCP | yes: initialize, tools/list, search, restart, watcher refresh |
| MCP agent toolset | yes | 1 tool: `zvec_grep_search` | yes |
| MCP full toolset | yes | 6 tools: search, rg, index, index_drop, index_status, server_status | yes (listed and search called) |
| R1 evidence | engine API only in maintained source | **not exposed** (no `zvec_grep_evidence` in maintained) | not exposed, not tested |
| R2/R3 facts | crate-private | no | not exposed |
| `--verify` (0.0.1) | removed in 0.3.0 | — | regression vs 0.0.1; replaced here by status health |
| Install/uninstall | yes | `--install` | not exercised (would touch real agent configs) |

## 2. Defects found, causes, fixes

1. **Out-of-the-box index creation and FTS open fail: `jieba_dict_dir not configured`.**
   The native zvec 0.8.0 FTS tokenizer needs a jieba dictionary; nothing in the
   repo, build, or installer provides it, and `zvec-rust` exposes no safe setter.
   Measured: 89/175 engine storage tests failed without `ZVEC_JIEBA_DICT_DIR`;
   all 175 passed with it. This made search/index appear “corrupt”.
   Fix: resolve the dictionary from `ZVEC_JIEBA_DICT_DIR`, then
   `<ZVEC_GREP_HOME>/jieba_dict`, then `data/jieba_dict` / `jieba_dict` /
   `../lib/data/jieba_dict` beside the executable; canonicalize it and pass it
   as `extra_params.jieba_dict_dir` when **creating** the FTS index. Reopening
   no longer constructs a schema: it reads the persisted path from the native
   collection manifest and validates it before `Collection::open`. This was
   necessary because a missing persisted dictionary makes the native library
   call `abort()` (SIGABRT), which would kill `status` or the daemon; a missing
   dictionary now returns a classified error instead.
2. **Corruption invisible to status.** A truncated `embedding.index.*.proxima`
   made `--status` print “100% ready” and `--check-ready` exit 0, while search
   failed with a low-level `STORAGE_FAILURE`.
   Fix: `verify_storage()` opens the fragment collections read-only from
   `info()`; `InfoResult.storage_error` maps to `Failed`; status prints
   `Health` + detail and `--check-ready` exits 1. Classification is explicit:
   checksum/corruption → “failed checksum” with rebuild advice; a missing
   dictionary or other open failure → “storage unavailable” with the original
   remedy and **no** rebuild advice. The MCP index-status output carries the
   same `storage_error` so an agent sees why the index is failed.
3. **Stale binary/runtime provenance.** The installed `/home/masis/.local/bin/zg`
   is `0.0.1` from 2026-11-01 (zvec 0.7.2 era); maintained source is
   `0.3.0-rc.2` with zvec 0.8.0 and index v3. No git/build identity is embedded
   in the binary. `rust/.cargo/config.toml` force-pins the Oct-31 native lib
   (which lacks the jieba ABI symbol) and a deleted target directory; builds
   need `ZVEC_LIB_DIR` and `ORT_DYLIB_PATH` overrides.
4. **Isolated child processes lost the dictionary.** An authorization test
   spawns a child with a replaced `HOME`; it now forwards the resolved
   dictionary path. Without that forwarding, indexing in a sanitized
   environment fails (a real deployment consideration for services).
5. **Pre-existing quality gate failure.** `cargo clippy --workspace
   --all-targets -- -D warnings` fails with 37 errors in untouched test code;
   the documented gate in `rust/AGENTS.md` is not currently met.
6. **Leftover storage.** The repo workspace keeps a 2.4 GB `.zvec-grep/index`
   tree from the old layout plus a 216 MB v2 generation; neither is used by the
   v3 binary. No cleanup command is offered.

## 3. Test results (all on the acceptance branch)

| Suite | Result |
|---|---|
| `cargo test -p zg-engine --lib` | **771 passed, 0 failed, 28 ignored** |
| of which storage | 175 passed (89 failed before the dictionary fix) |
| `-p zg-cli` | 180 passed + 5 integration |
| `-p zg-transport-mcp` | 60 passed |
| `-p zg-daemon` / `-p zg-daemon-protocol` | 137 / 7 passed |
| `cargo fmt --all --check` | clean |
| `cargo clippy --all-targets -D warnings` | **fails, 37 pre-existing errors** |
| End-to-end | fresh index → FTS/vector/hybrid → add file → delete file → restart daemon → search; corrupt index → status/exit code; missing persisted dictionary → no abort, exit 1; custom dictionary → reopen with no env/default dictionary; v2 index → rebuild-required |
| Focused new tests | dictionary resolution precedence; both-file validation; persisted-path parsing; corruption classification; classify checksum vs unrelated errors |

Not covered by an automated regression: corruption → engine `info` → CLI
readiness → MCP status. That path is exercised manually (receipts above); an
integration test should be added before release.

## 4. Agent comparison (paired pilot)

Frozen before running: 6 questions on `BurntSushi/memchr` @ `bd6068c`, model
`gpt-6-luna`, same prompt/budget, A = shell + `rg` + reads, B = same + zvec-grep
MCP (search). Questions and rubric: `pilot/tasks.json`. Counterbalanced order.

| Metric | A (rg only) | B (+ zvec-grep) |
|---|---:|---:|
| Automated rubric score (fact + citation) | 6/6 | 6/6 |
| Tool calls | 20 | 21 |
| MCP calls | 0 | **0** |
| Input tokens | 675,520 | 737,315 |
| Wall time | 158.5 s | 160.0 s |
| Shell output bytes | 267 KB | 342 KB |

The rubric is an automated first pass: it accepts a fact token and a citation
inside a broad range, so it cannot detect an unsupported sentence (the reviewer
injected a false `Searcher::invented_method` claim and still scored 1.0). The
answers were read manually and are substantively correct, but these are not
human-graded results.

A second arm (B2, the same three semantic tasks, plus one sentence naming the
MCP tool) used exactly one `zvec_grep_search` per task and still scored 3/3.
Against the corresponding A runs it used 420,222 vs 385,108 input tokens and
110.1 s vs 96.7 s — i.e. slightly more, not less. With n=3 and one run each this
is neither benefit nor harm.

Order was five A→B pairs and one B→A, so it is not balanced counterbalancing.
The freshness fixture is a dirty `src/ext.rs` on top of `bd6068c`; its diff hash
is recorded below.

Interpretation: the six tasks are **saturated**; `rg` alone solves them, so the
pilot cannot show benefit. Without a nudge the agent **never invoked
zvec-grep** even for the architecture and cross-file questions, so tool uptake,
not retrieval quality, is the binding constraint in this setup. The result
supports neither “helps” nor “hurts”; it supports “inconclusive, needs harder
tasks or a larger unfamiliar repository.”

Observed agent-facing limitations:
- FTS does not match partial identifiers (`atomic_wr`, `encode_entit` → no
  matches) while exact names work; managed `zg --rg` does match partials.
- The agent toolset exposes search only; exact/partial lookup still depends on
  the host shell.
- Search after a source change is fresh once the daemon has served the
  workspace (watcher updated the index within seconds); a process that never
  served the workspace does not watch it.
- MCP search rejects `preview:"none"` (only `short|full`), while the CLI accepts
  `none|short|full|detail` — a small interface asymmetry.

## 5. Operating cost (single-run, host load 5.2, not serving benchmarks)

| Operation | Wall | Peak RSS |
|---|---:|---:|
| Native `rg` | 0.00 s | 7 MB |
| `zg --rg` | 0.03 s | 29 MB |
| `zg --fts` (cold CLI) | 0.58 s | 291 MB |
| `zg` hybrid (cold CLI) | 0.69 s | 375 MB |
| MCP search, warm daemon | 0.01–0.15 s | shared daemon |
| Index 63 files (model warm) | 0.96 s | ~440 MB |
| No-op index check | 0.04 s | 67 MB |

## 6. Remaining blockers

- **Packaging**: the installer/build does not ship `data/jieba_dict`; the code
  can now find it, but a fresh install still needs the dictionary placed at
  `~/.zvec-grep/jieba_dict` (or beside the binary/lib).
- **Upgrade**: v2 → v3 requires a rebuild; no in-place migration is intended.
  Existing v2 workspaces must run `zg --index --rebuild`.
- **Release gate**: clippy `-D warnings` is red; binary has no embedded build
  provenance; native lib/ABI is environment-pinned rather than packaged.
- **Evidence**: no measured agent benefit yet; partial-identifier search gap;
  no R1/R2/R3 exposure in the maintained surface.
- **Disk**: stale 2.4 GB index tree left in the repo workspace.

## 7. Recommendation

- **Functional:** yes for the exercised paths (CLI search/rg/index/status,
  daemon + MCP search/tools/list, watcher refresh, restart, corrupt/v2/missing
  dictionary handling) on a fresh v3 index. Install/uninstall and remote
  embedding were not exercised.
- **Daily-stable:** conditionally yes for a single local user, after
  (a) install the dictionary with the binary, (b) rebuild existing indexes,
  (c) restart the daemon so CLI and MCP use the same binary.
- **Beneficial for a coding agent:** **not established**. The pilot is
  saturated and the agent did not adopt the tool unprompted.
- **Release-ready:** no — clippy gate, packaging, provenance, and upgrade-path
  work remain.

Suggested next step (bounded): one repeat of the pilot on a larger unfamiliar
repository (≥1,000 files) with 6 questions whose answers require cross-file
synthesis, balanced/repeated arms, human or model-graded conclusions, and an
explicit routing instruction. Do not change retrieval behavior before that
evidence exists.

## 8. Artifact identities

| Artifact | SHA-256 |
|---|---|
| Source diff vs `d9a9b1a` | `e03e362d6e4852b462355198edef222533f238c2836d831db3f0aa3869d20d9d` |
| Installed binary (0.3.0-rc.2) | `e8d98245884f6578b1937d7274037a7cc16507aa00b1145770593dee195414cd` |
| Backup installed binary (0.0.1) | `6db0f67e05e61ad6d50e8e9ea7b3e4593cfb07c99ac0af9f5064f48398db7ed4` |
| `libzvec_c_api.so` | `532f16dd91574cc692ba5f36f855dd187f04a40afe29dde48fa47a35f8c13c38` |
| `jieba.dict.utf8` / `hmm_model.utf8` | `6f7d4350…` / `f1779058…` |
| Potion Code 16M v2 weights | `75cf7a6c2171b230ad19b1e7d8e0b1aee86da5a02af8e7cacedd9921d227623c` |
| Pilot `tasks.json` / runner | `540faffb…` / `97953c11…` |
| Pilot `runs.jsonl` / `runs-b2.jsonl` | `bc57676d…` / `dd0502c8…` |
| Pilot repo revision | `bd6068c30e9074a90c285e47912fa0b047d07597` |
| Freshness fixture diff | `2368b74e441048e34f372e595161660de2b32e7701bfc6e3308fc66ace347502` |
