//! R4b paired evaluation: hybrid baseline, hybrid+P1 selection, R4b (P1+P2).
//!
//! Arms (same frozen corpus, same index, same budget):
//! 1. `hybrid_baseline` — one hybrid question route, first 10 results.
//! 2. `hybrid_p1` — the same hybrid candidates, assembled by the P1 selector.
//! 3. `r4b` — hybrid + FTS + derived lexical + typed facts, P1 selector.
//!
//! P3 (external-agent plan) is NOT_MEASURED: no blind plan exists for the
//! D01–D12 split, and a plan written after seeing gold is contaminated. The E1–E5
//! plan is on a different split and must not be extrapolated.
//!
//! Usage: set `R4B_OUTPUT_DIR` to an external directory and `R4B_RUN_ID` to a
//! unique label, then run `cargo run -q -p zg-engine --example r4b_paired -- <corpus-root>`.

use std::collections::HashMap;
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
    AbstainReason, AgentPlan, CandidateSource, EvidenceSource, SearchUnit, WorkflowBudget, run,
    run_with_plan,
};

const CASES: &[&str] = &[
    "D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10", "D11", "D12",
];

#[derive(Clone, Debug, Serialize)]
struct Gold {
    case: String,
    path: String,
    start_line: usize,
    end_line: usize,
}

#[derive(Serialize)]
struct Arm {
    covered: usize,
    total: usize,
    recall: f64,
    items: usize,
    verified: usize,
    calls: usize,
    source_reads: usize,
    source_bytes_read: usize,
    ranked_content_bytes: usize,
    tool_content_bytes: usize,
    ms: f64,
    abstain: Option<AbstainReason>,
    routes: Vec<String>,
}

#[derive(Serialize)]
struct CaseRow {
    id: String,
    gold: usize,
    hybrid_baseline: Arm,
    hybrid_byte_bounded: Arm,
    hybrid_p1: Arm,
    r4b: Arm,
}

#[derive(Serialize)]
struct Receipt {
    kind: &'static str,
    head: String,
    corpus_root: String,
    gold_spans: usize,
    budget: WorkflowBudget,
    p3_status: &'static str,
    p3_reason: &'static str,
    macro_hybrid_baseline: f64,
    macro_hybrid_byte_bounded: f64,
    tool_content_max: usize,
    tool_content_total: usize,
    tool_content_note: &'static str,
    macro_hybrid_p1: f64,
    macro_r4b: f64,
    macro_plan: f64,
    plan_pool: usize,
    plan_selected: usize,
    plan_sha256: String,
    pool_coverage: usize,
    pool_coverage_note: &'static str,
    selected_coverage: usize,
    citation_accuracy: f64,
    rows: Vec<CaseRow>,
    host: HostState,
}

#[derive(Serialize)]
struct HostState {
    loadavg: String,
    mem_available_kib: u64,
    pss_kib: Option<u64>,
}

fn pss_kib() -> Option<u64> {
    fs::read_to_string("/proc/self/smaps_rollup")
        .ok()?
        .lines()
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

struct Source {
    runtime: Runtime,
    engine: ZvecGrep,
    root: PathBuf,
    calls: usize,
    cancel_after: Option<usize>,
    parent_reference_scope: bool,
    entity_ranges: HashMap<String, Vec<SearchUnit>>,
    hit_trace: Vec<serde_json::Value>,
}

impl Source {
    fn new(root: &Path) -> Result<Self, String> {
        Ok(Self {
            runtime: Runtime::new().map_err(|error| error.to_string())?,
            engine: ZvecGrep::new(),
            root: root.to_path_buf(),
            calls: 0,
            cancel_after: None,
            parent_reference_scope: std::env::var_os("R4B_E2_PARENT_REFS").is_some(),
            entity_ranges: HashMap::new(),
            hit_trace: Vec::new(),
        })
    }

    fn units(
        result: &zg_engine::api::context::ContextResult,
        parent_reference_scope: bool,
    ) -> Vec<SearchUnit> {
        result
            .items
            .iter()
            .filter_map(|item| {
                let range = if parent_reference_scope {
                    &item.content_range
                } else {
                    &item.range
                };
                Some(SearchUnit {
                    path: item.relative_path.to_string_lossy().replace('\\', "/"),
                    start_line: range.start_line()?,
                    end_line: range.last_line()?,
                })
            })
            .collect()
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
        self.record_hits("lexical", pattern, &result);
        Ok(Self::units(&result, self.parent_reference_scope))
    }

    fn record_hits(
        &mut self,
        route: &str,
        query: &str,
        result: &zg_engine::api::context::ContextResult,
    ) {
        if self.parent_reference_scope {
            for item in &result.items {
                if item.entity_id.is_none() {
                    continue;
                }
                let (Some(start_line), Some(end_line)) =
                    (item.range.start_line(), item.range.last_line())
                else {
                    continue;
                };
                let path = item.relative_path.to_string_lossy().replace('\\', "/");
                let range = SearchUnit {
                    path: path.clone(),
                    start_line,
                    end_line,
                };
                let ranges = self.entity_ranges.entry(path).or_default();
                if !ranges.iter().any(|known| {
                    known.start_line == range.start_line && known.end_line == range.end_line
                }) {
                    ranges.push(range);
                }
            }
        }
        self.hit_trace.extend(result.items.iter().map(|item| {
            serde_json::json!({
                "route": route,
                "query": query,
                "rank": item.rank,
                "path": item.relative_path.to_string_lossy().replace('\\', "/"),
                "entity_id": item.entity_id,
                "entity_range": [item.range.start_line(), item.range.last_line()],
                "content_range": [item.content_range.start_line(), item.content_range.last_line()],
                "match_ranges": item.match_ranges,
                "excerpt_range": item.excerpt_range,
                "score": item.score,
                "matched_by": format!("{:?}", item.matched_by),
                "query_groups": item.query_groups.iter().map(|group| serde_json::json!({
                    "id": group.id,
                    "rank": group.rank,
                    "matched_by": format!("{:?}", group.matched_by),
                })).collect::<Vec<_>>(),
                "trace": item.trace,
                "content_bytes": item.content.len(),
            })
        }));
    }

    fn routed(
        &mut self,
        mode: Option<ContextRouteMode>,
        query: &str,
        limit: usize,
    ) -> Result<Vec<SearchUnit>, String> {
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
        let route = mode.map_or("hybrid", |route| match route {
            ContextRouteMode::Fts => "fts",
            ContextRouteMode::Vector => "vector",
        });
        self.record_hits(route, query, &result);
        Ok(Self::units(&result, self.parent_reference_scope))
    }
}

impl EvidenceSource for Source {
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

    fn hybrid(&mut self, question: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        self.calls += 1;
        self.routed(None, question, limit)
    }

    fn fts(&mut self, query: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        self.calls += 1;
        self.routed(Some(ContextRouteMode::Fts), query, limit)
    }

    fn symbol(&mut self, name: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
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
        self.hit_trace
            .extend(result.items.iter().enumerate().map(|(rank, item)| {
                serde_json::json!({
                    "route": "symbol",
                    "query": name,
                    "rank": rank,
                    "path": item.relative_path.to_string_lossy().replace('\\', "/"),
                    "entity_id": item.entity_id,
                    "entity_range": [item.range.start_line(), item.range.last_line()],
                    "content_range": [item.range.start_line(), item.range.last_line()],
                    "score": null,
                    "matched_by": "ExactSymbol",
                    "content_bytes": 0,
                })
            }));
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

    fn read(&mut self, path: &str) -> Result<Option<String>, String> {
        Ok(fs::read_to_string(self.root.join(path)).ok())
    }

    fn context_range(&self, unit: &SearchUnit) -> Option<SearchUnit> {
        if !self.parent_reference_scope {
            return None;
        }
        self.entity_ranges
            .get(&unit.path)?
            .iter()
            .filter(|range| range.start_line <= unit.start_line && unit.end_line <= range.end_line)
            .min_by_key(|range| range.end_line.saturating_sub(range.start_line))
            .cloned()
    }

    fn is_cancelled(&self) -> bool {
        self.cancel_after.is_some_and(|limit| self.calls >= limit)
    }
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
        0.0
    } else {
        values.iter().sum::<f64>() / values.len() as f64
    }
}

fn main() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let output_dir = std::env::var_os("R4B_OUTPUT_DIR")
        .map(PathBuf::from)
        .expect("R4B_OUTPUT_DIR must point outside the repository to preserve frozen receipts");
    let run_id =
        std::env::var("R4B_RUN_ID").expect("R4B_RUN_ID must identify this preregistered run");
    assert!(
        !run_id.is_empty()
            && run_id
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || ch == '-'),
        "R4B_RUN_ID may contain only ASCII letters, digits, and hyphens"
    );
    assert!(
        !output_dir.starts_with(&repo),
        "R4B_OUTPUT_DIR must be outside the repository"
    );
    fs::create_dir_all(&output_dir).expect("create external output directory");
    let root = std::env::args().nth(1).map_or_else(
        || PathBuf::from("/home/masis/.cache/zvec-grep/r4-discovery-20261115/corpus"),
        PathBuf::from,
    );
    let dev: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/cases/dev.json")).expect("dev"),
    )
    .expect("valid dev");

    let only = std::env::var("R4B_CASES").ok();
    let mut gold = Vec::new();
    let mut questions = Vec::new();
    for case in dev["cases"].as_array().expect("cases") {
        let id = case["id"].as_str().unwrap_or_default().to_owned();
        if !CASES.contains(&id.as_str()) {
            continue;
        }
        if let Some(only) = &only {
            if !only.split(',').any(|wanted| wanted == id) {
                continue;
            }
        }
        questions.push((
            id.clone(),
            case["question"].as_str().unwrap_or_default().to_owned(),
        ));
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
    }

    // Full REPORT-CONTRACT budget: <=3 rounds, <=6 tool calls, <=32 files,
    // pack <=24 KiB, cumulative delivered tool content <=64 KiB.
    let budget = WorkflowBudget {
        max_rounds: 3,
        max_tool_calls: 6,
        max_files: 32,
        max_source_reads: 32,
        hybrid_quota: 4,
        fts_quota: 2,
        ..WorkflowBudget::default()
    };
    // P1 alone: the hybrid candidate set, assembled by the new selector.
    // P1 arm: hybrid candidates only by default. With R4B_NO_HYBRID it instead
    // reproduces the R4 candidate pool, isolating the selection change.
    let legacy_pool = std::env::var("R4B_NO_HYBRID").is_ok();
    let p1_only = if legacy_pool {
        WorkflowBudget {
            use_hybrid: false,
            ..budget.clone()
        }
    } else {
        WorkflowBudget {
            use_fts: false,
            use_lexical: false,
            use_facts: false,
            ..budget.clone()
        }
    };

    let mut traced: Vec<serde_json::Value> = Vec::new();
    let mut rows = Vec::new();
    let mut hybrid_recalls = Vec::new();
    let mut bounded_recalls = Vec::new();
    let mut p1_recalls = Vec::new();
    let mut r4b_recalls = Vec::new();
    let mut pool_coverage = 0;
    let mut plan_recalls = Vec::new();
    let mut plan_pool = 0;
    let mut plan_selected = 0;
    let mut selected_coverage = 0;
    let mut verified_total = 0;
    let mut selected_total = 0;

    for (id, question) in &questions {
        let case_gold = gold
            .iter()
            .filter(|span| &span.case == id)
            .cloned()
            .collect::<Vec<_>>();
        let total = case_gold.len();

        // 1) fixed hybrid question baseline
        let mut source = Source::new(&root).expect("source");
        let started = Instant::now();
        let units = source.hybrid(question, 10).unwrap_or_default();
        let baseline_ms = started.elapsed().as_secs_f64() * 1000.0;
        let flat = units
            .iter()
            .map(|unit| (unit.path.clone(), unit.start_line, unit.end_line))
            .collect::<Vec<_>>();
        let baseline_covered = covered(&case_gold, &flat);
        // Fair baseline: the same hybrid list under the same 24 KiB evidence
        // budget the verified pack must respect, sized in SNIPPET units (the
        // deployable route sends matched snippets, not entity ranges). The
        // entity-range sizing reported 0.0000 because single spans are 30-56 KB
        // and never fit; that arm was invalid (audit A3).
        let mut bounded: Vec<(String, usize, usize)> = Vec::new();
        let mut bounded_bytes = 0usize;
        let options = ContextOptions {
            root: Some(root.clone()),
            query: Some(question.clone()),
            limit: Some(10),
            auto_update: false,
            refresh: Some(RefreshPolicy::Off),
            ..ContextOptions::default()
        };
        let result = source
            .runtime
            .block_on(source.engine.context(options))
            .expect("hybrid context");
        for item in &result.items {
            let size = item.content.len();
            if bounded_bytes + size > budget.max_bytes {
                continue;
            }
            bounded_bytes += size;
            if let (Some(start), Some(end)) = (
                item.content_range.start_line(),
                item.content_range.last_line(),
            ) {
                bounded.push((
                    item.relative_path.to_string_lossy().replace('\\', "/"),
                    start,
                    end,
                ));
            }
        }
        let baseline_bounded_covered = covered(&case_gold, &bounded);
        bounded_recalls.push(if total == 0 {
            0.0
        } else {
            baseline_bounded_covered as f64 / total as f64
        });

        // 2) hybrid + P1 selection
        let mut source = Source::new(&root).expect("source");
        let started = Instant::now();
        let p1 = run(&mut source, &root, question, p1_only.clone()).expect("p1 run");
        let p1_ms = started.elapsed().as_secs_f64() * 1000.0;
        let p1_flat = p1
            .selected
            .iter()
            .map(|span| (span.path.clone(), span.start_line, span.end_line))
            .collect::<Vec<_>>();
        let p1_covered = covered(&case_gold, &p1_flat);

        // 3) R4b (P1 + P2 + typed expansion)
        let mut source = Source::new(&root).expect("source");
        let started = Instant::now();
        let r4b = run(&mut source, &root, question, budget.clone()).expect("r4b run");
        let r4b_ms = started.elapsed().as_secs_f64() * 1000.0;
        let r4b_flat = r4b
            .selected
            .iter()
            .map(|span| (span.path.clone(), span.start_line, span.end_line))
            .collect::<Vec<_>>();
        let r4b_covered = covered(&case_gold, &r4b_flat);
        let r4b_verified = r4b.selected.iter().filter(|span| span.verified).count();
        verified_total += r4b_verified;
        selected_total += r4b.selected.len();
        pool_coverage += covered(
            &case_gold,
            &r4b.candidates
                .iter()
                .map(|unit| (unit.path.clone(), unit.start_line, unit.end_line))
                .collect::<Vec<_>>(),
        );
        selected_coverage += r4b_covered;
        // 4) R4b + blind external-agent plan (frozen file, sha in receipt)
        let plan: AgentPlan = {
            let raw: serde_json::Value = serde_json::from_str(
                &fs::read_to_string(
                    repo.join("benchmarks/rag-impact/results/r4b-blind-plan-20261117.json"),
                )
                .expect("blind plan"),
            )
            .expect("valid plan");
            let entry = &raw["plans"][id];
            AgentPlan {
                terms: Vec::new(),
                queries: entry["queries"]
                    .as_array()
                    .map(|values| {
                        values
                            .iter()
                            .filter_map(|v| v.as_str().map(ToString::to_string))
                            .collect()
                    })
                    .unwrap_or_default(),
                symbols: entry["symbols"]
                    .as_array()
                    .map(|values| {
                        values
                            .iter()
                            .filter_map(|v| v.as_str().map(ToString::to_string))
                            .collect()
                    })
                    .unwrap_or_default(),
                scopes: entry["scopes"]
                    .as_array()
                    .map(|values| {
                        values
                            .iter()
                            .filter_map(|v| v.as_str().map(ToString::to_string))
                            .collect()
                    })
                    .unwrap_or_default(),
            }
        };
        let mut source = Source::new(&root).expect("source");
        let started = Instant::now();
        // Direction-2 experiment budget: drop the FTS seed so the expand stage
        // keeps one typed symbol call inside the 6-call contract; the FTS gold
        // (D11) also carries a lexical rank-0 candidate.
        let plan_budget = WorkflowBudget {
            use_fts: false,
            ..budget.clone()
        };
        let with_plan =
            run_with_plan(&mut source, &root, question, plan_budget, &plan).expect("plan run");
        let plan_ms = started.elapsed().as_secs_f64() * 1000.0;
        let plan_flat = with_plan
            .selected
            .iter()
            .map(|span| (span.path.clone(), span.start_line, span.end_line))
            .collect::<Vec<_>>();
        let plan_covered = covered(&case_gold, &plan_flat);
        let plan_pool_covered = covered(
            &case_gold,
            &with_plan
                .candidates
                .iter()
                .map(|unit| (unit.path.clone(), unit.start_line, unit.end_line))
                .collect::<Vec<_>>(),
        );
        plan_recalls.push(if total == 0 {
            0.0
        } else {
            plan_covered as f64 / total as f64
        });
        plan_pool += plan_pool_covered;
        plan_selected += plan_covered;
        eprintln!(
            "{id} gold={total} r4b={r4b_covered} plan={plan_covered} plan_pool={plan_pool_covered}"
        );

        let routes = {
            let mut routes: Vec<String> = r4b
                .selected
                .iter()
                .flat_map(|span| span.sources.iter().map(|source| format!("{source:?}")))
                .collect();
            routes.sort_unstable();
            routes.dedup();
            routes
        };

        hybrid_recalls.push(if total == 0 {
            0.0
        } else {
            baseline_covered as f64 / total as f64
        });
        p1_recalls.push(if total == 0 {
            0.0
        } else {
            p1_covered as f64 / total as f64
        });
        r4b_recalls.push(if total == 0 {
            0.0
        } else {
            r4b_covered as f64 / total as f64
        });
        eprintln!(
            "{id} gold={total} r4b={r4b_covered} pool={}/{}",
            r4b.candidates.len(),
            r4b.selected.len()
        );

        if std::env::var("R4B_DECISIONS").is_ok() {
            traced.push(serde_json::json!({
                "case": id,
                "projection": if source.parent_reference_scope { "content_range_with_parent_reference_scope" } else { "entity_range" },
                "plan_ms": plan_ms,
                "plan_hits": source.hit_trace,
                "plan_pool": with_plan.candidates.iter().map(|unit| serde_json::json!({
                    "path": unit.path, "start_line": unit.start_line, "end_line": unit.end_line,
                })).collect::<Vec<_>>(),
                "plan_stages": with_plan.stages,
                "plan_stats": with_plan.stats,
                "plan_gaps": with_plan.gaps,
                "r4b_gaps": r4b.gaps,
                "plan_traced": with_plan.traced.iter().map(|entry| serde_json::json!({
                    "path": entry.unit.path, "start_line": entry.unit.start_line, "end_line": entry.unit.end_line,
                    "context_range": source.context_range(&entry.unit).map(|range| serde_json::json!({
                        "path": range.path, "start_line": range.start_line, "end_line": range.end_line,
                    })),
                    "sources": entry.sources, "route_rank": entry.route_rank,
                    "decision": entry.decision, "selected": entry.selected,
                })).collect::<Vec<_>>(),
                "plan_selected": with_plan.selected.iter().map(|span| serde_json::json!({
                    "path": span.path, "start_line": span.start_line, "end_line": span.end_line,
                    "source_sha256": span.source_sha256, "verified": span.verified,
                    "sources": span.sources, "content_bytes": span.content_bytes,
                })).collect::<Vec<_>>(),
                "gold": case_gold,
                "traced": r4b.traced,
                "selected": r4b.selected.iter().map(|span| serde_json::json!({
                    "path": span.path, "start_line": span.start_line, "end_line": span.end_line,
                    "sources": span.sources, "role": span.role, "content_bytes": span.content_bytes,
                })).collect::<Vec<_>>(),
            }));
        }
        rows.push(CaseRow {
            id: id.clone(),
            gold: total,
            hybrid_baseline: Arm {
                tool_content_bytes: 0,
                covered: baseline_covered,
                total,
                recall: hybrid_recalls.last().copied().unwrap_or(0.0),
                items: units.len(),
                verified: 0,
                calls: 1,
                source_reads: 0,
                source_bytes_read: 0,
                ranked_content_bytes: 0,
                ms: baseline_ms,
                abstain: None,
                routes: vec!["Hybrid".to_owned()],
            },
            hybrid_byte_bounded: Arm {
                tool_content_bytes: bounded_bytes,
                covered: baseline_bounded_covered,
                total,
                recall: bounded_recalls.last().copied().unwrap_or(0.0),
                items: bounded.len(),
                verified: 0,
                calls: 1,
                source_reads: 0,
                source_bytes_read: 0,
                ranked_content_bytes: 0,
                ms: baseline_ms,
                abstain: None,
                routes: vec!["Hybrid".to_owned()],
            },
            hybrid_p1: Arm {
                tool_content_bytes: p1.stats.tool_content_bytes,
                covered: p1_covered,
                total,
                recall: p1_recalls.last().copied().unwrap_or(0.0),
                items: p1.selected.len(),
                verified: p1.selected.iter().filter(|span| span.verified).count(),
                calls: p1.stats.tool_calls,
                source_reads: p1.stats.source_reads,
                source_bytes_read: p1.stats.bytes_read,
                ranked_content_bytes: p1.stats.ranked_content_bytes,
                ms: p1_ms,
                abstain: p1.abstain.clone(),
                routes: {
                    let mut routes: Vec<String> = p1
                        .selected
                        .iter()
                        .flat_map(|span| span.sources.iter().map(|source| format!("{source:?}")))
                        .collect();
                    routes.sort_unstable();
                    routes.dedup();
                    routes
                },
            },
            r4b: Arm {
                covered: r4b_covered,
                total,
                recall: r4b_recalls.last().copied().unwrap_or(0.0),
                items: r4b.selected.len(),
                verified: r4b_verified,
                calls: r4b.stats.tool_calls,
                source_reads: r4b.stats.source_reads,
                source_bytes_read: r4b.stats.bytes_read,
                ranked_content_bytes: r4b.stats.ranked_content_bytes,
                tool_content_bytes: r4b.stats.tool_content_bytes,
                ms: r4b_ms,
                abstain: r4b.abstain,
                routes,
            },
        });
    }

    let receipt = Receipt {
        kind: "r4b_paired",
        head: std::process::Command::new("git")
            .args(["rev-parse", "HEAD"])
            .current_dir(&repo)
            .output()
            .ok()
            .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
            .unwrap_or_default(),
        corpus_root: root.display().to_string(),
        gold_spans: gold.len(),
        budget,
        p3_status: "NOT_MEASURED",
        p3_reason: "no blind plan exists for D01-D12; a plan written after seeing gold is contaminated, and E1-E5 must not be extrapolated",
        macro_hybrid_baseline: mean(&hybrid_recalls),
        macro_hybrid_byte_bounded: mean(&bounded_recalls),
        tool_content_max: rows
            .iter()
            .map(|row| row.r4b.tool_content_bytes)
            .max()
            .unwrap_or(0),
        tool_content_total: rows.iter().map(|row| row.r4b.tool_content_bytes).sum(),
        tool_content_note: "workflow source-content bytes plus gap text; excludes serialized JSON/tool envelope and any external-agent delivery; internal ranking reads are separate",
        macro_hybrid_p1: mean(&p1_recalls),
        macro_r4b: mean(&r4b_recalls),
        macro_plan: mean(&plan_recalls),
        plan_pool,
        plan_selected,
        plan_sha256: {
            use sha2::{Digest, Sha256};
            hex::encode(Sha256::digest(
                fs::read(repo.join("benchmarks/rag-impact/results/r4b-blind-plan-20261117.json"))
                    .expect("plan bytes"),
            ))
        },
        pool_coverage,
        pool_coverage_note: "coverage of workflow candidate ranges under the adapter projection recorded per trace; not raw retrieval reachability",
        selected_coverage,
        citation_accuracy: if selected_total == 0 {
            0.0
        } else {
            verified_total as f64 / selected_total as f64
        },
        rows,
        host: HostState {
            loadavg: fs::read_to_string("/proc/loadavg")
                .unwrap_or_default()
                .trim()
                .to_owned(),
            mem_available_kib: mem_available_kib(),
            pss_kib: pss_kib(),
        },
    };
    if !traced.is_empty() {
        let trace_out = output_dir.join(format!("r4-evidence-trace-{run_id}.json"));
        assert!(
            !trace_out.exists(),
            "refusing to overwrite existing trace: {}",
            trace_out.display()
        );
        fs::write(
            &trace_out,
            serde_json::to_string_pretty(&serde_json::json!({"cases": traced})).expect("json"),
        )
        .expect("write trace");
    }
    let text = serde_json::to_string_pretty(&receipt).expect("json");
    let name = if legacy_pool {
        "r4b-p1-legacy-pool"
    } else {
        "r4b-paired"
    };
    let out = output_dir.join(format!("{name}-{run_id}.json"));
    assert!(
        !out.exists(),
        "refusing to overwrite existing receipt: {}",
        out.display()
    );
    fs::write(&out, &text).expect("write");
    println!(
        "hybrid={:.4} hybrid_bounded={:.4} hybrid_p1={:.4} r4b={:.4} plan={:.4} plan_pool_overlap={}/{} plan_sel={} pool={}/{} selected={}/{} citation={:.3}",
        receipt.macro_hybrid_baseline,
        receipt.macro_hybrid_byte_bounded,
        receipt.macro_hybrid_p1,
        receipt.macro_r4b,
        receipt.macro_plan,
        receipt.plan_pool,
        receipt.gold_spans,
        receipt.plan_selected,
        receipt.pool_coverage,
        receipt.gold_spans,
        receipt.selected_coverage,
        receipt.gold_spans,
        receipt.citation_accuracy
    );
    eprintln!("receipt: {}", out.display());
}

#[allow(dead_code)]
fn unused(_: CandidateSource) {}
