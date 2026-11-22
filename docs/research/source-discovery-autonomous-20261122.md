# Source discovery experiment — partial identifiers

Date: 2026-11-22

Branch: `work/source-discovery-autonomous-20261122`

Base: `2d7d2ac`
Scope: find a useful source entry point when the caller remembers only part of a symbol name. This experiment uses only the public zvec-grep checkout and its existing local index. It does not read the untracked `research/` tree, use HRM source, refresh or rebuild the existing index, change retrieval defaults, or send source data to a remote model.

## Finding

**Correction after read-only integrity verification:** this record does not
establish that partial-identifier failure was intrinsic to indexed FTS. The
original transcript recorded an exact-name result and “No matches” for
`searchWork`, but a later read-only check found the same workspace index
`CORRUPT`, and a subsequent run of the partial FTS query failed with checksum /
storage errors. Because the index state was not proven valid at the time of the
earlier observations, the FTS contrast cannot isolate query acquisition from
index failure. The native ripgrep result remains valid evidence that the
fallback can find this fragment on this source tree.

The useful entry point is `src/engine/pipeline/search/index.ts:89`; the same
literal also finds its caller at line 193 and the import/call in
`src/engine/service/workspace-index.ts`. This gives an agent a direct path from
partial-name recall to the definition and immediate navigation points.

Overall status: **GO for the scoped local `rg` fallback and its guidance;
INCONCLUSIVE for whether the wording change itself improves agent behavior over
the previous instructions.** The measured result is one public-source query.

## Frozen baseline and reproduction

The source checkout was clean at base `2d7d2ac` apart from the pre-existing,
untracked `research/` directory. The CLI was `zg 0.0.1`, SHA-256
`6db0f67e05e61ad6d50e8e9ea7b3e4593cfb07c99ac0af9f5064f48398db7ed4`. The local
index generation observed in its storage diagnostic was
`6e3c3a4d-4690-4868-8f9e-1023bbb7577c`; it was not modified.

```bash
env ZVEC_JIEBA_DICT_DIR=/home/masis/.cache/zvec-grep/out-noop-integration/jieba-dict \
  zg --fts searchWorkspaceIndex --limit 3 --mode direct --preview none
env ZVEC_JIEBA_DICT_DIR=/home/masis/.cache/zvec-grep/out-noop-integration/jieba-dict \
  zg --fts searchWork --limit 10 --mode direct --preview none
zg --rg -n -F searchWork src
/usr/bin/time -f 'elapsed_sec=%e maxrss_kb=%M' zg --rg -n -F searchWork src
```

Observed outputs:

| Query | Result |
|---|---|
| FTS `searchWorkspaceIndex` | Historical output: `src/engine/pipeline/search/index.ts:89-180`, `searchWorkspaceIndex`, rank 1; index validity was not established |
| FTS `searchWork` | Historical output: `No matches.`; a later query against the corrupt index failed, so this is not a valid indexed-search miss |
| `rg -F searchWork src` | Four occurrences in two files: the definition, its call, and the service import/call; all four were relevant for this query |
| Scoped `rg` cost | One observation: 0.01 s elapsed; 36,256 KiB maximum RSS on this host |

The successful baseline searches reported no stale status. A subsequent
unrelated FTS probe hit an invalid-checksum error while opening another index
partition; therefore no further measurements used the existing index. This
experiment did not attempt index repair or refresh.

A disposable synthetic FTS probe was considered to check CamelCase query
splitting. Its temporary workspace could not be indexed because the local
`local/potion-code-16m-v2` model artifact was absent. The CLI tried its normal
artifact sources and failed before indexing; no model artifact was downloaded
and no source was sent. I stopped rather than fetch a model or create another
index. A second probe used the Rust storage test fixture (static vectors, no
embedding model) but could not link: the only local `libzvec_c_api.so` lacked
the `zvec_config_data_set_jieba_dict_dir` and related symbols required by this
checkout. The temporary probe test was removed; no Rust source change remains.

## Hypotheses and decisions

| Hypothesis | Evidence | Decision |
|---|---|---|
| Split or normalize a partial CamelCase query and send it through indexed FTS | The original output looked like a partial-literal miss, but subsequent read-only integrity checks found index corruption and a repeated FTS request failed. | `INCONCLUSIVE`; no query-semantics conclusion is supported. Do not change the index or ranking under the current handoff. |
| Route a distinctive partial identifier to managed ripgrep, scoped to likely code paths | The actual `searchWork` query finds the definition and caller; one scoped run took 0.01 s / 36 MiB RSS. | `GO` as a low-complexity fallback-guidance change. Agent behavioral efficacy is `INCONCLUSIVE`; this is one query on one host, not a measured false-positive or broader-scan rate. |
| Add fuzzy/prefix matching to symbol metadata | It would require new search semantics and broader candidate acquisition; the current handoff rules out ranking/index work. | `NO_GO` for this mission. |
| Use a remote semantic planner to infer the missing identifier | The handoff blocks private-source inference without explicit authorization; this test needs no such inference. | `NO_GO` under the current authorization boundary. |

This probe does not separate acquisition from later stages on the indexed path,
because index validity is unknown for the historical “No matches” response. It
does establish that literal ripgrep can find the definition and immediate
navigation sites for this one partial identifier.

## Change

MCP and CLI-installed agent guidance now explicitly tells agents to search a
distinctive literal substring when a caller remembers only part of an
identifier, scope the search to likely code paths or file types, and avoid
treating an indexed no-hit as proof that the symbol is absent. The
managed-ripgrep tool description names partial identifiers directly. Indexed
retrieval, result ranking, index format, and production defaults are unchanged.

Expected trade-off: a scoped ripgrep scan uses local CPU and filesystem reads;
an overly broad fragment may return many unrelated matches. All four matches
were relevant in this single case; broader-query false-positive rates and costs
are unknown. The guidance asks for a distinctive substring and a likely
path/type scope to limit that cost.

## Prior work checked

These records informed the separation from earlier directions; they are
preserved in the handoff branch `work/r4-evidence-representation-20261119`:

- `rust/docs/r4-longrun-quality-20261121.md`: exact identifier search was
  5/5 rank-1; partial `EmployeeEditThongTin` produced 0 FTS results. Earlier
  diacritic folding and symbol-preference probes were rejected as defaults.
- `rust/docs/r4-deep-investigation-20261118.md` and
  `rust/docs/r4-gateab-20261117.md`: blind planner guesses and plan-query
  allocation were already measured; those engine/ranking directions are not
  repeated here.
- `rust/docs/r4-benchmark-readiness-handoff-20261121.md`: no new ranking,
  embedding or index work under the R4 authorization gate.

Reopen these exact tracked records without switching worktrees with:
`git show work/r4-evidence-representation-20261119:<path>`.

## Validation and review

- Baseline query outputs and scoped ripgrep output: recorded above.
- `npm run build`: pass.
- `node test/mcp-contract.test.mjs`: **25/25 pass**.
- `node test/install.test.mjs`: **80/80 pass** outside the sandbox. The first
  sandboxed run passed 55/80; one test hit `listen EPERM` and subprocess failures
  cascaded into empty-output assertions. The authorized rerun outside the
  sandbox passed all 80.
- The first contract run caught that inserting “partial identifier” between the existing “known word, symbol, filename” wording broke an established assertion. The description was reordered to keep the existing phrase intact while adding the new guidance; the rerun passed.
- Independent OpenAI review: the first pass found no P0/P1 and identified three P2 limitations: agent behavioral efficacy, broader cost/false-positive rates, and prior-work distinction. The report preserves these as `INCONCLUSIVE`/unknown and cites prior-work records. A focused follow-up reviewed the CLI-installed guidance and tests, found the MCP/CLI routing consistent, and identified one P3 rollback-doc omission; that omission is fixed below.
- No private HRM data was included in this report or review material.
- Sanitized review bundle: `docs/research/bundles/source-discovery-autonomous-20261122-review.zip` (checksum is in the adjacent `.sha256` file). It contains only the source diff, this report, and their manifest.

The remaining evaluation gap is behavioral: there was no controlled pair of
agents using the pre-change and updated instructions. The contract tests prove
the instructions are exposed in MCP and CLI-installed configurations, while
the query output proves the existing fallback can retrieve this entry point.
They do not prove a model follows the new rule more often or answers more
correctly.

## Rollback

Revert the guidance changes in `src/mcp/tools.ts` and `src/cli/install.ts`, plus
their assertions in `test/mcp-contract.test.mjs` and `test/install.test.mjs`.
The baseline index and the rest of the workspace are untouched.
