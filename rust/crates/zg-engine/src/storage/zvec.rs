//! Shared zvec initialization and collection operations.
use std::{
    collections::{BTreeSet, HashMap},
    fmt::Write,
    path::Path,
    sync::OnceLock,
};
use zvec_rust::{
    Collection, CollectionOptions, CollectionSchema, DataType, Doc, FieldSchema, IndexParams,
    SearchQuery,
};

use crate::{EngineError, EngineResult, utils::directory_exists};

pub(super) const WRITE_BATCH: usize = 1024;
static INITIALIZED: OnceLock<Result<(), String>> = OnceLock::new();

pub(super) fn initialize() -> EngineResult<()> {
    match INITIALIZED.get_or_init(|| zvec_rust::initialize(None).map_err(|error| error.to_string()))
    {
        Ok(()) => Ok(()),
        Err(message) => Err(EngineError::storage_failure(format!(
            "cannot initialize zvec: {message}"
        ))),
    }
}

/// Files every `jieba` dictionary directory must contain.
const JIEBA_DICT_FILES: [&str; 2] = ["jieba.dict.utf8", "hmm_model.utf8"];

fn is_jieba_dict_dir(path: &Path) -> bool {
    JIEBA_DICT_FILES
        .iter()
        .all(|name| path.join(name).is_file())
}

/// Resolves the `jieba` dictionary used by the FTS tokenizer.
///
/// The native zvec library does not discover the dictionary on its own. The
/// engine must pass it per collection, and opening an existing collection needs
/// the same directory, so the location is resolved from stable process-level
/// roots rather than the current working directory.
fn resolve_jieba_dict_dir(
    env_dir: Option<&Path>,
    home: Option<&Path>,
    executable: Option<&Path>,
) -> Option<std::path::PathBuf> {
    let mut candidates = Vec::new();
    if let Some(dir) = env_dir {
        candidates.push(dir.to_path_buf());
    }
    if let Some(home) = home {
        candidates.push(home.join("jieba_dict"));
    }
    if let Some(dir) = executable.and_then(Path::parent) {
        for relative in ["data/jieba_dict", "jieba_dict", "../lib/data/jieba_dict"] {
            candidates.push(dir.join(relative));
        }
    }
    candidates.into_iter().find(|dir| is_jieba_dict_dir(dir))
}

pub(crate) fn jieba_dict_dir() -> EngineResult<std::path::PathBuf> {
    let env_dir = std::env::var_os("ZVEC_JIEBA_DICT_DIR").map(std::path::PathBuf::from);
    let home = std::env::var_os("ZVEC_GREP_HOME")
        .map(std::path::PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME").map(|home| std::path::PathBuf::from(home).join(".zvec-grep"))
        });
    let executable = std::env::current_exe().ok();
    if let Some(dir) = resolve_jieba_dict_dir(
        env_dir.as_deref(),
        home.as_deref(),
        executable.as_deref(),
    ) {
        return Ok(dir);
    }
    Err(EngineError::invalid_argument(
        "FTS dictionary (jieba) is not available: set ZVEC_JIEBA_DICT_DIR to a directory containing jieba.dict.utf8 and hmm_model.utf8, or install the dictionary at <ZVEC_GREP_HOME>/jieba_dict",
    ))
}

pub(crate) fn is_corruption(message: &str) -> bool {
    let message = message.to_ascii_lowercase();
    message.contains("checksum") || message.contains("corrupt")
}

fn actionable_storage_error(error: EngineError) -> EngineError {
    let message = error.to_string();
    if is_corruption(&message) {
        return error.with_help(
            "the workspace index is corrupt or unreadable; rebuild it with `zg --index --rebuild`",
        );
    }
    if message.contains("jieba_dict_dir") || message.contains("dictionary") {
        return error.with_help(
            "the FTS dictionary is missing; set ZVEC_JIEBA_DICT_DIR or install it at <ZVEC_GREP_HOME>/jieba_dict and rebuild if the stored path is gone",
        );
    }
    error
}

/// Reads the dictionary path persisted in the native collection manifest.
///
/// The native library aborts the process when the persisted dictionary is gone,
/// so the location must be validated before the collection is opened. This is a
/// defensive read of one stable text field, not a general native-format parser.
fn persisted_jieba_dict_dir(collection: &Path) -> Option<std::path::PathBuf> {
    let entries = std::fs::read_dir(collection).ok()?;
    for entry in entries.flatten() {
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if !name.starts_with("manifest.") {
            continue;
        }
        let bytes = std::fs::read(entry.path()).ok()?;
        let marker = b"\"jieba_dict_dir\":\"";
        let start = bytes.windows(marker.len()).position(|w| w == marker)? + marker.len();
        let end = bytes[start..].iter().position(|byte| *byte == b'"')? + start;
        let value = std::str::from_utf8(&bytes[start..end]).ok()?;
        return Some(std::path::PathBuf::from(value));
    }
    None
}

/// Opens an existing collection without constructing a schema. Reopening must
/// not depend on currently discoverable native prerequisites: the FTS
/// dictionary location is persisted in the stored schema.
pub(super) fn open_fragment_collection(
    path: &Path,
    read_only: bool,
) -> EngineResult<Collection> {
    match persisted_jieba_dict_dir(path) {
        Some(dictionary) if !is_jieba_dict_dir(&dictionary) => {
            return Err(EngineError::storage_failure(format!(
                "the persisted FTS dictionary is missing: {}",
                dictionary.display()
            ))
            .with_help(
                "restore the dictionary directory, or rebuild the index with `zg --index --rebuild`",
            ));
        }
        None => {
            // Indexes created before the dictionary was persisted rely on the
            // environment at open time.
            if std::env::var_os("ZVEC_JIEBA_DICT_DIR").is_none() {
                return Err(EngineError::storage_failure(
                    "this index does not record an FTS dictionary path",
                )
                .with_help(
                    "set ZVEC_JIEBA_DICT_DIR to a directory containing jieba.dict.utf8 and hmm_model.utf8, or rebuild the index",
                ));
            }
        }
        Some(_) => {}
    }
    let text = native_path(path)?;
    let mut options = native(CollectionOptions::new(), "configure collection")?;
    native(
        options.set_read_only(read_only),
        "set collection access mode",
    )?;
    native(
        Collection::open(text, Some(&options)),
        &format!("open collection {}", path.display()),
    )
    .map_err(actionable_storage_error)
}

pub(super) fn open_existing_collection(
    path: &Path,
    read_only: bool,
) -> EngineResult<Collection> {
    let text = native_path(path)?;
    let mut options = native(CollectionOptions::new(), "configure collection")?;
    native(
        options.set_read_only(read_only),
        "set collection access mode",
    )?;
    native(
        Collection::open(text, Some(&options)),
        &format!("open collection {}", path.display()),
    )
    .map_err(actionable_storage_error)
}

/// Opens an existing collection or creates it from `schema` when absent.
pub(super) fn open_collection(
    path: &Path,
    schema: &CollectionSchema,
    read_only: bool,
) -> EngineResult<Collection> {
    if directory_exists(path)? {
        open_existing_collection(path, read_only)
    } else {
        create_collection(path, schema, read_only)
    }
}

pub(super) fn create_collection(
    path: &Path,
    schema: &CollectionSchema,
    read_only: bool,
) -> EngineResult<Collection> {
    if read_only {
        return Err(EngineError::not_found(format!(
            "index collection does not exist: {}",
            path.display()
        )));
    }
    let text = native_path(path)?;
    let mut options = native(CollectionOptions::new(), "configure collection")?;
    native(
        options.set_read_only(read_only),
        "set collection access mode",
    )?;
    native(
        Collection::create_and_open(text, schema, Some(&options)),
        &format!("create collection {}", path.display()),
    )
    .map_err(actionable_storage_error)
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

pub(super) fn fetch_map(
    collection: &Collection,
    keys: &[String],
) -> EngineResult<HashMap<String, Doc>> {
    let mut result = HashMap::with_capacity(keys.len());
    for batch in keys.chunks(WRITE_BATCH) {
        let refs = batch.iter().map(String::as_str).collect::<Vec<_>>();
        for doc in native(
            collection.fetch_with_options(&refs, None, false),
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

/// Read every distinct entity ID matching a scalar filter without a result cap.
/// Full batches split into open ranges around an already-collected pivot. This
/// also progresses when one entity has enough fragment rows to fill a batch.
pub(super) fn collect_entity_ids(
    collection: &Collection,
    filter: &str,
    validate: impl Fn(&Doc) -> EngineResult<()>,
) -> EngineResult<Vec<String>> {
    const BATCH_SIZE: usize = 1024;
    let mut ids = BTreeSet::new();
    let mut ranges: Vec<(Option<String>, Option<String>)> = vec![(None, None)];
    while let Some((lower, upper)) = ranges.pop() {
        let mut filter = filter.to_owned();
        if let Some(lower) = &lower {
            let _ = write!(filter, " AND entity_id > {}", quote(lower));
        }
        if let Some(upper) = &upper {
            let _ = write!(filter, " AND entity_id < {}", quote(upper));
        }
        let mut query = native(
            SearchQuery::scalar(i32::try_from(BATCH_SIZE).expect("batch size fits i32")),
            "create entity ID query",
        )?;
        native(query.set_filter(&filter), "filter entity IDs")?;
        native(
            query.set_output_fields(&["file_id", "entity_id"]),
            "project entity IDs",
        )?;
        native(query.set_include_vector(false), "omit entity vectors")?;
        let docs = native(collection.query(&query), "query entity IDs")?;
        let full = docs.len() == BATCH_SIZE;
        let mut batch = BTreeSet::new();
        for doc in docs {
            validate(&doc)?;
            let id = string_field(&doc, "entity_id")?;
            if lower.as_ref().is_some_and(|bound| id <= *bound)
                || upper.as_ref().is_some_and(|bound| id >= *bound)
            {
                return Err(corrupt(
                    "entity query returned an ID outside its requested range",
                ));
            }
            batch.insert(id);
        }
        if full {
            let pivot = batch
                .iter()
                .nth(batch.len() / 2)
                .expect("full batch")
                .clone();
            ranges.push((Some(pivot.clone()), upper));
            ranges.push((lower, Some(pivot)));
        }
        ids.extend(batch);
    }
    Ok(ids.into_iter().collect())
}

pub(super) fn quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "\\'"))
}

#[cfg(test)]
mod tests {
    use super::{actionable_storage_error, is_jieba_dict_dir};

    #[test]
    fn checksum_failure_carries_a_rebuild_instruction() {
        let error = crate::EngineError::storage_failure("zvec open collection: Invalid checksum");
        let error = actionable_storage_error(error);
        let rendered = error.to_string();
        assert!(rendered.contains("Invalid checksum"));
        assert!(
            format!("{error:?}").contains("rebuild") || rendered.contains("rebuild"),
            "corrupt storage must explain the rebuild path: {error:?}"
        );
    }

    #[test]
    fn unrelated_storage_failure_is_unchanged() {
        let error = crate::EngineError::storage_failure("permission denied");
        let error = actionable_storage_error(error);
        assert!(!format!("{error:?}").contains("rebuild"));
    }

    #[test]
    fn dictionary_resolution_prefers_environment_then_home_then_executable() {
        let env_dir = tempfile::tempdir().expect("env dir");
        let home = tempfile::tempdir().expect("home dir");
        let exe = tempfile::tempdir().expect("exe dir");
        for dir in [env_dir.path(), home.path(), exe.path()] {
            std::fs::write(dir.join("jieba.dict.utf8"), b"dict").expect("write");
            std::fs::write(dir.join("hmm_model.utf8"), b"model").expect("write");
        }
        std::fs::create_dir_all(home.path().join("jieba_dict")).expect("home dict");
        for name in ["jieba.dict.utf8", "hmm_model.utf8"] {
            std::fs::copy(home.path().join(name), home.path().join("jieba_dict").join(name))
                .expect("copy");
        }
        std::fs::create_dir_all(exe.path().join("data/jieba_dict")).expect("exe dict");
        for name in ["jieba.dict.utf8", "hmm_model.utf8"] {
            std::fs::copy(
                home.path().join("jieba_dict").join(name),
                exe.path().join("data/jieba_dict").join(name),
            )
            .expect("copy");
        }
        let resolved = super::resolve_jieba_dict_dir(
            Some(env_dir.path()),
            Some(home.path()),
            Some(&exe.path().join("zg")),
        );
        assert_eq!(resolved.as_deref(), Some(env_dir.path()));
        let resolved = super::resolve_jieba_dict_dir(
            None,
            Some(home.path()),
            Some(&exe.path().join("zg")),
        );
        assert_eq!(resolved, Some(home.path().join("jieba_dict")));
        let resolved = super::resolve_jieba_dict_dir(None, None, Some(&exe.path().join("zg")));
        assert_eq!(resolved, Some(exe.path().join("data/jieba_dict")));
    }

    #[test]
    fn persisted_dictionary_path_is_read_and_validated() {
        let collection = tempfile::tempdir().expect("collection");
        std::fs::write(
            collection.path().join("manifest.3"),
            b"text\x00\"jieba_dict_dir\":\"/tmp/dict\"\x00",
        )
        .expect("manifest");
        assert_eq!(
            super::persisted_jieba_dict_dir(collection.path()),
            Some(std::path::PathBuf::from("/tmp/dict"))
        );
        assert_eq!(
            super::persisted_jieba_dict_dir(&collection.path().join("missing")),
            None
        );
    }

    #[test]
    fn corruption_classification_is_specific() {
        assert!(super::is_corruption("Index segments meta checksum is invalid"));
        assert!(super::is_corruption("open collection: corrupt record"));
        assert!(!super::is_corruption("permission denied"));
        assert!(!super::is_corruption("jieba_dict_dir not configured"));
    }

    #[test]
    fn dictionary_directory_requires_both_files() {
        let directory = tempfile::tempdir().expect("temp dir");
        assert!(!is_jieba_dict_dir(directory.path()));
        std::fs::write(directory.path().join("jieba.dict.utf8"), b"dict").expect("write");
        assert!(!is_jieba_dict_dir(directory.path()));
        std::fs::write(directory.path().join("hmm_model.utf8"), b"model").expect("write");
        assert!(is_jieba_dict_dir(directory.path()));
    }
}
