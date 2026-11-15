# RAG operational baseline — 2026-11-15

Status: **READ-ONLY BASELINE. No installed binary, index, daemon or config was
changed.** Experiment worktree: `work/rag-requal-20261115` at
`27b81c0e0472644820955fd69d1a080fb76b16ca` (same commit as
`work/zg-recovered`), external root
`/home/masis/.cache/zvec-grep/rag-requal-20261115/`.

## 1. Installed runtime vs experiment build

| Item | Installed runtime | Experiment build |
|---|---|---|
| Binary | `/home/masis/.local/bin/zg` `0.0.1` | `<external>/target/release/zg` `0.3.0-rc.2` (build target removed after verification) |
| Binary SHA-256 | see `sha256sum` below | `f6e0a22a439b57e23c4a4f30a3db63b6a521286712532913cfd106394285a8d1` |
| Native library | `~/.local/lib/libzvec_c_api.so` `532f16dd…c13c38` | `<external>/target/release/libzvec_c_api.so` `5093c7d7…fe763e` |
| Index version | v2 | v3 |
| Status | active daemon + production indexes | isolated corpus/index, direct mode |

The experiment native library is byte-identical to the V2 rollback pair
`~/.local/share/zvec-grep/rollback-20261101T130300Z/libzvec_c_api.so`
(`5093c7d7…fe763e`). It was **not** copied over the installed library.

Installed library SHA-256:

```text
532f16dd91574cc692ba5f36f855dd187f04a40afe29dde48fa47a35f8c13c38  /home/masis/.local/lib/libzvec_c_api.so
```

The installed binary reports `0.0.1` via `zg --version`. It is the original V1
binary; the recovered branch's V2/RC binary is **not installed**. The
experiment receipt records only the experiment binary identity, not the
installed pair.

## 2. Model resolution (catalog ≠ active)

- `~/.config/zvec-grep/config.json` is empty; no model override is configured.
- No `ZVEC_GREP_EMBEDDING` was set in the observed shell environment.
- The installed indexes resolve to **`local/potion-code-16m-v2`**, 256
  dimensions, cosine. This is the active model for the production indexes.
- EmbeddingGemma Q8 GGUF exists in the local model cache but **no active index
  references it** and no credential/config path selects it. It is not the active
  model merely because it is present.
- The installed CLI's catalog/help default is the Potion model, consistent with
  active indexes. The experiment build also defaults to Potion; no model was
  changed for the RAG experiments.

Active index identity observed with the installed binary:

```text
~/itc/camera
  Embedding   local/potion-code-16m-v2
              256 dimensions · cosine
  FTS         tokenizer=jieba filters=lowercase
  Version     2
```

`--status --mode direct` returned “Workspace index status is unknown” for that
workspace, so freshness/coverage was not asserted from the installed CLI. The
experimental corpus index reports 818 indexed files, 15,039 entities,
0 failed/pending and `version=3`.

## 3. Index fleet (observed)

| Workspace | Files | Version | Notes |
|---|---:|---|---|
| `~/itc/camera` | 818 | v2 | shown by `--status`; status unknown from CLI |
| `~/itc/Ricons` | 13,551 | v2 | daemon log reports 7 failed, 2 pending |
| experiment corpus | 818 | v3 | 15,039 entities; 0 failed/pending |

The daemon (`PID 161009`, `zg --server run --mcp-toolset agent --listen
127.0.0.1:7999 --home ~/.zvec-grep`) has been running for ~2 days. It uses the
installed `0.0.1` binary and installed native library. No daemon restart or
index rebuild was performed.

## 4. Binary replacement / RC qualification

- The recovered-branch merge was recorded as `MERGED_AND_STABILIZED`, not
  released, promoted or installed.
- No owner authorization for replacing the installed binary was found in the
  current task history. The only related instruction found was the earlier
  branch-recovery constraint: “Do not install/restart/replace the installed
  `zg` binary or its native library.”
- Consequently binary replacement remains **not performed**. It is not a
  blocker for RAG research because all experiments used an isolated binary,
  external corpus/index and direct mode.

Rollback material exists and was not touched:

```text
~/.local/share/zvec-grep/rollback-20261101T130300Z/
  zg                 4,430,576 bytes
  libzvec_c_api.so  42,099,504 bytes  5093c7d7…fe763e
```

If replacement is ever authorized, the gates are: release qualification,
backup of the installed pair, explicit rollback, post-install smoke +
index-compatibility check, and owner acceptance. This report does **not**
authorize that operation.

## 5. Reproduction environment

- Corpus: immutable copy generated from
  `benchmarks/rag-impact/corpus-manifest.json` (822 files, 5,400,637 bytes;
  source `68a53a6865b81d05745c4aa47967204e8e128996`).
- Index: `<external>/corpus/.zvec-grep`, v3, Potion, 818 files; no live
  workspace was indexed and no production index was modified.
- Build: release profile, CPU default, Vulkan/CUDA/Metal not used.
- Direct mode only; server/daemon parity was not exercised in this phase.
- Held-out cases (`H01–H08`) were not executed.

Baseline identity hashes are in
`<external>/receipts/requal-foundation.json` (`identity` and `index`).
