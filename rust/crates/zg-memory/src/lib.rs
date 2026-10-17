//! Small process-memory maintenance hooks used by the resident daemon.
//!
//! glibc keeps freed arenas mapped for reuse. That is normally a useful
//! optimization, but a long-lived indexing daemon can otherwise retain the
//! peak temporary index/build footprint indefinitely. The event hook below is
//! deliberately rate-limited and the idle hook is much less frequent; normal
//! queries never call `malloc_trim`.

use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

const TRIM_ENV: &str = "ZVEC_GREP_MALLOC_TRIM";
const EVENT_INTERVAL_ENV: &str = "ZVEC_GREP_MALLOC_TRIM_MIN_INTERVAL_MS";
const IDLE_INTERVAL_ENV: &str = "ZVEC_GREP_MALLOC_TRIM_IDLE_SECS";
const DEFAULT_EVENT_INTERVAL: Duration = Duration::from_secs(1);
const DEFAULT_IDLE_INTERVAL: Duration = Duration::from_mins(5);

#[derive(Clone, Copy, Debug)]
enum TrimKind {
    Event,
    Idle,
}

static LAST_EVENT_TRIM: OnceLock<Mutex<Option<Instant>>> = OnceLock::new();
static LAST_IDLE_TRIM: OnceLock<Mutex<Option<Instant>>> = OnceLock::new();

/// Reclaims free glibc heap pages after a significant maintenance event.
///
/// This is enabled by default only on Linux/glibc. Set
/// `ZVEC_GREP_MALLOC_TRIM=0` (also accepts `false` or `off`) to disable it.
/// Calls are rate-limited so an eviction loop cannot turn into a trim loop.
pub fn reclaim(reason: &'static str) {
    if !enabled() || !due(TrimKind::Event, event_interval()) {
        return;
    }
    trim(reason, TrimKind::Event);
}

/// Performs an infrequent idle trim when the daemon's maintenance loop is idle.
///
/// The caller may invoke this at its normal maintenance cadence; this function
/// enforces the five-minute default interval. Set
/// `ZVEC_GREP_MALLOC_TRIM_IDLE_SECS=0` to disable idle trims while retaining
/// event-triggered reclamation.
pub fn reclaim_idle(reason: &'static str) {
    let interval = idle_interval();
    if !enabled() || interval.is_zero() || !due(TrimKind::Idle, interval) {
        return;
    }
    trim(reason, TrimKind::Idle);
}

/// Whether this build has an active malloc-trim implementation and it has not
/// been disabled by the environment.
#[must_use]
pub fn enabled() -> bool {
    supported()
        && !matches!(
            std::env::var(TRIM_ENV).ok().as_deref(),
            Some("0" | "false" | "off")
        )
}

fn event_interval() -> Duration {
    duration_from_env(EVENT_INTERVAL_ENV, DEFAULT_EVENT_INTERVAL)
}

fn idle_interval() -> Duration {
    duration_from_env(IDLE_INTERVAL_ENV, DEFAULT_IDLE_INTERVAL)
}

fn duration_from_env(name: &str, default: Duration) -> Duration {
    duration_from_value(name, std::env::var(name).ok().as_deref(), default)
}

fn duration_from_value(name: &str, value: Option<&str>, default: Duration) -> Duration {
    let Some(value) = value.and_then(|value| value.parse::<u64>().ok()) else {
        return default;
    };
    Duration::from_millis(match name {
        IDLE_INTERVAL_ENV => value.saturating_mul(1_000),
        _ => value,
    })
}

fn due(kind: TrimKind, interval: Duration) -> bool {
    let slot = match kind {
        TrimKind::Event => &LAST_EVENT_TRIM,
        TrimKind::Idle => &LAST_IDLE_TRIM,
    };
    let mut last = slot
        .get_or_init(|| Mutex::new(None))
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let now = Instant::now();
    if last.is_some_and(|previous| now.saturating_duration_since(previous) < interval) {
        return false;
    }
    *last = Some(now);
    true
}

fn trim(reason: &'static str, kind: TrimKind) {
    #[cfg(all(target_os = "linux", target_env = "gnu"))]
    {
        let before = rss_bytes();
        // SAFETY: malloc_trim is a thread-safe glibc allocator operation and
        // accepts zero as the documented "release all possible free pages"
        // padding. It does not dereference a Rust pointer or borrow Rust data.
        let result = unsafe { libc::malloc_trim(0) };
        let after = rss_bytes();
        tracing::debug!(
            reason,
            kind = ?kind,
            result,
            rss_before_bytes = before,
            rss_after_bytes = after,
            rss_reclaimed_bytes = before.saturating_sub(after),
            "glibc malloc_trim completed"
        );
    }

    #[cfg(not(all(target_os = "linux", target_env = "gnu")))]
    let _ = (reason, kind);
}

#[cfg(all(target_os = "linux", target_env = "gnu"))]
fn supported() -> bool {
    true
}

#[cfg(not(all(target_os = "linux", target_env = "gnu")))]
fn supported() -> bool {
    false
}

#[cfg(all(target_os = "linux", target_env = "gnu"))]
fn rss_bytes() -> u64 {
    let Ok(status) = std::fs::read_to_string("/proc/self/status") else {
        return 0;
    };
    status
        .lines()
        .find_map(|line| line.strip_prefix("VmRSS:")?.split_whitespace().next())
        .and_then(|value| value.parse::<u64>().ok())
        .map_or(0, |kilobytes| kilobytes.saturating_mul(1024))
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::{
        DEFAULT_EVENT_INTERVAL, DEFAULT_IDLE_INTERVAL, EVENT_INTERVAL_ENV, IDLE_INTERVAL_ENV,
        duration_from_env, duration_from_value,
    };

    #[test]
    fn default_intervals_are_conservative() {
        assert_eq!(
            duration_from_env("missing", DEFAULT_EVENT_INTERVAL),
            DEFAULT_EVENT_INTERVAL
        );
        assert_eq!(
            duration_from_env("missing", DEFAULT_IDLE_INTERVAL),
            DEFAULT_IDLE_INTERVAL
        );
    }

    #[test]
    fn configured_intervals_use_their_documented_units() {
        assert_eq!(
            duration_from_value(EVENT_INTERVAL_ENV, Some("1250"), DEFAULT_EVENT_INTERVAL),
            Duration::from_millis(1250)
        );
        assert_eq!(
            duration_from_value(IDLE_INTERVAL_ENV, Some("7"), DEFAULT_IDLE_INTERVAL),
            Duration::from_secs(7)
        );
        assert_eq!(
            duration_from_value(IDLE_INTERVAL_ENV, Some("invalid"), DEFAULT_IDLE_INTERVAL),
            DEFAULT_IDLE_INTERVAL
        );
    }
}
