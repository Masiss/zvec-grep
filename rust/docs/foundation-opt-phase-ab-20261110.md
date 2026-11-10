# Foundation optimization — Phase A/B results (2026-11-10)

Base `3bf6c18` (Index v3 candidate = `894b71e` + docs). Branch
`work/foundation-opt-20261110`, HEAD `a9f643a`. Isolated worktree
`/home/masis/src/worktrees/zvec-grep/foundation-opt-20261110`, external
`CARGO_TARGET_DIR`, temp and receipts outside the repository. Nothing merged,
pushed or released; no installed binary, daemon or index was touched.

## Commits

| Commit | Scope |
|---|---|
| `05cf15a` | **G1** move owned fragment text into the embedding input; build the metadata prefix in one buffer; stop cloning the whole component for each script block |
| `26df003` | **G2** typed scalar projection instead of a JSON tree; one file-membership projection per file; persisted metadata bytes locked by a regression test |
| `c7e21c1` | **G3** project `file_id` for ownership checks and only the vector column for reuse instead of full documents |
| `e97f4e1` | **G4** one hash lookup per manifest instead of per-fragment scans; move verified vectors instead of cloning |
| `b906cfc` | **M1–M3** opt-in per-file diagnostics (`ZVEC_GREP_INDEX_DIAGNOSTICS`) and reuse-miss taxonomy |
| `e8de8dc` | ignored microbenchmarks for the optimized paths |
| `b8914c8` | benchmark harness: `body` scenario really edits; M3 scenarios |
| `a9f643a` | reproducible drivers under `rust/scripts/` |

`G5` (streaming fingerprints) was implemented and then **dropped**:
microbenchmarks showed it slower than the buffered version
(1.38 → 2.10 µs JSON; 1.30 → 2.32 µs FP16). The commit is preserved only on the
local `backup/with-g5` branch.

## G1–G5 status

| Fix | Status | Evidence |
|---|---|---|
| G1 text copies | PASS | byte-equality oracle over borrowed/owned content, Unicode, CRLF, clipping, images, markdown; extraction suites green |
| G2 scalar projection | PASS | typed vs JSON projection equality; native projection fields unchanged; persisted metadata bytes unchanged (key-sorted round trip locked) |
| G3 narrow fetch | PASS | native integration tests: identity + vector returned, non-indexed columns absent, foreign ownership rejected; no write-back path |
| G4 manifest lookup + vector move | PASS | lookup equals linear scan over 400 unsorted entries; all reuse/mismatch tests unchanged |
| G5 streaming hashes | FAIL → rolled back | slower on both fingerprints; no benefit |

## Microbenchmarks (release, median)

| Path | Legacy | Current | Change |
|---|---:|---:|---:|
| G1 embedding input (2.5 KB, metadata) | 0.80 µs | 0.55 µs | −31% |
| G2 scalar projection | 0.26 µs | 0.21 µs | −27% |
| G3 ownership fetch (2 fragments) | 102.6 µs | 70.1 µs | −32% |
| G3 vector fetch (2 fragments) | 103.2 µs | 71.8 µs | −30% |
| G4 200-entry manifest lookup | 47.4 µs | 9.7 µs | −80% |

Run with `cargo test --release -p zg-engine --lib -- --ignored foundation_microbench --nocapture`.

## Body measurements (M1/M2)

Dense (300 C# files, 12 600 entities/fragments, mock 1024-d embeddings):

- source 524 917 B, FTS input 1 408 551 B, embedding input 1 720 251 B;
- per file: read ≈0.2 ms, extract 1.7–8.4 ms, prepare ≈0.05 ms;
- optimize stages: fragments 1.46 s, directories 0.13 s, entities 0.16 s,
  files 0.39 s (scalar stages run concurrently; not additive);
- pass wall 5.24 s.

Mixed (525 files): source 120 583 B, FTS 313 020 B, embedding 388 845 B,
pass wall 3.53 s.

## Paired resource results (5 repetitions, alternating order)

Host was busy (loadavg 3–8.5, other CPU-heavy user processes), so wall/user
deltas below ±20% are not resolvable; values are min/median of five runs.

| Corpus | Phase | Metric | Baseline | Candidate |
|---|---|---|---|---|
| dense | full | user CPU | 12.1/12.7 s | 12.1/13.6 s |
| dense | full | wall | 5.6/5.8 s | 5.7/6.2 s |
| dense | full | FS output blocks | 418k/425k | 418k/423k |
| dense | full | allocated bytes | 54 145 024 | 54 149 120 (1 block) |
| dense | incremental | user CPU | 1.2/1.4 s | 1.2/1.4 s |
| dense | incremental | FS output blocks | 204k/207k | 209k/211k |
| mixed | full | user CPU | 3.0/3.5 s | 3.2/3.5 s |
| mixed | incremental | FS output blocks | 124k/132k | 125k/130k |

RSS is unchanged (≈446 MB dense, ≈344 MB mixed, ≈410/386 MB incremental).
Allocated and apparent bytes are identical within block rounding; build-to-build
variation of the same binary is also one block (4 096 B).

**No end-to-end resource improvement is detectable at this scale, and no
regression is detectable either.** That is expected: the fixed work is
microseconds per fragment against a workload dominated by 12 600 embeddings,
54 MB of native storage and native maintenance.

## M3 incremental matrix (candidate binary)

| Scenario | Files | Entities | Reuse reason | Embed inputs | Wall | FS blocks |
|---|---|---:|---|---:|---:|---:|
| none | 0 | 0 | – | 0 | 0.79 s | 7 360 |
| touch (newer mtime, same bytes) | 0 | 0 | – | 0 | 0.69 s | 10 000 |
| body (trailing comment) | 1 modified | 43 | identity_unavailable 43 | 43 | 1.96 s | 207 104 |
| prepend | 1 modified | 43 | identity_unavailable 43 | 43 | 1.77 s | 201 376 |
| rename | 1 added, 1 deleted | 42 | – | 42 | 1.92 s | 207 976 |
| delete | 1 deleted | 0 | – | 0 | 1.04 s | 39 752 |
| samesize (same size+mtime, different bytes) | 0 | 0 | – | 0 | 0.75 s | 7 336 |
| append (new class) | 1 modified | 43 | identity_unavailable 43 | 43 | 2.95 s | 207 328 |

Observations:

- A one-file edit writes ~100 MB to the filesystem while the source file is
  1.7 KB: write amplification is native maintenance, not engine copies.
- In-place updates grow the index (54.15 → 56.09 MB) as already documented.
- Vector reuse is never exercised here because the benchmark model declares an
  unstable identity (Qwen backend), which is why every fragment reports
  `identity_unavailable`. G4's benefit is therefore only shown by the
  microbenchmark and by unit tests.
- `samesize` (same length, restored mtime, different bytes) is skipped as
  unchanged: a pre-existing freshness limitation, not introduced here.

## Verification

- `cargo test -p zg-engine --lib`: **752 passed, 26 ignored** (baseline 745/24;
  the difference is the new tests plus the two ignored microbenchmarks).
- `cargo test -p zg-engine --tests`: lib plus integration binaries
  `8+1+4+17+8+1+2+2+3+2` passed, 0 failed.
- `zg-cli` 180+5, `zg-daemon-protocol` 7, `zg-transport-mcp` 60,
  `zg-zvec-fp16` 2, `zg-host-native` 25+10+9 — all passed.
- `cargo fmt --all -- --check`: clean.
- `cargo clippy -p zg-engine --all-targets`: exactly the same 22 pre-existing
  warnings as the baseline (none new, none removed).
- Storage parity: forensics `collection_bytes`, `duplication` and
  `fragments_collection` are identical between paired builds; FTS query results
  are identical. Vector/hybrid result sets differ between *any* two independent
  builds of this synthetic corpus (reproduced with the baseline binary alone),
  so that is pre-existing ANN/tie behaviour, not a change here.

## Not measured / unknown

- No production embedding model; all resource runs use the harness mock server
  (separate process, so its CPU is excluded from the engine's `/usr/bin/time`).
- The reuse path could not be exercised end-to-end (unstable model identity).
- No allocator-level or native/Zvec CPU profile; `index_commit` and
  `index_optimize` remain wall measurements that include native work.
- Host noise prevents attributing deltas below ~20%.

## Remaining bottlenecks and next experiments

1. **E2 — reuse decoupled from source position** (unchanged from the audit):
   `identity_unavailable` is the only miss reason observed in practice; that
   blocks any reuse measurement with real models.
2. **E1 — precise dirty tracking / maintenance policy**: a one-file edit still
   writes ~100 MB and grows the index by ~2 MB; scalar `DIRTY_ALL` maintenance
   is the dominant cost.
3. **E3 — declaration/lexical/vector decoupling**: still the only lever that
   changes dense disk (~54 MB) and CPU (~12 600 vectors), and it remains an
   experiment requiring a schema and quality gate.

## Follow-up (2026-11-11)

The 0.79 s no-op figure recorded above was a cache/host state, not a steady-state
value: the follow-up audit measured 0.48 s with ~0.33 s spent opening the native
vector/FTS collection and the rest in flush/publication. That cost was removed on
`work/noop-fastpath-20261110` (0.50 → 0.06 s, no index writes), which also
explains most of the per-edit floor seen here — see
`noop-fastpath-handoff-20261111.md`, `foundation-noop-and-e3-prespike-20261110.md`
and `noop-fastpath-final-qualification-20261110.md`. The rest of this report
(G1–G5 dispositions, microbenchmarks, paired numbers) is unchanged.
