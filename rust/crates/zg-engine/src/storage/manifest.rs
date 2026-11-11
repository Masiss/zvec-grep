//! Additive per-file embedding-reuse manifests.
//!
//! A manifest is only an optimization hint. It is published after the native
//! file rows commit, and a missing or invalid manifest simply disables reuse.

use std::{
    collections::HashSet,
    fs,
    io::Read,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use crate::{
    EngineError, EngineResult,
    domain::FileId,
    utils::{atomic_write, directory_exists, open_regular_file, sync_directory},
};

const MANIFEST_VERSION: u32 = 2;
const COMPACT_MAGIC: &[u8; 4] = b"ZGM2";
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

    pub(crate) fn fragments(&self) -> &[FragmentManifestEntry] {
        &self.fragments
    }

    pub(crate) fn normalize(&mut self) {
        self.fragments.sort_unstable_by(|left, right| {
            (&left.model, &left.fragment_id).cmp(&(&right.model, &right.fragment_id))
        });
    }

    #[cfg(test)]
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
        if compact_encoded_len(self).is_none() {
            return Ok(None);
        }
        self.normalize();
        self.validate(self.file_id())?;
        encode_compact(self)
    }
}

fn decode_hex(value: &str, expected_bytes: usize) -> EngineResult<Vec<u8>> {
    if value.len() != expected_bytes * 2 || !is_lower_hex(value) {
        return Err(EngineError::storage_failure(
            "invalid reconciliation hex field",
        ));
    }
    hex::decode(value).map_err(|_| EngineError::storage_failure("invalid reconciliation hex field"))
}

fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 15) as usize] as char);
    }
    output
}

fn compact_encoded_len(manifest: &FileManifest) -> Option<(u32, usize)> {
    let count = u32::try_from(manifest.fragments.len()).ok()?;
    let mut encoded_len = 16_usize;
    for entry in &manifest.fragments {
        let model_len = entry.model.len();
        if model_len > usize::from(u16::MAX)
            || entry.fragment_id.len() != 40
            || entry.embedding_fingerprint.len() != 64
            || entry.vector_fingerprint.len() != 64
        {
            return None;
        }
        encoded_len = encoded_len.checked_add(86)?.checked_add(model_len)?;
        if encoded_len > MAX_FILE_MANIFEST_BYTES {
            return None;
        }
    }
    Some((count, encoded_len))
}

fn encode_compact(manifest: &FileManifest) -> EngineResult<Option<Vec<u8>>> {
    let Some((count, encoded_len)) = compact_encoded_len(manifest) else {
        return Ok(None);
    };
    let mut bytes = Vec::with_capacity(encoded_len);
    bytes.extend_from_slice(COMPACT_MAGIC);
    bytes.extend_from_slice(&MANIFEST_VERSION.to_le_bytes());
    bytes.extend_from_slice(&manifest.file_id.to_le_bytes());
    bytes.extend_from_slice(&count.to_le_bytes());
    for entry in &manifest.fragments {
        bytes.extend_from_slice(&decode_hex(&entry.fragment_id, 20)?);
        let model = entry.model.as_bytes();
        let length = u16::try_from(model.len())
            .map_err(|_| EngineError::storage_failure("reconciliation model name is too long"))?;
        bytes.extend_from_slice(&length.to_le_bytes());
        bytes.extend_from_slice(model);
        bytes.extend_from_slice(&decode_hex(&entry.embedding_fingerprint, 32)?);
        bytes.extend_from_slice(&decode_hex(&entry.vector_fingerprint, 32)?);
    }
    Ok(Some(bytes))
}

struct CompactReader<'a> {
    bytes: &'a [u8],
    cursor: usize,
}
impl<'a> CompactReader<'a> {
    fn take(&mut self, count: usize) -> Result<&'a [u8], ()> {
        let end = self.cursor.checked_add(count).ok_or(())?;
        let value = self.bytes.get(self.cursor..end).ok_or(())?;
        self.cursor = end;
        Ok(value)
    }
    fn u16(&mut self) -> Result<u16, ()> {
        Ok(u16::from_le_bytes(
            self.take(2)?.try_into().map_err(|_| ())?,
        ))
    }
    fn u32(&mut self) -> Result<u32, ()> {
        Ok(u32::from_le_bytes(
            self.take(4)?.try_into().map_err(|_| ())?,
        ))
    }
}

fn decode_compact(bytes: &[u8], expected: FileId) -> Result<FileManifest, ()> {
    let mut reader = CompactReader { bytes, cursor: 0 };
    if reader.take(4)? != COMPACT_MAGIC || reader.u32()? != MANIFEST_VERSION {
        return Err(());
    }
    let file_id = reader.u32()?;
    if file_id != expected.get() {
        return Err(());
    }
    let count = usize::try_from(reader.u32()?).map_err(|_| ())?;
    if count > bytes.len() / 87 {
        return Err(());
    }
    let mut fragments = Vec::with_capacity(count);
    for _ in 0..count {
        let fragment_id = encode_hex(reader.take(20)?);
        let model_len = usize::from(reader.u16()?);
        let model = std::str::from_utf8(reader.take(model_len)?)
            .map_err(|_| ())?
            .to_owned();
        let embedding_fingerprint = encode_hex(reader.take(32)?);
        let vector_fingerprint = encode_hex(reader.take(32)?);
        fragments.push(FragmentManifestEntry {
            fragment_id,
            model,
            embedding_fingerprint,
            vector_fingerprint,
        });
    }
    if reader.cursor != bytes.len() {
        return Err(());
    }
    let mut manifest = FileManifest {
        version: MANIFEST_VERSION,
        file_id,
        fragments,
    };
    manifest.validate(expected).map_err(|_| ())?;
    manifest.normalize();
    Ok(manifest)
}

pub(crate) fn vector_fingerprint(vector: &[f32]) -> String {
    // The native collection stores FP16. Hash the exact FP16 representation so
    // the published manifest matches the vector decoded from storage; hashing
    // the original f32 would reject every non-representable embedding on reuse.
    let mut bytes = Vec::with_capacity(vector.len().saturating_mul(2));
    for value in vector {
        bytes.extend_from_slice(&half::f16::from_f32(*value).to_bits().to_le_bytes());
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
        // Sidecars are auxiliary, but their directory must not be a symlink:
        // following it would read or write outside the index generation.
        let exists = directory_exists(&directory)?;
        if read_only {
            return Ok(Self {
                writable: false,
                directory,
            });
        }
        if !exists {
            fs::create_dir_all(&directory).map_err(|error| {
                EngineError::from_io(
                    format!(
                        "cannot create reconciliation manifest directory {}: {error}",
                        directory.display()
                    ),
                    &error,
                )
            })?;
        }
        Ok(Self {
            writable: true,
            directory,
        })
    }

    pub(crate) fn load(&self, file_id: FileId) -> Option<FileManifest> {
        let path = self.path(file_id);
        // Do not follow symlinks or read special files: a malicious or damaged
        // workspace must not turn a cache lookup into a blocking read.
        let metadata = match fs::symlink_metadata(&path) {
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_file() => {
                tracing::warn!(file_id = file_id.get(), path = %path.display(), "ignoring non-regular reconciliation manifest");
                return None;
            }
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return None,
            Err(error) => {
                tracing::warn!(file_id = file_id.get(), %error, "ignoring unreadable reconciliation manifest");
                return None;
            }
        };
        if metadata.len() > u64::try_from(MAX_FILE_MANIFEST_BYTES).unwrap_or(u64::MAX) {
            return None;
        }
        let file = match open_regular_file(&path) {
            Ok(file) => file,
            Err(error) => {
                tracing::warn!(file_id = file_id.get(), %error, "ignoring non-regular or unreadable reconciliation manifest");
                return None;
            }
        };
        if file.metadata().map_or(true, |metadata| {
            !metadata.is_file()
                || metadata.len() > u64::try_from(MAX_FILE_MANIFEST_BYTES).unwrap_or(u64::MAX)
        }) {
            return None;
        }
        let mut bytes = Vec::new();
        if let Err(error) = file
            .take(u64::try_from(MAX_FILE_MANIFEST_BYTES).unwrap_or(u64::MAX) + 1)
            .read_to_end(&mut bytes)
        {
            tracing::warn!(file_id = file_id.get(), %error, "ignoring unreadable reconciliation manifest");
            return None;
        }
        if bytes.len() > MAX_FILE_MANIFEST_BYTES {
            return None;
        }
        let mut manifest: FileManifest = if bytes.starts_with(COMPACT_MAGIC) {
            if let Ok(manifest) = decode_compact(&bytes, file_id) {
                manifest
            } else {
                tracing::warn!(
                    file_id = file_id.get(),
                    "ignoring invalid compact reconciliation manifest"
                );
                return None;
            }
        } else {
            match serde_json::from_slice(&bytes) {
                Ok(manifest) => manifest,
                Err(error) => {
                    tracing::warn!(file_id = file_id.get(), %error, "ignoring invalid reconciliation manifest");
                    return None;
                }
            }
        };
        if let Err(error) = manifest.validate(file_id) {
            tracing::warn!(file_id = file_id.get(), %error, "ignoring incompatible reconciliation manifest");
            return None;
        }
        manifest.normalize();
        Some(manifest)
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
        let decoded = decode_compact(&bytes, FileId::new(7)).expect("decode compact");
        let legacy_json = serde_json::to_vec(&decoded).expect("legacy JSON encoding");
        assert!(bytes.len() < legacy_json.len());
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
    fn malformed_compact_sidecars_are_nonfatal_cache_misses() {
        let directory = tempfile::tempdir().expect("temp directory");
        let store = ManifestStore::open(directory.path(), false).expect("manifest store");
        let file_id = FileId::new(23);
        let path = store.path(file_id);
        let mut manifest = FileManifest::new(file_id);
        manifest
            .fragments
            .push(entry("a".repeat(40).as_str(), "m", &"b".repeat(64)));
        let valid = manifest.encode_bounded().expect("encode").expect("bounded");
        assert_eq!(valid.len(), 16 + 20 + 2 + 1 + 32 + 32);

        let mut bad_magic = valid.clone();
        bad_magic[0] ^= 1;
        let mut bad_version = valid.clone();
        bad_version[4..8].copy_from_slice(&99_u32.to_le_bytes());
        let mut bad_identity = valid.clone();
        bad_identity[8..12].copy_from_slice(&99_u32.to_le_bytes());
        let truncated_header = b"ZGM2".to_vec();
        let mut truncated_entry = valid.clone();
        truncated_entry.pop();
        let mut invalid_utf8 = valid.clone();
        invalid_utf8[38] = 0xff;
        let mut huge_count = valid.clone();
        huge_count[12..16].copy_from_slice(&u32::MAX.to_le_bytes());
        let mut huge_model_length = valid.clone();
        huge_model_length[36..38].copy_from_slice(&u16::MAX.to_le_bytes());
        let mut trailing_bytes = valid;
        trailing_bytes.push(0);

        for malformed in [
            bad_magic,
            bad_version,
            bad_identity,
            truncated_header,
            truncated_entry,
            invalid_utf8,
            huge_count,
            huge_model_length,
            trailing_bytes,
        ] {
            fs::write(&path, malformed).expect("write malformed sidecar");
            assert!(store.load(file_id).is_none());
        }
    }

    #[test]
    fn oversized_compact_hints_are_skipped_before_binary_allocation() {
        let file_id = FileId::new(29);
        let mut long_model = FileManifest::new(file_id);
        long_model.fragments.push(FragmentManifestEntry {
            fragment_id: "a".repeat(40),
            model: "m".repeat(usize::from(u16::MAX) + 1),
            embedding_fingerprint: "b".repeat(64),
            vector_fingerprint: "c".repeat(64),
        });
        assert!(
            long_model
                .encode_bounded()
                .expect("optional oversized model")
                .is_none()
        );

        let mut oversized = FileManifest::new(file_id);
        for index in 0..65_u32 {
            oversized.fragments.push(FragmentManifestEntry {
                fragment_id: format!("{index:040x}"),
                model: "m".repeat(65_000),
                embedding_fingerprint: "b".repeat(64),
                vector_fingerprint: "c".repeat(64),
            });
        }
        assert!(
            oversized
                .encode_bounded()
                .expect("optional oversized manifest")
                .is_none()
        );
    }

    #[test]
    fn compact_format_preserves_model_input_and_fp16_vector_identity() {
        let file_id = FileId::new(17);
        let vector = [0.1_f32, -0.2, 1.25];
        let vector_fingerprint = vector_fingerprint(&vector);
        let mut manifest = FileManifest::new(file_id);
        manifest.fragments.push(FragmentManifestEntry {
            fragment_id: "a".repeat(40),
            model: "model-a".to_owned(),
            embedding_fingerprint: "b".repeat(64),
            vector_fingerprint: vector_fingerprint.clone(),
        });
        let bytes = manifest.encode_bounded().expect("encode").expect("bounded");
        let decoded = decode_compact(&bytes, file_id).expect("decode");
        let entry = decoded
            .find_embedding(&"a".repeat(40), "model-a", &"b".repeat(64))
            .expect("exact reusable identity");
        assert_eq!(entry.vector_fingerprint, vector_fingerprint);
        assert!(
            decoded
                .find_embedding(&"a".repeat(40), "model-b", &"b".repeat(64))
                .is_none()
        );
        assert!(
            decoded
                .find_embedding(&"a".repeat(40), "model-a", &"c".repeat(64))
                .is_none()
        );
    }

    #[test]
    fn vector_fingerprint_matches_the_fp16_representation() {
        let original = [0.1_f32, -0.2, 1.25, 0.333_333];
        let decoded = original
            .iter()
            .map(|value| half::f16::from_f32(*value).to_f32())
            .collect::<Vec<_>>();
        assert_eq!(vector_fingerprint(&original), vector_fingerprint(&decoded));

        // f32 values in the same FP16 bucket must hash identically; values in
        // different buckets must not. This is the invariant that lets reuse
        // compare a decoded stored vector with the original embedding.
        let near = [0.1_f32, 0.2, 0.3];
        let perturbed = [0.1_f32 + 1e-7, 0.2, 0.3];
        assert_ne!(near[0].to_bits(), perturbed[0].to_bits());
        assert_eq!(vector_fingerprint(&near), vector_fingerprint(&perturbed));
        let different = [0.1_f32, 0.2, 0.4];
        assert_ne!(vector_fingerprint(&near), vector_fingerprint(&different));
    }

    #[test]
    fn torn_or_incompatible_sidecar_is_ignored_and_can_be_republished() {
        let directory = tempfile::tempdir().expect("temp directory");
        let store = ManifestStore::open(directory.path(), false).expect("manifest store");
        let file_id = FileId::new(11);
        let path = store.path(file_id);
        let mut manifest = FileManifest::new(file_id);
        manifest
            .fragments
            .push(entry("c".repeat(40).as_str(), "m", &"d".repeat(64)));
        assert!(store.publish(&mut manifest).expect("publish"));
        assert!(
            fs::read(&path)
                .expect("compact bytes")
                .starts_with(COMPACT_MAGIC)
        );
        assert_eq!(
            store
                .load(file_id)
                .expect("published compact sidecar")
                .fragments
                .len(),
            1
        );

        // Simulate a torn write from an older/non-atomic writer and a future
        // schema version. Both are hints only: load must ignore them.
        fs::write(&path, b"{\"version\":2,\"file_id\":11").expect("torn sidecar");
        assert!(store.load(file_id).is_none());
        let incompatible = serde_json::json!({
            "version": 1,
            "file_id": 11,
            "fragments": []
        });
        fs::write(&path, serde_json::to_vec(&incompatible).expect("json")).expect("old sidecar");
        assert!(store.load(file_id).is_none());
        fs::write(&path, serde_json::to_vec(&manifest).expect("legacy json"))
            .expect("legacy v2 sidecar");
        assert_eq!(
            store.load(file_id).expect("legacy sidecar").fragments.len(),
            1
        );

        // A subsequent successful publish replaces the unusable sidecar.
        assert!(store.publish(&mut manifest).expect("republish"));
        assert_eq!(
            store
                .load(file_id)
                .expect("replacement manifest")
                .fragments
                .len(),
            1
        );
    }

    #[cfg(unix)]
    #[test]
    fn sidecar_directory_and_file_symlinks_are_not_followed() {
        let directory = tempfile::tempdir().expect("temp directory");
        let external = tempfile::tempdir().expect("external directory");
        let mut manifest = FileManifest::new(FileId::new(13));
        manifest
            .fragments
            .push(entry("c".repeat(40).as_str(), "m", &"d".repeat(64)));
        let bytes = manifest.encode_bounded().expect("encode").expect("bounded");
        let external_file = external.path().join("manifest.json");
        fs::write(&external_file, &bytes).expect("external manifest");

        let linked_directory = directory.path().join(MANIFEST_DIRECTORY);
        std::os::unix::fs::symlink(external.path(), &linked_directory).expect("directory symlink");
        assert!(ManifestStore::open(directory.path(), false).is_err());
        assert!(ManifestStore::open(directory.path(), true).is_err());

        fs::remove_file(&linked_directory).expect("remove directory symlink");
        fs::create_dir(&linked_directory).expect("manifest directory");
        let linked_file = linked_directory.join(format!("file-{:08x}.json", 13));
        std::os::unix::fs::symlink(&external_file, &linked_file).expect("file symlink");
        let store = ManifestStore::open(directory.path(), false).expect("manifest store");
        assert!(store.load(FileId::new(13)).is_none());
    }

    #[test]
    fn oversized_sidecar_is_ignored_without_blocking_reopen() {
        let directory = tempfile::tempdir().expect("temp directory");
        let store = ManifestStore::open(directory.path(), false).expect("manifest store");
        let path = store.path(FileId::new(12));
        let oversized = vec![b' '; MAX_FILE_MANIFEST_BYTES + 1];
        fs::write(&path, oversized).expect("oversized sidecar");
        assert!(store.load(FileId::new(12)).is_none());
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
                .expect("published manifest")
                .fragments
                .len(),
            1
        );
        store.delete(FileId::new(3)).expect("delete");
        assert!(store.load(FileId::new(3)).is_none());
    }
}
