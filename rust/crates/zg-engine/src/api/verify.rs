//! Read-only deep integrity verification of the active workspace generation.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use zg_integrity_native::IntegrityReport;

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct VerifyOptions {
    /// Workspace to verify. `None` uses the working directory.
    pub root: Option<PathBuf>,
    /// Treat incomplete coverage as a failure (native verifier already reports it).
    pub require_complete: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum VerifyOutcome {
    /// Complete verification with no corruption.
    Pass,
    /// Corruption, generation-identity mismatch or other fail-closed condition.
    Corrupt,
    /// Unavailable, uninitialized, unstable, legacy or coverage-incomplete.
    Incomplete,
}

impl VerifyOutcome {
    #[must_use]
    pub const fn exit_code(self) -> u8 {
        match self {
            Self::Pass => 0,
            Self::Corrupt => 1,
            Self::Incomplete => 2,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct VerifyResult {
    pub root: PathBuf,
    pub home: PathBuf,
    pub index_path: PathBuf,
    pub expected_generation: Option<String>,
    pub native_available: bool,
    pub native_abi: Option<u32>,
    pub outcome: VerifyOutcome,
    pub report: Option<IntegrityReport>,
    /// Fail-closed or unavailability reason when no native report applies.
    pub message: Option<String>,
}

impl VerifyResult {
    pub(crate) fn unavailable(
        root: PathBuf,
        home: PathBuf,
        index_path: PathBuf,
        expected_generation: Option<String>,
        message: impl Into<String>,
    ) -> Self {
        Self {
            root,
            home,
            index_path,
            expected_generation,
            native_available: false,
            native_abi: None,
            outcome: VerifyOutcome::Incomplete,
            report: None,
            message: Some(message.into()),
        }
    }

    pub(crate) fn corrupt(
        root: PathBuf,
        home: PathBuf,
        index_path: PathBuf,
        expected_generation: Option<String>,
        message: impl Into<String>,
    ) -> Self {
        Self {
            root,
            home,
            index_path,
            expected_generation,
            native_available: false,
            native_abi: None,
            outcome: VerifyOutcome::Corrupt,
            report: None,
            message: Some(message.into()),
        }
    }

    #[must_use]
    pub const fn exit_code(&self) -> u8 {
        self.outcome.exit_code()
    }
}
