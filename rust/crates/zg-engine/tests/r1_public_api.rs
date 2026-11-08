//! R1 public-API qualification from an external crate boundary.
//!
//! Uses only `zg_engine::api::evidence` public types and functions. No model,
//! index, network or source-tree mutation beyond a temporary fixture.

use std::fs;
use std::path::PathBuf;

use sha2::{Digest, Sha256};
use zg_engine::api::evidence::{
    EvidenceBudget, EvidencePack, EvidenceRequest, EvidenceStatus, assemble, revalidate,
};

#[allow(clippy::format_collect)]
fn digest(text: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    hasher
        .finalize()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn request(path: &str, text: &str) -> EvidenceRequest {
    EvidenceRequest {
        project: "r1-public-api".to_owned(),
        relative_path: PathBuf::from(path),
        expected_sha256: digest(text),
        start_byte: 0,
        end_byte: text.len(),
        expected_start_line: Some(1),
        expected_end_line: None,
    }
}

#[test]
fn public_evidence_api_round_trips_and_fails_closed() {
    let dir = tempfile::tempdir().expect("tempdir");
    let root = dir.path();
    let text = "line one\nline two\n";
    fs::write(root.join("source.txt"), text).expect("fixture");
    let budget = EvidenceBudget {
        max_bytes: 1024,
        max_items: 8,
    };
    let pack = assemble(root, &[request("source.txt", text)], budget).expect("assemble");
    assert_eq!(pack.schema_version, 1);
    assert_eq!(pack.status, EvidenceStatus::Ok);
    assert_eq!(pack.items.len(), 1);
    assert_eq!(pack.items[0].content, text);
    assert!(revalidate(root, &pack).expect("revalidate").is_empty());

    let serialized = serde_json::to_string(&pack).expect("serialize");
    let decoded: EvidencePack = serde_json::from_str(&serialized).expect("deserialize");
    assert_eq!(decoded, pack);

    fs::write(root.join("source.txt"), "changed\n").expect("mutate");
    let gaps = revalidate(root, &pack).expect("revalidate after change");
    assert!(!gaps.is_empty());

    let missing =
        assemble(root, &[request("missing.txt", "missing")], budget).expect("assemble missing");
    assert_eq!(missing.status, EvidenceStatus::Failed);
    assert!(!missing.gaps.is_empty());

    let invalid = assemble(
        root,
        &[],
        EvidenceBudget {
            max_bytes: 0,
            max_items: 0,
        },
    );
    assert!(invalid.is_err());
}
