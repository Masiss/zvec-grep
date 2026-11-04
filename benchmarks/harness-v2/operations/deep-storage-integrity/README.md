# Deep storage integrity audit evidence

Controlled probe on an isolated 64-file fixture. Raw results:
`results/probe-results.json`.

- Byte flip: vector `.proxima`, FTS RocksDB SST, idmap SST, reconciliation bin.
- Truncate: same targets.
- FTS and vector queries run read-only in a separate state per case.
- Original index and production data were not modified.

Conclusion: structural truncation is rejected; mid-file bit flips can survive
open/query; successful open is not full verification.
