//! Typed R2/R3 fact extraction for the adapter.
//!
//! Reuses the engine's C# and SQL fact parsers over file content read under
//! the pinned session root. Nothing is executed or written; SQL definitions
//! keep their selected/authority fields so excluded variants (`_old`,
//! `_old1`, `NewSolution`) are never presented as authority.

use serde_json::{Value, json};

use zg_engine::r4_experimental::{ProjectIdentity, parse_csharp_file, parse_sql_file};

/// Parses typed facts for one file, choosing the parser by extension.
/// When `method` is set, only facts belonging to that method are returned.
/// `exclusions` are session-scope path markers for non-authoritative
/// variants; they are benchmark configuration, not engine behavior.
///
/// # Errors
///
/// Returns a human-readable reason for unsupported or unreadable inputs.
pub fn parse_facts(
    path: &str,
    text: &str,
    method: Option<&str>,
    exclusions: &[String],
) -> Result<Value, String> {
    if path.ends_with(".cs") {
        let mut facts = csharp_json(path, text);
        if let Some(method) = method {
            facts = filter_csharp_method(facts, method);
        }
        Ok(facts)
    } else if path.ends_with(".sql") {
        let mut facts = sql_json(path, text, exclusions);
        if let Some(name) = method {
            facts["definitions"] = Value::Array(
                facts["definitions"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default()
                    .into_iter()
                    .filter(|definition| {
                        definition
                            .get("name")
                            .and_then(Value::as_str)
                            .is_some_and(|definition_name| {
                                definition_name.eq_ignore_ascii_case(name)
                            })
                    })
                    .collect(),
            );
        }
        Ok(facts)
    } else {
        Err(format!(
            "facts supports .cs and .sql sources; {path} is neither"
        ))
    }
}

fn filter_csharp_method(mut facts: Value, method: &str) -> Value {
    let Some(methods) = facts["methods"].as_array() else {
        return facts;
    };
    let Some(matched) = methods
        .iter()
        .find(|entry| entry.get("name").and_then(Value::as_str) == Some(method))
        .cloned()
    else {
        facts["methods"] = Value::Array(Vec::new());
        facts["assignments"] = Value::Array(Vec::new());
        facts["invocations"] = Value::Array(Vec::new());
        facts["saves"] = Value::Array(Vec::new());
        return facts;
    };
    let lines = matched.get("lines").cloned().unwrap_or(json!([0, 0]));
    let in_lines = |entry: &Value| {
        entry
            .get("line")
            .and_then(Value::as_u64)
            .map(|line| {
                lines[0].as_u64().unwrap_or(0) <= line && line <= lines[1].as_u64().unwrap_or(0)
            })
            .unwrap_or(false)
    };
    for section in ["assignments", "invocations", "saves"] {
        if let Some(entries) = facts[section].as_array() {
            let filtered: Vec<Value> = entries
                .iter()
                .filter(|entry| match entry.get("method").and_then(Value::as_str) {
                    Some(owner) => owner == method,
                    None => in_lines(entry),
                })
                .cloned()
                .collect();
            facts[section] = Value::Array(filtered);
        }
    }
    facts["methods"] = Value::Array(vec![matched]);
    facts
}

/// Compact counts-only summary used when the full facts dump would exceed the
/// delivered-content budget.
#[must_use]
pub fn summarize(facts: &Value) -> Value {
    let counts = [
        "types",
        "methods",
        "db_sets",
        "fields",
        "assignments",
        "invocations",
        "saves",
        "definitions",
    ];
    let mut summary = serde_json::Map::new();
    for key in counts {
        if let Some(entries) = facts["facts"][key].as_array() {
            summary.insert(key.to_owned(), json!(entries.len()));
        }
    }
    Value::Object(summary)
}

fn is_excluded(path: &str, exclusions: &[String]) -> bool {
    let lowered = path.to_ascii_lowercase();
    exclusions
        .iter()
        .any(|marker| lowered.contains(&marker.to_ascii_lowercase()))
}

fn project_label(path: &str) -> String {
    path.split('/')
        .next()
        .filter(|segment| !segment.is_empty())
        .unwrap_or("corpus")
        .to_owned()
}

fn csharp_json(path: &str, text: &str) -> Value {
    let identity = ProjectIdentity {
        name: project_label(path),
        assembly: project_label(path),
        root_namespace: None,
    };
    let facts = parse_csharp_file(&identity, path, text);
    json!({
        "kind": "csharp",
        "parse_error": facts.parse_error,
        "namespace": facts.namespace,
        "types": facts
            .types
            .iter()
            .map(|fact| json!({
                "name": fact.name,
                "kind": fact.kind,
                "partial": fact.partial,
                "lines": [fact.line_start, fact.line_end],
            }))
            .collect::<Vec<_>>(),
        "methods": facts
            .methods
            .iter()
            .map(|fact| json!({
                "type": fact.type_name,
                "name": fact.name,
                "signature": fact.signature,
                "lines": [fact.line_start, fact.line_end],
            }))
            .collect::<Vec<_>>(),
        "db_sets": facts
            .db_sets
            .iter()
            .map(|fact| json!({
                "context_type": fact.context_type,
                "property": fact.property,
                "element_type": fact.element_type,
                "lines": [fact.line_start, fact.line_end],
            }))
            .collect::<Vec<_>>(),
        "fields": facts
            .fields
            .iter()
            .map(|fact| json!({
                "type": fact.type_name,
                "name": fact.name,
                "field_type": fact.field_type,
            }))
            .collect::<Vec<_>>(),
        "assignments": facts
            .assignments
            .iter()
            .map(|fact| json!({
                "method": fact.method,
                "receiver": fact.receiver,
                "field": fact.field,
                "value": fact.value,
                "guard": fact.guard,
                "line": fact.line,
            }))
            .collect::<Vec<_>>(),
        "invocations": facts
            .invocations
            .iter()
            .map(|fact| json!({
                "method": fact.method,
                "receiver": fact.receiver,
                "called": fact.called,
                "arguments": fact.arguments,
                "guard": fact.guard,
                "line": fact.line,
                "status": format!("{:?}", fact.status).to_lowercase(),
                "target": fact.target.as_ref().map(|target| json!({
                    "path": target.path,
                    "type": target.type_name,
                    "method": target.method,
                })),
                "reason": fact.reason,
            }))
            .collect::<Vec<_>>(),
        "saves": facts
            .saves
            .iter()
            .map(|fact| json!({
                "method": fact.method,
                "receiver": fact.receiver,
                "context_type": fact.context_type,
                "guard": fact.guard,
                "line": fact.line,
            }))
            .collect::<Vec<_>>(),
    })
}

fn sql_json(path: &str, text: &str, exclusions: &[String]) -> Value {
    let selected = !is_excluded(path, exclusions);
    let authority = if selected { "adapter" } else { "excluded_variant" };
    let definitions = parse_sql_file(path, text, selected, authority);
    json!({
        "kind": "sql",
        "selected": selected,
        "authority": authority,
        "definitions": definitions
            .iter()
            .map(|definition| json!({
                "name": definition.name,
                "kind": format!("{:?}", definition.kind).to_lowercase(),
                "selected": definition.selected,
                "authority": definition.authority,
                "lines": [definition.line_start, definition.line_end],
                "writes": definition
                    .writes
                    .iter()
                    .map(|access| json!({
                        "table": access.table,
                        "kind": access.kind,
                        "operation": access.operation,
                        "line": access.line,
                    }))
                    .collect::<Vec<_>>(),
                "reads": definition
                    .reads
                    .iter()
                    .map(|access| json!({
                        "table": access.table,
                        "kind": access.kind,
                        "operation": access.operation,
                        "line": access.line,
                    }))
                    .collect::<Vec<_>>(),
                "columns": definition.columns,
            }))
            .collect::<Vec<_>>(),
    })
}
