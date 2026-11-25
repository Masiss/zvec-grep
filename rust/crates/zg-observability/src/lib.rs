//! Local-first observability events for zvec-grep.
//!
//! The recorder is opt-in: it only writes when `ZVEC_GREP_OBSERVABILITY=1` or
//! `ZVEC_GREP_OBSERVABILITY_MODE` is present. Emission never changes retrieval,
//! ranking, indexing, or daemon behavior. Events are bounded JSONL under the
//! runtime home; raw sensitive content is never captured by default.

use std::{
    collections::BTreeMap,
    error::Error,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{Mutex, OnceLock},
    time::{SystemTime, UNIX_EPOCH},
};

use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

mod resource;

pub const SCHEMA_VERSION: u32 = 1;
pub const CAPABILITY_VERSION: u32 = 1;
const MAX_BYTES: u64 = 10 * 1024 * 1024;
const MAX_FILES: usize = 5;
const MAX_AGE_SECS: u64 = 14 * 24 * 60 * 60;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Mode {
    Runtime,
    Dogfood,
    Research,
}

impl Mode {
    fn parse(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "runtime" => Some(Self::Runtime),
            "dogfood" => Some(Self::Dogfood),
            "research" => Some(Self::Research),
            _ => None,
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::Runtime => "runtime",
            Self::Dogfood => "dogfood",
            Self::Research => "research",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Capture {
    Metadata,
    Redacted,
    Full,
}

impl Capture {
    fn parse(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "metadata" => Some(Self::Metadata),
            "redacted" => Some(Self::Redacted),
            "full" => Some(Self::Full),
            _ => None,
        }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct Ids {
    pub trace_id: Option<String>,
    pub request_id: Option<String>,
    pub session_id: Option<String>,
    pub turn_id: Option<String>,
    pub tool_call_id: Option<String>,
    pub job_id: Option<String>,
}

impl Ids {
    #[must_use]
    pub fn from_env() -> Self {
        Self {
            trace_id: env("ZVEC_GREP_TRACE_ID"),
            request_id: env("ZVEC_GREP_REQUEST_ID"),
            session_id: env("ZVEC_GREP_SESSION_ID"),
            turn_id: env("ZVEC_GREP_TURN_ID"),
            tool_call_id: env("ZVEC_GREP_TOOL_CALL_ID"),
            job_id: None,
        }
    }

    fn as_value(&self) -> Value {
        let mut map = Map::new();
        insert_opt(&mut map, "trace_id", self.trace_id.as_deref());
        insert_opt(&mut map, "request_id", self.request_id.as_deref());
        insert_opt(&mut map, "session_id", self.session_id.as_deref());
        insert_opt(&mut map, "turn_id", self.turn_id.as_deref());
        insert_opt(&mut map, "tool_call_id", self.tool_call_id.as_deref());
        insert_opt(&mut map, "job_id", self.job_id.as_deref());
        Value::Object(map)
    }

    pub fn trace_or_generated(&self) -> String {
        self.trace_id.clone().unwrap_or_else(new_id)
    }
}

fn env(name: &str) -> Option<String> {
    std::env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty() && value.len() <= 512)
}

/// Explicit configuration advertised by the daemon and required from clients.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Config {
    pub capability: u32,
    pub enabled: bool,
    pub mode: String,
    pub capture: String,
    pub home: String,
}

#[derive(Debug)]
struct Recorder {
    config: Config,
    home: PathBuf,
    mode: Mode,
    capture: Capture,
    ids: Ids,
}

static ACTIVE: OnceLock<Recorder> = OnceLock::new();
static WRITE_LOCK: Mutex<()> = Mutex::new(());
static SEQUENCE: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

fn parse_config(
    enabled_raw: Option<&str>,
    mode_raw: Option<String>,
    capture_raw: Option<String>,
    home_raw: Option<String>,
    runtime_home: &Path,
) -> Result<Option<(Config, Mode, Capture, PathBuf)>, String> {
    let enabled = enabled_raw.is_some_and(|v| v == "1" || v.eq_ignore_ascii_case("true"));
    if !enabled && mode_raw.is_none() && capture_raw.is_none() && home_raw.is_none() {
        return Ok(None);
    }
    if !enabled {
        return Err(
            "ZVEC_GREP_OBSERVABILITY=1 is required when observability variables are set".to_owned(),
        );
    }
    let mode_raw = mode_raw.unwrap_or_else(|| "runtime".to_owned());
    let mode = Mode::parse(&mode_raw).ok_or_else(|| {
        format!("invalid ZVEC_GREP_OBSERVABILITY_MODE '{mode_raw}'; expected runtime, dogfood, or research")
    })?;
    let capture_raw = capture_raw.unwrap_or_else(|| "metadata".to_owned());
    let capture = Capture::parse(&capture_raw).ok_or_else(|| {
        format!("invalid ZVEC_GREP_OBSERVABILITY_CAPTURE '{capture_raw}'; expected metadata, redacted, or full")
    })?;
    let obs_home = home_raw.map_or_else(|| runtime_home.join("observability"), PathBuf::from);
    let obs_home = if obs_home.is_absolute() {
        obs_home
    } else {
        std::env::current_dir()
            .map_err(|error| format!("cannot resolve observability home: {error}"))?
            .join(obs_home)
    };
    let config = Config {
        capability: CAPABILITY_VERSION,
        enabled: true,
        mode: mode.as_str().to_owned(),
        capture: match capture {
            Capture::Metadata => "metadata",
            Capture::Redacted => "redacted",
            Capture::Full => "full",
        }
        .to_owned(),
        home: obs_home.to_string_lossy().into_owned(),
    };
    Ok(Some((config, mode, capture, obs_home)))
}

/// Pure configuration resolver shared by CLI, daemon status and tests.
///
/// # Errors
/// Returns the invalid variable name and expected values when configuration
/// is partial or malformed.
pub fn config_from_env(runtime_home: &Path) -> Result<Option<Config>, String> {
    Ok(parse_config(
        env("ZVEC_GREP_OBSERVABILITY").as_deref(),
        env("ZVEC_GREP_OBSERVABILITY_MODE"),
        env("ZVEC_GREP_OBSERVABILITY_CAPTURE"),
        env("ZVEC_GREP_OBSERVABILITY_HOME"),
        runtime_home,
    )?
    .map(|(config, _, _, _)| config))
}

/// Initialize from environment. Invalid configuration is a hard error.
///
/// # Errors
/// Returns configuration or filesystem errors; callers must fail closed.
pub fn init(home: &Path) -> Result<bool, Box<dyn Error>> {
    let Some((config, mode, capture, obs_home)) = parse_config(
        env("ZVEC_GREP_OBSERVABILITY").as_deref(),
        env("ZVEC_GREP_OBSERVABILITY_MODE"),
        env("ZVEC_GREP_OBSERVABILITY_CAPTURE"),
        env("ZVEC_GREP_OBSERVABILITY_HOME"),
        home,
    )?
    else {
        return Ok(false);
    };
    #[cfg(unix)]
    let created_home = !obs_home.exists();
    fs::create_dir_all(&obs_home)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        // Only harden directories we created; never chmod a pre-existing path.
        if created_home {
            let _ = fs::set_permissions(&obs_home, fs::Permissions::from_mode(0o700));
        }
    }
    let recorder = Recorder {
        config,
        home: obs_home,
        mode,
        capture,
        ids: Ids::from_env(),
    };
    let _ = ACTIVE.set(recorder);
    Ok(true)
}

pub fn enabled() -> bool {
    ACTIVE.get().is_some()
}

#[must_use]
pub fn config() -> Option<Config> {
    ACTIVE.get().map(|recorder| recorder.config.clone())
}

pub fn mode() -> Option<&'static str> {
    ACTIVE.get().map(|r| r.mode.as_str())
}

pub fn current_ids() -> Ids {
    ACTIVE.get().map(|r| r.ids.clone()).unwrap_or_default()
}

#[must_use]
pub fn stream_allowed(mode: Mode, stream: &str) -> bool {
    match mode {
        Mode::Runtime => stream == "runtime",
        Mode::Dogfood => stream != "research",
        Mode::Research => true,
    }
}

/// Mode is about lineage breadth; capture is about field value sensitivity.
pub fn apply_mode_fields(stream: &str, mode: Mode, fields: &mut Value) {
    if stream == "retrieval"
        && mode != Mode::Research
        && let Some(object) = fields.as_object_mut()
    {
        for key in [
            "groups",
            "routes",
            "acquisition",
            "fusion",
            "ranking",
            "selection",
            "group_results",
            "timings",
        ] {
            object.remove(key);
        }
    }
}

/// Emit a structured event. Streams are bounded by size, count, and age.
pub fn emit(stream: &str, event: &str, fields: Value) {
    let Some(recorder) = ACTIVE.get() else { return };
    if !matches!(
        stream,
        "runtime" | "retrieval" | "investigation" | "research"
    ) {
        return;
    }
    if !stream_allowed(recorder.mode, stream) {
        return;
    }
    let mut fields = sanitize(fields, recorder.capture, None);
    apply_mode_fields(stream, recorder.mode, &mut fields);
    let record = json!({
        "schema_version": SCHEMA_VERSION,
        "ts": Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
        "mode": recorder.mode.as_str(),
        "capture": match recorder.capture {
            Capture::Metadata => "metadata",
            Capture::Redacted => "redacted",
            Capture::Full => "full",
        },
        "event": event,
        "seq": SEQUENCE.fetch_add(1, std::sync::atomic::Ordering::Relaxed),
        "ids": recorder.ids.as_value(),
        "fields": fields,
    });
    let _ = write_record(&recorder.home, stream, &record.to_string());
}

fn write_record(home: &Path, stream: &str, line: &str) -> Result<(), Box<dyn Error>> {
    let _guard = WRITE_LOCK
        .lock()
        .map_err(|_| "observability lock poisoned")?;
    let dir = home.join(stream);
    #[cfg(unix)]
    let created_dir = !dir.exists();
    fs::create_dir_all(&dir)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if created_dir {
            let _ = fs::set_permissions(&dir, fs::Permissions::from_mode(0o700));
        }
    }
    let path = dir.join(format!("{}.jsonl", Utc::now().format("%Y-%m-%d")));
    if let Ok(metadata) = fs::metadata(&path)
        && metadata.len() + line.len() as u64 > MAX_BYTES
    {
        rotate(&dir, &path);
    }
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&path)?;
    writeln!(file, "{line}")?;
    prune(&dir)?;
    Ok(())
}

fn rotate(dir: &Path, active: &Path) {
    if active.exists() {
        for index in (1..=MAX_FILES).rev() {
            let from = if index == 1 {
                active.to_path_buf()
            } else {
                numbered(dir, index - 1)
            };
            let to = numbered(dir, index);
            if from.exists() {
                let _ = fs::remove_file(&to);
                let _ = fs::rename(&from, &to);
            }
        }
    }
}

fn numbered(dir: &Path, index: usize) -> PathBuf {
    let stem = format!("{}.jsonl", Utc::now().format("%Y-%m-%d"));
    dir.join(format!("{stem}.{index}"))
}

fn prune(dir: &Path) -> Result<(), Box<dyn Error>> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let mut files = Vec::new();
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().into_owned();
        let lower = name.to_ascii_lowercase();
        let jsonl = Path::new(&name)
            .extension()
            .is_some_and(|extension| extension.eq_ignore_ascii_case("jsonl"));
        if !jsonl && !lower.contains(".jsonl.") {
            continue;
        }
        let modified = entry
            .metadata()
            .and_then(|m| m.modified())
            .unwrap_or(UNIX_EPOCH)
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        if now.saturating_sub(modified) > MAX_AGE_SECS {
            let _ = fs::remove_file(entry.path());
            continue;
        }
        files.push((modified, entry.path()));
    }
    files.sort_by_key(|(modified, _)| *modified);
    while files.len() > MAX_FILES + 1 {
        let (_, path) = files.remove(0);
        let _ = fs::remove_file(path);
    }
    Ok(())
}

fn sanitize(value: Value, capture: Capture, parent_key: Option<&str>) -> Value {
    match value {
        Value::Object(map) => {
            let mut out = Map::new();
            for (key, value) in map {
                // Raw route/prompt queries must never be stored, even in full
                // capture; research lineage keeps only their digest.
                if key.eq_ignore_ascii_case("thinking") {
                    continue;
                }
                if ["query", "query_text", "raw_query"]
                    .iter()
                    .any(|candidate| key.eq_ignore_ascii_case(candidate))
                {
                    let digest = match &value {
                        Value::String(text) => format!("sha256:{}", short_hash(text)),
                        _ => "sha256:unavailable".to_owned(),
                    };
                    out.insert(key, Value::String(digest));
                    continue;
                }
                if is_secret_key(&key) && capture != Capture::Full {
                    continue;
                }
                if capture == Capture::Metadata
                    && is_free_text_key(&key)
                    && matches!(value, Value::String(_))
                {
                    continue;
                }
                out.insert(key.clone(), sanitize(value, capture, Some(&key)));
            }
            Value::Object(out)
        }
        Value::Array(values) => Value::Array(
            values
                .into_iter()
                .map(|value| sanitize(value, capture, parent_key))
                .collect(),
        ),
        Value::String(text) => {
            // Redacted mode keeps shape and length but replaces free text with a
            // digest; short enum-like fields remain readable for diagnosis.
            if capture == Capture::Redacted
                && !parent_key.is_some_and(|key| is_enum_like_key(key) || is_opaque_key(key))
                && !text.is_empty()
            {
                return Value::String(format!("sha256:{}", short_hash(&text)));
            }
            if text.len() > 512 {
                let digest = hash(&text);
                let mut end = 512;
                while !text.is_char_boundary(end) {
                    end -= 1;
                }
                Value::String(format!("{}…sha256:{}", &text[..end], &digest[..16]))
            } else {
                Value::String(text)
            }
        }
        other => other,
    }
}

fn is_opaque_key(key: &str) -> bool {
    let lower = key.to_ascii_lowercase();
    lower.ends_with("_id")
        || lower.ends_with("_sha256")
        || lower.ends_with("_hash")
        || lower.ends_with("_generation")
        || lower == "error_code"
        || lower == "evidence_ids"
}

fn is_free_text_key(key: &str) -> bool {
    [
        "detail",
        "progress_detail",
        "message",
        "hint",
        "output",
        "args",
        "text",
    ]
    .contains(&key.to_ascii_lowercase().as_str())
}

fn is_enum_like_key(key: &str) -> bool {
    [
        "mode",
        "capture",
        "event",
        "state",
        "reason",
        "source",
        "coverage",
        "freshness",
        "refresh",
        "refresh_policy",
        "matched_by",
        "selection_reason",
        "phase",
        "outcome",
        "sample_kind",
        "comm",
        "join_kind",
        "verdict",
    ]
    .contains(&key.to_ascii_lowercase().as_str())
}

fn is_secret_key(key: &str) -> bool {
    let lower = key.to_ascii_lowercase();
    let broad = [
        "token",
        "api_key",
        "apikey",
        "authorization",
        "password",
        "secret",
    ];
    let exact = [
        "query",
        "query_text",
        "raw_query",
        "prompt",
        "thinking",
        "answer_text",
    ];
    broad.iter().any(|needle| lower.contains(needle)) || exact.contains(&lower.as_str())
}

fn insert_opt(map: &mut Map<String, Value>, key: &str, value: Option<&str>) {
    if let Some(value) = value {
        map.insert(key.to_owned(), Value::String(value.to_owned()));
    }
}

/// Stable pseudonymous hash for local correlation without storing raw text.
#[must_use]
pub fn new_id() -> String {
    uuid::Uuid::new_v4().to_string()
}

#[must_use]
pub fn hash(value: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(value.as_bytes());
    let hex = b"0123456789abcdef";
    let mut out = String::with_capacity(64);
    for byte in hasher.finalize() {
        out.push(hex[(byte >> 4) as usize] as char);
        out.push(hex[(byte & 0x0f) as usize] as char);
    }
    out
}

#[must_use]
pub fn short_hash(value: &str) -> String {
    hash(value)[..16].to_owned()
}

#[must_use]
pub fn source_identity(root: &Path) -> String {
    short_hash(&root.to_string_lossy())
}

#[must_use]
pub fn evidence_id(
    root_id: &str,
    relative_path: &str,
    range: &str,
    content: &str,
    generation: &str,
) -> String {
    short_hash(&format!(
        "{root_id}\0{relative_path}\0{range}\0{}\0{generation}",
        hash(content)
    ))
}

/// Prefer the manifest storage generation; fall back to a stable path hash.
pub fn index_generation(root: &Path) -> String {
    let manifest = root.join(".zvec-grep").join("manifest.json");
    if let Ok(content) = fs::read_to_string(manifest)
        && let Ok(value) = serde_json::from_str::<Value>(&content)
        && let Some(generation) = value.get("storageGeneration").and_then(Value::as_str)
    {
        return generation.to_owned();
    }
    short_hash(&root.join(".zvec-grep").join("index").to_string_lossy())
}

/// Prune all local streams; called by `zg --observability`.
///
/// # Errors
/// Returns directory read or removal errors.
pub fn maintain(home: &Path) -> Result<(), Box<dyn Error>> {
    let _guard = WRITE_LOCK
        .lock()
        .map_err(|_| "observability lock poisoned")?;
    for stream in ["runtime", "retrieval", "investigation", "research"] {
        let dir = home.join(stream);
        if dir.is_dir() {
            prune(&dir)?;
        }
    }
    Ok(())
}

/// Cheap resource snapshot at phase boundaries; never sampled per file/token.
#[must_use]
pub fn resource_sample() -> Option<Value> {
    resource::sample()
}

/// Summarize local event files without loading them into memory twice.
///
/// # Errors
/// Returns filesystem errors from reading local event streams.
pub fn summary(home: &Path) -> Result<Value, Box<dyn Error>> {
    let mut streams = Map::new();
    for stream in ["runtime", "retrieval", "investigation", "research"] {
        let dir = home.join(stream);
        let mut events = BTreeMap::new();
        let mut total = 0usize;
        let mut files = 0usize;
        if let Ok(entries) = fs::read_dir(&dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if !path.is_file() || !path.to_string_lossy().contains(".jsonl") {
                    continue;
                }
                files += 1;
                let content = fs::read_to_string(&path).unwrap_or_default();
                for line in content.lines() {
                    let Ok(value) = serde_json::from_str::<Value>(line) else {
                        continue;
                    };
                    let Some(name) = value.get("event").and_then(Value::as_str) else {
                        continue;
                    };
                    *events.entry(name.to_owned()).or_insert(0usize) += 1;
                    total += 1;
                }
            }
        }
        streams.insert(
            stream.to_owned(),
            json!({"files": files, "events": total, "by_event": events}),
        );
    }
    Ok(Value::Object(streams))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hashes_are_stable_and_shortened() {
        assert_eq!(short_hash("abc"), short_hash("abc"));
        assert_ne!(short_hash("abc"), short_hash("abd"));
        assert_eq!(short_hash("abc").len(), 16);
    }

    #[test]
    fn secret_keys_are_dropped_in_metadata_mode() {
        let value =
            json!({"query": "needle", "token": "secret", "nested": {"api_key": "x", "count": 3}});
        let safe = sanitize(value, Capture::Metadata, None);
        assert!(safe.get("token").is_none());
        assert!(safe["nested"].get("api_key").is_none());
        assert_eq!(safe["nested"]["count"], 3);
    }

    #[test]
    fn config_rejects_partial_environment() {
        let result = parse_config(
            None,
            Some("research".into()),
            None,
            None,
            Path::new("/tmp/home"),
        );
        assert!(result.is_err());
        let result = parse_config(
            Some("1"),
            Some("bogus".into()),
            None,
            None,
            Path::new("/tmp/home"),
        );
        assert!(result.is_err());
    }

    #[test]
    fn config_is_explicit_and_comparable() {
        let (config, mode, capture, home) = parse_config(
            Some("1"),
            Some("dogfood".into()),
            None,
            Some("/tmp/events".into()),
            Path::new("/tmp/home"),
        )
        .expect("config")
        .expect("enabled");
        assert_eq!(config.mode, "dogfood");
        assert_eq!(config.capture, "metadata");
        assert_eq!(config.capability, CAPABILITY_VERSION);
        assert_eq!(mode, Mode::Dogfood);
        assert_eq!(capture, Capture::Metadata);
        assert_eq!(home, PathBuf::from("/tmp/events"));
    }

    #[test]
    fn mode_and_capture_coverage_matrix() {
        let modes = [Mode::Runtime, Mode::Dogfood, Mode::Research];
        let captures = [Capture::Metadata, Capture::Redacted, Capture::Full];
        for mode in modes {
            for capture in captures {
                assert!(stream_allowed(mode, "runtime"));
                assert_eq!(stream_allowed(mode, "retrieval"), mode != Mode::Runtime);
                assert_eq!(stream_allowed(mode, "research"), mode == Mode::Research);
                let mut fields = json!({
                    "trace_id": "trace-1",
                    "request_id": "request-1",
                    "query_sha256": "qhash",
                    "index_generation": "gen-1",
                    "query": "secret query",
                    "query_text": "secret query",
                    "answer_text": "secret answer",
                    "thinking": "secret thinking",
                    "progress_detail": "/home/masis/src/zvec-grep",
                    "ranking": [{"rank": 1}],
                    "group_results": [{"id": "Q1"}],
                    "timings": [{"name": "recall"}],
                });
                apply_mode_fields("retrieval", mode, &mut fields);
                let safe = sanitize(fields, capture, None);
                let raw = serde_json::to_string(&safe).unwrap_or_default();
                assert!(!raw.contains("secret query"), "query never stored raw");
                assert_eq!(safe["trace_id"], "trace-1", "trace survives capture");
                assert_eq!(safe["request_id"], "request-1", "request survives capture");
                assert_eq!(safe["query_sha256"], "qhash", "query hash survives capture");
                assert_eq!(
                    safe["index_generation"], "gen-1",
                    "generation survives capture"
                );
                assert!(!raw.contains("secret thinking"), "thinking never stored");
                if mode == Mode::Research {
                    assert!(safe.get("ranking").is_some());
                    assert!(safe.get("group_results").is_some());
                    assert!(safe.get("timings").is_some());
                } else {
                    assert!(safe.get("ranking").is_none());
                    assert!(safe.get("group_results").is_none());
                    assert!(safe.get("timings").is_none());
                }
                if capture == Capture::Metadata {
                    assert!(safe.get("answer_text").is_none());
                    assert!(!raw.contains("/home/masis/src/zvec-grep") || capture == Capture::Full);
                }
                if capture == Capture::Redacted {
                    assert!(!raw.contains("/home/masis/src/zvec-grep"));
                    assert!(raw.contains("sha256:"));
                }
                if capture == Capture::Full {
                    assert_eq!(safe["answer_text"], "secret answer");
                }
            }
        }
    }

    #[test]
    fn evidence_identity_separates_span_and_index() {
        let root = source_identity(Path::new("/workspace"));
        let a = evidence_id(&root, "src/a.rs", "1-10", "fn a() {}", "gen-1");
        let b = evidence_id(&root, "src/a.rs", "1-10", "fn a() {}", "gen-2");
        assert_ne!(a, b);
    }
}
