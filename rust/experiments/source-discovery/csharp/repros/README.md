# Reviewer finding reproductions

These are diagnostic reproductions, separate from both frozen benchmark suites.
They cover the post-review risks of generic type/method identity collisions,
`dynamic` locals, type-named local receivers, guessed property types, integer
literal overload selection, and ref/out/in overload identity. They are not
scored as independent validation cases.

Expected behavior:

- `Box<T>` and `Box<T, U>` have distinct type and method IDs and do not share
  caller edges.
- `Target(value)` with a `dynamic` local emits no definite edge and reports
  `unknown_argument_type`.
- `Parse()` and `Parse<T>()` have distinct symbol IDs; because the prototype
  does not bind generic invocations, the generic call is unresolved.
- `Read(int)` and `Read(ref int)` have distinct declaration IDs; local-variable
  argument types and ref call binding remain unresolved rather than creating an
  edge.
- A `Length` property returning `string` does not force an `int` edge; a large
  unsuffixed integer literal selects the fitting unsigned overload; and a local
  variable shadowing a type name produces uncertainty.
- A local function with the same name as a class method makes its containing
  method scope unresolved; the local body is skipped instead of being attributed
  to the containing method.
