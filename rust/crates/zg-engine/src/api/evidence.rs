//! Bounded, hash-checked source evidence for externally driven retrieval.
//!
//! This is the R1 internal contract: it reuses [`ContentRange`] coordinates and
//! returns typed items plus explicit gaps. It does not search, generate text or
//! publish a new MCP/CLI response shape.

use std::collections::{HashMap, HashSet};
use std::fmt;
use std::path::{Component, Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::context::result::ContentRange;

/// Schema version for serialized evidence packs.
pub const EVIDENCE_SCHEMA_VERSION: u32 = 1;

/// Per-pack bounds. A pack never exceeds these values; overflow is an explicit gap.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct EvidenceBudget {
    pub max_bytes: usize,
    pub max_items: usize,
}

/// One requested source span.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct EvidenceRequest {
    pub project: String,
    pub relative_path: PathBuf,
    /// SHA-256 of the whole source file at discovery time.
    pub expected_sha256: String,
    /// Half-open UTF-8 byte offsets into the file.
    pub start_byte: usize,
    pub end_byte: usize,
    /// Optional locator assertions; a mismatch is `StaleCoordinates`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expected_start_line: Option<usize>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expected_end_line: Option<usize>,
}

/// One validated source span.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct EvidenceItem {
    pub project: String,
    pub relative_path: PathBuf,
    pub source_sha256: String,
    pub range: ContentRange,
    pub content: String,
}

/// Why a requested span could not be included as evidence.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceGapCode {
    InvalidProject,
    AbsolutePath,
    Traversal,
    OutOfRoot,
    Symlink,
    Missing,
    StaleHash,
    InvalidRange,
    StaleCoordinates,
    NotUtf8,
    SnippetBudgetExceeded,
    ItemBudgetExceeded,
}

/// A typed gap; never silently replaced by a different source version.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct EvidenceGap {
    pub code: EvidenceGapCode,
    pub project: String,
    pub relative_path: PathBuf,
    pub detail: String,
}

/// Pack-level disposition.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceStatus {
    Ok,
    Partial,
    Empty,
    Failed,
}

/// Bounded evidence plus explicit gaps.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct EvidencePack {
    pub schema_version: u32,
    pub status: EvidenceStatus,
    pub items: Vec<EvidenceItem>,
    pub gaps: Vec<EvidenceGap>,
    pub deduplicated: usize,
}

/// Invalid pack input (as opposed to a per-item evidence gap).
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EvidenceError {
    InvalidRoot(PathBuf),
    InvalidBudget,
}

impl fmt::Display for EvidenceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidRoot(path) => write!(
                formatter,
                "evidence root is not a directory: {}",
                path.display()
            ),
            Self::InvalidBudget => formatter.write_str("evidence budget must be non-zero"),
        }
    }
}

impl std::error::Error for EvidenceError {}

/// Validates and assembles a bounded evidence pack.
///
/// Files are read and hashed once per canonical path. Missing, changed,
/// out-of-root, symlinked or malformed sources produce gaps, never replacement
/// content. Duplicate identical requests are collapsed.
///
/// # Errors
///
/// Returns [`EvidenceError`] when the root is not a directory or the budget is
/// zero. Per-span failures are returned in [`EvidencePack::gaps`].
#[allow(clippy::too_many_lines)]
pub fn assemble(
    root: &Path,
    requests: &[EvidenceRequest],
    budget: EvidenceBudget,
) -> Result<EvidencePack, EvidenceError> {
    if budget.max_bytes == 0 || budget.max_items == 0 {
        return Err(EvidenceError::InvalidBudget);
    }
    if !root.is_dir() {
        return Err(EvidenceError::InvalidRoot(root.to_path_buf()));
    }
    let root_canonical = root
        .canonicalize()
        .map_err(|_| EvidenceError::InvalidRoot(root.to_path_buf()))?;

    let mut cache: HashMap<PathBuf, CachedSource> = HashMap::new();
    let mut seen = HashSet::new();
    let mut items = Vec::new();
    let mut gaps = Vec::new();
    let mut used_bytes = 0usize;
    let mut deduplicated = 0usize;

    for request in requests {
        let mut gap = |code: EvidenceGapCode, detail: String| {
            gaps.push(EvidenceGap {
                code,
                project: request.project.clone(),
                relative_path: request.relative_path.clone(),
                detail,
            });
        };
        if request.project.trim().is_empty() {
            gap(
                EvidenceGapCode::InvalidProject,
                "project must not be empty".into(),
            );
            continue;
        }
        let key = (
            request.relative_path.clone(),
            request.start_byte,
            request.end_byte,
            request.expected_sha256.to_ascii_lowercase(),
            request.project.clone(),
        );
        if !seen.insert(key) {
            deduplicated += 1;
            continue;
        }
        let path = match resolve_path(root, &root_canonical, &request.relative_path) {
            Ok(path) => path,
            Err(code) => {
                gap(
                    code,
                    format!("path rejected: {}", request.relative_path.display()),
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
        if source.sha256 != request.expected_sha256.to_ascii_lowercase() {
            gap(
                EvidenceGapCode::StaleHash,
                format!(
                    "expected {}, found {}",
                    request.expected_sha256, source.sha256
                ),
            );
            continue;
        }
        let Some(text) = source.text.as_deref() else {
            gap(EvidenceGapCode::NotUtf8, "source is not valid UTF-8".into());
            continue;
        };
        let range = match range_for(text, request) {
            Ok(range) => range,
            Err(code) => {
                gap(code, "requested byte range is invalid".into());
                continue;
            }
        };
        let content = &text[request.start_byte..request.end_byte];
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
        items.push(EvidenceItem {
            project: request.project.clone(),
            relative_path: request.relative_path.clone(),
            source_sha256: source.sha256.clone(),
            range,
            content: content.to_owned(),
        });
    }

    let status = match (items.is_empty(), gaps.is_empty()) {
        (true, true) => EvidenceStatus::Empty,
        (true, false) => EvidenceStatus::Failed,
        (false, true) => EvidenceStatus::Ok,
        (false, false) => EvidenceStatus::Partial,
    };
    Ok(EvidencePack {
        schema_version: EVIDENCE_SCHEMA_VERSION,
        status,
        items,
        gaps,
        deduplicated,
    })
}

/// Revalidates every item against the current source before a final answer.
///
/// Returns gaps for changed hashes, changed coordinates or changed content.
/// An empty result means every citation still matches its source.
///
/// # Errors
///
/// Returns [`EvidenceError::InvalidRoot`] when the root is not a directory.
pub fn revalidate(root: &Path, pack: &EvidencePack) -> Result<Vec<EvidenceGap>, EvidenceError> {
    if !root.is_dir() {
        return Err(EvidenceError::InvalidRoot(root.to_path_buf()));
    }
    let root_canonical = root
        .canonicalize()
        .map_err(|_| EvidenceError::InvalidRoot(root.to_path_buf()))?;
    let mut gaps = Vec::new();
    for item in &pack.items {
        if item.project.trim().is_empty() {
            gaps.push(item_gap(
                EvidenceGapCode::InvalidProject,
                item,
                "project must not be empty during revalidation",
            ));
            continue;
        }
        let path = match resolve_path(root, &root_canonical, &item.relative_path) {
            Ok(path) => path,
            Err(code) => {
                gaps.push(item_gap(code, item, "path rejected during revalidation"));
                continue;
            }
        };
        let source = match CachedSource::read(&path) {
            Ok(source) => source,
            Err(code) => {
                gaps.push(item_gap(code, item, "cannot read during revalidation"));
                continue;
            }
        };
        if source.sha256 != item.source_sha256 {
            gaps.push(item_gap(
                EvidenceGapCode::StaleHash,
                item,
                "source hash changed after assembly",
            ));
            continue;
        }
        let Some(text) = source.text.as_deref() else {
            gaps.push(item_gap(
                EvidenceGapCode::NotUtf8,
                item,
                "source is no longer valid UTF-8",
            ));
            continue;
        };
        let Some((start, end)) = range_offsets(&item.range) else {
            gaps.push(item_gap(
                EvidenceGapCode::InvalidRange,
                item,
                "citation range is not byte-addressable",
            ));
            continue;
        };
        let expected_range = match range_for_offsets(text, start, end) {
            Ok(range) => range,
            Err(_) => {
                gaps.push(item_gap(
                    EvidenceGapCode::StaleCoordinates,
                    item,
                    "citation byte offsets are invalid for the current source",
                ));
                continue;
            }
        };
        if expected_range != item.range || text.get(start..end) != Some(item.content.as_str()) {
            gaps.push(item_gap(
                EvidenceGapCode::StaleCoordinates,
                item,
                "citation coordinates or content do not match the current source",
            ));
        }
    }
    Ok(gaps)
}

fn item_gap(code: EvidenceGapCode, item: &EvidenceItem, detail: &str) -> EvidenceGap {
    EvidenceGap {
        code,
        project: item.project.clone(),
        relative_path: item.relative_path.clone(),
        detail: detail.into(),
    }
}

struct CachedSource {
    sha256: String,
    text: Option<String>,
}

impl CachedSource {
    fn read(path: &Path) -> Result<Self, EvidenceGapCode> {
        let bytes = std::fs::read(path).map_err(|error| {
            if error.kind() == std::io::ErrorKind::NotFound {
                EvidenceGapCode::Missing
            } else {
                EvidenceGapCode::OutOfRoot
            }
        })?;
        let sha256 = hex::encode(Sha256::digest(&bytes));
        let text = String::from_utf8(bytes).ok();
        Ok(Self { sha256, text })
    }
}

fn resolve_path(
    root: &Path,
    root_canonical: &Path,
    relative: &Path,
) -> Result<PathBuf, EvidenceGapCode> {
    if relative.is_absolute() {
        return Err(EvidenceGapCode::AbsolutePath);
    }
    for component in relative.components() {
        match component {
            Component::Normal(_) | Component::CurDir => {}
            Component::ParentDir => return Err(EvidenceGapCode::Traversal),
            Component::RootDir | Component::Prefix(_) => {
                return Err(EvidenceGapCode::AbsolutePath);
            }
        }
    }
    let mut current = root.to_path_buf();
    for component in relative.components() {
        if let Component::Normal(part) = component {
            current.push(part);
            let metadata = std::fs::symlink_metadata(&current).map_err(|error| {
                if error.kind() == std::io::ErrorKind::NotFound {
                    EvidenceGapCode::Missing
                } else {
                    EvidenceGapCode::OutOfRoot
                }
            })?;
            if metadata.file_type().is_symlink() {
                return Err(EvidenceGapCode::Symlink);
            }
        }
    }
    let canonical = current
        .canonicalize()
        .map_err(|_| EvidenceGapCode::Missing)?;
    if !canonical.starts_with(root_canonical) {
        return Err(EvidenceGapCode::OutOfRoot);
    }
    Ok(canonical)
}

fn range_for(text: &str, request: &EvidenceRequest) -> Result<ContentRange, EvidenceGapCode> {
    let range = range_for_offsets(text, request.start_byte, request.end_byte)?;
    let ContentRange::Text {
        start_line,
        end_line,
        ..
    } = &range
    else {
        return Err(EvidenceGapCode::InvalidRange);
    };
    let (start_line, end_line) = (*start_line, *end_line);
    if request
        .expected_start_line
        .is_some_and(|line| line != start_line)
        || request
            .expected_end_line
            .is_some_and(|line| line != end_line)
    {
        return Err(EvidenceGapCode::StaleCoordinates);
    }
    Ok(range)
}

fn range_for_offsets(
    text: &str,
    start_byte: usize,
    end_byte: usize,
) -> Result<ContentRange, EvidenceGapCode> {
    if start_byte > end_byte
        || end_byte > text.len()
        || !text.is_char_boundary(start_byte)
        || !text.is_char_boundary(end_byte)
    {
        return Err(EvidenceGapCode::InvalidRange);
    }
    let starts = line_starts(text.as_bytes());
    let (start_line, start_column) = position(&starts, start_byte);
    let (end_line, end_column) = position(&starts, end_byte);
    Ok(ContentRange::Text {
        start_line,
        end_line,
        start_byte_offset: start_byte,
        end_byte_offset: end_byte,
        start_byte_column: start_column,
        end_byte_column: end_column,
    })
}

fn line_starts(bytes: &[u8]) -> Vec<usize> {
    let mut starts = vec![0];
    for (index, byte) in bytes.iter().enumerate() {
        if *byte == b'\n' {
            starts.push(index + 1);
        }
    }
    starts
}

fn position(starts: &[usize], offset: usize) -> (usize, usize) {
    let index = starts.partition_point(|&start| start <= offset);
    let line = index.max(1);
    let line_start = starts[line - 1];
    (line, offset - line_start)
}

fn range_offsets(range: &ContentRange) -> Option<(usize, usize)> {
    match range {
        ContentRange::Text {
            start_byte_offset,
            end_byte_offset,
            ..
        } => Some((*start_byte_offset, *end_byte_offset)),
        ContentRange::File | ContentRange::Byte { .. } => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    fn digest(bytes: &[u8]) -> String {
        hex::encode(Sha256::digest(bytes))
    }

    fn request(
        project: &str,
        relative: &str,
        sha: &str,
        start: usize,
        end: usize,
    ) -> EvidenceRequest {
        EvidenceRequest {
            project: project.into(),
            relative_path: relative.into(),
            expected_sha256: sha.into(),
            start_byte: start,
            end_byte: end,
            expected_start_line: None,
            expected_end_line: None,
        }
    }

    fn budget() -> EvidenceBudget {
        EvidenceBudget {
            max_bytes: 4096,
            max_items: 16,
        }
    }

    #[test]
    fn unicode_ranges_reject_split_codepoints_and_keep_exact_bytes() {
        let dir = tempdir().unwrap();
        let text = "a😀b\n中\n";
        fs::write(dir.path().join("unicode.cs"), text).unwrap();
        let sha = digest(text.as_bytes());
        let valid = request("p", "unicode.cs", &sha, 1, 5);
        let pack = assemble(dir.path(), &[valid], budget()).unwrap();
        assert_eq!(pack.items[0].content, "😀");
        assert_eq!(pack.items[0].range.start_line(), Some(1));
        assert!(matches!(
            pack.items[0].range,
            ContentRange::Text {
                start_line: 1,
                end_line: 1,
                start_byte_offset: 1,
                end_byte_offset: 5,
                start_byte_column: 1,
                end_byte_column: 5,
            }
        ));

        let split = request("p", "unicode.cs", &sha, 2, 5);
        let pack = assemble(dir.path(), &[split], budget()).unwrap();
        assert_eq!(pack.gaps[0].code, EvidenceGapCode::InvalidRange);
    }

    #[test]
    fn crlf_and_eof_coordinates_are_deterministic() {
        let dir = tempdir().unwrap();
        let text = "one\r\ntwo\r\nthree";
        fs::write(dir.path().join("crlf.cs"), text).unwrap();
        let sha = digest(text.as_bytes());
        let mut req = request("p", "crlf.cs", &sha, 5, 8);
        req.expected_start_line = Some(2);
        req.expected_end_line = Some(2);
        let pack = assemble(dir.path(), &[req], budget()).unwrap();
        assert_eq!(pack.items[0].content, "two");
        assert!(matches!(
            pack.items[0].range,
            ContentRange::Text {
                start_line: 2,
                end_line: 2,
                start_byte_column: 0,
                end_byte_column: 3,
                ..
            }
        ));

        let eof = request("p", "crlf.cs", &sha, text.len(), text.len());
        let pack = assemble(dir.path(), &[eof], budget()).unwrap();
        assert_eq!(pack.items[0].content, "");
        assert_eq!(pack.items[0].range.start_line(), Some(3));

        let mut stale = request("p", "crlf.cs", &sha, 5, 8);
        stale.expected_start_line = Some(1);
        let pack = assemble(dir.path(), &[stale], budget()).unwrap();
        assert_eq!(pack.gaps[0].code, EvidenceGapCode::StaleCoordinates);
    }

    #[cfg(unix)]
    #[test]
    fn traversal_symlink_and_out_of_root_are_rejected() {
        let dir = tempdir().unwrap();
        let outside = tempdir().unwrap();
        fs::write(outside.path().join("secret.txt"), "secret").unwrap();
        fs::write(dir.path().join("inside.txt"), "inside").unwrap();
        std::os::unix::fs::symlink(
            outside.path().join("secret.txt"),
            dir.path().join("link.txt"),
        )
        .unwrap();
        let sha = digest(b"inside");

        let traversal = request("p", "../secret.txt", &sha, 0, 1);
        let absolute = request("p", "/etc/passwd", &sha, 0, 1);
        let symlink = request("p", "link.txt", &sha, 0, 1);
        let pack = assemble(dir.path(), &[traversal, absolute, symlink], budget()).unwrap();
        assert_eq!(pack.status, EvidenceStatus::Failed);
        let codes: Vec<_> = pack.gaps.iter().map(|gap| gap.code).collect();
        assert!(codes.contains(&EvidenceGapCode::Traversal));
        assert!(codes.contains(&EvidenceGapCode::AbsolutePath));
        assert!(codes.contains(&EvidenceGapCode::Symlink));
    }

    #[test]
    fn stale_missing_and_renamed_sources_are_gaps() {
        let dir = tempdir().unwrap();
        let file = dir.path().join("old.cs");
        fs::write(&file, "old").unwrap();
        let old_sha = digest(b"old");
        fs::rename(&file, dir.path().join("renamed.cs")).unwrap();
        let req = request("p", "old.cs", &old_sha, 0, 3);
        let pack = assemble(dir.path(), &[req], budget()).unwrap();
        assert_eq!(pack.gaps[0].code, EvidenceGapCode::Missing);

        fs::write(dir.path().join("changed.cs"), "new").unwrap();
        let req = request("p", "changed.cs", &digest(b"old"), 0, 3);
        let pack = assemble(dir.path(), &[req], budget()).unwrap();
        assert_eq!(pack.gaps[0].code, EvidenceGapCode::StaleHash);
    }

    #[test]
    fn same_relative_name_in_two_projects_stays_separate() {
        let first = tempdir().unwrap();
        let second = tempdir().unwrap();
        fs::write(first.path().join("Same.cs"), "first").unwrap();
        fs::write(second.path().join("Same.cs"), "second").unwrap();
        let a = request("project-a", "Same.cs", &digest(b"first"), 0, 5);
        let b = request("project-b", "Same.cs", &digest(b"second"), 0, 6);
        let pack_a = assemble(first.path(), &[a], budget()).unwrap();
        let pack_b = assemble(second.path(), &[b], budget()).unwrap();
        assert_eq!(pack_a.items[0].project, "project-a");
        assert_eq!(pack_a.items[0].content, "first");
        assert_eq!(pack_b.items[0].project, "project-b");
        assert_eq!(pack_b.items[0].content, "second");
    }

    #[test]
    fn budget_is_fail_closed_and_duplicates_are_deduplicated() {
        let dir = tempdir().unwrap();
        let text = "0123456789";
        fs::write(dir.path().join("budget.cs"), text).unwrap();
        let sha = digest(text.as_bytes());
        let tight = EvidenceBudget {
            max_bytes: 4,
            max_items: 4,
        };
        let pack = assemble(dir.path(), &[request("p", "budget.cs", &sha, 0, 10)], tight).unwrap();
        assert!(pack.items.is_empty());
        assert_eq!(pack.gaps[0].code, EvidenceGapCode::SnippetBudgetExceeded);

        let req = request("p", "budget.cs", &sha, 0, 4);
        let pack = assemble(
            dir.path(),
            &[req.clone(), req],
            EvidenceBudget {
                max_bytes: 4,
                max_items: 1,
            },
        )
        .unwrap();
        assert_eq!(pack.items.len(), 1);
        assert_eq!(pack.deduplicated, 1);
    }

    #[test]
    fn empty_query_is_empty_but_invalid_budget_is_an_error() {
        let dir = tempdir().unwrap();
        let empty = assemble(dir.path(), &[], budget()).unwrap();
        assert_eq!(empty.status, EvidenceStatus::Empty);
        assert!(empty.items.is_empty() && empty.gaps.is_empty());

        let error = assemble(
            dir.path(),
            &[],
            EvidenceBudget {
                max_bytes: 1,
                max_items: 0,
            },
        )
        .unwrap_err();
        assert_eq!(error, EvidenceError::InvalidBudget);
    }

    #[test]
    fn revalidation_detects_changed_source_and_content() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("live.cs");
        fs::write(&path, "before").unwrap();
        let sha = digest(b"before");
        let pack = assemble(dir.path(), &[request("p", "live.cs", &sha, 0, 6)], budget()).unwrap();
        assert!(revalidate(dir.path(), &pack).unwrap().is_empty());

        fs::write(&path, "after").unwrap();
        let gaps = revalidate(dir.path(), &pack).unwrap();
        assert_eq!(gaps[0].code, EvidenceGapCode::StaleHash);
    }

    #[test]
    fn revalidation_rejects_forged_line_and_column_coordinates() {
        let dir = tempdir().unwrap();
        let text = "first\r\nsecond\n";
        fs::write(dir.path().join("coords.cs"), text).unwrap();
        let pack = assemble(
            dir.path(),
            &[request("p", "coords.cs", &digest(text.as_bytes()), 7, 10)],
            budget(),
        )
        .unwrap();
        assert!(revalidate(dir.path(), &pack).unwrap().is_empty());

        for field in ["start_line", "end_line", "start_column", "end_column"] {
            let mut forged = pack.clone();
            match (&mut forged.items[0].range, field) {
                (ContentRange::Text { start_line, .. }, "start_line") => *start_line += 1,
                (ContentRange::Text { end_line, .. }, "end_line") => *end_line += 1,
                (
                    ContentRange::Text {
                        start_byte_column, ..
                    },
                    "start_column",
                ) => *start_byte_column += 1,
                (
                    ContentRange::Text {
                        end_byte_column, ..
                    },
                    "end_column",
                ) => *end_byte_column += 1,
                _ => unreachable!("test uses text ranges and known coordinate fields"),
            }
            let gaps = revalidate(dir.path(), &forged).unwrap();
            assert_eq!(gaps.len(), 1, "forged {field} must yield one gap");
            assert_eq!(
                gaps[0].code,
                EvidenceGapCode::StaleCoordinates,
                "forged {field} must be stale"
            );
        }
    }

    #[test]
    fn revalidation_rejects_empty_or_whitespace_project() {
        let dir = tempdir().unwrap();
        fs::write(dir.path().join("project.cs"), "source").unwrap();
        let mut pack = assemble(
            dir.path(),
            &[request("p", "project.cs", &digest(b"source"), 0, 6)],
            budget(),
        )
        .unwrap();
        pack.items[0].project = " \t ".into();
        let gaps = revalidate(dir.path(), &pack).unwrap();
        assert_eq!(gaps.len(), 1);
        assert_eq!(gaps[0].code, EvidenceGapCode::InvalidProject);
    }
}
