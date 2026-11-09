//! Bounded One-Index discovery prototype types (test-only).
//!
//! Shared candidate/provenance envelope for the specialized discovery
//! strategies. The code strategy lives in `onindex_tests`; this module holds the
//! document grouping prototype used only by fixture tests. Nothing here is a
//! product path, a gate candidate, or a retrieval change.

#![allow(dead_code)]

use std::collections::BTreeMap;

/// One discovery candidate: a source range plus the evidence that produced it.
/// Code and document strategies share this envelope.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct DiscoveryCandidate {
    pub path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub score: usize,
    pub kind: DiscoveryKind,
    pub provenance: BTreeMap<String, String>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DiscoveryKind {
    Code,
    Document,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum DocumentKind {
    Markdown,
    Json,
    Csv,
    Log,
    Text,
}

impl DocumentKind {
    pub(crate) fn from_path(path: &str) -> Self {
        let lower = path.to_lowercase();
        if lower.ends_with(".md") || lower.ends_with(".markdown") {
            Self::Markdown
        } else if lower.ends_with(".json") || lower.ends_with(".jsonl") {
            Self::Json
        } else if lower.ends_with(".csv") || lower.ends_with(".tsv") {
            Self::Csv
        } else if lower.ends_with(".log") {
            Self::Log
        } else {
            Self::Text
        }
    }
}

/// Index metadata shape the real index already stores for Markdown entities.
#[derive(Clone, Debug, Default)]
pub(crate) struct DocumentSection {
    pub heading: Option<String>,
    pub scope: Option<String>,
    pub level: Option<usize>,
}

/// Group one document's matched ranges into section/key/event groups.
///
/// The strategy is bounded: it never expands beyond `max_groups` and never
/// reads outside `source`. Grouping is deterministic and order-stable.
pub(crate) fn group_document(
    path: &str,
    source: &str,
    matched_lines: &[usize],
    sections: &[DocumentSection],
    max_groups: usize,
) -> Vec<DiscoveryCandidate> {
    let kind = DocumentKind::from_path(path);
    let mut groups: BTreeMap<String, (usize, usize, BTreeMap<String, String>)> = BTreeMap::new();
    for &line in matched_lines {
        let (key, start, end, mut provenance) = match kind {
            DocumentKind::Markdown => markdown_group(source, line, sections),
            DocumentKind::Json => json_group(source, line),
            DocumentKind::Csv => csv_group(source, line),
            DocumentKind::Log => log_group(source, line),
            DocumentKind::Text => (
                "text".to_owned(),
                line,
                line,
                BTreeMap::from([("matched_by".to_owned(), "text".to_owned())]),
            ),
        };
        provenance.insert("document_kind".to_owned(), format!("{kind:?}"));
        provenance.insert("group".to_owned(), key.clone());
        let entry = groups.entry(key).or_insert((start, end, provenance));
        entry.0 = entry.0.min(start);
        entry.1 = entry.1.max(end);
    }
    let mut out: Vec<DiscoveryCandidate> = groups
        .into_iter()
        .take(max_groups)
        .map(|(_, (start, end, provenance))| DiscoveryCandidate {
            path: path.to_owned(),
            start_line: start,
            end_line: end,
            score: 100,
            kind: DiscoveryKind::Document,
            provenance,
        })
        .collect();
    out.sort_by(|left, right| {
        left.start_line
            .cmp(&right.start_line)
            .then_with(|| left.end_line.cmp(&right.end_line))
    });
    out
}

fn markdown_group(
    source: &str,
    line: usize,
    sections: &[DocumentSection],
) -> (String, usize, usize, BTreeMap<String, String>) {
    let lines: Vec<&str> = source.lines().collect();
    let index = line.saturating_sub(1).min(lines.len().saturating_sub(1));
    let mut stack: Vec<(usize, String, usize)> = Vec::new();
    for (offset, text) in lines.iter().enumerate().take(index + 1) {
        let trimmed = text.trim_start();
        if !trimmed.starts_with('#') {
            continue;
        }
        let level = trimmed.chars().take_while(|c| *c == '#').count();
        if level == 0 || level > 6 {
            continue;
        }
        let heading = trimmed[level..].trim().to_owned();
        if heading.is_empty() {
            continue;
        }
        while stack.last().is_some_and(|(open, _, _)| *open >= level) {
            stack.pop();
        }
        stack.push((level, heading, offset + 1));
    }
    // Prefer stored index metadata when it describes this range.
    if let Some(section) = sections.first()
        && (section.heading.is_some() || section.scope.is_some())
    {
        let mut path = Vec::new();
        if let Some(scope) = &section.scope {
            path.push(scope.clone());
        }
        if let Some(heading) = &section.heading {
            path.push(heading.clone());
        }
        if !path.is_empty() {
            stack = path
                .into_iter()
                .enumerate()
                .map(|(level, heading)| (level + 1, heading, index + 1))
                .collect();
        }
    }
    let heading_path = stack
        .iter()
        .map(|(_, heading, _)| heading.clone())
        .collect::<Vec<_>>()
        .join(" > ");
    let mut provenance = BTreeMap::new();
    provenance.insert("heading_path".to_owned(), heading_path.clone());
    let (level, _, heading_line) = stack.last().cloned().unwrap_or((1, String::new(), 1));
    provenance.insert("heading_level".to_owned(), level.to_string());
    let section_start = heading_line;
    let section_end = lines
        .iter()
        .enumerate()
        .skip(section_start)
        .find(|(_, text)| {
            let trimmed = text.trim_start();
            let open = trimmed.chars().take_while(|c| *c == '#').count();
            open > 0 && open <= level && trimmed.len() > open
        })
        .map_or(lines.len(), |(offset, _)| offset);
    (
        if heading_path.is_empty() {
            "document".to_owned()
        } else {
            heading_path
        },
        section_start.min(line),
        section_end.max(line),
        provenance,
    )
}

fn json_group(source: &str, line: usize) -> (String, usize, usize, BTreeMap<String, String>) {
    let mut provenance = BTreeMap::new();
    let key_path = source
        .lines()
        .nth(line.saturating_sub(1))
        .and_then(|text| {
            let quote = text.find('"')?;
            let rest = &text[quote + 1..];
            let end = rest.find('"')?;
            Some(rest[..end].to_owned())
        })
        .unwrap_or_else(|| "value".to_owned());
    provenance.insert("key_path".to_owned(), key_path.clone());
    (format!("key:{key_path}"), line, line, provenance)
}

fn csv_group(source: &str, line: usize) -> (String, usize, usize, BTreeMap<String, String>) {
    let mut lines = source.lines();
    let header = lines.next().unwrap_or_default();
    let columns: Vec<&str> = header.split(',').map(str::trim).collect();
    let row = source
        .lines()
        .nth(line.saturating_sub(1))
        .unwrap_or_default();
    let values: Vec<&str> = row.split(',').map(str::trim).collect();
    let key = values
        .first()
        .copied()
        .filter(|value| !value.is_empty())
        .unwrap_or("row");
    let mut provenance = BTreeMap::new();
    provenance.insert("columns".to_owned(), columns.join(","));
    provenance.insert("key".to_owned(), key.to_owned());
    provenance.insert("row".to_owned(), line.to_string());
    (format!("row:{key}"), line, line, provenance)
}

fn log_group(source: &str, line: usize) -> (String, usize, usize, BTreeMap<String, String>) {
    let text = source
        .lines()
        .nth(line.saturating_sub(1))
        .unwrap_or_default();
    let mut provenance = BTreeMap::new();
    let mut parts = text.split_whitespace();
    let timestamp = parts
        .next()
        .filter(|token| token.contains('-') || token.contains(':'))
        .unwrap_or("event");
    let level = parts
        .find(|token| {
            let upper = token.to_uppercase();
            ["ERROR", "WARN", "INFO", "DEBUG", "TRACE"].contains(&upper.as_str())
        })
        .unwrap_or("-")
        .to_owned();
    provenance.insert("timestamp".to_owned(), timestamp.to_owned());
    provenance.insert("level".to_owned(), level.clone());
    let event = text.split(['[', ']']).nth(1).unwrap_or("event").to_owned();
    provenance.insert("event".to_owned(), event.clone());
    (
        format!("event:{timestamp}:{level}:{event}"),
        line,
        line,
        provenance,
    )
}
