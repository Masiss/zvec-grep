//! Common v2/v3 indexing and retrieval benchmark harness.
//!
//! Usage:
//! ```text
//! index_bench --root DIR --out FILE --phase full|incremental|query \
//!   --corpus mixed|dense|duplicate --files N --index-version V [--rebuild]
//! ```
//!
//! The harness owns a local mock OpenAI-compatible embedding server, so the
//! same source runs against the v2 worktree and this branch.

use std::{
    collections::BTreeMap,
    fmt::Write as _,
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
#[cfg(unix)]
use std::os::unix::fs::MetadataExt;
use zg_engine::{
    ZvecGrep,
    api::{
        context::{
            ContextOptions,
            options::{ContextRoute, ContextRouteMode},
        },
        index::{
            IndexOptions,
            options::{Device, EmbeddingModelSpec},
        },
    },
};

struct Args {
    root: PathBuf,
    out: PathBuf,
    phase: String,
    corpus: String,
    files: usize,
    index_version: u32,
    rebuild: bool,
}

fn parse_args() -> Args {
    let mut args = std::env::args().skip(1);
    let mut root = None;
    let mut out = None;
    let mut phase = "full".to_owned();
    let mut corpus = "mixed".to_owned();
    let mut files = 300;
    let mut index_version = 3;
    let mut rebuild = false;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--root" => root = args.next().map(PathBuf::from),
            "--out" => out = args.next().map(PathBuf::from),
            "--phase" => phase = args.next().expect("phase"),
            "--corpus" => corpus = args.next().expect("corpus"),
            "--files" => files = args.next().expect("files").parse().expect("files"),
            "--index-version" => {
                index_version = args.next().expect("version").parse().expect("version");
            }
            "--rebuild" => rebuild = true,
            other => panic!("unknown argument {other}"),
        }
    }
    Args {
        root: root.expect("--root"),
        out: out.expect("--out"),
        phase,
        corpus,
        files,
        index_version,
        rebuild,
    }
}

#[tokio::main]
#[allow(clippy::too_many_lines)]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = parse_args();
    let server = MockEmbeddingServer::start()?;
    let mut evidence = BTreeMap::new();
    evidence.insert("phase", json!(args.phase));
    evidence.insert("corpus", json!(args.corpus));
    evidence.insert("files_requested", json!(args.files));
    match args.phase.as_str() {
        "full" | "incremental" => {
            if args.phase == "full" || !args.root.join(".zvec-grep").exists() {
                configure_workspace(&args.root, server.address, args.index_version)?;
            } else {
                set_endpoint(&args.root, server.address)?;
            }
            if args.phase == "full" || !args.root.join("src").exists() {
                write_corpus(&args.root, &args.corpus, args.files)?;
            } else {
                incremental_change(&args.root)?;
            }
            let engine = ZvecGrep::new();
            let started = Instant::now();
            let result = engine
                .index(IndexOptions {
                    root: Some(args.root.clone()),
                    rebuild: args.rebuild,
                    allow_remote: true,
                    api_key: Some("local-test-key".into()),
                    ..IndexOptions::default()
                })
                .await?;
            let elapsed = started.elapsed();
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
            evidence.insert("index", index_bytes(&args.root.join(".zvec-grep/index"))?);
            evidence.insert("self", process_usage());
            engine.close();
        }
        "rebuild" => {
            set_endpoint(&args.root, server.address)?;
            let engine = ZvecGrep::new();
            let started = Instant::now();
            let result = engine
                .index(IndexOptions {
                    root: Some(args.root.clone()),
                    rebuild: true,
                    allow_remote: true,
                    api_key: Some("local-test-key".into()),
                    embedding: Some(EmbeddingModelSpec {
                        reference: "qwen/text-embedding-v4".into(),
                        revision: None,
                        cache_dir: None,
                        endpoint: None,
                        device: Device::Cpu,
                    }),
                    endpoint: Some(format!("http://{}/embeddings", server.address)),
                    ..IndexOptions::default()
                })
                .await?;
            evidence.insert("wall_ms", json!(started.elapsed().as_millis()));
            evidence.insert(
                "files",
                json!({
                    "added": result.files_added,
                    "modified": result.files_modified,
                    "deleted": result.files_deleted,
                    "failed": result.files_failed,
                }),
            );
            evidence.insert("index", index_bytes(&args.root.join(".zvec-grep/index"))?);
            evidence.insert("self", process_usage());
            engine.close();
        }
        "query" => {
            set_endpoint(&args.root, server.address)?;
            let engine = ZvecGrep::new();
            let mut queries = BTreeMap::new();
            for (label, mode, query) in query_cases(&args.corpus) {
                queries.insert(
                    label.to_owned(),
                    measure_query(&engine, &args.root, mode, query).await?,
                );
            }
            evidence.insert("queries", json!(queries));
            evidence.insert("self", process_usage());
            engine.close();
        }
        other => panic!("unknown phase {other}"),
    }
    drop(server);
    fs::write(&args.out, serde_json::to_vec_pretty(&evidence)?)?;
    println!("{}", serde_json::to_string_pretty(&evidence)?);
    Ok(())
}

fn query_cases(corpus: &str) -> Vec<(&'static str, Vec<ContextRouteMode>, &'static str)> {
    match corpus {
        "toplevel" => vec![
            ("fts", vec![ContextRouteMode::Fts], "top0"),
            ("vector", vec![ContextRouteMode::Vector], "top0"),
            (
                "hybrid",
                vec![ContextRouteMode::Fts, ContextRouteMode::Vector],
                "top0",
            ),
        ],
        "duplicate" => vec![
            ("fts", vec![ContextRouteMode::Fts], "Widget"),
            ("vector", vec![ContextRouteMode::Vector], "Widget"),
            (
                "hybrid",
                vec![ContextRouteMode::Fts, ContextRouteMode::Vector],
                "Widget",
            ),
        ],
        _ => vec![
            ("fts", vec![ContextRouteMode::Fts], "Type0"),
            ("vector", vec![ContextRouteMode::Vector], "Type0"),
            (
                "hybrid",
                vec![ContextRouteMode::Fts, ContextRouteMode::Vector],
                "Type0",
            ),
        ],
    }
}

async fn measure_query(
    engine: &ZvecGrep,
    root: &Path,
    modes: Vec<ContextRouteMode>,
    query: &str,
) -> Result<Value, Box<dyn std::error::Error>> {
    let run = |modes: &[ContextRouteMode]| {
        let options = ContextOptions {
            root: Some(root.to_path_buf()),
            routes: modes
                .iter()
                .map(|mode| ContextRoute {
                    mode: *mode,
                    query: query.to_owned(),
                })
                .collect(),
            fuse: modes.len() > 1,
            auto_update: false,
            allow_remote: true,
            limit: Some(10),
            ..ContextOptions::default()
        };
        async move { engine.context(options).await }
    };
    let cold = Instant::now();
    let result = run(&modes).await?;
    let cold_us = cold.elapsed().as_micros();
    let mut warm = Vec::new();
    let mut items = result.items.len();
    let mut distinct = result
        .items
        .iter()
        .map(|item| item.entity_id.clone().unwrap_or_default())
        .collect::<std::collections::BTreeSet<_>>()
        .len();
    let mut files = result
        .items
        .iter()
        .map(|item| item.relative_path.clone())
        .collect::<std::collections::BTreeSet<_>>()
        .len();
    for _ in 0..20 {
        let started = Instant::now();
        let result = run(&modes).await?;
        warm.push(started.elapsed().as_micros());
        items = result.items.len();
        distinct = result
            .items
            .iter()
            .map(|item| item.entity_id.clone().unwrap_or_default())
            .collect::<std::collections::BTreeSet<_>>()
            .len();
        files = result
            .items
            .iter()
            .map(|item| item.relative_path.clone())
            .collect::<std::collections::BTreeSet<_>>()
            .len();
    }
    warm.sort_unstable();
    Ok(json!({
        "items": items,
        "distinct_entities": distinct,
        "distinct_files": files,
        "cold_us": cold_us,
        "warm_p50_us": warm[warm.len() / 2],
        "warm_p95_us": warm[warm.len() * 95 / 100],
    }))
}

fn write_corpus(root: &Path, corpus: &str, files: usize) -> Result<(), Box<dyn std::error::Error>> {
    let src = root.join("src");
    if src.exists() {
        fs::remove_dir_all(&src)?;
    }
    fs::create_dir_all(src.join("cs"))?;
    match corpus {
        "dense" => {
            for index in 0..files {
                let mut source = format!(
                    "namespace Bench.Dense{};\npublic class Dense{index}\n{{\n",
                    index % 11
                );
                for member in 0..30 {
                    let _ = writeln!(source, "    public int Property{member} {{ get; init; }}");
                }
                for member in 0..10 {
                    let _ = writeln!(
                        source,
                        "    public int Method{member}(int value) => value + {member};"
                    );
                }
                source.push_str("}\n");
                fs::write(src.join("cs").join(format!("Dense{index}.cs")), source)?;
            }
        }
        "noncs" => {
            fs::create_dir_all(src.join("rust"))?;
            fs::create_dir_all(src.join("python"))?;
            fs::create_dir_all(src.join("docs"))?;
            for index in 0..files {
                fs::write(
                    src.join("rust").join(format!("mod{index}.rs")),
                    format!(
                        "pub struct Type{index} {{ pub value: i32 }}\npub fn add{index}(a: i32) -> i32 {{ a + 1 }}\n"
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
        "toplevel" => {
            for index in 0..files {
                fs::write(
                    src.join("cs").join(format!("Top{index}.cs")),
                    format!(
                        "using System;\n// comment {index}\nConsole.WriteLine(\"top{index}\");\npublic class Top{index} {{ public void Run() {{ }} }}\n"
                    ),
                )?;
            }
        }
        "duplicate" => {
            for index in 0..files {
                fs::write(
                    src.join("cs").join(format!("Widget{index}.cs")),
                    "namespace Dup;\npublic class Widget { public void Run() { } public int Value { get; set; } }\n",
                )?;
            }
        }
        _ => {
            fs::create_dir_all(src.join("rust"))?;
            fs::create_dir_all(src.join("python"))?;
            fs::create_dir_all(src.join("docs"))?;
            for index in 0..files {
                let namespace = format!("Bench.N{}", index % 17);
                let source = format!(
                    concat!(
                        "namespace {namespace};\n",
                        "public partial class Type{index}\n",
                        "{{\n",
                        "    public int Value{index} {{ get; init; }}\n",
                        "    public string Name {{ get; set; }} = \"type{index}\";\n",
                        "    public int Add(int a, int b) => a + b;\n",
                        "    public int Add(int a, int b, int c) => a + b + c;\n",
                        "    public void Method{index}() {{ var local = Add(1, 2); }}\n",
                        "    public class Nested {{ public string Detail {{ get; set; }} = \"nested{index}\"; }}\n",
                        "}}\n",
                    ),
                    namespace = namespace,
                    index = index
                );
                fs::write(src.join("cs").join(format!("Type{index}.cs")), source)?;
                if index % 4 == 0 {
                    fs::write(
                        src.join("rust").join(format!("mod{index}.rs")),
                        format!("pub struct Type{index} {{ pub value: i32 }}\n"),
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
        }
    }
    Ok(())
}

fn incremental_change(root: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let path = root.join("src/cs/Type0.cs");
    let path = if path.exists() {
        path
    } else {
        root.join("src/cs/Dense0.cs")
    };
    let path = if path.exists() {
        path
    } else {
        root.join("src/cs/Widget0.cs")
    };
    let path = if path.exists() {
        path
    } else {
        root.join("src/rust/mod0.rs")
    };
    let mut source = fs::read_to_string(&path)?;
    source.push_str("public class IncrementalMarker { }\n");
    fs::write(path, source)?;
    Ok(())
}

fn index_bytes(path: &Path) -> std::io::Result<Value> {
    let mut allocated = 0;
    let mut apparent = 0;
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let size = allocated_bytes(&entry.path())?;
        let apparent_size = apparent_bytes(&entry.path())?;
        allocated += size;
        apparent += apparent_size;
    }
    Ok(json!({
        "allocated_bytes": allocated,
        "apparent_bytes": apparent,
    }))
}

fn allocated_bytes(path: &Path) -> std::io::Result<u64> {
    let meta = fs::symlink_metadata(path)?;
    #[cfg(unix)]
    let file_bytes = if meta.is_file() {
        meta.blocks() * 512
    } else {
        0
    };
    #[cfg(not(unix))]
    let file_bytes = if meta.is_file() { meta.len() } else { 0 };
    let mut total = file_bytes;
    if meta.is_dir() {
        for entry in fs::read_dir(path)? {
            total += allocated_bytes(&entry?.path())?;
        }
    }
    Ok(total)
}

fn apparent_bytes(path: &Path) -> std::io::Result<u64> {
    let meta = fs::symlink_metadata(path)?;
    if meta.is_file() {
        return Ok(meta.len());
    }
    let mut total = 0;
    for entry in fs::read_dir(path)? {
        total += apparent_bytes(&entry?.path())?;
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

fn set_endpoint(root: &Path, address: SocketAddr) -> Result<(), Box<dyn std::error::Error>> {
    let path = root.join(".zvec-grep/manifest.json");
    let mut manifest: Value = serde_json::from_slice(&fs::read(&path)?)?;
    manifest["index"]["descriptor"]["embeddingConfigs"]["qwen/text-embedding-v4"]["endpoint"] =
        json!(format!("http://{address}/embeddings"));
    fs::write(path, serde_json::to_vec(&manifest)?)?;
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
