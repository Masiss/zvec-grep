# R1 evidence surface — pre-registration (prototype)

Status: **FROZEN BEFORE IMPLEMENTATION MEASUREMENT.** Branch
`work/r1-evidence-surface-20261115` from `d9a9b1a`. Not merged, not released.

## 1. Scope

Add an **opt-in, additive** surface that turns caller-supplied source spans into
hash-checked evidence. It does not search, rank, index, embed, write source or
change any default.

- engine: `api::evidence::surface` (`assemble_spans`, `revalidate_spans`);
- MCP: `zvec_grep_evidence`, registered only in the `full` toolset (the
  `agent` default keeps exactly one tool: `zvec_grep_search`);
- no CLI subcommand, no daemon protocol change, no index/model change.

Out of scope and not authorized: R4 discovery/expansion work, GQ5, held-out
inspection, release/install, merge.

## 2. Span contract (frozen)

A span is addressed either by byte offsets (`mode: "bytes"`) or by one-based
inclusive line numbers (`mode: "lines"`). Line addressing resolves against the
source version that is served, and excludes the trailing line terminator.

| Field | Meaning |
|---|---|
| `project` | non-empty label; pack items keep it separate |
| `relative_path` | relative to root; absolute/traversal/symlink/out-of-root rejected |
| `expected_sha256` | optional. Present ⇒ must equal the served file hash (`verified`). Absent ⇒ hash recorded at read time (`observed`) |
| `address` | `bytes{start,end}` half-open UTF-8 byte offsets, or `lines{start_line,end_line}` one-based inclusive |

Verification labels: `verified` = caller asserted a hash and it matched;
`observed` = no prior assertion. **`observed` is never reported as verified, and
no pack is called verified unless every item is `verified`.**

Fail-closed rules:

1. A stale/missing/symlinked/out-of-root/absolute/traversal/renamed source is a
   typed gap; content from a different version is never substituted.
2. Content served for an item is always sliced from the bytes whose SHA-256 is
   reported for that item (single read per path per pack).
3. Budget overflow and item overflow are explicit gaps, not silent truncation.
4. Invalid UTF-8, invalid ranges and split codepoints are gaps.
5. Pack budget ceiling: `max_bytes ≤ 24 KiB` and `max_items ≤ 32` (the frozen
   report contract). Larger requests are rejected, not clamped.

Schema: `SPAN_SCHEMA_VERSION = 1`, reusing `EvidenceStatus`, `EvidenceGap` and
`EvidenceGapCode` from the existing R1 contract.

## 3. Acceptance gates (frozen; any failure ⇒ NO-GO)

Correctness / provenance:

| # | Gate |
|---|---|
| C1 | Contract + negative-control suite passes: stale hash, missing, renamed, symlink, traversal, absolute path, out-of-root, forged range, split codepoint, non-UTF-8, line 0, line beyond EOF, start>end, budget bytes, budget items, dedupe |
| C2 | `verified` items revalidate with 0 gaps against unchanged source |
| C3 | Any mutation of a cited file makes `revalidate_spans` report a gap for that item (100% detection, no silent pass) |
| C4 | 0 false-stale: unchanged sources revalidate with 0 gaps |
| C5 | Provenance invariant: for every item, the content equals the byte slice of the file whose SHA-256 is reported |
| C6 | Default search surface unchanged: `agent` toolset lists exactly 1 tool and the search output is byte-identical to baseline |

Safety / scope:

| # | Gate |
|---|---|
| S1 | No write path: no source, index, manifest or production artifact written |
| S2 | No held-out inspection; only `cases/dev.json` D01–D12 |
| S3 | No index/model/ranking/default change; no merge/release/install |

Measurement (reported once, single run, host state recorded):

| # | Metric |
|---|---|
| M1 | Paired stale-detection per arm on 8 mutation conditions × cited spans |
| M2 | Bytes read per arm (actual tool I/O), pack size, item count |
| M3 | Wall time percentiles and peak RSS for pack assembly |
| M4 | `revalidate_spans` cost after pack |

No numeric improvement target is invented here. M1–M4 are recorded facts; the
product claim allowed by this prototype is **citation integrity** (C1–C5), not
retrieval recall. Recall remains bounded by the known 0.4722 retrieval ceiling
and is not re-measured.

## 4. Paired arms (frozen before measurement)

Fixture: a scratch copy of the sources cited by the 12 real-source dev cases,
under `/home/masis/.cache/zvec-grep/r1-evidence-surface-20261115/`. The frozen
corpus is never modified.

| Arm | Behaviour (what today's search output allows) |
|---|---|
| A | trust the snippet; no revalidation |
| B | re-read the cited line range and compare text (best an agent can do without a file hash) |
| C | build an `observed` span pack, then revalidate it before the answer |

Mutation conditions applied to the scratch copy:

1. unchanged
2. edit outside the cited range
3. edit inside the cited range
4. same-size substitution inside the cited range
5. line inserted above the cited range (line numbers shift)
6. file renamed
7. file deleted
8. CRLF ↔ LF conversion

Frozen predictions:

- P1: A detects 0/8 conditions. B detects 3 and 4, misses 2, 5, 8 partly or
  wholly, and cannot distinguish 6/7 from "no evidence". C detects all
  conditions that change the file bytes (2,3,4,5,8) plus 6 and 7.
- P2: C produces 0 false-stale on condition 1.
- P3: C reads each distinct cited file once per pack; A reads none; B reads each
  cited file once per recheck.
- P4: C pack stays within 24 KiB / 32 items for the dev citation set.

A prediction that fails is reported as a failed prediction; the conditions and
gates are not edited afterwards.

## 5. Disposition

- **GO (prototype only)**: C1–C6 and S1–S3 pass, P1–P4 hold. Next step requires
  owner approval for public exposure (MCP tool in a default toolset, CLI,
  release), and a separate decision on cache/version-key and security review.
- **NO-GO**: any C/S gate fails, or P1/P2 fails. Do not widen the surface.

Neither outcome authorizes merge, release, installation, production index
changes or a public RAG/evidence claim.
