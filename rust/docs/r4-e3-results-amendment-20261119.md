# E3 result amendment — method-scoped typed references (2026-11-19)

Status: **E3 attempt 01 complete; not accepted. No rerun.** Raw receipt and
executable are preserved at
`/home/masis/.cache/zvec-grep/r4-evidence-20261119/r4-e3-20261119/attempt-01/`.
The E3 preregistration is
`rust/docs/r4-e3-prereg-20261119.md`.

## Result

E3 produced the same plan score as E2 attempt 02: macro **0.4861**, pool
**12/25**, selected **8/25**. R4 no-plan remained **0.2778**; hybrid bounded
remained **0.2500**. Thus the unchanged acceptance thresholds (macro ≥0.60 and
selected ≥12/25) were not met, nor was the historical best **0.5278** exceeded.
No selected-range difference from E2 was observed. D07 did change its symbol
lookup from exact `sp_UpdateOrgTotalEmployee` to generic `CoreService`, with no
selected evidence gain.

The reference-scope trace did run. It derived many sibling/method references
(D01 48, D07 50, D12 48 trace entries); it did not recover the desired D01
service method name or D12 procedure name. The trace shows the run used five
planned discovery calls plus one further lookup (`tool_calls=6`), followed by
`round=2 refs=0 calls_left=0`. Consequently, most derived references could not
be looked up under the frozen call budget. D07's specific SQL lookup was
replaced, not improved. The experiment therefore rejects method-scoping as a
useful change **under this fixed call allocation**; it does not establish that
those references would help with more calls. No call budget was increased and
no further attempt is authorized.

E3 passed the existing safety/resource checks: ≤6 calls, ≤32 source reads,
≤24,576 selected pack bytes, and ≤65,536 source-content-plus-gap estimate. All
selected plan spans were source/hash verified. The top-level `citation=1.000`
field is the no-plan arm; it is not a correctness or task-answer score.

## Resource receipt

`/usr/bin/time -v`: user **136.53 s**, system **7.92 s**, wall **2:17.87**,
maximum RSS **754,760 KiB**, no process swap. The preflight and midrun snapshots
recorded machine-wide I/O pressure; treat latency as single-run, noisy evidence,
not a performance claim.

## Interpretation

The evidence points to multiple distinct losses: (1) fragment projection
reduces the gold-overlapping candidate pool (12/25 versus the corrected
baseline32's 19/25); (2) broad class contexts can be fanned out to unrelated
methods, now fixed at rank time with C#/SQL regressions; and (3) the constrained
plan consumes all six calls before a second typed-reference lookup can repair
missing cross-file context. Existing R2/R3 parsing and the SQL string-literal
candidate logic remain distinct from verified execution facts. Task correctness
is still **NOT_MEASURED** because no privacy-valid independent answerer/grader
was available.

Do not interpret the unchanged score as a safety failure or as proof that
method-level reference extraction is universally ineffective. It is a bounded
no-go for this implementation under the frozen D01–D12 protocol and call cap.
