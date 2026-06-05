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
# airodump auto-names the pcap with the same -01 prefix as the csv
PCAP_PATH_2G = CSV_DIR / "pipineapple-recon-2g-01.cap"
PCAP_PATH_5G = CSV_DIR / "pipineapple-recon-5g-01.cap"

# Injection radio (Phase B/C). Resolved via the same role mechanism the
# recon adapters use (udev name or explicit role assignment).
INJECT_ROLE = "wlan-ap"

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
        when already idle.

        Teardown order matters for MT76/Realtek driver stability:

        1. Stop the poller so we don't try to emit after the jobs die.
        2. Send SIGINT to airodump-ng (not SIGTERM!). The aircrack-ng
           tools install a SIGINT handler that flushes CSV + pcap,
           releases the radio cleanly, and tears down the channel
           hopper. SIGTERM bypasses that handler and leaves the MT76
           driver in a state where the next operation (or just
           idleness) can kernel-hang the USB controller — which on
           the Pi 5 also serves the SSD, locking the whole box.
        3. Wait a beat after SIGINT for the driver flush to complete
           before bringing the interface down.

        Stop_signal needs to be SIGINT specifically; the JobManager
        accepts it via the new first_signal parameter.
        """
        import signal as _signal
        import time as _time

        messages: list[str] = []
        with self._lock:
            if self._state == STATE_IDLE:
                return True, ["no scan running"]
            self._state = STATE_STOPPING

        # Stop poller first so we don't try to emit after the jobs die.
        self._stop_poller()

        # Resolve interfaces so we can bring them down post-stop. We
        # don't take them down BEFORE airodump-ng exits: airodump-ng's
        # own SIGINT handler does the channel reset, and yanking the
        # interface out from under a running airodump is itself a
        # source of driver hangs.
        ifaces: list[str] = []
        for role in ("wlan-mon-2g", "wlan-mon-5g"):
            iface = self._resolve_iface_for_role(role)
            if iface:
                ifaces.append(iface)

        for label, attr in (("2.4GHz", "_job_id_2g"), ("5GHz", "_job_id_5g")):
            jid = getattr(self, attr)
            if jid:
                # SIGINT + longer grace — give the aircrack-ng handler
                # time to flush. 5s is generous; typical clean exit is <1s.
                ok, reason = job_manager.stop_job(
                    jid, grace=5.0, first_signal=_signal.SIGINT)
                messages.append(f"stop {label} (SIGINT): {reason}")
                setattr(self, attr, None)

        # Brief settle so any post-exit driver work finishes before we
        # touch the interfaces again. Without this we've seen the MT76
        # USB chipsets reset partway through the down-then-mode-change
        # dance.
        _time.sleep(0.5)

        # Now safe to bring the monitor interfaces down. We deliberately
        # do NOT flip them back to managed mode — recon often runs in
        # cycles and the up/down/up dance is wasteful. Operator flips
        # mode via Settings → Adapter Management when they're done.
        if not airodump.is_stub():
            from app.tools import iproute
            for iface in ifaces:
                try:
                    iproute.set_link_state(iface, "down")
                    messages.append(f"set {iface} down")
                except Exception as e:
                    messages.append(f"{iface} down failed: {e}")

        # Clean up CSV + pcap files so the next scan starts on -01 again
        for p in (CSV_PATH_2G, CSV_PATH_5G, PCAP_PATH_2G, PCAP_PATH_5G):
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

    # ---------- Detail views (slide-out backend) ----------
    def get_ap_detail(self, bssid: str) -> dict[str, Any] | None:
        """Snapshot record for one AP + parsed IEs from its pcap.

        The pcap to read is determined by the AP's band: 2.4 GHz APs
        live in PCAP_PATH_2G, 5 GHz in PCAP_PATH_5G. If the band is
        unknown (channel didn't land), try both — beacon parser
        returns None if there's no match.
        """
        from app.tools import beacon_parser
        target = bssid.lower()
        with self._lock:
            ap = None
            for b, rec in self._aps.items():
                if b.lower() == target:
                    ap = dict(rec)
                    break
        if ap is None:
            return None

        # Pick the right pcap based on the AP's band; fall back to
        # whichever pcap exists if band is unknown.
        band = ap.get("band")
        candidates: list[Path] = []
        if band == BAND_2G:
            candidates = [PCAP_PATH_2G]
        elif band == BAND_5G:
            candidates = [PCAP_PATH_5G]
        else:
            candidates = [PCAP_PATH_2G, PCAP_PATH_5G]

        parsed = None
        for p in candidates:
            parsed = beacon_parser.parse_latest_beacon(p, bssid)
            if parsed is not None:
                break

        # Clients currently associated to this AP (handy for the slide-
        # out's actions: "deauth all", per-client deauth in S07+).
        with self._lock:
            associated = [
                dict(c) for c in self._clients.values()
                if (c.get("bssid") or "").lower() == target
            ]

        return {
            "ap":         ap,
            "beacon":     parsed,         # None if pcap missing or BSSID not in it
            "associated": associated,
        }

    def get_client_detail(self, station_mac: str) -> dict[str, Any] | None:
        """Snapshot record for one client + full probe history.

        Probe history is parsed from both pcaps (clients can be visible
        on either band's monitor adapter depending on which channels
        the client probes on) and filtered to the requested MAC.
        """
        from app.tools import beacon_parser
        target = station_mac.lower()
        with self._lock:
            client = None
            for m, rec in self._clients.items():
                if m.lower() == target:
                    client = dict(rec)
                    break
        if client is None:
            return None

        # Aggregate probes from both pcaps. The two pcaps cover different
        # bands but a client probing for a given SSID may show up on
        # either (probe requests aren't band-locked the way beacons are
        # — clients probe their entire PNL on every channel they hop).
        probes: list[dict[str, Any]] = []
        seen_keys: set[tuple[str, str]] = set()
        for p in (PCAP_PATH_2G, PCAP_PATH_5G):
            for entry in beacon_parser.parse_probe_requests(p):
                if entry["station_mac"].lower() != target:
                    continue
                key = (entry["station_mac"], entry["ssid"])
                if key in seen_keys:
                    # Same probe seen on both bands — merge counts +
                    # widen the timing window.
                    for existing in probes:
                        if (existing["station_mac"], existing["ssid"]) == key:
                            existing["count"] += entry["count"]
                            existing["first_seen"] = min(
                                existing["first_seen"], entry["first_seen"])
                            existing["last_seen"] = max(
                                existing["last_seen"], entry["last_seen"])
                            break
                else:
                    probes.append(dict(entry))
                    seen_keys.add(key)
        probes.sort(key=lambda d: d["last_seen"], reverse=True)
        return {"client": client, "probes": probes}

    # ---------- Deauth (injection on wlan-ap) ----------
    def deauth_ap(
        self, bssid: str, client_mac: str | None = None, count: int = 10,
    ) -> tuple[bool, list[str]]:
        """Send deauth frames at ``bssid`` via the injection radio.

        Orchestration:

        1. Look up the AP in the current snapshot to get its channel
           (we can't pin without knowing it).
        2. Resolve the injection interface (role/iface ``wlan-ap``).
        3. Drop NM management on the injection iface (idempotent).
        4. Bring iface down → set monitor → bring up.
        5. Pin to the target's channel via ``iw set channel``.
        6. Run aireplay-ng --deauth.

        Returns ``(ok, messages)``. Messages cover each step so the
        UI can show what happened.
        """
        from app.services.adapters import get_service as get_adapter_service
        from app.tools import aireplay, iw, iproute, nm

        messages: list[str] = []
        target_bssid = bssid.lower()

        with self._lock:
            ap = None
            for b, rec in self._aps.items():
                if b.lower() == target_bssid:
                    ap = rec
                    break
        if ap is None:
            return False, [f"BSSID {bssid} not in current scan"]
        channel = ap.get("channel")
        if channel is None:
            return False, [f"BSSID {bssid} has no channel info — can't pin"]

        iface = self._resolve_iface_for_role(INJECT_ROLE)
        if not iface:
            return False, [
                f"no adapter assigned role {INJECT_ROLE!r}. "
                "Assign one via Settings → Adapter Management or via "
                "udev rules."
            ]

        # Drop NM (no-op if already unmanaged)
        ok, msg = nm.set_managed(iface, managed=False)
        messages.append(f"nm: {msg}")
        if not ok:
            return False, messages

        # Monitor mode (down → set type → up)
        ok, mode_msgs = get_adapter_service().set_mode(iface, "monitor")
        messages += [f"set_mode: {m}" for m in mode_msgs]
        if not ok:
            return False, messages

        # Pin channel
        ok, msg = iw.set_channel(iface, int(channel))
        messages.append(f"channel: {msg}")
        if not ok:
            return False, messages

        # Fire
        ok, msg = aireplay.send_deauth(
            iface, bssid, client_mac=client_mac, count=count)
        messages.append(f"deauth: {msg}")
        return ok, messages

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
        """Find the interface name for a recon role.

        Two ways an adapter can claim a role:

        1. Explicit role assignment in adapter_roles.json (Settings →
           Adapter Management dropdown). Keyed by MAC.
        2. Implicit via udev sticky name. Once udev renames an
           interface to ``wlan-mon-2g``, the *name* IS the role —
           no separate JSON entry needed. This is how the operator's
           current setup works: udev rules are pre-configured by MAC
           and the role-assignment UI was never used.

        Try the explicit assignment first; fall back to the name match.
        """
        # Late import to avoid a circular dep with adapter service.
        from app.services.adapters import get_service as get_adapter_service
        adapter_svc = get_adapter_service()
        adapters = adapter_svc.list_adapters()

        # 1. Role assignment by MAC
        roles = adapter_svc.get_roles()
        for mac, assigned in roles.items():
            if assigned == role:
                for ad in adapters:
                    if ad["mac"] == mac:
                        return ad["name"]

        # 2. Direct interface name match — udev already gave it the
        #    canonical role name.
        for ad in adapters:
            if ad["name"] == role:
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

        # SSID enrichment — clients only carry the AP BSSID from airodump,
        # which is a MAC and unreadable. Look up the BSSID in the merged
        # AP table and stamp ap_ssid onto the client record. If the AP
        # isn't in our scan (out of range / wrong band / hopper missed
        # the beacon), ap_ssid stays empty and the UI falls back to
        # showing the raw BSSID. Probed-ESSID lookups get the same
        # treatment: per probed name, is there a known AP with that
        # SSID in range? Useful signal for Karma-style impersonation
        # judgements later.
        #
        # Casing trap: airodump emits BSSIDs uppercase; clients carry the
        # same uppercase form for ``bssid``. Lowercase BOTH sides of the
        # lookup so we don't depend on it.
        bssid_to_ssid = {b.lower(): a.get("essid", "")
                         for b, a in merged_aps.items()}
        known_ssids = {a.get("essid", "") for a in merged_aps.values()
                       if a.get("essid")}
        for c in merged_clients.values():
            ap_bssid = (c.get("bssid") or "").lower()
            c["ap_ssid"] = bssid_to_ssid.get(ap_bssid, "")
            # Split probes into in-range vs not-in-range. The
            # not-in-range list is the privacy-interesting one — those
            # are SSIDs the device remembers from elsewhere and is
            # actively asking about ("MyOldOfficeWifi", "JoesCoffee",
            # "Hotel-Vegas"). Operator's own devices probing for old
            # networks they no longer use = PNL hygiene problem worth
            # surfacing.
            probed = c.get("probed_essids", [])
            c["probed_in_range"]     = [s for s in probed if s in known_ssids]
            c["probed_not_in_range"] = [s for s in probed if s and s not in known_ssids]

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
