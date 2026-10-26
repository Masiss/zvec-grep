use m4_rust_bridge_research::Adapter;
use std::{fs, io::Write, path::PathBuf, process::Command};

const FILE_ID: u64 = 41;

fn root(name: &str) -> PathBuf {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join(".work/rust-bridge-tests")
        .join(format!("{name}-{}", std::process::id()));
    if path.exists() {
        fs::remove_dir_all(&path).unwrap();
    }
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    path
}

fn persist_native_revision(root: &PathBuf, file_id: u64, revision: u64) {
    let dir = root.join("native-store");
    fs::create_dir_all(&dir).unwrap();
    let path = dir.join(format!("file-{file_id}.rev"));
    let mut file = fs::File::create(&path).unwrap();
    writeln!(file, "{revision}").unwrap();
    file.sync_all().unwrap();
    fs::File::open(dir).unwrap().sync_all().unwrap();
}

fn publish_docs(adapter: &mut Adapter, file_id: u64, revision: u64, docs: &[String]) {
    adapter.stage_file(file_id, revision, docs).unwrap();
    adapter.publish(file_id, revision).unwrap();
}

#[test]
fn file_mutation_publication_refill_and_rollback() {
    let root = root("publication");
    let mut adapter = Adapter::open(&root).unwrap();
    assert!(
        Adapter::open(&root).is_err(),
        "adapter enforces a single owner per root"
    );
    let old = (0..40)
        .map(|_| format!("refillterm {}", "stalehighscore ".repeat(24)))
        .collect::<Vec<_>>();
    publish_docs(&mut adapter, FILE_ID, 1, &old);
    assert_eq!(adapter.search("refillterm", 1, 128).unwrap()[0].revision, 1);
    publish_docs(
        &mut adapter,
        FILE_ID + 1,
        1,
        &["refillterm activecontrol".to_owned()],
    );

    let current = (0..300)
        .map(|_| "refillterm currentrevision".to_owned())
        .collect::<Vec<_>>();
    adapter.begin(FILE_ID, 2, current.len() as u32).unwrap();
    let refilled = adapter.search("refillterm", 1, 128).unwrap();
    assert_eq!(
        refilled.len(),
        1,
        "stale high-ranking candidates are refilled"
    );
    assert_eq!(refilled[0].file_id, FILE_ID + 1);
    adapter.append(FILE_ID, 2, 0, &current[..128]).unwrap();
    assert!(
        adapter
            .search("currentrevision", 1, 512)
            .unwrap()
            .is_empty(),
        "partial batch stays hidden"
    );
    adapter.append(FILE_ID, 2, 128, &current[128..256]).unwrap();
    assert!(
        adapter
            .search("currentrevision", 1, 512)
            .unwrap()
            .is_empty(),
        "multiple durable batches stay hidden"
    );
    adapter.append(FILE_ID, 2, 256, &current[256..]).unwrap();
    assert!(
        adapter
            .search("currentrevision", 1, 512)
            .unwrap()
            .is_empty(),
        "complete but unpublished file stays hidden"
    );
    adapter.publish(FILE_ID, 2).unwrap();
    let hits = adapter.search("currentrevision", 512, 512).unwrap();
    assert_eq!(hits.len(), 300);
    assert!(hits
        .iter()
        .all(|hit| hit.file_id == FILE_ID && hit.revision == 2));
    let mut ordinals = hits.iter().map(|hit| hit.ordinal).collect::<Vec<_>>();
    ordinals.sort_unstable();
    assert_eq!(ordinals, (0..300).collect::<Vec<_>>());

    adapter
        .stage_file(FILE_ID, 3, &["refillterm shortened".to_owned()])
        .unwrap();
    adapter
        .publish_after_native_commit(FILE_ID, 3, || Ok(()))
        .unwrap();
    let shortened = adapter.search("shortened", 10, 128).unwrap();
    assert_eq!(shortened.len(), 1, "publish deletes the old per-file tail");
    assert_eq!((shortened[0].revision, shortened[0].ordinal), (3, 0));
    adapter
        .stage_file(FILE_ID, 3, &["refillterm shortened".to_owned()])
        .unwrap();
    adapter
        .publish_after_native_commit(FILE_ID, 3, || Ok(()))
        .unwrap();
    assert!(adapter.stage_file(FILE_ID, 3, &[]).is_err());

    adapter.begin(FILE_ID, 4, 1).unwrap();
    adapter
        .append(FILE_ID, 4, 0, &["refillterm rollbackcandidate".to_owned()])
        .unwrap();
    assert_eq!(
        adapter
            .publish_after_native_commit(FILE_ID, 4, || Err("native commit injection".to_owned()))
            .unwrap_err(),
        "native commit injection"
    );
    drop(adapter);
    let mut reopened = Adapter::open(&root).unwrap();
    let remaining = reopened.search("refillterm", 10, 128).unwrap();
    assert!(
        remaining.iter().all(|hit| hit.file_id != FILE_ID),
        "rollback leaves that file fail-closed"
    );
    assert!(
        remaining.iter().any(|hit| hit.file_id == FILE_ID + 1),
        "other files remain visible"
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn crash_after_durable_batch_recovers_unpublished_then_retry_publishes() {
    if std::env::var_os("M4_RUST_BRIDGE_CRASH_ROOT").is_some() {
        let root = PathBuf::from(std::env::var_os("M4_RUST_BRIDGE_CRASH_ROOT").unwrap());
        let mut adapter = Adapter::open(&root).unwrap();
        adapter
            .stage_file(
                FILE_ID,
                2,
                &["recoverterm newone".into(), "recoverterm newtwo".into()],
            )
            .unwrap();
        persist_native_revision(&root, FILE_ID, 2);
        std::process::exit(73);
    }

    let root = root("recovery");
    let mut adapter = Adapter::open(&root).unwrap();
    publish_docs(
        &mut adapter,
        FILE_ID,
        1,
        &["recoverterm oldversion".to_owned()],
    );
    drop(adapter);

    let status = Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "crash_after_durable_batch_recovers_unpublished_then_retry_publishes",
            "--nocapture",
        ])
        .env("M4_RUST_BRIDGE_CRASH_ROOT", &root)
        .status()
        .unwrap();
    assert_eq!(
        status.code(),
        Some(73),
        "child must terminate at the durable-before-publish boundary"
    );

    assert_eq!(
        fs::read_to_string(root.join("native-store/file-41.rev")).unwrap(),
        "2\n"
    );
    let mut recovered = Adapter::open(&root).unwrap();
    let state = recovered.file_state(FILE_ID).unwrap();
    assert_eq!(
        (
            state.found,
            state.visibility,
            state.revision,
            state.expected,
            state.acknowledged
        ),
        (1, 1, 2, 2, 2)
    );
    assert!(
        recovered.search("recoverterm", 10, 128).unwrap().is_empty(),
        "reopen hides old and staged revisions"
    );
    recovered
        .stage_file(
            FILE_ID,
            2,
            &["recoverterm newone".into(), "recoverterm newtwo".into()],
        )
        .unwrap();
    recovered
        .publish_after_native_commit(FILE_ID, 2, || Ok(()))
        .unwrap();
    let hits = recovered.search("recoverterm", 10, 128).unwrap();
    assert_eq!(hits.len(), 2);
    assert!(hits.iter().all(|hit| hit.revision == 2));
    drop(recovered);

    let mut final_open = Adapter::open(&root).unwrap();
    assert_eq!(final_open.search("newone", 1, 64).unwrap()[0].revision, 2);
    final_open.delete(FILE_ID, 3).unwrap();
    assert!(final_open
        .search("recoverterm", 10, 128)
        .unwrap()
        .is_empty());
    drop(final_open);
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn malformed_state_fails_closed_and_oversized_input_is_rejected() {
    let root = root("validation");
    let mut adapter = Adapter::open(&root).unwrap();
    assert!(adapter.search("x", 65_537, 65_537).is_err());
    assert!(adapter.search("x", 2, 1).is_err());
    adapter.begin(FILE_ID, 1, 1).unwrap();
    let oversized = vec![b'x'; (2 << 20) + 1];
    let text = String::from_utf8(oversized).unwrap();
    assert!(adapter
        .append(FILE_ID, 1, 0, &[text])
        .unwrap_err()
        .contains("2 MiB"));
    assert!(adapter.publish(FILE_ID, 1).is_err());
    drop(adapter);

    let manifest = root.join("files/file-41.state");
    let bytes = fs::read(&manifest).unwrap();
    fs::write(&manifest, b"M4PF1 corrupted\n").unwrap();
    let error = Adapter::open(&root).err().unwrap();
    assert!(error.contains("malformed") || error.contains("checksum"));
    assert_eq!(fs::read(&manifest).unwrap(), b"M4PF1 corrupted\n");
    let oversized_state = vec![b'x'; 257];
    fs::write(&manifest, &oversized_state).unwrap();
    let error = Adapter::open(&root).err().unwrap();
    assert!(error.contains("256-byte limit"));
    assert_eq!(fs::read(&manifest).unwrap(), oversized_state);
    fs::write(&manifest, bytes).unwrap();
    fs::remove_dir_all(root).unwrap();
}
