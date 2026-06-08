"""Crack-job dispatcher — scp + remote hashcat over SSH.

For each crack job:

1. Resolve the source ``.22000`` file via the handshakes service
   (cached conversion from the capture's pcap, or built on demand).
2. ``scp`` it to a temp path on the configured remote target.
3. Launch ``hashcat -m 22000`` over SSH via the JobManager, with
   stdout redirected to a per-job log file.
4. A parser thread tails the log file every ``PARSE_INTERVAL`` seconds,
   extracts the Speed / Progress / Recovered / ETA fields hashcat
   emits in its periodic status blocks, and emits ``crack:status``
   SocketIO events for the UI.
5. When the SSH session ends, parse the final state, extract the
   cracked PSK if any, and persist to ``$DATA_DIR/crack_jobs.json``.

The Pi never does the actual PSK crypto — it's a dispatcher + a
progress-streaming proxy. ssh runs the show; hashcat runs on the
remote's GPU.

Async stop, app_context-pushing daemon threads, same pattern we
hardened in S06/S07. SSH process is killed via JobManager.stop_job
(SIGTERM then SIGKILL) — hashcat exits cleanly on SIGTERM and the SSH
session collapses with it.
"""

from __future__ import annotations

import json
import logging
import re
import signal
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.services.crack_targets import (
    _ssh_argv,
    run_scp,
)
from app.services.job_manager import job_manager

log = logging.getLogger(__name__)


# ---------- Constants ----------

PARSE_INTERVAL = 2.0           # seconds — log-tail poll cadence
HASHCAT_STATUS_TIMER = 10      # passed to hashcat --status-timer
SCP_TIMEOUT = 60.0             # .22000 files are tiny, 60s is generous


# ---------- One in-flight crack job ----------

class _CrackJob:
    """In-memory state for one running crack. Snapshots the target +
    capture record at start time so subsequent target/capture deletes
    don't break the running job's accounting."""

    def __init__(
        self,
        capture: dict[str, Any],
        target: dict[str, Any],
        hash_path_local: Path,
    ) -> None:
        self.id = uuid.uuid4().hex
        self.capture_id = capture["id"]
        self.target_id = target["id"]
        self.capture = dict(capture)              # snapshot
        self.target = dict(target)                # snapshot (incl. wordlist_path)
        self.hash_path_local = hash_path_local
        # Per-job remote temp file so concurrent runs against the same
        # target don't clobber each other.
        self.hash_path_remote = f"/tmp/pipineapple-{self.id}.22000"
        self.log_path = Path(f"/tmp/pipineapple-crack-{self.id}.log")
        self.job_id: str | None = None             # JobManager id
        self.status: str = "queued"                # queued / running / done / failed / stopped
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.cracked_password: str | None = None
        # Latest parsed status fields — pushed to UI via crack:status
        self.last_speed_hs: int | None = None
        self.last_percent: float | None = None
        self.last_recovered: int = 0
        self.last_eta: str | None = None
        self._stop_event = threading.Event()
        self._parser_thread: threading.Thread | None = None
        # Tail position so we don't re-parse the whole log every tick
        self._log_pos: int = 0


# ---------- The service ----------

class CrackService:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._jobs_path = data_dir / "crack_jobs.json"
        self._lock = threading.Lock()
        self._active: dict[str, _CrackJob] = {}

    # ---------- Public API ----------
    def start_crack(
        self, capture_id: str, target_id: str,
    ) -> tuple[bool, list[str], dict[str, Any] | None]:
        """Resolve capture + target, scp the .22000, launch hashcat
        via JobManager. Returns (ok, messages, job_dict_or_None)."""
        msgs: list[str] = []

        # 1. Resolve capture + ensure .22000 exists locally
        from app.services.handshakes import get_service as get_hs_svc
        hs = get_hs_svc()
        cap = hs.get_capture_record(capture_id)
        if cap is None:
            return False, [f"no capture with id {capture_id}"], None
        hash_path, conv_msg = hs.resolve_or_build_22000(cap)
        if hash_path is None:
            return False, [f"could not build .22000: {conv_msg}"], None
        msgs.append(f"hash file: {conv_msg}")

        # 2. Resolve target
        from app.services.crack_targets import get_service as get_ct_svc
        ct = get_ct_svc()
        target = ct.get_target(target_id)
        if target is None:
            return False, [f"no target with id {target_id}"], None

        # 3. Build job record (need its id for the remote path)
        job = _CrackJob(cap, target, hash_path)

        # 4. scp the .22000 to remote /tmp
        scp_r = run_scp(
            self._data_dir, target, hash_path, job.hash_path_remote,
            timeout=SCP_TIMEOUT,
        )
        if scp_r.returncode != 0:
            stderr = (scp_r.stderr or "unknown").strip().splitlines()
            last_err = stderr[-1] if stderr else "scp failed"
            return False, msgs + [f"scp failed: {last_err}"], None
        msgs.append(f"scp ok ({hash_path.stat().st_size}B → {target['name']})")

        # 5. Build the remote hashcat command. --potfile-disable so
        #    each run is independent (don't auto-recover an earlier
        #    crack from the remote's potfile). --quiet trims noise.
        #    --status with --status-timer emits the periodic blocks
        #    our parser reads. We chain `rm -f` to clean up the temp
        #    .22000 on the remote regardless of hashcat's exit status.
        remote_cmd = (
            f"hashcat -m 22000 --quiet --status --status-timer={HASHCAT_STATUS_TIMER} "
            f"--potfile-disable {job.hash_path_remote} {target['wordlist_path']}; "
            f"_rc=$?; rm -f {job.hash_path_remote}; exit $_rc"
        )

        # 6. Launch via JobManager. stdout_path captures hashcat's
        #    status output to a file we tail. The ssh subprocess is
        #    JobManager-owned so stop_crack can SIGTERM it (which
        #    propagates to hashcat on the remote).
        ssh_argv = _ssh_argv(self._data_dir, target, remote_cmd)
        jm_job = job_manager.start_job(
            ssh_argv,
            name=f"crack-{job.id[:8]}",
            tags=["crack"],
            stdout_path=str(job.log_path),
        )
        job.job_id = jm_job.id
        job.started_at = time.time()
        job.status = "running"

        # 7. Track + start the parser thread (daemon + app_context — same
        #    pattern we hardened in S07's recon teardown).
        from flask import current_app
        app = current_app._get_current_object()

        def _run() -> None:
            try:
                with app.app_context():
                    self._parser_loop(job)
            except Exception:
                log.exception("crack parser crashed for job %s", job.id)
            finally:
                self._finalize_job(job)

        t = threading.Thread(target=_run, daemon=True,
                             name=f"crack-parser-{job.id[:8]}")
        job._parser_thread = t
        with self._lock:
            self._active[job.id] = job
        t.start()

        msgs.append(f"launched hashcat (job {jm_job.id})")
        return True, msgs, self._job_to_dict(job)

    def stop_crack(self, job_id: str) -> tuple[bool, str]:
        """Stop a running crack job. SIGTERMs the SSH session, which
        SIGTERMs hashcat on the remote (SSH propagates), which exits
        cleanly. Best-effort."""
        with self._lock:
            job = self._active.get(job_id)
        if job is None:
            return False, f"no active crack job with id {job_id}"
        job._stop_event.set()
        if job.job_id:
            ok, reason = job_manager.stop_job(
                job.job_id, grace=5.0, first_signal=signal.SIGTERM,
            )
            log.info("crack %s: stop ssh: %s", job.id, reason)
        return True, "stopping in background"

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return all persisted crack jobs (oldest -> newest), plus any
        currently-active ones not yet persisted. Used by the UI."""
        out: list[dict[str, Any]] = []
        # Persisted history
        if self._jobs_path.is_file():
            try:
                data = json.loads(self._jobs_path.read_text())
                out.extend(data.get("jobs") or [])
            except (json.JSONDecodeError, OSError) as e:
                log.warning("crack_jobs.json read failed: %s", e)
        # Active overlay — running jobs aren't in the persisted file yet
        with self._lock:
            for job in self._active.values():
                out.append(self._job_to_dict(job))
        # Sort newest-first
        out.sort(key=lambda j: j.get("started_at") or 0, reverse=True)
        # De-dup if a job is somehow in both (active + persisted) — keep active
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for j in out:
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            deduped.append(j)
        return deduped

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        for j in self.list_jobs():
            if j["id"] == job_id:
                return j
        return None

    # ---------- Internals ----------
    def _parser_loop(self, job: _CrackJob) -> None:
        """Tail the per-job log file, parse hashcat status blocks,
        emit crack:status SocketIO events when the parsed state changes."""
        log.info("crack parser starting for job %s -> %s",
                 job.id, job.target["name"])
        last_emit_key: tuple | None = None
        while not job._stop_event.is_set():
            # Poll the log file
            try:
                self._tail_and_parse(job)
            except Exception:
                log.exception("crack parser tick failed for %s", job.id)

            # Detect whether the SSH job has exited (JobManager flips
            # its status). If so, do one final parse then break.
            from app.services.job_manager import JobStatus
            jm_job = job_manager._jobs.get(job.job_id) if job.job_id else None
            jm_status = jm_job.status if jm_job is not None else None
            done = jm_status is not None and jm_status != JobStatus.RUNNING

            # Emit if state changed (cheap dedup so we don't spam
            # SocketIO every 2s with identical data)
            key = (job.last_speed_hs, job.last_percent,
                   job.last_recovered, bool(job.cracked_password), done)
            if key != last_emit_key:
                last_emit_key = key
                self._emit_status(job, ended=False)

            if done:
                # One last parse to catch the final flush
                try:
                    self._tail_and_parse(job)
                except Exception:
                    log.exception("crack parser final tail failed for %s", job.id)
                # Determine final status
                if job.cracked_password:
                    job.status = "done"
                elif job._stop_event.is_set():
                    job.status = "stopped"
                elif jm_status and jm_status.value in ("killed",):
                    job.status = "stopped"
                elif jm_job and jm_job.exit_code == 1:
                    # hashcat exits 1 when the wordlist is exhausted
                    # without cracking — that's an expected outcome,
                    # not a failure of the dispatch.
                    job.status = "exhausted"
                else:
                    job.status = "failed"
                job.ended_at = time.time()
                log.info("crack %s ended: status=%s recovered=%d cracked=%s",
                         job.id, job.status, job.last_recovered,
                         bool(job.cracked_password))
                return

            job._stop_event.wait(PARSE_INTERVAL)
        # _stop_event tripped — let the finalize handler take over
        job.status = "stopped"
        job.ended_at = time.time()

    def _tail_and_parse(self, job: _CrackJob) -> None:
        """Read new bytes from the log file, update job state."""
        if not job.log_path.is_file():
            return
        try:
            with open(job.log_path, "rb") as f:
                f.seek(job._log_pos)
                new_bytes = f.read()
                job._log_pos = f.tell()
        except OSError:
            return
        if not new_bytes:
            return
        try:
            text = new_bytes.decode("utf-8", errors="replace")
        except Exception:
            return

        parsed = _parse_status(text)
        if "speed_hs" in parsed:
            job.last_speed_hs = parsed["speed_hs"]
        if "percent" in parsed:
            job.last_percent = parsed["percent"]
        if "recovered" in parsed:
            job.last_recovered = parsed["recovered"]
        if "eta" in parsed:
            job.last_eta = parsed["eta"]

        # Cracked PSK detection — hashcat prints the cracked line
        # whenever it finds a PSK, regardless of --quiet
        psk = _extract_cracked_psk(text)
        if psk is not None and job.cracked_password is None:
            job.cracked_password = psk
            log.info("crack %s found PSK: <length=%d>", job.id, len(psk))

    def _finalize_job(self, job: _CrackJob) -> None:
        """Persist to crack_jobs.json, emit final ended status, drop
        from active map. Always runs (called from the daemon thread's
        finally block)."""
        try:
            self._persist_job(job)
        except Exception:
            log.exception("persist crack job failed for %s", job.id)
        with self._lock:
            self._active.pop(job.id, None)
        self._emit_status(job, ended=True)

    def _persist_job(self, job: _CrackJob) -> None:
        try:
            data = json.loads(self._jobs_path.read_text())
            if not isinstance(data, dict) or "jobs" not in data:
                data = {"jobs": []}
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"jobs": []}
        data["jobs"].append(self._job_to_dict(job))
        self._jobs_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._jobs_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._jobs_path)

    def _emit_status(self, job: _CrackJob, *, ended: bool) -> None:
        try:
            from app import socketio
            payload = self._job_to_dict(job)
            payload["ended"] = ended
            socketio.emit("crack:status", payload, namespace="/")
        except Exception:
            log.exception("crack status emit failed for %s", job.id)

    def _job_to_dict(self, job: _CrackJob) -> dict[str, Any]:
        return {
            "id":               job.id,
            "capture_id":       job.capture_id,
            "target_id":        job.target_id,
            "target_name":      job.target.get("name"),
            "target_host":      job.target.get("host"),
            "capture_bssid":    job.capture.get("bssid"),
            "capture_essid":    job.capture.get("essid_at_capture"),
            "started_at":       job.started_at,
            "ended_at":         job.ended_at,
            "status":           job.status,
            "cracked_password": job.cracked_password,
            "last_speed_hs":    job.last_speed_hs,
            "last_percent":     job.last_percent,
            "last_recovered":   job.last_recovered,
            "last_eta":         job.last_eta,
        }


# ---------- hashcat status / output parsers ----------

# Examples we parse (varies a little by hashcat version):
#   Speed.#1.........:   12345 H/s (4.92ms) @ Accel:512 Loops:64 ...
#   Speed.#*.........:   12345 H/s
#   Speed............:   12345 H/s
_SPEED_RE = re.compile(
    r"Speed\.[\#\*0-9]*\.+:\s*([\d.]+)\s*(H/s|kH/s|MH/s|GH/s|TH/s)",
    re.IGNORECASE,
)
# Progress.........: 1234567/14344391 (8.61%)
_PROG_RE = re.compile(
    r"Progress\.+:\s*(\d+)/(\d+)\s*\(([\d.]+)%\)",
    re.IGNORECASE,
)
# Recovered........: 1/1 (100.00%) Digests
_RECOVERED_RE = re.compile(
    r"Recovered\.+:\s*(\d+)/(\d+)",
    re.IGNORECASE,
)
# Time.Estimated...: Mon Jun  9 00:42:13 2026 (42 mins, 7 secs)
_ETA_RE = re.compile(
    r"Time\.Estimated\.+:\s*[^\(]*\(([^)]+)\)",
    re.IGNORECASE,
)
# Cracked PSK line: WPA*<type>*<...>*<...>*<...>*<essid_hex>*<...>*<...>*<flags>:password
# All 22000 header fields are hex/numeric/star-separated and never
# contain ':', so the FIRST ':' in the line is the password delimiter.
# We use str.partition (not regex) so passwords containing ':' are
# preserved verbatim.
_CRACKED_LINE_RE = re.compile(r"^WPA\*[^\r\n:]+:[^\r\n]+$", re.MULTILINE)

_SPEED_MULT = {
    "H/s": 1, "kH/s": 1_000, "MH/s": 1_000_000,
    "GH/s": 1_000_000_000, "TH/s": 1_000_000_000_000,
}


def _parse_status(text: str) -> dict[str, Any]:
    """Pull Speed / Progress / Recovered / ETA out of a chunk of hashcat
    stdout. Returns whatever it found; missing keys mean "not in this
    chunk", caller should not zero them out."""
    out: dict[str, Any] = {}
    m = _SPEED_RE.search(text)
    if m:
        try:
            out["speed_hs"] = int(float(m.group(1)) * _SPEED_MULT[m.group(2)])
        except (ValueError, KeyError):
            pass
    m = _PROG_RE.search(text)
    if m:
        try:
            out["percent"] = float(m.group(3))
        except ValueError:
            pass
    m = _RECOVERED_RE.search(text)
    if m:
        try:
            out["recovered"] = int(m.group(1))
        except ValueError:
            pass
    m = _ETA_RE.search(text)
    if m:
        out["eta"] = m.group(1).strip()
    return out


def _extract_cracked_psk(text: str) -> str | None:
    """Find a cracked-PSK line like WPA*02*...:hunter2. None if not
    found in this chunk. Returns just the password (everything after
    the FIRST ':' on the line — passwords can contain ':' but 22000
    header fields cannot)."""
    m = _CRACKED_LINE_RE.search(text)
    if not m:
        return None
    _, _, psk = m.group(0).partition(":")
    psk = psk.strip()
    # Guard: tab-separated fields hashcat sometimes appends
    if "\t" in psk:
        psk = psk.split("\t", 1)[0].strip()
    return psk or None


# ---------- Module singleton ----------

_service: "CrackService | None" = None


def get_service() -> CrackService:
    global _service
    if _service is None:
        from flask import current_app
        _service = CrackService(current_app.config["DATA_DIR"])
    return _service
