//! Paired R4 workflow measurement on the frozen development cases.
//!
//! Arm `fixed_rg`: today's deployable behaviour — search the question's code
//! tokens with the lexical (rg) backend and take the first 10 matched ranges.
//! Arm `workflow`: DEFINE → DISCOVER → EXPAND → CHECK → FINAL with widening,
//! typed expansion, hash verification and abstention.
//!
//! The corpus is read-only. No index, model, daemon or production artifact is
//! touched. Usage:
//! `cargo run -p zg-engine --example r4_workflow_paired --release`

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::Serialize;
use tokio::runtime::Runtime;
use zg_engine::ZvecGrep;
use zg_engine::api::context::ContextOptions;
use zg_engine::rag::workflow::{
    AbstainReason, EvidenceSource, SearchUnit, WorkflowBudget, WorkflowOutcome, define, run,
};

const CASES: &[&str] = &[
    "D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10", "D11", "D12",
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
    baseline_covered: usize,
    baseline_recall: f64,
    baseline_selected: usize,
    baseline_bytes: usize,
    baseline_ms: f64,
    workflow_covered: usize,
    workflow_recall: f64,
    workflow_selected: usize,
    workflow_verified: usize,
    workflow_bytes: usize,
    workflow_bytes_read: usize,
    workflow_calls: usize,
    workflow_duplicate_calls: usize,
    workflow_rounds: usize,
    workflow_pool: usize,
    workflow_pool_covered: usize,
    workflow_abstain: Option<AbstainReason>,
    workflow_gaps: Vec<String>,
    workflow_ms: f64,
}

#[derive(Serialize)]
struct Scenario {
    id: String,
    question: String,
    selected: usize,
    tool_calls: usize,
    duplicate_calls: usize,
    abstain: Option<AbstainReason>,
    gap_keys: Vec<String>,
    elapsed_ms: f64,
}

#[derive(Serialize)]
struct Receipt {
    status: &'static str,
    branch_head: String,
    corpus_root: String,
    corpus_manifest_sha256: String,
    cases: Vec<String>,
    gold_spans: usize,
    budget: WorkflowBudget,
    host_before: HostState,
    host_after: HostState,
    cases_detail: Vec<CaseResult>,
    macro_baseline_recall: f64,
    macro_workflow_recall: f64,
    macro_delta_pp: f64,
    citation_accuracy: f64,
    totals: Totals,
    scenarios: Vec<Scenario>,
    reference_points: BTreeSet<String>,
}

#[derive(Serialize)]
struct Totals {
    baseline_selected: usize,
    workflow_selected: usize,
    workflow_verified: usize,
    workflow_bytes: usize,
    workflow_bytes_read: usize,
    workflow_calls: usize,
    workflow_pool_covered: usize,
    baseline_ms: f64,
    workflow_ms: f64,
}

#[derive(Serialize)]
struct HostState {
    loadavg: String,
    mem_available_kib: u64,
    pss_kib: Option<u64>,
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
            let Some(start_line) = item.range.start_line() else {
                continue;
            };
            let Some(end_line) = item.range.last_line() else {
                continue;
            };
            units.push(SearchUnit {
                path: item.relative_path.to_string_lossy().replace('\\', "/"),
                start_line,
                end_line,
            });
        }
        Ok(units)
    }
}

impl EvidenceSource for EngineSource {
    fn search(&mut self, pattern: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        self.calls += 1;
        self.search_units(pattern, limit, None)
    }

    fn search_in(
        &mut self,
        pattern: &str,
        limit: usize,
        scope: Option<&str>,
    ) -> Result<Vec<SearchUnit>, String> {
        self.calls += 1;
        self.search_units(pattern, limit, scope)
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

fn baseline_units(
    source: &mut EngineSource,
    question: &str,
    budget: &WorkflowBudget,
) -> Result<Vec<SearchUnit>, String> {
    let definition = define(question);
    let mut units: Vec<SearchUnit> = Vec::new();
    let mut seen = BTreeSet::new();
    for token in &definition.code_tokens {
        for unit in source.search_units(token, 50, None)? {
            if !admits(source.root.as_path(), &unit.path, budget) {
                continue;
            }
            if seen.insert(unit.clone()) {
                units.push(unit);
            }
            if units.len() >= 10 {
                return Ok(units);
            }
        }
    }
    Ok(units)
}

fn admits(root: &Path, path: &str, budget: &WorkflowBudget) -> bool {
    if !budget.allowed_extensions.is_empty() {
        let extension = Path::new(path)
            .extension()
            .and_then(std::ffi::OsStr::to_str)
            .map(str::to_lowercase)
            .unwrap_or_default();
        if !budget.allowed_extensions.contains(&extension) {
            return false;
        }
    }
    fs::metadata(root.join(path)).is_ok_and(|metadata| {
        metadata.is_file()
            && usize::try_from(metadata.len()).unwrap_or(usize::MAX) <= budget.max_file_bytes
    })
}

fn scenario(
    root: &Path,
    id: &str,
    question: &str,
    budget: WorkflowBudget,
    cancel_after: Option<usize>,
) -> Scenario {
    let mut source = EngineSource::new(root).expect("engine source");
    source.cancel_after = cancel_after;
    let started = Instant::now();
    let outcome: WorkflowOutcome = run(&mut source, root, question, budget).expect("workflow run");
    Scenario {
        id: id.to_owned(),
        question: question.to_owned(),
        selected: outcome.selected.len(),
        tool_calls: outcome.stats.tool_calls,
        duplicate_calls: outcome.stats.duplicate_calls,
        abstain: outcome.abstain,
        gap_keys: outcome.gaps.keys().cloned().collect(),
        elapsed_ms: started.elapsed().as_secs_f64() * 1000.0,
    }
}

#[allow(clippy::too_many_lines)]
fn main() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let corpus: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/corpus-manifest.json"))
            .expect("corpus manifest"),
    )
    .expect("valid corpus manifest");
    let root = PathBuf::from(corpus["source_root"].as_str().expect("source root"));
    let host_before = host_state();
    let gold = read_gold(&repo);
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
    let mut results = Vec::new();
    let mut baseline_recalls = Vec::new();
    let mut workflow_recalls = Vec::new();
    let mut totals = Totals {
        baseline_selected: 0,
        workflow_selected: 0,
        workflow_verified: 0,
        workflow_bytes: 0,
        workflow_bytes_read: 0,
        workflow_calls: 0,
        workflow_pool_covered: 0,
        baseline_ms: 0.0,
        workflow_ms: 0.0,
    };

    for (id, question) in &questions {
        let case_gold = gold
            .iter()
            .filter(|span| &span.case == id)
            .cloned()
            .collect::<Vec<_>>();
        let mut source = EngineSource::new(&root).expect("engine source");
        let started = Instant::now();
        let units = baseline_units(&mut source, question, &budget).expect("baseline search");
        let baseline_ms = started.elapsed().as_secs_f64() * 1000.0;
        let baseline_units = units
            .iter()
            .map(|unit| (unit.path.clone(), unit.start_line, unit.end_line))
            .collect::<Vec<_>>();
        let baseline_covered = covered(&case_gold, &baseline_units);
        let baseline_bytes = units
            .iter()
            .map(|unit| fs::read_to_string(root.join(&unit.path)).map_or(0, |text| text.len()))
            .sum::<usize>();

        let mut workflow_source = EngineSource::new(&root).expect("engine source");
        let started = Instant::now();
        let outcome =
            run(&mut workflow_source, &root, question, budget.clone()).expect("workflow run");
        let workflow_ms = started.elapsed().as_secs_f64() * 1000.0;
        let selected = outcome
            .selected
            .iter()
            .map(|span| (span.path.clone(), span.start_line, span.end_line))
            .collect::<Vec<_>>();
        let workflow_covered = covered(&case_gold, &selected);
        let pool = outcome
            .candidates
            .iter()
            .map(|unit| (unit.path.clone(), unit.start_line, unit.end_line))
            .collect::<Vec<_>>();
        let workflow_pool_covered = covered(&case_gold, &pool);
        let verified = outcome.selected.iter().filter(|span| span.verified).count();
        let baseline_recall = if case_gold.is_empty() {
            0.0
        } else {
            #[allow(clippy::cast_precision_loss)]
            {
                baseline_covered as f64 / case_gold.len() as f64
            }
        };
        let workflow_recall = if case_gold.is_empty() {
            0.0
        } else {
            #[allow(clippy::cast_precision_loss)]
            {
                workflow_covered as f64 / case_gold.len() as f64
            }
        };
        baseline_recalls.push(baseline_recall);
        workflow_recalls.push(workflow_recall);
        totals.baseline_selected += units.len();
        totals.workflow_selected += outcome.selected.len();
        totals.workflow_verified += verified;
        totals.workflow_bytes += outcome.stats.selected_bytes;
        totals.workflow_bytes_read += outcome.stats.bytes_read;
        totals.workflow_calls += outcome.stats.tool_calls;
        totals.workflow_pool_covered += workflow_pool_covered;
        totals.baseline_ms += baseline_ms;
        totals.workflow_ms += workflow_ms;
        results.push(CaseResult {
            id: (*id).to_owned(),
            gold: case_gold.len(),
            baseline_covered,
            baseline_recall,
            baseline_selected: units.len(),
            baseline_bytes,
            baseline_ms,
            workflow_covered,
            workflow_recall,
            workflow_selected: outcome.selected.len(),
            workflow_verified: verified,
            workflow_bytes: outcome.stats.selected_bytes,
            workflow_bytes_read: outcome.stats.bytes_read,
            workflow_calls: outcome.stats.tool_calls,
            workflow_duplicate_calls: outcome.stats.duplicate_calls,
            workflow_rounds: outcome.stats.rounds,
            workflow_pool: outcome.candidates.len(),
            workflow_pool_covered,
            workflow_abstain: outcome.abstain.clone(),
            workflow_gaps: outcome.gaps.keys().cloned().collect(),
            workflow_ms,
        });
    }

    let macro_baseline = mean(&baseline_recalls);
    let macro_workflow = mean(&workflow_recalls);
    let citation_accuracy = if totals.workflow_selected == 0 {
        0.0
    } else {
        totals.workflow_verified as f64 / totals.workflow_selected as f64
    };

    let mut scenarios = Vec::new();
    scenarios.push(scenario(
        &root,
        "no_evidence",
        "zzzznottoken qqqqabsentword",
        budget.clone(),
        None,
    ));
    scenarios.push(scenario(
        &root,
        "budget_one_call",
        "EmployeeEdit EmployeeEditThongTinCaNhan DbSet",
        WorkflowBudget {
            max_tool_calls: 1,
            ..budget.clone()
        },
        None,
    ));
    scenarios.push(scenario(
        &root,
        "cancel_after_one",
        "EmployeeEdit EmployeeEditThongTinCaNhan DbSet",
        budget.clone(),
        Some(1),
    ));
    scenarios.push(scenario(
        &root,
        "duplicate_query",
        "EmployeeEdit EmployeeEdit",
        budget.clone(),
        None,
    ));

    let receipt = Receipt {
        status: "R4_WORKFLOW_PAIRED",
        branch_head: command(&repo, &["git", "rev-parse", "HEAD"]),
        corpus_root: root.display().to_string(),
        corpus_manifest_sha256: sha256_file(
            &repo.join("benchmarks/rag-impact/corpus-manifest.json"),
        ),
        cases: CASES.iter().map(ToString::to_string).collect(),
        gold_spans: gold.len(),
        budget: budget.clone(),
        host_before,
        host_after: host_state(),
        cases_detail: results,
        macro_baseline_recall: macro_baseline,
        macro_workflow_recall: macro_workflow,
        macro_delta_pp: (macro_workflow - macro_baseline) * 100.0,
        citation_accuracy,
        totals,
        scenarios,
        reference_points: [
            "b0_oracle_best_call_0.4722".to_owned(),
            "fixed_deployable_fts_0.3750".to_owned(),
            "fixed_rg_code_tokens_0.3194".to_owned(),
            "reference_union_coverage_h6_0.875".to_owned(),
        ]
        .into_iter()
        .collect(),
    };
    let text = serde_json::to_string_pretty(&receipt).expect("serialize receipt");
    let out = repo.join("benchmarks/rag-impact/results/r4-workflow-paired-20261115.json");
    fs::write(&out, &text).expect("write receipt");
    println!("{text}");
    eprintln!("receipt: {}", out.display());
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
