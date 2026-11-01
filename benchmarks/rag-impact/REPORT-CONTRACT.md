# Evidence report contract (R1)

zvec-grep returns evidence; an external agent writes the final answer. The
report keeps three conclusion classes separate:

1. **Directly supported** — every claim cites a current `EvidenceItem` whose
   `source_sha256`, byte range and content still validate.
2. **Candidate / needs verification** — a plausible relation, receiver or
   mapping that lacks a complete typed chain. List the missing check.
3. **Unobserved gap** — absent body, dynamic SQL/reflection, missing project
   inclusion, stale source, or exhausted budget. Do not turn a gap into a
   negative claim about the whole system.

## Citation rules

- Cite project, relative path, `source_sha256` and the text range.
- The cited content must equal the current file slice; re-run `revalidate`
  before the final answer.
- A changed, renamed, deleted or out-of-root source invalidates its evidence.
  Never cite the old line number against new content.
- Same-named files in different projects are different evidence.
- Read/projection does not prove write/persistence. A method name does not prove
  a hard delete, a live procedure, or a database connection.
- Budget exhaustion yields `PARTIAL`, not an unbounded continuation.

## Pack status

- `ok` — items only.
- `partial` — items plus explicit gaps.
- `empty` — query returned no requests/items.
- `failed` — every request produced a gap.
