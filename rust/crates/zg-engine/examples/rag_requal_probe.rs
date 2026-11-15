//! Read-only RAG requalification probe over an existing index.
//!
//! ```text
//! rag_requal_probe --root DIR --input queries.json --out results.json [--limit N]
//! ```
//!
//! The input must contain `{"queries":[{"id","call","mode","query"}]}` where
//! `mode` is `fts`, `vector`, or `hybrid`. Each query is executed with explicit
//! routes and `auto_update=false`, so the emitted `diagnostics.index.routes` is
//! the effective execution plan used by the contract check.

use std::{fs, path::PathBuf, time::Instant};

use serde::Deserialize;
use serde_json::json;
use zg_engine::{
    ZvecGrep,
    api::context::{
        ContextOptions,
        options::{ContextRoute, ContextRouteMode},
        result::EntityMetadata,
    },
    api::info::{InfoOptions, InfoResult},
};

#[derive(Deserialize)]
struct Input {
    queries: Vec<Query>,
}

#[derive(Deserialize)]
struct Query {
    id: String,
    call: String,
    mode: String,
    query: String,
    #[serde(default)]
    prefer_symbol: bool,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let mut root = None;
    let mut input = None;
    let mut out = None;
    let mut limit = 50usize;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--root" => root = args.next().map(PathBuf::from),
            "--input" => input = args.next().map(PathBuf::from),
            "--out" => out = args.next().map(PathBuf::from),
            "--limit" => limit = args.next().expect("limit").parse().expect("limit"),
            other => panic!("unknown argument {other}"),
        }
    }
    let root = root.expect("--root");
    let input = input.expect("--input");
    let out = out.expect("--out");
    let corpus: Input = serde_json::from_slice(&fs::read(input)?)?;

    let engine = ZvecGrep::new();
    let info: InfoResult = engine
        .info(InfoOptions {
            root: Some(root.clone()),
            include_status: true,
        })
        .await?;
    let mut rows = Vec::new();
    for case in corpus.queries {
        let fuse = case.mode == "hybrid";
        let routes = match case.mode.as_str() {
            "fts" => vec![ContextRoute {
                mode: ContextRouteMode::Fts,
                query: case.query.clone(),
            }],
            "vector" => vec![ContextRoute {
                mode: ContextRouteMode::Vector,
                query: case.query.clone(),
            }],
            "hybrid" => vec![
                ContextRoute {
                    mode: ContextRouteMode::Fts,
                    query: case.query.clone(),
                },
                ContextRoute {
                    mode: ContextRouteMode::Vector,
                    query: case.query.clone(),
                },
            ],
            other => panic!("unsupported mode {other}"),
        };
        let started = Instant::now();
        let result = engine
            .context(ContextOptions {
                root: Some(root.clone()),
                routes: routes.clone(),
                fuse,
                prefer_symbol: case.prefer_symbol,
                auto_update: false,
                limit: Some(limit),
                ..ContextOptions::default()
            })
            .await?;
        let wall_ms = started.elapsed().as_secs_f64() * 1000.0;
        let items = result
            .items
            .iter()
            .map(|item| {
                let metadata = item.metadata.as_ref().map(|metadata| match metadata {
                    EntityMetadata::Code(code) => json!({
                        "kind": "code",
                        "symbol_name": code.symbol_name,
                        "symbol_type": code.symbol_type,
                        "scope": code.scope,
                        "language": code.language,
                    }),
                    EntityMetadata::Markdown(markdown) => json!({
                        "kind": "markdown",
                        "heading": markdown.heading,
                        "scope": markdown.scope,
                    }),
                });
                json!({
                    "path": item.relative_path.to_string_lossy(),
                    "start_line": item.range.start_line(),
                    "last_line": item.range.last_line(),
                    "line_count": item.range.line_count(),
                    "content_chars": item.content.chars().count(),
                    "matched_by": item.matched_by,
                    "entity_id": item.entity_id,
                    "score": item.score,
                    "status": item.status,
                    "metadata": metadata,
                })
            })
            .collect::<Vec<_>>();
        rows.push(json!({
            "id": case.id,
            "call": case.call,
            "requested_mode": case.mode,
            "query": case.query,
            "prefer_symbol": case.prefer_symbol,
            "fuse": fuse,
            "routes": routes.iter().map(|r| json!({
                "mode": match r.mode {
                    ContextRouteMode::Fts => "fts",
                    ContextRouteMode::Vector => "vector",
                },
                "query": r.query,
            })).collect::<Vec<_>>(),
            "wall_ms": wall_ms,
            "item_count": result.items.len(),
            "empty_reason": result.diagnostics.empty_reason,
            "index": result.diagnostics.index,
            "items": items,
        }));
    }
    engine.close();
    let evidence = json!({
        "schema": "rag-requal-probe/v1",
        "root": root,
        "info": info,
        "limit": limit,
        "peak_rss_kib": process_value("VmHWM:"),
        "queries": rows,
    });
    fs::write(out, serde_json::to_vec_pretty(&evidence)?)?;
    Ok(())
}

fn process_value(name: &str) -> u64 {
    fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|status| {
            status
                .lines()
                .find_map(|line| line.strip_prefix(name))
                .and_then(|value| value.split_whitespace().next())
                .and_then(|value| value.parse().ok())
        })
        .unwrap_or(0)
}
