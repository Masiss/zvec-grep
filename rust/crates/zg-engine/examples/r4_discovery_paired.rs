//! Paired R4 discovery measurement on the frozen development cases.
//!
//! Arm `fixed_fts`: today's deployable baseline — one indexed FTS route over the
//! question's code tokens, first 10 results, no expansion, no verification.
//! Arm `workflow`: FTS seed + backend-filtered lexical + exact symbol lookup +
//! typed C#/SQL fact expansion + hash-verified selection.
//!
//! Both arms run on the same isolated corpus copy and index, with the same
//! budget. The frozen corpus is never modified. Usage:
//! `cargo run -q -p zg-engine --example r4_discovery_paired -- <corpus-root>`

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::Serialize;
use tokio::runtime::Runtime;
use zg_engine::ZvecGrep;
use zg_engine::api::context::ContextOptions;
use zg_engine::api::context::options::{ContextRoute, ContextRouteMode, RefreshPolicy};
use zg_engine::api::symbols::SymbolsOptions;
use zg_engine::rag::workflow::{
    AbstainReason, CandidateSource, EvidenceRole, EvidenceSource, SearchUnit, WorkflowBudget,
    WorkflowOutcome, define, run,
};

const CASES: &[&str] = &[
    "D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10", "D11", "D12",
];
const GLOBS: &[&str] = &[
    "*.cs", "*.sql", "*.cshtml", "*.js", "*.ts", "*.tsx", "*.jsx", "*.py", "*.md", "*.txt",
];

#[derive(Clone, Debug)]
struct Gold {
    case: String,
    path: String,
    start_line: usize,
    end_line: usize,
}

#[derive(Serialize)]
struct CaseResult {
    id: String,
    gold: usize,
    fts_covered: usize,
    fts_recall: f64,
    fts_selected: usize,
    fts_ms: f64,
    workflow_pool_covered: usize,
    workflow_pool: usize,
    workflow_covered: usize,
    workflow_selected: usize,
    workflow_verified: usize,
    workflow_recall: f64,
    workflow_reads: usize,
    workflow_calls: usize,
    workflow_symbol_calls: usize,
    workflow_ms: f64,
    workflow_abstain: Option<AbstainReason>,
    workflow_gaps: Vec<String>,
    declaration_spans: usize,
    module_spans: usize,
    reference_spans: usize,
    proc_spans: usize,
}

#[derive(Serialize)]
struct Scenario {
    id: String,
    selected: usize,
    calls: usize,
    symbol_calls: usize,
    duplicate_calls: usize,
    abstain: Option<AbstainReason>,
    gaps: Vec<String>,
    elapsed_ms: f64,
}

#[derive(Serialize)]
struct HostState {
    loadavg: String,
    mem_available_kib: u64,
    pss_kib: Option<u64>,
    rss_kib: Option<u64>,
}

#[derive(Serialize)]
struct Receipt {
    status: &'static str,
    branch_head: String,
    corpus_root: String,
    corpus_files: usize,
    corpus_manifest_sha256: String,
    cases: Vec<String>,
    gold_spans: usize,
    budget: WorkflowBudget,
    host_before: HostState,
    host_after: HostState,
    macro_fts_recall: f64,
    macro_workflow_recall: f64,
    macro_delta_pp: f64,
    citation_accuracy: f64,
    pool_coverage: usize,
    selected_coverage: usize,
    fts_total_selected: usize,
    workflow_total_selected: usize,
    workflow_total_reads: usize,
    workflow_total_calls: usize,
    workflow_symbol_calls: usize,
    content_bytes: usize,
    token_estimate: usize,
    token_estimate_note: &'static str,
    total_fts_ms: f64,
    total_workflow_ms: f64,
    cases_detail: Vec<CaseResult>,
    scenarios: Vec<Scenario>,
}

struct EngineSource {
    runtime: Runtime,
    engine: ZvecGrep,
    root: PathBuf,
    calls: usize,
    cancel_after: Option<usize>,
}

impl EngineSource {
    fn new(root: &Path) -> Result<Self, String> {
        Ok(Self {
            runtime: Runtime::new().map_err(|error| error.to_string())?,
            engine: ZvecGrep::new(),
            root: root.to_path_buf(),
            calls: 0,
            cancel_after: None,
        })
    }

    fn search_units(
        &mut self,
        pattern: &str,
        limit: usize,
        scope: Option<&str>,
        extensions: &[String],
    ) -> Result<Vec<SearchUnit>, String> {
        let globs = if extensions.is_empty() {
            Vec::new()
        } else {
            extensions
                .iter()
                .map(|extension| format!("*.{extension}"))
                .collect::<Vec<_>>()
        };
        let options = ContextOptions {
            rg: true,
            query: Some(pattern.to_owned()),
            root: Some(self.root.clone()),
            rg_paths: scope
                .map(|scope| vec![self.root.join(scope)])
                .unwrap_or_default(),
            globs,
            limit: Some(limit),
            auto_update: false,
            ..ContextOptions::default()
        };
        let result = self
            .runtime
            .block_on(self.engine.context(options))
            .map_err(|error| error.report().to_string())?;
        Ok(result
            .items
            .iter()
            .filter_map(|item| {
                Some(SearchUnit {
                    path: item.relative_path.to_string_lossy().replace('\\', "/"),
                    start_line: item.range.start_line()?,
                    end_line: item.range.last_line()?,
                })
            })
            .collect())
    }

    fn fts_units(&mut self, query: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        let options = ContextOptions {
            root: Some(self.root.clone()),
            routes: vec![ContextRoute {
                mode: ContextRouteMode::Fts,
                query: query.to_owned(),
            }],
            limit: Some(limit),
            auto_update: false,
            refresh: Some(RefreshPolicy::Off),
            ..ContextOptions::default()
        };
        let result = self
            .runtime
            .block_on(self.engine.context(options))
            .map_err(|error| error.report().to_string())?;
        Ok(result
            .items
            .iter()
            .filter_map(|item| {
                Some(SearchUnit {
                    path: item.relative_path.to_string_lossy().replace('\\', "/"),
                    start_line: item.range.start_line()?,
                    end_line: item.range.last_line()?,
                })
            })
            .collect())
    }

    fn symbol_units(&mut self, name: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        let options = SymbolsOptions {
            root: Some(self.root.clone()),
            name: name.to_owned(),
            limit: Some(limit),
            auto_update: false,
            refresh: Some(RefreshPolicy::Off),
            ..SymbolsOptions::default()
        };
        let result = self
            .runtime
            .block_on(self.engine.symbols(options))
            .map_err(|error| error.report().to_string())?;
        Ok(result
            .items
            .iter()
            .filter_map(|item| {
                Some(SearchUnit {
                    path: item.relative_path.to_string_lossy().replace('\\', "/"),
                    start_line: item.range.start_line()?,
                    end_line: item.range.last_line()?,
                })
            })
            .collect())
    }
}

impl EvidenceSource for EngineSource {
    fn search(&mut self, pattern: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        self.calls += 1;
        self.search_units(pattern, limit, None, &[])
    }

    fn search_in(
        &mut self,
        pattern: &str,
        limit: usize,
        scope: Option<&str>,
    ) -> Result<Vec<SearchUnit>, String> {
        self.calls += 1;
        self.search_units(pattern, limit, scope, &[])
    }

    fn search_filtered(
        &mut self,
        pattern: &str,
        limit: usize,
        scope: Option<&str>,
        extensions: &[String],
    ) -> Result<Vec<SearchUnit>, String> {
        self.calls += 1;
        self.search_units(pattern, limit, scope, extensions)
    }

    fn symbol(&mut self, name: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        self.calls += 1;
        self.symbol_units(name, limit)
    }

    fn fts(&mut self, query: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        self.calls += 1;
        self.fts_units(query, limit)
    }

    fn read(&mut self, path: &str) -> Result<Option<String>, String> {
        Ok(fs::read_to_string(self.root.join(path)).ok())
    }

    fn is_cancelled(&self) -> bool {
        self.cancel_after.is_some_and(|limit| self.calls >= limit)
    }
}

fn pss_kib() -> Option<u64> {
    let text = fs::read_to_string("/proc/self/smaps_rollup").ok()?;
    text.lines()
        .find_map(|line| line.strip_prefix("Pss:"))
        .and_then(|value| value.split_whitespace().next())
        .and_then(|value| value.parse().ok())
}

fn rss_kib() -> Option<u64> {
    let text = fs::read_to_string("/proc/self/status").ok()?;
    text.lines()
        .find_map(|line| line.strip_prefix("VmHWM:"))
        .and_then(|value| value.split_whitespace().next())
        .and_then(|value| value.parse().ok())
}

fn mem_available_kib() -> u64 {
    fs::read_to_string("/proc/meminfo")
        .ok()
        .and_then(|text| {
            text.lines().find_map(|line| {
                line.strip_prefix("MemAvailable:")
                    .and_then(|value| value.split_whitespace().next())
                    .and_then(|value| value.parse().ok())
            })
        })
        .unwrap_or(0)
}

fn host_state() -> HostState {
    HostState {
        loadavg: fs::read_to_string("/proc/loadavg")
            .unwrap_or_default()
            .trim()
            .to_owned(),
        mem_available_kib: mem_available_kib(),
        pss_kib: pss_kib(),
        rss_kib: rss_kib(),
    }
}

fn sha256_file(path: &Path) -> String {
    use sha2::{Digest, Sha256};
    let bytes = fs::read(path).unwrap_or_default();
    hex::encode(Sha256::digest(&bytes))
}

fn read_gold(repo: &Path) -> Vec<Gold> {
    let dev: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/cases/dev.json")).expect("dev cases"),
    )
    .expect("valid dev cases");
    let mut gold = Vec::new();
    for case in dev["cases"].as_array().expect("cases") {
        let id = case["id"].as_str().expect("case id").to_owned();
        if !CASES.contains(&id.as_str()) {
            continue;
        }
        for reference in case["source_refs"].as_array().into_iter().flatten() {
            let path = reference["path"].as_str().expect("path").to_owned();
            for span in reference["spans"].as_array().into_iter().flatten() {
                gold.push(Gold {
                    case: id.clone(),
                    path: path.clone(),
                    start_line: usize::try_from(span["start_line"].as_u64().expect("start"))
                        .expect("start fits usize"),
                    end_line: usize::try_from(span["end_line"].as_u64().expect("end"))
                        .expect("end fits usize"),
                });
            }
        }
    }
    gold
}

fn covered(gold: &[Gold], units: &[(String, usize, usize)]) -> usize {
    gold.iter()
        .filter(|span| {
            units.iter().any(|(path, start, end)| {
                path == &span.path && *start <= span.end_line && span.start_line <= *end
            })
        })
        .count()
}

fn mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.iter().sum::<f64>() / values.len() as f64
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

#[allow(clippy::too_many_lines)]
fn main() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let corpus_manifest = repo.join("benchmarks/rag-impact/corpus-manifest.json");
    let manifest: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&corpus_manifest).expect("corpus manifest"))
            .expect("valid corpus manifest");
    let root = std::env::args().nth(1).map_or_else(
        || PathBuf::from(manifest["source_root"].as_str().expect("root")),
        PathBuf::from,
    );
    let dev: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/cases/dev.json")).expect("dev cases"),
    )
    .expect("valid dev cases");
    let questions = dev["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .filter_map(|case| {
            let id = case["id"].as_str()?;
            if !CASES.contains(&id) {
                return None;
            }
            Some((id.to_owned(), case["question"].as_str()?.to_owned()))
        })
        .collect::<Vec<_>>();
    let gold = read_gold(&repo);
    let host_before = host_state();
    let budget = WorkflowBudget {
        max_tool_calls: 24,
        max_files: 96,
        max_source_reads: 160,
        fts_quota: 3,
        scope_roots: ["Webs", "Services", "DBContexts", "DB"]
            .iter()
            .map(ToString::to_string)
            .collect(),
        ..WorkflowBudget::default()
    };

    let mut details = Vec::new();
    let mut fts_recalls = Vec::new();
    let mut workflow_recalls = Vec::new();
    let mut totals = Totals::default();
    for (id, question) in &questions {
        let case_gold = gold
            .iter()
            .filter(|span| &span.case == id)
            .cloned()
            .collect::<Vec<_>>();

        let mut source = EngineSource::new(&root).expect("engine source");
        let definition = define(question);
        let fts_query = definition.code_tokens.join(" ");
        let started = Instant::now();
        let fts_units = if fts_query.is_empty() {
            Vec::new()
        } else {
            source.fts_units(&fts_query, 10).expect("fts")
        };
        let fts_ms = started.elapsed().as_secs_f64() * 1000.0;
        let fts_flat = fts_units
            .iter()
            .map(|unit| (unit.path.clone(), unit.start_line, unit.end_line))
            .collect::<Vec<_>>();
        let fts_covered = covered(&case_gold, &fts_flat);

        let mut workflow_source = EngineSource::new(&root).expect("engine source");
        let started = Instant::now();
        let outcome: WorkflowOutcome =
            run(&mut workflow_source, &root, question, budget.clone()).expect("workflow run");
        let workflow_ms = started.elapsed().as_secs_f64() * 1000.0;
        let pool = outcome
            .candidates
            .iter()
            .map(|unit| (unit.path.clone(), unit.start_line, unit.end_line))
            .collect::<Vec<_>>();
        let selected = outcome
            .selected
            .iter()
            .map(|span| (span.path.clone(), span.start_line, span.end_line))
            .collect::<Vec<_>>();
        let pool_covered = covered(&case_gold, &pool);
        let selected_covered = covered(&case_gold, &selected);
        let verified = outcome.selected.iter().filter(|span| span.verified).count();
        let fts_recall = if case_gold.is_empty() {
            0.0
        } else {
            fts_covered as f64 / case_gold.len() as f64
        };
        let workflow_recall = if case_gold.is_empty() {
            0.0
        } else {
            selected_covered as f64 / case_gold.len() as f64
        };
        fts_recalls.push(fts_recall);
        workflow_recalls.push(workflow_recall);
        totals.fts_selected += fts_units.len();
        totals.workflow_selected += outcome.selected.len();
        totals.workflow_verified += verified;
        totals.workflow_reads += outcome.stats.bytes_read;
        totals.workflow_calls += outcome.stats.tool_calls;
        totals.workflow_symbol_calls += outcome.stats.symbol_calls;
        totals.content_bytes += outcome.stats.selected_bytes;
        totals.fts_ms += fts_ms;
        totals.workflow_ms += workflow_ms;
        totals.pool_coverage += pool_covered;
        totals.selected_coverage += selected_covered;
        let role_count = |role: EvidenceRole| {
            outcome
                .selected
                .iter()
                .filter(|span| span.role == role)
                .count()
        };
        details.push(CaseResult {
            id: (*id).to_owned(),
            gold: case_gold.len(),
            fts_covered,
            fts_recall,
            fts_selected: fts_units.len(),
            fts_ms,
            workflow_pool_covered: pool_covered,
            workflow_pool: pool.len(),
            workflow_covered: selected_covered,
            workflow_selected: outcome.selected.len(),
            workflow_verified: verified,
            workflow_recall,
            workflow_reads: outcome.stats.bytes_read,
            workflow_calls: outcome.stats.tool_calls,
            workflow_symbol_calls: outcome.stats.symbol_calls,
            workflow_ms,
            workflow_abstain: outcome.abstain.clone(),
            workflow_gaps: outcome.gaps.keys().cloned().collect(),
            declaration_spans: role_count(EvidenceRole::Declaration),
            module_spans: role_count(EvidenceRole::SeedHit) + role_count(EvidenceRole::Reference),
            reference_spans: role_count(EvidenceRole::Reference),
            proc_spans: role_count(EvidenceRole::ProcDefinition)
                + role_count(EvidenceRole::TableAccess),
        });
    }

    let citation_accuracy = if totals.workflow_selected == 0 {
        0.0
    } else {
        totals.workflow_verified as f64 / totals.workflow_selected as f64
    };
    let mut scenarios = Vec::new();
    for (id, question, cancel_after, small_budget) in [
        ("no_evidence", "zzzznottoken qqqqabsentword", None, false),
        (
            "budget_one_call",
            "EmployeeEdit EmployeeEditThongTinCaNhan DbSet",
            None,
            true,
        ),
        (
            "cancel_after_one",
            "EmployeeEdit EmployeeEditThongTinCaNhan DbSet",
            Some(1),
            false,
        ),
        ("duplicate_query", "EmployeeEdit EmployeeEdit", None, false),
    ] {
        let mut source = EngineSource::new(&root).expect("engine source");
        source.cancel_after = cancel_after;
        let run_budget = if small_budget {
            WorkflowBudget {
                max_tool_calls: 1,
                ..budget.clone()
            }
        } else {
            budget.clone()
        };
        let started = Instant::now();
        let outcome = run(&mut source, &root, question, run_budget).expect("workflow run");
        scenarios.push(Scenario {
            id: id.to_owned(),
            selected: outcome.selected.len(),
            calls: outcome.stats.tool_calls,
            symbol_calls: outcome.stats.symbol_calls,
            duplicate_calls: outcome.stats.duplicate_calls,
            abstain: outcome.abstain,
            gaps: outcome.gaps.keys().cloned().collect(),
            elapsed_ms: started.elapsed().as_secs_f64() * 1000.0,
        });
    }

    let receipt = Receipt {
        status: "R4_DISCOVERY_PAIRED",
        branch_head: command(&repo, &["git", "rev-parse", "HEAD"]),
        corpus_root: root.display().to_string(),
        corpus_files: manifest["totals"]["files"].as_u64().unwrap_or(0) as usize,
        corpus_manifest_sha256: sha256_file(&corpus_manifest),
        cases: CASES.iter().map(ToString::to_string).collect(),
        gold_spans: gold.len(),
        budget: budget.clone(),
        host_before,
        host_after: host_state(),
        macro_fts_recall: mean(&fts_recalls),
        macro_workflow_recall: mean(&workflow_recalls),
        macro_delta_pp: (mean(&workflow_recalls) - mean(&fts_recalls)) * 100.0,
        citation_accuracy,
        pool_coverage: totals.pool_coverage,
        selected_coverage: totals.selected_coverage,
        fts_total_selected: totals.fts_selected,
        workflow_total_selected: totals.workflow_selected,
        workflow_total_reads: totals.workflow_reads,
        workflow_total_calls: totals.workflow_calls,
        workflow_symbol_calls: totals.workflow_symbol_calls,
        content_bytes: totals.content_bytes,
        token_estimate: totals.content_bytes / 4,
        token_estimate_note: "ESTIMATE = content bytes / 4; no tokenizer was run",
        total_fts_ms: totals.fts_ms,
        total_workflow_ms: totals.workflow_ms,
        cases_detail: details,
        scenarios,
    };
    let text = serde_json::to_string_pretty(&receipt).expect("serialize receipt");
    let out = repo.join("benchmarks/rag-impact/results/r4-discovery-paired-20261115.json");
    fs::write(&out, &text).expect("write receipt");
    println!(
        "fts={:.4} workflow={:.4} delta={:+.2}pp pool={}/{} selected={} citation={:.3}",
        receipt.macro_fts_recall,
        receipt.macro_workflow_recall,
        receipt.macro_delta_pp,
        receipt.pool_coverage,
        receipt.gold_spans,
        receipt.selected_coverage,
        receipt.citation_accuracy
    );
    eprintln!("receipt: {}", out.display());
}

#[derive(Default)]
struct Totals {
    fts_selected: usize,
    workflow_selected: usize,
    workflow_verified: usize,
    workflow_reads: usize,
    workflow_calls: usize,
    workflow_symbol_calls: usize,
    content_bytes: usize,
    fts_ms: f64,
    workflow_ms: f64,
    pool_coverage: usize,
    selected_coverage: usize,
}

#[allow(dead_code)]
fn unused(_: CandidateSource, _: &BTreeSet<String>) {}
