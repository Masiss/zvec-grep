//! Paired native benchmark for the current single-index write path versus a
//! searchable base + delta path. This is intentionally ignored: it is a
//! measurement artifact, not a production integration.
//!
//! Run with host snapshots and one bounded workload, for example:
//!
//! ```text
//! cargo test -p zg-engine --test native_paired_benchmark -- --ignored --nocapture
//! ```
//!
//! `BENCH_BASE` and `BENCH_UPDATES` control the fixture size.

#![allow(
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss,
    clippy::ignored_unit_patterns,
    clippy::needless_pass_by_value,
    clippy::similar_names,
    clippy::too_many_lines
)]

use std::{
    collections::BTreeMap,
    env, fs,
    path::Path,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

use tempfile::TempDir;
use zvec_rust::{
    Collection, CollectionSchema, ConfigBuilder, DataType, Doc, Fts, IndexParams, MetricType,
    SearchQuery, initialize, shutdown,
};

const DIMENSION: u32 = 8;

/// Fixture vector dimension. Defaults to the original small fixture; raise it
/// (e.g. `BENCH_DIM=256`) to make the native HNSW merge cost representative.
fn dimension() -> u32 {
    env_usize("BENCH_DIM", DIMENSION as usize) as u32
}

#[derive(Clone)]
struct Update {
    logical_id: String,
    generation: u64,
    text: String,
    vector: Vec<f32>,
}

#[derive(Clone, Copy, Default)]
struct ProcStats {
    rss_kb: u64,
    pss_kb: u64,
    cpu_ticks: u64,
    write_bytes: u64,
}

struct ActionMetrics {
    elapsed: Duration,
    cpu_ticks: u64,
    write_bytes: u64,
    peak_rss_kb: u64,
    peak_pss_kb: u64,
}

struct BenchmarkSummary {
    updates: Vec<ActionMetrics>,
    searches: Vec<ActionMetrics>,
    compaction: Option<ActionMetrics>,
    verified: usize,
    storage_bytes_live: u64,
    storage_bytes_after_compaction: u64,
}

fn env_usize(name: &str, default: usize) -> usize {
    env::var(name)
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(default)
}

fn read_kb(path: &Path, key: &str) -> u64 {
    fs::read_to_string(path)
        .ok()
        .and_then(|text| {
            text.lines()
                .find(|line| line.starts_with(key))
                .and_then(|line| line.split_whitespace().nth(1))
                .and_then(|value| value.parse().ok())
        })
        .unwrap_or(0)
}

fn proc_stats() -> ProcStats {
    let status = Path::new("/proc/self/status");
    let smaps = Path::new("/proc/self/smaps_rollup");
    let io = Path::new("/proc/self/io");
    let stat = fs::read_to_string("/proc/self/stat").unwrap_or_default();
    let fields = stat
        .rfind(')')
        .map(|index| stat[index + 2..].split_whitespace().collect::<Vec<_>>())
        .unwrap_or_default();
    ProcStats {
        rss_kb: read_kb(status, "VmRSS:"),
        pss_kb: read_kb(smaps, "Pss:"),
        cpu_ticks: fields
            .get(11)
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(0)
            + fields
                .get(12)
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(0),
        write_bytes: read_kb(io, "write_bytes:"),
    }
}

fn measure<F, R>(action: F) -> (R, ActionMetrics)
where
    F: FnOnce() -> R,
{
    let before = proc_stats();
    let peak = Arc::new(Mutex::new(before));
    let stopped = Arc::new(AtomicBool::new(false));
    let sampler_peak = Arc::clone(&peak);
    let sampler_stopped = Arc::clone(&stopped);
    let sampler = thread::spawn(move || {
        while !sampler_stopped.load(Ordering::Relaxed) {
            let current = proc_stats();
            let mut peak = sampler_peak.lock().expect("peak lock");
            peak.rss_kb = peak.rss_kb.max(current.rss_kb);
            peak.pss_kb = peak.pss_kb.max(current.pss_kb);
            drop(peak);
            thread::sleep(Duration::from_millis(2));
        }
    });
    let started = Instant::now();
    let value = action();
    let elapsed = started.elapsed();
    stopped.store(true, Ordering::Relaxed);
    sampler.join().expect("resource sampler");
    let after = proc_stats();
    let peak = peak.lock().expect("peak lock after action");
    (
        value,
        ActionMetrics {
            elapsed,
            cpu_ticks: after.cpu_ticks.saturating_sub(before.cpu_ticks),
            write_bytes: after.write_bytes.saturating_sub(before.write_bytes),
            peak_rss_kb: peak.rss_kb.max(after.rss_kb),
            peak_pss_kb: peak.pss_kb.max(after.pss_kb),
        },
    )
}

fn schema() -> CollectionSchema {
    let mut schema = CollectionSchema::builder("paired_benchmark");
    schema = schema.add_field(
        zvec_rust::FieldSchema::new("logical_id", DataType::String, false, 0)
            .expect("logical id schema"),
    );
    schema = schema.add_field(
        zvec_rust::FieldSchema::new("generation", DataType::Uint64, false, 0)
            .expect("generation schema"),
    );
    schema = schema.add_field(
        zvec_rust::FieldSchema::new("deleted", DataType::Bool, false, 0).expect("deleted schema"),
    );
    let mut text =
        zvec_rust::FieldSchema::new("text", DataType::String, false, 0).expect("text schema");
    text.set_index_params(
        &IndexParams::fts(Some("jieba"), Some(&["lowercase"]), None).expect("fts params"),
    )
    .expect("text index");
    schema = schema.add_field(text);
    schema
        .add_vector_field(
            "embedding",
            DataType::VectorFp32,
            dimension(),
            IndexParams::hnsw(MetricType::L2, 16, 100).expect("hnsw params"),
        )
        .build()
        .expect("paired benchmark schema")
}

fn vector_for(index: usize, generation: u64) -> Vec<f32> {
    (0..dimension() as usize)
        .map(|axis| (((index * 31 + axis * 17 + generation as usize * 101) % 1009) as f32) / 1009.0)
        .collect()
}

fn make_doc(
    logical_id: &str,
    generation: u64,
    text: &str,
    vector: &[f32],
    primary_key: &str,
) -> Doc {
    let mut doc = Doc::new().expect("document");
    doc.set_pk(primary_key);
    doc.add_string("logical_id", logical_id)
        .expect("logical id");
    doc.add_u64("generation", generation).expect("generation");
    doc.add_bool("deleted", false).expect("deleted");
    doc.add_string("text", text).expect("text");
    doc.add_vector_f32("embedding", vector).expect("embedding");
    doc
}

fn insert_docs(collection: &Collection, docs: &[Doc]) {
    for chunk in docs.chunks(1024) {
        let refs = chunk.iter().collect::<Vec<_>>();
        let result = collection.insert(&refs).expect("insert docs");
        assert_eq!(result.error_count, 0, "insert errors: {:?}", result.results);
    }
}

fn base_rows(count: usize) -> Vec<(String, u64, String, Vec<f32>)> {
    let repeat = env::var("BENCH_TEXT_REPEAT")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(1);
    (0..count)
        .map(|index| {
            let logical_id = format!("doc-{index:06}");
            let mut text = String::new();
            for _ in 0..repeat {
                text.push_str(&format!("base document {index} common corpus "));
            }
            (logical_id, 0, text, vector_for(index, 0))
        })
        .collect()
}

fn workload(base: usize, updates: usize) -> Vec<Update> {
    (0..updates)
        .map(|index| {
            let target = index % base;
            Update {
                logical_id: format!("doc-{target:06}"),
                generation: index as u64 + 1,
                text: format!("update {index} needle-{index} corpus"),
                vector: vector_for(target, index as u64 + 1),
            }
        })
        .collect()
}

fn create_collection(path: &Path) -> Collection {
    fs::create_dir_all(path.parent().expect("collection parent"))
        .expect("create collection parent");
    let schema = schema();
    Collection::create_and_open(path.to_str().expect("utf-8 native path"), &schema, None)
        .expect("create collection")
}

fn populate_base(collection: &Collection, rows: &[(String, u64, String, Vec<f32>)]) {
    let docs: Vec<Doc> = rows
        .iter()
        .map(|(id, generation, text, vector)| make_doc(id, *generation, text, vector, id))
        .collect();
    insert_docs(collection, &docs);
}

fn query_rows(collection: &Collection, vector: &[f32], text: &str) -> Vec<(String, u64)> {
    let mut vector_query = SearchQuery::new("embedding", vector, 32).expect("vector query");
    vector_query
        .set_output_fields(&["logical_id", "generation", "deleted"])
        .expect("vector output");
    let mut rows = collection
        .query(&vector_query)
        .expect("vector search")
        .into_iter()
        .map(|doc| {
            (
                doc.get_string("logical_id")
                    .expect("logical id field")
                    .expect("logical id"),
                doc.get_u64("generation")
                    .expect("generation field")
                    .expect("generation"),
            )
        })
        .collect::<Vec<_>>();

    let mut fts = Fts::new().expect("fts payload");
    fts.set_match_string(text).expect("fts text");
    let mut text_query = SearchQuery::fts("text", &fts, 32).expect("fts query");
    text_query
        .set_output_fields(&["logical_id", "generation", "deleted"])
        .expect("fts output");
    rows.extend(
        collection
            .query(&text_query)
            .expect("fts search")
            .into_iter()
            .map(|doc| {
                (
                    doc.get_string("logical_id")
                        .expect("logical id field")
                        .expect("logical id"),
                    doc.get_u64("generation")
                        .expect("generation field")
                        .expect("generation"),
                )
            }),
    );
    rows
}

fn assert_complete(collection: &Collection, label: &str) {
    let stats = collection.stats().expect("collection stats");
    assert!(
        !stats.indexes.is_empty() && stats.indexes.iter().all(|index| index.completeness >= 1.0),
        "{label} has incomplete indexes: {stats:?}"
    );
}

fn verify_update(collections: &[&Collection], update: &Update) -> bool {
    collections.iter().any(|collection| {
        query_rows(collection, &update.vector, &update.text)
            .into_iter()
            .any(|(logical_id, generation)| {
                logical_id == update.logical_id && generation == update.generation
            })
    })
}

fn verify_base_delta(base: &Collection, delta: &Collection, update: &Update) -> bool {
    let mut latest = BTreeMap::<String, u64>::new();
    for collection in [base, delta] {
        for (logical_id, generation) in query_rows(collection, &update.vector, &update.text) {
            latest
                .entry(logical_id)
                .and_modify(|current| *current = (*current).max(generation))
                .or_insert(generation);
        }
    }
    latest.get(&update.logical_id).copied() == Some(update.generation)
        && verify_update(&[base, delta], update)
}

fn assert_stale_updates_hidden(collection: &Collection, updates: &[Update]) {
    for (index, older) in updates.iter().enumerate() {
        let replaced = updates
            .iter()
            .skip(index + 1)
            .any(|newer| newer.logical_id == older.logical_id);
        if !replaced {
            continue;
        }
        let stale_visible = query_rows(collection, &older.vector, &older.text)
            .into_iter()
            .any(|(logical_id, generation)| {
                logical_id == older.logical_id && generation == older.generation
            });
        assert!(
            !stale_visible,
            "stale generation {} remained visible for {}",
            older.generation, older.logical_id
        );
    }
}

fn tree_bytes(path: &Path) -> u64 {
    let Ok(metadata) = fs::metadata(path) else {
        return 0;
    };
    if metadata.is_file() {
        return metadata.len();
    }
    fs::read_dir(path)
        .ok()
        .into_iter()
        .flatten()
        .map(|entry| tree_bytes(&entry.expect("benchmark directory entry").path()))
        .sum()
}

fn percentile_ms(metrics: &[ActionMetrics], percentile: f64) -> f64 {
    if metrics.is_empty() {
        return 0.0;
    }
    let mut values = metrics
        .iter()
        .map(|metric| metric.elapsed.as_secs_f64() * 1_000.0)
        .collect::<Vec<_>>();
    values.sort_by(f64::total_cmp);
    let index = ((values.len() - 1) as f64 * percentile).round() as usize;
    values[index]
}

fn cpu_seconds(metrics: &[ActionMetrics]) -> f64 {
    let ticks = metrics.iter().map(|metric| metric.cpu_ticks).sum::<u64>();
    let hz = 100.0;
    ticks as f64 / hz
}

fn print_summary(label: &str, summary: &BenchmarkSummary) {
    let writes = summary
        .updates
        .iter()
        .map(|metric| metric.write_bytes)
        .sum::<u64>();
    let peak_rss = summary
        .updates
        .iter()
        .chain(summary.searches.iter())
        .map(|metric| metric.peak_rss_kb)
        .max()
        .unwrap_or(0);
    let peak_pss = summary
        .updates
        .iter()
        .chain(summary.searches.iter())
        .map(|metric| metric.peak_pss_kb)
        .max()
        .unwrap_or(0);
    let update_cpu_seconds = cpu_seconds(&summary.updates);
    let total_cpu_seconds = update_cpu_seconds
        + summary
            .compaction
            .as_ref()
            .map_or(0.0, |metric| cpu_seconds(std::slice::from_ref(metric)));
    println!(
        "paired_benchmark mode={label} updates={} verified={} update_p50_ms={:.3} update_p95_ms={:.3} search_p50_ms={:.3} search_p95_ms={:.3} update_cpu_seconds={:.3} total_cpu_seconds={:.3} peak_rss_kb={} peak_pss_kb={} proc_write_bytes={} storage_bytes_live={} storage_bytes_after_compaction={} compaction_ms={}",
        summary.updates.len(),
        summary.verified,
        percentile_ms(&summary.updates, 0.50),
        percentile_ms(&summary.updates, 0.95),
        percentile_ms(&summary.searches, 0.50),
        percentile_ms(&summary.searches, 0.95),
        update_cpu_seconds,
        total_cpu_seconds,
        peak_rss,
        peak_pss,
        writes,
        summary.storage_bytes_live,
        summary.storage_bytes_after_compaction,
        summary.compaction.as_ref().map_or_else(
            || "NA".into(),
            |metric| metric.elapsed.as_millis().to_string(),
        )
    );
}

fn run_single(
    root: &Path,
    rows: &[(String, u64, String, Vec<f32>)],
    updates: &[Update],
) -> BenchmarkSummary {
    let collection = create_collection(&root.join("single"));
    populate_base(&collection, rows);
    collection.flush().expect("flush single base");
    collection.optimize().expect("optimize single base");
    assert_complete(&collection, "single base");

    let mut update_metrics = Vec::new();
    let mut search_metrics = Vec::new();
    let mut verified = 0;
    for update in updates {
        let (_, metrics) = measure(|| {
            let doc = make_doc(
                &update.logical_id,
                update.generation,
                &update.text,
                &update.vector,
                &update.logical_id,
            );
            let result = collection.upsert(&[&doc]).expect("single upsert");
            assert_eq!(result.error_count, 0);
            collection.flush().expect("single flush");
            collection.optimize().expect("single optimize");
        });
        assert_complete(&collection, "single update");
        update_metrics.push(metrics);
        let (fresh, search) = measure(|| verify_update(&[&collection], update));
        assert!(
            fresh,
            "single-index result is not fresh for {}",
            update.logical_id
        );
        verified += usize::from(fresh);
        search_metrics.push(search);
    }
    assert_stale_updates_hidden(&collection, updates);
    BenchmarkSummary {
        updates: update_metrics,
        searches: search_metrics,
        compaction: None,
        verified,
        storage_bytes_live: tree_bytes(root),
        storage_bytes_after_compaction: tree_bytes(root),
    }
}

fn run_base_delta(
    root: &Path,
    rows: &[(String, u64, String, Vec<f32>)],
    updates: &[Update],
) -> BenchmarkSummary {
    let base = create_collection(&root.join("base"));
    populate_base(&base, rows);
    base.flush().expect("flush delta base");
    base.optimize().expect("optimize delta base");
    let delta = create_collection(&root.join("delta"));

    let mut update_metrics = Vec::new();
    let mut search_metrics = Vec::new();
    let mut verified = 0;
    for update in updates {
        let (_, metrics) = measure(|| {
            let doc = make_doc(
                &update.logical_id,
                update.generation,
                &update.text,
                &update.vector,
                &format!("{}@{}", update.logical_id, update.generation),
            );
            let result = delta.insert(&[&doc]).expect("delta insert");
            assert_eq!(result.error_count, 0);
            delta.flush().expect("delta flush");
        });
        update_metrics.push(metrics);
        let (fresh, search) = measure(|| verify_base_delta(&base, &delta, update));
        assert!(
            fresh,
            "base+delta result is not fresh for {}",
            update.logical_id
        );
        verified += usize::from(fresh);
        search_metrics.push(search);
    }

    let latest = updates.iter().fold(
        rows.iter()
            .map(|(id, generation, text, vector)| {
                (id.clone(), (*generation, text.clone(), vector.clone()))
            })
            .collect::<BTreeMap<_, _>>(),
        |mut latest, update| {
            latest.insert(
                update.logical_id.clone(),
                (
                    update.generation,
                    update.text.clone(),
                    update.vector.clone(),
                ),
            );
            latest
        },
    );
    let storage_bytes_live = tree_bytes(root);
    let compacted_path = root.join("compacted");
    let (_, compaction) = measure(|| {
        let compacted = create_collection(&compacted_path);
        let docs: Vec<Doc> = latest
            .iter()
            .map(|(id, (generation, text, vector))| make_doc(id, *generation, text, vector, id))
            .collect();
        insert_docs(&compacted, &docs);
        compacted.flush().expect("flush compacted base");
        compacted.optimize().expect("optimize compacted base");
        let (fresh, _) = measure(|| verify_update(&[&compacted], updates.last().expect("update")));
        assert!(fresh, "compacted result is not fresh");
        drop(compacted);
    });
    drop(delta);
    drop(base);
    fs::remove_dir_all(root.join("base")).expect("remove compacted base input");
    fs::remove_dir_all(root.join("delta")).expect("remove compacted delta input");

    BenchmarkSummary {
        updates: update_metrics,
        searches: search_metrics,
        compaction: Some(compaction),
        verified,
        storage_bytes_live,
        storage_bytes_after_compaction: tree_bytes(root),
    }
}

#[test]
#[ignore = "manual Gate D3 benchmark; run with host snapshots and --ignored --nocapture"]
fn native_paired_benchmark() {
    let base_count = env_usize("BENCH_BASE", 2_000);
    let update_count = env_usize("BENCH_UPDATES", 40);
    let rows = base_rows(base_count);
    let updates = workload(base_count, update_count);
    let temporary = TempDir::new().expect("benchmark temp directory");
    let mut config = ConfigBuilder::new()
        .num_threads(env_usize("ZVEC_GREP_NATIVE_THREADS", 2) as u32)
        .enable_console_log(env::var("BENCH_LOG").is_ok());
    if let Ok(dict_dir) = env::var("BENCH_JIEBA_DIR") {
        config = config.jieba_dict_dir(dict_dir);
    }
    let config = config.build();
    initialize(Some(&config)).expect("initialize native zvec");

    let single_root = temporary.path().join("single-run");
    let single = run_single(&single_root, &rows, &updates);
    let reopened_single = Collection::open(
        single_root
            .join("single")
            .to_str()
            .expect("utf-8 reopened single path"),
        None,
    )
    .expect("reopen single benchmark collection");
    assert_complete(&reopened_single, "reopened single");
    assert!(verify_update(
        &[&reopened_single],
        updates.last().expect("benchmark update")
    ));
    assert_stale_updates_hidden(&reopened_single, &updates);
    drop(reopened_single);
    print_summary("single", &single);
    shutdown().expect("shutdown single benchmark");

    if env::var("BENCH_SINGLE_ONLY").is_ok() {
        assert_eq!(single.verified, update_count);
        return;
    }

    initialize(Some(&config)).expect("reinitialize native zvec");
    let base_delta = run_base_delta(&temporary.path().join("base-delta-run"), &rows, &updates);
    print_summary("base_delta", &base_delta);
    shutdown().expect("shutdown base+delta benchmark");

    assert_eq!(single.verified, update_count);
    assert_eq!(base_delta.verified, update_count);
    let single_p95 = percentile_ms(&single.updates, 0.95);
    let delta_p95 = percentile_ms(&base_delta.updates, 0.95);
    println!(
        "paired_benchmark comparison update_p95_ratio={:.3} delta_update_cpu_seconds={:.3} single_update_cpu_seconds={:.3} delta_total_cpu_seconds={:.3} single_total_cpu_seconds={:.3}",
        delta_p95 / single_p95.max(0.001),
        cpu_seconds(&base_delta.updates),
        cpu_seconds(&single.updates),
        cpu_seconds(&base_delta.updates)
            + base_delta
                .compaction
                .as_ref()
                .map_or(0.0, |metric| cpu_seconds(std::slice::from_ref(metric))),
        cpu_seconds(&single.updates),
    );
}
