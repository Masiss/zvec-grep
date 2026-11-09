//! Reproducible C# structural-index measurement harness.
//!
//! This example is intentionally self-contained: it generates a deterministic
//! mixed-language corpus, embeds through a local mock OpenAI-compatible server,
//! and reports indexing, resource, disk and exact-symbol evidence as JSON.
//!
//! ```text
//! Usage:
//!   cargo run --release -p zg-engine --example csharp_index_probe -- \
//!     --root /path/to/scratch --out /path/to/result.json --phase full --files 300
//!   ... --phase incremental
//!   ... --phase query
//! ```

use std::{
    collections::BTreeMap,
    fs,
    io::{Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use serde_json::{Value, json};
use zg_engine::{
    ZvecGrep,
    api::{
        context::{
            ContextOptions,
            options::{ContextRoute, ContextRouteMode, QueryFilter, SymbolType},
        },
        index::IndexOptions,
        info::InfoOptions,
        symbols::SymbolsOptions,
    },
};

struct Args {
    root: PathBuf,
    out: PathBuf,
    phase: String,
    files: usize,
    index_version: u32,
}

fn parse_args() -> Args {
    let mut args = std::env::args().skip(1);
    let mut root = None;
    let mut out = None;
    let mut phase = "full".to_owned();
    let mut files = 300;
    let mut index_version = 3;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--root" => root = args.next().map(PathBuf::from),
            "--out" => out = args.next().map(PathBuf::from),
            "--phase" => phase = args.next().expect("phase"),
            "--files" => files = args.next().expect("files").parse().expect("files"),
            "--index-version" => {
                index_version = args.next().expect("version").parse().expect("version");
            }
            other => panic!("unknown argument {other}"),
        }
    }
    Args {
        root: root.expect("--root"),
        out: out.expect("--out"),
        phase,
        files,
        index_version,
    }
}

#[tokio::main]
#[allow(clippy::too_many_lines)]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = parse_args();
    let mut evidence = BTreeMap::new();
    evidence.insert("phase", json!(args.phase));
    evidence.insert("files_requested", json!(args.files));
    match args.phase.as_str() {
        "full" | "incremental" => {
            let server = MockEmbeddingServer::start()?;
            if args.phase == "full" || !args.root.join(".zvec-grep").exists() {
                configure_workspace(&args.root, server.address, args.index_version)?;
            } else {
                set_endpoint(&args.root, server.address)?;
            }
            if args.phase == "full" || !args.root.join("src").exists() {
                write_corpus(&args.root, args.files)?;
            } else {
                incremental_change(&args.root)?;
            }
            let engine = ZvecGrep::new();
            let started = Instant::now();
            let result = engine
                .index(IndexOptions {
                    root: Some(args.root.clone()),
                    allow_remote: true,
                    api_key: Some("local-test-key".into()),
                    ..IndexOptions::default()
                })
                .await?;
            let elapsed = started.elapsed();
            let index_dir = args.root.join(".zvec-grep/index");
            evidence.insert("wall_ms", json!(elapsed.as_millis()));
            evidence.insert(
                "files",
                json!({
                    "added": result.files_added,
                    "modified": result.files_modified,
                    "deleted": result.files_deleted,
                    "failed": result.files_failed,
                }),
            );
            evidence.insert("index_bytes", json!(directory_bytes(&index_dir)?));
            evidence.insert("self", process_usage());
            drop(server);
            engine.close();
        }
        "query" => {
            let engine = ZvecGrep::new();
            let names = ["Type0", "Type1", "Add", "Value0", "Method0", "Nested"];
            let mut samples = Vec::new();
            for (index, name) in names.iter().enumerate() {
                let started = Instant::now();
                let result = engine
                    .symbols(SymbolsOptions {
                        root: Some(args.root.clone()),
                        name: (*name).to_owned(),
                        auto_update: false,
                        limit: Some(50),
                        ..SymbolsOptions::default()
                    })
                    .await?;
                samples.push(json!({
                    "name": name,
                    "cold": index == 0,
                    "ms": started.elapsed().as_millis(),
                    "total": result.total,
                    "items": result.items.len(),
                }));
            }
            let mut exact = Vec::new();
            for _ in 0..30 {
                let started = Instant::now();
                let result = engine
                    .symbols(SymbolsOptions {
                        root: Some(args.root.clone()),
                        name: "Add".to_owned(),
                        auto_update: false,
                        ..SymbolsOptions::default()
                    })
                    .await?;
                exact.push((started.elapsed().as_micros(), result.total));
            }
            exact.sort_unstable();
            let started = Instant::now();
            let filtered = engine
                .context(ContextOptions {
                    root: Some(args.root.clone()),
                    routes: vec![ContextRoute {
                        mode: ContextRouteMode::Fts,
                        query: "Method0".to_owned(),
                    }],
                    filter: QueryFilter {
                        symbol_types: vec![SymbolType::Function],
                        ..QueryFilter::default()
                    },
                    auto_update: false,
                    allow_remote: true,
                    limit: Some(50),
                    ..ContextOptions::default()
                })
                .await?;
            evidence.insert(
                "fts_symbol_filter",
                json!({
                    "items": filtered.items.len(),
                    "all_function": filtered.items.iter().all(|item| matches!(
                        &item.metadata,
                        Some(zg_engine::api::context::result::EntityMetadata::Code(code))
                            if code.symbol_type == Some(SymbolType::Function)
                    )),
                    "ms": started.elapsed().as_millis(),
                }),
            );
            evidence.insert("exact_samples", json!(samples));
            evidence.insert(
                "exact_warm",
                json!({
                    "runs": exact.len(),
                    "p50_us": exact[exact.len() / 2].0,
                    "p95_us": exact[exact.len() * 95 / 100].0,
                    "max_us": exact.last().map(|sample| sample.0),
                    "total": exact.last().map(|sample| sample.1),
                }),
            );
            evidence.insert("self", process_usage());
            engine.close();
        }
        other => panic!("unknown phase {other}"),
    }
    fs::write(&args.out, serde_json::to_vec_pretty(&evidence)?)?;
    println!("{}", serde_json::to_string_pretty(&evidence)?);
    Ok(())
}

fn set_endpoint(root: &Path, address: SocketAddr) -> Result<(), Box<dyn std::error::Error>> {
    let path = root.join(".zvec-grep/manifest.json");
    let mut manifest: Value = serde_json::from_slice(&fs::read(&path)?)?;
    manifest["index"]["descriptor"]["embeddingConfigs"]["qwen/text-embedding-v4"]["endpoint"] =
        json!(format!("http://{address}/embeddings"));
    fs::write(path, serde_json::to_vec(&manifest)?)?;
    Ok(())
}

fn write_corpus(root: &Path, files: usize) -> Result<(), Box<dyn std::error::Error>> {
    let src = root.join("src");
    if src.exists() {
        fs::remove_dir_all(&src)?;
    }
    fs::create_dir_all(src.join("cs"))?;
    fs::create_dir_all(src.join("rust"))?;
    fs::create_dir_all(src.join("python"))?;
    fs::create_dir_all(src.join("docs"))?;
    for index in 0..files {
        let namespace = format!("Bench.N{}", index % 17);
        let source = format!(
            concat!(
                "namespace {namespace};\n",
                "\n",
                "public partial class Type{index}\n",
                "{{\n",
                "    public int Value{index} {{ get; init; }}\n",
                "    public string Name {{ get; set; }} = \"type{index}\";\n",
                "    public int Add(int a, int b) => a + b;\n",
                "    public int Add(int a, int b, int c) => a + b + c;\n",
                "    public void Method{index}()\n",
                "    {{\n",
                "        var local = Add(1, 2);\n",
                "        System.Console.WriteLine(local);\n",
                "    }}\n",
                "    public class Nested\n",
                "    {{\n",
                "        public string Detail {{ get; set; }} = \"nested{index}\";\n",
                "    }}\n",
                "}}\n",
            ),
            namespace = namespace,
            index = index,
        );
        fs::write(src.join("cs").join(format!("Type{index}.cs")), source)?;
        if index % 4 == 0 {
            fs::write(
                src.join("rust").join(format!("mod{index}.rs")),
                format!(
                    "pub struct Type{index} {{ pub value: i32 }}\nimpl Type{index} {{ pub fn add(&self, a: i32) -> i32 {{ a + self.value }} }}\n"
                ),
            )?;
            fs::write(
                src.join("python").join(format!("mod{index}.py")),
                format!("class Type{index}:\n    def add(self, a):\n        return a\n"),
            )?;
            fs::write(
                src.join("docs").join(format!("note{index}.md")),
                format!("# Note {index}\n\nType{index} documentation.\n"),
            )?;
        }
    }
    Ok(())
}

fn incremental_change(root: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let path = root.join("src/cs/Type0.cs");
    let mut source = fs::read_to_string(&path)?;
    source.push_str("public class IncrementalMarker { }\n");
    fs::write(path, source)?;
    Ok(())
}

fn directory_bytes(path: &Path) -> std::io::Result<u64> {
    let mut total = 0;
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let metadata = entry.metadata()?;
        if metadata.is_dir() {
            total += directory_bytes(&entry.path())?;
        } else {
            total += metadata.len();
        }
    }
    Ok(total)
}

fn process_usage() -> Value {
    let status = fs::read_to_string("/proc/self/status").unwrap_or_default();
    let mut rss_kib = 0;
    let mut hwm_kib = 0;
    for line in status.lines() {
        if let Some(value) = line.strip_prefix("VmRSS:") {
            rss_kib = value
                .split_whitespace()
                .next()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(0);
        }
        if let Some(value) = line.strip_prefix("VmHWM:") {
            hwm_kib = value
                .split_whitespace()
                .next()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(0);
        }
    }
    let pss_kib = fs::read_to_string("/proc/self/smaps_rollup")
        .ok()
        .and_then(|text| {
            text.lines()
                .find_map(|line| line.strip_prefix("Pss:"))
                .and_then(|value| value.split_whitespace().next())
                .and_then(|value| value.parse::<u64>().ok())
        })
        .unwrap_or(0);
    json!({
        "rss_kib": rss_kib,
        "peak_rss_kib": hwm_kib,
        "pss_kib": pss_kib,
    })
}

fn configure_workspace(
    root: &Path,
    address: SocketAddr,
    index_version: u32,
) -> Result<(), Box<dyn std::error::Error>> {
    let home = root.join(".zvec-grep");
    if home.exists() {
        fs::remove_dir_all(&home)?;
    }
    fs::create_dir_all(&home)?;
    let manifest = json!({
        "sourceRoot": {
            "recordedPath": fs::canonicalize(root)?.to_string_lossy(),
            "recordedUsername": std::env::var("USER").ok(),
        },
        "scan": {},
        "indexVersion": index_version,
        "storageGeneration": uuid::Uuid::new_v4().to_string(),
        "createdTime": 1,
        "updatedTime": 1,
        "index": {
            "state": "enabled",
            "descriptor": {
                "defaultEmbeddingModel": {
                    "model": {
                        "provider": "qwen", "name": "text-embedding-v4",
                        "contentKinds": ["text", "code"], "maxBatchSize": 10
                    },
                    "dimension": 1024, "metric": "cosine",
                    "maxInputTokens": 8192, "maxImageBytes": null
                },
                "embeddingRoutes": {},
                "embeddingConfigs": {
                    "qwen/text-embedding-v4": {
                        "endpoint": format!("http://{address}/embeddings")
                    }
                }
            }
        }
    });
    fs::write(home.join("manifest.json"), serde_json::to_vec(&manifest)?)?;
    Ok(())
}

struct MockEmbeddingServer {
    address: SocketAddr,
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}

impl MockEmbeddingServer {
    fn start() -> std::io::Result<Self> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let address = listener.local_addr()?;
        let stop = Arc::new(AtomicBool::new(false));
        let worker = thread::spawn({
            let stop = Arc::clone(&stop);
            move || {
                for stream in listener.incoming() {
                    if stop.load(Ordering::Acquire) {
                        break;
                    }
                    if let Ok(stream) = stream {
                        let _ = respond(stream);
                    }
                }
            }
        });
        Ok(Self {
            address,
            stop,
            worker: Some(worker),
        })
    }
}

impl Drop for MockEmbeddingServer {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        let _ = TcpStream::connect(self.address);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

fn respond(mut stream: TcpStream) -> std::io::Result<()> {
    stream.set_read_timeout(Some(Duration::from_secs(30)))?;
    stream.set_write_timeout(Some(Duration::from_secs(30)))?;
    let mut request = Vec::new();
    let mut buffer = [0; 8192];
    let (header_end, content_length) = loop {
        let count = stream.read(&mut buffer)?;
        if count == 0 {
            return Err(std::io::ErrorKind::UnexpectedEof.into());
        }
        request.extend_from_slice(&buffer[..count]);
        if let Some(end) = request.windows(4).position(|bytes| bytes == b"\r\n\r\n") {
            let headers = String::from_utf8_lossy(&request[..end]).to_ascii_lowercase();
            let length = headers
                .lines()
                .find_map(|line| line.strip_prefix("content-length:"))
                .expect("content length")
                .trim()
                .parse::<usize>()
                .expect("valid content length");
            break (end + 4, length);
        }
    };
    while request.len() < header_end + content_length {
        let count = stream.read(&mut buffer)?;
        if count == 0 {
            return Err(std::io::ErrorKind::UnexpectedEof.into());
        }
        request.extend_from_slice(&buffer[..count]);
    }
    let body: Value = serde_json::from_slice(&request[header_end..header_end + content_length])?;
    let items = body["input"].as_array().expect("text inputs");
    let dimension = usize::try_from(body["dimensions"].as_u64().expect("dimensions"))
        .expect("usize dimensions");
    let data = items
        .iter()
        .enumerate()
        .map(|(index, text)| {
            let mut vector = vec![0.0_f32; dimension];
            let text = text.as_str().expect("text");
            for word in text
                .split(|character: char| !character.is_alphanumeric())
                .filter(|word| !word.is_empty())
            {
                let hash = word.to_lowercase().bytes().fold(0usize, |hash, byte| {
                    hash.wrapping_mul(31).wrapping_add(usize::from(byte))
                });
                vector[hash % dimension] += 1.0;
            }
            json!({ "index": index, "embedding": vector })
        })
        .collect::<Vec<_>>();
    let response = serde_json::to_vec(&json!({ "data": data }))?;
    write!(
        stream,
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        response.len()
    )?;
    stream.write_all(&response)
}

#[allow(dead_code)]
fn info_options(root: &Path) -> InfoOptions {
    InfoOptions {
        root: Some(root.to_path_buf()),
        include_status: true,
    }
}
