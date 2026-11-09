# H7 control qualification (release caller) — result

Status: **NO-GO on the fixed 64 MiB worker gate.** Coverage, parity,
provenance, determinism and refs-off pass on every workload; the independent
easy (high-anchor) workload peaks at 66,776 KiB RSS, 1,240 KiB above the
65,536 KiB gate. PSS stays below the gate on all workloads.
Date: 2026-11-09. Base `59bc712` (`work/onindex`).
Pre-registration: `benchmarks/rag-impact/ONINDEX-CONTROL-QUALIFICATION-PREREGISTRATION.md`.
Historical E1/E1R/E1S receipts and verdicts are unchanged. Arm A/B stay
comparative evidence; nothing is promoted. Held-out untouched. Selection and
Expansion frozen. No defaults, index format, corpus or gold change.
Continuity and next steps: `rust/docs/rag-v1-handoff-20261109.md`.

## 1. Scope and workloads

This task qualifies the **existing H7 control** (project declaration index +
anchor-only facts + typed closure) with a release build. No candidate code was
developed or changed; only test-only measurement code was added.

| Workload | Cases | Anchor files/case |
|---|---|---|
| `dev` | D01–D12 | 17–33 |
| `independent-easy` (high-anchor) | E1–E5 | 67–87 |
| `independent-hard` (high-anchor) | H1–H5 | 64–82 |

Environment: release profile, external `CARGO_TARGET_DIR`, bwrap containment
(host and repo read-only; OUT and target writable), single mode per process,
host state captured before and after each run. The gate subject is the control
worker process; the index/daemon footprint is measured in a separate process and
reported separately. The gate definition is unchanged.

## 2. Coverage, parity, provenance, determinism

Single-pass control (the canonical gate run):

| Workload | @10 | @50 | @100 | @250 | @500 | micro | macro |
|---|---:|---:|---:|---:|---:|---:|---:|
| dev | 8 | 14 | 15 | 20 | **23** | 0.920 | 0.9444 |
| independent-easy | 2 | 3 | 4 | 5 | **5** | 1.000 | 1.0000 |
| independent-hard | 1 | 1 | 1 | 4 | **5** | 1.000 | 1.0000 |

- **dev vs frozen E1R receipt:** per-case coverage, candidate count and closure
  hash are identical for all 12 cases.
- **independent vs frozen E1R receipt:** per-case coverage and candidate counts
  are identical for all 10 cases. Candidate hashes differ **only because of the
  caller's file order**: the E1R caller passes every file, this control caller
  passes anchor files in manifest order, and equal-score tie-breaks keep the
  first inserted reason. Re-running the frozen E1R external caller reproduces
  the frozen hashes exactly (`e1r-external-{easy,hard}`), so this is a
  caller-order artefact, not a behaviour change.
- **Per-case candidate hash determinism:** identical between the two passes in
  one process (dev, easy, hard) and identical between two separate processes.
- **Provenance:** every candidate has a non-empty path and a non-inverted range;
  every closure reason carries `status|anchor|expr|declaration|target|rule`;
  resolved/unresolved histograms are emitted per case (`provenance_ok=true` on
  all workloads).
- **refs-off parity:** the E1 full-fact arm reproduces `7/12/14/20/20`
  (`20/25 @500`) with the frozen per-case vector (D02 2/6, D07 3/3, D12 1/2).
- No per-case regression against the frozen receipts.

## 3. End-to-end cost

Cold phase (both high-anchor and dev): declaration index build P3 92 ms / P2
758 ms / P1 606 ms, ~1.46 s and ~40 MiB peak RSS; not the gate peak.

Warm per-case cost (p50 / p95 ms):

| Workload | anchor read | anchor parse (fact acquisition) | closure | delivery | case |
|---|---:|---:|---:|---:|---:|
| dev | 0.3 / 0.4 | 158 / 190 | 3.4 / 5.9 | 0.1 / 0.1 | 162 / 196 |
| independent-easy | 0.9 / 1.8 | 640 / 1,284 | 44 / 92 | 0.2 / 0.4 | 682 / 1,378 |
| independent-hard | 0.7 / 0.8 | 519 / 555 | 41 / 47 | 0.2 / 0.2 | 561 / 603 |

- CPU: dev 3.34 s, easy 5.71 s, hard 4.18 s for the whole process (declaration
  build + one pass).
- Delivery payload: 1.19 MB (dev), 0.89 MB (easy), 0.90 MB (hard) for 500
  candidates/case.
- I/O: anchor source reads are page-cache hits (0.3–1.8 ms/case); the dominant
  cost is parsing, not reading.
- Warm p95 is below 5 s/case on every workload (max 1.38 s, easy E5).

## 4. Resource decomposition and gate

| Workload | worker peak RSS | worker PSS end | gate 65,536 KiB (RSS/PSS) |
|---|---:|---:|---|
| dev | 48,032 | 43,383 | PASS / PASS |
| independent-hard | 62,640 | 58,770 | PASS / PASS |
| independent-easy | **66,776** | 59,855 | **FAIL RSS** / PASS |

- Run-to-run: easy 66,684–67,104 KiB RSS across three single-pass runs; the
  breach is consistent.
- **Failing workload:** independent-easy (high-anchor, 67–87 anchor files/case).
- **Failing phase:** anchor fact acquisition on the largest anchor case (E5,
  87 anchor files): RSS jumps 61,676 → 68,080 KiB (+6.4 MiB) with a 1,284 ms
  anchor parse. Closure (92 ms) and delivery (0.4 ms) are negligible; the
  declaration build peaks at ~40 MiB and is not the cause.
- PSS stays under the gate on every workload (max 59,855 KiB); only the RSS
  peak breaches. The pre-registered gate requires both, so the verdict is FAIL.

**Daemon/index footprint** (separate process, frozen index opened read-only):
326,192 KiB RSS / 321,862 KiB PSS warm and idle; 386,344 KiB transient peak
during model/index warm-up.

**Total resources (worker + daemon, sum of processes, not a gate):**

| Workload | RSS total | PSS total |
|---|---:|---:|
| dev | 374,224 KiB (365.5 MiB) | 365,245 KiB (356.7 MiB) |
| independent-hard | 388,832 KiB (379.8 MiB) | 380,632 KiB (371.7 MiB) |
| independent-easy | 392,968 KiB (383.8 MiB) | 381,717 KiB (372.8 MiB) |

## 5. Concurrency

Worker pool over dev (shared read-only declaration index):

| K | wall | cases/s | p50 / p95 ms | worker peak RSS | worker PSS | worker CPU | CPU/wall | hash parity |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 2,382 ms | 5.04 | 199 / 262 | 54,044 | 50,005 | 2.38 s | 1.00 | match |
| 2 | 1,439 ms | 8.34 | 224 / 313 | 66,008 | 61,934 | 2.86 s | 1.99 | match |
| 4 | 862 ms | 13.92 | 281 / 326 | 88,204 | 84,126 | 3.30 s | 3.83 | match |

Independent-easy: K=2 → 2.06 cases/s, 99,004 KiB peak; K=4 → 2.76 cases/s,
141,484 KiB peak. A pool of two or more workers exceeds the 64 MiB worker
envelope even on dev, because each concurrent case holds its own anchor fact
graph. Concurrency is reported, not gated (per pre-registration).

## 6. Exact-symbol audit (read-only; no API change)

Probe (`storage::store::tables_tests::onindex_exact_symbol_audit`) opens an
existing index read-only and inspects exact lookup, filtered FTS and stored
fragment metadata. Consolidated output:
`benchmarks/rag-impact/results/onindex-exact-symbol-audit.json`.

Findings on both the fresh micro index and the frozen B0 index (818 files):

1. `find_resolution_candidates("CoreDBContext" | "EmployeeDetail" | "Foo" …)`
   returns **0 candidates**, and fragment documents have **no `symbol_name` and
   no `symbol_type` fields**.
2. `search_fts` with `symbol_type IN ('class' | 'function' | 'interface')`
   returns 0 hits, while the same query without the filter returns hits.
3. The filter code is correct: the existing unit test
   `symbol_filters_distinguish_classes_enums_and_unclassified_entities`
   exercises the same filter with populated data and passes. The problem is
   **absent data**, not a broken filter.

Root cause: C# is missing from the code extractor's language tables.
`extraction/code.rs::grammar()` and `extraction/code/adapter.rs::resolve_adapter()`
have no `FileFormat::CSharp` arm (the crate already depends on
`tree-sitter-c-sharp`, and `csharp_facts` handles C#). `extract_code` therefore
falls through to `fallback()` for `.cs`, indexing plain text chunks with no
`CodeMetadata`. Consequences: no symbol-name/symbol-type index fields, empty
exact-symbol lookup, empty symbol filters, and `--prefer-symbol` returning
results only through the base FTS route.

Minimal fix proposal (not implemented):

1. Add `FileFormat::CSharp => tree_sitter_c_sharp::LANGUAGE` to `grammar()` and a
   C# `LanguageAdapter` (class/struct/interface/enum/method/property declarations,
   `scope` = namespace + type breadcrumb, `signature`). This backfills
   `CodeMetadata` at index time; no index-format change is needed because the
   scalar fields and FTS already exist.
2. Expose a read-only exact-symbol enumeration
   (`symbol_name → [{path, range, symbol_type, scope}]`) with a fixed output
   bound. Today `ResolutionCandidate` carries entity id, file record and
   metadata but not the source range; the range lives in the entity payload.
3. Extend the existing unit tests to index a real `.cs` file through the
   pipeline (not only hand-written fixture docs) so this class of gap fails CI.

Until (1) lands, the index cannot answer declaration lookups for this corpus and
the arm-A architecture cannot be evaluated against it.

## 7. Decision

- **Control GO/NO-GO: NO-GO** on the fixed 64 MiB worker gate, failing the
  high-anchor independent-easy workload at 66,776 KiB RSS during anchor fact
  acquisition on the 87-anchor case. No tuning, gate change or candidate work
  was performed.
- **Freeze condition is NOT met.** Discovery candidate cannot be frozen.
- Coverage, parity, provenance, determinism and refs-off are satisfied, and PSS
  stays below the gate; the residual is a single RSS dimension on the
  highest-anchor workload.
- Next options (each needs its own pre-registration, none implemented here):
  bounded per-case anchor/fact acquisition with release of parse state between
  files, or an explicit anchor-file budget for high-anchor workloads. Both
  change the candidate, so they are not part of this qualification.
- Exact-symbol endpoint remains blocked on C# symbol extraction; the audit above
  supersedes the earlier "filter bug" hypothesis.

## 8. Evidence

- `benchmarks/rag-impact/results/onindex-control-dev.json` (1 pass)
- `benchmarks/rag-impact/results/onindex-control-dev-2p.json` (determinism)
- `benchmarks/rag-impact/results/onindex-control-independent-{easy,hard}.json`
- `benchmarks/rag-impact/results/onindex-control-independent-{easy,hard}-2p.json`
- `benchmarks/rag-impact/results/onindex-control-refs-off.json`
- `benchmarks/rag-impact/results/onindex-control-concurrency-{dev-k1,dev-k2,dev-k4,easy-k2,easy-k4}.json`
- `benchmarks/rag-impact/results/onindex-index-footprint.json`
- `benchmarks/rag-impact/results/onindex-exact-symbol-audit.json`
- Raw runs, host state and logs:
  `~/.local/share/zvec-grep/rag-impact/20261109-onindex/`
