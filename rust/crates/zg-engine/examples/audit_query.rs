//! Flexible FTS audit probe over an existing index.
//!
//! ```text
//! audit_query --root DIR --query TEXT [--limit N] [--out FILE]
//! ```

use std::{collections::BTreeMap, fs, path::PathBuf};

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
    let mut query = None;
    let mut limit = 10;
    let mut out = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--root" => root = args.next().map(PathBuf::from),
            "--query" => query = args.next(),
            "--limit" => limit = args.next().expect("limit").parse().expect("limit"),
            "--out" => out = args.next().map(PathBuf::from),
            other => panic!("unknown argument {other}"),
        }
    }
    let root = root.expect("--root");
    let query = query.expect("--query");
    let engine = ZvecGrep::new();
    let result = engine
        .context(ContextOptions {
            root: Some(root.clone()),
            routes: vec![ContextRoute {
                mode: ContextRouteMode::Fts,
                query: query.clone(),
            }],
            auto_update: false,
            allow_remote: true,
            limit: Some(limit),
            ..ContextOptions::default()
        })
        .await?;
    let items = result
        .items
        .iter()
        .map(|item| {
            let metadata = item.metadata.as_ref().map(|metadata| match metadata {
                zg_engine::api::context::result::EntityMetadata::Code(code) => json!({
                    "symbol_name": code.symbol_name,
                    "symbol_type": code.symbol_type,
                    "scope": code.scope,
                    "language": code.language,
                }),
                zg_engine::api::context::result::EntityMetadata::Markdown(markdown) => json!({
                    "heading": markdown.heading,
                    "scope": markdown.scope,
                }),
            });
            json!({
                "path": item.relative_path,
                "entity_id": item.entity_id,
                "matched_by": item.matched_by,
                "range": item.range,
                "metadata": metadata,
                "content": item.content.chars().take(200).collect::<String>(),
            })
        })
        .collect::<Vec<_>>();
    let mut per_file = BTreeMap::new();
    for item in &result.items {
        *per_file
            .entry(item.relative_path.to_string_lossy().into_owned())
            .or_insert(0usize) += 1;
    }
    let evidence = json!({
        "query": query,
        "limit": limit,
        "items": items,
        "distinct_entities": result.items.iter().filter_map(|item| item.entity_id.as_ref()).collect::<std::collections::BTreeSet<_>>().len(),
        "distinct_files": per_file.len(),
        "per_file": per_file,
        "diagnostics": result.diagnostics,
    });
    engine.close();
    if let Some(out) = out {
        fs::write(out, serde_json::to_vec_pretty(&evidence)?)?;
    }
    println!("{}", serde_json::to_string_pretty(&evidence)?);
    Ok(())
}
