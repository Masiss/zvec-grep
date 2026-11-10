//! Opt-in, bounded indexing diagnostics.
//!
//! Enabled only when `ZVEC_GREP_INDEX_DIAGNOSTICS` names an output file. The
//! default path is a single `enabled()` check, so ordinary indexing is
//! unaffected. Records per-file cardinality, transient input sizes (FTS and
//! embedding bytes), reuse outcomes and stage durations that the public
//! `IndexResult` timings do not carry.

use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
    time::Duration,
};

use serde::Serialize;

const OUTPUT_ENV: &str = "ZVEC_GREP_INDEX_DIAGNOSTICS";

/// Why a canonical fragment could not reuse a stored vector.
#[derive(Clone, Debug, Default, Serialize)]
pub(crate) struct ReuseDiagnostics {
    pub reused: usize,
    pub reuse_disabled: usize,
    pub identity_unavailable: usize,
    pub manifest_absent: usize,
    pub entry_absent: usize,
    pub vector_absent: usize,
    pub vector_mismatch: usize,
}

impl ReuseDiagnostics {
    pub(crate) fn merge(&mut self, other: &Self) {
        self.reused += other.reused;
        self.reuse_disabled += other.reuse_disabled;
        self.identity_unavailable += other.identity_unavailable;
        self.manifest_absent += other.manifest_absent;
        self.entry_absent += other.entry_absent;
        self.vector_absent += other.vector_absent;
        self.vector_mismatch += other.vector_mismatch;
    }
}

/// Per-file record; all values are what this indexing pass actually consumed.
#[derive(Clone, Debug, Default, Serialize)]
pub(crate) struct FileDiagnostics {
    pub relative_path: String,
    pub file_id: u32,
    pub formats: Vec<String>,
    pub source_bytes: u64,
    pub entity_count: usize,
    pub fragment_count: usize,
    pub fts_input_bytes: usize,
    pub embedding_input_bytes: usize,
    pub entity_languages: BTreeMap<String, usize>,
    pub reuse: ReuseDiagnostics,
    pub read_micros: u64,
    pub extract_micros: u64,
    pub prepare_micros: u64,
    pub reuse_micros: u64,
}

#[derive(Default, Serialize)]
struct Report {
    summary: Summary,
    files: Vec<FileDiagnostics>,
    stages: BTreeMap<String, StageTotals>,
}

/// Aggregate of the per-file records, so callers do not have to sum them.
#[derive(Default, Serialize)]
struct Summary {
    files: usize,
    source_bytes: u64,
    entity_count: usize,
    fragment_count: usize,
    fts_input_bytes: usize,
    embedding_input_bytes: usize,
    reuse: ReuseDiagnostics,
}

#[derive(Default, Serialize)]
struct StageTotals {
    calls: u64,
    micros: u64,
}

static STATE: OnceLock<Option<Mutex<Report>>> = OnceLock::new();

fn output_path() -> Option<&'static Path> {
    static PATH: OnceLock<Option<PathBuf>> = OnceLock::new();
    PATH.get_or_init(|| {
        std::env::var_os(OUTPUT_ENV)
            .filter(|value| !value.is_empty())
            .map(PathBuf::from)
    })
    .as_deref()
}

pub(crate) fn enabled() -> bool {
    output_path().is_some()
}

fn state() -> Option<&'static Mutex<Report>> {
    STATE
        .get_or_init(|| {
            output_path().map(|_| {
                // A previous run must not mix into this one.
                let _ = std::fs::remove_file(output_path().expect("enabled output path"));
                Mutex::new(Report::default())
            })
        })
        .as_ref()
}

pub(crate) fn record_file(diagnostics: FileDiagnostics) {
    let Some(shared) = state() else {
        return;
    };
    if let Ok(mut report) = shared.lock() {
        report.files.push(diagnostics);
    }
}

pub(crate) fn record_stage(stage: &str, duration: Duration) {
    let Some(shared) = state() else {
        return;
    };
    if let Ok(mut report) = shared.lock() {
        let totals = report.stages.entry(stage.to_owned()).or_default();
        totals.calls += 1;
        totals.micros = totals.micros.saturating_add(duration_micros(duration));
    }
}

/// Writes the collected report once per pass. Failures are non-fatal.
pub(crate) fn flush() {
    let Some(shared) = state() else {
        return;
    };
    let Some(path) = output_path() else {
        return;
    };
    let mut report = {
        let Ok(mut report) = shared.lock() else {
            return;
        };
        std::mem::take(&mut *report)
    };
    report.summary = summarize(&report.files);
    let bytes = match serde_json::to_vec_pretty(&report) {
        Ok(bytes) => bytes,
        Err(error) => {
            tracing::warn!(%error, "cannot encode indexing diagnostics");
            return;
        }
    };
    if let Err(error) = crate::utils::atomic_write(path, &bytes) {
        tracing::warn!(%error, path = %path.display(), "cannot write indexing diagnostics");
    }
}

fn summarize(files: &[FileDiagnostics]) -> Summary {
    let mut summary = Summary {
        files: files.len(),
        ..Summary::default()
    };
    for file in files {
        summary.source_bytes = summary.source_bytes.saturating_add(file.source_bytes);
        summary.entity_count += file.entity_count;
        summary.fragment_count += file.fragment_count;
        summary.fts_input_bytes = summary.fts_input_bytes.saturating_add(file.fts_input_bytes);
        summary.embedding_input_bytes = summary
            .embedding_input_bytes
            .saturating_add(file.embedding_input_bytes);
        summary.reuse.merge(&file.reuse);
    }
    summary
}

/// Bytes of one embedding input, counting text payloads and image data.
pub(crate) fn content_bytes(contents: &[crate::domain::Content]) -> usize {
    contents
        .iter()
        .map(|content| match content {
            crate::domain::Content::Text(text) | crate::domain::Content::Code(text) => text.len(),
            crate::domain::Content::Image(image) => image.data().len(),
        })
        .sum()
}

fn duration_micros(duration: Duration) -> u64 {
    duration.as_micros().try_into().unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reuse_reasons_accumulate_across_files() {
        let mut total = ReuseDiagnostics {
            reused: 2,
            entry_absent: 1,
            ..ReuseDiagnostics::default()
        };
        let next = ReuseDiagnostics {
            reused: 3,
            vector_mismatch: 4,
            ..ReuseDiagnostics::default()
        };
        total.merge(&next);
        assert_eq!(total.reused, 5);
        assert_eq!(total.entry_absent, 1);
        assert_eq!(total.vector_mismatch, 4);
    }

    #[test]
    fn summary_aggregates_files_and_reuse_reasons() {
        let files = [
            FileDiagnostics {
                source_bytes: 10,
                entity_count: 2,
                fragment_count: 3,
                fts_input_bytes: 30,
                embedding_input_bytes: 40,
                reuse: ReuseDiagnostics {
                    reused: 1,
                    entry_absent: 2,
                    ..ReuseDiagnostics::default()
                },
                ..FileDiagnostics::default()
            },
            FileDiagnostics {
                source_bytes: 5,
                entity_count: 1,
                fragment_count: 1,
                fts_input_bytes: 7,
                embedding_input_bytes: 9,
                reuse: ReuseDiagnostics {
                    vector_mismatch: 1,
                    ..ReuseDiagnostics::default()
                },
                ..FileDiagnostics::default()
            },
        ];
        let summary = summarize(&files);
        assert_eq!(summary.files, 2);
        assert_eq!(summary.source_bytes, 15);
        assert_eq!(summary.entity_count, 3);
        assert_eq!(summary.fragment_count, 4);
        assert_eq!(summary.fts_input_bytes, 37);
        assert_eq!(summary.embedding_input_bytes, 49);
        assert_eq!(summary.reuse.reused, 1);
        assert_eq!(summary.reuse.entry_absent, 2);
        assert_eq!(summary.reuse.vector_mismatch, 1);
    }

    #[test]
    fn report_serializes_the_measured_fields() {
        let mut languages = BTreeMap::new();
        languages.insert("csharp".to_owned(), 3);
        let report = Report {
            summary: Summary::default(),
            files: vec![FileDiagnostics {
                relative_path: "src/Type0.cs".into(),
                file_id: 4,
                formats: vec!["csharp".into()],
                source_bytes: 42,
                entity_count: 3,
                fragment_count: 3,
                fts_input_bytes: 90,
                embedding_input_bytes: 120,
                entity_languages: languages,
                reuse: ReuseDiagnostics {
                    reused: 1,
                    ..ReuseDiagnostics::default()
                },
                read_micros: 5,
                extract_micros: 6,
                prepare_micros: 7,
                reuse_micros: 8,
            }],
            stages: BTreeMap::new(),
        };
        let json = serde_json::to_value(&report).expect("serialize");
        let file = &json["files"][0];
        assert_eq!(file["source_bytes"], 42);
        assert_eq!(file["fts_input_bytes"], 90);
        assert_eq!(file["reuse"]["reused"], 1);
        assert_eq!(file["entity_languages"]["csharp"], 3);
    }
}
