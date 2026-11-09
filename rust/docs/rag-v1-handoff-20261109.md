# RAG v1.0 handoff — 2026-11-09 (One-Index audit + discovery experiments)

Branch: `work/onindex` (experiment only; not merged, not pushed).
Base: `9444eed` (`work/zg-recovered`). Worktree:
`/home/masis/src/worktrees/zvec-grep/onindex-20261109`.
Related branch: `work/h7-e1s` (H7 E1S reconcile + scope sharing; NO-GO, see that
branch's `rust/docs/rag-discovery-h7-e1s-reconcile-results.md`).

## 1. Status

| Layer | Status |
|---|---|
| One index (storage/query) | audited; no format change |
| Code Discovery arm B (bounded lexical) | **PASS on the dev protocol** (23/25, 1.07 s p95, 51.7 MiB) |
| Code Discovery arm A (index lookups) | **coverage PASS, latency/memory FAIL** |
| Document Discovery | interface + fixture prototype only |
| Selection | **FROZEN** |
| Expansion | **FROZEN** (H7 E2 not run) |
| R1 delivery | PARTIAL; independent qualification continues |
| RAG v1.0 | **NOT GO** |

No product code changed in this experiment; no default, index format, corpus,
gold, held-out or receipt was modified. Historical H7 E1/E1R/E1S receipts and
verdicts are unchanged.

## 2. Results

Release profile, bwrap containment, pre-built B0 index opened read-only for arm
A, 12 dev cases, seeds from `b0-official.json`, unchanged H7 closure.

| Arm | @500 | case p95 | peak RSS | PSS | independent |
|---|---:|---:|---:|---:|---|
| Control `full` (E1R path, lean caller) | 23/25 | 0.21 s | 48,004 KiB | 44,099 KiB | — |
| A `index` | 23/25 | 10.8 s | 444,792 KiB (incremental +117,976) | 427,696 KiB | 5/5, 5/5 |
| B `lexical` | 23/25 | 1.07 s | 52,956 KiB | 49,077 KiB | 5/5, 5/5 |

- refs-off baseline is 20/25 (full-fact protocol, frozen E1/E1S receipts).
- Determinism: two fresh runs per arm with identical per-case closure hashes.
- B on the independent anchor sets (64–87 anchor files/case) peaks at
  85,188 KiB (easy) / 77,748 KiB (hard) — above 64 MiB; a bounded
  anchor/frontier budget is required before any promotion.

## 3. What changed on the branch

Test-only prototype and measurement code plus docs/receipts:

- `extraction/onindex_tests.rs` — four-arm harness (`full` control, `index`,
  `lexical`), bounded lookup budgets, per-case lookup provenance, phase memory,
  I/O and latency.
- `extraction/discovery_proto.rs` + `_tests.rs` — shared candidate/provenance
  envelope and bounded document grouping (Markdown/JSON/CSV/log/text), 8 fixture
  tests.
- `examples/index_capability_probe.rs` — read-only index capability probe.
- `benchmarks/rag-impact/ONINDEX-DISCOVERY-PREREGISTRATION.md`,
  `rust/docs/rag-onindex-index-audit.md`,
  `rust/docs/rag-onindex-discovery-results.md`, receipts under
  `benchmarks/rag-impact/results/onindex-*`.

## 4. Exact next state

1. **Bounded frontier before promotion.** Define a fixed per-case anchor and
   verification-file budget that keeps the independent sets under the resource
   envelope, pre-register it, and re-measure; do not tune it against gold.
2. **Arm A needs a metadata-only exact-symbol endpoint.** `IndexStore`
   already enumerates exact hashed `symbol_name` definitions
   (`find_resolution_candidates`), but the public API exposes only ranked
   top-k. Expose `symbol_name → [{path, range, symbol_type, scope}]`, then
   re-measure A; also fix the public `symbol_type` filter, which currently
   returns no matches for every type.
3. **Tiered retrieval** is the recommended architecture: resident index for seed
   recall, exact-symbol enumeration for declarations, bounded lexical fallback
   (B) while (2) is unavailable, then Selection (frozen).
4. **Optional incremental metadata** (per-file declaration digests) only if the
   lexical scan’s ~12 MB/case I/O becomes the bottleneck; separate
   pre-registration, no new index format.
5. R1 public qualification continues independently.

## 5. Evidence index

- `benchmarks/rag-impact/ONINDEX-DISCOVERY-PREREGISTRATION.md`
- `rust/docs/rag-onindex-index-audit.md`
- `rust/docs/rag-onindex-discovery-results.md`
- `benchmarks/rag-impact/results/onindex-code-full-control.json`
- `benchmarks/rag-impact/results/onindex-code-a-index.json`
- `benchmarks/rag-impact/results/onindex-code-b-lexical.json`
- `benchmarks/rag-impact/results/onindex-code-{a,b}-independent-{easy,hard}.json`
- `benchmarks/rag-impact/results/onindex-document-prototype.json`
- raw runs/host state: `~/.local/share/zvec-grep/rag-impact/20261109-onindex/`

## 6. Verification

- Release runs, one process per mode; determinism twice per arm.
- Independent sets: easy 5/5, hard 5/5 for both arms.
- Document prototype: 8 fixture tests pass.
- Full `zg-engine` lib suite: **750 passed, 0 failed, 25 ignored** (one earlier
  run had a flaky crash-recovery test that passes in isolation twice; product
  code unchanged).
