# E3-01 v1 -> v2 errata and independent gold audit

## Why v2

The E3-02A source review found three evidence anchors in v1 that did not by
themselves prove the claimed flow. The annotations were not silently edited in
v1: v1 is preserved under `dataset/archive/v1/` and in commit `ae3aab2`.

| Query | v1 problem | v2 correction |
|---|---|---|
| `dapper-05-type-map-selection` | showed `GetTypeMap` and the `DefaultTypeMap` constructor, but not the default provider that connects them | added `SqlMapper.TypeMapProvider` line 3263; it is required evidence |
| `newtonsoft-11-property-name-flow` | showed only `mappedName = ...`, not the assignment into `JsonProperty.PropertyName` | added the assignment at `DefaultContractResolver.cs:1533`; both are required |
| `antlr-04-parser-recovery` | showed the default field and strategy, but not the call site where `Match` delegates to the strategy | added `Parser.cs:246` (`_errHandler.RecoverInline`); it is required |

No query text, category, hard negative, or absence regex changed in v2. No
retrieval result was used to choose these edits. The corpus source is unchanged.

## Freeze

- v1: `queries_sha256` `5e35c3d6...`, `corpus_sha256` `6f877914...`
- v2: `queries_sha256` `a2d2210e...`, `corpus_sha256` `ac8c5d80...`
- v1 archive: `dataset/archive/v1/` (queries, corpus, manifest)
- v2 current: `dataset/`; manifest is authoritative

## Independent review decisions

Every query, every anchor, every hard negative and all five no-answer cases were
read against the frozen source. No `zg` output, embedding similarity or ranking
was used. The audit used exact source ranges and executable absence patterns.

| Query | Cat | Answerable | Decision | Confidence | Review note |
|---|---|---:|---|---|---|
| `dapper-01-query-buffered` | exact_symbol | yes | PASS | high | PASS: Query<T> body selects ToList vs deferred; HN QueryFirst returns one row. |
| `dapper-02-column-name-map` | nl_implementation | yes | PASS | high | PASS: exact/case/underscore fallback in GetMember; global flag is supporting. |
| `dapper-03-string-param-size` | hardcoded_value | yes | PASS | high | PASS: Length==-1 path applies DefaultLength 4000 to short non-null strings; HN is ANSI flag. |
| `dapper-04-bypass-plan-cache` | configuration | yes | PASS | high | PASS: NoCache flag and AddToCache derivation; HN Pipelined is unrelated. |
| `dapper-05-type-map-selection` | cross_file | yes | PASS | high | PASS: GetTypeMap, DefaultTypeMap ctor and TypeMapProvider lambda all reviewed; v2 added provider anchor. |
| `dapper-06-literal-substitution` | sql_database | yes | PASS | high | PASS: public ReplaceLiterals, token parser and LiteralToken all reviewed. |
| `dapper-07-multimap-split-default` | hardcoded_value | yes | PASS | high | PASS: default splitOn literal Id; HN implementation consumes explicit value. |
| `dapper-08-async-cancellation` | data_flow | yes | PASS | high | PASS: QueryAsync captures command token and passes it to TryOpenAsync/reader; reader helper forwards it. |
| `dapper-09-buffered-false` | docs_vs_impl | yes | PASS | high | PASS: QueryImpl is iterator; Query<T> returns it when Buffered false; HN QuerySingle is wrong behavior. |
| `dapper-10-no-copy-encoding` | no_answer | no | PASS | high | PASS: absence regex over dapper/ has no binary COPY; provider tools excluded. |
| `dapper-12-no-connection-pool` | no_answer | no | PASS | high | PASS: no pool class/term in core; CommandTimeout HN is unrelated. |
| `newtonsoft-01-camel-case` | exact_symbol | yes | PASS | high | PASS: ResolvePropertyName camelCases; HN SnakeCase is a different strategy. |
| `newtonsoft-02-ignore-defaults` | nl_implementation | yes | PASS | high | PASS: Ignore flag + ShouldWriteProperty compares resolved default; HN null-only handling is wrong. |
| `newtonsoft-03-max-depth` | hardcoded_value | yes | PASS | high | PASS: DefaultMaxDepth=64 and getter fallback; HN date-format default is a different setting. |
| `newtonsoft-04-settings-to-contract` | configuration_flow | yes | PASS | high | PASS: setting getter, ApplySerializerSettings default/resolver, writer use all reviewed; broad class anchor removed in v1 repair. |
| `newtonsoft-05-enum-as-string` | serialization | yes | PASS | high | PASS: WriteJson resolves enum name and falls back to number; HN date converter is unrelated. |
| `newtonsoft-06-nan-infinity` | serialization | yes | PASS | high | PASS: FloatFormatHandling enum and JsonTextWriter float/double pass policy to JsonConvert. |
| `newtonsoft-07-linq-property-case` | linq | yes | PASS | high | PASS: Property(name, StringComparison) overload; exact overload HN does not. |
| `newtonsoft-08-iso-date-format` | hardcoded_value | yes | PASS | high | PASS: DefaultDateTimeFormat literal; HN kind-based format helper is not converter default. |
| `newtonsoft-09-preserve-references` | serialization | yes | PASS | high | PASS: policy enum and WriteReference/Id path; all anchors reviewed. |
| `newtonsoft-10-no-msgpack` | no_answer | no | PASS | high | PASS: no MessagePack term/type in frozen subset; BSON HN is different format. |
| `newtonsoft-12-no-yaml` | no_answer | no | PASS | high | PASS: no YAML term/type; XML converter HN is different format. |
| `antlr-01-adaptive-predict` | exact_symbol | yes | PASS | high | PASS: AdaptivePredict implementation; HN LL1Analyzer computes lookahead only. |
| `antlr-02-bail-on-error` | nl_implementation | yes | PASS | high | PASS: BailErrorStrategy.Recover sets exception and throws; HN default strategy recovers. |
| `antlr-03-invalid-state-number` | hardcoded_value | yes | PASS | high | PASS: sentinel -1 declaration; HN instance field uses it but is not the sentinel definition. |
| `antlr-04-parser-recovery` | cross_file | yes | PASS | high | PASS: default _errHandler, Match call into RecoverInline, ErrorHandler config, DefaultErrorStrategy.Recover; v2 added call anchor. |
| `antlr-05-atn-deserialize` | serialization | yes | PASS | high | PASS: Deserialize(int[]) entry point; source range checked. |
| `antlr-06-interval-complement` | data_structure | yes | PASS | high | PASS: Complement(IIntSet) implementation; range checked. |
| `antlr-07-multihop-error` | multi_hop | yes | PASS | high | PASS: inline recovery, rule-level Sync, and listener notification are separate anchors; claim no longer causal. |
| `antlr-08-no-left-recursion` | no_answer | no | PASS | high | PASS: no transformer/elimination terms in runtime subset; runtime prediction helpers are not grammar rewriting. |
| `dapper-11-parameter-flow` | multi_hop | yes | PASS | high | PASS: Add stores ParamInfo; AddParameters projects values to command; all three anchors checked. |
| `newtonsoft-11-property-name-flow` | multi_hop | yes | PASS | high | PASS: attribute property, mappedName, contract assignment, writer name emission; v2 added assignment anchor. |

## Hard negatives

All 20 hard negatives were checked for path/range overlap with evidence and for
being behaviorally wrong rather than merely nearby. No hard negative is counted
as relevant. The adversarial scorer fixture covers trap hits and unrelated
results.

## Disputed / unknown cases

None. Three v1 evidence gaps were fixed in v2; no query is marked DISPUTED.
Residual risk is annotation judgment, not an unresolved source contradiction.
