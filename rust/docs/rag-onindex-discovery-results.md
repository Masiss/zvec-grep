# One Index → Specialized Discovery: results (Code A/B, Documents)

Status: **Code B (bounded lexical declaration selection) meets the discovery
gates on the dev protocol; Code A (index-assisted declaration lookup) meets
coverage but fails latency and memory. Nothing promoted.**
Date: 2026-11-09. Base `9444eed`; branch `work/onindex`.
Pre-registration: `benchmarks/rag-impact/ONINDEX-DISCOVERY-PREREGISTRATION.md`.
Audit: `rust/docs/rag-onindex-index-audit.md`.
Historical H7 E1/E1R/E1S verdicts unchanged. E2 not run. Selection/Expansion
frozen. No production default, index format, corpus, gold, held-out or receipt
change. No product code changed by this experiment (harness and prototypes are
test-only).

## 1. Protocol and environment

- Corpus: frozen HRM2.0 compile subset (817 `.cs`, 5.4 MB) from the B0 workspace
  copy; SQL variants from the original checkout.
- Index (arm A only): the pre-built B0 index (818/818 files, 33 MB) copied to the
  experiment OUT and opened read-only (`refresh=off`, `auto_update=false`).
- Cases: 12 dev cases, seeds from `b0-official.json`, gold from `cases/dev.json`.
  Independent sets: E1–E5 (easy) and H1–H5 (hard) with their frozen anchors.
- Closure and scoring: unchanged H7 code (`expand_with_declarations`), cap 500,
  coverage by path + inclusive line overlap at 10/50/100/250/500.
- Build: **release**, external `CARGO_TARGET_DIR`, bwrap containment (host and
  repo read-only; OUT and target writable). Host: load average 2–11 during the
  session, ~12 GB swap in use; every mode was measured as one process per run,
  with host state captured before and after.
- refs-off baseline: **20/25** with the full-fact protocol (frozen E1/E1S
  receipts). The on-arm ceiling in every mode is **23/25**.

## 2. Code discovery results (release, dev protocol)

| Arm | @10 | @50 | @100 | @250 | @500 | case p50 / p95 | peak RSS | end PSS | search calls | bytes read |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Control `full` (E1R path) | 8 | 14 | 15 | 20 | **23** | 135 / 181 ms | **48,184 KiB** | 43,514 KiB | 0 | — |
| **A `index`** (index lookups) | 8 | 14 | 15 | 20 | **23** | 9,334 / 12,423 ms | **443,512 KiB** | 426,618 KiB | 309 | 58.2 MB |
| **B `lexical`** (bounded scan) | 8 | 14 | 15 | 20 | **23** | 541 / 1,014 ms | **52,524 KiB** | 48,266 KiB | 0 | 146.5 MB |

- Micro/macro @500: refs-off **20/25 = 0.800 / 0.9028**; on-arm in every mode
  **23/25 = 0.920 / 0.9444** (macro = mean per-case recall).
- Per-case coverage matches the frozen E1R receipt exactly in all three arms
  (including D02 `[0,3,3,5,5]` and D07 `[0,0,0,1,3]`); closure hashes are
  identical between two fresh runs of each arm.
- Independent sets: easy **5/5**, hard **5/5** for both A and B, with the same
  per-case coverage vectors as the frozen receipt.
- A’s incremental cost over the warm index: **+117,716 KiB RSS / +105,137 KiB
  PSS** (warm baseline 325,796 / 321,481 KiB). Total process ~433 MiB is
  index+model residency, not discovery work. A second fresh run reproduced the
  same per-case hashes and 440,040 KiB HWM.
- B’s second fresh run: identical hashes, 52,424 KiB HWM, p50 811 ms / p95
  1,568 ms. The 1.0–1.6 s p95 range is host-load variance on this machine, not a
  code difference; both runs remain under the 5 s/case gate.
- B’s incremental cost over the loaded harness: ~+26 MiB (no resident index).

### 2.1 Why A is slow

Declarations for the closure are enumerated through the public context API:
`query = name`, `prefer_symbol`, `limit = 64`, batched 8 names per call. 309
batched FTS calls for 12 cases cost ~8–10 s/case (≈600 ms per call). The index
has the data for a direct exact-symbol lookup, but the public surface only
offers ranked FTS/vector top-k. The internal `find_resolution_candidates` (exact
hashed `symbol_name` enumeration, no cap) is not exposed, and the public
`symbol_type` filter returns no matches for any type (live probe; see audit).

### 2.2 Why B works

One lexical pass over the 817 compile files per round builds a declaration-name
map (`class|struct|interface|record|enum <name>`); up to 256 sites per name and
1,024 verification files per case are parsed once with the existing
`csharp_facts` parser and reduced to `types/fields/db_sets`. Bounded rounds
(≤4) resolve names discovered by re-parsing the anchors with the accumulated
scope; budget ≤200 names/case. This reproduces the full declaration fan-out for
partial classes (e.g. 65 `CoreService` files in D02), which is what the
`receiver-type` closure needs.

Cost: 24,175 file reads and 146.5 MB read over 12 cases (~12.2 MB and ~2,000
reads/case), 2.4 s of lookup work total per case (p50 0.56 s/case overall).

## 3. Gate evaluation

| Gate | Control | A `index` | B `lexical` |
|---|---|---|---|
| Coverage @500 ≥23/25 | PASS 23 | PASS 23 | PASS 23 |
| Independent easy 5/5 | PASS | PASS 5/5 | PASS 5/5 |
| Independent hard ≥4/5 | PASS | PASS 5/5 | PASS 5/5 |
| Determinism (2 fresh runs) | PASS | PASS (identical hashes) | PASS (identical hashes) |
| Provenance | PASS | PASS (per-name status/reason; closure reasons unchanged) | PASS |
| No hardcode | PASS | PASS | PASS |
| Peak RSS/PSS, total process ≤65,536 KiB | **PASS 48,184** | **FAIL 443,512** | **PASS 52,524** |
| Peak RSS/PSS, incremental ≤65,536 KiB | n/a | **FAIL 117,716** | PASS (27 MiB) |
| Warm p95 ≤5 s/case | PASS 0.18 s | **FAIL 12.4 s** | **PASS 1.0–1.6 s** |
| I/O reported | PASS | 58.2 MB | 146.5 MB |
| Search calls reported | PASS 0 | PASS 309 | PASS (~2,000 file reads/case) |

Known resource limit for B: on the **independent** anchor sets (64–87 anchor
files/case vs 17–33 on dev) peak RSS is **86,296 KiB (easy)** and **77,040 KiB
(hard)**, above 64 MiB. Memory scales with the anchor and verification file
counts, so a bounded anchor/frontier budget would be required before using B
outside the dev protocol.

### 3.1 Control vs historical E1R gate

The control arm uses the same product path as E1R (`build_declaration_index` +
`parse_anchor_facts` + closure), but its caller holds only anchor sources and
does not keep two full corpus copies. Peak RSS is **48,184 KiB** (release) vs the
frozen E1R receipt’s 74,028 KiB (debug, duplicate corpus copies). This is a
caller/protocol difference, not a product change; the historical receipt and its
NO-GO verdict are unchanged. It shows the 64 MiB gate is reachable for the
parse-only worker when the caller does not duplicate sources.

## 4. Document discovery (design + fixture prototype)

Shared envelope (test-only `discovery_proto`): `DiscoveryCandidate { path,
start_line, end_line, score, kind: Code|Document, provenance }`. Code and
document strategies emit the same shape, so Selection can stay source-agnostic.

Document strategy (bounded, no typed closure, no new index):

| Kind | Grouping key | Provenance |
|---|---|---|
| Markdown | heading path derived from `#` levels; stored `heading/scope/level` wins | `heading_path`, `heading_level` |
| JSON | key path of the matched value | `key_path` |
| CSV | header columns + key column value | `columns`, `key`, `row` |
| Log | timestamp + level + event tag | `timestamp`, `level`, `event` |
| Text | one group | `matched_by=text` |

Fixture receipt: `benchmarks/rag-impact/results/onindex-document-prototype.json`
(5 fixtures, max 8 groups, deterministic). Eight fixture tests pass. No quality
or coverage claim is made; this is an interface prototype for a later,
separately pre-registered document experiment.

## 5. Architecture decision

1. **One index + specialized discovery is viable for code today with arm B.**
   It reaches the full 23/25 coverage with ~1.0–1.6 s/case p95 and 51.3 MiB peak
   on the dev protocol, with no index residency in the discovery worker and no
   product change. Selection can consume its candidates unchanged.
2. **Arm A is the right long-term lookup, but not through ranked FTS.** The
   index already stores hashed `symbol_name`; exposing a metadata-only exact
   enumeration (`symbol_name → [{path, range, symbol_type, scope}]`) would turn
   ~600 ms/name batches into scalar lookups and return every declaration site.
   Until then A is NO-GO on latency and memory.
3. **Tiered retrieval, not a new index format.** Recommended assembly:
   resident index for seed recall (FTS/vector/hybrid) → direct exact-symbol
   enumeration for declarations → bounded lexical fallback (B) when the symbol
   endpoint is unavailable → Selection (frozen) → Expansion (frozen) → R1.
4. **Optional incremental metadata** is justified only if the lexical scan’s
   I/O (12 MB/case) becomes the bottleneck; a per-file declaration digest cache
   would fit the existing manifest, but it is a separate pre-registered change.
5. **Bounded frontier is required before any promotion**: B’s independent-set
   peak (83 MiB) shows the resource envelope is anchor-count sensitive. A
   fixed per-case anchor/file budget must be defined and pre-registered; it
   must not be tuned against gold.

Nothing is promoted; no production default changed; H7 E2 not run; Selection and
Expansion stay frozen.

## 6. Verification

- Code A/B: release runs from the final tree, one process per mode; determinism
  twice per arm with identical per-case closure hashes (recorded in each
  receipt); independent sets both arms 5/5 + 5/5.
- Document prototype: 8 fixture tests pass; receipt emitted.
- Full `zg-engine` lib suite under bwrap containment: **750 passed, 0 failed,
  25 ignored** (second run). The first run had one flaky crash-recovery test
  (`graph_tests::process_exit_during_canonical_replacement_recovers_from_durable_status`)
  that passes twice in isolation; no product code was changed.
- `cargo fmt --check` clean for the new modules; clippy passes with the
  repository’s existing warnings.

## 7. Artefacts

- `benchmarks/rag-impact/ONINDEX-DISCOVERY-PREREGISTRATION.md`
- `rust/docs/rag-onindex-index-audit.md`
- `benchmarks/rag-impact/results/onindex-code-full-control.json`
- `benchmarks/rag-impact/results/onindex-code-a-index.json`
- `benchmarks/rag-impact/results/onindex-code-b-lexical.json`
- `benchmarks/rag-impact/results/onindex-code-{a,b}-independent-{easy,hard}.json`
- `benchmarks/rag-impact/results/onindex-document-prototype.json`
- Raw runs, host state and logs:
  `~/.local/share/zvec-grep/rag-impact/20261109-onindex/`
