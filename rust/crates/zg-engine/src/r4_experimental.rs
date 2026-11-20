//! Experimental public re-exports of internal typed-fact parsers.
//!
//! Enabled only by the non-default `r4-experimental-facts` feature for the
//! private R4 external-agent adapter. This is not part of the stable engine
//! API; default builds expose nothing new.

pub use crate::extraction::csharp_facts::{ProjectIdentity, parse_file as parse_csharp_file};
pub use crate::extraction::sql_facts::parse_sql_file;
