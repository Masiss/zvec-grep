# Continuation handoff — 2026-11-25 RC3 local promotion

Read this first for the current maintained/runtime state. It supersedes the
2026-10-31 continuation checkpoint. Historical integration detail remains in
[`../../HANDOFF.md`](../../HANDOFF.md) and
[`../../HANDOFF-20261031-NEXT.md`](../../HANDOFF-20261031-NEXT.md).

## Current state

- Canonical development branch: `main` (local and origin)
- Qualified tag: `rust-v0.3.0-rc.3`
- Qualified commit: `6cd4bdeddef4e8490ca390113edfc7250c56979c`
- Runtime provenance remains the qualified tag; subsequent `main` commits are
  documentation-only unless explicitly stated otherwise.
- Main worktree: `/home/masis/src/zvec-grep` on `main`
- Dogfood worktree: `/home/masis/src/zvec-grep-core-20261124`, detached at
  canonical `main` (no second maintained branch)
- Runtime binary: `/home/masis/.local/bin/zg` = `0.3.0-rc.3`
- Default runtime home: `~/.zvec-grep` → `~/.zvec-grep-dogfood`
- Legacy runtime home: `~/.zvec-grep-legacy-backup-20261125`
- Daemon: `127.0.0.1:7999`, agent toolset, plain `zg`
- Codex MCP: absolute `/home/masis/.local/bin/zg`, no wrapper/home override
- Model: `local/potion-code-16m-v2`, CPU, FP16
- Native runtime: zvec 0.8.0 with embedded Jieba; index format 3

The two worktrees are clean at this handoff commit. RC3 was promoted locally,
research branches were archived, and no upstream synchronization, RAG
implementation integration, engine behavior change, or model change was made.
See [`repository-consolidation-20261125.md`](repository-consolidation-20261125.md)
for the branch inventory and disposition.

## Indexes

| Workspace | State |
|---|---|
| `/home/masis/src/zvec-grep` | Rebuilt v3/Potion CPU with owner approval: 3,516 files, ~607 MiB, 100% ready |
| `/home/masis/src/zvec-grep-core-20261124` | v3/Potion CPU dogfood index: 1,442 files after handoff indexing, ~149 MiB, 100% ready |
| Original project index v2 | Preserved intact at `~/.cache/zvec-grep/rc3-promotion-20261125/protected-index-v2-backup` |
| Other test/fixture indexes | Left untouched; rebuild only if that workspace becomes active |

RC3 does not migrate v2 in place. An old active workspace must be rebuilt
explicitly:

```sh
cd <workspace>
zg --index --rebuild \
  --embedding local/potion-code-16m-v2 \
  --device cpu
```

Back up or move the old `.zvec-grep` first when rollback matters.

## Daily use

Plain `zg` is the only command; `zg-dogfood` was removed.

```sh
cd /home/masis/src/zvec-grep
zg --status --check-ready
zg --server status --check-ready
zg --version
zg --build-info
```

### Semantic / hybrid when the identifier is unknown

```sh
zg "where index corruption is detected before status reports ready" \
  --limit 5 --preview full --device cpu \
  --model-cache /home/masis/.zvec-grep/models

zg --hybrid "read-only fragment health verification" \
  --fts IndexStore --fuse --limit 5 --preview full --device cpu \
  --model-cache /home/masis/.zvec-grep/models
```

### Lexical when the identifier is known

```sh
zg --fts IndexStore --refresh off --limit 5 --preview full
```

Prefer `rg` or `zg --rg` for exact literals, regex, exhaustive occurrence
counts, file listing, non-indexed files, or when no index is available.

For a freshly rebuilt large project, MCP callers should set `autoUpdate: false`
to avoid an unnecessary full refresh. The CLI equivalent is `--refresh off`.

## Verified runtime acceptance

- Frozen artifact checksums and embedded provenance match the qualified commit.
- CLI FTS, vector and hybrid searches passed in both indexed project roots.
- MCP stdio and live HTTP MCP initialize/tools/list/search passed.
- Daemon indexed query remained identical across restart.
- Fresh Codex sessions made real `zvec_grep_search` calls and returned correct
  repository-relative source evidence.
- The primary project reindex completed in 3,516 files with source-scoped
  FTS/vector/hybrid acceptance.
- Sol High final verdict: **PROMOTION_COMPLETE** for the plain-`zg` setup.

## Rollback

Run only when rollback is required:

```sh
/home/masis/.cache/zvec-grep/rc3-promotion-20261125/rollback-runtime.sh
```

The script restores:

- rc2 executable and native library;
- removes the newly introduced ORT library;
- restores the pre-promotion Codex MCP config;
- restores `~/.zvec-grep` to the legacy home;
- restores the preserved project index v2 while retaining the rebuilt v3 index.

It does not reset Git refs. If needed and no newer commits exist:

```sh
cd /home/masis/src/zvec-grep-core-20261124
git reset --hard d9a9b1abad0711a3e77d0ec031ff44b771ea3d01
```

The rollback payload was validated in an isolated `bin/`–`lib/` layout as rc2.
The full rollback was not executed.

## Evidence and operational receipts

Directory:
`/home/masis/.cache/zvec-grep/rc3-promotion-20261125/`

Key files:

- `PROMOTION-PLAN.md` — approved local promotion plan.
- `PROMOTION-REPORT.md` — full promotion and reindex report.
- `DOGFOOD-GUIDE.md` — day-to-day commands and tool-selection guidance.
- `rollback-runtime.sh` — runtime/home/index rollback.
- `sol-plan-review3.txt` — Sol `PROCEED`.
- `sol-final-review2.txt` — Sol `PROMOTION_COMPLETE` for isolated-home setup.
- `sol-final-review3.txt` — Sol `PROMOTION_COMPLETE` for final plain-`zg` setup.
- `final-state-plain-zg.txt` — final Git/runtime/index state.
- `project-reindex-result.json` — project reindex acceptance.
- `codex-default-home-test-summary.json` — real MCP agent acceptance.
- `protected-index-v2-backup/` — original project index v2.
- `backup/` — old binary/library/config/registry/daemon/Git receipts.

Qualification evidence remains under:
`/home/masis/.cache/zvec-grep/rc3-qualification-20261125/`

## Known limitations and follow-ups

- Maintained branch is three qualified commits ahead of the cached remote; do
  not push without separate authorization.
- macOS, Windows, Linux ARM64 and CUDA remain unqualified.
- Two unrelated test daemons on ports 7997/7998 still map older cached binaries.
  They are not the daily `zg` daemon; restart or stop them only if their owners
  confirm they are unused.
- Existing Codex/app-server sessions may need restart to reload MCP config.
- Other legacy v2/test indexes are intentionally not rebuilt. Rebuild only an
  active workspace when needed.
- MCP auto-update on a large, freshly rebuilt project can be expensive; use
  `autoUpdate: false` until normal incremental refresh is desired.
- No agent-benefit or retrieval-quality claim is made by this promotion.

## Next session

1. Start with `zg --version`, `zg --server status --check-ready`, and
   `zg --status --check-ready` in the intended workspace.
2. Use plain `zg`; prefer semantic/hybrid for unknown concepts and `rg` for
   known literals.
3. If another active workspace reports v2/incompatible, back up its index and
   rebuild explicitly.
4. Do not push/publish, change Potion, add RAG features, run large benchmarks,
   or rebuild unrelated fixture indexes without new authorization.
