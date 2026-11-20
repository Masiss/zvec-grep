//! Engine-backed lexical/indexed discovery backend for the arm-b workflow.
//!
//! Modeled on the R4b paired harness (`zg-engine/examples/r4b_paired.rs`):
//! lexical search via the independent rg backend, hybrid/FTS via indexed
//! routes, exact symbols via metadata enumeration, file reads under the root.

use std::collections::HashMap;
use std::path::PathBuf;

use zg_engine::api::context::ContextOptions;
use zg_engine::api::context::options::{ContextRoute, ContextRouteMode, RefreshPolicy};
use zg_engine::api::symbols::SymbolsOptions;
use zg_engine::rag::workflow::{EvidenceSource, SearchUnit};
use zg_engine::ZvecGrep;

pub struct EngineSource {
    runtime: tokio::runtime::Runtime,
    engine: ZvecGrep,
    root: PathBuf,
    symbols_supported: bool,
    entity_ranges: HashMap<String, Vec<SearchUnit>>,
}

impl EngineSource {
    #[must_use]
    pub fn new(engine: ZvecGrep, root: PathBuf) -> Self {
        Self {
            runtime: tokio::runtime::Runtime::new()
                .expect("tokio runtime"),
            engine,
            root,
            symbols_supported: false,
            entity_ranges: HashMap::new(),
        }
    }

    fn context(&mut self, options: ContextOptions) -> Result<zg_engine::api::context::ContextResult, String> {
        self.runtime
            .block_on(self.engine.context(options))
            .map_err(|error| error.report().to_string())
    }

    fn units(
        &mut self,
        result: &zg_engine::api::context::ContextResult,
    ) -> Vec<SearchUnit> {
        result
            .items
            .iter()
            .filter_map(|item| {
                Some(SearchUnit {
                    path: item.relative_path.to_string_lossy().replace('\\', "/"),
                    start_line: item.range.start_line()?,
                    end_line: item.range.last_line()?,
                })
            })
            .collect()
    }
}

impl EvidenceSource for EngineSource {
    fn search(&mut self, pattern: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        let options = ContextOptions {
            rg: true,
            query: Some(pattern.to_owned()),
            root: Some(self.root.clone()),
            limit: Some(limit),
            auto_update: false,
            ..ContextOptions::default()
        };
        let result = self.context(options)?;
        Ok(self.units(&result))
    }

    fn search_filtered(
        &mut self,
        pattern: &str,
        limit: usize,
        scope: Option<&str>,
        extensions: &[String],
    ) -> Result<Vec<SearchUnit>, String> {
        let globs = extensions
            .iter()
            .map(|extension| format!("*.{extension}"))
            .collect::<Vec<_>>();
        let options = ContextOptions {
            rg: true,
            query: Some(pattern.to_owned()),
            root: Some(self.root.clone()),
            rg_paths: scope
                .map(|scope| vec![self.root.join(scope)])
                .unwrap_or_default(),
            globs,
            limit: Some(limit),
            auto_update: false,
            ..ContextOptions::default()
        };
        let result = self.context(options)?;
        Ok(self.units(&result))
    }

    fn symbol(&mut self, name: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        if !self.symbols_supported {
            return Ok(Vec::new());
        }
        let options = SymbolsOptions {
            root: Some(self.root.clone()),
            name: name.to_owned(),
            limit: Some(limit.min(50)),
            auto_update: false,
            ..SymbolsOptions::default()
        };
        let result = self
            .runtime
            .block_on(self.engine.symbols(options))
            .map_err(|error| error.report().to_string())?;
        Ok(result
            .items
            .iter()
            .filter_map(|item| {
                Some(SearchUnit {
                    path: item.relative_path.to_string_lossy().replace('\\', "/"),
                    start_line: item.range.start_line()?,
                    end_line: item.range.last_line()?,
                })
            })
            .collect())
    }

    fn hybrid(&mut self, question: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        let options = ContextOptions {
            root: Some(self.root.clone()),
            query: Some(question.to_owned()),
            limit: Some(limit),
            auto_update: false,
            refresh: Some(RefreshPolicy::Off),
            ..ContextOptions::default()
        };
        let result = self.context(options)?;
        for item in &result.items {
            if item.entity_id.is_none() {
                continue;
            }
            if let (Some(start_line), Some(end_line)) =
                (item.range.start_line(), item.range.last_line())
            {
                let path = item.relative_path.to_string_lossy().replace('\\', "/");
                let range = SearchUnit {
                    path: path.clone(),
                    start_line,
                    end_line,
                };
                let ranges = self.entity_ranges.entry(path).or_default();
                if !ranges.iter().any(|known| {
                    known.start_line == range.start_line && known.end_line == range.end_line
                }) {
                    ranges.push(range);
                }
            }
        }
        Ok(self.units(&result))
    }

    fn fts(&mut self, query: &str, limit: usize) -> Result<Vec<SearchUnit>, String> {
        let options = ContextOptions {
            root: Some(self.root.clone()),
            routes: vec![ContextRoute {
                mode: ContextRouteMode::Fts,
                query: query.to_owned(),
            }],
            limit: Some(limit),
            auto_update: false,
            refresh: Some(RefreshPolicy::Off),
            ..ContextOptions::default()
        };
        let result = self.context(options)?;
        Ok(self.units(&result))
    }

    fn read(&mut self, path: &str) -> Result<Option<String>, String> {
        let resolved = crate::resolve_under_root(&self.root, path)?;
        std::fs::read_to_string(self.root.join(resolved))
            .map(Some)
            .map_err(|error| error.to_string())
    }

    fn context_range(&self, unit: &SearchUnit) -> Option<SearchUnit> {
        self.entity_ranges
            .get(&unit.path)
            .and_then(|ranges| {
                ranges
                    .iter()
                    .find(|known| known.start_line <= unit.start_line && known.end_line >= unit.end_line)
            })
            .cloned()
    }
}

/// Enables the exact-symbol route once an index is present.
pub fn enable_symbols(source: &mut EngineSource) {
    source.symbols_supported = true;
}
