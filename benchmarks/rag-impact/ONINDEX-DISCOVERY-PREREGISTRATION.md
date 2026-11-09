# One Index → Specialized Discovery — pre-registration (Code A/B, Documents)

Status: **pre-registered before implementation.** Date: 2026-11-09.
Base `9444eed` (`work/zg-recovered`). Worktree:
`/home/masis/src/worktrees/zvec-grep/onindex-20261109`, branch `work/onindex`.
Historical H7 E1/E1R/E1S verdicts stay NO-GO. E2 is not run. Selection and
Expansion stay frozen. No production defaults, no index format, no push/release.

## 0. Index audit inputs already measured (before this pre-registration)

Index: pre-built B0 workspace index (`run1`, 818/818 files, 33 MB on disk,
FTS jieba+lowercase, potion-code-16m-v2 vectors), copied read-only to
`~/.local/share/zvec-grep/rag-impact/20261109-onindex/workspace`.

| Capability | Evidence |
|---|---|
| FTS recall by name/body | `--fts "employee"` returns code entities with ranges |
| Path/glob filters, `--limit` | available on the public context API |
| Exact-symbol recall (`prefer_symbol`) | FTS route filtered by hashed `symbol_name`; returns the declaring entity |
| `symbol_type` filter | **returns no matches for every type** — filter path unusable as-is |
| Symbol metadata in results | **not exposed** (`ContextItem.metadata`/`container` are `None`) |
| Reference/declaration edges in production | **not produced** (`ExtractionOutput.graph` is always `None` outside tests) |
| Resident process footprint | CLI query ~297 MB peak; in-process open + first query ~398 MB HWM; B0 server idle PSS 571,800 KiB, peak 769,113 KiB |

Consequence for measurement: the historical 64 MiB gate was defined on the
parse-only H7 harness. An index-resident process cannot meet an absolute 64 MiB
peak. This pre-registration therefore reports **two numbers and applies the
gate to both**:

- **total process peak** (RSS/PSS of the discovery process, index open included);
- **incremental discovery peak** (peak minus the warm, index-open baseline),
  which is the per-case budget a resident service actually spends.

No threshold is redefined: the historical ≤65,536 KiB gate is reported as PASS
or FAIL for the total process, and separately for the incremental delta with the
label "incremental". A GO requires coverage and correctness gates plus the total
gate; an incremental-only pass must be reported as FAIL on the historical gate
and as an architecture finding, not as a PASS.

## 1. Code Discovery experiments (at most two)

Both reuse the existing `csharp_facts` parser/fact layer and the existing H7
closure (`expand_with_declarations`), the frozen dev cases, seeds from
`b0-official.json`, and gold from `cases/dev.json`. No parser rewrite, no new
persistent index, no LSP/LLM/graph, no gold-driven policy.

Protocol (identical to H7 E1R/E1S):
- 12 dev cases (D01–D12), six frozen B0 calls per case as seeds (anchors).
- cap 500 distinct candidate ranges/case; coverage at 10/50/100/250/500 by
  path + inclusive line overlap; refs-off column reported with the **full-fact**
  protocol (E1 definition) so the baseline is comparable.
- determinism: two fresh runs, identical per-case closure hashes.

### Experiment A — index-assisted declaration lookup

Per case:
1. Parse the anchor files with an **empty** scope to obtain anchor methods,
   assignments, invocations and saves.
2. Collect the declaration names the closure needs from those facts: receiver
   types, save context types, and observed DbSet property element types.
3. For each name (deterministic order, bounded to `MAX_LOOKUPS = 96`/case),
   query the existing index: FTS route, `query = name`, `prefer_symbol = true`,
   `limit = 8`, `refresh = off`, `auto_update = false`, code files only.
4. Verify each returned range by reading **that file only** (full file, ≤1 parse
   per file per case) and keeping only declarations whose name equals the
   looked-up name. Unverified hits become `UNRESOLVED(reason)`.
5. Re-parse the anchor files with the verified scope (second pass), then run the
   unchanged closure.
6. Provenance per lookup: `{name, status, source=index, file, range, rules}`
   with reasons `no_index_hit`, `hit_not_code`, `not_declared`, `verify_failed`.

Bounded cost: ≤96 queries/case, ≤8 hits/query, one full-file parse per distinct
candidate file, one cached declaration extraction per file.

### Experiment B — bounded per-file structural extraction (lexical selection)

Same protocol, but declaration files are selected by a **bounded lexical scan**
instead of the index:
1. One pass over the corpus compile subset (818 `.cs` files), building a map
   `name → first N (≤4) declaration sites` for exactly the needed names using
   `class|struct|interface|record <name>` / `enum <name>` text matching.
2. Parse only the selected files (≤4/name, ≤`MAX_FILES = 64`/case), keep
   types/fields/DbSets only, drop the rest.
3. Same second anchor pass and closure.

No index open, no model, no embedding; memory is the parser/allocator envelope
only. This is the resource-oriented arm.

### Shared reuse check

The harness also runs the **full** mode (declaration index built from all 820
files, E1R definition) in the same process family, to prove the harness
reproduces the frozen E1R coverage/closure hashes before A/B are interpreted.
`full` is a control, not a candidate.

### Failure rule

If A fails coverage (<23/25 @500) or the resource/correctness gate, record the
per-name unresolved histogram and move to B. If both fail, keep NO-GO and report
the architectural limit. No retuning against gold, no cap changes.

## 2. Document Discovery (design + bounded fixture prototype only)

No typed closure for documents and no new index. The prototype must show:
- one shared candidate/provenance envelope for code and documents;
- document-specific grouping: Markdown heading/section path, JSON key/path,
  CSV header/row key, log event id/timestamp;
- bounded expansion and deterministic order.

Implementation is a test-only module over synthetic fixtures, using the same
`DiscoveryCandidate` envelope and the existing index metadata shape
(`MarkdownMetadata.heading/scope/level`) as the interface. It makes **no**
coverage/quality claim and is not a candidate for any gate.

## 3. Verification and gates

| Gate | Threshold |
|---|---|
| Coverage @500 dev12 | ≥23/25 |
| Coverage @10/50/100/250 | reported; no new threshold |
| Independent easy / hard | 5/5, ≥4/5 (same harness, index-mode where applicable) |
| Determinism | identical per-case closure hashes in two fresh runs |
| Provenance | every closure item carries anchor/expression/declaration/rule/status; every lookup carries a status+reason |
| No hardcode | no corpus identifier or case id in the product/prototype diff |
| Peak RSS/PSS, total process | ≤65,536 KiB |
| Peak RSS/PSS, incremental over warm index | ≤65,536 KiB (reported separately, not a substitute for the total gate) |
| Latency | warm p95 ≤5 s/case; cold reported separately |
| I/O | source bytes read vs rchar, per case and total |
| Search calls | reported per case (index queries / lexical scans / file parses) |

Measurements: isolated worktree, external `CARGO_TARGET_DIR`, bwrap containment
(host/repo read-only, OUT and target writable), pre-built index opened read-only
(`refresh=off`), debug profile for functional parity and **release profile for
the resource receipt** (production-representative). Host state recorded before
and after each measurement; single run per condition unless a decision needs a
repeat.

## 4. Architecture decision

Choose the arm with the best quality/resource trade-off, or NO-GO if both fail.
State explicitly when tiered retrieval, a bounded frontier, or optional
incremental metadata is required, and do not implement a new index format.
