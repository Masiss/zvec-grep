//! Independent index-truth audit for the isolated R4 index.
//!
//! Declarations are re-derived from source bytes with a deliberately
//! independent regex extractor (NOT the engine's R2 parser), then read back
//! through the real API routes: exact symbol lookup, FTS, vector and hybrid.
//! Nothing here uses gold at runtime; gold spans are provided as a diagnostic
//! list from the frozen dev cases for the offline ledger.
//!
//! Usage: `cargo run -q -p zg-engine --example r4_index_truth -- <corpus-root>`

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use serde::Serialize;
use tokio::runtime::Runtime;
use zg_engine::ZvecGrep;
use zg_engine::api::context::ContextOptions;
use zg_engine::api::context::options::{ContextRoute, ContextRouteMode, RefreshPolicy};
use zg_engine::api::symbols::SymbolsOptions;

#[derive(Clone, Serialize)]
struct Decl {
    path: String,
    name: String,
    start_line: usize,
    end_line: usize,
    kind: &'static str,
}

#[derive(Serialize)]
struct SpanCheck {
    id: String,
    path: String,
    gold_start: usize,
    gold_end: usize,
    enclosing: Option<Decl>,
    scanned: bool,
    persisted: bool,
    persisted_range: Option<(usize, usize)>,
    fts_token: Option<String>,
    fts_hit: bool,
    vector_hit: bool,
    stage_fail: &'static str,
}

#[derive(Serialize)]
struct Receipt {
    kind: &'static str,
    named_read_back: Vec<serde_json::Value>,
    corpus_root: String,
    files_total: usize,
    files_indexed: Option<u64>,
    gold_checks: Vec<SpanCheck>,
    gold_summary: BTreeSet<String>,
    sample_checks: Vec<SpanCheck>,
    sample_summary: BTreeSet<String>,
    notes: Vec<&'static str>,
}

/// Independent regex-free declaration extractor: brace-balanced scan for
/// C# method/class declarations and SQL CREATE blocks.
fn extract_decls(text: &str, path: &str) -> Vec<Decl> {
    let lines: Vec<&str> = text.lines().collect();
    let mut decls = Vec::new();
    let is_sql = path.ends_with(".sql");
    for (index, line) in lines.iter().enumerate() {
        let line_no = index + 1;
        if is_sql {
            let lower = line.to_lowercase();
            if lower.contains("create procedure") || lower.contains("create view") {
                if let Some(name) = extract_sql_name(&lower) {
                    let end = find_end(&lines, index, "begin", "end");
                    decls.push(Decl {
                        path: path.to_owned(),
                        name,
                        start_line: line_no,
                        end_line: end,
                        kind: "sql",
                    });
                }
            }
        } else {
            let trimmed = line.trim_start();
            let looks_decl = trimmed.starts_with("public ")
                || trimmed.starts_with("private ")
                || trimmed.starts_with("protected ")
                || trimmed.starts_with("internal ");
            if !looks_decl || trimmed.contains('=') {
                continue;
            }
            if let Some(name) = extract_cs_name(trimmed) {
                if name.chars().next().is_some_and(char::is_uppercase) && name.len() > 2 {
                    let end = brace_end(text, line_no);
                    decls.push(Decl {
                        path: path.to_owned(),
                        name,
                        start_line: line_no,
                        end_line: end,
                        kind: "cs",
                    });
                }
            }
        }
    }
    decls
}

fn extract_cs_name(trimmed: &str) -> Option<String> {
    let before_paren = trimmed.split('(').next()?;
    let cleaned: String = before_paren
        .chars()
        .filter(|ch| ch.is_alphanumeric() || *ch == '_')
        .collect::<String>();
    // class/interface/struct: the declared name, not the inheritance list
    for keyword in ["class ", "interface ", "struct "] {
        if let Some(index) = cleaned.find(keyword) {
            let rest = &cleaned[index + keyword.len()..];
            let name: String = rest
                .chars()
                .take_while(|ch| ch.is_alphanumeric() || *ch == '_')
                .collect();
            return (!name.is_empty()).then_some(name);
        }
    }
    // method/property: the last word before the parameter list
    let mut name = String::new();
    for word in before_paren.split_whitespace() {
        let word: String = word
            .chars()
            .filter(|ch| ch.is_alphanumeric() || *ch == '_')
            .collect();
        if !word.is_empty() {
            name = word;
        }
    }
    (!name.is_empty()).then_some(name)
}

fn extract_sql_name(lower: &str) -> Option<String> {
    let after = lower
        .split("procedure")
        .nth(1)
        .or_else(|| lower.split("view").nth(1))?;
    after
        .split_whitespace()
        .find(|word| {
            word.chars()
                .all(|ch| ch.is_alphanumeric() || ch == '_' || ch == '.' || ch == '[' || ch == ']')
        })
        .map(|word| {
            word.chars()
                .filter(|ch: &char| ch.is_alphanumeric() || *ch == '_' || *ch == '.')
                .collect::<String>()
        })
        .and_then(|word| word.split('.').last().map(ToString::to_string))
}

fn find_end(lines: &[&str], from: usize, open: &str, close: &str) -> usize {
    let mut depth = 0usize;
    for (index, line) in lines.iter().enumerate().skip(from) {
        let lower = line.to_lowercase();
        if lower.contains(open) {
            depth += 1;
        }
        if lower.contains(close) {
            depth = depth.saturating_sub(1);
            if depth == 0 {
                return index + 1;
            }
        }
    }
    lines.len()
}

fn brace_end(text: &str, start_line: usize) -> usize {
    let mut depth = 0i64;
    let mut seen = false;
    for (index, line) in text.lines().enumerate() {
        if index + 1 < start_line {
            continue;
        }
        for ch in line.chars() {
            if ch == '{' {
                depth += 1;
                seen = true;
            } else if ch == '}' {
                depth -= 1;
                if seen && depth == 0 {
                    return index + 1;
                }
            }
        }
    }
    text.lines().count()
}

fn body_token(text: &str, decl: &Decl) -> Option<String> {
    let lines: Vec<&str> = text.lines().collect();
    let body = lines
        .get(decl.start_line.saturating_sub(1)..decl.end_line.min(lines.len()))
        .unwrap_or(&[]);
    for line in body {
        for word in line.split(|ch: char| !ch.is_alphanumeric() && ch != '_') {
            let word = word.trim_matches('_');
            if word.len() >= 6 && word.chars().next().is_some_and(char::is_uppercase) {
                return Some(word.to_owned());
            }
        }
    }
    None
}

struct Probe {
    runtime: Runtime,
    engine: ZvecGrep,
    root: PathBuf,
}

impl Probe {
    fn symbols(&mut self, name: &str) -> Vec<(String, usize, usize)> {
        let options = SymbolsOptions {
            root: Some(self.root.clone()),
            name: name.to_owned(),
            limit: Some(20),
            auto_update: false,
            refresh: Some(RefreshPolicy::Off),
            ..SymbolsOptions::default()
        };
        self.runtime
            .block_on(self.engine.symbols(options))
            .map(|result| {
                result
                    .items
                    .iter()
                    .filter_map(|item| {
                        Some((
                            item.relative_path.to_string_lossy().replace('\\', "/"),
                            item.range.start_line()?,
                            item.range.last_line()?,
                        ))
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    fn route(
        &mut self,
        mode: Option<ContextRouteMode>,
        query: &str,
        limit: usize,
    ) -> Vec<(String, usize, usize)> {
        let options = ContextOptions {
            root: Some(self.root.clone()),
            routes: mode
                .map(|mode| {
                    vec![ContextRoute {
                        mode,
                        query: query.to_owned(),
                    }]
                })
                .unwrap_or_default(),
            query: mode.is_none().then(|| query.to_owned()),
            limit: Some(limit),
            auto_update: false,
            refresh: Some(RefreshPolicy::Off),
            ..ContextOptions::default()
        };
        self.runtime
            .block_on(self.engine.context(options))
            .map(|result| {
                result
                    .items
                    .iter()
                    .filter_map(|item| {
                        Some((
                            item.relative_path.to_string_lossy().replace('\\', "/"),
                            item.range.start_line()?,
                            item.range.last_line()?,
                        ))
                    })
                    .collect()
            })
            .unwrap_or_default()
    }
}

fn check(
    probe: &mut Probe,
    decls_by_file: &std::collections::BTreeMap<String, Vec<Decl>>,
    id: &str,
    path: &str,
    gold_start: usize,
    gold_end: usize,
) -> SpanCheck {
    // innermost overlapping declaration: gold spans may start at attribute
    // lines one row above the method, so containment is too strict; the
    // declaration with the largest start line among all overlapping ones is
    // the method (or the class when the span IS the class).
    let enclosing = decls_by_file
        .get(path)
        .and_then(|decls| {
            decls
                .iter()
                .filter(|decl| decl.start_line <= gold_end && gold_start <= decl.end_line)
                .max_by_key(|decl| decl.start_line)
        })
        .cloned();
    let mut check = SpanCheck {
        id: id.to_owned(),
        path: path.to_owned(),
        gold_start,
        gold_end,
        enclosing: enclosing.clone(),
        scanned: true,
        persisted: false,
        persisted_range: None,
        fts_token: None,
        fts_hit: false,
        vector_hit: false,
        stage_fail: "NOT_EXTRACTED",
    };
    let Some(decl) = enclosing else {
        return check;
    };
    let text = fs::read_to_string(probe.root.join(path)).unwrap_or_default();
    check.stage_fail = "NOT_PERSISTED";
    let persisted = probe.symbols(&decl.name);
    if let Some(hit) = persisted
        .iter()
        .find(|(p, a, b)| p == path && *a <= gold_start && gold_end <= *b)
        .or_else(|| {
            persisted
                .iter()
                .find(|(p, a, b)| p == path && *a <= gold_end && gold_start <= *b)
        })
    {
        check.persisted = true;
        check.persisted_range = Some((hit.1, hit.2));
    }
    if !check.persisted {
        return check;
    }
    check.stage_fail = "NOT_SEARCHABLE";
    let token = body_token(&text, &decl);
    check.fts_token = token.clone();
    if let Some(token) = token {
        let fts = probe.route(Some(ContextRouteMode::Fts), &token, 50);
        check.fts_hit = fts
            .iter()
            .any(|(p, a, b)| p == path && *a <= gold_end && gold_start <= *b);
        if check.fts_hit {
            check.stage_fail = "OK_SEARCHABLE";
        }
    } else {
        check.stage_fail = "NO_BODY_TOKEN";
        return check;
    }
    // vector findability: the declaration name itself as a code-ish query
    let vector = probe.route(Some(ContextRouteMode::Vector), &decl.name, 50);
    check.vector_hit = vector
        .iter()
        .any(|(p, a, b)| p == path && *a <= gold_end && gold_start <= *b);
    if check.fts_hit || check.vector_hit {
        check.stage_fail = "OK_SEARCHABLE";
    }
    check
}

fn main() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    let root = std::env::args().nth(1).map_or_else(
        || PathBuf::from("/home/masis/.cache/zvec-grep/r4-discovery-20261115/corpus"),
        PathBuf::from,
    );

    // 1. independent extraction over every admitted source file
    let mut decls_by_file = std::collections::BTreeMap::new();
    let mut files_total = 0usize;
    let mut cs_files = 0usize;
    let mut sql_files = 0usize;
    for entry in walk(&root) {
        let rel = entry
            .strip_prefix(&root)
            .expect("rel")
            .to_string_lossy()
            .replace('\\', "/");
        if rel.starts_with(".zvec-grep") {
            continue;
        }
        files_total += 1;
        if rel.ends_with(".cs") {
            cs_files += 1;
        }
        if rel.ends_with(".sql") {
            sql_files += 1;
        }
        if rel.ends_with(".cs") || rel.ends_with(".sql") {
            if let Ok(text) = fs::read_to_string(&entry) {
                let decls = extract_decls(&text, &rel);
                if !decls.is_empty() {
                    decls_by_file.insert(rel, decls);
                }
            }
        }
    }

    // 2. gold ledger (diagnostic input, offline only)
    let dev: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(repo.join("benchmarks/rag-impact/cases/dev.json")).expect("dev"),
    )
    .expect("valid dev");
    let mut probe = Probe {
        runtime: Runtime::new().map_err(|error| error.to_string()).unwrap(),
        engine: ZvecGrep::new(),
        root: root.clone(),
    };
    let mut gold_checks = Vec::new();
    for case in dev["cases"].as_array().expect("cases") {
        let id = case["id"].as_str().unwrap_or_default().to_owned();
        let keep = id.len() == 3
            && id.starts_with('D')
            && id[1..]
                .parse::<u32>()
                .is_ok_and(|number| (1..=12).contains(&number));
        if !keep {
            continue;
        }
        for reference in case["source_refs"].as_array().into_iter().flatten() {
            let path = reference["path"]
                .as_str()
                .unwrap_or_default()
                .replace('\\', "/");
            for span in reference["spans"].as_array().into_iter().flatten() {
                gold_checks.push(check(
                    &mut probe,
                    &decls_by_file,
                    &id,
                    &path,
                    span["start_line"].as_u64().unwrap_or(0) as usize,
                    span["end_line"].as_u64().unwrap_or(0) as usize,
                ));
            }
        }
    }

    // 3. independent sample: first declaration of every 8th file, gold-free
    let mut sample_checks = Vec::new();
    let files: Vec<String> = decls_by_file.keys().cloned().collect();
    for path in files.iter().step_by(8) {
        let decl = decls_by_file[path].first().expect("nonempty").clone();
        sample_checks.push(check(
            &mut probe,
            &decls_by_file,
            "SAMPLE",
            &path,
            decl.start_line,
            decl.start_line + decl.end_line.saturating_sub(decl.start_line),
        ));
    }

    let manifest: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(root.join(".zvec-grep/manifest.json")).expect("manifest"),
    )
    .expect("valid manifest");
    let files_indexed = manifest
        .pointer("/index/stats/files")
        .and_then(|value| value.as_u64())
        .or_else(|| {
            manifest
                .pointer("/scan/files")
                .and_then(|value| value.as_u64())
        });

    // 4. authoritative-name read-back for the five spans whose independent
    // extractor mislabelled the declaration: the true names come from the
    // declaration lines themselves (verified by sed in the audit).
    let mut named = Vec::new();
    for (name, path, gold_start, gold_end) in [
        (
            "CoreDBContext",
            "DBContexts/HRIS/DBContext.Core/Entities/CoreDBContext.cs",
            41,
            55,
        ),
        (
            "CoreDBContext",
            "DBContexts/HRIS/DBContext.Core/Entities/CoreDBContext.cs",
            9,
            20,
        ),
        (
            "CoreService",
            "Services/HRIS/Service.Core/Executes/Base/CoreService.cs",
            15,
            22,
        ),
        ("EmployeeBaseViews", "DB/Views/EmployeeBaseViews.sql", 1, 10),
        (
            "sp_UpdateOrgTotalEmployee",
            "DB/Store/sp_UpdateOrgTotalEmployee.sql",
            1,
            95,
        ),
        (
            "sp_UpdateOrgTotalEmployee",
            "DB/Store/sp_UpdateOrgTotalEmployee.sql",
            58,
            95,
        ),
    ] {
        let hits = probe.symbols(name);
        let hit = hits
            .iter()
            .find(|(p, a, b)| p == path && *a <= gold_end && gold_start <= *b);
        named.push(serde_json::json!({
            "name": name, "path": path, "gold": [gold_start, gold_end],
            "persisted": hit.is_some(),
            "range": hit.map(|(_, a, b)| (a.to_owned(), b.to_owned())),
            "total_hits": hits.len(),
        }));
    }

    let summarize = |checks: &[SpanCheck]| -> BTreeSet<String> {
        checks
            .iter()
            .map(|check| check.stage_fail.to_owned())
            .collect()
    };
    println!(
        "named read-back: {}",
        serde_json::to_string(&named).expect("json")
    );
    let receipt = Receipt {
        named_read_back: named,
        kind: "r4_index_truth_audit",
        corpus_root: root.display().to_string(),
        files_total,
        files_indexed,
        gold_summary: summarize(&gold_checks),
        gold_checks,
        sample_summary: summarize(&sample_checks),
        sample_checks,
        notes: vec![
            "declarations re-derived with an independent regex/brace extractor, not the engine parser",
            "persisted = exact symbol lookup returns a range overlapping the gold span in the same file",
            "searchable = FTS over an in-body identifier token hits the span; vector uses the declaration name",
            "gold is a diagnostic input only; the sample audit is gold-free",
        ],
    };
    let out = repo.join("benchmarks/rag-impact/results/r4-index-truth-20261118.json");
    fs::write(&out, serde_json::to_string_pretty(&receipt).expect("json")).expect("write");
    println!(
        "files={} cs={} sql={} decls={} gold_fail_stages={:?} sample_fail_stages={:?}",
        files_total,
        cs_files,
        sql_files,
        decls_by_file.values().map(Vec::len).sum::<usize>(),
        receipt.gold_summary,
        receipt.sample_summary,
    );
    eprintln!("receipt: {}", out.display());
}

fn walk(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                let name = path
                    .file_name()
                    .and_then(std::ffi::OsStr::to_str)
                    .unwrap_or("");
                if name == ".git" || name == ".zvec-grep" {
                    continue;
                }
                stack.push(path);
            } else {
                out.push(path);
            }
        }
    }
    out
}
