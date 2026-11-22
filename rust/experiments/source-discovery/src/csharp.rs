use std::{
    collections::{BTreeMap, BTreeSet, HashMap},
    fs,
    path::{Path, PathBuf},
};

use serde::Serialize;
use tree_sitter::{Node, Parser};

use crate::identifier_parts;

const MAX_FILES: usize = 2_500;
const MAX_FILE_BYTES: u64 = 2 * 1024 * 1024;

#[derive(Clone, Debug, Serialize)]
pub(crate) struct Declaration {
    pub(crate) symbol_id: String,
    pub(crate) name: String,
    pub(crate) kind: String,
    pub(crate) path: String,
    pub(crate) line: usize,
}

#[derive(Clone, Debug, Serialize, Eq, PartialEq, Ord, PartialOrd)]
pub(crate) struct CallEdge {
    pub(crate) caller: String,
    pub(crate) callee: String,
    pub(crate) path: String,
    pub(crate) line: usize,
}

#[derive(Clone, Debug, Serialize, Eq, PartialEq, Ord, PartialOrd)]
pub(crate) struct UnresolvedCall {
    pub(crate) caller: String,
    pub(crate) callee_name: String,
    pub(crate) path: String,
    pub(crate) line: usize,
    pub(crate) candidate_callees: Vec<String>,
    pub(crate) reason: String,
}

#[derive(Debug, Serialize)]
pub(crate) struct Discovery {
    pub(crate) files_scanned: usize,
    pub(crate) files_skipped: usize,
    pub(crate) oversized_files: usize,
    pub(crate) parse_error_files: usize,
    pub(crate) truncated: bool,
    pub(crate) declarations: Vec<Declaration>,
    pub(crate) call_edges: Vec<CallEdge>,
    pub(crate) unresolved_calls: Vec<UnresolvedCall>,
}

#[derive(Clone, Debug)]
struct TypeContext {
    symbol_id: String,
    name: String,
    kind: String,
    path: String,
    line: usize,
    partial: bool,
}

#[derive(Clone, Debug)]
struct Parameter {
    name: String,
    ty: Option<String>,
}

#[derive(Clone, Debug)]
struct Method {
    symbol_id: String,
    name: String,
    type_id: String,
    parameters: Vec<Parameter>,
    path: String,
    line: usize,
}

#[derive(Clone, Debug)]
struct CallerContext {
    symbol_id: String,
    type_id: String,
    parameters: BTreeMap<String, String>,
}

#[derive(Clone, Debug)]
struct Invocation {
    name: String,
    receiver: Option<String>,
    argument_types: Vec<Option<String>>,
    caller: CallerContext,
    path: String,
    line: usize,
}

#[derive(Default)]
struct Collected {
    types: Vec<TypeContext>,
    methods: Vec<Method>,
    invocations: Vec<Invocation>,
}

pub(crate) fn discover(root: &Path, query: &str) -> Result<Discovery, String> {
    let mut paths = Vec::new();
    let mut truncated = false;
    collect_cs_files(root, &mut paths, &mut truncated)?;
    paths.sort();

    let mut collected = Collected::default();
    let mut files_scanned = 0;
    let mut files_skipped = 0;
    let mut oversized_files = 0;
    let mut parse_error_files = 0;
    let mut parser = Parser::new();
    parser
        .set_language(&tree_sitter_c_sharp::LANGUAGE.into())
        .map_err(|error| format!("failed to configure C# parser: {error}"))?;

    for path in &paths {
        let metadata =
            fs::metadata(path).map_err(|error| format!("{}: {error}", path.display()))?;
        if metadata.len() > MAX_FILE_BYTES {
            oversized_files += 1;
            files_skipped += 1;
            continue;
        }
        let bytes = fs::read(path).map_err(|error| format!("{}: {error}", path.display()))?;
        let text = String::from_utf8_lossy(&bytes);
        let Some(tree) = parser.parse(text.as_bytes(), None) else {
            files_skipped += 1;
            continue;
        };
        if tree.root_node().has_error() {
            parse_error_files += 1;
            files_skipped += 1;
            continue;
        }
        files_scanned += 1;
        let relative = path
            .strip_prefix(root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        collect_tree(tree.root_node(), text.as_bytes(), &relative, &mut collected);
    }

    let methods_by_type_and_name = method_groups(&collected.methods);
    let type_ids_by_name = type_name_map(&collected.types);
    let mut edges = BTreeSet::new();
    let mut unresolved = BTreeSet::new();

    for invocation in &collected.invocations {
        let candidate_type = resolve_receiver_type(invocation, &type_ids_by_name, &collected.types);
        let (type_id, receiver_error) = match candidate_type {
            ReceiverType::Known(type_id) => (Some(type_id), None),
            ReceiverType::Dynamic => (None, Some("dynamic_dispatch")),
            ReceiverType::Unknown => (None, Some("unknown_receiver_type")),
        };
        let scoped_candidates: Vec<&Method> = type_id
            .as_ref()
            .and_then(|type_id| {
                methods_by_type_and_name.get(&(type_id.clone(), invocation.name.clone()))
            })
            .map(|methods| methods.iter().collect())
            .unwrap_or_default();
        let same_name_candidates = methods_by_name(&collected.methods, &invocation.name);

        if let Some(reason) = receiver_error {
            if !same_name_candidates.is_empty() {
                unresolved.insert(unresolved_call(invocation, same_name_candidates, reason));
            }
            continue;
        }
        if scoped_candidates.is_empty() {
            continue;
        }

        let arity_candidates: Vec<&Method> = scoped_candidates
            .iter()
            .filter(|method| method.parameters.len() == invocation.argument_types.len())
            .copied()
            .collect();
        if arity_candidates.is_empty() {
            unresolved.insert(unresolved_call(
                invocation,
                scoped_candidates,
                "no_matching_arity",
            ));
            continue;
        }

        let dynamic_argument = invocation
            .argument_types
            .iter()
            .any(|ty| ty.as_deref() == Some("dynamic"));
        if dynamic_argument {
            unresolved.insert(unresolved_call(
                invocation,
                arity_candidates,
                "dynamic_dispatch",
            ));
            continue;
        }

        let compatible: Vec<&Method> = arity_candidates
            .iter()
            .copied()
            .filter(|method| arguments_compatible(method, &invocation.argument_types))
            .collect();
        match compatible.as_slice() {
            [method] => {
                edges.insert(CallEdge {
                    caller: invocation.caller.symbol_id.clone(),
                    callee: method.symbol_id.clone(),
                    path: invocation.path.clone(),
                    line: invocation.line,
                });
            }
            [] => {
                unresolved.insert(unresolved_call(
                    invocation,
                    arity_candidates,
                    "no_compatible_overload",
                ));
            }
            _ => {
                unresolved.insert(unresolved_call(
                    invocation,
                    compatible,
                    "ambiguous_overloads",
                ));
            }
        };
    }

    if truncated || files_skipped > 0 {
        let method_names: HashMap<&str, &str> = collected
            .methods
            .iter()
            .map(|method| (method.symbol_id.as_str(), method.name.as_str()))
            .collect();
        for edge in &edges {
            unresolved.insert(UnresolvedCall {
                caller: edge.caller.clone(),
                callee_name: method_names
                    .get(edge.callee.as_str())
                    .copied()
                    .unwrap_or_default()
                    .to_owned(),
                path: edge.path.clone(),
                line: edge.line,
                candidate_callees: vec![edge.callee.clone()],
                reason: "incomplete_scan".to_owned(),
            });
        }
        edges.clear();
    }

    let query_parts = identifier_parts(query);
    let mut symbol_scores = BTreeMap::new();
    for method in &collected.methods {
        if let Some(score) = identifier_match_score(&query_parts, &identifier_parts(&method.name)) {
            symbol_scores
                .entry(method.symbol_id.clone())
                .and_modify(|existing: &mut usize| *existing = (*existing).max(score))
                .or_insert(score);
        }
    }
    for ty in &collected.types {
        if let Some(score) = identifier_match_score(&query_parts, &identifier_parts(&ty.name)) {
            symbol_scores
                .entry(ty.symbol_id.clone())
                .and_modify(|existing: &mut usize| *existing = (*existing).max(score))
                .or_insert(score);
        }
    }
    let best_score = symbol_scores.values().copied().max();
    let matched_ids: BTreeSet<String> = symbol_scores
        .into_iter()
        .filter(|(_, score)| Some(*score) == best_score)
        .map(|(symbol_id, _)| symbol_id)
        .collect();
    let declarations_by_id = declaration_map(&collected);
    let declarations = matched_ids
        .iter()
        .filter_map(|symbol_id| declarations_by_id.get(symbol_id).cloned())
        .collect();
    let matched_edges: Vec<CallEdge> = edges
        .into_iter()
        .filter(|edge| matched_ids.contains(&edge.callee))
        .collect();
    let matched_unresolved: Vec<UnresolvedCall> = unresolved
        .into_iter()
        .filter(|call| {
            call.candidate_callees
                .iter()
                .any(|id| matched_ids.contains(id))
        })
        .collect();

    Ok(Discovery {
        files_scanned,
        files_skipped,
        oversized_files,
        parse_error_files,
        truncated,
        declarations,
        call_edges: matched_edges,
        unresolved_calls: matched_unresolved,
    })
}

fn identifier_match_score(query: &[String], candidate: &[String]) -> Option<usize> {
    if query.is_empty() || candidate.is_empty() || query.len() > candidate.len() {
        return None;
    }
    candidate
        .windows(query.len())
        .filter_map(|window| {
            query
                .iter()
                .zip(window)
                .try_fold(0_usize, |score, (needle, haystack)| {
                    if !haystack.starts_with(needle) {
                        None
                    } else if haystack == needle {
                        Some(score + 2)
                    } else {
                        Some(score + 1)
                    }
                })
        })
        .max()
}

fn collect_cs_files(
    root: &Path,
    out: &mut Vec<PathBuf>,
    truncated: &mut bool,
) -> Result<(), String> {
    let entries = fs::read_dir(root).map_err(|error| format!("{}: {error}", root.display()))?;
    for entry in entries {
        let entry = entry.map_err(|error| error.to_string())?;
        let path = entry.path();
        let file_type = entry.file_type().map_err(|error| error.to_string())?;
        if file_type.is_dir() {
            let name = entry.file_name().to_string_lossy().to_ascii_lowercase();
            if is_excluded_directory(&name) {
                continue;
            }
            collect_cs_files(&path, out, truncated)?;
        } else if file_type.is_file()
            && path
                .extension()
                .is_some_and(|extension| matches!(extension.to_str(), Some("cs" | "csx")))
        {
            if is_generated_file(&path) {
                continue;
            }
            if out.len() == MAX_FILES {
                *truncated = true;
                return Ok(());
            }
            out.push(path);
        }
    }
    Ok(())
}

fn is_excluded_directory(name: &str) -> bool {
    name.starts_with('.')
        || matches!(
            name,
            "bin"
                | "obj"
                | "vendor"
                | "vendors"
                | "packages"
                | "node_modules"
                | "generated"
                | "third_party"
                | "third-party"
                | "external"
                | "target"
        )
}

fn is_generated_file(path: &Path) -> bool {
    let name = path
        .file_name()
        .unwrap_or_default()
        .to_string_lossy()
        .to_ascii_lowercase();
    name.ends_with(".g.cs") || name.ends_with(".generated.cs") || name.ends_with(".designer.cs")
}

fn collect_tree(root: Node<'_>, source: &[u8], path: &str, out: &mut Collected) {
    collect_node(root, source, path, "", &[], None, out, 0);
}

fn collect_node(
    node: Node<'_>,
    source: &[u8],
    path: &str,
    namespace: &str,
    types: &[String],
    caller: Option<&CallerContext>,
    out: &mut Collected,
    depth: usize,
) {
    if depth > 512 {
        return;
    }
    match node.kind() {
        "compilation_unit" => {
            let mut current_namespace = namespace.to_owned();
            let mut cursor = node.walk();
            for child in node.named_children(&mut cursor) {
                if child.kind() == "file_scoped_namespace_declaration" {
                    if let Some(name) = child.child_by_field_name("name") {
                        current_namespace = qualified_name(name, source);
                    }
                    continue;
                }
                collect_node(
                    child,
                    source,
                    path,
                    &current_namespace,
                    types,
                    caller,
                    out,
                    depth + 1,
                );
            }
        }
        "namespace_declaration" => {
            let child_namespace = node
                .child_by_field_name("name")
                .map(|name| join_name(namespace, &qualified_name(name, source)))
                .unwrap_or_else(|| namespace.to_owned());
            if let Some(body) = node.child_by_field_name("body") {
                collect_node(
                    body,
                    source,
                    path,
                    &child_namespace,
                    types,
                    caller,
                    out,
                    depth + 1,
                );
            }
        }
        "class_declaration"
        | "struct_declaration"
        | "interface_declaration"
        | "record_declaration" => {
            let Some(name_node) = node.child_by_field_name("name") else {
                return;
            };
            let Ok(name) = name_node.utf8_text(source) else {
                return;
            };
            let kind = match node.kind() {
                "class_declaration" => "class",
                "struct_declaration" => "struct",
                "interface_declaration" => "interface",
                _ => "record",
            };
            let mut nested_types = types.to_vec();
            nested_types.push(name.to_owned());
            let type_name = join_name(namespace, &nested_types.join("."));
            let partial = has_modifier(node, source, "partial");
            out.types.push(TypeContext {
                symbol_id: type_name.clone(),
                name: name.to_owned(),
                kind: kind.to_owned(),
                path: path.to_owned(),
                line: node.start_position().row + 1,
                partial,
            });
            if let Some(body) = node.child_by_field_name("body") {
                collect_node(
                    body,
                    source,
                    path,
                    namespace,
                    &nested_types,
                    caller,
                    out,
                    depth + 1,
                );
            }
        }
        "method_declaration" => {
            if types.is_empty() {
                return;
            }
            let Some(name_node) = node.child_by_field_name("name") else {
                return;
            };
            let Ok(name) = name_node.utf8_text(source) else {
                return;
            };
            let type_id = join_name(namespace, &types.join("."));
            let parameters = node
                .child_by_field_name("parameters")
                .map(|parameters| parse_parameters(parameters, source))
                .unwrap_or_default();
            let signature = parameters
                .iter()
                .map(|parameter| parameter.ty.as_deref().unwrap_or("?"))
                .collect::<Vec<_>>()
                .join(",");
            let symbol_id = format!("{type_id}::{name}({signature})");
            let method = Method {
                symbol_id: symbol_id.clone(),
                name: name.to_owned(),
                type_id,
                parameters: parameters.clone(),
                path: path.to_owned(),
                line: node.start_position().row + 1,
            };
            out.methods.push(method);
            let parameters = parameters
                .into_iter()
                .filter_map(|parameter| parameter.ty.map(|ty| (parameter.name, ty)))
                .collect();
            let method_context = CallerContext {
                symbol_id,
                type_id: out
                    .methods
                    .last()
                    .expect("just inserted method")
                    .type_id
                    .clone(),
                parameters,
            };
            if let Some(body) = node.child_by_field_name("body") {
                collect_node(
                    body,
                    source,
                    path,
                    namespace,
                    types,
                    Some(&method_context),
                    out,
                    depth + 1,
                );
            }
        }
        "invocation_expression" => {
            if let Some(caller) = caller {
                if let (Some(function), Some(arguments)) = (
                    node.child_by_field_name("function"),
                    node.child_by_field_name("arguments"),
                ) {
                    if let Some((name, receiver)) = parse_function(function, source) {
                        let argument_types =
                            parse_argument_types(arguments, source, &caller.parameters);
                        out.invocations.push(Invocation {
                            name,
                            receiver,
                            argument_types,
                            caller: caller.clone(),
                            path: path.to_owned(),
                            line: node.start_position().row + 1,
                        });
                    }
                }
            }
            recurse_named(node, source, path, namespace, types, caller, out, depth);
        }
        _ => recurse_named(node, source, path, namespace, types, caller, out, depth),
    }
}

fn recurse_named(
    node: Node<'_>,
    source: &[u8],
    path: &str,
    namespace: &str,
    types: &[String],
    caller: Option<&CallerContext>,
    out: &mut Collected,
    depth: usize,
) {
    let mut cursor = node.walk();
    for child in node.named_children(&mut cursor) {
        collect_node(
            child,
            source,
            path,
            namespace,
            types,
            caller,
            out,
            depth + 1,
        );
    }
}

fn parse_parameters(node: Node<'_>, source: &[u8]) -> Vec<Parameter> {
    let mut output = Vec::new();
    let mut cursor = node.walk();
    for parameter in node.named_children(&mut cursor) {
        if parameter.kind() != "parameter" {
            continue;
        }
        let name = parameter
            .child_by_field_name("name")
            .and_then(|name| name.utf8_text(source).ok())
            .unwrap_or("_")
            .to_owned();
        let ty = parameter
            .child_by_field_name("type")
            .and_then(|ty| ty.utf8_text(source).ok())
            .map(canonical_type);
        output.push(Parameter { name, ty });
    }
    output
}

fn parse_function(node: Node<'_>, source: &[u8]) -> Option<(String, Option<String>)> {
    if node.kind() == "member_access_expression" {
        let name = node.child_by_field_name("name")?.utf8_text(source).ok()?;
        let receiver = node
            .child_by_field_name("expression")
            .and_then(|receiver| receiver.utf8_text(source).ok())
            .map(str::to_owned);
        return Some((last_name(name), receiver));
    }
    let text = node.utf8_text(source).ok()?;
    Some((last_name(text), None))
}

fn parse_argument_types(
    node: Node<'_>,
    source: &[u8],
    parameters: &BTreeMap<String, String>,
) -> Vec<Option<String>> {
    let mut output = Vec::new();
    let mut cursor = node.walk();
    for argument in node.named_children(&mut cursor) {
        let expression = if argument.kind() == "argument" {
            let mut children = argument.walk();
            argument.named_children(&mut children).last()
        } else {
            Some(argument)
        };
        output.push(
            expression.and_then(|expression| infer_expression_type(expression, source, parameters)),
        );
    }
    output
}

fn infer_expression_type(
    node: Node<'_>,
    source: &[u8],
    parameters: &BTreeMap<String, String>,
) -> Option<String> {
    match node.kind() {
        "integer_literal" => {
            let literal = node.utf8_text(source).ok()?.to_ascii_lowercase();
            let suffix = literal
                .chars()
                .rev()
                .take_while(|character| character.is_ascii_alphabetic())
                .collect::<String>();
            let suffix = suffix.chars().rev().collect::<String>();
            Some(
                match suffix.as_str() {
                    "ul" | "lu" => "ulong",
                    "u" => "uint",
                    "l" => "long",
                    _ => "int",
                }
                .to_owned(),
            )
        }
        "real_literal" => {
            let literal = node.utf8_text(source).ok()?.to_ascii_lowercase();
            Some(
                if literal.ends_with('f') {
                    "float"
                } else if literal.ends_with('m') {
                    "decimal"
                } else {
                    "double"
                }
                .to_owned(),
            )
        }
        "string_literal" | "interpolated_string_expression" => Some("string".to_owned()),
        "character_literal" => Some("char".to_owned()),
        "boolean_literal" => Some("bool".to_owned()),
        "null_literal" => Some("null".to_owned()),
        "identifier" => node
            .utf8_text(source)
            .ok()
            .and_then(|name| parameters.get(name).cloned()),
        "member_access_expression" => {
            let member = node.child_by_field_name("name")?.utf8_text(source).ok()?;
            if member == "Length" {
                Some("int".to_owned())
            } else {
                None
            }
        }
        "parenthesized_expression" => {
            let mut cursor = node.walk();
            node.named_children(&mut cursor)
                .find_map(|child| infer_expression_type(child, source, parameters))
        }
        "cast_expression" => node
            .child_by_field_name("type")
            .and_then(|ty| ty.utf8_text(source).ok())
            .map(canonical_type),
        _ => None,
    }
}

fn resolve_receiver_type(
    invocation: &Invocation,
    type_ids_by_name: &HashMap<String, BTreeSet<String>>,
    types: &[TypeContext],
) -> ReceiverType {
    let Some(receiver) = invocation.receiver.as_deref() else {
        return ReceiverType::Known(invocation.caller.type_id.clone());
    };
    if receiver == "this" {
        return ReceiverType::Known(invocation.caller.type_id.clone());
    }
    if receiver == "base" {
        return ReceiverType::Unknown;
    }
    if let Some(ty) = invocation.caller.parameters.get(receiver) {
        if ty == "dynamic" {
            return ReceiverType::Dynamic;
        }
        return resolve_type_name(ty, type_ids_by_name, types)
            .map(ReceiverType::Known)
            .unwrap_or(ReceiverType::Unknown);
    }
    resolve_type_name(receiver, type_ids_by_name, types)
        .map(ReceiverType::Known)
        .unwrap_or(ReceiverType::Unknown)
}

enum ReceiverType {
    Known(String),
    Dynamic,
    Unknown,
}

fn resolve_type_name(
    name: &str,
    type_ids_by_name: &HashMap<String, BTreeSet<String>>,
    types: &[TypeContext],
) -> Option<String> {
    let normalized = canonical_type(name);
    if normalized.contains('.') {
        let exact: Vec<_> = types
            .iter()
            .filter(|ty| ty.symbol_id == normalized)
            .collect();
        if exact.len() == 1 || exact.iter().all(|ty| ty.partial) {
            return Some(normalized);
        }
    }
    let ids = type_ids_by_name.get(&normalized)?;
    if ids.len() == 1 {
        ids.iter().next().cloned()
    } else {
        None
    }
}

fn arguments_compatible(method: &Method, arguments: &[Option<String>]) -> bool {
    method.parameters.len() == arguments.len()
        && method
            .parameters
            .iter()
            .zip(arguments)
            .all(|(parameter, argument)| match (&parameter.ty, argument) {
                (Some(parameter), Some(argument)) => types_equivalent(parameter, argument),
                (Some(_), None) => true,
                _ => false,
            })
}

fn types_equivalent(left: &str, right: &str) -> bool {
    let left = canonical_type(left);
    let right = canonical_type(right);
    left == right
        || matches!(
            (left.as_str(), right.as_str()),
            ("int", "System.Int32")
                | ("System.Int32", "int")
                | ("string", "System.String")
                | ("System.String", "string")
                | ("bool", "System.Boolean")
                | ("System.Boolean", "bool")
        )
}

fn unresolved_call(
    invocation: &Invocation,
    candidates: Vec<&Method>,
    reason: &str,
) -> UnresolvedCall {
    let candidate_callees = candidates
        .into_iter()
        .map(|method| method.symbol_id.clone())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    UnresolvedCall {
        caller: invocation.caller.symbol_id.clone(),
        callee_name: invocation.name.clone(),
        path: invocation.path.clone(),
        line: invocation.line,
        candidate_callees,
        reason: reason.to_owned(),
    }
}

fn method_groups(methods: &[Method]) -> BTreeMap<(String, String), Vec<Method>> {
    let mut output: BTreeMap<(String, String), Vec<Method>> = BTreeMap::new();
    for method in methods {
        output
            .entry((method.type_id.clone(), method.name.clone()))
            .or_default()
            .push(method.clone());
    }
    for group in output.values_mut() {
        group.sort_by(|left, right| left.symbol_id.cmp(&right.symbol_id));
        group.dedup_by(|left, right| left.symbol_id == right.symbol_id);
    }
    output
}

fn methods_by_name<'a>(methods: &'a [Method], name: &str) -> Vec<&'a Method> {
    methods
        .iter()
        .filter(|method| method.name == name)
        .collect()
}

fn type_name_map(types: &[TypeContext]) -> HashMap<String, BTreeSet<String>> {
    let mut output: HashMap<String, BTreeSet<String>> = HashMap::new();
    for ty in types {
        output
            .entry(ty.name.clone())
            .or_default()
            .insert(ty.symbol_id.clone());
    }
    output
}

fn declaration_map(collected: &Collected) -> BTreeMap<String, Declaration> {
    let mut output = BTreeMap::new();
    for ty in &collected.types {
        output
            .entry(ty.symbol_id.clone())
            .or_insert_with(|| Declaration {
                symbol_id: ty.symbol_id.clone(),
                name: ty.name.clone(),
                kind: ty.kind.clone(),
                path: ty.path.clone(),
                line: ty.line,
            });
    }
    for method in &collected.methods {
        output
            .entry(method.symbol_id.clone())
            .or_insert_with(|| Declaration {
                symbol_id: method.symbol_id.clone(),
                name: method.name.clone(),
                kind: "method".to_owned(),
                path: method.path.clone(),
                line: method.line,
            });
    }
    output
}

fn has_modifier(node: Node<'_>, source: &[u8], expected: &str) -> bool {
    let mut cursor = node.walk();
    node.named_children(&mut cursor).any(|child| {
        child.kind() == "modifier" && child.utf8_text(source).is_ok_and(|text| text == expected)
    })
}

fn qualified_name(node: Node<'_>, source: &[u8]) -> String {
    node.utf8_text(source)
        .unwrap_or_default()
        .replace("global::", "")
        .replace(" ", "")
}

fn join_name(left: &str, right: &str) -> String {
    match (left.is_empty(), right.is_empty()) {
        (true, _) => right.to_owned(),
        (_, true) => left.to_owned(),
        _ => format!("{left}.{right}"),
    }
}

fn last_name(text: &str) -> String {
    text.rsplit("::")
        .next()
        .unwrap_or(text)
        .rsplit('.')
        .next()
        .unwrap_or(text)
        .split('<')
        .next()
        .unwrap_or(text)
        .to_owned()
}

fn canonical_type(text: &str) -> String {
    text.chars()
        .filter(|character| !character.is_whitespace())
        .collect::<String>()
        .replace("global::", "")
}
