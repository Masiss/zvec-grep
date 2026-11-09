# H7 E1S — reconcile + single targeted memory optimization (pre-registration)

Status: **pre-registered before implementation.** Date: 2026-11-09.
Base: `416a312` (`work/h7-resource`, H7 E1R). Worktree:
`/home/masis/src/worktrees/zvec-grep/h7-e1s-20261109`, branch `work/h7-e1s`.
Historical E1/E1R receipts stay immutable. H7 E2 is not authorized. Selection and
Expansion stay frozen.

## 1. Measurements already taken before this pre-registration

All runs used the external target
`~/.cache/zvec-grep/rust-target-h7-e1s-20261109`, bwrap containment (host and
repo read-only, only the experiment OUT and cache bound writable), debug test
profile, single-test processes. Evidence:
`~/.local/share/zvec-grep/rag-impact/20261109-h7-e1s/`.

### 1.1 Regression attribution (test `h7_e1s_reconcile`)

Four arms on the same 12 dev cases/seeds/gold:

| Arm | @10 | @50 | @100 | @250 | @500 |
|---|---:|---:|---:|---:|---:|
| E1 full-facts refs-off | 7 | 12 | 14 | 20 | **20** |
| E1R compact refs-off | 8 | 12 | 14 | 16 | **16** |
| E1 full-facts refs-on | 7 | 13 | 15 | 20 | **23** |
| E1R compact refs-on | 8 | 14 | 15 | 20 | **23** |

- The E1 full-facts off arm reproduces the historical E1 refs-off totals exactly
  (`7/12/14/20/20`), so the historical baseline is reproducible in this tree.
- The compact off arm reproduces the E1R receipt (`8/12/14/16/16`).
- Every lost off span is attributable to facts that only exist in non-anchor
  files, and the E1R off arm no longer parses those files:
  - D02 gold in `EmployeeCommand.cs` — candidate reason in E1
    `assignment query match, invocation query match`; not an anchor file.
  - D07 gold in `OrganizationCommand.cs` — `invocation query match, callee of seed`;
    not an anchor file.
  - D10 gold in `CoreDBContext.cs:9` — `method-name query match`; not an anchor
    file.
- The two compact off gains (D01 @10, D07 @100) come from a smaller competing
  candidate pool, not better retrieval.
- refs-on: 23/25 in both arms; no gold span flips at any budget except D06 @50
  (compact 3, full 2). Candidate hashes differ because the pools differ, but
  @500 coverage is identical.

**Attribution: the refs-off delta is a protocol/definition change, not a parser
semantics regression.** E1R computed refs-off through
`expand_with_declarations` on anchor-only facts; the E1R pre-registration
§3 actually defines refs-off as `expand_with_closure(..., false)` (the full-fact
baseline). The restricted off arm drops real project-wide method/assignment/
invocation evidence; it must not be reported as the historical baseline, and it
must not be used to claim a larger on-arm gain (fair gain vs E1 off = +3 @500;
E1 on = E1R on = 23).

### 1.2 Resource profile (tests `h7_e1s_resource`, `h7_e1s_attribution`, `h7_e1s_anchor_scaling`, `h7_e1s_parser_churn`)

Baseline reproduction of the fixed gate harness: coverage `8/14/15/20/23`,
refs-off `16`, peak RSS **72,956 KiB**, end PSS **68,910 KiB** (historical
74,028 / 69,387).

Phase PSS (same process):

| Phase | PSS KiB | Delta |
|---|---:|---:|
| process start (test binary) | 24,186 | — |
| corpus loaded (5.4 MB sources + frozen JSON) | 32,738 | +8,552 |
| declaration index built (820 files) | 53,122 | +20,384 |
| `all_files` second source copy | 55,090 | +1,968 |
| first anchor parse (26 files) | 67,478 | +12,388 |
| after dropping anchor project | 66,058 | −1,420 |

- Live declaration data is only **1.84 MB** (1,372 types, 8,689 fields,
  234 DbSets); the other ~18 MiB is allocator-retained churn. Dropping the
  declaration index after the run frees only 0.8 MiB PSS.
- Raw tree-sitter parsing of all 817 sources costs **+8.7 MiB** PSS in the first
  pass and ~0 on repeat passes; a reused vs fresh `Parser` per file makes no
  difference (47,312 KiB HWM in both modes).
- Anchor scaling: 26 anchor files parsed with the empty scope cost +6.4 MiB PSS;
  the same files through `parse_anchor_facts` (project scope) cost +5.6 MiB
  more, and the peak HWM grows during the scoped parses.

**Attribution: the remaining gate excess is allocator-retained churn, dominated
by (a) the full declaration pass and (b) anchor parses that deep-clone the whole
project scope into every parsed file.**

## 2. The single authorized optimization

**Share the project scope instead of deep-cloning it per parsed file.**

Mechanism (one change, C# fact extraction only):

1. `ProjectScope.fields_by_type` and `Context.shared_fields` become `Arc`-shared;
   `parse_file_with_scope` clones a refcount instead of the full project field
   map (8,689 entries × 4 strings) for every parsed file.
2. Files parsed through `parse_anchor_facts` no longer materialise the scope
   DbSet list into `FileFacts` (that caller clears it on arrival anyway); the
   aggregation callers keep the current seeding path unchanged.

Not in scope: parser rewrite, new parser mode, changed fact semantics, allocator
swap, global caches, harness/protocol changes, source-release tricks, caps,
gold, corpus, held-out, targets, persistent index.

The gate harness (`h7_e1s_resource`) is **byte-identical** to the baseline run,
including the second source copy and per-case `parse_anchor_facts` calls, so
before/after are directly comparable.

## 3. Fixed gate (unchanged)

| Gate | Threshold |
|---|---|
| Coverage @500 dev12 | ≥23/25 |
| Coverage @10/50/100/250 | report; no new threshold |
| Independent easy / hard | 5/5 and ≥4/5 |
| Determinism | identical candidate hashes in two fresh runs |
| Provenance | 100% closure items with anchor/expression/declaration/target/rule/status |
| No hardcode | no corpus identifier special case in the product diff |
| Peak PSS/RSS | ≤65,536 KiB |
| Latency | warm p95 per case ≤5 s; cold/warm reported |
| I/O | source bytes vs rchar reconciled; per-case I/O reported |

Coverage/provenance parity is required against the E1R compact arms: refs-on
must stay `8/14/15/20/23`; compact refs-off must stay `8/12/14/16/16`.

## 4. Prediction and stop rule

- Expected: remove ~11 M allocated objects in anchor parsing per case and ~10 MiB
  of transient scope memory; predicted peak RSS 62–66 MiB.
- If the fixed gate still fails after this one change: keep **NO-GO**, report the
  architectural limit (allocator-retained declaration churn + fixed process
  baseline + corpus/JSON residency), and do not attempt a second optimization,
  retune caps, or change the harness.
- If coverage/determinism/provenance regress: revert the change and keep NO-GO.
