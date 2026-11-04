# Legacy V1 runtime recovery inputs — NOT applied

These patches make the old fork's runtime work discoverable from the single
maintained branch without compiling or mixing its incompatible architecture
into the V2 0.8.0/FP16 runtime.

- `post-rc-runtime.patch`: Rust runtime/build/script differences from the parent
  of plan `4f26942` through archive snapshot `13a3555`. This includes features
  already ported separately and incompatible generation/integrity/M4 work;
  **never apply the patch wholesale**.
- `pre-recovery-dirty-source.patch`: exact tracked dirty diff captured before
  source recovery. It includes research, docs and mixed runtime/test changes.
- `provenance.json`: exact base/snapshot identities and patch SHA-256 values.
- Native and modified third-party source patches are in
  `../legacy-native-0.7.2/`. Original raw untracked/generated evidence is kept
  in the private preservation archive, not published as live product source.

The complete legacy tree/history is also available at tag
`archive/zg-v1-before-recovery-20261104`. The original released target is tag
`archive/zg-v2-release-20261104`. Current implementations and pending items are
tracked in `rust/docs/branch-recovery-status.md`.

These are preservation artifacts, not qualification or permission to change
native versions, layouts, concurrent optimize behavior, benchmark policies or
installed binaries. Selective recovery must preserve the V2 safety contracts.
