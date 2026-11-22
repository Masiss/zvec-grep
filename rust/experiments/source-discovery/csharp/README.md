# C# partial-class source discovery experiment

This is a continuation of the offline Tree-sitter prototype. The development
and independent validation C# fixtures, expected declarations, exact caller to
callee edges, and uncertainty cases are frozen before changing the prototype.
The development set covers partial classes, methods split across files,
overloads, same-named methods in different classes, typed and dynamic receivers,
comments/strings, generated/vendor paths, and missing identifiers.

Before implementation changes, capture actual outputs from:

1. ordinary case-sensitive `rg -n -F`;
2. case-insensitive literal `rg -n -i -F` (a stronger partial-identifier
   baseline);
3. the previous source-discovery binary (expected to scan no `.cs` files).

The validation fixtures use different types and method names. Preserve them and
their expected exact edges unchanged after the prototype is modified. No fixture
contains private HRM2.0 source.
