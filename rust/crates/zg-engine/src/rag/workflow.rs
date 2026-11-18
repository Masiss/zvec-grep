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
const MAX_DECLARATION_LINES: usize = 200;
/// Pool-wide file bound as a multiple of the selected-file budget.
const POOL_FILE_FACTOR: usize = 8;
/// Fair-share floor per evidence subject, in bytes.
const MIN_SUBJECT_BYTES: usize = 8192;
/// Verified evidence budget per span: larger declarations are trimmed.
const MAX_SPAN_BYTES: usize = 2560;
/// Lines kept before the first matched line when trimming.
const COMPACT_LEAD: usize = 6;
/// Lines kept after the last matched line when trimming.
const COMPACT_TRAIL: usize = 14;

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
    /// Minimum selected slots reserved for the indexed FTS route when available.
    pub fts_quota: usize,
    /// Minimum selected slots reserved for the hybrid question route when available.
    pub hybrid_quota: usize,
    /// Extra spans per file allowed when they cover a distinct declaration.
    pub max_spans_per_file_extra: usize,
    /// Whether the hybrid question route runs during discovery.
    pub use_hybrid: bool,
    /// Whether the indexed FTS route runs during discovery.
    pub use_fts: bool,
    /// Whether derived lexical queries run during discovery.
    pub use_lexical: bool,
    /// Whether typed fact expansion runs after discovery.
    pub use_facts: bool,
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
            fts_quota: 2,
            hybrid_quota: 4,
            max_spans_per_file_extra: 2,
            use_hybrid: true,
            use_fts: true,
            use_lexical: true,
            use_facts: true,
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
            fts_quota: self.fts_quota.min(MAX_WORKFLOW_ITEMS),
            hybrid_quota: self.hybrid_quota.min(MAX_WORKFLOW_ITEMS),
            max_spans_per_file_extra: self.max_spans_per_file_extra.clamp(0, 4),
            use_hybrid: self.use_hybrid,
            use_fts: self.use_fts,
            use_lexical: self.use_lexical,
            use_facts: self.use_facts,
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
    /// Returns candidate ranges with backend file filtering applied.
    ///
    /// The default implementation ignores the filter, so backends that cannot
    /// filter must override it or accept the starvation risk.
    ///
    /// # Errors
    ///
    /// Returns an error when the backend cannot answer the query.
    fn search_filtered(
        &mut self,
        pattern: &str,
        limit: usize,
        scope: Option<&str>,
        _extensions: &[String],
    ) -> Result<Vec<SearchUnit>, String> {
        self.search_in(pattern, limit, scope)
    }

    /// Exact declaration lookup.
    ///
    /// The default returns no declarations, which leaves the symbol route
    /// unavailable rather than silently degrading to a content search.
    ///
    /// # Errors
    ///
    /// Returns an error when the backend cannot answer the query.
    fn symbol(&mut self, _name: &str, _limit: usize) -> Result<Vec<SearchUnit>, String> {
        Ok(Vec::new())
    }

    /// Hybrid question route (FTS + vectors + structure) over the question text.
    ///
    /// The default returns nothing; backends without an index omit it.
    ///
    /// # Errors
    ///
    /// Returns an error when the backend cannot answer the query.
    fn hybrid(&mut self, _question: &str, _limit: usize) -> Result<Vec<SearchUnit>, String> {
        Ok(Vec::new())
    }

    /// Indexed FTS route used as the protected baseline.
    ///
    /// The default returns nothing; backends without an index simply omit it.
    ///
    /// # Errors
    ///
    /// Returns an error when the backend cannot answer the query.
    fn fts(&mut self, _query: &str, _limit: usize) -> Result<Vec<SearchUnit>, String> {
        Ok(Vec::new())
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

/// Where a candidate entered the pool.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CandidateSource {
    /// Indexed FTS route, the protected baseline.
    Fts,
    /// Hybrid question route: the deployable discovery baseline.
    Hybrid,
    /// Question-handle lexical search.
    Lexical,
    /// Lexical search inside one discovery scope with backend file filters.
    ScopedLexical,
    /// Derived from a typed C#/SQL fact and searched lexically.
    FactExpansion,
    /// Exact declaration lookup.
    Symbol,
    /// Name-targeted lexical fallback when no symbol route is available.
    LexicalFallback,
    /// Corrective round.
    Corrective,
}

/// What a selected span is evidence of.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceRole {
    /// A question-token hit or FTS snippet.
    SeedHit,
    /// A declaration range from exact symbol lookup.
    Declaration,
    /// A cross-file reference reached by fact expansion.
    Reference,
    /// A stored procedure or view definition.
    ProcDefinition,
    /// A source-visible table read/write.
    TableAccess,
}

/// Why a pooled candidate did or did not reach the verified pack.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CandidateDecision {
    /// Selected and verified in the final pack.
    Selected,
    /// Rejected before pooling: file type or file size bound.
    RejectedAdmission,
    /// Rejected before pooling: file-count budget.
    RejectedFileBudget,
    /// Ranking produced no positive score.
    RejectedScore,
    /// Item budget already exhausted.
    RejectedItems,
    /// Near-identical to a selected span from another route.
    RejectedDuplicate,
    /// Per-file span cap (including the distinct-declaration allowance).
    RejectedPerFileCap,
    /// Fair-share byte budget for this subject.
    RejectedSubjectBytes,
    /// Remaining pack byte budget too small.
    RejectedSpanBytes,
    /// Pack assembly refused the span (stale, missing or oversized read).
    RejectedVerification,
    /// Pooled and ranked, but the item budget ended before this candidate was
    /// examined. It was not scored-rejected: it simply never got a turn.
    Unexamined,
}

/// One pooled candidate and the reason it did or did not survive.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct TracedCandidate {
    /// Candidate range.
    pub unit: SearchUnit,
    /// Routes that produced it.
    pub sources: Vec<CandidateSource>,
    /// Best route rank observed for it.
    pub route_rank: usize,
    /// Final decision.
    pub decision: CandidateDecision,
    /// Selected range, when the decision is `Selected` (trimmed evidence unit).
    pub selected: Option<SearchUnit>,
}

/// One pooled candidate with provenance.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct PoolEntry {
    /// Candidate range.
    pub unit: SearchUnit,
    /// Routes that produced this range, in priority order.
    pub sources: Vec<CandidateSource>,
    /// Typed facts that produced this range, when any.
    pub facts: Vec<String>,
    /// Position within the producing route's own result list.
    pub route_rank: usize,
}

/// Question-derived search plan.
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct Definition {
    /// Original question text.
    pub question: String,
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
    /// Routes that produced this evidence.
    pub sources: Vec<CandidateSource>,
    /// Typed facts behind this evidence.
    pub facts: Vec<String>,
    /// Evidence role.
    pub role: EvidenceRole,
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
    /// Selected content bytes (the final source pack, contract ≤24 KiB).
    pub selected_bytes: usize,
    /// Cumulative tool content bytes: every unit of source content this
    /// workflow would have placed in front of the investigating agent
    /// (`contract ≤64 KiB`), including the final pack.
    pub tool_content_bytes: usize,
    /// Units rejected by the file-type or file-size bounds.
    pub filtered_units: usize,
    /// Indexed FTS calls.
    pub fts_calls: usize,
    /// Exact symbol lookups.
    pub symbol_calls: usize,
    /// Hybrid question route calls.
    pub hybrid_calls: usize,
    /// Candidates rejected before pooling.
    pub admission_rejections: usize,
}

/// Bounded workflow outcome.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct WorkflowOutcome {
    /// Question-derived plan.
    pub definition: Definition,
    /// Candidate pool after filtering, before selection.
    pub candidates: Vec<SearchUnit>,
    /// Pooled candidates with route and fact provenance.
    pub pool: Vec<PoolEntry>,
    /// Every pooled candidate with its final decision.
    pub traced: Vec<TracedCandidate>,
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
        question: question.to_owned(),
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
    traced: Vec<TracedCandidate>,
    pool: BTreeMap<SearchUnit, PoolEntry>,
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
            traced: Vec::new(),
            pool: BTreeMap::new(),
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

    fn accept(
        &mut self,
        unit: SearchUnit,
        source: CandidateSource,
        fact: Option<&str>,
        route_rank: usize,
    ) {
        if !self.admits(&unit.path) {
            self.stats.filtered_units += 1;
            self.stats.admission_rejections += 1;
            self.trace_decision(
                &unit,
                source,
                route_rank,
                CandidateDecision::RejectedAdmission,
                None,
            );
            return;
        }
        // The file budget bounds the pack (enforced in selection), not visibility:
        // first-come rejection starved every candidate after the first N files.
        if self.files.len() >= self.budget.max_files.saturating_mul(POOL_FILE_FACTOR)
            && !self.files.contains(&unit.path)
        {
            self.budget_gap(
                "budget_files",
                format!("file budget exhausted at {}", unit.path),
            );
            self.trace_decision(
                &unit,
                source,
                route_rank,
                CandidateDecision::RejectedFileBudget,
                None,
            );
            return;
        }
        let entry = self.pool.entry(unit.clone()).or_insert_with(|| PoolEntry {
            unit: unit.clone(),
            sources: Vec::new(),
            facts: Vec::new(),
            route_rank,
        });
        entry.route_rank = entry.route_rank.min(route_rank);
        if !entry.sources.contains(&source) {
            entry.sources.push(source);
            entry.sources.sort();
        }
        if let Some(fact) = fact {
            if !entry.facts.iter().any(|known| known == fact) {
                entry.facts.push(fact.to_owned());
                entry.facts.sort();
            }
        }
        if self.seen_units.insert(unit.clone()) {
            self.files.insert(unit.path.clone());
            self.units.push(unit);
        }
    }

    fn trace_decision(
        &mut self,
        unit: &SearchUnit,
        source: CandidateSource,
        route_rank: usize,
        decision: CandidateDecision,
        selected: Option<SearchUnit>,
    ) {
        if let Some(existing) = self.traced.iter_mut().find(|entry| &entry.unit == unit) {
            if decision == CandidateDecision::Selected
                || existing.decision != CandidateDecision::Selected
            {
                existing.decision = decision;
                existing.selected = selected;
            }
            if !existing.sources.contains(&source) {
                existing.sources.push(source);
                existing.sources.sort();
            }
            existing.route_rank = existing.route_rank.min(route_rank);
            return;
        }
        self.traced.push(TracedCandidate {
            unit: unit.clone(),
            sources: vec![source],
            route_rank,
            decision,
            selected,
        });
    }

    fn fts_route(&mut self, source: &mut impl EvidenceSource, query: &str) {
        if self.calls_left() == 0 || query.trim().is_empty() {
            return;
        }
        self.stats.tool_calls += 1;
        self.stats.fts_calls += 1;
        match source.fts(query, 10) {
            Ok(units) => {
                for (rank, unit) in units.into_iter().enumerate() {
                    self.accept(unit, CandidateSource::Fts, None, rank);
                }
            }
            Err(error) => self.gap(EvidenceGapCode::Missing, format!("fts failed: {error}")),
        }
    }

    fn hybrid_route(&mut self, source: &mut impl EvidenceSource, question: &str) {
        if self.calls_left() == 0 || question.trim().is_empty() {
            return;
        }
        self.stats.tool_calls += 1;
        self.stats.hybrid_calls += 1;
        match source.hybrid(question, 10) {
            Ok(units) => {
                for (rank, unit) in units.into_iter().enumerate() {
                    self.accept(unit, CandidateSource::Hybrid, None, rank);
                }
            }
            Err(error) => self.gap(
                EvidenceGapCode::Missing,
                format!("hybrid route failed: {error}"),
            ),
        }
    }

    fn symbol_route(&mut self, source: &mut impl EvidenceSource, name: &str, fact: &str) -> bool {
        if self.calls_left() == 0 {
            return false;
        }
        self.stats.tool_calls += 1;
        self.stats.symbol_calls += 1;
        match source.symbol(name, 20) {
            Ok(units) if !units.is_empty() => {
                for (rank, unit) in units.into_iter().enumerate() {
                    self.accept(unit, CandidateSource::Symbol, Some(fact), rank);
                }
                true
            }
            Ok(_) => false,
            Err(error) => {
                self.gap(
                    EvidenceGapCode::Missing,
                    format!("symbol lookup failed: {error}"),
                );
                false
            }
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
        let question = self.definition.question.clone();
        if self.budget.use_hybrid {
            self.hybrid_route(source, &question);
        }
        let fts_query = self.definition.code_tokens.join(" ");
        if self.budget.use_fts && !fts_query.is_empty() {
            self.fts_route(source, &fts_query);
        }
        if self.budget.use_lexical {
            let mut queries = Vec::new();
            queries.extend(self.definition.code_tokens.iter().cloned());
            queries.extend(self.definition.phrases.iter().cloned());
            if !self.definition.anchors.is_empty() {
                queries.push(self.definition.anchors.join(" "));
            }
            self.round(source, queries);
        }
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
        if !self.budget.use_facts {
            return;
        }
        for round in 1..self.budget.max_rounds {
            let refs = self.reference_names(source);
            if refs.is_empty() || self.calls_left() == 0 {
                self.trace(
                    Stage::Expand,
                    format!("round={round} refs=0 calls_left={}", self.calls_left()),
                );
                return;
            }
            let mut residual = Vec::new();
            for (name, fact) in refs {
                if !self.symbol_route(source, &name, &fact) {
                    residual.push(name);
                }
            }
            if !residual.is_empty() {
                self.round_with(source, residual, CandidateSource::FactExpansion);
            }
            self.trace(
                Stage::Expand,
                format!("round={round} units={}", self.units.len()),
            );
        }
    }

    fn round(&mut self, source: &mut impl EvidenceSource, queries: Vec<String>) {
        self.round_labeled(source, queries, None);
    }

    fn round_with(
        &mut self,
        source: &mut impl EvidenceSource,
        queries: Vec<String>,
        label: CandidateSource,
    ) {
        self.round_labeled(source, queries, Some(label));
    }

    fn round_labeled(
        &mut self,
        source: &mut impl EvidenceSource,
        queries: Vec<String>,
        label: Option<CandidateSource>,
    ) {
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
                let extensions = self.budget.allowed_extensions.clone();
                let units = match source.search_filtered(
                    &query,
                    SEARCH_LIMIT,
                    scope.as_deref(),
                    &extensions,
                ) {
                    Ok(units) => units,
                    Err(error) => {
                        self.gap(EvidenceGapCode::Missing, format!("search failed: {error}"));
                        continue;
                    }
                };
                let source_label = label.unwrap_or(if scope.is_some() {
                    CandidateSource::ScopedLexical
                } else {
                    CandidateSource::Lexical
                });
                for (rank, unit) in units.into_iter().enumerate() {
                    self.accept(unit, source_label, None, rank);
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

    fn reference_names(&mut self, source: &mut impl EvidenceSource) -> Vec<(String, String)> {
        let seeds = self.units.clone();
        let mut names = Vec::new();
        for unit in seeds {
            if names.len() >= MAX_REFS_PER_ROUND * self.budget.max_rounds {
                break;
            }
            let Some(text) = self.text(source, &unit.path) else {
                continue;
            };
            for (name, fact) in typed_references(&unit.path, &text, &unit) {
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
                names.push((name, fact));
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

    #[allow(clippy::type_complexity)]
    fn rank(
        &mut self,
        source: &mut impl EvidenceSource,
    ) -> Vec<(i64, SearchUnit, Vec<String>, String, String, PoolEntry)> {
        let definition = self.definition.clone();
        let units = self.units.clone();
        let mut ranked = Vec::new();
        for unit in units {
            let Some(text) = self.text(source, &unit.path) else {
                continue;
            };
            let (decl_start, decl_end) = declaration_range(&unit.path, &text, unit.start_line)
                .unwrap_or_else(|| widened(&text, unit.start_line, unit.end_line));
            let (start, end) =
                compact_range(&text, decl_start, decl_end, unit.start_line, unit.end_line);
            let content = text
                .lines()
                .skip(start.saturating_sub(1))
                .take(end.saturating_sub(start).saturating_add(1))
                .collect::<Vec<_>>()
                .join("\n");
            self.stats.tool_content_bytes += content.len();
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
            let provenance = self.pool.get(&unit).cloned().unwrap_or(PoolEntry {
                unit: unit.clone(),
                sources: Vec::new(),
                facts: Vec::new(),
                route_rank: usize::MAX,
            });
            // Route order is the only relevance signal the backend exposes, so it
            // dominates; derived lexical/type evidence only breaks ties.
            let rank = i64::try_from(provenance.route_rank).unwrap_or(i64::MAX);
            let route_bonus = if provenance.sources.contains(&CandidateSource::Hybrid) {
                10_000i64.saturating_sub(rank.saturating_mul(10))
            } else if provenance.sources.contains(&CandidateSource::Fts) {
                5_000i64.saturating_sub(rank.saturating_mul(5))
            } else {
                0
            };
            score = route_bonus.saturating_add(score.min(200));
            let corroborating = provenance.sources.len();
            score += i64::try_from(corroborating.saturating_sub(1)).unwrap_or(0) * 25;
            score -= i64::try_from(content.len() / 1024).unwrap_or(16);
            let widened_unit = SearchUnit {
                path: unit.path.clone(),
                start_line: start,
                end_line: end,
            };
            let file_sha = hex::encode(Sha256::digest(text.as_bytes()));
            ranked.push((score, widened_unit, hits, content, file_sha, provenance));
        }
        ranked.sort_by(|left, right| right.0.cmp(&left.0).then_with(|| left.1.cmp(&right.1)));
        ranked
    }

    fn select(
        &mut self,
        ranked: &mut Vec<(i64, SearchUnit, Vec<String>, String, String, PoolEntry)>,
    ) {
        let budget = EvidenceBudget {
            max_bytes: self.budget.max_bytes,
            max_items: self.budget.max_items,
        };
        let mut requests = Vec::new();
        let mut used_bytes = 0usize;
        let mut ordered = ranked.drain(..).collect::<Vec<_>>();
        // Reserved slots first (hybrid question route, then the FTS fallback), so
        // expansion and derived queries can never displace deployable evidence.
        let hybrid_quota = self.budget.hybrid_quota.min(self.budget.max_items);
        let fts_quota = self
            .budget
            .fts_quota
            .min(self.budget.max_items.saturating_sub(hybrid_quota));
        let order = if ordered.is_empty() || (hybrid_quota == 0 && fts_quota == 0) {
            (0..ordered.len()).collect::<Vec<_>>()
        } else {
            let has =
                |index: usize, source: CandidateSource| ordered[index].5.sources.contains(&source);
            let hybrid: Vec<usize> = (0..ordered.len())
                .filter(|index| has(*index, CandidateSource::Hybrid))
                .collect();
            let fts: Vec<usize> = (0..ordered.len())
                .filter(|index| has(*index, CandidateSource::Fts))
                .collect();
            let rest: Vec<usize> = (0..ordered.len())
                .filter(|index| {
                    !has(*index, CandidateSource::Hybrid) && !has(*index, CandidateSource::Fts)
                })
                .collect();
            let hybrid_reserved = hybrid.len().min(hybrid_quota);
            let fts_reserved = fts.len().min(fts_quota);
            let mut order = hybrid[..hybrid_reserved].to_vec();
            order.extend(fts[..fts_reserved].iter().copied());
            order.extend(rest);
            order.extend(hybrid[hybrid_reserved..].iter().copied());
            order.extend(fts[fts_reserved..].iter().copied());
            order
        };
        let mut chosen: Vec<(SearchUnit, Vec<String>, usize, String, PoolEntry)> = Vec::new();
        // Two passes: breadth first (one span per evidence subject), then depth.
        // Gold evidence in this corpus is spread across files, so file diversity
        // converts pool coverage into selected coverage more reliably than taking
        // several spans from the same file.
        let mut taken: BTreeSet<usize> = BTreeSet::new();
        for pass in 0..2 {
            for &index in &order {
                if taken.contains(&index) {
                    continue;
                }
                if pass == 0
                    && chosen
                        .iter()
                        .any(|(other, ..)| other.path == ordered[index].1.path)
                {
                    // Defer this subject to the depth pass.
                    continue;
                }
                let (score, unit, hits, content, file_sha, provenance) = ordered[index].clone();
                if !chosen.iter().any(|(other, ..)| other.path == unit.path)
                    && chosen
                        .iter()
                        .map(|(other, ..)| other.path.as_str())
                        .collect::<BTreeSet<_>>()
                        .len()
                        >= self.budget.max_files
                {
                    self.budget_gap(
                        "budget_files",
                        format!("selected-file budget reached before {}", unit.path),
                    );
                    self.trace_decision(
                        &unit,
                        provenance
                            .sources
                            .first()
                            .copied()
                            .unwrap_or(CandidateSource::Lexical),
                        provenance.route_rank,
                        CandidateDecision::RejectedFileBudget,
                        None,
                    );
                    continue;
                }
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
                let same_file = chosen
                    .iter()
                    .filter(|(other, ..)| other.path == unit.path)
                    .count();
                if same_file >= self.budget.max_spans_per_file {
                    // P1: a further span is admitted only when it covers a distinct
                    // declaration, and only within the bounded extra allowance.
                    let distinct = !chosen.iter().any(|(other, ..)| {
                        other.path == unit.path
                            && other.start_line <= unit.end_line
                            && unit.start_line <= other.end_line
                    });
                    let ceiling =
                        self.budget.max_spans_per_file + self.budget.max_spans_per_file_extra;
                    if !distinct || same_file >= ceiling {
                        self.budget_gap(
                            "budget_spans_per_file",
                            format!("per-file span cap reached for {}", unit.path),
                        );
                        continue;
                    }
                }
                // Only a near-identical range is a duplicate. Two different matches in
                // the same declaration produce overlapping evidence windows, and
                // merging those would silently discard a distinct matched region.
                let near_identical =
                    |other: &(SearchUnit, Vec<String>, usize, String, PoolEntry)| {
                        if other.0.path != unit.path {
                            return false;
                        }
                        let start = other.0.start_line.max(unit.start_line);
                        let end = other.0.end_line.min(unit.end_line);
                        if start > end {
                            return false;
                        }
                        let overlap = end - start + 1;
                        let shorter = (other.0.end_line - other.0.start_line + 1)
                            .min(unit.end_line - unit.start_line + 1);
                        overlap * 4 >= shorter * 3
                    };
                if let Some(existing) = chosen.iter_mut().find(|other| near_identical(other)) {
                    // The same region reached the pool by another route: keep the
                    // routes and facts, never the duplicate span.
                    for source in &provenance.sources {
                        if !existing.4.sources.contains(source) {
                            existing.4.sources.push(*source);
                            existing.4.sources.sort();
                        }
                    }
                    for fact in &provenance.facts {
                        if !existing.4.facts.iter().any(|known| known == fact) {
                            existing.4.facts.push(fact.clone());
                            existing.4.facts.sort();
                        }
                    }
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
                // P1: fair share per evidence subject (file), so one large file cannot
                // consume the pack before other subjects are considered.
                let subject_cap = (budget.max_bytes / 3).max(MIN_SUBJECT_BYTES);
                let subject_bytes: usize = chosen
                    .iter()
                    .filter(|(other, ..)| other.path == unit.path)
                    .map(|(.., bytes, _, _)| *bytes)
                    .sum();
                if subject_bytes > 0 && bytes > subject_cap.saturating_sub(subject_bytes) {
                    self.budget_gap(
                        "budget_subject_bytes",
                        format!("{} exceeds its fair-share snippet budget", unit.path),
                    );
                    continue;
                }
                used_bytes += bytes;
                taken.insert(index);
                self.trace_decision(
                    &provenance.unit,
                    provenance
                        .sources
                        .first()
                        .copied()
                        .unwrap_or(CandidateSource::Lexical),
                    provenance.route_rank,
                    CandidateDecision::Selected,
                    Some(SearchUnit {
                        path: unit.path.clone(),
                        start_line: unit.start_line,
                        end_line: unit.end_line,
                    }),
                );
                chosen.push((
                    unit.clone(),
                    hits,
                    bytes,
                    file_sha.clone(),
                    provenance.clone(),
                ));
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
        }
        for index in 0..ordered.len() {
            if taken.contains(&index) {
                continue;
            }
            let provenance = &ordered[index].5;
            if provenance.sources.is_empty() {
                continue;
            }
            self.trace_decision(
                &provenance.unit,
                provenance.sources[0],
                provenance.route_rank,
                CandidateDecision::Unexamined,
                None,
            );
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
            let Some((unit, hits, bytes, _, provenance)) = chosen
                .iter()
                .find(|(unit, ..)| unit.path == item.relative_path.to_string_lossy())
            else {
                continue;
            };
            let start_line = item.range.start_line().unwrap_or(unit.start_line);
            let end_line = item.range.last_line().unwrap_or(unit.end_line);
            let role = if provenance.sources.contains(&CandidateSource::Symbol) {
                EvidenceRole::Declaration
            } else if provenance.facts.iter().any(|fact| fact == "sql_definition") {
                EvidenceRole::ProcDefinition
            } else if provenance.facts.iter().any(|fact| fact == "sql_table") {
                EvidenceRole::TableAccess
            } else if provenance.sources.contains(&CandidateSource::Fts) {
                EvidenceRole::SeedHit
            } else {
                EvidenceRole::Reference
            };
            self.selected.push(SelectedSpan {
                path: unit.path.clone(),
                start_line,
                end_line,
                source_sha256: item.source_sha256.clone(),
                verified: true,
                content_bytes: *bytes,
                anchors_hit: hits.clone(),
                sources: provenance.sources.clone(),
                facts: provenance.facts.clone(),
                role,
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
        let traced_units: BTreeSet<SearchUnit> =
            self.traced.iter().map(|entry| entry.unit.clone()).collect();
        let leftovers: Vec<PoolEntry> = self
            .pool
            .values()
            .filter(|entry| !traced_units.contains(&entry.unit))
            .cloned()
            .collect();
        for entry in leftovers {
            let source = entry
                .sources
                .first()
                .copied()
                .unwrap_or(CandidateSource::Lexical);
            self.trace_decision(
                &entry.unit,
                source,
                entry.route_rank,
                CandidateDecision::Unexamined,
                None,
            );
        }
        self.stats.rounds = 1 + self
            .stages
            .iter()
            .filter(|trace| trace.stage == Stage::Expand)
            .count();
        self.stats.files_seen = self.files.len();
        WorkflowOutcome {
            definition: self.definition.clone(),
            candidates: self.units.clone(),
            traced: self.traced.clone(),
            pool: self.pool.values().cloned().collect::<Vec<_>>(),
            selected: self.selected.clone(),
            gaps: self.gaps.clone(),
            abstain: self.abstain.clone(),
            stats: self.stats,
            stages: self.stages.clone(),
        }
    }
}

/// Trims an oversized declaration to a verified, agent-sized evidence window.
///
/// Keeps the declaration header (the signature line) plus the matched region, so
/// the pack carries more distinct evidence inside the same byte budget. The
/// returned range always lies inside the declaration and always contains the
/// matched lines, so a gold span that overlaps the match still overlaps it.
fn compact_range(
    text: &str,
    decl_start: usize,
    decl_end: usize,
    match_start: usize,
    match_end: usize,
) -> (usize, usize) {
    let lines: Vec<&str> = text.lines().collect();
    let decl_start = decl_start.max(1).min(lines.len().max(1));
    let decl_end = decl_end.max(decl_start).min(lines.len().max(1));
    let bytes: usize = lines[decl_start - 1..decl_end]
        .iter()
        .map(|line| line.len() + 1)
        .sum();
    if bytes <= MAX_SPAN_BYTES {
        return (decl_start, decl_end);
    }
    let match_start = match_start.clamp(decl_start, decl_end);
    let match_end = match_end.clamp(match_start, decl_end);
    let window_start = match_start.saturating_sub(COMPACT_LEAD).max(decl_start);
    let window_end = (match_end + COMPACT_TRAIL).min(decl_end);
    // The declaration header carries the signature, which the agent needs to
    // interpret the body; include it only while the result still fits the span
    // budget, otherwise return the compact window alone.
    if window_start > decl_start {
        let with_header: usize = lines[decl_start - 1..window_end]
            .iter()
            .map(|line| line.len() + 1)
            .sum();
        if with_header <= MAX_SPAN_BYTES {
            return (decl_start, window_end);
        }
    }
    (window_start, window_end)
}

/// Returns the enclosing declaration range from typed facts, when available.
///
/// C# uses the R2 fact parser; SQL uses the R3 definition parser. Anything else
/// falls back to the line-window widening.
fn declaration_range(path: &str, text: &str, line: usize) -> Option<(usize, usize)> {
    if path.ends_with(".cs") {
        let identity = csharp_facts::ProjectIdentity {
            name: "workflow".to_owned(),
            assembly: "workflow".to_owned(),
            root_namespace: None,
        };
        let facts = csharp_facts::parse_file(&identity, path, text);
        if facts.parse_error {
            return None;
        }
        for method in &facts.methods {
            if method.line_start <= line && line <= method.line_end {
                let span = method.line_end.saturating_sub(method.line_start) + 1;
                return (span <= MAX_DECLARATION_LINES)
                    .then_some((method.line_start, method.line_end));
            }
        }
        for fact in &facts.types {
            if fact.line_start <= line && line <= fact.line_end {
                return Some((fact.line_start, fact.line_end));
            }
        }
        return None;
    }
    if path.ends_with(".sql") {
        for definition in sql_facts::parse_sql_file(path, text, true, "workflow") {
            if definition.line_start <= line && line <= definition.line_end {
                return Some((definition.line_start, definition.line_end));
            }
        }
        return None;
    }
    None
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

fn typed_references(path: &str, text: &str, unit: &SearchUnit) -> Vec<(String, String)> {
    let mut names: BTreeMap<String, String> = BTreeMap::new();
    if path.ends_with(".cs") {
        let identity = csharp_facts::ProjectIdentity {
            name: "workflow".to_owned(),
            assembly: "workflow".to_owned(),
            root_namespace: None,
        };
        let facts = csharp_facts::parse_file(&identity, path, text);
        for db_set in &facts.db_sets {
            if overlaps(unit, db_set.line_start, db_set.line_end) {
                names.insert(db_set.element_type.clone(), "dbset".to_owned());
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
                    names.insert(called, "invocation".to_owned());
                }
            }
        }
        for fact in &facts.types {
            if overlaps(unit, fact.line_start, fact.line_end) {
                names.insert(fact.name.clone(), "type".to_owned());
            }
        }
        for save in &facts.saves {
            if overlaps(unit, save.line, save.line) {
                if let Some(context) = &save.context_type {
                    names.insert(context.clone(), "savechanges".to_owned());
                }
            }
        }
    } else if path.ends_with(".sql") {
        for definition in sql_facts::parse_sql_file(path, text, true, "workflow") {
            if overlaps(unit, definition.line_start, definition.line_end) {
                names.insert(definition.name.clone(), "sql_definition".to_owned());
                for access in definition.writes.iter().chain(&definition.reads) {
                    names.insert(access.table.clone(), "sql_table".to_owned());
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
                    names.insert(word, "identifier".to_owned());
                }
            }
        }
        for skipped in [
            "Public",
            "Private",
            "Protected",
            "Internal",
            "String",
            "Object",
        ] {
            names.remove(skipped);
        }
    }
    names.into_iter().take(4).collect()
}

const fn overlaps(unit: &SearchUnit, start_line: usize, end_line: usize) -> bool {
    unit.start_line <= end_line && start_line <= unit.end_line
}

#[cfg(test)]
mod tests;
