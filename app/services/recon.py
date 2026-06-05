"""Recon service — passive WiFi scanning via two parallel airodumps.

Pipeline:

1. ``start_scan()`` resolves which interfaces play which band roles
   (``wlan-mon-2g``, ``wlan-mon-5g``), puts each into monitor mode,
   and launches one airodump-ng job per band through the JobManager.
   Each writes its CSV under ``/tmp/pipineapple-recon-<band>-01.csv``
   (the ``-01`` suffix is airodump's auto-increment — we delete prior
   runs first so we always land on ``-01``).
2. A background poller wakes every ``POLL_INTERVAL`` seconds, parses
   both CSVs, merges them by BSSID (APs) and station MAC (clients),
   computes a snapshot, and emits ``recon:update`` over SocketIO if
   anything changed.
3. ``stop_scan()`` stops both jobs, removes the CSVs, and resets state.
   It deliberately does NOT restore the adapters to managed mode —
   the operator does that explicitly from Settings → Adapter
   Management. Recon often runs in cycles; keeping the adapters in
   monitor mode between runs avoids a slow up/down/up dance.

Stub mode (Mac dev): we skip the real adapter mode change + airodump
launch, and the poller serves canned data from ``airodump.stub_snapshot``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from app.services.job_manager import job_manager
from app.tools import airodump

log = logging.getLogger(__name__)

# ---------- Constants ----------
POLL_INTERVAL = 1.0                                  # seconds between CSV polls
CSV_DIR = Path("/tmp")
CSV_PREFIX_2G = CSV_DIR / "pipineapple-recon-2g"
CSV_PREFIX_5G = CSV_DIR / "pipineapple-recon-5g"
CSV_PATH_2G = CSV_DIR / "pipineapple-recon-2g-01.csv"
CSV_PATH_5G = CSV_DIR / "pipineapple-recon-5g-01.csv"

# Bands recognised by the service.
BAND_2G = "2.4GHz"
BAND_5G = "5GHz"

# Recon state machine values surfaced in ``get_status``.
STATE_IDLE     = "idle"
STATE_STARTING = "starting"
STATE_RUNNING  = "running"
STATE_STOPPING = "stopping"


class ReconService:
    """Singleton-ish: instantiated once by the factory + held in module state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: str = STATE_IDLE
        self._started_at: float | None = None
        self._job_id_2g: str | None = None
        self._job_id_5g: str | None = None
        self._poller_thread: threading.Thread | None = None
        self._poller_stop = threading.Event()
        # Snapshot caches — read by the poller, served by HTTP.
        self._aps: dict[str, dict] = {}        # bssid -> ap dict
        self._clients: dict[str, dict] = {}    # station_mac -> client dict
        self._last_emit_hash: int = 0          # to skip no-op emits

    # ---------- Public API ----------
    def start_scan(self) -> tuple[bool, list[str]]:
        """Launch both airodump jobs after putting the adapters into
        monitor mode. Idempotent: if already running, returns ok with
        a "no-op" message."""
        messages: list[str] = []
        with self._lock:
            if self._state in (STATE_RUNNING, STATE_STARTING):
                return True, ["scan already running"]
            self._state = STATE_STARTING
            self._aps.clear()
            self._clients.clear()
            self._last_emit_hash = 0

        try:
            ok_2g, msgs_2g, iface_2g = self._launch_band(
                role="wlan-mon-2g", band="bg", prefix=CSV_PREFIX_2G,
                csv_path=CSV_PATH_2G,
            )
            messages += msgs_2g
            if not ok_2g:
                with self._lock:
                    self._state = STATE_IDLE
                return False, messages

            ok_5g, msgs_5g, iface_5g = self._launch_band(
                role="wlan-mon-5g", band="a", prefix=CSV_PREFIX_5G,
                csv_path=CSV_PATH_5G,
            )
            messages += msgs_5g
            if not ok_5g:
                # 2.4 GHz is up but 5 GHz failed — tear down 2.4 to
                # leave a clean state. Better than running half-blind.
                messages.append("rolling back 2.4 GHz job")
                if self._job_id_2g:
                    job_manager.stop_job(self._job_id_2g)
                    self._job_id_2g = None
                with self._lock:
                    self._state = STATE_IDLE
                return False, messages

            messages.append(f"scanning on {iface_2g} (2.4GHz) + {iface_5g} (5GHz)")
        except Exception as e:
            log.exception("recon.start_scan failed")
            messages.append(f"unexpected error: {e}")
            with self._lock:
                self._state = STATE_IDLE
            return False, messages

        with self._lock:
            self._state = STATE_RUNNING
            self._started_at = time.time()

        self._start_poller()
        return True, messages

    def stop_scan(self) -> tuple[bool, list[str]]:
        """Stop both airodump jobs and the poller thread. Safe to call
        when already idle."""
        messages: list[str] = []
        with self._lock:
            if self._state == STATE_IDLE:
                return True, ["no scan running"]
            self._state = STATE_STOPPING

        # Stop poller first so we don't try to emit after the jobs die.
        self._stop_poller()

        for label, attr in (("2.4GHz", "_job_id_2g"), ("5GHz", "_job_id_5g")):
            jid = getattr(self, attr)
            if jid:
                ok, reason = job_manager.stop_job(jid)
                messages.append(f"stop {label}: {reason}")
                setattr(self, attr, None)

        # Clean up CSV files so the next scan starts on -01 again
        for p in (CSV_PATH_2G, CSV_PATH_5G):
            try:
                if p.is_file():
                    p.unlink()
            except OSError as e:
                messages.append(f"unlink {p.name}: {e}")

        with self._lock:
            self._state = STATE_IDLE
            self._started_at = None

        # Final emit with empty snapshot so the UI clears.
        self._emit_update(force=True)
        return True, messages

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state":        self._state,
                "started_at":   self._started_at,
                "ap_count":     len(self._aps),
                "client_count": len(self._clients),
                "stub":         airodump.is_stub(),
            }

    def get_snapshot(self) -> dict[str, Any]:
        """Current AP + client tables as plain JSON-serialisable dicts.

        Sort APs by signal strength descending (strongest first); sort
        clients by last-seen descending so the most recent activity
        bubbles up.
        """
        with self._lock:
            aps = sorted(
                self._aps.values(),
                key=lambda a: a.get("signal_dbm") or -999,
                reverse=True,
            )
            clients = sorted(
                self._clients.values(),
                key=lambda c: c.get("last_seen") or "",
                reverse=True,
            )
            return {
                "status":  self._status_unlocked(),
                "aps":     aps,
                "clients": clients,
            }

    # ---------- Internals ----------
    def _status_unlocked(self) -> dict[str, Any]:
        return {
            "state":        self._state,
            "started_at":   self._started_at,
            "ap_count":     len(self._aps),
            "client_count": len(self._clients),
            "stub":         airodump.is_stub(),
        }

    def _resolve_iface_for_role(self, role: str) -> str | None:
        """Look up the interface currently assigned to ``role`` in the
        adapter service. Returns None if no adapter has that role."""
        # Late import to avoid a circular dep with adapter service.
        from app.services.adapters import get_service as get_adapter_service
        roles = get_adapter_service().get_roles()
        for mac, assigned in roles.items():
            if assigned == role:
                # adapter roles are stored by MAC; we need the iface
                # name. Cross-reference with list_adapters.
                for ad in get_adapter_service().list_adapters():
                    if ad["mac"] == mac:
                        return ad["name"]
        return None

    def _launch_band(
        self, role: str, band: str, prefix: Path, csv_path: Path,
    ) -> tuple[bool, list[str], str | None]:
        """Resolve the interface for ``role``, ensure it's in monitor
        mode, delete any stale CSV, then launch airodump-ng. Returns
        ``(ok, messages, iface_name_or_None)``."""
        messages: list[str] = []

        iface = self._resolve_iface_for_role(role)
        if not iface:
            messages.append(
                f"no adapter assigned role {role!r}. "
                f"Assign one via Settings -> Adapter Management."
            )
            return False, messages, None

        if airodump.is_stub():
            # No real adapter ops, no real airodump. Pretend we launched.
            messages.append(f"(stub) would scan {iface} on band {band}")
            # Stash a fake job id so stop_scan can no-op cleanly.
            if role == "wlan-mon-2g":
                self._job_id_2g = f"stub-2g-{int(time.time())}"
            else:
                self._job_id_5g = f"stub-5g-{int(time.time())}"
            return True, messages, iface

        # Real path: ensure monitor mode.
        from app.services.adapters import get_service as get_adapter_service
        ok, mode_msgs = get_adapter_service().set_mode(iface, "monitor")
        messages += [f"set_mode {iface} monitor: {m}" for m in mode_msgs]
        if not ok:
            return False, messages, iface

        # Wipe stale CSVs from any prior run so airodump lands on -01.
        for p in csv_path.parent.glob(f"{prefix.name}-*.csv"):
            try:
                p.unlink()
            except OSError:
                pass
        # Also wipe accompanying log/cap files airodump may have left.
        for ext in ("kismet.csv", "kismet.netxml", "cap", "log.csv"):
            for p in csv_path.parent.glob(f"{prefix.name}-*.{ext}"):
                try:
                    p.unlink()
                except OSError:
                    pass

        cmd = airodump.build_cmd(iface, str(prefix), band=band)
        job = job_manager.start_job(
            cmd,
            name=f"recon-{band}",
            tags=["recon", band],
            stdout_path=f"/tmp/pipineapple-recon-{band}.log",
        )
        if role == "wlan-mon-2g":
            self._job_id_2g = job.id
        else:
            self._job_id_5g = job.id
        messages.append(f"started airodump on {iface} (job {job.id})")
        return True, messages, iface

    # ---------- Poller ----------
    def _start_poller(self) -> None:
        self._poller_stop.clear()
        t = threading.Thread(
            target=self._poller_loop, daemon=True, name="recon-poller",
        )
        self._poller_thread = t
        t.start()

    def _stop_poller(self) -> None:
        self._poller_stop.set()
        t = self._poller_thread
        if t is not None and t.is_alive():
            t.join(timeout=POLL_INTERVAL + 1.0)
        self._poller_thread = None

    def _poller_loop(self) -> None:
        """Read both CSVs (or stub data), merge, emit deltas."""
        log.info("recon poller starting")
        while not self._poller_stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("recon poller tick failed")
            self._poller_stop.wait(POLL_INTERVAL)
        log.info("recon poller exiting")

    def _tick(self) -> None:
        """One poll: parse both CSVs, merge into self._aps/_clients, emit."""
        if airodump.is_stub():
            aps_2g, clients_2g = airodump.stub_snapshot("bg")
            aps_5g, clients_5g = airodump.stub_snapshot("a")
        else:
            aps_2g, clients_2g = airodump.parse_csv(CSV_PATH_2G)
            aps_5g, clients_5g = airodump.parse_csv(CSV_PATH_5G)

        merged_aps: dict[str, dict] = {}
        for ap in aps_2g + aps_5g:
            existing = merged_aps.get(ap.bssid)
            if existing is None:
                merged_aps[ap.bssid] = ap.to_dict()
            else:
                # Same BSSID from both adapters — keep stronger signal.
                if (ap.signal_dbm or -999) > (existing.get("signal_dbm") or -999):
                    merged_aps[ap.bssid] = ap.to_dict()

        merged_clients: dict[str, dict] = {}
        for c in clients_2g + clients_5g:
            existing = merged_clients.get(c.station_mac)
            if existing is None:
                merged_clients[c.station_mac] = c.to_dict()
            else:
                # Merge probed ESSIDs across bands; keep stronger signal.
                merged_probed = list(set(existing.get("probed_essids", []))
                                     | set(c.probed_essids))
                if (c.signal_dbm or -999) > (existing.get("signal_dbm") or -999):
                    merged = c.to_dict()
                    merged["probed_essids"] = merged_probed
                    merged_clients[c.station_mac] = merged
                else:
                    existing["probed_essids"] = merged_probed

        with self._lock:
            self._aps = merged_aps
            self._clients = merged_clients

        self._emit_update()

    def _emit_update(self, force: bool = False) -> None:
        """Push the current snapshot to all SocketIO clients, unless
        nothing changed since the last emit."""
        snap = self.get_snapshot()
        # Cheap change detection: hash the sorted serialisable form.
        h = hash((
            tuple((a["bssid"], a.get("signal_dbm"), a.get("last_seen"),
                   a.get("beacons"), a.get("essid"))
                  for a in snap["aps"]),
            tuple((c["station_mac"], c.get("signal_dbm"),
                   c.get("last_seen"), c.get("packets"))
                  for c in snap["clients"]),
        ))
        if not force and h == self._last_emit_hash:
            return
        self._last_emit_hash = h

        # Late import — socketio is created by the factory.
        try:
            from app import socketio
            socketio.emit("recon:update", snap, namespace="/")
        except Exception:
            log.exception("recon: emit failed")


# Module-level singleton.
_service: ReconService | None = None


def get_service() -> ReconService:
    global _service
    if _service is None:
        _service = ReconService()
    return _service
