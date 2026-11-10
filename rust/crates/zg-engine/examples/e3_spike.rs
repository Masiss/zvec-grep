//! E3 pre-spike harness: index a real C# corpus with a real local embedding
//! model, then dump ranked hits per route so quality can be scored offline.
//!
//! Usage:
//!   `e3_spike --root DIR --out FILE --phase index|query --model REF`
//!   `[--cache DIR] [--queries FILE] [--limit N] [--repeat N]`
//!
//! `--repeat` runs each query route N times and keeps the fastest sample.
//!
//! This is a measurement tool only; it uses the public engine API.

use std::{collections::BTreeMap, fs, path::PathBuf};

use serde_json::{Value, json};
use zg_engine::{
    ZvecGrep,
    api::{
        context::{
            ContextOptions,
            options::{ContextRoute, ContextRouteMode},
        },
        index::IndexOptions,
    },
};

struct Args {
    root: PathBuf,
    out: PathBuf,
    phase: String,
    model: String,
    cache: Option<PathBuf>,
    queries: Option<PathBuf>,
    limit: usize,
    repeat: usize,
}

fn parse_args() -> Args {
    let mut args = std::env::args().skip(1);
    let mut root = None;
    let mut out = None;
    let mut phase = "index".to_owned();
    let mut model = "local/embeddinggemma-300m".to_owned();
    let mut cache = None;
    let mut queries = None;
    let mut limit = 50usize;
    let mut repeat = 20usize;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--root" => root = args.next().map(PathBuf::from),
            "--out" => out = args.next().map(PathBuf::from),
            "--phase" => phase = args.next().expect("phase"),
            "--model" => model = args.next().expect("model"),
            "--cache" => cache = args.next().map(PathBuf::from),
            "--queries" => queries = args.next().map(PathBuf::from),
            "--limit" => limit = args.next().expect("limit").parse().expect("limit"),
            "--repeat" => repeat = args.next().expect("repeat").parse().expect("repeat"),
            other => panic!("unknown argument {other}"),
        }
    }
    Args {
        root: root.expect("--root"),
        out: out.expect("--out"),
        phase,
        model,
        cache,
        queries,
        limit,
        repeat,
    }
}

fn configure_workspace(
    root: &std::path::Path,
    model: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let home = root.join(".zvec-grep");
    if home.exists() {
        fs::remove_dir_all(&home)?;
    }
    fs::create_dir_all(&home)?;
    let (provider, name) = model
        .split_once('/')
        .expect("provider/name model reference");
    let descriptor = json!({
        "defaultEmbeddingModel": {
            "model": {
                "provider": provider, "name": name,
                "contentKinds": ["text", "code"], "maxBatchSize": 16
            },
            "dimension": 768, "metric": "cosine",
            "maxInputTokens": 2048, "maxImageBytes": null
        },
        "embeddingRoutes": {},
        "embeddingConfigs": {}
    });
    let manifest = json!({
        "sourceRoot": {
            "recordedPath": fs::canonicalize(root)?.to_string_lossy(),
            "recordedUsername": std::env::var("USER").ok(),
        },
        "scan": {},
        "indexVersion": 3,
        "storageGeneration": uuid::Uuid::new_v4().to_string(),
        "createdTime": 1,
        "updatedTime": 1,
        "index": { "state": "enabled", "descriptor": descriptor }
    });
    fs::write(home.join("manifest.json"), serde_json::to_vec(&manifest)?)?;
    Ok(())
}

#[expect(
    clippy::too_many_lines,
    reason = "one linear index/query harness with a single evidence map"
)]
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = parse_args();
    let engine = ZvecGrep::new();
    let mut evidence = BTreeMap::new();
    evidence.insert("phase", json!(args.phase));
    evidence.insert("model", json!(args.model));
    evidence.insert("root", json!(args.root.display().to_string()));

    match args.phase.as_str() {
        "index" => {
            if !args.root.join(".zvec-grep/manifest.json").exists() {
                configure_workspace(&args.root, &args.model)?;
            }
            let started = std::time::Instant::now();
            let result = engine
                .index(IndexOptions {
                    root: Some(args.root.clone()),
                    allow_remote: false,
                    model_cache: args.cache.clone(),
                    ..IndexOptions::default()
                })
                .await?;
            evidence.insert("wall_ms", json!(started.elapsed().as_millis()));
            evidence.insert("entities_created", json!(result.entities_created));
            evidence.insert(
                "files",
                json!({
                    "added": result.files_added,
                    "failed": result.files_failed,
                }),
            );
            evidence.insert("timings", serde_json::to_value(&result.timings)?);
            evidence.insert(
                "index",
                json!({ "path": args.root.join(".zvec-grep/index").display().to_string() }),
            );
        }
        "query" => {
            let queries: Vec<Value> =
                serde_json::from_slice(&fs::read(args.queries.as_ref().expect("--queries"))?)?;
            let mut results = Vec::new();
            for query in &queries {
                let id = query["id"].as_str().expect("id").to_owned();
                let text = query["query"].as_str().expect("query").to_owned();
                let mut modes = BTreeMap::new();
                for (label, routes) in [
                    ("fts", vec![ContextRouteMode::Fts]),
                    ("vector", vec![ContextRouteMode::Vector]),
                    (
                        "hybrid",
                        vec![ContextRouteMode::Fts, ContextRouteMode::Vector],
                    ),
                ] {
                    let fuse = routes.len() > 1;
                    let options = ContextOptions {
                        root: Some(args.root.clone()),
                        routes: routes
                            .iter()
                            .map(|mode| ContextRoute {
                                mode: *mode,
                                query: text.clone(),
                            })
                            .collect(),
                        fuse,
                        auto_update: false,
                        allow_remote: false,
                        limit: Some(args.limit),
                        ..ContextOptions::default()
                    };
                    let mut best = u128::MAX;
                    let mut result = None;
                    for _ in 0..args.repeat.max(1) {
                        let started = std::time::Instant::now();
                        let measured = engine.context(options.clone()).await?;
                        best = best.min(started.elapsed().as_micros());
                        result = Some(measured);
                    }
                    let result = result.expect("at least one measurement");
                    let elapsed = best;
                    let hits = result
                        .items
                        .iter()
                        .map(|item| {
                            json!({
                                "entity_id": item.entity_id,
                                "path": item.relative_path.to_string_lossy(),
                                "symbol_type": item
                                    .metadata
                                    .as_ref()
                                    .map(|metadata| serde_json::to_value(metadata).unwrap_or(Value::Null)),
                                "matched_by": item.matched_by,
                                "rank": item.rank,
                            })
                        })
                        .collect::<Vec<_>>();
                    modes.insert(
                        label,
                        json!({ "us": elapsed, "items": hits.len(), "hits": hits }),
                    );
                }
                results.push(json!({ "id": id, "query": text, "modes": modes }));
            }
            evidence.insert("queries", json!(results));
        }
        other => panic!("unknown phase {other}"),
    }
    evidence.insert("usage", json!({
        "rss_kib": std::fs::read_to_string("/proc/self/status").ok()
            .and_then(|text| text.lines().find_map(|line| line.strip_prefix("VmHWM:").map(|v| v.trim().to_owned()))),
    }));
    fs::write(&args.out, serde_json::to_vec_pretty(&evidence)?)?;
    engine.close();
    Ok(())
}
