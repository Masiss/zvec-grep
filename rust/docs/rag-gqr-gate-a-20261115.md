# GQ-R source liveness and binding repair — Gate A report 2026-11-15

Status: **Gate A PASS** for repaired candidate v5. Historical candidate
`3821168` and old GQ/GQ2/GQ3 data remain unchanged as baseline/development
evidence.

## 1. Root cause confirmed from source

The GQ failure was reproduced in source, not inferred from the old report:

- `Services/.../WorkProcessCommand.cs:164` is a **commented-out**
  `ChangeWorkProcess` declaration.
- `EmployeeCommand.cs:472` is a **commented-out** `ChangeWorkProcess(...)` call.
- `EditEmployeeJobAndGroup` live body (461–520) only has `SaveChanges` and cache
  deletes.
- The old extractor parsed comment text as live declarations/calls, producing
  false `VERIFIED` wrapper chains through `ChangeWorkProcess`.
- `DbSet<EmployeeChangeRequestDetail> EmployeeChangeRequestDetails` at
  `CoreDBContext.cs:29` was not discoverable because query identifiers were
  truncated to two tokens and property/type discovery was absent.
- `CONTROL` method ranges were overwritten by `WRAPPER` role when the same
  source had a binding.

## 2. Repair contract

### Liveness

`liveness_20261115.py` is a conservative lexical scanner, not a C# compiler.

It handles:

- `//` line comments and `/* ... */` block comments;
- regular strings with escapes;
- verbatim strings (`@"..."`, `@$"..."`, `$@"..."`);
- raw triple-quoted strings;
- interpolated strings (treated conservatively; content is not executable code
  for call/declaration extraction);
- char literals;
- `#if false` / `#if 0` inactive branches;
- `#else` / `#endif`;
- unknown preprocessor symbols are `UNKNOWN`, not live.

Commented/inactive code is blanked from the code stream; string values are
retained separately for SQL invocation discovery. Unclosed strings/comments and
unclosed preprocessor branches fail conservative.

### Binding

- Direct `VERIFIED`: live method body contains an explicit `execute/exec sp_*`
  string and exactly one SQL definition.
- Wrapper `VERIFIED`: every chain member is a live indexed method and the
  terminal direct edge is verified.
- Name-only, ambiguous, unresolved and non-live references are not promoted.
- A wrapper chain through a commented method cannot be indexed.
- Source/target paths and inclusive ranges remain on every binding.

### Grounding

- Commented/inactive methods cannot become `CONTROL`/`WRAPPER` evidence.
- Exact `DbSet` property/type discovery is included for property/type queries.
- `CONTROL`, `WRAPPER`, `SQL`, `VIEW`, `PROPERTY`, `TYPE`, `DATA` are distinct
  roles.
- Long methods use `SaveChanges` windows/blocks, not the whole method, when the
  task is write/entity evidence.
- Unknown SQL/method near-names abstain.
- Negative “does X invoke Y?” does not add SQL when X has no verified binding.

## 3. Gate A tests

`test_liveness_20261115.py` covers synthetic:

- line/block comments and “nested-looking” delimiters;
- escaped, verbatim, raw and interpolated strings;
- `//`/`/* */` inside string/SQL text;
- `#if false`, `#else`, unknown symbol;
- commented declarations/calls around live calls;
- `DbSet` property;
- SQL string `execute sp_TestProc`;
- commented corpus `ChangeWorkProcess` and live `EditEmployeeJobAndGroup`.

Result: **PASS**.

`verify_gate_a_20261115.py` independently checks:

- no `ChangeWorkProcess` in live index;
- `EditEmployeeJobAndGroup` has no commented call/proc;
- `EmployeeChangeRequestDetails` resolves to `EmployeeChangeRequestDetail`;
- every `VERIFIED` binding has explicit or fully live wrapper evidence;
- no name-only verification;
- target paths exist;
- post-repair binding counts.

Result: **PASS**, 96 checks.

### Post-repair binding counts

| State | Count |
|---|---:|
| VERIFIED | 13 |
| AMBIGUOUS | 2 |
| UNRESOLVED | 0 |
| Direct explicit | 1 |
| Wrapper chain | 12 |
| Name-only | 2 |

The two former false wrapper chains through `ChangeWorkProcess` are gone.

## 4. Regression against old GQ (development only)

With repaired bindings/grounding:

- GQ-03 no longer selects commented `ChangeWorkProcess` or SQL; selects live
  `EditEmployeeJobAndGroup` only.
- GQ-04 abstains instead of selecting commented code.
- GQ-01 returns property 29 and type 15–92.
- GQ-02/GQ-05/GQ-10 return live control + wrapper + SQL with distinct roles.

These are **regression evidence**, not final generalization: old GQ was used to
find and verify the defects.

## 5. Gate A verdict

**PASS.** The known GQ correctness defects are repaired at the
extraction/binding/liveness mechanism, with adversarial tests and source-level
verification. This does not yet prove the grounding quality claim; that is
Gate B/GQ4.
