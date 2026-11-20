//! `r4-adapter` — private experimental CLI for the R4 external-agent arms.
//!
//! Usage:
//! ```text
//! r4-adapter session new --dir <session-dir> --root <corpus-root> --profile arm-a|arm-b|arm-c --case D01
//! r4-adapter call <session-dir> search  --query Q [--mode hybrid|fts|vector|rg] [--limit N] [--scope S] [--round R]
//! r4-adapter call <session-dir> symbol  --name N [--round R]
//! r4-adapter call <session-dir> facts   --path P [--round R]
//! r4-adapter call <session-dir> evidence --requests <file.json|-> [--round R]
//! r4-adapter call <session-dir> verify   [--pack-id N] [--round R]
//! r4-adapter call <session-dir> workflow --question Q [--round R]
//! r4-adapter call <session-dir> status
//! r4-adapter session finalize <session-dir>
//! r4-adapter session cancel <session-dir>
//! r4-adapter session receipt <session-dir>
//! ```
//!
//! Every `call` prints one JSON envelope on stdout and appends it to the
//! session trace. The ledger is authoritative across the whole session.

use std::io::Read as _;
use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Parser, Subcommand};
use serde_json::Value;

use zg_r4_adapter::{CallInput, Profile, Session, Tool, parse_span_requests};

#[derive(Parser)]
#[command(name = "r4-adapter", about = "Private R4 external-agent experimental adapter")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Creates a fresh session with an authoritative cumulative ledger.
    Session {
        #[command(subcommand)]
        session: SessionCommand,
    },
    /// Executes one tool call under the session budget.
    Call {
        /// Session directory containing ledger.json.
        dir: PathBuf,
        #[command(subcommand)]
        tool: ToolCommand,
    },
}

#[derive(Subcommand)]
enum SessionCommand {
    /// Creates the session directory and ledger.
    New {
        #[arg(long)]
        dir: PathBuf,
        #[arg(long)]
        root: PathBuf,
        #[arg(long)]
        profile: String,
        #[arg(long)]
        case: String,
        /// Excluded-variant path marker (repeatable). Defaults to the frozen
        /// benchmark scope list (`_old`, `_old1`, `NewSolution`).
        #[arg(long = "exclude")]
        exclude: Vec<String>,
    },
    /// Marks the session terminal and writes the receipt.
    Finalize { dir: PathBuf },
    /// Cancels the session (terminal) and writes the receipt.
    Cancel { dir: PathBuf },
    /// Re-prints the frozen receipt.
    Receipt { dir: PathBuf },
}

#[derive(Subcommand)]
enum ToolCommand {
    Search {
        #[arg(long)]
        query: String,
        #[arg(long)]
        mode: Option<String>,
        #[arg(long)]
        limit: Option<usize>,
        #[arg(long)]
        scope: Option<String>,
        #[arg(long)]
        round: Option<usize>,
    },
    Symbol {
        #[arg(long)]
        name: String,
        #[arg(long)]
        round: Option<usize>,
    },
    Facts {
        #[arg(long)]
        path: String,
        /// Optional method-name filter so large files stay within budget.
        #[arg(long)]
        method: Option<String>,
        #[arg(long)]
        round: Option<usize>,
    },
    Evidence {
        /// JSON file with the span request array, or `-` for stdin.
        #[arg(long)]
        requests: String,
        #[arg(long)]
        round: Option<usize>,
    },
    Verify {
        #[arg(long)]
        pack_id: Option<usize>,
        #[arg(long)]
        round: Option<usize>,
    },
    Workflow {
        #[arg(long)]
        question: String,
        #[arg(long)]
        round: Option<usize>,
    },
    Status,
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    
    match cli.command {
        Command::Session { session } => match session {
            SessionCommand::New {
                dir,
                root,
                profile,
                case,
                exclude,
            } => {
                let Some(profile) = Profile::parse(&profile) else {
                    return fail("profile must be arm-a, arm-b or arm-c");
                };
                let session = if exclude.is_empty() {
                    Session::new(&dir, &root, profile, &case)
                } else {
                    Session::new_configured(&dir, &root, profile, &case, exclude)
                };
                match session {
                    Ok(session) => print_json(serde_json::json!({
                        "ok": true,
                        "session_id": session.ledger.session_id,
                        "case": session.ledger.case,
                        "profile": session.ledger.profile,
                        "root": session.ledger.root.display().to_string(),
                    })),
                    Err(message) => fail(&message),
                }
            }
            SessionCommand::Finalize { dir } => run_session_op(&dir, |session| {
                session.finalize(false)
            }),
            SessionCommand::Cancel { dir } => run_session_op(&dir, |session| {
                session.finalize(true)
            }),
            SessionCommand::Receipt { dir } => match Session::open(&dir) {
                Ok(session) => print_receipt(&session),
                Err(message) => fail(&message),
            },
        },
        Command::Call { dir, tool } => run_call(&dir, tool),
    }
}

fn run_session_op(
    dir: &std::path::Path,
    operation: impl FnOnce(&mut Session) -> Result<String, String>,
) -> ExitCode {
    match Session::open(dir) {
        Ok(mut session) => match operation(&mut session) {
            Ok(text) => {
                println!("{text}");
                ExitCode::SUCCESS
            }
            Err(message) => fail(&message),
        },
        Err(message) => fail(&message),
    }
}

fn print_receipt(session: &Session) -> ExitCode {
    match session.write_receipt() {
        Ok(text) => {
            println!("{text}");
            ExitCode::SUCCESS
        }
        Err(message) => fail(&message),
    }
}

fn run_call(dir: &std::path::Path, tool: ToolCommand) -> ExitCode {
    let Ok(mut session) = Session::open(dir) else {
        return fail("cannot open session: ledger.json missing or corrupt");
    };
    let input = match build_input(tool) {
        Ok(input) => input,
        Err(message) => return fail(&message),
    };
    match session.call(input) {
        Ok(envelope) => {
            println!("{envelope}");
            ExitCode::SUCCESS
        }
        Err(message) => fail(&message),
    }
}

fn build_input(tool: ToolCommand) -> Result<CallInput, String> {
    Ok(match tool {
        ToolCommand::Search {
            query,
            mode,
            limit,
            scope,
            round,
        } => CallInput {
            tool: Tool::Search,
            round: round.unwrap_or(0),
            query: Some(query),
            mode,
            limit,
            scope,
            name: None,
            path: None,
            method: None,
            requests: Vec::new(),
            pack_id: None,
            question: None,
        },
        ToolCommand::Symbol { name, round } => CallInput {
            tool: Tool::Symbol,
            round: round.unwrap_or(0),
            query: None,
            mode: None,
            limit: None,
            scope: None,
            name: Some(name),
            path: None,
            method: None,
            requests: Vec::new(),
            pack_id: None,
            question: None,
        },
        ToolCommand::Facts {
            path,
            method,
            round,
        } => CallInput {
            tool: Tool::Facts,
            round: round.unwrap_or(0),
            query: None,
            mode: None,
            limit: None,
            scope: None,
            name: None,
            path: Some(path),
            method,
            requests: Vec::new(),
            pack_id: None,
            question: None,
        },
        ToolCommand::Evidence { requests, round } => {
            let text = if requests == "-" {
                let mut buffer = String::new();
                std::io::stdin()
                    .read_to_string(&mut buffer)
                    .map_err(|error| format!("cannot read stdin: {error}"))?;
                buffer
            } else {
                std::fs::read_to_string(&requests)
                    .map_err(|error| format!("cannot read {requests}: {error}"))?
            };
            let value: Value = serde_json::from_str(&text)
                .map_err(|error| format!("invalid span request JSON: {error}"))?;
            CallInput {
                tool: Tool::Evidence,
                round: round.unwrap_or(0),
                query: None,
                mode: None,
                limit: None,
                scope: None,
                name: None,
                path: None,
                method: None,
                requests: parse_span_requests(&value)?,
                pack_id: None,
                question: None,
            }
        }
        ToolCommand::Verify { pack_id, round } => CallInput {
            tool: Tool::Verify,
            round: round.unwrap_or(0),
            query: None,
            mode: None,
            limit: None,
            scope: None,
            name: None,
            path: None,
            method: None,
            requests: Vec::new(),
            pack_id,
            question: None,
        },
        ToolCommand::Workflow { question, round } => CallInput {
            tool: Tool::Workflow,
            round: round.unwrap_or(0),
            query: None,
            mode: None,
            limit: None,
            scope: None,
            name: None,
            path: None,
            method: None,
            requests: Vec::new(),
            pack_id: None,
            question: Some(question),
        },
        ToolCommand::Status => CallInput {
            tool: Tool::Status,
            round: 0,
            query: None,
            mode: None,
            limit: None,
            scope: None,
            name: None,
            path: None,
            method: None,
            requests: Vec::new(),
            pack_id: None,
            question: None,
        },
    })
}

fn print_json(value: Value) -> ExitCode {
    match serde_json::to_string_pretty(&value) {
        Ok(text) => {
            println!("{text}");
            ExitCode::SUCCESS
        }
        Err(message) => fail(&message.to_string()),
    }
}

fn fail(message: &str) -> ExitCode {
    println!(
        "{}",
        serde_json::json!({ "ok": false, "error": message })
    );
    ExitCode::FAILURE
}
