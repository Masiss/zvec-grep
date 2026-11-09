//! Metadata-only exact-symbol enumeration.
//!
//! Lookup is by exact declaration name over indexed `symbol_name` metadata. It
//! never uses full-text ranking, embeddings, source files or a call graph, and
//! it returns declarations only. Reference occurrences are not declarations and
//! are never promoted into this result.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use super::context::{
    options::RefreshPolicy,
    result::{ContentRange, ContextItemStatus, Language},
};
use crate::domain::SymbolType;

/// Options accepted by [`crate::ZvecGrep::symbols`].
#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct SymbolsOptions {
    /// Workspace root. `None` uses the process working directory.
    pub root: Option<PathBuf>,
    /// Exact, case-sensitive declaration name.
    pub name: String,
    /// Exact scope breadcrumb, for example `Acme.Core::Widget`.
    pub scope: Option<String>,
    /// Restrict results to these declaration categories; empty means all.
    pub symbol_types: Vec<SymbolType>,
    /// Exact workspace-relative path using `/` separators.
    pub path: Option<String>,
    /// Maximum returned declarations. Defaults to 50 and is capped at 500.
    pub limit: Option<usize>,
    /// Refresh policy. `Wait` reconciles the index before the query; `Off` uses
    /// the stored index as-is and may expose stale metadata.
    pub refresh: Option<RefreshPolicy>,
    /// Uses the legacy auto-update behavior when `refresh` is absent.
    pub auto_update: bool,
    /// Maximum time to wait for workspace admission, in milliseconds.
    pub lock_timeout_ms: Option<u64>,
    /// Allows remote embedding when a refresh needs it, without persisting a grant.
    pub allow_remote: bool,
    /// Exact remote destinations approved for this refresh, without persisting grants.
    pub authorized_remote: Vec<crate::authorization::IndexAuthorization>,
    /// Remote embedding key used only by a refresh and never saved.
    pub api_key: Option<String>,
    /// Index-only embedding concurrency for a refresh.
    pub embedding_concurrency: Option<usize>,
    /// Device override used only by a refresh.
    pub device: Option<crate::api::index::options::Device>,
    /// Model cache override used only by a refresh.
    pub model_cache: Option<PathBuf>,
    /// Runtime-only cooperative cancellation for this request.
    #[serde(skip)]
    pub signal: Option<tokio_util::sync::CancellationToken>,
}

impl SymbolsOptions {
    pub(crate) fn effective_limit(&self) -> crate::EngineResult<usize> {
        let limit = self.limit.unwrap_or(50);
        if limit == 0 || limit > 500 {
            return Err(crate::EngineError::invalid_argument(
                "symbol result limit must be between 1 and 500",
            ));
        }
        Ok(limit)
    }

    pub(crate) fn normalized_name(&self) -> crate::EngineResult<&str> {
        let name = self.name.trim();
        if name.is_empty() {
            return Err(crate::EngineError::invalid_argument(
                "symbol name must not be empty",
            ));
        }
        Ok(name)
    }
}

/// Distinguishes a declaration from a reference occurrence.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SymbolRole {
    Definition,
    Reference,
}

/// One exact-name declaration. `content` is intentionally absent.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct SymbolItem {
    pub entity_id: String,
    pub name: String,
    pub symbol_type: Option<SymbolType>,
    pub scope: Option<String>,
    pub signature: Option<String>,
    pub language: Option<Language>,
    pub relative_path: PathBuf,
    pub absolute_path: PathBuf,
    /// Source range of the declaration, excluding any surrounding comments.
    pub range: ContentRange,
    /// Always [`SymbolRole::Definition`] for metadata-only enumeration.
    pub role: SymbolRole,
    /// Source freshness relative to the stored snapshot.
    pub status: ContextItemStatus,
}

/// Result of one exact-symbol query.
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct SymbolsResult {
    pub root: PathBuf,
    pub name: String,
    /// Total matching declarations before `limit`.
    pub total: usize,
    /// Whether `items` is a strict prefix of `total`.
    pub truncated: bool,
    pub items: Vec<SymbolItem>,
}

impl SymbolsResult {
    /// Returns whether any returned declaration may be stale.
    #[must_use]
    pub fn has_stale_items(&self) -> bool {
        self.items
            .iter()
            .any(|item| item.status == ContextItemStatus::PossiblyStale)
    }
}
