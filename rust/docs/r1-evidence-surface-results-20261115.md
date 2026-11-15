# R1 evidence surface — prototype results

Status: **PROTOTYPE GO (branch only). Public exposure NOT authorized.**
Branch `work/r1-evidence-surface-20261115` from `d9a9b1a`. Not merged, not
released, no installed binary/daemon/index change.
Pre-registration: `r1-evidence-surface-prereg-20261115.md` (frozen before
measurement).

## 1. What was built

| Layer | Addition |
|---|---|
| engine | `api::evidence::surface` — `assemble_spans`, `revalidate_spans`, `SpanRequest`, `SpanAddress`, `SpanItem`, `SpanPack`, `SurfaceError`; `SPAN_SCHEMA_VERSION = 1`; hard ceiling 24 KiB / 32 items |
| engine internals | `pack_status`, `resolve_path`, `CachedSource`, `range_for_offsets`, `line_starts` shared with the existing R1 contract (visibility only, no behavior change) |
| MCP | `zvec_grep_evidence`, registered in the `full` toolset only; `agent` (default) still lists exactly one tool |
| tests | 17 external contract/negative-control tests, 4 line-addressing unit tests, 1 MCP end-to-end test (observed → verified → stale → agent-toolset absence) |
| receipt | `benchmarks/rag-impact/results/r1-evidence-surface-paired-20261115.json`, produced by `examples/r1_evidence_surface_paired.rs` |

Span contract: `bytes{start,end}` or `lines{start_line,end_line}` (one-based
inclusive, trailing terminator excluded). `expected_sha256` present ⇒
`verified`; absent ⇒ `observed`. `observed` is never reported as verified and no
pack is called verified unless every item is `verified`.

Read-only by construction: no write call exists in `surface.rs` (no
`fs::write`, `remove_*`, `create_dir`, `File::create`, `OpenOptions`).

## 2. Gates (frozen)

| Gate | Result | Evidence |
|---|---|---|
| C1 contract + negative controls | PASS | 17/17 `tests/r1_evidence_surface.rs`; 4/4 line-offset unit tests |
| C2 verified items revalidate clean | PASS | `verified_span_serves_exact_bytes_and_revalidates_clean`, per-case revalidation 0 gaps |
| C3 every cited-file mutation is detected | PASS | arms C: 25/25 on all six byte-changing conditions |
| C4 zero false-stale | PASS | unchanged condition: arm C 0/25 detected, 0 gaps |
| C5 provenance invariant | PASS | `provenance_invariant_content_belongs_to_the_reported_hash`; single read per path per pack |
| C6 default search unchanged | PASS | `agent_server_exposes_only_search` (1 tool), `full_server_exposes_all_seven_tools`, golden search-format test unchanged |
| S1 no writes | PASS | no write API in the surface; fixture mutations only in the scratch copy |
| S2 no held-out | PASS | only `cases/dev.json` D01–D12 (25 spans, 9 files) |
| S3 no index/model/ranking/default change | PASS | installed `zg 0.0.1` `6db0f67e…`, daemon PID 161009, manifest `0539af2c…`, toolset `agent` — all unchanged |

Test totals on this branch (serialized, `TMPDIR` override): zg-engine lib 769
passed, integration suites 8/1/4/17/8/1/2/11/17 passed; zg-transport-mcp 61
passed; zg-daemon 137 passed; `cargo clippy -p zg-engine -p zg-transport-mcp
--all-targets` clean for new code.

## 3. Paired measurement (single run per condition, D01–D12, 25 spans, 9 files)

Arms: A trust snippet · B re-read cited lines and compare text · C span pack with
the frozen expected hash · D span pack without a prior hash, then immediate
revalidation (control).

Detected spans / 25:

| Condition | A | B | C | D |
|---|---:|---:|---:|---:|
| unchanged | 0 | 0 | **0** | 0 |
| edit outside cited range | 0 | 1 | **25** | 0 |
| edit inside cited range | 0 | 17 | **25** | 0 |
| same-size substitution inside range | 0 | 17 | **25** | 0 |
| line inserted above range | 0 | 25 | **25** | 0 |
| renamed | 0 | 25 | **25** | 25 |
| deleted | 0 | 25 | **25** | 25 |
| CRLF↔LF conversion | 0 | 25 | **25** | 0 |

Gap codes for arm C: `stalehash` for all six byte-changing conditions, `missing`
for rename/delete; no other codes.

Bytes read per arm per condition (distinct cited files): A 0, B/C/D 224 257 B
(unchanged), 229 212 B (CRLF conversion). Arm C reads each distinct cited file
exactly once per pack; its cost is O(total bytes of distinct cited files).

Per-case packs on unchanged sources (12 packs, one per dev case): 25/25 items,
0 gaps, max pack content 19 030 B, max serialized pack 21 814 B, slowest pack
8.2 ms. Two runs on the same code:

| Run | Host load | assemble total | revalidate total | PSS |
|---|---|---:|---:|---|
| quiet | 0.75 / 0.87 | 19.4 ms | ~20 ms | 3 015 → 3 151 KiB |
| committed receipt | 4.09 (python at ~200 % CPU) | 49.4 ms | 37.9 ms | 3 045 → 3 118 KiB |

Both runs are disclosed; the committed receipt is the noisy-host run, so treat
latency as a range, not a p95. No p95, concurrency or peak-RSS gate was
attempted.

## 4. Frozen predictions, graded

| Prediction | Outcome |
|---|---|
| P1: A detects 0; B detects ranges-only; C detects all byte-changing conditions plus rename/delete | **PARTIALLY FAILED.** A and C hold. B is stronger than predicted: it also detects inserted lines (25/25), rename/delete (25/25) and CRLF conversion (25/25), and detects inside-range edits only for spans that cover the mutated line (17/25). B's real blind spot is a change **elsewhere in the file** (1/25, and only because EOF append touched a span ending at EOF). |
| P2: C has 0 false-stale on unchanged | **HELD** (0/25). |
| P3: C reads each distinct file once; A reads none | **HELD** (224 257 B once per pack; A 0). |
| P4: C pack stays within 24 KiB / 32 items | **HELD per case** (max 19 030 B). **FAILED dataset-wide**: a single pack over all 25 spans hits the 24 KiB ceiling (10 items, 10 gaps). Packs are per answer/case, not per dataset. |

Failed predictions are reported, not re-frozen. Conditions and gates were not
edited after measurement.

## 5. Findings that change the next decision

1. **The 24 KiB budget is a per-answer budget.** A dataset-wide pack silently
   becomes `partial`. Any product surface must define the pack unit (one
   question/answer) or the caller sees budget gaps.
2. **`observed` mode cannot detect a prior-version change.** Arm D detects 0/25
   byte changes; it only reports missing files. Staleness detection therefore
   requires the caller to carry a previously observed hash (`verified` mode).
   Reporting an `observed` pack as "current and verified" would be wrong.
3. **Snippet-only re-reading is a weak but non-zero baseline.** B catches
   in-range edits, line shifts, renames/deletes and line-ending changes; it
   misses edits elsewhere in the same file, and it cannot distinguish "my cited
   range is intact" from "the file changed". That is the precise gap the surface
   closes.
4. **Cost is dominated by hashing whole files.** Verified mode reads O(file
   bytes) per distinct cited file; there is no file-size cap in this prototype.
   A 100 MB file cited once costs 100 MB of I/O.
5. **No cross-file snapshot.** Consistency is per file (one read per path per
   pack) and per item (content sliced from the reported hash). A pack is not a
   transactionally consistent snapshot of the workspace.

## 6. Environment defects found while verifying

Both are pre-existing; neither is caused by this branch.

1. `.cargo/config.toml` forces `TMPDIR=/home/masis/src/zvec-grep/.tmp`. Tests
   that use `std::env::temp_dir()` then operate on a repo directory with stale
   entries, which makes several `zg-transport-mcp` tests fail
   deterministically. Working command:
   `cargo test -p <crate> --config 'env.TMPDIR.value="/tmp"' --config 'env.TMPDIR.force=true'`.
2. The `zg-engine` config suite fails one test when run in parallel (a different
   config test each time, also at baseline `d9a9b1a`); the full suite passes with
   `--test-threads=1`. Recorded as `UNRESOLVED` test-isolation issue.

## 7. Not done / residual gaps

- MCP `evidence` is exposed only in the `full` toolset; no default-toolset, CLI,
  installer/guidance, daemon-protocol or release work.
- No cache/version key; callers must revalidate per answer (explicit no-cache).
- No security review of the exposed surface (root admission, auth/audit,
  secret/PII policy, response-size policy beyond the pack ceiling). Path escapes
  are rejected, but "which roots may be served to which agent" is undefined.
- No p95/PSS/concurrency qualification; no held-out validation.
- Recall is untouched: this surface wraps caller-addressed spans and does not
  search. The known retrieval ceiling (0.4722) still bounds any RAG claim.

## 8. Disposition

**Prototype GO**: gates C1–C6 and S1–S3 pass on a branch, with the four findings
above recorded and two predictions graded honestly.

**Not authorized and not requested by this result:**

- exposing `zvec_grep_evidence` in the default `agent` toolset, a CLI command,
  or an installed/daemon release;
- merging this branch;
- claiming RAG quality, recall or read-reduction improvement;
- promoting the prototype to a public product surface.

Owner decisions required before any widening: (1) pack unit and default
toolset, (2) cache/version-key contract, (3) security/privacy review of a
file-serving read tool, (4) performance/concurrency qualification, (5) held-out
validation, (6) whether the `observed` mode ships at all.
