//! Fixture tests for the bounded document discovery prototype (test-only).

use super::discovery_proto::{DiscoveryKind, DocumentKind, DocumentSection, group_document};

const MARKDOWN: &str = "\
# Handbook
Intro text.

## Payroll
Payroll rules apply here.
Retry policy: retry after 3 attempts.

## Leave
Leave requests are approved by the manager.
";

const JSON_DOC: &str = r#"{
  "service": {
    "retry": {"attempts": 3, "delay": "2s"},
    "timeout": "30s"
  },
  "logging": {"level": "info"}
}"#;

const CSV_DOC: &str = "\
employee_id,name,department
42,Alice,Payroll
43,Bob,Leave
44,Carol,Payroll
";

const LOG_DOC: &str = "\
2026-11-09T10:00:00Z INFO [sync] started employee=42
2026-11-09T10:00:01Z ERROR [sync] retry failed employee=42
2026-11-09T10:00:02Z INFO [leave] approved employee=43
";

#[test]
fn markdown_hits_group_by_heading_path() {
    let groups = group_document("docs/handbook.md", MARKDOWN, &[6, 7], &[], 8);
    assert_eq!(groups.len(), 1);
    let group = &groups[0];
    assert_eq!(group.kind, DiscoveryKind::Document);
    assert_eq!(group.provenance["heading_path"], "Handbook > Payroll");
    assert_eq!(group.start_line, 4);
    assert_eq!(group.end_line, 7);
}

#[test]
fn markdown_sections_stay_separate() {
    let groups = group_document("docs/handbook.md", MARKDOWN, &[7, 11], &[], 8);
    assert_eq!(groups.len(), 2);
    assert_eq!(groups[0].provenance["heading_path"], "Handbook > Payroll");
    assert_eq!(groups[1].provenance["heading_path"], "Handbook > Leave");
}

#[test]
fn stored_section_metadata_wins() {
    let section = DocumentSection {
        heading: Some("Payroll".into()),
        scope: Some("Handbook".into()),
        level: Some(2),
    };
    let groups = group_document("docs/handbook.md", MARKDOWN, &[6], &[section], 8);
    assert_eq!(groups[0].provenance["heading_path"], "Handbook > Payroll");
    assert_eq!(groups[0].provenance["heading_level"], "2");
}

#[test]
fn json_hits_map_to_key_paths() {
    let kind = DocumentKind::from_path("config/service.json");
    assert_eq!(kind, DocumentKind::Json);
    let groups = group_document("config/service.json", JSON_DOC, &[3, 4, 8], &[], 8);
    assert!(groups.len() >= 2);
    assert_eq!(groups[0].provenance["key_path"], "retry");
    assert!(
        groups
            .iter()
            .all(|group| group.kind == DiscoveryKind::Document)
    );
}

#[test]
fn csv_hits_group_by_row_key_and_keep_columns() {
    let kind = DocumentKind::from_path("data/employees.csv");
    assert_eq!(kind, DocumentKind::Csv);
    let groups = group_document("data/employees.csv", CSV_DOC, &[2, 4], &[], 8);
    assert_eq!(groups.len(), 2);
    assert_eq!(groups[0].provenance["key"], "42");
    assert_eq!(groups[1].provenance["key"], "44");
    assert_eq!(
        groups[0].provenance["columns"],
        "employee_id,name,department"
    );
}

#[test]
fn log_hits_group_by_event() {
    let kind = DocumentKind::from_path("logs/sync.log");
    assert_eq!(kind, DocumentKind::Log);
    let groups = group_document("logs/sync.log", LOG_DOC, &[1, 2, 3], &[], 8);
    assert_eq!(groups.len(), 3);
    assert_eq!(groups[0].provenance["level"], "INFO");
    assert_eq!(groups[0].provenance["event"], "sync");
    assert_eq!(groups[1].provenance["level"], "ERROR");
    assert_eq!(groups[2].provenance["timestamp"], "2026-11-09T10:00:02Z");
}

#[test]
fn grouping_is_deterministic_and_bounded() {
    let first = group_document("logs/sync.log", LOG_DOC, &[1, 2, 3], &[], 2);
    let second = group_document("logs/sync.log", LOG_DOC, &[1, 2, 3], &[], 2);
    assert_eq!(first, second);
    assert_eq!(first.len(), 2);
}

#[test]
fn plain_text_falls_back_to_one_group() {
    let groups = group_document("notes.txt", "alpha\nbeta\ngamma\n", &[2, 3], &[], 4);
    assert_eq!(groups.len(), 1);
    assert_eq!(groups[0].start_line, 2);
    assert_eq!(groups[0].end_line, 3);
    assert_eq!(groups[0].provenance["matched_by"], "text");
}

/// Emits the fixture grouping receipt for the discovery results note.
#[test]
fn onindex_document_groups_receipt() {
    let fixtures = [
        ("docs/handbook.md", MARKDOWN, vec![7, 11]),
        ("config/service.json", JSON_DOC, vec![3, 8]),
        ("data/employees.csv", CSV_DOC, vec![2, 4]),
        ("logs/sync.log", LOG_DOC, vec![1, 2, 3]),
        ("notes.txt", "alpha\nbeta\ngamma\n", vec![2, 3]),
    ];
    let mut rows = Vec::new();
    for (path, source, lines) in fixtures {
        let groups = group_document(path, source, &lines, &[], 8);
        rows.push(serde_json::json!({
            "path": path,
            "matched_lines": lines,
            "groups": groups.iter().map(|group| serde_json::json!({
                "start_line": group.start_line,
                "end_line": group.end_line,
                "kind": format!("{:?}", group.kind),
                "provenance": group.provenance,
            })).collect::<Vec<_>>(),
        }));
    }
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "status": "ONINDEX_DOCUMENT_PROTOTYPE",
            "bounded_max_groups": 8,
            "fixtures": rows.len(),
            "rows": rows,
        }))
        .expect("json")
    );
}
