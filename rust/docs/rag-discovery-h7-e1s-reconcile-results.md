# H7 E1S — refs-off reconciliation + single memory optimization result

Status: **NO-GO (resource).** Date: 2026-11-09.
Base `416a312` (`work/h7-resource`, H7 E1R); branch `work/h7-e1s`; worktree
`/home/masis/src/worktrees/zvec-grep/h7-e1s-20261109`.
Pre-registration: `benchmarks/rag-impact/DISCOVERY-H7-E1S-PREREGISTRATION.md`.
Historical E1/E1R receipts and H7 E1/E1R NO-GO verdicts are unchanged. E2 is not
run; no cap, gate, corpus, gold or held-out change. Selection/Expansion remain
frozen.

Environment: debug test profile, single-test processes under bwrap
(host and repo read-only; only the experiment OUT and the external target dir
writable), `CARGO_TARGET_DIR=~/.cache/zvec-grep/rust-target-h7-e1s-20261109`,
`ORT_DYLIB_PATH` pointing at the rollback runtime. No push, no release, no
`rust/target` write.

## 1. Refs-off regression (20/25 → 16/25 @500) — attribution

Four arms over the same 12 dev cases, seeds and gold, one process
(`results/h7-e1s-reconcile.json`):

| Arm | @10 | @50 | @100 | @250 | @500 |
|---|---:|---:|---:|---:|---:|
| E1 full-facts refs-off | 7 | 12 | 14 | 20 | **20** |
| E1R compact refs-off | 8 | 12 | 14 | 16 | **16** |
| E1 full-facts refs-on | 7 | 13 | 15 | 20 | **23** |
| E1R compact refs-on | 8 | 14 | 15 | 20 | **23** |

- The full-facts off arm reproduces the historical E1 totals exactly, so the
  E1 baseline is reproducible in this tree on today's corpus.
- The compact off arm reproduces the E1R receipt exactly.
- Every off span lost by E1R is a gold span in a **non-anchor file** whose
  candidate evidence came from full project facts:
  - D02 `EmployeeCommand.cs` — `assignment query match, invocation query match`;
  - D07 `OrganizationCommand.cs` — `invocation query match, callee of seed`;
  - D10 `CoreDBContext.cs:9` — `method-name query match`.
- The two compact off gains (D01 @10, D07 @100) are smaller-pool ranking effects,
  not better retrieval.
- refs-on is 23/25 in both arms with no gold span flipping at any budget except
  D06 @50 (compact 3, full 2); per-case closure hashes differ only because the
  candidate pools differ.

**Method: the 20→16 delta is a protocol/definition change, not a parser
semantics regression.** The E1R harness computed refs-off through
`expand_with_declarations` on anchor-only facts. The E1R pre-registration §3
defines refs-off as `expand_with_closure(..., false)` — the full-fact baseline.
Restricting off to anchor-only facts removes real project-wide method,
assignment and invocation evidence. Consequences:

- refs-off in the E1R receipt is **not comparable** to the E1 refs-off column;
- the on-arm gain over the historical baseline is **+3 @500** (23 vs 20), not
  +7 (23 vs 16);
- future receipts must compute refs-off with the full-fact protocol (or label a
  restricted arm explicitly as a different arm).

## 2. Resource attribution

Baseline reproduction of the fixed gate harness (identical tree, single test
process): coverage `8/14/15/20/23`, refs-off `16`, peak RSS **72,956 KiB**, end
PSS **68,910 KiB** (historical receipt: 74,028 / 69,387). Attribution runs in
`results/h7-e1s-attribution.json`, `h7-e1s-anchor-scaling.json`,
`h7-e1s-parser-churn.json`.

| Phase | PSS KiB | Delta |
|---|---:|---:|
| process start (test binary + linked libs) | 24,186 | — |
| corpus loaded (5.4 MB sources + frozen JSON) | 32,738 | +8,552 |
| declaration index built (820 files) | 53,122 | +20,384 |
| second full source copy (`all_files`) | 55,090 | +1,968 |
| first anchor parse (26 files) | 67,478 | +12,388 |
| after dropping the anchor project | 66,058 | −1,420 |
| after dropping declarations at end | 65,306 | −752 |

- Live declaration data is **1.84 MB** (1,372 types, 8,689 fields, 234 DbSets).
  The remaining ~18 MiB of the declaration phase is allocator-retained churn:
  dropping the whole index frees only ~0.8 MiB PSS.
- Raw tree-sitter parsing of all 817 sources costs +8.7 MiB PSS on the first pass
  and ~0 on later passes. Fresh vs reused `Parser` per file makes no difference
  (47,312 KiB peak in both).
- Anchor parsing with the empty scope costs +6.4 MiB for 26 files; the scoped
  path adds churn but the peak is dominated by fact construction, not by scope
  size (see §3).
- 12 anchor parses across cases reuse the retained heap; the peak is reached in
  the first cases and stays.

## 3. The single optimization (pre-registered) and its result

Change: share the project scope through `Arc` instead of deep-cloning it into
every parsed file — `ProjectScope.fields_by_type` and `Context.shared_fields`,
plus `ProjectScope.db_sets`/`FileFacts.db_sets` as `Arc<Vec<DbSetFact>>` with
copy-on-write for the DbSet-property recording path. No parser rewrite, no new
parser mode, no changed fact semantics, no cap/harness/gate change.

A first variant that skipped seeding the scope DbSets into anchor files was
**rejected**: `dbset_element_for` consults `FileFacts.db_sets` during type
inference, so the anchor arm's candidate pool changed (390→381 candidates and
22/25 @500). It was replaced by the Arc approach above, which keeps the seeded
list and preserves every candidate byte.

Parity (final tree, `results/h7-e1s-resource-after.json`):

| Check | Before | After |
|---|---|---|
| coverage @10/50/100/250/500 | 8/14/15/20/23 | 8/14/15/20/23 |
| refs-off @10/50/100/250/500 | 8/12/14/16/16 | 8/12/14/16/16 |
| per-case candidates | 234–500 | identical |
| per-case closure hash | frozen | identical |
| closure diagnostics / items | frozen | identical |
| independent easy / hard | 5/5, 5/5 | 5/5, 5/5 (exact receipt parity) |
| determinism (two fresh runs) | PASS | PASS (identical hashes/coverage) |
| on-arm latency p50 / p95 | — | 16.5 ms / 26.8 ms |
| total run | — | 6.77 s |
| declaration build | — | 2.90 s |

Resource (same harness, same host class, single runs):

| Measure | Before | After |
|---|---:|---:|
| peak RSS (`VmHWM`) | 72,956 KiB | **71,396–73,608 KiB** (3 runs) |
| end PSS | 68,910 KiB | 67,518–69,730 KiB |
| gate 65,536 KiB | FAIL | **FAIL** |

**The optimization is semantics-neutral but memory-neutral at the gate.** The
deep clones it removes were transient and were already served from
allocator-retained pages, so they did not drive the peak. Measured run-to-run
spread on this host is ~2 MiB (host load average 1.7–2.5, ~11 GB swap in use),
larger than the effect of the change.

## 4. Gate

| Gate | Result |
|---|---|
| Coverage @500 ≥23 | PASS 23/25 |
| Independent easy 5/5 | PASS |
| Independent hard ≥4/5 | PASS 5/5 |
| Determinism | PASS |
| Provenance (closure reason/diagnostic template) | PASS (identical to E1R) |
| No hardcode | PASS (no corpus identifier in the product diff) |
| Peak PSS/RSS ≤64 MiB | **FAIL 71,396–73,608 KiB** |
| I/O reconciliation | FAIL/partial (initial 5.4 MB source load still precedes the counter) |
| Latency warm p95 ≤5 s/case | PASS (26.8 ms) |

Verdict: **NO-GO (resource)**. E1 and E1R stay historical NO-GO; H7 E2 remains
unauthorized.

## 5. Architectural limit (why the fixed 64 MiB gate is not reachable here)

The gate is an absolute peak-RSS threshold measured in one process that parses
the whole corpus. With the authorized change set, its floor decomposes as:

- **~24 MiB** fixed process baseline (test binary, linked runtime, libc);
- **~9 MiB** corpus residency (5.4 MB sources plus frozen JSON) with a further
  ~2 MiB second copy held by the measurement harness;
- **~19–20 MiB** allocator-retained churn from building the declaration index
  over 820 files, while the live index is only 1.84 MB;
- **~12 MiB** retained churn from anchor full-fact construction (methods,
  assignments, invocations, saves), which is inherent to the existing tree-sitter
  walk's per-node string allocation.

Passing 65,536 KiB would require reducing the retained churn by roughly 6–9 MiB
in an absolute-RSS sense. Under the current constraints that means one of:

1. a string-interning/arena refactor of the fact producers (explicitly outside
   the authorized scope: "no parser rewrite", one optimization only);
2. an allocator change or an explicit trim/arena reset (needs `unsafe` or a new
   dependency; `unsafe_code = "forbid"` in the workspace);
3. a different memory model for the gate path (out-of-process or streaming
   declaration build), which is an architecture change, not a memory tweak.

Reporting the limit rather than repeating attempts is the pre-registered stop
rule.

## 6. Verification performed

- `cargo test -p zg-engine --lib extraction::h7_e1s_tests::h7_e1s_resource`
  (single-test process, three runs; final receipt from the final tree).
- `...h7_e1s_reconcile`, `...h7_e1s_attribution`, `...h7_e1s_anchor_scaling`,
  `...h7_e1s_parser_churn`.
- `h7_e1r_compact_external` on `dev-independent` and `dev-independent-hard`:
  exact parity with the frozen E1R receipt (coverage, refs-off, candidates,
  hashes, gold).
- Full `zg-engine` lib suite under the qualified bwrap containment:
  **741 passed, 0 failed, 29 ignored** (98 s).
- `cargo fmt --check` clean; `cargo clippy -p zg-engine --lib --tests` exit 0.

## 7. Artefacts

- `benchmarks/rag-impact/DISCOVERY-H7-E1S-PREREGISTRATION.md`
- `benchmarks/rag-impact/results/h7-e1s-reconcile.json`
- `benchmarks/rag-impact/results/h7-e1s-resource-before.json`
- `benchmarks/rag-impact/results/h7-e1s-resource-after.json`
- `benchmarks/rag-impact/results/h7-e1s-attribution.json`
- `benchmarks/rag-impact/results/h7-e1s-anchor-scaling.json`
- `benchmarks/rag-impact/results/h7-e1s-parser-churn.json`
- `benchmarks/rag-impact/results/h7-e1s-independent-{easy,hard}.json`
- Raw harness logs and host state:
  `~/.local/share/zvec-grep/rag-impact/20261109-h7-e1s/`
