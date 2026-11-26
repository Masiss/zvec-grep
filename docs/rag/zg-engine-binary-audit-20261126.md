# zg engine/binary read-only audit — 2026-11-26

## Parent independent review

**Audit accepted.** The parent independently confirmed:

- audit manifest: zero hash failures; frozen source/model hashes unchanged;
- wrapper SHA-256 `74a65154…` and literal target `zg-51c9eba`;
- installed/daemon executable SHA-256 `940180c7…`; daemon PID 1291226 and
  `/healthz` returned `{"status":"ok"}`;
- installed binary contains none of the research RAG flag strings, while G1
  contains them;
- G1 without `LD_LIBRARY_PATH` exits 127 on `libzvec_c_api.so`; with
  `/home/masis/.local/lib` it exits 0;
- embedded provenance: installed clean `51c9eba`, G1 dirty `313e48d`, both index
  format 3.

No remediation was applied and no daemon/index operation was performed. The
untracked plan documents in the main worktree predated the audit.

## Verdict

**There is no evidence that the installed 51c9eba engine is corrupt or unable to search.** It starts, has valid dynamic dependencies, the daemon is healthy, and both the installed binary and G1 completed vector searches against a copied frozen clap index. The real index is format v3, matching both binaries, and recent daemon retrieval records report the expected generation with `freshness: fresh`.

There are, however, three concrete reasons `zg` can appear broken or old:

1. **The installed command and daemon are intentionally old relative to the research ranker.** `~/.local/bin/zg` always execs `zg-51c9eba`; it has no research-binary selector. The daemon also runs that exact binary. Neither contains the research RAG flag strings, and the installed build predates coordinate-repair commit `b336cbb`.
2. **Direct CLI use from the research worktree fails before searching because `/home/masis/.zvec-grep` is a symlink.** Both `--status` and an explicit `--fts` query exit 1 with `ZG.ENGINE.INVALID_ARGUMENT: cannot use '/home/masis/.zvec-grep': symbolic links are not supported`. The same status succeeds in an empty `/tmp` workspace. Setting `ZVEC_GREP_HOME` or passing a status root did not avoid this ancestor-workspace symlink rejection.
3. **`zg-g1` is not self-contained.** Direct execution exits 127 because `libzvec_c_api.so` is not beside it and `LD_LIBRARY_PATH` is unset. Adding `LD_LIBRARY_PATH=/home/masis/.local/lib` makes help/version and vector search work. Its embedded provenance is a dirty build at `313e48d`, not a clean `b336cbb` build.

Secondary wrapper issue: setting `ZVEC_GREP_OBSERVABILITY=0` while invoking the wrapper makes every command fail, because the wrapper still supplies the other observability variables and the engine rejects that partial configuration. Direct execution with `ZVEC_GREP_OBSERVABILITY=0` and the other three variables unset succeeds. This is not the current caller failure—the current caller has observability enabled—but it is reproducible.

**Classification:** old binary = confirmed; hardcoded wrapper selection = confirmed; G1 loader packaging mismatch = confirmed; home-symlink CLI failure = confirmed; stale daemon = confirmed but operational; index/schema mismatch = not observed; installed source/build mismatch = not observed; G1 clean-source provenance = unresolved because the build was dirty; general installed-engine search failure = not reproduced.

## Command and binary identity

| Item | Evidence |
|---|---|
| Current `zg` | `/home/masis/.local/bin/zg`; `type -a` and `which -a` returned only this path (repeated because PATH contains duplicate entries) |
| Wrapper | POSIX shell, 533 bytes, mode 0755, mtime `2026-11-25 22:45:48.207976906 +07`; SHA-256 `74a65154d1aa3e8b49731a93d102ffc1e50895c3a454294d351d1c65b9254acb` |
| Wrapper target | Literal final line: `exec /home/masis/.local/bin/zg-51c9eba "$@"` |
| Clean PATH | With `PATH=/home/masis/.local/bin:/usr/local/bin:/usr/bin:/bin`, `type -a zg`, `command -v zg`, and `zg --version` all resolve to the same wrapper/binary; version `0.3.0-rc.3`, exit 0 |
| Installed executable | `/home/masis/.local/bin/zg-51c9eba`; 64,420,768 bytes; mode 0755; mtime `2026-11-25 22:45:12.575975819 +07`; SHA-256 `940180c7c74a199c5effbaec604bb69af1a9e9bdfb46881beb6231a838a9be2d` |
| Installed build info | commit `51c9ebacf6d25c060de03ecf8af07633a55cd383`, `dirty: false`, release/default, build `2026-11-25T15:42:38Z`, index format 3 |
| G1 path | `/home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/final/build/zg-g1` is a symlink to `../build/zg-bom` resolution `/home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/build/zg-bom` |
| G1 executable | 64,822,432 bytes; mode 0755; mtime `2026-11-26 15:33:21.7164951960 +07`; SHA-256 `48855c5ba673d0801331f3ec06f41d8079da3e0f906c4ca31d5ed70f7d4eab8f` |
| G1 build info | commit `313e48df254ba5d0f7f599ecf9af8265feda735f`, **`dirty: true`**, release/default, build `2026-11-26T08:32:15Z`, index format 3 |

The wrapper only sets `ZVEC_GREP_OBSERVABILITY`, `_MODE`, `_CAPTURE`, and `_HOME`. It does not inspect `ZVEC_BINARY`, `ZG_BINARY`, `ZVEC_GREP_HOME`, or any research selector. Current caller values for those binary/home selectors are unset. Codex invokes the wrapper at `/home/masis/.local/bin/zg` with `--server --stdio --mcp-toolset agent`.

Raw evidence: `raw/zg-audit-command-resolution.txt`, `raw/zg-audit-wrapper-bytes.txt`, `raw/wrapper-trace-version.stderr`, `raw/wrapper-current-build-info-v2.stdout`, `raw/g1-direct-build-info-local-lib-v2.stdout`, `raw/clean-path-resolution-and-version.stdout`.

## Source and provenance comparison

- Main repo remains at `51c9ebacf6d25c060de03ecf8af07633a55cd383`; the same three untracked RAG documents existed before and after the audit. No staged changes.
- Research worktree remains at `0ef8ad3ec22c450e6a9e224f528b48e90a4666b1` on `work/recover-rag-evidence-20261126`, clean.
- Coordinate/ranker commit is `b336cbb094ab8fa806ae13f73199e729aaded972` (`research(rag): repair fragment evidence coordinates`, `2026-11-26T15:37:02+07`).
- Both `51c9eba` and G1's embedded `313e48d` are ancestors of `b336cbb`. The installed binary therefore predates the coordinate repair. G1 was built about 4m47s before the `b336cbb` commit and reports a dirty tree; the dirty delta cannot be reconstructed from build-info alone, so this audit does **not** claim byte-for-byte source equivalence to `b336cbb`.
- No clean build artifact embedding `b336cbb` was found. Nearby research artifacts are older debug candidates or the dirty G1 release binary.
- Tag `rust-v0.3.0-rc.3` points to packaging/provenance commit `6cd4bdeddef4e8490ca390113edfc7250c56979c`; it is not the installed binary's embedded source commit.

### Feature-string evidence

Counts are literal occurrences in the ELF files. Absence/presence alone is not proof, but here it agrees with the embedded commits and source.

| String | Installed 51c9eba | G1 |
|---|---:|---:|
| `ZVEC_RAG_FRAGMENT_FLOW` | 0 | 4 |
| `ZVEC_RAG_FRAGMENT_RELEVANCE` | 0 | 1 |
| `ZVEC_RAG_RANK_RRF_ONLY` | 0 | 1 |
| `ZVEC_RAG_FRAGMENT_SELECT_RANK_ONLY` | 0 | 1 |
| `ZVEC_RAG_ITEM_BUDGET` | 0 | 1 |
| `ZVEC_RAG_BYTE_BUDGET` | 0 | 1 |
| `ZVEC_GREP_OBSERVABILITY` | 15 | 15 |
| `ZVEC_GREP_OBSERVABILITY_MODE` | 3 | 3 |
| `ZVEC_GREP_OBSERVABILITY_CAPTURE` | 3 | 3 |
| `ZVEC_GREP_OBSERVABILITY_HOME` | 4 | 4 |

Main source at `51c9eba` contains none of the six RAG strings. Research source defines them in `pipelines/indexed_search/context.rs` and `ranking/mod.rs`. Raw evidence: `raw/binary-feature-summary.txt`, `raw/binary-feature-strings.txt`, `raw/zg-audit-research-flags-exact.txt`.

## Dynamic linking and native runtime

- Both ELF files use `/lib64/ld-linux-x86-64.so.2`; host glibc is 2.44.
- Installed binary RUNPATH is `$ORIGIN:$ORIGIN/../lib`; `ldd` resolves `libzvec_c_api.so` to `/home/masis/.local/lib/libzvec_c_api.so` in both current and clean environments.
- `/home/masis/.local/lib/libzvec_c_api.so` SHA-256 is `5093c7d722304b0efaa7425f206456ca902dc377d5a0ae8056f1c5c010fe763e`, exactly matching both binaries' embedded native hash.
- `/home/masis/.local/lib/libonnxruntime.so.1` SHA-256 is `3c8181b339025dd18899916357e74290029722e1d5a5778b4071a733f813420d`, also exactly matching embedded provenance. ORT is dynamically loaded only for applicable model backends; it is not an ELF `NEEDED` entry. The successful potion/model2vec vector smoke does not by itself prove every transformer/ORT path.
- G1 has the same RUNPATH but no packaged `libzvec_c_api.so` under its experiment tree. `ldd` prints `libzvec_c_api.so => not found`; actual execution exits 127. This is the reproduced G1 loader failure.
- With `LD_LIBRARY_PATH=/home/masis/.local/lib`, G1 executes and searches successfully. No `ORT_DYLIB_PATH` is set in the caller or daemon; that is not the cause of the installed binary's current behavior.

Raw evidence: `raw/zg-audit-linking.txt`, `raw/zg-audit-native-libraries.txt`, `raw/g1-direct-version-no-libpath.stderr`, `raw/g1-direct-version-local-lib-v2.stdout`.

## Running daemon on port 7999

| Field | Observed value |
|---|---|
| Listener | `127.0.0.1:7999`, PID 1291226, fd 10, socket inode 21005632 |
| Command | `/home/masis/.local/bin/zg-51c9eba --server run --mcp-toolset agent --listen 127.0.0.1:7999 --home /home/masis/.zvec-grep` |
| Start | `Wed Nov 25 22:45:59 2026 +07` (`2026-11-25T15:45:59Z`), still running at audit end |
| `/proc/1291226/exe` | `/home/masis/.local/bin/zg-51c9eba`, SHA-256 `940180c7...a9be2d` |
| cwd | `/home/masis/src/zvec-grep` |
| Environment | Observability=1, mode=research, capture=metadata, home=`/home/masis/.zvec-grep/observability`; `LD_LIBRARY_PATH` empty; `ORT_DYLIB_PATH` unset; `ZVEC_GREP_HOME` unset |
| Runtime home | `/home/masis/.zvec-grep` → `/home/masis/.zvec-grep-dogfood` |
| Instance record | PID 1291226, ready=true, protocol=2, agent toolset, expected observability capability |
| Open files | bootstrap log, `daemon/logs/server.log`, sockets; no index file was open at the snapshots |
| MCP child | PID 1373727, same installed binary/hash, `--server --stdio --mcp-toolset agent`, started `2026-11-26T07:06:24Z` |

`GET /healthz` returned HTTP 200 and `{"status":"ok"}`. `zg --server status --home /home/masis/.zvec-grep` returned ready/PID/URL/toolset, exit 0. The server log contains four ready records and no error record. Today's retrieval log contains four deliveries, all generation `4a41e2e8-b066-48da-81d1-9853c800ebec`, all `fresh`, 13 delivered items total; latest retrieval timestamp `2026-11-26T08:58:34.613Z`. The runtime stream contains resource samples and no error/failure-like events.

Thus the daemon is **stale relative to research code**, but it is not a dead or mismatched daemon. No signal, restart, stop, or shutdown request was sent.

Raw evidence: `raw/zg-audit-processes-detailed.txt`, `raw/zg-audit-process-network-env.txt`, `raw/zg-audit-processes-final.txt`, `raw/zg-audit-daemon-real-home.txt`, `raw/daemon-healthz-get.stdout`, `raw/wrapper-isolated-server-status.stdout`, `raw/runtime-event-summary.txt`.

## Index/schema and model evidence

- Real main index: `/home/masis/src/zvec-grep/.zvec-grep`, about 238 MiB.
- Manifest: `indexVersion: 3`, generation `4a41e2e8-b066-48da-81d1-9853c800ebec`, local `potion-code-16m-v2`, 256-dimensional cosine, CPU.
- Both binaries report `indexFormatVersion: 3`; the stored schema also declares potion/256/cosine. No schema mismatch was observed.
- The model cache has a completed potion model artifact. The frozen clap index uses the same model family and format v3.
- This audit deliberately did not query the real index because query/cache/lock writes could not be ruled out. Smoke searches used copies.

Raw evidence: `raw/zg-audit-index-model-metadata.txt`, `raw/zg-audit-frozen-corpus-metadata.txt`.

## Reproductions and exact outcomes

All audit commands used isolated HOME/XDG directories and an observability home under the audit directory. Exact stdout, stderr, exit codes, timestamps, and byte counts are in `raw/<label>.{stdout,stderr,rc,meta}` and `COMMAND-INDEX.tsv`.

### 1. Installed command works

- Wrapper help/version/build-info in current-style and clean environments: exit 0, version `0.3.0-rc.3`.
- Direct installed build-info: exit 0, commit `51c9eba`, dirty=false.

### 2. Research-worktree CLI failure (home symlink)

From `/home/masis/src/zvec-grep-rust-rag-20261126`:

```sh
env HOME="$A/tmp/home" \
  ZVEC_GREP_OBSERVABILITY=1 ZVEC_GREP_OBSERVABILITY_MODE=research \
  ZVEC_GREP_OBSERVABILITY_CAPTURE=metadata \
  ZVEC_GREP_OBSERVABILITY_HOME="$A/tmp/observability" \
  /home/masis/.local/bin/zg --fts push_arg_values \
  --mode direct --refresh off --limit 1 --preview none --color never
```

Exit **1**, stderr:

```text
Error[ZG.ENGINE.INVALID_ARGUMENT]: cannot use '/home/masis/.zvec-grep': symbolic links are not supported
Error location: crates/zg-engine/src/utils/filesystem.rs:16:54
Reporting location: crates/zg-engine/src/lib.rs:62:36
```

`--status /home/masis/src/zvec-grep-rust-rag-20261126` fails identically. In an empty `/tmp/zg-engine-audit-workspace.*`, `--status "$W"` exits **0** with `Coverage Unavailable (index not configured)`. Setting `ZVEC_GREP_HOME` to an audit directory did not cure the research-root failure, indicating workspace discovery selects the symlinked ancestor independently of that runtime-home override.

Raw evidence: `raw/research-root-fts-minimal-failure.stderr`, `raw/wrapper-research-root-status.stderr`, `raw/wrapper-isolated-status-zvec-home.stderr`, `raw/wrapper-isolated-status-outside-home.stdout`.

### 3. G1 direct loader failure and recovery

```sh
env -i PATH=/usr/bin:/bin HOME="$A/tmp/home" \
  /home/masis/.cache/zvec-grep/fragment-coordinate-repair-20261126/final/build/zg-g1 --version
```

Exit **127**:

```text
.../zg-g1: error while loading shared libraries: libzvec_c_api.so: cannot open shared object file: No such file or directory
```

Adding `LD_LIBRARY_PATH=/home/masis/.local/lib` produces `0.3.0-rc.3`, exit 0.

### 4. Wrapper observability-disable failure

Wrapper with `ZVEC_GREP_OBSERVABILITY=0`: exit **1**, `ZG.OBSERVABILITY.CONFIG: ZVEC_GREP_OBSERVABILITY=1 is required when observability variables are set`.

Direct `zg-51c9eba` with `ZVEC_GREP_OBSERVABILITY=0` and the other three observability variables unset: exit **0**. Source validation is `zg-observability/src/lib.rs:145-161`; wrapper defaults are lines 4-7 of `~/.local/bin/zg`.

### 5. Frozen-corpus vector smoke

Source corpus: `/home/masis/.cache/zvec-grep/rust-rag-20261126/corpus/clap`, frozen commit `4201754a400f676c9744da66383c89eb9f41af88`, index generation `09f464df-a502-4360-bfcf-62a48773a04a`. It was copied with `cp -a --reflink=auto`; queries used only the copies with `--mode direct --refresh off`.

```sh
/home/masis/.local/bin/zg-51c9eba \
  --vector 'typed command line conversion' --mode direct --refresh off \
  --limit 2 --preview none --color never "$COPY"
```

- Installed binary: exit **0**, two ranked hits, 3.14 s wall, max RSS 389,964 KiB.
- G1 with `LD_LIBRARY_PATH=/home/masis/.local/lib`: exit **0**, two ranked hits, 0.66 s wall on a fresh copy, max RSS 393,472 KiB.
- Before/after SHA-256 manifests match exactly: **1,165 frozen source/index files** and **4 model-cache files** unchanged. These are smoke results, not a ranking benchmark or p95 measurement.

Raw evidence: `raw/frozen-copy-installed-vector.*`, `raw/g1-fresh-vector-libpath-only.*`, `raw/frozen-integrity-final.txt`, `raw/frozen-query-resource-before.txt`, `raw/frozen-query-resource-after.txt`.

## Recommended remediation options (not applied)

1. **If the goal is research behavior:** build a clean binary from `b336cbb` or a later approved commit, record its hash/build-info, package `libzvec_c_api.so` (and applicable ORT assets) under the expected `../lib`, then explicitly select that binary. The current wrapper cannot do this. Any daemon switch requires a separately approved controlled restart.
2. **If the goal is only to run G1 now:** invoke it directly with `LD_LIBRARY_PATH=/home/masis/.local/lib`; for non-model2vec/ORT backends also set `ORT_DYLIB_PATH=/home/masis/.local/lib/libonnxruntime.so.1` or `ZVEC_GREP_NATIVE_LIB_DIR=/home/masis/.local/lib`. Treat the dirty `313e48d` provenance as a research-only limitation.
3. **Fix direct CLI workspace discovery:** `/home/masis/.zvec-grep` is a symlink to the dogfood home. An approved engine fix should prevent a symlinked ancestor outside the requested workspace from being selected (or canonicalize the runtime home safely), or the deployment should stop exposing that symlink as a workspace marker. Merely setting `ZVEC_GREP_HOME` did not fix the reproduced status/query path.
4. **Fix wrapper disable semantics:** only default the related observability variables when observability is enabled, or require callers to unset all four. Until changed, bypass the wrapper and unset all four variables to disable capture.
5. **Do not rebuild/replace the real index based on this audit.** Format/generation evidence is consistent. If a real-workspace status/refresh is desired, perform it later under an explicit authorization because it may update caches/locks/state.

## Safety and limitations

- No production binary, wrapper, daemon, index, config, source repository, or service was modified. Nothing was installed, committed, pushed, killed, restarted, or signaled.
- `zg doctor` was not run because no doctor subcommand is advertised. Potentially mutating index/config/install/server-start/stop commands were skipped. Real-index queries were skipped; copied indexes were used.
- Audit-created corpus copies and `/tmp` workspace were removed after hashes were preserved. Raw logs, receipts, hash manifests, and the command index remain under this audit directory.
- Host state was captured before/after: initial load 1.83/1.99/1.86, available memory 4.6 GiB, swap free 140 MiB; final load 2.04/2.14/1.98, available memory 4.9 GiB, swap free 228 KiB. Root free space remained 22 GiB. No performance conclusion is drawn from the smoke timings.
