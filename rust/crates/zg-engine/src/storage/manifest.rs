//! Versioned, bounded per-file reconciliation manifests.
//!
//! Manifests are published atomically after native rows are applied. The file's
//! `NotIndexed` state remains the visibility barrier if publication is interrupted.

use std::{
    collections::HashSet,
    fs,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use crate::{
    EngineError, EngineResult,
    domain::FileId,
    utils::{atomic_write, sync_directory},
};

const MANIFEST_VERSION: u32 = 1;
pub(crate) const MAX_FILE_MANIFEST_BYTES: usize = 4 * 1024 * 1024;
const MANIFEST_DIRECTORY: &str = "reconciliation";

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FileManifest {
    version: u32,
    file_id: u32,
    pub(crate) entities: Vec<EntityManifestEntry>,
    pub(crate) fragments: Vec<FragmentManifestEntry>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct EntityManifestEntry {
    pub(crate) entity_id: String,
    pub(crate) fingerprint: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FragmentManifestEntry {
    pub(crate) fragment_id: String,
    pub(crate) entity_id: String,
    pub(crate) model: String,
    pub(crate) embedding_fingerprint: String,
    pub(crate) projection_fingerprint: String,
}

impl FileManifest {
    pub(crate) fn new(file_id: FileId) -> Self {
        Self {
            version: MANIFEST_VERSION,
            file_id: file_id.get(),
            entities: Vec::new(),
            fragments: Vec::new(),
        }
    }

    pub(crate) fn file_id(&self) -> FileId {
        FileId::new(self.file_id)
    }

    pub(crate) fn normalize(&mut self) {
        self.entities
            .sort_unstable_by(|left, right| left.entity_id.cmp(&right.entity_id));
        self.fragments.sort_unstable_by(|left, right| {
            (&left.model, &left.embedding_fingerprint, &left.fragment_id).cmp(&(
                &right.model,
                &right.embedding_fingerprint,
                &right.fragment_id,
            ))
        });
    }

    pub(crate) fn find_embedding(
        &self,
        model: &str,
        fingerprint: &str,
    ) -> Option<&FragmentManifestEntry> {
        self.fragments
            .binary_search_by(|entry| {
                entry
                    .model
                    .as_str()
                    .cmp(model)
                    .then_with(|| entry.embedding_fingerprint.as_str().cmp(fingerprint))
            })
            .ok()
            .and_then(|index| self.fragments.get(index))
    }

    fn validate(&self, expected_file_id: FileId) -> EngineResult<()> {
        if self.version != MANIFEST_VERSION || self.file_id != expected_file_id.get() {
            return Err(EngineError::storage_failure(
                "reconciliation manifest version or file identity is invalid",
            ));
        }
        let expected_prefix = format!("{:08x}", expected_file_id.get());
        let mut entities = HashSet::with_capacity(self.entities.len());
        for entry in &self.entities {
            if entry.entity_id.len() != 32
                || !entry.entity_id.starts_with(&expected_prefix)
                || !is_lower_hex(&entry.entity_id)
                || !is_lower_hex(&entry.fingerprint)
                || !entities.insert(entry.entity_id.as_str())
            {
                return Err(EngineError::storage_failure(
                    "reconciliation manifest contains an invalid or duplicate entity",
                ));
            }
        }
        let mut fragments = HashSet::with_capacity(self.fragments.len());
        for entry in &self.fragments {
            if entry.fragment_id.len() != 40
                || !entry.fragment_id.starts_with(&entry.entity_id)
                || !is_lower_hex(&entry.fragment_id)
                || !entities.contains(entry.entity_id.as_str())
                || entry.model.is_empty()
                || !is_lower_hex(&entry.embedding_fingerprint)
                || !is_lower_hex(&entry.projection_fingerprint)
                || !fragments.insert(entry.fragment_id.as_str())
            {
                return Err(EngineError::storage_failure(
                    "reconciliation manifest contains an invalid or duplicate fragment",
                ));
            }
        }
        Ok(())
    }

    pub(crate) fn encode_bounded(&mut self) -> EngineResult<Option<Vec<u8>>> {
        self.normalize();
        self.validate(self.file_id())?;
        let bytes = serde_json::to_vec(self).map_err(|error| {
            EngineError::storage_failure(format!("cannot encode reconciliation manifest: {error}"))
        })?;
        Ok((bytes.len() <= MAX_FILE_MANIFEST_BYTES).then_some(bytes))
    }
}

fn is_lower_hex(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

pub(crate) struct ManifestStore {
    directory: PathBuf,
    writable: bool,
}

impl ManifestStore {
    pub(crate) fn open(storage_root: &Path, read_only: bool) -> EngineResult<Self> {
        let directory = storage_root.join(MANIFEST_DIRECTORY);
        if read_only {
            return Ok(Self {
                writable: false,
                directory,
            });
        }
        fs::create_dir_all(&directory).map_err(|error| {
            EngineError::from_io(
                format!(
                    "cannot create reconciliation manifest directory {}: {error}",
                    directory.display()
                ),
                &error,
            )
        })?;
        Ok(Self {
            writable: true,
            directory,
        })
    }

    pub(crate) fn load(&self, file_id: FileId) -> EngineResult<Option<FileManifest>> {
        let path = self.path(file_id);
        let metadata = match fs::metadata(&path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => {
                return Err(EngineError::from_io(
                    format!(
                        "cannot inspect reconciliation manifest {}: {error}",
                        path.display()
                    ),
                    &error,
                ));
            }
        };
        if usize::try_from(metadata.len()).unwrap_or(usize::MAX) > MAX_FILE_MANIFEST_BYTES {
            return Ok(None);
        }
        let bytes = fs::read(&path).map_err(|error| {
            EngineError::from_io(
                format!(
                    "cannot read reconciliation manifest {}: {error}",
                    path.display()
                ),
                &error,
            )
        })?;
        let mut manifest: FileManifest = match serde_json::from_slice(&bytes) {
            Ok(manifest) => manifest,
            Err(error) => {
                tracing::warn!(file_id = file_id.get(), %error, "ignoring invalid reconciliation manifest");
                return Ok(None);
            }
        };
        if let Err(error) = manifest.validate(file_id) {
            tracing::warn!(file_id = file_id.get(), %error, "ignoring incompatible reconciliation manifest");
            return Ok(None);
        }
        manifest.normalize();
        Ok(Some(manifest))
    }

    pub(crate) fn publish(&self, manifest: &mut FileManifest) -> EngineResult<bool> {
        if !self.writable {
            return Err(EngineError::permission_denied(
                "cannot publish a reconciliation manifest in read-only storage",
            ));
        }
        let Some(bytes) = manifest.encode_bounded()? else {
            return Ok(false);
        };
        atomic_write(&self.path(manifest.file_id()), &bytes)?;
        Ok(true)
    }

    pub(crate) fn delete(&self, file_id: FileId) -> EngineResult<()> {
        if !self.writable {
            return Err(EngineError::permission_denied(
                "cannot delete a reconciliation manifest in read-only storage",
            ));
        }
        let path = self.path(file_id);
        match fs::remove_file(&path) {
            Ok(()) => sync_directory(&self.directory),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(EngineError::from_io(
                format!(
                    "cannot delete reconciliation manifest {}: {error}",
                    path.display()
                ),
                &error,
            )),
        }
    }

    fn path(&self, file_id: FileId) -> PathBuf {
        self.directory
            .join(format!("file-{:08x}.json", file_id.get()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::FileId;

    #[test]
    fn manifest_round_trips_and_looks_up_embeddings_independently_of_fragment_identity() {
        let mut manifest = FileManifest::new(FileId::new(7));
        let entity_id = format!("00000007{}", "1".repeat(24));
        let fragment_id = format!("{entity_id}00000000");
        manifest.entities.push(EntityManifestEntry {
            entity_id: entity_id.clone(),
            fingerprint: "c".repeat(64),
        });
        manifest.fragments.push(FragmentManifestEntry {
            fragment_id: fragment_id.clone(),
            entity_id,
            model: "provider/model".to_owned(),
            embedding_fingerprint: "a".repeat(64),
            projection_fingerprint: "b".repeat(64),
        });
        let bytes = manifest
            .encode_bounded()
            .expect("encode")
            .expect("within quota");
        let mut decoded: FileManifest = serde_json::from_slice(&bytes).expect("decode");
        decoded.normalize();
        let found = decoded
            .find_embedding("provider/model", &"a".repeat(64))
            .expect("embedding fingerprint lookup");
        assert_eq!(found.fragment_id, fragment_id);
        assert_eq!(decoded.file_id(), FileId::new(7));
    }

    #[test]
    fn manifest_store_atomically_round_trips_and_deletes_file_manifests() {
        let directory = tempfile::tempdir().expect("temp directory");
        let store = ManifestStore::open(directory.path(), false).expect("manifest store");
        let mut manifest = FileManifest::new(FileId::new(3));
        manifest.entities.push(EntityManifestEntry {
            entity_id: format!("00000003{}", "2".repeat(24)),
            fingerprint: "d".repeat(64),
        });
        assert!(store.publish(&mut manifest).expect("publish"));
        assert_eq!(
            store
                .load(FileId::new(3))
                .expect("load")
                .expect("published manifest")
                .entities
                .len(),
            1
        );
        store.delete(FileId::new(3)).expect("delete");
        assert!(store.load(FileId::new(3)).expect("load absent").is_none());
    }

    #[test]
    fn oversized_manifest_is_not_published_and_legacy_read_only_storage_is_optional() {
        let directory = tempfile::tempdir().expect("temp directory");
        let store = ManifestStore::open(directory.path(), false).expect("manifest store");
        let mut manifest = FileManifest::new(FileId::new(1));
        manifest.entities.push(EntityManifestEntry {
            entity_id: format!("00000001{}", "3".repeat(24)),
            fingerprint: "a".repeat(MAX_FILE_MANIFEST_BYTES),
        });
        assert!(!store.publish(&mut manifest).expect("bounded publish"));

        let legacy_path = directory.path().join("legacy");
        let read_only =
            ManifestStore::open(&legacy_path, true).expect("read-only legacy manifest store");
        assert!(
            read_only
                .load(FileId::new(1))
                .expect("no manifest")
                .is_none()
        );
    }
}
