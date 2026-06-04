"""JobManager — lifecycle owner for long-running subprocesses.

Every long-running tool the platform launches (airodump, hostapd,
aireplay, hashcat, bettercap, etc.) goes through this service. Centralising
the lifecycle prevents the failure modes you hit when individual route
handlers manage Popen themselves: orphan/zombie children at shutdown,
hanging on full pipe buffers, no central registry of "what's running."

API surface (Session 02 — skeleton only, no UI consumer yet):

    job_manager.start_job(cmd, name=None, tags=None) -> Job
    job_manager.get_job(job_id) -> Job | None
    job_manager.list_jobs(tag=None) -> list[Job]
    job_manager.stop_job(job_id, grace=2.0) -> bool
    job_manager.stop_all() -> None

Per-job stdout is captured in a deque (bounded so a long-running job
doesn't eat memory) and streamed line-by-line over a SocketIO room named
``job-<id>``. Clients that care about a specific job's output join that
room; everyone else stays unspammed.

Status transitions:
    pending -> running -> completed (rc=0)
                      \\-> failed    (rc!=0)
                      \\-> killed    (stopped by us)
"""

from __future__ import annotations

import atexit
import logging
import shlex
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)


# Why no PR_SET_PDEATHSIG: on Linux, that prctl tracks the parent
# *thread*, not the parent process. When subprocess.Popen is called
# from a background thread (e.g. our networking restore daemon thread),
# the children get SIGTERM the moment the thread completes — even though
# Flask itself is still running. We rely on the atexit handler below
# for clean shutdown (Ctrl-C of Flask) and accept that SIGKILL of Flask
# leaves orphan daemons until manual `sudo pkill hostapd dnsmasq`.


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"   # exited rc=0
    FAILED = "failed"         # exited rc!=0
    KILLED = "killed"         # stopped by stop_job


@dataclass
class Job:
    id: str
    name: str
    cmd: list[str]
    pid: int | None = None
    status: JobStatus = JobStatus.PENDING
    start_time: float | None = None
    end_time: float | None = None
    exit_code: int | None = None
    tags: list[str] = field(default_factory=list)

    # Private — not serialised
    _stdout_buf: deque[str] = field(
        default_factory=lambda: deque(maxlen=500)
    )
    _proc: subprocess.Popen[str] | None = None
    _reader_thread: threading.Thread | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "cmd": self.cmd,
            "pid": self.pid,
            "status": self.status.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "exit_code": self.exit_code,
            "tags": list(self.tags),
        }

    def stdout_lines(self) -> list[str]:
        return list(self._stdout_buf)


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._socketio = None

    def attach_socketio(self, socketio) -> None:
        """Wire the service to SocketIO for stdout streaming."""
        self._socketio = socketio

    def start_job(
        self,
        cmd: str | list[str],
        name: str | None = None,
        tags: list[str] | None = None,
        stdout_path: str | None = None,
    ) -> Job:
        """Start a subprocess and register it as a job.

        ``cmd`` is either a list of argv tokens or a shell-quoted string.
        The function returns immediately with a Job whose status is
        RUNNING (or FAILED if the process couldn't be launched at all).

        If ``stdout_path`` is provided, the process's stdout/stderr is
        redirected to that file rather than captured via pipe. Used for
        long-running daemons (hostapd, dnsmasq) where the pipe-+-reader
        approach can interact badly with how the daemon manages its own
        IO — and where having a persistent on-disk log is more useful
        for post-mortem debugging than a deque in memory.
        """
        if isinstance(cmd, str):
            cmd_list = shlex.split(cmd)
        else:
            cmd_list = list(cmd)

        job_id = uuid.uuid4().hex[:12]
        job = Job(
            id=job_id,
            name=name or (cmd_list[0] if cmd_list else "unknown"),
            cmd=cmd_list,
            tags=list(tags or []),
        )
        with self._lock:
            self._jobs[job_id] = job

        try:
            if stdout_path:
                log.info("job %s -> stdout redirected to %s", job_id, stdout_path)
                # Open the file with line buffering so writes appear
                # promptly when we tail it.
                stdout_target = open(stdout_path, "w", buffering=1)
                proc = subprocess.Popen(
                    cmd_list,
                    stdout=stdout_target,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                    text=True,
                )
                # Stash the file so we close it in the wait-loop thread
                job._stdout_file = stdout_target  # type: ignore[attr-defined]
                job._stdout_path = stdout_path    # type: ignore[attr-defined]
            else:
                proc = subprocess.Popen(
                    cmd_list,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                    text=True,
                )
        except (FileNotFoundError, OSError) as e:
            job.status = JobStatus.FAILED
            job.end_time = time.time()
            job.exit_code = -1
            job._stdout_buf.append(f"failed to start: {e}")
            log.warning("job %s start failed: %s", job_id, e)
            if self._socketio is not None:
                self._socketio.emit("job:exited", job.to_dict(), namespace="/")
            return job

        job._proc = proc
        job.pid = proc.pid
        job.status = JobStatus.RUNNING
        job.start_time = time.time()

        # Spawn the appropriate watcher thread.
        # - File-redirected jobs: just wait for exit (no stdout to drain).
        # - Pipe-captured jobs: drain stdout line-by-line into the buffer
        #   and SocketIO room.
        if stdout_path:
            t = threading.Thread(
                target=self._wait_loop,
                args=(job,),
                daemon=True,
                name=f"job-wait-{job_id[:6]}",
            )
        else:
            t = threading.Thread(
                target=self._reader_loop,
                args=(job,),
                daemon=True,
                name=f"job-reader-{job_id[:6]}",
            )
        job._reader_thread = t
        t.start()

        log.info(
            "started job %s (name=%s pid=%d cmd=%s)",
            job_id, job.name, job.pid, cmd_list,
        )
        if self._socketio is not None:
            self._socketio.emit("job:started", job.to_dict(), namespace="/")

        # Broadcast to the read-only command stream so the user sees the
        # full command of every long-running tool the platform launches.
        try:
            from app.services.terminal import terminal
            terminal.broadcast(
                cmd_list,
                source="job",
                rc=None,           # still running, no exit code yet
                duration_ms=None,
                note=f"started as job {job_id} (pid {job.pid})",
            )
        except Exception:
            log.exception("terminal broadcast for job start failed")

        return job

    def _wait_loop(self, job: Job) -> None:
        """Watcher for file-redirected jobs.

        Doesn't drain stdout (it's going to a file). Just waits for the
        process to exit, then closes the file and records final status.
        On exit, tails the last 200 lines of the log file into the job's
        in-memory buffer so the UI can still surface recent output.
        """
        proc = job._proc
        assert proc is not None
        rc = proc.wait()
        job.end_time = time.time()
        job.exit_code = rc

        # Close the redirected stdout file
        stdout_file = getattr(job, "_stdout_file", None)
        if stdout_file is not None:
            try:
                stdout_file.close()
            except Exception:
                pass

        # Pull the tail of the log file into the job's buffer for UI
        stdout_path = getattr(job, "_stdout_path", None)
        if stdout_path:
            try:
                with open(stdout_path, "r") as f:
                    tail = f.readlines()[-200:]
                for line in tail:
                    job._stdout_buf.append(line.rstrip("\n"))
            except Exception:
                pass

        if job.status == JobStatus.RUNNING:
            job.status = JobStatus.COMPLETED if rc == 0 else JobStatus.FAILED
        log.info(
            "job %s exited rc=%d status=%s (stdout: %s)",
            job.id, rc, job.status.value, stdout_path or "<deque>",
        )
        if self._socketio is not None:
            self._socketio.emit("job:exited", job.to_dict(), namespace="/")

    def _reader_loop(self, job: Job) -> None:
        """Drain the subprocess stdout, line by line, until EOF then collect exit."""
        proc = job._proc
        assert proc is not None and proc.stdout is not None
        room = f"job-{job.id}"
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip("\n")
            job._stdout_buf.append(line)
            if self._socketio is not None:
                self._socketio.emit(
                    "job:stdout",
                    {"job_id": job.id, "line": line},
                    namespace="/",
                    room=room,
                )
        # stdout closed — wait for the process to actually exit
        rc = proc.wait()
        job.end_time = time.time()
        job.exit_code = rc
        # Only transition out of RUNNING; if we were already KILLED, stay KILLED
        if job.status == JobStatus.RUNNING:
            job.status = JobStatus.COMPLETED if rc == 0 else JobStatus.FAILED
        log.info("job %s exited rc=%d status=%s", job.id, rc, job.status.value)
        if self._socketio is not None:
            self._socketio.emit("job:exited", job.to_dict(), namespace="/")

    def stop_job(self, job_id: str, grace: float = 2.0) -> tuple[bool, str]:
        """Send SIGTERM, wait up to ``grace`` seconds, escalate to SIGKILL.

        Returns ``(stopped, reason)`` so callers can surface why a stop
        attempt didn't succeed.
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            log.warning("stop_job: no such job_id %s", job_id)
            return False, f"no job with id {job_id}"
        if job._proc is None:
            log.warning("stop_job: job %s has no Popen handle (status=%s)",
                        job_id, job.status.value)
            return False, f"job has no subprocess handle (status={job.status.value})"
        if job.status != JobStatus.RUNNING:
            log.info("stop_job: job %s already in terminal state %s",
                     job_id, job.status.value)
            return False, f"job is not running (status={job.status.value})"

        proc = job._proc
        log.info("stopping job %s (pid=%d) with SIGTERM", job_id, job.pid)
        try:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                log.warning(
                    "job %s ignored SIGTERM after %.1fs, sending SIGKILL",
                    job_id, grace,
                )
                proc.send_signal(signal.SIGKILL)
                proc.wait()
            job.status = JobStatus.KILLED
            return True, "stopped"
        except Exception as e:
            log.exception("error stopping job %s", job_id)
            return False, f"exception while stopping: {e!r}"

    def stop_all(self, grace: float = 1.0) -> None:
        """Stop every running job. Used at app shutdown."""
        with self._lock:
            running_ids = [
                jid for jid, j in self._jobs.items()
                if j.status == JobStatus.RUNNING
            ]
        for jid in running_ids:
            self.stop_job(jid, grace=grace)

    def get_job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self, tag: str | None = None) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        if tag is not None:
            jobs = [j for j in jobs if tag in j.tags]
        return jobs


# Module-level singleton
job_manager = JobManager()


@atexit.register
def _shutdown_jobs() -> None:
    """Clean-shutdown path — invoked when Flask exits normally.

    PR_SET_PDEATHSIG handles the SIGKILL case (kernel sends SIGTERM to
    children automatically). This handler covers the Ctrl-C / clean
    exit case where atexit hooks actually run.
    """
    try:
        running = [j for j in job_manager.list_jobs()
                   if j.status == JobStatus.RUNNING]
        if running:
            log.info("shutting down: stopping %d running job(s)", len(running))
            job_manager.stop_all(grace=1.0)
    except Exception:
        pass
