//! Read-only discovery forensics for the frozen R4 development cases.
//!
//! This is an analysis tool, not product code. It reproduces the measured
//! workflow's discovery for D01-D12 and records, for every gold span, the raw
//! facts needed to attribute why the span did or did not enter the candidate
//! pool: query handles, returned ranks, unlimited-scan rank, file admission,
//! pool membership, selection, and symbol-reachability.
//!
//! The frozen corpus is only read. No index, model, daemon or production
//! artifact is touched. Usage:
//! `cargo run -q -p zg-engine --example r4_discovery_forensics`

#![allow(
    clippy::struct_excessive_bools,
    clippy::too_many_lines,
    clippy::collapsible_if,
    clippy::cast_precision_loss
)]

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::Serialize;
use tokio::runtime::Runtime;
use zg_engine::ZvecGrep;
use zg_engine::api::context::ContextOptions;
use zg_engine::rag::workflow::{EvidenceSource, SearchUnit, WorkflowBudget, define, run};

const CASES: &[&str] = &[
    "D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10", "D11", "D12",
];
const FULL_SCAN_LIMIT: usize = 5000;
const SYMBOL_LIMIT: usize = 250;

#[derive(Clone, Debug, Serialize)]
struct GoldSpan {
    case: String,
    anchor: String,
    path: String,
    start_line: usize,
    end_line: usize,
    /// Text of the span.
    text: String,
    /// Declaration name that encloses the span, if one was found.
    declaration: Option<String>,
    /// Line range of the enclosing declaration.
    declaration_range: Option<(usize, usize)>,
}

#[derive(Clone, Debug, Serialize)]
struct SearchRecord {
    query: String,
    scope: Option<String>,
    returned: usize,
}

#[derive(Clone, Debug, Serialize)]
struct SpanRecord {
    case: String,
    anchor: String,
    path: String,
    start_line: usize,
    end_line: usize,
    in_manifest: bool,
    admitted: bool,
    file_bytes: usize,
    pooled: bool,
    pooled_file: bool,
    /// Units in the pooled file that overlap the span (empty when none).
    pool_hits: Vec<UnitRecord>,
    pool_units_in_file: Vec<UnitRecord>,
    selected: bool,
    /// Units selected in the same case whose path equals this span's file.
    selected_units_in_file: Vec<UnitRecord>,
    /// True when any DEFINE query text occurs inside the span.
    handle_in_span: Vec<String>,
    /// True when any DEFINE query text occurs in the enclosing declaration.
    handle_in_declaration: Vec<String>,
    /// Best (smallest) rank at which the span's file appears for a matching
    /// query inside the measured limit.
    rank_within_limit: Option<usize>,
    /// Best rank at which the span's file appears for a matching query under an
    /// unlimited scan.
    rank_full_scan: Option<usize>,
    /// Whether an unlimited scan returned a unit that overlaps the span.
    full_scan_overlaps: bool,
    /// Declaration-name search result, when a declaration was found.
    symbol_query: Option<String>,
    symbol_rank: Option<usize>,
    symbol_found_in_limit: bool,
}

#[derive(Clone, Debug, Serialize)]
struct UnitRecord {
    path: String,
    start_line: usize,
    end_line: usize,
}

#[derive(Serialize)]
struct CaseRecord {
    id: String,
    question: String,
    code_tokens: Vec<String>,
    anchors: Vec<String>,
    phrases: Vec<String>,
    tool_calls: usize,
    pool: usize,
    pool_files: usize,
    selected: usize,
    searches: Vec<SearchRecord>,
    selected_units: Vec<UnitRecord>,
    elapsed_ms: f64,
}

#[derive(Serialize)]
struct Receipt {
    status: &'static str,
    branch_head: String,
    corpus_root: String,
    budget: WorkflowBudget,
    cases: Vec<CaseRecord>,
    spans: Vec<SpanRecord>,
    elapsed_ms: f64,
}

struct EngineSource {
    runtime: Runtime,
    engine: ZvecGrep,
    root: PathBuf,
    log: Vec<SearchRecord>,
}

impl EngineSource {
    fn new(root: &Path) -> Result<Self, String> {
        Ok(Self {
            runtime: Runtime::new().map_err(|error| error.to_string())?,
            engine: ZvecGrep::new(),
            root: root.to_path_buf(),
            log: Vec::new(),
        })
    }

    fn units(
        &mut self,
        pattern: &str,
        limit: usize,
        scope: Option<&str>,
    ) -> Result<Vec<SearchUnit>, String> {
        let options = ContextOptions {
            rg: true,
            query: Some(pattern.to_owned()),
            root: Some(self.root.clone()),
            rg_paths: scope
                .map(|scope| vec![self.root.join(scope)])
                .unwrap_or_default(),
            limit: Some(limit),
            auto_update: false,
            ..ContextOptions::default()
        };
        let result = self
            .runtime
            .block_on(self.engine.context(options))
            .map_err(|error| error.report().to_string())?;
        let mut units = Vec::new();
        for item in &result.items {
            let (Some(start), Some(end)) = (item.range.start_line(), item.range.last_line()) else {
                continue;
            };
            units.push(SearchUnit {
                path: item.relative_path.to_string_lossy().replace('\\', "/"),
                start_line: start,
                end_line: end,
            });
        }
        Ok(units)
    }
}

impl EvidenceSource for EngineSource {
    fn search(&mut self, pattern: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        let units = self.units(pattern, limit, None)?;
        self.log.push(SearchRecord {
            query: pattern.to_owned(),
            scope: None,
            returned: units.len(),
        });
        Ok(units)
    }

    fn search_in(
        &mut self,
        pattern: &str,
        limit: usize,
        scope: Option<&str>,
    ) -> Result<Vec<SearchUnit>, String> {
        let units = self.units(pattern, limit, scope)?;
        self.log.push(SearchRecord {
            query: pattern.to_owned(),
            scope: scope.map(ToOwned::to_owned),
            returned: units.len(),
        });
        Ok(units)
    }

    fn read(&mut self, path: &str) -> Result<Option<String>, String> {
        Ok(fs::read_to_string(self.root.join(path)).ok())
    }
}

fn line_starts(text: &str) -> Vec<usize> {
    let mut starts = vec![0];
    for (index, byte) in text.as_bytes().iter().enumerate() {
        if *byte == b'\n' {
            starts.push(index + 1);
        }
    }
    starts
}

fn line_bounds(text: &str, start_line: usize, end_line: usize) -> Option<(usize, usize)> {
    if start_line == 0 || end_line < start_line {
        return None;
    }
    let line_count = text.split_inclusive('\n').count();
    if end_line > line_count {
        return None;
    }
    let starts = line_starts(text);
    let start = starts[start_line - 1];
    let mut end = starts.get(end_line).copied().unwrap_or(text.len());
    if text.as_bytes().get(end.wrapping_sub(1)) == Some(&b'\n') {
        end = end.saturating_sub(1);
        if text.as_bytes().get(end.wrapping_sub(1)) == Some(&b'\r') {
            end = end.saturating_sub(1);
        }
    }
    (end >= start).then_some((start, end))
}

fn span_text(text: &str, start_line: usize, end_line: usize) -> Option<String> {
    let (start, end) = line_bounds(text, start_line, end_line)?;
    text.get(start..end).map(ToOwned::to_owned)
}

const DECL_PREFIXES: &[&str] = &[
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
];

fn is_declaration(line: &str) -> bool {
    let trimmed = line.trim_start();
    DECL_PREFIXES
        .iter()
        .any(|prefix| trimmed.starts_with(prefix))
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
        let head = &trimmed[..open];
        if let Some(candidate) = head.split_whitespace().last() {
            let name = candidate.trim();
            if !name.is_empty() && !name.contains('=') && name != "get" && name != "set" {
                return Some(name.to_owned());
            }
        }
    }
    let head = trimmed.split('{').next().unwrap_or(trimmed).trim();
    let mut head_tokens = head
        .split(|ch: char| !(ch.is_alphanumeric() || ch == '_'))
        .filter(|token| !token.is_empty())
        .collect::<Vec<_>>();
    head_tokens
        .pop()
        .filter(|name| *name != "get" && *name != "set")
        .map(ToOwned::to_owned)
}

fn enclosing_declaration(
    text: &str,
    start_line: usize,
    end_line: usize,
) -> Option<(String, usize, usize)> {
    let lines = text.lines().collect::<Vec<_>>();
    if lines.is_empty() {
        return None;
    }
    let last = lines.len();
    let start = start_line.clamp(1, last);
    let mut decl_start = None;
    let limit = start.saturating_sub(200).max(1);
    for offset in (limit - 1..start).rev() {
        let line = lines[offset];
        if is_declaration(line) && declaration_name(line).is_some() {
            decl_start = Some(offset + 1);
            break;
        }
    }
    let decl_start = decl_start?;
    let name = declaration_name(lines[decl_start - 1])?;
    let mut depth = 0i64;
    let mut decl_end = end_line.clamp(decl_start, last);
    for (offset, line) in lines.iter().enumerate().skip(decl_start - 1) {
        depth += i64::try_from(line.matches('{').count()).unwrap_or(0);
        depth -= i64::try_from(line.matches('}').count()).unwrap_or(0);
        decl_end = offset + 1;
        if depth <= 0 && offset + 1 >= start {
            break;
        }
        if offset + 1 - decl_start > 400 {
            break;
        }
    }
    Some((name, decl_start, decl_end))
}

fn overlaps(unit: &SearchUnit, path: &str, start_line: usize, end_line: usize) -> bool {
    unit.path == path && unit.start_line <= end_line && start_line <= unit.end_line
}

fn main() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let corpus: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/corpus-manifest.json"))
            .expect("corpus manifest"),
    )
    .expect("valid corpus manifest");
    let root = PathBuf::from(corpus["source_root"].as_str().expect("source root"));
    let manifest_paths = corpus["files"]
        .as_array()
        .expect("manifest files")
        .iter()
        .filter_map(|file| file["path"].as_str().map(ToOwned::to_owned))
        .collect::<BTreeSet<_>>();
    let dev: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/cases/dev.json")).expect("dev cases"),
    )
    .expect("valid dev cases");

    let budget = WorkflowBudget {
        max_tool_calls: 24,
        max_files: 96,
        max_source_reads: 160,
        scope_roots: ["Webs", "Services", "DBContexts", "DB"]
            .iter()
            .map(ToString::to_string)
            .collect(),
        ..WorkflowBudget::default()
    };

    let started = Instant::now();
    let mut case_records = Vec::new();
    let mut span_records = Vec::new();
    let mut gold_by_case: BTreeMap<String, Vec<GoldSpan>> = BTreeMap::new();

    for case in dev["cases"].as_array().expect("cases") {
        let id = case["id"].as_str().expect("case id").to_owned();
        if !CASES.contains(&id.as_str()) {
            continue;
        }
        let question = case["question"].as_str().expect("question").to_owned();
        for reference in case["source_refs"].as_array().into_iter().flatten() {
            let path = reference["path"].as_str().expect("path").to_owned();
            let anchor = reference["anchor_id"].as_str().unwrap_or("?").to_owned();
            let Ok(text) = fs::read_to_string(root.join(&path)) else {
                continue;
            };
            for span in reference["spans"].as_array().into_iter().flatten() {
                let start_line = usize::try_from(span["start_line"].as_u64().expect("start"))
                    .expect("start fits usize");
                let end_line = usize::try_from(span["end_line"].as_u64().expect("end"))
                    .expect("end fits usize");
                let declaration = enclosing_declaration(&text, start_line, end_line);
                gold_by_case.entry(id.clone()).or_default().push(GoldSpan {
                    case: id.clone(),
                    anchor: anchor.clone(),
                    path: path.clone(),
                    start_line,
                    end_line,
                    text: span_text(&text, start_line, end_line).unwrap_or_default(),
                    declaration: declaration.as_ref().map(|(name, ..)| name.clone()),
                    declaration_range: declaration.map(|(_, start, end)| (start, end)),
                });
            }
        }

        let mut source = EngineSource::new(&root).expect("engine source");
        let case_started = Instant::now();
        let outcome = run(&mut source, &root, &question, budget.clone()).expect("workflow run");
        let elapsed_ms = case_started.elapsed().as_secs_f64() * 1000.0;
        let definition = define(&question);
        let pool_files = outcome
            .candidates
            .iter()
            .map(|unit| unit.path.clone())
            .collect::<BTreeSet<_>>();

        for gold in gold_by_case.get(&id).cloned().unwrap_or_default() {
            let file_bytes = fs::metadata(root.join(&gold.path))
                .map_or(0, |meta| usize::try_from(meta.len()).unwrap_or(0));
            let admitted = budget
                .allowed_extensions
                .iter()
                .any(|extension| gold.path.to_lowercase().ends_with(&format!(".{extension}")))
                && file_bytes <= budget.max_file_bytes;
            let pooled = outcome
                .candidates
                .iter()
                .any(|unit| overlaps(unit, &gold.path, gold.start_line, gold.end_line));
            let pooled_file = pool_files.contains(&gold.path);
            let selected = outcome.selected.iter().any(|span| {
                span.path == gold.path
                    && span.start_line <= gold.end_line
                    && gold.start_line <= span.end_line
            });

            let mut handle_in_span = Vec::new();
            let mut handle_in_declaration = Vec::new();
            let mut queries = definition.code_tokens.clone();
            queries.extend(definition.phrases.clone());
            if !definition.anchors.is_empty() {
                queries.push(definition.anchors.join(" "));
            }
            for query in &queries {
                let needle = query.to_lowercase();
                if needle.is_empty() {
                    continue;
                }
                if gold.text.to_lowercase().contains(&needle) {
                    handle_in_span.push(query.clone());
                }
                if let Some((start, end)) = gold.declaration_range {
                    if let Some(decl_text) = span_text(
                        &fs::read_to_string(root.join(&gold.path)).unwrap_or_default(),
                        start,
                        end,
                    ) {
                        if decl_text.to_lowercase().contains(&needle) {
                            handle_in_declaration.push(query.clone());
                        }
                    }
                }
            }

            // Rank of this span's file for each DEFINE query inside the measured
            // limit, then under an unlimited scan.
            let mut rank_within_limit = None;
            let mut rank_full_scan = None;
            let mut full_scan_overlaps = false;
            for query in handle_in_span.iter().chain(handle_in_declaration.iter()) {
                let units = source
                    .units(query, budget.max_tool_calls.max(250), None)
                    .unwrap_or_default();
                let measured = units
                    .iter()
                    .take(250)
                    .position(|unit| unit.path == gold.path);
                if let Some(rank) = measured {
                    rank_within_limit =
                        Some(rank_within_limit.map_or(rank, |best: usize| best.min(rank)));
                }
                let full = source
                    .units(query, FULL_SCAN_LIMIT, None)
                    .unwrap_or_default();
                if let Some(rank) = full.iter().position(|unit| unit.path == gold.path) {
                    rank_full_scan =
                        Some(rank_full_scan.map_or(rank, |best: usize| best.min(rank)));
                }
                if full
                    .iter()
                    .any(|unit| overlaps(unit, &gold.path, gold.start_line, gold.end_line))
                {
                    full_scan_overlaps = true;
                }
            }

            let symbol_query = gold.declaration.clone();
            let symbol_rank = symbol_query.as_ref().and_then(|name| {
                source
                    .units(name, SYMBOL_LIMIT, None)
                    .ok()
                    .and_then(|units| units.iter().position(|unit| unit.path == gold.path))
            });

            span_records.push(SpanRecord {
                case: gold.case.clone(),
                anchor: gold.anchor.clone(),
                path: gold.path.clone(),
                start_line: gold.start_line,
                end_line: gold.end_line,
                in_manifest: manifest_paths.contains(&gold.path),
                admitted,
                file_bytes,
                pooled,
                pooled_file,
                pool_hits: outcome
                    .candidates
                    .iter()
                    .filter(|unit| overlaps(unit, &gold.path, gold.start_line, gold.end_line))
                    .map(|unit| UnitRecord {
                        path: unit.path.clone(),
                        start_line: unit.start_line,
                        end_line: unit.end_line,
                    })
                    .collect(),
                pool_units_in_file: outcome
                    .candidates
                    .iter()
                    .filter(|unit| unit.path == gold.path)
                    .take(8)
                    .map(|unit| UnitRecord {
                        path: unit.path.clone(),
                        start_line: unit.start_line,
                        end_line: unit.end_line,
                    })
                    .collect(),
                selected,
                selected_units_in_file: outcome
                    .selected
                    .iter()
                    .filter(|span| span.path == gold.path)
                    .map(|span| UnitRecord {
                        path: span.path.clone(),
                        start_line: span.start_line,
                        end_line: span.end_line,
                    })
                    .collect(),
                handle_in_span,
                handle_in_declaration,
                rank_within_limit,
                rank_full_scan,
                full_scan_overlaps,
                symbol_query,
                symbol_rank,
                symbol_found_in_limit: symbol_rank.is_some(),
            });
        }

        case_records.push(CaseRecord {
            id: id.clone(),
            question,
            code_tokens: definition.code_tokens,
            anchors: definition.anchors,
            phrases: definition.phrases,
            tool_calls: outcome.stats.tool_calls,
            pool: outcome.candidates.len(),
            pool_files: pool_files.len(),
            selected: outcome.selected.len(),
            searches: source.log.clone(),
            selected_units: outcome
                .selected
                .iter()
                .map(|span| UnitRecord {
                    path: span.path.clone(),
                    start_line: span.start_line,
                    end_line: span.end_line,
                })
                .collect(),
            elapsed_ms,
        });
    }

    let receipt = Receipt {
        status: "R4_DISCOVERY_FORENSICS",
        branch_head: command(&repo, &["git", "rev-parse", "HEAD"]),
        corpus_root: root.display().to_string(),
        budget,
        cases: case_records,
        spans: span_records,
        elapsed_ms: started.elapsed().as_secs_f64() * 1000.0,
    };
    let text = serde_json::to_string_pretty(&receipt).expect("serialize receipt");
    let out = repo.join("benchmarks/rag-impact/results/r4-discovery-forensics-20261115.json");
    fs::write(&out, &text).expect("write receipt");
    println!("{text}");
    eprintln!("receipt: {}", out.display());
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
