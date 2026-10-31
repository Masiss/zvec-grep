//! Human and machine-readable rendering for resident daemon index jobs.

use std::io::{self, Write};

use zg_daemon_protocol::{JobView, JobViewPhase, JobViewProgress, JobViewReason, JobViewState};

use crate::{ColorMode, theme::StatusTheme};

/// Writes the daemon job list without terminal styling.
///
/// # Errors
/// Returns the underlying writer error.
pub fn write_jobs_result(
    writer: impl Write,
    jobs: &[JobView],
    include_finished: bool,
) -> io::Result<()> {
    write_jobs(writer, jobs, include_finished, ColorMode::Never, false)
}

/// Writes the daemon job list with optional terminal styling.
///
/// # Errors
/// Returns the underlying writer error.
pub fn write_jobs(
    mut writer: impl Write,
    jobs: &[JobView],
    include_finished: bool,
    color: ColorMode,
    terminal: bool,
) -> io::Result<()> {
    let theme = StatusTheme::new(color, terminal);
    let visible = visible_jobs(jobs, include_finished);
    let running = visible
        .iter()
        .filter(|job| job.state == JobViewState::Running)
        .count();
    let queued = visible
        .iter()
        .filter(|job| job.state == JobViewState::Queued)
        .count();
    let finished = visible.len().saturating_sub(running + queued);
    if visible.is_empty() {
        if include_finished {
            writeln!(writer, "No daemon index jobs.")?;
        } else {
            writeln!(writer, "No active daemon index jobs.")?;
        }
        return Ok(());
    }
    if finished > 0 {
        writeln!(
            writer,
            "{}: {} running, {} queued, {} recent",
            theme.accent("Daemon index jobs"),
            running,
            queued,
            finished
        )?;
    } else {
        writeln!(
            writer,
            "{}: {} running, {} queued",
            theme.accent("Daemon index jobs"),
            running,
            queued
        )?;
    }
    for job in visible {
        write_job(&mut writer, theme, job, true)?;
    }
    Ok(())
}

/// Writes the daemon job list as a JSON array.
///
/// # Errors
/// Returns the underlying writer error.
pub fn write_jobs_json(
    mut writer: impl Write,
    jobs: &[JobView],
    include_finished: bool,
) -> io::Result<()> {
    serde_json::to_writer_pretty(&mut writer, &visible_jobs(jobs, include_finished))?;
    writeln!(writer)
}

/// Describes a queued/running job transition for `zg --index` without claiming
/// a queue position or a phase the daemon did not report.
///
/// # Errors
/// Returns the underlying writer error.
pub fn write_job_transition(mut writer: impl Write, job: &JobView) -> io::Result<()> {
    match job.state {
        JobViewState::Queued => {
            write!(
                writer,
                "Daemon index job {} queued for {}",
                job.id,
                job.root.display()
            )?;
            if let Some(blocker) = job.blocking_job_id {
                write!(writer, " behind job {blocker}")?;
            }
            writeln!(writer)
        }
        JobViewState::Running => writeln!(
            writer,
            "Daemon index job {} running for {}",
            job.id,
            job.root.display()
        ),
        JobViewState::Succeeded | JobViewState::Failed | JobViewState::Cancelled => Ok(()),
    }
}

fn visible_jobs(jobs: &[JobView], include_finished: bool) -> Vec<&JobView> {
    jobs.iter()
        .filter(|job| {
            include_finished || matches!(job.state, JobViewState::Queued | JobViewState::Running)
        })
        .collect()
}

fn write_job(
    writer: &mut impl Write,
    theme: StatusTheme,
    job: &JobView,
    include_reason: bool,
) -> io::Result<()> {
    let state = match job.state {
        JobViewState::Running => theme.success("running"),
        JobViewState::Queued => theme.warning("queued"),
        JobViewState::Succeeded => theme.success("succeeded"),
        JobViewState::Failed => theme.danger("failed"),
        JobViewState::Cancelled => theme.muted("cancelled"),
    };
    writeln!(
        writer,
        "  {state:<10} {}  {}",
        job.id,
        theme.path(&job.root.display().to_string())
    )?;
    let mut details = Vec::new();
    if include_reason {
        details.push(format!("reason {}", reason(job.reason)));
    }
    if let Some(phase) = job.phase {
        details.push(format!("phase {}", phase_label(phase)));
    }
    if let Some(progress) = &job.progress
        && let Some(summary) = progress_label(progress)
    {
        details.push(summary);
    }
    details.push(format!("waited {}", format_waited(job.waited_millis)));
    if let Some(blocker) = job.blocking_job_id {
        details.push(format!("blocked by {blocker}"));
    }
    writeln!(writer, "             {}", theme.muted(&details.join(" · ")))
}

fn reason(reason: JobViewReason) -> &'static str {
    match reason {
        JobViewReason::Manual => "manual",
        JobViewReason::Watch => "watch",
    }
}

fn phase_label(phase: JobViewPhase) -> &'static str {
    match phase {
        JobViewPhase::Scanning => "scanning",
        JobViewPhase::Indexing => "indexing",
        JobViewPhase::Done => "done",
    }
}

fn progress_label(progress: &JobViewProgress) -> Option<String> {
    match (progress.files_indexed, progress.files_total) {
        (Some(indexed), Some(total)) => Some(format!("{indexed}/{total} files")),
        (Some(indexed), None) => Some(format!("{indexed} files")),
        (None, Some(total)) => Some(format!("{total} files total")),
        (None, None) => None,
    }
    .map(|files| match progress.files_failed {
        Some(failed) if failed > 0 => format!("{files}, {failed} failed"),
        _ => files,
    })
}

fn format_waited(millis: u64) -> String {
    if millis < 1_000 {
        return format!("{millis} ms");
    }
    let seconds = millis / 1_000;
    if seconds < 60 {
        let tenths = (millis % 1_000) / 100;
        return format!("{seconds}.{tenths} s");
    }
    let minutes = seconds / 60;
    if minutes < 60 {
        return format!("{minutes}m {}s", seconds % 60);
    }
    format!("{}h {}m", minutes / 60, minutes % 60)
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;

    use uuid::Uuid;
    use zg_daemon_protocol::{JobView, JobViewPhase, JobViewProgress, JobViewReason, JobViewState};

    use super::{format_waited, write_job_transition, write_jobs, write_jobs_json};

    fn job(state: JobViewState, id: u128) -> JobView {
        JobView {
            id: Uuid::from_u128(id),
            root: PathBuf::from("/workspace"),
            reason: JobViewReason::Manual,
            state,
            phase: None,
            progress: None,
            waited_millis: 1_250,
            blocking_job_id: None,
        }
    }

    fn render(jobs: &[JobView], include_finished: bool) -> String {
        let mut output = Vec::new();
        write_jobs(
            &mut output,
            jobs,
            include_finished,
            crate::ColorMode::Never,
            false,
        )
        .expect("render jobs");
        String::from_utf8(output).expect("utf-8")
    }

    #[test]
    fn queued_job_reports_reason_wait_and_blocker_without_queue_position() {
        let active = job(JobViewState::Running, 1);
        let mut queued = job(JobViewState::Queued, 2);
        queued.blocking_job_id = Some(active.id);
        let output = render(&[active.clone(), queued.clone()], false);
        assert!(output.contains("1 running, 1 queued"));
        assert!(output.contains(&queued.id.to_string()));
        assert!(output.contains("reason manual"));
        assert!(output.contains("waited 1.2 s"));
        assert!(output.contains(&format!("blocked by {}", active.id)));
        assert!(!output.contains("position"));
    }

    #[test]
    fn running_job_reports_only_observed_phase_and_progress() {
        let mut running = job(JobViewState::Running, 3);
        running.phase = Some(JobViewPhase::Indexing);
        running.progress = Some(JobViewProgress {
            files_indexed: Some(12),
            files_total: Some(40),
            files_failed: Some(1),
            detail: None,
        });
        let output = render(std::slice::from_ref(&running), false);
        assert!(output.contains("running"));
        assert!(output.contains("phase indexing"));
        assert!(output.contains("12/40 files, 1 failed"));
    }

    #[test]
    fn queued_job_without_progress_does_not_invent_a_phase() {
        let queued = job(JobViewState::Queued, 4);
        let output = render(&[queued], false);
        assert!(!output.contains("phase"));
        assert!(!output.contains("files"));
        assert!(output.contains("queued"));
    }

    #[test]
    fn finished_jobs_are_hidden_unless_requested() {
        let finished = job(JobViewState::Succeeded, 5);
        assert_eq!(
            render(std::slice::from_ref(&finished), false),
            "No active daemon index jobs.\n"
        );
        let output = render(&[finished], true);
        assert!(output.contains("succeeded"));
        assert!(output.contains("0 running, 0 queued, 1 recent"));
    }

    #[test]
    fn json_output_is_filtered_and_round_trips() {
        let running = job(JobViewState::Running, 6);
        let finished = job(JobViewState::Failed, 7);
        let mut output = Vec::new();
        write_jobs_json(&mut output, &[running.clone(), finished.clone()], false)
            .expect("json jobs");
        let decoded: Vec<JobView> = serde_json::from_slice(&output).expect("valid jobs json");
        assert_eq!(decoded, vec![running]);

        let mut output = Vec::new();
        write_jobs_json(&mut output, std::slice::from_ref(&finished), true).expect("json jobs");
        let decoded: Vec<JobView> = serde_json::from_slice(&output).expect("valid jobs json");
        assert_eq!(decoded, vec![finished]);
    }

    #[test]
    fn index_transition_distinguishes_queued_and_running() {
        let mut queued = job(JobViewState::Queued, 8);
        queued.blocking_job_id = Some(Uuid::from_u128(9));
        let mut output = Vec::new();
        write_job_transition(&mut output, &queued).expect("queued transition");
        let text = String::from_utf8(output).expect("utf-8");
        assert!(text.contains("queued"));
        assert!(text.contains("behind job"));

        let running = job(JobViewState::Running, 10);
        let mut output = Vec::new();
        write_job_transition(&mut output, &running).expect("running transition");
        let text = String::from_utf8(output).expect("utf-8");
        assert!(text.contains("running"));
    }

    #[test]
    fn waited_duration_is_human_readable() {
        assert_eq!(format_waited(250), "250 ms");
        assert_eq!(format_waited(1_250), "1.2 s");
        assert_eq!(format_waited(65_000), "1m 5s");
        assert_eq!(format_waited(3_661_000), "1h 1m");
    }
}
