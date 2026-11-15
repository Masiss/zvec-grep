//! Deterministic DEFINE → DISCOVER → EXPAND → CHECK → FINAL workflow.
//!
//! This is the R4 integration seam, not a reasoning service. It is a bounded,
//! deterministic request loop an agent adapter can drive:
//!
//! 1. **DEFINE** — derive anchors and code tokens from the question.
//! 2. **DISCOVER** — run bounded lexical searches and collect candidate spans.
//! 3. **EXPAND** — read seed files, derive typed C#/SQL reference names from
//!    them, and search those names (bounded rounds and file set).
//! 4. **CHECK** — widen each candidate to its enclosing declaration, rank by
//!    anchor coverage, assemble hash-checked evidence, and run one corrective
//!    search for a required code token that no selected span covers.
//! 5. **FINAL** — return selected spans, typed gaps, stage traces and an
//!    abstention reason when the evidence is insufficient.
//!
//! Bounds are enforced, not advisory: rounds, tool calls, scopes, distinct
//! files, source reads, items, per-file spans, bytes and cancellation. Every
//! boundary becomes a typed gap; nothing is silently truncated and no content
//! is ever interpreted as an instruction.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::api::evidence::surface::{
    MAX_PACK_BYTES, MAX_PACK_ITEMS, SpanAddress, SpanRequest, assemble_spans,
};
use crate::api::evidence::{EvidenceBudget, EvidenceGapCode};
use crate::extraction::{csharp_facts, sql_facts};

/// Frozen report-contract ceilings reused by the workflow.
pub const MAX_WORKFLOW_ITEMS: usize = MAX_PACK_ITEMS;
/// Frozen report-contract byte ceiling reused by the workflow.
pub const MAX_WORKFLOW_BYTES: usize = MAX_PACK_BYTES;
const SEARCH_LIMIT: usize = 250;
const MAX_ANCHORS: usize = 8;
const MAX_CODE_TOKENS: usize = 4;
const MAX_REFS_PER_ROUND: usize = 8;
const MAX_WIDEN_BACK: usize = 40;
const MAX_WIDEN_LEN: usize = 60;

/// Bounded loop and output limits.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct WorkflowBudget {
    /// Discovery/expansion rounds; 1 means discover only.
    pub max_rounds: usize,
    /// Lexical searches across all rounds and the corrective round.
    pub max_tool_calls: usize,
    /// Distinct source files that may enter the candidate pool.
    pub max_files: usize,
    /// Source reads allowed for expansion, widening and ranking.
    pub max_source_reads: usize,
    /// Selected spans in the final evidence pack.
    pub max_items: usize,
    /// Selected content bytes in the final evidence pack.
    pub max_bytes: usize,
    /// File extensions admitted into the candidate pool; empty admits all.
    pub allowed_extensions: Vec<String>,
    /// Largest source file admitted into the candidate pool, in bytes.
    pub max_file_bytes: usize,
    /// Workspace-relative discovery scopes; empty searches the whole workspace.
    pub scope_roots: Vec<String>,
    /// Selected spans per file; diversity bound for the item budget.
    pub max_spans_per_file: usize,
}

impl Default for WorkflowBudget {
    fn default() -> Self {
        Self {
            max_rounds: 2,
            max_tool_calls: 12,
            max_files: 32,
            max_source_reads: 64,
            max_items: 10,
            max_bytes: MAX_WORKFLOW_BYTES,
            allowed_extensions: [
                "cs", "sql", "cshtml", "js", "ts", "tsx", "jsx", "py", "java", "go", "rs", "md",
                "txt",
            ]
            .iter()
            .map(ToString::to_string)
            .collect(),
            max_file_bytes: 1024 * 1024,
            scope_roots: Vec::new(),
            max_spans_per_file: 2,
        }
    }
}

impl WorkflowBudget {
    /// Clamps the budget to the frozen pack ceiling and to non-zero bounds.
    #[must_use]
    pub fn clamped(self) -> Self {
        Self {
            max_rounds: self.max_rounds.clamp(1, 4),
            max_tool_calls: self.max_tool_calls.clamp(1, 64),
            max_files: self.max_files.clamp(1, 128),
            max_source_reads: self.max_source_reads.clamp(1, 256),
            max_items: self.max_items.clamp(1, MAX_WORKFLOW_ITEMS),
            max_bytes: self.max_bytes.clamp(1, MAX_WORKFLOW_BYTES),
            allowed_extensions: self
                .allowed_extensions
                .iter()
                .map(|extension| extension.trim_start_matches('.').to_lowercase())
                .filter(|extension| !extension.is_empty())
                .collect(),
            max_file_bytes: self.max_file_bytes.clamp(1024, 16 * 1024 * 1024),
            scope_roots: self
                .scope_roots
                .iter()
                .map(|scope| scope.trim_matches('/').to_owned())
                .filter(|scope| !scope.is_empty())
                .collect(),
            max_spans_per_file: self.max_spans_per_file.clamp(1, 8),
        }
    }
}

/// One candidate source range.
#[derive(Clone, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
pub struct SearchUnit {
    /// Path relative to the workspace root.
    pub path: String,
    /// One-based first line.
    pub start_line: usize,
    /// One-based last line.
    pub end_line: usize,
}

/// Lexical discovery backend.
pub trait EvidenceSource {
    /// Returns candidate ranges for a literal pattern, bounded by `limit`.
    ///
    /// # Errors
    ///
    /// Returns an error when the backend cannot answer the query.
    fn search(&mut self, pattern: &str, limit: usize) -> Result<Vec<SearchUnit>, String>;
    /// Returns candidate ranges for a literal pattern inside one workspace scope.
    ///
    /// The default implementation ignores the scope.
    ///
    /// # Errors
    ///
    /// Returns an error when the backend cannot answer the query.
    fn search_in(
        &mut self,
        pattern: &str,
        limit: usize,
        _scope: Option<&str>,
    ) -> Result<Vec<SearchUnit>, String> {
        self.search(pattern, limit)
    }
    /// Reads a source file relative to the workspace root.
    ///
    /// # Errors
    ///
    /// Returns an error when the file cannot be read.
    fn read(&mut self, path: &str) -> Result<Option<String>, String>;
    /// Reports caller cancellation.
    fn is_cancelled(&self) -> bool {
        false
    }
}

/// Question-derived search plan.
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct Definition {
    /// Content words that are not stopwords.
    pub anchors: Vec<String>,
    /// Identifier-shaped tokens from the question.
    pub code_tokens: Vec<String>,
    /// Leading and trailing anchor phrases.
    pub phrases: Vec<String>,
    /// Whether the question asks about SQL/persistence.
    pub wants_sql: bool,
}

/// Workflow stages.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    /// Question analysis.
    Define,
    /// Lexical discovery.
    Discover,
    /// Typed reference expansion.
    Expand,
    /// Selection, verification and corrective round.
    Check,
    /// Outcome assembly.
    Final,
}

/// One stage trace entry.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct StageTrace {
    /// Stage name.
    pub stage: Stage,
    /// Tool calls made in the stage.
    pub tool_calls: usize,
    /// Distinct files seen at the end of the stage.
    pub files: usize,
    /// Candidate units at the end of the stage.
    pub candidates: usize,
    /// Bounded human-readable note.
    pub note: String,
}

/// One selected, hash-checked span.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct SelectedSpan {
    /// Path relative to the workspace root.
    pub path: String,
    /// One-based first line.
    pub start_line: usize,
    /// One-based last line.
    pub end_line: usize,
    /// SHA-256 of the file version the content came from.
    pub source_sha256: String,
    /// Whether a supplied hash had to match.
    pub verified: bool,
    /// Content bytes in the pack.
    pub content_bytes: usize,
    /// Which question code tokens or anchors the span covers.
    pub anchors_hit: Vec<String>,
}

/// Why the workflow abstained.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case", tag = "reason")]
pub enum AbstainReason {
    /// No candidate or selected span.
    NoEvidence,
    /// Required question code tokens are absent from the selected evidence.
    MissingCodeTokens {
        /// Tokens that no selected span covers.
        tokens: Vec<String>,
    },
    /// The caller cancelled the loop.
    Cancelled,
}

/// Loop statistics.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct WorkflowStats {
    /// Discovery/expansion rounds run.
    pub rounds: usize,
    /// Lexical searches performed.
    pub tool_calls: usize,
    /// Repeated searches suppressed by deduplication.
    pub duplicate_calls: usize,
    /// Distinct files in the candidate pool.
    pub files_seen: usize,
    /// Source reads performed.
    pub source_reads: usize,
    /// Source bytes read.
    pub bytes_read: usize,
    /// Selected content bytes.
    pub selected_bytes: usize,
    /// Units rejected by the file-type or file-size bounds.
    pub filtered_units: usize,
}

/// Bounded workflow outcome.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct WorkflowOutcome {
    /// Question-derived plan.
    pub definition: Definition,
    /// Candidate pool after filtering, before selection.
    pub candidates: Vec<SearchUnit>,
    /// Selected hash-checked spans.
    pub selected: Vec<SelectedSpan>,
    /// Typed gaps keyed by gap code.
    pub gaps: BTreeMap<String, Vec<String>>,
    /// Abstention reason, when the evidence is insufficient.
    pub abstain: Option<AbstainReason>,
    /// Loop statistics.
    pub stats: WorkflowStats,
    /// Stage traces in execution order.
    pub stages: Vec<StageTrace>,
}

const STOPWORDS: &[&str] = &[
    "la", "là", "co", "có", "cua", "của", "va", "và", "nhung", "những", "nao", "nào", "gi", "gì",
    "di", "đi", "qua", "đâu", "khi", "thì", "cho", "trong", "ra", "sao", "nhu", "như", "the",
    "thế", "mot", "một", "cac", "các", "duoc", "được", "bi", "bị", "tu", "từ", "voi", "với", "hay",
    "khong", "không", "phai", "phải", "moi", "mọi", "lan", "lần", "sua", "sửa", "doi", "đổi",
    "the", "thể", "anh", "ảnh", "huong", "hưởng", "ngoai", "ngoài", "vao", "vào", "noi", "nơi",
    "nay", "này", "do", "đó", "o", "ở", "tai", "tại", "bang", "bằng", "de", "để", "neu", "nếu",
    "vi", "vì", "nen", "nên", "rang", "rằng", "cung", "cũng", "chi", "chỉ", "con", "còn", "da",
    "đã", "dang", "đang", "se", "sẽ", "boi", "bởi", "theo", "tren", "trên", "duoi", "dưới", "giua",
    "giữa", "sau", "truoc", "trước", "khien", "khiến", "lam", "làm", "cai", "cái", "chay", "chạy",
    "bao", "nhiêu", "nhieu", "hết", "het",
];

const SQL_HINTS: &[&str] = &[
    "sql",
    "proc",
    "procedure",
    "stored",
    "database",
    "db",
    "savechanges",
    "ef",
    "entity",
    "table",
    "bảng",
    "dữ liệu",
];

fn words(text: &str) -> Vec<String> {
    text.split(|ch: char| !(ch.is_alphanumeric() || ch == '_'))
        .filter(|word| !word.is_empty())
        .map(ToOwned::to_owned)
        .collect()
}

fn is_code_shaped(word: &str) -> bool {
    word.len() >= 3
        && (word.contains('_')
            || word.chars().any(|ch| ch.is_ascii_uppercase())
            || word.chars().filter(char::is_ascii_uppercase).count() > 1)
}

/// Derives the bounded search plan from a question.
#[must_use]
pub fn define(question: &str) -> Definition {
    let lower = question.to_lowercase();
    let mut anchors = Vec::new();
    let mut code_tokens = Vec::new();
    for word in words(question) {
        let normalized = word.to_lowercase();
        if is_code_shaped(&word) && !word.chars().all(|ch| ch.is_numeric()) {
            if !code_tokens.iter().any(|token| token == &word)
                && code_tokens.len() < MAX_CODE_TOKENS
            {
                code_tokens.push(word.clone());
            }
        }
        if normalized.chars().count() < 3 || STOPWORDS.contains(&normalized.as_str()) {
            continue;
        }
        if !anchors.iter().any(|anchor| anchor == &normalized) && anchors.len() < MAX_ANCHORS {
            anchors.push(normalized);
        }
    }
    let mut phrases = Vec::new();
    if anchors.len() >= 2 {
        phrases.push(
            anchors
                .iter()
                .take(3)
                .cloned()
                .collect::<Vec<_>>()
                .join(" "),
        );
        let tail = anchors[anchors.len().saturating_sub(3)..].join(" ");
        if phrases.first().is_none_or(|first| first != &tail) {
            phrases.push(tail);
        }
    }
    Definition {
        anchors,
        code_tokens,
        phrases,
        wants_sql: SQL_HINTS.iter().any(|hint| lower.contains(hint)),
    }
}

/// Runs the bounded workflow against a discovery backend.
///
/// # Errors
///
/// Backend errors are recorded as gaps; the only hard error is an invalid
/// evidence root, which is returned so the caller can distinguish a configuration
/// failure from an evidence gap.
pub fn run(
    source: &mut impl EvidenceSource,
    root: &Path,
    question: &str,
    budget: WorkflowBudget,
) -> Result<WorkflowOutcome, String> {
    let budget = budget.clamped();
    if !root.is_dir() {
        return Err(format!(
            "evidence root is not a directory: {}",
            root.display()
        ));
    }
    let definition = define(question);
    let mut state = State::new(root, definition, budget);
    state.stages.push(StageTrace {
        stage: Stage::Define,
        tool_calls: 0,
        files: 0,
        candidates: 0,
        note: format!(
            "anchors={} code_tokens={} sql={}",
            state.definition.anchors.len(),
            state.definition.code_tokens.len(),
            state.definition.wants_sql
        ),
    });
    if state.definition.anchors.is_empty() && state.definition.code_tokens.is_empty() {
        state.abstain = Some(AbstainReason::NoEvidence);
        return Ok(state.finish());
    }
    state.discover(source)?;
    if state.abstain.is_some() {
        return Ok(state.finish());
    }
    state.expand(source);
    if state.abstain.is_some() {
        return Ok(state.finish());
    }
    state.check(source);
    Ok(state.finish())
}

struct State<'a> {
    root: &'a Path,
    definition: Definition,
    budget: WorkflowBudget,
    units: Vec<SearchUnit>,
    seen_units: BTreeSet<SearchUnit>,
    seen_calls: BTreeSet<String>,
    refs_seen: BTreeSet<String>,
    files: BTreeSet<String>,
    texts: HashMap<String, Option<String>>,
    gaps: BTreeMap<String, Vec<String>>,
    stats: WorkflowStats,
    stages: Vec<StageTrace>,
    abstain: Option<AbstainReason>,
    selected: Vec<SelectedSpan>,
}

impl<'a> State<'a> {
    fn new(root: &'a Path, definition: Definition, budget: WorkflowBudget) -> Self {
        Self {
            root,
            definition,
            budget,
            units: Vec::new(),
            seen_units: BTreeSet::new(),
            seen_calls: BTreeSet::new(),
            refs_seen: BTreeSet::new(),
            files: BTreeSet::new(),
            texts: HashMap::new(),
            gaps: BTreeMap::new(),
            stats: WorkflowStats::default(),
            stages: Vec::new(),
            abstain: None,
            selected: Vec::new(),
        }
    }

    fn gap(&mut self, code: EvidenceGapCode, detail: String) {
        self.gaps
            .entry(format!("{code:?}").to_lowercase())
            .or_default()
            .push(detail);
    }

    fn budget_gap(&mut self, key: &str, detail: String) {
        self.gaps.entry(key.to_owned()).or_default().push(detail);
    }

    fn admits(&mut self, path: &str) -> bool {
        if !self.budget.allowed_extensions.is_empty() {
            let extension = Path::new(path)
                .extension()
                .and_then(std::ffi::OsStr::to_str)
                .map(str::to_lowercase)
                .unwrap_or_default();
            if !self.budget.allowed_extensions.contains(&extension) {
                return false;
            }
        }
        match std::fs::metadata(self.root.join(path)) {
            Ok(metadata) => {
                metadata.is_file()
                    && usize::try_from(metadata.len()).unwrap_or(usize::MAX)
                        <= self.budget.max_file_bytes
            }
            Err(_) => false,
        }
    }

    fn calls_left(&self) -> usize {
        self.budget
            .max_tool_calls
            .saturating_sub(self.stats.tool_calls)
    }

    fn trace(&mut self, stage: Stage, note: String) {
        self.stages.push(StageTrace {
            stage,
            tool_calls: self.stats.tool_calls,
            files: self.files.len(),
            candidates: self.units.len(),
            note,
        });
    }

    fn discover(&mut self, source: &mut impl EvidenceSource) -> Result<(), String> {
        let mut queries = Vec::new();
        queries.extend(self.definition.code_tokens.iter().cloned());
        queries.extend(self.definition.phrases.iter().cloned());
        if !self.definition.anchors.is_empty() {
            queries.push(self.definition.anchors.join(" "));
        }
        self.round(source, queries);
        self.trace(
            Stage::Discover,
            format!(
                "queries={} units={}",
                self.stats.tool_calls,
                self.units.len()
            ),
        );
        Ok(())
    }

    fn expand(&mut self, source: &mut impl EvidenceSource) {
        for round in 1..self.budget.max_rounds {
            let refs = self.reference_names(source);
            if refs.is_empty() || self.calls_left() == 0 {
                self.trace(
                    Stage::Expand,
                    format!("round={round} refs=0 calls_left={}", self.calls_left()),
                );
                return;
            }
            self.round(source, refs);
            self.trace(
                Stage::Expand,
                format!("round={round} units={}", self.units.len()),
            );
        }
    }

    fn round(&mut self, source: &mut impl EvidenceSource, queries: Vec<String>) {
        for query in queries {
            if source.is_cancelled() {
                self.abstain = Some(AbstainReason::Cancelled);
                return;
            }
            let normalized = query.trim().to_lowercase();
            if normalized.is_empty() {
                continue;
            }
            if self.seen_calls.contains(&normalized) {
                self.stats.duplicate_calls += 1;
                continue;
            }
            self.seen_calls.insert(normalized);
            let scopes: Vec<Option<String>> = if self.budget.scope_roots.is_empty() {
                vec![None]
            } else {
                self.budget
                    .scope_roots
                    .iter()
                    .map(|scope| Some(scope.clone()))
                    .collect()
            };
            for scope in scopes {
                if self.calls_left() == 0 {
                    self.budget_gap(
                        "budget_tool_calls",
                        format!("tool-call budget exhausted before query {query}"),
                    );
                    return;
                }
                self.stats.tool_calls += 1;
                let units = match source.search_in(&query, SEARCH_LIMIT, scope.as_deref()) {
                    Ok(units) => units,
                    Err(error) => {
                        self.gap(EvidenceGapCode::Missing, format!("search failed: {error}"));
                        continue;
                    }
                };
                for unit in units {
                    if !self.admits(&unit.path) {
                        self.stats.filtered_units += 1;
                        continue;
                    }
                    if self.files.len() >= self.budget.max_files && !self.files.contains(&unit.path)
                    {
                        self.budget_gap(
                            "budget_files",
                            format!("file budget exhausted at {}", unit.path),
                        );
                        continue;
                    }
                    if self.seen_units.insert(unit.clone()) {
                        self.files.insert(unit.path.clone());
                        self.units.push(unit);
                    }
                }
            }
        }
    }

    fn text(&mut self, source: &mut impl EvidenceSource, path: &str) -> Option<String> {
        if let Some(cached) = self.texts.get(path) {
            return cached.clone();
        }
        if self.stats.source_reads >= self.budget.max_source_reads {
            self.budget_gap(
                "budget_source_reads",
                format!("source-read budget exhausted at {path}"),
            );
            return None;
        }
        self.stats.source_reads += 1;
        let text = match source.read(path) {
            Ok(text) => text,
            Err(error) => {
                self.gap(
                    EvidenceGapCode::OutOfRoot,
                    format!("source read failed: {path}: {error}"),
                );
                None
            }
        };
        if text.is_none() {
            self.gap(
                EvidenceGapCode::Missing,
                format!("source unreadable during expansion: {path}"),
            );
        }
        self.stats.bytes_read += text.as_ref().map_or(0, String::len);
        self.texts.insert(path.to_owned(), text.clone());
        text
    }

    fn reference_names(&mut self, source: &mut impl EvidenceSource) -> Vec<String> {
        let seeds = self.units.clone();
        let mut names = Vec::new();
        for unit in seeds {
            if names.len() >= MAX_REFS_PER_ROUND * self.budget.max_rounds {
                break;
            }
            let Some(text) = self.text(source, &unit.path) else {
                continue;
            };
            for name in typed_references(&unit.path, &text, &unit) {
                if name.len() < 4 || self.refs_seen.contains(&name) {
                    continue;
                }
                if self
                    .definition
                    .anchors
                    .iter()
                    .any(|anchor| anchor == &name.to_lowercase())
                {
                    continue;
                }
                self.refs_seen.insert(name.clone());
                names.push(name);
            }
        }
        names.truncate(MAX_REFS_PER_ROUND);
        names
    }

    fn check(&mut self, source: &mut impl EvidenceSource) {
        let mut ranked = self.rank(source);
        self.select(&mut ranked);
        if let Some(missing) = self.missing_code_tokens() {
            if self.calls_left() > 0 && !source.is_cancelled() {
                let queries = missing.clone();
                self.round(source, queries);
                {
                    let mut reranked = self.rank(source);
                    self.selected.clear();
                    self.select(&mut reranked);
                }
            }
        }
        let missing = self.missing_code_tokens().unwrap_or_default();
        if self.selected.is_empty() {
            self.abstain = Some(AbstainReason::NoEvidence);
        } else if !missing.is_empty() {
            for token in &missing {
                self.gap(
                    EvidenceGapCode::Missing,
                    format!("required code token absent from selected evidence: {token}"),
                );
            }
            self.abstain = Some(AbstainReason::MissingCodeTokens { tokens: missing });
        }
        self.trace(
            Stage::Check,
            format!("selected={} pool={}", self.selected.len(), self.units.len()),
        );
    }

    fn rank(
        &mut self,
        source: &mut impl EvidenceSource,
    ) -> Vec<(i64, SearchUnit, Vec<String>, String, String)> {
        let definition = self.definition.clone();
        let units = self.units.clone();
        let mut ranked = Vec::new();
        for unit in units {
            let Some(text) = self.text(source, &unit.path) else {
                continue;
            };
            let (start, end) = widened(&text, unit.start_line, unit.end_line);
            let content = text
                .lines()
                .skip(start.saturating_sub(1))
                .take(end.saturating_sub(start).saturating_add(1))
                .collect::<Vec<_>>()
                .join("\n");
            let lower = content.to_lowercase();
            let mut hits = Vec::new();
            let mut score = 0i64;
            for token in &definition.code_tokens {
                if lower.contains(&token.to_lowercase()) {
                    score += 40;
                    hits.push(token.clone());
                }
            }
            for anchor in &definition.anchors {
                if lower.contains(anchor) {
                    score += 8;
                    hits.push(anchor.clone());
                }
            }
            let path_lower = unit.path.to_lowercase();
            for anchor in &definition.anchors {
                if path_lower.contains(anchor) {
                    score += 4;
                }
            }
            if is_declaration(&content) {
                score += 15;
            }
            score -= i64::try_from(content.len() / 1024).unwrap_or(16);
            let widened_unit = SearchUnit {
                path: unit.path.clone(),
                start_line: start,
                end_line: end,
            };
            let file_sha = hex::encode(Sha256::digest(text.as_bytes()));
            ranked.push((score, widened_unit, hits, content, file_sha));
        }
        ranked.sort_by(|left, right| right.0.cmp(&left.0).then_with(|| left.1.cmp(&right.1)));
        ranked
    }

    fn select(&mut self, ranked: &mut Vec<(i64, SearchUnit, Vec<String>, String, String)>) {
        let budget = EvidenceBudget {
            max_bytes: self.budget.max_bytes,
            max_items: self.budget.max_items,
        };
        let mut requests = Vec::new();
        let mut used_bytes = 0usize;
        let mut chosen: Vec<(SearchUnit, Vec<String>, usize, String)> = Vec::new();
        for (score, unit, hits, content, file_sha) in ranked.drain(..) {
            if chosen.len() >= self.budget.max_items {
                self.budget_gap(
                    "budget_items",
                    format!("item budget exhausted before {}", unit.path),
                );
                break;
            }
            if score <= 0 {
                continue;
            }
            if chosen
                .iter()
                .filter(|(other, ..)| other.path == unit.path)
                .count()
                >= self.budget.max_spans_per_file
            {
                self.budget_gap(
                    "budget_spans_per_file",
                    format!("per-file span cap reached for {}", unit.path),
                );
                continue;
            }
            if chosen.iter().any(|(other, ..)| {
                other.path == unit.path
                    && other.start_line <= unit.end_line
                    && unit.start_line <= other.end_line
            }) {
                continue;
            }
            let bytes = content.len();
            if bytes > budget.max_bytes.saturating_sub(used_bytes) {
                self.budget_gap(
                    "budget_bytes",
                    format!("{} exceeds remaining snippet budget", unit.path),
                );
                continue;
            }
            used_bytes += bytes;
            chosen.push((unit.clone(), hits, bytes, file_sha.clone()));
            requests.push(SpanRequest {
                project: "workflow".to_owned(),
                relative_path: unit.path.clone().into(),
                expected_sha256: Some(file_sha),
                address: SpanAddress::Lines {
                    start_line: unit.start_line,
                    end_line: unit.end_line,
                },
            });
        }
        if requests.is_empty() {
            return;
        }
        let pack = match assemble_spans(self.root, &requests, budget) {
            Ok(pack) => pack,
            Err(error) => {
                self.gap(EvidenceGapCode::Missing, format!("pack rejected: {error}"));
                return;
            }
        };
        for gap in &pack.gaps {
            self.gap(
                gap.code,
                format!("{}: {}", gap.relative_path.display(), gap.detail),
            );
        }
        for item in &pack.items {
            let Some((unit, hits, bytes, _)) = chosen
                .iter()
                .find(|(unit, ..)| unit.path == item.relative_path.to_string_lossy())
            else {
                continue;
            };
            let start_line = item.range.start_line().unwrap_or(unit.start_line);
            let end_line = item.range.last_line().unwrap_or(unit.end_line);
            self.selected.push(SelectedSpan {
                path: unit.path.clone(),
                start_line,
                end_line,
                source_sha256: item.source_sha256.clone(),
                verified: true,
                content_bytes: *bytes,
                anchors_hit: hits.clone(),
            });
        }
        self.stats.selected_bytes = self.selected.iter().map(|span| span.content_bytes).sum();
    }

    fn missing_code_tokens(&self) -> Option<Vec<String>> {
        let missing = self
            .definition
            .code_tokens
            .iter()
            .filter(|token| {
                !self.selected.iter().any(|span| {
                    span.anchors_hit
                        .iter()
                        .any(|hit| hit.eq_ignore_ascii_case(token))
                })
            })
            .cloned()
            .collect::<Vec<_>>();
        (!missing.is_empty()).then_some(missing)
    }

    fn finish(&mut self) -> WorkflowOutcome {
        self.stats.rounds = 1 + self
            .stages
            .iter()
            .filter(|trace| trace.stage == Stage::Expand)
            .count();
        self.stats.files_seen = self.files.len();
        WorkflowOutcome {
            definition: self.definition.clone(),
            candidates: self.units.clone(),
            selected: self.selected.clone(),
            gaps: self.gaps.clone(),
            abstain: self.abstain.clone(),
            stats: self.stats,
            stages: self.stages.clone(),
        }
    }
}

fn widened(text: &str, start_line: usize, end_line: usize) -> (usize, usize) {
    let lines = text.lines().collect::<Vec<_>>();
    if lines.is_empty() {
        return (1, 1);
    }
    let last = lines.len();
    let mut start = start_line.clamp(1, last);
    let back_limit = start.saturating_sub(MAX_WIDEN_BACK).max(1);
    while start > back_limit {
        let previous = lines[start - 2];
        if is_declaration(previous) || previous.trim() == "}" {
            break;
        }
        start -= 1;
    }
    let end = end_line.clamp(start, last);
    let mut widened_end = end;
    let limit = (start + MAX_WIDEN_LEN).min(last);
    while widened_end < limit {
        let next = lines[widened_end];
        if next.trim_end() == "}" {
            widened_end += 1;
            break;
        }
        widened_end += 1;
    }
    (start, widened_end.max(end))
}

fn is_declaration(line: &str) -> bool {
    let trimmed = line.trim_start();
    [
        "class ",
        "struct ",
        "interface ",
        "public ",
        "private ",
        "protected ",
        "internal ",
        "static ",
        "void ",
        "CREATE PROCEDURE",
        "ALTER PROCEDURE",
        "CREATE VIEW",
        "ALTER VIEW",
    ]
    .iter()
    .any(|prefix| trimmed.starts_with(prefix))
}

fn typed_references(path: &str, text: &str, unit: &SearchUnit) -> Vec<String> {
    let mut names = BTreeSet::new();
    if path.ends_with(".cs") {
        let identity = csharp_facts::ProjectIdentity {
            name: "workflow".to_owned(),
            assembly: "workflow".to_owned(),
            root_namespace: None,
        };
        let facts = csharp_facts::parse_file(&identity, path, text);
        for db_set in &facts.db_sets {
            if overlaps(unit, db_set.line_start, db_set.line_end) {
                names.insert(db_set.element_type.clone());
            }
        }
        for invocation in &facts.invocations {
            if overlaps(unit, invocation.line, invocation.line) {
                let called = invocation
                    .called
                    .split(['<', '.'])
                    .next()
                    .unwrap_or_default()
                    .trim()
                    .to_owned();
                if called.len() >= 4 {
                    names.insert(called);
                }
            }
        }
        for fact in &facts.types {
            if overlaps(unit, fact.line_start, fact.line_end) {
                names.insert(fact.name.clone());
            }
        }
        for save in &facts.saves {
            if overlaps(unit, save.line, save.line) {
                if let Some(context) = &save.context_type {
                    names.insert(context.clone());
                }
            }
        }
    } else if path.ends_with(".sql") {
        for definition in sql_facts::parse_sql_file(path, text, true, "workflow") {
            if overlaps(unit, definition.line_start, definition.line_end) {
                names.insert(definition.name.clone());
                for access in definition.writes.iter().chain(&definition.reads) {
                    names.insert(access.table.clone());
                }
            }
        }
    } else {
        for line in text.lines().skip(unit.start_line.saturating_sub(1)).take(
            unit.end_line
                .saturating_sub(unit.start_line)
                .saturating_add(1),
        ) {
            for word in words(line) {
                if word.len() >= 4
                    && (word.chars().next().is_some_and(char::is_uppercase) || word.contains('_'))
                {
                    names.insert(word);
                }
            }
        }
        names.retain(|name| {
            ![
                "Public",
                "Private",
                "Protected",
                "Internal",
                "String",
                "Object",
            ]
            .contains(&name.as_str())
        });
    }
    names.into_iter().take(4).collect()
}

const fn overlaps(unit: &SearchUnit, start_line: usize, end_line: usize) -> bool {
    unit.start_line <= end_line && start_line <= unit.end_line
}

#[cfg(test)]
mod tests;
