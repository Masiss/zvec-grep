use std::{
    collections::{BTreeMap, BTreeSet},
    env, fs,
    path::{Path, PathBuf},
    process,
    time::Instant,
};

use serde::Serialize;
use tree_sitter::{Language, Node, Parser};

mod csharp;

#[derive(Clone, Debug, Serialize)]
struct Caller {
    name: String,
    path: String,
    line: usize,
    call_line: usize,
}

#[derive(Clone, Debug, Serialize)]
struct Declaration {
    name: String,
    kind: String,
    path: String,
    line: usize,
    callers: Vec<Caller>,
}

#[derive(Serialize)]
struct ResultSet {
    query: String,
    files_scanned: usize,
    declarations: Vec<Declaration>,
    csharp: Option<csharp::Discovery>,
    legacy_truncated: bool,
}

#[derive(Clone)]
struct RawDeclaration {
    name: String,
    kind: String,
    path: String,
    line: usize,
    return_type: Option<String>,
}

#[derive(Clone)]
struct RawCall {
    callee: String,
    caller: Option<String>,
    caller_path: String,
    caller_line: usize,
    call_line: usize,
}

const MAX_LEGACY_FILES: usize = 2_500;
const MAX_LEGACY_DIRECTORIES: usize = 10_000;
const MAX_LEGACY_DIRECTORY_DEPTH: usize = 64;

fn main() {
    let mut args = env::args_os().skip(1);
    let Some(root) = args.next().map(PathBuf::from) else {
        eprintln!("usage: zg-source-discovery-prototype ROOT QUERY");
        process::exit(2);
    };
    let Some(query) = args.next().and_then(|value| value.into_string().ok()) else {
        eprintln!("usage: zg-source-discovery-prototype ROOT QUERY");
        process::exit(2);
    };
    if args.next().is_some() || query.trim().is_empty() {
        eprintln!("expected exactly ROOT and a non-empty QUERY");
        process::exit(2);
    }

    match discover(&root, &query) {
        Ok((mut result, elapsed_ns)) => {
            // Timing is emitted separately from the scored payload so callers can
            // compare tool latency without affecting result byte measurements.
            result.declarations.shrink_to_fit();
            let mut value = serde_json::to_value(result).expect("serialize result");
            value["elapsed_ns"] = serde_json::json!(elapsed_ns);
            println!(
                "{}",
                serde_json::to_string(&value).expect("serialize output")
            );
        }
        Err(error) => {
            eprintln!("{error}");
            process::exit(1);
        }
    }
}

fn discover(root: &Path, query: &str) -> Result<(ResultSet, u128), String> {
    let started = Instant::now();
    let mut paths = Vec::new();
    let mut legacy_truncated = false;
    let mut legacy_directories = 0;
    collect_legacy_files(
        root,
        &mut paths,
        &mut legacy_truncated,
        &mut legacy_directories,
        0,
    )?;
    paths.sort();

    let mut parser = Parser::new();
    parser
        .set_language(&tree_sitter_rust::LANGUAGE.into())
        .map_err(|error| format!("failed to configure Rust parser: {error}"))?;

    let mut declarations = Vec::new();
    let mut calls = Vec::new();
    for path in &paths {
        let Some(language) = language_for_path(path) else {
            continue;
        };
        parser
            .set_language(&language)
            .map_err(|error| format!("{}: unsupported parser: {error}", path.display()))?;
        let bytes = fs::read(path).map_err(|error| format!("{}: {error}", path.display()))?;
        let text = String::from_utf8_lossy(&bytes);
        let Some(tree) = parser.parse(text.as_bytes(), None) else {
            continue;
        };
        let relative = path
            .strip_prefix(root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        collect_nodes(
            tree.root_node(),
            text.as_bytes(),
            &relative,
            None,
            0,
            &mut declarations,
            &mut calls,
        );
    }

    let query_parts = identifier_parts(query);
    let matching_names: BTreeSet<String> = declarations
        .iter()
        .filter(|declaration| matches_query(&query_parts, &identifier_parts(&declaration.name)))
        .map(|declaration| declaration.name.clone())
        .collect();
    let mut callers_by_callee: BTreeMap<String, BTreeSet<(String, String, usize, usize)>> =
        BTreeMap::new();
    let returned_type_producers: BTreeMap<String, BTreeSet<String>> = declarations
        .iter()
        .filter_map(|declaration| {
            declaration
                .return_type
                .as_ref()
                .map(|ty| (ty.clone(), declaration.name.clone()))
        })
        .fold(BTreeMap::new(), |mut result, (ty, function)| {
            result.entry(ty).or_default().insert(function);
            result
        });
    for call in calls {
        if matching_names.contains(&call.callee) {
            if let Some(caller) = call.caller.as_ref() {
                callers_by_callee
                    .entry(call.callee.clone())
                    .or_default()
                    .insert((
                        caller.clone(),
                        call.caller_path.clone(),
                        call.caller_line,
                        call.call_line,
                    ));
            }
        }
        for ty in &matching_names {
            if returned_type_producers
                .get(ty)
                .is_some_and(|producers| producers.contains(&call.callee))
            {
                if let Some(caller) = call.caller.as_ref() {
                    callers_by_callee.entry(ty.clone()).or_default().insert((
                        caller.clone(),
                        call.caller_path.clone(),
                        call.caller_line,
                        call.call_line,
                    ));
                }
            }
        }
    }

    let results = declarations
        .into_iter()
        .filter(|declaration| matching_names.contains(&declaration.name))
        .map(|declaration| {
            let callers = callers_by_callee
                .get(&declaration.name)
                .into_iter()
                .flatten()
                .map(|(name, path, line, call_line)| Caller {
                    name: name.clone(),
                    path: path.clone(),
                    line: *line,
                    call_line: *call_line,
                })
                .collect();
            Declaration {
                name: declaration.name,
                kind: declaration.kind,
                path: declaration.path,
                line: declaration.line,
                callers,
            }
        })
        .collect();

    let csharp_scan = csharp::discover(root, query)?;
    let csharp = if csharp_scan.files_found > 0 {
        Some(csharp_scan)
    } else {
        None
    };
    let legacy_files_scanned = paths
        .iter()
        .filter(|path| {
            path.extension().is_some_and(|extension| {
                matches!(extension.to_str(), Some("rs" | "py" | "ts" | "tsx"))
            })
        })
        .count();

    Ok((
        ResultSet {
            query: query.to_owned(),
            files_scanned: legacy_files_scanned,
            declarations: results,
            csharp,
            legacy_truncated,
        },
        started.elapsed().as_nanos(),
    ))
}

fn collect_legacy_files(
    root: &Path,
    out: &mut Vec<PathBuf>,
    truncated: &mut bool,
    directories_scanned: &mut usize,
    depth: usize,
) -> Result<(), String> {
    if out.len() >= MAX_LEGACY_FILES {
        *truncated = true;
        return Ok(());
    }
    if depth > MAX_LEGACY_DIRECTORY_DEPTH || *directories_scanned >= MAX_LEGACY_DIRECTORIES {
        *truncated = true;
        return Ok(());
    }
    *directories_scanned += 1;
    let entries = fs::read_dir(root).map_err(|error| format!("{}: {error}", root.display()))?;
    let mut entries = entries
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    entries.sort_by_key(std::fs::DirEntry::file_name);
    for entry in entries {
        if out.len() >= MAX_LEGACY_FILES {
            *truncated = true;
            break;
        }
        let path = entry.path();
        let file_type = entry.file_type().map_err(|error| error.to_string())?;
        if file_type.is_dir() {
            let name = entry.file_name().to_string_lossy().to_ascii_lowercase();
            if !matches!(
                name.as_str(),
                ".git"
                    | "bin"
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
            ) && !name.starts_with('.')
            {
                collect_legacy_files(&path, out, truncated, directories_scanned, depth + 1)?;
            }
        } else if file_type.is_file()
            && path
                .extension()
                .is_some_and(|ext| matches!(ext.to_str(), Some("rs" | "py" | "ts" | "tsx")))
        {
            out.push(path);
        }
    }
    Ok(())
}

fn collect_nodes(
    node: Node<'_>,
    source: &[u8],
    path: &str,
    caller: Option<(String, usize)>,
    depth: usize,
    declarations: &mut Vec<RawDeclaration>,
    calls: &mut Vec<RawCall>,
) {
    let mut current_caller = caller;
    if matches!(
        node.kind(),
        "function_item" | "function_definition" | "function_declaration"
    ) {
        if let Some(name_node) = node.child_by_field_name("name") {
            if let Ok(name) = name_node.utf8_text(source) {
                current_caller = Some((name.to_owned(), node.start_position().row + 1));
            }
        }
    }

    let kind = match node.kind() {
        "function_item" | "function_definition" | "function_declaration" => Some("function"),
        "struct_item" => Some("struct"),
        "class_definition" | "class_declaration" => Some("class"),
        "enum_item" => Some("enum"),
        "trait_item" => Some("trait"),
        "type_item" => Some("type"),
        "const_item" => Some("constant"),
        "static_item" => Some("static"),
        _ => None,
    };
    if let (Some(kind), Some(name_node)) = (kind, node.child_by_field_name("name")) {
        if let Ok(name) = name_node.utf8_text(source) {
            declarations.push(RawDeclaration {
                name: name.to_owned(),
                kind: kind.to_owned(),
                path: path.to_owned(),
                line: node.start_position().row + 1,
                return_type: if matches!(
                    node.kind(),
                    "function_item" | "function_definition" | "function_declaration"
                ) {
                    node.child_by_field_name("return_type")
                        .and_then(|ty| ty.utf8_text(source).ok())
                        .map(str::trim)
                        .map(str::to_owned)
                } else {
                    None
                },
            });
        }
    }

    if matches!(node.kind(), "call_expression" | "call") {
        if let Some(function_node) = node.child_by_field_name("function") {
            if let Ok(callee) = function_node.utf8_text(source) {
                calls.push(RawCall {
                    callee: callee
                        .rsplit("::")
                        .next()
                        .unwrap_or(callee)
                        .rsplit('.')
                        .next()
                        .unwrap_or(callee)
                        .to_owned(),
                    caller: current_caller.as_ref().map(|(name, _)| name.clone()),
                    caller_path: path.to_owned(),
                    caller_line: current_caller.as_ref().map_or(0, |(_, line)| *line),
                    call_line: node.start_position().row + 1,
                });
            }
        }
    }
    if node.kind() == "struct_expression" {
        if let Some(type_node) = node.child_by_field_name("name") {
            if let Ok(callee) = type_node.utf8_text(source) {
                calls.push(RawCall {
                    callee: callee.to_owned(),
                    caller: current_caller.as_ref().map(|(name, _)| name.clone()),
                    caller_path: path.to_owned(),
                    caller_line: current_caller.as_ref().map_or(0, |(_, line)| *line),
                    call_line: node.start_position().row + 1,
                });
            }
        }
    }

    if depth > 512 {
        return;
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        collect_nodes(
            child,
            source,
            path,
            current_caller.clone(),
            depth + 1,
            declarations,
            calls,
        );
    }
}

fn language_for_path(path: &Path) -> Option<Language> {
    match path.extension()?.to_str()? {
        "rs" => Some(tree_sitter_rust::LANGUAGE.into()),
        "py" => Some(tree_sitter_python::LANGUAGE.into()),
        "ts" => Some(tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()),
        "tsx" => Some(tree_sitter_typescript::LANGUAGE_TSX.into()),
        _ => None,
    }
}

pub(crate) fn identifier_parts(value: &str) -> Vec<String> {
    let chars: Vec<char> = value.chars().collect();
    let mut words = Vec::new();
    let mut current = String::new();
    for (index, character) in chars.iter().copied().enumerate() {
        if !character.is_alphanumeric() {
            if !current.is_empty() {
                words.push(current.to_lowercase());
                current.clear();
            }
            continue;
        }
        let boundary = character.is_uppercase()
            && !current.is_empty()
            && (chars[index - 1].is_lowercase()
                || (chars.get(index + 1).is_some_and(|next| next.is_lowercase())
                    && chars[index - 1].is_uppercase()));
        if boundary {
            words.push(current.to_lowercase());
            current.clear();
        }
        current.push(character);
    }
    if !current.is_empty() {
        words.push(current.to_lowercase());
    }
    words
}

pub(crate) fn matches_query(query: &[String], candidate: &[String]) -> bool {
    if query.is_empty() || candidate.is_empty() || query.len() > candidate.len() {
        return false;
    }
    candidate.windows(query.len()).any(|window| {
        query
            .iter()
            .zip(window)
            .all(|(needle, haystack)| haystack.starts_with(needle))
    })
}
