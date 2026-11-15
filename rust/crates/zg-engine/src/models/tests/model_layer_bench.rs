use std::{
    env,
    hint::black_box,
    path::PathBuf,
    process::Command,
    time::{Duration, Instant},
};

use futures_util::future::join_all;
use serde_json::json;

use super::super::{
    ModelRuntimeLease, ModelRuntimeManager, ModelRuntimeRequest,
    spi::{EmbeddingOptions, EmbeddingPrepareOptions},
};
use crate::domain::{
    Content,
    model::{Device, EmbeddingPurpose, ModelConfig},
};

const DEFAULT_MODEL_REFERENCE: &str = "local/potion-code-16m-v2";

/// Manual, release-mode throughput and RSS harness used by
/// `scripts/validate-onnxruntime-models.py`.
#[tokio::test(flavor = "multi_thread")]
#[ignore = "manual model-layer throughput and RSS benchmark"]
async fn model_layer_throughput() {
    if env_flag("ZG_MODEL_BENCH_BASELINE") {
        std::thread::sleep(std::time::Duration::from_millis(250));
        println!(
            "ZG_MODEL_BENCH_JSON={}",
            json!({ "implementation": "rust", "mode": "baseline" })
        );
        return;
    }

    let batch_size = env_usize("ZG_MODEL_BENCH_BATCH", 256);
    let concurrency = env_usize("ZG_MODEL_BENCH_CONCURRENCY", 1);
    let vectors_per_round = env_usize("ZG_MODEL_BENCH_VECTORS", 16_384);
    let rounds = env_usize("ZG_MODEL_BENCH_ROUNDS", 5);
    let warmup_waves = env_usize("ZG_MODEL_BENCH_WARMUP_WAVES", 2);
    let model_reference =
        env::var("ZG_MODEL_BENCH_MODEL").unwrap_or_else(|_| DEFAULT_MODEL_REFERENCE.to_owned());
    let device = env::var("ZG_MODEL_BENCH_DEVICE")
        .ok()
        .map(|value| parse_device(&value));
    assert!(batch_size > 0, "batch size must be positive");
    assert!(concurrency > 0, "concurrency must be positive");
    assert!(rounds > 0, "round count must be positive");
    let vectors_per_wave = batch_size
        .checked_mul(concurrency)
        .expect("vectors per wave should fit usize");
    assert_eq!(
        vectors_per_round % vectors_per_wave,
        0,
        "vectors per round must be divisible by batch * concurrency"
    );

    let cache = env::var_os("ZG_MODEL_BENCH_CACHE").map(PathBuf::from);
    let manager = ModelRuntimeManager::new();
    let model = create_benchmark_model(&manager, &model_reference, cache, device, concurrency);
    assert!(
        batch_size <= model.info().model().max_batch_size(),
        "batch size exceeds model limit"
    );
    let batch = benchmark_batch(batch_size);

    for _ in 0..warmup_waves {
        black_box(run_wave(&model, &batch, concurrency).await);
    }
    std::thread::sleep(Duration::from_millis(100));
    let loaded_rss_bytes = current_rss_bytes();

    let waves_per_round = vectors_per_round / vectors_per_wave;
    let mut elapsed_seconds = Vec::with_capacity(rounds);
    let mut checksum = 0.0_f64;
    for _ in 0..rounds {
        let started = Instant::now();
        for _ in 0..waves_per_round {
            checksum += run_wave(&model, &batch, concurrency).await;
        }
        elapsed_seconds.push(started.elapsed().as_secs_f64());
    }
    black_box(checksum);
    drop(model);
    manager.close();

    let vectors_per_round_f64 = count_as_f64(vectors_per_round, "vectors per round");
    let vectors_per_second = elapsed_seconds
        .iter()
        .map(|seconds| vectors_per_round_f64 / seconds)
        .collect::<Vec<_>>();
    let requests_per_round = waves_per_round * concurrency;
    let requests_per_round_f64 = count_as_f64(requests_per_round, "requests per round");
    let requests_per_second = elapsed_seconds
        .iter()
        .map(|seconds| requests_per_round_f64 / seconds)
        .collect::<Vec<_>>();
    println!(
        "ZG_MODEL_BENCH_JSON={}",
        json!({
            "implementation": "rust",
            "mode": "model",
            "model": model_reference,
            "device": device.map(device_name),
            "batch_size": batch_size,
            "concurrency": concurrency,
            "vectors_per_round": vectors_per_round,
            "rounds": rounds,
            "warmup_waves": warmup_waves,
            "loaded_rss_bytes": loaded_rss_bytes,
            "elapsed_seconds": elapsed_seconds,
            "vectors_per_second": vectors_per_second,
            "requests_per_second": requests_per_second,
            "checksum": checksum,
        })
    );
}

fn create_benchmark_model(
    manager: &ModelRuntimeManager,
    model_reference: &str,
    cache: Option<PathBuf>,
    device: Option<Device>,
    concurrency: usize,
) -> ModelRuntimeLease {
    manager
        .acquire(ModelRuntimeRequest::new(
            model_reference,
            ModelConfig {
                api_key: env::var("DASHSCOPE_API_KEY").ok(),
                cache_dir: cache,
                device,
                ..ModelConfig::default()
            },
            Some(concurrency),
        ))
        .expect("benchmark model runtime should be acquired")
}

fn parse_device(value: &str) -> Device {
    match value {
        "auto" => Device::Auto,
        "cpu" => Device::Cpu,
        "metal" => Device::Metal,
        "vulkan" => Device::Vulkan,
        "cuda" => Device::Cuda,
        _ => panic!("invalid ZG_MODEL_BENCH_DEVICE={value:?}"),
    }
}

const fn device_name(device: Device) -> &'static str {
    match device {
        Device::Auto => "auto",
        Device::Cpu => "cpu",
        Device::Metal => "metal",
        Device::Vulkan => "vulkan",
        Device::Cuda => "cuda",
    }
}

async fn run_wave(model: &ModelRuntimeLease, batch: &[Vec<Content>], concurrency: usize) -> f64 {
    let results =
        join_all((0..concurrency).map(|_| model.embed(batch, EmbeddingOptions::default(), None)))
            .await;
    results
        .into_iter()
        .map(|result| {
            result
                .expect("benchmark embedding should succeed")
                .vectors
                .iter()
                .map(|vector| {
                    f64::from(vector.first().copied().unwrap_or_default())
                        + f64::from(vector.last().copied().unwrap_or_default())
                })
                .sum::<f64>()
        })
        .sum()
}

fn benchmark_batch(batch_size: usize) -> Vec<Vec<Content>> {
    (0..batch_size)
        .map(|index| {
            vec![Content::Text(format!(
                "pub fn benchmark_{index}(value: usize) -> usize {{ let adjusted = value.wrapping_mul(31).wrapping_add({index}); adjusted ^ 0x5a5a }}"
            ))]
        })
        .collect()
}

fn env_usize(name: &str, default: usize) -> usize {
    env::var(name).map_or(default, |value| {
        value
            .parse::<usize>()
            .unwrap_or_else(|error| panic!("invalid {name}={value:?}: {error}"))
    })
}

fn env_flag(name: &str) -> bool {
    env::var_os(name).is_some_and(|value| value != "0" && value != "false")
}

fn count_as_f64(value: usize, label: &str) -> f64 {
    f64::from(u32::try_from(value).unwrap_or_else(|error| panic!("{label} exceeds u32: {error}")))
}

fn current_rss_bytes() -> u64 {
    let output = Command::new("ps")
        .args(["-o", "rss=", "-p", &std::process::id().to_string()])
        .output()
        .expect("ps should report benchmark RSS");
    assert!(output.status.success(), "ps should exit successfully");
    let kibibytes = String::from_utf8(output.stdout)
        .expect("ps RSS should be UTF-8")
        .trim()
        .parse::<u64>()
        .expect("ps RSS should be an integer");
    kibibytes
        .checked_mul(1_024)
        .expect("benchmark RSS should fit u64")
}

// ---------------------------------------------------------------------------
// E4 experiment: fixed representative workload and semantic sanity.
// These are manual, ignored probes; they are not production evidence.
// ---------------------------------------------------------------------------

#[derive(serde::Deserialize)]
struct WorkloadItem {
    text: String,
    purpose: String,
}

#[derive(Default)]
struct TimingBucket {
    latencies: Vec<f64>,
    chars: usize,
    vectors: usize,
}

impl TimingBucket {
    fn record(&mut self, seconds: f64, batch: &[Vec<Content>]) {
        self.latencies.push(seconds);
        self.chars += batch
            .iter()
            .flat_map(|input| input.iter())
            .map(|content| match content {
                Content::Text(text) => text.chars().count(),
                _ => 0,
            })
            .sum::<usize>();
        self.vectors += batch.len();
    }

    fn value(&self) -> serde_json::Value {
        let total = self.latencies.iter().sum::<f64>();
        let mut sorted = self.latencies.clone();
        sorted.sort_by(f64::total_cmp);
        json!({
            "calls": self.latencies.len(),
            "vectors": self.vectors,
            "chars": self.chars,
            "total_seconds": total,
            "vectors_per_second": if total > 0.0 { self.vectors as f64 / total } else { 0.0 },
            "chars_per_second": if total > 0.0 { self.chars as f64 / total } else { 0.0 },
            "call_latency_ms_p50": percentile(&sorted, 0.50) * 1000.0,
            "call_latency_ms_p95": percentile(&sorted, 0.95) * 1000.0,
            "call_latency_ms_max": sorted.last().copied().unwrap_or_default() * 1000.0,
        })
    }
}

fn percentile(sorted: &[f64], fraction: f64) -> f64 {
    if sorted.is_empty() {
        return 0.0;
    }
    let index = ((sorted.len() - 1) as f64 * fraction).round() as usize;
    sorted[index]
}

fn process_metric(name: &str) -> Option<u64> {
    let status = std::fs::read_to_string("/proc/self/status").ok()?;
    let line = status.lines().find(|line| line.starts_with(name))?;
    line.split_whitespace()
        .nth(1)?
        .parse::<u64>()
        .ok()
        .map(|kib| kib * 1024)
}

fn process_pss_bytes() -> Option<u64> {
    let rollup = std::fs::read_to_string("/proc/self/smaps_rollup").ok()?;
    let line = rollup.lines().find(|line| line.starts_with("Pss:"))?;
    line.split_whitespace()
        .nth(1)?
        .parse::<u64>()
        .ok()
        .map(|kib| kib * 1024)
}

/// Fixed real-workload embedding throughput at the model layer.
///
/// Enable with `--ignored` and `ZG_MODEL_BENCH_WORKLOAD` pointing at a JSONL
/// file of `{"text": ..., "purpose": "query"|"document"}` rows. Batches are
/// homogeneous by purpose because the backend takes one purpose per call.
#[tokio::test(flavor = "multi_thread")]
#[ignore = "manual E4 fixed-workload model-layer benchmark"]
async fn model_layer_fixed_workload() {
    let workload_path = env::var("ZG_MODEL_BENCH_WORKLOAD")
        .expect("ZG_MODEL_BENCH_WORKLOAD must point at the fixed workload JSONL");
    let model_reference =
        env::var("ZG_MODEL_BENCH_MODEL").expect("ZG_MODEL_BENCH_MODEL must be set");
    let cache = env::var_os("ZG_MODEL_BENCH_CACHE").map(PathBuf::from);
    let rounds = env_usize("ZG_MODEL_BENCH_ROUNDS", 3);
    let batch_size = env_usize("ZG_MODEL_BENCH_BATCH", 16);
    let device = env::var("ZG_MODEL_BENCH_DEVICE")
        .ok()
        .map(|value| parse_device(&value));
    let texts = std::fs::read_to_string(&workload_path).expect("read fixed workload");
    let items = texts
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str::<WorkloadItem>(line).expect("valid workload row"))
        .collect::<Vec<_>>();
    let mut queries = Vec::new();
    let mut documents = Vec::new();
    for item in items {
        let input = vec![Content::Text(item.text)];
        match item.purpose.as_str() {
            "query" => queries.push(input),
            "document" => documents.push(input),
            other => panic!("unknown workload purpose {other:?}"),
        }
    }
    assert!(!queries.is_empty() && !documents.is_empty());

    let manager = ModelRuntimeManager::new();
    let load_started = Instant::now();
    let model = create_benchmark_model(&manager, &model_reference, cache, device, 1);
    model
        .prepare(EmbeddingPrepareOptions::default(), None)
        .await
        .expect("prepare fixed-workload model");
    let cold_load_seconds = load_started.elapsed().as_secs_f64();
    let loaded_rss_bytes = process_metric("VmRSS:");
    let loaded_pss_bytes = process_pss_bytes();

    let mut buckets = std::collections::BTreeMap::new();
    for (purpose_name, inputs, purpose) in [
        ("query", &queries, EmbeddingPurpose::Query),
        ("document", &documents, EmbeddingPurpose::Document),
    ] {
        let batch = inputs.iter().take(batch_size).cloned().collect::<Vec<_>>();
        black_box(
            model
                .embed(
                    &batch,
                    EmbeddingOptions {
                        purpose,
                        ..Default::default()
                    },
                    None,
                )
                .await
                .expect("fixed-workload warmup"),
        );
        let mut bucket = TimingBucket::default();
        for _ in 0..rounds {
            let mut offset = 0;
            while offset < inputs.len() {
                let end = (offset + batch_size).min(inputs.len());
                let wave = inputs[offset..end].to_vec();
                let started = Instant::now();
                let result = model
                    .embed(
                        &wave,
                        EmbeddingOptions {
                            purpose,
                            ..Default::default()
                        },
                        None,
                    )
                    .await
                    .expect("fixed-workload inference");
                black_box(&result);
                bucket.record(started.elapsed().as_secs_f64(), &wave);
                offset = end;
            }
        }
        buckets.insert(purpose_name, bucket);
    }

    drop(model);
    manager.close();
    println!(
        "ZG_MODEL_BENCH_JSON={}",
        json!({
            "mode": "fixed-workload",
            "model": model_reference,
            "device": device.map(device_name),
            "batch_size": batch_size,
            "rounds": rounds,
            "cold_load_seconds": cold_load_seconds,
            "loaded_rss_bytes": loaded_rss_bytes,
            "loaded_pss_bytes": loaded_pss_bytes,
            "peak_rss_bytes_vmhwm": process_metric("VmHWM:"),
            "buckets": buckets.into_iter().map(|(k, v)| (k, v.value())).collect::<Vec<_>>(),
        })
    );
}

/// Semantic sanity: the correct code passage must be closer than a distractor
/// for each of a few independent query/document pairs.
#[tokio::test(flavor = "multi_thread")]
#[ignore = "manual E4 semantic sanity probe"]
async fn model_layer_semantic_sanity() {
    let model_reference =
        env::var("ZG_MODEL_BENCH_MODEL").expect("ZG_MODEL_BENCH_MODEL must be set");
    let cache = env::var_os("ZG_MODEL_BENCH_CACHE").map(PathBuf::from);
    let device = env::var("ZG_MODEL_BENCH_DEVICE")
        .ok()
        .map(|value| parse_device(&value));
    let pairs = [
        (
            "Make an HTTP GET request and parse the JSON response body into a typed object.",
            "using var client = new HttpClient(); var json = await client.GetStringAsync(url); return JsonConvert.DeserializeObject<T>(json);",
            "ALTER TABLE customers ADD COLUMN email varchar(255) NOT NULL;",
        ),
        (
            "Check whether a string contains any digit characters.",
            "foreach (char c in text) if (char.IsDigit(c)) return true; return false;",
            "public void Dispose() { _stream?.Dispose(); GC.SuppressFinalize(this); }",
        ),
        (
            "Configure camel case property names when serializing an object.",
            "contract.Resolver = new DefaultContractResolver { NamingStrategy = new CamelCaseNamingStrategy() };",
            "int[] values = { 3, 1, 2 }; Array.Sort(values);",
        ),
    ];

    let manager = ModelRuntimeManager::new();
    let model = create_benchmark_model(&manager, &model_reference, cache, device, 1);
    model
        .prepare(EmbeddingPrepareOptions::default(), None)
        .await
        .expect("prepare semantic-sanity model");
    let mut margins = Vec::new();
    for (query, positive, negative) in pairs {
        let query_vector = model
            .embed(
                &[vec![Content::Text(query.to_owned())]],
                EmbeddingOptions {
                    purpose: EmbeddingPurpose::Query,
                    ..Default::default()
                },
                None,
            )
            .await
            .expect("query embedding")
            .vectors
            .remove(0);
        let docs = model
            .embed(
                &[
                    vec![Content::Text(positive.to_owned())],
                    vec![Content::Text(negative.to_owned())],
                ],
                EmbeddingOptions {
                    purpose: EmbeddingPurpose::Document,
                    ..Default::default()
                },
                None,
            )
            .await
            .expect("document embeddings")
            .vectors;
        let positive_score = cosine(&query_vector, &docs[0]);
        let negative_score = cosine(&query_vector, &docs[1]);
        margins.push(positive_score - negative_score);
        println!(
            "SANITY model={model_reference} positive={positive_score:.6} negative={negative_score:.6} margin={:.6}",
            positive_score - negative_score
        );
    }
    drop(model);
    manager.close();
    let min_margin = margins.iter().copied().fold(f64::INFINITY, f64::min);
    println!("SANITY_MIN_MARGIN={min_margin:.6}");
    assert!(
        min_margin > 0.0,
        "semantic sanity failed: a distractor was closer than the correct passage (min margin {min_margin:.6})"
    );
}

fn cosine(left: &[f32], right: &[f32]) -> f64 {
    assert_eq!(left.len(), right.len(), "vector dimensions must match");
    let mut dot = 0.0_f64;
    let mut left_norm = 0.0_f64;
    let mut right_norm = 0.0_f64;
    for (&left_value, &right_value) in left.iter().zip(right) {
        let left_value = f64::from(left_value);
        let right_value = f64::from(right_value);
        dot += left_value * right_value;
        left_norm += left_value * left_value;
        right_norm += right_value * right_value;
    }
    dot / (left_norm.sqrt() * right_norm.sqrt()).max(f64::MIN_POSITIVE)
}
