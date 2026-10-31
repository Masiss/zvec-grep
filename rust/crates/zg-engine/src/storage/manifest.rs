//! Additive per-file embedding-reuse manifests.
//!
//! A manifest is only an optimization hint. It is published after the native
//! file rows commit, and a missing or invalid manifest simply disables reuse.

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

const MANIFEST_VERSION: u32 = 2;
const MAX_FILE_MANIFEST_BYTES: usize = 4 * 1024 * 1024;
const MANIFEST_DIRECTORY: &str = "reconciliation";

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FileManifest {
    version: u32,
    file_id: u32,
    pub(crate) fragments: Vec<FragmentManifestEntry>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FragmentManifestEntry {
    pub(crate) fragment_id: String,
    pub(crate) model: String,
    pub(crate) embedding_fingerprint: String,
    /// Hash of the stored FP16-decoded vector. This binds the manifest to the
    /// exact bytes in the native collection, so a rolled-back vector cannot be
    /// reused merely because its fragment ID and input fingerprint match.
    pub(crate) vector_fingerprint: String,
}

impl FileManifest {
    pub(crate) fn new(file_id: FileId) -> Self {
        Self {
            version: MANIFEST_VERSION,
            file_id: file_id.get(),
            fragments: Vec::new(),
        }
    }

    pub(crate) fn file_id(&self) -> FileId {
        FileId::new(self.file_id)
    }

    pub(crate) fn normalize(&mut self) {
        self.fragments.sort_unstable_by(|left, right| {
            (&left.model, &left.fragment_id).cmp(&(&right.model, &right.fragment_id))
        });
    }

    pub(crate) fn find_embedding(
        &self,
        fragment_id: &str,
        model: &str,
        fingerprint: &str,
    ) -> Option<&FragmentManifestEntry> {
        self.fragments.iter().find(|entry| {
            entry.fragment_id == fragment_id
                && entry.model == model
                && entry.embedding_fingerprint == fingerprint
        })
    }

    fn validate(&self, expected_file_id: FileId) -> EngineResult<()> {
        if self.version != MANIFEST_VERSION || self.file_id != expected_file_id.get() {
            return Err(EngineError::storage_failure(
                "reconciliation manifest version or file identity is invalid",
            ));
        }
        let mut fragments = HashSet::with_capacity(self.fragments.len());
        for entry in &self.fragments {
            if !is_lower_hex(&entry.fragment_id)
                || !is_lower_hex(&entry.embedding_fingerprint)
                || !is_lower_hex(&entry.vector_fingerprint)
                || entry.model.is_empty()
                || !fragments.insert(entry.fragment_id.as_str())
            {
                return Err(EngineError::storage_failure(
                    "reconciliation manifest contains an invalid or duplicate fragment",
                ));
            }
        }
        Ok(())
    }

    fn encode_bounded(&mut self) -> EngineResult<Option<Vec<u8>>> {
        self.normalize();
        self.validate(self.file_id())?;
        let bytes = serde_json::to_vec(self).map_err(|error| {
            EngineError::storage_failure(format!("cannot encode reconciliation manifest: {error}"))
        })?;
        Ok((bytes.len() <= MAX_FILE_MANIFEST_BYTES).then_some(bytes))
    }
}

pub(crate) fn vector_fingerprint(vector: &[f32]) -> String {
    let mut bytes = Vec::with_capacity(vector.len().saturating_mul(4));
    for value in vector {
        bytes.extend_from_slice(&value.to_bits().to_le_bytes());
    }
    crate::utils::sha256_hex(&bytes)
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
            return Ok(());
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

    fn entry(id: &str, model: &str, fingerprint: &str) -> FragmentManifestEntry {
        FragmentManifestEntry {
            fragment_id: id.to_owned(),
            model: model.to_owned(),
            embedding_fingerprint: fingerprint.to_owned(),
            vector_fingerprint: "e".repeat(64),
        }
    }

    #[test]
    fn manifest_round_trips_and_requires_exact_fingerprint_match() {
        let mut manifest = FileManifest::new(FileId::new(7));
        manifest
            .fragments
            .push(entry("a".repeat(40).as_str(), "m", &"b".repeat(64)));
        let bytes = manifest.encode_bounded().expect("encode").expect("bounded");
        let mut decoded: FileManifest = serde_json::from_slice(&bytes).expect("decode");
        decoded.normalize();
        assert_eq!(decoded.file_id(), FileId::new(7));
        assert!(
            decoded
                .find_embedding(&"a".repeat(40), "m", &"b".repeat(64))
                .is_some()
        );
        assert!(
            decoded
                .find_embedding(&"a".repeat(40), "m", &"c".repeat(64))
                .is_none()
        );
    }

    #[test]
    fn manifest_store_publishes_loads_and_deletes_atomically() {
        let directory = tempfile::tempdir().expect("temp directory");
        let store = ManifestStore::open(directory.path(), false).expect("manifest store");
        let mut manifest = FileManifest::new(FileId::new(3));
        manifest
            .fragments
            .push(entry("c".repeat(40).as_str(), "m", &"d".repeat(64)));
        assert!(store.publish(&mut manifest).expect("publish"));
        assert_eq!(
            store
                .load(FileId::new(3))
                .expect("load")
                .expect("published manifest")
                .fragments
                .len(),
            1
        );
        store.delete(FileId::new(3)).expect("delete");
        assert!(store.load(FileId::new(3)).expect("load absent").is_none());
    }
}
