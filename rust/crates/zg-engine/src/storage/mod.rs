//! Persistent index tables and their coordinated lifecycle.
mod directories;
mod entities;
mod files;
mod fragments;
pub(crate) mod manifest;
mod path;
pub(crate) mod read_session;
mod record;
pub(crate) mod staging;
mod store;
pub(crate) mod types;
mod zvec;

pub(crate) use store::IndexStore;
#[cfg(test)]
pub(crate) use store::{monotonic_time_ns_for_test, record_native_fts_span_for_test};
