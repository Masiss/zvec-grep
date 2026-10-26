//! Search over an existing workspace index using FTS and vectors.

pub(crate) mod context;
mod format_filter;
mod path_filter;
mod pipeline;
mod ranking;
pub(crate) mod service;
pub(crate) mod storage;

pub(crate) mod writer;

#[cfg(test)]
mod m4_integration_tests;
