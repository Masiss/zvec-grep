//! Conservative clean-scan cache. Every accepted hit re-hashes all source and
//! selected storage bytes; uncertain files or hints simply disable the hit.

use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
};

use tokio_util::sync::CancellationToken;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::{
    EngineError, EngineResult,
    api::index::IndexOptions,
    domain::Workspace,
    utils::{atomic_write, open_regular_file, sync_directory},
};

const FILE_NAME: &str = "clean-scan.json";
const VERSION: u32 = 1;
const MAX_HINT_BYTES: u64 = 4096;
const MAX_TREE_ENTRIES: usize = 20_000;
const MAX_TREE_FILE_BYTES: u64 = 16 * 1024 * 1024;
const MAX_TREE_BYTES: u64 = 64 * 1024 * 1024;

#[derive(Clone, Copy)]
struct SealLimits {
    entries: usize,
    file_bytes: u64,
    total_bytes: u64,
}

const DEFAULT_SEAL_LIMITS: SealLimits = SealLimits {
    entries: MAX_TREE_ENTRIES,
    file_bytes: MAX_TREE_FILE_BYTES,
    total_bytes: MAX_TREE_BYTES,
};

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
    let bytes = relative.to_string_lossy().into_owned();
    #[cfg(not(unix))]
    let bytes = bytes.as_bytes();
    hash.update(u64::try_from(bytes.len()).unwrap_or(u64::MAX).to_le_bytes());
    hash.update(bytes);
    Ok(())
}

fn check_cancelled(signal: Option<&CancellationToken>) -> EngineResult<()> {
    if signal.is_some_and(CancellationToken::is_cancelled) {
        return Err(EngineError::cancelled(
            "clean-scan verification was cancelled",
        ));
    }
    Ok(())
}

fn budget_exceeded() -> EngineError {
    EngineError::storage_failure("clean-scan seal exceeds its bounded cache budget")
}

fn tree_entries(
    root: &Path,
    excluded: Option<&Path>,
    limits: SealLimits,
    signal: Option<&CancellationToken>,
) -> EngineResult<Vec<(PathBuf, fs::Metadata)>> {
    let mut entries = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    let mut total_bytes = 0_u64;
    while let Some(directory) = pending.pop() {
        check_cancelled(signal)?;
        let read_dir = fs::read_dir(&directory).map_err(|error| {
            EngineError::from_io(
                format!(
                    "cannot scan clean-scan directory {}: {error}",
                    directory.display()
                ),
                &error,
            )
        })?;
        let mut children = Vec::new();
        for child in read_dir {
            check_cancelled(signal)?;
            let entry = child
                .map_err(|error| {
                    EngineError::from_io("cannot enumerate clean-scan directory", &error)
                })?
                .path();
            if excluded.is_some_and(|excluded| entry == excluded) {
                continue;
            }
            if entries.len().saturating_add(children.len()) >= limits.entries {
                return Err(budget_exceeded());
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
            if metadata.is_file() {
                if metadata.len() > limits.file_bytes {
                    return Err(budget_exceeded());
                }
                total_bytes = total_bytes
                    .checked_add(metadata.len())
                    .filter(|total| *total <= limits.total_bytes)
                    .ok_or_else(budget_exceeded)?;
            }
            children.push((entry, metadata));
        }
        children.sort_by(|left, right| left.0.cmp(&right.0));
        for (entry, metadata) in children {
            if metadata.is_dir() {
                pending.push(entry.clone());
            }
            entries.push((entry, metadata));
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

#[cfg(test)]
fn hash_tree(root: &Path, excluded: Option<&Path>, allow_symlinks: bool) -> EngineResult<String> {
    hash_tree_with_limits(root, excluded, allow_symlinks, DEFAULT_SEAL_LIMITS, None)
}

fn hash_regular_file(
    hash: &mut Sha256,
    entry: &Path,
    metadata: &fs::Metadata,
    limits: SealLimits,
    bytes_read: &mut u64,
    signal: Option<&CancellationToken>,
) -> EngineResult<()> {
    let file = open_regular_file(entry)
        .map_err(|error| EngineError::from_io("cannot open clean-scan file", &error))?;
    let before = file
        .metadata()
        .map_err(|error| EngineError::from_io("cannot stat clean-scan file", &error))?;
    if !same_file(&before, metadata) {
        return Err(EngineError::storage_failure(
            "clean-scan file identity changed while opening",
        ));
    }
    let mut limited = (&file).take(metadata.len());
    let mut file_bytes = 0_u64;
    let mut buffer = vec![0_u8; 64 * 1024].into_boxed_slice();
    loop {
        check_cancelled(signal)?;
        let remaining_file = metadata.len().saturating_sub(file_bytes);
        let remaining_total = limits.total_bytes.saturating_sub(*bytes_read);
        let request_bytes = remaining_file.min(remaining_total);
        if request_bytes == 0 {
            break;
        }
        let buffer_len = u64::try_from(buffer.len()).unwrap_or(u64::MAX);
        let request = usize::try_from(request_bytes.min(buffer_len)).unwrap_or(buffer.len());
        let count = limited
            .read(&mut buffer[..request])
            .map_err(|error| EngineError::from_io("cannot hash clean-scan file", &error))?;
        if count == 0 {
            break;
        }
        let count_u64 = u64::try_from(count).unwrap_or(u64::MAX);
        file_bytes = file_bytes.saturating_add(count_u64);
        *bytes_read = bytes_read.saturating_add(count_u64);
        hash.update(&buffer[..count]);
    }
    if file_bytes != metadata.len() {
        return Err(budget_exceeded());
    }
    let after = file
        .metadata()
        .map_err(|error| EngineError::from_io("cannot restat clean-scan file", &error))?;
    if !same_file(&before, &after) {
        return Err(EngineError::storage_failure(
            "clean-scan file changed while hashing",
        ));
    }
    Ok(())
}

fn hash_tree_with_limits(
    root: &Path,
    excluded: Option<&Path>,
    allow_symlinks: bool,
    limits: SealLimits,
    signal: Option<&CancellationToken>,
) -> EngineResult<String> {
    check_cancelled(signal)?;
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
    let entries = tree_entries(root, excluded, limits, signal)?;
    let mut hash = Sha256::new();
    hash.update(b"tree-root");
    hash_metadata(&mut hash, &root_meta)?;
    let mut bytes_read = 0_u64;
    for (entry, metadata) in entries {
        check_cancelled(signal)?;
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
            let target_bytes = target.to_string_lossy().into_owned();
            #[cfg(not(unix))]
            let target_bytes = target_bytes.as_bytes();
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
            hash_regular_file(
                &mut hash,
                &entry,
                &metadata,
                limits,
                &mut bytes_read,
                signal,
            )?;
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
    let file = open_regular_file(&target).ok()?;
    let opened_metadata = file.metadata().ok()?;
    if opened_metadata.len() > MAX_HINT_BYTES {
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
    cfg!(all(
        unix,
        not(any(target_os = "espidf", target_os = "horizon"))
    )) && !options.rebuild
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

fn optional_tree_hash(
    root: &Path,
    excluded: Option<&Path>,
    allow_symlinks: bool,
    signal: Option<&CancellationToken>,
) -> EngineResult<Option<String>> {
    optional_tree_hash_with_limits(root, excluded, allow_symlinks, DEFAULT_SEAL_LIMITS, signal)
}

fn optional_tree_hash_with_limits(
    root: &Path,
    excluded: Option<&Path>,
    allow_symlinks: bool,
    limits: SealLimits,
    signal: Option<&CancellationToken>,
) -> EngineResult<Option<String>> {
    match hash_tree_with_limits(root, excluded, allow_symlinks, limits, signal) {
        Ok(hash) => {
            check_cancelled(signal)?;
            Ok(Some(hash))
        }
        Err(error) if error.code() == EngineError::CANCELLED => Err(error),
        Err(_) => Ok(None),
    }
}

pub(crate) enum CheckResult {
    MissBeforeSource,
    MissAfterSource(Option<String>),
    Hit,
}

pub(crate) fn check(
    home: &Path,
    source_root: &Path,
    storage_root: &Path,
    workspace: &Workspace,
    options: &IndexOptions,
    signal: Option<&CancellationToken>,
) -> EngineResult<CheckResult> {
    check_cancelled(signal)?;
    let Some(snapshot) = read(home) else {
        check_cancelled(signal)?;
        return Ok(CheckResult::MissBeforeSource);
    };
    let Ok(settings) = settings_fingerprint(options, workspace) else {
        check_cancelled(signal)?;
        return Ok(CheckResult::MissBeforeSource);
    };
    check_cancelled(signal)?;
    if snapshot.settings != settings {
        return Ok(CheckResult::MissBeforeSource);
    }
    let Some(source) = optional_tree_hash(source_root, Some(home), true, signal)? else {
        return Ok(CheckResult::MissAfterSource(None));
    };
    if snapshot.source != source {
        return Ok(CheckResult::MissAfterSource(Some(source)));
    }
    let Some(storage) = optional_tree_hash(storage_root, None, false, signal)? else {
        return Ok(CheckResult::MissAfterSource(Some(source)));
    };
    check_cancelled(signal)?;
    if snapshot.storage == storage {
        Ok(CheckResult::Hit)
    } else {
        Ok(CheckResult::MissAfterSource(Some(source)))
    }
}

pub(crate) fn source_fingerprint(
    source_root: &Path,
    home: &Path,
    signal: Option<&CancellationToken>,
) -> EngineResult<Option<String>> {
    optional_tree_hash(source_root, Some(home), true, signal)
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
    signal: Option<&CancellationToken>,
) {
    let Some(source_before_index) = source_before_index else {
        return;
    };
    let Ok(Some(source)) = optional_tree_hash(source_root, Some(home), true, signal) else {
        return;
    };
    if source != source_before_index {
        return;
    }
    let Ok(Some(storage)) = optional_tree_hash(storage_root, None, false, signal) else {
        return;
    };
    let Ok(settings) = settings_fingerprint(options, workspace) else {
        return;
    };
    if check_cancelled(signal).is_err() {
        return;
    }
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
    fn tiny_seal_budgets_reject_oversized_files_and_entry_sets_during_enumeration() {
        let directory = tempfile::tempdir().expect("source root");
        let oversized = directory.path().join("large.bin");
        let file = fs::File::create(&oversized).expect("sparse file");
        file.set_len(32).expect("size sparse file");
        drop(file);
        let tiny_file_limit = SealLimits {
            entries: 8,
            file_bytes: 8,
            total_bytes: 16,
        };
        assert!(tree_entries(directory.path(), None, tiny_file_limit, None).is_err());
        assert!(
            hash_tree_with_limits(directory.path(), None, true, tiny_file_limit, None).is_err()
        );
        assert_eq!(
            optional_tree_hash_with_limits(directory.path(), None, true, tiny_file_limit, None)
                .expect("budget miss is nonfatal"),
            None
        );

        fs::remove_file(&oversized).expect("remove sparse file");
        for name in ["large-a", "large-b"] {
            fs::write(directory.path().join(name), [b'x'; 8]).expect("small file");
        }
        let tiny_total_limit = SealLimits {
            entries: 8,
            file_bytes: 8,
            total_bytes: 12,
        };
        assert!(tree_entries(directory.path(), None, tiny_total_limit, None).is_err());
        for name in ["large-a", "large-b"] {
            fs::remove_file(directory.path().join(name)).expect("remove small file");
        }
        for name in ["a", "b", "c"] {
            fs::write(directory.path().join(name), b"x").expect("small file");
        }
        let tiny_entry_limit = SealLimits {
            entries: 2,
            file_bytes: 8,
            total_bytes: 16,
        };
        assert!(tree_entries(directory.path(), None, tiny_entry_limit, None).is_err());
    }

    #[test]
    fn cancellation_is_checked_before_enumeration_and_hashing() {
        let directory = tempfile::tempdir().expect("source root");
        fs::write(directory.path().join("file"), b"payload").expect("source file");
        let signal = CancellationToken::new();
        signal.cancel();
        let error = hash_tree_with_limits(
            directory.path(),
            None,
            true,
            DEFAULT_SEAL_LIMITS,
            Some(&signal),
        )
        .expect_err("cancelled clean-scan check");
        assert_eq!(error.code(), EngineError::CANCELLED);
    }

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
        #[cfg(unix)]
        {
            use std::os::unix::fs::symlink;
            let external = tempfile::tempdir().expect("external marker");
            fs::write(external.path().join("snapshot"), b"not a marker").expect("external file");
            symlink(external.path().join("snapshot"), path(directory.path()))
                .expect("marker symlink");
            assert!(read(directory.path()).is_none());
        }
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
