# RAG v1.0 handoff — 2026-11-09 (after H7 E1S)

Branch: `work/h7-e1s` (experiment only; not merged, not pushed).
Base: `416a312` (`work/h7-resource`). Worktree:
`/home/masis/src/worktrees/zvec-grep/h7-e1s-20261109`.
Main repo `work/zg-recovered` HEAD `9444eed` remains the integration branch.

## 1. Status

| Layer | Status |
|---|---|
| H7 E1 typed reference closure | **historical NO-GO**, receipt unchanged |
| H7 E1R compact declaration index | **NO-GO (resource)**, receipt unchanged |
| H7 E1S reconcile + scope sharing | **NO-GO (resource)**; coverage/independent/determinism/provenance PASS |
| Discovery | **NO-GO** |
| Selection | **FROZEN** |
| Expansion | **FROZEN** |
| R1 delivery | **PARTIAL**; independent qualification continues |
| RAG v1.0 | **NOT GO** |

E2 is not run. No cap, gate, corpus, gold, held-out, target or default change.
No push/release. Protected `rust/target` untouched (external target only).

## 2. What E1S established

1. **Refs-off regression is a protocol artefact, not a parser regression.**
   With the E1 full-fact protocol the off arm reproduces `7/12/14/20/20` exactly.
   E1R computed off on anchor-only facts, which removes project-wide method /
   assignment / invocation evidence in non-anchor files (D02, D07, D10). Fair
   on-arm gain over E1 is **+3 @500**, not +7.
2. **The 64 MiB gate is not reachable with the authorized change set.**
   Peak RSS 72,956 KiB before, 71,396–73,608 KiB after the pre-registered
   scope-sharing change (semantics byte-identical). Decomposition: ~24 MiB
   process baseline, ~9 MiB corpus residency, ~19–20 MiB allocator-retained
   declaration churn (live index only 1.84 MB), ~12 MiB retained anchor fact
   construction. Run-to-run spread on this host is ~2 MiB.
3. **Scope sharing is memory-neutral at the gate.** The removed deep clones were
   transient and served from already-retained pages. It is kept on the
   experiment branch only; it does not justify promotion or a contract change.

## 3. Current commit contents (branch `work/h7-e1s`)

- `ProjectScope.fields_by_type` / `Context.shared_fields` are `Arc`-shared;
  `ProjectScope.db_sets` / `FileFacts.db_sets` are `Arc<Vec<DbSetFact>>` with
  copy-on-write for DbSet-property recording. Semantics unchanged (identical
  candidate sets and hashes).
- Test-only measurement harness `extraction/h7_e1s_tests.rs` (four ignored
  readings: reconcile, resource, attribution, anchor scaling, parser churn).
- Pre-registration and results note plus receipts.

## 4. Exact next state

Do not re-attempt H7 resource tuning or E2 without a new owner decision. Any
further work on the 64 MiB gate needs an explicitly authorized *different*
memory model, for example:

- string interning / arena ownership for fact producers (currently excluded as a
  parser rewrite);
- an allocator or process-model decision (out-of-process declaration build,
  streaming parse), which is an architecture change;
- a gate redefinition approved by the owner (e.g. separate peak for the
  declaration phase vs per-case serving), which is a contract change.

Independent R1 work can continue now: CLI/MCP/service integration, cache/version
invalidation, p95/PSS/concurrency load, generated/results admission,
PII/secret/auth/audit, independent validation and release/rollback.

## 5. Evidence index

- `benchmarks/rag-impact/DISCOVERY-H7-E1S-PREREGISTRATION.md`
- `rust/docs/rag-discovery-h7-e1s-reconcile-results.md`
- `benchmarks/rag-impact/results/h7-e1s-reconcile.json`
- `benchmarks/rag-impact/results/h7-e1s-resource-before.json`
- `benchmarks/rag-impact/results/h7-e1s-resource-after.json`
- `benchmarks/rag-impact/results/h7-e1s-attribution.json`
- `benchmarks/rag-impact/results/h7-e1s-anchor-scaling.json`
- `benchmarks/rag-impact/results/h7-e1s-parser-churn.json`
- `benchmarks/rag-impact/results/h7-e1s-independent-{easy,hard}.json`
- raw logs/host state: `~/.local/share/zvec-grep/rag-impact/20261109-h7-e1s/`

## 6. Verification (summary)

- Resource harness: coverage `8/14/15/20/23`, refs-off full-fact protocol
  `20/25`; compact refs-off `16/25` labelled as restricted arm.
- Independent easy 5/5, hard 5/5 with exact receipt hash parity.
- Determinism: two fresh runs, identical per-case hashes and coverage.
- Full `zg-engine` lib suite in qualified bwrap containment: 741 passed,
  0 failed, 29 ignored.
- `cargo fmt --check` clean, `cargo clippy -p zg-engine --lib --tests` exit 0.
