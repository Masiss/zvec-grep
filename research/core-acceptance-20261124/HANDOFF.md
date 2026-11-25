# Core acceptance handoff — 2026-11-25

**Decision:** functional paths demonstrated; daily stability **provisional**;
agent benefit **unestablished**; release readiness **unmet**.

Read `REPORT.md` first. Worktree: `/home/masis/src/zvec-grep-core-20261124`,
branch `work/core-acceptance-20261124` at maintained `d9a9b1a`
(`work/zg-recovered`, 0.3.0-rc.2). Nothing was merged, pushed, or released.

## State

- Installed binary replaced with the acceptance build
  (`/home/masis/.local/bin/zg`, 0.3.0-rc.2, SHA-256 `e8d98245…`); the previous
  0.0.1 binary is backed up in `artifacts/zg-0.0.1-installed-20261101`
  (`6db0f67e…`).
- The FTS dictionary is installed at `~/.zvec-grep/jieba_dict`.
- The running daemon on 127.0.0.1:7999 is still the **old** process image. It
  must be restarted to use the new binary and v3 indexes:
  ```bash
  zg --server off
  zg --server on --mcp-toolset agent
  ```
  Existing v2 workspace indexes then need `zg --index --rebuild`.
- Fixes: dictionary resolution + persistence at FTS schema creation; safe
  reopen with persisted-path validation (native abort avoided); actionable and
  classified storage errors; `verify_storage` wired into status so corruption
  is visible and `--check-ready` exits 1; MCP index status carries the error.
- Tests: engine 771 passed; storage 175; cli 180; mcp 60; daemon 137;
  protocol 7; fmt clean. Clippy `-D warnings` still fails with 37 pre-existing
  errors.

## Next actions (ordered)

1. Restart the daemon and rebuild v2 workspace indexes; verify CLI and MCP
   report the same version and a ready index.
2. Ship `data/jieba_dict` with the binary/lib in the installer so a fresh
   install needs no manual step.
3. Fix the clippy gate and embed build/native provenance before any release.
4. Run one harder, balanced pilot on a ≥1,000-file unfamiliar repository
   (6 cross-file questions, repeated arms, model-graded conclusions). Do not
   change retrieval behavior before that evidence.
5. Optional cleanup: remove the stale 2.4 GB `.zvec-grep/index` tree in the
   source checkout (old layout; unused by v3).
