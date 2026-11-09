//! Read-only forensic breakdown of an existing zvec-grep index.
//!
//! Usage:
//!   `index_forensics <index-dir> <out.json>`

use std::{
    collections::{BTreeMap, HashMap},
    fs,
    path::{Path, PathBuf},
};

use serde_json::{Value, json};
#[cfg(unix)]
use std::os::unix::fs::MetadataExt;
use zvec_rust::{Collection, CollectionOptions};

#[derive(Default)]
struct CollectionStats {
    docs: u64,
    string_fields: BTreeMap<String, u64>,
    vectors: u64,
    vector_dims: u64,
}

#[allow(clippy::too_many_lines)]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args().skip(1);
    let index = PathBuf::from(args.next().expect("index dir"));
    let out = PathBuf::from(args.next().expect("out json"));
    let mut result = serde_json::Map::new();
    result.insert("index_dir".into(), json!(index.display().to_string()));
    result.insert(
        "collection_bytes".into(),
        json!(
            directory_sizes(&index)?
                .into_iter()
                .collect::<BTreeMap<_, _>>()
        ),
    );

    eprintln!("open files");
    let files = open_collection(&index.join("files"))?;
    let mut file_sizes = HashMap::new();
    let mut file_paths = HashMap::new();
    for doc in files.iter_with_options(None, false)? {
        let doc = doc?;
        let payload = doc.get_string("payload")?.expect("file payload");
        let value: Value = serde_json::from_str(&payload)?;
        let id = value["id"].as_u64().expect("file id");
        if let Some(size) = value["snapshot"]["size_bytes"].as_u64() {
            file_sizes.insert(id, size);
        }
        if let Some(path) = value["relative_path"].as_str() {
            file_paths.insert(id, path.to_owned());
        }
    }
    files.close()?;

    eprintln!("open entities");
    let entities = open_collection(&index.join("entities"))?;
    let mut entity_stats = CollectionStats::default();
    let mut per_file = HashMap::<u64, FileAggregate>::new();
    let mut by_language = BTreeMap::<String, GroupAggregate>::new();
    let mut by_symbol = BTreeMap::<String, GroupAggregate>::new();
    let mut content_kinds = BTreeMap::<String, GroupAggregate>::new();
    let mut entity_fragment_count = 0u64;
    for doc in entities.iter_with_options(None, false)? {
        let doc = doc?;
        entity_stats.docs += 1;
        accumulate_strings(
            &doc,
            &["entity_id", "payload", "metadata"],
            &mut entity_stats,
        )?;
        let payload = doc.get_string("payload")?.expect("entity payload");
        let metadata = if doc.has_field("metadata") && !doc.is_field_null("metadata") {
            doc.get_string("metadata")?.unwrap_or_default()
        } else {
            "null".to_owned()
        };
        let value: Value = serde_json::from_str(&payload)?;
        let file_id = value["file_id"].as_u64().expect("file id");
        let content_bytes = content_len(&value["content"]);
        let payload_bytes = payload.len() as u64;
        let metadata_bytes = metadata.len() as u64;
        let fragments = value["fragments"].as_array().map_or(0, Vec::len) as u64;
        entity_fragment_count += fragments;
        let parsed: Value = serde_json::from_str(&metadata)?;
        let language = parsed["language"].as_str().unwrap_or("none").to_owned();
        let symbol = parsed["symbol_type"].as_str().unwrap_or("none").to_owned();
        let kind = value["content"]["kind"]
            .as_str()
            .unwrap_or("none")
            .to_owned();
        let group = per_file.entry(file_id).or_default();
        group.entities += 1;
        group.content_bytes += content_bytes;
        group.payload_bytes += payload_bytes;
        group.metadata_bytes += metadata_bytes;
        group.fragments += fragments;
        for (map, key) in [
            (&mut by_language, language),
            (&mut by_symbol, symbol),
            (&mut content_kinds, kind),
        ] {
            let group = map.entry(key).or_default();
            group.entities += 1;
            group.content_bytes += content_bytes;
            group.payload_bytes += payload_bytes;
            group.metadata_bytes += metadata_bytes;
            group.fragments += fragments;
        }
    }
    entities.close()?;

    let schema: Value = serde_json::from_slice(&fs::read(index.join("schema.json"))?)?;
    let dimension = schema["embeddings"]
        .as_array()
        .and_then(|embeddings| embeddings.first())
        .and_then(|embedding| embedding["dimension"].as_u64())
        .expect("embedding dimension");
    let mut fragment_stats = CollectionStats::default();
    let mut fragment_entity_ids = HashMap::<String, u64>::new();
    let mut fragment_dirs = Vec::new();
    for entry in fs::read_dir(&index)? {
        let entry = entry?;
        let name = entry.file_name().into_string().unwrap_or_default();
        if name.starts_with("fragments_") {
            fragment_dirs.push(entry.path());
        }
    }
    for dir in &fragment_dirs {
        eprintln!("open {}", dir.display());
        let fragments = open_collection(dir)?;
        for doc in fragments.iter_with_options(None, false)? {
            let doc = doc?;
            fragment_stats.docs += 1;
            accumulate_strings(
                &doc,
                &[
                    "entity_id",
                    "document_id",
                    "text",
                    "symbol_name",
                    "symbol_type",
                ],
                &mut fragment_stats,
            )?;
            let entity_id = doc.get_string("entity_id")?.expect("entity id");
            *fragment_entity_ids.entry(entity_id).or_default() += 1;
            fragment_stats.vectors += 1;
            fragment_stats.vector_dims += dimension;
        }
        fragments.close()?;
    }

    result.insert(
        "entities_collection".into(),
        json!({
            "docs": entity_stats.docs,
            "string_fields": entity_stats.string_fields,
            "fragments_declared": entity_fragment_count,
        }),
    );
    result.insert(
        "fragments_collection".into(),
        json!({
            "docs": fragment_stats.docs,
            "string_fields": fragment_stats.string_fields,
            "vectors": fragment_stats.vectors,
            "vector_dims": fragment_stats.vector_dims,
            "vector_bytes_estimate": fragment_stats.vectors * dimension * 2,
        }),
    );
    result.insert(
        "files".into(),
        json!({
            "count": file_sizes.len(),
            "total_source_bytes": file_sizes.values().sum::<u64>(),
        }),
    );
    result.insert(
        "by_language".into(),
        json!(
            by_language
                .into_iter()
                .map(|(k, v)| (k, v.json()))
                .collect::<BTreeMap<_, _>>()
        ),
    );
    result.insert(
        "by_symbol_type".into(),
        json!(
            by_symbol
                .into_iter()
                .map(|(k, v)| (k, v.json()))
                .collect::<BTreeMap<_, _>>()
        ),
    );
    result.insert(
        "by_content_kind".into(),
        json!(
            content_kinds
                .into_iter()
                .map(|(k, v)| (k, v.json()))
                .collect::<BTreeMap<_, _>>()
        ),
    );
    let mut files_out = Vec::new();
    for (id, aggregate) in per_file {
        let source = file_sizes.get(&id).copied().unwrap_or(0);
        let path = file_paths.get(&id).cloned().unwrap_or_default();
        files_out.push(json!({
            "file_id": id,
            "path": path,
            "source_bytes": source,
            "entities": aggregate.entities,
            "content_bytes": aggregate.content_bytes,
            "payload_bytes": aggregate.payload_bytes,
            "metadata_bytes": aggregate.metadata_bytes,
            "fragments": aggregate.fragments,
            "content_ratio": ratio(aggregate.content_bytes, source),
            "fragment_docs": aggregate.fragments,
        }));
    }
    files_out.sort_by(|a, b| {
        a["path"]
            .as_str()
            .unwrap_or("")
            .cmp(b["path"].as_str().unwrap_or(""))
    });
    result.insert("files_detail".into(), json!(files_out));
    let unique_source: u64 = file_sizes.values().sum();
    let total_content: u64 = result["by_language"]
        .as_object()
        .expect("languages")
        .values()
        .filter_map(|group| group["content_bytes"].as_u64())
        .sum();
    result.insert(
        "duplication".into(),
        json!({
            "source_bytes": unique_source,
            "entity_content_bytes": total_content,
            "content_over_source": ratio(total_content, unique_source),
            "fragment_docs": fragment_stats.docs,
            "fragment_docs_per_entity": ratio(fragment_stats.docs, entity_stats.docs),
            "fragment_text_bytes": fragment_stats.string_fields.get("text").copied().unwrap_or(0),
            "fragment_text_over_source": ratio(
                fragment_stats.string_fields.get("text").copied().unwrap_or(0),
                unique_source
            ),
            "metadata_bytes": entity_stats.string_fields.get("metadata").copied().unwrap_or(0),
            "payload_bytes": entity_stats.string_fields.get("payload").copied().unwrap_or(0),
            "vector_bytes_estimate": fragment_stats.vectors * dimension * 2,
        }),
    );
    fs::write(&out, serde_json::to_vec_pretty(&result)?)?;
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}

#[derive(Default)]
struct FileAggregate {
    entities: u64,
    content_bytes: u64,
    payload_bytes: u64,
    metadata_bytes: u64,
    fragments: u64,
}

#[derive(Default)]
struct GroupAggregate {
    entities: u64,
    content_bytes: u64,
    payload_bytes: u64,
    metadata_bytes: u64,
    fragments: u64,
}

impl GroupAggregate {
    fn json(&self) -> Value {
        json!({
            "entities": self.entities,
            "content_bytes": self.content_bytes,
            "payload_bytes": self.payload_bytes,
            "metadata_bytes": self.metadata_bytes,
            "fragments": self.fragments,
        })
    }
}

#[allow(clippy::cast_precision_loss)]
fn ratio(numerator: u64, denominator: u64) -> f64 {
    if denominator == 0 {
        0.0
    } else {
        numerator as f64 / denominator as f64
    }
}

fn content_len(content: &Value) -> u64 {
    match content["kind"].as_str() {
        Some("text" | "code") => content["value"]
            .as_str()
            .map_or(0, |value| value.len() as u64),
        Some("image") => content["value"]["data"]
            .as_str()
            .map_or(0, |value| value.len() as u64),
        _ => 0,
    }
}

fn accumulate_strings(
    doc: &zvec_rust::Doc,
    names: &[&str],
    stats: &mut CollectionStats,
) -> Result<(), Box<dyn std::error::Error>> {
    for name in names {
        if doc.has_field(name)
            && !doc.is_field_null(name)
            && let Some(value) = doc.get_string(name)?
        {
            *stats.string_fields.entry((*name).to_owned()).or_default() += value.len() as u64;
        }
    }
    Ok(())
}

fn open_collection(path: &Path) -> Result<Collection, Box<dyn std::error::Error>> {
    #[cfg(windows)]
    let path = dunce::simplified(path);
    let mut options = CollectionOptions::new()?;
    options.set_read_only(true)?;
    Ok(Collection::open(
        path.to_str().expect("UTF-8 collection path"),
        Some(&options),
    )?)
}

fn directory_sizes(path: &Path) -> std::io::Result<BTreeMap<String, u64>> {
    let mut sizes = BTreeMap::new();
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let name = entry.file_name().into_string().unwrap_or_default();
        let metadata = entry.metadata()?;
        let bytes = if metadata.is_dir() {
            allocated_bytes(&entry.path())?
        } else {
            metadata.len()
        };
        sizes.insert(name, bytes);
    }
    Ok(sizes)
}

fn allocated_bytes(path: &Path) -> std::io::Result<u64> {
    let meta = fs::symlink_metadata(path)?;
    let mut total = if meta.is_file() {
        meta.blocks() * 512
    } else {
        0
    };
    if meta.is_dir() {
        for entry in fs::read_dir(path)? {
            total += allocated_bytes(&entry?.path())?;
        }
    }
    Ok(total)
}
