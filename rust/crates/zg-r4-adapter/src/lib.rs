//! Private experimental R4 external-agent adapter.
//!
//! This crate is the smallest integration that lets a real external agent use
//! existing zvec-grep capabilities (hybrid/FTS/lexical search, exact symbols,
//! R2/R3 typed facts, R1 hash-checked evidence) interactively under the frozen
//! R0 cumulative session budget. It is experimental benchmark infrastructure,
//! not a public engine contract: do not expose it through the default MCP
//! profile, public CLI or daemon.
//!
//! Session model:
//! - `session new` pins the corpus root, profile and case and creates an
//!   authoritative ledger (`ledger.json`, atomically replaced) plus an
//!   append-only trace (`trace.jsonl`). No command resets the ledger.
//! - Every investigation tool call consumes one call slot; the exact
//!   serialized envelope bytes count toward the 64 KiB cumulative
//!   delivered-content budget. Errors, gaps, metadata and wrappers are
//!   accounted as delivered.
//! - Meta operations (`status`, `receipt`) consume no call slot but their
//!   serialized output still counts as delivered bytes.
//! - A "round" is declared by the caller (`--round`, 1..=max, non-decreasing).
//!   The frozen R0 contract defines rounds for engine-internal workflow
//!   stages; for an interactive session each round is one agent investigation
//!   iteration. This mapping is an interpretation and is reported as such,
//!   not a relaxation: rounds never exceed the frozen maximum.
//! - `finalize` (or `cancel`) makes the session terminal; terminal sessions
//!   refuse further investigation calls without consuming slots.
//!
//! Profiles:
//! - `arm-a`: ordinary bounded search only (the product control).
//! - `arm-b`: engine-managed deterministic R4 workflow (baseline32 budget),
//!   plus evidence revalidation; internal operations are reported through
//!   workflow statistics, never hidden.
//! - `arm-c`: search + exact symbols + typed facts + hash-checked evidence +
//!   verification. The agent chooses the investigation.

use std::fs;
use std::io::Write as _;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use std::os::unix::io::AsRawFd as _;

use zg_engine::api::context::ContextOptions;
use zg_engine::api::context::options::{ContextRoute, ContextRouteMode, RefreshPolicy};
use zg_engine::api::evidence::surface::{self, SpanRequest};
use zg_engine::api::evidence::EvidenceBudget;
use zg_engine::api::symbols::SymbolsOptions;
use zg_engine::rag::workflow::{self, WorkflowBudget, WorkflowOutcome};
use zg_engine::ZvecGrep;

pub mod facts;
pub mod source;

/// Frozen R0 session budget. Non-negotiable.
pub const MAX_CALLS: usize = 6;
pub const MAX_ROUNDS: usize = 3;
pub const MAX_DISTINCT_FILES: usize = 32;
pub const MAX_DELIVERED_BYTES: usize = 64 * 1024;
pub const MAX_PACK_BYTES: usize = 24 * 1024;
pub const MAX_PACK_ITEMS: usize = 10;

pub const LEDGER_FILE: &str = "ledger.json";
pub const TRACE_FILE: &str = "trace.jsonl";
pub const PACKS_DIR: &str = "packs";
pub const RECEIPT_FILE: &str = "receipt.json";
pub const LOCK_FILE: &str = "session.lock";
/// Fail-closed bound on waiting for the session lock.
pub const LOCK_TIMEOUT_S: u64 = 120;
pub const SCHEMA_VERSION: u32 = 1;

/// Default excluded-variant path markers. These are benchmark scope
/// configuration carried from the frozen `scope-manifest.json`, not engine
/// behavior; a session can override them with `Session::new_configured`.
pub const DEFAULT_EXCLUSIONS: &[&str] = &["_old", "_old1", "newsolution"];

/// Adapter-level gap codes (budget containment), reported alongside engine
/// gaps. Never silent truncation.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AdapterGapCode {
    /// Delivering content from one more distinct file would exceed the file budget.
    FileBudgetExceeded,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Profile {
    ArmA,
    ArmB,
    ArmC,
}

impl Profile {
    #[must_use]
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "arm-a" => Some(Self::ArmA),
            "arm-b" => Some(Self::ArmB),
            "arm-c" => Some(Self::ArmC),
            _ => None,
        }
    }

    #[must_use]
    pub fn allows(self, tool: Tool) -> bool {
        match self {
            Self::ArmA => matches!(tool, Tool::Search | Tool::Status),
            Self::ArmB => matches!(tool, Tool::Workflow | Tool::Verify | Tool::Status),
            Self::ArmC => matches!(
                tool,
                Tool::Search
                    | Tool::Symbol
                    | Tool::Facts
                    | Tool::Evidence
                    | Tool::Verify
                    | Tool::Status
            ),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Tool {
    Search,
    Symbol,
    Facts,
    Evidence,
    Verify,
    Workflow,
    Status,
}

impl Tool {
    #[must_use]
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "search" => Some(Self::Search),
            "symbol" => Some(Self::Symbol),
            "facts" => Some(Self::Facts),
            "evidence" => Some(Self::Evidence),
            "verify" => Some(Self::Verify),
            "workflow" => Some(Self::Workflow),
            "status" => Some(Self::Status),
            _ => None,
        }
    }

    /// Whether the call consumes an investigation call slot. Meta and
    /// verification operations (`status`, `verify`) do not: the R0 protocol
    /// requires a final verification to stay possible within the frozen
    /// investigation budget, and neither operation delivers source content
    /// beyond its own small envelope, which is still byte-accounted.
    #[must_use]
    pub const fn consumes_slot(self) -> bool {
        !matches!(self, Tool::Status | Tool::Verify)
    }
}

/// One history entry per call attempt (admitted or gate-rejected).
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct CallRecord {
    pub call: usize,
    pub tool: String,
    pub round: usize,
    pub ok: bool,
    pub wall_ms: f64,
    pub delivered_bytes: usize,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct PackRecord {
    pub id: usize,
    pub sha256: String,
    pub items: usize,
    pub bytes: usize,
    pub verified: bool,
    /// `evidence` packs can be revalidated; `workflow` packs are the
    /// engine-managed outcome and are already hash-checked at selection.
    pub kind: String,
}

/// The authoritative cumulative ledger. Written atomically after every call.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Ledger {
    pub schema_version: u32,
    pub session_id: String,
    pub case: String,
    pub profile: String,
    pub root: PathBuf,
    pub created_unix: u64,
    /// `None` until finalize/cancel.
    pub terminal: Option<String>,
    pub calls_used: usize,
    pub rounds_used: usize,
    pub last_round: usize,
    pub distinct_files: Vec<String>,
    pub delivered_bytes: usize,
    pub packs: Vec<PackRecord>,
    pub history: Vec<CallRecord>,
    /// Excluded-variant path markers (benchmark scope configuration).
    pub exclusions: Vec<String>,
}

/// Gate-level rejections: no call slot is consumed because the session state
/// itself rejects the operation. The error envelope still counts as delivered
/// bytes.
#[derive(Debug)]
pub enum GateError {
    Terminal(String),
    NoCallsLeft,
    ProfileForbidden,
    InvalidRound(String),
}

#[derive(Debug)]
pub enum CallError {
    Gate(GateError),
    /// The call was admitted and consumed a slot; execution failed.
    Execution { code: String, message: String },
}

impl CallError {
    #[must_use]
    pub fn code(&self) -> &str {
        match self {
            Self::Gate(GateError::Terminal(_)) => "terminal",
            Self::Gate(GateError::NoCallsLeft) => "no_calls_left",
            Self::Gate(GateError::ProfileForbidden) => "profile_forbidden",
            Self::Gate(GateError::InvalidRound(_)) => "invalid_round",
            Self::Execution { code, .. } => code,
        }
    }

    #[must_use]
    pub fn message(&self) -> String {
        match self {
            Self::Gate(GateError::Terminal(reason)) => {
                format!("session is terminal: {reason}")
            }
            Self::Gate(GateError::NoCallsLeft) => "call budget exhausted".to_owned(),
            Self::Gate(GateError::ProfileForbidden) => {
                "tool is not allowed for this session profile".to_owned()
            }
            Self::Gate(GateError::InvalidRound(message)) => {
                format!("invalid round: {message}")
            }
            Self::Execution { message, .. } => message.clone(),
        }
    }
}

/// Input for one call.
#[derive(Debug)]
pub struct CallInput {
    pub tool: Tool,
    /// 1..=MAX_ROUNDS, non-decreasing across the session.
    pub round: usize,
    pub query: Option<String>,
    pub mode: Option<String>,
    pub limit: Option<usize>,
    pub scope: Option<String>,
    pub name: Option<String>,
    pub path: Option<String>,
    /// Optional method filter for the facts tool.
    pub method: Option<String>,
    /// Evidence span requests, already parsed.
    pub requests: Vec<SpanRequest>,
    /// Pack id for verify; `None` verifies the latest pack.
    pub pack_id: Option<usize>,
    /// Question for the arm-b workflow tool.
    pub question: Option<String>,
}

/// Result of executing one tool: the serde value placed in the envelope and
/// the ordered distinct files whose content this call delivers.
pub struct ToolOutput {
    pub result: Value,
    pub files: Vec<String>,
}

pub struct Session {
    pub dir: PathBuf,
    pub ledger: Ledger,
}

/// RAII guard for the session-wide `flock`. Dropping releases the lock.
struct SessionLock {
    #[allow(dead_code)]
    file: fs::File,
}

impl Session {
    /// Creates a new session directory with a fresh ledger.
    ///
    /// # Errors
    ///
    /// Fails when the directory exists and is non-empty, the root is not a
    /// readable directory, or IO fails.
    pub fn new(dir: &Path, root: &Path, profile: Profile, case: &str) -> Result<Self, String> {
        if dir.exists()
            && fs::read_dir(dir)
                .map_err(|e| e.to_string())?
                .next()
                .is_some()
        {
            return Err(format!(
                "session directory is not empty: {}",
                dir.display()
            ));
        }
        let root = root
            .canonicalize()
            .map_err(|error| format!("root is not a readable directory: {error}"))?;
        if !root.is_dir() {
            return Err(format!("root is not a directory: {}", root.display()));
        }
        fs::create_dir_all(dir).map_err(|error| error.to_string())?;
        let session_id = hex(&Sha256::digest(
            format!("{:?}-{:?}-{case}", std::time::SystemTime::now(), root).as_bytes(),
        ))[..16]
            .to_owned();
        let exclusions: Vec<String> = DEFAULT_EXCLUSIONS.iter().map(|m| (*m).to_owned()).collect();
        let ledger = Self::fresh_ledger(session_id, profile, case, root, exclusions);
        let session = Self {
            dir: dir.to_path_buf(),
            ledger,
        };
        session.save_ledger()?;
        Ok(session)
    }

    /// Creates a session with explicit excluded-variant markers (benchmark
    /// scope configuration).
    ///
    /// # Errors
    ///
    /// Same failure conditions as [`Session::new`].
    pub fn new_configured(
        dir: &Path,
        root: &Path,
        profile: Profile,
        case: &str,
        exclusions: Vec<String>,
    ) -> Result<Self, String> {
        let mut session = Self::new(dir, root, profile, case)?;
        session.ledger.exclusions = exclusions;
        session.save_ledger()?;
        Ok(session)
    }

    #[allow(clippy::too_many_arguments)]
    fn fresh_ledger(
        session_id: String,
        profile: Profile,
        case: &str,
        root: PathBuf,
        exclusions: Vec<String>,
    ) -> Ledger {
        Ledger {
            schema_version: SCHEMA_VERSION,
            session_id,
            case: case.to_owned(),
            profile: match profile {
                Profile::ArmA => "arm-a".to_owned(),
                Profile::ArmB => "arm-b".to_owned(),
                Profile::ArmC => "arm-c".to_owned(),
            },
            root,
            created_unix: unix_now(),
            terminal: None,
            calls_used: 0,
            rounds_used: 0,
            last_round: 0,
            distinct_files: Vec::new(),
            delivered_bytes: 0,
            packs: Vec::new(),
            history: Vec::new(),
            exclusions,
        }
    }

    /// Loads an existing session. The ledger is authoritative.
    ///
    /// # Errors
    ///
    /// Fails when the ledger is missing, unreadable or schema-mismatched.
    pub fn open(dir: &Path) -> Result<Self, String> {
        let path = dir.join(LEDGER_FILE);
        let text = fs::read_to_string(&path)
            .map_err(|error| format!("cannot read ledger {}: {error}", path.display()))?;
        let ledger: Ledger = serde_json::from_str(&text)
            .map_err(|error| format!("corrupt ledger {}: {error}", path.display()))?;
        if ledger.schema_version != SCHEMA_VERSION {
            return Err(format!(
                "ledger schema {} is not supported (expected {SCHEMA_VERSION})",
                ledger.schema_version
            ));
        }
        Ok(Self {
            dir: dir.to_path_buf(),
            ledger,
        })
    }

    /// Acquires the session-wide exclusive lock. The lock is an OS-level
    /// advisory `flock`, so a crashed process cannot leave the session
    /// wedged; the kernel releases it at process death.
    fn lock(&self) -> Result<SessionLock, String> {
        let path = self.dir.join(LOCK_FILE);
        let file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .map_err(|error| format!("cannot open session lock: {error}"))?;
        let deadline = std::time::Instant::now()
            + std::time::Duration::from_secs(LOCK_TIMEOUT_S);
        loop {
            let status = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) };
            if status == 0 {
                return Ok(SessionLock { file });
            }
            let errno = std::io::Error::last_os_error().raw_os_error().unwrap_or(0);
            if errno != libc::EWOULDBLOCK && errno != libc::EAGAIN && errno != libc::EINTR {
                return Err(format!("session lock failed (errno {errno})"));
            }
            if std::time::Instant::now() >= deadline {
                return Err(format!(
                    "session lock timeout after {LOCK_TIMEOUT_S}s; another call holds the session"
                ));
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
    }

    /// Reloads the authoritative ledger from disk. Callers must hold the
    /// session lock so no other process can mutate it in between.
    fn reload_ledger(&mut self) -> Result<(), String> {
        let text = fs::read_to_string(self.dir.join(LEDGER_FILE))
            .map_err(|error| format!("cannot reload ledger: {error}"))?;
        let ledger: Ledger = serde_json::from_str(&text)
            .map_err(|error| format!("corrupt ledger: {error}"))?;
        if ledger.schema_version != SCHEMA_VERSION {
            return Err("ledger schema mismatch".to_owned());
        }
        self.ledger = ledger;
        Ok(())
    }

    fn save_ledger(&self) -> Result<(), String> {
        let tmp = self.dir.join(format!("{LEDGER_FILE}.tmp"));
        let text = serde_json::to_string_pretty(&self.ledger).map_err(|e| e.to_string())?;
        fs::write(&tmp, text.as_bytes()).map_err(|e| e.to_string())?;
        fs::rename(&tmp, self.dir.join(LEDGER_FILE)).map_err(|e| e.to_string())
    }

    fn append_trace(&self, envelope: &Value) -> Result<(), String> {
        let path = self.dir.join(TRACE_FILE);
        let mut file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .map_err(|e| e.to_string())?;
        let mut line = serde_json::to_string(envelope).map_err(|e| e.to_string())?;
        line.push('\n');
        file.write_all(line.as_bytes())
            .and_then(|()| file.flush())
            .map_err(|e| e.to_string())
    }

    #[must_use]
    pub fn profile(&self) -> Profile {
        Profile::parse(&self.ledger.profile).unwrap_or(Profile::ArmC)
    }

    #[must_use]
    pub fn remaining(&self) -> Value {
        json!({
            "calls_left": MAX_CALLS.saturating_sub(self.ledger.calls_used),
            "rounds_left": MAX_ROUNDS.saturating_sub(self.ledger.rounds_used),
            "files_left": MAX_DISTINCT_FILES.saturating_sub(self.ledger.distinct_files.len()),
            "delivered_bytes_left": MAX_DELIVERED_BYTES.saturating_sub(self.ledger.delivered_bytes),
            "pack_bytes_max": MAX_PACK_BYTES,
            "pack_items_max": MAX_PACK_ITEMS,
        })
    }

    /// Gate check before dispatch: terminal state, slot availability, profile
    /// permission and round validity.
    fn admit(&self, tool: Tool, round: Option<usize>) -> Result<usize, GateError> {
        if let Some(reason) = self.ledger.terminal.clone() {
            return Err(GateError::Terminal(reason));
        }
        if !self.profile().allows(tool) {
            return Err(GateError::ProfileForbidden);
        }
        if tool.consumes_slot() {
            if self.ledger.calls_used >= MAX_CALLS {
                return Err(GateError::NoCallsLeft);
            }
            let round = round.unwrap_or(self.ledger.last_round.max(1));
            if round == 0 || round > MAX_ROUNDS {
                return Err(GateError::InvalidRound(format!(
                    "round {round} outside 1..={MAX_ROUNDS}"
                )));
            }
            if round < self.ledger.last_round {
                return Err(GateError::InvalidRound(format!(
                    "round {round} cannot go back below the current round {}",
                    self.ledger.last_round
                )));
            }
            Ok(round)
        } else {
            Ok(self.ledger.last_round)
        }
    }

    /// Executes one tool call under the cumulative budget and returns the
    /// exact serialized envelope written to the caller.
    ///
    /// The ledger is updated and persisted before the envelope is returned.
    ///
    /// # Errors
    ///
    /// Returns a human-readable error only for adapter IO/state failures; the
    /// accounted tool-result envelope itself is always the `Ok` string.
    pub fn call(&mut self, input: CallInput) -> Result<String, String> {
        // Transaction boundary: hold the exclusive session lock across the
        // whole read (reload) -> admit -> execute -> commit cycle so
        // concurrent processes cannot admit against a stale ledger or lose
        // accounting updates.
        let _lock = self.lock()?;
        self.reload_ledger()?;
        let started = std::time::Instant::now();
        let tool = input.tool;
        let round = match self.admit(tool, (input.round > 0).then_some(input.round)) {
            Ok(round) => round,
            Err(error) => {
                let call_error = CallError::Gate(error);
                return self.emit_error(tool, input.round, call_error, started);
            }
        };

        // Consume the slot before execution: an admitted attempt is accounted
        // even when the engine fails, per the frozen accounting semantics.
        let call_index = self.ledger.calls_used + 1;
        if tool.consumes_slot() {
            self.ledger.calls_used += 1;
            if round > self.ledger.rounds_used {
                self.ledger.rounds_used = round;
            }
            self.ledger.last_round = round;
        }

        let execution = self.execute(tool, &input, call_index);
        let wall_ms = started.elapsed().as_secs_f64() * 1000.0;

        let (ok, result, files) = match execution {
            Ok(output) => {
                // File budget: content from new files beyond the limit is
                // dropped with an explicit gap before serialization.
                let (filtered, files) = self.apply_file_budget(output.result, &output.files);
                (true, filtered, files)
            }
            Err(call_error) => {
                (false, error_value(&call_error), Vec::new())
            }
        };

        for file in files {
            if !self.ledger.distinct_files.contains(&file) {
                self.ledger.distinct_files.push(file);
            }
        }

        let mut envelope = json!({
            "schema_version": SCHEMA_VERSION,
            "session": self.ledger.session_id,
            "case": self.ledger.case,
            "call": call_index,
            "tool": tool_value(tool),
            "round": round,
            "ok": ok,
            "result": result,
        });

        // Delivered-content budget: the exact final serialized envelope
        // counts in full. Measure the exact bytes that will be delivered, then
        // degrade to an explicit error envelope if they do not fit. The call
        // is consumed and no content is delivered in that case.
        envelope["budget"] = self.remaining();
        let mut text = serde_json::to_string_pretty(&envelope).map_err(|e| e.to_string())?;
        if ok && text.len() + self.ledger.delivered_bytes > MAX_DELIVERED_BYTES {
            envelope = json!({
                "schema_version": SCHEMA_VERSION,
                "session": self.ledger.session_id,
                "case": self.ledger.case,
                "call": call_index,
                "tool": tool_value(tool),
                "round": round,
                "ok": false,
                "result": {
                    "code": "delivered_budget_exceeded",
                    "message": "serialized result did not fit the remaining 64 KiB delivered-content budget; call consumed, no content delivered",
                },
            });
            envelope["budget"] = self.remaining();
            text = serde_json::to_string_pretty(&envelope).map_err(|e| e.to_string())?;
        }
        self.ledger.delivered_bytes += text.len();
        self.ledger.history.push(CallRecord {
            call: call_index,
            tool: tool_value(tool).to_string(),
            round,
            ok,
            wall_ms,
            delivered_bytes: text.len(),
        });
        self.save_ledger()?;
        self.append_trace(&envelope)?;
        Ok(text)
    }

    /// Applies the distinct-file budget. Content from new files beyond the
    /// limit is replaced by explicit `file_budget_exceeded` markers (item
    /// slots keep their path, gaps are appended to the result's gap list).
    /// Returns the (possibly rewritten) result and the admitted files.
    fn apply_file_budget(&self, mut result: Value, files: &[String]) -> (Value, Vec<String>) {
        let mut admitted = Vec::new();
        let mut gaps = Vec::new();
        for file in files {
            if self.ledger.distinct_files.contains(file)
                || admitted.contains(file)
                || self.ledger.distinct_files.len() + admitted.len() < MAX_DISTINCT_FILES
            {
                admitted.push(file.clone());
            } else {
                gaps.push(json!({
                    "code": AdapterGapCode::FileBudgetExceeded,
                    "path": file,
                    "detail": "32-file budget reached; no content from this file is delivered",
                }));
            }
        }
        if !gaps.is_empty() {
            if let Some(items) = result.get_mut("items").and_then(Value::as_array_mut) {
                for item in items.iter_mut() {
                    let path = item
                        .get("path")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_owned();
                    if !admitted.contains(&path) {
                        *item = json!({
                            "path": path,
                            "dropped": "file_budget_exceeded",
                        });
                    }
                }
            }
            if let Some(gap_list) = result.get_mut("gaps").and_then(Value::as_array_mut) {
                gap_list.extend(gaps);
            }
        }
        (result, admitted)
    }

    fn emit_error(
        &mut self,
        tool: Tool,
        round: usize,
        error: CallError,
        started: std::time::Instant,
    ) -> Result<String, String> {
        let wall_ms = started.elapsed().as_secs_f64() * 1000.0;
        let mut envelope = json!({
            "schema_version": SCHEMA_VERSION,
            "session": self.ledger.session_id,
            "case": self.ledger.case,
            "call": self.ledger.calls_used + 1,
            "tool": tool_value(tool),
            "round": round,
            "ok": false,
            "result": error_value(&error),
        });
        envelope["budget"] = self.remaining();
        let text = serde_json::to_string_pretty(&envelope).map_err(|e| e.to_string())?;
        self.ledger.delivered_bytes += text.len();
        self.ledger.history.push(CallRecord {
            call: self.ledger.calls_used + 1,
            tool: tool_value(tool).to_string(),
            round: self.ledger.last_round,
            ok: false,
            wall_ms,
            delivered_bytes: text.len(),
        });
        self.save_ledger()?;
        self.append_trace(&envelope)?;
        Ok(text)
    }

    fn execute(
        &mut self,
        tool: Tool,
        input: &CallInput,
        call_index: usize,
    ) -> Result<ToolOutput, CallError> {
        match tool {
            Tool::Search => self.search(input),
            Tool::Symbol => self.symbol(input),
            Tool::Facts => self.facts(input),
            Tool::Evidence => self.evidence(input, call_index),
            Tool::Verify => self.verify(input),
            Tool::Workflow => self.workflow(input),
            Tool::Status => {
                let packs = serde_json::to_value(&self.ledger.packs)
                    .map_err(|e| CallError::Execution { code: "serialize".into(), message: e.to_string() })?;
                Ok(ToolOutput {
                    result: json!({
                        "calls_used": self.ledger.calls_used,
                        "rounds_used": self.ledger.rounds_used,
                        "distinct_files": self.ledger.distinct_files.len(),
                        "delivered_bytes": self.ledger.delivered_bytes,
                        "packs": packs,
                        "terminal": self.ledger.terminal,
                    }),
                    files: Vec::new(),
                })
            }
        }
    }

    fn search(&mut self, input: &CallInput) -> Result<ToolOutput, CallError> {
        let query = input.query.clone().unwrap_or_default();
        if query.trim().is_empty() {
            return Err(invalid("search requires a non-empty query"));
        }
        let limit = input.limit.unwrap_or(10).clamp(1, 50);
        let options = match input.mode.as_deref() {
            None | Some("hybrid") => ContextOptions {
                root: Some(self.ledger.root.clone()),
                query: Some(query.clone()),
                limit: Some(limit),
                auto_update: false,
                refresh: Some(RefreshPolicy::Off),
                ..ContextOptions::default()
            },
            Some("fts") | Some("vector") => {
                let mode = match input.mode.as_deref() {
                    Some("vector") => ContextRouteMode::Vector,
                    _ => ContextRouteMode::Fts,
                };
                ContextOptions {
                    root: Some(self.ledger.root.clone()),
                    routes: vec![ContextRoute {
                        mode,
                        query: query.clone(),
                    }],
                    limit: Some(limit),
                    auto_update: false,
                    refresh: Some(RefreshPolicy::Off),
                    ..ContextOptions::default()
                }
            }
            Some("rg") => ContextOptions {
                rg: true,
                query: Some(query.clone()),
                root: Some(self.ledger.root.clone()),
                rg_paths: input
                    .scope
                    .as_ref()
                    .map(|scope| vec![self.ledger.root.join(scope)])
                    .unwrap_or_default(),
                limit: Some(limit),
                auto_update: false,
                refresh: Some(RefreshPolicy::Off),
                ..ContextOptions::default()
            },
            Some(other) => {
                return Err(invalid(&format!(
                    "unknown search mode {other}; expected hybrid, fts, vector or rg"
                )));
            }
        };
        let engine = ZvecGrep::new();
        let runtime = tokio::runtime::Runtime::new()
            .map_err(|error| CallError::Execution { code: "runtime".into(), message: error.to_string() })?;
        let result = runtime
            .block_on(engine.context(options))
            .map_err(|error| CallError::Execution {
                code: "engine".into(),
                message: error.report().to_string(),
            })?;

        let mut items = Vec::new();
        let mut files = Vec::new();
        let mut hashes: std::collections::HashMap<String, String> =
            std::collections::HashMap::new();
        for item in &result.items {
            let path = item.relative_path.to_string_lossy().replace('\\', "/");
            let sha = match hashes.get(&path) {
                Some(known) => known.clone(),
                None => {
                    let computed =
                        file_sha256(&self.ledger.root.join(&item.relative_path)).unwrap_or_default();
                    hashes.insert(path.clone(), computed.clone());
                    computed
                }
            };
            if !files.contains(&path) {
                files.push(path.clone());
            }
            items.push(json!({
                "rank": item.rank,
                "path": path,
                "sha256": sha,
                "range": [item.range.start_line(), item.range.last_line()],
                "content_range": [item.content_range.start_line(), item.content_range.last_line()],
                "match_ranges": item.match_ranges,
                "content": item.content,
                "status": item.status,
                "score": item.score,
                "matched_by": format!("{:?}", item.matched_by),
            }));
        }
        Ok(ToolOutput {
            result: json!({
                "query": query,
                "mode": input.mode.clone().unwrap_or_else(|| "hybrid".to_owned()),
                "limit": limit,
                "items": items,
                "gaps": [],
            }),
            files,
        })
    }

    fn symbol(&mut self, input: &CallInput) -> Result<ToolOutput, CallError> {
        let name = input.name.clone().unwrap_or_default();
        if name.trim().is_empty() {
            return Err(invalid("symbol requires a non-empty exact name"));
        }
        let options = SymbolsOptions {
            root: Some(self.ledger.root.clone()),
            name: name.clone(),
            limit: Some(50),
            auto_update: false,
            ..SymbolsOptions::default()
        };
        let engine = ZvecGrep::new();
        let runtime = tokio::runtime::Runtime::new()
            .map_err(|error| CallError::Execution { code: "runtime".into(), message: error.to_string() })?;
        let result = runtime
            .block_on(engine.symbols(options))
            .map_err(|error| CallError::Execution {
                code: "engine".into(),
                message: error.report().to_string(),
            })?;
        let items = result
            .items
            .iter()
            .map(|item| {
                let path = item.relative_path.to_string_lossy().replace('\\', "/");
                json!({
                    "name": item.name,
                    "scope": item.scope,
                    "symbol_type": item.symbol_type.map(|kind| format!("{kind:?}")),
                    "signature": item.signature,
                    "path": path,
                    "range": [item.range.start_line(), item.range.last_line()],
                    "status": item.status,
                })
            })
            .collect::<Vec<_>>();
        Ok(ToolOutput {
            result: json!({
                "name": name,
                "total": result.total,
                "truncated": result.truncated,
                "items": items,
                "gaps": [],
            }),
            files: Vec::new(),
        })
    }

    fn facts(&mut self, input: &CallInput) -> Result<ToolOutput, CallError> {
        let path = input.path.clone().unwrap_or_default();
        if path.trim().is_empty() {
            return Err(invalid("facts requires a workspace-relative path"));
        }
        let root = self.ledger.root.clone();
        let relative = resolve_under_root(&root, &path).map_err(|message| invalid(&message))?;
        let text = fs::read_to_string(root.join(&relative))
            .map_err(|error| invalid(&format!("cannot read {path}: {error}")))?;
        let sha = file_sha256(&root.join(&relative)).unwrap_or_default();
        let display = relative.to_string_lossy().replace('\\', "/");
        let exclusions = self.ledger.exclusions.clone();
        let facts = facts::parse_facts(&display, &text, input.method.as_deref(), &exclusions)
            .map_err(|message| invalid(&message))?;
        let files = vec![display.clone()];
        let result = json!({
            "path": display,
            "sha256": sha,
            "facts": facts,
            "gaps": [],
        });
        // A whole-file facts dump can exceed the remaining delivered budget.
        // Degrade to an explicit summary with a gap; never silently truncate.
        if serde_json::to_string(&result)
            .map(|text| text.len() + self.ledger.delivered_bytes > MAX_DELIVERED_BYTES)
            .unwrap_or(true)
        {
            let summary = facts::summarize(&result);
            return Ok(ToolOutput {
                result: json!({
                    "path": relative.to_string_lossy().replace('\\', "/"),
                    "sha256": sha,
                    "facts": summary,
                    "gaps": [{
                        "code": "facts_budget_exceeded",
                        "detail": "full facts did not fit the remaining delivered-content budget; retry with a method filter",
                    }],
                }),
                files,
            });
        }
        Ok(ToolOutput { result, files })
    }

    fn evidence(&mut self, input: &CallInput, call_index: usize) -> Result<ToolOutput, CallError> {
        if input.requests.is_empty() {
            return Err(invalid("evidence requires at least one span request"));
        }
        if input.requests.len() > MAX_PACK_ITEMS {
            return Err(invalid(&format!(
                "evidence accepts at most {MAX_PACK_ITEMS} requests per call"
            )));
        }
        let root = self.ledger.root.clone();
        let budget = EvidenceBudget {
            max_bytes: MAX_PACK_BYTES,
            max_items: MAX_PACK_ITEMS,
        };

        // File budget: requests for new files beyond the limit are dropped
        // with an explicit typed gap instead of being served.
        let mut gaps = Vec::new();
        let mut admitted: Vec<String> = Vec::new();
        for request in &input.requests {
            let path = request
                .relative_path
                .to_string_lossy()
                .replace('\\', "/");
            if admitted.contains(&path) || self.ledger.distinct_files.contains(&path) {
                admitted.push(path);
                continue;
            }
            if self.ledger.distinct_files.len() + admitted.len() >= MAX_DISTINCT_FILES {
                gaps.push(json!({
                    "code": AdapterGapCode::FileBudgetExceeded,
                    "path": path,
                    "detail": "32-file budget reached; span not requested",
                }));
                continue;
            }
            admitted.push(path);
        }
        let spans: Vec<SpanRequest> = input
            .requests
            .iter()
            .filter(|request| {
                let path = request
                    .relative_path
                    .to_string_lossy()
                    .replace('\\', "/");
                admitted.contains(&path)
            })
            .cloned()
            .collect();
        let pack = surface::assemble_spans(&root, &spans, budget).map_err(|error| {
            CallError::Execution {
                code: "evidence".into(),
                message: error.to_string(),
            }
        })?;

        let mut items = Vec::new();
        let mut files = Vec::new();
        for item in &pack.items {
            let path = item.relative_path.to_string_lossy().replace('\\', "/");
            if !files.contains(&path) {
                files.push(path.clone());
            }
            items.push(json!({
                "project": item.project,
                "path": path,
                "sha256": item.source_sha256,
                "verification": item.verification,
                "range": [item.range.start_line(), item.range.last_line()],
                "content": item.content,
            }));
        }
        let engine_gaps = serde_json::to_value(&pack.gaps)
            .map_err(|e| CallError::Execution { code: "serialize".into(), message: e.to_string() })?;
        let mut all_gaps = match engine_gaps {
            Value::Array(entries) => entries,
            _ => Vec::new(),
        };
        all_gaps.extend(gaps);

        let status = format!("{:?}", pack.status).to_lowercase();
        let result = json!({
            "status": status,
            "items": items,
            "gaps": Value::Array(all_gaps),
            "deduplicated": pack.deduplicated,
        });
        // Refuse before storing anything when the presentation cannot fit the
        // remaining delivered-content budget; a stored-but-undelivered pack
        // would be a phantom artifact.
        let presentation = serde_json::to_string(&result)
            .map_err(|e| CallError::Execution { code: "serialize".into(), message: e.to_string() })?;
        if presentation.len() + self.ledger.delivered_bytes + 2048 > MAX_DELIVERED_BYTES {
            return Err(CallError::Execution {
                code: "delivered_budget_exceeded".into(),
                message: "the evidence pack does not fit the remaining 64 KiB delivered-content budget; call consumed, nothing stored".to_owned(),
            });
        }
        // The stored pack file is the real `SpanPack` so revalidation can
        // round-trip it; the envelope below is only presentation.
        let serialized = serde_json::to_string(&pack)
            .map_err(|e| CallError::Execution { code: "serialize".into(), message: e.to_string() })?;
        let stored_sha = hex(&Sha256::digest(serialized.as_bytes()));
        let id = self.ledger.packs.len() + 1;
        let packs_dir = self.dir.join(PACKS_DIR);
        fs::create_dir_all(&packs_dir)
            .map_err(|e| CallError::Execution { code: "io".into(), message: e.to_string() })?;
        let pack_path = packs_dir.join(format!("pack-{call_index}-{id}.json"));
        fs::write(&pack_path, serialized.as_bytes())
            .map_err(|e| CallError::Execution { code: "io".into(), message: e.to_string() })?;
        self.ledger.packs.push(PackRecord {
            id,
            sha256: stored_sha.clone(),
            items: pack.items.len(),
            bytes: serialized.len(),
            verified: false,
            kind: "evidence".into(),
        });
        Ok(ToolOutput {
            result: json!({
                "pack": {
                    "id": id,
                    "sha256": stored_sha,
                    "file": pack_path.file_name().and_then(|name| name.to_str()).unwrap_or_default(),
                },
                "status": status,
                "items": items,
                "gaps": result.get("gaps").cloned().unwrap_or_else(|| json!([])),
                "deduplicated": pack.deduplicated,
            }),
            files,
        })
    }

    fn verify(&mut self, input: &CallInput) -> Result<ToolOutput, CallError> {
        let id = input.pack_id.unwrap_or(self.ledger.packs.len());
        if let Some(record) = self.ledger.packs.get(id.saturating_sub(1))
            && record.kind != "evidence" {
                return Err(invalid(
                    "verify revalidates evidence packs; workflow packs are hash-checked at selection",
                ));
            }
        let pack = self
            .load_pack(input.pack_id)
            .map_err(|message| invalid(&message))?;
        let root = self.ledger.root.clone();
        let gaps = surface::revalidate_spans(&root, &pack).map_err(|error| {
            CallError::Execution {
                code: "verify".into(),
                message: error.to_string(),
            }
        })?;
        let id = input.pack_id.unwrap_or(self.ledger.packs.len());
        let clean = gaps.is_empty();
        if clean
            && let Some(record) = self.ledger.packs.get_mut(id.saturating_sub(1)) {
                record.verified = true;
            }
        let gaps_value = serde_json::to_value(&gaps)
            .map_err(|e| CallError::Execution { code: "serialize".into(), message: e.to_string() })?;
        Ok(ToolOutput {
            result: json!({
                "pack_id": id,
                "verified": clean,
                "gaps": gaps_value,
            }),
            files: Vec::new(),
        })
    }

    fn load_pack(&self, pack_id: Option<usize>) -> Result<surface::SpanPack, String> {
        let id = pack_id.unwrap_or(self.ledger.packs.len());
        if id == 0 || id > self.ledger.packs.len() {
            return Err(format!("pack {id} does not exist in this session"));
        }
        let packs_dir = self.dir.join(PACKS_DIR);
        let stem = self.ledger.packs[id - 1].id;
        let suffix = format!("-{stem}.json");
        let mut entries = fs::read_dir(&packs_dir)
            .map_err(|error| error.to_string())?
            .flatten()
            .map(|entry| entry.file_name())
            .collect::<Vec<_>>();
        entries.sort();
        for name in entries {
            let Some(text) = name.to_str() else { continue };
            if text.ends_with(&suffix) {
                let content = fs::read_to_string(packs_dir.join(text))
                    .map_err(|error| error.to_string())?;
                return serde_json::from_str(&content).map_err(|error| error.to_string());
            }
        }
        Err(format!("pack file for id {id} is missing"))
    }

    fn workflow(&mut self, input: &CallInput) -> Result<ToolOutput, CallError> {
        let question = input.question.clone().unwrap_or_default();
        if question.trim().is_empty() {
            return Err(invalid("workflow requires a non-empty question"));
        }
        let budget = WorkflowBudget {
            max_rounds: 3,
            max_tool_calls: 6,
            max_files: 32,
            max_source_reads: 32,
            hybrid_quota: 4,
            fts_quota: 2,
            ..WorkflowBudget::default()
        };
        let root = self.ledger.root.clone();
        let engine = ZvecGrep::new();
        let mut source = source::EngineSource::new(engine, root.clone());
        source::enable_symbols(&mut source);
        let outcome = workflow::run(&mut source, &root, &question, budget).map_err(|message| {
            CallError::Execution {
                code: "workflow".into(),
                message,
            }
        })?;
        let WorkflowOutcome {
            definition,
            selected,
            gaps,
            abstain,
            stats,
            stages,
            ..
        } = &outcome;
        let mut files = Vec::new();
        for span in selected {
            if !files.contains(&span.path) {
                files.push(span.path.clone());
            }
        }
        let result = json!({
            "definition": serde_json::to_value(definition).unwrap_or(Value::Null),
            "selected": serde_json::to_value(selected).unwrap_or(Value::Null),
            "gaps": serde_json::to_value(gaps).unwrap_or(Value::Null),
            "abstain": serde_json::to_value(abstain).unwrap_or(Value::Null),
            "stats": serde_json::to_value(stats).unwrap_or(Value::Null),
            "stages": serde_json::to_value(stages).unwrap_or(Value::Null),
            "internal_accounting": {
                "note": "engine-internal reads and ranking are not delivered tool content; they are reported here for explicit accounting",
                "tool_calls": stats.tool_calls,
                "source_reads": stats.source_reads,
                "files_seen": stats.files_seen,
                "ranked_content_bytes": stats.ranked_content_bytes,
            },
        });
        let serialized = serde_json::to_string(&result)
            .map_err(|e| CallError::Execution { code: "serialize".into(), message: e.to_string() })?;
        if serialized.len() + self.ledger.delivered_bytes + 2048 > MAX_DELIVERED_BYTES {
            return Err(CallError::Execution {
                code: "delivered_budget_exceeded".into(),
                message: "the workflow outcome does not fit the remaining 64 KiB delivered-content budget; call consumed, nothing stored".to_owned(),
            });
        }
        let sha = hex(&Sha256::digest(serialized.as_bytes()));
        let id = self.ledger.packs.len() + 1;
        let packs_dir = self.dir.join(PACKS_DIR);
        fs::create_dir_all(&packs_dir)
            .map_err(|e| CallError::Execution { code: "io".into(), message: e.to_string() })?;
        let pack_path = packs_dir.join(format!("pack-workflow-{id}.json"));
        fs::write(&pack_path, serialized.as_bytes())
            .map_err(|e| CallError::Execution { code: "io".into(), message: e.to_string() })?;
        self.ledger.packs.push(PackRecord {
            id,
            sha256: sha,
            items: selected.len(),
            bytes: serialized.len(),
            verified: false,
            kind: "workflow".into(),
        });
        Ok(ToolOutput { result, files })
    }

    /// Marks the session terminal and writes the final receipt.
    ///
    /// # Errors
    ///
    /// Fails on IO or serialization errors, or when already terminal.
    pub fn finalize(&mut self, cancelled: bool) -> Result<String, String> {
        let _lock = self.lock()?;
        self.reload_ledger()?;
        if self.ledger.terminal.is_some() {
            return Err("session is already terminal".to_owned());
        }
        self.ledger.terminal =
            Some(if cancelled { "cancelled" } else { "finalized" }.into());
        self.save_ledger()?;
        self.write_receipt()
    }

    /// Writes (or re-prints) the frozen session receipt with artifact hashes.
    ///
    /// # Errors
    ///
    /// Fails on IO or serialization errors.
    pub fn write_receipt(&self) -> Result<String, String> {
        let trace = fs::read_to_string(self.dir.join(TRACE_FILE)).unwrap_or_default();
        let trace_sha = hex(&Sha256::digest(trace.as_bytes()));
        let packs_dir = self.dir.join(PACKS_DIR);
        let mut pack_hashes = Vec::new();
        if let Ok(entries) = fs::read_dir(&packs_dir) {
            let mut names: Vec<_> = entries.flatten().map(|entry| entry.file_name()).collect();
            names.sort();
            for name in names {
                if let Some(text) = name.to_str() {
                    let bytes = fs::read(packs_dir.join(text)).unwrap_or_default();
                    pack_hashes.push(json!({
                        "file": text,
                        "sha256": hex(&Sha256::digest(&bytes)),
                        "bytes": bytes.len(),
                    }));
                }
            }
        }
        let receipt = json!({
            "schema_version": SCHEMA_VERSION,
            "session": self.ledger.session_id,
            "case": self.ledger.case,
            "profile": self.ledger.profile,
            "root": self.ledger.root.display().to_string(),
            "terminal": self.ledger.terminal,
            "budget": {
                "calls_used": self.ledger.calls_used,
                "calls_max": MAX_CALLS,
                "rounds_used": self.ledger.rounds_used,
                "rounds_max": MAX_ROUNDS,
                "distinct_files": self.ledger.distinct_files.len(),
                "distinct_files_max": MAX_DISTINCT_FILES,
                "delivered_bytes": self.ledger.delivered_bytes,
                "delivered_bytes_max": MAX_DELIVERED_BYTES,
                "pack_bytes_max": MAX_PACK_BYTES,
                "pack_items_max": MAX_PACK_ITEMS,
            },
            "files": self.ledger.distinct_files,
            "packs": self.ledger.packs,
            "history": self.ledger.history,
            "artifacts": {
                "trace_sha256": trace_sha,
                "packs": pack_hashes,
            },
        });
        let pretty = serde_json::to_string_pretty(&receipt).map_err(|e| e.to_string())?;
        fs::write(self.dir.join(RECEIPT_FILE), pretty.as_bytes())
            .map_err(|e| e.to_string())?;
        Ok(pretty)
    }
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        let _ = write!(out, "{byte:02x}");
    }
    out
}

fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_secs())
        .unwrap_or(0)
}

fn invalid(message: &str) -> CallError {
    CallError::Execution {
        code: "invalid_input".into(),
        message: message.to_owned(),
    }
}

fn error_value(error: &CallError) -> Value {
    json!({
        "code": error.code(),
        "message": error.message(),
    })
}

fn tool_value(tool: Tool) -> Value {
    match tool {
        Tool::Search => json!("search"),
        Tool::Symbol => json!("symbol"),
        Tool::Facts => json!("facts"),
        Tool::Evidence => json!("evidence"),
        Tool::Verify => json!("verify"),
        Tool::Workflow => json!("workflow"),
        Tool::Status => json!("status"),
    }
}

fn file_sha256(path: &Path) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    Some(hex(&Sha256::digest(&bytes)))
}

/// Resolves a workspace-relative path under the root, fail-closed against
/// absolute paths, traversal, out-of-root targets and symlinks.
///
/// # Errors
///
/// Returns a human-readable rejection reason.
pub fn resolve_under_root(root: &Path, relative: &str) -> Result<PathBuf, String> {
    let candidate = Path::new(relative);
    if candidate.is_absolute() {
        return Err(format!("path {relative} must be workspace-relative"));
    }
    for component in candidate.components() {
        if !matches!(component, std::path::Component::Normal(_)) {
            return Err(format!("path {relative} contains a non-normal component"));
        }
    }
    let joined = root.join(candidate);
    match joined.canonicalize() {
        Ok(resolved) => {
            if !resolved.starts_with(root) {
                return Err(format!("path {relative} resolves outside the workspace"));
            }
            Ok(resolved
                .strip_prefix(root)
                .unwrap_or(&resolved)
                .to_path_buf())
        }
        Err(error) => Err(format!("cannot resolve {relative}: {error}")),
    }
}

/// Parses evidence span requests from JSON.
///
/// # Errors
///
/// Fails when a request is malformed.
pub fn parse_span_requests(value: &Value) -> Result<Vec<SpanRequest>, String> {
    let array = value
        .as_array()
        .ok_or_else(|| "span requests must be a JSON array".to_owned())?;
    let mut requests = Vec::new();
    for entry in array {
        let project = entry
            .get("project")
            .and_then(Value::as_str)
            .unwrap_or("corpus")
            .to_owned();
        let relative_path = entry
            .get("relative_path")
            .and_then(Value::as_str)
            .ok_or_else(|| "each request needs relative_path".to_owned())?;
        let expected_sha256 = entry
            .get("expected_sha256")
            .and_then(Value::as_str)
            .map(str::to_owned);
        let address = entry
            .get("address")
            .ok_or_else(|| "each request needs an address".to_owned())?;
        let mode = address
            .get("mode")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let address = match mode {
            "lines" => {
                let start_line = address
                    .get("start_line")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| "lines address needs start_line".to_owned())?
                    as usize;
                let end_line = address
                    .get("end_line")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| "lines address needs end_line".to_owned())?
                    as usize;
                surface::SpanAddress::Lines {
                    start_line,
                    end_line,
                }
            }
            "bytes" => {
                let start = address
                    .get("start")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| "bytes address needs start".to_owned())?
                    as usize;
                let end = address
                    .get("end")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| "bytes address needs end".to_owned())?
                    as usize;
                surface::SpanAddress::Bytes { start, end }
            }
            other => return Err(format!("unknown address mode {other}")),
        };
        requests.push(SpanRequest {
            project,
            relative_path: PathBuf::from(relative_path),
            expected_sha256,
            address,
        });
    }
    Ok(requests)
}
