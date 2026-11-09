//! Exact-symbol latency and pagination probe for v3.
//!
//! ```text
//! symbol_bench --root DIR --out FILE --name Widget --limit 50
//! ```

use std::{collections::BTreeSet, fs, path::PathBuf, time::Instant};

use serde_json::json;
use zg_engine::{ZvecGrep, api::symbols::SymbolsOptions};

#[tokio::main]
#[allow(unused_assignments)]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let mut root = None;
    let mut out = None;
    let mut name = "Widget".to_owned();
    let mut limit = 50;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--root" => root = args.next().map(PathBuf::from),
            "--out" => out = args.next().map(PathBuf::from),
            "--name" => name = args.next().expect("name"),
            "--limit" => limit = args.next().expect("limit").parse().expect("limit"),
            other => panic!("unknown argument {other}"),
        }
    }
    let root = root.expect("--root");
    let out = out.expect("--out");
    let engine = ZvecGrep::new();
    let mut cold = None;
    let mut warm = Vec::new();
    for index in 0..21 {
        let started = Instant::now();
        let result = engine
            .symbols(SymbolsOptions {
                root: Some(root.clone()),
                name: name.clone(),
                auto_update: false,
                limit: Some(limit),
                ..SymbolsOptions::default()
            })
            .await?;
        let elapsed = started.elapsed().as_micros();
        if index == 0 {
            cold = Some(elapsed);
            assert!(result.total > 0);
        } else {
            warm.push(elapsed);
        }
    }
    warm.sort_unstable();

    let started = Instant::now();
    let mut cursor = None;
    let mut ids = BTreeSet::new();
    let mut pages = 0;
    let mut total = 0;
    let mut complete = None;
    loop {
        let page = engine
            .symbols(SymbolsOptions {
                root: Some(root.clone()),
                name: name.clone(),
                auto_update: false,
                limit: Some(limit),
                cursor,
                ..SymbolsOptions::default()
            })
            .await?;
        total = page.total;
        complete = Some(page.coverage.complete);
        ids.extend(page.items.into_iter().map(|item| item.entity_id));
        pages += 1;
        match page.next_cursor {
            Some(next) => cursor = Some(next),
            None => break,
        }
        assert!(pages < 100_000);
    }
    let paged_us = started.elapsed().as_micros();
    let evidence = json!({
        "name": name,
        "limit": limit,
        "cold_us": cold,
        "warm_p50_us": warm[warm.len() / 2],
        "warm_p95_us": warm[warm.len() * 95 / 100],
        "warm_max_us": warm.last(),
        "total": total,
        "pages": pages,
        "collected": ids.len(),
        "coverage_complete": complete.unwrap_or(false),
        "paged_total_us": paged_us,
        "self": process_usage(),
    });
    engine.close();
    fs::write(&out, serde_json::to_vec_pretty(&evidence)?)?;
    println!("{}", serde_json::to_string_pretty(&evidence)?);
    Ok(())
}

fn process_usage() -> serde_json::Value {
    let status = fs::read_to_string("/proc/self/status").unwrap_or_default();
    let value = |name: &str| {
        status
            .lines()
            .find_map(|line| line.strip_prefix(name))
            .and_then(|value| value.split_whitespace().next())
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(0)
    };
    let pss = fs::read_to_string("/proc/self/smaps_rollup")
        .ok()
        .and_then(|text| {
            text.lines()
                .find_map(|line| line.strip_prefix("Pss:"))
                .and_then(|value| value.split_whitespace().next())
                .and_then(|value| value.parse::<u64>().ok())
        })
        .unwrap_or(0);
    json!({
        "rss_kib": value("VmRSS:"),
        "peak_rss_kib": value("VmHWM:"),
        "pss_kib": pss,
    })
}
