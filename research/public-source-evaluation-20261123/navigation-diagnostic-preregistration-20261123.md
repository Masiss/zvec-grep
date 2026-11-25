# Bounded navigation mechanics diagnostic — preregistration

Status: **frozen before navigator runs; not a new validation**.

## Why this diagnostic is permitted

The post-hoc source audit found a potentially useful but unresolved entry-point contrast on val-02: R4 delivers a `BufferedTokenStream` class-level item while lexical `rg` has no item from the target file. The delivered R4 fragment is `Fill`, far from the requested `Consume` behavior, and its adjudicated task outcome is not useful. On val-03, R4 internally recalled accepted sibling fragments but withheld them from the navigator; `rg` had an accepted-range item. These facts justify one equal-policy follow-up mechanic measurement, but do not establish an actionable entry-point advantage. Fresh-context GPT-6.1 Sol High approved a diagnostic under an all-task policy.

## Frozen identities

- Repository source HEAD: `663566546f03768b9b021c65eb3accc4aa0720a1`.
- Public source scope: 1,279 locked `.cs` files from ANTLR, Dapper, and Newtonsoft.Json; source lock SHA-256 `214298e7f84f610a2a47acee48aa1f102f472bee6c9e8a76cdd95a6a9df7ebf2`.
- Materialized source root: `/tmp/zvec-public-source-20261123`.
- All ten prompts, in original order, from `author/validation-prompts.json`; SHA-256 `50d5b5628c69b4c9c321984407e908a0f46092b728ba4aebb8ac27d491837dd6`.
- Original R4 and `rg` starting packs only: `runs/validation-final-20261123/{r4,rg}/val-XX/{hybrid-delivered,rg-delivered}.json`; frozen run manifest SHA-256 values are R4 `1e49a9a47129aa984ae07d2df58ca152527508d8e08c64ed9d7ad6964559b824` and rg `46ea3a8543025abaec19d8f9063a271adbd03ccc36a748412648799631cf1abf`.
- Independent gold is held for adjudication only: SHA-256 `450253d1c1e7c3e2409ea42ccf48749ad83c236784ba29f4160dae47a7688181`. No navigator or navigation coordinator may receive the gold file, report, aggregate outcomes, opposite arm, or expected paths/ranges.

## Inclusion and assignment

Include every prompt `val-01` through `val-10`, including the abstention control, all ties, misses, and gold-scope uncertainty. Compare only frozen R4 and lexical `rg` packs; Candidate C is excluded. This is 20 independent navigation units. Each unit receives a fresh agent context with exactly one prompt and one starting pack; randomized opaque arm IDs conceal method names from navigators and adjudicator. Assignments may not be dropped or replaced after outcomes are seen.

This is a prospective process on a previously exposed diagnostic set. It cannot remove historical contamination or become independent validation. The coordinators have seen gold and must not provide gold-guided routes. Navigator isolation is procedural because filesystem permissions are shared; if an agent accesses excluded report/gold artifacts, discard that unit, preserve its receipt, and do not replace it.

## Navigation protocol

- Navigator sees the verbatim task question and only the assigned original pack. It may inspect that pack's disclosed file paths/content and navigate the locked public source tree using the same tools for every arm: literal `rg` search and bounded source-range reads. No network, package install, runtime, external index, semantic index query, answer model, or private source.
- No expected path, symbol, target line, gold-derived search term, hidden hybrid candidate, or other arm's pack is supplied. The navigator chooses follow-up queries based only on the question, assigned pack, and source it has itself read.
- Maximum per unit: 3 rounds, 6 tool calls, 24,576 additional UTF-8 source bytes read or returned, no more than 10 final evidence items, and no more than 24,576 source bytes in the final evidence pack. Cumulative bytes exposed (starting pack plus all follow-up source returns/reads) must not exceed 65,536. Count repeated source bytes each time they are exposed. Search metadata/command text is logged separately; source snippets count toward source budgets.
- Stop at the first of budget exhaustion, explicit abstention, or navigator declaration that it has enough source evidence to check the requested behavior. Navigator submits source paths/ranges, exact tool calls, source-byte accounting, final evidence subset, whether the request remains unresolved, stopping reason, and elapsed time if observable. It must not draft or state an answer.
- No retries, no changed policy, no selective reruns, no candidate tuning. If accounting cannot be reconstructed, mark the unit invalid and retain the reason; do not replace it.

## Adjudication and decision criteria

An independent adjudicator receives randomized opaque method IDs, the prompt, the frozen required behavior/ranges, and each unit's final evidence pack. It does not see route names, ranks, navigation transcript, or the paired arm while labeling. It records for each pack: first behavior-specific evidence rank/range; whether that evidence is an actionable implementation starting point (requires a code path tied to the asked behavior, not a file/class name alone); number of required behavior steps covered; bounded sufficiency; ambiguity/misleading evidence; and whether gold scope misses a valid alternate implementation. Pairwise labels are revealed only after labels are sealed.

S1 contrast is descriptive: compare how often and at what delivered-source cost each route yields an adjudicated actionable starting point. S2 contrast is descriptive: compare bounded sufficiency, follow-up calls, bytes, and latency under equal caps. This diagnostic does not estimate S3 and does not pass the frozen Candidate C gate.

Do not call semantic value observed on a single exposed sample. A route difference can only justify designing a new independent benchmark if it is consistent across all included tasks, supported by source lines, and not driven by a single cherry-picked task; ties and failures remain in denominators. If there is no source-verified difference or it costs materially more without lower navigation effort, keep `HOLD`. No numerical statistical-significance or generalization claim is permitted.

## Output and reproduction

Save one raw receipt per task/arm under `research/public-source-evaluation-20261123/navigation-diagnostic-20261123/`, preserving prompts/pack hashes, navigator/model identity as available, exact calls, output paths/ranges, byte/time accounting, stop reason, and any isolation breach. Save anonymous adjudication separately until all labels are sealed. Hash every receipt and the consolidated result. No product source, public API/default, index, or frozen benchmark changes are allowed.
