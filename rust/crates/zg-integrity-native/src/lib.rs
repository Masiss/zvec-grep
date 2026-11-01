//! Narrow, opt-in Rust wrapper for the native read-only integrity verifier.
//!
//! The verifier symbols are optional: an installed native library that predates
//! the verifier simply reports [`IntegrityError::Unavailable`]. Resolution is
//! dynamic, so normal open/search/index paths are unaffected and no link-time
//! dependency on the W3 candidate is introduced.
//!
//! This crate is not wired into `zg-engine` yet; W4 decides how the operation
//! is exposed.

use std::path::Path;

use serde::Deserialize;

/// Selects which artifact categories the native verifier checks.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct VerifyFlags(u32);

impl VerifyFlags {
    /// `RocksDB` SST, MANIFEST and column-family metadata.
    pub const ROCKSDB: Self = Self(0x1);
    /// Proxima header/footer/segment-table structure.
    pub const PROXIMA_STRUCTURE: Self = Self(0x2);
    /// Proxima content and per-segment CRCs.
    pub const PROXIMA_CONTENT: Self = Self(0x4);
    /// Return `INCOMPLETE` when requested coverage is not complete.
    pub const REQUIRE_COMPLETE: Self = Self(0x8);

    /// Default: `RocksDB` plus `Proxima` structure and content.
    #[must_use]
    pub const fn all() -> Self {
        Self(0x1 | 0x2 | 0x4)
    }

    /// Raw bit mask.
    #[must_use]
    pub const fn bits(self) -> u32 {
        self.0
    }

    /// Combine two flag sets.
    #[must_use]
    pub const fn union(self, other: Self) -> Self {
        Self(self.0 | other.0)
    }

    /// Whether every flag in `other` is present.
    #[must_use]
    pub const fn contains(self, other: Self) -> bool {
        self.0 & other.0 == other.0
    }
}

/// Native return code.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum IntegrityCode {
    /// No corruption and complete coverage.
    PassComplete,
    /// Corruption was detected.
    Corrupt,
    /// Verification completed without proving complete coverage.
    Incomplete,
    /// The supplied path or argument was rejected.
    InvalidArg,
}

impl IntegrityCode {
    const fn from_status(status: IntegrityStatus) -> Self {
        match status {
            IntegrityStatus::PassComplete => Self::PassComplete,
            IntegrityStatus::IncompleteLegacy | IntegrityStatus::Incomplete => Self::Incomplete,
            IntegrityStatus::Corrupt => Self::Corrupt,
        }
    }
}

/// Detailed verification status reported by the native verifier.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum IntegrityStatus {
    /// Every present artifact category was verified and complete.
    PassComplete,
    /// Readable legacy artifacts without checksum coverage.
    IncompleteLegacy,
    /// Incomplete coverage or an unstable/actively written generation.
    Incomplete,
    /// At least one artifact failed verification.
    Corrupt,
}

/// Coverage of one artifact category.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct IntegrityCoverage {
    /// Category name (`proxima`, `rocksdb`, `ipc`, `sidecars`, `wal`).
    pub name: String,
    /// `covered`, `partial`, `unsupported`, `skipped` or `not_present`.
    pub status: String,
    /// Number of components checked.
    pub files: u64,
    /// Total bytes covered.
    pub bytes: u64,
}

/// One verified file or `RocksDB` directory.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct IntegrityComponent {
    /// Artifact kind.
    pub kind: String,
    /// Absolute path as reported by the verifier.
    pub path: String,
    /// `ok`, `corrupt`, `legacy`, `partial` or `unsupported`.
    pub status: String,
    /// Whether this component contributed complete coverage.
    pub complete: bool,
    /// Component size in bytes.
    pub bytes: u64,
    /// Component-level error, empty on success.
    #[serde(default)]
    pub error: String,
    /// Proxima segment count.
    #[serde(default)]
    pub segments: Option<u64>,
    /// Proxima segments carrying a CRC.
    #[serde(default)]
    pub segments_with_crc: Option<u64>,
    /// Proxima segments without a CRC.
    #[serde(default)]
    pub segments_without_crc: Option<u64>,
    /// Bytes in segments without a CRC.
    #[serde(default)]
    pub unprotected_bytes: Option<u64>,
    /// Proxima per-segment CRC mismatches.
    #[serde(default)]
    pub segment_crc_mismatches: Option<u64>,
    /// Proxima content size.
    #[serde(default)]
    pub content_size: Option<u64>,
    /// Proxima content CRC.
    #[serde(default)]
    pub content_crc: Option<u32>,
    /// Proxima checksum capability marker.
    #[serde(default)]
    pub capability: Option<u64>,
    /// Whether the checksum capability marker is the W2b/W3 value.
    #[serde(default)]
    pub capability_ok: Option<bool>,
    /// Whether content CRC verification was requested.
    #[serde(default)]
    pub content_crc_checked: Option<bool>,
    /// `RocksDB` log files observed in this column-family directory.
    #[serde(default)]
    pub wal_files: Option<u64>,
}

/// Typed native integrity report.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
#[allow(clippy::struct_excessive_bools)]
pub struct IntegrityReport {
    /// Overall status.
    pub status: IntegrityStatus,
    /// No corruption detected.
    pub pass: bool,
    /// Every present artifact category was verified.
    pub complete: bool,
    /// Legacy artifacts without checksum coverage were found.
    pub legacy: bool,
    /// The target was a stable generation.
    pub stable: bool,
    /// `stable` or `active_writer`.
    pub stability: String,
    /// Human-readable stability detail.
    pub stability_detail: String,
    /// Flags used by the native verifier.
    pub flags: u32,
    /// Collection path as reported by the native verifier.
    pub collection_path: String,
    /// Generation UUID derived from the storage path, when present.
    pub generation: Option<String>,
    /// Per-category coverage.
    pub coverage: Vec<IntegrityCoverage>,
    /// Present coverage that was not verified.
    pub missing_coverage: Vec<String>,
    /// Known format limitations that structural checks cannot close.
    #[serde(default)]
    pub limitations: Vec<String>,
    /// Aggregated errors.
    pub errors: Vec<String>,
    /// Per-component details.
    pub components: Vec<IntegrityComponent>,
}

/// Successful native verification result.
#[derive(Clone, Debug)]
pub struct Verification {
    /// Native return code.
    pub code: IntegrityCode,
    /// Parsed report.
    pub report: IntegrityReport,
    /// Raw JSON exactly as returned by the native verifier.
    pub raw_json: String,
}

impl Verification {
    /// Whether the native verifier detected corruption.
    #[must_use]
    pub fn is_corrupt(&self) -> bool {
        self.code == IntegrityCode::Corrupt || !self.report.pass
    }

    /// Whether every present artifact category was verified.
    #[must_use]
    pub fn is_complete(&self) -> bool {
        self.report.complete && self.report.stable
    }
}

/// Failure to run the native verifier.
#[derive(Debug, thiserror::Error)]
pub enum IntegrityError {
    /// The loaded native library does not expose the verifier symbols.
    #[error("native integrity verifier is unavailable in the loaded library")]
    Unavailable,
    /// The verifier rejected the argument.
    #[error("native integrity verifier rejected the collection path")]
    InvalidArgument,
    /// The verifier returned an unexpected code.
    #[error("native integrity verifier returned unexpected code {0}")]
    UnexpectedCode(i32),
    /// The returned report was not valid JSON.
    #[error("native integrity report is invalid: {0}")]
    InvalidReport(String),
    /// The path cannot be represented as a C string.
    #[error("collection path cannot be represented as a C string")]
    InvalidPath,
    /// The platform has no native verifier wrapper.
    #[error("native integrity verification is not supported on this platform")]
    Unsupported,
}

/// Verify a sealed collection or generation directory read-only.
///
/// `flags` of zero is treated by the native verifier as the full default set.
///
/// # Errors
/// Returns [`IntegrityError::Unavailable`] when the loaded native library lacks
/// the verifier, and [`IntegrityError::InvalidArgument`] for a rejected path.
pub fn verify_collection(path: &Path, flags: VerifyFlags) -> Result<Verification, IntegrityError> {
    #[cfg(target_os = "linux")]
    {
        linux::verify(path, flags)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (path, flags);
        Err(IntegrityError::Unsupported)
    }
}

#[cfg(target_os = "linux")]
mod linux {
    use std::{
        ffi::{CStr, CString, c_char, c_int, c_uint},
        os::unix::ffi::OsStrExt,
        path::Path,
    };

    use super::{IntegrityCode, IntegrityError, Verification, VerifyFlags, deserialize};

    /// Opaque `zvec_integrity_report`.
    #[repr(C)]
    struct NativeReport {
        _private: [u8; 0],
    }

    type VerifyFn = unsafe extern "C" fn(*const c_char, c_uint, *mut *mut NativeReport) -> c_int;
    type ReportOkFn = unsafe extern "C" fn(*const NativeReport) -> c_int;
    type ReportCompleteFn = unsafe extern "C" fn(*const NativeReport) -> c_int;
    type ReportJsonFn = unsafe extern "C" fn(*const NativeReport) -> *const c_char;
    type ReportFreeFn = unsafe extern "C" fn(*mut NativeReport);

    struct Symbols {
        verify: VerifyFn,
        complete: ReportCompleteFn,
        json: ReportJsonFn,
        free: ReportFreeFn,
        _ok: ReportOkFn,
    }

    /// Symbols live in the already-loaded `libzvec_c_api.so`; `this()` only
    /// exposes the current process' global symbol scope.
    fn symbols() -> Result<Symbols, IntegrityError> {
        // SAFETY: `this()` does not load a new image and the returned handle
        // outlives every symbol used below.
        let library = libloading::os::unix::Library::this();
        // SAFETY: each symbol is resolved by its C name and transmuted to the
        // declared signature. A symbol with the wrong ABI would be an upstream
        // contract violation; the wrapper only calls symbols exported by W3.
        unsafe {
            let verify = library
                .get::<VerifyFn>(b"zvec_collection_verify_integrity\0")
                .map_err(|_| IntegrityError::Unavailable)?;
            let ok = library
                .get::<ReportOkFn>(b"zvec_integrity_report_ok\0")
                .map_err(|_| IntegrityError::Unavailable)?;
            let complete = library
                .get::<ReportCompleteFn>(b"zvec_integrity_report_complete\0")
                .map_err(|_| IntegrityError::Unavailable)?;
            let json = library
                .get::<ReportJsonFn>(b"zvec_integrity_report_json\0")
                .map_err(|_| IntegrityError::Unavailable)?;
            let free = library
                .get::<ReportFreeFn>(b"zvec_integrity_report_free\0")
                .map_err(|_| IntegrityError::Unavailable)?;
            Ok(Symbols {
                verify: *verify,
                complete: *complete,
                json: *json,
                free: *free,
                _ok: *ok,
            })
        }
    }

    struct Guard {
        report: *mut NativeReport,
        free: ReportFreeFn,
    }

    impl Drop for Guard {
        fn drop(&mut self) {
            // SAFETY: the report pointer came from the native verifier and is
            // freed exactly once by this guard.
            unsafe { (self.free)(self.report) };
        }
    }

    pub(super) fn verify(path: &Path, flags: VerifyFlags) -> Result<Verification, IntegrityError> {
        let path =
            CString::new(path.as_os_str().as_bytes()).map_err(|_| IntegrityError::InvalidPath)?;
        let symbols = symbols()?;
        let mut report: *mut NativeReport = std::ptr::null_mut();
        // SAFETY: `path` is NUL-terminated and outlives the call. `report` is
        // initialized and only freed by the matching native destructor below.
        let code = unsafe {
            (symbols.verify)(path.as_ptr(), flags.bits(), std::ptr::addr_of_mut!(report))
        };
        if report.is_null() {
            return match code {
                3 => Err(IntegrityError::InvalidArgument),
                other => Err(IntegrityError::UnexpectedCode(other)),
            };
        }
        let guard = Guard {
            report,
            free: symbols.free,
        };
        // SAFETY: the report is valid until the guard drops.
        let complete = unsafe { (symbols.complete)(guard.report) } != 0;
        // SAFETY: the JSON pointer is owned by the report and NUL-terminated.
        let json_pointer = unsafe { (symbols.json)(guard.report) };
        if json_pointer.is_null() {
            return Err(IntegrityError::InvalidReport(
                "native verifier returned no JSON".to_owned(),
            ));
        }
        // SAFETY: validated non-null and terminated by the native allocator.
        let raw_json = unsafe { CStr::from_ptr(json_pointer) }
            .to_string_lossy()
            .into_owned();
        let mut parsed = deserialize(&raw_json)?;
        parsed.complete = complete;
        let code = IntegrityCode::from_status(parsed.status);
        // `guard` releases the native report here, after the JSON was copied.
        drop(guard);
        Ok(Verification {
            code,
            report: parsed,
            raw_json,
        })
    }
}

fn deserialize(json: &str) -> Result<IntegrityReport, IntegrityError> {
    serde_json::from_str(json).map_err(|error| IntegrityError::InvalidReport(error.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"{
        "status": "PASS_COMPLETE",
        "pass": true,
        "complete": true,
        "legacy": false,
        "stable": true,
        "stability": "stable",
        "stability_detail": "shared lock acquired; no active writer",
        "flags": 7,
        "collection_path": "/tmp/gen/storage",
        "generation": "00000000-0000-4000-8000-000000000000",
        "coverage": [
            {"name": "proxima", "status": "covered", "files": 1, "bytes": 4096},
            {"name": "rocksdb", "status": "covered", "files": 2, "bytes": 8192},
            {"name": "wal", "status": "not_present", "files": 0, "bytes": 0}
        ],
        "missing_coverage": [],
        "limitations": ["ipc.content_checksum", "sidecars.content_checksum"],
        "errors": [],
        "components": [
            {"kind": "proxima", "path": "/tmp/gen/storage/a.proxima", "status": "ok",
             "complete": true, "bytes": 4096, "error": "", "segments": 3,
             "segments_with_crc": 3, "segments_without_crc": 0,
             "unprotected_bytes": 0, "segment_crc_mismatches": 0,
             "content_size": 2048, "content_crc": 123,
             "capability": 6510969491857068337, "capability_ok": true,
             "content_crc_checked": true},
            {"kind": "rocksdb", "path": "/tmp/gen/storage/files", "status": "ok",
             "complete": true, "bytes": 8192, "error": "", "wal_files": 0}
        ]
    }"#;

    #[test]
    fn parses_complete_report() {
        let report = deserialize(SAMPLE).expect("report");
        assert_eq!(report.status, IntegrityStatus::PassComplete);
        assert!(report.complete);
        assert_eq!(report.coverage.len(), 3);
        assert_eq!(report.limitations.len(), 2);
        assert_eq!(report.components[0].segments, Some(3));
        assert_eq!(report.components[1].wal_files, Some(0));
        assert_eq!(
            report.generation.as_deref(),
            Some("00000000-0000-4000-8000-000000000000")
        );
    }

    #[test]
    fn rejects_unknown_status() {
        let json = SAMPLE.replace("PASS_COMPLETE", "PASS");
        assert!(deserialize(&json).is_err());
    }

    #[test]
    fn maps_status_to_code() {
        assert_eq!(
            IntegrityCode::from_status(IntegrityStatus::PassComplete),
            IntegrityCode::PassComplete
        );
        assert_eq!(
            IntegrityCode::from_status(IntegrityStatus::IncompleteLegacy),
            IntegrityCode::Incomplete
        );
        assert_eq!(
            IntegrityCode::from_status(IntegrityStatus::Corrupt),
            IntegrityCode::Corrupt
        );
    }

    #[test]
    fn flags_union_and_contains() {
        let flags = VerifyFlags::ROCKSDB.union(VerifyFlags::PROXIMA_CONTENT);
        assert!(flags.contains(VerifyFlags::ROCKSDB));
        assert!(!flags.contains(VerifyFlags::PROXIMA_STRUCTURE));
        assert_eq!(VerifyFlags::all().bits(), 7);
    }
}
