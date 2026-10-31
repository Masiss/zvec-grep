# Harness V2 contracts (v1)

This directory is an importable-on-demand contract layer, not an installed
package. Tests add the directory to `sys.path` and import `policy_v1` or
`receipt_v1` directly.

- `policy_v1.py` — read-only policy schema v1 loader and purpose lookup.
- `receipt_v1.py` — fail-closed compatibility reader for historical
  containment receipts and operation plans.
- `golden/manifest.json` — frozen policy hash, fixture hashes, and normalized
  expected fields. Fixtures under `golden/fixtures/` are verbatim copies of
  historical receipts; originals are never rewritten.

Run the suite:

```bash
python3 -m unittest discover -s benchmarks/harness-v2/contracts/tests -v
```
