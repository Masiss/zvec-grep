//! Read-only probe: would declaration/identifier-targeted queries reach the
//! frozen gold spans inside a small result limit?
//!
//! Analysis only. Measures an upper bound on a symbol/identifier-targeted
//! discovery route; it does not implement that route and writes nothing except
//! its receipt. The frozen corpus is only read.

#![allow(
    clippy::struct_excessive_bools,
    clippy::too_many_lines,
    clippy::collapsible_if,
    clippy::cast_precision_loss,
    clippy::struct_field_names
)]

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use serde::Serialize;
use tokio::runtime::Runtime;
use zg_engine::ZvecGrep;
use zg_engine::api::context::ContextOptions;

const CASES: &[&str] = &[
    "D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10", "D11", "D12",
];
const LIMIT: usize = 50;

#[derive(Serialize)]
struct SpanReach {
    case: String,
    path: String,
    start_line: usize,
    end_line: usize,
    declaration: Option<String>,
    queries: Vec<String>,
    /// Smallest rank at which this file appears in any targeted query.
    best_file_rank: Option<usize>,
    /// Query that produced `best_file_rank`.
    best_query: Option<String>,
    /// Smallest rank at which a unit overlaps the span.
    best_span_rank: Option<usize>,
    reached_top10: bool,
    reached_top50: bool,
}

struct Engine {
    runtime: Runtime,
    engine: ZvecGrep,
    root: PathBuf,
}

impl Engine {
    fn new(root: &Path) -> Result<Self, String> {
        Ok(Self {
            runtime: Runtime::new().map_err(|error| error.to_string())?,
            engine: ZvecGrep::new(),
            root: root.to_path_buf(),
        })
    }

    fn search(&mut self, pattern: &str) -> Vec<(String, usize, usize)> {
        let options = ContextOptions {
            rg: true,
            query: Some(pattern.to_owned()),
            root: Some(self.root.clone()),
            limit: Some(LIMIT),
            auto_update: false,
            ..ContextOptions::default()
        };
        let Ok(result) = self.runtime.block_on(self.engine.context(options)) else {
            return Vec::new();
        };
        result
            .items
            .iter()
            .filter_map(|item| {
                let (start, end) = (item.range.start_line()?, item.range.last_line()?);
                Some((
                    item.relative_path.to_string_lossy().replace('\\', "/"),
                    start,
                    end,
                ))
            })
            .collect()
    }
}

fn declaration_name(line: &str) -> Option<String> {
    let trimmed = line.trim();
    let tokens = trimmed
        .split(|ch: char| !(ch.is_alphanumeric() || ch == '_'))
        .filter(|token| !token.is_empty())
        .collect::<Vec<_>>();
    if trimmed.starts_with("CREATE") || trimmed.starts_with("ALTER") {
        return tokens
            .iter()
            .position(|token| *token == "PROCEDURE" || *token == "VIEW")
            .and_then(|index| tokens.get(index + 1))
            .map(|name| (*name).to_owned());
    }
    if let Some(open) = trimmed.find('(') {
        if let Some(candidate) = trimmed[..open].split_whitespace().last() {
            let name = candidate.trim();
            if !name.is_empty() && !name.contains('=') && name != "get" && name != "set" {
                return Some(name.to_owned());
            }
        }
    }
    trimmed
        .split('{')
        .next()
        .unwrap_or(trimmed)
        .split_whitespace()
        .last()
        .filter(|name| *name != "get" && *name != "set")
        .map(ToOwned::to_owned)
}

fn is_declaration(line: &str) -> bool {
    let trimmed = line.trim_start();
    [
        "class ",
        "struct ",
        "interface ",
        "public ",
        "private ",
        "protected ",
        "internal ",
        "static ",
        "void ",
        "CREATE PROCEDURE",
        "ALTER PROCEDURE",
        "CREATE VIEW",
        "ALTER VIEW",
    ]
    .iter()
    .any(|prefix| trimmed.starts_with(prefix))
}

fn enclosing(text: &str, start_line: usize) -> Option<String> {
    let lines = text.lines().collect::<Vec<_>>();
    let start = start_line.clamp(1, lines.len());
    let limit = start.saturating_sub(200).max(1);
    for offset in (limit - 1..start).rev() {
        let line = lines[offset];
        if is_declaration(line) {
            if let Some(name) = declaration_name(line) {
                return Some(name);
            }
        }
    }
    None
}

fn span_identifiers(text: &str, start_line: usize, end_line: usize) -> Vec<String> {
    let mut identifiers = BTreeSet::new();
    for line in text
        .lines()
        .skip(start_line.saturating_sub(1))
        .take(end_line.saturating_sub(start_line).saturating_add(1))
    {
        for token in line.split(|ch: char| !(ch.is_alphanumeric() || ch == '_')) {
            if token.len() >= 6
                && token.chars().next().is_some_and(char::is_uppercase)
                && ![
                    "Public",
                    "Private",
                    "Protected",
                    "Internal",
                    "String",
                    "Return",
                ]
                .contains(&token)
            {
                identifiers.insert(token.to_owned());
            }
        }
    }
    identifiers.into_iter().take(3).collect()
}

fn main() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let corpus: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/corpus-manifest.json"))
            .expect("corpus manifest"),
    )
    .expect("valid corpus manifest");
    let root = PathBuf::from(corpus["source_root"].as_str().expect("source root"));
    let dev: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/cases/dev.json")).expect("dev cases"),
    )
    .expect("valid dev cases");

    let mut engine = Engine::new(&root).expect("engine");
    let mut spans = Vec::new();
    for case in dev["cases"].as_array().expect("cases") {
        let id = case["id"].as_str().expect("case id");
        if !CASES.contains(&id) {
            continue;
        }
        for reference in case["source_refs"].as_array().into_iter().flatten() {
            let path = reference["path"].as_str().expect("path").to_owned();
            let Ok(text) = fs::read_to_string(root.join(&path)) else {
                continue;
            };
            for span in reference["spans"].as_array().into_iter().flatten() {
                let start_line = usize::try_from(span["start_line"].as_u64().expect("start"))
                    .expect("start fits usize");
                let end_line =
                    usize::try_from(span["end_line"].as_u64().expect("end")).expect("end");
                let stem = Path::new(&path)
                    .file_stem()
                    .and_then(std::ffi::OsStr::to_str)
                    .unwrap_or_default()
                    .to_owned();
                let declaration = enclosing(&text, start_line);
                let mut queries = Vec::new();
                if let Some(name) = &declaration {
                    queries.push(name.clone());
                }
                if !stem.is_empty() {
                    queries.push(stem.clone());
                }
                queries.extend(span_identifiers(&text, start_line, end_line));
                queries.dedup();

                let mut best_file_rank: Option<usize> = None;
                let mut best_query = None;
                let mut best_span_rank: Option<usize> = None;
                for query in &queries {
                    let units = engine.search(query);
                    if let Some(rank) = units.iter().position(|(unit_path, ..)| unit_path == &path)
                    {
                        if best_file_rank.is_none_or(|best| rank < best) {
                            best_file_rank = Some(rank);
                            best_query = Some(query.clone());
                        }
                    }
                    if let Some(rank) =
                        units.iter().position(|(unit_path, unit_start, unit_end)| {
                            unit_path == &path && *unit_start <= end_line && start_line <= *unit_end
                        })
                    {
                        best_span_rank = Some(best_span_rank.map_or(rank, |best| best.min(rank)));
                    }
                }
                spans.push(SpanReach {
                    case: id.to_owned(),
                    path: path.clone(),
                    start_line,
                    end_line,
                    declaration,
                    queries,
                    best_file_rank,
                    best_query,
                    best_span_rank,
                    reached_top10: best_span_rank.is_some_and(|rank| rank < 10),
                    reached_top50: best_span_rank.is_some(),
                });
            }
        }
    }

    let covered_top10 = spans.iter().filter(|span| span.reached_top10).count();
    let covered_top50 = spans.iter().filter(|span| span.reached_top50).count();
    let file_reached = spans
        .iter()
        .filter(|span| span.best_file_rank.is_some())
        .count();
    let receipt = serde_json::json!({
        "status": "R4_SYMBOL_REACHABILITY",
        "branch_head": command(&repo, &["git", "rev-parse", "HEAD"]),
        "corpus_root": root.display().to_string(),
        "limit": LIMIT,
        "spans": spans.len(),
        "span_overlap_top10": covered_top10,
        "span_overlap_top50": covered_top50,
        "file_reached": file_reached,
        "records": spans,
    });
    let text = serde_json::to_string_pretty(&receipt).expect("serialize receipt");
    let out = repo.join("benchmarks/rag-impact/results/r4-symbol-reachability-20261115.json");
    fs::write(&out, &text).expect("write receipt");
    println!(
        "spans={} span_top10={} span_top50={} file_reached={} -> {}",
        receipt["spans"],
        covered_top10,
        covered_top50,
        file_reached,
        out.display()
    );
}

fn command(repo: &Path, argv: &[&str]) -> String {
    std::process::Command::new(argv[0])
        .args(&argv[1..])
        .current_dir(repo)
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .unwrap_or_default()
}
