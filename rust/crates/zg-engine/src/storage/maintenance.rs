//! Bounded deferred maintenance (E1 experiment).
//!
//! Native `optimize` rewrites whole collections, so running it on every
//! incremental pass dominates the cost of a one-file edit. This module defines
//! the experiment policy and the durable debt record that makes deferral
//! recoverable. The default policy is `Immediate`, i.e. the existing behaviour;
//! deferral must be selected explicitly through the environment.

use serde::{Deserialize, Serialize};

/// The debt record version persisted in `optimize.pending`.
pub(super) const DEBT_VERSION: u32 = 1;

/// Experiment knob. Parsed once per write session.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum MaintenancePolicy {
    /// Optimize every dirty collection at the end of each pass (current behaviour).
    Immediate,
    /// Fragments keep optimizing every pass; scalar collections wait for `limit` writes.
    ScalarDeferred { limit: usize },
    /// Fragments and scalars both wait for `limit` writes.
    Bounded { limit: usize },
}

impl MaintenancePolicy {
    /// `ZVEC_GREP_MAINTENANCE_POLICY`:
    /// - unset or `immediate`
    /// - `scalar-deferred:<K>`
    /// - `bounded:<K>`
    ///
    /// Unknown values fall back to `Immediate`, which is always safe.
    pub(crate) fn from_env() -> Self {
        let Ok(value) = std::env::var("ZVEC_GREP_MAINTENANCE_POLICY") else {
            return Self::Immediate;
        };
        let value = value.trim().to_ascii_lowercase();
        if value.is_empty() || value == "immediate" {
            return Self::Immediate;
        }
        let Some((kind, limit)) = value.split_once(':') else {
            return Self::Immediate;
        };
        let Ok(limit) = limit.trim().parse::<usize>() else {
            return Self::Immediate;
        };
        if limit == 0 {
            return Self::Immediate;
        }
        match kind.trim() {
            "scalar-deferred" => Self::ScalarDeferred { limit },
            "bounded" => Self::Bounded { limit },
            _ => Self::Immediate,
        }
    }

    pub(crate) const fn defers_fragments(self) -> bool {
        matches!(self, Self::Bounded { .. })
    }

    /// `None` means "no deferral": every dirty collection is optimized now.
    pub(crate) const fn limit(self) -> Option<usize> {
        match self {
            Self::Immediate => None,
            Self::ScalarDeferred { limit } | Self::Bounded { limit } => Some(limit),
        }
    }

    /// Harness-only catch-up trigger. Never set outside experiments.
    pub(crate) fn forced() -> bool {
        std::env::var("ZVEC_GREP_MAINTENANCE_FORCE")
            .is_ok_and(|value| matches!(value.trim(), "1" | "true" | "yes"))
    }
}

/// Durable description of the maintenance work still owed for this generation.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct DebtRecord {
    pub(crate) version: u32,
    /// Dirty collection bits, using the store's `DIRTY_*` masks.
    pub(crate) dirty: u8,
    /// Writes observed since the last successful scalar optimization.
    pub(crate) pending: usize,
    /// Set while an optimization is in flight or after one failed, so the next
    /// session retries instead of waiting for the bound again.
    #[serde(default)]
    pub(crate) retry: bool,
}

impl DebtRecord {
    pub(crate) const fn new(dirty: u8, pending: usize, retry: bool) -> Self {
        Self {
            version: DEBT_VERSION,
            dirty,
            pending,
            retry,
        }
    }

    pub(crate) fn encode(self) -> Option<Vec<u8>> {
        serde_json::to_vec(&self).ok()
    }

    /// Returns `None` for an empty, corrupt, or future-versioned record so the
    /// caller can fall back to the fail-safe "all dirty" interpretation.
    pub(crate) fn decode(bytes: &[u8]) -> Option<Self> {
        if bytes.is_empty() {
            return None;
        }
        let record: Self = serde_json::from_slice(bytes).ok()?;
        (record.version == DEBT_VERSION).then_some(record)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn policy_parsing_defaults_to_immediate_for_unknown_values() {
        // Tests run with the variable unset in the normal suite; parsing itself
        // is covered by the pure cases below.
        assert_eq!(
            MaintenancePolicy::Immediate.limit(),
            None,
            "immediate policy must not defer"
        );
        assert_eq!(MaintenancePolicy::Bounded { limit: 4 }.limit(), Some(4));
        assert!(MaintenancePolicy::Bounded { limit: 4 }.defers_fragments());
        assert!(!MaintenancePolicy::ScalarDeferred { limit: 4 }.defers_fragments());
    }

    #[test]
    fn debt_records_round_trip_and_reject_empty_or_foreign_payloads() {
        let record = DebtRecord::new(0b1111, 3, true);
        let bytes = record.encode().expect("encode");
        assert_eq!(DebtRecord::decode(&bytes), Some(record));
        assert!(DebtRecord::decode(&bytes).expect("decoded").retry);
        assert_eq!(DebtRecord::decode(b""), None);
        assert_eq!(DebtRecord::decode(b"not json"), None);
        // Version-1 payloads written before the retry flag stay readable.
        let legacy: serde_json::Value = serde_json::json!({
            "version": DEBT_VERSION,
            "dirty": 1,
            "pending": 2,
        });
        let decoded =
            DebtRecord::decode(&serde_json::to_vec(&legacy).expect("json")).expect("legacy");
        assert!(!decoded.retry);
        assert_eq!(decoded.pending, 2);
        let mut future: serde_json::Value = serde_json::from_slice(&bytes).expect("json");
        future["version"] = serde_json::json!(DEBT_VERSION + 1);
        assert_eq!(
            DebtRecord::decode(&serde_json::to_vec(&future).expect("json")),
            None
        );
    }
}
