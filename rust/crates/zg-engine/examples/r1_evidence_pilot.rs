//! R1 release-gate evidence pilot on the frozen HRM2.0 development cases.
//!
//! Read-only: assembles/revalidates source spans from the frozen dev cases. It
//! does not search, index, call a model, inspect held-out data or write source.

use std::fs;
use std::path::PathBuf;
use std::time::Instant;

use zg_engine::api::evidence::{EvidenceBudget, EvidenceRequest, assemble, revalidate};

fn line_offsets(text: &str) -> Vec<usize> {
    let mut offsets = vec![0usize];
    let mut total = 0usize;
    for line in text.split_inclusive('\n') {
        total += line.len();
        offsets.push(total);
    }
    offsets
}

fn pss_kib() -> Option<u64> {
    let text = fs::read_to_string("/proc/self/smaps_rollup").ok()?;
    text.lines()
        .find_map(|line| line.strip_prefix("Pss:"))
        .and_then(|value| value.split_whitespace().next())
        .and_then(|value| value.parse().ok())
}

#[allow(clippy::too_many_lines)]
fn main() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let corpus: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/corpus-manifest.json"))
            .expect("corpus manifest"),
    )
    .expect("valid corpus manifest");
    let dev: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/cases/dev.json")).expect("dev cases"),
    )
    .expect("valid dev cases");
    let root = PathBuf::from(corpus["source_root"].as_str().expect("source root"));
    let mut cases = Vec::new();
    let mut total_ms = 0f64;
    for case in dev["cases"].as_array().expect("cases") {
        let Some(id) = case["id"].as_str() else {
            continue;
        };
        if id.starts_with("D13")
            || id.starts_with("D14")
            || id.starts_with("D15")
            || id.starts_with("D16")
        {
            continue;
        }
        let mut requests = Vec::new();
        for reference in case["source_refs"].as_array().into_iter().flatten() {
            let path = reference["path"].as_str().expect("path");
            let source = fs::read_to_string(root.join(path)).expect("source");
            let offsets = line_offsets(&source);
            for span in reference["spans"].as_array().into_iter().flatten() {
                let start_line = usize::try_from(span["start_line"].as_u64().expect("start"))
                    .expect("start line fits usize");
                let end_line = usize::try_from(span["end_line"].as_u64().expect("end"))
                    .expect("end line fits usize");
                let start_byte = offsets[start_line.saturating_sub(1).min(offsets.len() - 1)];
                let end_line_index = end_line.min(offsets.len() - 1);
                let line_start = offsets[end_line_index.saturating_sub(1)];
                let line_end = offsets[end_line_index];
                let end_byte = line_start
                    + source[line_start..line_end]
                        .trim_end_matches(['\r', '\n'])
                        .len();
                requests.push(EvidenceRequest {
                    project: "HRM2.0".to_owned(),
                    relative_path: PathBuf::from(path),
                    expected_sha256: reference["sha256"].as_str().expect("sha").to_owned(),
                    start_byte,
                    end_byte: end_byte.max(start_byte),
                    expected_start_line: Some(start_line),
                    expected_end_line: Some(end_line),
                });
            }
        }
        let started = Instant::now();
        let pack = assemble(
            &root,
            &requests,
            EvidenceBudget {
                max_bytes: 24 * 1024,
                max_items: 32,
            },
        )
        .expect("assemble");
        let revalidation = revalidate(&root, &pack).expect("revalidate");
        let elapsed_ms = started.elapsed().as_secs_f64() * 1000.0;
        total_ms += elapsed_ms;
        cases.push(serde_json::json!({
            "id": id,
            "requests": requests.len(),
            "status": format!("{:?}", pack.status),
            "items": pack.items.len(),
            "gaps": pack.gaps,
            "deduplicated": pack.deduplicated,
            "revalidation_gaps": revalidation,
            "elapsed_ms": elapsed_ms,
        }));
    }
    let receipt = serde_json::json!({
        "schema_version": 1,
        "status": "R1_V2_DEV_EVIDENCE_PILOT",
        "source_root": root,
        "cases": cases,
        "total_elapsed_ms": total_ms,
        "process_pss_kib": pss_kib(),
        "limits": [
            "development cases only; held-out was not inspected",
            "24 KiB per-case evidence budget",
            "no search/model/index; read-only assemble+revalidate"
        ],
    });
    let out = std::env::var("R1_OUT").unwrap_or_else(|_| "/tmp/r1-v2-dev-pilot.json".to_owned());
    fs::write(&out, serde_json::to_string_pretty(&receipt).expect("json")).expect("write receipt");
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "R1_V2_DEV_EVIDENCE_PILOT",
            "cases": receipt["cases"].as_array().map_or(0, Vec::len),
            "total_elapsed_ms": total_ms,
            "process_pss_kib": pss_kib(),
        }))
        .expect("summary")
    );
}
