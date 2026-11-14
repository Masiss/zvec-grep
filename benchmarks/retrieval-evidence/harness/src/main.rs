//! Minimal independent harness for the E3-01 evidence dataset.
//!
//! It only calls the public `zg-engine` API.  Query design and gold evidence are
//! frozen independently in `dataset/queries.json`; this binary never reads them
//! during indexing and never edits source.

use std::{collections::BTreeMap, env, fs, path::PathBuf, process::ExitCode, time::Instant};

use anyhow::{anyhow, bail, Context, Result};
use serde::Serialize;
use zg_engine::{
    api::{
        context::{
            options::{ContextOptions, ContextRoute, ContextRouteMode, QueryFilter},
            result::{
                ContentRange, ContextItem, ContextQueryGroupRole, EntityMetadata, IndexDiagnostics,
                MatchedBy,
            },
        },
        index::{
            options::{Device, EmbeddingModelSpec, ScanRulesUpdate},
            IndexOptions,
        },
        info::InfoOptions,
    },
    ZvecGrep,
};

#[derive(Debug)]
struct Args {
    command: String,
    root: PathBuf,
    cache: Option<PathBuf>,
    model: String,
    rebuild: bool,
    dataset: Option<PathBuf>,
    globs: Vec<String>,
    route: String,
    limit: usize,
    output: Option<PathBuf>,
    /// Reproduce the E4 harness defect: a bare `query` adds a primary
    /// FTS+Vector group in addition to the explicit routes.
    legacy_primary: bool,
}

impl Args {
    fn parse() -> Result<Self> {
        let mut values = BTreeMap::new();
        let mut command = None;
        for argument in env::args().skip(1) {
            if !argument.starts_with("--") {
                if command.is_none() {
                    command = Some(argument);
                    continue;
                }
                bail!("unexpected argument {argument}");
            }
            let (key, value) = argument
                .split_once('=')
                .ok_or_else(|| anyhow!("expected --key=value, got {argument}"))?;
            values.insert(key.trim_start_matches("--").to_owned(), value.to_owned());
        }
        let command = command.ok_or_else(|| anyhow!("usage: index|query --root=... [options]"))?;
        let root = values
            .remove("root")
            .map(PathBuf::from)
            .ok_or_else(|| anyhow!("--root is required"))?;
        let cache = values.remove("cache").map(PathBuf::from);
        let model = values
            .remove("model")
            .unwrap_or_else(|| "local/potion-code-16m-v2".to_owned());
        let rebuild = values
            .remove("rebuild")
            .map(|value| value == "true" || value == "1")
            .unwrap_or(false);
        let dataset = values.remove("dataset").map(PathBuf::from);
        let globs = values
            .remove("globs")
            .map(|value| {
                value
                    .split(',')
                    .map(str::trim)
                    .filter(|glob| !glob.is_empty())
                    .map(str::to_owned)
                    .collect::<Vec<_>>()
            })
            .filter(|globs| !globs.is_empty())
            .unwrap_or_else(|| vec!["*.cs".to_owned()]);
        let route = values
            .remove("route")
            .unwrap_or_else(|| "hybrid".to_owned());
        let limit = values
            .remove("limit")
            .map(|value| value.parse::<usize>())
            .transpose()?
            .unwrap_or(50);
        let output = values.remove("output").map(PathBuf::from);
        let legacy_primary = values
            .remove("legacy-primary")
            .map(|value| value == "true" || value == "1")
            .unwrap_or(false);
        if let Some((key, _)) = values.iter().next() {
            bail!("unknown option --{key}");
        }
        Ok(Self {
            command,
            root,
            cache,
            model,
            rebuild,
            dataset,
            globs,
            route,
            limit,
            output,
            legacy_primary,
        })
    }
}

fn model_spec(args: &Args) -> EmbeddingModelSpec {
    EmbeddingModelSpec {
        reference: args.model.clone(),
        revision: None,
        cache_dir: args.cache.clone(),
        endpoint: None,
        device: match std::env::var("ZVEC_GREP_E8_DEVICE").as_deref() {
            Ok("vulkan") => Device::Vulkan,
            Ok("auto") => Device::Auto,
            Ok("cuda") => Device::Cuda,
            _ => Device::Cpu,
        },
    }
}

fn routes(route: &str) -> Result<(Vec<ContextRoute>, bool, bool)> {
    let query = String::new();
    let (routes, fuse, prefer_symbol) = match route {
        "fts" => (
            vec![ContextRoute {
                mode: ContextRouteMode::Fts,
                query,
            }],
            false,
            false,
        ),
        "vector" => (
            vec![ContextRoute {
                mode: ContextRouteMode::Vector,
                query,
            }],
            false,
            false,
        ),
        "hybrid" => (
            vec![
                ContextRoute {
                    mode: ContextRouteMode::Fts,
                    query: query.clone(),
                },
                ContextRoute {
                    mode: ContextRouteMode::Vector,
                    query,
                },
            ],
            true,
            false,
        ),
        "hybrid-symbol" => (
            vec![
                ContextRoute {
                    mode: ContextRouteMode::Fts,
                    query: query.clone(),
                },
                ContextRoute {
                    mode: ContextRouteMode::Vector,
                    query,
                },
            ],
            true,
            true,
        ),
        other => bail!("unknown route {other}"),
    };
    Ok((routes, fuse, prefer_symbol))
}

#[derive(Clone, Debug, Serialize)]
struct ContractFailure {
    query_id: String,
    check: String,
    detail: String,
}

#[derive(Clone, Debug, Serialize)]
struct ExpectedPlan {
    route_modes: Vec<String>,
    group_count: usize,
    primary_groups: usize,
    duplicate_routes: usize,
    fuse: bool,
    prefer_symbol: bool,
}

#[derive(Clone, Debug, Serialize)]
struct ActualPlan {
    route_modes: Vec<String>,
    group_count: usize,
    primary_groups: usize,
    duplicate_routes: usize,
}

#[derive(Clone, Debug, Serialize)]
struct ContractCheck {
    name: String,
    status: String,
    detail: String,
}

#[derive(Clone, Debug, Serialize)]
struct MeasurementContract {
    version: u32,
    intended_route: String,
    expected: ExpectedPlan,
    actual: ActualPlan,
    dataset: serde_json::Value,
    model: serde_json::Value,
    limit: usize,
    filters: Vec<String>,
    queries_checked: usize,
    checks: Vec<ContractCheck>,
    failures: Vec<ContractFailure>,
    status: String,
}

fn mode_name(mode: ContextRouteMode) -> String {
    match mode {
        ContextRouteMode::Fts => "fts".to_owned(),
        ContextRouteMode::Vector => "vector".to_owned(),
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    hex::encode(Sha256::digest(bytes))
}

fn read_json(path: &std::path::Path) -> Result<serde_json::Value> {
    serde_json::from_slice(&fs::read(path).with_context(|| format!("read {}", path.display()))?)
        .with_context(|| format!("parse {}", path.display()))
}

fn indexed_model(root: &std::path::Path) -> Result<String> {
    let manifest = read_json(&root.join(".zvec-grep").join("manifest.json"))?;
    let model = &manifest["index"]["descriptor"]["defaultEmbeddingModel"]["model"];
    let provider = model["provider"]
        .as_str()
        .ok_or_else(|| anyhow!("index manifest has no provider"))?;
    let name = model["name"]
        .as_str()
        .ok_or_else(|| anyhow!("index manifest has no model name"))?;
    Ok(format!("{provider}/{name}"))
}

fn dataset_identity(
    dataset_path: &std::path::Path,
    dataset: &serde_json::Value,
) -> Result<serde_json::Value> {
    let bytes = fs::read(dataset_path)
        .with_context(|| format!("read dataset {}", dataset_path.display()))?;
    let sha256 = sha256_hex(&bytes);
    let manifest_path = dataset_path
        .parent()
        .ok_or_else(|| anyhow!("dataset has no parent directory"))?
        .join("manifest.json");
    let manifest = read_json(&manifest_path)?;
    let pinned = manifest["queries_sha256"]
        .as_str()
        .ok_or_else(|| anyhow!("dataset manifest has no queries_sha256"))?;
    Ok(serde_json::json!({
        "version": dataset["dataset"].as_str().unwrap_or("unknown"),
        "sha256": sha256,
        "pinned_sha256": pinned,
        "manifest": manifest_path,
        "hash_status": if sha256 == pinned { "PASS" } else { "FAIL" },
    }))
}

fn check_plan(
    query_id: &str,
    text: &str,
    corpus: &str,
    limit: usize,
    expected_modes: &[ContextRouteMode],
    fuse: bool,
    diagnostics: Option<&IndexDiagnostics>,
    items: &[ContextItem],
    failures: &mut Vec<ContractFailure>,
) -> Result<Option<ActualPlan>> {
    let mut fail = |check: &str, detail: String| {
        failures.push(ContractFailure {
            query_id: query_id.to_owned(),
            check: check.to_owned(),
            detail,
        });
    };
    let Some(index) = diagnostics else {
        fail(
            "diagnostics_present",
            "index diagnostics missing".to_owned(),
        );
        return Ok(None);
    };
    let mut actual_modes = index
        .routes
        .iter()
        .map(|route| mode_name(route.mode))
        .collect::<Vec<_>>();
    actual_modes.sort();
    let mut expected_names = expected_modes
        .iter()
        .copied()
        .map(mode_name)
        .collect::<Vec<_>>();
    expected_names.sort();

    let primary_groups = index
        .query_groups
        .iter()
        .filter(|group| group.role == ContextQueryGroupRole::Primary)
        .count();
    let mut seen = std::collections::BTreeSet::new();
    let duplicate_routes = index
        .routes
        .iter()
        .filter(|route| !seen.insert((mode_name(route.mode), route.query.clone())))
        .count();

    if index.query_groups.len() != 1 {
        fail(
            "group_count",
            format!("expected 1 group, got {}", index.query_groups.len()),
        );
    }
    if primary_groups != 0 {
        fail(
            "primary_groups",
            format!("expected 0 primary groups, got {primary_groups}"),
        );
    }
    if actual_modes != expected_names {
        fail(
            "route_modes",
            format!("expected {expected_names:?}, got {actual_modes:?}"),
        );
    }
    if duplicate_routes != 0 {
        fail(
            "duplicate_routes",
            format!("expected 0 duplicate routes, got {duplicate_routes}"),
        );
    }
    for route in &index.routes {
        if route.query != text {
            fail(
                "route_query",
                format!(
                    "route {} query {:?} != {:?}",
                    mode_name(route.mode),
                    route.query,
                    text
                ),
            );
        }
    }
    for group in &index.query_groups {
        let joined = format!("{text} | {text}");
        if group.query != text && group.query != joined {
            fail(
                "group_query",
                format!(
                    "group {} query {:?} is not the requested query",
                    group.id, group.query
                ),
            );
        }
        if group.role == ContextQueryGroupRole::Primary {
            fail("group_role", format!("group {} is primary", group.id));
        }
    }
    if items.len() > limit {
        fail(
            "limit",
            format!("{} items returned for limit {limit}", items.len()),
        );
    }
    let prefix = format!("{corpus}/");
    for item in items {
        let path = item.relative_path.to_string_lossy().replace('\\', "/");
        if !path.starts_with(&prefix) {
            fail(
                "filter",
                format!("item path {path:?} escapes corpus filter {prefix:?}"),
            );
            break;
        }
        if expected_modes == [ContextRouteMode::Fts]
            && matches!(item.matched_by, MatchedBy::Vector | MatchedBy::FtsAndVector)
        {
            fail(
                "hidden_vector",
                format!("pure FTS item matched_by={:?}", item.matched_by),
            );
            break;
        }
        if expected_modes == [ContextRouteMode::Vector]
            && matches!(item.matched_by, MatchedBy::Fts | MatchedBy::FtsAndVector)
        {
            fail(
                "hidden_fts",
                format!("pure vector item matched_by={:?}", item.matched_by),
            );
            break;
        }
    }
    let _ = fuse;
    Ok(Some(ActualPlan {
        route_modes: actual_modes,
        group_count: index.query_groups.len(),
        primary_groups,
        duplicate_routes,
    }))
}

#[derive(Serialize)]
struct QueryDoc {
    query_id: String,
    latency_ms: f64,
    items: Vec<Candidate>,
}

#[derive(Serialize)]
struct Candidate {
    rank: usize,
    path: String,
    start_line: Option<usize>,
    end_line: Option<usize>,
    score: Option<f64>,
    kind: String,
    matched_by: String,
    query_group_ids: Vec<String>,
    symbol_type: Option<String>,
    symbol_name: Option<String>,
    signature: Option<String>,
    container_symbol_type: Option<String>,
    container_symbol_name: Option<String>,
    excerpt: String,
}

fn code_fields(
    metadata: Option<&EntityMetadata>,
) -> (Option<String>, Option<String>, Option<String>) {
    let Some(EntityMetadata::Code(code)) = metadata else {
        return (None, None, None);
    };
    (
        code.symbol_type
            .map(|kind| format!("{kind:?}").to_ascii_lowercase()),
        code.symbol_name.clone(),
        code.signature.clone(),
    )
}

fn candidate(item: ContextItem) -> Result<Candidate> {
    let (start_line, end_line) = match item.content_range {
        ContentRange::Text {
            start_line,
            end_line,
            ..
        } => (Some(start_line), Some(end_line)),
        ContentRange::Byte { .. } | ContentRange::File => (None, None),
    };
    let path = item
        .relative_path
        .to_str()
        .ok_or_else(|| anyhow!("non-UTF-8 relative path"))?
        .replace('\\', "/");
    let (symbol_type, symbol_name, signature) = code_fields(item.metadata.as_ref());
    let (container_symbol_type, container_symbol_name) = item
        .container
        .as_ref()
        .map(|container| code_fields(container.metadata.as_ref()))
        .map(|(kind, name, _)| (kind, name))
        .unwrap_or((None, None));
    Ok(Candidate {
        rank: item.rank,
        path,
        start_line,
        end_line,
        score: item.score,
        kind: format!("{:?}", item.kind).to_ascii_lowercase(),
        matched_by: serde_json::to_value(item.matched_by)
            .ok()
            .and_then(|value| value.as_str().map(str::to_owned))
            .unwrap_or_else(|| "unknown".to_owned()),
        query_group_ids: item
            .query_groups
            .iter()
            .map(|group| group.id.clone())
            .collect(),
        symbol_type,
        symbol_name,
        signature,
        container_symbol_type,
        container_symbol_name,
        excerpt: item.content.chars().take(240).collect(),
    })
}

async fn run() -> Result<()> {
    let args = Args::parse()?;
    let engine = ZvecGrep::new();
    match args.command.as_str() {
        "index" => {
            let result = engine
                .index(IndexOptions {
                    root: Some(args.root.clone()),
                    rebuild: args.rebuild,
                    embedding: Some(model_spec(&args)),
                    model_cache: args.cache.clone(),
                    scan: ScanRulesUpdate {
                        globs: Some(
                            args.globs
                                .iter()
                                .map(|glob| {
                                    zg_engine::api::index::options::GlobRule::from(glob.as_str())
                                })
                                .collect(),
                        ),
                        ..ScanRulesUpdate::default()
                    },
                    ..IndexOptions::default()
                })
                .await?;
            println!(
                "indexed files={} added={} modified={} deleted={} failed={} entities={}",
                result.files_scanned,
                result.files_added,
                result.files_modified,
                result.files_deleted,
                result.files_failed,
                result.entities_created
            );
            if let Some(path) = args.output.as_ref() {
                let payload = serde_json::json!({
                    "duration_micros": result.duration_micros,
                    "timings": result.timings,
                    "files_scanned": result.files_scanned,
                    "files_added": result.files_added,
                    "files_modified": result.files_modified,
                    "files_deleted": result.files_deleted,
                    "files_failed": result.files_failed,
                    "entities_created": result.entities_created,
                });
                std::fs::write(path, serde_json::to_vec_pretty(&payload)?)?;
            }
        }
        "query" => {
            let dataset_path = args
                .dataset
                .as_ref()
                .ok_or_else(|| anyhow!("--dataset is required for query"))?;
            let dataset: serde_json::Value =
                serde_json::from_slice(&fs::read(dataset_path)?).context("read dataset")?;
            let queries = dataset["queries"]
                .as_array()
                .ok_or_else(|| anyhow!("dataset queries must be an array"))?;
            let (route_templates, fuse, prefer_symbol) = routes(&args.route)?;
            let info = engine
                .info(InfoOptions {
                    root: Some(args.root.clone()),
                    include_status: true,
                })
                .await?;
            let index_status = info.status;
            let expected_modes = route_templates
                .iter()
                .map(|route| route.mode)
                .collect::<Vec<_>>();
            let identity = dataset_identity(dataset_path, &dataset)?;
            let indexed_model = indexed_model(&args.root)?;
            let mut failures = Vec::<ContractFailure>::new();
            if identity["hash_status"] != "PASS" {
                failures.push(ContractFailure {
                    query_id: "*".to_owned(),
                    check: "dataset_hash".to_owned(),
                    detail: format!(
                        "dataset sha256 {} != pinned {}",
                        identity["sha256"], identity["pinned_sha256"]
                    ),
                });
            }
            if indexed_model != args.model {
                failures.push(ContractFailure {
                    query_id: "*".to_owned(),
                    check: "model_identity".to_owned(),
                    detail: format!(
                        "index model {indexed_model:?} != requested {:?}",
                        args.model
                    ),
                });
            }
            if args.legacy_primary {
                failures.push(ContractFailure {
                    query_id: "*".to_owned(),
                    check: "legacy_primary".to_owned(),
                    detail: "a primary query was added on top of explicit routes".to_owned(),
                });
            }
            let mut actual_plans = Vec::<ActualPlan>::new();
            let mut filters = std::collections::BTreeSet::new();
            let mut results = Vec::with_capacity(queries.len());
            for query in queries {
                let query_id = query["id"]
                    .as_str()
                    .ok_or_else(|| anyhow!("query id missing"))?;
                let text = query["question"]
                    .as_str()
                    .ok_or_else(|| anyhow!("query question missing"))?;
                let corpus = query["corpus"]
                    .as_str()
                    .ok_or_else(|| anyhow!("query corpus missing"))?;
                let routes = route_templates
                    .iter()
                    .map(|template| ContextRoute {
                        mode: template.mode,
                        query: text.to_owned(),
                    })
                    .collect();
                filters.insert(format!("{corpus}/**"));
                let started = Instant::now();
                let result = engine
                    .context(ContextOptions {
                        query: args.legacy_primary.then(|| text.to_owned()),
                        routes,
                        fuse,
                        root: Some(args.root.clone()),
                        limit: Some(args.limit),
                        auto_update: false,
                        prefer_symbol,
                        model_cache: args.cache.clone(),
                        filter: QueryFilter {
                            globs: vec![format!("{corpus}/**").into()],
                            ..QueryFilter::default()
                        },
                        ..ContextOptions::default()
                    })
                    .await?;
                let latency_ms = started.elapsed().as_secs_f64() * 1000.0;
                if let Some(plan) = check_plan(
                    query_id,
                    text,
                    corpus,
                    args.limit,
                    &expected_modes,
                    fuse,
                    result.diagnostics.index.as_ref(),
                    &result.items,
                    &mut failures,
                )? {
                    actual_plans.push(plan);
                }
                let items = result
                    .items
                    .into_iter()
                    .map(candidate)
                    .collect::<Result<Vec<_>>>()?;
                results.push(QueryDoc {
                    query_id: query_id.to_owned(),
                    latency_ms,
                    items,
                });
            }
            let mode_names = expected_modes
                .iter()
                .copied()
                .map(mode_name)
                .collect::<Vec<_>>();
            let distinct_plans = actual_plans
                .iter()
                .map(|plan| plan.route_modes.clone())
                .collect::<std::collections::BTreeSet<_>>();
            if distinct_plans.len() > 1 {
                failures.push(ContractFailure {
                    query_id: "*".to_owned(),
                    check: "route_stability".to_owned(),
                    detail: format!("queries produced different route sets: {distinct_plans:?}"),
                });
            }
            let actual = actual_plans.first().cloned().unwrap_or(ActualPlan {
                route_modes: Vec::new(),
                group_count: 0,
                primary_groups: 0,
                duplicate_routes: 0,
            });
            let dataset_ok = identity["hash_status"] == "PASS";
            let model_ok = indexed_model == args.model;
            let queries_ok = results.len() == queries.len();
            let legacy = args.legacy_primary;
            let status = if !legacy && failures.is_empty() && dataset_ok && model_ok && queries_ok {
                "PASS"
            } else {
                "FAIL"
            };
            let checks = vec![
                ContractCheck {
                    name: "dataset_hash".to_owned(),
                    status: if dataset_ok { "PASS" } else { "FAIL" }.to_owned(),
                    detail: identity["sha256"].as_str().unwrap_or("").to_owned(),
                },
                ContractCheck {
                    name: "model_identity".to_owned(),
                    status: if model_ok { "PASS" } else { "FAIL" }.to_owned(),
                    detail: format!("{indexed_model}"),
                },
                ContractCheck {
                    name: "query_count".to_owned(),
                    status: if queries_ok { "PASS" } else { "FAIL" }.to_owned(),
                    detail: format!("{} results for {} queries", results.len(), queries.len()),
                },
                ContractCheck {
                    name: "route_contract".to_owned(),
                    status: if failures.is_empty() { "PASS" } else { "FAIL" }.to_owned(),
                    detail: format!("{} failures", failures.len()),
                },
                ContractCheck {
                    name: "legacy_primary".to_owned(),
                    status: if legacy { "FAIL" } else { "PASS" }.to_owned(),
                    detail: if legacy {
                        "contaminated legacy request"
                    } else {
                        "absent"
                    }
                    .to_owned(),
                },
            ];
            let contract = MeasurementContract {
                version: 1,
                intended_route: args.route.clone(),
                expected: ExpectedPlan {
                    route_modes: mode_names,
                    group_count: 1,
                    primary_groups: 0,
                    duplicate_routes: 0,
                    fuse,
                    prefer_symbol,
                },
                actual,
                dataset: identity,
                model: serde_json::json!({
                    "requested": args.model,
                    "indexed": indexed_model,
                    "status": if model_ok { "PASS" } else { "FAIL" },
                }),
                limit: args.limit,
                filters: filters.into_iter().collect(),
                queries_checked: results.len(),
                checks,
                failures,
                status: status.to_owned(),
            };
            if status != "PASS" && !legacy {
                eprintln!(
                    "measurement contract failed: {}",
                    serde_json::to_string_pretty(&contract)?
                );
                bail!("refusing to emit metrics without a PASS measurement contract");
            }
            let run = serde_json::json!({
                "dataset": dataset["dataset"].as_str().unwrap_or("e3-01-v2"),
                "run": {
                    "route": args.route,
                    "limit": args.limit,
                    "model": args.model,
                    "root": args.root,
                    "globs": args.globs,
                    "measurement_contract": contract,
                    "index": index_status.as_ref().map(|status| serde_json::json!({
                        "files_scanned": status.files_scanned,
                        "files_indexed": status.files_indexed,
                        "files_pending": status.files_pending,
                        "files_failed": status.files_failed,
                    })),
                },
                "results": results,
            });
            let text = serde_json::to_vec_pretty(&run)?;
            match args.output {
                Some(path) => fs::write(path, text)?,
                None => println!("{}", String::from_utf8(text)?),
            }
        }
        other => bail!("unknown command {other}"),
    }
    Ok(())
}

fn main() -> ExitCode {
    let runtime = match tokio::runtime::Runtime::new() {
        Ok(runtime) => runtime,
        Err(error) => {
            eprintln!("error: {error:#}");
            return ExitCode::FAILURE;
        }
    };
    match runtime.block_on(run()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("error: {error:#}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod e6_contract_tests {
    use super::*;
    use zg_engine::api::context::result::{IndexQueryGroupDiagnostics, IndexRouteDiagnostics};

    fn diagnostics(routes: Vec<(ContextRouteMode, &str)>, primary: bool) -> IndexDiagnostics {
        IndexDiagnostics {
            hits_returned: 0,
            query_groups: vec![IndexQueryGroupDiagnostics {
                id: "Q1".to_owned(),
                query: if routes.len() > 1 {
                    "alpha | alpha".to_owned()
                } else {
                    "alpha".to_owned()
                },
                role: if primary {
                    ContextQueryGroupRole::Primary
                } else {
                    ContextQueryGroupRole::Supplemental
                },
            }],
            routes: routes
                .into_iter()
                .map(|(mode, query)| IndexRouteDiagnostics {
                    id: mode_name(mode),
                    mode,
                    query: query.to_owned(),
                })
                .collect(),
        }
    }

    fn check(actual: &IndexDiagnostics, expected: &[ContextRouteMode]) -> Vec<ContractFailure> {
        let mut failures = Vec::new();
        check_plan(
            "q",
            "alpha",
            "dapper",
            50,
            expected,
            false,
            Some(actual),
            &[],
            &mut failures,
        )
        .expect("plan check");
        failures
    }

    fn item(path: &str, matched: MatchedBy) -> ContextItem {
        use zg_engine::api::context::result::{
            ContextItemKind, ContextItemStatus, ContextSelectionReason,
        };
        ContextItem {
            kind: ContextItemKind::IndexedEntity,
            rank: 1,
            absolute_path: PathBuf::from("/workspace").join(path),
            relative_path: PathBuf::from(path),
            range: ContentRange::File,
            excerpt_range: None,
            content_range: ContentRange::File,
            match_ranges: Vec::new(),
            content: String::new(),
            outline: None,
            content_role: None,
            status: ContextItemStatus::Fresh,
            score: None,
            matched_by: matched,
            metadata: None,
            entity_id: None,
            container: None,
            trace: None,
            query_groups: Vec::new(),
            selection_reason: Some(ContextSelectionReason::GlobalFill),
            coverage_group: None,
        }
    }

    #[test]
    fn rejects_filter_escape_hidden_vector_and_limit() {
        let mut failures = Vec::new();
        check_plan(
            "q",
            "alpha",
            "dapper",
            50,
            &[ContextRouteMode::Fts],
            false,
            Some(&diagnostics(vec![(ContextRouteMode::Fts, "alpha")], false)),
            &[item("other/escape.cs", MatchedBy::Fts)],
            &mut failures,
        )
        .expect("plan");
        assert!(failures.iter().any(|failure| failure.check == "filter"));

        let mut failures = Vec::new();
        check_plan(
            "q",
            "alpha",
            "dapper",
            50,
            &[ContextRouteMode::Fts],
            false,
            Some(&diagnostics(vec![(ContextRouteMode::Fts, "alpha")], false)),
            &[item("dapper/a.cs", MatchedBy::Vector)],
            &mut failures,
        )
        .expect("plan");
        assert!(failures
            .iter()
            .any(|failure| failure.check == "hidden_vector"));

        let mut failures = Vec::new();
        check_plan(
            "q",
            "alpha",
            "dapper",
            1,
            &[ContextRouteMode::Fts],
            false,
            Some(&diagnostics(vec![(ContextRouteMode::Fts, "alpha")], false)),
            &[
                item("dapper/a.cs", MatchedBy::Fts),
                item("dapper/b.cs", MatchedBy::Fts),
            ],
            &mut failures,
        )
        .expect("plan");
        assert!(failures.iter().any(|failure| failure.check == "limit"));
    }

    #[test]
    fn rejects_multiple_groups() {
        let mut actual = diagnostics(vec![(ContextRouteMode::Fts, "alpha")], false);
        actual.query_groups.push(IndexQueryGroupDiagnostics {
            id: "Q2".to_owned(),
            query: "alpha".to_owned(),
            role: ContextQueryGroupRole::Supplemental,
        });
        let mut failures = Vec::new();
        check_plan(
            "q",
            "alpha",
            "dapper",
            50,
            &[ContextRouteMode::Fts],
            false,
            Some(&actual),
            &[],
            &mut failures,
        )
        .expect("plan");
        assert!(failures
            .iter()
            .any(|failure| failure.check == "group_count"));
    }

    #[test]
    fn accepts_pure_fts_and_hybrid() {
        assert!(check(
            &diagnostics(vec![(ContextRouteMode::Fts, "alpha")], false),
            &[ContextRouteMode::Fts]
        )
        .is_empty());
        assert!(check(
            &diagnostics(
                vec![
                    (ContextRouteMode::Fts, "alpha"),
                    (ContextRouteMode::Vector, "alpha"),
                ],
                false,
            ),
            &[ContextRouteMode::Fts, ContextRouteMode::Vector],
        )
        .is_empty());
    }

    #[test]
    fn rejects_hidden_primary_vector_and_duplicates() {
        let hidden = check(
            &diagnostics(
                vec![
                    (ContextRouteMode::Fts, "alpha"),
                    (ContextRouteMode::Vector, "alpha"),
                ],
                false,
            ),
            &[ContextRouteMode::Fts],
        );
        assert!(hidden.iter().any(|failure| failure.check == "route_modes"));

        let primary = check(
            &diagnostics(vec![(ContextRouteMode::Fts, "alpha")], true),
            &[ContextRouteMode::Fts],
        );
        assert!(primary
            .iter()
            .any(|failure| failure.check == "primary_groups"));

        let duplicate = check(
            &diagnostics(
                vec![
                    (ContextRouteMode::Fts, "alpha"),
                    (ContextRouteMode::Fts, "alpha"),
                ],
                false,
            ),
            &[ContextRouteMode::Fts],
        );
        assert!(duplicate
            .iter()
            .any(|failure| failure.check == "duplicate_routes"));
    }
}
