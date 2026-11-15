//! Exact-symbol Discovery probe over an existing index (read-only).
//!
//! ```text
//! rag_symbols_probe --root DIR --input names.json --out results.json
//! ```
//!
//! Input: `{"queries":[{"id","names":["Name1","Name2"]}]}`. Symbols are exact,
//! case-sensitive declaration names from the question; no refresh is requested.

use std::{fs, path::PathBuf};

use serde::Deserialize;
use serde_json::json;
use zg_engine::{
    ZvecGrep,
    api::symbols::{SymbolsOptions, SymbolsResult},
};

#[derive(Deserialize)]
struct Input {
    queries: Vec<Query>,
}

#[derive(Deserialize)]
struct Query {
    id: String,
    names: Vec<String>,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let (mut root, mut input, mut out) = (None, None, None);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--root" => root = args.next().map(PathBuf::from),
            "--input" => input = args.next().map(PathBuf::from),
            "--out" => out = args.next().map(PathBuf::from),
            other => panic!("unknown argument {other}"),
        }
    }
    let root = root.expect("--root");
    let input = input.expect("--input");
    let out = out.expect("--out");
    let corpus: Input = serde_json::from_slice(&fs::read(input)?)?;
    let engine = ZvecGrep::new();
    let mut rows = Vec::new();
    for case in corpus.queries {
        let mut items = Vec::new();
        let mut failures = Vec::new();
        for name in &case.names {
            match engine
                .symbols(SymbolsOptions {
                    root: Some(root.clone()),
                    name: name.clone(),
                    auto_update: false,
                    limit: Some(50),
                    ..SymbolsOptions::default()
                })
                .await
            {
                Ok(SymbolsResult { items: found, .. }) => {
                    for item in found {
                        items.push(json!({
                            "query_name": name,
                            "entity_id": item.entity_id,
                            "name": item.name,
                            "symbol_type": item.symbol_type,
                            "scope": item.scope,
                            "signature": item.signature,
                            "language": item.language,
                            "path": item.relative_path.to_string_lossy(),
                            "start_line": item.range.start_line(),
                            "last_line": item.range.last_line(),
                            "line_count": item.range.line_count(),
                        }));
                    }
                }
                Err(error) => failures.push(json!({"name": name, "error": error.code()})),
            }
        }
        rows.push(json!({
            "id": case.id,
            "names": case.names,
            "items": items,
            "failures": failures,
        }));
    }
    engine.close();
    let evidence = json!({
        "schema": "rag-symbols-probe/v1",
        "root": root,
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
