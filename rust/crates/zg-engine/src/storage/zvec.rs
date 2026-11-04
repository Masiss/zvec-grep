//! Shared zvec initialization and collection operations.
use std::{collections::HashMap, path::Path, sync::OnceLock};
use zvec_rust::{
    Collection, CollectionOptions, CollectionSchema, DataType, Doc, FieldSchema, IndexParams,
};

use crate::{EngineError, EngineResult};

pub(super) const WRITE_BATCH: usize = 1024;
const DEFAULT_NATIVE_THREADS: u32 = 2;
const DEFAULT_NATIVE_MEMORY_LIMIT_BYTES: u64 = 512 * 1024 * 1024;
// Contained research operations may lower this internal target without changing
// the default or the external cgroup hard limit.
const NATIVE_MEMORY_LIMIT_ENV: &str = "ZVEC_GREP_NATIVE_MEMORY_LIMIT_BYTES";
static INITIALIZED: OnceLock<Result<(), String>> = OnceLock::new();

pub(super) fn initialize() -> EngineResult<()> {
    match INITIALIZED.get_or_init(|| {
        let config = zvec_rust::ConfigBuilder::new()
            .num_threads(native_thread_count())
            .memory_limit(native_memory_limit_bytes()?)
            .build();
        zvec_rust::initialize(Some(&config)).map_err(|error| error.to_string())
    }) {
        Ok(()) => Ok(()),
        Err(message) => Err(EngineError::storage_failure(format!(
            "cannot initialize zvec: {message}"
        ))),
    }
}

fn native_thread_count() -> u32 {
    native_thread_count_from(std::env::var("ZVEC_GREP_NATIVE_THREADS").ok().as_deref())
}

fn native_thread_count_from(value: Option<&str>) -> u32 {
    value
        .and_then(|value| value.parse::<u32>().ok())
        .filter(|&value| value > 0)
        .unwrap_or(DEFAULT_NATIVE_THREADS)
}

fn native_memory_limit_bytes() -> Result<u64, String> {
    match std::env::var(NATIVE_MEMORY_LIMIT_ENV) {
        Ok(value) => native_memory_limit_bytes_from(Some(&value)),
        Err(std::env::VarError::NotPresent) => Ok(DEFAULT_NATIVE_MEMORY_LIMIT_BYTES),
        Err(error) => Err(format!("read {NATIVE_MEMORY_LIMIT_ENV}: {error}")),
    }
}

fn native_memory_limit_bytes_from(value: Option<&str>) -> Result<u64, String> {
    let Some(value) = value else {
        return Ok(DEFAULT_NATIVE_MEMORY_LIMIT_BYTES);
    };
    let bytes = value
        .parse::<u64>()
        .map_err(|error| format!("invalid {NATIVE_MEMORY_LIMIT_ENV}: {error}"))?;
    if bytes == 0 {
        return Err(format!(
            "{NATIVE_MEMORY_LIMIT_ENV} must be greater than zero"
        ));
    }
    Ok(bytes)
}

pub(super) fn open_collection(
    path: &Path,
    schema: &CollectionSchema,
    read_only: bool,
) -> EngineResult<Collection> {
    let text = native_path(path)?;
    let mut options = native(CollectionOptions::new(), "configure collection")?;
    native(
        options.set_read_only(read_only),
        "set collection access mode",
    )?;
    if path.exists() {
        native(
            Collection::open(text, Some(&options)),
            &format!("open collection {}", path.display()),
        )
    } else if read_only {
        Err(EngineError::not_found(format!(
            "index collection does not exist: {}",
            path.display()
        )))
    } else {
        native(
            Collection::create_and_open(text, schema, Some(&options)),
            &format!("create collection {}", path.display()),
        )
    }
}

fn native_path(path: &Path) -> EngineResult<&str> {
    // zvec rejects the `?` in Windows verbatim prefixes. Simplify only when
    // the regular path identifies the same location; retain internal paths.
    #[cfg(windows)]
    let path = dunce::simplified(path);
    path.to_str().ok_or_else(|| {
        EngineError::invalid_argument(format!(
            "zvec storage path must be UTF-8: {}",
            path.display()
        ))
    })
}

pub(super) fn scalar(
    schema: &mut CollectionSchema,
    name: &str,
    data_type: DataType,
    nullable: bool,
    indexed: bool,
) -> EngineResult<()> {
    let mut field = native(
        FieldSchema::new(name, data_type, nullable, 0),
        "define scalar field",
    )?;
    if indexed {
        native(
            field.set_index_params(&native(
                IndexParams::invert(true, false),
                "define scalar index",
            )?),
            "attach scalar index",
        )?;
    }
    native(schema.add_field(&field), "add scalar field")
}

pub(super) fn wildcard_string(
    schema: &mut CollectionSchema,
    name: &str,
    nullable: bool,
) -> EngineResult<()> {
    let mut field = native(
        FieldSchema::new(name, DataType::String, nullable, 0),
        "define indexed path string",
    )?;
    native(
        field.set_index_params(&native(
            IndexParams::invert(false, true),
            "define path string index",
        )?),
        "attach path string index",
    )?;
    native(schema.add_field(&field), "add indexed path string")
}

pub(super) fn disjunction_filter(field: &str, values: &[u32]) -> String {
    values
        .iter()
        .map(|value| format!("{field} = {value}"))
        .collect::<Vec<_>>()
        .join(" OR ")
}

pub(super) fn fetch_map(
    collection: &Collection,
    keys: &[String],
) -> EngineResult<HashMap<String, Doc>> {
    fetch_map_with_vectors(collection, keys, false)
}

pub(super) fn fetch_map_with_vectors(
    collection: &Collection,
    keys: &[String],
    include_vectors: bool,
) -> EngineResult<HashMap<String, Doc>> {
    let mut result = HashMap::with_capacity(keys.len());
    for batch in keys.chunks(WRITE_BATCH) {
        let refs = batch.iter().map(String::as_str).collect::<Vec<_>>();
        for doc in native(
            collection.fetch_with_options(&refs, None, include_vectors),
            "fetch stored records",
        )? {
            result.insert(doc_key(&doc)?.to_owned(), doc);
        }
    }
    Ok(result)
}

pub(super) fn write_docs(
    collection: &Collection,
    docs: &[Doc],
    operation: &str,
) -> EngineResult<()> {
    for batch in docs.chunks(WRITE_BATCH) {
        let refs = batch.iter().collect::<Vec<_>>();
        let result = native(collection.upsert(&refs), operation)?;
        if result.results.len() != batch.len()
            || result.error_count != 0
            || result.success_count != u64::try_from(batch.len()).unwrap_or(u64::MAX)
            || result.results.iter().any(|status| !status.success)
        {
            let detail = result
                .results
                .iter()
                .find(|status| !status.success)
                .map_or_else(
                    || "incomplete write result".to_owned(),
                    |status| format!("{}: {}", status.code, status.message),
                );
            return Err(EngineError::storage_failure(format!(
                "{operation}: {detail}"
            )));
        }
    }
    Ok(())
}

pub(super) fn u32_field(doc: &Doc, name: &str) -> EngineResult<u32> {
    native(doc.get_u32(name), "read numeric field")?.ok_or_else(|| corrupt("missing numeric field"))
}

pub(super) fn doc_key(doc: &Doc) -> EngineResult<&str> {
    doc.get_pk()
        .ok_or_else(|| corrupt("stored record has no primary key"))
}

pub(super) fn string_field(doc: &Doc, field: &str) -> EngineResult<String> {
    native(doc.get_string(field), "read stored field")?
        .ok_or_else(|| corrupt(format!("stored field {field} is missing")))
}

pub(super) fn corrupt(message: impl std::fmt::Display) -> EngineError {
    EngineError::storage_failure(message.to_string())
}
#[track_caller]
pub(super) fn native<T>(result: zvec_rust::Result<T>, operation: &str) -> EngineResult<T> {
    result.map_err(|error| EngineError::storage_failure(format!("zvec {operation}: {error}")))
}

#[cfg(test)]
mod tests {
    use super::{
        DEFAULT_NATIVE_MEMORY_LIMIT_BYTES, DEFAULT_NATIVE_THREADS, native_memory_limit_bytes_from,
        native_thread_count_from,
    };

    #[test]
    fn native_thread_count_defaults_when_unset_or_invalid() {
        assert_eq!(native_thread_count_from(None), DEFAULT_NATIVE_THREADS);
        assert_eq!(native_thread_count_from(Some("0")), DEFAULT_NATIVE_THREADS);
        assert_eq!(
            native_thread_count_from(Some("not-a-number")),
            DEFAULT_NATIVE_THREADS
        );
    }

    #[test]
    fn native_thread_count_accepts_positive_override() {
        assert_eq!(native_thread_count_from(Some("1")), 1);
        assert_eq!(native_thread_count_from(Some("4")), 4);
    }

    #[test]
    fn native_memory_limit_defaults_and_accepts_positive_override() {
        assert_eq!(
            native_memory_limit_bytes_from(None).expect("default memory limit"),
            DEFAULT_NATIVE_MEMORY_LIMIT_BYTES
        );
        assert_eq!(
            native_memory_limit_bytes_from(Some("402653184")).expect("memory override"),
            402_653_184
        );
    }

    #[test]
    fn native_memory_limit_rejects_invalid_and_zero_overrides() {
        assert!(native_memory_limit_bytes_from(Some("0")).is_err());
        assert!(native_memory_limit_bytes_from(Some("not-a-number")).is_err());
    }
}
