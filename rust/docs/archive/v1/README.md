# Retired V1 plans and evidence summaries

These documents preserve the newer work that was accidentally developed on the
old `work/zg-optimization` fork. They are historical source material, not active
instructions or proof that a feature exists on `work/zg-recovered`.

- V1 source snapshot: tag `archive/zg-v1-before-recovery-20261104` (`13a3555`).
- V2 released target: tag `archive/zg-v2-release-20261104` (`c2347d5`).
- Active recovery state: [`../../branch-recovery-status.md`](../../branch-recovery-status.md).
- Historical receipts retain their original bytes and source/native identities.
  V1 test/performance/crash PASS statements do not qualify V2.
- Relative links and absolute local paths inside these documents describe the
  historical checkout. Use the archive tag for exact original path resolution.
- The W4 installed files matched its provenance at recovery start, but its
  rollout receipt records FTS/server failures despite the summary's smoke-PASS
  wording. Preserve the discrepancy; no install/rollback is performed by
  source recovery.

The current fork keeps zvec-rust 0.8.0, FP16 and the portable V2 layout. Native
0.7.2 patches are archived in `research/legacy-native-0.7.2/` and are not applied
to the current runtime. M4 remains standalone research, not a default backend.
