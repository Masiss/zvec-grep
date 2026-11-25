# Public-source retrieval evaluation preflight — 2026-11-23

## Status

**Preflight only; no evaluation run and no retrieval change.** This checkpoint begins the public-source evaluation required by the semantic source-retrieval goal. The existing private/local qualification remains `INCONCLUSIVE` and is not treated as public validation.

## Preserved evidence and constraints

- Current worktree is `/home/masis/src/zvec-grep`, branch `work/source-discovery-autonomous-20261122`, HEAD `663566546f03768b9b021c65eb3accc4aa0720a1`.
- The worktree already contains dirty and untracked research artifacts. This checkpoint adds one new file and does not alter those files or any source code.
- The latest indexed-semantic handoff reports an unqualified v3 local index, an unresolved 822-source/818-index difference, and cached-binary provenance gaps. Its three-task measurements remain exploratory.
- Prior findings remain diagnostic constraints: baseline hybrid 1/9 useful-source delivery; H1 fused 0/9; H3/RRF unvalidated; C-NEW-03 same-entity fragment-selection regression; C-DAP-CTRL-01 multi-fragment completeness ceiling; seven of nine historical tasks target README/AML spans outside the frozen `.cs` corpus. None of those tasks may be reused as independent validation.
- The workspace zvec-grep index search failed to open FTS storage. No index rebuild, repair, or mutation was attempted.
- The cached R4 binaries match the hashes in the qualification report (`zg` `144fa20e…`; `r4-adapter` `bdd6f620…`) and the checked R4 source declares `CURRENT_INDEX_VERSION = 3`. That source tree is now at `e7d0a89c27e199489ea4ad786c619211bad5ac2d`, beyond the report's cited `2bf2f1ef…` checkpoint. The cached v3 `zg` exposes no `--verify` command; the installed `zg 0.0.1` verifier rejects v3 as unsupported. No verifier or route was run against the preserved local index in this turn.
- A separate clean clone at the R4 source commit was built offline using cached native dependencies. The new `zg` and `r4-adapter` hashes and dependency receipts are recorded in [`r4-index-build.json`](../../research/public-source-evaluation-20261123/receipts/r4-index-build.json). A fresh v3 scratch index at `/tmp/zvec-public-source-20261123` reports 1,279/1,279 file coverage and passes `--check-ready`; its tree hash is recorded in the receipt. No retrieval queries have run.

## Public source snapshots found

Existing public snapshots are available under `/home/masis/.cache/zvec-grep/e3-01/corpus-src/` and their old indexed work copy is under `.../e3-01/work/corpus/`.

| Repository | Upstream archive revision (from archive root) | Archive SHA-256 | `.cs` files in archive | `.cs` files in old work copy |
|---|---|---|---:|---:|
| ANTLR | `2ac185af855ff39b4b8c41854b756767099c356f` | `05d8404b6f732b55d4beaf0d95b4eb1761d29be672a1da030c553f105b8d9d66` | 189 | 132 |
| Dapper | `eb47546a408bbf6c178bda4f04dc205bc51ffbfe` | `2bec491ea26ac33dfa1c8f3f3b928b7f0c50c152066c2947a2432985aa81a7c4` | 157 | 51 |
| Newtonsoft.Json | `0a2e291c0d9c0c7675d445703e51750363a549ef` | `d88190702cc9b27c51dd1075b2773479f5f91e8256e73579beaa6cedf659a388` | 933 | 209 |

The old work corpus index manifest records `indexVersion: 3` and a `*.cs` scan glob with default ignores enabled. Its 392 reconciliation files match the old work copy's 392 `.cs` files by count, but those binary receipts have not been decoded or integrity-verified. The indexed work copy is not interchangeable with the complete archive: the complete archives contain 1,279 `.cs` files total. Hash comparison confirms every `.cs` file in each old work copy has matching content somewhere in the corresponding archive, but the paths differ (for example, ANTLR's `Runtime/AntlrFileStream.cs` maps to `runtime/CSharp/src/AntlrFileStream.cs`). Therefore source-range targets must be adjudicated against the exact frozen corpus paths, not guessed from old index paths.

## Frozen source scope

The archive contents were checked against their extracted trees member by member: 1,279 included `.cs` files, zero missing, zero content mismatches. The durable inventory is [`source.lock.json`](../../research/public-source-evaluation-20261123/source.lock.json), SHA-256 `214298e7f84f610a2a47acee48aa1f102f472bee6c9e8a76cdd95a6a9df7ebf2`. It records each repository revision, archive digest, `.cs` path, and file-content digest. The reproducible materializer [`materialize_corpus.py`](../../research/public-source-evaluation-20261123/materialize_corpus.py) created `/tmp/zvec-public-source-20261123` with exactly 1,279 locked files and no other files. Reproduction: `python3 research/public-source-evaluation-20261123/materialize_corpus.py /tmp/zvec-public-source-20261123`. This freezes source scope only; task questions, adjudication rules, budgets, and success criteria remain unfrozen.

## Task split and adjudication status

- The author created four development tasks and ten validation prompts in a separate agent context. An overlap check found four development/validation topic duplicates; the author replaced the development tasks. All six development target ranges pass source-lock path, content-hash, and line-bound checks.
- The revised development split has four tasks (SHA-256 `1ae88b905d4eb3f05edd1a89b2ebac8ee4b96303e31a6de17ece7ee939e208d7`); all six target ranges pass source-lock path, content-hash, and line-bound checks. Validation prompts are SHA-256 `50d5b5628c69b4c9c321984407e908a0f46092b728ba4aebb8ac27d491837dd6`; the author seal is `0497e8574927f37a74614381a7bdd0ca324790d65e411daeb5d967e50d4671d1`. Types are six multi-fragment, two single-fragment, one abstention control, and one ambiguity control.
- A separate adjudicator agent independently validated ten prompts: nine answerable and one requiring abstention. Its private gold has 18 accepted ranges and SHA-256 `450253d1c1e7c3e2409ea42ccf48749ad83c236784ba29f4160dae47a7688181`. A metadata-only verifier confirmed the gold/prompt seals and all source hashes and line bounds without revealing ranges to the retrieval engineer.
- The validation prompts and development targets are separate; the retrieval engineer has not seen validation gold. The historical contamination audit is **partial**: reachable frozen records did not include prompt/target text for the complete nine-task historical set. The public validation set therefore cannot support a fully independent generalization claim until this limitation is resolved; any result from it must be labeled exploratory. Development baselines are recorded; validation outputs remain unseen.

## Evaluation gates before any retrieval run

1. Freeze one exact source revision per repository, then generate a manifest of every included `.cs` path and content hash from the same directory that will be indexed.
2. Use the same inclusion policy for the indexed corpus, lexical baseline, gold target adjudication, and returned source ranges. Do not use README, AML, or other non-`.cs` targets in this code-only track.
3. Build only fresh scratch indexes for these public sources if needed. Do not reuse or modify protected or legacy indexes as if they were qualified.
4. Keep development tasks separate from the ten-task validation set across three repositories. Include single-fragment, multi-fragment, and negative/ambiguous controls; avoid historical diagnostic targets.
5. Freeze questions, targets, adjudication rules, item/byte budgets, baseline workflows, and success criteria before retrieval. Task author, gold adjudicator, and retrieval engineer used separate agent contexts. The filesystem is shared, so gold blinding is enforced by not opening the private file; the retrieval engineer has only the prompts and hashes so far.
6. Do not begin ranking or fragment-policy implementation before the source-scope checks, baseline protocol, and mandatory independent design challenge are satisfied.

## Next action

The frozen baseline protocol is [public-source-evaluation-protocol-20261123.md](public-source-evaluation-protocol-20261123.md). The R4 runtime and scratch-index receipts are complete, and development baselines are scored. Next is the mandatory fresh-context Sol design challenge, with no validation prompts or gold. Keep validation outputs unseen until the final run and keep private gold out of the retrieval engineer's context through scoring.
