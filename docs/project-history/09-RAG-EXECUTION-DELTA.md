# 09 — RAG Execution Delta

This is a **gap analysis**, not a new roadmap. It compares the canonical plan in
`docs/rag/implementation-plan.md` (2026-10-28, PROPOSED) with verified
implementation and research through 2026-11-15.

## 1. Delta matrix

| Canonical phase | Planned outcome | Verified reality | Delta / decision |
|---|---|---|---|
| R0 | Frozen scope, source authority, questions, budgets | Benchmark/corpus/cases/B0 frozen; HRM2 anchors documented | **Mostly complete**; keep frozen targets; confirm held-out access policy |
| R1 | Evidence/source pack | Library API + dev pilot; correctness tests; no CLI/MCP; release gates missing | **Internal delta**: productize or explicitly keep library-only |
| R2 | Bounded C# facts | Side parser + tests; not default indexed metadata | **Source-fact delta**: not wired into public indexing |
| R3 | EF6/SQL facts + cross-file | Source-visible SQL/proc/view binding; no live DB/deployment authority; full expansion absent | **Partial**: authority and expansion gaps |
| R4 | Multi-turn agent loop + public integration | Bounded expander only; R4-A/B/D1/D2 FAIL; no loop/public protocol | **Major missing phase**; R1 remains fallback |
| R5 | Paired quality/footprint on pilot | No paired dev+held-out, no source-byte/time/PSS gate | **Not started** |
| R6 | Opt-in release/rollback | No RAG feature flag, public contract or release | **Not started** |

Post-R4 S1/S1b/RefInt/Evidence/GQ/GQR/GS are **outside** this matrix. They
explore how to close the R4 discovery/selection gap; they do not advance the
canonical phase gate by themselves.

## 2. The central delta

The plan assumed an evidence layer over existing retrieval. The verified
bottleneck is earlier:

```text
B0 best-call (oracle) 0.4722
fixed deployable FTS  0.3750
S1/S1b selected       6/25 → 10/25
Reference union       22/25, but selection 10/25
R4 candidate          0.2917
```

The candidate pool can be expanded, but the system has not reliably selected
task-relevant evidence. Therefore:

- more R4 packing/ranking tweaks are not the shortest path;
- the documented R4 audit already says a new retrieval/discovery experiment
  requires owner approval;
- post-R4 GS creates a typed resolver/regression foundation, but GQ5 is not run;
- an external-agent MVP cannot be claimed from R1 library tests.

## 3. Shortest justified path to an external-agent MVP

This is a decision sequence, not a new schedule.

### Decision A — R1 product shape

Choose one:

1. **Library-only internal**: document limits, no CLI/MCP, no R6.
2. **Public evidence API**: add versioned CLI/MCP/daemon surface and close R1
   release gates.
3. **No RAG integration now**: keep R1/research archived.

Acceptance for public R1:

- versioned response/unsupported behavior;
- source identity/provenance/revalidation;
- security/privacy (root admission, secrets/PII, auth/audit);
- cache/invalidation or explicit no-cache contract;
- p95/PSS/concurrency;
- release/rollback/feature flag;
- dev + held-out evidence validation.

### Decision B — R4 scope

If R4 is resumed, write a **new preregistration** for a full scripted agent loop:

- preserve B0 evidence before expansion;
- file/byte budget, not item-only;
- CHECK/corrective round;
- missing/stale/budget/cancel gates;
- held-out and performance measurement.

Do not claim R4 completion from the existing expander or S1/S1b/RefInt
prototypes.

If R4 is not resumed, explicitly declare the RAG program paused. Do not keep
launching selection micro-missions.

### Decision C — GQ5

If GS is continued:

- fresh ≥8 dev / ≥8 held-out source-first set;
- freeze candidate/cases/labels before execution;
- no reuse of GQ/GQ2/GQ3/GQ4;
- zero false VERIFIED/non-live/fabricated evidence;
- required-group recall ≥0.80; role ≥0.85; wrong-kind 0; negative safety 1;
- abstention/token/resource gates;
- independent adjudication if possible.

Do not merge GS before this.

### Decision D — R5/R6

Only after a working R4 loop and R1 integration:

- paired control/candidate on frozen pilot;
- source spans/relations/claims, reads/bytes/time, PSS/CPU, invalidation;
- opt-in release with feature flag, unsupported behavior, process-boundary
  tests, upgrade/reopen/cancel/rollback;
- owner approval.

## 4. Phase acceptance (from canonical plan, not newly invented)

| Phase | Acceptance that must actually be demonstrated |
|---|---|
| R0 | frozen source/cases/targets; no post-hoc target change; provenance |
| R1 | all evidence fields/hashes/ranges; gaps not silent; revalidation; three conclusion classes; budget/dedup |
| R2 | project/type/method/DbSet/SaveChanges/guard facts for selected scope; unresolved/dynamic explicit |
| R3 | proc/view/source variant/read-write/dynamic facts; no deployment authority confusion |
| R4 | full loop handles missing/stale/budget/cancel; scripted-agent end-to-end; public contract |
| R5 | paired dev+held-out quality/footprint; complete metric definitions |
| R6 | opt-in, versioned, rollbackable, documented unsupported behavior |

## 5. Dependencies

```
R0 freeze
  ├── R1 product shape
  │     ├── security/privacy/versioning/cache/performance
  │     └── R6 release
  ├── R2/R3 facts
  │     └── R4 full loop
  │           └── R5 paired qualification
  │                 └── R6 release
  └── post-R4 GS
        └── GQ5 (only if R4 discovery is revisited)
```

R1 can be shipped independently as a library only if the owner accepts the
library-only limitation. R4/R5/R6 do not become true because R1 exists.

## 6. Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| Repeating selection micro-missions | endless NO-GO, no product MVP | owner decision to resume R4 discovery or stop |
| Treating GS readiness as qualification | false release claim | GQ5 + independent adjudication |
| Mixing B0 oracle/fixed baseline | invalid benefit claim | name metric and baseline explicitly |
| Old E4/E3 route tables | invalid comparisons | E5/E6 contract; no reuse |
| Source v3 vs installed v2 | compatibility/data risk | explicit rebuild/rollback, no silent migration |
| R1 library-only forgotten | no external agent MVP | product decision before R6 |
| Plan stale | governance drift | reconcile docs or archive research |
| Observer sees installed `zg 0.0.1` | false “RAG shipped” | state separation |

## 7. Independently testable outcomes

For any future claim, require a receipt that demonstrates:

1. exact candidate/corpus/source SHA;
2. dev/held-out separation and no target change;
3. required evidence spans or explicit gaps;
4. route contract and model/device identity;
5. error/abstention behavior;
6. resource/time measurement;
7. reproducible command and artifact hash;
8. rollback path;
9. public exposure status (library/CLI/MCP/daemon);
10. owner decision ID.

## 8. Recommendation

The shortest honest path is:

1. **Decide R1 product shape now.** A library-only R1 is useful but not an
   external-agent RAG MVP.
2. **Do not start another selection/grounding micro-mission** unless R4
   discovery is explicitly re-authorized with a new preregistration.
3. **If continuing GS**, run GQ5; do not merge readiness.
4. **If not continuing**, archive post-R4 branches and keep R1 as documented
   internal capability.
5. **Do not claim R5/R6** until a full R4 loop and public R1 contract exist.

## 9. Evidence anchors

- `docs/rag/implementation-plan.md:7-140,187`.
- `docs/rag/README.md:3,88,172`.
- `rust/docs/rag-r1-release-gates.md:1-45`.
- `rust/docs/rag-r4-candidate-pilot.md:1-70`.
- `rust/docs/rag-r4-bottleneck-and-r1-release-audit.md:1-170`.
- `rust/docs/rag-r4-followup-results.md:1-50`.
- `rust/docs/e5-*`, `e6-*`.
- `06-RAG-POST-R4-RESEARCH.md`.
- `08-DECISIONS-AND-OPEN-QUESTIONS.md`.
