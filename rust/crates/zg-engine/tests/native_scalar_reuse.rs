//! Focused correctness test for the research scalar-reuse fast path
//! (`ZVEC_RESEARCH_SCALAR_REUSE`, default off). Ignored: research artifact.
//!
//! Run with the rebuilt native library forced in, e.g.:
//! ```text
//! LD_LIBRARY_PATH=.local/native/zvec-native-build/lib \
//!   ZVEC_RESEARCH_SCALAR_REUSE=1 REUSE_LOG=1 \
//!   <zg_engine-test-binary> native_scalar_reuse --ignored --nocapture
//! ```

#![allow(clippy::cast_possible_truncation, clippy::cast_precision_loss)]

use std::env;

use tempfile::TempDir;
use zvec_rust::{
    Collection, CollectionSchema, ConfigBuilder, DataType, Doc, FieldSchema, IndexParams,
    MetricType, SearchQuery, initialize, shutdown,
};

const DIM: u32 = 8;

fn vector_for(index: u32) -> Vec<f32> {
    (0..DIM)
        .map(|axis| ((index + 1) as f32) * (axis as f32 + 1.0) / 1000.0)
        .collect()
}

fn schema() -> CollectionSchema {
    let mut builder = CollectionSchema::builder("scalar_reuse");
    let mut file_id = FieldSchema::new("file_id", DataType::Uint32, false, 0).expect("file_id");
    file_id
        .set_index_params(&IndexParams::invert(true, false).expect("invert"))
        .expect("file_id index");
    builder = builder.add_field(file_id);
    let mut tag = FieldSchema::new("tag", DataType::String, false, 0).expect("tag");
    tag.set_index_params(&IndexParams::invert(false, true).expect("invert"))
        .expect("tag index");
    builder = builder.add_field(tag);
    builder
        .add_vector_field(
            "embedding",
            DataType::VectorFp32,
            DIM,
            IndexParams::hnsw(MetricType::L2, 16, 100).expect("hnsw"),
        )
        .build()
        .expect("schema")
}

fn make_doc(pk: &str, file_id: u32, tag: &str) -> Doc {
    let mut doc = Doc::new().expect("doc");
    doc.set_pk(pk);
    doc.add_u32("file_id", file_id).expect("file_id");
    doc.add_string("tag", tag).expect("tag");
    doc.add_vector_f32("embedding", &vector_for(file_id))
        .expect("embedding");
    doc
}

fn insert_all(collection: &Collection, docs: &[Doc]) {
    for chunk in docs.chunks(512) {
        let refs = chunk.iter().collect::<Vec<_>>();
        let result = collection.insert(&refs).expect("insert");
        assert_eq!(result.error_count, 0, "insert errors: {:?}", result.results);
    }
}

fn query_by_file_id(collection: &Collection, file_id: u32) -> Vec<(String, String)> {
    let mut query = SearchQuery::new("embedding", &vector_for(file_id), 8).expect("query");
    query.set_output_fields(&["tag"]).expect("output fields");
    query
        .set_filter(&format!("file_id = {file_id}"))
        .expect("filter");
    collection
        .query(&query)
        .expect("query")
        .into_iter()
        .map(|doc| {
            (
                doc.get_pk().unwrap_or_default().to_owned(),
                doc.get_string("tag").expect("tag").unwrap_or_default(),
            )
        })
        .collect()
}

fn unfiltered_count(collection: &Collection) -> usize {
    let mut query = SearchQuery::new("embedding", &vector_for(0), 5000).expect("query");
    query.set_output_fields(&["tag"]).expect("output fields");
    collection.query(&query).expect("query").len()
}

#[test]
#[ignore = "research correctness test; run with --ignored and the rebuilt native library"]
fn native_scalar_reuse() {
    let mut config = ConfigBuilder::new().num_threads(1);
    if env::var("REUSE_LOG").is_ok() {
        config = config.enable_console_log(true);
    }
    if let Ok(dict) = env::var("REUSE_JIEBA_DIR") {
        config = config.jieba_dict_dir(dict);
    }
    let config = config.build();
    initialize(Some(&config)).expect("initialize");

    let temp = TempDir::new().expect("tempdir");
    let path = temp.path().join("scalar_reuse");
    let path_str = path.to_str().expect("utf8 path");

    let base_count: u32 = 1000;
    let tail_count: u32 = 200;

    {
        let collection = Collection::create_and_open(path_str, &schema(), None).expect("create");
        let base: Vec<Doc> = (0..base_count)
            .map(|i| make_doc(&format!("b{i}"), i, &format!("base{i}")))
            .collect();
        insert_all(&collection, &base);
        collection.flush().expect("flush base");
        collection.optimize().expect("optimize base");

        // Tail append: with the flag on this is the reuse fast path.
        let tail: Vec<Doc> = (0..tail_count)
            .map(|j| make_doc(&format!("t{j}"), base_count + j, &format!("tail{j}")))
            .collect();
        insert_all(&collection, &tail);
        collection.flush().expect("flush tail");
        collection.optimize().expect("optimize tail");

        // Forward values reachable for both the copied base and the appended tail.
        assert_eq!(
            unfiltered_count(&collection),
            (base_count + tail_count) as usize
        );
        assert_eq!(
            query_by_file_id(&collection, 5),
            vec![("b5".to_owned(), "base5".to_owned())]
        );
        assert_eq!(
            query_by_file_id(&collection, base_count + 5),
            vec![("t5".to_owned(), "tail5".to_owned())]
        );
        let fetched = collection.fetch(&["b7"]).expect("fetch");
        assert_eq!(
            fetched[0].get_string("tag").expect("tag").as_deref(),
            Some("base7")
        );
    }

    // Read-only reopen: both blocks survive the manifest round-trip.
    {
        let collection = Collection::open(path_str, None).expect("reopen");
        assert_eq!(
            unfiltered_count(&collection),
            (base_count + tail_count) as usize
        );
        assert_eq!(
            query_by_file_id(&collection, 5),
            vec![("b5".to_owned(), "base5".to_owned())]
        );
        assert_eq!(
            query_by_file_id(&collection, base_count + 5),
            vec![("t5".to_owned(), "tail5".to_owned())]
        );

        // Update an existing base doc (tombstone, non-purge) then optimize.
        let updated = make_doc("b5", 5, "updated5");
        collection.upsert(&[&updated]).expect("upsert");
        collection.flush().expect("flush update");
        collection.optimize().expect("optimize update");
        assert_eq!(
            query_by_file_id(&collection, 5),
            vec![("b5".to_owned(), "updated5".to_owned())]
        );

        // Delete a base doc; the tombstone must stay hidden across optimize.
        collection.delete(&["b6"]).expect("delete");
        collection.flush().expect("flush delete");
        collection.optimize().expect("optimize delete");
        assert!(
            query_by_file_id(&collection, 6).is_empty(),
            "deleted doc still visible"
        );
        assert_eq!(
            unfiltered_count(&collection),
            (base_count + tail_count - 1) as usize
        );
    }

    // Reopen again after the update/delete cycle.
    {
        let collection = Collection::open(path_str, None).expect("reopen2");
        assert_eq!(
            query_by_file_id(&collection, 5),
            vec![("b5".to_owned(), "updated5".to_owned())]
        );
        assert!(query_by_file_id(&collection, 6).is_empty());
        assert_eq!(
            query_by_file_id(&collection, base_count + 5),
            vec![("t5".to_owned(), "tail5".to_owned())]
        );
        drop(collection);
    }

    shutdown().expect("shutdown");
}
