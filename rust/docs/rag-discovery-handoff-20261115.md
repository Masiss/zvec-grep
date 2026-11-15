# RAG Discovery requalification — handoff 2026-11-15

Status: **DISCOVERY NOT QUALIFIED; NO PRODUCTION CHANGE.**
Two bounded experiments were run (both NO-GO), one structural hypothesis was
deferred. The candidate pool improved substantially, but top-10 evidence did
not qualify. Selection/Expansion/release require owner authorization.

## 1. Branch and worktree

| Item | Value |
|---|---|
| Worktree | `/home/masis/src/worktrees/zvec-grep/rag-requal-20261115` |
| Branch | `work/rag-requal-20261115` |
| Base HEAD | `27b81c0e0472644820955fd69d1a080fb76b16ca` |
| Remote | `origin` (push this branch only; do not merge `main`) |
| Main worktree | unchanged, on `work/zg-recovered` |
| External root | `/home/masis/.cache/zvec-grep/rag-requal-20261115/` |

## 2. Artifacts

| Artifact | SHA-256 / identity |
|---|---|
| requal receipt | `17ae8f32925f7e75a93b69f001e853a128ca812900e84ddad50119e2be8ce8e0` |
| raw probe | `84b7593a76e53e0379e3b9aad2d1923c59458bf89e36d809a0b9da2569188e27` |
| refinement receipt | `d1fdda1584340af9428a1c22f7837f6fcc1cabdbe4d8c3c881cb37c366993f25` |
| symbol channel | `4a6abf6f58e9980721ef888c3f7b465d1d354566016e1c02a2abaade42024f2d` |
| experiment binary | `f6e0a22a439b57e23c4a4f30a3db63b6a521286712532913cfd106394285a8d1` (`0.3.0-rc.2`) |
| experiment probe | `5b6545e58db1ee572d4c890b24f06299017e0b0cbc34b191a0070d9cf9d3fbdb` |
| symbol probe | `9143f78224133ddaa09e0c7891f91a44b7df3b20714c3c3938e94338cf8536d2` |
| corpus manifest | `c706c8dd9b560572a2c1739c2c2bda676982f4c3b5da4cd4b32bcfeb66359cdb` |
| dev cases | `27626109fe4d2bf02beec474809424bbd58dd5a2b709263c6dde0926a4a9c311` |
| index manifest | `d7052d3f670c704bb1ce6c632b9689f60dcad75d207fb76dc8f3a4764473fc4a` |
| corpus tree | `2b761e66fa43f47d9b5ee9b0cc3a799f05cf8b92c2f8794304aa297efab874a3` |

Additional inputs: `benchmarks/rag-impact/cases/heldout.json`
`2d81048e…e38f6d` (sealed, not run); `dev-independent.json`
`d79fc7f7…a4b8a`; `dev-independent-hard.json` `a6d21cdf…f8937`.

## 3. What changed

Experiment branch only:

- `rust/crates/zg-engine/examples/rag_requal_probe.rs` — read-only route probe;
- `rust/crates/zg-engine/examples/rag_symbols_probe.rs` — exact-symbol probe;
- `benchmarks/rag-impact/tools/requalify_20261115.py` — corrected Recall@10,
  strict evidence, route/cost accounting;
- `benchmarks/rag-impact/tools/refine_evidence_20261115.py` — granularity
  audit + bounded refinement experiment;
- `benchmarks/rag-impact/tools/verify_requal_20261115.py` — independent
  verifier with negative controls;
- four reports under `rust/docs/` (baseline, requalification, experiments,
  this handoff).

No engine library, CLI, schema, daemon, index or installed binary was changed.

## 4. Verification

| Check | Result |
|---|---|
| Route contract (requested = effective) | 84/84 rows PASS |
| Independent recomputation | PASS |
| Negative controls | 4/4 rejected |
| R1 `api::evidence` | 10/10 PASS |
| R1 `r1_public_api` | 2/2 PASS |
| Held-out | sealed, not run |
| Repeat-run variance | not measured (single run) |
| Production indexes/binaries/daemon | untouched |

## 5. Dependency map

### Selection

- Candidate pool: **18/25 precise spans**, 10-unit selectable ceiling 18/25.
- Deployable top-10: 0.333 (fixed FTS), 0.417 (hybrid), 0.597 (oracle
  best-of-six); strict 0.125.
- A Selection layer can reuse: indexed declarations/scope/symbol metadata,
  exact-symbol probe, declaration splitter, route contract, frozen dev set.
- Missing: a deployable selector (the historical Selection NO-GO pool ceiling
  14/25 and best 6/25 is obsolete under v3, but Selection is still frozen).
- Gate before implementation: owner authorization; a new pre-registration;
  no-gold policy; dev-only development; held-out evaluated once; report recall,
  strict evidence, pack size, determinism and source reads.

### Expansion

- Missing 7 spans are cross-file/SQL. The production index has no reference
  graph or SQL procedure binding; E1 is experimental and excluded; H6/H7
  resource gates failed.
- Do not open Expansion until Selection is qualified and the candidate pool
  reaches the 20/25 gate. No implementation was started.

### R1

- Correctness contract still PASS on merged HEAD.
- Release remains blocked by the historical R1 gaps: real-source pilot
  provenance, full dev + held-out pack assembly, latency/PSS, daemon/server
  parity, and independent verification at release scope. No release or binary
  replacement is proposed here.

## 6. Roadmap

1. **Measurement correction (this branch).** Use Recall@10, strict evidence,
   union/selectable ceiling and cost as separate fields; never call a union
   result Recall@10. Keep the verifier with negative controls.
2. **Selection qualification (owner decision).** Authorize a bounded,
   dev-only Selection experiment on the frozen v3 pool. Suggested gates:
   top-10 ≥0.60, strict ≥0.30, selectable ≥16/25, pack ≤ B0’s 3,716
   tokens/case, 100% valid ranges, deterministic, no gold. If it passes,
   re-run held-out exactly once.
3. **Discovery gap (only if needed).** If Selection can exploit 18/25 but the
   candidate gate still blocks, design a bounded reference/SQL binding
   experiment with an explicit 64 MiB-style resource gate. Do not revive E1R
   without fixing its resource failure.
4. **Release (separate authorization).** Rebuild/qualify the installed pair,
   back up, install, verify index compatibility (v2→v3 requires explicit
   `--rebuild`), post-install smoke and owner acceptance. No release is
   authorized by this handoff.

## 7. Binary replacement status

**Not performed.** No owner authorization for replacing the installed
`0.0.1` binary/library was found. The installed pair and daemon remain on v2;
the experiment used an isolated v3 index and direct mode. Rollback pair:

```text
~/.local/share/zvec-grep/rollback-20261101T130300Z/zg
~/.local/share/zvec-grep/rollback-20261101T130300Z/libzvec_c_api.so
```

Do not replace, promote, restart the daemon or rebuild production indexes
without a new explicit authorization.

## 8. Owner decisions

| ID | Decision |
|---|---|
| D-M1 | Accept the corrected measurement contract and the Discovery NO-GO verdict. |
| D-M2 | Authorize (or refuse) a bounded Selection qualification on the v3 pool. |
| D-M3 | Choose the release path: keep installed `0.0.1`; authorize a separate release/install unit; or defer. |

## 9. Reproduction

```bash
R=/home/masis/.cache/zvec-grep/rag-requal-20261115
cd /home/masis/src/worktrees/zvec-grep/rag-requal-20261115

python3 benchmarks/rag-impact/tools/requalify_20261115.py \
  --root "$R/corpus" --binary "$R/target/release/zg" \
  --probe "$R/target/release/examples/rag_requal_probe" \
  --out "$R/receipts/requal-foundation.json" \
  --native-lib-dir "$R/target/release" --home "$R/home" --tmp "$R/tmp"

python3 benchmarks/rag-impact/tools/verify_requal_20261115.py \
  --receipt "$R/receipts/requal-foundation.json" \
  --probe "$R/receipts/raw-probe.json" --root "$R/corpus" \
  --dev benchmarks/rag-impact/cases/dev.json \
  --corpus-manifest benchmarks/rag-impact/corpus-manifest.json \
  --refinement "$R/receipts/refine-experiment.json" --negative
```

## 10. Cleanup state

External build target (~1.5 GB) and logs are retained until the receipts are
reviewed; the corpus/index (~89 MB) and receipts (~3 MB) are retained. The
experiment worktree is committed and pushed as `work/rag-requal-20261115`; the
main worktree is unchanged. No production index, binary or daemon was touched.

Final commit: recorded in the branch tip and remote ref after the documentation
commit.
