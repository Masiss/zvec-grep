# 07 — Operations, Release and Runtime State

## 1. Three states to keep separate

1. **Source/maintained branch** — `work/zg-recovered` `27b81c0`, index v3
   code, E8-P2 merged.
2. **Release candidate package** — Rust `0.3.0-rc.2`, code `289674b`, tag
   `c2347d5`; release notes claim RC/merge readiness, **not production
   qualification**.
3. **Installed/runtime** — `zg 0.0.1`, binary `6db0f67e…`, native
   `532f16dd…`, daemon PID `161009`, active index **v2**/Potion.

The installed/runtime state does **not** automatically match source or release
notes. Always name which state a claim describes.

## 2. Current installed CLI

```text
$HOME/.local/bin/zg
version: 0.0.1
sha256: 6db0f67e05e61ad6d50e8e9ea7b3e4593cfb07c99ac0af9f5064f48398db7ed4
build id: 78ee4a26d0a81265061f22f8cfa781e2422af7d6
native: $HOME/.local/lib/libzvec_c_api.so
native sha256: 532f16dd91574cc692ba5f36f855dd187f04a40afe29dde48fa47a35f8c13c38
```

The CLI exposes search, `--rg`, `--index`, `--status`, `--verify`, `--config`,
`--auth`, `--server`, `--install`, `--uninstall`. It does **not** expose an
`evidence`/RAG command.

## 3. Daemon/runtime

Read-only observed:

```text
PID 161009
zg --server run --mcp-toolset agent --listen 127.0.0.1:7999 --home $HOME/.zvec-grep
Server: ready
URL: http://127.0.0.1:7999/mcp
loaded native lib: 532f16dd…
```

The daemon started before this audit. It was not restarted, stopped, upgraded,
or signalled. `/proc/161009/maps` is the evidence for the loaded library; the
installed library on disk is the same SHA.

## 4. Active index

```text
Workspace: /home/masis/src/zvec-grep
manifest: .zvec-grep/manifest.json
manifest sha256: 0539af2c0e55b2a1515a466537bdcaecd509466e3ddddf4f0af560aa1ff32e47
indexVersion: 2
embedding: local/potion-code-16m-v2
dimension: 256, cosine
FTS: jieba, lowercase
generation: 6e3c3a4d-4690-4868-8f9e-1023bbb7577c
status: "Workspace index status is unknown"
verify: PASS_COMPLETE, native ABI 1
```

`--status` and `--verify` answer different questions. “Unknown” status is not
proof of corruption; PASS_COMPLETE is not proof that a different source/version
would be compatible.

## 5. Index/version compatibility

### Current source

- `CURRENT_INDEX_VERSION = 3` in
  `rust/crates/zg-engine/src/workspace/mod.rs:13`.
- A missing/older/newer/malformed index reports `INDEX_INCOMPATIBLE` /
  rebuild-required; there is no silent in-place migration.
- Rebuild uses a staged generation; failed build keeps the active generation.
- Workspace `.zvec-grep/index` path may be preserved for compatibility; the
  active generation is under `generations/<uuid>`.

### Installed release

- Release notes for 0.3.0-rc.2 state index format remains **version 2**; no
  in-place migration; incompatible version → `RebuildRequired`, explicit
  `zg --index --rebuild`.
- The E8-P2 merge later moved source to v3. Installed v2 remains v2.

### Implication

Do not:
- open installed v2 under source v3 code and expect automatic migration;
- replace the binary before compatibility/rollback rehearsal;
- assume v3 C#/FTS improvements apply to the installed v2 index;
- run `--index --rebuild` on a production workspace as a “test”.

## 6. Rollback

- Tag `rollback/pre-e8p2-merge-20261115` = `6d5b523`.
- Installed rollback pair:
  `$HOME/.local/share/zvec-grep/rollback-20261101T130300Z/` with binary
  `ad1d9c2c…` and native lib `5093c7d…`.
- The rollback pair is not automatically swapped. Restoring it is an operational
  decision; the source/index version must be compatible with the target pair.
- A rollback must preserve the old active generation; never delete it before
  verifying the replacement.

## 7. R1 evidence exposure

R1 is **lib-only**:

- `rust/crates/zg-engine/src/api/evidence.rs` is public engine API;
- no CLI command, no MCP tool, no daemon endpoint uses it;
- `rust/docs/rag-r1-release-gates.md:34` explicitly says missing public
  integration;
- no versioned public response/unsupported policy;
- no cache/invalidation/security review.

Therefore “zvec-grep has RAG” is imprecise. It has an internal evidence API,
an R4 prototype and a frozen post-R4 candidate; none is exposed as the normal
agent product.

## 8. Native/device operations

- CPU is default; Vulkan/CUDA/Metal opt-in.
- Accelerator indexes are rebuild-only for writes; incremental writes fail
  closed.
- Device identity is part of the index/embedding identity; cross-device writes
  must not be silently reused.
- Current installed daemon loaded CPU native lib; no Vulkan evidence in `maps`.

## 9. Safe read-only audit commands

These are acceptable for future state refresh (run from the workspace):

```bash
git status -sb
git log -1 --format='%H%n%ci%n%s'
git worktree list
git ls-remote --heads origin
zg --version
sha256sum "$(command -v zg)"
ldd "$(command -v zg)"
zg --status
zg --verify
zg --server status
ps -eo pid,ppid,etime,rss,args | rg 'zg --server|zvec-grep'
cat .zvec-grep/manifest.json
sha256sum .zvec-grep/manifest.json
```

Do **not** use these as state mutation:

```text
zg --index ...
zg --index --rebuild ...
zg --config ... set ...
zg --auth ...
zg --server stop|on ...
zg --install/uninstall ...
```

`--status` may update harmless runtime registry/state in some versions; it does
not create/rebuild an index. `--verify` is explicitly read-only.

## 10. Release readiness

### RC 0.3.0-rc.2

`docs/release/RELEASE-READINESS.md:1-25` says:

- **RC/merge readiness: READY** on the audited branch;
- **production rollout readiness: NOT READY** due to unresolved 64-file
  confirmation, no 421-file/production benchmark, native 0.8 lock limitation,
  deferred #10 maintenance.

Release notes say no in-place index migration; rebuild explicit; sidecar
optional; M2 reuse default; watcher debounce/compute cap are owner-accepted
behavior changes.

### Current maintained branch after E8-P2

E8-P2 merges Index v3, no-op, E2, P1; source is not installed. Its docs say no
installed binary/daemon/index change. Current main test receipts: 765 tests,
0 failed, 28 ignored in default/Vulkan; focused E6/P2 verifier PASS.

### Binary replacement — authorization vs execution

A historical chat statement allowed binary replacement **in principle**, but the
supplement also records that no release/install prompt was actually sent to the
execution agent. The runtime audit confirms the installed pair is still the old
binary/library and the active index is v2.

Therefore:

- do not cite the chat statement as proof of replacement;
- do not treat it as standing authorization to rebuild/replace now;
- replacement still requires release gates, backup, rollback rehearsal and
  post-install verification, as an owner decision;
- `MERGED_INTERNAL` no-op/E8-P2 does not imply `INSTALLED` or `ACTIVE_RUNTIME`.

### RAG

No RAG release: R1 internal; R4 NO-GO; R5/R6 absent; post-R4 candidate not
merged.

## 11. Operational anti-patterns

- “Main” without SHA.
- “Installed” from source `--help` version.
- “Active model” from a cached artifact.
- “Index v3” on a v2 runtime.
- “RAG qualified” from a tiny dev fixture.
- “R1 public” from library tests.
- Rebuilding/replacing before a rollback pair and compatibility check.
- Deleting `.zvec-grep/index` because the active generation is elsewhere.
- Running benchmark/route experiments against a live production workspace.

## 12. Evidence anchors

- `PROJECT-STATE.md` — current snapshot.
- `rust/docs/daemon-cli-ux-v2.md:1-45` — runtime/protocol behavior.
- `docs/release/RELEASE-READINESS.md:1-40`.
- `docs/release/RELEASE-NOTES-0.3.0-rc.2.md:1-55`.
- `rust/docs/e8p2-integration-20261115.md:13-40,65-150`.
- `rust/docs/rag-r1-release-gates.md:28-45`.
- Runtime commands listed in §9; no mutation.
