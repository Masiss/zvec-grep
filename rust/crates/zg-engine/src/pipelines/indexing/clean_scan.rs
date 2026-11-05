//! Conservative clean-scan cache. Every accepted hit re-hashes all source and
//! selected storage bytes; uncertain files or hints simply disable the hit.

use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::{
    EngineError, EngineResult,
    api::index::IndexOptions,
    domain::Workspace,
    utils::{atomic_write, sync_directory},
};

const FILE_NAME: &str = "clean-scan.json";
const VERSION: u32 = 1;
const MAX_HINT_BYTES: u64 = 4096;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    version: u32,
    source: String,
    storage: String,
    settings: String,
}

fn path(home: &Path) -> PathBuf {
    home.join(FILE_NAME)
}

fn digest(bytes: &[u8]) -> String {
    let mut hash = Sha256::new();
    hash.update(bytes);
    hex::encode(hash.finalize())
}

fn settings_fingerprint(options: &IndexOptions, workspace: &Workspace) -> EngineResult<String> {
    let env_value = |name| std::env::var_os(name).map(|value| value.to_string_lossy().into_owned());
    let mut normalized_options = options.clone();
    // The service resolves an omitted concurrency value before indexing; it is
    // represented by the environment variables below in both pre/post seals.
    normalized_options.embedding_concurrency = None;
    let settings = serde_json::to_vec(&(
        normalized_options,
        workspace.scan(),
        workspace.index_version(),
        workspace.storage_generation(),
        workspace.index(),
        env_value("ZVEC_GREP_EMBEDDING_CONCURRENCY"),
        env_value("ZVEC_GREP_LLAMA_CPP_EMBEDDING_CONCURRENCY"),
        env_value("ZVEC_GREP_MODEL_COMPUTE_THREADS"),
        env_value("ZVEC_GREP_EMBEDDING"),
        env_value("ZVEC_GREP_MODEL_CACHE_CAPACITY"),
        env_value("ZVEC_GREP_MODEL_IDLE_TIMEOUT_SECONDS"),
        std::thread::available_parallelism()
            .map(std::num::NonZeroUsize::get)
            .ok(),
    ))
    .map_err(|error| {
        EngineError::internal(format!("cannot fingerprint clean-scan settings: {error}"))
    })?;
    Ok(digest(&settings))
}

fn feed_path(hash: &mut Sha256, root: &Path, entry: &Path) -> EngineResult<()> {
    let relative = entry
        .strip_prefix(root)
        .map_err(|error| EngineError::internal(format!("clean-scan path escaped root: {error}")))?;
    #[cfg(unix)]
    let bytes = {
        use std::os::unix::ffi::OsStrExt;
        relative.as_os_str().as_bytes()
    };
    #[cfg(not(unix))]
    let bytes = relative.to_string_lossy().as_bytes();
    hash.update(u64::try_from(bytes.len()).unwrap_or(u64::MAX).to_le_bytes());
    hash.update(bytes);
    Ok(())
}

fn tree_entries(
    root: &Path,
    excluded: Option<&Path>,
) -> EngineResult<Vec<(PathBuf, fs::Metadata)>> {
    let mut entries = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(directory) = pending.pop() {
        let mut children = fs::read_dir(&directory)
            .map_err(|error| {
                EngineError::from_io(
                    format!(
                        "cannot scan clean-scan directory {}: {error}",
                        directory.display()
                    ),
                    &error,
                )
            })?
            .map(|entry| entry.map(|entry| entry.path()))
            .collect::<std::io::Result<Vec<_>>>()
            .map_err(|error| {
                EngineError::from_io("cannot enumerate clean-scan directory", &error)
            })?;
        children.sort();
        for entry in children {
            if excluded.is_some_and(|excluded| entry == excluded) {
                continue;
            }
            let metadata = fs::symlink_metadata(&entry).map_err(|error| {
                EngineError::from_io(
                    format!(
                        "cannot inspect clean-scan entry {}: {error}",
                        entry.display()
                    ),
                    &error,
                )
            })?;
            entries.push((entry.clone(), metadata.clone()));
            if metadata.is_dir() {
                pending.push(entry);
            }
        }
    }
    entries.sort_by(|left, right| left.0.cmp(&right.0));
    Ok(entries)
}

fn same_file(left: &fs::Metadata, right: &fs::Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        left.dev() == right.dev()
            && left.ino() == right.ino()
            && left.len() == right.len()
            && left.mtime() == right.mtime()
            && left.mtime_nsec() == right.mtime_nsec()
            && left.ctime() == right.ctime()
            && left.ctime_nsec() == right.ctime_nsec()
    }
    #[cfg(not(unix))]
    {
        left.len() == right.len() && left.modified().ok() == right.modified().ok()
    }
}

fn hash_metadata(hash: &mut Sha256, metadata: &fs::Metadata) -> EngineResult<()> {
    use std::time::UNIX_EPOCH;

    hash.update(metadata.len().to_le_bytes());
    let modified = metadata.modified().map_err(|error| {
        EngineError::from_io("cannot read clean-scan modification time", &error)
    })?;
    let modified = modified.duration_since(UNIX_EPOCH).map_err(|error| {
        EngineError::internal(format!(
            "clean-scan modification time predates epoch: {error}"
        ))
    })?;
    hash.update(modified.as_secs().to_le_bytes());
    hash.update(modified.subsec_nanos().to_le_bytes());
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        hash.update(metadata.dev().to_le_bytes());
        hash.update(metadata.ino().to_le_bytes());
        hash.update(metadata.ctime().to_le_bytes());
        hash.update(metadata.ctime_nsec().to_le_bytes());
    }
    Ok(())
}

fn hash_tree(root: &Path, excluded: Option<&Path>, allow_symlinks: bool) -> EngineResult<String> {
    let root_meta = fs::symlink_metadata(root).map_err(|error| {
        EngineError::from_io(
            format!("cannot inspect clean-scan root {}: {error}", root.display()),
            &error,
        )
    })?;
    if root_meta.file_type().is_symlink() || !root_meta.is_dir() {
        return Err(EngineError::storage_failure(
            "clean-scan root is not a regular directory",
        ));
    }
    let entries = tree_entries(root, excluded)?;
    let mut hash = Sha256::new();
    hash.update(b"tree-root");
    hash_metadata(&mut hash, &root_meta)?;
    for (entry, metadata) in entries {
        let kind = metadata.file_type();
        feed_path(&mut hash, root, &entry)?;
        hash_metadata(&mut hash, &metadata)?;
        if kind.is_symlink() {
            if !allow_symlinks {
                return Err(EngineError::storage_failure(
                    "symlink in selected storage prevents a clean-scan seal",
                ));
            }
            hash.update(b"symlink");
            let target = fs::read_link(&entry)
                .map_err(|error| EngineError::from_io("cannot read clean-scan symlink", &error))?;
            #[cfg(unix)]
            let target_bytes = {
                use std::os::unix::ffi::OsStrExt;
                target.as_os_str().as_bytes()
            };
            #[cfg(not(unix))]
            let target_bytes = target.to_string_lossy().as_bytes();
            hash.update(
                u64::try_from(target_bytes.len())
                    .unwrap_or(u64::MAX)
                    .to_le_bytes(),
            );
            hash.update(target_bytes);
        } else if kind.is_dir() {
            hash.update(b"directory");
        } else if kind.is_file() {
            hash.update(b"file");
            let mut file = fs::File::open(&entry)
                .map_err(|error| EngineError::from_io("cannot open clean-scan file", &error))?;
            let before = file
                .metadata()
                .map_err(|error| EngineError::from_io("cannot stat clean-scan file", &error))?;
            if !same_file(&before, &metadata) {
                return Err(EngineError::storage_failure(
                    "clean-scan file identity changed while opening",
                ));
            }
            let mut buffer = vec![0_u8; 64 * 1024].into_boxed_slice();
            loop {
                let count = file
                    .read(&mut buffer)
                    .map_err(|error| EngineError::from_io("cannot hash clean-scan file", &error))?;
                if count == 0 {
                    break;
                }
                hash.update(&buffer[..count]);
            }
            let after = file
                .metadata()
                .map_err(|error| EngineError::from_io("cannot restat clean-scan file", &error))?;
            if !same_file(&before, &after) {
                return Err(EngineError::storage_failure(
                    "clean-scan file changed while hashing",
                ));
            }
        } else {
            return Err(EngineError::storage_failure(
                "unsupported special file in clean-scan tree",
            ));
        }
    }
    Ok(hex::encode(hash.finalize()))
}

fn read(home: &Path) -> Option<Snapshot> {
    let target = path(home);
    let metadata = fs::symlink_metadata(&target).ok()?;
    if metadata.file_type().is_symlink() || !metadata.is_file() || metadata.len() > MAX_HINT_BYTES {
        return None;
    }
    let file = fs::File::open(target).ok()?;
    if !file.metadata().ok()?.is_file() {
        return None;
    }
    let mut bytes = Vec::new();
    file.take(MAX_HINT_BYTES + 1).read_to_end(&mut bytes).ok()?;
    if bytes.len() > usize::try_from(MAX_HINT_BYTES).ok()? {
        return None;
    }
    let snapshot: Snapshot = serde_json::from_slice(&bytes).ok()?;
    (snapshot.version == VERSION).then_some(snapshot)
}

pub(crate) fn eligible(options: &IndexOptions) -> bool {
    !options.rebuild
        && !options.reset_paths
        && options.changes.is_empty()
        && options.scan == crate::api::index::options::ScanRulesUpdate::default()
        && options.embedding.is_none()
        && options.embedding_concurrency.is_none()
        && !options.allow_remote
        && options.authorized_remote.is_empty()
        && options.api_key.is_none()
        && options.runtime_api_key.is_none()
        && options.endpoint.is_none()
        && options.device.is_none()
        && options.runtime_device.is_none()
        && options.model_cache.is_none()
        && options.runtime_model_cache.is_none()
}

pub(crate) fn matches(
    home: &Path,
    source_root: &Path,
    storage_root: &Path,
    workspace: &Workspace,
    options: &IndexOptions,
) -> bool {
    let Some(snapshot) = read(home) else {
        return false;
    };
    // The workspace metadata directory is a direct child of the source root.
    let (Ok(source), Ok(storage), Ok(settings)) = (
        hash_tree(source_root, Some(home), true),
        hash_tree(storage_root, None, false),
        settings_fingerprint(options, workspace),
    ) else {
        return false;
    };
    snapshot.source == source && snapshot.storage == storage && snapshot.settings == settings
}

pub(crate) fn source_fingerprint(source_root: &Path, home: &Path) -> Option<String> {
    hash_tree(source_root, Some(home), true).ok()
}

pub(crate) fn invalidate(home: &Path) {
    let target = path(home);
    if fs::remove_file(&target).is_ok() {
        let _ = sync_directory(home);
    }
}

pub(crate) fn publish(
    home: &Path,
    source_root: &Path,
    storage_root: &Path,
    workspace: &Workspace,
    options: &IndexOptions,
    source_before_index: Option<&str>,
) {
    let Some(source_before_index) = source_before_index else {
        return;
    };
    let Ok(source) = hash_tree(source_root, Some(home), true) else {
        return;
    };
    if source != source_before_index {
        return;
    }
    let Ok(storage) = hash_tree(storage_root, None, false) else {
        return;
    };
    let Ok(settings) = settings_fingerprint(options, workspace) else {
        return;
    };
    let snapshot = Snapshot {
        version: VERSION,
        source,
        storage,
        settings,
    };
    let Ok(bytes) = serde_json::to_vec(&snapshot) else {
        return;
    };
    if atomic_write(&path(home), &bytes).is_ok() {
        let _ = sync_directory(home);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn source_tree_seal_excludes_workspace_but_detects_same_size_content_rewrite() {
        let directory = tempfile::tempdir().expect("temp root");
        let home = directory.path().join(".zvec-grep");
        fs::create_dir(&home).expect("workspace home");
        let file = directory.path().join("source.txt");
        fs::write(&file, b"first").expect("write initial");
        fs::write(home.join(FILE_NAME), b"marker changes are excluded").expect("marker");
        let first = hash_tree(directory.path(), Some(&home), true).expect("first hash");
        fs::write(home.join(FILE_NAME), b"new marker data").expect("update marker");
        assert_eq!(
            first,
            hash_tree(directory.path(), Some(&home), true).expect("marker excluded")
        );
        fs::write(&file, b"other").expect("same-size rewrite");
        assert_ne!(
            first,
            hash_tree(directory.path(), Some(&home), true).expect("source rehash")
        );
    }

    #[test]
    fn storage_seal_detects_content_changes_and_refuses_symlinks() {
        let directory = tempfile::tempdir().expect("storage root");
        let file = directory.path().join("native.data");
        fs::write(&file, b"first").expect("initial storage");
        let before = hash_tree(directory.path(), None, false).expect("storage seal");
        fs::write(&file, b"other").expect("same-size storage rewrite");
        assert_ne!(
            before,
            hash_tree(directory.path(), None, false).expect("updated seal")
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::symlink;
            let outside = tempfile::tempdir().expect("external directory");
            symlink(outside.path(), directory.path().join("external")).expect("storage symlink");
            assert!(hash_tree(directory.path(), None, false).is_err());
        }
    }

    #[test]
    fn invalid_and_oversized_hints_are_nonfatal_misses_and_invalidate_cleanly() {
        let directory = tempfile::tempdir().expect("workspace home");
        fs::write(path(directory.path()), b"broken").expect("corrupt hint");
        assert!(read(directory.path()).is_none());
        fs::write(
            path(directory.path()),
            vec![b'x'; usize::try_from(MAX_HINT_BYTES).expect("hint limit fits usize") + 1],
        )
        .expect("oversized hint");
        assert!(read(directory.path()).is_none());
        invalidate(directory.path());
        assert!(read(directory.path()).is_none());
    }

    #[cfg(unix)]
    #[test]
    fn tree_hash_detects_same_size_rewrite_with_restored_mtime() {
        use std::process::Command;

        let directory = tempfile::tempdir().expect("temp root");
        let reference = directory.path().join("reference");
        let file = directory.path().join("source.txt");
        fs::write(&reference, b"x").expect("reference file");
        fs::write(&file, b"first").expect("write initial");
        assert!(
            Command::new("touch")
                .args([
                    "-r",
                    reference.to_str().expect("UTF-8 reference path"),
                    file.to_str().expect("UTF-8 source path")
                ])
                .status()
                .expect("touch reference mtime")
                .success()
        );
        let first_mtime = fs::metadata(&file)
            .expect("metadata")
            .modified()
            .expect("mtime");
        let first = hash_tree(directory.path(), None, true).expect("first hash");
        fs::write(&file, b"other").expect("same-size rewrite");
        assert!(
            Command::new("touch")
                .args([
                    "-r",
                    reference.to_str().expect("UTF-8 reference path"),
                    file.to_str().expect("UTF-8 source path")
                ])
                .status()
                .expect("restore mtime")
                .success()
        );
        assert_eq!(
            first_mtime,
            fs::metadata(&file)
                .expect("metadata")
                .modified()
                .expect("restored mtime")
        );
        assert_ne!(
            first,
            hash_tree(directory.path(), None, true).expect("second hash")
        );
    }
}
