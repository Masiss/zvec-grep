# Workspace

The workspace module manages workspace persistence and lifecycle.

## Storage layout

Each workspace stores its settings and index in the source directory's fixed
`.zvec-grep` child:

```text
source/
└── .zvec-grep/
    ├── manifest.json       saved settings and active index generation
    ├── index/              active index data
    │   └── reconciliation/ optional per-file embedding-reuse manifests
    ├── temp/               build and recovery data
    ├── locks/              workspace access coordination
    ├── credentials.key     optional key for saved encrypted API credentials
    └── authorization.json  optional remote-service consent records
```

Temporary and optional entries are created when needed. Downloaded models are
cached separately.

## Key conventions

- **Discovery.** Search and status walk upward to find the nearest workspace.
  A manifest or pending build record identifies a workspace.
- **Relocation.** Move or copy the source directory together with `.zvec-grep`.
  Moving `.zvec-grep` independently or rearranging its contents is unsupported.
- **Publication.** Incremental indexing updates the active index in place.
  First builds and rebuilds prepare an index in `temp/` and publish it after
  storage closes. Interrupted publication can be recovered; a failed rebuild
  before the manifest commits preserves the previous index.
- **Compatibility.** `indexVersion` determines index compatibility.
  Incompatible indexes must be rebuilt; there is no in-place migration.
- **Rebuild preflight.** A rebuild requires at least one active-index copy of
  free space; the conservative same-size target is `2 × active + 4 GiB`. Between
  the floor and the target the build proceeds with a warning because a shrinking
  rebuild may fit. A failed build preserves the active index. The error help
  reports exact bytes and remediation.

- **Maintenance.** `index/…/optimize.pending` records retry intent for native
  collection optimization. Fragment optimization runs outside the store state
  lock; scalar collection optimization remains under it. The marker is removed
  only after a successful, generation-unchanged finalization.
- **Embedding reuse.** `index/…/reconciliation/file-*.json` is an optional,
  additive sidecar (version 2, ≤4 MiB). Missing, corrupt, oversized, or older
  sidecars are ignored; the directory and entries must not be symlinks or
  special files. Deleting the directory disables reuse without affecting
  canonical data or queries.
