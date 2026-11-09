//! Read-only index capability probe for the RAG One-Index architecture audit.
//!
//! Opens an existing workspace index and runs bounded FTS / symbol queries,
//! sampling `/proc/self` PSS/RSS around each step. No index writes, no corpus
//! changes: pass a workspace whose `.zvec-grep/index` already exists.
//!
//! Usage: cargo run -p zg-engine --example index_capability_probe -- <workspace> [query...]

use std::path::PathBuf;
use std::time::Instant;

use zg_engine::ZvecGrep;
use zg_engine::api::context::ContextOptions;
use zg_engine::api::context::options::{ContextRoute, ContextRouteMode, QueryFilter, SymbolType};

fn status_kib(key: &str) -> u64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|text| {
            text.lines()
                .find(|line| line.starts_with(key))
                .and_then(|line| line.split_whitespace().nth(1))
                .and_then(|value| value.parse().ok())
        })
        .unwrap_or(0)
}

fn pss_kib() -> u64 {
    std::fs::read_to_string("/proc/self/smaps_rollup")
        .ok()
        .and_then(|text| {
            text.lines()
                .find(|line| line.starts_with("Pss:"))
                .and_then(|line| line.split_whitespace().nth(1))
                .and_then(|value| value.parse().ok())
        })
        .unwrap_or(0)
}

fn sample(label: &str, start: Instant) -> serde_json::Value {
    serde_json::json!({
        "label": label,
        "elapsed_ms": start.elapsed().as_secs_f64() * 1000.0,
        "rss_kib": status_kib("VmRSS:"),
        "rss_hwm_kib": status_kib("VmHWM:"),
        "pss_kib": pss_kib(),
    })
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let root = PathBuf::from(args.next().expect("workspace root"));
    let queries: Vec<String> = args.collect();
    let queries = if queries.is_empty() {
        vec!["employee".to_owned()]
    } else {
        queries
    };

    let start = Instant::now();
    let mut phases = vec![sample("process_start", start)];
    let engine = ZvecGrep::new();

    let mut rows = Vec::new();
    for (index, query) in queries.iter().enumerate() {
        for prefer_symbol in [false, true] {
            for symbol_types in [
                Vec::new(),
                vec![SymbolType::Class],
                vec![SymbolType::Function],
            ] {
                let before = sample("before_query", start);
                let options = ContextOptions {
                    query: Some(query.clone()),
                    root: Some(root.clone()),
                    routes: vec![ContextRoute {
                        mode: ContextRouteMode::Fts,
                        query: query.clone(),
                    }],
                    prefer_symbol,
                    limit: Some(8),
                    auto_update: false,
                    refresh: Some(zg_engine::api::context::options::RefreshPolicy::Off),
                    ..ContextOptions::default()
                };
                let options = ContextOptions {
                    filter: QueryFilter {
                        symbol_types: symbol_types.clone(),
                        ..QueryFilter::default()
                    },
                    ..options
                };
                let started = Instant::now();
                let result = engine.context(options).await;
                let elapsed = started.elapsed().as_secs_f64() * 1000.0;
                match result {
                    Ok(context) => {
                        let items: Vec<serde_json::Value> = context
                            .items
                            .iter()
                            .take(8)
                            .map(|item| {
                                let symbol = item
                                    .metadata
                                    .as_ref()
                                    .map(|metadata| {
                                        serde_json::to_value(metadata)
                                            .unwrap_or(serde_json::Value::Null)
                                    })
                                    .unwrap_or(serde_json::Value::Null);
                                serde_json::json!({
                                    "path": item.relative_path,
                                    "range": format!("{:?}", item.range),
                                    "kind": format!("{:?}", item.kind),
                                    "matched_by": format!("{:?}", item.matched_by),
                                    "score": item.score,
                                    "symbol": symbol,
                                    "container": item.container.as_ref().map(|c| format!("{:?}", c.range)),
                                })
                            })
                            .collect();
                        rows.push(serde_json::json!({
                            "query": query,
                            "prefer_symbol": prefer_symbol,
                            "symbol_types": symbol_types,
                            "elapsed_ms": elapsed,
                            "items": items,
                            "diagnostics": {
                                "empty_reason": context.diagnostics.empty_reason.map(|r| format!("{r:?}")),
                                "hits": context.diagnostics.index.as_ref().map(|i| i.hits_returned),
                                "routes": context.diagnostics.index.as_ref().map(|i| {
                                    i.routes.iter().map(|r| r.id.clone()).collect::<Vec<_>>()
                                }),
                            },
                            "pss_after": pss_kib(),
                            "rss_after": status_kib("VmRSS:"),
                            "hwm_after": status_kib("VmHWM:"),
                        }));
                    }
                    Err(error) => {
                        rows.push(serde_json::json!({
                            "query": query,
                            "prefer_symbol": prefer_symbol,
                            "symbol_types": symbol_types,
                            "elapsed_ms": elapsed,
                            "error": error.to_string(),
                        }));
                    }
                }
                if index == 0 && !prefer_symbol && symbol_types.is_empty() {
                    phases.push(before);
                }
            }
        }
    }
    phases.push(sample("end", start));
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "INDEX_CAPABILITY_PROBE",
            "root": root,
            "phases": phases,
            "rows": rows,
            "rss_hwm_kib": status_kib("VmHWM:"),
            "pss_end_kib": pss_kib(),
        }))?
    );
    Ok(())
}
