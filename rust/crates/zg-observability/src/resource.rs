//! Low-overhead resource snapshots for runtime diagnosis.
//!
//! On Linux this reads `/proc` directly. Samples are taken only at phase
//! boundaries and on a bounded daemon interval; deltas are computed between
//! consecutive samples, so a fresh process reports zero CPU until the second
//! sample. Thread IDs are hashed; only the top eight CPU consumers are kept.

use std::{
    collections::BTreeMap,
    fs,
    sync::Mutex,
    time::{Duration, Instant},
};

use serde_json::{Value, json};

const MAX_THREADS: usize = 8;
const MIN_INTERVAL: Duration = Duration::from_millis(200);

#[derive(Clone, Debug, Default)]
struct CpuSnapshot {
    at: Option<Instant>,
    process_ticks: u64,
    host_total: u64,
    host_idle: u64,
    threads: BTreeMap<String, (u64, String, String)>,
}

static LAST: Mutex<CpuSnapshot> = Mutex::new(CpuSnapshot::new_const());

impl CpuSnapshot {
    const fn new_const() -> Self {
        Self {
            at: None,
            process_ticks: 0,
            host_total: 0,
            host_idle: 0,
            threads: BTreeMap::new(),
        }
    }
}

fn parse_proc_stat_line(line: &str) -> Option<(String, Vec<u64>)> {
    let mut fields = line.split_whitespace();
    let name = fields.next()?.to_owned();
    let values = fields
        .map(|value| value.parse::<u64>().unwrap_or(0))
        .collect();
    Some((name, values))
}

fn parse_process_stat(content: &str) -> Option<(u64, String)> {
    let close = content.rfind(')')?;
    let mut fields = content[close + 1..].split_whitespace();
    let state = fields.next()?.to_owned();
    // Field 14 (utime) is token index 11 after field 3 (state).
    let utime = fields.nth(10)?.parse::<u64>().ok()?;
    let stime = fields.next()?.parse::<u64>().ok()?;
    Some((utime.saturating_add(stime), state))
}

fn parse_status(content: &str) -> (Option<u64>, Option<u64>) {
    let mut rss = None;
    let mut threads = None;
    for line in content.lines() {
        if let Some(value) = line.strip_prefix("VmRSS:") {
            rss = value
                .split_whitespace()
                .next()
                .and_then(|v| v.parse::<u64>().ok());
        } else if let Some(value) = line.strip_prefix("Threads:") {
            threads = value
                .split_whitespace()
                .next()
                .and_then(|v| v.parse::<u64>().ok());
        }
    }
    (rss, threads)
}

fn parse_mem_available(content: &str) -> Option<u64> {
    content.lines().find_map(|line| {
        line.strip_prefix("MemAvailable:")?
            .split_whitespace()
            .next()?
            .parse::<u64>()
            .ok()
            .map(|kilobytes| kilobytes.saturating_mul(1024))
    })
}

#[allow(
    clippy::too_many_lines,
    clippy::cast_precision_loss,
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss
)]
fn linux_sample() -> Option<Value> {
    let host = fs::read_to_string("/proc/stat").ok()?;
    let host_cpu = parse_proc_stat_line(host.lines().next()?)?;
    let host_total = host_cpu.1.iter().copied().sum::<u64>();
    let host_idle =
        host_cpu.1.get(3).copied().unwrap_or(0) + host_cpu.1.get(4).copied().unwrap_or(0);
    let self_stat = fs::read_to_string("/proc/self/stat").ok()?;
    let (process_ticks, _) = parse_process_stat(&self_stat)?;
    let status = fs::read_to_string("/proc/self/status").ok()?;
    let (rss_kb, thread_count) = parse_status(&status);
    let available = fs::read_to_string("/proc/meminfo")
        .ok()
        .and_then(|content| parse_mem_available(&content));

    let mut threads = BTreeMap::new();
    if let Ok(entries) = fs::read_dir("/proc/self/task") {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.parse::<u64>().is_err() {
                continue;
            }
            let Ok(content) = fs::read_to_string(entry.path().join("stat")) else {
                continue;
            };
            let Some((cpu, state)) = parse_process_stat(&content) else {
                continue;
            };
            let comm = content
                .split_once('(')
                .and_then(|(_, rest)| rest.rsplit_once(')'))
                .map(|(comm, _)| comm.to_owned())
                .unwrap_or_default();
            threads.insert(name, (cpu, state, comm));
        }
    }

    let now = Instant::now();
    let mut last = LAST.lock().ok()?;
    let (elapsed_secs, process_delta, host_delta, idle_delta) = match last.at {
        Some(at) if now.duration_since(at) >= MIN_INTERVAL => {
            let elapsed = now.duration_since(at).as_secs_f64();
            (
                elapsed,
                process_ticks.saturating_sub(last.process_ticks),
                host_total.saturating_sub(last.host_total),
                host_idle.saturating_sub(last.host_idle),
            )
        }
        _ => (0.0, 0, 0, 0),
    };
    let clock = 100.0;
    let cpu_count = std::thread::available_parallelism().map_or(1.0, |value| value.get() as f64);
    let process_cpu = if elapsed_secs > 0.0 {
        (process_delta as f64 / clock / elapsed_secs / cpu_count) * 100.0
    } else {
        0.0
    };
    let host_cpu = if host_delta > 0 {
        ((host_delta.saturating_sub(idle_delta)) as f64 / host_delta as f64) * 100.0
    } else {
        0.0
    };

    let mut ranked = threads
        .iter()
        .map(|(tid, (ticks, state, comm))| {
            let before = last.threads.get(tid).map_or(*ticks, |(ticks, _, _)| *ticks);
            (ticks.saturating_sub(before), tid, state, comm)
        })
        .collect::<Vec<_>>();
    ranked.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.cmp(b.1)));
    let top_threads = ranked
        .into_iter()
        .take(MAX_THREADS)
        .map(|(delta, tid, state, comm)| {
            json!({
                "tid_sha256": hash_tid(tid),
                "cpu_ticks_delta": delta,
                "state": state,
                "comm": comm,
            })
        })
        .collect::<Vec<_>>();

    *last = CpuSnapshot {
        at: Some(now),
        process_ticks,
        host_total,
        host_idle,
        threads,
    };
    Some(json!({
        "source": "proc",
        "rss_bytes": rss_kb.map(|kb| kb.saturating_mul(1024)),
        "available_memory_bytes": available,
        "threads": thread_count,
        "process_cpu_percent": round(process_cpu),
        "host_cpu_percent": round(host_cpu),
        "top_threads": top_threads,
        "sample_interval_ms": (elapsed_secs * 1000.0) as u64,
    }))
}

fn hash_tid(tid: &str) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(tid.as_bytes());
    let hex = b"0123456789abcdef";
    let mut out = String::with_capacity(16);
    for byte in digest.iter().take(8) {
        out.push(hex[(byte >> 4) as usize] as char);
        out.push(hex[(byte & 0x0f) as usize] as char);
    }
    out
}

fn round(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

/// Process/thread/host snapshot. Returns `null` on unsupported platforms.
pub fn sample() -> Option<Value> {
    #[cfg(target_os = "linux")]
    {
        linux_sample()
    }
    #[cfg(not(target_os = "linux"))]
    {
        let pid = sysinfo::get_current_pid().ok()?;
        let mut system = sysinfo::System::new();
        system.refresh_all();
        let process = system.process(pid)?;
        Some(json!({
            "source": "sysinfo",
            "rss_bytes": process.memory(),
            "available_memory_bytes": system.available_memory(),
            "process_cpu_percent": round(process.cpu_usage() as f64),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_linux_process_stat_with_comm_spaces() {
        let line = "123 (zg worker one) S 1 2 3 4 5 0 0 0 0 0 42 7 0 0";
        assert_eq!(parse_process_stat(line), Some((49, "S".to_owned())));
    }

    #[test]
    fn parses_memory_and_thread_status() {
        let status = "Name:\tzg\nThreads:\t17\nVmRSS:\t2048 kB\n";
        assert_eq!(parse_status(status), (Some(2048), Some(17)));
        let meminfo = "MemTotal: 100 kB\nMemAvailable: 40 kB\n";
        assert_eq!(parse_mem_available(meminfo), Some(40 * 1024));
    }
}
