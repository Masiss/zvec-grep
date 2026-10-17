//! Runnable native-Zvec proof for a versioned base + delta read path.
//!
//! This is a research integration test, not production storage code. It creates
//! two real zvec collections, writes/optimizes them independently, performs
//! vector and FTS queries against both, applies generation/tombstone filtering,
//! and exercises WAL recovery before manifest publication.

#![allow(
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss,
    clippy::explicit_iter_loop,
    clippy::needless_pass_by_value,
    clippy::too_many_lines
)]

use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::{self, BufRead, BufReader, Write},
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use tempfile::TempDir;
use zvec_rust::{
    Collection, CollectionSchema, ConfigBuilder, DataType, Doc, Fts, IndexParams, MetricType,
    SearchQuery, initialize, shutdown,
};

const DIMENSION: u32 = 4;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
struct Manifest {
    generation: u64,
    base: String,
    deltas: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
enum WalRecord {
    DeltaPublished { segment: String, generation: u64 },
}

#[derive(Clone, Debug)]
struct Row {
    logical_id: String,
    generation: u64,
    deleted: bool,
}

#[derive(Clone, Debug)]
struct Hit {
    row: Row,
    score: f64,
}

struct NativeIndex {
    base: Collection,
    delta: Collection,
}

fn schema() -> CollectionSchema {
    let mut schema = CollectionSchema::builder("native_base_delta");
    schema = schema.add_field(
        zvec_rust::FieldSchema::new("logical_id", DataType::String, false, 0)
            .expect("logical id schema"),
    );
    schema = schema.add_field(
        zvec_rust::FieldSchema::new("generation", DataType::Uint64, false, 0)
            .expect("generation schema"),
    );
    schema = schema.add_field(
        zvec_rust::FieldSchema::new("deleted", DataType::Bool, false, 0).expect("deleted schema"),
    );

    let mut text =
        zvec_rust::FieldSchema::new("text", DataType::String, false, 0).expect("text schema");
    text.set_index_params(
        &IndexParams::fts(Some("jieba"), Some(&["lowercase"]), None).expect("fts params"),
    )
    .expect("attach fts index");
    schema = schema.add_field(text);

    schema
        .add_vector_field(
            "embedding",
            DataType::VectorFp32,
            DIMENSION,
            IndexParams::hnsw(MetricType::L2, 16, 100).expect("hnsw params"),
        )
        .build()
        .expect("native base+delta schema")
}

fn row_doc(
    logical_id: &str,
    generation: u64,
    deleted: bool,
    text: &str,
    vector: [f32; DIMENSION as usize],
) -> Doc {
    let mut doc = Doc::new().expect("document");
    doc.set_pk(&format!("{logical_id}@{generation}"));
    doc.add_string("logical_id", logical_id)
        .expect("logical id");
    doc.add_u64("generation", generation).expect("generation");
    doc.add_bool("deleted", deleted).expect("deleted");
    doc.add_string("text", text).expect("text");
    doc.add_vector_f32("embedding", &vector).expect("embedding");
    doc
}

fn create_collection(path: &Path) -> Collection {
    let schema = schema();
    Collection::create_and_open(path.to_str().expect("native path is utf-8"), &schema, None)
        .expect("create native collection")
}

fn open_collection(path: &Path) -> Collection {
    Collection::open(path.to_str().expect("native path is utf-8"), None)
        .expect("open native collection")
}

fn insert_rows(collection: &Collection, rows: Vec<Doc>) {
    let references = rows.iter().collect::<Vec<_>>();
    let result = collection.insert(&references).expect("insert native rows");
    assert_eq!(
        result.error_count, 0,
        "native insert errors: {:?}",
        result.results
    );
}

fn row_from_doc(doc: &Doc) -> Row {
    Row {
        logical_id: doc
            .get_string("logical_id")
            .expect("logical id field")
            .expect("logical id value"),
        generation: doc
            .get_u64("generation")
            .expect("generation field")
            .expect("generation value"),
        deleted: doc
            .get_bool("deleted")
            .expect("deleted field")
            .expect("deleted value"),
    }
}

fn all_rows(collection: &Collection) -> Vec<Row> {
    collection
        .iter_with_options(Some(&["logical_id", "generation", "deleted"]), false)
        .expect("native iterator")
        .map(|doc| row_from_doc(&doc.expect("iterate native row")))
        .collect()
}

fn latest_rows(index: &NativeIndex) -> BTreeMap<String, Row> {
    let mut latest = BTreeMap::new();
    for row in all_rows(&index.base)
        .into_iter()
        .chain(all_rows(&index.delta))
    {
        let replace = latest
            .get(&row.logical_id)
            .is_none_or(|current: &Row| current.generation < row.generation);
        if replace {
            latest.insert(row.logical_id.clone(), row);
        }
    }
    latest
}

fn vector_query(collection: &Collection, vector: &[f32], topk: usize) -> Vec<(Row, usize)> {
    let topk = i32::try_from(topk).expect("top-k fits native integer");
    let mut query = SearchQuery::new("embedding", vector, topk).expect("vector query");
    query
        .set_output_fields(&["logical_id", "generation", "deleted"])
        .expect("vector output fields");
    collection
        .query(&query)
        .expect("native vector query")
        .into_iter()
        .enumerate()
        .map(|(rank, doc)| (row_from_doc(&doc), rank + 1))
        .collect()
}

fn fts_query(collection: &Collection, text: &str, topk: usize) -> Vec<(Row, usize)> {
    let mut fts = Fts::new().expect("fts payload");
    fts.set_match_string(text).expect("fts text");
    let topk = i32::try_from(topk).expect("top-k fits native integer");
    let mut query = SearchQuery::fts("text", &fts, topk).expect("fts query");
    query
        .set_output_fields(&["logical_id", "generation", "deleted"])
        .expect("fts output fields");
    collection
        .query(&query)
        .expect("native fts query")
        .into_iter()
        .enumerate()
        .map(|(rank, doc)| (row_from_doc(&doc), rank + 1))
        .collect()
}

fn merge_with_refill<F>(
    index: &NativeIndex,
    limit: usize,
    initial_probe: usize,
    query: F,
) -> Vec<Hit>
where
    F: Fn(&Collection, usize) -> Vec<(Row, usize)>,
{
    let latest = latest_rows(index);
    let mut merged = BTreeMap::<String, Hit>::new();
    for collection in [&index.base, &index.delta] {
        let mut probe = initial_probe.max(1);
        loop {
            let candidates = query(collection, probe);
            let mut visible = 0;
            for (row, rank) in candidates.iter() {
                let Some(current) = latest.get(&row.logical_id) else {
                    continue;
                };
                if current.generation != row.generation || row.deleted {
                    continue;
                }
                visible += 1;
                let hit = Hit {
                    row: row.clone(),
                    // Rank fusion avoids comparing independent native index
                    // score scales (especially BM25 across base and delta).
                    score: 1.0 / (60.0 + *rank as f64),
                };
                merged
                    .entry(row.logical_id.clone())
                    .and_modify(|existing| {
                        existing.score = existing.score.max(hit.score);
                    })
                    .or_insert(hit);
                if visible == limit {
                    break;
                }
            }
            if visible >= limit || candidates.len() < probe {
                break;
            }
            let next = probe.saturating_mul(2).max(probe + 1);
            if next == probe {
                break;
            }
            probe = next;
        }
    }

    let mut hits = merged.into_values().collect::<Vec<_>>();
    hits.sort_by(|left, right| {
        right
            .score
            .total_cmp(&left.score)
            .then_with(|| left.row.logical_id.cmp(&right.row.logical_id))
    });
    hits.truncate(limit);
    hits
}

fn hybrid_search(index: &NativeIndex, vector: &[f32], text: &str, limit: usize) -> Vec<Hit> {
    let vector_hits = merge_with_refill(index, limit, limit, |collection, probe| {
        vector_query(collection, vector, probe)
    });
    let text_hits = merge_with_refill(index, limit, limit, |collection, probe| {
        fts_query(collection, text, probe)
    });
    let mut fused = BTreeMap::<String, Hit>::new();
    for (rank, hit) in vector_hits.into_iter().enumerate() {
        let contribution = 1.0 / (60.0 + (rank + 1) as f64);
        fused
            .entry(hit.row.logical_id.clone())
            .and_modify(|existing| existing.score += contribution)
            .or_insert(Hit {
                row: hit.row,
                score: contribution,
            });
    }
    for (rank, hit) in text_hits.into_iter().enumerate() {
        let contribution = 1.0 / (60.0 + (rank + 1) as f64);
        fused
            .entry(hit.row.logical_id.clone())
            .and_modify(|existing| existing.score += contribution)
            .or_insert(Hit {
                row: hit.row,
                score: contribution,
            });
    }
    let mut hits = fused.into_values().collect::<Vec<_>>();
    hits.sort_by(|left, right| right.score.total_cmp(&left.score));
    hits.truncate(limit);
    hits
}

fn manifest_path(root: &Path) -> PathBuf {
    root.join("manifest.json")
}

fn wal_path(root: &Path) -> PathBuf {
    root.join("wal.jsonl")
}

fn segment_path(root: &Path, name: &str) -> PathBuf {
    root.join(name)
}

fn publish_manifest(root: &Path, manifest: &Manifest) -> io::Result<()> {
    for name in std::iter::once(&manifest.base).chain(manifest.deltas.iter()) {
        if !segment_path(root, name).is_dir() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!("missing native segment {name}"),
            ));
        }
    }
    let temporary = root.join("manifest.json.tmp");
    let bytes = serde_json::to_vec_pretty(manifest).map_err(io::Error::other)?;
    let mut file = fs::File::create(&temporary)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    fs::rename(temporary, manifest_path(root))?;
    Ok(())
}

fn append_wal(root: &Path, record: &WalRecord) -> io::Result<()> {
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(wal_path(root))?;
    serde_json::to_writer(&mut file, record).map_err(io::Error::other)?;
    file.write_all(b"\n")?;
    file.sync_all()
}

fn recover_manifest(root: &Path) -> io::Result<Manifest> {
    let mut manifest: Manifest =
        serde_json::from_slice(&fs::read(manifest_path(root))?).map_err(io::Error::other)?;
    if let Ok(file) = fs::File::open(wal_path(root)) {
        for line in BufReader::new(file).lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }
            let WalRecord::DeltaPublished {
                segment,
                generation,
            } = serde_json::from_str(&line).map_err(io::Error::other)?;
            if generation > manifest.generation {
                manifest.generation = generation;
                if !manifest.deltas.contains(&segment) {
                    manifest.deltas.push(segment);
                }
            }
        }
    }
    publish_manifest(root, &manifest)?;
    Ok(manifest)
}

fn open_index(root: &Path, manifest: &Manifest) -> NativeIndex {
    assert_eq!(manifest.deltas.len(), 1, "proof expects one delta");
    NativeIndex {
        base: open_collection(&segment_path(root, &manifest.base)),
        delta: open_collection(&segment_path(root, &manifest.deltas[0])),
    }
}

#[test]
fn native_base_delta_proof_uses_real_collections_and_recovers_after_wal_only_publish() {
    let temporary = TempDir::new().expect("temporary native proof directory");
    let root = temporary.path();
    fs::create_dir_all(root).expect("proof root");

    let config = ConfigBuilder::new()
        .num_threads(1)
        .enable_console_log(false)
        .build();
    initialize(Some(&config)).expect("initialize native zvec");

    let base_path = segment_path(root, "base");
    let delta_path = segment_path(root, "delta-2");
    let base = create_collection(&base_path);
    insert_rows(
        &base,
        vec![
            row_doc("old", 1, false, "alpha base", [1.0, 0.0, 0.0, 0.0]),
            row_doc("fallback", 1, false, "fallback text", [0.9, 0.1, 0.0, 0.0]),
            row_doc("gone", 1, false, "delete me", [0.0, 1.0, 0.0, 0.0]),
            row_doc(
                "lexical",
                1,
                false,
                "rare needle base",
                [0.0, 0.0, 1.0, 0.0],
            ),
        ],
    );
    base.flush().expect("flush native base");
    base.optimize().expect("optimize native base");
    assert_eq!(base.stats().expect("base stats").doc_count, 4);

    publish_manifest(
        root,
        &Manifest {
            generation: 1,
            base: "base".into(),
            deltas: Vec::new(),
        },
    )
    .expect("publish base manifest");

    let delta = create_collection(&delta_path);
    insert_rows(
        &delta,
        vec![
            row_doc(
                "old",
                2,
                false,
                "updated needle delta",
                [0.0, 1.0, 0.0, 0.0],
            ),
            row_doc("gone", 2, true, "", [0.0, 1.0, 0.0, 0.0]),
        ],
    );
    delta.flush().expect("flush native delta");
    delta.optimize().expect("optimize native delta");
    assert_eq!(delta.stats().expect("delta stats").doc_count, 2);

    // Simulate a crash after the durable delta and WAL, before manifest rename.
    append_wal(
        root,
        &WalRecord::DeltaPublished {
            segment: "delta-2".into(),
            generation: 2,
        },
    )
    .expect("append acknowledged delta WAL");
    drop(base);
    drop(delta);

    let recovered_manifest = recover_manifest(root).expect("recover WAL-only delta");
    assert_eq!(recovered_manifest.deltas, ["delta-2"]);
    let mut index = open_index(root, &recovered_manifest);

    // Base top-1 is the stale `old` row. Refill must reach `fallback`; the
    // deleted `gone` row must not consume the result slot.
    let vector_hits = merge_with_refill(&index, 2, 1, |collection, probe| {
        vector_query(collection, &[1.0, 0.0, 0.0, 0.0], probe)
    });
    let vector_ids = vector_hits
        .iter()
        .map(|hit| hit.row.logical_id.as_str())
        .collect::<Vec<_>>();
    assert!(vector_ids.contains(&"fallback"));
    assert!(vector_ids.contains(&"old"));
    assert!(!vector_ids.contains(&"gone"));

    let deleted_query = merge_with_refill(&index, 2, 1, |collection, probe| {
        vector_query(collection, &[0.0, 1.0, 0.0, 0.0], probe)
    });
    assert!(deleted_query.iter().all(|hit| hit.row.logical_id != "gone"));
    assert!(deleted_query.iter().any(|hit| hit.row.logical_id == "old"));

    let hybrid = hybrid_search(&index, &[0.0, 1.0, 0.0, 0.0], "needle", 2);
    assert!(hybrid.iter().any(|hit| hit.row.logical_id == "old"));
    assert!(hybrid.iter().all(|hit| !hit.row.deleted));

    // A failed manifest validation leaves the previous published generation
    // untouched; the temporary file is never made active.
    let previous = recovered_manifest.clone();
    assert!(
        publish_manifest(
            root,
            &Manifest {
                generation: 99,
                base: "missing-base".into(),
                deltas: Vec::new(),
            },
        )
        .is_err()
    );
    assert_eq!(
        recover_manifest(root).expect("manifest remains recoverable"),
        previous
    );

    drop(index);
    shutdown().expect("shutdown native zvec");

    // Restart proof: reopen both real native collections from the recovered
    // manifest and verify that the update remains searchable.
    initialize(Some(&config)).expect("reinitialize native zvec");
    let manifest_after_restart = recover_manifest(root).expect("recover after restart");
    index = open_index(root, &manifest_after_restart);
    let after_restart = merge_with_refill(&index, 2, 1, |collection, probe| {
        vector_query(collection, &[0.0, 1.0, 0.0, 0.0], probe)
    });
    assert!(after_restart.iter().any(|hit| hit.row.logical_id == "old"));
    assert!(after_restart.iter().all(|hit| hit.row.logical_id != "gone"));
    println!(
        "native_base_delta_proof base_docs={} delta_docs={} restart_hits={} hybrid_hits={}",
        index
            .base
            .stats()
            .expect("base stats after restart")
            .doc_count,
        index
            .delta
            .stats()
            .expect("delta stats after restart")
            .doc_count,
        after_restart.len(),
        hybrid.len()
    );

    drop(index);
    shutdown().expect("final shutdown native zvec");
}
