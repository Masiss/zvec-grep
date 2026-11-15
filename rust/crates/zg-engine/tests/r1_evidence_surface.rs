//! Contract and negative-control suite for the opt-in R1 evidence surface.
//!
//! Read-only: every test runs against a temporary directory. No index, model,
//! source corpus or held-out case is touched.

use std::fs;
use std::os::unix::fs::symlink;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};
use tempfile::tempdir;
use zg_engine::api::evidence::surface::{
    MAX_PACK_BYTES, MAX_PACK_ITEMS, SPAN_SCHEMA_VERSION, SpanAddress, SpanPack, SpanRequest,
    SpanVerification, SurfaceError, assemble_spans, revalidate_spans,
};
use zg_engine::api::evidence::{EvidenceBudget, EvidenceGapCode, EvidenceStatus};

fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn budget(max_bytes: usize, max_items: usize) -> EvidenceBudget {
    EvidenceBudget {
        max_bytes,
        max_items,
    }
}

fn pack_budget() -> EvidenceBudget {
    budget(16 * 1024, 16)
}

fn bytes_span(
    project: &str,
    path: &str,
    sha: Option<&str>,
    start: usize,
    end: usize,
) -> SpanRequest {
    SpanRequest {
        project: project.into(),
        relative_path: PathBuf::from(path),
        expected_sha256: sha.map(ToOwned::to_owned),
        address: SpanAddress::Bytes { start, end },
    }
}

fn lines_span(
    project: &str,
    path: &str,
    sha: Option<&str>,
    start_line: usize,
    end_line: usize,
) -> SpanRequest {
    SpanRequest {
        project: project.into(),
        relative_path: PathBuf::from(path),
        expected_sha256: sha.map(ToOwned::to_owned),
        address: SpanAddress::Lines {
            start_line,
            end_line,
        },
    }
}

fn write(root: &Path, relative: &str, contents: &[u8]) {
    let path = root.join(relative);
    fs::create_dir_all(path.parent().expect("fixture parent")).expect("fixture directory");
    fs::write(&path, contents).expect("fixture file");
}

fn assemble(root: &Path, spans: &[SpanRequest]) -> SpanPack {
    assemble_spans(root, spans, pack_budget()).expect("valid pack request")
}

fn gap_codes(pack: &SpanPack) -> Vec<EvidenceGapCode> {
    pack.gaps.iter().map(|gap| gap.code).collect()
}

#[test]
fn verified_span_serves_exact_bytes_and_revalidates_clean() {
    let dir = tempdir().expect("valid evidence fixture");
    let source = b"fn a() {\n    one();\n}\n";
    write(dir.path(), "src/a.rs", source);
    let sha = digest(source);
    let pack = assemble(dir.path(), &[lines_span("p", "src/a.rs", Some(&sha), 1, 2)]);
    assert_eq!(pack.status, EvidenceStatus::Ok);
    assert_eq!(pack.schema_version, SPAN_SCHEMA_VERSION);
    assert_eq!(pack.items.len(), 1);
    let item = &pack.items[0];
    assert_eq!(item.verification, SpanVerification::Verified);
    assert_eq!(item.source_sha256, sha);
    assert_eq!(item.content, "fn a() {\n    one();");
    assert!(pack.gaps.is_empty());
    assert_eq!(
        revalidate_spans(dir.path(), &pack).expect("valid result"),
        Vec::new()
    );
}

#[test]
fn observed_span_is_never_reported_as_verified() {
    let dir = tempdir().expect("valid evidence fixture");
    let source = b"alpha\nbeta\n";
    write(dir.path(), "doc.txt", source);
    let pack = assemble(dir.path(), &[lines_span("p", "doc.txt", None, 2, 2)]);
    assert_eq!(pack.status, EvidenceStatus::Ok);
    let item = &pack.items[0];
    assert_eq!(item.verification, SpanVerification::Observed);
    assert_eq!(item.source_sha256, digest(source));
    assert_eq!(item.content, "beta");
    assert_eq!(
        revalidate_spans(dir.path(), &pack).expect("valid result"),
        Vec::new()
    );
}

#[test]
fn stale_hash_is_a_gap_and_never_substitutes_content() {
    let dir = tempdir().expect("valid evidence fixture");
    write(dir.path(), "a.cs", b"class A {}\n");
    let stale = digest(b"class Old {}\n");
    let pack = assemble(dir.path(), &[bytes_span("p", "a.cs", Some(&stale), 0, 11)]);
    assert_eq!(pack.status, EvidenceStatus::Failed);
    assert!(pack.items.is_empty(), "stale content must not be served");
    assert_eq!(gap_codes(&pack), vec![EvidenceGapCode::StaleHash]);
}

#[test]
fn missing_renamed_and_empty_project_are_typed_gaps() {
    let dir = tempdir().expect("valid evidence fixture");
    write(dir.path(), "a.cs", b"class A {}\n");
    let pack = assemble(
        dir.path(),
        &[
            bytes_span("p", "missing.cs", None, 0, 1),
            bytes_span("  ", "a.cs", None, 0, 1),
        ],
    );
    assert_eq!(pack.status, EvidenceStatus::Failed);
    assert_eq!(
        gap_codes(&pack),
        vec![EvidenceGapCode::Missing, EvidenceGapCode::InvalidProject]
    );
}

#[test]
fn path_escapes_are_rejected_before_reading() {
    let dir = tempdir().expect("valid evidence fixture");
    let outside = tempdir().expect("valid evidence fixture");
    fs::write(outside.path().join("secret.txt"), b"secret\n").expect("valid result");
    write(dir.path(), "inner/a.txt", b"inner\n");
    symlink(
        outside.path().join("secret.txt"),
        dir.path().join("link.txt"),
    )
    .expect("valid span request");
    symlink(outside.path(), dir.path().join("linkdir")).expect("valid result");

    let pack = assemble(
        dir.path(),
        &[
            bytes_span("p", "../secret.txt", None, 0, 1),
            bytes_span("p", "/etc/passwd", None, 0, 1),
            bytes_span("p", "link.txt", None, 0, 1),
            bytes_span("p", "linkdir/secret.txt", None, 0, 1),
        ],
    );
    assert_eq!(pack.status, EvidenceStatus::Failed);
    assert_eq!(
        gap_codes(&pack),
        vec![
            EvidenceGapCode::Traversal,
            EvidenceGapCode::AbsolutePath,
            EvidenceGapCode::Symlink,
            EvidenceGapCode::Symlink,
        ]
    );
    assert!(pack.items.is_empty());
}

#[test]
fn forged_and_split_ranges_are_rejected() {
    let dir = tempdir().expect("valid evidence fixture");
    write(dir.path(), "u.txt", "héllo\n".as_bytes());
    let pack = assemble(
        dir.path(),
        &[
            bytes_span("p", "u.txt", None, 0, 999),
            bytes_span("p", "u.txt", None, 5, 2),
            // 2 is inside the two-byte encoding of 'é'.
            bytes_span("p", "u.txt", None, 2, 3),
        ],
    );
    assert_eq!(pack.status, EvidenceStatus::Failed);
    assert_eq!(
        gap_codes(&pack),
        vec![
            EvidenceGapCode::InvalidRange,
            EvidenceGapCode::InvalidRange,
            EvidenceGapCode::InvalidRange,
        ]
    );
}

#[test]
fn line_addressing_rejects_zero_reversed_and_past_eof() {
    let dir = tempdir().expect("valid evidence fixture");
    write(dir.path(), "l.txt", b"one\ntwo\n");
    let pack = assemble(
        dir.path(),
        &[
            lines_span("p", "l.txt", None, 0, 1),
            lines_span("p", "l.txt", None, 2, 1),
            lines_span("p", "l.txt", None, 1, 3),
        ],
    );
    assert_eq!(pack.status, EvidenceStatus::Failed);
    assert_eq!(
        gap_codes(&pack),
        vec![
            EvidenceGapCode::InvalidRange,
            EvidenceGapCode::InvalidRange,
            EvidenceGapCode::InvalidRange,
        ]
    );
}

#[test]
fn non_utf8_source_is_a_gap() {
    let dir = tempdir().expect("valid evidence fixture");
    write(dir.path(), "bin.dat", &[0xff, 0xfe, 0x00]);
    let pack = assemble(dir.path(), &[bytes_span("p", "bin.dat", None, 0, 1)]);
    assert_eq!(pack.status, EvidenceStatus::Failed);
    assert_eq!(gap_codes(&pack), vec![EvidenceGapCode::NotUtf8]);
}

#[test]
fn budget_overflow_is_an_explicit_gap() {
    let dir = tempdir().expect("valid evidence fixture");
    write(dir.path(), "a.txt", b"0123456789\n");
    write(dir.path(), "b.txt", b"0123456789\n");
    let pack = assemble_spans(
        dir.path(),
        &[
            bytes_span("p", "a.txt", None, 0, 10),
            bytes_span("p", "b.txt", None, 0, 10),
        ],
        budget(10, 8),
    )
    .expect("valid span request");
    assert_eq!(pack.status, EvidenceStatus::Partial);
    assert_eq!(pack.items.len(), 1);
    assert_eq!(
        gap_codes(&pack),
        vec![EvidenceGapCode::SnippetBudgetExceeded]
    );

    let pack = assemble_spans(
        dir.path(),
        &[
            bytes_span("p", "a.txt", None, 0, 10),
            bytes_span("p", "b.txt", None, 0, 10),
        ],
        budget(4096, 1),
    )
    .expect("valid span request");
    assert_eq!(pack.items.len(), 1);
    assert_eq!(gap_codes(&pack), vec![EvidenceGapCode::ItemBudgetExceeded]);
}

#[test]
fn budget_above_the_surface_ceiling_is_rejected() {
    let dir = tempdir().expect("valid evidence fixture");
    assert_eq!(
        assemble_spans(dir.path(), &[], budget(MAX_PACK_BYTES + 1, 1)),
        Err(SurfaceError::BudgetExceedsCap)
    );
    assert_eq!(
        assemble_spans(dir.path(), &[], budget(1, MAX_PACK_ITEMS + 1)),
        Err(SurfaceError::BudgetExceedsCap)
    );
    assert_eq!(
        assemble_spans(dir.path(), &[], budget(0, 1)),
        Err(SurfaceError::InvalidBudget)
    );
    assert_eq!(
        assemble_spans(&dir.path().join("nope"), &[], pack_budget()),
        Err(SurfaceError::InvalidRoot(dir.path().join("nope")))
    );
}

#[test]
fn duplicate_requests_are_collapsed_and_counted() {
    let dir = tempdir().expect("valid evidence fixture");
    let source = b"alpha\nbeta\n";
    write(dir.path(), "d.txt", source);
    let sha = digest(source);
    let pack = assemble(
        dir.path(),
        &[
            lines_span("p", "d.txt", Some(&sha), 1, 2),
            // Same resolved bytes through byte addressing.
            bytes_span("p", "d.txt", Some(&sha), 0, 10),
            lines_span("p", "d.txt", Some(&sha), 1, 2),
        ],
    );
    assert_eq!(pack.items.len(), 1);
    assert_eq!(pack.deduplicated, 2);
    assert!(pack.gaps.is_empty());
}

#[test]
fn empty_request_is_empty_not_failed() {
    let dir = tempdir().expect("valid evidence fixture");
    let pack = assemble(dir.path(), &[]);
    assert_eq!(pack.status, EvidenceStatus::Empty);
    assert!(pack.items.is_empty() && pack.gaps.is_empty());
}

#[test]
fn same_relative_name_in_two_projects_stays_separate() {
    let dir = tempdir().expect("valid evidence fixture");
    write(dir.path(), "p1/a.txt", b"one\n");
    write(dir.path(), "p2/a.txt", b"two\n");
    let pack = assemble(
        dir.path(),
        &[
            lines_span("p1", "p1/a.txt", None, 1, 1),
            lines_span("p2", "p2/a.txt", None, 1, 1),
        ],
    );
    assert_eq!(pack.items.len(), 2);
    assert_eq!(pack.items[0].project, "p1");
    assert_eq!(pack.items[0].content, "one");
    assert_eq!(pack.items[1].content, "two");
}

#[test]
fn provenance_invariant_content_belongs_to_the_reported_hash() {
    let dir = tempdir().expect("valid evidence fixture");
    let source = b"// header\nfn target() {}\n// tail\n";
    write(dir.path(), "src/t.rs", source);
    let pack = assemble(dir.path(), &[lines_span("p", "src/t.rs", None, 2, 2)]);
    let item = &pack.items[0];
    let file = fs::read(dir.path().join("src/t.rs")).expect("valid result");
    assert_eq!(item.source_sha256, digest(&file));
    match item.range {
        zg_engine::api::context::result::ContentRange::Text {
            start_byte_offset,
            end_byte_offset,
            ..
        } => {
            assert_eq!(
                item.content.as_bytes(),
                &file[start_byte_offset..end_byte_offset]
            );
            assert_eq!(
                item.content,
                std::str::from_utf8(&file[start_byte_offset..end_byte_offset])
                    .expect("valid result")
            );
        }
        _ => panic!("span ranges must be byte addressable"),
    }
}

#[test]
fn revalidation_detects_every_source_mutation() {
    let dir = tempdir().expect("valid evidence fixture");
    let original = b"alpha\nbeta\ngamma\n";
    write(dir.path(), "m.txt", original);
    let pack = assemble(dir.path(), &[lines_span("p", "m.txt", None, 1, 2)]);
    assert_eq!(
        revalidate_spans(dir.path(), &pack).expect("valid result"),
        Vec::new()
    );

    fs::write(dir.path().join("m.txt"), b"alpha\nbeta\ngamma!\n").expect("valid result");
    let gaps = revalidate_spans(dir.path(), &pack).expect("valid result");
    assert_eq!(
        gaps.iter().map(|gap| gap.code).collect::<Vec<_>>(),
        vec![EvidenceGapCode::StaleHash]
    );

    fs::remove_file(dir.path().join("m.txt")).expect("valid result");
    let gaps = revalidate_spans(dir.path(), &pack).expect("valid result");
    assert_eq!(
        gaps.iter().map(|gap| gap.code).collect::<Vec<_>>(),
        vec![EvidenceGapCode::Missing]
    );
}

#[test]
fn pack_round_trips_through_schema_v1_json() {
    let dir = tempdir().expect("valid evidence fixture");
    write(dir.path(), "a.txt", b"one\ntwo\n");
    let pack = assemble(dir.path(), &[lines_span("p", "a.txt", None, 2, 2)]);
    let json = serde_json::to_string(&pack).expect("serializable pack");
    let parsed: SpanPack = serde_json::from_str(&json).expect("valid pack json");
    assert_eq!(parsed, pack);
    let value: serde_json::Value = serde_json::from_str(&json).expect("valid pack json");
    assert_eq!(value["schema_version"], 1);
    assert_eq!(value["status"], "ok");
    assert_eq!(value["items"][0]["verification"], "observed");
    assert_eq!(
        value["items"][0]["source_sha256"],
        pack.items[0].source_sha256
    );
}

#[test]
fn span_request_deserializes_both_address_modes() {
    let bytes: SpanRequest = serde_json::from_str(
        r#"{"project":"p","relative_path":"a.rs","address":{"mode":"bytes","start":0,"end":4}}"#,
    )
    .expect("valid span request");
    assert_eq!(bytes.expected_sha256, None);
    assert_eq!(bytes.address, SpanAddress::Bytes { start: 0, end: 4 });

    let lines: SpanRequest = serde_json::from_str(
        r#"{"project":"p","relative_path":"a.rs","expected_sha256":"AA","address":{"mode":"lines","start_line":2,"end_line":3}}"#,
    )
    .expect("valid span request");
    assert_eq!(lines.expected_sha256.as_deref(), Some("AA"));
    assert_eq!(
        lines.address,
        SpanAddress::Lines {
            start_line: 2,
            end_line: 3,
        }
    );
}
