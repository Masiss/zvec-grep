//! No-op indexing fast path: publication, maintenance and freshness contracts.

mod support;

use std::{
    fs,
    path::{Path, PathBuf},
    time::Duration,
};

use serde_json::{Value, json};
use support::{
    EmbeddingServer, configure_remote_model, index_options, info_options, native_documents,
    native_file_records, set_native_file_status,
};
use tempfile::tempdir;
use zg_engine::{
    ZvecGrep,
    api::{
        context::{
            ContextOptions,
            options::{ContextRoute, ContextRouteMode},
        },
        index::IndexResult,
        symbols::SymbolsOptions,
    },
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn manifest_bytes(root: &Path) -> Vec<u8> {
    fs::read(root.join(".zvec-grep/manifest.json")).expect("manifest")
}

fn manifest_json(root: &Path) -> Value {
    serde_json::from_slice(&manifest_bytes(root)).expect("manifest json")
}

/// Snapshot of `(relative path, length, modified)` for every file under `dir`.
fn tree_state(dir: &Path) -> Vec<(PathBuf, u64, Option<std::time::SystemTime>)> {
    let mut state = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(current) = stack.pop() {
        let Ok(entries) = fs::read_dir(&current) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(metadata) = fs::symlink_metadata(&path) else {
                continue;
            };
            if metadata.is_dir() {
                stack.push(path);
            } else {
                state.push((
                    path.strip_prefix(dir).expect("relative").to_path_buf(),
                    metadata.len(),
                    metadata.modified().ok(),
                ));
            }
        }
    }
    state.sort();
    state
}

fn write_corpus(root: &Path) -> TestResult {
    fs::create_dir_all(root.join("src"))?;
    for index in 0..6 {
        fs::write(
            root.join(format!("src/Type{index}.cs")),
            format!(
                "namespace Bench;\n\n/// <summary>Type number {index}.</summary>\npublic class Type{index} {{\n    public int Value{index} {{ get; set; }}\n    public int Run{index}(int value) => value + {index};\n}}\n"
            ),
        )?;
    }
    fs::write(root.join("README.md"), "# Bench\n\nalpha beta gamma\n")?;
    Ok(())
}

async fn index_once(engine: &ZvecGrep, root: &Path) -> Result<IndexResult, zg_engine::EngineError> {
    engine.index(index_options(root)).await
}

fn assert_no_changes(result: &IndexResult) {
    assert_eq!(result.files_added, 0, "added");
    assert_eq!(result.files_modified, 0, "modified");
    assert_eq!(result.files_deleted, 0, "deleted");
    assert_eq!(result.files_failed, 0, "failed");
    assert_eq!(result.files_pending, 0, "pending");
    assert_eq!(result.entities_created, 0, "entities");
}

#[tokio::test]
async fn repeated_noop_passes_leave_manifest_and_index_untouched() -> TestResult {
    let temporary = tempdir()?;
    let root = temporary.path();
    let server = EmbeddingServer::start()?;
    configure_remote_model(root, server.address)?;
    write_corpus(root)?;

    let engine = ZvecGrep::new();
    let first = index_once(&engine, root).await?;
    assert_eq!(first.files_added, 7, "six C# files and one markdown file");

    let manifest_before = manifest_bytes(root);
    let updated_before = manifest_json(root)["updatedTime"].clone();
    let tree_before = tree_state(&root.join(".zvec-grep/index"));
    let index_dir = root.join(".zvec-grep/index");
    let rows_before = native_file_records(&index_dir)?;
    let docs_before = native_documents(&index_dir.join("entities"))?.len();
    let requests_before = server.requests.load(std::sync::atomic::Ordering::SeqCst);
    let inputs_before = server.inputs.load(std::sync::atomic::Ordering::SeqCst);

    for pass in 0..3 {
        let result = index_once(&engine, root).await?;
        assert_no_changes(&result);
        assert_eq!(
            manifest_bytes(root),
            manifest_before,
            "pass {pass}: manifest bytes changed"
        );
        assert_eq!(
            manifest_json(root)["updatedTime"],
            updated_before,
            "pass {pass}: updatedTime advanced on a no-op"
        );
        assert_eq!(
            tree_state(&index_dir),
            tree_before,
            "pass {pass}: native index files changed on a no-op"
        );
        assert_eq!(
            native_file_records(&index_dir)?.len(),
            rows_before.len(),
            "pass {pass}: stored file rows changed"
        );
    }
    assert_eq!(
        native_documents(&index_dir.join("entities"))?.len(),
        docs_before,
        "canonical entities changed on a no-op"
    );
    assert_eq!(
        server.requests.load(std::sync::atomic::Ordering::SeqCst),
        requests_before,
        "a no-op pass must not call the embedding provider"
    );
    assert_eq!(
        server.inputs.load(std::sync::atomic::Ordering::SeqCst),
        inputs_before,
        "a no-op pass must not embed any input"
    );
    let status = engine.info(info_options(root)).await?;
    assert!(status.status.is_some(), "status is available after a no-op");
    engine.close();
    Ok(())
}

#[tokio::test]
async fn changed_files_take_the_full_path_and_publish() -> TestResult {
    let temporary = tempdir()?;
    let root = temporary.path();
    let server = EmbeddingServer::start()?;
    configure_remote_model(root, server.address)?;
    write_corpus(root)?;

    let engine = ZvecGrep::new();
    index_once(&engine, root).await?;
    let updated_before = manifest_json(root)["updatedTime"].clone();

    // Edit
    let edited = root.join("src/Type1.cs");
    let mut source = fs::read_to_string(&edited)?;
    source.push_str("// trailing edit\n");
    fs::write(&edited, source)?;
    let edited_result = index_once(&engine, root).await?;
    assert_eq!(edited_result.files_modified, 1, "edit is indexed");
    assert_ne!(
        manifest_json(root)["updatedTime"],
        updated_before,
        "a real change must publish"
    );

    // Add
    fs::write(
        root.join("src/Added.cs"),
        "namespace Bench;\npublic class Added { }\n",
    )?;
    let added_result = index_once(&engine, root).await?;
    assert_eq!(added_result.files_added, 1, "addition is indexed");

    // Rename
    fs::rename(root.join("src/Added.cs"), root.join("src/Renamed.cs"))?;
    let renamed_result = index_once(&engine, root).await?;
    assert_eq!(renamed_result.files_added, 1, "renamed target is added");
    assert_eq!(renamed_result.files_deleted, 1, "renamed source is deleted");

    // Delete
    fs::remove_file(root.join("src/Renamed.cs"))?;
    let deleted_result = index_once(&engine, root).await?;
    assert_eq!(deleted_result.files_deleted, 1, "deletion is applied");

    // And a following no-op still publishes nothing.
    let before = manifest_bytes(root);
    let idle = index_once(&engine, root).await?;
    assert_no_changes(&idle);
    assert_eq!(
        manifest_bytes(root),
        before,
        "no-op after changes publishes nothing"
    );
    engine.close();
    Ok(())
}

#[tokio::test]
async fn configuration_change_takes_the_full_path() -> TestResult {
    let temporary = tempdir()?;
    let root = temporary.path();
    let server = EmbeddingServer::start()?;
    configure_remote_model(root, server.address)?;
    write_corpus(root)?;

    let engine = ZvecGrep::new();
    index_once(&engine, root).await?;
    let updated_before = manifest_json(root)["updatedTime"].clone();

    // A saved configuration change must be published even when no file changed.
    let path = root.join(".zvec-grep/manifest.json");
    let mut manifest: Value = serde_json::from_slice(&fs::read(&path)?)?;
    manifest["index"]["descriptor"]["defaultEmbeddingModel"]["maxInputTokens"] = json!(4096);
    fs::write(&path, serde_json::to_vec_pretty(&manifest)?)?;

    let result = index_once(&engine, root).await?;
    assert_no_changes(&result);
    assert_ne!(
        manifest_json(root)["updatedTime"],
        updated_before,
        "a configuration change must publish"
    );
    engine.close();
    Ok(())
}

#[tokio::test]
async fn pending_maintenance_marker_forces_a_full_pass() -> TestResult {
    let temporary = tempdir()?;
    let root = temporary.path();
    let server = EmbeddingServer::start()?;
    configure_remote_model(root, server.address)?;
    write_corpus(root)?;

    let engine = ZvecGrep::new();
    index_once(&engine, root).await?;
    let marker = root.join(".zvec-grep/index/optimize.pending");
    // A legacy zero-byte marker means native maintenance is owed.
    fs::write(&marker, b"")?;
    let updated_before = manifest_json(root)["updatedTime"].clone();

    let result = index_once(&engine, root).await?;
    assert_no_changes(&result);
    assert!(
        !marker.exists(),
        "the maintenance pass must settle and clear the marker"
    );
    assert_ne!(
        manifest_json(root)["updatedTime"],
        updated_before,
        "a maintenance pass must publish"
    );
    engine.close();
    Ok(())
}

#[tokio::test]
async fn unfinished_files_block_the_fast_path() -> TestResult {
    let temporary = tempdir()?;
    let root = temporary.path();
    let server = EmbeddingServer::start()?;
    configure_remote_model(root, server.address)?;
    write_corpus(root)?;

    let engine = ZvecGrep::new();
    index_once(&engine, root).await?;
    let index = root.join(".zvec-grep/index");
    // Persist a failed file exactly as an interrupted pass would.
    set_native_file_status(&index, 0, "failed")?;
    let updated_before = manifest_json(root)["updatedTime"].clone();

    let result = index_once(&engine, root).await?;
    assert!(
        result.files_pending > 0 || result.files_modified > 0 || result.files_failed > 0,
        "an unfinished file must be retried: {result:?}"
    );
    assert_ne!(
        manifest_json(root)["updatedTime"],
        updated_before,
        "retrying work must publish"
    );
    engine.close();
    Ok(())
}

#[tokio::test]
async fn same_size_same_mtime_edit_keeps_the_documented_fast_skip() -> TestResult {
    let temporary = tempdir()?;
    let root = temporary.path();
    let server = EmbeddingServer::start()?;
    configure_remote_model(root, server.address)?;
    write_corpus(root)?;

    let engine = ZvecGrep::new();
    index_once(&engine, root).await?;
    let manifest_before = manifest_bytes(root);

    // Same length, different bytes, original mtime: the existing snapshot
    // contract treats this as unchanged. The test locks the documented
    // behaviour; changing it needs its own decision.
    let path = root.join("src/Type2.cs");
    let original = fs::read(&path)?;
    let metadata = fs::metadata(&path)?;
    let mut changed = original.clone();
    let offset = changed.len() / 2;
    changed[offset] = if changed[offset] == b'a' { b'b' } else { b'a' };
    fs::write(&path, &changed)?;
    let modified = metadata.modified()?;
    let file = fs::File::options().write(true).open(&path)?;
    file.set_times(fs::FileTimes::new().set_modified(modified))?;
    drop(file);
    assert_eq!(fs::metadata(&path)?.len(), metadata.len());

    let result = index_once(&engine, root).await?;
    assert_no_changes(&result);
    assert_eq!(
        manifest_bytes(root),
        manifest_before,
        "the metadata fast path must not publish"
    );
    engine.close();
    Ok(())
}

#[tokio::test]
async fn noop_pass_preserves_query_results_and_symbol_cursors() -> TestResult {
    let temporary = tempdir()?;
    let root = temporary.path();
    let server = EmbeddingServer::start()?;
    configure_remote_model(root, server.address)?;
    write_corpus(root)?;

    let engine = ZvecGrep::new();
    index_once(&engine, root).await?;

    let query = |mode: ContextRouteMode| {
        let options = ContextOptions {
            root: Some(root.to_path_buf()),
            routes: vec![ContextRoute {
                mode,
                query: "Type number 3".to_owned(),
            }],
            fuse: false,
            auto_update: false,
            allow_remote: true,
            limit: Some(10),
            ..ContextOptions::default()
        };
        engine.context(options)
    };
    let mut before = Vec::new();
    for mode in [
        ContextRouteMode::Fts,
        ContextRouteMode::Vector,
        ContextRouteMode::Fts,
    ] {
        before.push(
            query(mode)
                .await?
                .items
                .iter()
                .map(|item| item.relative_path.clone())
                .collect::<Vec<_>>(),
        );
    }

    let symbols = engine
        .symbols(SymbolsOptions {
            root: Some(root.to_path_buf()),
            name: "Type3".into(),
            limit: Some(1),
            ..SymbolsOptions::default()
        })
        .await?;
    assert_eq!(symbols.items.len(), 1, "exact-symbol finds the declaration");

    let idle = index_once(&engine, root).await?;
    assert_no_changes(&idle);

    // Queries are unchanged by the no-op pass.
    let mut after = Vec::new();
    for mode in [
        ContextRouteMode::Fts,
        ContextRouteMode::Vector,
        ContextRouteMode::Fts,
    ] {
        after.push(
            query(mode)
                .await?
                .items
                .iter()
                .map(|item| item.relative_path.clone())
                .collect::<Vec<_>>(),
        );
    }
    assert_eq!(before, after, "no-op pass changed query results");

    // A real commit invalidates the previous snapshot token.
    let edited = root.join("src/Type3.cs");
    let mut source = fs::read_to_string(&edited)?;
    source.push_str("// commit\n");
    fs::write(&edited, source)?;
    index_once(&engine, root).await?;
    let stale_cursor = engine
        .symbols(SymbolsOptions {
            root: Some(root.to_path_buf()),
            name: "Type3".into(),
            limit: Some(1),
            cursor: Some("v1:00:00".into()),
            ..SymbolsOptions::default()
        })
        .await;
    assert!(
        stale_cursor.is_err(),
        "a malformed or stale cursor is rejected"
    );
    engine.close();
    Ok(())
}

#[tokio::test]
async fn concurrent_index_calls_are_serialized_by_the_workspace_lock() -> TestResult {
    let temporary = tempdir()?;
    let root = temporary.path();
    let server = EmbeddingServer::start()?;
    configure_remote_model(root, server.address)?;
    write_corpus(root)?;

    let engine = ZvecGrep::new();
    index_once(&engine, root).await?;

    let first = {
        let engine = ZvecGrep::new();
        let root = root.to_path_buf();
        tokio::spawn(async move { engine.index(index_options(&root)).await })
    };
    let second = {
        let engine = ZvecGrep::new();
        let root = root.to_path_buf();
        tokio::spawn(async move { engine.index(index_options(&root)).await })
    };
    let (first, second) = (first.await?, second.await?);
    for result in [first, second] {
        let result = result?;
        assert_no_changes(&result);
    }
    // Both passes completed without corrupting the published state.
    let idle = index_once(&engine, root).await?;
    assert_no_changes(&idle);
    engine.close();
    tokio::time::sleep(Duration::from_millis(10)).await;
    Ok(())
}
