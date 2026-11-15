//! Deterministic retrieval parity probe for an existing index.
//!
//! ```text
//! query_check --root DIR --out FILE --corpus mixed|dense|duplicate
//! ```

use std::{fs, path::PathBuf};

use serde_json::json;
use zg_engine::{
    ZvecGrep,
    api::context::{
        ContextOptions,
        options::{ContextRoute, ContextRouteMode},
    },
};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let mut root = None;
    let mut out = None;
    let mut corpus = "mixed".to_owned();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--root" => root = args.next().map(PathBuf::from),
            "--out" => out = args.next().map(PathBuf::from),
            "--corpus" => corpus = args.next().expect("corpus"),
            other => panic!("unknown argument {other}"),
        }
    }
    let root = root.expect("--root");
    let out = out.expect("--out");
    let engine = ZvecGrep::new();
    let (query, expected) = match corpus.as_str() {
        "dense" => ("Dense0", "src/cs/Dense0.cs"),
        "duplicate" => ("Widget", "src/cs/Widget"),
        _ => ("Type0", "src/cs/Type0.cs"),
    };
    let mut modes = Vec::new();
    for (label, routes, prefer_symbol) in [
        ("fts", vec![ContextRouteMode::Fts], false),
        ("fts_prefer_symbol", vec![ContextRouteMode::Fts], true),
    ] {
        let result = engine
            .context(ContextOptions {
                root: Some(root.clone()),
                routes: routes
                    .iter()
                    .map(|mode| ContextRoute {
                        mode: *mode,
                        query: query.to_owned(),
                    })
                    .collect(),
                fuse: routes.len() > 1,
                prefer_symbol,
                auto_update: false,
                limit: Some(10),
                ..ContextOptions::default()
            })
            .await?;
        let paths = result
            .items
            .iter()
            .map(|item| item.relative_path.to_string_lossy().into_owned())
            .collect::<Vec<_>>();
        modes.push(json!({
            "mode": label,
            "items": result.items.len(),
            "expected_hit": paths.iter().any(|path| path.contains(expected)),
            "paths": paths,
        }));
    }
    let evidence = json!({
        "corpus": corpus,
        "query": query,
        "expected": expected,
        "modes": modes,
        "self": {
            "rss_kib": process_value("VmRSS:"),
            "peak_rss_kib": process_value("VmHWM:"),
        },
    });
    engine.close();
    fs::write(&out, serde_json::to_vec_pretty(&evidence)?)?;
    println!("{}", serde_json::to_string_pretty(&evidence)?);
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
