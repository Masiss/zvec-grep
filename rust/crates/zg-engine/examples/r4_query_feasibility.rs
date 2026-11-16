//! Read-only feasibility probe for three query-understanding directions.
//!
//! 1. `agent_plan`: the frozen external-agent plan (queries + symbols) for the
//!    blind dev-independent cases E1-E5.
//! 2. `vector`: the existing Potion Code 16M v2 index vectors, question text only.
//! 3. `hybrid`: the engine's default question route on the same index.
//!
//! Gold overlap is computed only after the plan file was written and hashed.
//! Nothing here writes to the corpus index or the production workspace.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::Serialize;
use tokio::runtime::Runtime;
use zg_engine::ZvecGrep;
use zg_engine::api::context::ContextOptions;
use zg_engine::api::context::options::{ContextRoute, ContextRouteMode, RefreshPolicy};
use zg_engine::api::symbols::SymbolsOptions;

const CHUNK: usize = 10;

#[derive(Serialize, Clone)]
struct Gold {
    case: String,
    path: String,
    start_line: usize,
    end_line: usize,
}

#[derive(Serialize)]
struct Unit {
    path: String,
    start_line: usize,
    end_line: usize,
}

#[derive(Serialize)]
struct ArmResult {
    case: String,
    gold: usize,
    gold_files: usize,
    files_hit: usize,
    covered: usize,
    units: usize,
    calls: usize,
    ms: f64,
}

#[derive(Serialize)]
struct CaseReport {
    id: String,
    question: String,
    gold: usize,
    plan_queries: Vec<String>,
    plan_symbols: Vec<String>,
    agent_plan: ArmResult,
    vector: ArmResult,
    hybrid: ArmResult,
    plan_query_hits: Vec<usize>,
    plan_symbol_hits: Vec<usize>,
}

#[derive(Serialize)]
struct Receipt {
    kind: &'static str,
    plan_sha256: String,
    corpus_root: String,
    cases: Vec<CaseReport>,
    agent_plan_total: (usize, usize),
    vector_total: (usize, usize),
    hybrid_total: (usize, usize),
    agent_plan_ms: f64,
    vector_ms: f64,
    hybrid_ms: f64,
    notes: Vec<&'static str>,
}

fn gold_of(case: &serde_json::Value) -> Vec<Gold> {
    let mut gold = Vec::new();
    let id = case["id"].as_str().unwrap_or_default().to_owned();
    for reference in case["source_refs"].as_array().into_iter().flatten() {
        let path = reference["path"].as_str().unwrap_or_default().to_owned();
        for span in reference["spans"].as_array().into_iter().flatten() {
            gold.push(Gold {
                case: id.clone(),
                path: path.clone(),
                start_line: span["start_line"].as_u64().unwrap_or(0) as usize,
                end_line: span["end_line"].as_u64().unwrap_or(0) as usize,
            });
        }
    }
    gold
}

fn covered(gold: &[Gold], units: &[Unit]) -> usize {
    gold.iter()
        .filter(|span| {
            units.iter().any(|unit| {
                unit.path == span.path
                    && unit.start_line <= span.end_line
                    && span.start_line <= unit.end_line
            })
        })
        .count()
}

fn files_hit(gold: &[Gold], units: &[Unit]) -> usize {
    let mut files: Vec<&str> = gold.iter().map(|span| span.path.as_str()).collect();
    files.sort_unstable();
    files.dedup();
    files
        .iter()
        .filter(|path| units.iter().any(|unit| unit.path == **path))
        .count()
}

fn gold_files(gold: &[Gold]) -> usize {
    let mut files: Vec<&str> = gold.iter().map(|span| span.path.as_str()).collect();
    files.sort_unstable();
    files.dedup();
    files.len()
}

struct Probe {
    runtime: Runtime,
    engine: ZvecGrep,
    root: PathBuf,
    calls: usize,
}

impl Probe {
    fn new(root: &Path) -> Result<Self, String> {
        Ok(Self {
            runtime: Runtime::new().map_err(|error| error.to_string())?,
            engine: ZvecGrep::new(),
            root: root.to_path_buf(),
            calls: 0,
        })
    }

    fn fallback(mut units: Vec<Unit>, limit: usize, same_file: bool) -> Vec<Unit> {
        if same_file {
            units.sort_by(|left, right| left.start_line.cmp(&right.start_line));
        }
        units.truncate(limit);
        units
    }

    fn rg_query(&mut self, pattern: &str, limit: usize) -> Result<Vec<Unit>, String> {
        self.calls += 1;
        let options = ContextOptions {
            rg: true,
            query: Some(pattern.to_owned()),
            root: Some(self.root.clone()),
            globs: ["*.cs", "*.sql", "*.cshtml", "*.js"]
                .iter()
                .map(ToString::to_string)
                .collect(),
            limit: Some(limit),
            auto_update: false,
            ..ContextOptions::default()
        };
        let result = self
            .runtime
            .block_on(self.engine.context(options))
            .map_err(|error| error.report().to_string())?;
        Ok(Self::fallback(
            result
                .items
                .iter()
                .filter_map(|item| {
                    Some(Unit {
                        path: item.relative_path.to_string_lossy().replace('\\', "/"),
                        start_line: item.range.start_line()?,
                        end_line: item.range.last_line()?,
                    })
                })
                .collect(),
            limit,
            false,
        ))
    }

    fn symbols(&mut self, name: &str, limit: usize) -> Result<Vec<Unit>, String> {
        self.calls += 1;
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
                Some(Unit {
                    path: item.relative_path.to_string_lossy().replace('\\', "/"),
                    start_line: item.range.start_line()?,
                    end_line: item.range.last_line()?,
                })
            })
            .collect())
    }

    fn route(
        &mut self,
        mode: Option<ContextRouteMode>,
        query: &str,
        limit: usize,
    ) -> Result<Vec<Unit>, String> {
        self.calls += 1;
        let options = ContextOptions {
            root: Some(self.root.clone()),
            routes: mode
                .map(|mode| {
                    vec![ContextRoute {
                        mode,
                        query: query.to_owned(),
                    }]
                })
                .unwrap_or_default(),
            query: mode.is_none().then(|| query.to_owned()),
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
                Some(Unit {
                    path: item.relative_path.to_string_lossy().replace('\\', "/"),
                    start_line: item.range.start_line()?,
                    end_line: item.range.last_line()?,
                })
            })
            .collect())
    }
}

fn mean(pairs: &[(usize, usize)]) -> f64 {
    if pairs.is_empty() {
        return 0.0;
    }
    pairs
        .iter()
        .map(|(hit, total)| {
            if *total == 0 {
                0.0
            } else {
                *hit as f64 / *total as f64
            }
        })
        .sum::<f64>()
        / pairs.len() as f64
}

fn main() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let root = std::env::args().nth(1).map_or_else(
        || PathBuf::from("/home/masis/.cache/zvec-grep/r4-discovery-20261115/corpus"),
        PathBuf::from,
    );
    let plan_path = repo.join("benchmarks/rag-impact/results/r4-query-plan-freeze-20261115.json");
    let plan: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&plan_path).expect("frozen plan"))
            .expect("valid plan");
    let plan_sha256 = {
        use sha2::{Digest, Sha256};
        hex::encode(Sha256::digest(fs::read(&plan_path).expect("plan bytes")))
    };
    let split = std::env::args().nth(2).unwrap_or_else(|| "devi".to_owned());
    let cases_file = if split == "dev" {
        "benchmarks/rag-impact/cases/dev.json"
    } else {
        "benchmarks/rag-impact/cases/dev-independent.json"
    };
    let devi: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(repo.join(cases_file)).expect("cases"))
            .expect("valid cases");
    let case_ids: Vec<String> = if split == "dev" {
        (1..=12).map(|index| format!("D{index:02}")).collect()
    } else {
        (1..=5).map(|index| format!("E{index}")).collect()
    };

    let mut reports = Vec::new();
    let mut plan_pairs = Vec::new();
    let mut vector_pairs = Vec::new();
    let mut hybrid_pairs = Vec::new();
    let mut plan_ms = 0.0;
    let mut vector_ms = 0.0;
    let mut hybrid_ms = 0.0;

    for case in devi["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .filter(|case| {
            case_ids
                .iter()
                .any(|id| Some(id.as_str()) == case["id"].as_str())
        })
    {
        let id = case["id"].as_str().unwrap_or_default().to_owned();
        let question = case["question"].as_str().unwrap_or_default().to_owned();
        let gold = gold_of(case);
        let planned = !plan["plans"].get(&id).is_none();
        let queries: Vec<String> = plan["plans"][&id]["queries"]
            .as_array()
            .map(|values| {
                values
                    .iter()
                    .filter_map(|value| value.as_str().map(ToString::to_string))
                    .collect()
            })
            .unwrap_or_default();
        let symbols: Vec<String> = plan["plans"][&id]["symbols"]
            .as_array()
            .map(|values| {
                values
                    .iter()
                    .filter_map(|value| value.as_str().map(ToString::to_string))
                    .collect()
            })
            .unwrap_or_default();

        // 1) frozen external-agent plan
        let mut probe = Probe::new(&root).expect("probe");
        let mut plan_units: Vec<Unit> = Vec::new();
        if !planned {
            plan_units.extend(probe.rg_query(&question, 50).unwrap_or_default());
        }
        let mut query_hits = Vec::new();
        let mut symbol_hits = Vec::new();
        let started = Instant::now();
        for query in &queries {
            let units = probe.rg_query(query, 50).unwrap_or_default();
            query_hits.push(covered(&gold, &units));
            plan_units.extend(units);
        }
        for symbol in &symbols {
            let units = probe.symbols(symbol, 20).unwrap_or_default();
            symbol_hits.push(covered(&gold, &units));
            plan_units.extend(units);
        }
        plan_ms += started.elapsed().as_secs_f64() * 1000.0;
        let plan_arm = ArmResult {
            case: id.clone(),
            gold: gold.len(),
            gold_files: gold_files(&gold),
            files_hit: files_hit(&gold, &plan_units),
            covered: covered(&gold, &plan_units),
            units: plan_units.len(),
            calls: probe.calls,
            ms: started.elapsed().as_secs_f64() * 1000.0,
        };

        // 2) vector-only on the existing index
        let mut probe = Probe::new(&root).expect("probe");
        let started = Instant::now();
        let units = probe
            .route(Some(ContextRouteMode::Vector), &question, CHUNK)
            .unwrap_or_default();
        let elapsed = started.elapsed().as_secs_f64() * 1000.0;
        vector_ms += elapsed;
        let vector_arm = ArmResult {
            case: id.clone(),
            gold: gold.len(),
            gold_files: gold_files(&gold),
            files_hit: files_hit(&gold, &units),
            covered: covered(&gold, &units),
            units: units.len(),
            calls: probe.calls,
            ms: elapsed,
        };

        // 3) default hybrid question route
        let mut probe = Probe::new(&root).expect("probe");
        let started = Instant::now();
        let units = probe.route(None, &question, CHUNK).unwrap_or_default();
        let elapsed = started.elapsed().as_secs_f64() * 1000.0;
        hybrid_ms += elapsed;
        let hybrid_arm = ArmResult {
            case: id.clone(),
            gold: gold.len(),
            gold_files: gold_files(&gold),
            files_hit: files_hit(&gold, &units),
            covered: covered(&gold, &units),
            units: units.len(),
            calls: probe.calls,
            ms: elapsed,
        };

        plan_pairs.push((plan_arm.covered, gold.len()));
        vector_pairs.push((vector_arm.covered, gold.len()));
        hybrid_pairs.push((hybrid_arm.covered, gold.len()));
        eprintln!(
            "{id} gold={} plan={} vector={} hybrid={}",
            gold.len(),
            plan_arm.covered,
            vector_arm.covered,
            hybrid_arm.covered
        );
        reports.push(CaseReport {
            id,
            question,
            gold: gold.len(),
            plan_queries: queries,
            plan_symbols: symbols,
            agent_plan: plan_arm,
            vector: vector_arm,
            hybrid: hybrid_arm,
            plan_query_hits: query_hits,
            plan_symbol_hits: symbol_hits,
        });
    }

    let receipt = Receipt {
        kind: "r4_query_to_entity_feasibility",
        plan_sha256,
        corpus_root: root.display().to_string(),
        cases: reports,
        agent_plan_total: (
            plan_pairs.iter().map(|pair| pair.0).sum(),
            plan_pairs.iter().map(|pair| pair.1).sum(),
        ),
        vector_total: (
            vector_pairs.iter().map(|pair| pair.0).sum(),
            vector_pairs.iter().map(|pair| pair.1).sum(),
        ),
        hybrid_total: (
            hybrid_pairs.iter().map(|pair| pair.0).sum(),
            hybrid_pairs.iter().map(|pair| pair.1).sum(),
        ),
        agent_plan_ms: plan_ms,
        vector_ms,
        hybrid_ms,
        notes: vec![
            "gold overlap computed after the plan file was written and hashed",
            "E1-E5 are dev-independent, not held-out; they are not a qualification",
            "vector arm uses the existing Potion Code 16M v2 index; no model was changed",
            "recall here is candidate-union reachability within the arm's own window, not Recall@10 of a selected pack",
        ],
    };
    let out = repo.join(format!(
        "benchmarks/rag-impact/results/r4-query-feasibility-{split}-20261115.json"
    ));
    fs::write(&out, serde_json::to_string_pretty(&receipt).expect("json")).expect("write");
    println!(
        "plan={:.4} ({}/{}) vector={:.4} ({}/{}) hybrid={:.4} ({}/{})",
        mean(&plan_pairs),
        receipt.agent_plan_total.0,
        receipt.agent_plan_total.1,
        mean(&vector_pairs),
        receipt.vector_total.0,
        receipt.vector_total.1,
        mean(&hybrid_pairs),
        receipt.hybrid_total.0,
        receipt.hybrid_total.1
    );
    eprintln!("receipt: {}", out.display());
}
