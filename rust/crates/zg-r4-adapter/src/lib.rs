//! Private experimental R4 external-agent adapter.
//!
//! This crate is the smallest integration that lets a real external agent use
//! existing zvec-grep capabilities (hybrid/FTS/lexical search, exact symbols,
//! R2/R3 typed facts, R1 hash-checked evidence) interactively under the frozen
//! R0 cumulative session budget. It is experimental benchmark infrastructure,
//! not a public engine contract: do not expose it through the default MCP
//! profile, public CLI or daemon.
//!
//! Session model (post-audit, schema 2):
//! - `session new` pins the corpus root, profile, case and excluded-variant
//!   markers, and creates an authoritative ledger plus an append-only trace.
//!   No command resets the ledger.
//! - Every mutating operation holds an OS-level `flock` across
//!   reload → admit → reserve → execute → commit, so concurrent processes can
//!   neither admit against a stale ledger nor lose accounting updates.
//! - Call admission is a *reservation*: the slot (and an `in_flight` marker) is
//!   persisted before execution. A crash between reservation and commit leaves
//!   the slot consumed and the session terminal (`invalidated_by_crash`);
//!   interrupted work can never be re-used silently.
//! - Budgets are cumulative across the whole session and cover external
//!   calls, engine-managed workflow internals (shared call pool), actual
//!   source-file reads (distinct files and read operations), distinct
//!   delivered files, and the exact serialized envelope bytes delivered to
//!   the answerer. Errors, gaps, metadata and wrappers are accounted.
//! - Delivered-byte accounting is exact: the envelope's own bytes are
//!   included via a fixpoint so the `delivered_bytes_left` shown to the
//!   answerer matches reality; if even a minimal error envelope cannot fit,
//!   the session terminates without delivering over-budget content.
//! - Evidence packs are committed only after the serialized envelope fits the
//!   remaining budget (no phantom artifacts) and pack files are hash-checked
//!   on load (tamper fail-closed). Revalidation failure clears the persisted
//!   `verified` state.
//! - `verify` is bounded: once per pack, no call slot, bytes accounted, and
//!   always possible within the frozen investigation budget.
//! - A "round" is trusted-controller territory: the adapter enforces bounds
//!   (1..=3, non-decreasing) but the benchmark controller owns round
//!   assignment; answerer self-declaration is not a security boundary.

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
use zg_engine::rag::workflow::{self, AgentPlan, WorkflowBudget, WorkflowOutcome};
use zg_engine::ZvecGrep;

pub mod facts;
pub mod source;

/// Frozen R0 session budget. Non-negotiable.
pub const MAX_CALLS: usize = 6;
pub const MAX_ROUNDS: usize = 3;
/// Distinct source files actually read (opened) by adapter operations.
pub const MAX_DISTINCT_READS: usize = 32;
/// Distinct source files whose content is delivered to the answerer.
pub const MAX_DISTINCT_FILES: usize = 32;
/// Cumulative read operations (file opens for content/hashes).
pub const MAX_READ_OPS: usize = 64;
pub const MAX_DELIVERED_BYTES: usize = 64 * 1024;
pub const MAX_PACK_BYTES: usize = 24 * 1024;
pub const MAX_PACK_ITEMS: usize = 10;
/// Engine-managed workflow internals share the external call pool.
pub const MAX_INTERNAL_CALLS: usize = MAX_CALLS;

pub const LEDGER_FILE: &str = "ledger.json";
pub const TRACE_FILE: &str = "trace.jsonl";
pub const PACKS_DIR: &str = "packs";
pub const RECEIPT_FILE: &str = "receipt.json";
pub const LOCK_FILE: &str = "session.lock";
/// Fail-closed bound on waiting for the session lock.
pub const LOCK_TIMEOUT_S: u64 = 120;
pub const SCHEMA_VERSION: u32 = 2;
/// Ceiling for the minimal terminal marker delivered when no valid response
/// fits the remaining budget.
pub const TERMINAL_MARKER_MAX_BYTES: usize = 512;

/// Default excluded-variant path markers. These are benchmark scope
/// configuration carried from the frozen `scope-manifest.json`, not engine
/// behavior; a session can override them with `Session::new_configured`.
pub const DEFAULT_EXCLUSIONS: &[&str] = &["_old", "_old1", "newsolution"];

/// Adapter-level gap codes (budget containment), reported alongside engine
/// gaps. Never silent truncation.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AdapterGapCode {
    /// Reading or delivering one more distinct file would exceed a file budget.
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

    /// Whether the call consumes an investigation call slot. Meta
    /// (`status`) and bounded verification (`verify`, once per pack) do not;
    /// the R0 protocol requires a final verification to stay possible within
    /// the frozen investigation budget, and neither operation delivers
    /// source content beyond its own byte-accounted envelope.
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
    /// Read operations attributed to this call.
    #[serde(default)]
    pub read_ops: usize,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct PackRecord {
    pub id: usize,
    pub sha256: String,
    pub items: usize,
    pub bytes: usize,
    /// *Current* validity of the pack against the live source. Cleared when
    /// revalidation fails; historical verification events stay in `history`.
    pub verified: bool,
    /// Whether this pack has had its one allowed verification.
    #[serde(default)]
    pub verifications: usize,
    /// `evidence` packs can be revalidated; `workflow` packs are
    /// hash-checked at selection.
    pub kind: String,
}

/// The authoritative cumulative ledger. Written atomically under the session
/// lock; the call slot is reserved (with `in_flight`) before execution.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Ledger {
    pub schema_version: u32,
    pub session_id: String,
    pub case: String,
    pub profile: String,
    pub root: PathBuf,
    pub created_unix: u64,
    /// `None` until finalize/cancel; set automatically on crash detection.
    pub terminal: Option<String>,
    pub calls_used: usize,
    /// Engine-managed workflow internal calls (shared pool with `calls_used`).
    #[serde(default)]
    pub internal_calls: usize,
    pub rounds_used: usize,
    pub last_round: usize,
    /// Distinct source files whose content is delivered.
    pub distinct_files: Vec<String>,
    /// Distinct source files actually opened/read (superset relation varies).
    #[serde(default)]
    pub read_files: Vec<String>,
    /// Total source-read operations.
    #[serde(default)]
    pub read_ops: usize,
    pub delivered_bytes: usize,
    pub packs: Vec<PackRecord>,
    pub history: Vec<CallRecord>,
    /// Excluded-variant path markers (benchmark scope configuration).
    pub exclusions: Vec<String>,
    /// The call index reserved and not yet committed (crash marker).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub in_flight: Option<usize>,
}

/// Gate-level rejections: no call slot is consumed because the session state
/// itself rejects the operation. The error envelope is still byte-accounted
/// when it fits the remaining budget.
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
    /// 1..=MAX_ROUNDS, non-decreasing across the session. The benchmark
    /// controller owns round assignment.
    pub round: usize,
    pub query: Option<String>,
    pub mode: Option<String>,
    pub limit: Option<usize>,
    /// Include the declaration outline (member list / calls) in search items.
    pub outline: bool,
    /// Prefer declarations whose exact name appears in the query (engine
    /// `prefer_symbol` route). Opt-in: it prioritizes identifier-shaped
    /// queries and is not a default ranking change.
    pub prefer_symbol: bool,
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
    /// Preregistered blind plan for the arm-b workflow tool, when the
    /// protocol supplies one (fresh-blind or frozen reproduction mode).
    pub plan: Option<AgentPlan>,
    /// SHA-256 of the exact plan file bytes, recorded before execution so the
    /// receipt can prove which frozen artifact produced the pack.
    pub plan_sha256: Option<String>,
}

/// A loaded pack, typed by its family so verification stays honest for both.
enum PackPayload {
    Spans(surface::SpanPack),
    Workflow(Vec<WorkflowSpanRef>),
}

struct WorkflowSpanRef {
    path: String,
    start_line: usize,
    end_line: usize,
    source_sha256: String,
}

/// Deferred pack publication. Committed by the caller only after the final
/// serialized envelope fits the delivered budget, so a degraded call never
/// leaves a phantom pack artifact.
pub struct PackCommit {
    pub kind: String,
    pub sha256: String,
    pub items: usize,
    pub bytes: usize,
    pub payload: String,
    pub file_name: String,
}

/// Result of executing one tool: the serde value placed in the envelope,
/// the ordered distinct files whose content this call delivers, the distinct
/// source files this call actually opened, the number of read operations,
/// and an optional deferred pack publication.
pub struct ToolOutput {
    pub result: Value,
    pub files: Vec<String>,
    pub commit: Option<PackCommit>,
}

pub struct Session {
    pub dir: PathBuf,
    pub ledger: Ledger,
    /// Reads performed by the call currently in flight. Recorded at the moment
    /// of the open, so an error, panic-free abort or decode failure after the
    /// read cannot make the ledger report fewer source accesses than happened.
    /// Merged into `ledger` once the call finishes (success or failure).
    pending_reads: Vec<String>,
    pending_read_ops: usize,
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
            pending_reads: Vec::new(),
            pending_read_ops: 0,
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
            internal_calls: 0,
            rounds_used: 0,
            last_round: 0,
            distinct_files: Vec::new(),
            read_files: Vec::new(),
            read_ops: 0,
            delivered_bytes: 0,
            packs: Vec::new(),
            history: Vec::new(),
            exclusions,
            in_flight: None,
        }
    }

    /// Loads an existing session. The ledger is authoritative; a crashed
    /// in-flight call makes the session terminal so consumed budget can
    /// never be silently recovered.
    ///
    /// # Errors
    ///
    /// Fails when the ledger is missing, unreadable or schema-mismatched.
    pub fn open(dir: &Path) -> Result<Self, String> {
        let path = dir.join(LEDGER_FILE);
        let text = fs::read_to_string(&path)
            .map_err(|error| format!("cannot read ledger {}: {error}", path.display()))?;
        let mut ledger: Ledger = serde_json::from_str(&text)
            .map_err(|error| format!("corrupt ledger {}: {error}", path.display()))?;
        if ledger.schema_version != SCHEMA_VERSION {
            return Err(format!(
                "ledger schema {} is not supported (expected {SCHEMA_VERSION})",
                ledger.schema_version
            ));
        }
        if let Some(call) = ledger.in_flight {
            if ledger.terminal.is_none() {
                ledger.terminal = Some(format!(
                    "invalidated_by_crash (call {call} reserved but never committed; its budget stays consumed)"
                ));
            }
        }
        Ok(Self {
            dir: dir.to_path_buf(),
            ledger,
            pending_reads: Vec::new(),
            pending_read_ops: 0,
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
        let deadline =
            std::time::Instant::now() + std::time::Duration::from_secs(LOCK_TIMEOUT_S);
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
        let mut ledger: Ledger = serde_json::from_str(&text)
            .map_err(|error| format!("corrupt ledger: {error}"))?;
        if ledger.schema_version != SCHEMA_VERSION {
            return Err("ledger schema mismatch".to_owned());
        }
        if let Some(call) = ledger.in_flight {
            if ledger.terminal.is_none() {
                ledger.terminal = Some(format!(
                    "invalidated_by_crash (call {call} reserved but never committed; its budget stays consumed)"
                ));
            }
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
            "calls_left": MAX_CALLS
                .saturating_sub(self.ledger.calls_used)
                .saturating_sub(self.ledger.internal_calls),
            "rounds_left": MAX_ROUNDS.saturating_sub(self.ledger.rounds_used),
            "read_files_left": MAX_DISTINCT_READS.saturating_sub(self.ledger.read_files.len()),
            "files_left": MAX_DISTINCT_FILES.saturating_sub(self.ledger.distinct_files.len()),
            "delivered_bytes_left": MAX_DELIVERED_BYTES.saturating_sub(self.ledger.delivered_bytes),
            "pack_bytes_max": MAX_PACK_BYTES,
            "pack_items_max": MAX_PACK_ITEMS,
        })
    }

    /// Gate check before dispatch: terminal state, slot availability, profile
    /// permission, round validity and verify bounds.
    fn admit(&self, tool: Tool, round: Option<usize>) -> Result<usize, GateError> {
        if let Some(reason) = self.ledger.terminal.clone() {
            return Err(GateError::Terminal(reason));
        }
        if !self.profile().allows(tool) {
            return Err(GateError::ProfileForbidden);
        }
        if tool.consumes_slot() {
            if self.ledger.calls_used + self.ledger.internal_calls >= MAX_CALLS {
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
    /// Transaction boundary (under the session lock): reload → admit →
    /// reserve (persist) → execute → commit. A crash after reservation
    /// leaves the slot consumed and the session terminal on next open.
    ///
    /// # Errors
    ///
    /// Returns a human-readable error only for adapter IO/state failures; the
    /// accounted tool-result envelope itself is always the `Ok` string.
    pub fn call(&mut self, input: CallInput) -> Result<String, String> {
        // Transaction boundary: hold the exclusive session lock across the
        // whole read (reload) -> admit -> reserve -> execute -> commit cycle
        // so concurrent processes cannot admit against a stale ledger or
        // lose accounting updates.
        let _lock = self.lock()?;
        self.reload_ledger()?;

        // Gate checks against the freshly reloaded ledger. Gate refusals do
        // not consume a slot and are accounted only when their error
        // envelope fits the remaining budget.
        // Verification is bounded by byte accounting (each envelope is
        // fully accounted and the hard 64 KiB cap stops any flood) rather
        // than a once-per-pack gate, so a pack can always be re-checked after
        // its source changed; the persisted `verified` state follows the
        // latest revalidation.
        let round = match self.admit(input.tool, (input.round > 0).then_some(input.round)) {
            Ok(round) => round,
            Err(error) => {
                let call_error = CallError::Gate(error);
                return self.emit_error(input.tool, input.round, call_error);
            }
        };

        let started = std::time::Instant::now();
        let tool = input.tool;

        // Reservation: persist the consumed slot and an in-flight marker
        // before any work, so a crash cannot silently return the budget.
        let call_index = self.ledger.calls_used + 1;
        if tool.consumes_slot() {
            self.ledger.calls_used += 1;
            if round > self.ledger.rounds_used {
                self.ledger.rounds_used = round;
            }
            self.ledger.last_round = round;
            self.ledger.in_flight = Some(call_index);
            self.save_ledger()?;
        }

        let execution = self.execute(tool, &input, call_index);
        let wall_ms = started.elapsed().as_secs_f64() * 1000.0;

        let (mut ok, mut result, files, mut commit) = match execution {
            Ok(output) => {
                // Delivered-file budget: content from new files beyond the
                // cap is dropped with an explicit gap before serialization.
                // (Read budgets were enforced by each tool before reading.)
                let (filtered, files) =
                    self.apply_file_budget(output.result, &output.files);
                (true, filtered, files, output.commit)
            }
            Err(call_error) => (false, error_value(&call_error), Vec::new(), None),
        };
        // Source-read accounting is recorded at the moment of each open, so a
        // decode error, I/O error or engine failure after the read still
        // counts. It is the single source of truth: no tool-reported counter
        // is added on top of it, and no counter is ever clamped.
        let pending_over = self.commit_pending_reads();
        for file in &files {
            if !self.ledger.distinct_files.contains(file) {
                self.ledger.distinct_files.push(file.clone());
            }
        }
        let read_ops = self.ledger.read_ops;
        if pending_over && ok {
            self.ledger.terminal = Some("read_budget_violation".to_owned());
            ok = false;
            result = json!({
                "code": "read_budget_violation",
                "detail": "operation performed source reads it was not admitted for; run invalidated",
            });
            commit = None;
        }

        let session_id = self.ledger.session_id.clone();
        let case = self.ledger.case.clone();
        let mut envelope = json!({
            "schema_version": SCHEMA_VERSION,
            "session": session_id,
            "case": case,
            "call": call_index,
            "tool": tool_value(tool),
            "round": round,
            "ok": ok,
            "result": result,
        });

        // Delivered-content budget: the exact final serialized envelope
        // counts in full, including its own budget block. A bounded fixpoint
        // makes `delivered_bytes_left` consistent with the bytes actually
        // delivered by this very envelope; if the digits do not stabilize
        // within a few iterations, a conservative value is used so the
        // invariant (never over budget) always holds.
        let delivered_after = self.ledger.delivered_bytes;
        let mut remaining_left = MAX_DELIVERED_BYTES.saturating_sub(delivered_after);
        let mut text = String::new();
        for iteration in 0..8 {
            envelope["budget"] = self.remaining_with(remaining_left);
            let candidate = match serde_json::to_string_pretty(&envelope) {
                Ok(text) => text,
                Err(error) => return Err(error.to_string()),
            };
            let corrected =
                MAX_DELIVERED_BYTES.saturating_sub(delivered_after + candidate.len());
            text = candidate;
            if corrected == remaining_left {
                break;
            }
            if iteration == 7 {
                remaining_left = corrected.saturating_sub(1);
                envelope["budget"] = self.remaining_with(remaining_left);
                text = serde_json::to_string_pretty(&envelope).map_err(|e| e.to_string())?;
                break;
            }
            remaining_left = corrected;
        }

        if ok && self.ledger.delivered_bytes + text.len() > MAX_DELIVERED_BYTES {
            // Degrade to an explicit error envelope; the call is consumed
            // and no content (nor pack) is delivered.
            ok = false;
            envelope = json!({
                "schema_version": SCHEMA_VERSION,
                "session": session_id,
                "case": case,
                "call": call_index,
                "tool": tool_value(tool),
                "round": round,
                "ok": false,
                "result": {
                    "code": "delivered_budget_exceeded",
                    "message": "serialized result did not fit the remaining 64 KiB delivered-content budget; call consumed, no content delivered",
                },
            });
            let delivered_after = self.ledger.delivered_bytes;
            let mut remaining_left =
                MAX_DELIVERED_BYTES.saturating_sub(delivered_after + text.len());
            // Bounded fixpoint: the budget field's own digit width can make the
            // exact-byte equation oscillate, so cap iterations and exit with the
            // conservative (smaller) remaining value.
            for _ in 0..8 {
                envelope["budget"] = self.remaining_with(remaining_left);
                let candidate = serde_json::to_string_pretty(&envelope)
                    .map_err(|e| e.to_string())?;
                let corrected = MAX_DELIVERED_BYTES
                    .saturating_sub(delivered_after + candidate.len());
                text = candidate;
                if corrected == remaining_left || corrected == 0 {
                    break;
                }
                remaining_left = corrected.min(remaining_left);
            }
        }

        // Commit: only now persist the pack (a degraded call never
        // publishes a phantom artifact), accounting and the completed-call
        // marker, then publish.
        if ok {
            if let Some(commit) = commit {
                let packs_dir = self.dir.join(PACKS_DIR);
                fs::create_dir_all(&packs_dir)
                    .map_err(|e| format!("cannot create packs dir: {e}"))?;
                fs::write(packs_dir.join(&commit.file_name), commit.payload.as_bytes())
                    .map_err(|e| format!("cannot persist pack: {e}"))?;
                let id = self.ledger.packs.len() + 1;
                self.ledger.packs.push(PackRecord {
                    id,
                    sha256: commit.sha256,
                    items: commit.items,
                    bytes: commit.bytes,
                    verified: false,
                    verifications: 0,
                    kind: commit.kind,
                });
                if let Some(result) = envelope.get_mut("result") {
                    if let Some(pack) = result.get_mut("pack") {
                        pack["id"] = json!(id);
                    }
                }
                // The id inside the serialized text changed; recompute
                // exact bytes with the same fixpoint (ids are short, the
                // delta is a few bytes and is subtracted conservatively).
                let delivered_after = self.ledger.delivered_bytes;
                let mut remaining_left =
                    MAX_DELIVERED_BYTES.saturating_sub(delivered_after + text.len());
                for _ in 0..8 {
                    envelope["budget"] = self.remaining_with(remaining_left);
                    let candidate = serde_json::to_string_pretty(&envelope)
                        .map_err(|e| e.to_string())?;
                    let corrected = MAX_DELIVERED_BYTES
                        .saturating_sub(delivered_after + candidate.len());
                    text = candidate;
                    if corrected == remaining_left
                        || delivered_after + text.len() > MAX_DELIVERED_BYTES
                    {
                        break;
                    }
                    remaining_left = corrected.min(remaining_left);
                }
            }
        }

        if self.ledger.delivered_bytes + text.len() > MAX_DELIVERED_BYTES {
            // Hard invariant: nothing over-budget is ever published. Mark
            // the session terminal and emit only a minimal marker if it
            // fits; otherwise emit nothing.
            self.ledger.terminal =
                Some("delivered_budget_exhausted".to_owned());
            self.ledger.history.push(CallRecord {
                call: call_index,
                tool: tool_value(tool).to_string(),
                round,
                ok: false,
                wall_ms,
                delivered_bytes: 0,
                read_ops,
            });
            self.ledger.in_flight = None;
            self.save_ledger()?;
            let marker = json!({
                "schema_version": SCHEMA_VERSION,
                "session": session_id,
                "case": case,
                "terminal": "delivered_budget_exhausted",
            });
            let marker_text = serde_json::to_string(&marker).map_err(|e| e.to_string())?;
            if self.ledger.delivered_bytes + marker_text.len() + 1 <= MAX_DELIVERED_BYTES {
                self.ledger.delivered_bytes += marker_text.len() + 1;
                self.save_ledger()?;
                return Ok(format!("{marker_text}\n"));
            }
            self.save_ledger()?;
            return Ok(String::new());
        }

        self.ledger.delivered_bytes += text.len();
        self.ledger.history.push(CallRecord {
            call: call_index,
            tool: tool_value(tool).to_string(),
            round,
            ok,
            wall_ms,
            delivered_bytes: text.len(),
            read_ops,
        });
        if tool == Tool::Verify {
            let id = input.pack_id.unwrap_or(self.ledger.packs.len());
            if let Some(record) = self.ledger.packs.get_mut(id.saturating_sub(1)) {
                record.verifications += 1;
                record.verified = envelope_ok_verified(&text);
            }
        }
        self.ledger.in_flight = None;
        self.save_ledger()?;
        self.append_trace(&envelope)?;
        Ok(text)
    }

    fn remaining_read_ops(&self) -> usize {
        MAX_READ_OPS
            .saturating_sub(self.ledger.read_ops)
            .saturating_sub(self.pending_read_ops)
    }

    fn remaining_read_files(&self) -> usize {
        MAX_DISTINCT_READS
            .saturating_sub(self.ledger.read_files.len())
            .saturating_sub(
                self.pending_reads
                    .iter()
                    .filter(|file| !self.ledger.read_files.contains(file))
                    .count(),
            )
    }

    /// Records a source read at the moment it happens. This is the only
    /// accounting a failure path can rely on, so it deliberately does not
    /// depend on the tool returning successfully.
    fn note_source_read(&mut self, path: &str) {
        if !self.pending_reads.iter().any(|known| known == path) {
            self.pending_reads.push(path.to_owned());
        }
        self.pending_read_ops += 1;
    }

    /// Merges the in-flight read accounting into the ledger. Returns true when
    /// a read happened that was not admitted for (call must fail closed).
    fn commit_pending_reads(&mut self) -> bool {
        let mut over = false;
        for file in std::mem::take(&mut self.pending_reads) {
            if !self.ledger.read_files.contains(&file) {
                if self.ledger.read_files.len() >= MAX_DISTINCT_READS {
                    over = true;
                }
                self.ledger.read_files.push(file);
            }
        }
        let ops = std::mem::take(&mut self.pending_read_ops);
        if self.ledger.read_ops + ops > MAX_READ_OPS {
            over = true;
        }
        self.ledger.read_ops += ops;
        over
    }

    /// Admits source reads BEFORE any file is opened. Both budgets must be
    /// honoured: distinct source files (frozen R0 `<=32`) and total read
    /// operations (defensive `<=64`). No path may perform a read it has not
    /// been admitted for, and no counter may be clamped afterwards.
    fn admit_reads(
        &self,
        ops: usize,
        new_files: usize,
        reason: &str,
    ) -> Result<(), CallError> {
        if ops > self.remaining_read_ops() || new_files > self.remaining_read_files() {
            return Err(CallError::Execution {
                code: "read_budget_exceeded".into(),
                message: format!(
                    "{reason} needs {ops} read op(s) over {new_files} new file(s), but only {} op(s) / {} file(s) remain; refused before opening any source",
                    self.remaining_read_ops(),
                    self.remaining_read_files()
                ),
            });
        }
        Ok(())
    }

    fn remaining_with(&self, delivered_left: usize) -> Value {
        json!({
            "calls_left": MAX_CALLS
                .saturating_sub(self.ledger.calls_used)
                .saturating_sub(self.ledger.internal_calls),
            "rounds_left": MAX_ROUNDS.saturating_sub(self.ledger.rounds_used),
            "read_files_left": MAX_DISTINCT_READS.saturating_sub(self.ledger.read_files.len()),
            "files_left": MAX_DISTINCT_FILES.saturating_sub(self.ledger.distinct_files.len()),
            "delivered_bytes_left": delivered_left,
            "pack_bytes_max": MAX_PACK_BYTES,
            "pack_items_max": MAX_PACK_ITEMS,
        })
    }

    /// Applies the delivered-file budget. Content from new files beyond the
    /// cap is dropped with an explicit gap before serialization. Returns
    /// (result, admitted delivered files).
    fn apply_file_budget(
        &self,
        mut result: Value,
        files: &[String],
    ) -> (Value, Vec<String>) {
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
                    "detail": "file budget reached; no content from this file is delivered",
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
    ) -> Result<String, String> {
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
        let delivered_after = self.ledger.delivered_bytes;
        let mut remaining_left = MAX_DELIVERED_BYTES.saturating_sub(delivered_after);
        let mut text = String::new();
        for _ in 0..8 {
            envelope["budget"] = self.remaining_with(remaining_left);
            text = serde_json::to_string_pretty(&envelope).map_err(|e| e.to_string())?;
            let corrected = MAX_DELIVERED_BYTES.saturating_sub(delivered_after + text.len());
            if corrected == remaining_left {
                break;
            }
            remaining_left = corrected.min(remaining_left);
        }
        if delivered_after + text.len() > MAX_DELIVERED_BYTES {
            // Hard invariant: never publish over-budget content. Emit the
            // minimal terminal marker if it fits, otherwise nothing.
            self.ledger.terminal = Some("delivered_budget_exhausted".to_owned());
            self.save_ledger()?;
            let marker = json!({
                "schema_version": SCHEMA_VERSION,
                "session": self.ledger.session_id,
                "case": self.ledger.case,
                "terminal": "delivered_budget_exhausted",
            });
            let marker_text = serde_json::to_string(&marker).map_err(|e| e.to_string())?;
            if self.ledger.delivered_bytes + marker_text.len() + 1 <= MAX_DELIVERED_BYTES {
                self.ledger.delivered_bytes += marker_text.len() + 1;
                self.save_ledger()?;
                return Ok(format!("{marker_text}\n"));
            }
            return Ok(String::new());
        }
        self.ledger.delivered_bytes += text.len();
        self.ledger.history.push(CallRecord {
            call: self.ledger.calls_used + 1,
            tool: tool_value(tool).to_string(),
            round: self.ledger.last_round,
            ok: false,
            wall_ms: 0.0,
            delivered_bytes: text.len(),
            read_ops: 0,
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
                let packs = serde_json::to_value(&self.ledger.packs).map_err(|e| {
                    CallError::Execution { code: "serialize".into(), message: e.to_string() }
                })?;
                Ok(ToolOutput {
                    result: json!({
                        "calls_used": self.ledger.calls_used,
                        "internal_calls": self.ledger.internal_calls,
                        "rounds_used": self.ledger.rounds_used,
                        "distinct_files": self.ledger.distinct_files.len(),
                        "read_files": self.ledger.read_files.len(),
                        "read_ops": self.ledger.read_ops,
                        "delivered_bytes": self.ledger.delivered_bytes,
                        "packs": packs,
                        "terminal": self.ledger.terminal,
                    }),
                    files: Vec::new(),
                    commit: None,
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
                prefer_symbol: input.prefer_symbol,
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
                    prefer_symbol: input.prefer_symbol,
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
        let runtime = tokio::runtime::Runtime::new().map_err(|error| {
            CallError::Execution { code: "runtime".into(), message: error.to_string() }
        })?;
        let result = runtime
            .block_on(engine.context(options))
            .map_err(|error| CallError::Execution {
                code: "engine".into(),
                message: error.report().to_string(),
            })?;

        // Provenance-correct hashing: only FRESH items whose file can be
        // admitted under the read budget get a current-file hash. Stale
        // indexed excerpts are delivered without a hash and flagged, so a
        // current hash is never attached to stale indexed content.
        let mut items = Vec::new();
        let mut files = Vec::new();
        let mut reads = Vec::new();
        let mut read_ops = 0usize;
        let mut hashes: std::collections::HashMap<String, String> =
            std::collections::HashMap::new();
        let mut encodings: std::collections::HashMap<String, FileEncoding> =
            std::collections::HashMap::new();
        let mut gaps = Vec::new();
        for item in &result.items {
            let path = item.relative_path.to_string_lossy().replace('\\', "/");
            let fresh = item.status
                == zg_engine::api::context::result::ContextItemStatus::Fresh;
            let sha = if fresh {
                match hashes.get(&path) {
                    Some(known) => Some(known.clone()),
                    None => {
                        // Hashing a fresh item opens its file, so it costs a
                        // read operation whether or not the file was already
                        // read: a known file is still re-read here. Admission
                        // happens first; with no op left the item is dropped
                        // with a gap and the file is never opened.
                        let known = self.ledger.read_files.contains(&path)
                            || reads.contains(&path);
                        let new_files = usize::from(!known);
                        let admitted = self.admit_reads(1, new_files, "search hashing").is_ok();
                        if admitted {
                            self.note_source_read(&path);
                            let source = fs::read(self.ledger.root.join(&item.relative_path));
                            let computed = source
                                .ok()
                                .map(|bytes| {
                                    let encoding = detect_encoding(&bytes);
                                    encodings.insert(path.clone(), encoding);
                                    hex(&Sha256::digest(&bytes))
                                });
                            read_ops += 1;
                            if let Some(hash) = computed.clone() {
                                hashes.insert(path.clone(), hash.clone());
                                reads.push(path.clone());
                            }
                            computed
                        } else {
                            gaps.push(json!({
                                "code": AdapterGapCode::FileBudgetExceeded,
                                "path": path,
                                "detail": "source-read budget reached (32 distinct files / 64 read ops); item dropped without reading this file",
                            }));
                            continue;
                        }
                    }
                }
            } else {
                None
            };
            if !files.contains(&path) {
                files.push(path.clone());
            }
            items.push(json!({
                "rank": item.rank,
                "path": path,
                "sha256": sha,
                "possibly_stale": !fresh,
                "range": [item.range.start_line(), item.range.last_line()],
                "content_range": [item.content_range.start_line(), item.content_range.last_line()],
                "match_ranges": item.match_ranges,
                // Exact source coordinates of `content`. `content_range` above
                // is human-friendly (inclusive last line), while these are the
                // authoritative half-open UTF-8 byte offsets: content is
                // exactly file bytes [start_byte_offset, end_byte_offset).
                // `content_columns` are (start column, end column) where the end
                // column is on the range's exclusive end line, so line/column
                // pairs stay internally consistent with the byte offsets.
                "content_byte_offsets": content_byte_offsets(&item.content_range),
                "content_columns": content_columns(&item.content_range),
                // The engine slices decoded text: byte offsets above are in
                // decoded UTF-8 space (a BOM is stripped). `raw_file_byte_offsets`
                // translate them into raw file bytes so a consumer can slice the
                // file it hashes; null when the encoding makes the mapping
                // non-linear (UTF-16/32), in which case decode first.
                "file_encoding": encodings.get(&path).map_or("unknown", |e| e.name),
                "raw_file_byte_offsets": raw_file_byte_offsets(
                    &item.content_range,
                    encodings.get(&path).copied(),
                ),
                "content_sha256": hex(&Sha256::digest(item.content.as_bytes())),
                // Which declaration the excerpt belongs to. Without this a
                // consumer cannot tell "same file, wrong method" from a hit on
                // the method it asked about, nor which callers/relations the
                // excerpt sits in.
                "declaration": declaration_block(item.metadata.as_ref()),
                "outline": outline_value(item.outline.as_deref(), input.outline),
                "content": item.content,
                "status": item.status,
                "score": item.score,
                "matched_by": format!("{:?}", item.matched_by),
            }));
        }
        let result = json!({
            "query": query,
            "mode": input.mode.clone().unwrap_or_else(|| "hybrid".to_owned()),
            "limit": limit,
            "items": items,
            "gaps": Value::Array(gaps),
        });
        Ok(ToolOutput {
            result,
            files,
            commit: None,
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
        let runtime = tokio::runtime::Runtime::new().map_err(|error| {
            CallError::Execution { code: "runtime".into(), message: error.to_string() }
        })?;
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
        // Metadata-only enumeration: no source file is opened, so no read
        // accounting applies.
        Ok(ToolOutput {
            result: json!({
                "name": name,
                "total": result.total,
                "truncated": result.truncated,
                "items": items,
                "gaps": [],
            }),
            files: Vec::new(),
            commit: None,
        })
    }

    fn facts(&mut self, input: &CallInput) -> Result<ToolOutput, CallError> {
        let path = input.path.clone().unwrap_or_default();
        if path.trim().is_empty() {
            return Err(invalid("facts requires a workspace-relative path"));
        }
        let root = self.ledger.root.clone();
        let relative = resolve_under_root(&root, &path).map_err(|message| invalid(&message))?;
        let display = relative.to_string_lossy().replace('\\', "/");
        // Read admission happens before the file is opened; the bytes are read
        // once and reused for both the text and the recorded identity.
        let already_read = self.ledger.read_files.contains(&display);
        self.admit_reads(1, usize::from(!already_read), "facts")?;
        let bytes = fs::read(root.join(&relative))
            .map_err(|error| invalid(&format!("cannot read {path}: {error}")))?;
        // The source access has happened: account for it before any decoding or
        // parsing step that could fail, so a decode error cannot under-report.
        self.note_source_read(&display);
        let sha = hex(&Sha256::digest(&bytes));
        let text = String::from_utf8(bytes)
            .map_err(|_| invalid(&format!("cannot read {path}: stream did not contain valid UTF-8")))?;
        let exclusions = self.ledger.exclusions.clone();
        let facts = facts::parse_facts(&display, &text, input.method.as_deref(), &exclusions)
            .map_err(|message| invalid(&message))?;
        let files = vec![display.clone()];
        let reads = vec![display.clone()];
        let result = json!({
            "path": display,
            "sha256": sha,
            "facts": facts,
            "gaps": [],
        });
        // A whole-file facts dump can exceed the remaining delivered budget.
        // Degrade to an explicit summary with a gap; never silently truncate.
        // The presentation margin keeps room for the envelope wrapper, so a
        // borderline dump degrades to the summary instead of the call-level
        // budget error.
        // Measure the exact presentation form (pretty) that will be
        // delivered, not the compact form.
        const FACTS_PRESENTATION_MARGIN: usize = 4096;
        if serde_json::to_string_pretty(&result)
            .map(|text| {
                text.len() + self.ledger.delivered_bytes
                    > MAX_DELIVERED_BYTES - FACTS_PRESENTATION_MARGIN
            })
            .unwrap_or(true)
        {
            let summary = facts::summarize(&result);
            return Ok(ToolOutput {
                result: json!({
                    "path": display,
                    "sha256": sha,
                    // The degraded payload is a *different* shape on purpose:
                    // `facts` keeps its full-document contract (lists) and is
                    // null here, while counts are reported separately, so a
                    // consumer never iterates a scalar where a list was
                    // promised.
                    "facts": Value::Null,
                    "facts_summary": summary,
                    "gaps": [{
                        "code": "facts_budget_exceeded",
                        "detail": "full facts did not fit the remaining delivered-content budget; retry with a method filter or inspect facts_summary",
                    }],
                }),
                files,
                commit: None,
            });
        }
        Ok(ToolOutput {
            result,
            files,
            commit: None,
        })
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

        // Read admission: every distinct file the assembler may open is
        // admitted before any read, against BOTH the distinct-file and the
        // read-operation budgets. Requests that cannot be admitted are dropped
        // with an explicit typed gap instead of being opened.
        let mut gaps = Vec::new();
        let mut admitted: Vec<usize> = Vec::new();
        let mut admitted_files: Vec<String> = Vec::new();
        let mut admitted_ops = 0usize;
        for (index, request) in input.requests.iter().enumerate() {
            let path = request
                .relative_path
                .to_string_lossy()
                .replace('\\', "/");
            // Each served span is one open of its file, so every request needs
            // its own read operation even when several spans share a path.
            let known = self.ledger.read_files.contains(&path) || admitted_files.contains(&path);
            let new_files = usize::from(!known);
            if self
                .admit_reads(1, new_files, "evidence")
                .and_then(|()| {
                    if admitted_ops + 1 > self.remaining_read_ops() {
                        return Err(CallError::Execution {
                            code: "read_budget_exceeded".into(),
                            message: "remaining read operations exhausted".into(),
                        });
                    }
                    Ok(())
                })
                .is_err()
            {
                gaps.push(json!({
                    "code": AdapterGapCode::FileBudgetExceeded,
                    "path": path,
                    "detail": "source-read budget reached (32 distinct files / 64 read ops); span not requested",
                }));
                continue;
            }
            admitted_ops += 1;
            if new_files == 1 {
                admitted_files.push(path.clone());
            }
            admitted.push(index);
        }
        // Admission is per request (one served span = one open), not per path:
        // two spans of the same file are two operations.
        let spans: Vec<SpanRequest> = input
            .requests
            .iter()
            .enumerate()
            .filter(|(index, _request)| admitted.contains(index))
            .map(|(_, request)| request.clone())
            .collect();
        let pack = surface::assemble_spans(&root, &spans, budget).map_err(|error| {
            CallError::Execution {
                code: "evidence".into(),
                message: error.to_string(),
            }
        })?;

        let mut items = Vec::new();
        let mut files = Vec::new();
        let mut reads = Vec::new();
        let mut read_ops = 0usize;
        for item in &pack.items {
            let path = item.relative_path.to_string_lossy().replace('\\', "/");
            if !files.contains(&path) {
                files.push(path.clone());
            }
            if !reads.contains(&path) {
                reads.push(path.clone());
            }
            // Each served span is one open of its file, even when several
            // spans share a path. Recorded at the open so a later serialization
            // failure cannot hide it.
            self.note_source_read(&path);
            read_ops += 1;
            items.push(json!({
                "project": item.project,
                "path": path,
                "sha256": item.source_sha256,
                "verification": item.verification,
                "range": [item.range.start_line(), item.range.last_line()],
                // Evidence slices raw file bytes, so these offsets address the
                // exact file version `sha256` identifies (unlike search, whose
                // decoded-text offsets are reported separately).
                "content_byte_offsets": content_byte_offsets(&item.range),
                "content_columns": content_columns(&item.range),
                "content_sha256": hex(&Sha256::digest(item.content.as_bytes())),
                "content": item.content,
            }));
        }
        // Files whose span was served as a stale/gap entry were still
        // opened by the assembler: count those reads honestly.
        for gap in &pack.gaps {
            let path = gap
                .relative_path
                .to_string_lossy()
                .replace('\\', "/");
            let opened = !matches!(
                gap.code,
                surface_gap_codes::Missing | surface_gap_codes::OutOfRoot | surface_gap_codes::Traversal
                    | surface_gap_codes::AbsolutePath | surface_gap_codes::InvalidProject | surface_gap_codes::Symlink
            );
            if opened {
                if !reads.contains(&path) {
                    reads.push(path.clone());
                }
                // A gap path that was opened without producing an item is a
                // real source access: reserve and record its operation. When an
                // item for the same path was already served, the assembler
                // opened it for that item and the op is already counted.
                if !files.contains(&path) {
                    self.note_source_read(&path);
                    read_ops += 1;
                }
            }
        }
        let engine_gaps = serde_json::to_value(&pack.gaps).map_err(|e| {
            CallError::Execution { code: "serialize".into(), message: e.to_string() }
        })?;
        let mut all_gaps = match engine_gaps {
            Value::Array(entries) => entries,
            _ => Vec::new(),
        };
        all_gaps.extend(gaps);

        let status = format!("{:?}", pack.status).to_lowercase();
        let id = self.ledger.packs.len() + 1;
        let file_name = format!("pack-{call_index}-{id}.json");
        let result = json!({
            "pack": {
                "id": id,
                "sha256": Value::Null,
                "file": file_name,
            },
            "status": status,
            "items": items,
            "gaps": Value::Array(all_gaps),
            "deduplicated": pack.deduplicated,
        });
        // The stored pack file is the real `SpanPack` so revalidation can
        // round-trip it; publication is deferred to the caller's
        // commit-after-fit-check.
        let payload = serde_json::to_string(&pack).map_err(|e| {
            CallError::Execution { code: "serialize".into(), message: e.to_string() }
        })?;
        let sha = hex(&Sha256::digest(payload.as_bytes()));
        Ok(ToolOutput {
            result,
            files,
            commit: Some(PackCommit {
                kind: "evidence".into(),
                sha256: sha,
                items: pack.items.len(),
                bytes: payload.len(),
                payload,
                file_name,
            }),
        })
    }

    fn verify(&mut self, input: &CallInput) -> Result<ToolOutput, CallError> {
        let id = input.pack_id.unwrap_or(self.ledger.packs.len());
        let (kind, payload) = self
            .load_pack(input.pack_id)
            .map_err(|message| invalid(&message))?;
        let root = self.ledger.root.clone();
        // Revalidation re-opens every pack span. Admit the whole operation
        // before reading anything, so verification can never bypass the read
        // budget by re-reading what an earlier call already opened.
        let (verify_ops, verify_new_files) = match &payload {
            PackPayload::Spans(pack) => {
                let mut new_paths: Vec<String> = Vec::new();
                for item in &pack.items {
                    let path = item.relative_path.to_string_lossy().replace('\\', "/");
                    if !self.ledger.read_files.contains(&path) && !new_paths.contains(&path) {
                        new_paths.push(path);
                    }
                }
                (pack.items.len(), new_paths.len())
            }
            PackPayload::Workflow(spans) => {
                let mut new_paths: Vec<String> = Vec::new();
                for span in spans {
                    if !self.ledger.read_files.contains(&span.path)
                        && !new_paths.contains(&span.path)
                    {
                        new_paths.push(span.path.clone());
                    }
                }
                (spans.len(), new_paths.len())
            }
        };
        self.admit_reads(verify_ops, verify_new_files, "verify")?;
        // Both pack families are revalidated against the live source. Evidence
        // packs re-check recorded bytes; workflow packs re-hash the selected
        // spans against their recorded source identity.
        let (gaps, item_paths) = match &payload {
            PackPayload::Spans(pack) => {
                let gaps = surface::revalidate_spans(&root, pack).map_err(|error| {
                    CallError::Execution {
                        code: "verify".into(),
                        message: error.to_string(),
                    }
                })?;
                let paths = pack
                    .items
                    .iter()
                    .map(|item| item.relative_path.to_string_lossy().replace('\\', "/"))
                    .collect::<Vec<_>>();
                let gaps: Vec<serde_json::Value> = gaps
                    .into_iter()
                    .map(|gap| serde_json::to_value(gap).unwrap_or(serde_json::Value::Null))
                    .collect();
                (gaps, paths)
            }
            PackPayload::Workflow(spans) => {
                let mut gaps: Vec<serde_json::Value> = Vec::new();
                for span in spans {
                    let absolute = root.join(&span.path);
                    let actual = fs::read(&absolute).ok().map(|bytes| hex(&Sha256::digest(&bytes)));
                    match actual {
                        Some(hash) if hash == span.source_sha256 => {}
                        Some(_) => gaps.push(json!({
                            "code": "stale_hash",
                            "path": span.path,
                            "detail": "source changed since the workflow selected this span",
                        })),
                        None => gaps.push(json!({
                            "code": "missing",
                            "path": span.path,
                            "detail": "selected span file is no longer readable",
                        })),
                    }
                }
                let paths = spans.iter().map(|span| span.path.clone()).collect::<Vec<_>>();
                (gaps, paths)
            }
        };
        // Revalidation re-opens every pack item file: honest read accounting,
        // recorded at the open so a later failure path keeps the truth.
        let mut reads = Vec::new();
        let mut read_ops = 0usize;
        for path in &item_paths {
            if !reads.contains(path) {
                reads.push(path.clone());
            }
            self.note_source_read(path);
            read_ops += 1;
        }
        // Current validity: a failed revalidation clears the persisted
        // verified state; the historical verification event stays in history.
        let clean = gaps.is_empty();
        let _ = &kind;
        if let Some(record) = self.ledger.packs.get_mut(id.saturating_sub(1)) {
            record.verified = clean;
        }
        Ok(ToolOutput {
            result: json!({
                "pack_id": id,
                "kind": kind,
                "verified": clean,
                "gaps": gaps,
            }),
            files: Vec::new(),
            commit: None,
        })
    }

    fn load_pack(&self, pack_id: Option<usize>) -> Result<(String, PackPayload), String> {
        let id = pack_id.unwrap_or(self.ledger.packs.len());
        if id == 0 || id > self.ledger.packs.len() {
            return Err(format!("pack {id} does not exist in this session"));
        }
        let record = &self.ledger.packs[id - 1];
        let packs_dir = self.dir.join(PACKS_DIR);
        let suffix = format!("-{}.json", record.id);
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
                // Pack identity: the file contents must match the recorded
                // SHA-256; a mismatch is tamper, fail closed.
                let actual = hex(&Sha256::digest(content.as_bytes()));
                if actual != record.sha256 {
                    return Err(format!(
                        "pack file {text} does not match its recorded identity; refusing to load a tampered pack"
                    ));
                }
                let kind = record.kind.clone();
                let payload = if kind == "workflow" {
                    let value: serde_json::Value =
                        serde_json::from_str(&content).map_err(|error| error.to_string())?;
                    let mut spans = Vec::new();
                    for span in value
                        .get("selected")
                        .and_then(|selected| selected.as_array())
                        .into_iter()
                        .flatten()
                    {
                        let (Some(path), Some(start), Some(end), Some(sha)) = (
                            span.get("path").and_then(|v| v.as_str()),
                            span.get("start_line").and_then(|v| v.as_u64()),
                            span.get("end_line").and_then(|v| v.as_u64()),
                            span.get("source_sha256").and_then(|v| v.as_str()),
                        ) else {
                            continue;
                        };
                        spans.push(WorkflowSpanRef {
                            path: path.to_owned(),
                            start_line: start as usize,
                            end_line: end as usize,
                            source_sha256: sha.to_owned(),
                        });
                    }
                    PackPayload::Workflow(spans)
                } else {
                    PackPayload::Spans(
                        serde_json::from_str(&content).map_err(|error| error.to_string())?,
                    )
                };
                return Ok((kind, payload));
            }
        }
        Err(format!("pack file for id {id} is missing"))
    }

    fn workflow(&mut self, input: &CallInput) -> Result<ToolOutput, CallError> {
        let question = input.question.clone().unwrap_or_default();
        if question.trim().is_empty() {
            return Err(invalid("workflow requires a non-empty question"));
        }
        // Engine-managed work draws from the *remaining* session budget: no
        // invocation re-grants a full pool. Internal calls share the frozen
        // 6-call pool with external calls; reads and files draw from the
        // remaining distinct-file budgets.
        let remaining_calls = MAX_CALLS
            .saturating_sub(self.ledger.calls_used)
            .saturating_sub(self.ledger.internal_calls);
        if remaining_calls == 0 {
            return Err(CallError::Execution {
                code: "no_calls_left".into(),
                message: "no call budget remains for engine-managed work".to_owned(),
            });
        }
        let remaining_reads = self
            .remaining_read_files()
            .min(self.remaining_read_ops());
        if remaining_reads == 0 {
            return Err(CallError::Execution {
                code: "read_budget_exceeded".into(),
                message: "no source-read budget remains for engine-managed work".to_owned(),
            });
        }
        let budget = WorkflowBudget {
            max_rounds: MAX_ROUNDS.saturating_sub(self.ledger.rounds_used).max(1),
            max_tool_calls: remaining_calls,
            max_files: remaining_reads.max(1),
            max_source_reads: remaining_reads.max(1),
            hybrid_quota: remaining_calls.min(4),
            fts_quota: remaining_calls.min(2),
            ..WorkflowBudget::default()
        };
        let root = self.ledger.root.clone();
        let engine = ZvecGrep::new();
        let mut source = source::EngineSource::new(engine, root.clone());
        source::enable_symbols(&mut source);
        // Plan provenance is computed BEFORE execution and delivered with the
        // result, so a receipt can prove which plan produced the pack.
        let plan_identity = input.plan.as_ref().map(|plan| {
            json!({
                "source": "preregistered-plan",
                "sha256": input.plan_sha256,
                "queries": plan.queries.len(),
                "symbols": plan.symbols.len(),
                "scopes": plan.scopes.len(),
            })
        }).unwrap_or_else(|| json!({
            "source": "question-only",
            "sha256": Value::Null,
            "note": "diagnostic arm: no preregistered plan was supplied",
        }));
        let outcome = match &input.plan {
            Some(plan) => workflow::run_with_plan(
                &mut source,
                &root,
                &question,
                budget.clone(),
                plan,
            ),
            None => workflow::run(&mut source, &root, &question, budget.clone()),
        }
        .map_err(|message| CallError::Execution {
            code: "workflow".into(),
            message,
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
            "plan": plan_identity,
            "internal_accounting": {
                "note": "engine-internal calls and reads draw from the same cumulative session pool; internal reads are not delivered tool content and are reported here for explicit accounting",
                "tool_calls": stats.tool_calls,
                "source_reads": stats.source_reads,
                "read_paths": stats.read_paths,
                "files_seen": stats.files_seen,
                "ranked_content_bytes": stats.ranked_content_bytes,
                "budget_granted": {
                    "max_tool_calls": budget.max_tool_calls,
                    "max_source_reads": budget.max_source_reads,
                    "max_files": budget.max_files,
                },
            },
        });
        self.ledger.internal_calls += stats.tool_calls;
        // Accounting identity: the workflow *read* these files (open + decode);
        // the selected spans are only what it delivered. Record each actual
        // read now, so the distinct-file and read-op budgets reflect real work
        // even if a later step fails.
        for path in &stats.read_paths {
            self.note_source_read(&path.replace('\\', "/"));
        }
        let serialized = serde_json::to_string(&result).map_err(|e| {
            CallError::Execution { code: "serialize".into(), message: e.to_string() }
        })?;
        let sha = hex(&Sha256::digest(serialized.as_bytes()));
        let id = self.ledger.packs.len() + 1;
        let file_name = format!("pack-workflow-{id}.json");
        Ok(ToolOutput {
            result,
            files: files.clone(),
            commit: Some(PackCommit {
                kind: "workflow".into(),
                sha256: sha,
                items: selected.len(),
                bytes: serialized.len(),
                payload: serialized,
                file_name,
            }),
        })
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
                "internal_calls": self.ledger.internal_calls,
                "calls_max": MAX_CALLS,
                "rounds_used": self.ledger.rounds_used,
                "rounds_max": MAX_ROUNDS,
                "distinct_files": self.ledger.distinct_files.len(),
                "distinct_files_max": MAX_DISTINCT_FILES,
                "read_files": self.ledger.read_files.len(),
                "read_files_max": MAX_DISTINCT_READS,
                "read_ops": self.ledger.read_ops,
                "read_ops_max": MAX_READ_OPS,
                "delivered_bytes": self.ledger.delivered_bytes,
                "delivered_bytes_max": MAX_DELIVERED_BYTES,
                "pack_bytes_max": MAX_PACK_BYTES,
                "pack_items_max": MAX_PACK_ITEMS,
            },
            "files": self.ledger.distinct_files,
            "read_files": self.ledger.read_files,
            "packs": self.ledger.packs,
            "history": self.ledger.history,
            "exclusions": self.ledger.exclusions,
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

/// Surface gap codes that did not open the file at all.
mod surface_gap_codes {
    pub use zg_engine::api::evidence::EvidenceGapCode as Code;
    pub const Missing: Code = Code::Missing;
    pub const OutOfRoot: Code = Code::OutOfRoot;
    pub const Traversal: Code = Code::Traversal;
    pub const AbsolutePath: Code = Code::AbsolutePath;
    pub const InvalidProject: Code = Code::InvalidProject;
    pub const Symlink: Code = Code::Symlink;
}

fn envelope_ok_verified(text: &str) -> bool {
    serde_json::from_str::<Value>(text)
        .ok()
        .and_then(|value| {
            value
                .get("result")
                .and_then(|result| result.get("verified"))
                .and_then(Value::as_bool)
        })
        .unwrap_or(false)
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

/// Only materialized when the caller asks for it: outlines are discovery
/// aids (member lists/calls) and cost up to 3.6 KB per item.
fn outline_value(outline: Option<&str>, include: bool) -> serde_json::Value {
    match (include, outline) {
        (true, Some(text)) => json!(text),
        _ => serde_json::Value::Null,
    }
}

/// Compact identity of the declaration an excerpt belongs to.
fn declaration_block(
    metadata: Option<&zg_engine::api::context::result::EntityMetadata>,
) -> serde_json::Value {
    let Some(zg_engine::api::context::result::EntityMetadata::Code(code)) = metadata else {
        return serde_json::Value::Null;
    };
    let mut block = serde_json::Map::new();
    if let Some(kind) = &code.symbol_type {
        block.insert("symbol_type".into(), json!(format!("{kind:?}").to_lowercase()));
    }
    if let Some(name) = &code.symbol_name {
        block.insert("symbol_name".into(), json!(name));
    }
    if let Some(scope) = &code.scope {
        block.insert("scope".into(), json!(scope));
    }
    if let Some(signature) = &code.signature {
        block.insert("signature".into(), json!(signature));
    }
    if let Some(visibility) = &code.visibility {
        block.insert("visibility".into(), json!(format!("{visibility:?}").to_lowercase()));
    }
    serde_json::Value::Object(block)
}

fn content_byte_offsets(range: &zg_engine::api::context::result::ContentRange) -> serde_json::Value {
    use zg_engine::api::context::result::ContentRange;
    match range {
        ContentRange::Text {
            start_byte_offset,
            end_byte_offset,
            ..
        } => json!([start_byte_offset, end_byte_offset]),
        ContentRange::Byte {
            start_offset,
            end_offset,
        } => json!([start_offset, end_offset]),
        ContentRange::File => serde_json::Value::Null,
    }
}

/// Raw-file byte offsets equivalent to the engine's decoded-text offsets, or
/// null when a BOM shift cannot represent them linearly (UTF-16/32).
fn raw_file_byte_offsets(
    range: &zg_engine::api::context::result::ContentRange,
    encoding: Option<FileEncoding>,
) -> serde_json::Value {
    use zg_engine::api::context::result::ContentRange;
    let ContentRange::Text {
        start_byte_offset,
        end_byte_offset,
        ..
    } = range
    else {
        return serde_json::Value::Null;
    };
    let Some(encoding) = encoding else {
        return serde_json::Value::Null;
    };
    if !encoding.linear {
        return serde_json::Value::Null;
    }
    json!([
        start_byte_offset + encoding.bom_bytes,
        end_byte_offset + encoding.bom_bytes
    ])
}

fn content_columns(range: &zg_engine::api::context::result::ContentRange) -> serde_json::Value {
    use zg_engine::api::context::result::ContentRange;
    match range {
        ContentRange::Text {
            start_byte_column,
            end_byte_column,
            ..
        } => json!([start_byte_column, end_byte_column]),
        _ => serde_json::Value::Null,
    }
}

fn file_sha256(path: &Path) -> Option<String> {
    let bytes = fs::read(path).ok()?;
    Some(hex(&Sha256::digest(&bytes)))
}

/// Encoding prefix detected from the raw file bytes. `bom_bytes` is the number
/// of leading bytes that the engine strips before computing decoded-text byte
/// offsets: 3 for a UTF-8 BOM, and the full BOM for UTF-16/32 (where the
/// decoded offset space is not a linear shift of the raw file).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct FileEncoding {
    name: &'static str,
    bom_bytes: usize,
    linear: bool,
}

fn detect_encoding(bytes: &[u8]) -> FileEncoding {
    const UTF8: FileEncoding = FileEncoding { name: "utf-8", bom_bytes: 0, linear: true };
    const UTF8_BOM: FileEncoding = FileEncoding { name: "utf-8-bom", bom_bytes: 3, linear: true };
    if bytes.starts_with(&[0xff, 0xfe, 0x00, 0x00]) {
        return FileEncoding { name: "utf-32le", bom_bytes: 4, linear: false };
    }
    if bytes.starts_with(&[0x00, 0x00, 0xfe, 0xff]) {
        return FileEncoding { name: "utf-32be", bom_bytes: 4, linear: false };
    }
    if bytes.starts_with(&[0xff, 0xfe]) {
        return FileEncoding { name: "utf-16le", bom_bytes: 2, linear: false };
    }
    if bytes.starts_with(&[0xfe, 0xff]) {
        return FileEncoding { name: "utf-16be", bom_bytes: 2, linear: false };
    }
    if bytes.starts_with(&[0xef, 0xbb, 0xbf]) {
        return UTF8_BOM;
    }
    UTF8
}

/// SHA-256 of arbitrary bytes, lowercase hex. Public so the CLI can record the
/// plan-artifact identity without duplicating hashing logic.
#[must_use]
pub fn sha256_hex(bytes: &[u8]) -> String {
    hex(&Sha256::digest(bytes))
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        let _ = write!(out, "{byte:02x}");
    }
    out
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
            Ok(resolved.strip_prefix(root).unwrap_or(&resolved).to_path_buf())
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
                    .ok_or_else(|| "lines address needs start_line".to_owned())? as usize;
                let end_line = address
                    .get("end_line")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| "lines address needs end_line".to_owned())? as usize;
                surface::SpanAddress::Lines {
                    start_line,
                    end_line,
                }
            }
            "bytes" => {
                let start = address
                    .get("start")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| "bytes address needs start".to_owned())? as usize;
                let end = address
                    .get("end")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| "bytes address needs end".to_owned())? as usize;
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
