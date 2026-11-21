# Handoff — next session (2026-11-21, end of day)

Single entry point. Read this, then `rust/docs/r4-integrity-gate-20261121.md`
(the report) for the findings and evidence behind the current state.

Status: **READY_FOR_AUTHORIZED_INFERENCE — SEMANTIC_EVALUATION_PENDING**.
Answer quality **NOT_MEASURED**, not `QUALIFIED`, not `RAG_MVP_GO`, no
architecture winner. Nothing in the harness can award a formal `PASS` without a
registered evaluator (verified: 0 formal PASS in 36 synthetic runs).

## 1. Repository state right now

| Item | Value |
|---|---|
| Active worktree | `/home/masis/src/zvec-grep-r4-evidence-20261119` |
| Branch | `work/r4-evidence-representation-20261119` |
| HEAD | `60160c0` — **pushed**, tracks `origin/work/r4-evidence-representation-20261119` |
| Other worktree | `/home/masis/src/zvec-grep` on `main` (`2d7d2ac`, ff to `origin/main`) |
| Worktrees | exactly two (main + this one); `git worktree prune` clean |
| Local-only branches | none — every branch has an `origin/` counterpart |
| Untracked in main worktree | `research/` (M4 native research, results kept, build dir removed) |
| Base of this round | `77e681e` → `60160c0`, 10 commits |

Rule that still holds: at most two local worktrees, and any branch must be
pushed before its worktree is removed.

## 2. What this round finished

1. **Semantic grading integrity** — registry authority (`cases/evaluators.json`,
   shipped empty), five-way input binding (`case`, `answer_sha256`,
   `pack_sha256`, `question_sha256`, `rubric_sha256`), mandatory judgement of
   every claim, consistency rules (PASS cannot declare an unsupported claim,
   FAIL must name one at least), `INVALID` → `UNGRADABLE`, non-frozen question →
   `NOT_MEASURED`.
2. **Source-read accounting at the I/O boundary** — engine exposes observed
   span assembly (`assemble_spans_observed`, `revalidate_observed`), the adapter
   records before opening, merges boundary reads on every exit path, and refuses
   to commit more opens than it reserved (`pending_admitted_ops`).
3. **Arm B plan chain** — the workflow pack carries both the *file* digest and
   the digest of the plan **actually executed** (canonical UTF-8, sorted keys);
   the driver recomputes it and fails closed. An arm-b run without a plan
   identity is reported as an invalid primary control.
4. **Independent review** (DeepSeek Flash 4.1 High, thinking=high, sanitized
   bundle only): first pass FIX-FIRST with one blocking finding, second pass no
   blocker. All findings closed except the acknowledged out-of-model one documented in
   `r4-integrity-gate-20261121.md` §6c.

Verified: adapter 51/51 + 2 internal accounting tests; engine 804/804 serially
(2 `config::tests` collide only in parallel); harness 79 checks; grader 56
fixtures; synthetic E2E 36/36 with 12/12 plan chains closed.

## 3. Environment after the housekeeping pass (important)

Deleted (build artifacts only): `research/m4-native/.work/build` (13G, its 9
`.cc` sources preserved in `.work/build-sources-preserved/`), the recovered
target's `debug/examples`, the active target's `debug/{incremental,examples}`,
`~/.cache/zvec-grep/e3-01/target`, and the in-repo `rust/target` of the main
worktree. Disk: 11G → 35G free.

**Never delete** (not reproducible from source):

- `~/.cache/zvec-grep/rust-target-recovered-20261105/debug/build/zvec-rust-sys-*/out/zvec-prebuilt/`
  — the `LD_LIBRARY_PATH` the engine links against.
- `~/.cache/zvec-grep/rust-target-recovered-20261105/ort-prebuilt/` — ORT cache.
- `~/.cache/zvec-grep/r4-sql-symbols-20261118/corpus` — the frozen corpus.
- Every `r4-*` receipt/bundle directory.

Remaining big caches, deliberately kept: recovered `debug/deps` (9.0G) and the
active `target/debug/deps` (8.6G). Removing them frees ~17G and costs ~10 min of
recompilation; that trade was not taken unilaterally.

## 4. Reproduce / run

```bash
cd /home/masis/src/zvec-grep-r4-evidence-20261119/rust
export LD_LIBRARY_PATH=/home/masis/.cache/zvec-grep/rust-target-recovered-20261105/debug/build/zvec-rust-sys-9d9cb66e2b7fe09f/out/zvec-prebuilt
export ZG_ORT_CACHE_DIR=/home/masis/.cache/zvec-grep/rust-target-recovered-20261105/ort-prebuilt
export CARGO_TARGET_DIR=/home/masis/.cache/zvec-grep/r4-evidence-20261119/target

cargo test -p zg-r4-adapter                      # expect 2 + 51 passing
cargo build -p zg-r4-adapter --bins              # writes target/debug/r4-adapter
```

```bash
# synthetic end-to-end (no network, no private inference)
python3 /home/masis/.cache/zvec-grep/r4-longrun-20261121/run_three_arm_v5.py
# -> ~/.cache/zvec-grep/r4-longrun-20261121/three-arm-v6/three-arm-summary.json
#    expect: 36 runs, 0 violations, 12/12 plan chains, 0 formal PASS
```

```bash
# self-tests
python3 benchmarks/rag-impact/tools/r4_grade_selftest.py \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus /tmp/g
python3 benchmarks/rag-impact/tools/r4_bench_driver_selftest.py \
  /home/masis/.cache/zvec-grep/r4-evidence-20261119/target/debug/r4-adapter \
  /home/masis/.cache/zvec-grep/r4-sql-symbols-20261118/corpus /tmp/h
```

## 5. The two owner decisions that unblock everything

**D1 — register a semantic evaluator.** The production registry is empty on
purpose. To fill it: name the evaluator identity (independent model + exact
version and served identity, or a named human), its prompt/instructions, and the
artifacts it may read (frozen answer + frozen pack + evaluator-only materials;
never gold spans, never the answerer session). Then add it to
`benchmarks/rag-impact/cases/evaluators.json` with `trusted: true` and a
fingerprint. Check `r4_grade.py --help` / `evaluate_semantic` for the exact
accepted shape.

**D2 — authorize private-source inference.** Provider, exact model and served
identity, endpoint, and the answerer data flow (what leaves the machine). Until
both decisions exist, real runs are `OPERATION_BLOCKED`; the harness itself is
ready.

After D1+D2, the run is: D01–D12 × 3 arms through
`r4_bench_driver.py --backend openai-responses --authorized-inference --grade`,
then each frozen pair graded in its own process with
`--semantic <evaluation> --registry cases/evaluators.json`. Report structural
and semantic layers separately; a formal PASS requires both.

## 6. Do not retry (already falsified or out of scope)

- Ranking/embedding/index tuning (fold, `prefer_symbol`, full-declaration
  delivery, outline expansion were all measured this round).
- Held-out H01–H08, GQ5, R5, R6.
- Pushing corrections into the adapter without a reproduction: the accounting and
  provenance invariants are pinned by negative controls; changing them breaks the
  contract tests.
- Treating a synthetic evaluator as real: fixtures live in a test-local registry.

## 7. Where the evidence lives

| What | Where |
|---|---|
| Findings + fixes + both reviews | `rust/docs/r4-integrity-gate-20261121.md` |
| This state, frozen identities, evaluator contract | `rust/docs/r4-integrity-gate-handoff-20261121.md` |
| Synthetic E2E receipts | `~/.cache/zvec-grep/r4-longrun-20261121/three-arm-v6/` |
| Sanitized review bundle | `~/.cache/zvec-grep/r4-integrity-gate-20261121/bundle-final/` + zip (manifest is the identity) |
| Adapter/grader/harness source | `rust/crates/zg-r4-adapter/`, `benchmarks/rag-impact/tools/` |
