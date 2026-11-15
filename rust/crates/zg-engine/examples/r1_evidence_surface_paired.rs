//! Paired prototype benchmark for the opt-in R1 evidence surface.
//!
//! Arms (all scripted, no model, no index, no agent):
//!   A  trust the snippet, no revalidation
//!   B  re-read the cited line range and compare text (snippet-only capability)
//!   C  span pack with the frozen expected hash, i.e. verify a prior citation
//!   D  span pack with no prior hash, then immediate revalidation (control)
//!
//! Fixture: a scratch copy of the sources cited by frozen dev cases D01-D12.
//! The frozen corpus is only read. Mutations are applied to the copy.
//!
//! Usage: `cargo run -p zg-engine --example r1_evidence_surface_paired`
//! Env:   `ZG_SURFACE_FIXTURE_ROOT` (default under `~/.cache/zvec-grep`)

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::Serialize;
use sha2::{Digest, Sha256};
use zg_engine::api::evidence::EvidenceBudget;
use zg_engine::api::evidence::surface::{
    MAX_PACK_BYTES, MAX_PACK_ITEMS, SpanAddress, SpanRequest, assemble_spans, revalidate_spans,
};

const CASES: &[&str] = &[
    "D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D10", "D11", "D12",
];

#[derive(Clone, Debug)]
struct FrozenSpan {
    case: String,
    path: String,
    sha256: String,
    start_line: usize,
    end_line: usize,
    text: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
enum Arm {
    A,
    B,
    C,
    D,
}

#[derive(Clone, Debug, Default, Serialize)]
struct ArmMetrics {
    tested: usize,
    detected: usize,
    not_detected: usize,
    gap_codes: BTreeMap<String, usize>,
    bytes_read: usize,
    elapsed_ms: f64,
}

#[derive(Clone, Debug, Serialize)]
struct ConditionReport {
    id: String,
    description: String,
    arms: Vec<(String, ArmMetrics)>,
}

#[derive(Serialize)]
struct Receipt {
    status: &'static str,
    date: String,
    branch_head: String,
    corpus_source_root: String,
    corpus_manifest_sha256: String,
    cases: Vec<String>,
    spans: usize,
    spans_by_case: BTreeMap<String, usize>,
    files: usize,
    budget: BTreeMap<String, usize>,
    host: HostState,
    conditions: Vec<ConditionReport>,
    per_case_packs: Vec<CasePack>,
    per_case_totals: PerCaseTotals,
}

#[derive(Serialize)]
struct PerCaseTotals {
    requested_spans: usize,
    items: usize,
    gaps: usize,
    max_content_bytes: usize,
    assemble_ms_total: f64,
    revalidate_ms_total: f64,
}

#[derive(Serialize)]
struct CasePack {
    case: String,
    requested_spans: usize,
    items: usize,
    gaps: usize,
    deduplicated: usize,
    gap_codes: BTreeMap<String, usize>,
    content_bytes: usize,
    serialized_bytes: usize,
    assemble_ms: f64,
    revalidate_gaps: usize,
    revalidate_ms: f64,
}

#[derive(Serialize)]
struct HostState {
    loadavg: String,
    mem_available_kib: u64,
    pss_kib_before: Option<u64>,
    pss_kib_after: Option<u64>,
}

const CONDITIONS: &[(&str, &str)] = &[
    ("unchanged", "no mutation"),
    (
        "edit_outside_range",
        "append a line at EOF (outside the cited span)",
    ),
    (
        "edit_inside_range",
        "append a marker to the first cited line",
    ),
    (
        "same_size_substitution_inside_range",
        "replace the first 8 bytes of the first cited line",
    ),
    (
        "line_inserted_above_range",
        "insert one line at the top of the file (shifts line numbers)",
    ),
    ("renamed", "rename the cited file"),
    ("deleted", "delete the cited file"),
    (
        "crlf_lf_conversion",
        "convert line endings across the whole file",
    ),
];

fn pss_kib() -> Option<u64> {
    let text = fs::read_to_string("/proc/self/smaps_rollup").ok()?;
    text.lines()
        .find_map(|line| line.strip_prefix("Pss:"))
        .and_then(|value| value.split_whitespace().next())
        .and_then(|value| value.parse().ok())
}

fn mem_available_kib() -> u64 {
    fs::read_to_string("/proc/meminfo")
        .ok()
        .and_then(|text| {
            text.lines().find_map(|line| {
                line.strip_prefix("MemAvailable:")
                    .and_then(|value| value.split_whitespace().next())
                    .and_then(|value| value.parse().ok())
            })
        })
        .unwrap_or(0)
}

fn line_starts(text: &str) -> Vec<usize> {
    let mut starts = vec![0];
    for (index, byte) in text.as_bytes().iter().enumerate() {
        if *byte == b'\n' {
            starts.push(index + 1);
        }
    }
    starts
}

/// One-based inclusive lines to half-open bytes, excluding the trailing terminator.
fn line_bounds(text: &str, start_line: usize, end_line: usize) -> Option<(usize, usize)> {
    if start_line == 0 || end_line < start_line {
        return None;
    }
    let line_count = text.split_inclusive('\n').count();
    if end_line > line_count {
        return None;
    }
    let starts = line_starts(text);
    let start = starts[start_line - 1];
    let mut end = starts.get(end_line).copied().unwrap_or(text.len());
    if text.as_bytes().get(end.wrapping_sub(1)) == Some(&b'\n') {
        end = end.saturating_sub(1);
        if text.as_bytes().get(end.wrapping_sub(1)) == Some(&b'\r') {
            end = end.saturating_sub(1);
        }
    }
    (end >= start).then_some((start, end))
}

fn sha256(text: &str) -> String {
    hex::encode(Sha256::digest(text.as_bytes()))
}

fn read_frozen_spans(repo: &Path, corpus_root: &Path) -> Vec<FrozenSpan> {
    let dev: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/cases/dev.json")).expect("dev cases"),
    )
    .expect("valid dev cases");
    let mut spans = Vec::new();
    for case in dev["cases"].as_array().expect("cases") {
        let id = case["id"].as_str().expect("case id").to_owned();
        if !CASES.contains(&id.as_str()) {
            continue;
        }
        for reference in case["source_refs"].as_array().into_iter().flatten() {
            let path = reference["path"].as_str().expect("path").to_owned();
            let expected_sha = reference["sha256"].as_str().expect("sha").to_owned();
            let text = fs::read_to_string(corpus_root.join(&path)).expect("frozen source");
            assert_eq!(
                sha256(&text),
                expected_sha,
                "frozen source hash changed for {path}"
            );
            for span in reference["spans"].as_array().into_iter().flatten() {
                let start_line = usize::try_from(span["start_line"].as_u64().expect("start"))
                    .expect("start fits usize");
                let end_line = usize::try_from(span["end_line"].as_u64().expect("end"))
                    .expect("end fits usize");
                let (start, end) =
                    line_bounds(&text, start_line, end_line).expect("frozen span bounds");
                spans.push(FrozenSpan {
                    case: id.clone(),
                    path: path.clone(),
                    sha256: expected_sha.clone(),
                    start_line,
                    end_line,
                    text: text[start..end].to_owned(),
                });
            }
        }
    }
    spans
}

fn mutate(root: &Path, files: &BTreeSet<String>, condition: &str, spans: &[FrozenSpan]) {
    for relative in files {
        let path = root.join(relative);
        if condition == "renamed" || condition == "deleted" {
            continue;
        }
        let original = fs::read_to_string(&path).expect("fixture source");
        let first_span = spans
            .iter()
            .find(|span| &span.path == relative)
            .expect("span for file");
        let mutated = match condition {
            "unchanged" => original,
            "edit_outside_range" => format!("{original}// appended outside cited range\n"),
            "edit_inside_range" => {
                let (_, end) = line_bounds(&original, first_span.start_line, first_span.start_line)
                    .expect("first cited line");
                format!("{} /* mutated */{}", &original[..end], &original[end..])
            }
            "same_size_substitution_inside_range" => {
                let (start, end) =
                    line_bounds(&original, first_span.start_line, first_span.start_line)
                        .expect("first cited line");
                let line = &original[start..end];
                if line.len() >= 8 {
                    let mut mutated = String::with_capacity(original.len());
                    mutated.push_str(&original[..start]);
                    mutated.push_str("MUTATED!");
                    mutated.push_str(&original[start + 8..]);
                    mutated
                } else {
                    format!("MUTATED!{original}")
                }
            }
            "line_inserted_above_range" => format!("// inserted above cited range\n{original}"),
            "crlf_lf_conversion" => {
                if original.contains("\r\n") {
                    original.replace("\r\n", "\n")
                } else {
                    original.replace('\n', "\r\n")
                }
            }
            other => panic!("unknown condition {other}"),
        };
        fs::write(&path, mutated).expect("mutated fixture");
    }
    if condition == "renamed" {
        for relative in files {
            let from = root.join(relative);
            let to = root.join(format!("{relative}.moved"));
            fs::rename(from, to).expect("renamed fixture");
        }
    }
    if condition == "deleted" {
        for relative in files {
            fs::remove_file(root.join(relative)).expect("deleted fixture");
        }
    }
}

fn arm_a(_span: &FrozenSpan, _root: &Path) -> (bool, Option<String>, usize) {
    (false, None, 0)
}

fn arm_b(span: &FrozenSpan, root: &Path) -> (bool, Option<String>, usize) {
    let path = root.join(&span.path);
    let Ok(bytes) = fs::read(&path) else {
        return (true, Some("missing".to_owned()), 0);
    };
    let size = bytes.len();
    let Ok(text) = String::from_utf8(bytes) else {
        return (true, Some("not_utf8".to_owned()), size);
    };
    let Some((start, end)) = line_bounds(&text, span.start_line, span.end_line) else {
        return (true, Some("stale_coordinates".to_owned()), size);
    };
    (
        text[start..end] != span.text,
        (text[start..end] != span.text).then(|| "text_changed".to_owned()),
        size,
    )
}

fn span_request(span: &FrozenSpan, expected_sha256: Option<String>) -> SpanRequest {
    SpanRequest {
        project: "HRM2.0".to_owned(),
        relative_path: PathBuf::from(&span.path),
        expected_sha256,
        address: SpanAddress::Lines {
            start_line: span.start_line,
            end_line: span.end_line,
        },
    }
}

fn arm_c(span: &FrozenSpan, root: &Path) -> (bool, Option<String>, usize) {
    let budget = EvidenceBudget {
        max_bytes: MAX_PACK_BYTES,
        max_items: MAX_PACK_ITEMS,
    };
    let size = fs::metadata(root.join(&span.path))
        .map_or(0, |meta| usize::try_from(meta.len()).unwrap_or(0));
    let pack = assemble_spans(
        root,
        &[span_request(span, Some(span.sha256.clone()))],
        budget,
    )
    .expect("valid pack request");
    if pack.items.is_empty() {
        (
            true,
            pack.gaps
                .first()
                .map(|gap| format!("{:?}", gap.code).to_lowercase()),
            size,
        )
    } else {
        (false, None, size)
    }
}

fn arm_d(span: &FrozenSpan, root: &Path) -> (bool, Option<String>, usize) {
    let budget = EvidenceBudget {
        max_bytes: MAX_PACK_BYTES,
        max_items: MAX_PACK_ITEMS,
    };
    let size = fs::metadata(root.join(&span.path))
        .map_or(0, |meta| usize::try_from(meta.len()).unwrap_or(0));
    let pack = assemble_spans(root, &[span_request(span, None)], budget).expect("valid pack");
    if pack.items.is_empty() {
        return (
            true,
            pack.gaps
                .first()
                .map(|gap| format!("{:?}", gap.code).to_lowercase()),
            size,
        );
    }
    let gaps = revalidate_spans(root, &pack).expect("valid revalidation");
    (
        !gaps.is_empty(),
        gaps.first()
            .map(|gap| format!("{:?}", gap.code).to_lowercase()),
        size,
    )
}

#[allow(clippy::too_many_lines)]
fn main() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let corpus: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/corpus-manifest.json"))
            .expect("corpus manifest"),
    )
    .expect("valid corpus manifest");
    let corpus_root = PathBuf::from(corpus["source_root"].as_str().expect("source root"));
    let manifest_sha = sha256(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/corpus-manifest.json"))
            .expect("corpus manifest"),
    );

    let spans = read_frozen_spans(&repo, &corpus_root);
    let files = spans
        .iter()
        .map(|span| span.path.clone())
        .collect::<BTreeSet<_>>();

    let fixture_root = std::env::var("ZG_SURFACE_FIXTURE_ROOT").map_or_else(
        |_| {
            PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_owned()))
                .join(".cache/zvec-grep/r1-evidence-surface-20261115/fixture")
        },
        PathBuf::from,
    );
    if fixture_root.exists() {
        fs::remove_dir_all(&fixture_root).expect("clean fixture root");
    }

    let pss_before = pss_kib();
    let mut conditions = Vec::new();
    for (condition, description) in CONDITIONS {
        let root = fixture_root.join(condition);
        for relative in &files {
            let target = root.join(relative);
            fs::create_dir_all(target.parent().expect("target parent")).expect("fixture dir");
            fs::copy(corpus_root.join(relative), &target).expect("fixture copy");
        }
        mutate(&root, &files, condition, &spans);

        let mut arms = Vec::new();
        for arm in [Arm::A, Arm::B, Arm::C, Arm::D] {
            let started = Instant::now();
            let mut metrics = ArmMetrics::default();
            let mut seen_files = BTreeSet::new();
            for span in &spans {
                let (detected, gap, bytes) = match arm {
                    Arm::A => arm_a(span, &root),
                    Arm::B => arm_b(span, &root),
                    Arm::C => arm_c(span, &root),
                    Arm::D => arm_d(span, &root),
                };
                metrics.tested += 1;
                if detected {
                    metrics.detected += 1;
                } else {
                    metrics.not_detected += 1;
                }
                if let Some(gap) = gap {
                    *metrics.gap_codes.entry(gap).or_insert(0) += 1;
                }
                if seen_files.insert(span.path.clone()) {
                    metrics.bytes_read += bytes;
                }
            }
            metrics.elapsed_ms = started.elapsed().as_secs_f64() * 1000.0;
            arms.push((format!("{arm:?}").to_lowercase(), metrics));
        }
        conditions.push(ConditionReport {
            id: (*condition).to_owned(),
            description: (*description).to_owned(),
            arms,
        });
    }

    let mut per_case_packs = Vec::new();
    for case in CASES {
        let root = fixture_root.join("unchanged");
        let requests = spans
            .iter()
            .filter(|span| span.case == *case)
            .map(|span| span_request(span, Some(span.sha256.clone())))
            .collect::<Vec<_>>();
        let budget = EvidenceBudget {
            max_bytes: MAX_PACK_BYTES,
            max_items: MAX_PACK_ITEMS,
        };
        let started = Instant::now();
        let pack = assemble_spans(&root, &requests, budget).expect("valid pack");
        let assemble_ms = started.elapsed().as_secs_f64() * 1000.0;
        let started = Instant::now();
        let revalidate_gaps = revalidate_spans(&root, &pack).expect("valid revalidation");
        let revalidate_ms = started.elapsed().as_secs_f64() * 1000.0;
        let mut gap_codes = BTreeMap::new();
        for gap in &pack.gaps {
            *gap_codes
                .entry(format!("{:?}", gap.code).to_lowercase())
                .or_insert(0) += 1;
        }
        per_case_packs.push(CasePack {
            case: (*case).to_owned(),
            requested_spans: requests.len(),
            items: pack.items.len(),
            gaps: pack.gaps.len(),
            deduplicated: pack.deduplicated,
            gap_codes,
            content_bytes: pack.items.iter().map(|item| item.content.len()).sum(),
            serialized_bytes: serde_json::to_string(&pack).map_or(0, |text| text.len()),
            assemble_ms,
            revalidate_gaps: revalidate_gaps.len(),
            revalidate_ms,
        });
    }
    let per_case_totals = PerCaseTotals {
        requested_spans: per_case_packs.iter().map(|pack| pack.requested_spans).sum(),
        items: per_case_packs.iter().map(|pack| pack.items).sum(),
        gaps: per_case_packs.iter().map(|pack| pack.gaps).sum(),
        max_content_bytes: per_case_packs
            .iter()
            .map(|pack| pack.content_bytes)
            .max()
            .unwrap_or(0),
        assemble_ms_total: per_case_packs.iter().map(|pack| pack.assemble_ms).sum(),
        revalidate_ms_total: per_case_packs.iter().map(|pack| pack.revalidate_ms).sum(),
    };

    let receipt = Receipt {
        status: "R1_EVIDENCE_SURFACE_PAIRED",
        date: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or_else(|_| String::new(), |elapsed| elapsed.as_secs().to_string()),
        branch_head: command(&repo, &["git", "rev-parse", "HEAD"]),
        corpus_source_root: corpus_root.display().to_string(),
        corpus_manifest_sha256: manifest_sha,
        cases: CASES.iter().map(ToString::to_string).collect(),
        spans: spans.len(),
        spans_by_case: {
            let mut counts = BTreeMap::new();
            for span in &spans {
                *counts.entry(span.case.clone()).or_insert(0) += 1;
            }
            counts
        },
        files: files.len(),
        budget: BTreeMap::from([
            ("max_bytes".to_owned(), MAX_PACK_BYTES),
            ("max_items".to_owned(), MAX_PACK_ITEMS),
        ]),
        host: HostState {
            loadavg: fs::read_to_string("/proc/loadavg")
                .unwrap_or_default()
                .trim()
                .to_owned(),
            mem_available_kib: mem_available_kib(),
            pss_kib_before: pss_before,
            pss_kib_after: pss_kib(),
        },
        conditions,
        per_case_packs,
        per_case_totals,
    };
    let text = serde_json::to_string_pretty(&receipt).expect("serialize receipt");
    let out = repo.join("benchmarks/rag-impact/results/r1-evidence-surface-paired-20261115.json");
    fs::write(&out, &text).expect("write receipt");
    println!("{text}");
    eprintln!("receipt: {}", out.display());
}

fn command(repo: &Path, argv: &[&str]) -> String {
    std::process::Command::new(argv[0])
        .args(&argv[1..])
        .current_dir(repo)
        .output()
        .ok()
        .filter(|output| output.status.success())
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
        .unwrap_or_default()
}
