# Query-time structure mapping diagnostic — R4 v3 C#

**Result:** `STRUCTURE_MAPPING_FEASIBLE` for the matched R4 v3 C# path, as an isolated scratch prototype. The expanded 48-file check exactly joins all 548 unique Tree-sitter method ranges to all 548 structured function entities; no persistent schema or production path was changed.

## What is already present

- R4 v3's C# Tree-sitter extractor creates full class and method entities. Method entities already have full source byte ranges, `symbol_name`, `scope`, declaration signature and attached documentation text. Their `CodeMetadata` does not store explicit `has_body`, body range or documentation range.
- A returned entity preserves full entity content and its file source range. Each candidate fragment preserves an entity-relative half-open byte range. This is sufficient to translate fragment bytes to file bytes and compare them to method AST spans reconstructed from that entity.
- Indexed-search context construction already calls `code_outline` on the stored entity content at retrieval. That routine parses with the existing grammar and lists member names/signatures or call names; it drops AST coordinates and returns only outline text. The backing graph contract has method-like node fields, but the code extraction result currently returns `graph: None`; the indexing pipeline therefore persists an empty graph for these code files. The indexed-search path does not read that graph.
- `csharp_facts::MethodFact` carries method identity and line start/end only. It is a side-facts representation, not a fragment-to-method byte mapping in indexed retrieval.

## Scratch prototype and evidence

The temporary change in the matched R4 v3 snapshot added `code_member_spans`, which uses the same `outline_format`, `resolve_adapter`, Tree-sitter grammar, and parser helpers as the current outline. Given entity content and metadata, it returns method declaration, body, preceding XML documentation, node kind and signature ranges. A caller joins entity-relative ranges with the entity's file source offset and fragment's entity-relative range.

On a 20,455-byte stored `BufferedTokenStream` class entity, the mapper found 33 member spans. Across 25 warm debug-build calls, median parse-plus-map time was **7,386 μs** in that local environment. A later full-file check covered four files (12.3–101.7 KB), joined **167/167** Tree-sitter method spans to the extractor's exact method byte ranges, and measured warm parse-plus-map medians from **3.36 ms to 32.22 ms**. These are local unit-test timings, not service-latency estimates. Existing `code_outline` already reparses stored entity content; returning the outline and spans from one parse avoids a second parse for that entity. A full-file parse remains a separate scope choice.

The prototype checks that query-time method ranges equal method entity ranges from full-file extraction. Focused structural tests passed:

- C# method body and preceding XML documentation map to exact byte ranges.
- An interface method declaration maps with no implementation body.
- Full-file method spans match exactly for all four locked public C# files; `JsonTextReader.cs` has a Tree-sitter parse-error flag, but still yields 73 spans matching 73 extracted method entities.
- A CRLF + Unicode fixture maps 8/8 methods to exact extractor byte ranges, including overloads, constructor, generic method, nested class method, partial-class method, expression-bodied method, and an interface declaration without a body.

## A02 fragment-to-method mapping

All ranges below are absolute half-open decoded UTF-8 byte offsets in the locked public source, after joining the entity-local parser range to the entity's source start.

| Fragment source lines | Related methods | What the mapper establishes |
|---|---|---|
| 99–162 | Constructor, `Mark`, `Release`, `Reset`, `Seek` | Contains the complete `Reset` method byte range and other complete methods. |
| 146–200 | `Reset`, `Seek`, `Consume` | Despite its line range, it starts 24 bytes after `Reset` begins and contains only 44/68 method bytes. Line-only completeness would wrongly label this as a full method. |
| 307–346 | `LT`; documentation owned by `AdjustSeekIndex` | Contains part of `LT` plus 859 bytes of `AdjustSeekIndex` XML documentation, but no `AdjustSeekIndex` implementation bytes. This is the highest-cosine negative fragment in the five-item probe. |
| 339–388 | `AdjustSeekIndex`, `LazyInit`, `Setup`, `SetTokenSource`, two `GetTokens` overloads; docs for three members | One fragment intersects several implementation bodies and documentation ranges. It cannot have one exclusive method owner. |
| 374–416 | `SetTokenSource`, two `GetTokens` overloads; docs for the later overload | Includes the tail of `SetTokenSource`, full short overload bodies, and documentation/body from a later overload. |

## Cross-file mapping and parse scope

| File | Stored class-entity local spans | Full-file AST spans | Exact byte joins to extracted method entities | Parse error |
|---|---:|---:|---:|---|
| `BufferedTokenStream.cs` | 33/33 | 33/33 | 33/33 | no |
| `TokenStreamRewriter.cs` | 53 spans across four class entities | 44 unique methods | 44/44 | no |
| `UnbufferedTokenStream.cs` | 17/17 | 17/17 | 17/17 | no |
| `JsonTextReader.cs` | 6/73 from its stored class entity | 73 | 73/73 | yes |

The R4 C# side-facts parser independently reports method line ranges: 33, 44, 17 and 73 methods respectively, with the same parse-error flag on `JsonTextReader.cs`. It does not itself supply byte-accurate body or documentation spans. For the parse-error file, entity-local outline is incomplete because the class entity ends at an error-recovery boundary; the method entities remain present, and a full-file parser recovers their exact ranges. Therefore mappings should preserve both scope and provenance, and should mark unparsed bytes unknown instead of treating a partial class outline as exhaustive.

Warm full-file parse-plus-map medians in this debug scratch run: 7.99 ms for 21.6 KB; 10.11 ms for 29.0 KB; 3.36 ms for 12.3 KB; 32.22 ms for 101.7 KB. Full-file parsing was performed separately at query-like time; no file cache or AST reuse was included. Existing indexing already parses the full file once, and existing context construction reparses each stored entity for outline, so possible reuse seams exist, but neither integration nor full service cost is proven. Raw measurements: `csharp-mapping-entity-crosscheck-r4-v3.json` and `csharp-mapping-fullfile-crosscheck-r4-v3.json`.

The complete interval details and documentation overlaps are in `potion-diagnostic-a02-r4-v3.json::structural_mapping`.

## Expanded 48-file validation

A scratch-only exporter ran the exact R4 v3 extraction path on the 48 source-locked C# files already selected for the behavior study, then mapped method, body and preceding documentation ranges from class/interface entities. It emitted 995 entities and 1,351 physical fragment ranges. An independent deterministic validator rechecked every source SHA-256, every fragment's exact source bytes, the serialized R0 input hash, all mapped range bounds, and that every mapped body lies inside its method range; all checks passed.

There were 568 mapper instances because 20 nested methods are visible both through an outer type entity and through their own nested-type entity. After collapsing by `(path, absolute method byte range, name)`, the mapper produced 548 unique methods. These matched the 548 independently extracted structured function entities exactly by path, byte range, and name: 548/548, with no missing or extra methods. Preserve both owner projections as provenance, but deduplicate identical physical source ranges for ranking and scoring.

Four files carry a Tree-sitter parse-error flag (`JsonValidatingReader.cs`, `BsonWriter.cs`, `JsonConvert.cs`, and `JTokenWriter.cs`). Their extracted function ranges still matched the full-file mapper exactly in this sample. Keep the parse-error flag with each mapping and do not generalize this recovery result to other malformed files. The 48-file extraction-plus-mapping test took 0.57 seconds in the local debug test run; this includes source loading and is not an isolated parser benchmark or service-latency measurement.

Machine-readable validation receipt: `csharp-r4-v3-48-file-mapping-validation.json`. The full candidate/input export and scratch test source are under `.tmp/indexed-semantic-foundations-20261124/`; they contain no query labels or similarity scores.

## Extended behavior-study inventory

The current task-input export adds the eligible Dapper `ParameterTests.cs` and Newtonsoft `JsonConverter.cs` files, for 51 locked source files. All 51 SHA-256 values match the authoritative `source.lock.json` (`214298e7…`). R4 v3 extraction produced 1,178 entities and 1,591 physical fragments. The mapper emitted 699 owner projections; after collapsing repeated nested-owner projections, its 659 unique `(path, byte range, name, signature)` method ranges match all 659 extracted function entities exactly. There are 40 duplicate projections, no unmatched or missing function ranges, no invalid source slices, and no invalid method/body/doc bounds. Four parse-error files are present as distractors; preserve their error provenance in any representation receipt and do not treat parser recovery as independently verified semantics.

The local debug extraction-plus-map test completed in 0.68 seconds over these 51 files; this is not request latency or isolated parsing cost. Machine-readable receipt: `csharp-r4-v3-51-file-mapping-validation.json`. The 51-file set is a curated slice from three locked repositories, not an exhaustive repository mapping study.

## Limits and next validation

- **Owner and method overlap:** supported for the tested R4 v3 entities without reading current workspace files or changing index schema. Exact method ownership can be joined using the extracted method entity's range and metadata; a class-local outline may be incomplete when parse recovery splits the class.
- **Full/partial/multiple methods:** supported by half-open byte containment/intersection. A fragment can relate to multiple methods; no single `method_id` assumption is needed.
- **Declaration versus implementation:** the parser prototype distinguishes methods with an AST body from declarations without one. Constructors, nested types and overloads occur in the four-file corpus; properties/accessor bodies, local functions, expression-bodied members, CRLF/multibyte fixtures, and cross-file partial declarations still need explicit correctness tests.
- **Documentation relation:** preceding comment bytes can be mapped and associated with the method. Current metadata stores the documentation string but not this range. The prototype's matching follows the C# adapter's current `///`/`/**` association rule; broader language coverage and attributes need tests.
- **Properties/accessors and local functions:** the existing extractor gives the whole property an exact `Value` source range that includes accessor text. This method-only span mapper does not produce separate getter/setter ranges; a local function remains inside the parent method span and is not emitted as a separate member. Selection must preserve those as property-owned or nested/unknown until an accessor/local-function map is implemented.
- **Candidate-set relationships:** ranges and method scope let selection compare methods/ranges among already available candidate fragments. They cannot recover a missing entity or route fragment; this remains separate from acquisition/cutoff problems.
- **Source versions:** the maintained root HEAD lacks the C# grammar. The result only applies to the matched R4 v3 snapshot that produced the C# RAG traces. Resolve that implementation gap before proposing a product rollout.

Next: validate overloads, nested types, interface and abstract declarations, expression-bodied methods, attributes, partial classes, CRLF/multibyte source, parse errors, and fragments crossing method and documentation boundaries. Keep the mapping ephemeral in the experiment; do not promote or persist fields until the benchmark measures usefulness.
