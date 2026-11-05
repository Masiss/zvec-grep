use std::io::{self, Write};

use zg_daemon_protocol::{JobView, JobViewState};

pub fn write_jobs(
    mut writer: impl Write,
    jobs: &[JobView],
    all: bool,
    json: bool,
) -> io::Result<()> {
    let jobs = jobs
        .iter()
        .filter(|job| all || matches!(job.state, JobViewState::Queued | JobViewState::Running))
        .collect::<Vec<_>>();
    if json {
        serde_json::to_writer_pretty(&mut writer, &jobs)?;
        writeln!(writer)?;
        return Ok(());
    }
    if jobs.is_empty() {
        writeln!(
            writer,
            "{}",
            if all {
                "No daemon index jobs."
            } else {
                "No active daemon index jobs."
            }
        )?;
        return Ok(());
    }
    writeln!(writer, "Daemon index jobs:")?;
    for job in jobs {
        let state = match job.state {
            JobViewState::Queued => "queued",
            JobViewState::Running => "running",
            JobViewState::Succeeded => "succeeded",
            JobViewState::Failed => "failed",
            JobViewState::Cancelled => "cancelled",
        };
        let reason = match job.reason {
            zg_daemon_protocol::JobViewReason::Manual => "manual",
            zg_daemon_protocol::JobViewReason::Watch => "watch",
        };
        writeln!(
            writer,
            "  {state:<10} {}  {} ({reason})",
            job.id,
            job.root.display()
        )?;
        if let Some(phase) = job.phase {
            writeln!(
                writer,
                "             phase {}",
                match phase {
                    zg_daemon_protocol::JobViewPhase::Scanning => "scanning",
                    zg_daemon_protocol::JobViewPhase::Indexing => "indexing",
                    zg_daemon_protocol::JobViewPhase::Finalizing => "finalizing",
                    zg_daemon_protocol::JobViewPhase::Done => "done",
                }
            )?;
        }
        if let Some(progress) = &job.progress {
            let counts = match (progress.files_indexed, progress.files_total) {
                (Some(indexed), Some(total)) => format!("{indexed}/{total} files"),
                (Some(indexed), None) => format!("{indexed} files"),
                (None, Some(total)) => format!("{total} files total"),
                (None, None) => String::new(),
            };
            let failed = progress.files_failed.unwrap_or_default();
            if !counts.is_empty() || failed > 0 {
                writeln!(
                    writer,
                    "             {counts}{}",
                    if failed > 0 {
                        format!(", {failed} failed")
                    } else {
                        String::new()
                    }
                )?;
            }
            if let Some(detail) = &progress.detail {
                writeln!(writer, "             {detail}")?;
            }
        }
        if matches!(job.state, JobViewState::Queued | JobViewState::Running) {
            writeln!(writer, "             cancellable yes")?;
        }
        if let Some(blocker) = job.blocking_job_id {
            writeln!(writer, "             blocked by {blocker}")?;
        }
        writeln!(writer, "             waited {} ms", job.waited_millis)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::write_jobs;
    use std::path::PathBuf;
    use uuid::Uuid;
    use zg_daemon_protocol::{JobView, JobViewReason, JobViewState};

    fn job(state: JobViewState) -> JobView {
        JobView {
            id: Uuid::from_u128(5),
            root: PathBuf::from("/workspace"),
            reason: JobViewReason::Manual,
            state,
            phase: None,
            progress: None,
            waited_millis: 1250,
            blocking_job_id: None,
        }
    }

    #[test]
    fn queued_metadata_reports_phase_progress_blocker_and_cancellability() {
        let mut active = job(JobViewState::Running);
        active.phase = Some(zg_daemon_protocol::JobViewPhase::Indexing);
        active.progress = Some(zg_daemon_protocol::JobViewProgress {
            files_indexed: Some(2),
            files_total: Some(5),
            files_failed: Some(1),
            detail: Some("embedding".into()),
        });
        let mut queued = job(JobViewState::Queued);
        queued.id = Uuid::from_u128(6);
        queued.blocking_job_id = Some(active.id);
        let mut output = Vec::new();
        write_jobs(&mut output, &[active, queued.clone()], false, false).expect("render");
        let output = String::from_utf8(output).expect("utf8");
        assert!(output.contains("phase indexing"));
        assert!(output.contains("2/5 files, 1 failed"));
        assert!(output.contains("cancellable yes"));
        assert!(output.contains(&format!(
            "blocked by {}",
            queued.blocking_job_id.expect("blocker")
        )));
    }

    #[test]
    fn active_job_output_filters_retained_history_and_json_is_structured() {
        let jobs = [job(JobViewState::Running), job(JobViewState::Succeeded)];
        let mut output = Vec::new();
        write_jobs(&mut output, &jobs, false, false).expect("active output");
        let output = String::from_utf8(output).expect("utf8");
        assert!(output.contains("running"));
        assert!(!output.contains("succeeded"));
        let mut output = Vec::new();
        write_jobs(&mut output, &jobs, true, true).expect("json output");
        let parsed: serde_json::Value = serde_json::from_slice(&output).expect("json");
        assert_eq!(parsed.as_array().expect("array").len(), 2);
    }
}
