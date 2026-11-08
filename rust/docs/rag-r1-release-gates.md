# R1 lightweight — v1.0 release gates

Status: **R1 correctness slice PASS; V2 development evidence pilot PASS; public
v1.0 release still blocked by productization gates.**
Date: 2026-11-08. Read-only against the frozen HRM2.0 checkout; held-out was not
inspected. No candidate/R4 change and no new model/index/LSP.

## Evidence added

- `api::evidence` focused suite: **10 passed, 0 failed** (Unicode/CRLF/EOF,
  traversal/symlink, stale/renamed, same-name projects, budget/dedup, empty vs
  invalid query, changed source, forged coordinates).
- V2 development evidence pilot on all 12 real-source cases:
  - `benchmarks/rag-impact/results/r1-v2-dev-pilot.json`
  - 25 requested spans, **25 items, 0 gaps, 0 revalidation gaps**, all 12 packs
    `Ok`.
  - Two read-only smoke runs: total assemble+revalidate **32.9 ms** and
    **235.3 ms** (disk/cache variability), process PSS ~**2.6 MiB**. This is a
    smoke/baseline, not a p95, concurrency or peak-RSS qualification.
  - Harness: `rust/crates/zg-engine/examples/r1_evidence_pilot.rs`.
  - The pilot reads the frozen dev gold spans and source hashes; it does not
    search, index, call a model, write source or touch held-out.

## Gate matrix

| Gate | Status | Evidence / gap |
|---|---|---|
| R1 deterministic contract tests | PASS | 10/10 focused tests |
| Fail-closed path/hash/range/UTF-8 | PASS | tests + pilot gaps=0 |
| V2 real-source dev pilot | PASS | 12/12 packs, 25/25 items, 0 gaps |
| V2 real-source held-out | NOT RUN | forbidden for this audit |
| Public engine API contract | PASS | `tests/r1_public_api.rs` from an external crate: assemble, revalidate, schema-v1 JSON round-trip, missing/invalid failures |
| Public MCP/CLI service integration | MISSING | no CLI/MCP/daemon consumer; library API only |
| Versioning/unsupported behavior | PARTIAL | schema-v1 round-trip covered; no public compatibility/unsupported-behavior policy |
| Cache/version key and invalidation | MISSING | no cache; per-answer revalidation only |
| Performance/footprint gate | PARTIAL | one 33 ms smoke, 2.6 MiB PSS; no p95/PSS/concurrency |
| Security/privacy review | MISSING | traversal tested; no secret/PII/root/auth/audit policy |
| Broader evidence validation | PARTIAL | 12 dev cases pass; held-out and independent review absent |
| Release/rollback/feature flag | MISSING | no release note, rollback or unsupported-behavior policy |

## Verdict

R1 correctness and V2 dev evidence are ready for an **internal/library-only**
capability with documented limits. Public v1.0 still requires owner decisions and
the integration, versioning, cache, security/privacy, performance and release
gates above. No R4 candidate work is authorized by this gate report.
