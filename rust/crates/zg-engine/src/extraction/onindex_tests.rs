//! One-Index discovery experiments (test-only, always `#[ignore]`).
//!
//! Modes (env `ONINDEX_MODE`):
//! * `full`    — control: E1R declaration index over all files, anchor facts,
//!               closure, plus the full-fact refs-off baseline.
//! * `index`   — Experiment A: declaration names resolved through the existing
//!               search index, verified by parsing only the hit files.
//! * `lexical` — Experiment B: declaration files selected by one bounded
//!               lexical scan, then parsed and reduced to declarations.
//!
//! Environment:
//!   ONINDEX_MODE=full|index|lexical
//!   ONINDEX_SOURCE_ROOT=<corpus root>        (default: OUT/workspace)
//!   ONINDEX_WORKSPACE=<indexed workspace>    (default: source root)
//!   ONINDEX_CASES=<cases json>               (default: cases/dev.json + b0 seeds)
//!   ONINDEX_ANCHORS=<anchors json>           (default: seeds from b0-official)
//!
//! No product behaviour is exercised outside the existing extraction APIs and
//! the read-only context query API.

#![allow(clippy::too_many_lines)]

use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;
use std::time::Instant;

use crate::ZvecGrep;
use crate::api::context::ContextOptions;
use crate::api::context::options::{FileFormat, QueryFilter, RefreshPolicy};

use super::candidate::{CandidateItem, ClosureDiagnostics, Seed, expand_with_declarations};
use super::csharp_facts::{
    ProjectDeclarations, ProjectFacts, ProjectIdentity, build_declaration_index,
    parse_anchor_facts, parse_file,
};
use super::sql_facts::SqlCatalog;

const MAX_LOOKUPS: usize = 200;
const MAX_ROUNDS: usize = 4;
const MAX_INDEX_HITS: usize = 64;
const MAX_LEXICAL_SITES_PER_NAME: usize = 256;
const MAX_VERIFY_FILES: usize = 1024;

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

fn io_snapshot() -> BTreeMap<String, u64> {
    std::fs::read_to_string("/proc/self/io")
        .ok()
        .map(|text| {
            text.lines()
                .filter_map(|line| {
                    let (key, value) = line.split_once(':')?;
                    Some((key.trim().to_owned(), value.trim().parse().ok()?))
                })
                .collect()
        })
        .unwrap_or_default()
}

fn io_delta(before: &BTreeMap<String, u64>, after: &BTreeMap<String, u64>) -> serde_json::Value {
    let mut out = serde_json::Map::new();
    for key in [
        "rchar",
        "wchar",
        "syscr",
        "syscw",
        "read_bytes",
        "write_bytes",
        "cancelled_write_bytes",
    ] {
        let a = after.get(key).copied().unwrap_or(0);
        let b = before.get(key).copied().unwrap_or(0);
        out.insert(key.to_owned(), serde_json::json!(a.saturating_sub(b)));
    }
    serde_json::Value::Object(out)
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

fn ranges_overlap(a_start: usize, a_end: usize, b_start: usize, b_end: usize) -> bool {
    a_start <= b_end && b_start <= a_end
}

fn covered(items: &[CandidateItem], gold: &[(String, usize, usize)]) -> usize {
    gold.iter()
        .filter(|(path, start, end)| {
            items.iter().any(|item| {
                item.path == *path && ranges_overlap(item.start_line, item.end_line, *start, *end)
            })
        })
        .count()
}

fn rank(items: &[CandidateItem], path: &str, start: usize, end: usize) -> Option<usize> {
    items.iter().position(|item| {
        item.path == path && ranges_overlap(item.start_line, item.end_line, start, end)
    })
}

fn coverage(items: &[CandidateItem], gold: &[(String, usize, usize)]) -> serde_json::Value {
    let at = |budget: usize| covered(&items[..items.len().min(budget)], gold);
    serde_json::json!({
        "10": at(10), "50": at(50), "100": at(100), "250": at(250), "500": at(500),
    })
}

fn sort_dedup(items: &mut Vec<CandidateItem>) {
    items.sort_by(|left, right| {
        right
            .score
            .cmp(&left.score)
            .then_with(|| left.path.cmp(&right.path))
            .then_with(|| left.start_line.cmp(&right.start_line))
            .then_with(|| left.end_line.cmp(&right.end_line))
    });
    items.dedup_by(|left, right| {
        left.path == right.path
            && left.start_line == right.start_line
            && left.end_line == right.end_line
    });
}

fn hash_items(items: &[CandidateItem]) -> String {
    use std::hash::{Hash, Hasher};
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    for item in items {
        item.path.hash(&mut hasher);
        item.start_line.hash(&mut hasher);
        item.end_line.hash(&mut hasher);
        item.score.hash(&mut hasher);
        item.reason.hash(&mut hasher);
    }
    format!("{:016x}", hasher.finish())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Mode {
    Full,
    Index,
    Lexical,
}

impl Mode {
    fn from_env() -> Self {
        match std::env::var("ONINDEX_MODE").as_deref() {
            Ok("index") => Self::Index,
            Ok("lexical") => Self::Lexical,
            _ => Self::Full,
        }
    }
    const fn as_str(self) -> &'static str {
        match self {
            Self::Full => "full",
            Self::Index => "index",
            Self::Lexical => "lexical",
        }
    }
}

struct Corpus {
    source_root: PathBuf,
    workspace: PathBuf,
    /// Compile files in manifest order: (relative path, project id).
    compile: Vec<(String, String)>,
    project_csproj: BTreeMap<String, String>,
    catalog: SqlCatalog,
}

impl Corpus {
    fn load() -> Self {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let manifest: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(repo.join("benchmarks/rag-impact/corpus-manifest.json"))
                .expect("corpus manifest"),
        )
        .expect("json");
        let default_root = repo.parent().expect("repo parent").to_path_buf();
        let _ = &default_root;
        let source_root = std::env::var("ONINDEX_SOURCE_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                PathBuf::from(
                    "/home/masis/.local/share/zvec-grep/rag-impact/20261109-onindex/workspace",
                )
            });
        let workspace = std::env::var("ONINDEX_WORKSPACE")
            .map(PathBuf::from)
            .unwrap_or_else(|_| source_root.clone());
        let mut compile = Vec::new();
        for file in manifest["files"].as_array().expect("files") {
            if file["kind"].as_str() != Some("cs_compile") {
                continue;
            }
            let path = file["path"].as_str().expect("path").to_owned();
            let project = file["project"].as_str().expect("project").to_owned();
            compile.push((path, project));
        }
        let mut project_csproj = BTreeMap::new();
        for project in manifest["projects"].as_array().expect("projects") {
            project_csproj.insert(
                project["id"].as_str().expect("id").to_owned(),
                project["path"].as_str().expect("path").to_owned(),
            );
        }
        let sql_root = std::env::var("ONINDEX_SQL_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("/home/masis/itc/Ricons/HRM2.0"));
        let read_sql = |path: &str| {
            std::fs::read_to_string(sql_root.join(path))
                .unwrap_or_else(|_| panic!("sql source {path}"))
        };
        let catalog = SqlCatalog::from_sources(&[
            (
                "DB/Store/sp_UpdateOrgTotalEmployee.sql".to_owned(),
                read_sql("DB/Store/sp_UpdateOrgTotalEmployee.sql"),
                true,
                "selected_source_variant".to_owned(),
            ),
            (
                "DB/Store/sp_UpdateOrgTotalEmployee_old.sql".to_owned(),
                read_sql("DB/Store/sp_UpdateOrgTotalEmployee_old.sql"),
                false,
                "excluded_variant".to_owned(),
            ),
            (
                "DB/Store/sp_UpdateOrgTotalEmployee_old1.sql".to_owned(),
                read_sql("DB/Store/sp_UpdateOrgTotalEmployee_old1.sql"),
                false,
                "excluded_variant".to_owned(),
            ),
            (
                "DB/Views/EmployeeBaseViews.sql".to_owned(),
                read_sql("DB/Views/EmployeeBaseViews.sql"),
                true,
                "selected_source_variant".to_owned(),
            ),
        ]);
        Self {
            source_root,
            workspace,
            compile,
            project_csproj,
            catalog,
        }
    }

    fn read(&self, path: &str) -> String {
        std::fs::read_to_string(self.source_root.join(path))
            .unwrap_or_else(|_| panic!("source {path}"))
    }

    fn identity(&self, project: &str) -> ProjectIdentity {
        let csproj = self
            .project_csproj
            .get(project)
            .unwrap_or_else(|| panic!("project {project}"));
        ProjectIdentity::from_csproj(&self.source_root.join(csproj))
    }

    /// All files for one project in manifest order.
    fn project_files(&self, project: &str) -> Vec<(String, String)> {
        self.compile
            .iter()
            .filter(|(_, id)| id == project)
            .map(|(path, _)| (path.clone(), self.read(path)))
            .collect()
    }

    /// Anchor sources only, in manifest order (equivalent to passing every file
    /// to `parse_anchor_facts`, which skips non-anchor paths).
    fn anchor_files(&self, paths: &BTreeSet<String>) -> Vec<(String, String)> {
        self.compile
            .iter()
            .filter(|(path, _)| paths.contains(path))
            .map(|(path, _)| (path.clone(), self.read(path)))
            .collect()
    }
}

struct CaseSpec {
    id: String,
    query: String,
    seeds: Vec<Seed>,
    gold: Vec<(String, usize, usize)>,
}

fn load_cases() -> Vec<CaseSpec> {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    if let (Ok(cases_path), Ok(anchors_path)) = (
        std::env::var("ONINDEX_CASES"),
        std::env::var("ONINDEX_ANCHORS"),
    ) {
        let cases: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&cases_path).expect("cases"))
                .expect("json");
        let anchors: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&anchors_path).expect("anchors"))
                .expect("json");
        let by_id: BTreeMap<String, &serde_json::Value> = anchors["cases"]
            .as_array()
            .expect("anchor cases")
            .iter()
            .filter_map(|case| Some((case["id"].as_str()?.to_owned(), case)))
            .collect();
        let mut out = Vec::new();
        for case in cases["cases"].as_array().expect("cases") {
            let Some(id) = case["id"].as_str() else {
                continue;
            };
            let Some(anchor) = by_id.get(id) else {
                continue;
            };
            let mut seeds = Vec::new();
            for unit in anchor["seeds"].as_array().into_iter().flatten() {
                seeds.push(Seed {
                    path: unit["path"].as_str().unwrap_or_default().to_owned(),
                    start_line: unit["start_line"].as_u64().unwrap_or(0) as usize,
                    end_line: unit["end_line"].as_u64().unwrap_or(0) as usize,
                });
            }
            let gold = gold_of(case);
            out.push(CaseSpec {
                id: id.to_owned(),
                query: case["question"].as_str().unwrap_or_default().to_owned(),
                seeds,
                gold,
            });
        }
        return out;
    }

    let dev: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(repo.join("benchmarks/rag-impact/cases/dev.json")).expect("dev"),
    )
    .expect("json");
    let b0: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(repo.join("benchmarks/rag-impact/results/b0-official.json"))
            .expect("b0"),
    )
    .expect("json");
    let mut out = Vec::new();
    for case in dev["cases"].as_array().expect("cases") {
        let Some(id) = case["id"].as_str() else {
            continue;
        };
        if !id.starts_with('D')
            || ["D13", "D14", "D15", "D16"]
                .iter()
                .any(|prefix| id.starts_with(prefix))
        {
            continue;
        }
        let Some(b0_case) = b0["quality"]["dev_real_cases"]
            .as_array()
            .expect("b0")
            .iter()
            .find(|entry| entry["id"] == id)
        else {
            continue;
        };
        let mut seeds = BTreeSet::new();
        for call in b0_case["calls"].as_array().into_iter().flatten() {
            for unit in call["units"].as_array().into_iter().flatten() {
                let path = unit["path"].as_str().unwrap_or_default();
                let start = unit["start_line"].as_u64().unwrap_or(0) as usize;
                let end = unit["end_line"].as_u64().unwrap_or(0) as usize;
                if !path.is_empty() && start > 0 {
                    seeds.insert((path.to_owned(), start, end));
                }
            }
        }
        out.push(CaseSpec {
            id: id.to_owned(),
            query: case["question"].as_str().unwrap_or_default().to_owned(),
            seeds: seeds
                .into_iter()
                .map(|(path, start_line, end_line)| Seed {
                    path,
                    start_line,
                    end_line,
                })
                .collect(),
            gold: gold_of(case),
        });
    }
    out
}

fn gold_of(case: &serde_json::Value) -> Vec<(String, usize, usize)> {
    let mut gold = Vec::new();
    for reference in case["source_refs"].as_array().into_iter().flatten() {
        let path = reference["path"].as_str().unwrap_or_default().to_owned();
        for span in reference["spans"].as_array().into_iter().flatten() {
            if let (Some(start), Some(end)) =
                (span["start_line"].as_u64(), span["end_line"].as_u64())
            {
                gold.push((path.clone(), start as usize, end as usize));
            }
        }
    }
    gold
}

fn needed_names(project: &ProjectFacts) -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    for invocation in &project.invocations {
        if let Some(receiver) = &invocation.receiver_type {
            names.insert(receiver.clone());
        }
    }
    for save in &project.saves {
        if let Some(context) = &save.context_type {
            names.insert(context.clone());
        }
    }
    for file in &project.files {
        for assignment in &file.assignments {
            if let Some(receiver) = &assignment.receiver_type {
                names.insert(receiver.clone());
            }
        }
    }
    names
}

/// Bootstrap identifier harvest for the first lookup round: capitalized member
/// access receivers (`Type.member`) in the anchor sources. General heuristic,
/// no corpus names.
fn harvest_names(sources: &[(String, String)]) -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    for (_, source) in sources {
        let bytes = source.as_bytes();
        let mut index = 0;
        while index < bytes.len() {
            let byte = bytes[index];
            let starts_ident = byte.is_ascii_alphabetic() || byte == b'_';
            if !starts_ident {
                index += 1;
                continue;
            }
            let start = index;
            while index < bytes.len()
                && (bytes[index].is_ascii_alphanumeric() || bytes[index] == b'_')
            {
                index += 1;
            }
            let token = &source[start..index];
            let mut probe = index;
            while probe < bytes.len() && (bytes[probe] == b' ' || bytes[probe] == b'\t') {
                probe += 1;
            }
            if probe < bytes.len()
                && bytes[probe] == b'.'
                && token.len() > 2
                && token.as_bytes()[0].is_ascii_uppercase()
            {
                names.insert(token.to_owned());
            }
        }
    }
    names
}

struct LookupProvider<'a> {
    mode: Mode,
    corpus: &'a Corpus,
    web: Option<&'a ZvecGrep>,
    runtime: Option<&'a tokio::runtime::Runtime>,
    parsed: BTreeMap<String, ProjectDeclarations>,
    queried: BTreeSet<String>,
    lookups: Vec<serde_json::Value>,
    search_calls: usize,
    file_reads: usize,
    bytes_read: usize,
}

impl<'a> LookupProvider<'a> {
    fn new(
        mode: Mode,
        corpus: &'a Corpus,
        web: Option<&'a ZvecGrep>,
        runtime: Option<&'a tokio::runtime::Runtime>,
    ) -> Self {
        Self {
            mode,
            corpus,
            web,
            runtime,
            parsed: BTreeMap::new(),
            queried: BTreeSet::new(),
            lookups: Vec::new(),
            search_calls: 0,
            file_reads: 0,
            bytes_read: 0,
        }
    }

    fn file_declarations(&mut self, path: &str) -> &ProjectDeclarations {
        if !self.parsed.contains_key(path) {
            let source = self.corpus.read(path);
            self.file_reads += 1;
            self.bytes_read += source.len();
            let identity = self.corpus.identity(
                self.corpus
                    .compile
                    .iter()
                    .find(|(candidate, _)| candidate == path)
                    .map(|(_, project)| project.as_str())
                    .unwrap_or("P1"),
            );
            let facts = parse_file(&identity, path, &source);
            let mut declarations = ProjectDeclarations::default();
            declarations.types = facts.types;
            declarations.fields = facts.fields;
            declarations.db_sets = facts.db_sets;
            self.parsed.insert(path.to_owned(), declarations);
        }
        self.parsed.get(path).expect("parsed")
    }

    /// Index candidate files for a batch of declaration names.
    fn index_batch(&mut self, names: &[String]) -> Vec<String> {
        let (Some(engine), Some(runtime)) = (self.web, self.runtime) else {
            return Vec::new();
        };
        let mut files = Vec::new();
        for chunk in names.chunks(8) {
            let options = ContextOptions {
                queries: chunk.to_vec(),
                root: Some(self.corpus.workspace.clone()),
                prefer_symbol: true,
                limit: Some(MAX_INDEX_HITS),
                auto_update: false,
                refresh: Some(RefreshPolicy::Off),
                filter: QueryFilter {
                    included_formats: vec![FileFormat::CSharp],
                    ..QueryFilter::default()
                },
                ..ContextOptions::default()
            };
            self.search_calls += 1;
            let Ok(context) = runtime.block_on(engine.context(options)) else {
                continue;
            };
            for item in &context.items {
                let path = item.relative_path.to_string_lossy().to_string();
                if !files.contains(&path) {
                    files.push(path);
                }
            }
        }
        files.truncate(MAX_VERIFY_FILES);
        files
    }

    /// One bounded lexical scan of the compile set for the requested names.
    fn lexical_batch(&mut self, names: &BTreeSet<String>) -> Vec<String> {
        let mut sites: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for (path, _) in &self.corpus.compile {
            let source = self.corpus.read(path);
            self.file_reads += 1;
            self.bytes_read += source.len();
            let declared = declaration_names_in_source(&source);
            for name in names {
                if !declared.contains(name.as_str()) {
                    continue;
                }
                let entries = sites.entry(name.clone()).or_default();
                if entries.len() < MAX_LEXICAL_SITES_PER_NAME && !entries.contains(path) {
                    entries.push(path.clone());
                }
            }
            if sites.len() == names.len()
                && sites
                    .values()
                    .all(|entries| entries.len() >= MAX_LEXICAL_SITES_PER_NAME)
            {
                break;
            }
        }
        let mut files: Vec<String> = sites
            .into_values()
            .flatten()
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        files.truncate(MAX_VERIFY_FILES);
        files
    }

    /// Resolve a batch of names: select candidate files, verify by bounded
    /// parsing, merge verified declarations, and log every name.
    fn resolve_batch(&mut self, names: &[String], declarations: &mut ProjectDeclarations) {
        if names.is_empty() {
            return;
        }
        for name in names {
            self.queried.insert(name.clone());
        }
        let name_set: BTreeSet<String> = names.iter().cloned().collect();
        let (candidate_files, source_label): (Vec<String>, &'static str) =
            if self.mode == Mode::Index {
                (self.index_batch(names), "index")
            } else {
                (self.lexical_batch(&name_set), "lexical")
            };
        let mut resolved: BTreeSet<String> = BTreeSet::new();
        let mut verified_files: BTreeMap<String, usize> = BTreeMap::new();
        for path in &candidate_files {
            let facts = self.file_declarations(path).clone();
            let mut used = false;
            for name in names {
                let matched = declaration_subset(&facts, name);
                if matched.types.is_empty()
                    && matched.fields.is_empty()
                    && matched.db_sets.is_empty()
                {
                    continue;
                }
                resolved.insert(name.clone());
                verified_files.insert(name.clone(), *verified_files.get(name).unwrap_or(&0) + 1);
                declarations.merge(matched);
                used = true;
            }
            if used {
                self.file_reads += 0;
            }
        }
        for name in names {
            let (status, reason) = if resolved.contains(name) {
                ("resolved", None)
            } else if candidate_files.is_empty() {
                ("unresolved", Some("no_candidate_file"))
            } else {
                ("unresolved", Some("not_declared_in_candidates"))
            };
            self.lookups.push(serde_json::json!({
                "name": name,
                "source": source_label,
                "candidate_files": candidate_files.len(),
                "verified_files": verified_files.get(name).copied().unwrap_or(0),
                "status": status,
                "reason": reason,
            }));
        }
    }
}

/// Declaration names introduced by `class|struct|interface|record|enum` tokens.
fn declaration_names_in_source(source: &str) -> BTreeSet<&str> {
    let mut names = BTreeSet::new();
    let bytes = source.as_bytes();
    for keyword in ["class", "struct", "interface", "record", "enum"] {
        let mut from = 0;
        while let Some(offset) = source[from..].find(keyword) {
            let start = from + offset;
            from = start + keyword.len();
            if start > 0 {
                let previous = bytes[start - 1];
                if previous.is_ascii_alphanumeric() || previous == b'_' {
                    continue;
                }
            }
            let mut index = from;
            while index < bytes.len() && !bytes[index].is_ascii_whitespace() {
                index += 1;
            }
            while index < bytes.len() && bytes[index].is_ascii_whitespace() {
                index += 1;
            }
            let name_start = index;
            while index < bytes.len()
                && (bytes[index].is_ascii_alphanumeric() || bytes[index] == b'_')
            {
                index += 1;
            }
            if index > name_start {
                names.insert(&source[name_start..index]);
            }
        }
    }
    names
}

fn declaration_subset(facts: &ProjectDeclarations, name: &str) -> ProjectDeclarations {
    let mut out = ProjectDeclarations::default();
    for type_fact in &facts.types {
        if type_fact.name == name {
            out.types.push(type_fact.clone());
        }
    }
    for field in &facts.fields {
        if field.type_name == name {
            out.fields.push(field.clone());
        }
    }
    for db_set in &facts.db_sets {
        if db_set.context_type == name {
            out.db_sets.push(db_set.clone());
        }
    }
    out
}

struct CaseResult {
    row: serde_json::Value,
}

fn run_case(
    _mode: Mode,
    corpus: &Corpus,
    declarations: &ProjectDeclarations,
    case: &CaseSpec,
    provider: Option<&mut LookupProvider<'_>>,
    phases: &mut Vec<serde_json::Value>,
    start: Instant,
) -> CaseResult {
    let anchor_paths: BTreeSet<String> = case.seeds.iter().map(|seed| seed.path.clone()).collect();
    let identity = corpus.identity("P1");
    let io_before = io_snapshot();
    let case_start = Instant::now();
    let mut declarations_owned = ProjectDeclarations::default();
    let mut provider_lookups = Vec::new();
    let mut round_log: Vec<serde_json::Value> = Vec::new();
    let mut rounds = 0usize;
    let mut anchor_parse_ms = 0.0;
    let mut lookup_ms = 0.0;
    let mut project;

    let provider_mode = provider.is_some();
    if let Some(provider) = provider {
        provider.lookups.clear();
        provider.queried.clear();
        provider.parsed.clear();
        let anchors = corpus.anchor_files(&anchor_paths);
        let parse_start = Instant::now();
        project = parse_anchor_facts(&identity, &anchors, &anchor_paths, &declarations_owned);
        anchor_parse_ms += parse_start.elapsed().as_secs_f64() * 1000.0;
        let mut harvest_available = true;
        for _ in 0..MAX_ROUNDS {
            rounds += 1;
            let mut names = needed_names(&project);
            let new_names: Vec<String> = names
                .iter()
                .filter(|name| !provider.queried.contains(*name))
                .cloned()
                .collect();
            if new_names.is_empty() && harvest_available {
                // No typed receiver names yet: fall back to one bounded
                // identifier harvest from the anchor sources.
                harvest_available = false;
                names.extend(harvest_names(&anchors));
            }
            let budget = MAX_LOOKUPS.saturating_sub(provider.queried.len());
            let new_names: Vec<String> = names
                .into_iter()
                .filter(|name| !provider.queried.contains(name))
                .take(budget)
                .collect();
            if new_names.is_empty() {
                break;
            }
            let lookup_start = Instant::now();
            provider.resolve_batch(&new_names, &mut declarations_owned);
            let round_ms = lookup_start.elapsed().as_secs_f64() * 1000.0;
            lookup_ms += round_ms;
            round_log.push(serde_json::json!({
                "round": rounds,
                "names": new_names.len(),
                "declaration_types": declarations_owned.types.len(),
                "declaration_db_sets": declarations_owned.db_sets.len(),
                "lookup_ms": round_ms,
            }));
            let parse_start = Instant::now();
            project = parse_anchor_facts(&identity, &anchors, &anchor_paths, &declarations_owned);
            anchor_parse_ms += parse_start.elapsed().as_secs_f64() * 1000.0;
        }
        provider_lookups = provider.lookups.clone();
    } else {
        let anchors = corpus.anchor_files(&anchor_paths);
        let parse_start = Instant::now();
        project = parse_anchor_facts(&identity, &anchors, &anchor_paths, declarations);
        anchor_parse_ms += parse_start.elapsed().as_secs_f64() * 1000.0;
    }

    let active: &ProjectDeclarations = if provider_mode {
        &declarations_owned
    } else {
        declarations
    };
    let mut diagnostics = ClosureDiagnostics::default();
    let mut items = expand_with_declarations(
        &project,
        active,
        &corpus.catalog,
        &case.seeds,
        &case.query,
        500,
        true,
        &mut diagnostics,
    );
    sort_dedup(&mut items);

    let case_io = io_delta(&io_before, &io_snapshot());
    let after = sample("after_case", start);
    phases.push(after);
    let row = serde_json::json!({
        "id": case.id,
        "round_log": round_log,
        "gold": case.gold.len(),
        "coverage": coverage(&items, &case.gold),
        "candidates": items.len(),
        "closure_items": diagnostics.closure_items,
        "diagnostics": diagnostics,
        "ranks": case.gold.iter().map(|(p,s,e)| rank(&items,p,*s,*e)).collect::<Vec<_>>(),
        "closure_hash": hash_items(&items),
        "anchor_files": anchor_paths.len(),
        "rounds": rounds,
        "anchor_parse_ms": anchor_parse_ms,
        "lookup_ms": lookup_ms,
        "case_ms": case_start.elapsed().as_secs_f64() * 1000.0,
        "lookups": provider_lookups,
        "io_case": case_io,
    });
    if std::env::var("ONINDEX_DUMP_CASE").is_ok_and(|dump| dump == case.id) {
        let dump_items: Vec<serde_json::Value> = items
            .iter()
            .filter(|item| item.reason.starts_with("resolved|"))
            .take(200)
            .map(|item| {
                serde_json::json!({
                    "path": item.path,
                    "start": item.start_line,
                    "end": item.end_line,
                    "score": item.score,
                    "reason": item.reason,
                })
            })
            .collect();
        eprintln!(
            "{}",
            serde_json::to_string_pretty(&serde_json::json!({
                "dump_case": case.id,
                "items": dump_items,
            }))
            .expect("json")
        );
    }
    CaseResult { row }
}

/// Test entry point. Run one mode per process.
#[test]
#[ignore = "manual experiment: cargo test --lib extraction::onindex_tests::onindex_code_discovery -- --ignored --exact"]
fn onindex_code_discovery() {
    let start = Instant::now();
    let mode = Mode::from_env();
    let mut phases = vec![sample("process_start", start)];
    let corpus = Corpus::load();
    phases.push(sample("corpus_loaded", start));
    let cases = load_cases();
    phases.push(sample("cases_loaded", start));

    let mut declarations = ProjectDeclarations::default();
    if mode == Mode::Full {
        let p3 = build_declaration_index(&corpus.identity("P3"), &corpus.project_files("P3"));
        declarations.merge(p3);
        let p2 = build_declaration_index(&corpus.identity("P2"), &corpus.project_files("P2"));
        declarations.merge(p2);
        let p1 = build_declaration_index(&corpus.identity("P1"), &corpus.project_files("P1"));
        declarations.merge(p1);
        phases.push(sample("declarations_built", start));
    }

    let engine = ZvecGrep::new();
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("runtime");
    if mode == Mode::Index {
        // Warm the index once: the first query pays index and model residency.
        let warm = ContextOptions {
            queries: vec!["warmup".to_owned()],
            root: Some(corpus.workspace.clone()),
            prefer_symbol: true,
            limit: Some(1),
            auto_update: false,
            refresh: Some(RefreshPolicy::Off),
            filter: QueryFilter {
                included_formats: vec![FileFormat::CSharp],
                ..QueryFilter::default()
            },
            ..ContextOptions::default()
        };
        let _ = runtime.block_on(engine.context(warm));
        phases.push(sample("index_warm", start));
    }

    let mut provider = LookupProvider::new(
        mode,
        &corpus,
        (mode == Mode::Index).then_some(&engine),
        (mode == Mode::Index).then_some(&runtime),
    );
    let mut rows = Vec::new();
    let mut totals = BTreeMap::new();
    for case in &cases {
        let provider_ref = (mode != Mode::Full).then_some(&mut provider);
        let result = run_case(
            mode,
            &corpus,
            &declarations,
            case,
            provider_ref,
            &mut phases,
            start,
        );
        for budget in ["10", "50", "100", "250", "500"] {
            let value = result.row["coverage"][budget].as_u64().unwrap_or(0);
            *totals.entry(budget.to_owned()).or_insert(0u64) += value;
        }
        rows.push(result.row);
    }

    if mode == Mode::Full {
        // Full-fact refs-off baseline (E1 definition), reported once.
    }

    let end = sample("end", start);
    let lookup_summary = {
        let mut resolved = 0usize;
        let mut unresolved = BTreeMap::<String, usize>::new();
        for row in &rows {
            for lookup in row["lookups"].as_array().into_iter().flatten() {
                if lookup["status"] == "resolved" {
                    resolved += 1;
                } else {
                    let reason = lookup["reason"].as_str().unwrap_or("unknown").to_owned();
                    *unresolved.entry(reason).or_insert(0) += 1;
                }
            }
        }
        serde_json::json!({"resolved": resolved, "unresolved": unresolved})
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "ONINDEX_CODE_DISCOVERY",
            "mode": mode.as_str(),
            "source_root": corpus.source_root,
            "workspace": corpus.workspace,
            "case_count": cases.len(),
            "totals": totals,
            "search_calls": provider.search_calls,
            "file_reads": provider.file_reads,
            "bytes_read": provider.bytes_read,
            "lookup_summary": lookup_summary,
            "phases": phases,
            "rows": rows,
            "end": end,
            "rss_hwm_kib": status_kib("VmHWM:"),
            "pss_end_kib": pss_kib(),
        }))
        .expect("json")
    );
}

// ---------------------------------------------------------------------------
// H7 control qualification (release-caller). Measurement only.
// ---------------------------------------------------------------------------

/// Process CPU time in milliseconds (user + system).
fn cpu_ms() -> u64 {
    let Ok(text) = std::fs::read_to_string("/proc/self/stat") else {
        return 0;
    };
    let Some(close) = text.rfind(')') else {
        return 0;
    };
    let fields: Vec<&str> = text[close + 2..].split_whitespace().collect();
    let utime = fields
        .get(11)
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(0);
    let stime = fields
        .get(12)
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(0);
    let ticks = utime + stime;
    // USER_HZ is 100 on Linux; convert to milliseconds.
    ticks * 10
}

fn sample_cpu(label: &str, start: Instant, cpu_start: u64) -> serde_json::Value {
    let mut value = sample(label, start);
    value["cpu_ms"] = serde_json::json!(cpu_ms().saturating_sub(cpu_start));
    value
}

/// A closure reason must carry status, anchor expression, declaration, target
/// and rule fields (`anchor`/`expr` themselves contain one `|`).
fn closure_reason_ok(reason: &str) -> bool {
    let parts: Vec<&str> = reason.split('|').collect();
    parts.len() >= 6
        && parts[0] == "resolved"
        && parts[1].starts_with("anchor=")
        && parts[2].starts_with("expr=")
        && parts[3].starts_with("declaration=")
        && parts[4].starts_with("target=")
        && parts[5].starts_with("rule=")
}

fn provenance_of(items: &[CandidateItem]) -> serde_json::Value {
    let mut closure = 0usize;
    let mut closure_ok = 0usize;
    let mut bad_ranges = 0usize;
    let mut empty_paths = 0usize;
    for item in items {
        if item.path.is_empty() {
            empty_paths += 1;
        }
        if item.start_line == 0 || item.start_line > item.end_line {
            bad_ranges += 1;
        }
        if item.reason.starts_with("resolved|") {
            closure += 1;
            if closure_reason_ok(&item.reason) {
                closure_ok += 1;
            }
        }
    }
    serde_json::json!({
        "closure_items": closure,
        "closure_reasons_ok": closure_ok,
        "empty_paths": empty_paths,
        "bad_ranges": bad_ranges,
        "ok": empty_paths == 0 && bad_ranges == 0 && closure == closure_ok,
    })
}

fn delivery(items: &[CandidateItem]) -> (usize, f64) {
    let payload: Vec<serde_json::Value> = items
        .iter()
        .map(|item| {
            serde_json::json!({
                "path": item.path,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "score": item.score,
                "reason": item.reason,
            })
        })
        .collect();
    let started = Instant::now();
    let encoded = serde_json::to_vec(&payload).expect("deliver");
    (encoded.len(), started.elapsed().as_secs_f64() * 1000.0)
}

struct ControlRow {
    row: serde_json::Value,
    pass: usize,
}

fn control_rows_json(rows: &[ControlRow]) -> Vec<serde_json::Value> {
    rows.iter().map(|row| row.row.clone()).collect()
}

fn run_control_case(
    corpus: &Corpus,
    declarations: &ProjectDeclarations,
    case: &CaseSpec,
    pass: usize,
    start: Instant,
) -> ControlRow {
    let anchor_paths: BTreeSet<String> = case.seeds.iter().map(|seed| seed.path.clone()).collect();
    let identity = corpus.identity("P1");
    let case_start = Instant::now();
    let cpu_start = cpu_ms();
    let io_before = io_snapshot();

    let read_start = Instant::now();
    let anchors = corpus.anchor_files(&anchor_paths);
    let anchor_read_ms = read_start.elapsed().as_secs_f64() * 1000.0;
    let bytes = anchors
        .iter()
        .map(|(_, source)| source.len())
        .sum::<usize>();

    let parse_start = Instant::now();
    let project = parse_anchor_facts(&identity, &anchors, &anchor_paths, declarations);
    let anchor_parse_ms = parse_start.elapsed().as_secs_f64() * 1000.0;

    let closure_start = Instant::now();
    let mut diagnostics = ClosureDiagnostics::default();
    let mut items = expand_with_declarations(
        &project,
        declarations,
        &corpus.catalog,
        &case.seeds,
        &case.query,
        500,
        true,
        &mut diagnostics,
    );
    sort_dedup(&mut items);
    let closure_ms = closure_start.elapsed().as_secs_f64() * 1000.0;
    let provenance = provenance_of(&items);
    let (delivery_bytes, delivery_ms) = delivery(&items);
    let io_case = io_delta(&io_before, &io_snapshot());
    let case_ms = case_start.elapsed().as_secs_f64() * 1000.0;
    let cpu_case = cpu_ms().saturating_sub(cpu_start);
    let after = sample("after_case", start);
    let _ = after;
    let row = serde_json::json!({
        "id": case.id,
        "pass": pass,
        "gold": case.gold.len(),
        "anchor_files": anchor_paths.len(),
        "coverage": coverage(&items, &case.gold),
        "candidates": items.len(),
        "closure_items": diagnostics.closure_items,
        "diagnostics": diagnostics,
        "ranks": case.gold.iter().map(|(p,s,e)| rank(&items,p,*s,*e)).collect::<Vec<_>>(),
        "closure_hash": hash_items(&items),
        "provenance": provenance,
        "phases": {
            "anchor_read_ms": anchor_read_ms,
            "anchor_parse_ms": anchor_parse_ms,
            "closure_ms": closure_ms,
            "delivery_ms": delivery_ms,
            "case_ms": case_ms,
        },
        "cpu_ms": cpu_case,
        "io_case": io_case,
        "anchor_bytes": bytes,
        "delivery_bytes": delivery_bytes,
        "rss_kib": status_kib("VmRSS:"),
        "rss_hwm_kib": status_kib("VmHWM:"),
        "pss_kib": pss_kib(),
    });
    ControlRow { row, pass }
}

fn percentile(values: &mut [f64], quantile: f64) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.sort_by(f64::total_cmp);
    let index = ((values.len() - 1) as f64 * quantile).round() as usize;
    values[index]
}

/// Control qualification: two full passes over one workload (env selects the
/// workload files), per-phase cost, provenance and resource decomposition.
#[test]
#[ignore = "manual: cargo test --release --lib extraction::onindex_tests::onindex_control_qualification -- --ignored --exact --nocapture"]
fn onindex_control_qualification() {
    let start = Instant::now();
    let cpu_start = cpu_ms();
    let mut phases = vec![sample_cpu("process_start", start, cpu_start)];
    let corpus = Corpus::load();
    phases.push(sample_cpu("corpus_loaded", start, cpu_start));
    let cases = load_cases();
    phases.push(sample_cpu("cases_loaded", start, cpu_start));

    let mut declarations = ProjectDeclarations::default();
    for project in ["P3", "P2", "P1"] {
        let project_start = Instant::now();
        let project_cpu = cpu_ms();
        let io_before = io_snapshot();
        let files = corpus.project_files(project);
        let source_bytes: usize = files.iter().map(|(_, source)| source.len()).sum();
        let built = build_declaration_index(&corpus.identity(project), &files);
        let declaration_types = built.types.len();
        let declaration_fields = built.fields.len();
        let declaration_db_sets = built.db_sets.len();
        declarations.merge(built);
        let mut value = sample_cpu(
            &format!("declarations_{project}"),
            project_start,
            project_cpu,
        );
        value["files"] = serde_json::json!(files.len());
        value["source_bytes"] = serde_json::json!(source_bytes);
        value["types"] = serde_json::json!(declaration_types);
        value["fields"] = serde_json::json!(declaration_fields);
        value["db_sets"] = serde_json::json!(declaration_db_sets);
        value["io"] = io_delta(&io_before, &io_snapshot());
        phases.push(value);
    }
    let cold_end = sample_cpu("declarations_built", start, cpu_start);

    let passes: usize = std::env::var("ONINDEX_PASSES")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(1)
        .clamp(1, 4);
    let mut rows = Vec::new();
    let mut totals = BTreeMap::new();
    for pass in 0..passes {
        for (index, case) in cases.iter().enumerate() {
            let result = run_control_case(&corpus, &declarations, case, pass, start);
            if pass + 1 == passes {
                for budget in ["10", "50", "100", "250", "500"] {
                    let value = result.row["coverage"][budget].as_u64().unwrap_or(0);
                    *totals.entry(budget.to_owned()).or_insert(0u64) += value;
                }
            }
            if pass == 0 {
                let mut phase = sample_cpu(
                    if index == 0 {
                        "after_cold_case"
                    } else {
                        "after_cold_pass"
                    },
                    start,
                    cpu_start,
                );
                phase["case_id"] = serde_json::json!(case.id);
                phases.push(phase);
            }
            rows.push(result);
        }
    }
    let end = sample_cpu("end", start, cpu_start);

    // Warm (second pass) per-case latencies and CPU.
    let mut case_ms: Vec<f64> = rows
        .iter()
        .filter(|row| row.pass + 1 == passes)
        .map(|row| row.row["phases"]["case_ms"].as_f64().unwrap_or(0.0))
        .collect();
    let p50 = percentile(&mut case_ms.clone(), 0.5);
    let p95 = percentile(&mut case_ms, 0.95);
    let mut per_phase = BTreeMap::<String, Vec<f64>>::new();
    for row in rows.iter().filter(|row| row.pass + 1 == passes) {
        for (key, value) in row.row["phases"].as_object().into_iter().flatten() {
            per_phase
                .entry(key.clone())
                .or_default()
                .push(value.as_f64().unwrap_or(0.0));
        }
    }
    let phase_stats: BTreeMap<String, serde_json::Value> = per_phase
        .into_iter()
        .map(|(key, mut values)| {
            let p50 = percentile(&mut values.clone(), 0.5);
            let p95 = percentile(&mut values, 0.95);
            (key, serde_json::json!({"p50_ms": p50, "p95_ms": p95}))
        })
        .collect();
    let hashes_pass0: Vec<&str> = rows
        .iter()
        .filter(|row| row.pass == 0)
        .filter_map(|row| row.row["closure_hash"].as_str())
        .collect();
    let hashes_pass1: Vec<&str> = rows
        .iter()
        .filter(|row| row.pass + 1 == passes)
        .filter_map(|row| row.row["closure_hash"].as_str())
        .collect();
    let micro = totals.get("500").copied().unwrap_or(0);
    let gold_total: u64 = cases.iter().map(|case| case.gold.len() as u64).sum();
    let macro_recall = rows
        .iter()
        .filter(|row| row.pass + 1 == passes)
        .map(|row| {
            let covered = row.row["coverage"]["500"].as_f64().unwrap_or(0.0);
            let gold = row.row["gold"].as_f64().unwrap_or(1.0);
            covered / gold
        })
        .sum::<f64>()
        / cases.len() as f64;
    let provenance_ok = rows
        .iter()
        .filter(|row| row.pass + 1 == passes)
        .all(|row| row.row["provenance"]["ok"].as_bool().unwrap_or(false));
    let diagnostics: BTreeMap<String, u64> = {
        let mut merged = BTreeMap::new();
        for row in rows.iter().filter(|row| row.pass + 1 == passes) {
            if let Some(unresolved) = row.row["diagnostics"]["unresolved"].as_object() {
                for (reason, value) in unresolved {
                    *merged.entry(reason.clone()).or_insert(0) += value.as_u64().unwrap_or(0);
                }
            }
        }
        merged
    };

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "ONINDEX_CONTROL_QUALIFICATION",
            "workload": std::env::var("ONINDEX_WORKLOAD").unwrap_or_else(|_| "dev".to_owned()),
            "case_count": cases.len(),
            "totals": totals,
            "micro_recall": micro,
            "gold_total": gold_total,
            "macro_recall": macro_recall,
            "per_case_ms": {"p50": p50, "p95": p95},
            "phase_stats": phase_stats,
            "provenance_ok": provenance_ok,
            "diagnostics": diagnostics,
            "determinism": {
                "passes": passes,
                "identical": passes < 2 || hashes_pass0 == hashes_pass1,
            },
            "phases": phases,
            "cold_declarations": cold_end,
            "rows": control_rows_json(&rows),
            "end": end,
            "rss_hwm_kib": status_kib("VmHWM:"),
            "pss_end_kib": pss_kib(),
            "cpu_ms_total": cpu_ms().saturating_sub(cpu_start),
        }))
        .expect("json")
    );
}

/// Full-fact refs-off parity (E1 definition) in its own process.
#[test]
#[ignore = "manual: cargo test --release --lib extraction::onindex_tests::onindex_control_refs_off -- --ignored --exact --nocapture"]
fn onindex_control_refs_off() {
    use super::candidate::expand_with_closure;
    use super::csharp_facts::{parse_project, parse_project_with_references};

    let start = Instant::now();
    let cpu_start = cpu_ms();
    let corpus = Corpus::load();
    let cases = load_cases();
    let p3 = parse_project(&corpus.identity("P3"), &corpus.project_files("P3"));
    let p2 = parse_project_with_references(
        &corpus.identity("P2"),
        &corpus.project_files("P2"),
        std::slice::from_ref(&p3),
    );
    let p1 = parse_project_with_references(
        &corpus.identity("P1"),
        &corpus.project_files("P1"),
        &[p2.clone(), p3.clone()],
    );
    let mut merged = p1;
    for project in [&p2, &p3] {
        merged.files.extend(project.files.clone());
        merged.types.extend(project.types.clone());
        merged.methods.extend(project.methods.clone());
        merged.db_sets.extend(project.db_sets.clone());
        merged.fields.extend(project.fields.clone());
        merged.assignments.extend(project.assignments.clone());
        merged.invocations.extend(project.invocations.clone());
        merged.saves.extend(project.saves.clone());
    }
    let mut rows = Vec::new();
    let mut totals = BTreeMap::new();
    for case in &cases {
        let mut items = expand_with_closure(
            &merged,
            &corpus.catalog,
            &case.seeds,
            &case.query,
            500,
            false,
        );
        sort_dedup(&mut items);
        for budget in ["10", "50", "100", "250", "500"] {
            let value = items[..items.len().min(budget.parse().unwrap_or(500))]
                .iter()
                .filter(|item| {
                    case.gold.iter().any(|(path, start, end)| {
                        item.path == *path
                            && ranges_overlap(item.start_line, item.end_line, *start, *end)
                    })
                })
                .count();
            let _ = value;
        }
        let row = serde_json::json!({
            "id": case.id,
            "gold": case.gold.len(),
            "coverage": coverage(&items, &case.gold),
            "candidates": items.len(),
            "closure_hash": hash_items(&items),
        });
        for budget in ["10", "50", "100", "250", "500"] {
            let value = row["coverage"][budget].as_u64().unwrap_or(0);
            *totals.entry(budget.to_owned()).or_insert(0u64) += value;
        }
        rows.push(row);
    }
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "ONINDEX_CONTROL_REFS_OFF",
            "workload": std::env::var("ONINDEX_WORKLOAD").unwrap_or_else(|_| "dev".to_owned()),
            "case_count": cases.len(),
            "totals": totals,
            "rows": rows,
            "rss_hwm_kib": status_kib("VmHWM:"),
            "pss_end_kib": pss_kib(),
            "cpu_ms_total": cpu_ms().saturating_sub(cpu_start),
            "elapsed_ms": start.elapsed().as_secs_f64() * 1000.0,
        }))
        .expect("json")
    );
}

/// Worker-pool concurrency: K threads share the read-only declaration index.
#[test]
#[ignore = "manual: ONINDEX_CONCURRENCY=4 cargo test --release --lib extraction::onindex_tests::onindex_control_concurrency -- --ignored --exact --nocapture"]
fn onindex_control_concurrency() {
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};

    let start = Instant::now();
    let cpu_start = cpu_ms();
    let concurrency: usize = std::env::var("ONINDEX_CONCURRENCY")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(4)
        .max(1);
    let corpus = Arc::new(Corpus::load());
    let cases = Arc::new(load_cases());
    let mut declarations = ProjectDeclarations::default();
    for project in ["P3", "P2", "P1"] {
        declarations.merge(build_declaration_index(
            &corpus.identity(project),
            &corpus.project_files(project),
        ));
    }
    let declarations = Arc::new(declarations);
    let corpus_start = sample_cpu("worker_ready", start, cpu_start);

    let wall_cpu_start = cpu_ms();
    let cursor = Arc::new(AtomicUsize::new(0));
    let results = Arc::new(std::sync::Mutex::new(
        Vec::<(usize, serde_json::Value)>::new(),
    ));
    let wall = Instant::now();
    let mut handles = Vec::new();
    for worker in 0..concurrency {
        let corpus = Arc::clone(&corpus);
        let cases = Arc::clone(&cases);
        let declarations = Arc::clone(&declarations);
        let cursor = Arc::clone(&cursor);
        let results = Arc::clone(&results);
        handles.push(std::thread::spawn(move || {
            loop {
                let index = cursor.fetch_add(1, Ordering::SeqCst);
                let Some(case) = cases.get(index) else {
                    break;
                };
                let case_start = Instant::now();
                let cpu_start = cpu_ms();
                let anchors =
                    corpus.anchor_files(&case.seeds.iter().map(|seed| seed.path.clone()).collect());
                let anchor_paths: BTreeSet<String> =
                    case.seeds.iter().map(|seed| seed.path.clone()).collect();
                let project = parse_anchor_facts(
                    &corpus.identity("P1"),
                    &anchors,
                    &anchor_paths,
                    &declarations,
                );
                let mut diagnostics = ClosureDiagnostics::default();
                let mut items = expand_with_declarations(
                    &project,
                    &declarations,
                    &corpus.catalog,
                    &case.seeds,
                    &case.query,
                    500,
                    true,
                    &mut diagnostics,
                );
                sort_dedup(&mut items);
                let (delivery_bytes, _) = delivery(&items);
                let row = serde_json::json!({
                    "id": case.id,
                    "worker": worker,
                    "case_ms": case_start.elapsed().as_secs_f64() * 1000.0,
                    "cpu_ms": cpu_ms().saturating_sub(cpu_start),
                    "closure_hash": hash_items(&items),
                    "candidates": items.len(),
                    "delivery_bytes": delivery_bytes,
                });
                results.lock().expect("results").push((index, row));
            }
        }));
    }
    for handle in handles {
        handle.join().expect("worker");
    }
    let wall_ms = wall.elapsed().as_secs_f64() * 1000.0;
    let worker_cpu_ms = cpu_ms().saturating_sub(wall_cpu_start);
    let mut rows: Vec<(usize, serde_json::Value)> = results.lock().expect("results").clone();
    rows.sort_by_key(|(index, _)| *index);
    let mut case_ms: Vec<f64> = rows
        .iter()
        .map(|(_, row)| row["case_ms"].as_f64().unwrap_or(0.0))
        .collect();
    let p50 = percentile(&mut case_ms.clone(), 0.5);
    let p95 = percentile(&mut case_ms, 0.95);
    let hashes: BTreeMap<String, String> = rows
        .iter()
        .map(|(_, row)| {
            (
                row["id"].as_str().unwrap_or_default().to_owned(),
                row["closure_hash"].as_str().unwrap_or_default().to_owned(),
            )
        })
        .collect();
    let baseline_mismatch = std::env::var("ONINDEX_BASELINE_HASHES")
        .ok()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str::<BTreeMap<String, String>>(&text).ok())
        .map(|baseline| {
            baseline
                .iter()
                .filter(|(id, hash)| hashes.get(*id) != Some(*hash))
                .map(|(id, _)| id.clone())
                .collect::<Vec<_>>()
        });
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "ONINDEX_CONTROL_CONCURRENCY",
            "workload": std::env::var("ONINDEX_WORKLOAD").unwrap_or_else(|_| "dev".to_owned()),
            "concurrency": concurrency,
            "case_count": rows.len(),
            "wall_ms": wall_ms,
            "cases_per_second": rows.len() as f64 / (wall_ms / 1000.0),
            "case_ms": {"p50": p50, "p95": p95},
            "worker_ready": corpus_start,
            "cpu_ms_total": cpu_ms().saturating_sub(cpu_start),
            "worker_cpu_ms": worker_cpu_ms,
            "cpu_wall_ratio": worker_cpu_ms as f64 / wall_ms.max(1.0),
            "rss_hwm_kib": status_kib("VmHWM:"),
            "pss_end_kib": pss_kib(),
            "rows": rows.into_iter().map(|(_, row)| row).collect::<Vec<_>>(),
            "baseline_mismatch": baseline_mismatch,
        }))
        .expect("json")
    );
}

/// Daemon/index footprint: open the frozen index read-only, warm it, idle.
#[test]
#[ignore = "manual: cargo test --release --lib extraction::onindex_tests::onindex_index_footprint -- --ignored --exact --nocapture"]
fn onindex_index_footprint() {
    let start = Instant::now();
    let cpu_start = cpu_ms();
    let corpus = Corpus::load();
    let mut phases = vec![sample_cpu("process_start", start, cpu_start)];
    let engine = ZvecGrep::new();
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("runtime");
    let warm = ContextOptions {
        queries: vec!["warmup".to_owned()],
        root: Some(corpus.workspace.clone()),
        prefer_symbol: true,
        limit: Some(1),
        auto_update: false,
        refresh: Some(RefreshPolicy::Off),
        filter: QueryFilter {
            included_formats: vec![FileFormat::CSharp],
            ..QueryFilter::default()
        },
        ..ContextOptions::default()
    };
    let _ = runtime.block_on(engine.context(warm));
    phases.push(sample_cpu("index_warm", start, cpu_start));
    std::thread::sleep(std::time::Duration::from_millis(1500));
    phases.push(sample_cpu("index_idle", start, cpu_start));
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "ONINDEX_INDEX_FOOTPRINT",
            "phases": phases,
            "rss_hwm_kib": status_kib("VmHWM:"),
            "pss_end_kib": pss_kib(),
            "cpu_ms_total": cpu_ms().saturating_sub(cpu_start),
        }))
        .expect("json")
    );
}
