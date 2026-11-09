# RAG v1.0 handoff — 2026-11-09 (One-Index audit, Code A/B, H7 control qualification)

Branch: `work/onindex` (experiment only; not merged, not pushed).
Base: `9444eed` (`work/zg-recovered`). Worktree:
`/home/masis/src/worktrees/zvec-grep/onindex-20261109`.
HEAD at handoff: `7a5311d`.
Related branch: `work/h7-e1s` (H7 E1S reconcile + scope sharing; NO-GO, see
`rust/docs/rag-discovery-h7-e1s-reconcile-results.md` on that branch).

Isolation used throughout: release build, external `CARGO_TARGET_DIR`
(`~/.cache/zvec-grep/rust-target-onindex-20261109`), bwrap containment
(host/repo read-only, only OUT and the target writable), pre-built B0 index
opened read-only. Main tree clean; protected `rust/target` untouched; no
push/release; no held-out access.

## 1. Status

| Layer | Status |
|---|---|
| One index (storage/query) | audited; no format change |
| Code Discovery arm B (bounded lexical) | PASS on the dev protocol (23/25, ~1.0 s p95, 51.3 MiB) |
| Code Discovery arm A (index lookups) | coverage PASS; latency/memory FAIL (**and blocked by missing C# symbols**) |
| H7 control (release caller) | **NO-GO on the fixed 64 MiB worker gate** (high-anchor workload) |
| Frozen Discovery candidate | **NOT frozen** (control gate not met) |
| Document Discovery | interface + fixture prototype only |
| Selection | **FROZEN** |
| Expansion | **FROZEN** (H7 E2 not run) |
| R1 delivery | PARTIAL; independent qualification continues |
| RAG v1.0 | **NOT GO** |

No product code changed on this branch: only test-only measurement harnesses,
read-only probes, fixtures, docs and receipts. No default, index format, corpus,
gold, held-out or historical receipt was modified. H7 E1/E1R/E1S verdicts and
receipts stay exactly as recorded.

## 2. H7 control qualification (the latest result)

Pre-registration:
`benchmarks/rag-impact/ONINDEX-CONTROL-QUALIFICATION-PREREGISTRATION.md`.
Full note: `rust/docs/rag-onindex-control-qualification.md`.

Same semantics as H7 E1R (`build_declaration_index` + `parse_anchor_facts` +
unchanged closure), release profile, one process per workload, single pass for
the gate and two passes for determinism.

| Workload | Anchors/case | @10/50/100/250/500 | p50/p95 | worker peak RSS | worker PSS | Gate |
|---|---|---:|---:|---:|---:|---|
| dev | 17–33 | 8/14/15/20/**23** | 162/195 ms | 48,032 KiB | 43,383 KiB | PASS |
| independent-hard | 64–82 | 1/1/1/4/**5** | 561/603 ms | 62,640 KiB | 58,770 KiB | PASS |
| independent-easy | 67–87 | 2/3/4/5/**5** | 682/1,378 ms | **66,776 KiB** | 59,855 KiB | **FAIL RSS** |

- Correctness/parity: dev coverage, candidate counts and closure hashes are
  identical to the frozen E1R receipt; independent coverage/counts are identical
  (hashes differ only through caller file-order tie-breaks; the frozen E1R
  caller reproduces the frozen hashes exactly). Provenance, determinism and the
  full-fact refs-off parity (`7/12/14/20/20` = 20/25) pass.
- Failing workload/phase: independent-easy, **anchor fact acquisition** on the
  87-anchor case (RSS 61,676 → 68,080 KiB, parse 1,284 ms). Closure 44–92 ms and
  delivery 0.2–0.4 ms are negligible; declaration build peaks at ~40 MiB.
- Only RSS breaches; PSS is under the gate on every workload. The gate requires
  both, so the verdict is NO-GO. No tuning, no gate change, no freeze.
- Resource decomposition: worker above; **daemon/index** process 326,192 KiB RSS
  / 321,862 KiB PSS warm+idle (386,344 KiB transient warm-up); totals
  (worker+daemon) 374,224 / 388,832 / 392,968 KiB RSS for dev / hard / easy.
- Concurrency (dev, shared read-only declaration index): K=1 5.04 cases/s,
  54,044 KiB; K=2 8.34 cases/s, 66,008 KiB; K=4 13.92 cases/s, 88,204 KiB.
  Hash parity holds. A pool of two or more workers exceeds the 64 MiB worker
  envelope, so admission/bounded-pool policy is required before scaling.

## 3. Comparative arms (not promoted)

Release, dev protocol, from the previous experiment on this branch
(`rust/docs/rag-onindex-discovery-results.md`):

| Arm | @500 | p95 | peak RSS | Note |
|---|---:|---:|---:|---|
| A `index` (public FTS lookups) | 23/25 | 10.8–12.4 s | 443,512 KiB | incremental +117.7 MiB; fails latency/memory |
| B `lexical` (bounded scan) | 23/25 | 1.0–1.6 s | 52,524 KiB | independent easy/hard 5/5; dev-protocol only |

Arm B is the only arm that fits the dev-protocol envelope, but it is **not**
promoted: the control itself is NO-GO on a high-anchor workload and B’s
independent-set peak is higher (86,296 KiB easy / 77,040 KiB hard).

## 4. Exact-symbol audit — root cause and proposal

Read-only probe (`storage::store::tables_tests::onindex_exact_symbol_audit`)
against a fresh micro index and the frozen B0 index:

1. `find_resolution_candidates(...)` returns **0** for every C# name.
2. Fragment documents carry **no `symbol_name`/`symbol_type`**; the
   `symbol_type` filter therefore matches nothing.
3. The filter code is correct — the existing unit test with populated data
   passes.

Root cause: `extraction/code.rs::grammar()` and `extraction/code/adapter.rs::resolve_adapter()`
have **no C# arm** (although the crate depends on `tree-sitter-c-sharp`), so
`.cs` falls through to `fallback()` and is indexed as plain text without
`CodeMetadata`.

Minimal fix proposal (not implemented): add a C# grammar + `LanguageAdapter`
(declarations, scope, signature) so `CodeMetadata` is written at index time
(no index-format change needed); then expose a bounded exact-symbol enumeration
`symbol_name → [{path, range, symbol_type, scope}]` (today `ResolutionCandidate`
carries entity id, file record and metadata but not the source range); add a
pipeline test that indexes a real `.cs` file so this gap cannot regress.

## 5. Document Discovery

Interface prototype only (`extraction/discovery_proto.rs` + 8 fixture tests,
receipt `onindex-document-prototype.json`): shared
`DiscoveryCandidate { path, range, score, kind, provenance }` envelope with
Markdown heading path, JSON key path, CSV header/key, log event grouping, and a
text fallback. No quality/coverage claim, no typed closure for documents, no
gate. It stays a separate track.

## 6. Branch contents (what is on `work/onindex`)

- `rust/crates/zg-engine/src/extraction/onindex_tests.rs` — four-arm harness
  (`full`/`index`/`lexical`), phase/CPU/IO/memory sampling, provenance,
  refs-off parity, control qualification, worker-pool concurrency, index
  footprint.
- `rust/crates/zg-engine/src/extraction/discovery_proto*.rs` — document
  grouping prototype + fixtures.
- `rust/crates/zg-engine/src/storage/store/tables_tests.rs` — read-only
  exact-symbol audit probe.
- `rust/crates/zg-engine/examples/index_capability_probe.rs` — index capability
  probe.
- Docs: `rag-onindex-index-audit.md`, `rag-onindex-discovery-results.md`,
  `rag-onindex-control-qualification.md`, this handoff.
- Pre-registrations: `ONINDEX-DISCOVERY-PREREGISTRATION.md`,
  `ONINDEX-CONTROL-QUALIFICATION-PREREGISTRATION.md`.
- Receipts: `benchmarks/rag-impact/results/onindex-{code,control,index,document,exact-symbol}*`.

## 7. Exact next state

1. **Do not freeze or promote Discovery.** The control gate is NO-GO; arm A/B
   stay comparative evidence.
2. If the control is to be qualified further, the only authorized direction is
   a **new pre-registered candidate change** for bounded per-case anchor/fact
   acquisition (release parse state between files, bounded working set) or an
   explicit anchor-file budget for high-anchor workloads. Do not tune against
   gold, do not change the 64 MiB gate, and re-run the same three workloads.
3. **C# symbol extraction is the prerequisite for the index-assisted path.**
   Without it, arm A cannot use exact symbols and the `symbol_type` filter has no
   data. Land the adapter/grammar first, then the bounded exact-symbol endpoint,
   then re-measure A. Do not add an index format.
4. **Tiered retrieval** remains the recommended architecture: resident index for
   seed recall, exact-symbol enumeration for declarations once available,
   bounded lexical fallback (B) meanwhile, then Selection (frozen).
5. **Worker pool sizing** must be part of the serving contract: K≥2 exceeds the
   64 MiB worker envelope even on dev. Admission control or a bounded pool needs
   its own pre-registration.
6. R1 public qualification continues independently (CLI/MCP/service, cache
   invalidation, concurrency, admission, PII/auth/audit, release/rollback).

## 8. Evidence index

- `benchmarks/rag-impact/ONINDEX-DISCOVERY-PREREGISTRATION.md`
- `benchmarks/rag-impact/ONINDEX-CONTROL-QUALIFICATION-PREREGISTRATION.md`
- `rust/docs/rag-onindex-index-audit.md`
- `rust/docs/rag-onindex-discovery-results.md`
- `rust/docs/rag-onindex-control-qualification.md`
- `benchmarks/rag-impact/results/onindex-control-{dev,independent-easy,independent-hard}*.json`
- `benchmarks/rag-impact/results/onindex-control-refs-off.json`
- `benchmarks/rag-impact/results/onindex-control-concurrency-*.json`
- `benchmarks/rag-impact/results/onindex-index-footprint.json`
- `benchmarks/rag-impact/results/onindex-exact-symbol-audit.json`
- `benchmarks/rag-impact/results/onindex-code-*.json`,
  `benchmarks/rag-impact/results/onindex-document-prototype.json`
- Raw runs, host state and logs:
  `~/.local/share/zvec-grep/rag-impact/20261109-onindex/`

## 9. Verification of this branch

- Control: release, one process per workload; two passes + two processes with
  identical per-case hashes; refs-off parity 20/25.
- Arms A/B: release; determinism twice per arm; independent sets 5/5 + 5/5.
- Document prototype: 8 fixture tests.
- Full `zg-engine` lib suite under bwrap: **750 passed, 0 failed, 30 ignored**.
  One earlier run had a flaky crash-recovery test that passes twice in
  isolation; product code is unchanged.
- `cargo fmt --check` clean.

## 10. Caveats

- Historical H7 E1/E1R/E1S receipts and verdicts are immutable; nothing here
  revises them. The lean control caller (anchor-only sources) reaches 48,032 KiB
  versus the frozen E1R 74,028 KiB (debug, duplicate corpus copies); that is a
  caller/protocol difference, not a product change.
- The 64 MiB gate is a worker metric. The resident daemon/index (~318 MiB PSS)
  is reported separately and is not subtracted from or added to the gate.
- `arm A`/`arm B` and the control are experiment labels; none is a RAG v1.0 GO.
