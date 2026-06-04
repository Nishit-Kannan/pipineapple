"""Smoke tests for the JobManager.

Uses shell builtins (`echo`, `sleep`) so it runs anywhere without
depending on lab tooling.
"""

from __future__ import annotations

import time

import pytest

from app.services.job_manager import JobManager, JobStatus


def _wait_for_status(job, expected, timeout=2.0):
    """Poll a job's status until it reaches ``expected`` or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if job.status == expected:
            return True
        time.sleep(0.02)
    return False


def test_completed_job_with_stdout():
    """echo exits 0 and its stdout reaches the buffer."""
    jm = JobManager()
    job = jm.start_job(["echo", "hello world"], name="test-echo")
    assert job.status == JobStatus.RUNNING
    assert job.pid is not None

    assert _wait_for_status(job, JobStatus.COMPLETED), (
        f"job did not complete in time, final status={job.status}"
    )
    assert job.exit_code == 0
    assert any("hello world" in line for line in job.stdout_lines())


def test_killed_job():
    """A long-running job can be stopped cleanly."""
    jm = JobManager()
    job = jm.start_job(["sleep", "30"], name="test-sleep")
    assert job.status == JobStatus.RUNNING

    # Let the process actually start
    time.sleep(0.05)
    stopped, reason = jm.stop_job(job.id, grace=1.0)
    assert stopped is True, f"stop failed: {reason}"

    assert _wait_for_status(job, JobStatus.KILLED, timeout=2.0), (
        f"job did not transition to KILLED, final status={job.status}"
    )


def test_nonexistent_binary_fails_gracefully():
    """A bad command marks the job FAILED instead of raising."""
    jm = JobManager()
    job = jm.start_job(
        ["this-binary-definitely-does-not-exist-xyz"],
        name="test-bad",
    )
    assert job.status == JobStatus.FAILED
    assert job.pid is None
    assert any("failed to start" in line for line in job.stdout_lines())


def test_list_and_get():
    """list_jobs() returns registered jobs; get_job() round-trips."""
    jm = JobManager()
    j1 = jm.start_job(["echo", "one"], name="a", tags=["test"])
    j2 = jm.start_job(["echo", "two"], name="b", tags=["test", "extra"])
    assert _wait_for_status(j1, JobStatus.COMPLETED)
    assert _wait_for_status(j2, JobStatus.COMPLETED)

    all_jobs = jm.list_jobs()
    assert len(all_jobs) == 2
    extras = jm.list_jobs(tag="extra")
    assert len(extras) == 1
    assert extras[0].id == j2.id

    assert jm.get_job(j1.id).id == j1.id
    assert jm.get_job("nonexistent-id") is None


def test_stop_all_does_not_throw_when_nothing_running():
    jm = JobManager()
    jm.stop_all()  # noop, should not raise
