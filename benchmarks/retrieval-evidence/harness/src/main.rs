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
            result::{ContentRange, ContextItem, EntityMetadata},
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
        device: Device::Cpu,
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
                                .map(|glob| zg_engine::api::index::options::GlobRule::from(glob.as_str()))
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
            let run = serde_json::json!({
                "dataset": dataset["dataset"].as_str().unwrap_or("e3-01-v2"),
                "run": {
                    "route": args.route,
                    "limit": args.limit,
                    "model": args.model,
                    "root": args.root,
                    "globs": args.globs,
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
