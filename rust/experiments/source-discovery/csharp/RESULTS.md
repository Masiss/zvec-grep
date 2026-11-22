# C# source discovery — continuation results

Date: 2026-11-22  
Start checkpoint: `e1f3d9d`  
Frozen C# corpora/cases: `9ee18f8`  
Initial C# implementation: `7723fdc`  
Caller uncertainty fix: `55f8aa2`

## Decision

**INCONCLUSIVE** for product adoption and generalization.
The measured synthetic cases show that Tree-sitter plus scoped type/signature
matching can find partial-class methods and produce exact caller-to-callee
symbol edges without the old prototype's name-only cross-linking. This does not
establish an improvement on HRM2.0 or prove Roslyn-equivalent C# semantics.

## Approach choice

- Case-sensitive and case-insensitive literal `rg` are the cheap lexical
  baselines. They return matching lines, including comments, strings, vendor,
  and generated code, but do not attach a method identity to a call site.
- The previous Tree-sitter prototype scanned zero C# files. Reusing its Rust
  CLI and adding the cached `tree-sitter-c-sharp 0.23.5` grammar provides C# AST
  declarations/calls while staying offline and avoiding an index/model.
- Roslyn assemblies and a .NET SDK are also installed locally. A semantic-model
  route could resolve more C# language rules, but it would need compilation
  references/project context and a .NET sidecar. It was not implemented or
  benchmarked. The selected prototype instead resolves only relationships it
  can support from local syntax and simple static types, and reports the rest
  as unresolved.

## Frozen data and baselines

Development contains 9 tasks; independent validation contains 6. Both corpus
sets include partial classes split over files, overloads, same-named methods in
different types, cross-file callers, dynamic dispatch, generated/vendor noise,
and negative queries. The fixture/case hashes and actual baseline outputs are
stored under `fixtures/{dev,validation}/baseline.json`. Checkpoint `9ee18f8`
predates implementation changes.

Baseline commands ran with `--glob '*.cs'` against the whole frozen fixture
root, so generated/vendor files and all source lines were visible to `rg`:

| Suite | Tasks | case-sensitive `rg` lines | case-insensitive `rg` lines | Previous prototype C# files scanned |
|---|---:|---:|---:|---:|
| Development | 9 | 20 | 42 | 0 |
| Independent validation | 6 | 1 | 20 | 0 |

The case-insensitive baseline is a strong substring baseline for PascalCase
recall. It still cannot identify declarations, overload identities, or exact
caller edges from its lines alone.

## Results

Three subprocess runs per task; latency includes process startup and the
`/usr/bin/time` wrapper. RSS is peak resident set size. These numbers come from
small local synthetic corpora on one host.

| Suite | Declaration TP/FP/FN | Exact caller→callee symbol edges TP/FP/FN | Uncertainty checks | Median latency: `rg -i` / prototype | Median RSS: `rg -i` / prototype | Median output bytes: `rg -i` / prototype |
|---|---:|---:|---:|---:|---:|---:|
| Development | 15/0/0 | 20/0/0 | 3/3 matched | 11.80 / 10.68 ms | 6,824 / 6,440 KiB | 610 / 1,315 B |
| Independent validation | 7/0/0 | 10/0/0 | 1 valid check matched; 1 frozen expected tuple is invalid | 11.74 / 9.13 ms | 6,728 / 6,076 KiB | 602 / 828 B |

The validation gold for `overload-edges-by-parameter-type` is inconsistent:
its source declares the caller parameter `dynamic`, but its expected result says
`object` and `ambiguous_overloads`. The candidate conservatively returns
`dynamic_dispatch`. The frozen case was not changed. Its declaration and exact
edge scores remain valid; exclude that one uncertainty assertion when judging
uncertainty precision/recall. The separate `uncertain-overload-call` case
confirms the caller-query uncertainty result after the fix.

Edge scoring compares exact `(caller symbol ID, callee symbol ID)` pairs. It
does not score repeated call-site multiplicity; call-site file/line is still
present in candidate output. Unresolved-call scoring checks caller, method
name, and reason, but not the candidate callee ID set. These are remaining
measurement limits.

`rg -i` median output is smaller than the prototype's structured JSON. The
prototype trades those bytes for declaration signatures, scoped edges, and
explicit uncertainty. It suppresses comment/string pseudo-calls and excludes
generated/vendor trees; it does not reduce output bytes in the aggregate.

The caller-query filter was corrected after a development reproduction showed
that querying `StartUnknown` or `ParseUnknown` returned no unresolved call. The
filter now includes unresolved calls whose caller is a matched declaration, as
well as unresolved calls whose possible callees match. Before/after records are
`results-validation-pre-fix-20261122.json` and
`results-validation-final-20261122.json`.

## Implementation boundary and cost

`src/csharp.rs` merges partial types by namespace, nested type identity, and
generic arity; method IDs include generic arity, explicit-interface qualifier,
and ref/out/in parameter signatures. It resolves same-type and typed-receiver
calls by scope, arity, and simple known argument types. Unknown argument types,
`dynamic`, unknown receivers, overload ambiguity, generic method binding, parse
errors, oversized files, truncated scans, and local-function scopes produce
unresolved records. It scans at most 2,500 `.cs`/`.csx` files in sorted path
order, at most 10,000 directories and 64 levels, skips files over 2 MiB, and
excludes hidden/build/vendor/generated directories and common `.g.cs`/`.designer.cs`
patterns. C# discovery runs independently of the legacy-language file cap. Each directory is enumerated into a sorted list before the file cap is applied, so an exceptionally large single directory can still cost memory/time.

This is syntax plus a small type resolver, not a full C# compiler. It does not
resolve inheritance, extension methods, aliases/usings, generic method calls,
explicit-interface implementation dispatch, optional parameters, or general
implicit numeric conversions. Unknown member receivers, untracked locals,
dynamic dispatch, and local-function scopes fail closed, which can omit valid
relationships. The module is 1,162 lines and adds a Tree-Sitter grammar
dependency, so it is substantially more complex than `rg`.

Both C# fixture suites compiled with the locally installed .NET SDK using an
empty NuGet source list. `cargo check --offline` and `cargo build --offline`
passed for the standalone tool. No external dependency or model was downloaded.

## Review, reproducibility, rollback

The fresh-context reviewer found and reproduced false-edge risks from generic
identity collisions, unknown argument types, member properties named `Length`,
large integer literal typing, type-name shadowing, and local-function scope.
They are reproduced in `repros/` and fixed: generic arities/ref modifiers now
distinguish IDs; `.Length` is inferred only for known strings/arrays; integer
literal suffixes/ranges are parsed; untracked receivers fail closed; and
containing methods with local functions do not emit definite edges. The scan
is deterministic, bounded by file/directory/depth limits, and independent of
the legacy cap. Final diagnostic outputs are in
`repros/results-after-review-fixes.json`. The frozen validation gold mismatch
is preserved.

Final reviewer recommendation: **INCONCLUSIVE for product adoption**. The final
candidate has 20/20 development and 10/10 independent exact symbol-edge pairs
with no false edges in these fixtures. It does not establish general C# binding
accuracy, large-repository performance, or HRM2.0 gains. Directory traversal still
loads each directory listing before sorting. The reviewer saw only synthetic
material; no private source was shared.

Reproduce the C# suite from the repository root:

```bash
CARGO_TARGET_DIR=/tmp/zvec-source-discovery-target \
  cargo build --offline --manifest-path rust/experiments/source-discovery/Cargo.toml
python3 rust/experiments/source-discovery/evaluate_csharp.py --suite dev \
  --exe /tmp/zvec-source-discovery-target/debug/zg-source-discovery-prototype
python3 rust/experiments/source-discovery/evaluate_csharp.py --suite validation \
  --exe /tmp/zvec-source-discovery-target/debug/zg-source-discovery-prototype
```

Rollback the final post-review fix commit listed in the root handoff, then
remove `7723fdc`, then the frozen C# corpus `9ee18f8`. No production route
depends on this tool.
