# Direct exact-file/header route v1 — PASS offline gates

Date: 2026-11-26. One preregistered pack-policy arm on frozen C# benchmark v2.

## Verdict

**PASS for the frozen offline gates. Not a production-port approval.**

The arm rescued D10 without changing any other pack. C# improved to 3/12 all-gold
and 5/25 exact while precision increased and mean tokens decreased.

## Policy

- Extract explicit identifiers with the existing gold-blind logic.
- Match at most two identifiers to exact file stems.
- Insert one <=1024-byte source window per file before C0 fill:
  - start at the nearest preceding namespace/class declaration line when present;
  - otherwise start after BOM/file start;
  - adjust end to a UTF-8 boundary.
- Add at most one referenced-type file, preferring the longest identifier-prefix
  match with the primary file identifier.
- Hard cap: two files/windows.
- If C0 already covers the direct windows, keep the pack byte-identical.
- Otherwise clip C0 items against direct intervals and fill the unchanged
  24 KiB/64-item cap in original order.

No index, model, query route, ranker, resolver, source or gold change.

## Results

| Metric | Benchmark v2 C0 | Direct-file v1 |
|---|---:|---:|
| All-gold cases | 2/12 | **3/12** |
| Exact spans | 3/25 | **5/25** |
| Macro precision | .012356 | **.014628** |
| Mean tokens | 6,590.42 | **6,583.92** |

- Gained: D10, exact 2/2.
- Lost: none.
- D09 and D11 remain all-gold.
- Only D10 changed; the other 11 packs are byte-identical to C0.
- D10 pack: 21 items, 24,353 bytes, valid hash/range/content, zero gaps.
- D07/D12 remain unchanged because their frozen questions contain no explicit
  file identifier; the arm does not guess the omitted proc name.

## Validation

- Benchmark-v2 manifest: 843 entries, zero failures.
- Input/case/index/binary hashes unchanged.
- All 12 packs independently validated.
- All 11 no-match/already-covered packs byte-identical to C0.
- D10 direct windows independently checked to contain both gold spans.
- All preregistered gates pass.

## Limitations and next decision

This is an offline Python pack-prefix proof, not an engine implementation. The
namespace/prefix heuristics are generic but have only been evaluated on the
12-case C# slice; no held-out or runtime p95 claim is made.

A production candidate requires a separately authorized opt-in engine port with
unit/integration tests, parity against this receipt, held-out evaluation and
runtime/p95 validation. D07/D12 require query-context or benchmark erratum work,
not more guessing in this route.

Artifacts: `PREREGISTRATION.md`, `RUN-A.json`, `GATES.json`, `packs/csharp/A/`,
`scores/`, `audit/PARENT-AUDIT.json`.
