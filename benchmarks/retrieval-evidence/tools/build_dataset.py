#!/usr/bin/env python3
"""Build the frozen E3-01 evidence dataset from third-party source only.

This tool never calls zg or any retrieval engine.  Queries and anchors were
written from source reading; `--source` points at the external corpus checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

DATASET_VERSION = "e3-01-v1"
SCHEMA_VERSION = 1
FROZEN_AT = "2026-11-12"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_line(text: str, marker: str) -> int:
    idx = text.find(marker)
    if idx < 0:
        raise SystemExit(f"marker not found: {marker!r}")
    return text.count("\n", 0, idx) + 1


def block_end(lines: list[str], start: int) -> int:
    """Return 1-based inclusive end of the brace block beginning at/after start."""
    in_block = False
    depth = 0
    in_line_comment = False
    in_block_comment = False
    in_string = False
    in_char = False
    escaped = False
    for number in range(start - 1, len(lines)):
        line = lines[number]
        i = 0
        while i < len(line):
            ch = line[i]
            nxt = line[i + 1] if i + 1 < len(line) else ""
            if in_line_comment:
                break
            if in_block_comment:
                if ch == "*" and nxt == "/":
                    in_block_comment = False
                    i += 2
                else:
                    i += 1
                continue
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                i += 1
                continue
            if in_char:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == "'":
                    in_char = False
                i += 1
                continue
            if ch == "/" and nxt == "/":
                in_line_comment = True
                break
            if ch == "/" and nxt == "*":
                in_block_comment = True
                i += 2
                continue
            if ch == '"':
                in_string = True
                i += 1
                continue
            if ch == "'":
                in_char = True
                i += 1
                continue
            if ch == "{":
                in_block = True
                depth += 1
            elif ch == "}" and in_block:
                depth -= 1
                if depth == 0:
                    return number + 1
            i += 1
        in_line_comment = False
    raise SystemExit(f"unterminated block starting at line {start}")


def make_anchor(
    source: Path,
    path: str,
    marker: str,
    symbol: str,
    role: str,
    rationale: str,
    group: str = "primary",
    confidence: str = "high",
    scope: str = "block",
) -> dict:
    if group == "primary" and role == "supporting":
        group = "supporting"
    full = source / path
    text = full.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = find_line(text, marker)
    try:
        end = start if scope == "line" else block_end(lines, start)
    except SystemExit as error:
        raise SystemExit(f"{path}: {symbol}: {error}") from error
    content = "\n".join(lines[start - 1 : end]) + "\n"
    return {
        "group": group,
        "path": path,
        "start_line": start,
        "end_line": end,
        "symbol": symbol,
        "role": role,
        "rationale": rationale,
        "confidence": confidence,
        "content_sha256": sha256_bytes(content.encode("utf-8")),
        "preview": content[:240].replace("\n", " "),
    }


def negative(
    source: Path, path: str, marker: str, symbol: str, why: str, scope: str = "block"
) -> dict:
    anchor = make_anchor(
        source,
        path,
        marker,
        symbol,
        role="hard_negative",
        rationale=why,
        group="hard_negative",
        confidence="high",
        scope=scope,
    )
    return anchor


def infer_leakage(question: str, anchors: list[dict], negatives: list[dict]) -> str:
    tokens = set()
    for anchor in anchors + negatives:
        stem = Path(anchor["path"]).stem
        tokens.add(stem)
        if stem.endswith("Attribute"):
            tokens.add(stem[: -len("Attribute")])
        for part in anchor["symbol"].split("."):
            head = part.split("(")[0].strip()
            tokens.add(head)
            if "<" in head:
                tokens.add(head)
                tokens.add(head.split("<")[0] + "<>")
    return "identifier" if any(len(token) >= 4 and token in question for token in tokens) else "none"


def q(
    qid: str,
    repo: str,
    category: str,
    question: str,
    intent: str,
    anchors: list[dict],
    *,
    answerable: bool = True,
    leakage: str = "auto",
    required_groups: list[str] | None = None,
    hard_negatives: list[dict] | None = None,
    absence_checks: list[dict] | None = None,
    notes: str = "",
) -> dict:
    if answerable:
        for index, anchor in enumerate(anchors, start=1):
            anchor["id"] = f"{qid}-e{index}"
        for index, anchor in enumerate(hard_negatives or [], start=1):
            anchor["id"] = f"{qid}-n{index}"
        groups = required_groups or sorted({a["group"] for a in anchors})
        solved = {
            "required_groups": groups,
            "required_anchor_ids": [a["id"] for a in anchors],
            "min_primary_evidence": 1,
        }
    else:
        for index, anchor in enumerate(hard_negatives or [], start=1):
            anchor["id"] = f"{qid}-n{index}"
        solved = {"required_groups": [], "min_primary_evidence": 0, "max_false_positives": 0}
    return {
        "id": qid,
        "corpus": repo,
        "corpus_revision": DATASET_VERSION,
        "category": category,
        "question": question,
        "intent": intent,
        "answerable": answerable,
        "leakage": (
            infer_leakage(question, anchors, hard_negatives or [])
            if leakage == "auto"
            else leakage
        ),
        "evidence": anchors,
        "hard_negatives": hard_negatives or [],
        "absence_checks": absence_checks or [],
        "solved_when": solved,
        "annotation": {
            "provenance": "source-first manual annotation; no zg results consulted",
            "confidence": "high" if answerable else "high",
            "review": "second-pass source review",
        },
        "notes": notes,
    }


def corpus_manifest(source: Path) -> dict:
    repos = [
        {
            "id": "dapper",
            "url": "https://github.com/DapperLib/Dapper",
            "revision": "eb47546a408bbf6c178bda4f04dc205bc51ffbfe",
            "tag": "2.1.89",
            "license": "Apache-2.0",
            "license_path": "dapper/License.txt",
            "subset": "Dapper/",
            "excluded": "tests, benchmark projects, EF/Rainbow/SqlBuilder packages",
            "tarball_sha256": "2bec491ea26ac33dfa1c8f3f3b928b7f0c50c152066c2947a2432985aa81a7c4",
        },
        {
            "id": "newtonsoft",
            "url": "https://github.com/JamesNK/Newtonsoft.Json",
            "revision": "0a2e291c0d9c0c7675d445703e51750363a549ef",
            "tag": "13.0.3",
            "license": "MIT",
            "license_path": "newtonsoft/LICENSE.md",
            "subset": "Newtonsoft.Json/{Serialization,Linq,Utilities,Converters,*.cs}",
            "excluded": "tests, benchmarks, documentation, BSON/Schema",
            "tarball_sha256": "d88190702cc9b27c51dd1075b2773479f5f91e8256e73579beaa6cedf659a388",
        },
        {
            "id": "antlr",
            "url": "https://github.com/antlr/antlr4",
            "revision": "2ac185af855ff39b4b8c41854b756767099c356f",
            "tag": "4.13.2",
            "license": "BSD-3-Clause",
            "license_path": "antlr/LICENSE.txt",
            "subset": "runtime/CSharp/src/{Atn,Misc,*.cs}",
            "excluded": "tests, tool/generator, other language runtimes",
            "tarball_sha256": "05d8404b6f732b55d4beaf0d95b4eb1761d29be672a1da030c553f105b8d9d66",
        },
    ]
    files = []
    for path in sorted(source.rglob("*.cs")):
        rel = path.relative_to(source).as_posix()
        data = path.read_bytes()
        files.append(
            {
                "path": rel,
                "sha256": sha256_bytes(data),
                "bytes": len(data),
                "lines": data.count(b"\n") + (0 if data.endswith(b"\n") else 1),
            }
        )
    return {
        "name": "e3-01-independent-retrieval-evidence",
        "version": DATASET_VERSION,
        "schema_version": SCHEMA_VERSION,
        "frozen_at": FROZEN_AT,
        "repos": repos,
        "files": files,
        "counts": {
            "files": len(files),
            "lines": sum(item["lines"] for item in files),
            "repos": len(repos),
        },
    }


def build_queries(source: Path) -> list[dict]:
    A = lambda *args, **kwargs: make_anchor(source, *args, **kwargs)
    N = lambda *args, **kwargs: negative(source, *args, **kwargs)
    queries = []

    # ---------------- Dapper ----------------
    queries.append(q(
        "dapper-01-query-buffered", "dapper", "exact_symbol",
        "Find the synchronous Query<T> overload that materializes a plain SQL result set and returns it as a sequence, with the buffered flag controlling eager consumption.",
        "Locate the implementation entry point an agent would open to understand Query<T> buffering.",
        [A("dapper/Dapper/SqlMapper.cs", "public static IEnumerable<T> Query<T>(this IDbConnection cnn, string sql", "SqlMapper.Query<T>(string)", "primary", "Declares the buffered Query<T> overload and forwards to QueryImpl.")],
        leakage="identifier",
        hard_negatives=[N("dapper/Dapper/SqlMapper.cs", "public static T QueryFirst<T>(this IDbConnection cnn, string sql", "SqlMapper.QueryFirst<T>", "Returns a single row; it does not materialize the full buffered sequence.")],
        notes="The API name is intentionally part of the task; keep separate from natural-language queries."
    ))
    queries.append(q(
        "dapper-02-column-name-map", "dapper", "nl_implementation",
        "When a database column name differs from the C# member only by letter case or underscores, how does row materialization still find the right member?",
        "Find the case/underscore fallback used by the default type map.",
        [
            A("dapper/Dapper/DefaultTypeMap.cs", "public SqlMapper.IMemberMap? GetMember(string columnName)", "DefaultTypeMap.GetMember", "primary", "Implements exact, case-insensitive, underscore and suffix matching for a column."),
            A("dapper/Dapper/DefaultTypeMap.cs", "public static bool MatchNamesWithUnderscores", "DefaultTypeMap.MatchNamesWithUnderscores", "supporting", "Controls the underscore fallback.", scope="line"),
        ],
        hard_negatives=[N("dapper/Dapper/CustomPropertyTypeMap.cs", "public SqlMapper.IMemberMap? GetMember(string columnName)", "CustomPropertyTypeMap.GetMember", "Explicit property map path; does not implement the default name fallback.")],
    ))
    queries.append(q(
        "dapper-03-string-param-size", "dapper", "hardcoded_value",
        "If a caller wraps a short string in DbString without setting a length, what size is assigned to the provider parameter, and which constant controls it?",
        "Extract the default string parameter size behavior.",
        [
            A("dapper/Dapper/DbString.cs", "if (Length == -1 && Value is not null && Value.Length <= DefaultLength)", "DbString.AddParameters default size", "primary", "Uses DefaultLength when no explicit length was supplied and the value fits."),
            A("dapper/Dapper/DbString.cs", "public const int DefaultLength = 4000", "DbString.DefaultLength", "supporting", "Defines the concrete default size.", scope="line"),
        ],
        hard_negatives=[N("dapper/Dapper/DbString.cs", "public static bool IsAnsiDefault", "DbString.IsAnsiDefault", "Selects ANSI encoding; it does not set the default parameter size.", scope="line")],
    ))
    queries.append(q(
        "dapper-04-bypass-plan-cache", "dapper", "configuration",
        "Which command flag prevents a query from using the cached execution plan?",
        "Locate the public flag and the cache-bypass behavior.",
        [
            A("dapper/Dapper/CommandFlags.cs", "NoCache = 4", "CommandFlags.NoCache", "primary", "Declares the cache-bypass flag.", scope="line"),
            A("dapper/Dapper/CommandDefinition.cs", "internal bool AddToCache => (Flags & CommandFlags.NoCache) == 0;", "CommandDefinition.AddToCache", "supporting", "Derives cache eligibility from the NoCache flag.", scope="line"),
        ],
        hard_negatives=[N("dapper/Dapper/CommandFlags.cs", "Pipelined = 2", "CommandFlags.Pipelined", "Controls async pipelining, not the plan cache.", scope="line")],
    ))
    queries.append(q(
        "dapper-05-type-map-selection", "dapper", "cross_file",
        "How does a row-materialization call decide which ITypeMap to use, and where is the default implementation?",
        "Connect the public type-map lookup to the default mapper.",
        [
            A("dapper/Dapper/SqlMapper.cs", "public static ITypeMap GetTypeMap(Type type)", "SqlMapper.GetTypeMap", "primary", "Resolves an explicit or default type map."),
            A("dapper/Dapper/DefaultTypeMap.cs", "public DefaultTypeMap(Type type)", "DefaultTypeMap(Type)", "supporting", "Constructs the default mapper for a CLR type."),
        ],
        leakage="identifier",
    ))
    queries.append(q(
        "dapper-06-literal-substitution", "dapper", "sql_database",
        "How are literal placeholders such as {=name} replaced before a command is executed?",
        "Find the literal-token parser and replacement routine.",
        [
            A("dapper/Dapper/SqlMapper.cs", "public static void ReplaceLiterals(this IParameterLookup parameters, IDbCommand command)", "SqlMapper.ReplaceLiterals", "primary", "Public replacement entry point."),
            A("dapper/Dapper/SqlMapper.cs", "internal static IList<LiteralToken> GetLiteralTokens(string sql)", "SqlMapper.GetLiteralTokens", "supporting", "Parses literal tokens from SQL."),
            A("dapper/Dapper/SqlMapper.LiteralToken.cs", "internal readonly struct LiteralToken", "LiteralToken", "supporting", "Carries the parsed token and literal text."),
        ],
    ))
    queries.append(q(
        "dapper-07-multimap-split-default", "dapper", "hardcoded_value",
        "What column name does multi-mapping use by default to split a joined row into the first and second objects?",
        "Identify the default splitOn value in the public multi-map overload.",
        [A("dapper/Dapper/SqlMapper.cs", 'string splitOn = "Id"', "SqlMapper.Query<TFirst,TSecond,TReturn> splitOn", "primary", "The default split column is the literal Id.", scope="line")],
        hard_negatives=[N("dapper/Dapper/SqlMapper.cs", "private static IEnumerable<TReturn> MultiMapImpl<TFirst, TSecond, TThird", "SqlMapper.MultiMapImpl", "Consumes an explicit splitOn value; it does not define the default.")],
    ))
    queries.append(q(
        "dapper-08-async-cancellation", "dapper", "data_flow",
        "When an async command supplies a cancellation token, where is it passed into ADO.NET opening and reader operations?",
        "Trace the cancellation token through the async execution path.",
        [
            A("dapper/Dapper/SqlMapper.Async.cs", "private static async Task<IEnumerable<T>> QueryAsync<T>(this IDbConnection cnn", "SqlMapper.QueryAsync<T>", "primary", "Captures the command token and passes it to connection opening and reader calls.", group="query_path"),
            A("dapper/Dapper/SqlMapper.Async.cs", "private static Task<DbDataReader> ExecuteReaderWithFlagsFallbackAsync", "SqlMapper.ExecuteReaderWithFlagsFallbackAsync", "supporting", "Forwards the token to DbCommand.ExecuteReaderAsync.", group="reader_call"),
        ],
        required_groups=["query_path", "reader_call"],
        notes="The supporting anchor is a single call-site line; the evaluator should match the containing candidate, not require the line itself to be a chunk."
    ))
    queries.append(q(
        "dapper-09-buffered-false", "dapper", "docs_vs_impl",
        "What actually happens when Query<T> is called with buffered set to false, as opposed to the eager path?",
        "Distinguish the deferred iterator from the eager implementation.",
        [
            A("dapper/Dapper/SqlMapper.cs", "private static IEnumerable<T> QueryImpl<T>(this IDbConnection cnn, CommandDefinition command, Type effectiveType)", "SqlMapper.QueryImpl<T>", "primary", "Chooses between eager and deferred row consumption.", group="implementation"),
            A("dapper/Dapper/SqlMapper.cs", "return command.Buffered ? data.ToList() : data;", "SqlMapper.Query<T> buffered branch", "supporting", "The buffered flag selects eager ToList materialization versus the deferred iterator.", group="selection", scope="line"),
        ],
        leakage="identifier",
        hard_negatives=[N("dapper/Dapper/SqlMapper.cs", "public static T QuerySingle<T>(this IDbConnection cnn, string sql", "SqlMapper.QuerySingle<T>", "Returns one row eagerly; it is not the deferred full-result behavior controlled by buffered=false.")],
    ))
    queries.append(q(
        "dapper-10-no-copy-encoding", "dapper", "no_answer",
        "Does this corpus implement PostgreSQL binary COPY encoding or a COPY stream writer for bulk export?",
        "Check that Dapper's included core does not contain the out-of-scope provider-tools COPY implementation.",
        [],
        answerable=False,
        hard_negatives=[N("dapper/Dapper/DbString.cs", "param.DbType = IsAnsi", "DbString.DbType assignment", "Selects string DbType values; it is not binary COPY encoding.")],
        absence_checks=[
            {"pattern": r"BinaryCopy|COPY\s+.*BINARY|NpgsqlBinaryImporter", "scope": "dapper/", "expected_matches": 0},
            {"pattern": r"class\s+\w*Copy\w*", "scope": "dapper/", "expected_matches": 0},
        ],
        notes="ProviderTools was deliberately excluded; absence is scoped to the frozen corpus, not the upstream project."
    ))


    queries.append(q(
        "dapper-12-no-connection-pool", "dapper", "no_answer",
        "Does Dapper's core implement connection pooling or manage a reusable pool of ADO.NET connections?",
        "Check that pooling remains an ADO.NET provider concern and is absent from the frozen core.",
        [],
        answerable=False,
        hard_negatives=[N("dapper/Dapper/SqlMapper.Settings.cs", "public static int? CommandTimeout", "SqlMapper.Settings.CommandTimeout", "Configures command timeout; it does not pool or own connections.", scope="line")],
        absence_checks=[
            {"pattern": r"ConnectionPool|DbConnectionPool|PooledConnection|ConnectionPooling", "scope": "dapper/", "expected_matches": 0},
            {"pattern": r"class\s+\w*Pool\w*", "scope": "dapper/", "expected_matches": 0},
        ],
        notes="Absence is scoped to Dapper's frozen core; provider packages were excluded."
    ))

    # ---------------- Newtonsoft.Json ----------------
    queries.append(q(
        "newtonsoft-01-camel-case", "newtonsoft", "exact_symbol",
        "Which method turns a C# property name into camelCase when the camel-case naming strategy is selected?",
        "Locate the naming strategy override.",
        [A("newtonsoft/Newtonsoft.Json/Serialization/CamelCaseNamingStrategy.cs", "protected override string ResolvePropertyName(string name)", "CamelCaseNamingStrategy.ResolvePropertyName", "primary", "Implements the camelCase property-name transform.")],
        hard_negatives=[N("newtonsoft/Newtonsoft.Json/Serialization/SnakeCaseNamingStrategy.cs", "protected override string ResolvePropertyName(string name)", "SnakeCaseNamingStrategy.ResolvePropertyName", "Produces snake_case, not camelCase.")],
    ))
    queries.append(q(
        "newtonsoft-02-ignore-defaults", "newtonsoft", "nl_implementation",
        "How can a serializer omit members whose value equals the C# default, and where is that decision made while writing an object?",
        "Find the setting and the writer-side value comparison.",
        [
            A("newtonsoft/Newtonsoft.Json/DefaultValueHandling.cs", "Ignore = 1", "DefaultValueHandling.Ignore", "primary", "Public flag for omitting default-valued members.", group="setting", scope="line"),
            A("newtonsoft/Newtonsoft.Json/Serialization/JsonSerializerInternalWriter.cs", "private bool ShouldWriteProperty(object? memberValue", "JsonSerializerInternalWriter.ShouldWriteProperty", "supporting", "Compares the member value with its resolved default before writing.", group="writer"),
        ],
        hard_negatives=[N("newtonsoft/Newtonsoft.Json/NullValueHandling.cs", "Ignore = 1", "NullValueHandling.Ignore", "Omits nulls only; does not compare arbitrary CLR defaults.", scope="line")],
        required_groups=["setting", "writer"],
    ))
    queries.append(q(
        "newtonsoft-03-max-depth", "newtonsoft", "hardcoded_value",
        "What is the serializer's default maximum nesting depth when the caller does not override it?",
        "Extract the built-in default depth constant.",
        [
            A("newtonsoft/Newtonsoft.Json/JsonSerializerSettings.cs", "internal const int DefaultMaxDepth = 64", "JsonSerializerSettings.DefaultMaxDepth", "primary", "The default nesting limit is 64.", scope="line"),
            A("newtonsoft/Newtonsoft.Json/JsonSerializerSettings.cs", "get => _maxDepthSet ? _maxDepth : DefaultMaxDepth", "JsonSerializerSettings.MaxDepth", "supporting", "Uses the constant only when no explicit depth was set."),
        ],
        hard_negatives=[N("newtonsoft/Newtonsoft.Json/JsonSerializerSettings.cs", "internal const string DefaultDateFormatString", "JsonSerializerSettings.DefaultDateFormatString", "Another serializer default; it is a date format, not the depth limit.", scope="line")],
    ))
    queries.append(q(
        "newtonsoft-04-settings-to-contract", "newtonsoft", "configuration_flow",
        "How do serializer settings such as the contract resolver and default value handling reach the object being serialized?",
        "Trace settings into the serializer and contract resolver.",
        [
            A("newtonsoft/Newtonsoft.Json/JsonSerializerSettings.cs", "public DefaultValueHandling DefaultValueHandling", "JsonSerializerSettings.DefaultValueHandling", "primary", "Public setting that distinguishes an omitted value from an explicit default.", group="setting"),
            A("newtonsoft/Newtonsoft.Json/JsonSerializer.cs", "serializer.DefaultValueHandling = settings.DefaultValueHandling;", "JsonSerializer.ApplySerializerSettings default handling", "supporting", "Applies the setting only when the settings object explicitly supplied it.", group="apply", scope="line"),
            A("newtonsoft/Newtonsoft.Json/JsonSerializer.cs", "serializer.ContractResolver = settings.ContractResolver;", "JsonSerializer.ApplySerializerSettings contract resolver", "supporting", "Applies the configured contract resolver.", group="apply_resolver", scope="line"),
            A("newtonsoft/Newtonsoft.Json/Serialization/JsonSerializerInternalWriter.cs", "private bool ShouldWriteProperty(object? memberValue", "JsonSerializerInternalWriter.ShouldWriteProperty", "supporting", "Writer reads the resolved default handling during output.", group="writer"),
        ],
        required_groups=["setting", "apply", "apply_resolver", "writer"],
    ))
    queries.append(q(
        "newtonsoft-05-enum-as-string", "newtonsoft", "serialization",
        "How is an enum written as its symbolic name rather than its numeric value?",
        "Locate the converter that serializes enum names.",
        [A("newtonsoft/Newtonsoft.Json/Converters/StringEnumConverter.cs", "public override void WriteJson(JsonWriter writer, object? value, JsonSerializer serializer)", "StringEnumConverter.WriteJson", "primary", "Writes the resolved enum name, falling back to numeric output when configured.")],
        hard_negatives=[N("newtonsoft/Newtonsoft.Json/Converters/IsoDateTimeConverter.cs", "public class IsoDateTimeConverter", "IsoDateTimeConverter", "Serializes dates, not enum names.")],
    ))
    queries.append(q(
        "newtonsoft-06-nan-infinity", "newtonsoft", "serialization",
        "What happens when a float or double being written is NaN or infinity, and which setting controls the output form?",
        "Trace the float writer and the FloatFormatHandling policy.",
        [
            A("newtonsoft/Newtonsoft.Json/FloatFormatHandling.cs", "public enum FloatFormatHandling", "FloatFormatHandling", "primary", "Defines string/symbol/null handling for non-finite floats."),
            A("newtonsoft/Newtonsoft.Json/JsonTextWriter.cs", "public override void WriteValue(float value)", "JsonTextWriter.WriteValue(float)", "supporting", "Applies the configured float format while writing."),
        ],
    ))
    queries.append(q(
        "newtonsoft-07-linq-property-case", "newtonsoft", "linq",
        "How can a JObject child property be looked up with a case-insensitive comparison?",
        "Find the JObject property overload with StringComparison.",
        [A("newtonsoft/Newtonsoft.Json/Linq/JObject.cs", "public JProperty? Property(string name, StringComparison comparison)", "JObject.Property(string,StringComparison)", "primary", "Supports caller-selected string comparison for property lookup.")],
        hard_negatives=[N("newtonsoft/Newtonsoft.Json/Linq/JObject.cs", "public JProperty? Property(string name)", "JObject.Property(string)", "Ordinal/exact overload; it does not expose case-insensitive comparison.")],
    ))
    queries.append(q(
        "newtonsoft-08-iso-date-format", "newtonsoft", "hardcoded_value",
        "What exact format string does the ISO date converter use when no custom format is supplied?",
        "Extract the default ISO date format literal.",
        [A("newtonsoft/Newtonsoft.Json/Converters/IsoDateTimeConverter.cs", "private const string DefaultDateTimeFormat", "IsoDateTimeConverter.DefaultDateTimeFormat", "primary", "Defines the round-trip ISO format including fractional seconds and K.", scope="line")],
        hard_negatives=[N("newtonsoft/Newtonsoft.Json/Utilities/DateTimeUtils.cs", "public static string ToDateTimeFormat(DateTimeKind kind)", "DateTimeUtils.ToDateTimeFormat", "Chooses a DateTimeKind suffix helper; it is not the converter's default format string.")],
    ))
    queries.append(q(
        "newtonsoft-09-preserve-references", "newtonsoft", "serialization",
        "How are object references preserved during serialization so that repeated objects become identifiers instead of duplicated JSON?",
        "Find the reference-writing path and its settings flag.",
        [
            A("newtonsoft/Newtonsoft.Json/PreserveReferencesHandling.cs", "public enum PreserveReferencesHandling", "PreserveReferencesHandling", "primary", "Declares the reference-preservation policy."),
            A("newtonsoft/Newtonsoft.Json/Serialization/JsonSerializerInternalWriter.cs", "private void WriteReference(JsonWriter writer, object value)", "JsonSerializerInternalWriter.WriteReference", "supporting", "Writes the reference marker for an already-seen object."),
            A("newtonsoft/Newtonsoft.Json/Serialization/JsonSerializerInternalWriter.cs", "private void WriteReferenceIdProperty", "JsonSerializerInternalWriter.WriteReferenceIdProperty", "supporting", "Writes the id metadata for the first occurrence."),
        ],
    ))
    queries.append(q(
        "newtonsoft-10-no-msgpack", "newtonsoft", "no_answer",
        "Does this corpus contain a MessagePack serializer or MessagePack wire-format support?",
        "Verify that only JSON/BSON-oriented serialization is present in the selected subset.",
        [],
        answerable=False,
        hard_negatives=[N("newtonsoft/Newtonsoft.Json/Converters/BsonObjectIdConverter.cs", "public class BsonObjectIdConverter", "BsonObjectIdConverter", "BSON object-id support, not MessagePack.")],
        absence_checks=[
            {"pattern": r"MessagePack|msgpack", "scope": "newtonsoft/", "expected_matches": 0},
            {"pattern": r"class\s+\w*MsgPack\w*", "scope": "newtonsoft/", "expected_matches": 0},
        ],
        notes="BSON exists in the wider project; the selected frozen subset contains no MessagePack implementation."
    ))


    queries.append(q(
        "newtonsoft-12-no-yaml", "newtonsoft", "no_answer",
        "Does this corpus contain a YAML serializer or YAML wire-format support?",
        "Verify that the selected Newtonsoft subset supports JSON-family formats but not YAML.",
        [],
        answerable=False,
        hard_negatives=[N("newtonsoft/Newtonsoft.Json/Converters/XmlNodeConverter.cs", "public class XmlNodeConverter : JsonConverter", "XmlNodeConverter", "Serializes XML nodes; it is not YAML support.")],
        absence_checks=[
            {"pattern": r"Yaml|YAML|YamlDotNet", "scope": "newtonsoft/", "expected_matches": 0},
            {"pattern": r"class\s+\w*Yaml\w*", "scope": "newtonsoft/", "expected_matches": 0},
        ],
        notes="Absence is scoped to the frozen subset; YAML support would require another package."
    ))

    # ---------------- ANTLR runtime ----------------
    queries.append(q(
        "antlr-01-adaptive-predict", "antlr", "exact_symbol",
        "Where does the parser simulator choose the next alternative using adaptive prediction?",
        "Locate the AdaptivePredict implementation.",
        [A("antlr/Runtime/Atn/ParserATNSimulator.cs", "public virtual int AdaptivePredict(ITokenStream input, int decision", "ParserATNSimulator.AdaptivePredict", "primary", "Implements adaptive alternative selection.")],
        hard_negatives=[N("antlr/Runtime/Atn/LL1Analyzer.cs", "public class LL1Analyzer", "LL1Analyzer", "Computes lookahead sets; it does not select the next alternative.")],
    ))
    queries.append(q(
        "antlr-02-bail-on-error", "antlr", "nl_implementation",
        "Which error strategy aborts parsing immediately instead of attempting recovery, and how does it do that?",
        "Find the bail strategy's Recover override.",
        [A("antlr/Runtime/BailErrorStrategy.cs", "public override void Recover(Parser recognizer, RecognitionException e)", "BailErrorStrategy.Recover", "primary", "Consumes until the rule start and throws a cancellation-style exception.")],
        hard_negatives=[N("antlr/Runtime/DefaultErrorStrategy.cs", "public virtual void Recover(Parser recognizer, RecognitionException e)", "DefaultErrorStrategy.Recover", "Performs recovery rather than aborting.")],
    ))
    queries.append(q(
        "antlr-03-invalid-state-number", "antlr", "hardcoded_value",
        "What sentinel state number marks an ATN state as invalid or unassigned?",
        "Extract the ATNState constant.",
        [A("antlr/Runtime/Atn/ATNState.cs", "public const int InvalidStateNumber = -1", "ATNState.InvalidStateNumber", "primary", "Uses -1 as the invalid state sentinel.", scope="line")],
        hard_negatives=[N("antlr/Runtime/Atn/ATNState.cs", "public int stateNumber = InvalidStateNumber;", "ATNState.stateNumber", "An instance field initialized from the sentinel; it is not the sentinel declaration.", scope="line")],
    ))
    queries.append(q(
        "antlr-04-parser-recovery", "antlr", "cross_file",
        "When generated parser code encounters a recognition error, how is recovery delegated to the configured strategy, and which strategy is installed by default?",
        "Connect parser error-handler delegation to the default strategy without claiming a Parser.Recover method.",
        [
            A("antlr/Runtime/Parser.cs", "private IAntlrErrorStrategy _errHandler = new DefaultErrorStrategy();", "Parser._errHandler", "primary", "Installs DefaultErrorStrategy when a parser is constructed.", group="default_strategy", scope="line"),
            A("antlr/Runtime/Parser.cs", "public virtual IAntlrErrorStrategy ErrorHandler", "Parser.ErrorHandler", "supporting", "Exposes the strategy used by generated parser code and allows replacement.", group="delegation"),
            A("antlr/Runtime/DefaultErrorStrategy.cs", "public virtual void Recover(Parser recognizer, RecognitionException e)", "DefaultErrorStrategy.Recover", "supporting", "Implements the default rule-level recovery behavior.", group="recovery_behavior"),
        ],
        required_groups=["default_strategy", "delegation", "recovery_behavior"],
    ))
    queries.append(q(
        "antlr-05-atn-deserialize", "antlr", "serialization",
        "How is a serialized ATN represented as integer data turned back into runtime ATN states and rules?",
        "Locate the ATN deserialization entry point.",
        [A("antlr/Runtime/Atn/ATNDeserializer.cs", "public virtual ATN Deserialize(int[] data)", "ATNDeserializer.Deserialize", "primary", "Reads the serialized integer stream into an ATN.")],
    ))
    queries.append(q(
        "antlr-06-interval-complement", "antlr", "data_structure",
        "How does IntervalSet compute the complement of a token or character range?",
        "Find the complement implementation.",
        [A("antlr/Runtime/Misc/IntervalSet.cs", "public virtual Antlr4.Runtime.Misc.IntervalSet Complement(IIntSet vocabulary)", "IntervalSet.Complement", "primary", "Computes the complement against a vocabulary set.")],
    ))
    queries.append(q(
        "antlr-07-multihop-error", "antlr", "multi_hop",
        "Contrast the two error-recovery paths: how inline recovery supplies a missing token, and how rule-level recovery resynchronizes input and reports diagnostics.",
        "Requires the inline recovery path, rule-level synchronization, and listener notification as separate pieces, not one causal chain.",
        [
            A("antlr/Runtime/DefaultErrorStrategy.cs", "public virtual IToken RecoverInline(Parser recognizer)", "DefaultErrorStrategy.RecoverInline", "primary", "Produces a synthetic missing token or throws.", group="inline_recovery"),
            A("antlr/Runtime/DefaultErrorStrategy.cs", "public virtual void Sync(Parser recognizer)", "DefaultErrorStrategy.Sync", "supporting", "Rule-level recovery consumes tokens until the follow set can continue.", group="rule_sync"),
            A("antlr/Runtime/Parser.cs", "public virtual void NotifyErrorListeners(IToken offendingToken, string msg, RecognitionException e)", "Parser.NotifyErrorListeners", "supporting", "Dispatches diagnostics independently of the recovery path.", group="diagnostics"),
        ],
        required_groups=["inline_recovery", "rule_sync", "diagnostics"],
    ))
    queries.append(q(
        "antlr-08-no-left-recursion", "antlr", "no_answer",
        "Does the C# runtime in this corpus eliminate left recursion or rewrite grammar rules?",
        "Verify that left-recursion elimination belongs to the tool/generator, not the selected runtime.",
        [],
        answerable=False,
        hard_negatives=[N("antlr/Runtime/Atn/ParserATNSimulator.cs", "public virtual int AdaptivePredict", "ParserATNSimulator.AdaptivePredict", "Predicts alternatives at runtime; it does not rewrite left-recursive grammar rules.")],
        absence_checks=[
            {"pattern": r"eliminateLeftRecursion|LeftRecursiveRuleTransformer|LeftRecursiveRuleAnalyzer|rewriteLeftRecursion", "scope": "antlr/", "expected_matches": 0},
        ],
        notes="Generator/tool sources were excluded; absence is scoped to the frozen runtime subset."
    ))


    queries.append(q(
        "dapper-11-parameter-flow", "dapper", "multi_hop",
        "Trace how a named value added to DynamicParameters becomes provider parameters, including type and size handling.",
        "Requires the public Add path, ParamInfo storage, and AddParameters projection.",
        [
            A("dapper/Dapper/DynamicParameters.cs", "public void Add(string name, object? value = null, DbType? dbType = null", "DynamicParameters.Add", "primary", "Stores a named value and optional provider metadata.", group="parameter_store"),
            A("dapper/Dapper/DynamicParameters.ParamInfo.cs", "private sealed class ParamInfo", "DynamicParameters.ParamInfo", "supporting", "Holds the value, DbType, direction and size.", group="parameter_metadata"),
            A("dapper/Dapper/DynamicParameters.cs", "protected void AddParameters(IDbCommand command, SqlMapper.Identity identity)", "DynamicParameters.AddParameters", "supporting", "Projects stored values into the provider command.", group="command_projection"),
        ],
        required_groups=["parameter_store", "parameter_metadata", "command_projection"],
    ))
    # Extra multi-hop coverage in Newtonsoft.
    queries.append(q(
        "newtonsoft-11-property-name-flow", "newtonsoft", "multi_hop",
        "Trace how a JsonProperty attribute's explicit property name reaches the contract used by the writer.",
        "Requires attribute metadata, contract-property creation, and writer name emission.",
        [
            A("newtonsoft/Newtonsoft.Json/JsonPropertyAttribute.cs", "public string? PropertyName", "JsonPropertyAttribute.PropertyName", "primary", "Carries the explicit JSON name from the attribute.", group="attribute", scope="line"),
            A("newtonsoft/Newtonsoft.Json/Serialization/DefaultContractResolver.cs", "mappedName = propertyAttribute.PropertyName", "DefaultContractResolver property name", "supporting", "Copies the explicit attribute name into the contract property.", group="contract_property"),
            A("newtonsoft/Newtonsoft.Json/Serialization/JsonSerializerInternalWriter.cs", "property.WritePropertyName(writer);", "JsonSerializerInternalWriter property name", "supporting", "Writes using the resolved contract property name.", group="json_emission", scope="line"),
        ],
        required_groups=["attribute", "contract_property", "json_emission"],
    ))
    return queries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path("dataset"))
    args = parser.parse_args()
    source = args.source
    queries = build_queries(source)
    ids = [item["id"] for item in queries]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate query id")
    corpus = corpus_manifest(source)
    args.output.mkdir(parents=True, exist_ok=True)
    queries_doc = {
        "dataset": DATASET_VERSION,
        "schema_version": SCHEMA_VERSION,
        "queries": queries,
    }
    queries_bytes = json.dumps(queries_doc, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    corpus_bytes = json.dumps(corpus, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    (args.output / "queries.json").write_bytes(queries_bytes)
    (args.output / "corpus.json").write_bytes(corpus_bytes)
    manifest = {
        "dataset": DATASET_VERSION,
        "schema_version": SCHEMA_VERSION,
        "frozen_at": FROZEN_AT,
        "query_count": len(queries),
        "corpus_files": corpus["counts"]["files"],
        "corpus_lines": corpus["counts"]["lines"],
        "queries_sha256": sha256_bytes(queries_bytes),
        "corpus_sha256": sha256_bytes(corpus_bytes),
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    counts = {}
    for item in queries:
        key = (item["category"], item["answerable"])
        counts[key] = counts.get(key, 0) + 1
    print(json.dumps({"queries": len(queries), "categories": {f"{k[0]}:{k[1]}": v for k, v in sorted(counts.items())}}, indent=2))


if __name__ == "__main__":
    main()
