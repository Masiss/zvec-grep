# Selection recovery report — 2026-11-26

Status: **DONE for the authorized bounded experiment; NO-GO for promotion**.
The hard two-language gate was not met; no held-out data, production binary,
daemon, index, model, default, or deployment was changed.

## Scope and environment

- Plan executed: `docs/rag/selection-recovery-plan-20261126.md`.
- Experimental branch/worktree:
  `work/recover-rag-evidence-20261126` /
  `/home/masis/src/zvec-grep-rust-rag-20261126`, start `7a2ebab`.
- Main worktree remains `main` @ `51c9eba`; installed CLI remains SHA-256
  `940180c7c74a199c5effbaec604bb69af1a9e9bdfb46881beb6231a838a9be2d`.
- Artifact root: `/home/masis/.cache/zvec-grep/selection-recovery-20261126`;
  no prior receipt was overwritten. Build target was external to the repo;
  `ORT_DYLIB_PATH=/home/masis/.local/lib/libonnxruntime.so.1` avoided a
  network ONNX Runtime download (upstream release URL returned 404).
- Frozen inputs are listed in `frozen/MANIFEST.txt` and verified by
  `frozen/SHA256SUMS`: Rust R01–R12 (`c6e1a7e7…`), C# D01–D12 (`27626109…`),
  both indexes, Potion model/tokenizer, and the K0/K1 binaries. The poisoned
  `clap/tests/ui/arg_required_else_help.rs` path is absent from the case set.

## G0/G1 baseline replication

Full 12+12 dev cases were rerun for K0/K1/K4 with the frozen binaries.
Exact coverage, precision, delivered bytes and oracle match the existing
`CORRECT-FLOW-REPORT.md` receipt exactly. The token counter was corrected to
`count_tokens_full`: the initial `count_tokens` run clipped every pack at 512
tokens and is preserved only as a measurement defect in the logs.

| Arm | Rust exact | Rust precision | Rust tokens | Rust p95 | C# exact | C# precision | C# tokens | C# p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| K0 baseline | 0.250 | 0.0493 | 4,213 | 1.64 s | 0.083 | 0.0068 | 4,624 | 2.07 s |
| K1 previous fragment | 0.153 | 0.0418 | 1,302 | 1.98 s | **0.167** | **0.0295** | **946** | 2.55 s |
| K4 corrected fragment | 0.125 | 0.0105 | 4,143 | 2.61 s | 0.083 | 0.0086 | 3,021 | 7.70 s |

## G1 coordinate adapter fix

Two concrete defects were fixed in the experimental code:

1. `write_verified_pack()` passed `ContentRange::last_line()` to the evidence
   request's exclusive `expected_end_line`. It now passes a new
   `ContentRange::end_line()` accessor. For R10 this changes the pack from 14
   items / 15 gaps / 2,379 bytes to 24 items / 11 gaps / 22,147 bytes; R03
   changes from 22/13/5,442 to 22/8/22,453; D06 from 6/19/5,364 to
   21/4/20,181. Remaining gaps are fragment-coordinate mismatches, not the
   adapter off-by-one.
2. `clip_fragment_overlap()` previously selected only the longest remaining
   segment and could emit an inconsistent endpoint line for a mid-line clip.
   It now emits **every** non-overlapping remainder with verified half-open
   coordinates. Regression tests cover both sides of an intervening fragment
   and the exclusive endpoint assertion.

Final G1-fixed binary SHA-256 `36017f2749b21ccfbc10a2f29cad377a4a3a554ccba623de8d2985b37bfcee1a`
(an intermediate build `67d2267d…` was superseded after the clip endpoint
refinement and is not the reported binary); focused context tests 12 passed,
evidence tests 11 passed, `cargo fmt --check` clean.

| Arm | Rust exact | Rust range | Rust precision | Rust tokens | Rust p95 | C# exact | C# precision | C# tokens | C# p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| G1K1 | 0.153 | 0.292 | 0.0562 | 2,852 | 1.40 s | 0.083 | 0.0144 | 2,286 | 4.31 s |
| G1K4 | **0.208** | **0.417** | 0.0468 | 6,313 | 1.48 s | 0.083 | 0.0260 | 3,814 | 4.37 s |

G1 improves Rust range/coverage and precision over K4, but C# exact coverage
drops below K1 and token cost grows. It does not pass the both-language gate.

## Candidate C2 (recorded failed hypothesis)

An env-gated `coverage_budget_select` was added as a research candidate:
fragment query-term coverage dominates, rank is a tie-break, byte cap 5,632,
item safeguard 64, no duplicate source lines, no default change. New env gates:
`ZVEC_RAG_QUERY_COVERAGE_SELECT`, `ZVEC_RAG_SELECT_BYTE_CAP`.

Candidate binary SHA-256 `aa03cb25…`. Full dev result: Rust exact `0.000`,
C# exact `0.083`, precision `0.000`/`0.010`, tokens `754`/`706`, p95
`1.11 s`/`3.83 s`. The simulated estimate (Rust ~0.111, C# ~0.194) did not
hold on the real context items, so the hypothesis is rejected. C1
(RRF-support env on the prior binary) was only a canary: R01/D01 had no gold
coverage in either arm, so it was not promoted to a full arm.

## Diagnostic evidence

Traces show the remaining loss is selection, not discovery or fusion: for K4,
gold-bearing fragments exist in the ranked pool for 11/12 Rust and 11/12 C#
cases (162 and 199 fragment rows respectively), while only 33 and 14 are
delivered. The best oracle selection inside the recorded pool is 0.764 Rust /
0.722 C# range recall, so the source material is present but not selected.
Simulation artifacts are under `results/g2/` and the preregistration under
`SELECTION-RECOVERY-PREREGISTRATION.md` in the artifact root.

## Regression and limits

- Normal-search parity: 6/6 sampled Rust/C# cases byte-identical between K0
  and the final experimental binary with all RAG env switches unset
  (`results/parity-normal-search.json`).
- Full `cargo test -p zg-engine --lib`: 769 passed, 10 failed, 28 ignored;
  all 10 failures are pre-existing environment failures caused by
  `~/.zvec-grep` being a symlink to the dogfood home, unrelated to the changed
  selection code. No test was weakened or ignored to obtain a pass.
- No task-correctness/blind-evaluator run: the preregistered quality gate
  failed before that stage. No independent/held-out data was opened.
- Single dev run per arm; C# keyword grading remains weak and high-abstention.
  Token comparisons use the same full counter for every arm in this report.

## Decision

Keep all changes research-only on the experimental branch. **NO-GO** for
promotion or deployment. The remaining bottleneck is fragment-level relevance
plus selection under a fixed source budget; coordinate adapter preservation is
fixed but insufficient. Promotion still requires a candidate that passes both
languages' coverage/precision/token/latency gates and task correctness, plus
independent-data confirmation.

## Reproduce

```bash
W=/home/masis/src/zvec-grep-rust-rag-20261126
A=/home/masis/.cache/zvec-grep/selection-recovery-20261126
cd "$W/rust"
ORT_DYLIB_PATH=/home/masis/.local/lib/libonnxruntime.so.1 \
CARGO_TARGET_DIR="$A/target" TMPDIR="$W/.tmp" cargo build -p zg --release
"$A/run-c2.sh"          # C2 full arms
"$A/run-g1.sh"          # G1-fixed K1/K4 arms
```
