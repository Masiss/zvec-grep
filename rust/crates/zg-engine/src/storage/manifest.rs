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

const LEGACY_MANIFEST_VERSION: u32 = 1;
const MANIFEST_VERSION: u32 = 2;
const MANIFEST_MAGIC: &[u8; 4] = b"ZGM2";
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
        if !matches!(self.version, LEGACY_MANIFEST_VERSION | MANIFEST_VERSION)
            || self.file_id != expected_file_id.get()
        {
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
        let bytes = self.encode_compact()?;
        Ok((bytes.len() <= MAX_FILE_MANIFEST_BYTES).then_some(bytes))
    }

    fn encode_compact(&self) -> EngineResult<Vec<u8>> {
        let entity_len =
            self.entities.len().checked_mul(48).ok_or_else(|| {
                EngineError::storage_failure("reconciliation manifest is too large")
            })?;
        let mut bytes = Vec::with_capacity(16 + entity_len + self.fragments.len() * 128);
        bytes.extend_from_slice(MANIFEST_MAGIC);
        bytes.extend_from_slice(&MANIFEST_VERSION.to_le_bytes());
        bytes.extend_from_slice(&self.file_id.to_le_bytes());
        let entity_count = u32::try_from(self.entities.len()).map_err(|_| {
            EngineError::storage_failure("reconciliation manifest has too many entities")
        })?;
        bytes.extend_from_slice(&entity_count.to_le_bytes());
        for entry in &self.entities {
            bytes.extend_from_slice(&decode_hex(&entry.entity_id, 16)?);
            bytes.extend_from_slice(&decode_hex(&entry.fingerprint, 32)?);
        }
        let fragment_count = u32::try_from(self.fragments.len()).map_err(|_| {
            EngineError::storage_failure("reconciliation manifest has too many fragments")
        })?;
        bytes.extend_from_slice(&fragment_count.to_le_bytes());
        for entry in &self.fragments {
            bytes.extend_from_slice(&decode_hex(&entry.fragment_id, 20)?);
            bytes.extend_from_slice(&decode_hex(&entry.entity_id, 16)?);
            let model = entry.model.as_bytes();
            let model_len = u16::try_from(model.len()).map_err(|_| {
                EngineError::storage_failure("reconciliation manifest model is too long")
            })?;
            bytes.extend_from_slice(&model_len.to_le_bytes());
            bytes.extend_from_slice(model);
            bytes.extend_from_slice(&decode_hex(&entry.embedding_fingerprint, 32)?);
            bytes.extend_from_slice(&decode_hex(&entry.projection_fingerprint, 32)?);
        }
        Ok(bytes)
    }
}

fn decode_hex(value: &str, expected_bytes: usize) -> EngineResult<Vec<u8>> {
    if value.len() != expected_bytes * 2 || !is_lower_hex(value) {
        return Err(EngineError::storage_failure(
            "reconciliation manifest contains an invalid hex value",
        ));
    }
    let mut bytes = Vec::with_capacity(expected_bytes);
    for pair in value.as_bytes().chunks_exact(2) {
        let high = (pair[0] as char).to_digit(16).unwrap_or_default();
        let low = (pair[1] as char).to_digit(16).unwrap_or_default();
        bytes.push(((high << 4) | low) as u8);
    }
    Ok(bytes)
}

fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(HEX[(byte >> 4) as usize] as char);
        out.push(HEX[(byte & 0x0f) as usize] as char);
    }
    out
}

struct ManifestReader<'a> {
    bytes: &'a [u8],
    position: usize,
}

impl<'a> ManifestReader<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, position: 0 }
    }

    fn take(&mut self, length: usize) -> Result<&'a [u8], String> {
        let end = self
            .position
            .checked_add(length)
            .ok_or_else(|| "manifest length overflow".to_owned())?;
        let slice = self
            .bytes
            .get(self.position..end)
            .ok_or_else(|| "manifest is truncated".to_owned())?;
        self.position = end;
        Ok(slice)
    }

    fn u16(&mut self) -> Result<u16, String> {
        let bytes: [u8; 2] = self.take(2)?.try_into().map_err(|_| "invalid u16")?;
        Ok(u16::from_le_bytes(bytes))
    }

    fn u32(&mut self) -> Result<u32, String> {
        let bytes: [u8; 4] = self.take(4)?.try_into().map_err(|_| "invalid u32")?;
        Ok(u32::from_le_bytes(bytes))
    }

    fn hex(&mut self, length: usize) -> Result<String, String> {
        Ok(encode_hex(self.take(length)?))
    }
}

fn decode_compact(bytes: &[u8], expected_file_id: FileId) -> Result<FileManifest, String> {
    let mut reader = ManifestReader::new(bytes);
    if reader.take(MANIFEST_MAGIC.len())? != MANIFEST_MAGIC {
        return Err("invalid manifest magic".to_owned());
    }
    let version = reader.u32()?;
    if version != MANIFEST_VERSION {
        return Err("unsupported compact manifest version".to_owned());
    }
    let file_id = reader.u32()?;
    if file_id != expected_file_id.get() {
        return Err("manifest file identity mismatch".to_owned());
    }
    let entity_count = reader.u32()? as usize;
    if entity_count > bytes.len() / 48 {
        return Err("manifest entity count exceeds payload".to_owned());
    }
    let mut entities = Vec::with_capacity(entity_count);
    for _ in 0..entity_count {
        entities.push(EntityManifestEntry {
            entity_id: reader.hex(16)?,
            fingerprint: reader.hex(32)?,
        });
    }
    let fragment_count = reader.u32()? as usize;
    if fragment_count > bytes.len() / 120 {
        return Err("manifest fragment count exceeds payload".to_owned());
    }
    let mut fragments = Vec::with_capacity(fragment_count);
    for _ in 0..fragment_count {
        let fragment_id = reader.hex(20)?;
        let entity_id = reader.hex(16)?;
        let model_len = reader.u16()? as usize;
        let model = std::str::from_utf8(reader.take(model_len)?)
            .map_err(|_| "manifest model is not UTF-8".to_owned())?
            .to_owned();
        fragments.push(FragmentManifestEntry {
            fragment_id,
            entity_id,
            model,
            embedding_fingerprint: reader.hex(32)?,
            projection_fingerprint: reader.hex(32)?,
        });
    }
    if reader.position != bytes.len() {
        return Err("manifest has trailing bytes".to_owned());
    }
    Ok(FileManifest {
        version,
        file_id,
        entities,
        fragments,
    })
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
        match self.load_path(&self.path(file_id), file_id)? {
            Some(manifest) => return Ok(Some(manifest)),
            None if self.path(file_id).exists() => return Ok(None),
            None => {}
        }
        self.load_path(&self.legacy_path(file_id), file_id)
    }

    fn load_path(&self, path: &Path, file_id: FileId) -> EngineResult<Option<FileManifest>> {
        let metadata = match fs::metadata(path) {
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
        let bytes = fs::read(path).map_err(|error| {
            EngineError::from_io(
                format!(
                    "cannot read reconciliation manifest {}: {error}",
                    path.display()
                ),
                &error,
            )
        })?;
        let mut manifest = if bytes.starts_with(MANIFEST_MAGIC) {
            match decode_compact(&bytes, file_id) {
                Ok(manifest) => manifest,
                Err(error) => {
                    tracing::warn!(file_id = file_id.get(), %error, "ignoring invalid reconciliation manifest");
                    return Ok(None);
                }
            }
        } else {
            match serde_json::from_slice::<FileManifest>(&bytes) {
                Ok(manifest) => manifest,
                Err(error) => {
                    tracing::warn!(file_id = file_id.get(), %error, "ignoring invalid reconciliation manifest");
                    return Ok(None);
                }
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
        match fs::remove_file(self.legacy_path(manifest.file_id())) {
            Ok(()) => sync_directory(&self.directory),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(EngineError::from_io(
                format!(
                    "cannot remove legacy reconciliation manifest {}: {error}",
                    self.legacy_path(manifest.file_id()).display()
                ),
                &error,
            )),
        }?;
        Ok(true)
    }

    pub(crate) fn delete(&self, file_id: FileId) -> EngineResult<()> {
        if !self.writable {
            return Err(EngineError::permission_denied(
                "cannot delete a reconciliation manifest in read-only storage",
            ));
        }
        let mut removed = false;
        for path in [self.path(file_id), self.legacy_path(file_id)] {
            match fs::remove_file(&path) {
                Ok(()) => removed = true,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(error) => {
                    return Err(EngineError::from_io(
                        format!(
                            "cannot delete reconciliation manifest {}: {error}",
                            path.display()
                        ),
                        &error,
                    ));
                }
            }
        }
        if removed {
            sync_directory(&self.directory)?;
        }
        Ok(())
    }

    fn path(&self, file_id: FileId) -> PathBuf {
        self.directory
            .join(format!("file-{:08x}.bin", file_id.get()))
    }

    fn legacy_path(&self, file_id: FileId) -> PathBuf {
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
        assert!(bytes.len() < 2_000, "compact manifest should be small");
        let mut decoded = decode_compact(&bytes, FileId::new(7)).expect("decode");
        decoded.normalize();
        let found = decoded
            .find_embedding("provider/model", &"a".repeat(64))
            .expect("embedding fingerprint lookup");
        assert_eq!(found.fragment_id, fragment_id);
        assert_eq!(decoded.file_id(), FileId::new(7));
    }

    #[test]
    fn compact_manifest_rejects_truncated_and_trailing_bytes() {
        let mut manifest = FileManifest::new(FileId::new(7));
        manifest.entities.push(EntityManifestEntry {
            entity_id: format!("00000007{}", "1".repeat(24)),
            fingerprint: "c".repeat(64),
        });
        let bytes = manifest
            .encode_bounded()
            .expect("encode")
            .expect("within quota");
        assert!(decode_compact(&bytes[..bytes.len() - 1], FileId::new(7)).is_err());
        let mut trailing = bytes.clone();
        trailing.push(0);
        assert!(decode_compact(&trailing, FileId::new(7)).is_err());
    }

    #[test]
    fn legacy_json_manifest_remains_readable() {
        let directory = tempfile::tempdir().expect("temp directory");
        let store = ManifestStore::open(directory.path(), false).expect("manifest store");
        let mut legacy = FileManifest::new(FileId::new(5));
        legacy.version = LEGACY_MANIFEST_VERSION;
        legacy.entities.push(EntityManifestEntry {
            entity_id: format!("00000005{}", "4".repeat(24)),
            fingerprint: "e".repeat(64),
        });
        let bytes = serde_json::to_vec(&legacy).expect("legacy encode");
        fs::write(store.legacy_path(FileId::new(5)), bytes).expect("legacy write");
        let loaded = store
            .load(FileId::new(5))
            .expect("legacy load")
            .expect("legacy manifest");
        assert_eq!(loaded.entities.len(), 1);
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
        for index in 0..100_000_u32 {
            manifest.entities.push(EntityManifestEntry {
                entity_id: format!("00000001{index:024x}"),
                fingerprint: "a".repeat(64),
            });
        }
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
