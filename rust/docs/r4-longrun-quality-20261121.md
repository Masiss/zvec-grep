# Long-run mission: search quality, foundation correctness & RAG effectiveness (2026-11-21)

Base `ce8f984` → HEAD at the end of this mission (branch
`work/r4-evidence-representation-20261119`, local, unpushed).

Identities: frozen corpus `/home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus`
(822 manifest files, 5.4 MB, index manifest `f6f9695a…`), embedding
potion-code-16m-v2 (dim 256, CPU), adapter built from this HEAD,
`LD_LIBRARY_PATH` = pinned zvec-prebuilt. No index rebuild, no model change,
no ranking default change, no public API change.

Relevance labels in this document are **diagnostic** (implementation agent),
not independent grading. Gold spans are used as an oracle diagnostic only,
never to steer a step of a blind/scripted run.

## 1. Metric reconciliation (the first required task)

The previous report's "8/25 gold coverage" and "47% of top-10 are >300-line
fragments" did not survive re-derivation. Corrected, with definitions:

| Metric | Definition | Value |
|---|---|---|
| Gold spans | frozen `case_spans` (path + inclusive line span) | 25 over D01–D12 |
| Entity-range overlap | delivered item's **entity/declaration** range intersects a gold span, same file | **9/25** |
| Delivered-excerpt overlap | the item's **delivered `content_range`** intersects a gold span | **4/25** |
| Gold-file reachability | a gold *file* appears anywhere in top-10 | **11/22 files** (10/12 cases) |
| Known-item rank-1 | exact identifier FTS returns the right declaration at rank 1 | **5/5** |

- The old 8/25 counted matching *items* rather than matched *gold spans*
  (a counting bug in the earlier script). The independent advisor's 9/25 and
  4/25 reproduce exactly.
- **Entity overlap ≠ delivered evidence.** Five gold spans sit inside a
  declaration the index matched while the delivered 30-line excerpt covers a
  different part of that declaration. This is the parent-range inflation the
  mission warned about; the honest delivery number is **4/25**.
- The "47% >300 lines" figure measured **entity/declaration ranges**, not
  delivered excerpts. Characterized properly (120 top-10 items):

  | | min | p50 | p90 | max |
  |---|---|---|---|---|
  | entity lines | 1 | 270 | 1067 | 1313 |
  | delivered excerpt lines | 1 | **29** | 51 | **65** |
  | delivered excerpt bytes | 36 | 1859 | 1976 | 2069 |

  Delivered context is small and bounded; the problem was never oversized
  excerpts but *which* 30 lines get delivered.
- Canonical R0 Recall@10 is the **macro average of per-case best-of-six
  calls**; none of the numbers above are that metric. For reference, the
  single verbatim hybrid call gives macro 0.50 (entity) / 0.25 (delivered).

## 2. Confirmed correctness defects (fixed, with regression tests)

| # | Defect | Reproduction | Status |
|---|---|---|---|
| C1 | Excerpt slices were byte-aligned (mid-line/mid-token) while coordinates claimed whole lines | D04 top-1 began `tNull(x => x.StaffCode…` at declared line 756 (engine-level: reproduced with `zg --mode direct`) | fixed `7ff962c` (line-aligned) |
| C2 | `content_columns` paired the exclusive end-line column with the inclusive `last_line` → coordinates did not reconstruct the content | 120-item reconstruction failed on line-aligned ranges | fixed `5152b6e` (byte offsets authoritative) |
| C3 | Byte offsets are **decoded-text** offsets (BOM stripped) while `path`+`sha256` identify raw bytes; 664/1812 corpus files have a UTF-8 BOM → offsets shifted by 3 | 59/120 excerpts not reproducible by slicing the file | fixed `5152b6e`: `raw_file_byte_offsets`, `file_encoding`, `content_sha256`; **120/120 now byte-identical** |
| C4 | SQL `reads` reported cursors, table-valued functions and table variables as tables; inline calls yielded garbled names (`splitstringtointarray(@ids`) | `sp_UpdateOrgTotalEmployee` reads | fixed `5758280`: typed `kind` = table/table_function/table_variable/cursor |
| C5 | `verify` deserialized every pack as an evidence SpanPack → workflow packs failed with `invalid_input`, so arm-b evidence could never be revalidated | arm-b workflow then verify | fixed `ec38484`: kind-aware verify re-hashes selected spans |
| C6 | arm-b read accounting used *selected spans* as reads (7 files vs 17 actually opened) | D02 workflow receipt | fixed `0815388` (engine `read_paths`) |
| C7 | Facts degraded payload replaced `facts.saves` (list) with a scalar, silently changing the shape | facts after budget pressure | fixed (degrade now emits `facts: null` + `facts_summary`) |
| C8 | Three delivered-byte fixpoint loops were unbounded | code audit | fixed (all bounded to 8 iterations) |

Verified after fixes: engine 802–803/803 (the documented `config::tests`
parallel-lock flake passes in isolation), adapter contract **35/35**,
synthetic three-arm end-to-end **36/36 runs, zero violations**.

## 3. Foundation checks with synthetic fixtures

Fixture workspace (external, indexed separately): plain LF, UTF-8 BOM+CRLF,
UTF-16LE, 6 KB single line, empty, whitespace-only, no trailing newline,
mixed-line-ending SQL, markdown fence.

- Byte-faithful delivery: every UTF-8/UTF-8-BOM excerpt reconstructs exactly
  from raw file bytes at the reported offsets (BOM handled).
- Long single line: excerpt `content_range [1,1]`, columns `[45,6054]`,
  content 6009 B — coordinates consistent.
- No-EOF file: symbol and excerpt correct.
- Empty file: skipped at index with an explicit `Empty` reason.
- Freshness: after modifying a file without refresh, search returns
  `status: possibly_stale`, **withholds `sha256`**, and delivers the indexed
  (not current) text — the consumer cannot mistake stale content for current.
- **UTF-16 limitation (honest gaps, no wrong output):** the file is indexed
  and searchable, but `facts` fails `invalid_input` and `evidence` returns a
  typed `not_utf8` gap because both read raw bytes as UTF-8. No corpus file is
  UTF-16/32, so benchmark impact is zero; converting them would be an engine
  contract change (owner decision).
- Markdown fenced code is searchable but not resolvable by `symbol` (fence
  content is not a code entity). Documented, not a correctness defect.

## 4. Question-to-anchor discovery (the real bottleneck)

Investigated with deterministic, gold-free probes plus guided (explicitly
labelled non-blind) investigations:

| Probe | Result |
|---|---|
| Exact identifier → FTS | rank-1 correct declaration **5/5** |
| Partial identifier (`EmployeeEditThongTin`) → FTS | **0 results** (tokenizer is jieba+lowercase; CamelCase boundaries are tokens, no prefix matching) |
| Exact `symbol` lookup | exact + case-sensitive by contract; ambiguous names return all declarations with scope+signature; nonexistent → 0 |
| Verbatim business questions (hybrid) | 4/25 delivered spans, 11/22 files, 6/12 cases zero-hit |
| Query-side diacritic folding (E1, probe) | 6/25 delivered, but **9/22 files** (worse); fused union 12/22 files, 6/25 spans at ~2× query cost |
| `prefer_symbol` (E3, probe) | 5/25 spans, D04 named method to rank 1, but **9/22 files** (worse) |
| Scripted lexical discovery loop (6 calls) | 6/25 spans — seeds are generic (`ChangeCompare`, `CommandResult`) |

Root cause is specific and now measurable: the corpus is
**`partial class CoreService` spread over 307 files** (27 for
`EmployeeController`). A class-level hit is therefore a per-file member
fragment, not the class; its outline lists only that file's members. Method
names carry the meaning, and Vietnamese business vocabulary does not reach
them lexically.

Consequences:
- Identifier-driven queries are reliable. Business questions need a
  vocabulary→identifier step that the index cannot provide.
- Diacritic folding and symbol preference each move a couple of dev cases
  and cost file-level reachability: both are **NO_GO as defaults**; they stay
  available as explicit opt-ins with truthful semantics.
- Statically guided reachability is real: with the CoreService hit in
  EmployeeCommand.cs, `symbol` → `facts --method` → `saves` at lines 285/425
  → `evidence` both spans → `verify` completes D05's atomicity question in
  5 calls without gold. That is capability proof, not blind-agent proof.
- D02 and D10 have **no gold-file anchor** in top-10 (0/4 and 0/2): those are
  acquisition failures (concept→identifier), not ranking or context defects.

## 5. Delivered-evidence improvements shipped

- `declaration` block on every search item: symbol_type, symbol_name, scope,
  signature, visibility. A consumer can now tell "same file, wrong method"
  from a hit on the method it asked about. Cost: **+8.1%** envelope bytes
  (22.5→24.3 KB mean top-10 per case).
- `search --outline`: the engine's structural outline (members + calls),
  opt-in (≤3.6 KB/item).
- `search --prefer-symbol`: exposes the existing symbol-preference route,
  opt-in.
- Exact coordinates: `content_byte_offsets` (decoded), `raw_file_byte_offsets`
  (raw file, null for UTF-16/32), `content_columns`, `content_sha256`
  (search and evidence).
- SQL facts: typed access kinds; inline-call identifiers preserved.
- Workflow: real `read_paths` accounting; verifiable packs.

## 6. Harness (R4 inference) — completed evidence

Acceptance items and how they are proven (`r4_bench_driver_selftest.py`,
29/29 checks):

1. initial request carries question + instructions + arm-specific tool
   schemas (mock validates equality with the arm contract);
2. tool requests parsed and strictly validated (unknown tool, bool/float,
   traversal path, cross-arm tool all rejected without execution);
3. actual tool outputs returned to the model as `function_call_output` items
   keyed by `call_id`, preceded by the assistant `function_call` item;
4. the mock's next response is **derived from the received output** (it reads
   the delivered top item and requests evidence/symbol/facts from it);
5. round limit **blocks** further turns (a status-spamming transport is
   refused, and refused turns never execute);
6. arm-b receives the trusted plan (`--plan-file`) and produces a workflow
   pack from it;
7. final answer captured, frozen, SHA-256 verified;
8. common final verification runs for arms with packs (arm-b True, arm-c
   True, arm-a reports "no pack" — correct, not a failure);
9. independent grading runs in a separate process that sees only the frozen
   answer + rubric;
10. accounting matches actual calls/reads/rounds (arm-b: 1 external + 5
    internal, 17 reads for 7 selected files);
11. unauthorized real inference is refused (exit 3);
12. authorized execution reaches a real HTTP transport — proven against a
    loopback Responses-API server (request shape, tool parsing, served-model
    identity), with no egress and no private source.

Three-arm synthetic run over all 12 dev cases (mock transport, real corpus,
real adapter):

| Arm | calls (ext+int) | read files | delivered bytes | pack verified | wall median |
|---|---|---|---|---|---|
| A | 2 + 0 | 9–14 | 24.8–38.8 KB | n/a (no pack) | 1.66 s |
| B | 1 + 5 | 15–25 | 12.7–14.6 KB | **True** (12/12) | 5.29 s |
| C | 2 + 0 | 8–10 | 17.6–31.3 KB | **True** (12/12) | 1.25 s |

Peak child RSS 423 MiB (embedding model inside search). No violations, no
budget overruns, frozen answers hashed in all 36 runs.

## 7. Answers to the mission's questions

1. **How correct is zvec-grep?** Exact for identifier-anchored work: known-item
   and symbol lookups are rank-1 correct, excerpts are now byte-identical to
   the source at their declared coordinates (120/120), SQL facts are typed,
   evidence revalidates honestly, stale content is flagged and unhashed.
   Not sufficient for Vietnamese business questions: 4/25 delivered gold
   spans, 11/22 gold files reachable.
2. **Biggest error source?** Not the excerpt layer (fixed) and not the
   relation layer (typed) — it is **concept→identifier acquisition** on a
   corpus whose domain nouns live only in identifiers, compounded by
   `partial class` fragmentation (307 files for one class).
3. **Defects fixed:** 8 confirmed correctness defects (C1–C8), each with a
   before/after reproduction and (except C8) a regression test.
4. **Did question-to-anchor discovery improve?** Only marginally and not
   free: declaration identity + outlines make each hit interpretable and give
   an authoritative member list per file; diacritic folding (+2 spans, −2
   files) and `prefer_symbol` (+1 span, −2 files) were rejected as defaults.
5. **Did delivered evidence improve?** Yes on trust: exactly verifiable bytes,
   correct encoding offsets, declaration identity, typed SQL relations,
   verified workflow packs. Not on recall: selection is unchanged (4/25).
6. **Do symbols/relations recover evidence?** Yes when an anchor exists:
   `symbol` → `facts` → `evidence` completed D05 (guards, both SaveChanges)
   and the C#→SQL chain in D06 in ≤5 calls. They cannot invent an anchor.
7. **Experiment verdicts:** E1 diacritic folding — **NO_GO** (marginal, 2×
   cost, worse file reachability); E2 declaration identity + opt-in outline —
   **GO** (shipped, +8.1% bytes); E3 `prefer_symbol` — **NO_GO as default**
   (shipped as opt-in).
8. **Agent-driven architecture:** evidence supports direct-agent integration
   *with an identifier-discovery step*; it does not support relying on lexical
   retrieval for business questions, and it does not support dropping
   engine-managed work (arm-b remains the only arm with a verified pack at
   ~1/2 the delivered bytes).
9. **Does the harness run end-to-end?** Yes — 36/36 synthetic runs through the
   real conversation path, and the real HTTP transport is exercised against a
   loopback provider. Real private-source inference remains blocked on the
   owner's authorization.
10. **Owner decisions needed:** (a) method-level index units (the single
    highest-value representation change; now backed by 307-file partial-class
    evidence), (b) whether to decode non-UTF-8 files in facts/evidence,
    (c) `prefer_symbol`/diacritic-folding promotion, (d) private-source
    inference authorization.
11. **Highest-value next action:** method-level index units with declaration
    headers (preregistered success: selected ≥12/25 at 24 KiB, macro ≥0.60,
    ranks/calls unchanged), because every measured acquisition failure traces
    to class-fragment granularity.

## 8. Limits of these conclusions

- Relevance labels are diagnostic; no independent grader ran on real answers.
- Single-call measurements (no repeats) for the retrieval tables; the index
  and ranking are deterministic.
- Only 12 dev cases; no held-out set was opened.
- The synthetic three-arm run proves harness correctness, not answer quality:
  the mock answerer is deterministic and its "answers" are transcript facts.
- UTF-16/32 coverage is a known gap, zero corpus impact.
