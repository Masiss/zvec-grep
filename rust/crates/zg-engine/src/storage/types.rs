//! Data exchanged with index storage operations.

use std::{
    collections::HashMap,
    path::{Path, PathBuf},
};

use crate::domain::{
    CodeMetadata, Entity, EntityFragment, EntityId, FileId, FileRecord, FragmentId, Range,
    SourcePath, SymbolType, model::EmbeddingModelInfo,
};

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum WorkspaceIndexStorageOptions {
    ReadOnly {
        storage_path: PathBuf,
    },
    ReadWrite {
        storage_path: PathBuf,
        embeddings: Vec<EmbeddingModelInfo>,
    },
}

impl WorkspaceIndexStorageOptions {
    pub(crate) fn storage_path(&self) -> &Path {
        match self {
            Self::ReadOnly { storage_path, .. } | Self::ReadWrite { storage_path, .. } => {
                storage_path
            }
        }
    }

    pub(crate) const fn is_read_only(&self) -> bool {
        matches!(self, Self::ReadOnly { .. })
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct StoredEntity {
    pub entity: Entity,
    pub file: FileRecord,
}

/// Canonical definition metadata for resolution, without source bodies or fragments.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct ResolutionCandidate {
    pub entity_id: EntityId,
    pub file: FileRecord,
    pub metadata: crate::domain::CodeMetadata,
}

/// File attributes needed by query filters, without content hashes or index status.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct StoredFileAttributes {
    pub id: FileId,
    pub relative_path: PathBuf,
    pub modified_epoch_ms: Option<u64>,
}

impl From<&FileRecord> for StoredFileAttributes {
    fn from(file: &FileRecord) -> Self {
        Self {
            id: file.id(),
            relative_path: file.relative_path().to_path_buf(),
            modified_epoch_ms: file.snapshot().modified_epoch_ms,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct IndexedFragment {
    pub entity_id: EntityId,
    pub fragment_id: FragmentId,
    /// Unique configured embedding model reference (provider/name).
    pub model: String,
    pub vector: Vec<f32>,
    pub fts_text: String,
}

/// One exact-name declaration with its source location, without content.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct SymbolDeclaration {
    pub entity_id: EntityId,
    pub file: FileRecord,
    pub metadata: CodeMetadata,
    pub source_range: Range,
}

/// Metadata-only ordering key for exact-symbol continuation.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd, serde::Deserialize, serde::Serialize)]
pub(crate) struct SymbolCursorKey {
    pub relative_path: String,
    pub scope: Option<String>,
    pub symbol_type: Option<String>,
    pub signature: Option<String>,
    pub entity_id: String,
}

impl SymbolDeclaration {
    pub(crate) fn cursor_key(&self) -> SymbolCursorKey {
        SymbolCursorKey {
            relative_path: normalized_relative_path(&self.file),
            scope: self.metadata.scope.clone(),
            symbol_type: self
                .metadata
                .symbol_type
                .map(|kind| kind.as_str().to_owned()),
            signature: self.metadata.signature.clone(),
            entity_id: self.entity_id.as_str().to_owned(),
        }
    }
}

pub(crate) fn normalized_relative_path(file: &FileRecord) -> String {
    file.relative_path()
        .as_path()
        .to_string_lossy()
        .replace('\\', "/")
}

/// Workspace-level completeness of the index the page was read from.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct SymbolFileCoverage {
    pub indexed: u64,
    pub unfinished: u64,
    pub failed: u64,
}

/// Bounded exact-symbol page plus the total number of matching declarations.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct SymbolDeclarationPage {
    pub total: usize,
    pub items: Vec<SymbolDeclaration>,
    pub has_more: bool,
    pub coverage: SymbolFileCoverage,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct StorageSearchFilter {
    pub path: Option<StoragePathFilter>,
    pub file_ids: Option<Vec<FileId>>,
    pub entity_ids: Option<Vec<EntityId>>,
    pub symbol_names: Option<Vec<String>>,
    pub symbol_types: Option<Vec<SymbolType>>,
}

/// Boolean predicates over metadata projected into each model collection.
#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum StoragePathFilter {
    All,
    None,
    Directory(SourcePath),
    FileNameExact(String),
    FileNamePrefix(String),
    FileNameSuffix(String),
    And(Vec<Self>),
    Or(Vec<Self>),
    Not(Box<Self>),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum StorageSearchPath {
    Fts,
    Vector,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct StorageSearchHit {
    pub document_id: String,
    pub entity_id: EntityId,
    pub file_id: FileId,
    pub path: StorageSearchPath,
    pub score: f64,
}

#[derive(Default)]
pub(crate) struct StoredSearchData {
    pub entities: HashMap<EntityId, StoredEntity>,
    pub fragments: HashMap<String, EntityFragment>,
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use super::WorkspaceIndexStorageOptions;

    #[test]
    fn storage_options_keep_mode_and_path_together() {
        let path = PathBuf::from("workspace-index");
        let options = WorkspaceIndexStorageOptions::ReadOnly {
            storage_path: path.clone(),
        };

        assert!(options.is_read_only());
        assert_eq!(options.storage_path(), path);
    }
}
