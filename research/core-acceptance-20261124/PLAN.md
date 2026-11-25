# zvec-grep core acceptance — plan (draft v1, pre-review)

Date: 2026-11-24. Budget: 4 h, checkpoints that can stop independently.

## Question

Is the zvec-grep core stable enough for daily use, and does a coding agent
equipped with it work better than the same agent using only `rg` and ordinary
file reads? Freeze RAG research; no RAG tuning, graph, fragment selector,
autonomous relation expansion, or embedding-model change. Keep Potion Code 16M
v2.

## Facts established before the design (verified today)

- Root checkout `6635665` (branch `work/source-discovery-autonomous-20261122`)
  has `rust/crates` identical to `main` (2026-10-15); it is **not** the newest
  core source.
- Maintained source (per `rust/AGENTS.md` and `rust/docs/branch-recovery-status.md`):
  `work/zg-recovered` @ `d9a9b1a`, the `0.3.0-rc.2` line with zvec 0.8.0. The
  r4 tip `e7d0a89` contains 83 additional RAG/research commits and is not the
  maintained branch. Acceptance worktree: `work/core-acceptance-20261124` @
  `d9a9b1a` (revised from the initial plan after checking the canonical branch).
- Documentation mismatch found: `rust/AGENTS.md` says “Index format is version
  2”, but `CURRENT_INDEX_VERSION` is 3 (bumped with the C# declaration index).
- Installed binary `/home/masis/.local/bin/zg` is dated 2026-11-01,
  SHA-256 `6db0f67e…`, version string `0.0.1`, built against zvec 0.7.2 era;
  newest source uses `zvec-rust =0.8.0` and `CURRENT_INDEX_VERSION = 3`.
  Installed binary is therefore stale and cannot read a v3 index; provenance is
  not recorded in the binary.
- Workspace index `.zvec-grep` declares `indexVersion: 2`, has a 2.4 GB
  `index/` tree and a 216 MB `generations/…/storage` tree.
- Reproduced failure is **two independent defects**:
  1. `open_fts_indexers` fails with `jieba_dict_dir not configured` unless
     `ZVEC_JIEBA_DICT_DIR` (or `data/jieba_dict` beside the native library) is
     set. This is a configuration/acquisition defect, not corruption.
  2. With jieba configured, the same generation fails with
     `embedding.index.2.proxima: Invalid checksum`; `zg --verify` reports
     `CORRUPT` / `IndexUnpacker rejected header/footer/segment table/content CRC`.
     This is real corruption of the vector index.
- `zg --status` prints "Workspace index status is unknown" for the corrupt
  generation instead of reporting corruption or a rebuild action.
- Build/environment hazards: `rust/.cargo/config.toml` force-pins
  `LD_LIBRARY_PATH` to the Oct-31 native lib that **lacks** the jieba ABI
  symbol, and pins a deleted target dir; ORT archive is absent from the new
  target so the build must run with `ZVEC_LIB_DIR` and `ORT_DYLIB_PATH`.

## Checkpoints

- **C0 Core matrix (no writes).** From source + built binary + installed binary:
  classify every advertised capability as implemented / exposed / actually
  works. Cover CLI, MCP (agent and full toolsets), daemon, lexical/regex/FTS/
  vector/hybrid, workspace lifecycle, incremental update/watcher, embedding
  reuse, storage/integrity, start/stop/recover/config/error handling, and
  R1/R2/R3 exposure. Historical benchmark results are not assumed valid.
- **C1 Isolated reproduction.** Copy the v2 index (never mutate the original)
  and reproduce: jieba-missing FTS failure; proxima checksum failure; v2 index
  against a v3 binary; status/verify behavior. Build a fresh index with the new
  binary in a small disposable workspace and test search/lifecycle/restart.
- **C2 Minimal fixes in the experimental branch.** Only correctness/stability:
  (a) actionable, agent-readable error/status for corrupt or incompatible
  generations with a safe rebuild path; (b) jieba dictionary acquisition or a
  clear fail-closed diagnostic when it is absent; (c) native-library/runtime
  provenance checks if cheap. No API widening, no retrieval changes. Each fix
  needs a regression test. Owner has authorized overwriting the installed
  binary, not merging/pushing.
- **C3 Regression evidence.** Targeted engine/CLI/MCP tests plus a fresh-index
  end-to-end smoke (index → search → update one file → search again → restart
  daemon → search). Full suite only if it fits the budget.
- **C4 Agent pilot (6–8 tasks).** Public repository not used in prior RAG
  benchmarks; frozen task questions with gold conclusions and citations.
  Conditions: A = codex CLI with shell/`rg`/file reads; B = same agent + MCP
  zvec-grep built from C2. Same model, prompt, repo revision, permissions and
  call/time budget. Measure correctness, citation validity, tool calls, source
  bytes read, input tokens, wall time, runtime cost; record when zvec-grep
  helped or hurt and why it was/was not used. Small enough to run and grade in
  the remaining budget; no new benchmark framework.
- **C5 Sol final review + handoff.** Sol checks actual code, comparison method,
  correctness, regression tests, and pilot results. Report distinguishes:
  functional / daily-stable / real agent benefit / release-ready. Artifacts and
  canonical handoff updated; no push/merge/release.

## Non-goals

- RAG validation, private HRM held-out data, new retrieval features, graph,
  embedding changes, index schema changes, new benchmark framework.
- Weakening condition A (no hidden hints, no pre-seeded index, same read tools).

## Open risks

- Release build of the 0.8.0/zvec line may fail or exceed the budget; fallback
  is a debug build for functional gates and an explicitly labeled limitation
  for timing.
- The corrupt index may not be replaceable safely without owner approval;
  original is preserved and only copies are used.

## Review outcome (recorded)

Design review returned **REVISE**: use the maintained line, separate the three
failure cases, narrow the fix scope, cut the pilot to four paired tasks, add a
hard budget, and preserve diagnostics. Implemented changes and the final
**REVISE** verdict are in `REPORT.md`; transcripts are in `artifacts/`.

## Review request to Sol

Critique this plan before implementation. Answer specifically: (1) is the
source choice right; (2) are the two reproduced defects diagnosed correctly
from the evidence, and is the proposed fix scope minimal and sufficient;
(3) what must be added to make the A/B pilot fair and gradeable within the
remaining budget, and what should be cut; (4) which claims the evidence cannot
support; (5) the minimum regression set for C3. Return a short decision
(PROCEED / REVISE / REJECT) plus required changes.
