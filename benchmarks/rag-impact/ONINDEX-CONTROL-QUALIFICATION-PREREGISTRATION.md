# H7 control qualification — pre-registration (release caller)

Status: **pre-registered before implementation/measurement.** Date: 2026-11-09.
Base `59bc712` (`work/onindex`), worktree
`/home/masis/src/worktrees/zvec-grep/onindex-20261109`.
This task qualifies the **existing H7 control** (`full` path: project declaration
index + anchor facts + typed closure). It does **not** develop a new discovery
candidate. Arm A/B receipts stay comparative evidence and are not promoted.
Historical E1/E1R/E1S receipts stay immutable. Held-out untouched. Selection and
Expansion stay frozen. No defaults, index format, corpus, gold or receipt change.

## 1. Workloads (same semantics, corpus, budgets)

| Workload | Cases | Anchors | Notes |
|---|---|---|---|
| `dev` | D01–D12 (12) | frozen B0 call units, 17–33 anchor files/case | the historical H7 gate workload |
| `independent-easy` | E1–E5 (5) | frozen anchors, **64–87 anchor files/case** | high-anchor workload |
| `independent-hard` | H1–H5 (5) | frozen anchors, **64–87 anchor files/case** | high-anchor workload |

`independent-easy` and `independent-hard` are exactly the 64–87 anchor-file
workloads; the anchor count per case is measured and reported so the workload
class is explicit rather than asserted.

Semantics are unchanged: `build_declaration_index` over the three compile
projects, `parse_anchor_facts` with anchor-only full facts, the unchanged
`expand_with_declarations` closure, cap 500 distinct ranges/case, coverage by
path + inclusive line overlap at 10/50/100/250/500, gold from the frozen case
files.

## 2. Measurements

### 2.1 Correctness and parity
- coverage @10/50/100/250/500, micro and macro, per case and total;
- per-case candidate hash (path, range, score, reason), compared across two
  passes in the same process and against the frozen E1R / independent receipts;
- **refs-off parity**: the E1 full-fact protocol (`expand_with_closure(...,
  false)` on merged P1+P2+P3 facts) in a separate process, expected `20/25`
  with the frozen per-case vector;
- provenance: every candidate has a path and a non-inverted range; every closure
  reason has the five `status|expression|declaration|target|rule` fields;
  resolved/unresolved histograms reported;
- regressions: per-case coverage must not fall below the frozen receipt for the
  same workload;
- determinism: two passes in the same process and two separate processes must
  produce identical per-case hashes.

### 2.2 End-to-end cost
- cold phases: corpus/case load, P3/P2/P1 declaration build, first case;
- warm per-case phases: anchor source read, anchor parse (fact acquisition),
  closure, delivery (serializing the bounded candidate payload);
- p50/p95 of per-case wall time; CPU seconds (utime+stime) per phase and per
  case; I/O (`rchar`/`syscr`) per phase; payload bytes delivered.

### 2.3 Resource decomposition (gate definition unchanged)
- **worker footprint** = peak RSS/PSS of the control process (this is the
  historical H7 gate subject);
- **daemon/index footprint** = a separate process that opens the frozen index
  read-only, runs one warm query and idles (measured separately);
- **total resources** = worker + daemon (sum of the two processes), reported but
  never substituted for the gate.
- Gate: worker peak RSS/PSS **≤ 65,536 KiB**; warm p95 **≤ 5 s/case**.
- Gate definition is not changed. Nothing is subtracted from the worker.

### 2.4 Concurrency
- worker pool with K = 1, 2, 4 threads over the workload, shared read-only
  declaration index, per-thread anchor parse + closure + delivery;
- per-case p50/p95, throughput, process peak RSS/PSS, total CPU and CPU/wall;
- hashes must match the sequential baseline.

## 3. Pass/fail and freeze condition

Control GO requires all of:
1. coverage @500 ≥ 23/25 on dev, independent easy 5/5, independent hard ≥4/5;
2. no per-case coverage regression against the frozen receipts;
3. refs-off parity `20/25` with the frozen per-case vector;
4. identical per-case hashes across passes and processes;
5. provenance check PASS;
6. worker peak RSS/PSS ≤ 65,536 KiB on every workload;
7. warm p95 ≤ 5 s/case on every workload.

If GO, the Discovery **candidate** definition may be frozen at this control
path (freeze covers the discovery control only; it does not activate Selection
or Expansion, does not change defaults and does not promote arm A/B).
If NO-GO, report the failing phase/workload and stop; no tuning, no gate change.

## 4. Read-only exact-symbol audit (parallel, no API change)

Audit `IndexStore::find_resolution_candidates`, the `symbol_name` metadata
encoding, the public `symbol_type` filter path and the fragment metadata schema.
Deliver a root-cause note and a minimal fix proposal. Do not implement a new API
or change the index format in this task.
