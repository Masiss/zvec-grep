//! Lossless identity keys and optional Unicode query projections are separate.

use std::path::Path;

use crate::{EngineError, EngineResult, domain::SourcePath};

use super::zvec::{native, scalar, wildcard_string};
use crate::domain::{DirectoryId, FileRecord};
use zvec_rust::{CollectionSchema, DataType, Doc};

pub(super) fn encode_path(path: &SourcePath) -> EngineResult<String> {
    serde_json::to_string(path).map_err(|error| {
        EngineError::storage_failure(format!("cannot encode identity path: {error}"))
    })
}

pub(super) fn decode_path(value: &str) -> EngineResult<SourcePath> {
    serde_json::from_str(value).map_err(|error| {
        EngineError::storage_failure(format!("cannot decode identity path: {error}"))
    })
}

/// Hex preserves the portable identity representation and needs no SQL escaping.
pub(super) fn path_key(path: &SourcePath) -> EngineResult<String> {
    Ok(hex::encode(encode_path(path)?.as_bytes()))
}

/// This projection accelerates path matching; it never establishes identity.
pub(super) fn query_path(path: &Path) -> Option<String> {
    let value = path.to_str()?;
    #[cfg(windows)]
    return Some(value.replace('\\', "/"));
    #[cfg(not(windows))]
    Some(value.to_owned())
}

pub(super) fn file_membership_schema(schema: &mut CollectionSchema) -> EngineResult<()> {
    scalar(
        schema,
        "ancestor_directory_ids",
        DataType::ArrayUint32,
        false,
        true,
    )?;
    wildcard_string(schema, "file_name", false)
}

/// Per-file membership values shared by every fragment projection of one file.
pub(super) struct FileMembership<'a> {
    ancestor_directory_ids: Vec<u32>,
    file_name: &'a str,
}

/// Build the reusable projection once per file instead of once per fragment.
pub(super) fn file_membership_projection<'a>(
    file: &'a FileRecord,
    directories: &[DirectoryId],
) -> FileMembership<'a> {
    FileMembership {
        ancestor_directory_ids: directories.iter().map(|id| id.get()).collect(),
        // Non-Unicode names have no STRING representation. The source-path cache disables name
        // pushdown in this workspace; exact native paths remain in FileRecord.
        file_name: file
            .relative_path()
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or(""),
    }
}

pub(super) fn write_file_membership(
    doc: &mut Doc,
    membership: &FileMembership<'_>,
) -> EngineResult<()> {
    native(
        doc.add_array_u32(
            "ancestor_directory_ids",
            &membership.ancestor_directory_ids,
        ),
        "encode ancestor directories",
    )?;
    native(
        doc.add_string("file_name", membership.file_name),
        "encode file name",
    )?;
    Ok(())
}

pub(super) fn file_membership_doc(
    doc: &mut Doc,
    file: &FileRecord,
    directories: &[DirectoryId],
) -> EngineResult<()> {
    write_file_membership(doc, &file_membership_projection(file, directories))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identity_keys_and_query_projections_use_portable_components() {
        let path = SourcePath::new(Path::new("images").join("中文 房间").join("living.jpg"))
            .expect("source path");
        let encoded = r#"{"components":["images","中文 房间","living.jpg"]}"#;
        assert_eq!(
            path_key(&path).expect("identity"),
            hex::encode(encoded.as_bytes())
        );
        assert_eq!(
            query_path(&path),
            Some("images/中文 房间/living.jpg".into())
        );
    }
}
