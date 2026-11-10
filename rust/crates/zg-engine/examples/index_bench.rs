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
    fs::{self, FileTimes},
    io::{Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    thread,
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
    root: Option<PathBuf>,
    out: Option<PathBuf>,
    phase: String,
    corpus: String,
    files: usize,
    index_version: u32,
    rebuild: bool,
    change: String,
    diagnostics: Option<PathBuf>,
    mock_server: bool,
    port_file: Option<PathBuf>,
    stop_file: Option<PathBuf>,
    stats_file: Option<PathBuf>,
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
    let mut change = "append".to_owned();
    let mut diagnostics = None;
    let mut mock_server = false;
    let mut port_file = None;
    let mut stop_file = None;
    let mut stats_file = None;
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
            "--change" => change = args.next().expect("change"),
            "--diagnostics" => diagnostics = args.next().map(PathBuf::from),
            "--mock-server" => mock_server = true,
            "--port-file" => port_file = args.next().map(PathBuf::from),
            "--stop-file" => stop_file = args.next().map(PathBuf::from),
            "--stats-file" => stats_file = args.next().map(PathBuf::from),
            other => panic!("unknown argument {other}"),
        }
    }
    Args {
        root,
        out,
        phase,
        corpus,
        files,
        index_version,
        rebuild,
        change,
        diagnostics,
        mock_server,
        port_file,
        stop_file,
        stats_file,
    }
}

#[tokio::main]
#[allow(clippy::too_many_lines)]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = parse_args();
    if args.mock_server {
        return run_mock_server(&args);
    }
    let root = args.root.clone().expect("--root");
    let out = args.out.clone().expect("--out");
    if let Some(diagnostics) = &args.diagnostics {
        // The driver sets `ZVEC_GREP_INDEX_DIAGNOSTICS` for this process; stale
        // output from an earlier run must not be mistaken for this one.
        let _ = fs::remove_file(diagnostics);
    }
    let mut server = MockEmbeddingServer::start()?;
    let mut evidence = BTreeMap::new();
    evidence.insert("phase", json!(args.phase));
    evidence.insert("corpus", json!(args.corpus));
    evidence.insert("files_requested", json!(args.files));
    evidence.insert("change", json!(args.change));
    evidence.insert(
        "diagnostics_env",
        json!(std::env::var("ZVEC_GREP_INDEX_DIAGNOSTICS").ok()),
    );
    match args.phase.as_str() {
        "full" | "incremental" => {
            if args.phase == "full" || !root.join(".zvec-grep").exists() {
                configure_workspace(&root, server.address, args.index_version)?;
            } else {
                set_endpoint(&root, server.address)?;
            }
            if args.phase == "full" || !root.join("src").exists() {
                write_corpus(&root, &args.corpus, args.files)?;
            } else {
                apply_change(&root, &args.change)?;
            }
            let engine = ZvecGrep::new();
            let started = Instant::now();
            let result = engine
                .index(IndexOptions {
                    root: Some(root.clone()),
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
            evidence.insert("index", index_bytes(&root.join(".zvec-grep/index"))?);
            evidence.insert("entities_created", json!(result.entities_created));
            evidence.insert("timings", json!(result.timings));
            evidence.insert("self", process_usage());
            engine.close();
        }
        "rebuild" => {
            set_endpoint(&root, server.address)?;
            let engine = ZvecGrep::new();
            let started = Instant::now();
            let result = engine
                .index(IndexOptions {
                    root: Some(root.clone()),
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
            evidence.insert("index", index_bytes(&root.join(".zvec-grep/index"))?);
            evidence.insert("entities_created", json!(result.entities_created));
            evidence.insert("timings", json!(result.timings));
            evidence.insert("self", process_usage());
            engine.close();
        }
        "monotonic" => {
            set_endpoint(&root, server.address)?;
            let engine = ZvecGrep::new();
            let mut ticks = Vec::new();
            for tick in 0..20 {
                let path = ["src/cs/Type0.cs", "src/cs/Dense0.cs", "src/cs/Widget0.cs"]
                    .iter()
                    .map(|path| root.join(path))
                    .find(|path| path.exists())
                    .expect("corpus file");
                let mut source = fs::read_to_string(&path)?;
                let _ = writeln!(source, "// tick {tick}");
                fs::write(&path, source)?;
                let result = engine
                    .index(IndexOptions {
                        root: Some(root.clone()),
                        allow_remote: true,
                        api_key: Some("local-test-key".into()),
                        ..IndexOptions::default()
                    })
                    .await?;
                let manifest: Value =
                    serde_json::from_slice(&fs::read(root.join(".zvec-grep/manifest.json"))?)?;
                ticks.push(json!({
                    "tick": tick,
                    "updated_time": manifest["updatedTime"],
                    "modified": result.files_modified,
                }));
            }
            evidence.insert("ticks", json!(ticks));
            engine.close();
        }
        "query" => {
            set_endpoint(&root, server.address)?;
            let engine = ZvecGrep::new();
            let mut queries = BTreeMap::new();
            let limit = if args.corpus == "quality" { 50 } else { 10 };
            for (label, mode, query) in query_cases(&args.corpus) {
                queries.insert(
                    label,
                    measure_query(&engine, &root, mode, &query, limit).await?,
                );
            }
            evidence.insert("queries", json!(queries));
            evidence.insert("self", process_usage());
            engine.close();
        }
        other => panic!("unknown phase {other}"),
    }
    evidence.insert("server", server.finish());
    if let Some(diagnostics) = &args.diagnostics {
        let value = fs::read(diagnostics)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
            .unwrap_or(Value::Null);
        evidence.insert("diagnostics", value);
    }
    fs::write(&out, serde_json::to_vec_pretty(&evidence)?)?;
    println!("{}", serde_json::to_string_pretty(&evidence)?);
    Ok(())
}

#[allow(clippy::too_many_lines)]
fn query_cases(corpus: &str) -> Vec<(String, Vec<ContextRouteMode>, String)> {
    match corpus {
        "cjk" => vec![
            (
                "fts".to_owned(),
                vec![ContextRouteMode::Fts],
                "发票对账".to_owned(),
            ),
            (
                "fts".to_owned(),
                vec![ContextRouteMode::Fts],
                "仓库库存".to_owned(),
            ),
            (
                "fts".to_owned(),
                vec![ContextRouteMode::Fts],
                "不存在的词语".to_owned(),
            ),
        ],
        "quality" => {
            let mut cases = Vec::new();
            for (_, _, query) in quality_topics() {
                for (mode, routes) in [
                    ("fts", vec![ContextRouteMode::Fts]),
                    ("vector", vec![ContextRouteMode::Vector]),
                    (
                        "hybrid",
                        vec![ContextRouteMode::Fts, ContextRouteMode::Vector],
                    ),
                ] {
                    cases.push((format!("{mode}:{query}"), routes, query.to_owned()));
                }
            }
            cases
        }
        "dense" => vec![
            (
                "fts".to_owned(),
                vec![ContextRouteMode::Fts],
                "Dense0".to_owned(),
            ),
            (
                "vector".to_owned(),
                vec![ContextRouteMode::Vector],
                "Dense0".to_owned(),
            ),
            (
                "hybrid".to_owned(),
                vec![ContextRouteMode::Fts, ContextRouteMode::Vector],
                "Dense0".to_owned(),
            ),
        ],
        "toplevel" => vec![
            (
                "fts".to_owned(),
                vec![ContextRouteMode::Fts],
                "top0".to_owned(),
            ),
            (
                "vector".to_owned(),
                vec![ContextRouteMode::Vector],
                "top0".to_owned(),
            ),
            (
                "hybrid".to_owned(),
                vec![ContextRouteMode::Fts, ContextRouteMode::Vector],
                "top0".to_owned(),
            ),
        ],
        "duplicate" => vec![
            (
                "fts".to_owned(),
                vec![ContextRouteMode::Fts],
                "Widget".to_owned(),
            ),
            (
                "vector".to_owned(),
                vec![ContextRouteMode::Vector],
                "Widget".to_owned(),
            ),
            (
                "hybrid".to_owned(),
                vec![ContextRouteMode::Fts, ContextRouteMode::Vector],
                "Widget".to_owned(),
            ),
        ],
        _ => vec![
            (
                "fts".to_owned(),
                vec![ContextRouteMode::Fts],
                "Type0".to_owned(),
            ),
            (
                "vector".to_owned(),
                vec![ContextRouteMode::Vector],
                "Type0".to_owned(),
            ),
            (
                "hybrid".to_owned(),
                vec![ContextRouteMode::Fts, ContextRouteMode::Vector],
                "Type0".to_owned(),
            ),
        ],
    }
}

async fn measure_query(
    engine: &ZvecGrep,
    root: &Path,
    modes: Vec<ContextRouteMode>,
    query: &str,
    limit: usize,
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
            limit: Some(limit),
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
    let mut result_paths = Vec::new();
    for _ in 0..20 {
        let started = Instant::now();
        let result = run(&modes).await?;
        warm.push(started.elapsed().as_micros());
        result_paths = result
            .items
            .iter()
            .map(|item| item.relative_path.to_string_lossy().into_owned())
            .collect();
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
        "paths": result_paths,
    }))
}

#[allow(clippy::too_many_lines)]
fn quality_topics() -> Vec<(&'static str, &'static str, &'static str)> {
    vec![
        (
            "invoice-reconciliation",
            "InvoiceReconciliation",
            "invoice reconciliation",
        ),
        (
            "warehouse-inventory",
            "WarehouseInventory",
            "warehouse inventory",
        ),
        (
            "patient-scheduling",
            "PatientScheduling",
            "patient scheduling",
        ),
        (
            "claim-adjudication",
            "ClaimAdjudication",
            "claim adjudication",
        ),
        ("payroll-export", "PayrollExport", "payroll export"),
        ("audit-logging", "AuditLogging", "audit logging"),
        ("device-telemetry", "DeviceTelemetry", "device telemetry"),
        ("document-indexing", "DocumentIndexing", "document indexing"),
        ("access-control", "AccessControl", "access control"),
        (
            "billing-reconciliation",
            "BillingReconciliation",
            "billing reconciliation",
        ),
    ]
}

#[allow(clippy::too_many_lines)]
fn write_corpus(root: &Path, corpus: &str, files: usize) -> Result<(), Box<dyn std::error::Error>> {
    let src = root.join("src");
    if src.exists() {
        fs::remove_dir_all(&src)?;
    }
    fs::create_dir_all(src.join("cs"))?;
    match corpus {
        "cjk" => {
            fs::create_dir_all(src.join("docs"))?;
            for (index, text) in [
                "发票对账流程说明。财务系统每日核对发票与总账。",
                "仓库库存盘点规则。库存不足时触发补货通知。",
                "患者预约排班指南。门诊排班与预约冲突处理。",
                "理赔审核与 adjudication 流程。",
            ]
            .into_iter()
            .enumerate()
            {
                fs::write(
                    src.join("docs").join(format!("note{index}.md")),
                    format!("# 文档 {index}\n\n{text}\n"),
                )?;
            }
        }
        "quality" => {
            let mut truth = Vec::new();
            for (slug, pascal, query) in quality_topics() {
                let mut relevant = Vec::new();
                let dir = src.join("cs").join(slug);
                fs::create_dir_all(&dir)?;
                for index in 0..8 {
                    let path = format!("src/cs/{slug}/Concept{index}.cs");
                    let body = format!(
                        "namespace Quality.{pascal};\n\n/// <summary>\n/// Handles {query} for tenant {tenant}. Ledger matching, exception review and reporting.\n/// </summary>\npublic class Concept{index}\n{{\n    public int Tenant {{ get; init; }} = {tenant};\n    public string Status {{ get; set; }} = \"open\";\n    public void Handle(int id) {{ var ledger = LoadLedger(id); }}\n    public string Describe() => \"{query}\";\n    private object LoadLedger(int id) => id;\n}}\n",
                        index = index,
                        tenant = index + 1,
                    );
                    fs::write(root.join(&path), body)?;
                    relevant.push(path);
                }
                let docs = src.join("docs").join(slug);
                fs::create_dir_all(&docs)?;
                for index in 0..2 {
                    let path = format!("src/docs/{slug}/guide{index}.md");
                    fs::write(
                        root.join(&path),
                        format!("# {query} guide {index}\n\nOperations for {query}.\n"),
                    )?;
                    relevant.push(path);
                }
                fs::create_dir_all(src.join("rust"))?;
                fs::create_dir_all(src.join("python"))?;
                let rust_path = format!("src/rust/{slug}.rs");
                fs::write(
                    root.join(&rust_path),
                    format!("pub fn {pascal}() -> &\'static str {{ \"{query}\" }}\n"),
                )?;
                relevant.push(rust_path);
                let python_path = format!("src/python/{slug}.py");
                fs::write(
                    root.join(&python_path),
                    format!("def {pascal}():\n    return \"{query}\"\n"),
                )?;
                relevant.push(python_path);
                truth.push(json!({"query": query, "relevant": relevant}));
            }
            fs::write(
                root.join("quality_truth.json"),
                serde_json::to_vec_pretty(&json!({"queries": truth}))?,
            )?;
        }
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

/// Applies one M3 scenario to the corpus. `none` leaves the workspace untouched.
fn apply_change(root: &Path, change: &str) -> Result<(), Box<dyn std::error::Error>> {
    let target = ["src/cs/Type0.cs", "src/cs/Dense0.cs", "src/cs/Widget0.cs", "src/rust/mod0.rs"]
        .iter()
        .map(|path| root.join(path))
        .find(|path| path.exists());
    let Some(target) = target else {
        if change == "none" {
            return Ok(());
        }
        return Err("corpus has no change target".into());
    };
    match change {
        "none" => {}
        "append" => {
            let mut source = fs::read_to_string(&target)?;
            source.push_str("public class IncrementalMarker { }\n");
            fs::write(target, source)?;
        }
        "touch" => {
            // Same bytes, newer mtime: the snapshot check must treat it as modified
            // metadata but unchanged content.
            let bytes = fs::read(&target)?;
            let modified = fs::metadata(&target)?.modified()?;
            fs::write(&target, bytes)?;
            let file = fs::File::options().write(true).open(&target)?;
            file.set_times(FileTimes::new().set_modified(modified + Duration::from_secs(2)))?;
        }
        "body" => {
            // Trailing edit: declarations before it keep their identity, so this
            // is the scenario that exercises vector reuse.
            let mut source = fs::read_to_string(&target)?;
            source.push_str("// edited body\n");
            fs::write(target, source)?;
        }
        "prepend" => {
            let source = fs::read_to_string(&target)?;
            fs::write(&target, format!("// prepended line\n{source}"))?;
        }
        "samesize" => {
            // Same length, different bytes, original mtime restored.
            let mut bytes = fs::read(&target)?;
            let modified = fs::metadata(&target)?.modified()?;
            let index = bytes.len() / 2;
            bytes[index] = if bytes[index] == b'a' { b'b' } else { b'a' };
            let len = bytes.len();
            fs::write(&target, bytes)?;
            let file = fs::File::options().write(true).open(&target)?;
            file.set_times(FileTimes::new().set_modified(modified))?;
            debug_assert_eq!(fs::metadata(&target)?.len(), len as u64);
        }
        "rename" => {
            let renamed = target.with_file_name("Renamed.cs");
            fs::rename(&target, renamed)?;
        }
        "delete" => {
            fs::remove_file(&target)?;
        }
        other => panic!("unknown change {other}"),
    }
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
    child: Child,
    stop_file: PathBuf,
    stats_file: PathBuf,
}

impl MockEmbeddingServer {
    /// Runs the mock provider in a child process so its CPU is attributable
    /// separately from the engine under measurement.
    fn start() -> std::io::Result<Self> {
        let base = std::env::temp_dir().join(format!(
            "zg-mock-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|duration| duration.as_nanos())
                .unwrap_or_default()
        ));
        fs::create_dir_all(&base)?;
        let port_file = base.join("port");
        let stop_file = base.join("stop");
        let stats_file = base.join("stats.json");
        let child = Command::new(std::env::current_exe()?)
            .arg("--mock-server")
            .arg("--port-file")
            .arg(&port_file)
            .arg("--stop-file")
            .arg(&stop_file)
            .arg("--stats-file")
            .arg(&stats_file)
            .stdout(Stdio::null())
            .stderr(Stdio::inherit())
            .spawn()?;
        let deadline = Instant::now() + Duration::from_secs(10);
        let address = loop {
            if let Ok(text) = fs::read_to_string(&port_file)
                && let Ok(address) = text.trim().parse::<SocketAddr>()
            {
                break address;
            }
            if Instant::now() > deadline {
                let mut child = child;
                let _ = child.kill();
                let _ = child.wait();
                return Err(std::io::Error::other(
                    "mock embedding server did not report a port",
                ));
            }
            thread::sleep(Duration::from_millis(10));
        };
        Ok(Self {
            address,
            child,
            stop_file,
            stats_file,
        })
    }

    fn finish(&mut self) -> Value {
        let cpu = proc_cpu(self.child.id());
        let _ = fs::write(&self.stop_file, b"stop");
        // Wake the blocked accept loop, then let the child publish its counters.
        let _ = TcpStream::connect(self.address);
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            match self.child.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
                _ => {
                    let _ = self.child.kill();
                    let _ = self.child.wait();
                    break;
                }
            }
        }
        let stats: Value = fs::read(&self.stats_file)
            .ok()
            .and_then(|bytes| serde_json::from_slice(&bytes).ok())
            .unwrap_or(Value::Null);
        json!({ "model": "separate_process", "stats": stats, "cpu": cpu })
    }
}

/// `(user_ms, sys_ms, rss_kib)` from `/proc/<pid>/stat`.
fn proc_cpu(pid: u32) -> Value {
    let Ok(stat) = fs::read_to_string(format!("/proc/{pid}/stat")) else {
        return Value::Null;
    };
    let Some(rest) = stat.rsplit_once(')').map(|(_, rest)| rest) else {
        return Value::Null;
    };
    let fields = rest.split_whitespace().collect::<Vec<_>>();
    let ticks = |index: usize| {
        fields
            .get(index)
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(0)
    };
    // Fields 14/15 are utime/stime after the `comm` field; RSS is field 24.
    let user = ticks(11);
    let sys = ticks(12);
    let rss_pages = ticks(21);
    json!({
        "pid": pid,
        "user_ms": user.saturating_mul(1000) / 100,
        "sys_ms": sys.saturating_mul(1000) / 100,
        "rss_kib": rss_pages.saturating_mul(4),
    })
}

fn run_mock_server(args: &Args) -> Result<(), Box<dyn std::error::Error>> {
    let port_file = args.port_file.clone().expect("--port-file");
    let stop_file = args.stop_file.clone().expect("--stop-file");
    let stats_file = args.stats_file.clone().expect("--stats-file");
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let address = listener.local_addr()?;
    fs::write(&port_file, address.to_string())?;
    let mut requests = 0_u64;
    let mut inputs = 0_u64;
    let mut wall_us = 0_u128;
    loop {
        let Ok((stream, _)) = listener.accept() else {
            break;
        };
        if stop_file.exists() {
            break;
        }
        let started = Instant::now();
        let served = respond(stream)?;
        wall_us += started.elapsed().as_micros();
        requests += served.0;
        inputs += served.1;
    }
    let stats = json!({
        "requests": requests,
        "inputs": inputs,
        "wall_us": u64::try_from(wall_us).unwrap_or(u64::MAX),
    });
    fs::write(&stats_file, serde_json::to_vec(&stats)?)?;
    Ok(())
}

fn respond(mut stream: TcpStream) -> std::io::Result<(u64, u64)> {
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
    stream.write_all(&response)?;
    Ok((1, items.len() as u64))
}
