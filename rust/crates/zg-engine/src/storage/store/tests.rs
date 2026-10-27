use super::super::types::StoragePathFilter;
use super::*;
use crate::domain::SourcePath;
use crate::domain::model::Metric;
use crate::domain::{
    CodeMetadata, Content, Entity, EntityFragment, EntityId, EntityMetadata, FileIndexStatus,
    FileRecord, FileSnapshot, FragmentId, Range, SymbolType, TableCell, TableCellRole,
    TableContent,
};

/// Test input keeps canonical entities and model outputs separate, as the writer does.
#[derive(Clone)]
struct FixtureEntity {
    entity: Entity,
    model: String,
    vector: Vec<f32>,
    fts_text: String,
}

fn fixture_records(
    fixtures: &[FixtureEntity],
) -> (Vec<Entity>, Vec<super::super::types::IndexedFragment>) {
    let entities = fixtures
        .iter()
        .map(|fixture| fixture.entity.clone())
        .collect();
    let entries =
        fixtures
            .iter()
            .flat_map(|fixture| {
                fixture.entity.fragments.iter().map(|fragment| {
                    super::super::types::IndexedFragment {
                        entity_id: fixture.entity.id.clone(),
                        fragment_id: fragment.id.clone(),
                        model: fixture.model.clone(),
                        embedding_fingerprint: None,
                        vector: fixture.vector.clone(),
                        fts_text: fixture.fts_text.clone(),
                    }
                })
            })
            .collect();
    (entities, entries)
}

impl IndexStore {
    fn replace_fixture_file(
        &self,
        file: &FileRecord,
        fixtures: &[FixtureEntity],
    ) -> EngineResult<()> {
        let (entities, entries) = fixture_records(fixtures);
        self.replace_file(file, &entities, &entries)
    }
}

fn file_at(storage: &IndexStore, path: &str) -> (FileRecord, FixtureEntity) {
    let (mut file, mut entry) = fixture(None, "template", "orchard", vec![1.0, 0.0, 0.0]);
    file.relative_path = crate::domain::SourcePath::new(path).expect("source path");
    file.id = storage
        .resolve_file_ids(&[file.relative_path.to_path_buf()])
        .expect("reserve identity")[0];
    let entity = &mut entry.entity;
    entity.file_id = file.id;
    entity.id = EntityId::new(file.id, &entity.content, entity.source_range).expect("entity id");
    entity.fragments[0].id = FragmentId::new(&entity.id, 0);
    (file, entry)
}

#[test]
fn store_state_lock_wait_telemetry_is_thread_local_and_resettable() {
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);
    let _ = storage.take_state_lock_wait_telemetry_for_test();

    storage.list_files().expect("read file catalog");
    let (count, total_ns, max_ns) = storage.take_state_lock_wait_telemetry_for_test();
    assert!(count >= 1);
    assert!(total_ns >= max_ns);

    assert_eq!(storage.take_state_lock_wait_telemetry_for_test(), (0, 0, 0));
    storage.close().expect("close storage");
}

#[test]
fn gate_a_checkpoint_only_runs_on_clean_storage() {
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);
    storage
        .checkpoint_only()
        .expect("checkpoint with pending maintenance");
}

#[test]
#[allow(
    clippy::too_many_lines,
    reason = "Gate A reopen consistency sequence"
)]
fn gate_a_vector_search_correct_after_reopen() {
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);
    let (file, fixture) = fixture(Some(&storage), "first", "orchard", vec![1.0, 0.0, 0.0]);
    storage
        .replace_fixture_file(&file, std::slice::from_ref(&fixture))
        .expect("stored vector");
    storage
        .checkpoint_only()
        .expect("checkpoint without optimize");

    storage.close().expect("close before reopen");
    let reopened = open(temporary.path(), true);
    let hits = reopened
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 5, None)
        .expect("reopen search");
    assert!(!hits.is_empty(), "reopen finds committed vector");
    reopened.close().expect("close reopened");
}

#[test]
#[allow(
    clippy::too_many_lines,
    reason = "Gate A delete consistency sequence"
)]
fn gate_a_deleted_vector_not_searchable_after_checkpoint() {
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);
    let (file, fixture) = fixture(Some(&storage), "first", "orchard", vec![1.0, 0.0, 0.0]);
    storage
        .replace_fixture_file(&file, std::slice::from_ref(&fixture))
        .expect("stored vector");
    storage
        .checkpoint_only()
        .expect("commit without optimize");
    storage
        .delete_file(file.id)
        .expect("delete committed vector");
    storage
        .checkpoint_only()
        .expect("checkpoint deletion");
    let hits = storage
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 5, None)
        .expect("post-deletion search");
    assert!(hits.is_empty(), "deleted vector must not remain searchable");
    storage.close().expect("close after delete");
}

#[test]
#[allow(
    clippy::too_many_lines,
    reason = "Gate A crash/reopen no-ghost sequence"
)]
fn gate_a_crash_recovery_sees_checkpoint_state_not_partial() {
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);
    let (file, fixture) = fixture(Some(&storage), "first", "orchard", vec![1.0, 0.0, 0.0]);
    storage
        .replace_fixture_file(&file, std::slice::from_ref(&fixture))
        .expect("stored vector");

    // Checkpoint commits the only mutation before attempting a hard reopen.
    storage
        .checkpoint_only()
        .expect("checkpoint without native optimize");
    let before = storage
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, None)
        .expect("search before reopen");
    assert!(!before.is_empty(), "committed vector visible before reopen");

    storage.close().expect("simulate graceful stop before crash point");
    let reopened = open(temporary.path(), true);
    let after = reopened
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, None)
        .expect("search after reopen");
    assert_eq!(
        after.len(),
        before.len(),
        "reopen must not lose checkpointed content"
    );
    assert!(!after.is_empty(), "checkpointed content must survive reopen");

    // A second fence write/delete isn't needed here; any remaining state stays within
    // defer and is safely visible only after optimize is later allowed.
    reopened.close().expect("close reopened");
}

#[test]
#[allow(
    clippy::too_many_lines,
    reason = "Gate B accumulation pending-search sequence"
)]
fn gate_b_one_hundred_commit_before_optimize_stays_searchable() {
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);

    for index in 0..100 {
        let (file, fixture) = fixture(
            Some(&storage),
            &format!("debtor-{index}"),
            "orchard batch",
            vec![1.0, 0.0, 0.0],
        );
        storage
            .replace_fixture_file(&file, std::slice::from_ref(&fixture))
            .expect("stored debtor vector");
        storage
            .checkpoint_only()
            .expect("checkpoint mutation before optimize");
    }

    let pre = storage
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 100, None)
        .expect("search pending fragments");
    assert!(
        pre.len() >= 100,
        "at least one pending vector per committed debtor"
    );

    storage.finalize_writes().expect("native optimize checkpointed debt");
    let after = storage
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 100, None)
        .expect("search after deferred optimize");
    assert!(
        after.len() >= 100,
        "all debtor vectors survive optimize"
    );

    storage.close().expect("close after gate B");
}

#[cfg(test)]
impl IndexStore {
    pub(crate) fn fragment_completeness_summary_for_test(&self) -> EngineResult<f32> {
        self.read(|state| state.fragments.completeness_summary())
    }
}

/// Peak-RSS (VmHWM) and swap of this process, read from /proc/self/status.
#[cfg(test)]
fn gate_e_proc_status_kb(key: &str) -> Option<u64> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    for line in status.lines() {
        if let Some(rest) = line.strip_prefix(key) {
            if let Some(rest) = rest.strip_prefix(':') {
                return rest.trim().split_whitespace().next()?.parse().ok();
            }
        }
    }
    None
}

/// User+system CPU ticks of the whole test process (all threads included).
#[cfg(test)]
fn gate_e_proc_cpu_ticks() -> (Option<u64>, Option<u64>, Option<u64>) {
    let stat = match std::fs::read_to_string("/proc/self/stat") {
        Ok(stat) => stat,
        Err(_) => return (None, None, None),
    };
    let rest = match stat.rsplit_once(')') {
        Some((_, rest)) => rest.trim_start(),
        None => return (None, None, None),
    };
    let fields: Vec<&str> = rest.split_whitespace().collect();
    let tick = |offset: usize| -> Option<u64> { fields.get(offset)?.parse().ok() };
    // After pid+comm, indices are: 9=majflt, 10=utime, 11=stime.
    (tick(10), tick(11), tick(9))
}

#[cfg(test)]
fn gate_e_dir_size(path: &std::path::Path) -> u64 {
    let mut total = 0;
    if let Ok(entries) = std::fs::read_dir(path) {
        for entry in entries.flatten() {
            let file_type = entry.file_type().ok();
            if file_type.is_some_and(|kind| kind.is_dir()) {
                total += gate_e_dir_size(&entry.path());
            } else if let Ok(metadata) = entry.metadata() {
                total += metadata.len();
            }
        }
    }
    total
}

#[cfg(test)]
fn gate_e_percentile(sorted: &[u128], pct: usize) -> u128 {
    if sorted.is_empty() {
        return 0;
    }
    let index = (sorted.len().saturating_sub(1)).saturating_mul(pct) / 100;
    sorted[index]
}

#[test]
#[ignore = "Gate E A/B experiment; run explicitly: GATE_E_MODE=eager|deferred GATE_E_N=N GATE_E_PROBES=P cargo test -p zg-engine --lib storage::store::tests::gate_e_a_b -- --ignored --nocapture"]
#[allow(
    clippy::too_many_lines,
    reason = "Gate eager vs deferred total optimize path"
)]
fn gate_e_a_b_deferred_benchmark_square_fixture() {
    let mode = std::env::var("GATE_E_MODE").unwrap_or_else(|_| "both".into());
    let total_or_default = |key: &str, default: usize| {
        std::env::var(key)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(default)
    };
    let total = total_or_default("GATE_E_N", 100);
    let probes = total_or_default("GATE_E_PROBES", 100);

    fn workload(finalize_each: bool, total: usize, probes: usize) {
        let directory = tempfile::tempdir().expect("workspace");
        let storage = open(directory.path(), false);
        let mut expected: Vec<(Vec<f32>, FileId)> = Vec::with_capacity(total);

        let (start_utime, start_stime, _) = gate_e_proc_cpu_ticks();
        let began = std::time::Instant::now();
        for index in 0..total {
            // Distinct directions per file so recall@5 can distinguish neighbours.
            let angle = index as f32 * 0.0031;
            let vector = vec![1.0, angle.cos(), angle.sin()];
            let (file, entry) = fixture(
                Some(&storage),
                &format!("bench-{index}"),
                &format!("bench body {index}"),
                vector.clone(),
            );
            storage
                .replace_fixture_file(&file, std::slice::from_ref(&entry))
                .expect("stored bench vector");
            if finalize_each {
                storage
                    .finalize_writes()
                    .expect("eager checkpoint and optimize");
            } else {
                storage.checkpoint_only().expect("deferred checkpoint");
            }
            expected.push((vector, file.id));
        }
        let write_wall_us = began.elapsed().as_micros();

        // Rotate probe targets so a bounded probe count still spreads over the corpus.
        let probe_targets: Vec<usize> = (0..probes)
            .map(|step| (step * 7919) % total)
            .collect();

        let run_probes = |phase: &str| {
            let mut latencies = Vec::with_capacity(probe_targets.len());
            let mut recall_hits = 0usize;
            let probe_began = std::time::Instant::now();
            for target in &probe_targets {
                let (vector, id) = &expected[*target];
                let query_began = std::time::Instant::now();
                let hits = storage
                    .search_vector("fixture/fixture-model", vector, 5, None)
                    .expect("probe search");
                latencies.push(query_began.elapsed().as_micros());
                if hits.iter().take(5).any(|hit| hit.file_id == *id) {
                    recall_hits += 1;
                }
            }
            latencies.sort();
            println!(
                "GATE_E_PROBE mode={} phase={} probes={} recall5={}/{} wall_us={} \
p50_us={} p95_us={} p99_us={}",
                if finalize_each { "eager" } else { "deferred" },
                phase,
                latencies.len(),
                recall_hits,
                latencies.len(),
                probe_began.elapsed().as_micros(),
                gate_e_percentile(&latencies, 50),
                gate_e_percentile(&latencies, 95),
                gate_e_percentile(&latencies, 99),
            );
        };

        if !finalize_each {
            // Deferred: measure the maintenance debt window honestly, before optimize.
            let completeness = storage
                .fragment_completeness_summary_for_test()
                .expect("pre-optimize completeness");
            run_probes("pre_optimize");
            println!(
                "GATE_E_META mode=deferred completeness_before_optimize={completeness}"
            );
            let optimize_began = std::time::Instant::now();
            storage.finalize_writes().expect("deferred final optimize");
            println!(
                "GATE_E_META mode=deferred final_optimize_us={}",
                optimize_began.elapsed().as_micros()
            );
            run_probes("post_optimize");
        } else {
            run_probes("post_optimize");
        }

        let completeness_final = storage
            .fragment_completeness_summary_for_test()
            .expect("final completeness");
        let (utime, stime, majflt) = gate_e_proc_cpu_ticks();
        println!(
            "GATE_E_META mode={} total={} write_wall_us={} completeness_final={completeness_final} \
peak_rss_kb={:?} swap_kb={:?} cpu_utime_ticks={:?} cpu_stime_ticks={:?} majflt={:?} \
storage_bytes={}",
            if finalize_each { "eager" } else { "deferred" },
            total,
            write_wall_us,
            gate_e_proc_status_kb("VmHWM"),
            gate_e_proc_status_kb("VmSwap"),
            utime.map(|value| value.saturating_sub(start_utime.unwrap_or_default())),
            stime.map(|value| value.saturating_sub(start_stime.unwrap_or_default())),
            majflt,
            gate_e_dir_size(directory.path()),
        );

        storage.close().expect("close workload storage");
        directory.close().expect("drop workload directory");
    }

    match mode.as_str() {
        "eager" => workload(true, total, probes),
        "deferred" => workload(false, total, probes),
        _ => {
            workload(true, total, probes);
            workload(false, total, probes);
        }
    }
}

#[test]
#[allow(
    clippy::too_many_lines,
    reason = "Gate C deferred vs repeated eager optimize path"
)]
fn gate_c_repeated_checkpoints_until_one_final_optimize() {
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);

    for index in 0..10 {
        let (file, fixture) = fixture(
            Some(&storage),
            &format!("eager-{index}"),
            "orchard batch",
            vec![1.0, 0.0, 0.0],
        );
        storage
            .replace_fixture_file(&file, std::slice::from_ref(&fixture))
            .expect("stored eager vector");
        storage
            .checkpoint_only()
            .expect("accumulate maintenance debt");
    }
    storage
        .finalize_writes()
        .expect("single final optimize checkpointed batch");
    let after = storage
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 100, None)
        .expect("search after single scheduled optimize");
    assert!(after.len() >= 10);
    storage.close().expect("close");
}

#[test]
#[allow(
    clippy::too_many_lines,
    reason = "Gate A search boundary sequence"
)]
fn gate_a_vector_search_callable_after_checkpoint_only() {
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);
    let (file, fixture) = fixture(Some(&storage), "first", "orchard", vec![1.0, 0.0, 0.0]);
    storage
        .replace_fixture_file(&file, std::slice::from_ref(&fixture))
        .expect("stored vector");
    storage
        .checkpoint_only()
        .expect("checkpoint without native optimize");

    let hits = storage
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 5, None)
        .expect("post-checkpoint search");
    assert!(!hits.is_empty(), "vector should be searchable after checkpoint");

    storage.delete_file(file.id).expect("delete committed vector");
    storage
        .checkpoint_only()
        .expect("checkpoint after deletion");
    let hits_after = storage
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 5, None)
        .expect("post-deletion search");
    assert!(
        hits_after.is_empty(),
        "deleted vector should not remain after checkpoint"
    );

    // Reopen demonstrates that checkpoint durability is sane under the temporary seam.
    storage.close().expect("close writable storage");
    let reopened = open(temporary.path(), true);
    let reopened_hits = reopened
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 5, None)
        .expect("post-reopen search");
    assert!(reopened_hits.is_empty(), "reopen sees no deleted vector");
    reopened.close().expect("close reopened storage");
}

#[test]
fn manifest_reuses_exact_embedding_and_selectively_reconciles_file_rows() {
    let directory = tempfile::tempdir().expect("workspace");
    let storage = open(directory.path(), false);
    let (file, stable) = fixture(
        Some(&storage),
        "reconcile",
        "stable entity content",
        vec![1.0, 0.0, 0.0],
    );
    let (_, removed) = fixture(
        Some(&storage),
        "reconcile",
        "removed entity content",
        vec![0.0, 1.0, 0.0],
    );
    let (_, added) = fixture(
        Some(&storage),
        "reconcile",
        "added entity content",
        vec![0.0, 0.0, 1.0],
    );
    let stable_embedding = crate::utils::sha256_hex(b"stable embedding input");
    let removed_embedding = crate::utils::sha256_hex(b"removed embedding input");
    let added_embedding = crate::utils::sha256_hex(b"added embedding input");
    let make_entry = |fixture: &FixtureEntity, fingerprint: &str| {
        let (mut entities, mut entries) = fixture_records(std::slice::from_ref(fixture));
        entries[0].embedding_fingerprint = Some(fingerprint.to_owned());
        (entities.remove(0), entries.remove(0))
    };
    let (stable_entity, stable_entry) = make_entry(&stable, &stable_embedding);
    let (removed_entity, removed_entry) = make_entry(&removed, &removed_embedding);
    storage
        .replace_file(
            &file,
            &[stable_entity.clone(), removed_entity.clone()],
            &[stable_entry.clone(), removed_entry.clone()],
        )
        .expect("initial indexed generation");

    let first_manifest = storage
        .load_embedding_manifest(file.id)
        .expect("load manifest")
        .expect("manifest published");
    let reusable = first_manifest
        .find_embedding(&stable_entry.model, &stable_embedding)
        .expect("stable embedding fingerprint");
    assert_eq!(reusable.fragment_id, stable_entry.fragment_id.as_str());
    assert_eq!(
        storage
            .fetch_fragment_vectors(&stable_entry.model, &[reusable.fragment_id.clone()])
            .expect("fetch reusable vector")[&reusable.fragment_id],
        stable_entry.vector
    );
    storage.close().expect("close before reopen");

    let storage = open(directory.path(), false);
    let reopened_manifest = storage
        .load_embedding_manifest(file.id)
        .expect("load reopened manifest")
        .expect("persisted manifest");
    let reopened_reusable = reopened_manifest
        .find_embedding(&stable_entry.model, &stable_embedding)
        .expect("reopened stable embedding fingerprint");
    assert_eq!(
        reopened_reusable.fragment_id,
        stable_entry.fragment_id.as_str()
    );
    assert_eq!(
        storage
            .fetch_fragment_vectors(
                &stable_entry.model,
                &[reopened_reusable.fragment_id.clone()]
            )
            .expect("fetch reopened reusable vector")[&reopened_reusable.fragment_id],
        stable_entry.vector
    );

    let (added_entity, added_entry) = make_entry(&added, &added_embedding);
    let mut changed_stable = stable_entry.clone();
    changed_stable.fts_text = "stable updated searchable projection".to_owned();
    let mut replacement_file = file.clone();
    replacement_file.snapshot.size_bytes = 100;
    replacement_file.snapshot.content_hash = Some("replacement-snapshot".to_owned());
    storage
        .replace_file(
            &replacement_file,
            &[stable_entity, added_entity],
            &[changed_stable, added_entry],
        )
        .expect("selective replacement");

    assert!(
        storage
            .search_fts("removed", 10, None)
            .expect("removed projection")
            .is_empty()
    );
    assert_eq!(
        storage
            .search_fts("updated", 10, None)
            .expect("updated stable projection")
            .len(),
        1
    );
    assert_eq!(
        storage
            .search_fts("added", 10, None)
            .expect("new projection")
            .len(),
        1
    );
    let second_manifest = storage
        .load_embedding_manifest(file.id)
        .expect("load replacement manifest")
        .expect("replacement manifest published");
    assert_eq!(second_manifest.entities.len(), 2);
    assert_eq!(
        second_manifest
            .find_embedding(&stable_entry.model, &stable_embedding)
            .expect("stable embedding retained")
            .fragment_id,
        stable_entry.fragment_id.as_str()
    );
    storage.close().expect("close storage");
}

#[test]
fn search_hydration_hides_unfinished_file_replacements() {
    let directory = tempfile::tempdir().expect("workspace");
    let storage = open(directory.path(), false);
    let (file, entry) = file_at(&storage, "src/main.rs");
    storage
        .replace_fixture_file(&file, &[entry])
        .expect("indexed fixture");
    let hits = storage
        .search_fts("orchard", 10, None)
        .expect("search indexed fixture");
    assert_eq!(hits.len(), 1);

    let mut unfinished = file.clone();
    unfinished.index_status = FileIndexStatus::NotIndexed;
    storage
        .write(|state| {
            let directories = state.directories.ensure(&unfinished.relative_path)?;
            state.files.put(&unfinished, &directories)
        })
        .expect("simulate interrupted replacement");

    let loaded = storage
        .load_search_hits(&hits)
        .expect("hydrate search hits");
    assert!(loaded.entities.is_empty());
    assert!(loaded.fragments.is_empty());
}

#[test]
#[expect(
    clippy::too_many_lines,
    reason = "Compare FTS and ANN across the same complete metadata fixture"
)]
fn directory_and_filename_filters_match_both_retrieval_collections() {
    let directory = tempfile::tempdir().expect("workspace");
    let storage = open(directory.path(), false);
    let paths = [
        "main.rs",
        "src/main.rs",
        "src/nested/lib.rs",
        "src/main.ts",
        "src-old/main.rs",
        "docs/readme.md",
        "src/under_score.rs",
        "src/percent%.rs",
        "src/quote'name.rs",
    ];
    let mut files = Vec::new();
    for path in paths {
        let (file, entry) = file_at(&storage, path);
        storage
            .replace_fixture_file(&file, &[entry])
            .expect("indexed fixture");
        files.push(file);
    }
    let src = SourcePath::new("src").expect("path");
    let nested = SourcePath::new("src/nested").expect("path");
    let cases = [
        (
            StoragePathFilter::Directory(src.clone()),
            vec![1, 2, 3, 6, 7, 8],
        ),
        (
            StoragePathFilter::Not(Box::new(StoragePathFilter::Directory(src.clone()))),
            vec![0, 4, 5],
        ),
        (
            StoragePathFilter::Not(Box::new(StoragePathFilter::Or(vec![
                StoragePathFilter::Directory(src.clone()),
                StoragePathFilter::FileNameExact("main.rs".into()),
            ]))),
            vec![5],
        ),
        (
            StoragePathFilter::FileNameExact("main.rs".into()),
            vec![0, 1, 4],
        ),
        (
            StoragePathFilter::FileNamePrefix("main".into()),
            vec![0, 1, 3, 4],
        ),
        (
            StoragePathFilter::FileNameSuffix(".rs".into()),
            vec![0, 1, 2, 4, 6, 7, 8],
        ),
        (
            StoragePathFilter::Not(Box::new(StoragePathFilter::Not(Box::new(
                StoragePathFilter::FileNameSuffix(".md".into()),
            )))),
            vec![5],
        ),
        (StoragePathFilter::FileNamePrefix("under_".into()), vec![6]),
        (
            StoragePathFilter::FileNameExact("quote'name.rs".into()),
            vec![8],
        ),
        (StoragePathFilter::FileNameSuffix("%.rs".into()), vec![7]),
        (
            StoragePathFilter::And(vec![
                StoragePathFilter::Directory(src.clone()),
                StoragePathFilter::FileNameSuffix(".rs".into()),
                StoragePathFilter::Not(Box::new(StoragePathFilter::Directory(nested.clone()))),
            ]),
            vec![1, 6, 7, 8],
        ),
        (
            StoragePathFilter::Or(vec![
                StoragePathFilter::Directory(nested.clone()),
                StoragePathFilter::FileNameSuffix(".md".into()),
            ]),
            vec![2, 5],
        ),
        (StoragePathFilter::None, vec![]),
        (
            StoragePathFilter::Not(Box::new(StoragePathFilter::All)),
            vec![],
        ),
        (
            StoragePathFilter::Not(Box::new(StoragePathFilter::None)),
            (0..files.len()).collect(),
        ),
        (StoragePathFilter::And(vec![]), (0..files.len()).collect()),
        (StoragePathFilter::Or(vec![]), vec![]),
    ];
    for (path, indices) in cases {
        let filter = StorageSearchFilter {
            path: Some(path.clone()),
            ..StorageSearchFilter::default()
        };
        let mut expected = indices
            .into_iter()
            .map(|index| files[index].id)
            .collect::<Vec<_>>();
        expected.sort();
        for hits in [
            storage
                .search_fts("orchard", 20, Some(&filter))
                .expect("FTS"),
            storage
                .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 20, Some(&filter))
                .expect("vector"),
        ] {
            let mut actual = hits.into_iter().map(|hit| hit.file_id).collect::<Vec<_>>();
            actual.sort();
            assert_eq!(actual, expected, "{path:?}");
        }
    }
    storage
        .delete_file(files[2].id)
        .expect("delete nested file");
    let filter = StorageSearchFilter {
        path: Some(StoragePathFilter::Directory(nested.clone())),
        ..StorageSearchFilter::default()
    };
    assert!(
        storage
            .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 20, Some(&filter))
            .expect("no stale vectors")
            .is_empty()
    );
    storage.close().expect("checkpoint");
    let reader = open(directory.path(), true);
    assert!(
        reader
            .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 20, Some(&filter))
            .expect("reopened filter")
            .is_empty()
    );
    assert!(reader.resolve_file_ids(&[PathBuf::from("new.rs")]).is_err());
    reader.close().expect("close reader");
}

#[test]
fn file_ids_are_local_to_index_records_and_rebuilds_are_independent() {
    let temporary = tempfile::tempdir().expect("workspace");
    let first_home = temporary.path().join("first");
    let second_home = temporary.path().join("second");
    let first = open(&first_home, false);
    let (file, entry) = file_at(&first, "src/main.rs");
    first.replace_fixture_file(&file, &[entry]).expect("write");
    let reserved = first
        .resolve_file_ids(&[PathBuf::from("pending.rs")])
        .expect("reserve")[0];
    first.close().expect("checkpoint");
    let reopened = open(&first_home, false);
    assert_eq!(
        reopened
            .resolve_file_ids(&[PathBuf::from("src/main.rs")])
            .expect("stored ID"),
        [file.id]
    );
    // An unstored reservation is not a durable source record.
    assert_eq!(
        reopened
            .resolve_file_ids(&[PathBuf::from("different.rs")])
            .expect("new ID"),
        [reserved]
    );
    reopened.close().expect("close");
    let second = open(&second_home, false);
    assert_eq!(
        second
            .resolve_file_ids(&[PathBuf::from("unrelated.rs")])
            .expect("new generation"),
        [FileId::new(0)]
    );
    second.close().expect("close");
}

#[test]
fn deleting_a_record_releases_its_path_but_never_reuses_a_live_id() {
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);
    let (file, entry) = file_at(&storage, "src/main.rs");
    storage
        .replace_fixture_file(&file, &[entry])
        .expect("write");
    storage.delete_file(file.id).expect("delete");
    let (replacement, entry) = file_at(&storage, "src/main.rs");
    assert_ne!(file.id, replacement.id);
    storage
        .replace_fixture_file(&replacement, &[entry])
        .expect("replace");
    let (mut mismatched, entry) = file_at(&storage, "other.rs");
    mismatched.relative_path = replacement.relative_path.clone();
    assert!(storage.replace_fixture_file(&mismatched, &[entry]).is_err());
    storage.close().expect("checkpoint");
    let reopened = open(temporary.path(), false);
    assert_eq!(
        reopened
            .resolve_file_ids(&[PathBuf::from("src/main.rs")])
            .expect("retained"),
        [replacement.id]
    );
    assert!(
        reopened
            .resolve_file_ids(&[PathBuf::from("new.rs")])
            .expect("new")[0]
            > replacement.id
    );
    reopened.close().expect("close");
}

#[test]
fn updating_file_snapshot_preserves_entities_and_vectors() {
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);
    let (file, entry) = file_at(&storage, "src/main.rs");
    storage
        .replace_fixture_file(&file, &[entry])
        .expect("write");

    let mut updated = file.clone();
    updated.snapshot.modified_epoch_ms = Some(42);
    storage
        .update_file_snapshot(&updated)
        .expect("update snapshot");

    let stored = storage.list_files().expect("files");
    assert_eq!(stored.len(), 1);
    assert_eq!(stored[0].snapshot, updated.snapshot);
    assert_eq!(
        storage
            .search_fts("orchard", 10, None)
            .expect("entries")
            .len(),
        1
    );
    storage.close().expect("close");

    let reopened = open(temporary.path(), false);
    assert_eq!(
        reopened.list_files().expect("reopened files")[0].snapshot,
        updated.snapshot
    );
    assert_eq!(
        reopened
            .search_fts("orchard", 10, None)
            .expect("reopened entries")
            .len(),
        1
    );
    reopened.close().expect("reopened close");
}

#[test]
fn failed_files_retain_queryable_paths_and_directory_ownership() {
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);
    let (file, _) = file_at(&storage, "src/deep/failed.rs");
    let expected = [0, 1];
    storage
        .mark_file_failed(&file, "extraction failed")
        .expect("record failure");
    assert_eq!(
        storage.list_file_paths().expect("paths"),
        [(file.id, file.relative_path.to_path_buf())]
    );
    storage.close().expect("checkpoint");

    let path = temporary.path().join("storage/files");
    let mut options = zvec_rust::CollectionOptions::new().expect("options");
    options.set_read_only(true).expect("read only");
    #[cfg(windows)]
    let path = dunce::simplified(&path);
    let files = zvec_rust::Collection::open(path.to_str().expect("native path"), Some(&options))
        .expect("inspect files");
    let mut docs = files.iter_with_options(None, false).expect("iterator");
    let doc = docs.next().expect("failed file").expect("document");
    for key in expected {
        let mut query = zvec_rust::SearchQuery::scalar(2).expect("query");
        query
            .set_filter(&format!("ancestor_directory_ids CONTAIN_ANY ({key})"))
            .expect("directory filter");
        assert_eq!(files.query(&query).expect("directory membership").len(), 1);
    }
    assert_eq!(
        doc.get_string("file_name").expect("file name"),
        Some("failed.rs".into())
    );
    assert!(docs.next().is_none());
}

#[test]
fn identical_file_failures_do_not_accumulate_native_segments() {
    fn segments(home: &Path) -> usize {
        fs::read_dir(home.join("storage/files"))
            .expect("file collection")
            .filter_map(Result::ok)
            .filter(|entry| {
                entry.file_type().expect("entry type").is_dir()
                    && entry.file_name().to_string_lossy().parse::<u64>().is_ok()
            })
            .count()
    }

    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);
    let (mut file, _) = file_at(&storage, "keys/unreadable.xml");
    file.snapshot.content_hash = None;
    storage
        .mark_file_failed(&file, "prepare: permission denied")
        .expect("first failure");
    storage.close().expect("persist failure");

    // Opening a writer enumerates paths and seals the previous mutable segment.
    // After that warmup, identical failures must not populate another segment.
    let storage = open(temporary.path(), false);
    storage
        .mark_file_failed(&file, "prepare: permission denied")
        .expect("same failure after reopen");
    storage.close().expect("close warmup writer");
    let baseline = segments(temporary.path());
    for _ in 0..3 {
        let storage = open(temporary.path(), false);
        storage
            .mark_file_failed(&file, "prepare: permission denied")
            .expect("unchanged failure");
        storage.close().expect("close retry writer");
        assert_eq!(segments(temporary.path()), baseline);
    }

    let storage = open(temporary.path(), false);
    file.snapshot.size_bytes += 1;
    storage
        .mark_file_failed(&file, "prepare: permission denied")
        .expect("changed snapshot is still saved");
    assert_eq!(
        storage.list_files().expect("files")[0].snapshot,
        file.snapshot
    );
    storage
        .mark_file_failed(&file, "prepare: different failure")
        .expect("changed error is still saved");
    assert_eq!(
        storage.list_files().expect("files")[0].index_status.error(),
        Some("prepare: different failure")
    );
    let (_, entry) = file_at(&storage, "keys/unreadable.xml");
    file.snapshot.content_hash = Some("recovered".into());
    storage
        .replace_fixture_file(&file, &[entry])
        .expect("later successful retry still replaces failure");
    assert!(
        storage.list_files().expect("files")[0]
            .index_status
            .is_indexed()
    );
    storage.close().expect("close recovered writer");
}

#[test]
fn query_attributes_follow_replacement_failure_deletion_and_reopen() {
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);
    let (mut file, entry) = file_at(&storage, "source.txt");
    file.snapshot.modified_epoch_ms = Some(0);
    storage
        .replace_fixture_file(&file, &[entry])
        .expect("write");
    assert_eq!(
        storage.list_file_attributes().expect("attributes"),
        [StoredFileAttributes::from(&file)]
    );

    file.snapshot.modified_epoch_ms = None;
    storage
        .mark_file_failed(&file, "extractor failed")
        .expect("replace with failed record");
    assert_eq!(
        storage.list_file_attributes().expect("updated attributes"),
        [StoredFileAttributes::from(&file)]
    );
    storage.close().expect("checkpoint");

    let reopened = open(temporary.path(), false);
    assert_eq!(
        reopened
            .list_file_attributes()
            .expect("persisted attributes"),
        [StoredFileAttributes::from(&file)]
    );
    reopened.delete_file(file.id).expect("delete");
    assert!(
        reopened
            .list_file_attributes()
            .expect("deleted attributes")
            .is_empty()
    );
    reopened.close().expect("close");
}

fn schema() -> EmbeddingModelInfo {
    EmbeddingModelInfo {
        model: crate::domain::model::ModelInfo {
            provider: "fixture".to_owned(),
            name: "fixture-model".to_owned(),
            endpoint: None,
        },
        dimension: 3,
        metric: Metric::Cosine,
        max_batch_size: 32,
        max_input_tokens: None,
        max_image_bytes: None,
    }
}

#[test]
fn stored_model_info_preserves_metadata_and_only_checks_index_fields() {
    let directory = tempfile::tempdir().expect("fixture directory");
    let home = directory.path();
    let mut original = schema();
    original.model.endpoint = Some("https://models.example.test/embeddings".into());
    original.metric = Metric::DotProduct;
    original.max_input_tokens = Some(8192);
    original.max_image_bytes = Some(1_048_576);
    IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
        storage_path: home.to_owned(),
        embeddings: vec![original.clone()],
    })
    .expect("create index")
    .close()
    .expect("close index");
    let descriptor = home.join("storage/schema.json");
    let persisted = fs::read(&descriptor).expect("read descriptor");
    let record: serde_json::Value = serde_json::from_slice(&persisted).expect("descriptor JSON");
    assert_eq!(record, serde_json::json!({"embeddings": [original]}));
    assert_eq!(
        read_json::<SchemaRecord>(&descriptor)
            .expect("read model info")
            .embeddings()
            .expect("valid model info"),
        vec![original.clone()]
    );

    let mut current = original.clone();
    current.model.endpoint = Some("https://new.example.test/embeddings".into());
    current.max_batch_size = 64;

    IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
        storage_path: home.to_owned(),
        embeddings: vec![current.clone()],
    })
    .expect("runtime metadata changes do not invalidate an index")
    .close()
    .expect("close reused index");

    let mut invalid = current.clone();
    invalid.max_batch_size = 0;
    let error = IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
        storage_path: home.to_owned(),
        embeddings: vec![invalid],
    })
    .err()
    .expect("reopening must validate incoming metadata too");
    assert_eq!(error.code(), EngineError::INVALID_ARGUMENT);
    assert!(error.message().contains("max_batch_size"));

    for field in [
        "provider",
        "name",
        "dimension",
        "metric",
        "max_input_tokens",
        "max_image_bytes",
    ] {
        let mut changed = current.clone();
        match field {
            "provider" => changed.model.provider = "other".into(),
            "name" => changed.model.name = "other".into(),
            "dimension" => changed.dimension += 1,
            "metric" => changed.metric = Metric::Cosine,
            "max_input_tokens" => changed.max_input_tokens = Some(4096),
            "max_image_bytes" => changed.max_image_bytes = None,
            _ => unreachable!(),
        }
        let error = IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
            storage_path: home.to_owned(),
            embeddings: vec![changed],
        })
        .err()
        .expect("index fields must match");
        assert!(error.message().contains("rebuild the index"), "{field}");
    }
    assert_eq!(
        fs::read(descriptor).expect("unchanged descriptor"),
        persisted
    );
}

#[test]
fn rejects_corrupt_embedding_limits_before_opening_native_storage() {
    let directory = tempfile::tempdir().expect("fixture directory");
    let path = directory.path().join("storage");
    fs::create_dir(&path).expect("storage directory");
    for field in ["maxBatchSize", "maxInputTokens", "maxImageBytes"] {
        let mut record =
            serde_json::to_value(SchemaRecord::new(&[schema()])).expect("descriptor JSON");
        record["embeddings"][0][field] = serde_json::json!(0);
        fs::write(
            path.join("schema.json"),
            serde_json::to_vec(&record).expect("encode descriptor"),
        )
        .expect("write corrupt descriptor");
        for options in [
            WorkspaceIndexStorageOptions::ReadOnly {
                storage_path: directory.path().to_owned(),
            },
            WorkspaceIndexStorageOptions::ReadWrite {
                storage_path: directory.path().to_owned(),
                embeddings: vec![schema()],
            },
        ] {
            let error = IndexStore::open(options).err().expect("corrupt metadata");
            assert_eq!(error.code(), EngineError::STORAGE_FAILURE);
            assert!(
                error
                    .message()
                    .contains("invalid stored embedding model information")
            );
        }
    }
    assert_eq!(fs::read_dir(path).expect("storage files").count(), 1);
}

#[test]
fn initializes_schema_only_in_empty_storage() {
    let directory = tempfile::tempdir().expect("fixture directory");
    let path = directory.path().join("storage");
    fs::create_dir(&path).expect("empty storage directory");
    open(directory.path(), false)
        .close()
        .expect("initialize empty storage");
    assert!(path.join("schema.json").is_file());
    open(directory.path(), true)
        .close()
        .expect("reopen initialized storage");
}

#[test]
fn rejects_existing_collections_without_schema_before_opening_them() {
    for collection in ["files", "directories", "entities", "fragments_existing"] {
        let directory = tempfile::tempdir().expect("fixture directory");
        let path = directory.path().join("storage");
        let collection_path = path.join(collection);
        fs::create_dir_all(&collection_path).expect("existing collection");
        let marker = collection_path.join("preserved");
        fs::write(&marker, "existing data").expect("existing collection data");
        for options in [
            WorkspaceIndexStorageOptions::ReadOnly {
                storage_path: directory.path().to_owned(),
            },
            WorkspaceIndexStorageOptions::ReadWrite {
                storage_path: directory.path().to_owned(),
                embeddings: vec![schema()],
            },
        ] {
            let error = IndexStore::open(options)
                .err()
                .expect("missing schema must reject existing storage");
            assert_eq!(error.code(), EngineError::STORAGE_FAILURE);
            assert!(error.message().contains("missing its schema"));
            assert!(error.message().contains("rebuild the index"));
            assert!(!path.join("schema.json").exists());
            assert_eq!(
                fs::read_to_string(&marker).expect("preserved data"),
                "existing data"
            );
        }
    }
}

fn open(path: &Path, read_only: bool) -> IndexStore {
    let options = if read_only {
        WorkspaceIndexStorageOptions::ReadOnly {
            storage_path: path.to_owned(),
        }
    } else {
        WorkspaceIndexStorageOptions::ReadWrite {
            storage_path: path.to_owned(),
            embeddings: vec![schema()],
        }
    };
    IndexStore::open(options).expect("open real zvec storage")
}

fn fixture(
    storage: Option<&IndexStore>,
    name: &str,
    text: &str,
    vector: Vec<f32>,
) -> (FileRecord, FixtureEntity) {
    let relative_path = crate::domain::SourcePath::new(format!("{name}.txt")).expect("source path");
    let id = storage.map_or_else(
        || FileId::new(1),
        |storage| {
            storage
                .resolve_file_ids(&[relative_path.to_path_buf()])
                .expect("reserve file ID")[0]
        },
    );
    let file = FileRecord {
        id,
        relative_path,
        snapshot: FileSnapshot {
            size_bytes: text.len() as u64,
            modified_epoch_ms: Some(1),
            content_hash: Some(crate::utils::sha256_hex(text.as_bytes())),
        },
        index_status: FileIndexStatus::NotIndexed,
    };
    let content = Content::Text(text.to_owned());
    let entity_id = EntityId::new(id, &content, Range::Full).expect("entity id");
    let entry = FixtureEntity {
        fts_text: format!("quoted'\\name\0suffix\n{text}\n"),
        model: "fixture/fixture-model".into(),
        entity: Entity {
            id: entity_id.clone(),
            file_id: id,
            source_range: Range::Full,
            fragments: vec![EntityFragment {
                id: FragmentId::new(&entity_id, 0),
                range: Range::Full,
            }],
            content,
            metadata: Some(EntityMetadata::Code(CodeMetadata {
                symbol_type: Some(SymbolType::Function),
                symbol_name: Some("quoted'\\name\0suffix".to_owned()),
                scope: None,
                signature: None,
                documentation: None,
            })),
        },
        vector,
    };
    (file, entry)
}

#[test]
#[allow(
    clippy::too_many_lines,
    reason = "Keep the storage lifecycle in execution order"
)]
fn persists_filters_and_replaces_complete_files() {
    let directory = tempfile::tempdir().expect("fixture directory");
    let home = directory.path();
    let storage = open(home, false);
    let (first, entry) = fixture(
        Some(&storage),
        "first",
        "orchard\0苹果 数据库",
        vec![1.0, 0.0, 0.0],
    );
    let (second, other) = fixture(
        Some(&storage),
        "second",
        "orchard vineyard",
        vec![0.0, 1.0, 0.0],
    );
    storage
        .replace_fixture_file(&first, std::slice::from_ref(&entry))
        .expect("first file");
    storage
        .replace_fixture_file(&second, &[other])
        .expect("second file");

    let filter = StorageSearchFilter {
        path: None,
        file_ids: Some(vec![first.id]),
        entity_ids: Some(vec![entry.entity.id.clone()]),
        symbol_names: Some(vec!["quoted'\\name\0suffix".to_owned()]),
        symbol_types: Some(vec![SymbolType::Function]),
    };
    for query in ["orchard", "数据库", "orchard\0"] {
        let hits = storage
            .search_fts(query, 10, Some(&filter))
            .expect("filtered FTS");
        assert_eq!(hits.len(), 1);
        let loaded = storage.load_search_hits(&hits).expect("FTS result details");
        assert_eq!(
            loaded.fragments[&hits[0].document_id],
            entry.entity.fragments[0]
        );
    }
    let hits = storage
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, Some(&filter))
        .expect("filtered ANN");
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].file_id, first.id);
    let loaded = storage.load_search_hits(&hits).expect("ANN result details");
    let stored = &loaded.entities[&hits[0].entity_id];
    assert_eq!(stored.file.snapshot, first.snapshot);
    assert!(stored.file.index_status.is_indexed());
    let ranked = storage
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, None)
        .expect("ranked ANN");
    assert_eq!(ranked.len(), 2);
    let loaded = storage
        .load_search_hits(&ranked)
        .expect("ranked result details");
    assert_eq!(
        loaded.fragments[&ranked[0].document_id],
        entry.entity.fragments[0]
    );
    for rejected in [
        StorageSearchFilter {
            file_ids: Some(vec![second.id]),
            ..filter.clone()
        },
        StorageSearchFilter {
            entity_ids: Some(vec![
                EntityId::new(first.id, &Content::Text("missing".into()), Range::Full)
                    .expect("entity id"),
            ]),
            ..filter.clone()
        },
        StorageSearchFilter {
            symbol_names: Some(vec!["quoted'\\name suffix".to_owned()]),
            ..filter.clone()
        },
        StorageSearchFilter {
            symbol_types: Some(vec![SymbolType::Value]),
            ..filter.clone()
        },
    ] {
        assert!(
            storage
                .search_fts("orchard", 10, Some(&rejected))
                .expect("FTS exclusion")
                .is_empty()
        );
        assert!(
            storage
                .search_vector(
                    "fixture/fixture-model",
                    &[1.0, 0.0, 0.0],
                    10,
                    Some(&rejected)
                )
                .expect("ANN exclusion")
                .is_empty()
        );
    }
    assert_eq!(loaded.entities[&entry.entity.id].entity, entry.entity);
    let empty = StorageSearchFilter {
        file_ids: Some(Vec::new()),
        ..StorageSearchFilter::default()
    };
    assert!(
        storage
            .search_fts("orchard", 10, Some(&empty))
            .expect("empty filter")
            .is_empty()
    );
    assert!(
        storage
            .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, Some(&empty))
            .expect("empty filter")
            .is_empty()
    );

    storage
        .mark_file_failed(&first, "fixture extraction error")
        .expect("mark failed");
    assert!(
        storage
            .search_fts("数据库", 10, None)
            .expect("old content removed")
            .is_empty()
    );
    assert!(
        storage
            .search_vector("fixture/fixture-model", &entry.vector, 10, Some(&filter))
            .expect("old vector removed")
            .is_empty()
    );
    assert_eq!(
        storage.list_files().expect("files")[0].index_status.error(),
        Some("fixture extraction error")
    );
    storage
        .replace_fixture_file(&first, &[entry])
        .expect("retry failed file");
    storage.delete_file(second.id).expect("delete file");
    storage.close().expect("close writer");
    assert_eq!(
        storage.list_files().expect_err("closed lease").code(),
        EngineError::RESOURCE_CLOSED
    );
    let reader = open(home, true);
    assert_eq!(reader.list_files().expect("reopened files").len(), 1);
    assert_eq!(
        reader
            .search_fts("数据库", 10, None)
            .expect("reopened FTS")
            .len(),
        1
    );
    assert_eq!(
        reader
            .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, None)
            .expect("reopened ANN")
            .len(),
        1
    );
    assert!(reader.delete_file(first.id).is_err());
    let second_reader = open(home, true);
    reader.close().expect("close one reader");
    assert_eq!(
        second_reader
            .list_files()
            .expect("other lease survives")
            .len(),
        1
    );
    assert!(IndexStore::delete(home).is_err());
    second_reader.close().expect("close other reader");
    IndexStore::delete(home).expect("drop storage");
    assert!(!IndexStore::exists(home).expect("absence"));
}

#[test]
fn checkpoint_preserves_failure_status() {
    let directory = tempfile::tempdir().expect("fixture directory");
    let home = directory.path();
    let storage = open(home, false);
    let (file, entry) = fixture(Some(&storage), "source", "orchard", vec![1.0, 0.0, 0.0]);
    let (failed, _) = fixture(Some(&storage), "failed", "unreadable", vec![0.0, 1.0, 0.0]);
    storage
        .replace_fixture_file(&file, &[entry])
        .expect("small write");
    storage
        .mark_file_failed(&failed, "fixture failure")
        .expect("failed source");
    storage.checkpoint().expect("explicit checkpoint");
    storage
        .checkpoint()
        .expect("empty checkpoint is idempotent");
    drop(storage);

    let reader = open(home, true);
    assert_eq!(
        reader
            .search_fts("orchard", 10, None)
            .expect("finalized source")
            .len(),
        1
    );
    let files = reader.list_files().expect("finalized file status");
    let stored = files
        .iter()
        .find(|file| file.id == failed.id)
        .expect("failed source remains");
    let status = &stored.index_status;
    assert_eq!(status.error(), Some("fixture failure"));
    assert_eq!(status.indexed_epoch_ms(), None);
    reader.close().expect("close reader");
}

#[test]
#[allow(
    clippy::too_many_lines,
    reason = "verify invalid writes leave the same writer usable"
)]
fn rejects_invalid_writes_without_poisoning_storage() {
    let directory = tempfile::tempdir().expect("fixture directory");
    let home = directory.path();
    let storage = open(home, false);
    let (file, mut entry) = fixture(
        Some(&storage),
        "source",
        "healthy document",
        vec![1.0, 0.0, 0.0],
    );
    for invalid in [vec![1.0], vec![f32::NAN, 0.0, 0.0]] {
        entry.vector = invalid;
        assert_eq!(
            storage
                .replace_fixture_file(&file, std::slice::from_ref(&entry))
                .expect_err("invalid vector")
                .code(),
            EngineError::INVALID_ARGUMENT
        );
        assert!(
            storage
                .list_files()
                .expect("storage still usable")
                .is_empty()
        );
    }
    entry.vector = vec![1.0, 0.0, 0.0];
    storage
        .replace_fixture_file(&file, std::slice::from_ref(&entry))
        .expect("valid write after rejections");
    let healthy_files = storage.list_files().expect("healthy file records");
    entry.vector = vec![1.0];
    assert_eq!(
        storage
            .replace_fixture_file(&file, &[entry])
            .expect_err("invalid replacement of source")
            .code(),
        EngineError::INVALID_ARGUMENT
    );
    assert_eq!(
        storage.list_files().expect("preserved file records"),
        healthy_files
    );
    let (_, mut invalid_table) = fixture(
        Some(&storage),
        "source",
        "rejected table",
        vec![1.0, 0.0, 0.0],
    );
    let entity = &mut invalid_table.entity;
    entity.content = Content::Table(TableContent {
        row_count: 1,
        column_count: 1,
        cells: vec![TableCell {
            row: 0,
            column: 0,
            row_span: 0,
            column_span: 1,
            contents: vec![Content::Text("invalid zero-height cell".to_owned())],
            kind: TableCellRole::Data,
        }],
    });
    let error = storage
        .replace_fixture_file(&file, &[invalid_table])
        .expect_err("invalid table must be rejected before writing file state");
    assert_eq!(error.code(), EngineError::INVALID_ARGUMENT);
    assert!(error.message().contains("table cell span"));
    assert_eq!(
        storage.list_files().expect("preserved file records"),
        healthy_files
    );
    assert_eq!(
        storage
            .search_fts("healthy", 10, None)
            .expect("invalid input leaves accepted writes readable")
            .len(),
        1
    );
    assert!(
        IndexStore::open(WorkspaceIndexStorageOptions::ReadOnly {
            storage_path: home.to_owned(),
        })
        .is_err(),
        "readers cannot open during a writer lease"
    );
    storage.close().expect("close writer");
    let mut incompatible = schema();
    incompatible.dimension = 4;
    assert!(
        IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
            storage_path: home.to_owned(),
            embeddings: vec![incompatible],
        })
        .is_err()
    );
    let storage = open(home, false);
    assert_eq!(
        storage
            .list_files()
            .expect("schema rejection preserved index")
            .len(),
        1
    );
    storage.close().expect("close writer");
}

#[test]
fn persists_zero_cosine_vector_without_failing_its_file() {
    let directory = tempfile::tempdir().expect("fixture directory");
    let writer = open(directory.path(), false);
    let (file, entry) = fixture(
        Some(&writer),
        "zero",
        "hexadecimal shading data",
        vec![0.0, 0.0, 0.0],
    );
    writer
        .replace_fixture_file(&file, &[entry])
        .expect("zvec accepts a zero vector");
    writer.close().expect("persist zero vector");

    let reader = open(directory.path(), true);
    let files = reader.list_files().expect("read indexed file");
    assert_eq!(files.len(), 1);
    assert!(files[0].index_status.is_indexed());
    assert_eq!(
        reader
            .search_fts("hexadecimal", 10, None)
            .expect("search text from zero-vector fragment")
            .len(),
        1
    );
    reader.close().expect("close reader");
}

#[test]
fn invalid_file_states_and_owners_leave_storage_unchanged() {
    let directory = tempfile::tempdir().expect("fixture directory");
    let storage = open(directory.path(), false);
    let (file, entry) = fixture(
        Some(&storage),
        "state",
        "source content",
        vec![1.0, 0.0, 0.0],
    );
    let mut unread = file.clone();
    unread.snapshot.content_hash = None;
    assert!(
        storage
            .replace_fixture_file(&unread, std::slice::from_ref(&entry))
            .is_err()
    );
    let mut other = file.clone();
    other.id = FileId::new(99);
    assert!(
        storage
            .replace_fixture_file(&other, std::slice::from_ref(&entry))
            .is_err()
    );
    let mut duplicate_entity = entry.clone();
    duplicate_entity.entity.fragments[0].id = FragmentId::new(&entry.entity.id, 1);
    let mut duplicate_fragment = entry.clone();
    duplicate_fragment.entity.id =
        EntityId::new(file.id, &Content::Text("other".into()), Range::Full).expect("entity id");
    for (duplicate, expected_error) in [
        (duplicate_entity, "duplicate entity id"),
        (duplicate_fragment, "duplicate fragment id"),
    ] {
        let error = storage
            .replace_fixture_file(&file, &[entry.clone(), duplicate])
            .expect_err("canonical IDs must be unique across the batch");
        assert_eq!(error.message(), expected_error);
    }
    let (entities, entries) = fixture_records(std::slice::from_ref(&entry));
    let mut wrong_owner = entries[0].clone();
    wrong_owner.entity_id =
        EntityId::new(file.id, &Content::Text("unrelated".into()), Range::Full).expect("entity id");
    let mut unknown_fragment = entries[0].clone();
    unknown_fragment.fragment_id = FragmentId::new(&entry.entity.id, 1);
    for invalid in [
        vec![],
        vec![entries[0].clone(), entries[0].clone()],
        vec![wrong_owner],
        vec![unknown_fragment],
    ] {
        assert!(
            storage.replace_file(&file, &entities, &invalid).is_err(),
            "every canonical fragment requires exactly one projection of its owner"
        );
    }
    assert!(storage.list_files().expect("no partial records").is_empty());
    storage
        .replace_fixture_file(&file, &[])
        .expect("successful empty extraction");
    storage.close().expect("checkpoint empty result");
    let reader = open(directory.path(), true);
    let files = reader.list_files().expect("read empty indexed source");
    assert_eq!(files.len(), 1);
    assert!(files[0].index_status.is_indexed());
    assert_eq!(files[0].index_status.entity_count(), 0);
    assert_eq!(files[0].snapshot, file.snapshot);
    reader.close().expect("close reader");
}

#[test]
fn writes_fragments_across_native_batch_boundaries() {
    let directory = tempfile::tempdir().expect("fixture directory");
    let home = directory.path();
    let storage = open(home, false);
    let (file, prototype) = fixture(Some(&storage), "batch", "harvest", vec![1.0, 0.0, 0.0]);
    let entries = (0..1025)
        .map(|index| {
            let mut entity = prototype.entity.clone();
            entity.content = Content::Text(format!("harvest {index}"));
            entity.id =
                EntityId::new(file.id, &entity.content, entity.source_range).expect("entity id");
            entity.fragments[0].id = FragmentId::new(&entity.id, 0);
            FixtureEntity {
                model: "fixture/fixture-model".into(),
                entity,
                vector: prototype.vector.clone(),
                fts_text: prototype.fts_text.clone(),
            }
        })
        .collect::<Vec<_>>();
    storage
        .replace_fixture_file(&file, &entries)
        .expect("batched write");
    let last = entries.last().expect("last batch entry");
    let filter = StorageSearchFilter {
        entity_ids: Some(vec![last.entity.id.clone()]),
        ..StorageSearchFilter::default()
    };
    assert_eq!(
        storage
            .search_fts("harvest", 1030, None)
            .expect("all batches")
            .len(),
        entries.len()
    );
    let hits = storage
        .search_vector(
            "fixture/fixture-model",
            &prototype.vector,
            10,
            Some(&filter),
        )
        .expect("last batch ANN");
    assert_eq!(hits.len(), 1);
    let loaded = storage
        .load_search_hits(&hits)
        .expect("last batch result details");
    assert_eq!(
        loaded.fragments[&hits[0].document_id],
        last.entity.fragments[0]
    );
    storage
        .replace_fixture_file(&file, &[])
        .expect("replace with empty file");
    assert!(
        storage
            .search_fts("harvest", 10, None)
            .expect("no stale fragments")
            .is_empty()
    );
    assert!(
        storage
            .search_vector(
                "fixture/fixture-model",
                &prototype.vector,
                10,
                Some(&filter)
            )
            .expect("no stale vector")
            .is_empty()
    );
    storage.close().expect("close storage");
}

#[cfg(unix)]
#[test]
fn reopened_readers_detect_non_unicode_names_without_loading_the_allocation_cache() {
    use std::{ffi::OsString, os::unix::ffi::OsStringExt};
    let temporary = tempfile::tempdir().expect("workspace");
    let storage = open(temporary.path(), false);
    let (mut file, _) = fixture(None, "native", "source", vec![1.0, 0.0, 0.0]);
    file.relative_path =
        SourcePath::new(OsString::from_vec(b"src/\xff.rs".to_vec())).expect("native path");
    file.id = storage
        .resolve_file_ids(&[file.relative_path.to_path_buf()])
        .expect("ID")[0];
    storage
        .mark_file_failed(&file, "fixture")
        .expect("file record");
    assert!(storage.has_non_unicode_file_names().expect("writer"));
    storage.close().expect("checkpoint");
    let reader = open(temporary.path(), true);
    assert!(reader.has_non_unicode_file_names().expect("reader"));
    reader.close().expect("close");
    let writer = open(temporary.path(), false);
    writer.delete_file(file.id).expect("delete");
    writer.close().expect("checkpoint");
    let reader = open(temporary.path(), true);
    assert!(!reader.has_non_unicode_file_names().expect("empty reader"));
    reader.close().expect("close");
}

#[test]
fn directory_collection_preserves_membership_after_reopen_and_replacement() {
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let (file, entry) = file_at(&storage, "src/nested/file.rs");
    storage
        .replace_fixture_file(&file, &[entry])
        .expect("indexed source");
    storage.close().expect("checkpoint");
    assert!(home.path().join("storage/directories").is_dir());
    let filter = StorageSearchFilter {
        path: Some(StoragePathFilter::Directory(
            crate::domain::SourcePath::new("src").expect("path"),
        )),
        ..StorageSearchFilter::default()
    };
    let reader = open(home.path(), true);
    assert_eq!(
        reader
            .search_fts("orchard", 10, Some(&filter))
            .expect("directory query")
            .len(),
        1
    );
    reader.close().expect("close reader");
    let writer = open(home.path(), false);
    let (_, entry) = file_at(&writer, "src/nested/file.rs");
    writer
        .replace_fixture_file(&file, &[entry])
        .expect("reindex source");
    writer.close().expect("checkpoint recovered source");
    let reader = open(home.path(), true);
    assert_eq!(
        reader
            .search_fts("orchard", 10, Some(&filter))
            .expect("recovered directory query")
            .len(),
        1
    );
    reader.close().expect("close reader");
}

fn multi_model_schema() -> Vec<EmbeddingModelInfo> {
    let text = schema();
    let mut vision = schema();
    vision.model.name = "vision".into();
    vision.dimension = 2;
    vec![text, vision]
}

fn open_multi_model(path: &Path) -> IndexStore {
    IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
        storage_path: path.to_owned(),
        embeddings: multi_model_schema(),
    })
    .expect("multi-model storage")
}

fn multi_model_file(storage: &IndexStore) -> (FileRecord, Vec<FixtureEntity>) {
    let (file, text) = fixture(
        Some(storage),
        "nested/mixed",
        "orchard text",
        vec![1.0, 0.0, 0.0],
    );
    let mut image = text.clone();
    image.model = "fixture/vision".into();
    image.vector = vec![0.0, 1.0];
    let entity = &mut image.entity;
    entity.content = Content::Image(
        crate::domain::ImageContent::new(vec![1, 2, 3], crate::domain::FileFormat::Png)
            .expect("image"),
    );
    entity.id = EntityId::new(file.id, &entity.content, entity.source_range).expect("entity id");
    entity.fragments[0].id = FragmentId::new(&entity.id, 0);
    image.fts_text = "quoted'\\name\0suffix\n[image:png]\n".into();
    (file, vec![text, image])
}

fn vector_index_completeness(storage: &IndexStore, model: &str) -> f32 {
    storage
        .read(|state| {
            let stats = state
                .fragments
                .collection(model)?
                .stats()
                .expect("native index statistics");
            assert_eq!(stats.indexes.len(), 1);
            Ok(stats.indexes[0].completeness)
        })
        .expect("vector index completeness")
}

#[test]
fn finalization_builds_every_model_index_in_reopened_flush_only_storage() {
    let home = tempfile::tempdir().expect("workspace");
    let writer = open_multi_model(home.path());
    let (file, entries) = multi_model_file(&writer);
    writer
        .replace_fixture_file(&file, &entries)
        .expect("complete file");
    writer.close().expect("flush without optimizing");

    let writer = open_multi_model(home.path());
    for entry in &entries {
        assert!(vector_index_completeness(&writer, &entry.model) < 1.0);
    }
    writer.finalize_writes().expect("finish existing indexes");
    writer.finalize_writes().expect("unchanged finalization");
    writer.close().expect("close optimized writer");

    let reader = open(home.path(), true);
    for entry in &entries {
        assert!(vector_index_completeness(&reader, &entry.model) >= 1.0);
        let hits = reader
            .search_vector(&entry.model, &entry.vector, 10, None)
            .expect("optimized model retrieval");
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].document_id, entry.entity.fragments[0].id.as_str());
    }
    assert_eq!(reader.search_fts("name", 10, None).expect("FTS").len(), 2);
    reader.close().expect("close reader");
}

#[test]
fn staged_replacement_streams_batches_and_reopens_consistently() {
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let (old_file, old_fixture) = fixture(
        Some(&storage),
        "staged",
        "orchard stale content",
        vec![1.0, 0.0, 0.0],
    );
    let (old_entities, mut old_entries) = fixture_records(std::slice::from_ref(&old_fixture));
    old_entries[0].embedding_fingerprint =
        Some(crate::utils::sha256_hex(b"old staged embedding input"));
    storage
        .replace_file(&old_file, &old_entities, &old_entries)
        .expect("initial file");

    let (file, mut replacement) = fixture(
        Some(&storage),
        "staged",
        "starfruit fresh content",
        vec![0.0, 0.0, 1.0],
    );
    replacement.entity.fragments = (0..129_u32)
        .map(|ordinal| EntityFragment {
            id: FragmentId::new(&replacement.entity.id, ordinal),
            range: Range::Full,
        })
        .collect();
    let entities = vec![replacement.entity.clone()];
    let embedding_fingerprint = crate::utils::sha256_hex(b"staged embedding input");
    let entries = replacement
        .entity
        .fragments
        .iter()
        .map(|fragment| super::super::types::IndexedFragment {
            entity_id: replacement.entity.id.clone(),
            fragment_id: fragment.id.clone(),
            model: replacement.model.clone(),
            embedding_fingerprint: Some(embedding_fingerprint.clone()),
            vector: replacement.vector.clone(),
            fts_text: replacement.fts_text.clone(),
        })
        .collect::<Vec<_>>();
    let mut staged = crate::storage::staging::DiskStagedBatches::create(file.clone(), entities)
        .expect("create staging");
    staged
        .append(entries[..128].to_vec())
        .expect("first staging batch");
    staged
        .append(entries[128..].to_vec())
        .expect("second staging batch");
    storage
        .replace_file_from_staged(staged)
        .expect("publish complete staged file");
    let manifest = storage
        .load_embedding_manifest(file.id)
        .expect("load staged manifest")
        .expect("staged manifest published");
    assert_eq!(manifest.fragments.len(), 129);
    let reusable = manifest
        .find_embedding(&replacement.model, &embedding_fingerprint)
        .expect("staged embedding fingerprint");
    assert_eq!(
        storage
            .fetch_fragment_vectors(&replacement.model, &[reusable.fragment_id.clone()])
            .expect("fetch staged reusable vector")[&reusable.fragment_id],
        replacement.vector
    );
    storage
        .finalize_writes()
        .expect("finalize staged replacement");
    assert!(
        storage
            .search_fts("orchard", 10, None)
            .expect("old FTS")
            .is_empty()
    );
    assert_eq!(
        storage
            .search_fts("starfruit", 200, None)
            .expect("new FTS")
            .len(),
        129
    );
    assert_eq!(
        storage
            .search_vector("fixture/fixture-model", &[0.0, 0.0, 1.0], 200, None)
            .expect("new vectors")
            .len(),
        129
    );
    storage.close().expect("checkpoint");

    let reader = open(home.path(), true);
    assert_eq!(
        reader
            .search_fts("starfruit", 200, None)
            .expect("reopened FTS")
            .len(),
        129
    );
    assert_eq!(
        reader
            .search_vector("fixture/fixture-model", &[0.0, 0.0, 1.0], 200, None)
            .expect("reopened vectors")
            .len(),
        129
    );
    reader.close().expect("close reader");
}

#[test]
fn staged_publish_failure_is_hidden_after_reopen_and_retry_is_idempotent() {
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let (old_file, old_fixture) = fixture(
        Some(&storage),
        "staged-recovery",
        "orchard stale content",
        vec![1.0, 0.0, 0.0],
    );
    let (old_entities, mut old_entries) = fixture_records(std::slice::from_ref(&old_fixture));
    old_entries[0].embedding_fingerprint =
        Some(crate::utils::sha256_hex(b"old staged recovery embedding"));
    storage
        .replace_file(&old_file, &old_entities, &old_entries)
        .expect("initial file");

    let (file, mut replacement) = fixture(
        Some(&storage),
        "staged-recovery",
        "starfruit fresh content",
        vec![0.0, 0.0, 1.0],
    );
    replacement.entity.fragments = (0..129_u32)
        .map(|ordinal| EntityFragment {
            id: FragmentId::new(&replacement.entity.id, ordinal),
            range: Range::Full,
        })
        .collect();
    let entities = vec![replacement.entity.clone()];
    let embedding_fingerprint = crate::utils::sha256_hex(b"staged recovery embedding");
    let entries = replacement
        .entity
        .fragments
        .iter()
        .map(|fragment| super::super::types::IndexedFragment {
            entity_id: replacement.entity.id.clone(),
            fragment_id: fragment.id.clone(),
            model: replacement.model.clone(),
            embedding_fingerprint: Some(embedding_fingerprint.clone()),
            vector: replacement.vector.clone(),
            fts_text: replacement.fts_text.clone(),
        })
        .collect::<Vec<_>>();
    let make_stage = || {
        let mut staged =
            crate::storage::staging::DiskStagedBatches::create(file.clone(), entities.clone())
                .expect("create stage");
        for batch in entries.chunks(crate::storage::staging::STAGING_BATCH_FRAGMENTS) {
            staged.append(batch.to_vec()).expect("append stage batch");
        }
        staged
    };

    storage.fail_next_staged_publish_after_projection();
    assert!(storage.replace_file_from_staged(make_stage()).is_err());
    assert!(
        storage
            .load_embedding_manifest(file.id)
            .expect("inspect interrupted manifest")
            .is_none()
    );
    let partial_hits = storage
        .search_fts("starfruit", 200, None)
        .expect("partial projections remain in the native table");
    assert_eq!(partial_hits.len(), 128);
    let partial = storage
        .load_search_hits(&partial_hits)
        .expect("hydrate partial projections");
    assert!(partial.entities.is_empty());
    assert!(partial.fragments.is_empty());
    storage
        .finalize_writes()
        .expect("persist interrupted state");
    storage.close().expect("reopen interrupted state");

    let storage = open(home.path(), false);
    assert!(matches!(
        storage.list_files().expect("file status")[0].index_status,
        FileIndexStatus::NotIndexed
    ));
    assert!(
        storage
            .load_embedding_manifest(file.id)
            .expect("hide interrupted manifest")
            .is_none()
    );
    let partial_hits = storage
        .search_fts("starfruit", 200, None)
        .expect("reopened partial projections");
    assert_eq!(partial_hits.len(), 128);
    let partial = storage
        .load_search_hits(&partial_hits)
        .expect("hide reopened partial projections");
    assert!(partial.entities.is_empty());
    assert!(partial.fragments.is_empty());

    storage
        .replace_file_from_staged(make_stage())
        .expect("retry complete replacement");
    storage.finalize_writes().expect("finalize retry");
    let complete_hits = storage
        .search_fts("starfruit", 200, None)
        .expect("complete replacement");
    assert_eq!(complete_hits.len(), 129);
    let complete = storage
        .load_search_hits(&complete_hits)
        .expect("hydrate retried replacement");
    assert_eq!(complete.entities.len(), 1);
    assert_eq!(complete.fragments.len(), 129);
    storage.close().expect("checkpoint completed replacement");

    let reader = open(home.path(), true);
    let hits = reader
        .search_fts("starfruit", 200, None)
        .expect("reopened complete replacement");
    assert_eq!(hits.len(), 129);
    assert_eq!(
        reader
            .load_search_hits(&hits)
            .expect("hydrate reopened retry")
            .fragments
            .len(),
        129
    );
    reader.close().expect("close reader");
}

#[test]
fn finalization_preserves_replacements_and_deletions_after_reopen() {
    let home = tempfile::tempdir().expect("workspace");
    let writer = open(home.path(), false);
    let (first, entry) = fixture(Some(&writer), "first", "orchard", vec![1.0, 0.0, 0.0]);
    let (second, other) = fixture(Some(&writer), "second", "vineyard", vec![0.0, 1.0, 0.0]);
    writer
        .replace_fixture_file(&first, &[entry])
        .expect("first");
    writer
        .replace_fixture_file(&second, &[other])
        .expect("second");
    writer.finalize_writes().expect("build initial indexes");
    let (replacement, entry) = fixture(Some(&writer), "first", "meadow", vec![0.0, 0.0, 1.0]);
    writer
        .replace_fixture_file(&replacement, &[entry])
        .expect("replacement");
    writer.delete_file(second.id).expect("deletion");
    writer.finalize_writes().expect("optimize changed indexes");
    writer.close().expect("close writer");

    let reader = open(home.path(), true);
    assert!(vector_index_completeness(&reader, "fixture/fixture-model") >= 1.0);
    for removed in ["orchard", "vineyard"] {
        assert!(
            reader
                .search_fts(removed, 10, None)
                .expect("removed FTS")
                .is_empty()
        );
    }
    let hits = reader
        .search_fts("meadow", 10, None)
        .expect("replacement FTS");
    assert_eq!(hits.len(), 1);
    assert_eq!(hits[0].file_id, first.id);
    let vectors = reader
        .search_vector("fixture/fixture-model", &[0.0, 0.0, 1.0], 10, None)
        .expect("replacement vector");
    assert_eq!(vectors.len(), 1);
    assert_eq!(vectors[0].document_id, hits[0].document_id);
    reader.close().expect("close reader");
}

#[test]
fn model_tables_partition_fragments_and_failed_files_clear_every_partition() {
    let home = tempfile::tempdir().expect("workspace");
    let writer = open_multi_model(home.path());
    let (file, entries) = multi_model_file(&writer);
    writer
        .replace_fixture_file(&file, &entries)
        .expect("complete file");
    writer.close().expect("checkpoint");

    let storage_path = home.path().join("storage");
    let mut collections = fs::read_dir(&storage_path)
        .expect("storage layout")
        .map(|entry| entry.expect("directory entry"))
        .filter(|entry| entry.file_type().expect("type").is_dir())
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    collections.sort();
    let mut expected = vec![
        "directories".to_owned(),
        "files".to_owned(),
        "entities".to_owned(),
        "reconciliation".to_owned(),
    ];
    expected.extend(
        multi_model_schema()
            .iter()
            .map(super::super::fragments::fragment_collection_name),
    );
    expected.sort();
    assert_eq!(
        collections, expected,
        "three canonical collections plus one per model"
    );

    let reader = open(home.path(), true);
    for (model, vector, id) in [
        (
            "fixture/fixture-model",
            vec![1.0, 0.0, 0.0],
            entries[0].entity.fragments[0].id.as_str(),
        ),
        (
            "fixture/vision",
            vec![0.0, 1.0],
            entries[1].entity.fragments[0].id.as_str(),
        ),
    ] {
        let hits = reader
            .search_vector(model, &vector, 10, None)
            .expect("partition query");
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].document_id, id);
        let loaded = reader.load_search_hits(&hits).expect("canonical fragment");
        assert_eq!(loaded.fragments[id].id.as_str(), id);
    }
    assert!(
        reader
            .search_vector("fixture/unknown", &[1.0, 0.0], 10, None)
            .is_err()
    );
    assert!(
        reader
            .search_vector("fixture/vision", &[1.0, 0.0, 0.0], 10, None)
            .is_err()
    );
    // Indexed owner metadata is searchable in both disjoint partitions.
    let hits = reader.search_fts("name", 10, None).expect("all-table FTS");
    assert_eq!(hits.len(), 2);
    assert_ne!(hits[0].document_id, hits[1].document_id);
    reader.close().expect("close reader");

    let writer = open_multi_model(home.path());
    writer
        .mark_file_failed(&file, "vision request failed")
        .expect("file failure");
    let failed = writer.list_files().expect("failed files");
    assert!(
        matches!(&failed[0].index_status, FileIndexStatus::Failed { error } if error == "vision request failed")
    );
    assert!(
        writer
            .search_fts("name", 10, None)
            .expect("no FTS remnants")
            .is_empty()
    );
    for entry in &entries {
        assert!(
            writer
                .search_vector(&entry.model, &entry.vector, 10, None)
                .expect("no vector remnants")
                .is_empty()
        );
    }
    writer.close().expect("persist failure");
    let native = open(home.path(), true);
    assert_eq!(native.list_files().expect("failure remains").len(), 1);
    let loaded = native
        .load_search_hits(&hits)
        .expect("removed canonical entities are skipped");
    assert!(loaded.entities.is_empty());
    assert!(loaded.fragments.is_empty());
}

#[test]
fn model_set_changes_require_rebuild_and_order_does_not() {
    let home = tempfile::tempdir().expect("workspace");
    open_multi_model(home.path()).close().expect("checkpoint");
    let mut reversed = multi_model_schema();
    reversed.reverse();
    IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
        storage_path: home.path().to_owned(),
        embeddings: reversed,
    })
    .expect("same models in different order")
    .close()
    .expect("close");
    for embeddings in [vec![schema()], vec![schema(), schema()]] {
        assert!(
            IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
                storage_path: home.path().to_owned(),
                embeddings,
            })
            .is_err()
        );
    }
}

fn optimize_calls(storage: &IndexStore) -> super::OptimizeCalls {
    storage
        .read(|state| Ok(state.optimize_calls))
        .expect("optimize counters")
}

fn code_metadata_mut(fixture: &mut FixtureEntity) -> &mut CodeMetadata {
    let EntityMetadata::Code(code) = fixture
        .entity
        .metadata
        .as_mut()
        .expect("fixture carries code metadata")
    else {
        unreachable!("fixture uses code metadata");
    };
    code
}

/// Replace one file through the incremental (manifest-backed) path.
fn replace_indexed_fixture(
    storage: &IndexStore,
    file: &FileRecord,
    fixture: &FixtureEntity,
    fingerprint_seed: &str,
) -> EngineResult<()> {
    let (entities, mut entries) = fixture_records(std::slice::from_ref(fixture));
    entries[0].embedding_fingerprint = Some(crate::utils::sha256_hex(fingerprint_seed.as_bytes()));
    storage.replace_file(file, &entities, &entries)
}

#[test]
fn non_indexed_metadata_change_skips_fragment_optimize() {
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let (file, entry) = fixture(Some(&storage), "metadata", "orchard", vec![1.0, 0.0, 0.0]);
    replace_indexed_fixture(&storage, &file, &entry, "metadata embedding").expect("initial file");
    storage.finalize_writes().expect("initial finalize");
    let baseline = optimize_calls(&storage);
    assert!(
        baseline.fragments >= 1,
        "initial indexing optimizes fragments"
    );

    // Documentation and signature are not projected into fragment filters.
    let mut updated = entry.clone();
    let code = code_metadata_mut(&mut updated);
    code.documentation = Some("updated documentation".to_owned());
    code.signature = Some("fn orchard()".to_owned());
    replace_indexed_fixture(&storage, &file, &updated, "metadata embedding")
        .expect("metadata-only replacement");
    storage.finalize_writes().expect("metadata-only finalize");

    let after = optimize_calls(&storage);
    assert_eq!(
        after.fragments, baseline.fragments,
        "non-indexed metadata must not re-optimize the fragment vector index"
    );
    assert!(
        after.entities > baseline.entities,
        "the entity table still finalizes"
    );
    assert_eq!(
        after.files,
        baseline.files + 1,
        "the file row still finalizes"
    );

    let hits = storage
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, None)
        .expect("vector search");
    assert_eq!(hits.len(), 1);
    let loaded = storage.load_search_hits(&hits).expect("search details");
    let stored = &loaded.entities[&entry.entity.id];
    let EntityMetadata::Code(code) = stored.entity.metadata.as_ref().expect("metadata") else {
        unreachable!("fixture uses code metadata");
    };
    assert_eq!(code.documentation.as_deref(), Some("updated documentation"));
    assert_eq!(
        loaded.fragments[&hits[0].document_id],
        entry.entity.fragments[0]
    );

    // The unchanged indexed filter field still resolves through the projection.
    let filter = StorageSearchFilter {
        symbol_names: Some(vec!["quoted'\\name\0suffix".to_owned()]),
        ..StorageSearchFilter::default()
    };
    assert_eq!(
        storage
            .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, Some(&filter))
            .expect("filtered search")
            .len(),
        1
    );

    storage.close().expect("close");
    let reader = open(home.path(), true);
    assert_eq!(
        reader
            .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, None)
            .expect("reopened search")
            .len(),
        1
    );
    reader.close().expect("close");
}

#[test]
fn indexed_metadata_change_rewrites_fragments_and_optimizes() {
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let (file, entry) = fixture(Some(&storage), "indexed", "orchard", vec![1.0, 0.0, 0.0]);
    replace_indexed_fixture(&storage, &file, &entry, "indexed embedding").expect("initial file");
    storage.finalize_writes().expect("initial finalize");
    let baseline = optimize_calls(&storage);

    let mut updated = entry.clone();
    code_metadata_mut(&mut updated).symbol_name = Some("renamed_symbol".to_owned());
    replace_indexed_fixture(&storage, &file, &updated, "indexed embedding")
        .expect("indexed metadata replacement");
    storage
        .finalize_writes()
        .expect("indexed metadata finalize");

    let after = optimize_calls(&storage);
    assert!(
        after.fragments > baseline.fragments,
        "changing a projected metadata field invalidates the fragment vector index"
    );

    let renamed = StorageSearchFilter {
        symbol_names: Some(vec!["renamed_symbol".to_owned()]),
        ..StorageSearchFilter::default()
    };
    assert_eq!(
        storage
            .search_vector(
                "fixture/fixture-model",
                &[1.0, 0.0, 0.0],
                10,
                Some(&renamed)
            )
            .expect("renamed filter")
            .len(),
        1
    );
    let previous = StorageSearchFilter {
        symbol_names: Some(vec!["quoted'\\name\0suffix".to_owned()]),
        ..StorageSearchFilter::default()
    };
    assert!(
        storage
            .search_vector(
                "fixture/fixture-model",
                &[1.0, 0.0, 0.0],
                10,
                Some(&previous)
            )
            .expect("stale filter")
            .is_empty(),
        "the replaced projection must not keep the old metadata filter value"
    );

    storage.close().expect("close");
    let reader = open(home.path(), true);
    assert_eq!(
        reader
            .search_vector(
                "fixture/fixture-model",
                &[1.0, 0.0, 0.0],
                10,
                Some(&StorageSearchFilter {
                    symbol_names: Some(vec!["renamed_symbol".to_owned()]),
                    ..StorageSearchFilter::default()
                })
            )
            .expect("reopened renamed filter")
            .len(),
        1
    );
    reader.close().expect("close");
}

#[test]
fn file_deletion_forces_fragment_optimize_and_removes_vectors() {
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let (file, entry) = fixture(Some(&storage), "delete", "orchard", vec![1.0, 0.0, 0.0]);
    storage
        .replace_fixture_file(&file, std::slice::from_ref(&entry))
        .expect("initial file");
    storage.finalize_writes().expect("initial finalize");
    let baseline = optimize_calls(&storage);

    storage.delete_file(file.id).expect("delete file");
    storage.finalize_writes().expect("finalize deletion");
    let after = optimize_calls(&storage);
    assert!(
        after.fragments > baseline.fragments,
        "deletion tombstones force a fragment optimize"
    );
    assert!(after.entities > baseline.entities);
    assert!(after.files > baseline.files);
    assert!(
        storage
            .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, None)
            .expect("search after deletion")
            .is_empty()
    );

    storage.close().expect("close");
    let reader = open(home.path(), true);
    assert!(
        reader
            .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, None)
            .expect("reopened search after deletion")
            .is_empty()
    );
    reader.close().expect("close");
}

#[test]
fn repeated_finalize_without_writes_does_not_optimize() {
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let (file, entry) = fixture(Some(&storage), "idempotent", "orchard", vec![1.0, 0.0, 0.0]);
    storage
        .replace_fixture_file(&file, std::slice::from_ref(&entry))
        .expect("initial file");
    storage.finalize_writes().expect("initial finalize");
    let baseline = optimize_calls(&storage);

    storage.finalize_writes().expect("unchanged finalize");
    assert_eq!(optimize_calls(&storage), baseline);
    storage.finalize_writes().expect("unchanged finalize again");
    assert_eq!(optimize_calls(&storage), baseline);
    storage.close().expect("close");
}

#[test]
fn file_deletion_does_not_optimize_untouched_directories() {
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let (file, entry) = fixture(
        Some(&storage),
        "nested/orchard",
        "orchard",
        vec![1.0, 0.0, 0.0],
    );
    storage
        .replace_fixture_file(&file, std::slice::from_ref(&entry))
        .expect("initial file");
    storage.finalize_writes().expect("initial finalize");
    let baseline = optimize_calls(&storage);
    assert!(
        baseline.directories >= 1,
        "creating a directory finalizes the directory table"
    );

    storage.delete_file(file.id).expect("delete file");
    storage.finalize_writes().expect("finalize deletion");
    let after = optimize_calls(&storage);
    assert_eq!(
        after.directories, baseline.directories,
        "deletion must not optimize an untouched directory table"
    );
    assert!(after.fragments > baseline.fragments);
    storage.close().expect("close");
}

#[test]
fn staged_non_indexed_metadata_change_skips_fragment_optimize() {
    use crate::storage::staging::DiskStagedBatches;

    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let (file, entry) = fixture(
        Some(&storage),
        "staged-metadata",
        "orchard",
        vec![1.0, 0.0, 0.0],
    );
    let embedding = crate::utils::sha256_hex(b"staged metadata embedding");
    let build_stage = |entity: &FixtureEntity| {
        let (entities, mut entries) = fixture_records(std::slice::from_ref(entity));
        entries[0].embedding_fingerprint = Some(embedding.clone());
        let mut staged = DiskStagedBatches::create(file.clone(), entities).expect("create stage");
        staged.append(entries).expect("append projections");
        staged
    };
    storage
        .replace_file_from_staged(build_stage(&entry))
        .expect("initial staged replacement");
    storage.finalize_writes().expect("initial finalize");
    let baseline = optimize_calls(&storage);
    assert!(baseline.fragments >= 1);

    let mut updated = entry.clone();
    code_metadata_mut(&mut updated).documentation = Some("staged documentation".to_owned());
    storage
        .replace_file_from_staged(build_stage(&updated))
        .expect("metadata-only staged replacement");
    storage.finalize_writes().expect("metadata-only finalize");

    assert_eq!(
        optimize_calls(&storage).fragments,
        baseline.fragments,
        "staged non-indexed metadata must not re-optimize fragments"
    );
    let hits = storage
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, None)
        .expect("vector search");
    assert_eq!(hits.len(), 1);
    let loaded = storage.load_search_hits(&hits).expect("search details");
    let EntityMetadata::Code(code) = loaded.entities[&entry.entity.id]
        .entity
        .metadata
        .as_ref()
        .expect("metadata")
    else {
        unreachable!("fixture uses code metadata");
    };
    assert_eq!(code.documentation.as_deref(), Some("staged documentation"));

    storage.close().expect("close");
    let reader = open(home.path(), true);
    assert_eq!(
        reader
            .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, None)
            .expect("reopened search")
            .len(),
        1
    );
    reader.close().expect("close");
}

/// Flip a fully indexed file to `NotIndexed` without touching its rows,
/// mirroring a scanner-marked pending retry that keeps its last generation.
fn mark_file_not_indexed(storage: &IndexStore, file_id: FileId) {
    storage
        .write(|state| {
            let mut file = state
                .files
                .fetch(&[file_id])?
                .remove(&file_id)
                .expect("stored file");
            let directories = state.directories.ensure(&file.relative_path)?;
            file.index_status = FileIndexStatus::NotIndexed;
            state.files.put(&file, &directories)
        })
        .expect("mark file not indexed");
}

fn staged_fixture(
    file: &FileRecord,
    fixture: &FixtureEntity,
    fingerprint_seed: &str,
) -> crate::storage::staging::DiskStagedBatches {
    let (entities, mut entries) = fixture_records(std::slice::from_ref(fixture));
    entries[0].embedding_fingerprint = Some(crate::utils::sha256_hex(fingerprint_seed.as_bytes()));
    let mut staged = crate::storage::staging::DiskStagedBatches::create(file.clone(), entities)
        .expect("create stage");
    staged.append(entries).expect("append projections");
    staged
}

#[test]
fn pending_file_with_published_snapshot_stays_searchable() {
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let (pending, pending_entry) =
        fixture(Some(&storage), "pending", "orchard", vec![1.0, 0.0, 0.0]);
    let (stable, stable_entry) = fixture(Some(&storage), "stable", "vineyard", vec![0.0, 1.0, 0.0]);
    replace_indexed_fixture(&storage, &pending, &pending_entry, "pending embedding")
        .expect("pending file");
    replace_indexed_fixture(&storage, &stable, &stable_entry, "stable embedding")
        .expect("stable file");
    storage.finalize_writes().expect("finalize");
    assert!(
        storage
            .load_embedding_manifest(pending.id)
            .expect("lookup")
            .is_some()
    );

    mark_file_not_indexed(&storage, pending.id);

    // The pending file keeps its published snapshot and stays searchable.
    let fts = storage
        .search_fts("orchard", 10, None)
        .expect("pending FTS");
    assert_eq!(fts.len(), 1);
    assert_eq!(fts[0].file_id, pending.id);
    let loaded = storage
        .load_search_hits(&fts)
        .expect("hydrate pending snapshot");
    assert_eq!(
        loaded.fragments[&fts[0].document_id],
        pending_entry.entity.fragments[0]
    );
    assert!(loaded.entities.contains_key(&pending_entry.entity.id));

    // Other files are unaffected, and vector + FTS both stay exact.
    assert_eq!(
        storage
            .search_fts("vineyard", 10, None)
            .expect("stable FTS")
            .len(),
        1
    );
    let vectors = storage
        .search_vector(
            "fixture/fixture-model",
            &[1.0, 0.0, 0.0],
            10,
            Some(&StorageSearchFilter {
                file_ids: Some(vec![pending.id]),
                ..StorageSearchFilter::default()
            }),
        )
        .expect("pending vector");
    assert_eq!(vectors.len(), 1);
    assert_eq!(vectors[0].file_id, pending.id);
    assert_eq!(
        storage
            .search_vector(
                "fixture/fixture-model",
                &[0.0, 1.0, 0.0],
                10,
                Some(&StorageSearchFilter {
                    file_ids: Some(vec![stable.id]),
                    ..StorageSearchFilter::default()
                }),
            )
            .expect("stable vector")
            .len(),
        1
    );
    let filter = StorageSearchFilter {
        file_ids: Some(vec![pending.id]),
        ..StorageSearchFilter::default()
    };
    assert_eq!(
        storage
            .search_fts("orchard", 10, Some(&filter))
            .expect("filtered FTS")
            .len(),
        1
    );

    storage.close().expect("close");
    let reader = open(home.path(), true);
    assert_eq!(
        reader
            .search_fts("orchard", 10, None)
            .expect("reopened pending FTS")
            .len(),
        1
    );
    reader.close().expect("close");
}

#[test]
fn pending_file_without_published_snapshot_stays_hidden() {
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let (file, entry) = fixture(Some(&storage), "nosnapshot", "orchard", vec![1.0, 0.0, 0.0]);
    // A fixture without embedding fingerprints publishes no manifest.
    storage
        .replace_fixture_file(&file, std::slice::from_ref(&entry))
        .expect("index without manifest");
    storage.finalize_writes().expect("finalize");
    assert!(
        storage
            .load_embedding_manifest(file.id)
            .expect("lookup")
            .is_none()
    );

    mark_file_not_indexed(&storage, file.id);

    // The native projection is still present, but with no trustworthy snapshot
    // the file must be hidden from hydrated search results.
    let hits = storage.search_fts("orchard", 10, None).expect("raw FTS");
    assert_eq!(hits.len(), 1);
    let loaded = storage.load_search_hits(&hits).expect("hydrate");
    assert!(loaded.entities.is_empty());
    assert!(loaded.fragments.is_empty());
    storage.close().expect("close");
}

#[test]
fn failed_replacement_invalidates_previous_snapshot_and_retry_restores_it() {
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let (file, entry) = fixture(Some(&storage), "replace", "orchard", vec![1.0, 0.0, 0.0]);
    storage
        .replace_file_from_staged(staged_fixture(&file, &entry, "replace embedding"))
        .expect("initial staged replacement");
    storage.finalize_writes().expect("finalize");
    assert_eq!(
        storage
            .search_fts("orchard", 10, None)
            .expect("initial FTS")
            .len(),
        1
    );

    // A failed apply must invalidate the previous snapshot before mutating rows.
    storage.fail_next_staged_publish_after_projection();
    assert!(
        storage
            .replace_file_from_staged(staged_fixture(&file, &entry, "replace embedding"))
            .is_err()
    );
    assert!(
        storage
            .load_embedding_manifest(file.id)
            .expect("lookup")
            .is_none(),
        "the failed apply must not leave a trustworthy snapshot"
    );
    let hits = storage
        .search_fts("orchard", 10, None)
        .expect("post-failure FTS");
    assert_eq!(hits.len(), 1);
    let loaded = storage.load_search_hits(&hits).expect("hydrate");
    assert!(loaded.entities.is_empty(), "partial state stays hidden");

    // Retry publishes a fresh snapshot; results are restored exactly once.
    storage
        .replace_file_from_staged(staged_fixture(&file, &entry, "replace embedding"))
        .expect("retry staged replacement");
    storage.finalize_writes().expect("finalize retry");
    let restored = storage
        .search_fts("orchard", 10, None)
        .expect("restored FTS");
    assert_eq!(restored.len(), 1, "retry must not duplicate results");
    let loaded = storage
        .load_search_hits(&restored)
        .expect("hydrate restored");
    assert!(loaded.entities.contains_key(&entry.entity.id));
    assert_eq!(
        storage
            .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 10, None)
            .expect("restored vector")
            .len(),
        1
    );

    storage.close().expect("close");
    let reader = open(home.path(), true);
    assert_eq!(
        reader
            .search_fts("orchard", 10, None)
            .expect("reopened restored FTS")
            .len(),
        1
    );
    reader.close().expect("close");
}

#[test]
fn delete_only_without_optimize_keeps_queries_exact() {
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    let model = "fixture/fixture-model".to_owned();
    let mut files = Vec::new();
    for i in 0..12u32 {
        let text = format!("token{i}");
        let mut vector = vec![0.0f32; 3];
        vector[(i as usize) % 3] = 1.0;
        let (file, entry) = fixture(Some(&storage), &format!("doc{i}"), &text, vector);
        replace_indexed_fixture(&storage, &file, &entry, &format!("emb{i}")).expect("index");
        files.push((file, entry));
    }
    storage.finalize_writes().expect("optimize");

    let deleted: Vec<FileId> = files[..5].iter().map(|(file, _)| file.id).collect();
    storage
        .write(|state| {
            state.fragments.delete_files(&deleted)?;
            state.entities.delete_files(&deleted)?;
            for id in &deleted {
                state.manifests.delete(*id)?;
            }
            for (file, _) in files.iter().take(5) {
                state.files.mark_deleting(file.id)?;
            }
            state.files.delete_many(&deleted)
        })
        .expect("delete rows without optimize");
    storage.checkpoint().expect("flush");

    let deleted_fragment_ids = files
        .iter()
        .take(5)
        .flat_map(|(_, entry)| entry.entity.fragments.iter())
        .map(|fragment| fragment.id.as_str().to_owned())
        .collect::<std::collections::HashSet<_>>();
    let vector_hits = storage
        .search_vector(&model, &[1.0, 0.0, 0.0], 20, None)
        .expect("vector");
    assert_eq!(
        vector_hits.len(),
        7,
        "vector search must return only live rows"
    );
    for hit in &vector_hits {
        assert!(!deleted_fragment_ids.contains(&hit.document_id));
    }
    let deleted_filter = StorageSearchFilter {
        file_ids: Some(vec![deleted[0]]),
        ..StorageSearchFilter::default()
    };
    assert!(
        storage
            .search_vector(&model, &[1.0, 0.0, 0.0], 20, Some(&deleted_filter))
            .expect("deleted filter")
            .is_empty()
    );
    assert!(
        storage
            .search_fts("token0", 20, Some(&deleted_filter))
            .expect("deleted FTS filter")
            .is_empty()
    );

    for i in 0..5 {
        assert!(
            storage
                .search_fts(&format!("token{i}"), 10, None)
                .expect("fts")
                .is_empty(),
            "deleted {i} must not appear before reopen"
        );
    }
    for i in 5..12 {
        assert_eq!(
            storage
                .search_fts(&format!("token{i}"), 10, None)
                .expect("fts")
                .len(),
            1,
            "kept {i} must appear before reopen"
        );
    }
    storage.close().expect("close");

    let reader = open(home.path(), true);
    for i in 0..5 {
        assert!(
            reader
                .search_fts(&format!("token{i}"), 10, None)
                .expect("fts")
                .is_empty(),
            "deleted {i} must not appear after reopen"
        );
    }
    for i in 5..12 {
        assert_eq!(
            reader
                .search_fts(&format!("token{i}"), 10, None)
                .expect("fts")
                .len(),
            1,
            "kept {i} must appear after reopen"
        );
    }
    reader.close().expect("close");
}

/// Bounded merge measurement. Run explicitly with `--ignored --nocapture`
/// under a chosen native library, e.g. `MEASURE_N=6000`.
#[test]
#[ignore = "measurement: run explicitly against a chosen native library"]
fn measure_merge_without_deletes() {
    let count: u32 = std::env::var("MEASURE_N")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(4_000);
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);
    for i in 0..count {
        let (file, entry) = fixture(
            Some(&storage),
            &format!("m{i}"),
            &format!("token{i}"),
            vec![1.0, 0.0, 0.0],
        );
        replace_indexed_fixture(&storage, &file, &entry, &format!("emb{i}")).expect("index");
    }
    storage.finalize_writes().expect("initial finalize");
    let (extra, extra_entry) = fixture(Some(&storage), "zzz-extra", "extra", vec![0.0, 1.0, 0.0]);
    replace_indexed_fixture(&storage, &extra, &extra_entry, "extra-emb").expect("index extra");
    let start = std::time::Instant::now();
    storage.finalize_writes().expect("measured merge");
    let elapsed = start.elapsed();
    let hwm = std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|status| {
            status
                .lines()
                .find(|line| line.starts_with("VmHWM:"))
                .map(str::to_owned)
        })
        .unwrap_or_else(|| "VmHWM: ?".to_owned());
    println!("MEASURE n={count} merge_ms={} {hwm}", elapsed.as_millis());
    storage.close().expect("close");
}

fn dir_size_bytes(path: &Path) -> u64 {
    let Ok(entries) = std::fs::read_dir(path) else {
        return 0;
    };
    let mut total = 0;
    for entry in entries.filter_map(std::result::Result::ok) {
        let Ok(kind) = entry.file_type() else {
            continue;
        };
        if kind.is_dir() {
            total += dir_size_bytes(&entry.path());
        } else if let Ok(meta) = entry.metadata() {
            total += meta.len();
        }
    }
    total
}

fn count_numeric_subdirs(path: &Path) -> usize {
    let Ok(entries) = std::fs::read_dir(path) else {
        return 0;
    };
    entries
        .filter_map(std::result::Result::ok)
        .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_dir()))
        .filter(|entry| {
            entry
                .file_name()
                .to_str()
                .is_some_and(|name| !name.is_empty() && name.bytes().all(|b| b.is_ascii_digit()))
        })
        .count()
}

/// Incremental fan-out measurement. Indexes `MEASURE_N` files one at a time,
/// finalizing after each, then reports per-finalize latency, the persisted
/// segment fan-out of the fragment collection, and storage bytes. Run
/// explicitly against a chosen native library; compare
/// `ZVEC_RESEARCH_MAX_SEGMENTS` unset (baseline full merge) against a bounded
/// value, on the same binary.
#[test]
#[ignore = "measurement: run explicitly against a chosen native library"]
fn measure_incremental_fanout() {
    let count: u32 = std::env::var("MEASURE_N")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(300);
    let home = tempfile::tempdir().expect("workspace");
    let storage = open(home.path(), false);

    let mut times: Vec<u64> = Vec::with_capacity(count as usize);
    for i in 0..count {
        let (file, entry) = fixture(
            Some(&storage),
            &format!("m{i}"),
            &format!("token{i} orchard"),
            vec![1.0, 0.0, 0.0],
        );
        replace_indexed_fixture(&storage, &file, &entry, &format!("emb{i}")).expect("index");
        let start = std::time::Instant::now();
        storage.finalize_writes().expect("finalize");
        times.push(start.elapsed().as_micros() as u64);
    }

    let storage_root = home.path().join("storage");
    let fragment_dir = std::fs::read_dir(&storage_root).ok().and_then(|entries| {
        entries
            .filter_map(std::result::Result::ok)
            .map(|entry| entry.path())
            .find(|path| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.starts_with("fragments_"))
            })
    });
    let segments = fragment_dir.as_deref().map_or(0, count_numeric_subdirs);
    let bytes = dir_size_bytes(&storage_root);
    if let Some(dir) = fragment_dir.as_deref() {
        let mut names: Vec<String> = std::fs::read_dir(dir)
            .map(|entries| {
                entries
                    .filter_map(std::result::Result::ok)
                    .map(|entry| entry.file_name().to_string_lossy().into_owned())
                    .collect()
            })
            .unwrap_or_default();
        names.sort();
        println!(
            "FANOUT_LAYOUT fragment_dir={} entries={names:?}",
            dir.display()
        );
    }

    let live_vector = storage
        .search_vector("fixture/fixture-model", &[1.0, 0.0, 0.0], 5, None)
        .expect("live vector search");
    let live_fts = storage
        .search_fts("token0", 5, None)
        .expect("live fts search");

    storage.close().expect("close");
    let reader = open(home.path(), true);
    let reopened = reader
        .search_fts("token0", 5, None)
        .expect("reopened fts search");
    reader.close().expect("close");

    times.sort_unstable();
    let at = |q: f64| times[(((times.len() - 1) as f64) * q) as usize];
    println!(
        "FANOUT n={count} total_ms={} p50_ms={:.1} p95_ms={:.1} max_ms={:.1} segments={segments} storage_bytes={bytes} live_vector={} live_fts={} reopened_fts={}",
        times.iter().sum::<u64>() / 1000,
        at(0.5) as f64 / 1000.0,
        at(0.95) as f64 / 1000.0,
        (*times.last().expect("non-empty")) as f64 / 1000.0,
        live_vector.len(),
        live_fts.len(),
        reopened.len(),
    );
}

// ---------------------------------------------------------------------------
// Gate F: bounded-debt sweep harness (opt-in via `--ignored`, env-configured).
//
// Modes:
//   GATE_F_MODE=baseline  build a byte-reusable baseline snapshot at
//                         GATE_F_BASELINE_DIR (seeded deterministically).
//   GATE_F_MODE=sweep     run one bounded-debt variant against a copy of that
//                         snapshot at GATE_F_WORKDIR.
//
// The runner copies the same baseline for every variant, keeps the filesystem,
// build mode and mutation order identical, and samples /proc while the sweep
// runs so CPU % over time, RSS/PSS/swap, major faults and disk I/O are captured.
// ---------------------------------------------------------------------------
#[cfg(test)]
mod gate_f {
    use super::*;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Mutex};
    use std::thread::JoinHandle;
    use std::time::Duration;

    struct GateFSample {
        at_ms: u128,
        utime: u64,
        stime: u64,
        rss_kb: u64,
        pss_kb: Option<u64>,
        swap_kb: u64,
        read_bytes: u64,
        write_bytes: u64,
        majflt: u64,
    }

    struct GateFSummary {
        samples: usize,
        peak_rss_kb: u64,
        peak_pss_kb: u64,
        peak_swap_kb: u64,
        cpu_ticks: u64,
        max_window_cpu_pct: u64,
        majflt: u64,
        io_read_bytes: u64,
        io_write_bytes: u64,
    }

    fn read_kv(text: &str, prefix: &str) -> Option<u64> {
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix(prefix) {
                if let Some(rest) = rest.strip_prefix(':') {
                    return rest.trim().split_whitespace().next()?.parse().ok();
                }
            }
        }
        None
    }

    fn proc_cpu() -> (u64, u64, u64) {
        let stat = std::fs::read_to_string("/proc/self/stat").unwrap_or_default();
        let rest = match stat.rsplit_once(')') {
            Some((_, rest)) => rest.trim_start(),
            None => return (0, 0, 0),
        };
        let fields: Vec<&str> = rest.split_whitespace().collect();
        let at = |i: usize| fields.get(i).and_then(|v| v.parse().ok()).unwrap_or(0);
        (at(10), at(11), at(9))
    }

    fn sample_once(at_ms: u128) -> GateFSample {
        let status = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
        let io = std::fs::read_to_string("/proc/self/io").unwrap_or_default();
        let rollup = std::fs::read_to_string("/proc/self/smaps_rollup").unwrap_or_default();
        let (utime, stime, majflt) = proc_cpu();
        GateFSample {
            at_ms,
            utime,
            stime,
            rss_kb: read_kv(&status, "VmRSS").unwrap_or(0),
            pss_kb: read_kv(&rollup, "Pss"),
            swap_kb: read_kv(&status, "VmSwap").unwrap_or(0),
            read_bytes: read_kv(&io, "read_bytes").unwrap_or(0),
            write_bytes: read_kv(&io, "write_bytes").unwrap_or(0),
            majflt,
        }
    }

    fn start_sampler() -> (
        Arc<AtomicBool>,
        Arc<Mutex<Vec<GateFSample>>>,
        JoinHandle<()>,
    ) {
        let stop = Arc::new(AtomicBool::new(false));
        let samples = Arc::new(Mutex::new(Vec::new()));
        let begun = std::time::Instant::now();
        let stop_flag = Arc::clone(&stop);
        let sink = Arc::clone(&samples);
        let handle = std::thread::spawn(move || {
            loop {
                let at_ms = begun.elapsed().as_millis();
                let sample = sample_once(at_ms);
                sink.lock().expect("sampler lock").push(sample);
                if stop_flag.load(Ordering::Relaxed) {
                    break;
                }
                std::thread::sleep(Duration::from_millis(25));
            }
        });
        (stop, samples, handle)
    }

    fn summarize(samples: &[GateFSample]) -> GateFSummary {
        let mut summary = GateFSummary {
            samples: samples.len(),
            peak_rss_kb: 0,
            peak_pss_kb: 0,
            peak_swap_kb: 0,
            cpu_ticks: 0,
            max_window_cpu_pct: 0,
            majflt: 0,
            io_read_bytes: 0,
            io_write_bytes: 0,
        };
        if samples.is_empty() {
            return summary;
        }
        summary.peak_rss_kb = samples.iter().map(|s| s.rss_kb).max().unwrap_or(0);
        summary.peak_pss_kb = samples.iter().filter_map(|s| s.pss_kb).max().unwrap_or(0);
        summary.peak_swap_kb = samples.iter().map(|s| s.swap_kb).max().unwrap_or(0);
        summary.majflt = samples.iter().map(|s| s.majflt).max().unwrap_or(0);
        let first = &samples[0];
        let last = &samples[samples.len() - 1];
        summary.cpu_ticks = (last.utime + last.stime).saturating_sub(first.utime + first.stime);
        summary.io_read_bytes = last.read_bytes.saturating_sub(first.read_bytes);
        summary.io_write_bytes = last.write_bytes.saturating_sub(first.write_bytes);
        for pair in samples.windows(2) {
            let dwall = pair[1].at_ms.saturating_sub(pair[0].at_ms);
            if dwall == 0 {
                continue;
            }
            let dticks = (pair[1].utime + pair[1].stime)
                .saturating_sub(pair[0].utime + pair[0].stime);
            // CLK_TCK is 100 on Linux, so one tick is 10ms of CPU.
            let cpu_pct = dticks.saturating_mul(10) * 100 / dwall as u64;
            summary.max_window_cpu_pct = summary.max_window_cpu_pct.max(cpu_pct);
        }
        summary
    }

    fn schema_dim(dimension: usize) -> EmbeddingModelInfo {
        let mut info = schema();
        info.dimension = dimension;
        info
    }

    fn open_dim(path: &Path, dimension: usize) -> IndexStore {
        IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
            storage_path: path.to_owned(),
            embeddings: vec![schema_dim(dimension)],
        })
        .expect("open Gate F storage")
    }

    fn gen_vector(seed: u64, dim: usize, index: u64) -> Vec<f32> {
        let mut state = seed
            .wrapping_add(0x9E37_79B9_7F4A_7C15)
            .wrapping_mul(index.wrapping_add(1));
        (0..dim)
            .map(|_| {
                state = state
                    .wrapping_mul(6_364_136_223_846_793_005)
                    .wrapping_add(1_442_695_040_888_963_407);
                ((state >> 33) as f32 / (1u64 << 31) as f32) - 1.0
            })
            .collect()
    }

    fn copy_dir(src: &Path, dst: &Path) {
        std::fs::create_dir_all(dst).expect("create copy dir");
        for entry in std::fs::read_dir(src).expect("read baseline dir") {
            let entry = entry.expect("baseline entry");
            let target = dst.join(entry.file_name());
            if entry.file_type().expect("baseline type").is_dir() {
                copy_dir(&entry.path(), &target);
            } else {
                std::fs::copy(entry.path(), &target).expect("copy baseline file");
            }
        }
    }

    fn percentile(sorted: &[u128], pct: usize) -> u128 {
        if sorted.is_empty() {
            return 0;
        }
        sorted[(sorted.len() - 1).saturating_mul(pct) / 100]
    }

    fn env_usize(key: &str, fallback: usize) -> usize {
        std::env::var(key)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(fallback)
    }

    fn env_u64(key: &str, fallback: u64) -> u64 {
        std::env::var(key)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(fallback)
    }

    fn env_path(key: &str) -> PathBuf {
        PathBuf::from(std::env::var(key).unwrap_or_else(|_| panic!("missing env {key}")))
    }

    fn build_baseline(directory: &Path, dim: usize, count: usize, seed: u64) {
        let storage = open_dim(directory, dim);
        for index in 0..count {
            let vector = gen_vector(seed, dim, index as u64);
            let (file, entry) = fixture(
                Some(&storage),
                &format!("base-{index}"),
                &format!("baseline body {index}"),
                vector,
            );
            storage
                .replace_fixture_file(&file, std::slice::from_ref(&entry))
                .expect("seed baseline vector");
        }
        storage.finalize_writes().expect("finalize baseline");
        storage.close().expect("close baseline");
    }

    #[test]
    #[ignore = "Gate F bounded-debt sweep; driven by the runner with GATE_F_* env" ]
    #[allow(clippy::too_many_lines)]
    fn gate_f_bounded_debt_sweep() {
        let mode = std::env::var("GATE_F_MODE").unwrap_or_else(|_| "sweep".into());
        let dim = env_usize("GATE_F_DIM", 64);
        let baseline = env_usize("GATE_F_BASELINE", 1000);
        let seed = env_u64("GATE_F_SEED", 0x5EED_1234_ABCD);

        if mode == "baseline" {
            let directory = env_path("GATE_F_BASELINE_DIR");
            build_baseline(&directory, dim, baseline, seed);
            println!(
                "GATE_F_BASELINE dir={} dim={} count={} bytes={}",
                directory.display(),
                dim,
                baseline,
                gate_e_dir_size(&directory)
            );
            return;
        }

        // Optional self-copy for callers that do not copy externally.
        if let Ok(source) = std::env::var("GATE_F_COPY_FROM") {
            let target = env_path("GATE_F_WORKDIR");
            if target.exists() {
                std::fs::remove_dir_all(&target).expect("clear workdir");
            }
            copy_dir(Path::new(&source), &target);
        }

        let workdir = env_path("GATE_F_WORKDIR");
        let max_debt = env_usize("GATE_F_MAX_DEBT", 1).max(1);
        let mutations = env_usize("GATE_F_MUTATIONS", 1000);
        let probe_every = env_usize("GATE_F_PROBE_EVERY", (mutations / 20).max(1));
        let probes = env_usize("GATE_F_PROBES_PER_EPOCH", 50);
        let limit_mb = env_u64("GATE_F_RESOURCE_LIMIT_MB", 0);
        let label = std::env::var("GATE_F_LABEL")
            .unwrap_or_else(|_| format!("debt{max_debt}"));

        let storage = open_dim(&workdir, dim);

        // Resolve baseline identities so recall probes can verify them.
        let baseline_paths: Vec<PathBuf> = (0..baseline)
            .map(|index| PathBuf::from(format!("base-{index}.txt")))
            .collect();
        let baseline_ids = storage
            .resolve_file_ids(&baseline_paths)
            .expect("resolve baseline ids");
        let mut known: Vec<(Vec<f32>, FileId)> = baseline_ids
            .iter()
            .enumerate()
            .map(|(index, id)| (gen_vector(seed, dim, index as u64), *id))
            .collect();

        println!(
            "GATE_F_RUN label={label} max_debt={max_debt} mutations={mutations} baseline={baseline} \
dim={dim} probe_every={probe_every} probes_per_epoch={probes} resource_limit_mb={limit_mb}"
        );

        let (stop, samples, sampler) = start_sampler();
        let mut optimizes: Vec<u128> = Vec::new();
        let mut pending = 0usize;
        let mut recall_ok = 0usize;
        let mut recall_total = 0usize;
        let mut aborted = false;
        let mut epoch = 0usize;
        let sweep_began = std::time::Instant::now();

        let run_epoch = |phase: &str,
                             epoch_index: usize,
                             completed: usize,
                             storage: &IndexStore,
                             known: &[(Vec<f32>, FileId)],
                             probes: usize,
                             recall_ok: &mut usize,
                             recall_total: &mut usize| {
            let mut latencies = Vec::with_capacity(probes);
            for step in 0..probes {
                let target = (epoch_index * 7919 + step * 131) % known.len();
                let (vector, id) = &known[target];
                let began = std::time::Instant::now();
                let hits = storage
                    .search_vector("fixture/fixture-model", vector, 5, None)
                    .expect("epoch probe search");
                latencies.push(began.elapsed().as_micros());
                *recall_total += 1;
                if hits.iter().take(5).any(|hit| hit.file_id == *id) {
                    *recall_ok += 1;
                }
            }
            latencies.sort();
            let completeness = storage
                .fragment_completeness_summary_for_test()
                .unwrap_or(-1.0);
            println!(
                "GATE_F_EPOCH label={} phase={} epoch={} completed={} p50_us={} p95_us={} \
p99_us={} completeness={completeness}",
                std::env::var("GATE_F_LABEL").unwrap_or_default(),
                phase,
                epoch_index,
                completed,
                percentile(&latencies, 50),
                percentile(&latencies, 95),
                percentile(&latencies, 99),
            );
        };

        for index in 0..mutations {
            let vector = gen_vector(seed ^ 0xABCD_0000, dim, index as u64);
            let (file, entry) = fixture(
                Some(&storage),
                &format!("mut-{index}"),
                &format!("mutation body {index}"),
                vector.clone(),
            );
            storage
                .replace_fixture_file(&file, std::slice::from_ref(&entry))
                .expect("store mutation vector");
            known.push((vector, file.id));
            pending += 1;

            if max_debt == 1 {
                let began = std::time::Instant::now();
                storage.finalize_writes().expect("eager optimize");
                optimizes.push(began.elapsed().as_micros());
                pending = 0;
            } else if pending >= max_debt {
                let began = std::time::Instant::now();
                storage.finalize_writes().expect("bounded optimize");
                optimizes.push(began.elapsed().as_micros());
                pending = 0;
            } else {
                storage.checkpoint_only().expect("deferred checkpoint");
            }

            if index % probe_every == 0 {
                run_epoch(
                    "sweep",
                    epoch,
                    index + 1,
                    &storage,
                    &known,
                    probes,
                    &mut recall_ok,
                    &mut recall_total,
                );
                epoch += 1;

                if limit_mb > 0 {
                    let sample = sample_once(0);
                    let pressure = std::cmp::max(
                        sample.pss_kb.unwrap_or(0),
                        sample.rss_kb,
                    );
                    if pressure / 1024 >= limit_mb {
                        aborted = true;
                        println!(
                            "GATE_F_ABORT label={} at_mutation={} pressure_mb={}",
                            label,
                            index + 1,
                            pressure / 1024
                        );
                        break;
                    }
                }
            }
        }

        if !aborted {
            if pending > 0 || optimizes.is_empty() {
                let began = std::time::Instant::now();
                storage.finalize_writes().expect("final optimize");
                optimizes.push(began.elapsed().as_micros());
            }
            run_epoch(
                "final",
                epoch,
                mutations,
                &storage,
                &known,
                probes,
                &mut recall_ok,
                &mut recall_total,
            );
        }

        let wall_us = sweep_began.elapsed().as_micros();
        let final_completeness = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(-1.0);
        let storage_bytes = gate_e_dir_size(&workdir);
        let total_optimize_us: u128 = optimizes.iter().sum();
        let max_optimize_us = optimizes.iter().copied().max().unwrap_or(0);

        stop.store(true, Ordering::Relaxed);
        sampler.join().expect("join sampler");
        let summary = summarize(&samples.lock().expect("samples lock"));

        println!(
            "GATE_F_SUMMARY label={label} max_debt={max_debt} aborted={aborted} \
completed={} wall_us={wall_us} num_optimizes={} total_optimize_us={total_optimize_us} \
max_single_optimize_us={max_optimize_us} peak_rss_kb={} peak_pss_kb={} peak_swap_kb={} \
cpu_ticks={} max_window_cpu_pct={} majflt={} io_read_bytes={} io_write_bytes={} \
storage_bytes={storage_bytes} recall5={recall_ok}/{recall_total} final_completeness={final_completeness} \
samples={}",
            if aborted { epoch * probe_every } else { mutations },
            optimizes.len(),
            summary.peak_rss_kb,
            summary.peak_pss_kb,
            summary.peak_swap_kb,
            summary.cpu_ticks,
            summary.max_window_cpu_pct,
            summary.majflt,
            summary.io_read_bytes,
            summary.io_write_bytes,
            summary.samples,
        );

        storage.close().expect("close sweep storage");
    }
}

// ---------------------------------------------------------------------------
// Gate G: foreground-impact harness for native optimize.
//   GATE_G_PHASE=predebt   accumulate maintenance debt (checkpoint only), close.
//   GATE_G_PHASE=optimize  wait for start handshake / idle gate, then time ONE
//                          finalize_writes() (the native optimize).
// Scheduling (taskset/nice/ionice/idle gate) is applied by the runner.
// ---------------------------------------------------------------------------
#[cfg(test)]
mod gate_g {
    use super::*;
    use std::io::Write;
    use std::path::PathBuf;
    use std::time::Duration;

    fn env_usize(key: &str, fallback: usize) -> usize {
        std::env::var(key)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(fallback)
    }

    fn env_u64(key: &str, fallback: u64) -> u64 {
        std::env::var(key)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(fallback)
    }

    fn env_path(key: &str) -> PathBuf {
        PathBuf::from(std::env::var(key).unwrap_or_else(|_| panic!("missing env {key}")))
    }

    fn schema_dim(dimension: usize) -> EmbeddingModelInfo {
        let mut info = schema();
        info.dimension = dimension;
        info
    }

    fn open_dim(path: &Path, dimension: usize) -> IndexStore {
        IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
            storage_path: path.to_owned(),
            embeddings: vec![schema_dim(dimension)],
        })
        .expect("open Gate G storage")
    }

    fn gen_vector(seed: u64, dim: usize, index: u64) -> Vec<f32> {
        let mut state = seed
            .wrapping_add(0x9E37_79B9_7F4A_7C15)
            .wrapping_mul(index.wrapping_add(1));
        (0..dim)
            .map(|_| {
                state = state
                    .wrapping_mul(6_364_136_223_846_793_005)
                    .wrapping_add(1_442_695_040_888_963_407);
                ((state >> 33) as f32 / (1u64 << 31) as f32) - 1.0
            })
            .collect()
    }

    fn proc_cpu() -> (u64, u64) {
        let stat = std::fs::read_to_string("/proc/self/stat").unwrap_or_default();
        let rest = match stat.rsplit_once(')') {
            Some((_, rest)) => rest.trim_start(),
            None => return (0, 0),
        };
        let fields: Vec<&str> = rest.split_whitespace().collect();
        let at = |i: usize| fields.get(i).and_then(|v| v.parse().ok()).unwrap_or(0);
        (at(10), at(11))
    }

    fn psi_cpu_avg10() -> f64 {
        let text = std::fs::read_to_string("/proc/pressure/cpu").unwrap_or_default();
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix("some ") {
                for field in rest.split_whitespace() {
                    if let Some(value) = field.strip_prefix("avg10=") {
                        return value.parse().unwrap_or(0.0);
                    }
                }
            }
        }
        0.0
    }

    #[test]
    #[ignore = "Gate G worker; driven by the runner with GATE_G_* env"]
    #[allow(clippy::too_many_lines)]
    fn gate_g_worker() {
        let workdir = env_path("GATE_G_WORKDIR");
        let dim = env_usize("GATE_G_DIM", 64);
        let seed = env_u64("GATE_G_SEED", 0x5EED_1234_ABCD);
        let phase = std::env::var("GATE_G_PHASE").unwrap_or_else(|_| "optimize".into());
        let storage = open_dim(&workdir, dim);

        if phase == "predebt" {
            let predebt = env_usize("GATE_G_PREDEBT", 1000);
            for index in 0..predebt {
                let vector = gen_vector(seed ^ 0x2222, dim, index as u64);
                let (file, entry) = fixture(
                    Some(&storage),
                    &format!("gmut-{index}"),
                    &format!("gate g body {index}"),
                    vector,
                );
                storage
                    .replace_fixture_file(&file, std::slice::from_ref(&entry))
                    .expect("predebt insert");
                storage.checkpoint_only().expect("predebt checkpoint");
            }
            let completeness = storage
                .fragment_completeness_summary_for_test()
                .unwrap_or(-1.0);
            println!("GATE_G_PREDEBT_DONE predebt={predebt} completeness={completeness}");
            storage.close().expect("close predebt");
            return;
        }

        println!("GATE_G_READY");
        std::io::stdout().flush().ok();

        if let Ok(path) = std::env::var("GATE_G_START_FILE") {
            let path = PathBuf::from(path);
            let began = std::time::Instant::now();
            while !path.exists() && began.elapsed().as_secs() < 120 {
                std::thread::sleep(Duration::from_millis(50));
            }
        }

        if std::env::var("GATE_G_IDLE_GATE").map(|v| v == "1").unwrap_or(false) {
            let threshold = std::env::var("GATE_G_IDLE_PSI_CPU")
                .ok()
                .and_then(|v| v.parse::<f64>().ok())
                .unwrap_or(5.0);
            let deadline = env_u64("GATE_G_IDLE_DEADLINE_S", 30);
            let began = std::time::Instant::now();
            loop {
                let psi = psi_cpu_avg10();
                if psi < threshold || began.elapsed().as_secs() >= deadline {
                    println!(
                        "GATE_G_IDLE_OPEN psi_avg10={psi:.2} waited_s={}",
                        began.elapsed().as_secs()
                    );
                    break;
                }
                std::thread::sleep(Duration::from_millis(200));
            }
        }

        let (u0, s0) = proc_cpu();
        let rss_before = gate_e_proc_status_kb("VmRSS").unwrap_or(0);
        let began = std::time::Instant::now();
        storage.finalize_writes().expect("gate g optimize");
        let wall_us = began.elapsed().as_micros();
        let (u1, s1) = proc_cpu();
        let rss_after = gate_e_proc_status_kb("VmRSS").unwrap_or(0);
        let hwm = gate_e_proc_status_kb("VmHWM").unwrap_or(0);
        let completeness = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(-1.0);
        println!(
            "GATE_G_OPTIMIZE wall_us={wall_us} cpu_ticks={} rss_before_kb={rss_before} \
rss_after_kb={rss_after} vmem_hwm_kb={hwm} completeness={completeness}",
            (u1 + s1).saturating_sub(u0 + s0)
        );
        storage.close().expect("close optimize");
    }
}

// ---------------------------------------------------------------------------
// Gate H — native concurrency (H3), Rust mutex blocking (H1), optimize thread
// count (H2). H3 drives zvec_rust directly so the Rust StoreState mutex is not
// in the picture; run it under an external watchdog `timeout`.
// ---------------------------------------------------------------------------
#[cfg(test)]
mod gate_h {
    use super::*;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::time::{Duration, Instant};

    fn env_usize(key: &str, fallback: usize) -> usize {
        std::env::var(key)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(fallback)
    }

    fn vector(seed: u64, dim: usize, index: u64) -> Vec<f32> {
        let mut state = seed
            .wrapping_add(0x9E37_79B9_7F4A_7C15)
            .wrapping_mul(index.wrapping_add(1));
        (0..dim)
            .map(|_| {
                state = state
                    .wrapping_mul(6_364_136_223_846_793_005)
                    .wrapping_add(1_442_695_040_888_963_407);
                ((state >> 33) as f32 / (1u64 << 31) as f32) - 1.0
            })
            .collect()
    }

    fn percentile(sorted: &[u128], pct: usize) -> u128 {
        if sorted.is_empty() {
            return 0;
        }
        sorted[(sorted.len() - 1) * pct / 100]
    }

    fn summarize(latencies: &mut Vec<u128>) -> (u128, u128, u128, u128) {
        latencies.sort_unstable();
        (
            percentile(latencies, 50),
            percentile(latencies, 95),
            percentile(latencies, 99),
            latencies.last().copied().unwrap_or(0),
        )
    }

    #[test]
    #[ignore = "Gate H H3 native concurrency; run under an external timeout watchdog"]
    #[allow(clippy::too_many_lines)]
    fn gate_h_h3_native_concurrency() {
        let _ = crate::storage::zvec::initialize();

        let dim = env_usize("GATE_H_DIM", 64);
        let batch = env_usize("GATE_H_BATCH", 2000);
        let rounds = env_usize("GATE_H_ROUNDS", 3);
        let label = std::env::var("GATE_H_LABEL").unwrap_or_else(|_| "h3".into());

        let directory = tempfile::tempdir().expect("gate h workspace");
        let path = directory.path().join("h3_collection");
        let path_str = path.to_str().expect("utf8 path").to_owned();

        let mut schema = zvec_rust::CollectionSchema::new("h3").expect("h3 schema");
        let mut field = zvec_rust::FieldSchema::new(
            "embedding",
            zvec_rust::DataType::VectorFp32,
            false,
            dim as u32,
        )
        .expect("vector field");
        field
            .set_index_params(
                &zvec_rust::IndexParams::hnsw(zvec_rust::MetricType::Cosine, 50, 500)
                    .expect("hnsw params"),
            )
            .expect("attach vector index");
        schema.add_field(&field).expect("add vector field");
        let options = zvec_rust::CollectionOptions::new().expect("collection options");
        let collection =
            zvec_rust::Collection::create_and_open(&path_str, &schema, Some(&options))
                .expect("open h3 collection");

        // The probe vector is identical to pk "h3-0" so recall@1 is checkable.
        let probe = vector(0xABCD, dim, 0);
        let probe_pk = "h3-0".to_owned();

        let insert = |first: usize, count: usize| {
            let mut offset = 0;
            while offset < count {
                let take = (count - offset).min(1000);
                let mut docs: Vec<zvec_rust::Doc> = Vec::with_capacity(take);
                for index in (first + offset)..(first + offset + take) {
                    let mut doc = zvec_rust::Doc::new().expect("doc");
                    doc.set_pk(&format!("h3-{index}"));
                    doc.add_vector_f32("embedding", &vector(0xABCD, dim, index as u64))
                        .expect("add vector");
                    docs.push(doc);
                }
                let refs: Vec<&zvec_rust::Doc> = docs.iter().collect();
                collection.insert(&refs).expect("insert chunk");
                offset += take;
            }
        };

        for round in 0..rounds {
            insert(round * batch, batch);
            collection.flush().expect("flush batch");

            let completeness_before = collection
                .stats()
                .ok()
                .and_then(|stats| {
                    stats
                        .indexes
                        .iter()
                        .find(|index| index.name == "embedding")
                        .map(|index| f64::from(index.completeness))
                })
                .unwrap_or(f64::NAN);

            let optimizing = AtomicBool::new(false);
            let stop_flag = AtomicBool::new(false);
            let completed_during = AtomicU64::new(0);
            let errors = AtomicU64::new(0);

            let (optimize_us, during, idle, during_count, recall_ok, recall_total) =
                std::thread::scope(|scope| {
                    let query_thread = scope.spawn(|| {
                        let mut during: Vec<u128> = Vec::new();
                        let mut idle: Vec<u128> = Vec::new();
                        let mut recall_ok = 0usize;
                        let mut recall_total = 0usize;
                        while !stop_flag.load(Ordering::Relaxed) {
                            let is_during = optimizing.load(Ordering::Relaxed);
                            let began = Instant::now();
                            let query =
                                zvec_rust::SearchQuery::new("embedding", &probe, 10).expect("query");
                            match collection.query(&query) {
                                Ok(hits) => {
                                    if is_during {
                                        completed_during.fetch_add(1, Ordering::Relaxed);
                                    }
                                    if let Some(first) = hits.first() {
                                        recall_total += 1;
                                        if first.get_pk() == Some(probe_pk.as_str()) {
                                            recall_ok += 1;
                                        }
                                    }
                                    let elapsed = began.elapsed().as_micros();
                                    if is_during {
                                        during.push(elapsed);
                                    } else {
                                        idle.push(elapsed);
                                    }
                                }
                                Err(_) => {
                                    errors.fetch_add(1, Ordering::Relaxed);
                                }
                            }
                        }
                        (during, idle, recall_ok, recall_total)
                    });

                    let began = Instant::now();
                    optimizing.store(true, Ordering::Relaxed);
                    collection.optimize().expect("native optimize");
                    let optimize_us = began.elapsed().as_micros();
                    // Sample post-optimize (idle) latency for comparison.
                    optimizing.store(false, Ordering::Relaxed);
                    std::thread::sleep(Duration::from_millis(150));

                    stop_flag.store(true, Ordering::Relaxed);
                    let (mut during, mut idle, recall_ok, recall_total) =
                        query_thread.join().expect("join query thread");
                    let (dp50, dp95, dp99, dmax) = summarize(&mut during);
                    let (ip50, ip95, ip99, imax) = summarize(&mut idle);
                    (
                        optimize_us,
                        (dp50, dp95, dp99, dmax),
                        (ip50, ip95, ip99, imax),
                        during.len(),
                        recall_ok,
                        recall_total,
                    )
                });

            let completeness_after = collection
                .stats()
                .ok()
                .and_then(|stats| {
                    stats
                        .indexes
                        .iter()
                        .find(|index| index.name == "embedding")
                        .map(|index| f64::from(index.completeness))
                })
                .unwrap_or(f64::NAN);
            let doc_count = collection.stats().ok().map(|stats| stats.doc_count).unwrap_or(0);

            println!(
                "GATE_H_H3 label={label} round={round} docs={doc_count} \
optimize_us={optimize_us} \
during_count={during_count} during_p50_us={} during_p95_us={} during_p99_us={} during_max_us={} \
idle_p50_us={} idle_p95_us={} idle_p99_us={} idle_max_us={} \
recall_at_1={recall_ok}/{recall_total} errors={} \
completeness_before={completeness_before} completeness_after={completeness_after}",
                during.0, during.1, during.2, during.3,
                idle.0, idle.1, idle.2, idle.3,
                errors.load(Ordering::Relaxed),
            );
        }

        collection.close().expect("close h3 collection");
        directory.close().expect("drop h3 workspace");
    }

    fn schema_dim(dimension: usize) -> EmbeddingModelInfo {
        let mut info = schema();
        info.dimension = dimension;
        info
    }

    #[test]
    #[ignore = "Gate H H1 rust mutex blocking; needs GATE_H_STORE (a debt snapshot copy)"]
    #[allow(clippy::too_many_lines)]
    fn gate_h_h1_mutex_blocking() {
        let _ = crate::storage::zvec::initialize();
        let dim = env_usize("GATE_H_DIM", 64);
        let store_path = std::env::var("GATE_H_STORE")
            .expect("GATE_H_STORE must point to a debt snapshot copy");
        let storage = IndexStore::open(WorkspaceIndexStorageOptions::ReadWrite {
            storage_path: std::path::PathBuf::from(&store_path),
            embeddings: vec![schema_dim(dim)],
        })
        .expect("open H1 store");
        let probe = vector(0xABCD, dim, 0);

        let optimizing = AtomicBool::new(false);
        let stop_flag = AtomicBool::new(false);
        let errors = AtomicU64::new(0);

        let (optimize_us, during, idle, during_count, idle_count) = std::thread::scope(|scope| {
            let searcher = scope.spawn(|| {
                let mut during: Vec<u128> = Vec::new();
                let mut idle: Vec<u128> = Vec::new();
                while !stop_flag.load(Ordering::Relaxed) {
                    let is_during = optimizing.load(Ordering::Relaxed);
                    let began = Instant::now();
                    match storage.search_vector("fixture/fixture-model", &probe, 5, None) {
                        Ok(_) => {
                            let elapsed = began.elapsed().as_micros();
                            if is_during {
                                during.push(elapsed);
                            } else {
                                idle.push(elapsed);
                            }
                        }
                        Err(_) => {
                            errors.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                }
                (during, idle)
            });

            let began = Instant::now();
            optimizing.store(true, Ordering::Relaxed);
            storage.finalize_writes().expect("native optimize under store mutex");
            let optimize_us = began.elapsed().as_micros();
            optimizing.store(false, Ordering::Relaxed);
            std::thread::sleep(Duration::from_millis(150));
            stop_flag.store(true, Ordering::Relaxed);

            let (mut during, mut idle) = searcher.join().expect("join searcher");
            let (dp50, dp95, dp99, dmax) = summarize(&mut during);
            let (ip50, ip95, ip99, imax) = summarize(&mut idle);
            let during_count = during.len();
            let idle_count = idle.len();
            println!(
                "GATE_H_H1 optimize_us={optimize_us} \
during_count={during_count} during_p50_us={dp50} during_p95_us={dp95} during_p99_us={dp99} during_max_us={dmax} \
idle_count={idle_count} idle_p50_us={ip50} idle_p95_us={ip95} idle_p99_us={ip99} idle_max_us={imax} \
errors={}",
                errors.load(Ordering::Relaxed),
            );
            (optimize_us, (dp50, dp95, dp99, dmax), (ip50, ip95, ip99, imax), during_count, idle_count)
        });
        let _ = (optimize_us, during, idle, during_count, idle_count);
        storage.close().expect("close H1 store");
    }

    struct MutationReport {
        inserted: Vec<(String, Vec<f32>)>,
        updated: Vec<(String, Vec<f32>)>,
        deleted: Vec<String>,
        during: Vec<u128>,
        idle: Vec<u128>,
        errors: u64,
        ops: u64,
    }

    #[test]
    #[ignore = "Gate I concurrent mutation + optimize; run under an external timeout watchdog"]
    #[allow(clippy::too_many_lines)]
    fn gate_h_i_concurrent_mutation() {
        let _ = crate::storage::zvec::initialize();
        let dim = env_usize("GATE_H_DIM", 64);
        let seed_n = env_usize("GATE_H_SEED_DOCS", 4000);
        let cap = env_usize("GATE_H_OPS", 4000);
        let label = std::env::var("GATE_H_LABEL").unwrap_or_else(|_| "i".into());
        const SEED: u64 = 0x5EED_C0DE;

        let directory = tempfile::tempdir().expect("gate i workspace");
        let path = directory.path().join("i_collection");
        let path_str = path.to_str().expect("utf8").to_owned();

        let mut schema = zvec_rust::CollectionSchema::new("gate_i").expect("schema");
        let mut field =
            zvec_rust::FieldSchema::new("embedding", zvec_rust::DataType::VectorFp32, false, dim as u32)
                .expect("vector field");
        field
            .set_index_params(
                &zvec_rust::IndexParams::hnsw(zvec_rust::MetricType::Cosine, 50, 500).expect("hnsw"),
            )
            .expect("attach index");
        schema.add_field(&field).expect("add field");
        let options = zvec_rust::CollectionOptions::new().expect("options");
        let collection = zvec_rust::Collection::create_and_open(&path_str, &schema, Some(&options))
            .expect("open gate i collection");

        // Seed docs: pk "seed-{i}" with vector SEED,i.
        let mut offset = 0;
        while offset < seed_n {
            let take = (seed_n - offset).min(1000);
            let mut docs = Vec::with_capacity(take);
            for index in offset..offset + take {
                let mut doc = zvec_rust::Doc::new().expect("doc");
                doc.set_pk(&format!("seed-{index}"));
                doc.add_vector_f32("embedding", &vector(SEED, dim, index as u64))
                    .expect("vector");
                docs.push(doc);
            }
            let refs: Vec<&zvec_rust::Doc> = docs.iter().collect();
            collection.insert(&refs).expect("seed insert");
            offset += take;
        }
        collection.flush().expect("flush seed");

        let probe = vector(SEED, dim, 0);
        let probe_pk = "seed-0".to_owned();
        let optimizing = AtomicBool::new(false);
        let stop = AtomicBool::new(false);

        let (optimize_us, mutations, queries, violations, query_errors) =
            std::thread::scope(|scope| {
                let mutating = scope.spawn(|| {
                    let mut report = MutationReport {
                        inserted: Vec::new(),
                        updated: Vec::new(),
                        deleted: Vec::new(),
                        during: Vec::new(),
                        idle: Vec::new(),
                        errors: 0,
                        ops: 0,
                    };
                    let mut ins = 0usize;
                    let mut upd = 1usize; // keep seed-0 stable for the probe
                    let mut del = seed_n / 2;
                    while !stop.load(Ordering::Relaxed) && report.ops < cap as u64 {
                        let during = optimizing.load(Ordering::Relaxed);
                        let began = Instant::now();
                        let kind = report.ops % 3;
                        let outcome = if kind == 1 && upd < seed_n / 2 {
                            let pk = format!("seed-{upd}");
                            let vec = vector(SEED ^ 0x2222, dim, 2_000_000 + upd as u64);
                            let mut doc = zvec_rust::Doc::new().expect("doc");
                            doc.set_pk(&pk);
                            doc.add_vector_f32("embedding", &vec).expect("vector");
                            let ok = collection.update(&[&doc]).is_ok();
                            if ok {
                                report.updated.push((pk, vec));
                            }
                            upd += 1;
                            ok
                        } else if kind == 2 && del < seed_n {
                            let pk = format!("seed-{del}");
                            let ok = collection.delete(&[pk.as_str()]).is_ok();
                            if ok {
                                report.deleted.push(pk);
                            }
                            del += 1;
                            ok
                        } else {
                            let pk = format!("ins-{ins}");
                            let vec = vector(SEED ^ 0x1111, dim, 1_000_000 + ins as u64);
                            let mut doc = zvec_rust::Doc::new().expect("doc");
                            doc.set_pk(&pk);
                            doc.add_vector_f32("embedding", &vec).expect("vector");
                            let ok = collection.insert(&[&doc]).is_ok();
                            if ok {
                                report.inserted.push((pk, vec));
                            }
                            ins += 1;
                            ok
                        };
                        if !outcome {
                            report.errors += 1;
                        }
                        let elapsed = began.elapsed().as_micros();
                        if during {
                            report.during.push(elapsed);
                        } else {
                            report.idle.push(elapsed);
                        }
                        report.ops += 1;
                        std::thread::sleep(Duration::from_micros(200));
                    }
                    report
                });

                let searching = scope.spawn(|| {
                    let mut violations = 0u64;
                    let mut errors = 0u64;
                    while !stop.load(Ordering::Relaxed) {
                        let query =
                            zvec_rust::SearchQuery::new("embedding", &probe, 5).expect("query");
                        match collection.query(&query) {
                            Ok(hits) => {
                                if hits.first().and_then(|doc| doc.get_pk()) != Some(probe_pk.as_str())
                                {
                                    violations += 1;
                                }
                            }
                            Err(_) => errors += 1,
                        }
                    }
                    (violations, errors)
                });

                let began = Instant::now();
                optimizing.store(true, Ordering::Relaxed);
                collection.optimize().expect("optimize during mutation");
                let optimize_us = began.elapsed().as_micros();
                optimizing.store(false, Ordering::Relaxed);
                std::thread::sleep(Duration::from_millis(200));
                stop.store(true, Ordering::Relaxed);

                let mutations = mutating.join().expect("join mutator");
                let (violations, query_errors) = searching.join().expect("join searcher");
                (optimize_us, mutations, (), violations, query_errors)
            });
        let _ = queries;

        // ---- verification after optimize ----
        let present = |pk: &str, vec: &[f32]| -> bool {
            let query = zvec_rust::SearchQuery::new("embedding", vec, 5).expect("verify query");
            collection
                .query(&query)
                .map(|hits| hits.iter().any(|doc| doc.get_pk() == Some(pk)))
                .unwrap_or(false)
        };
        let mut inserted_ok = 0usize;
        let mut inserted_total = 0usize;
        for (pk, vec) in mutations.inserted.iter().take(200) {
            inserted_total += 1;
            if present(pk, vec) {
                inserted_ok += 1;
            }
        }
        let mut updated_ok = 0usize;
        let mut updated_total = 0usize;
        let mut updated_last: std::collections::HashMap<&str, &Vec<f32>> = std::collections::HashMap::new();
        for (pk, vec) in &mutations.updated {
            updated_last.insert(pk.as_str(), vec);
        }
        for (pk, vec) in updated_last.iter().take(200) {
            updated_total += 1;
            let pk: &str = pk;
            let vec: &Vec<f32> = vec;
            if present(pk, vec) {
                updated_ok += 1;
            }
        }
        let mut deleted_survived = 0usize;
        let mut deleted_total = 0usize;
        for pk in mutations.deleted.iter().take(200) {
            deleted_total += 1;
            let index: usize = pk.trim_start_matches("seed-").parse().unwrap_or(0);
            if present(pk, &vector(SEED, dim, index as u64)) {
                deleted_survived += 1;
            }
        }
        let mut during = mutations.during.clone();
        let mut idle = mutations.idle.clone();
        let (mdp50, mdp95, _, mdmax) = summarize(&mut during);
        let (mip50, mip95, _, mimax) = summarize(&mut idle);
        let doc_count = collection.stats().ok().map(|stats| stats.doc_count).unwrap_or(0);

        println!(
            "GATE_H_I label={label} optimize_us={optimize_us} ops={} mut_errors={} \
doc_count={doc_count} \
writer_during_n={} writer_p50_us={mdp50} writer_p95_us={mdp95} writer_max_us={mdmax} \
writer_idle_n={} writer_idle_p50_us={mip50} writer_idle_p95_us={mip95} writer_idle_max_us={mimax} \
inserted_searchable={inserted_ok}/{inserted_total} updated_current={updated_ok}/{updated_total} \
deleted_still_returned={deleted_survived}/{deleted_total} \
search_violations={violations} query_errors={query_errors}",
            mutations.ops,
            mutations.errors,
            mutations.during.len(),
            mutations.idle.len(),
        );

        collection.close().expect("close gate i collection");
        directory.close().expect("drop gate i workspace");
    }

    // Lifecycle modes for Gate I crash/recovery. Driven by the runner:
    //   GATE_H_I_MODE=build | optimize | verify   with GATE_H_I_PATH.
    #[test]
    #[ignore = "Gate I lifecycle (kill mid-optimize, reopen, retry); env-driven"]
    #[allow(clippy::too_many_lines)]
    fn gate_h_i_lifecycle() {
        let _ = crate::storage::zvec::initialize();
        let dim = env_usize("GATE_H_DIM", 64);
        let docs = env_usize("GATE_H_I_DOCS", 8000);
        let mode = std::env::var("GATE_H_I_MODE").unwrap_or_else(|_| "verify".into());
        let path = std::env::var("GATE_H_I_PATH").expect("GATE_H_I_PATH");
        const SEED: u64 = 0x5EED_C0DE;

        let options = zvec_rust::CollectionOptions::new().expect("options");

        if mode == "build" {
            let mut schema = zvec_rust::CollectionSchema::new("gate_i_life").expect("schema");
            let mut field = zvec_rust::FieldSchema::new(
                "embedding",
                zvec_rust::DataType::VectorFp32,
                false,
                dim as u32,
            )
            .expect("vector field");
            field
                .set_index_params(
                    &zvec_rust::IndexParams::hnsw(zvec_rust::MetricType::Cosine, 50, 500)
                        .expect("hnsw"),
                )
                .expect("attach index");
            schema.add_field(&field).expect("add field");
            let collection =
                zvec_rust::Collection::create_and_open(&path, &schema, Some(&options))
                    .expect("create lifecycle collection");
            let mut offset = 0;
            while offset < docs {
                let take = (docs - offset).min(1000);
                let mut batch = Vec::with_capacity(take);
                for index in offset..offset + take {
                    let mut doc = zvec_rust::Doc::new().expect("doc");
                    doc.set_pk(&format!("seed-{index}"));
                    doc.add_vector_f32("embedding", &vector(SEED, dim, index as u64))
                        .expect("vector");
                    batch.push(doc);
                }
                let refs: Vec<&zvec_rust::Doc> = batch.iter().collect();
                collection.insert(&refs).expect("insert");
                offset += take;
            }
            collection.flush().expect("flush");
            let completeness = collection
                .stats()
                .ok()
                .and_then(|s| s.indexes.iter().find(|i| i.name == "embedding").map(|i| i.completeness))
                .unwrap_or(f64::NAN as f32);
            println!("GATE_H_IL mode=build docs={docs} completeness_before={completeness}");
            collection.close().expect("close build");
            return;
        }

        let collection = zvec_rust::Collection::open(&path, Some(&options)).expect("open lifecycle");
        if mode == "optimize" {
            let began = Instant::now();
            collection.optimize().expect("optimize (may be killed)");
            println!("GATE_H_IL mode=optimize optimize_us={} (not killed)", began.elapsed().as_micros());
            collection.close().expect("close optimize");
            return;
        }

        // verify: retry optimize if still incomplete, then check docs + recall.
        let before = collection
            .stats()
            .ok()
            .and_then(|s| s.indexes.iter().find(|i| i.name == "embedding").map(|i| i.completeness))
            .unwrap_or(f32::NAN);
        if !(before >= 1.0) {
            collection.optimize().expect("retry optimize after kill");
        }
        let after = collection
            .stats()
            .ok()
            .and_then(|s| s.indexes.iter().find(|i| i.name == "embedding").map(|i| i.completeness))
            .unwrap_or(f32::NAN);
        let doc_count = collection.stats().ok().map(|s| s.doc_count).unwrap_or(0);
        let probe = vector(SEED, dim, 0);
        let query = zvec_rust::SearchQuery::new("embedding", &probe, 5).expect("query");
        let recall_ok = collection
            .query(&query)
            .map(|hits| hits.first().and_then(|doc| doc.get_pk()) == Some("seed-0"))
            .unwrap_or(false);
        println!(
            "GATE_H_IL mode=verify completeness_before={before} completeness_after={after} \
doc_count={doc_count} recall_seed0_top1={recall_ok}"
        );
        collection.close().expect("close verify");
    }
}

// ---------------------------------------------------------------------------
// Gate J — test-only prototype of lock split + epoch/ack debt accounting.
//
// Models the intended M3-B control flow on the real native Collection:
//   * the "store state" is a Mutex<Debt>; the Collection is reached through an
//     Arc so native optimize() can run WITHOUT holding that mutex (lock split);
//   * every fragment mutation bumps Debt::epoch and sets fragments_pending;
//   * finalize captures snapshot_epoch, runs optimize() outside the lock, and
//     only clears fragments_pending when epoch is unchanged (ack the exact debt).
// No scheduler, idle policy, K=50, or production default changes.
// ---------------------------------------------------------------------------
#[cfg(test)]
mod gate_j {
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};

    fn env_usize(key: &str, fallback: usize) -> usize {
        std::env::var(key)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(fallback)
    }

    fn vector(seed: u64, dim: usize, index: u64) -> Vec<f32> {
        let mut state = seed
            .wrapping_add(0x9E37_79B9_7F4A_7C15)
            .wrapping_mul(index.wrapping_add(1));
        (0..dim)
            .map(|_| {
                state = state
                    .wrapping_mul(6_364_136_223_846_793_005)
                    .wrapping_add(1_442_695_040_888_963_407);
                ((state >> 33) as f32 / (1u64 << 31) as f32) - 1.0
            })
            .collect()
    }

    fn completeness(collection: &zvec_rust::Collection) -> f32 {
        collection
            .stats()
            .ok()
            .and_then(|stats| {
                stats
                    .indexes
                    .iter()
                    .find(|index| index.name == "embedding")
                    .map(|index| index.completeness)
            })
            .unwrap_or(f32::NAN)
    }

    fn schema(dim: usize) -> zvec_rust::CollectionSchema {
        let mut schema = zvec_rust::CollectionSchema::new("gate_j").expect("schema");
        let mut field = zvec_rust::FieldSchema::new(
            "embedding",
            zvec_rust::DataType::VectorFp32,
            false,
            dim as u32,
        )
        .expect("vector field");
        field
            .set_index_params(
                &zvec_rust::IndexParams::hnsw(zvec_rust::MetricType::Cosine, 50, 500)
                    .expect("hnsw"),
            )
            .expect("attach index");
        schema.add_field(&field).expect("add field");
        schema
    }

    fn make_doc(pk: &str, vec: &[f32]) -> zvec_rust::Doc {
        let mut doc = zvec_rust::Doc::new().expect("doc");
        doc.set_pk(pk);
        doc.add_vector_f32("embedding", vec).expect("vector");
        doc
    }

    fn seed(collection: &zvec_rust::Collection, dim: usize, docs: usize) {
        let mut offset = 0;
        while offset < docs {
            let take = (docs - offset).min(1000);
            let batch: Vec<zvec_rust::Doc> = (offset..offset + take)
                .map(|index| make_doc(&format!("seed-{index}"), &vector(0x5EED_C0DE, dim, index as u64)))
                .collect();
            let refs: Vec<&zvec_rust::Doc> = batch.iter().collect();
            collection.insert(&refs).expect("seed insert");
            offset += take;
        }
    }

    struct Debt {
        epoch: u64,
        fragments_pending: bool,
    }

    struct Proto {
        debt: Mutex<Debt>,
        collection: zvec_rust::Collection,
    }

    /// Capture debt epoch, run native optimize OUTSIDE the state mutex, then
    /// acknowledge only if no new mutation arrived (epoch unchanged).
    fn split_finalize(proto: &Arc<Proto>, gate: &AtomicBool) -> (u128, bool) {
        let snapshot_epoch = proto.debt.lock().expect("state").epoch;
        gate.store(true, Ordering::SeqCst);
        let began = Instant::now();
        proto.collection.optimize().expect("native optimize");
        let optimize_us = began.elapsed().as_micros();
        gate.store(false, Ordering::SeqCst);
        let mut debt = proto.debt.lock().expect("state");
        let acked = debt.epoch == snapshot_epoch;
        if acked {
            debt.fragments_pending = false;
        }
        (optimize_us, acked)
    }

    fn mutate(proto: &Arc<Proto>, dim: usize, counter: u64) -> (String, Vec<f32>) {
        let pk = format!("ins-{counter}");
        let vec = vector(0x1111_2222, dim, 1_000_000 + counter);
        let mut debt = proto.debt.lock().expect("state");
        let doc = make_doc(&pk, &vec);
        proto
            .collection
            .insert(&[&doc])
            .expect("mutate insert during optimize");
        debt.epoch += 1;
        debt.fragments_pending = true;
        (pk, vec)
    }

    #[test]
    #[ignore = "Gate J lock-split + epoch/ack prototype; run under an external timeout watchdog"]
    #[allow(clippy::too_many_lines)]
    fn gate_j_lock_split_proto() {
        let _ = crate::storage::zvec::initialize();
        let dim = env_usize("GATE_H_DIM", 64);
        let seed_n = env_usize("GATE_H_SEED_DOCS", 4000);
        let label = std::env::var("GATE_H_LABEL").unwrap_or_else(|_| "j".into());

        let directory = tempfile::tempdir().expect("gate j workspace");
        let path_str = directory
            .path()
            .join("j_collection")
            .to_str()
            .expect("utf8")
            .to_owned();
        let options = zvec_rust::CollectionOptions::new().expect("options");
        let collection =
            zvec_rust::Collection::create_and_open(&path_str, &schema(dim), Some(&options))
                .expect("create gate j collection");
        seed(&collection, dim, seed_n);
        collection.flush().expect("flush seed");
        let completeness_start = completeness(&collection);

        let proto = Arc::new(Proto {
            debt: Mutex::new(Debt {
                epoch: 0,
                fragments_pending: true,
            }),
            collection,
        });
        let gate = Arc::new(AtomicBool::new(false));
        let stop = Arc::new(AtomicBool::new(false));
        let inserted = Arc::new(Mutex::new(Vec::<(String, Vec<f32>)>::new()));
        let b_errors = Arc::new(AtomicU64::new(0));
        let c_checked = Arc::new(AtomicU64::new(0));
        let c_found = Arc::new(AtomicU64::new(0));

        let (optimize_us, acked_after_a) = std::thread::scope(|scope| {
            let mutator = {
                let proto = Arc::clone(&proto);
                let gate = Arc::clone(&gate);
                let stop = Arc::clone(&stop);
                let inserted = Arc::clone(&inserted);
                let errors = Arc::clone(&b_errors);
                scope.spawn(move || {
                    let mut counter = 0u64;
                    while !stop.load(Ordering::Relaxed) {
                        if gate.load(Ordering::Relaxed) {
                            match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                                mutate(&proto, dim, counter)
                            })) {
                                Ok((pk, vec)) => {
                                    inserted.lock().expect("inserted").push((pk, vec));
                                    counter += 1;
                                }
                                Err(_) => {
                                    errors.fetch_add(1, Ordering::Relaxed);
                                }
                            }
                        } else {
                            std::thread::sleep(Duration::from_micros(100));
                        }
                    }
                })
            };
            let searcher = {
                let proto = Arc::clone(&proto);
                let stop = Arc::clone(&stop);
                let inserted = Arc::clone(&inserted);
                let checked = Arc::clone(&c_checked);
                let found = Arc::clone(&c_found);
                scope.spawn(move || {
                    while !stop.load(Ordering::Relaxed) {
                        // Snapshot the newest required result, then query WITHOUT
                        // holding the state mutex (lock split for the read path).
                        let target = inserted.lock().expect("inserted").last().cloned();
                        if let Some((pk, vec)) = target {
                            let query =
                                zvec_rust::SearchQuery::new("embedding", &vec, 5).expect("query");
                            if let Ok(hits) = proto.collection.query(&query) {
                                checked.fetch_add(1, Ordering::Relaxed);
                                if hits.iter().any(|doc| doc.get_pk() == Some(pk.as_str())) {
                                    found.fetch_add(1, Ordering::Relaxed);
                                }
                            }
                        } else {
                            std::thread::sleep(Duration::from_micros(100));
                        }
                    }
                })
            };

            // Job A: split finalize concurrent with B (mutator) and C (searcher).
            let (optimize_us, acked) = split_finalize(&proto, &gate);
            stop.store(true, Ordering::Relaxed);
            mutator.join().expect("join mutator");
            searcher.join().expect("join searcher");
            (optimize_us, acked)
        });

        let (pending_after_a, epoch_after_a) = {
            let debt = proto.debt.lock().expect("state");
            (debt.fragments_pending, debt.epoch)
        };
        let b_inserted = inserted.lock().expect("inserted").len();

        // Pass 2: nothing mutates now, so the same debt must be acked and cleared.
        let (optimize2_us, acked_after_pass2) = split_finalize(&proto, &gate);
        let (pending_after_pass2, _) = {
            let debt = proto.debt.lock().expect("state");
            (debt.fragments_pending, debt.epoch)
        };
        let completeness_end = completeness(&proto.collection);

        // All inserted-during-optimize docs must be searchable after convergence.
        let mut inserted_searchable = 0usize;
        for (pk, vec) in inserted.lock().expect("inserted").iter() {
            let query = zvec_rust::SearchQuery::new("embedding", vec, 5).expect("query");
            if proto
                .collection
                .query(&query)
                .map(|hits| hits.iter().any(|doc| doc.get_pk() == Some(pk.as_str())))
                .unwrap_or(false)
            {
                inserted_searchable += 1;
            }
        }

        println!(
            "GATE_J label={label} optimize_a_us={optimize_us} acked_after_a={acked_after_a} \
pending_after_a={pending_after_a} epoch_after_a={epoch_after_a} \
b_inserted_during={b_inserted} b_errors={} \
search_c_checked={} search_c_found_new={} \
optimize2_us={optimize2_us} acked_after_pass2={acked_after_pass2} pending_after_pass2={pending_after_pass2} \
completeness_start={completeness_start} completeness_end={completeness_end} \
inserted_searchable={inserted_searchable}/{b_inserted}",
            b_errors.load(Ordering::Relaxed),
            c_checked.load(Ordering::Relaxed),
            c_found.load(Ordering::Relaxed),
        );

        drop(proto);
        directory.close().expect("drop gate j workspace");
    }

    // Crash/reopen for the prototype: build debt, kill mid-optimize, reopen and
    // converge. Debt is recomputed from native completeness on reopen, exactly
    // like production `optimize_fragments = flag || fragments_incomplete`.
    #[test]
    #[ignore = "Gate J lifecycle (kill mid-optimize, reopen, converge); env-driven"]
    #[allow(clippy::too_many_lines)]
    fn gate_j_lifecycle() {
        let _ = crate::storage::zvec::initialize();
        let dim = env_usize("GATE_H_DIM", 64);
        let docs = env_usize("GATE_J_DOCS", 16000);
        let mode = std::env::var("GATE_J_MODE").unwrap_or_else(|_| "verify".into());
        let path = std::env::var("GATE_J_PATH").expect("GATE_J_PATH");
        let options = zvec_rust::CollectionOptions::new().expect("options");

        if mode == "build" {
            let collection =
                zvec_rust::Collection::create_and_open(&path, &schema(dim), Some(&options))
                    .expect("create");
            seed(&collection, dim, docs);
            collection.flush().expect("flush");
            println!("GATE_J_LIFECYCLE mode=build docs={docs} completeness={}", completeness(&collection));
            collection.close().expect("close build");
            return;
        }

        let collection = zvec_rust::Collection::open(&path, Some(&options)).expect("open");
        if mode == "split" {
            let proto = Arc::new(Proto {
                debt: Mutex::new(Debt {
                    epoch: 0,
                    fragments_pending: true,
                }),
                collection,
            });
            let gate = AtomicBool::new(false);
            let (us, acked) = split_finalize(&proto, &gate);
            println!("GATE_J_LIFECYCLE mode=split optimize_us={us} acked={acked} (not killed)");
            return;
        }

        // verify: recompute debt from native completeness, then converge.
        let before = completeness(&collection);
        let mut optimize_us = 0u128;
        if before < 1.0 {
            let began = Instant::now();
            collection.optimize().expect("retry optimize after kill");
            optimize_us = began.elapsed().as_micros();
        }
        let after = completeness(&collection);
        let probe = vector(0x5EED_C0DE, dim, 0);
        let query = zvec_rust::SearchQuery::new("embedding", &probe, 5).expect("query");
        let recall = collection
            .query(&query)
            .map(|hits| hits.first().and_then(|doc| doc.get_pk()) == Some("seed-0"))
            .unwrap_or(false);
        let doc_count = collection.stats().ok().map(|stats| stats.doc_count).unwrap_or(0);
        println!(
            "GATE_J_LIFECYCLE mode=verify completeness_before={before} optimize_us={optimize_us} \
completeness_after={after} doc_count={doc_count} recall_seed0_top1={recall}"
        );
        collection.close().expect("close verify");
    }
}

// ---------------------------------------------------------------------------
// Gate K — production IndexStore lock-split acceptance (real store, not Proto).
// Verifies: search served during optimize (latency not equal to optimize wall),
// a writer commits during optimize without losing fragment debt, optimize does
// not acknowledge the new mutation, and the next round reaches completeness 1.
// ---------------------------------------------------------------------------
#[cfg(test)]
mod gate_k {
    use super::*;
    use std::sync::Mutex;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::time::{Duration, Instant};

    fn pct(sorted: &[u128], percent: usize) -> u128 {
        if sorted.is_empty() {
            return 0;
        }
        sorted[(sorted.len() - 1) * percent / 100]
    }

    #[test]
    #[ignore = "Gate K production lock-split acceptance; run under an external timeout watchdog"]
    #[allow(clippy::too_many_lines)]
    fn gate_k_lock_split_store() {
        let temporary = tempfile::tempdir().expect("workspace");
        let storage = open(temporary.path(), false);
        let model = "fixture/fixture-model";
        let probe = vec![1.0_f32, 0.0, 0.0];

        for index in 0..200 {
            let (file, entry) = fixture(
                Some(&storage),
                &format!("base-{index}"),
                &format!("base {index}"),
                probe.clone(),
            );
            storage
                .replace_fixture_file(&file, std::slice::from_ref(&entry))
                .expect("seed base");
        }
        storage.finalize_writes().expect("baseline optimize");

        for index in 0..3000 {
            let (file, entry) = fixture(
                Some(&storage),
                &format!("debt-{index}"),
                &format!("debt {index}"),
                probe.clone(),
            );
            storage
                .replace_fixture_file(&file, std::slice::from_ref(&entry))
                .expect("seed debt");
        }

        let mut idle_ref = Vec::new();
        for _ in 0..40 {
            let began = Instant::now();
            let _ = storage.search_vector(model, &probe, 5, None).expect("idle search");
            idle_ref.push(began.elapsed().as_micros());
        }
        idle_ref.sort_unstable();

        let optimizing = AtomicBool::new(false);
        let stop = AtomicBool::new(false);
        let writer_ops = AtomicUsize::new(0);
        let late: Mutex<Vec<(FileId, Vec<f32>)>> = Mutex::new(Vec::new());

        let (finalize_us, mut during, mut after) = std::thread::scope(|scope| {
            let searcher = scope.spawn(|| {
                let mut during = Vec::new();
                let mut after = Vec::new();
                while !stop.load(Ordering::Relaxed) {
                    let is_during = optimizing.load(Ordering::Relaxed);
                    let began = Instant::now();
                    let _ = storage.search_vector(model, &probe, 5, None).expect("search");
                    let elapsed = began.elapsed().as_micros();
                    if is_during {
                        during.push(elapsed);
                    } else {
                        after.push(elapsed);
                    }
                }
                (during, after)
            });
            let writer = scope.spawn(|| {
                let mut n = 0usize;
                while !stop.load(Ordering::Relaxed) && n < 50 {
                    if optimizing.load(Ordering::Relaxed) {
                        let vector = vec![0.0_f32, 1.0, (n as f32) + 1.0];
                        let (file, entry) = fixture(
                            Some(&storage),
                            &format!("late-{n}"),
                            &format!("late {n}"),
                            vector.clone(),
                        );
                        storage
                            .replace_fixture_file(&file, std::slice::from_ref(&entry))
                            .expect("write during optimize");
                        late.lock().expect("late").push((file.id, vector));
                        writer_ops.fetch_add(1, Ordering::Relaxed);
                        n += 1;
                        std::thread::sleep(Duration::from_millis(5));
                    } else {
                        std::thread::sleep(Duration::from_micros(200));
                    }
                }
            });

            optimizing.store(true, Ordering::Relaxed);
            let began = Instant::now();
            storage.finalize_writes().expect("optimize under lock split");
            let finalize_us = began.elapsed().as_micros();
            optimizing.store(false, Ordering::Relaxed);
            std::thread::sleep(Duration::from_millis(150));
            stop.store(true, Ordering::Relaxed);

            let (during, after) = searcher.join().expect("join searcher");
            writer.join().expect("join writer");
            (finalize_us, during, after)
        });

        during.sort_unstable();
        after.sort_unstable();
        let during_p50 = pct(&during, 50);
        let during_p95 = pct(&during, 95);
        let during_max = during.last().copied().unwrap_or(0);

        let (pending_after_first, _epoch) = storage
            .finalize_debt_for_test()
            .expect("debt after first optimize");
        let wrote = writer_ops.load(Ordering::Relaxed);
        let late_entries = late.lock().expect("late").clone();

        // Second maintenance round with no concurrent writers must converge.
        storage.finalize_writes().expect("second optimize");
        let (pending_after_second, _) = storage
            .finalize_debt_for_test()
            .expect("debt after second optimize");
        let completeness = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(f32::NAN);

        let mut late_searchable = 0usize;
        let fts_hits = storage
            .search_fts("late", 500, None)
            .expect("fts late search");
        let fts_ids: std::collections::HashSet<FileId> =
            fts_hits.iter().map(|hit| hit.file_id).collect();
        for (file_id, _vector) in &late_entries {
            if fts_ids.contains(file_id) {
                late_searchable += 1;
            }
        }
        let late_in_index = storage
            .list_file_paths()
            .expect("paths")
            .iter()
            .filter(|(_, path)| path.to_string_lossy().contains("late-"))
            .count();

        println!(
            "GATE_K finalize_us={finalize_us} during_n={} during_p50_us={during_p50} \
during_p95_us={during_p95} during_max_us={during_max} idle_ref_p95_us={} after_n={} \
writer_ops={wrote} pending_after_first={pending_after_first} pending_after_second={pending_after_second} \
completeness={completeness} late_searchable={late_searchable}/{} late_in_index={late_in_index}",
            during.len(),
            pct(&idle_ref, 95),
            after.len(),
            late_entries.len(),
        );

        // Search must not be blocked for the optimize wall.
        assert!(during.len() > 0, "searcher never observed the optimize window");
        assert!(wrote > 0, "writer never committed during optimize");
        assert!(
            during_p95 < finalize_us.max(1),
            "search p95 ({during_p95}us) blocked by optimize wall ({finalize_us}us)"
        );
        // A writer during optimize must not lose its fragment debt.
        assert!(
            pending_after_first,
            "optimize acknowledged the concurrent writer's debt"
        );
        assert!(
            late_searchable == late_entries.len(),
            "writer vectors must be searchable after converged maintenance"
        );
        assert!(!pending_after_second, "second round must clear the debt");
        assert!(
            completeness >= 1.0,
            "completeness must converge to 1.0, got {completeness}"
        );

        storage.close().expect("close store");
    }

    #[test]
    #[ignore = "Gate K optimize failure recoverability; run under an external timeout watchdog"]
    fn gate_k_optimize_failure_recoverable() {
        let temporary = tempfile::tempdir().expect("workspace");
        let storage = open(temporary.path(), false);
        let probe = vec![1.0_f32, 0.0, 0.0];
        for index in 0..500 {
            let (file, entry) = fixture(
                Some(&storage),
                &format!("f-{index}"),
                &format!("f {index}"),
                probe.clone(),
            );
            storage
                .replace_fixture_file(&file, std::slice::from_ref(&entry))
                .expect("seed");
        }

        storage.set_fail_next_optimize_for_test();
        let error = storage
            .finalize_writes()
            .expect_err("injected optimize failure must surface");
        let (pending_after_failure, _) = storage.finalize_debt_for_test().expect("debt");
        let completeness_before_retry = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(f32::NAN);

        storage.finalize_writes().expect("retry after failure");
        let (pending_after_retry, _) = storage.finalize_debt_for_test().expect("debt2");
        let completeness_after_retry = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(f32::NAN);

        println!(
            "GATE_K_FAIL error_code={} pending_after_failure={pending_after_failure} \
completeness_before_retry={completeness_before_retry} pending_after_retry={pending_after_retry} \
completeness_after_retry={completeness_after_retry}",
            error.code(),
        );

        assert!(pending_after_failure, "failure must keep the debt recoverable");
        assert!(!pending_after_retry, "retry must clear the debt");
        assert!(completeness_after_retry >= 1.0, "retry must converge");
        storage.close().expect("close store");
    }

    #[test]
    #[ignore = "Gate K close waits for maintenance; run under an external timeout watchdog"]
    fn gate_k_close_waits_for_maintenance() {
        let temporary = tempfile::tempdir().expect("workspace");
        let path = temporary.path().to_owned();
        let probe = vec![1.0_f32, 0.0, 0.0];
        let storage = open(&path, false);
        for index in 0..2000 {
            let (file, entry) = fixture(
                Some(&storage),
                &format!("c-{index}"),
                &format!("c {index}"),
                probe.clone(),
            );
            storage
                .replace_fixture_file(&file, std::slice::from_ref(&entry))
                .expect("seed");
        }

        let (close_ok, finalize_ok, observed_started, waited) = std::thread::scope(|scope| {
            let finalizer = scope.spawn(|| storage.finalize_writes());

            let deadline = Instant::now() + Duration::from_secs(10);
            while !storage.maintenance_started_for_test() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_micros(200));
            }
            let observed_started = storage.maintenance_started_for_test();

            let close_result = storage.close();
            let waited = storage.close_waited_for_maintenance_for_test();
            let finalize_result = finalizer.join().expect("join finalizer");
            (
                close_result.is_ok(),
                finalize_result.is_ok(),
                observed_started,
                waited,
            )
        });

        let reopened = open(&path, true);
        let hits = reopened
            .search_vector("fixture/fixture-model", &probe, 5, None)
            .expect("search after close/reopen");
        println!(
            "GATE_K_CLOSE observed_started={observed_started} close_waited={waited} \
close_ok={close_ok} finalize_ok={finalize_ok} reopened_hits={}",
            hits.len()
        );
        assert!(
            observed_started,
            "test must observe a running optimize before close"
        );
        assert!(waited, "close must actually wait for the running optimize");
        assert!(close_ok, "close must succeed");
        assert!(finalize_ok, "finalize must complete after close waits");
        assert!(!hits.is_empty(), "reopened store still serves search");
        reopened.close().expect("close reopened");
    }

    fn env_usize(key: &str, fallback: usize) -> usize {
        std::env::var(key)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(fallback)
    }

    fn proc_kb(key: &str) -> Option<u64> {
        let text = std::fs::read_to_string("/proc/self/status").ok()?;
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix(key) {
                if let Some(rest) = rest.strip_prefix(':') {
                    return rest.trim().split_whitespace().next()?.parse().ok();
                }
            }
        }
        None
    }

    fn pss_kb() -> Option<u64> {
        let text = std::fs::read_to_string("/proc/self/smaps_rollup").ok()?;
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix("Pss:") {
                return rest.trim().split_whitespace().next()?.parse().ok();
            }
        }
        None
    }

    fn cpu_ticks() -> u64 {
        let stat = std::fs::read_to_string("/proc/self/stat").unwrap_or_default();
        let rest = stat.rsplit_once(')').map(|(_, rest)| rest).unwrap_or("");
        let fields: Vec<&str> = rest.split_whitespace().collect();
        let at = |i: usize| fields.get(i).and_then(|v| v.parse::<u64>().ok()).unwrap_or(0);
        at(10).saturating_add(at(11))
    }

    #[test]
    #[ignore = "Gate K store-level peak RSS/PSS/CPU + Arc retention; run under an external watchdog"]
    fn gate_k_memory_arc_retention() {
        let temporary = tempfile::tempdir().expect("workspace");
        let storage = open(temporary.path(), false);
        let model = "fixture/fixture-model";
        let probe = vec![1.0_f32, 0.0, 0.0];

        for index in 0..1000 {
            let (file, entry) = fixture(
                Some(&storage),
                &format!("mem0-{index}"),
                &format!("mem0 {index}"),
                probe.clone(),
            );
            storage
                .replace_fixture_file(&file, std::slice::from_ref(&entry))
                .expect("seed");
        }
        storage.finalize_writes().expect("first optimize");
        let cpu_before = cpu_ticks();
        let mut series = vec![(0usize, proc_kb("VmRSS").unwrap_or(0), pss_kb().unwrap_or(0))];

        for cycle in 1..=5usize {
            for index in 0..200 {
                let (file, entry) = fixture(
                    Some(&storage),
                    &format!("mem{cycle}-{index}"),
                    &format!("mem{cycle} {index}"),
                    probe.clone(),
                );
                storage
                    .replace_fixture_file(&file, std::slice::from_ref(&entry))
                    .expect("cycle write");
            }
            storage.finalize_writes().expect("cycle optimize");
            let _ = storage.search_vector(model, &probe, 5, None).expect("cycle search");
            series.push((
                cycle,
                proc_kb("VmRSS").unwrap_or(0),
                pss_kb().unwrap_or(0),
            ));
        }
        let cpu_after = cpu_ticks();
        let hwm = proc_kb("VmHWM").unwrap_or(0);
        println!(
            "GATE_K_MEM series(cycle,rss_kb,pss_kb)={series:?} vmhwm_kb={hwm} cpu_ticks_total={}",
            cpu_after.saturating_sub(cpu_before)
        );

        let first_rss = series.first().map(|s| s.1).unwrap_or(0);
        let last_rss = series.last().map(|s| s.1).unwrap_or(0);
        assert!(
            last_rss <= first_rss.saturating_mul(3).saturating_add(262144),
            "RSS retention grew abnormally: first={first_rss}KB last={last_rss}KB"
        );
        storage.close().expect("close store");
    }

    #[test]
    #[ignore = "Gate K store-level crash/reopen; env-driven with GATE_K_MODE/PATH"]
    fn gate_k_lifecycle() {
        let mode = std::env::var("GATE_K_MODE").unwrap_or_else(|_| "verify".into());
        let path = std::path::PathBuf::from(
            std::env::var("GATE_K_PATH").expect("GATE_K_PATH"),
        );
        let docs = env_usize("GATE_K_DOCS", 6000);
        let model = "fixture/fixture-model";
        let probe = vec![1.0_f32, 0.0, 0.0];

        if mode == "build" {
            let storage = open(&path, false);
            for index in 0..docs {
                let (file, entry) = fixture(
                    Some(&storage),
                    &format!("l-{index}"),
                    &format!("l {index}"),
                    probe.clone(),
                );
                storage
                    .replace_fixture_file(&file, std::slice::from_ref(&entry))
                    .expect("seed lifecycle");
            }
            let completeness = storage
                .fragment_completeness_summary_for_test()
                .unwrap_or(f32::NAN);
            storage.close().expect("close build");
            println!("GATE_K_LIFE mode=build docs={docs} completeness_before_optimize={completeness}");
            return;
        }

        let storage = open(&path, false);
        if mode == "split" {
            use std::io::Write as _;
            println!("GATE_K_LIFE mode=split optimizing");
            let _ = std::io::stdout().flush();
            storage.finalize_writes().expect("optimize (may be killed)");
            println!("GATE_K_LIFE mode=split (not killed)");
            return;
        }

        let before = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(f32::NAN);
        let (pending, _) = storage.finalize_debt_for_test().expect("debt");
        if before < 1.0 || pending {
            storage.finalize_writes().expect("retry optimize after kill");
        }
        let after = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(f32::NAN);
        let hits = storage
            .search_vector(model, &probe, 5, None)
            .expect("search after reopen");
        let files = storage.list_file_paths().expect("paths").len();
        println!(
            "GATE_K_LIFE mode=verify completeness_before={before} completeness_after={after} \
files={files} hits={}",
            hits.len()
        );
        assert!(after >= 1.0, "completeness must converge after reopen");
        assert!(files >= docs, "no data loss across crash/reopen");
        assert!(!hits.is_empty(), "reopened store serves search");
        storage.close().expect("close verify");
    }
}

// ---------------------------------------------------------------------------
// M3-B Step 2 — bounded deferred native maintenance.
// ---------------------------------------------------------------------------
#[cfg(test)]
mod step2 {
    use super::*;
    use std::time::Duration;

    fn insert(storage: &IndexStore, name: &str, vector: &[f32]) {
        let (file, entry) = fixture(
            Some(storage),
            name,
            &format!("{name} body"),
            vector.to_vec(),
        );
        storage
            .replace_fixture_file(&file, std::slice::from_ref(&entry))
            .expect("insert");
    }

    #[test]
    #[ignore = "Step 2: defer below threshold then run at threshold"]
    fn step2_defers_below_threshold() {
        let temporary = tempfile::tempdir().expect("workspace");
        let storage = open(temporary.path(), false);
        let model = "fixture/fixture-model";
        let probe = vec![1.0_f32, 0.0, 0.0];
        storage.set_maintenance_policy_for_test(50, 3_600_000);

        for index in 0..10 {
            insert(&storage, &format!("d-{index}"), &probe);
        }
        let base = optimize_calls(&storage).fragments;
        storage.finalize_writes().expect("deferred finalize");
        let after_defer = optimize_calls(&storage).fragments;
        let (pending, _) = storage.finalize_debt_for_test().expect("debt");
        let hits = storage
            .search_vector(model, &probe, 5, None)
            .expect("pre-optimize search");
        assert_eq!(after_defer, base, "below-threshold debt must defer optimize");
        assert!(pending, "debt must stay pending after defer");
        assert!(!hits.is_empty(), "staged data must be searchable before optimize");

        for index in 10..50 {
            insert(&storage, &format!("d-{index}"), &probe);
        }
        storage.finalize_writes().expect("threshold finalize");
        let after_run = optimize_calls(&storage).fragments;
        let (pending_after, _) = storage.finalize_debt_for_test().expect("debt2");
        let completeness = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(f32::NAN);
        println!(
            "STEP2_DEFER base={base} after_defer={after_defer} after_run={after_run} \
pending={pending} pending_after={pending_after} completeness={completeness}"
        );
        assert!(after_run > after_defer, "threshold must trigger optimize");
        assert!(!pending_after, "threshold run must clear debt");
        assert!(completeness >= 1.0, "completeness converges after run");
        storage.close().expect("close");
    }

    #[test]
    #[ignore = "Step 2: age deadline forces maintenance"]
    fn step2_deadline_forces() {
        let temporary = tempfile::tempdir().expect("workspace");
        let storage = open(temporary.path(), false);
        let probe = vec![1.0_f32, 0.0, 0.0];
        storage.set_maintenance_policy_for_test(1_000_000, 150);
        for index in 0..5 {
            insert(&storage, &format!("t-{index}"), &probe);
        }
        storage.finalize_writes().expect("deferred");
        let (pending, _) = storage.finalize_debt_for_test().expect("debt");
        std::thread::sleep(Duration::from_millis(300));
        storage.finalize_writes().expect("deadline finalize");
        let (pending_after, _) = storage.finalize_debt_for_test().expect("debt2");
        let completeness = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(f32::NAN);
        println!(
            "STEP2_DEADLINE pending={pending} pending_after={pending_after} completeness={completeness}"
        );
        assert!(pending, "must defer before deadline");
        assert!(!pending_after, "deadline must force maintenance");
        assert!(completeness >= 1.0);
        storage.close().expect("close");
    }

    #[test]
    #[ignore = "Step 2: deletion forces maintenance even at completeness 1.0"]
    fn step2_delete_forces() {
        let temporary = tempfile::tempdir().expect("workspace");
        let storage = open(temporary.path(), false);
        let model = "fixture/fixture-model";
        let keep = vec![1.0_f32, 0.0, 0.0];
        let gone = vec![0.0_f32, 1.0, 0.0];

        storage.set_maintenance_policy_for_test(0, 3_600_000);
        insert(&storage, "keep-0", &keep);
        let (gone_file, gone_entry) = fixture(Some(&storage), "gone-0", "gone body", gone.clone());
        let gone_id = gone_file.id;
        storage
            .replace_fixture_file(&gone_file, std::slice::from_ref(&gone_entry))
            .expect("insert gone");
        storage.finalize_writes().expect("eager finalize");
        let completeness_before = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(f32::NAN);

        // Now defer aggressively, then delete: deletion must still force.
        storage.set_maintenance_policy_for_test(1_000_000, 3_600_000);
        storage.delete_files(&[gone_id]).expect("delete");
        storage.finalize_writes().expect("delete finalize");
        let (pending, _) = storage.finalize_debt_for_test().expect("debt");
        let completeness_after = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(f32::NAN);
        let ghost = storage
            .search_vector(model, &gone, 5, None)
            .expect("search deleted")
            .iter()
            .any(|hit| hit.file_id == gone_id);
        println!(
            "STEP2_DELETE completeness_before={completeness_before} pending={pending} \
completeness_after={completeness_after} ghost={ghost}"
        );
        assert!(!pending, "deletion must force maintenance and clear debt");
        assert!(!ghost, "deleted fragment must not be returned");
        storage.close().expect("close");
    }

    #[test]
    #[ignore = "Step 2: reopen detects persisted deletion maintenance"]
    fn step2_reopen_detects_delete_debt() {
        let temporary = tempfile::tempdir().expect("workspace");
        let path = temporary.path().to_owned();
        let model = "fixture/fixture-model";
        let gone = vec![0.0_f32, 1.0, 0.0];
        let gone_id;
        {
            let storage = open(&path, false);
            storage.set_maintenance_policy_for_test(1_000_000, 3_600_000);
            insert(&storage, "keep-1", &[1.0, 0.0, 0.0]);
            let (file, entry) = fixture(Some(&storage), "gone-1", "gone body", gone.clone());
            gone_id = file.id;
            storage
                .replace_fixture_file(&file, std::slice::from_ref(&entry))
                .expect("insert gone");
            // Persist the delete marker without running maintenance.
            storage.delete_files(&[gone_id]).expect("delete");
            storage.checkpoint_only().expect("checkpoint marker");
            storage.close().expect("close before reopen");
        }
        let reopened = open(&path, false);
        reopened.set_maintenance_policy_for_test(1_000_000, 3_600_000);
        let (pending, _) = reopened.finalize_debt_for_test().expect("debt");
        reopened.finalize_writes().expect("reopen maintenance");
        let (pending_after, _) = reopened.finalize_debt_for_test().expect("debt2");
        let ghost = reopened
            .search_vector(model, &gone, 5, None)
            .expect("search deleted")
            .iter()
            .any(|hit| hit.file_id == gone_id);
        println!("STEP2_REOPEN pending={pending} pending_after={pending_after} ghost={ghost}");
        assert!(!pending_after, "reopen must complete pending delete maintenance");
        assert!(!ghost, "deleted fragment must not survive reopen maintenance");
        reopened.close().expect("close");
    }

    fn proc_kb(key: &str) -> u64 {
        let text = std::fs::read_to_string("/proc/self/status").unwrap_or_default();
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix(key) {
                if let Some(rest) = rest.strip_prefix(':') {
                    return rest
                        .trim()
                        .split_whitespace()
                        .next()
                        .and_then(|v| v.parse().ok())
                        .unwrap_or(0);
                }
            }
        }
        0
    }

    fn env_usize(key: &str, fallback: usize) -> usize {
        std::env::var(key)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(fallback)
    }

    #[test]
    #[ignore = "Step 2 A/B benchmark; run with ZVEC_GREP_MAINTENANCE_THRESHOLD=1 then 50"]
    fn step2_ab_benchmark() {
        let temporary = tempfile::tempdir().expect("workspace");
        let storage = open(temporary.path(), false);
        let model = "fixture/fixture-model";
        let probe = vec![1.0_f32, 0.0, 0.0];
        let batches = env_usize("STEP2_BATCHES", 100);
        let per_batch = env_usize("STEP2_PER", 5);
        let threshold = std::env::var("ZVEC_GREP_MAINTENANCE_THRESHOLD").unwrap_or_else(|_| "?".into());

        let began = std::time::Instant::now();
        let mut n = 0usize;
        for _batch in 0..batches {
            for _ in 0..per_batch {
                insert(&storage, &format!("ab-{n}"), &probe);
                n += 1;
            }
            storage.finalize_writes().expect("finalize batch");
        }
        let wall_ms = began.elapsed().as_millis();
        let optimizes = optimize_calls(&storage).fragments;
        let vmhwm_kb = proc_kb("VmHWM");

        let mut latencies = Vec::new();
        for _ in 0..200 {
            let t = std::time::Instant::now();
            let _ = storage
                .search_vector(model, &probe, 5, None)
                .expect("search with debt");
            latencies.push(t.elapsed().as_micros());
        }
        latencies.sort_unstable();
        let p95 = latencies[(latencies.len() - 1) * 95 / 100];
        let (pending, _) = storage.finalize_debt_for_test().expect("debt");
        let completeness = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(f32::NAN);
        println!(
            "STEP2_AB threshold={threshold} batches={batches} per={per_batch} mutations={n} \
optimizes={optimizes} wall_ms={wall_ms} vmhwm_kb={vmhwm_kb} search_p95_us={p95} \
pending={pending} completeness={completeness}"
        );
        storage.close().expect("close");
    }

    #[test]
    #[ignore = "Step 2: metadata-only updates do not accrue fragment debt"]
    fn step2_metadata_only_ignored() {
        let temporary = tempfile::tempdir().expect("workspace");
        let storage = open(temporary.path(), false);
        storage.set_maintenance_policy_for_test(1, 3_600_000);
        let (file, entry) = fixture(Some(&storage), "meta-0", "meta body", vec![1.0, 0.0, 0.0]);
        storage
            .replace_fixture_file(&file, std::slice::from_ref(&entry))
            .expect("insert");
        storage.finalize_writes().expect("eager finalize");
        let base = optimize_calls(&storage).fragments;

        for _ in 0..20 {
            storage.update_file_snapshot(&file).expect("metadata update");
        }
        let debt = storage.fragment_debt_for_test().expect("debt");
        storage.finalize_writes().expect("finalize after metadata");
        let after = optimize_calls(&storage).fragments;
        println!("STEP2_META base={base} after={after} fragment_debt={debt}");
        assert_eq!(debt, 0, "metadata-only updates must not accrue fragment debt");
        assert_eq!(
            after, base,
            "metadata-only updates must not trigger fragment maintenance"
        );
        storage.close().expect("close");
    }

    #[test]
    #[ignore = "Step 2: deletion persists the marker immediately"]
    fn step2_delete_marker_persisted_immediately() {
        let temporary = tempfile::tempdir().expect("workspace");
        let path = temporary.path().to_owned();
        let marker = path.join("storage").join("maintenance.delete");
        let storage = open(&path, false);
        storage.set_maintenance_policy_for_test(1_000_000, 3_600_000);
        let (file, entry) = fixture(Some(&storage), "dm-0", "dm body", vec![0.0, 1.0, 0.0]);
        let id = file.id;
        storage
            .replace_fixture_file(&file, std::slice::from_ref(&entry))
            .expect("insert");
        storage.finalize_writes().expect("defer finalize");
        let before = marker.exists();
        storage.delete_files(&[id]).expect("delete");
        let after_delete = marker.exists();
        println!("STEP2_MARKER before_delete={before} after_delete={after_delete}");

        // Eager policy completes maintenance and clears the marker.
        storage.set_maintenance_policy_for_test(0, 3_600_000);
        storage.finalize_writes().expect("maintenance");
        let after_ack = marker.exists();
        println!("STEP2_MARKER after_ack={after_ack}");
        assert!(after_delete, "delete must persist the marker immediately");
        assert!(!after_ack, "acknowledged maintenance must clear the marker");
        storage.close().expect("close");
    }

    #[test]
    #[ignore = "Step 2: marker publish failure fails the delete (fail-safe)"]
    fn step2_delete_marker_failure_is_failsafe() {
        use std::os::unix::fs::PermissionsExt;
        let temporary = tempfile::tempdir().expect("workspace");
        let path = temporary.path().to_owned();
        let storage_dir = path.join("storage");
        let marker = storage_dir.join("maintenance.delete");
        let storage = open(&path, false);
        storage.set_maintenance_policy_for_test(0, 3_600_000);
        let (file, entry) = fixture(Some(&storage), "fs-0", "fs body", vec![0.0, 1.0, 0.0]);
        let id = file.id;
        storage
            .replace_fixture_file(&file, std::slice::from_ref(&entry))
            .expect("insert");
        storage.finalize_writes().expect("eager finalize");

        let original = std::fs::metadata(&storage_dir)
            .expect("stat storage")
            .permissions();
        std::fs::set_permissions(&storage_dir, std::fs::Permissions::from_mode(0o555))
            .expect("readonly storage");
        let delete_error = storage.delete_files(&[id]);
        std::fs::set_permissions(&storage_dir, original).expect("restore storage");

        let marker_exists = marker.exists();
        let still_searchable = storage
            .search_vector("fixture/fixture-model", &[0.0, 1.0, 0.0], 5, None)
            .expect("search")
            .iter()
            .any(|hit| hit.file_id == id);
        println!(
            "STEP2_FAILSAFE delete_err={} marker_exists={marker_exists} still_searchable={still_searchable}",
            delete_error.is_err()
        );
        assert!(delete_error.is_err(), "marker publish failure must fail the delete");
        assert!(!marker_exists, "no marker must be left behind on failed publish");
        assert!(still_searchable, "delete must not have partially applied");
        storage.close().expect("close");
    }

    #[test]
    #[ignore = "Step 2: restart recovers insert-only pending debt via age"]
    fn step2_restart_recovers_insert_debt() {
        let temporary = tempfile::tempdir().expect("workspace");
        let path = temporary.path().to_owned();
        let probe = vec![1.0_f32, 0.0, 0.0];
        {
            let storage = open(&path, false);
            storage.set_maintenance_policy_for_test(50, 100_000);
            for index in 0..10 {
                insert(&storage, &format!("r-{index}"), &probe);
            }
            storage.finalize_writes().expect("defer");
            let (pending, _) = storage.finalize_debt_for_test().expect("debt");
            assert!(pending, "insert debt must be pending before close");
            storage.close().expect("close");
        }
        let storage = open(&path, false);
        storage.set_maintenance_policy_for_test(50, 1);
        std::thread::sleep(Duration::from_millis(20));
        storage.finalize_writes().expect("recover");
        let (pending, _) = storage.finalize_debt_for_test().expect("debt2");
        let completeness = storage
            .fragment_completeness_summary_for_test()
            .unwrap_or(f32::NAN);
        println!("STEP2_RESTART pending_after={pending} completeness={completeness}");
        assert!(!pending, "restart + age deadline must recover insert debt");
        assert!(completeness >= 1.0, "completeness converges after recovery");
        storage.close().expect("close");
    }
}

// ---------------------------------------------------------------------------
// Engineering-complexity audit proofs (read-only witnesses for F2/F6).
// ---------------------------------------------------------------------------
#[cfg(test)]
mod audit {
    use super::*;
    use std::time::{Duration, Instant};

    #[test]
    #[ignore = "F2 proof: metadata-only mutation during optimize blocks ack -> redundant fragment re-optimize"]
    fn f2_metadata_only_during_optimize_redundant_reoptimize() {
        let temporary = tempfile::tempdir().expect("workspace");
        let storage = open(temporary.path(), false);
        storage.set_maintenance_policy_for_test(0, 3_600_000);

        let (xfile, xentry) = fixture(Some(&storage), "x-0", "x body", vec![1.0, 0.0, 0.0]);
        storage
            .replace_fixture_file(&xfile, std::slice::from_ref(&xentry))
            .expect("insert x");
        storage.finalize_writes().expect("settle");
        let (x_entities, x_entries) = fixture_records(std::slice::from_ref(&xentry));

        // New fragment debt so the next finalize optimizes fragments.
        let (yfile, yentry) = fixture(Some(&storage), "y-0", "y body", vec![0.0, 1.0, 0.0]);
        storage
            .replace_fixture_file(&yfile, std::slice::from_ref(&yentry))
            .expect("insert y");

        std::thread::scope(|scope| {
            let finalizer = scope.spawn(|| storage.finalize_writes());
            let deadline = Instant::now() + Duration::from_secs(10);
            while !storage.maintenance_started_for_test() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_micros(100));
            }
            // Same-content replace: marks FILES (bumps mutation_epoch) but not
            // FRAGMENTS (no fragment debt change).
            storage
                .replace_file(&xfile, &x_entities, &x_entries)
                .expect("metadata replace");
            finalizer
                .join()
                .expect("join finalizer")
                .expect("finalize under concurrent metadata replace");
        });

        let f2 = optimize_calls(&storage).fragments;
        let (pending_after_2, _) = storage.finalize_debt_for_test().expect("debt");
        storage.finalize_writes().expect("third finalize");
        let f3 = optimize_calls(&storage).fragments;
        println!("AUDIT_F2 f2={f2} f3={f3} pending_after_2={pending_after_2}");
        // F2 hypothesis NOT confirmed: the concurrent replace genuinely leaves the
        // fragment index incomplete, so the extra optimize is required, not redundant.
        assert!(pending_after_2, "concurrent mutation must not be acknowledged");
        storage.close().expect("close");
    }

    #[test]
    #[ignore = "F6 witness: finalize Phase C can run after close set closed"]
    fn f6_phase_c_can_run_after_close() {
        let mut observed = false;
        for iteration in 0..8 {
            let temporary = tempfile::tempdir().expect("workspace");
            let storage = open(temporary.path(), false);
            storage.set_maintenance_policy_for_test(0, 3_600_000);
            for index in 0..5 {
                let (file, entry) = fixture(
                    Some(&storage),
                    &format!("f6-{iteration}-{index}"),
                    "f6 body",
                    vec![1.0, 0.0, 0.0],
                );
                storage
                    .replace_fixture_file(&file, std::slice::from_ref(&entry))
                    .expect("insert");
            }
            std::thread::scope(|scope| {
                let finalizer = scope.spawn(|| storage.finalize_writes());
                let deadline = Instant::now() + Duration::from_secs(10);
                while !storage.maintenance_started_for_test() && Instant::now() < deadline {
                    std::thread::sleep(Duration::from_micros(50));
                }
                let _ = storage.close();
                let _ = finalizer.join().expect("join finalizer");
            });
            if storage.last_finalize_saw_closed_for_test() {
                observed = true;
            }
        }
        println!("AUDIT_F6 saw_closed_in_phase_c={observed}");
    }

    #[test]
    #[ignore = "F6 recovery: delete debt survives a finalize/close race and is recovered on reopen"]
    fn f6_close_race_recovery() {
        let mut ghosts = 0usize;
        let mut lingering_markers = 0usize;
        let mut finalize_errors = 0usize;
        for iteration in 0..6 {
            let temporary = tempfile::tempdir().expect("workspace");
            let path = temporary.path().to_owned();
            let marker = path.join("storage").join("maintenance.delete");
            let gone = vec![0.0_f32, 1.0, 0.0];
            let gone_id = {
                let storage = open(&path, false);
                storage.set_maintenance_policy_for_test(0, 3_600_000);
                let (file, entry) =
                    fixture(Some(&storage), &format!("race-gone-{iteration}"), "race gone", gone.clone());
                let id = file.id;
                storage
                    .replace_fixture_file(&file, std::slice::from_ref(&entry))
                    .expect("insert");
                storage.finalize_writes().expect("settle");
                // Durable delete marker + staged tombstones.
                storage.delete_files(&[id]).expect("delete");

                // Race: finalize (optimize + maybe ack) against close.
                std::thread::scope(|scope| {
                    let finalizer = scope.spawn(|| storage.finalize_writes());
                    let deadline = Instant::now() + Duration::from_secs(10);
                    while !storage.maintenance_started_for_test() && Instant::now() < deadline {
                        std::thread::sleep(Duration::from_micros(50));
                    }
                    let _ = storage.close();
                    let _ = finalizer.join().expect("join finalizer");
                });
                id
            };

            // Reopen: any surviving delete debt must be recovered.
            let reopened = open(&path, false);
            reopened.set_maintenance_policy_for_test(0, 3_600_000);
            if reopened.finalize_writes().is_err() {
                finalize_errors += 1;
            }
            let ghost = reopened
                .search_vector("fixture/fixture-model", &gone, 5, None)
                .expect("search deleted")
                .iter()
                .any(|hit| hit.file_id == gone_id);
            if ghost {
                ghosts += 1;
            }
            if marker.exists() {
                lingering_markers += 1;
            }
            reopened.close().expect("close reopened");
        }
        println!(
            "AUDIT_F6_RECOVERY ghosts={ghosts} lingering_markers={lingering_markers} \
finalize_errors={finalize_errors}"
        );
        assert_eq!(ghosts, 0, "no ghost after close/finalize race + reopen");
        assert_eq!(lingering_markers, 0, "marker cleared after recovered maintenance");
        assert_eq!(finalize_errors, 0, "recovery finalize must succeed");
    }
}
