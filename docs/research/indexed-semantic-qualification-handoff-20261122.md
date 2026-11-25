# Handoff — indexed semantic retrieval qualification

**Status: INCONCLUSIVE.** Three local-only frozen tasks have exploratory results, but the R4 v3 index has not passed a deep integrity verifier. The available read-only verifier supports v2 and returns `INCOMPLETE` for v3. Do not describe the route measurements as qualified quality evidence.

- Research report: [`indexed-semantic-qualification-20261122.md`](indexed-semantic-qualification-20261122.md)
- Frozen validation tasks/protocol: [`validation-freeze.json`](../../research/indexed-semantic-qualification-20261122/validation-freeze.json)
- Sanitized task results: [`results.json`](../../research/indexed-semantic-qualification-20261122/results.json)
- Raw local artifacts manifest SHA-256: `22453bca2da0c60e9c2681e1e52cfcbe849a0a60663f5fc183e836995f3dddd9` (raw private contents remain outside the repository).
- R4 source: branch `work/r4-evidence-representation-20261119`, checkpoint `2bf2f1ef13e71d321668eef89f1de75f37978033`.
- Current workspace: branch `work/source-discovery-autonomous-20261122`, HEAD `663566546f03768b9b021c65eb3accc4aa0720a1`.

Findings from this slice: vector/hybrid reached some frozen target files but overlapped **0/9 searchable gold spans**; capped native `rg` and a post-hoc lexical IDF sensitivity each overlapped 1/9. The OR first-hit `rg` pool (`--max-count 1` per file) overlapped 3/9 at median 1.43 MB raw output. It is not a full-recall ceiling: IDF ran each term separately and merged contexts, so the pools are not nested. This is retrieval overlap only, not answer quality. One additional D07 span was marked `NOT_PERSISTED` and excluded. The 822 source / 818 indexed-file difference and cached binary build provenance remain unresolved.

The frozen `engine_binary_sha256` identifies the cached Rust `zg` used for status; route queries used cached Rust `r4-adapter`, SHA-256 `bdd6f6207e35757a0f6cec5797ca5d6b72739fea59257cc9c41b8e9535f810fd`. The original freeze was preserved; see `results.json` execution identity supplement.

**Next decision:** use or obtain an integrity verifier compatible with index v3, without rebuilding the persisted index. Then explain the file-count difference and record binary build provenance before rerunning the frozen probe. No H01–H08, remote inference, API calls, production/index/API/default/embedding changes, or upstream synchronization occurred. HEAD did not change; additions are documentation and sanitized aggregates only.
