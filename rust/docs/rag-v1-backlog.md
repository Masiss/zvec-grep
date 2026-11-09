# RAG v1.0 — reconciled backlog and D/S/E status

Date: 2026-11-08. Product baseline: current `work/zg-recovered` (upstream-based
integration plus recovery commits; no W3/W4 re-port). No push/release.

## Closed as historical NO-GO

| Candidate | Result | Preserved evidence |
|---|---|---|
| R4 candidate pilot | Recall 0.2917 vs fixed 0.3750 | `candidate-pilot.json`, attribution |
| R4-A multi-call seeds | 0.2917 | `candidate-r4a.json` |
| R4-B file clusters | 0.2917; cost-only | `candidate-r4b.json` |
| R4-D2 file-hit ranges | 0.3750, equal fixed | `candidate-r4d2.json`, tool, prereg |
| R4-D1 identifier expansion | 0.3472 | `candidate-r4d1.json`, tool, prereg |
| Gap workflow | 0.3333 | archive patch + receipt |

The failed implementations and regression fixtures stay historical; they are not
promoted. The gap prototype is archived at
`~/.local/share/zvec-grep/r4-gap-prototype/worktree-archive-20261108/`
(patch, tests, design, result, SHA256SUMS), not integrated.

## Active status

- **H7 E1 (fact-layer typed reference closure):** coverage @500 23/25,
  @250 20/25, @100 15/25; refs-off 20/25; independent easy/hard 5/5;
  deterministic. **FAIL resource:** peak RSS 393.4 MiB; forensics shows
  ~347 MiB parse baseline + ~38 MiB full ProjectFacts merge + ~3.2 MiB closure,
  not closure alone. p95/I/O partially measured; allocations not instrumented.
  E1 NO-GO; E2 not run. E1R compact declaration index: **23/25 @500**,
  independent 5/5, deterministic; peak RSS **74,028 KiB**, PSS **69,387 KiB**;
  still **NO-GO** resource. Pre-profile found DbSet scope duplication
  (38k/99k/53k vs 234 unique). E2 not run; no cap tuning.
- **Discovery:** H5 deterministic cap-500 ordering reaches **19/25 (76%)**
  with identical selected hashes across hash seeds; it is the H2 ceiling and
  cannot reach 20/25. H6 frequency-ranked observed-name expansion (mislabelled as structural
  references) reaches **20/25 (80%)** by one incidental D02 span, but p95 wall 10.97 s, CPU 15.3 s, hard independent eval
  not run; resource/generalization fails. H2 bounded 15/25; H2 full 19/25 is an
  order artifact. Hard independent set: H1/H2 4/5, H4 5/5; easy set 5/5 all.
  Discovery **NO-GO**; missing D02 C4/C5, D10 C4, D12 S1.
  Selection/Expansion frozen.
- **Selection:** frozen; A/B historical policies selected 4/25 and 6/25. No
  Selection work is authorized until Discovery reaches its gate.
- **Expansion:** gap prototype validates anchors/definitions but cannot find
  unobserved files; archived, no integration.
- **R1 delivery:** evidence contract 10/10 tests; V2 dev pilot 12/12 packs,
  25/25 items, 0 gaps, 0 revalidation gaps. Public v1.0 still needs service/CLI/
  MCP integration, version/cache policy, p95/PSS/concurrency, security/privacy,
  independent validation and release/rollback.

## Open issue audit

- **Concurrency lock:** R1 evidence uses local state and does not hold a shared
  store lock; no concurrent qualification test yet. Outside-lock scalar
  maintenance remains blocked by the earlier storage decision; do not re-enable.
- **Freshness:** R1 revalidate tests pass and gap finalize revalidates; no
  cache/version key, so every final answer re-reads/re-hashes. Open for public
  cache policy.
- **Generated-content contamination:** the scan policy excludes generated/
  results paths (file_selection tests); R1 evidence does not apply that scan
  policy to caller-supplied paths. Open admission/denylist decision.
- **PII/privacy:** no secret/PII classification or redaction in R1 evidence;
  traversal/symlink/UTF-8 are covered. Open security gate.
- **Protected `rust/target` incident:** first delegated build wrote into the
  protected original V1 target; parent interrupted. Evidence/incident preserved
  at `~/.local/share/zvec-grep/r4-gap-prototype/parent-containment-review/`;
  prior bytes were not captured and no restoration is claimed. All new builds
  must use external `CARGO_TARGET_DIR`.

## Operating rules

- General-purpose, lightweight, continuous indexing; no new LSP/LLM/graph/heavy
  index, no corpus/gold/target/held-out changes, no push/release.
- Fixed FTS 0.3750 is the deployable comparison; oracle 0.4722 is historical;
  blind target 0.5722 unchanged.
- New experiments require pre-registration, dev-only first, no gold tuning, and
  a distinct metric per D/S/E layer.
- If an experiment fails, keep NO-GO, record cause, and continue independent
  work; do not retune indefinitely.

## Next authorized work

1. Owner decision on H7 resource: E1R is NO-GO at 74,028 KiB peak RSS / 69,387
   KiB PSS. Do not run E2 or tune caps against gold. Any further resource work
   needs a new bounded pre-registration and the same fixed 64 MiB gate.
   E1S (branch `work/h7-e1s`) added a reconcile + scope-sharing result: refs-off
   is a protocol artifact (20/25 full-fact vs 16/25 anchor-only) and the
   pre-registered scope sharing is memory-neutral; the fixed gate stays NO-GO
   for the E1R harness. See `rust/docs/rag-discovery-h7-e1s-reconcile-results.md`
   on that branch.
   Control qualification (branch `work/onindex`, release): coverage/parity/
   provenance/determinism/refs-off pass on dev and both high-anchor workloads,
   but the worker gate is **NO-GO** on independent-easy (66,776 KiB RSS vs
   65,536; PSS passes). Peak is anchor fact acquisition in the 87-anchor case.
   Discovery candidate is **not frozen**. See
   `rust/docs/rag-onindex-control-qualification.md`.
2. One-Index discovery (branch `work/onindex`): audit + two code arms + a
   document prototype, then the H7 control qualification. Arm B (bounded lexical
   declaration selection) reaches 23/25 with ~1.0 s/case p95 and 51.3 MiB peak
   (release, dev protocol); arm A (index-assisted lookups through the public FTS
   surface) reaches 23/25 but fails latency (10.8 s/case) and memory. Arm A is
   blocked on data: C# has no code-outline extractor, so `.cs` is indexed as
   plain text with no `symbol_name`/`symbol_type` and exact lookup returns
   nothing (the `symbol_type` filter itself is correct). Remaining work, each
   requiring its own pre-registration: bounded per-case anchor/fact acquisition
   for the control; then C# symbol extraction followed by a bounded exact-symbol
   endpoint and an A re-measure; worker-pool admission control (K≥2 exceeds the
   worker envelope). Do not add an index format and do not change the 64 MiB
   gate. See `rust/docs/rag-onindex-discovery-results.md`,
   `rust/docs/rag-onindex-control-qualification.md` and
   `rust/docs/rag-v1-handoff-20261109.md`.
3. R1 public qualification: CLI/MCP/service integration, cache/version
   invalidation, concurrency/performance, generated/results admission,
   PII/secret/auth/audit, independent validation and release/rollback.
4. Selection/Expansion only after Discovery evidence is sufficient and
   explicitly authorized.
5. Continuity: `rust/docs/rag-v1-handoff-20261109.md`.
