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

- **Discovery:** H5 deterministic cap-500 ordering reaches **19/25 (76%)**
  with identical selected hashes across hash seeds; it is the H2 ceiling and
  cannot reach 20/25. H6 structural references reach **20/25 (80%)** by one
  incidental D02 span, but p95 wall 10.97 s, CPU 15.3 s, hard independent eval
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

1. Discovery: query/symbol formulation and code-aware candidate retrieval,
   using depth-50 candidates and general-purpose signals; pre-register before
   measuring.
2. R1 public qualification: concurrency/performance/cache/security/release gates.
3. Selection/Expansion only after Discovery evidence is sufficient.
