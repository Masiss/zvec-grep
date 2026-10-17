//! Temporary read-only export of indexed source paths for a bounded research build.
#![allow(clippy::unwrap_used, clippy::expect_used)]
use std::{
    fs::File,
    io::{BufWriter, Write},
    path::Path,
};
use zvec_rust::{Collection, CollectionOptions, ConfigBuilder, initialize, shutdown};
const STORAGE: &str =
    "/home/masis/itc/HIS_EMR/.zvec-grep/generations/d26907d0-ce6a-48a7-ba70-689c858cff7f/storage";
#[test]
#[ignore = "read-only live workspace probe"]
fn export_files() {
    let config = ConfigBuilder::new()
        .num_threads(2)
        .enable_console_log(false)
        .build();
    initialize(Some(&config)).expect("initialize zvec");
    let mut options = CollectionOptions::new().expect("options");
    options.set_read_only(true).expect("read-only");
    let collection = Collection::open(
        Path::new(STORAGE).join("files").to_str().unwrap(),
        Some(&options),
    )
    .expect("open files");
    let output = File::create("/tmp/indexed-files.tsv").expect("output");
    let mut output = BufWriter::new(output);
    let mut iter = collection
        .iter_with_options(Some(&["file_id", "path", "payload"]), false)
        .expect("iterate");
    while let Some(result) = iter.next() {
        let doc = result.expect("doc");
        let id = doc.get_u32("file_id").unwrap().unwrap();
        let path = doc.get_string("path").unwrap().unwrap_or_default();
        let payload = doc.get_string("payload").unwrap().unwrap_or_default();
        let value: serde_json::Value = serde_json::from_str(&payload).unwrap();
        let rel = value["relative_path"]["value"].as_str().unwrap_or("");
        let bytes = value["snapshot"]["size_bytes"].as_u64().unwrap_or(0);
        writeln!(output, "{}\t{}\t{}\t{}", id, rel, bytes, path).unwrap();
    }
    output.flush().unwrap();
    drop(iter);
    drop(collection);
    shutdown().expect("shutdown zvec");
}
