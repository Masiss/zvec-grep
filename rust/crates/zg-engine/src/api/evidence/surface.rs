//! Opt-in evidence surface: caller-addressed spans to hash-checked evidence.
//!
//! The surface is additive. It does not search, rank, index, embed or write
//! source, and it does not change any default. Callers address source spans
//! either by byte offsets or by one-based line numbers; every served item is
//! sliced from the bytes whose SHA-256 the item reports.
//!
//! A caller that supplies `expected_sha256` gets a `verified` item only when the
//! served file hash matches. Without a prior hash the item is `observed`; it is
//! never reported as verified.

use std::collections::{HashMap, HashSet};
use std::fmt;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use super::{
    CachedSource, EVIDENCE_SCHEMA_VERSION, EvidenceBudget, EvidenceError, EvidenceGap,
    EvidenceGapCode, EvidencePack, EvidenceStatus, line_starts, pack_status, range_for_offsets,
    resolve_path, revalidate,
};
use crate::api::context::result::ContentRange;

/// Schema version for serialized span packs.
pub const SPAN_SCHEMA_VERSION: u32 = EVIDENCE_SCHEMA_VERSION;

/// Hard ceiling for a requested pack, mirroring the frozen report contract.
pub const MAX_PACK_BYTES: usize = 24 * 1024;
/// Hard ceiling on items per pack, mirroring the frozen report contract.
pub const MAX_PACK_ITEMS: usize = 32;

/// How a caller addresses one source span.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(tag = "mode", rename_all = "snake_case")]
pub enum SpanAddress {
    /// Half-open UTF-8 byte offsets into the source.
    Bytes {
        /// Inclusive start byte offset.
        start: usize,
        /// Exclusive end byte offset.
        end: usize,
    },
    /// One-based inclusive line numbers, resolved against the served source.
    Lines {
        /// First cited line.
        start_line: usize,
        /// Last cited line; its trailing line terminator is excluded.
        end_line: usize,
    },
}

/// One requested span.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct SpanRequest {
    /// Non-empty project label; items keep it separate.
    pub project: String,
    /// Path relative to the evidence root.
    pub relative_path: PathBuf,
    /// Optional prior file hash. Present means the caller asserts it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expected_sha256: Option<String>,
    /// Address of the requested span.
    pub address: SpanAddress,
}

/// How strongly an item's source version is established.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SpanVerification {
    /// The caller supplied the file hash and it matched the served bytes.
    Verified,
    /// No prior hash: the hash was established while serving these bytes.
    Observed,
}

/// One served span.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct SpanItem {
    /// Project label from the request.
    pub project: String,
    /// Path relative to the evidence root.
    pub relative_path: PathBuf,
    /// SHA-256 of the file version these bytes were sliced from.
    pub source_sha256: String,
    /// Whether a caller-supplied hash had to match.
    pub verification: SpanVerification,
    /// Half-open byte range and one-based line coordinates.
    pub range: ContentRange,
    /// Exact source bytes of `range`.
    pub content: String,
}

/// Bounded evidence plus explicit gaps.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct SpanPack {
    /// Serialized schema version.
    pub schema_version: u32,
    /// Pack-level disposition.
    pub status: EvidenceStatus,
    /// Served spans in request order.
    pub items: Vec<SpanItem>,
    /// Typed reasons a requested span is absent.
    pub gaps: Vec<EvidenceGap>,
    /// Exact duplicate requests that were collapsed.
    pub deduplicated: usize,
}

/// Rejected pack input, as opposed to a per-span evidence gap.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SurfaceError {
    /// The root is not a readable directory.
    InvalidRoot(PathBuf),
    /// A budget dimension is zero.
    InvalidBudget,
    /// A budget exceeds the frozen surface ceiling.
    BudgetExceedsCap,
}

impl fmt::Display for SurfaceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidRoot(path) => {
                write!(
                    formatter,
                    "evidence root is not a directory: {}",
                    path.display()
                )
            }
            Self::InvalidBudget => formatter.write_str("evidence budget must be non-zero"),
            Self::BudgetExceedsCap => write!(
                formatter,
                "evidence budget exceeds the surface ceiling of {MAX_PACK_BYTES} bytes / {MAX_PACK_ITEMS} items"
            ),
        }
    }
}

impl std::error::Error for SurfaceError {}

impl From<EvidenceError> for SurfaceError {
    fn from(error: EvidenceError) -> Self {
        match error {
            EvidenceError::InvalidRoot(path) => Self::InvalidRoot(path),
            EvidenceError::InvalidBudget => Self::InvalidBudget,
        }
    }
}

/// Validates and assembles a bounded span pack.
///
/// Each distinct file is read once per pack, so every item is sliced from the
/// bytes whose hash it reports. Missing, changed, out-of-root, symlinked,
/// non-UTF-8 or malformed spans produce typed gaps, never replacement content.
/// Exact duplicate requests (same project, path, asserted hash and resolved
/// bytes) are collapsed and counted.
///
/// # Errors
///
/// Returns [`SurfaceError`] when the root is not a directory or the budget is
/// zero or above the surface ceiling. Per-span failures are
/// returned in [`SpanPack::gaps`].
#[allow(clippy::too_many_lines)]
pub fn assemble_spans(
    root: &Path,
    spans: &[SpanRequest],
    budget: EvidenceBudget,
) -> Result<SpanPack, SurfaceError> {
    if budget.max_bytes == 0 || budget.max_items == 0 {
        return Err(SurfaceError::InvalidBudget);
    }
    if budget.max_bytes > MAX_PACK_BYTES || budget.max_items > MAX_PACK_ITEMS {
        return Err(SurfaceError::BudgetExceedsCap);
    }
    if !root.is_dir() {
        return Err(SurfaceError::InvalidRoot(root.to_path_buf()));
    }
    let root_canonical = root
        .canonicalize()
        .map_err(|_| SurfaceError::InvalidRoot(root.to_path_buf()))?;

    let mut cache: HashMap<PathBuf, CachedSource> = HashMap::new();
    let mut seen: HashSet<(String, PathBuf, String, usize, usize)> = HashSet::new();
    let mut items: Vec<SpanItem> = Vec::new();
    let mut gaps: Vec<EvidenceGap> = Vec::new();
    let mut used_bytes = 0usize;
    let mut deduplicated = 0usize;

    for span in spans {
        let mut gap = |code: EvidenceGapCode, detail: String| {
            gaps.push(EvidenceGap {
                code,
                project: span.project.clone(),
                relative_path: span.relative_path.clone(),
                detail,
            });
        };
        if span.project.trim().is_empty() {
            gap(
                EvidenceGapCode::InvalidProject,
                "project must not be empty".into(),
            );
            continue;
        }
        let path = match resolve_path(root, &root_canonical, &span.relative_path) {
            Ok(path) => path,
            Err(code) => {
                gap(
                    code,
                    format!("path rejected: {}", span.relative_path.display()),
                );
                continue;
            }
        };
        let source = match cache.entry(path.clone()) {
            std::collections::hash_map::Entry::Occupied(entry) => entry.into_mut(),
            std::collections::hash_map::Entry::Vacant(entry) => match CachedSource::read(&path) {
                Ok(source) => entry.insert(source),
                Err(code) => {
                    gap(code, format!("cannot read: {}", path.display()));
                    continue;
                }
            },
        };
        let verification = match &span.expected_sha256 {
            Some(expected) if expected.to_ascii_lowercase() != source.sha256 => {
                gap(
                    EvidenceGapCode::StaleHash,
                    format!("expected {expected}, found {}", source.sha256),
                );
                continue;
            }
            Some(_) => SpanVerification::Verified,
            None => SpanVerification::Observed,
        };
        let Some(text) = source.text.as_deref() else {
            gap(EvidenceGapCode::NotUtf8, "source is not valid UTF-8".into());
            continue;
        };
        let (start_byte, end_byte) = match span.address {
            SpanAddress::Bytes { start, end } => (start, end),
            SpanAddress::Lines {
                start_line,
                end_line,
            } => match line_offsets(text, start_line, end_line) {
                Ok(bounds) => bounds,
                Err(code) => {
                    gap(code, "requested line range is invalid".into());
                    continue;
                }
            },
        };
        let range = match range_for_offsets(text, start_byte, end_byte) {
            Ok(range) => range,
            Err(code) => {
                gap(code, "requested byte range is invalid".into());
                continue;
            }
        };
        let key = (
            span.project.clone(),
            span.relative_path.clone(),
            source.sha256.clone(),
            start_byte,
            end_byte,
        );
        if !seen.insert(key) {
            deduplicated += 1;
            continue;
        }
        let Some(content) = text.get(start_byte..end_byte) else {
            gap(
                EvidenceGapCode::InvalidRange,
                "requested byte range is invalid".into(),
            );
            continue;
        };
        if content.len() > budget.max_bytes.saturating_sub(used_bytes) {
            gap(
                EvidenceGapCode::SnippetBudgetExceeded,
                format!("{} bytes exceeds remaining snippet budget", content.len()),
            );
            continue;
        }
        if items.len() >= budget.max_items {
            gap(
                EvidenceGapCode::ItemBudgetExceeded,
                "item budget exhausted".into(),
            );
            continue;
        }
        used_bytes += content.len();
        items.push(SpanItem {
            project: span.project.clone(),
            relative_path: span.relative_path.clone(),
            source_sha256: source.sha256.clone(),
            verification,
            range,
            content: content.to_owned(),
        });
    }

    let status = pack_status(items.is_empty(), gaps.is_empty());
    Ok(SpanPack {
        schema_version: SPAN_SCHEMA_VERSION,
        status,
        items,
        gaps,
        deduplicated,
    })
}

/// Resolves one-based inclusive line numbers to half-open byte offsets.
///
/// The trailing line terminator of `end_line` is excluded, matching cited
/// source text rather than file layout. CRLF and a missing final newline are
/// handled deterministically.
fn line_offsets(
    text: &str,
    start_line: usize,
    end_line: usize,
) -> Result<(usize, usize), EvidenceGapCode> {
    if start_line == 0 || end_line < start_line {
        return Err(EvidenceGapCode::InvalidRange);
    }
    let line_count = text.split_inclusive('\n').count();
    if end_line > line_count {
        return Err(EvidenceGapCode::InvalidRange);
    }
    let starts = line_starts(text.as_bytes());
    let start_byte = starts[start_line - 1];
    let mut end_byte = starts.get(end_line).copied().unwrap_or(text.len());
    if text.as_bytes().get(end_byte.wrapping_sub(1)) == Some(&b'\n') {
        end_byte = end_byte.saturating_sub(1);
        if text.as_bytes().get(end_byte.wrapping_sub(1)) == Some(&b'\r') {
            end_byte = end_byte.saturating_sub(1);
        }
    }
    if end_byte < start_byte {
        return Err(EvidenceGapCode::InvalidRange);
    }
    Ok((start_byte, end_byte))
}

/// Revalidates every served span against the current source.
///
/// Returns gaps for changed hashes or changed coordinates. An empty result
/// means every citation still matches the source version it was sliced from;
/// `observed` items are revalidated exactly like `verified` items.
///
/// # Errors
///
/// Returns [`SurfaceError::InvalidRoot`] when the root is not a directory.
pub fn revalidate_spans(root: &Path, pack: &SpanPack) -> Result<Vec<EvidenceGap>, SurfaceError> {
    let evidence = EvidencePack {
        schema_version: EVIDENCE_SCHEMA_VERSION,
        status: pack.status,
        items: pack
            .items
            .iter()
            .map(|item| super::EvidenceItem {
                project: item.project.clone(),
                relative_path: item.relative_path.clone(),
                source_sha256: item.source_sha256.clone(),
                range: item.range.clone(),
                content: item.content.clone(),
            })
            .collect(),
        gaps: Vec::new(),
        deduplicated: 0,
    };
    Ok(revalidate(root, &evidence)?)
}

#[cfg(test)]
mod tests;
