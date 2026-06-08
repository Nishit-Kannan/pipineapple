"""PineAP — the rogue-AP engine.

The Hak5 Pineapple's headline feature. Owns:

* The **SSID pool** at ``$DATA_DIR/pineap_pool.json`` — a deduplicated
  set of SSIDs the platform has observed (recon scans, probe-request
  observations) or the operator has added manually. Pinned entries are
  protected from any future auto-eviction. Source-tagged so the UI can
  show why each entry was added.
* The **mode state** at ``$DATA_DIR/pineap_state.json`` — which radio
  the engine is bound to, what mode it's in, whether broadcast is
  enabled, whether capture is enabled, last-changed timestamp.

The Pineapple's three operation modes, faithful to the Hak5 semantics:

* ``passive`` — engine is configured but hostapd is not broadcasting.
  Use to stage settings without creating airspace noise. Default.
* ``active`` — broadcasting the pool as fake beacons. Every device in
  range sees the pool as available networks. Real attack surface
  starts here. (S11 lights this up; S10 only persists the choice.)
* ``advanced`` — ``active`` + Karma probe-response replies. Probe
  responses for *any* SSID a client asks for, not just pool entries.
  Most dangerous mode against saved open networks. (S11.)

Session 10 builds the singleton + pool/state CRUD + Settings tab + the
auto-population hooks. Hostapd lifecycle is stubbed: ``start()`` in
``passive`` mode just flips the state flag and emits a notification.
S11 will replace the stub with real hostapd-via-JobManager.

Singleton because the service holds live state (running mode,
in-flight job id once S11 lands). Same lesson hardened in
``crack.py`` / ``networking.py``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import signal
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------- S11 lifecycle constants ----------

DEFAULT_IFACE       = "wlan-ap"
DEFAULT_KARMA_IFACE = "wlan-mon-5g"
# Injection radio for the optional Evil WPA "deauth the real AP"
# (evil-twin) coupling. The third Alfa — free once recon is paused,
# which Evil WPA does on start. Despite the 2g label it's a dual-band
# mt76x2u, so it tunes the target's channel on either band.
DEFAULT_DEAUTH_IFACE = "wlan-mon-2g"
DEFAULT_CHANNEL     = 6
DEFAULT_HW_MODE     = "g"
DEFAULT_PRIMARY_SSID = "PineApple"     # operator should override before going live
DEFAULT_SUBNET      = "10.0.0.0/24"
DEFAULT_GATEWAY     = "10.0.0.1"
DEFAULT_DHCP_RANGE  = ("10.0.0.10", "10.0.0.250")

# Config + log files for the rogue AP. Living in /tmp keeps them
# tmpfs-fast and self-clearing on reboot — matching the same pattern
# we use for the management AP. The dnsmasq log is the tailer's input.
HOSTAPD_CONFIG_PATH  = Path("/tmp/pipineapple-pineap-hostapd.conf")
DNSMASQ_CONFIG_PATH  = Path("/tmp/pipineapple-pineap-dnsmasq.conf")
DNSMASQ_LOG_PATH     = Path("/tmp/pipineapple-pineap-dnsmasq.log")
DNSMASQ_LEASES_PATH  = Path("/tmp/pipineapple-pineap-dnsmasq.leases")
DNSMASQ_PID_PATH     = Path("/tmp/pipineapple-pineap-dnsmasq.pid")


# ---------- Modes ----------

class PineAPMode(str, Enum):
    """str-enum so it round-trips through JSON without explicit
    encoding. Compare with ``PineAPMode.PASSIVE.value`` or by string
    equality (``state['mode'] == 'passive'``)."""
    OFF      = "off"          # Engine not configured / hostapd not running
    PASSIVE  = "passive"      # Configured but not broadcasting (S10 limit)
    ACTIVE   = "active"       # Broadcasting pool as fake beacons (S11)
    ADVANCED = "advanced"     # Active + Karma probe responses (S11)

    @classmethod
    def from_str(cls, s: str) -> "PineAPMode | None":
        try:
            return cls(s.lower().strip())
        except (AttributeError, ValueError):
            return None


# Sources we tag SSIDs with — informational only, but useful for
# operator review ("why is this in my pool?").
SOURCE_RECON  = "recon"      # Auto-added from a recon scan beacon
SOURCE_PROBE  = "probe"      # Auto-added from a client probe request
SOURCE_MANUAL = "manual"     # Operator typed it in
SOURCE_IMPORT = "import"     # Reserved for S13 import-from-file

# 802.11 caps SSIDs at 32 bytes (UTF-8). hostapd accepts up to 32 chars
# in its plaintext form; longer SSIDs require utf8_ssid=1 + hex
# encoding. We validate length in bytes (not codepoints) for spec
# compliance.
MAX_SSID_BYTES = 32

# Conservative character set for the manual-add path — printable ASCII
# plus space + a few common safe symbols. Auto-population sources
# (recon/probe) bypass this and only get the length check, because
# real-world SSIDs include emoji, CJK, etc. and we shouldn't drop
# them silently.
_MANUAL_SSID_RE = re.compile(r"^[\x20-\x7e]{1,32}$")


# ---------- Service ----------

class PineAPService:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir   = data_dir
        self._pool_path  = data_dir / "pineap_pool.json"
        self._state_path = data_dir / "pineap_state.json"
        self._lock = threading.Lock()
        # Lazy load — the factory creates the singleton during
        # _attach_services, before all config is settled. First call
        # to any public method triggers _load().
        self._state: dict[str, Any] | None = None
        self._pool:  list[dict[str, Any]] | None = None

    # ---------- Persistence ----------
    def _load(self) -> None:
        # Caller holds self._lock.
        if self._state is None:
            try:
                loaded = json.loads(self._state_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                loaded = {}
            # Merge defaults so older state files (e.g. S10 vintage,
            # missing the S11 lifecycle fields) get the new keys
            # populated rather than KeyError'ing later. Loaded values
            # always win for keys that exist on both sides.
            defaults = self._default_state()
            defaults.update(loaded)
            self._state = defaults
        if self._pool is None:
            try:
                data = json.loads(self._pool_path.read_text())
                self._pool = list(data.get("ssids") or [])
            except (FileNotFoundError, json.JSONDecodeError):
                self._pool = []

    def _default_state(self) -> dict[str, Any]:
        return {
            "mode":              PineAPMode.OFF.value,
            "broadcast_enabled": False,
            "capture_enabled":   False,
            "iface":             DEFAULT_IFACE,
            "karma_iface":       DEFAULT_KARMA_IFACE,
            "deauth_iface":      DEFAULT_DEAUTH_IFACE,
            "primary_ssid":      DEFAULT_PRIMARY_SSID,
            "primary_hidden":    False,    # ignore_broadcast_ssid for the primary BSS
            "channel":           DEFAULT_CHANNEL,
            "hw_mode":           DEFAULT_HW_MODE,
            "subnet":            DEFAULT_SUBNET,
            "gateway_ip":        DEFAULT_GATEWAY,
            "dhcp_range":        list(DEFAULT_DHCP_RANGE),
            # Salt for deterministic per-SSID BSSID generation. Auto-
            # generated on first save so the operator never has to
            # think about it. Stable across reboots, different across
            # deployments — see hostapd.bssid_for_ssid().
            "bssid_salt":        secrets.token_hex(16),
            # ---- Security mode (S12) ----
            # "open" (Open SSID tab) or "wpa2" (Evil WPA tab). The
            # engine is the same hostapd instance either way; this
            # just controls whether wpa=2 lines get rendered in the
            # config and whether we generate a random PSK at start.
            "security_mode":     "open",
            # Last generated rogue PSK (for operator visibility — the
            # value doesn't matter functionally since clients fail at
            # M3 with our random PSK, but operators want to know what
            # the engine set). Regenerated on every wpa2 Start.
            "last_rogue_psk":    None,
            # Evil WPA clone target metadata — populated by
            # clone_evil_wpa_target() when operator clicks "Clone to
            # PineAP" on a Recon AP slide-out. Lets the UI show
            # "cloning HomeNet (real BSSID a4:c1:38:...) → rogue BSSID
            # 82:5f:..." so the operator can tell them apart on the
            # Handshakes page.
            "evil_wpa_target":   None,
            "last_changed":      time.time(),
            "running":           False,    # True only while hostapd is up
            # JobManager IDs for the running daemons — used by stop()
            # to SIGTERM each one. Cleared on stop.
            "hostapd_job_id":    None,
            "dnsmasq_job_id":    None,
            # Karma + sentinel running flags (services manage their own
            # lifecycle; we just track for the UI status pill)
            "karma_running":     False,
            "sentinel_running":  False,
            # Evil WPA EAPOL sniffer running flag (S12)
            "evil_wpa_running":  False,
            # Optional "evil-twin" deauth coupling (S12): when True AND a
            # real target BSSID is known (i.e. cloned from Recon), Start
            # also fires broadcast deauth at the real AP on the spare
            # radio to force its clients to re-associate — some land on
            # our clone and hand us M1+M2. Default off, opt-in, lab-only.
            # No-op against MFP-required (802.11w) targets.
            "evil_wpa_deauth":   False,
        }

    def _save_state(self) -> None:
        # Caller holds self._lock.
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2))
        tmp.replace(self._state_path)

    def _save_pool(self) -> None:
        # Caller holds self._lock.
        self._pool_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._pool_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ssids": self._pool}, indent=2))
        tmp.replace(self._pool_path)

    # ---------- Public: state / mode ----------
    def get_state(self) -> dict[str, Any]:
        with self._lock:
            self._load()
            # Returning a copy so callers can't mutate our internal state
            return dict(self._state)  # type: ignore[arg-type]

    def set_mode(self, mode: str) -> tuple[bool, str]:
        m = PineAPMode.from_str(mode)
        if m is None:
            return False, f"unknown mode {mode!r} (expected off/passive/active/advanced)"
        with self._lock:
            self._load()
            if self._state["mode"] == m.value:
                return True, f"mode already {m.value}"
            # S10 limit: we can persist any mode but starting hostapd
            # in active/advanced isn't wired yet. Allow set_mode so
            # the operator can stage state; start() will reject
            # active/advanced until S11.
            old = self._state["mode"]
            self._state["mode"] = m.value
            self._state["last_changed"] = time.time()
            self._save_state()
        log.info("pineap mode: %s -> %s", old, m.value)
        return True, f"mode set to {m.value}"

    def set_broadcast(self, enabled: bool) -> tuple[bool, str]:
        with self._lock:
            self._load()
            self._state["broadcast_enabled"] = bool(enabled)
            self._state["last_changed"] = time.time()
            self._save_state()
        return True, f"broadcast {'enabled' if enabled else 'disabled'}"

    def set_capture(self, enabled: bool) -> tuple[bool, str]:
        with self._lock:
            self._load()
            self._state["capture_enabled"] = bool(enabled)
            self._state["last_changed"] = time.time()
            self._save_state()
        return True, f"capture {'enabled' if enabled else 'disabled'}"

    def set_ap_config(
        self,
        primary_ssid: str | None = None,
        channel: int | None = None,
        primary_hidden: bool | None = None,
        hw_mode: str | None = None,
        security_mode: str | None = None,
        evil_wpa_deauth: bool | None = None,
    ) -> tuple[bool, str]:
        """Update the rogue AP config (primary SSID + channel + hidden +
        band + security mode). Refuses while PineAP is running —
        operator must stop first, change config, restart. (Changing
        channel under a live hostapd would disconnect every client
        anyway.)

        ``security_mode`` is "open" (Open SSID tab path) or "wpa2"
        (Evil WPA tab path). When set to "wpa2" the engine will
        generate a fresh random PSK on every Start (logged in the
        notification drawer) — clients fail at M3 with our random
        PSK, but by then we've collected the M1+M2 partial.
        """
        with self._lock:
            self._load()
            if self._state["running"]:
                return False, "stop PineAP before changing AP config"
            if primary_ssid is not None:
                p = primary_ssid.strip()
                if not p or len(p.encode("utf-8")) > MAX_SSID_BYTES:
                    return False, "primary SSID must be 1-32 bytes UTF-8"
                self._state["primary_ssid"] = p
            if channel is not None:
                try:
                    ch = int(channel)
                except (TypeError, ValueError):
                    return False, "channel must be an int"
                if not (1 <= ch <= 196):
                    return False, f"channel {ch} out of valid range"
                self._state["channel"] = ch
            if primary_hidden is not None:
                self._state["primary_hidden"] = bool(primary_hidden)
            if hw_mode is not None:
                if hw_mode not in ("g", "a"):
                    return False, f"hw_mode must be 'g' or 'a', got {hw_mode!r}"
                self._state["hw_mode"] = hw_mode
            if security_mode is not None:
                if security_mode not in ("open", "wpa2"):
                    return False, f"security_mode must be 'open' or 'wpa2', got {security_mode!r}"
                # Changing away from wpa2 also clears the cloned-target
                # metadata so the UI doesn't show stale "cloning from..."
                # banners after the operator manually switches to open.
                if security_mode == "open" and self._state.get("security_mode") == "wpa2":
                    self._state["evil_wpa_target"] = None
                    # Switching back to open also disarms the evil-twin
                    # deauth coupling (it only makes sense for WPA clones).
                    self._state["evil_wpa_deauth"] = False
                self._state["security_mode"] = security_mode
            if evil_wpa_deauth is not None:
                self._state["evil_wpa_deauth"] = bool(evil_wpa_deauth)
            self._state["last_changed"] = time.time()
            self._save_state()
        return True, "AP config updated"

    def clone_evil_wpa_target(
        self,
        bssid: str,
        essid: str,
        channel: int,
        *,
        source_signal_dbm: int | None = None,
        source_security: str | None = None,
        source_mfp_required: bool | None = None,
    ) -> tuple[bool, str]:
        """Set up Evil WPA cloning from a Recon-observed AP. One-click
        from the Recon AP slide-out. Configures primary_ssid + channel
        + security_mode=wpa2 in one shot, plus records the source AP's
        metadata so the operator can correlate captured handshakes
        back to the real target on the Handshakes page.

        Refuses while PineAP is running."""
        bssid = (bssid or "").strip().lower()
        essid = (essid or "").strip()
        if not bssid or not essid:
            return False, "bssid and essid are required"
        try:
            ch = int(channel)
        except (TypeError, ValueError):
            return False, "channel must be an int"
        if not (1 <= ch <= 196):
            return False, f"channel {ch} out of valid range"
        if len(essid.encode("utf-8")) > MAX_SSID_BYTES:
            return False, f"essid exceeds {MAX_SSID_BYTES} bytes"
        # Channel selection determines hw_mode: 2.4GHz = 'g', 5GHz = 'a'.
        # mt76 cards on this hardware support both bands; the 'g' for
        # 5GHz check is fine because hostapd will refuse channels that
        # don't match the mode.
        hw_mode = "a" if ch >= 36 else "g"

        with self._lock:
            self._load()
            if self._state["running"]:
                return False, "stop PineAP before cloning a new target"
            self._state["primary_ssid"]   = essid
            self._state["channel"]        = ch
            self._state["hw_mode"]        = hw_mode
            self._state["security_mode"]  = "wpa2"
            self._state["primary_hidden"] = False
            self._state["evil_wpa_target"] = {
                "source_bssid":        bssid,
                "source_essid":        essid,
                "source_channel":      ch,
                "source_signal_dbm":   source_signal_dbm,
                "source_security":     source_security,
                # 802.11w state of the real AP. When True, the evil-twin
                # deauth coupling can't dislodge clients (frames rejected)
                # — the UI warns and the operator can still opt in, but it
                # will be a no-op. None = unknown (older recon detail).
                "source_mfp_required": bool(source_mfp_required) if source_mfp_required is not None else None,
                "cloned_at":           time.time(),
            }
            # Each fresh clone starts with the deauth coupling disarmed —
            # it's an explicit opt-in per target.
            self._state["evil_wpa_deauth"] = False
            self._state["last_changed"] = time.time()
            self._save_state()
        log.info("pineap: cloned Evil WPA target %r (BSSID %s, ch%d, sig=%s)",
                 essid, bssid, ch, source_signal_dbm)
        return True, f"cloned {essid!r} (real BSSID {bssid}, ch{ch}) — ready to Start"

    # ---------- Public: SSID pool ----------
    def list_pool(self) -> list[dict[str, Any]]:
        """Return all pool entries, pinned first, then newest-last_seen."""
        with self._lock:
            self._load()
            entries = [dict(e) for e in (self._pool or [])]
        entries.sort(
            key=lambda e: (not e.get("pinned", False),
                           -(e.get("last_seen") or 0)),
        )
        return entries

    def add_ssid(
        self, ssid: str, source: str = SOURCE_MANUAL,
        *, pin: bool = False,
    ) -> tuple[bool, str]:
        """Add or refresh an SSID in the pool. Idempotent — existing
        entries get their ``last_seen`` and ``observed_count`` bumped
        rather than duplicated. Validation depth depends on source:
        ``manual`` requires printable ASCII; auto-sources only enforce
        length so real-world non-ASCII SSIDs pass through."""
        ssid = ssid if ssid is not None else ""

        # Length: 802.11 caps at 32 bytes UTF-8
        if not ssid:
            return False, "empty SSID"
        ssid_bytes = ssid.encode("utf-8", errors="replace")
        if len(ssid_bytes) > MAX_SSID_BYTES:
            return False, f"SSID exceeds {MAX_SSID_BYTES} bytes"

        # Manual-add validation: printable ASCII only. Auto-sources
        # bypass — we'd rather store an emoji-laced SSID than silently
        # drop a real-world target.
        if source == SOURCE_MANUAL and not _MANUAL_SSID_RE.match(ssid):
            return False, "manual SSIDs must be 1-32 printable ASCII chars"

        now = time.time()
        with self._lock:
            self._load()
            assert self._pool is not None
            # Case-sensitive match — SSIDs ARE case-sensitive per spec
            existing = next((e for e in self._pool if e.get("ssid") == ssid), None)
            if existing:
                existing["last_seen"]      = now
                existing["observed_count"] = (existing.get("observed_count") or 0) + 1
                if pin:
                    existing["pinned"] = True
                # If the existing entry was auto-collected and operator
                # is now manually adding, promote the source so the UI
                # shows it as operator-curated.
                if source == SOURCE_MANUAL and existing.get("source") != SOURCE_MANUAL:
                    existing["source"] = SOURCE_MANUAL
                self._save_pool()
                return True, f"refreshed {ssid!r}"
            # New entry
            self._pool.append({
                "ssid":           ssid,
                "source":         source,
                "first_seen":     now,
                "last_seen":      now,
                "observed_count": 1,
                "pinned":         pin,
                "hidden":         False,    # operator can mark to suppress from broadcast
            })
            self._save_pool()
        log.info("pineap pool +%s (%s)", ssid, source)
        return True, f"added {ssid!r}"

    def remove_ssid(self, ssid: str) -> tuple[bool, str]:
        with self._lock:
            self._load()
            assert self._pool is not None
            new_pool = [e for e in self._pool if e.get("ssid") != ssid]
            if len(new_pool) == len(self._pool):
                return False, f"SSID {ssid!r} not in pool"
            self._pool = new_pool
            self._save_pool()
        return True, f"removed {ssid!r}"

    def set_pinned(self, ssid: str, pinned: bool) -> tuple[bool, str]:
        with self._lock:
            self._load()
            assert self._pool is not None
            for e in self._pool:
                if e.get("ssid") == ssid:
                    e["pinned"] = bool(pinned)
                    self._save_pool()
                    return True, f"{ssid!r} {'pinned' if pinned else 'unpinned'}"
        return False, f"SSID {ssid!r} not in pool"

    def set_hidden(self, ssid: str, hidden: bool) -> tuple[bool, str]:
        """Mark an entry to be excluded from broadcast without removing
        it. Useful for "in pool but not advertising right now" without
        losing the auto-collected timestamps."""
        with self._lock:
            self._load()
            assert self._pool is not None
            for e in self._pool:
                if e.get("ssid") == ssid:
                    e["hidden"] = bool(hidden)
                    self._save_pool()
                    return True, f"{ssid!r} {'hidden' if hidden else 'visible'}"
        return False, f"SSID {ssid!r} not in pool"

    def clear_pool(self, *, include_pinned: bool = False) -> tuple[bool, str, int]:
        """Drop all entries. By default pinned entries survive; pass
        ``include_pinned=True`` to nuke everything."""
        with self._lock:
            self._load()
            assert self._pool is not None
            before = len(self._pool)
            if include_pinned:
                self._pool = []
            else:
                self._pool = [e for e in self._pool if e.get("pinned")]
            removed = before - len(self._pool)
            self._save_pool()
        return True, f"cleared {removed} entries", removed

    # ---------- Public: full lifecycle (S11) ----------
    def start(self) -> tuple[bool, list[str]]:
        """Bring up PineAP per the current mode.

        passive  → just flips the running flag (operator wants the
                   engine "armed but silent" — useful staging area).
        active   → bring up wlan-ap → render+launch dnsmasq →
                   render+launch hostapd → start captive sentinel +
                   client-recon log tailer → push the rogue subnet
                   into the management-access deny-list.
        advanced → everything in active, plus pause recon on the
                   karma radio + start the Scapy karma injector.

        Idempotent: re-calling start while already running returns
        a "no-op" success.
        """
        msgs: list[str] = []
        with self._lock:
            self._load()
            mode = self._state["mode"]
            if mode == PineAPMode.OFF.value:
                return False, ["mode is 'off' — pick a mode before starting"]
            if self._state["running"]:
                return True, [f"already running in {mode} mode"]

            # Take a snapshot of the config fields under the lock; the
            # rest of start() runs outside it so the long blocking
            # operations (subprocess waits, network setup) don't hold
            # the lock and block other request handlers.
            snap = dict(self._state)
            pool_snap = list(self._pool or [])

        if mode == PineAPMode.PASSIVE.value:
            with self._lock:
                self._state["running"]      = True
                self._state["last_changed"] = time.time()
                self._save_state()
            msgs.append("started in passive mode (configured, hostapd silent)")
            log.info("pineap: %s", msgs[-1])
            return True, msgs

        # Active or Advanced path
        ok, sub_msgs = self._start_broadcast(snap, pool_snap,
                                             advanced=(mode == PineAPMode.ADVANCED.value))
        msgs.extend(sub_msgs)
        if not ok:
            # Try to undo whatever did come up so we don't leak state
            log.warning("pineap: start failed mid-way, attempting rollback")
            self._tear_down_broadcast()
            return False, msgs

        with self._lock:
            self._state["running"]      = True
            self._state["last_changed"] = time.time()
            self._save_state()
        return True, msgs

    def stop(self) -> tuple[bool, list[str]]:
        msgs: list[str] = []
        with self._lock:
            self._load()
            if not self._state.get("running"):
                return True, ["already stopped"]
            mode = self._state["mode"]
            running_broadcast = mode in (PineAPMode.ACTIVE.value,
                                         PineAPMode.ADVANCED.value)

        if running_broadcast:
            msgs.extend(self._tear_down_broadcast())

        with self._lock:
            self._state["running"]            = False
            self._state["hostapd_job_id"]     = None
            self._state["dnsmasq_job_id"]     = None
            self._state["karma_running"]      = False
            self._state["evil_wpa_running"]   = False
            self._state["sentinel_running"]   = False
            self._state["last_changed"]       = time.time()
            self._save_state()
        msgs.append("stopped")
        log.info("pineap: stopped (mode was %s)",
                 self._state.get("mode", "?"))
        return True, msgs

    # ---------- Internal lifecycle steps ----------
    def _start_broadcast(
        self, snap: dict[str, Any], pool: list[dict[str, Any]],
        *, advanced: bool,
    ) -> tuple[bool, list[str]]:
        """Bring up the AP stack: interface → dnsmasq → hostapd → sentinel
        → (karma if advanced). Each step that succeeds records its state
        so _tear_down_broadcast can undo it. Returns (ok, messages)."""
        from app.tools import hostapd as hostapd_tool
        from app.tools import dnsmasq as dnsmasq_tool
        from app.tools import iproute, rfkill, nm
        from app.tools._common import stub_mode
        from app.services.job_manager import job_manager
        from app.services.access_control import access_control

        msgs: list[str] = []
        iface       = snap["iface"]
        primary_ssid = snap["primary_ssid"]
        channel     = int(snap["channel"])
        hw_mode     = snap["hw_mode"]
        gateway     = snap["gateway_ip"]
        subnet      = snap["subnet"]
        dhcp_start, dhcp_end = snap["dhcp_range"]
        salt        = snap["bssid_salt"]

        # ---- 1. Interface bring-up ----
        rfkill.unblock_wifi()
        ok, msg = nm.set_managed(iface, False)
        msgs.append(f"nm.unmanage({iface}): {msg}")
        if not ok and not stub_mode():
            # nmcli failures aren't fatal — the iface may already be
            # unmanaged or NM may not be running. Continue.
            log.warning("pineap: nm.set_managed soft-failure: %s", msg)
        # Ensure the interface is in 'managed' type before hostapd takes
        # over. The mt76 driver fails to switch monitor → AP directly
        # ("could not configure driver mode"); the reliable sequence is
        # monitor → managed (us) → AP (hostapd). The kernel refuses
        # `iw set type` while the interface is administratively up
        # ("Device or resource busy") — so we must bring it down first,
        # change type, then bring it back up. If it was already managed
        # and down, this is essentially a no-op.
        iproute.set_link_state(iface, "down")
        try:
            from app.tools import iw
            ok, msg = iw.set_type(iface, "managed")
            msgs.append(f"iw set type managed {iface}: {msg}")
        except Exception as e:
            log.warning("pineap: iw.set_type soft-failure on %s: %s", iface, e)
        # Flush in case a stale address from a previous run lingers
        iproute.flush_address(iface)
        ok, msg = iproute.add_address(iface, f"{gateway}/24")
        msgs.append(f"ip addr add {gateway}/24 dev {iface}: {msg}")
        if not ok and not stub_mode():
            return False, msgs
        ok, msg = iproute.set_link_state(iface, "up")
        msgs.append(f"ip link up {iface}: {msg}")
        if not ok and not stub_mode():
            return False, msgs

        # ---- 2. dnsmasq config + launch ----
        # Include log-dhcp + log-queries so client_recon's tailer can
        # parse the verbose stream. log-facility points to our own log
        # file (not syslog) so we can read it without root syslog perms.
        dnsmasq_body = dnsmasq_tool.render_config(
            iface=iface,
            gateway_ip=gateway,
            dhcp_range_start=dhcp_start,
            dhcp_range_end=dhcp_end,
            dhcp_lease="12h",
            forward_dns=True,
            log_queries=True,
            # The management AP's dnsmasq holds 127.0.0.1:53 + 0.0.0.0:67
            # with bind-interfaces. Coexist via bind-dynamic + SO_BINDTODEVICE
            # so PineAP's dnsmasq only handles wlan-ap traffic and doesn't
            # fight the mgmt-ap dnsmasq for loopback DNS.
            coexist_with_other_dnsmasq=True,
        )
        # Append fields render_config doesn't directly support:
        #   - log-facility: where to send the verbose log (our tailer
        #     reads this)
        #   - dhcp-leasefile: stable path for the sentinel to resolve
        #     IP→MAC against
        #   - pid-file: needed for clean teardown if we ever lose the
        #     JobManager reference
        dnsmasq_body += (
            f"log-facility={DNSMASQ_LOG_PATH}\n"
            f"dhcp-leasefile={DNSMASQ_LEASES_PATH}\n"
            f"pid-file={DNSMASQ_PID_PATH}\n"
        )
        dnsmasq_tool.write_config(DNSMASQ_CONFIG_PATH, dnsmasq_body)
        # Remove any stale log file from a previous run. We used to
        # truncate via Python's write_text(""), but that leaves the
        # file owned by root — and dnsmasq drops privileges to nobody
        # right after startup, then can't open the root-owned file for
        # writing ("Permission denied" → dnsmasq aborts). Unlink lets
        # dnsmasq create a fresh file as its own user every Start. The
        # tailer waits for the file to (re)appear (see client_recon
        # _tail_loop file-not-exists branch) and seeks to start to
        # catch every line.
        try:
            DNSMASQ_LOG_PATH.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("pineap: could not unlink stale dnsmasq log: %s", e)

        dnsmasq_cmd = [
            "dnsmasq", "--keep-in-foreground",
            "-C", str(DNSMASQ_CONFIG_PATH),
        ]
        try:
            dns_job = job_manager.start_job(
                dnsmasq_cmd, name="pineap-dnsmasq", tags=["pineap"],
            )
            with self._lock:
                self._state["dnsmasq_job_id"] = dns_job.id
            msgs.append(f"dnsmasq started (job {dns_job.id})")
        except Exception as e:
            log.exception("pineap: dnsmasq launch failed")
            return False, msgs + [f"dnsmasq launch failed: {e}"]

        # ---- 3. Render + launch hostapd ----
        primary_bssid = hostapd_tool.bssid_for_ssid(primary_ssid, salt)
        # Build extras from pool: pinned first, exclude hidden, exclude
        # primary, cap at MAX_BSS - 1 (chip limit). Pool entries with
        # the same SSID as primary would just collide; skip them.
        extras: list[dict[str, Any]] = []
        # Cap at MAX_BSS - 1 because the primary BSS already counts as
        # one. Check BEFORE the append (not after) — checking after
        # would still allow 1 extra when MAX_BSS=1, because the
        # ``>= MAX_BSS - 1`` test is satisfied after the first append.
        # This bit us on the mt76x2u cap=1 path: hostapd was getting a
        # 2-BSS config even with DEFAULT_MAX_BSS=1.
        for e in sorted(pool, key=lambda x: (not x.get("pinned"),
                                             -(x.get("last_seen") or 0))):
            if len(extras) >= hostapd_tool.DEFAULT_MAX_BSS - 1:
                break
            ssid = e.get("ssid")
            if not ssid or e.get("hidden") or ssid == primary_ssid:
                continue
            extras.append({
                "ssid":   ssid,
                "bssid":  hostapd_tool.bssid_for_ssid(ssid, salt),
                "hidden": False,
            })

        # Security mode determines whether we render an open AP or a
        # WPA2-PSK AP. For wpa2, generate a fresh random PSK every
        # Start — the value doesn't matter functionally (clients fail
        # at M3 with our random PSK, but by then we've collected M2's
        # MIC which is the partial we want), and randomizing it
        # guarantees we never accidentally accept a real client
        # connection. Persisted to state so the operator can see what
        # was generated; surfaced in the notification drawer too.
        security_mode = snap.get("security_mode", "open")
        rogue_psk: str | None = None
        if security_mode == "wpa2":
            # 16 url-safe bytes → ~22 ASCII chars (alphanumeric + - _).
            # All valid WPA passphrase chars (spec: 8-63 ASCII).
            rogue_psk = secrets.token_urlsafe(16)
            with self._lock:
                self._state["last_rogue_psk"] = rogue_psk
                self._save_state()

        hostapd_body = hostapd_tool.render_config(
            iface=iface,
            ssid=primary_ssid,
            password=rogue_psk,             # None → open AP; str → wpa=2
            channel=channel,
            hw_mode=hw_mode,
            primary_bssid=primary_bssid,
            hidden=bool(snap.get("primary_hidden")),
            extra_bsses=extras,
        )
        hostapd_tool.write_config(HOSTAPD_CONFIG_PATH, hostapd_body)
        hostapd_cmd = ["hostapd", str(HOSTAPD_CONFIG_PATH)]
        try:
            hap_job = job_manager.start_job(
                hostapd_cmd, name="pineap-hostapd", tags=["pineap"],
            )
            with self._lock:
                self._state["hostapd_job_id"] = hap_job.id
            sec_label = "WPA2-PSK" if security_mode == "wpa2" else "open"
            psk_label = f" (rogue PSK: {rogue_psk})" if rogue_psk else ""
            msgs.append(
                f"hostapd started [{sec_label}]: SSID {primary_ssid!r} + "
                f"{len(extras)} extras on ch{channel} (job {hap_job.id})"
                f"{psk_label}"
            )
        except Exception as e:
            log.exception("pineap: hostapd launch failed")
            return False, msgs + [f"hostapd launch failed: {e}"]

        # ---- 4. Captive sentinel listener ----
        try:
            from app.services.captive_sentinel import get_service as get_sentinel
            sentinel = get_sentinel()
            ok, msg = sentinel.start(
                bind_host=gateway, bind_port=80, stub=stub_mode(),
            )
            with self._lock:
                self._state["sentinel_running"] = ok
            msgs.append(f"captive sentinel: {msg}")
        except Exception as e:
            log.exception("pineap: sentinel start failed")
            msgs.append(f"sentinel start failed: {e}")
            # Sentinel is enrichment-only — not fatal

        # ---- 5. client_recon log tailer + lease-file poller ----
        # The lease poller is the *primary* source of truth for
        # connected clients; the tailer is enrichment (OS fingerprint
        # via DHCP option 55, DNS query history). Running both means
        # clients show up even if log parsing falters on a format
        # we haven't accounted for.
        try:
            from app.services.client_recon import get_service as get_recon
            client_recon = get_recon()
            ok, msg = client_recon.start_tailer(DNSMASQ_LOG_PATH)
            msgs.append(f"client-recon tailer: {msg}")
            ok, msg = client_recon.start_lease_poller(DNSMASQ_LEASES_PATH)
            msgs.append(f"client-recon lease poller: {msg}")
        except Exception as e:
            log.exception("pineap: client_recon start failed")
            msgs.append(f"client-recon start failed: {e}")

        # ---- 6. Auto-add subnet to deny-list ----
        try:
            ok, msg = access_control.add_cidr(subnet)
            msgs.append(f"deny-list +{subnet}: {msg}")
        except Exception as e:
            log.exception("pineap: deny-list add failed")
            msgs.append(f"deny-list add failed: {e} — add {subnet} manually!")

        # ---- 6.5. NAT + IP forwarding so the rogue subnet gets internet ----
        # Without this, victim clients associate + DHCP + get DNS, but every
        # outbound TCP/UDP packet dies at the Pi's gateway because there's no
        # route translation. Captive-portal probes return "No Internet" and
        # the OS keeps cellular as the primary route (which limits how much
        # the rogue actually sees). The same pattern S04.9 used for the mgmt
        # AP — different subnet, same wrapper.
        try:
            from app.tools import iptables
            ok, msg = iptables.enable_ip_forward()
            msgs.append(f"ip_forward: {msg}")
            ok, msg = iptables.ensure_nat_masquerade(subnet)
            msgs.append(f"nat MASQUERADE +{subnet}: {msg}")
            ok, msg = iptables.ensure_forward_rules(subnet)
            msgs.append(f"FORWARD +{subnet}: {msg}")
        except Exception as e:
            log.exception("pineap: NAT setup failed")
            msgs.append(f"NAT setup failed: {e} — phone may show 'No Internet'")

        # ---- 7. Karma OR Evil WPA sniffer (mutually exclusive — both
        #         want wlan-mon-5g). Decision rules:
        #
        #   security_mode=open + mode=advanced  → Karma (probe-response
        #                                          injector for pool SSIDs)
        #   security_mode=wpa2 + mode in (active, advanced) → Evil WPA
        #                                          EAPOL sniffer
        #   anything else → neither
        #
        # Karma + Evil WPA can't run simultaneously on this hardware
        # because both pin wlan-mon-5g to hostapd's channel. If the
        # operator wants both behaviours they'd need a fourth radio,
        # out of scope.
        mon_iface = snap.get("karma_iface", DEFAULT_KARMA_IFACE)
        want_karma = (advanced and security_mode == "open")
        want_evil_wpa = (security_mode == "wpa2")

        if want_karma or want_evil_wpa:
            # Pause recon to free the monitor radio (same operational
            # cost for either sub-service)
            try:
                from app.services.recon import get_service as get_recon_svc
                rs = get_recon_svc()
                if hasattr(rs, "stop_scan"):
                    rs.stop_scan()
                msgs.append(f"recon paused ({mon_iface} claimed)")
            except Exception:
                log.exception("pineap: recon-pause failed")
                msgs.append("recon-pause failed (continuing)")

        if want_karma:
            try:
                from app.services.karma import get_service as get_karma
                karma = get_karma()
                ok, msg = karma.start(
                    iface=mon_iface,
                    channel=channel,
                    primary_bssid=primary_bssid,
                )
                with self._lock:
                    self._state["karma_running"] = ok
                msgs.append(f"karma: {msg}")
            except Exception as e:
                log.exception("pineap: karma start failed")
                msgs.append(f"karma start failed: {e}")

        if want_evil_wpa:
            # Optional evil-twin deauth coupling: only when the operator
            # opted in AND we have a real target BSSID (i.e. cloned from
            # Recon — a from-scratch WPA SSID has no AP to deauth). The
            # deauth fires on the spare radio (wlan-mon-2g), freed because
            # recon was paused above. MFP-required targets reject the
            # frames — we still attempt (warned in the UI), so it's a
            # no-op rather than an error there.
            target = snap.get("evil_wpa_target") or {}
            real_bssid = target.get("source_bssid")
            deauth_on = bool(snap.get("evil_wpa_deauth")) and bool(real_bssid)
            deauth_iface = snap.get("deauth_iface", DEFAULT_DEAUTH_IFACE)
            if snap.get("evil_wpa_deauth") and not real_bssid:
                msgs.append("evil_wpa deauth requested but no real target "
                            "BSSID (clone from Recon first) — skipping deauth")
            try:
                from app.services.evil_wpa import get_service as get_evil_wpa
                ew = get_evil_wpa()
                ok, msg = ew.start(
                    iface=mon_iface,
                    channel=channel,
                    ap_bssid=primary_bssid,
                    ssid=primary_ssid,
                    deauth_enabled=deauth_on,
                    deauth_iface=deauth_iface if deauth_on else None,
                    deauth_bssid=real_bssid if deauth_on else None,
                )
                with self._lock:
                    self._state["evil_wpa_running"] = ok
                msgs.append(f"evil_wpa: {msg}")
                if deauth_on:
                    mfp = target.get("source_mfp_required")
                    mfp_note = (" (target advertises MFP-required — deauth "
                                "frames will be rejected)") if mfp else ""
                    msgs.append(f"evil-twin deauth armed at {real_bssid} "
                                f"on {deauth_iface}{mfp_note}")
            except Exception as e:
                log.exception("pineap: evil_wpa start failed")
                msgs.append(f"evil_wpa start failed: {e}")

        return True, msgs

    def _tear_down_broadcast(self) -> list[str]:
        """Reverse of _start_broadcast. Best-effort — each step
        independently logs but doesn't abort the others if it fails."""
        from app.services.access_control import access_control
        from app.services.job_manager import job_manager
        from app.tools import iproute

        msgs: list[str] = []
        with self._lock:
            self._load()
            snap = dict(self._state)

        # 1. Karma OR Evil WPA (mutually exclusive — both pin wlan-mon-5g).
        #    Evil WPA's stop() also tears down the coupled deauth thread
        #    on the spare radio.
        if snap.get("karma_running"):
            try:
                from app.services.karma import get_service as get_karma
                ok, msg = get_karma().stop()
                msgs.append(f"karma stop: {msg}")
            except Exception as e:
                msgs.append(f"karma stop failed: {e}")
        if snap.get("evil_wpa_running"):
            try:
                from app.services.evil_wpa import get_service as get_evil_wpa
                ok, msg = get_evil_wpa().stop()
                msgs.append(f"evil_wpa stop: {msg}")
            except Exception as e:
                msgs.append(f"evil_wpa stop failed: {e}")
        # Restore recon if either sub-service had paused it
        if snap.get("karma_running") or snap.get("evil_wpa_running"):
            try:
                from app.services.recon import get_service as get_recon_svc
                rs = get_recon_svc()
                if hasattr(rs, "start_scan"):
                    rs.start_scan()
                msgs.append("recon restored")
            except Exception:
                log.exception("pineap: recon-restore failed")
                msgs.append("recon-restore failed (manual restart may be needed)")

        # 2. Captive sentinel + client_recon tailer
        try:
            from app.services.captive_sentinel import get_service as get_sentinel
            ok, msg = get_sentinel().stop()
            msgs.append(f"sentinel stop: {msg}")
        except Exception as e:
            msgs.append(f"sentinel stop failed: {e}")
        try:
            from app.services.client_recon import get_service as get_client_recon
            cr = get_client_recon()
            cr.stop_tailer()
            cr.stop_lease_poller()
            msgs.append("client-recon tailer + lease poller stopped")
        except Exception as e:
            msgs.append(f"client-recon stop failed: {e}")

        # 3. hostapd + dnsmasq via JobManager
        for key, label in (("hostapd_job_id", "hostapd"),
                           ("dnsmasq_job_id", "dnsmasq")):
            jid = snap.get(key)
            if jid:
                try:
                    ok, reason = job_manager.stop_job(jid, grace=3.0,
                                                     first_signal=signal.SIGTERM)
                    msgs.append(f"{label} stop ({jid[:8]}): {reason}")
                except Exception as e:
                    msgs.append(f"{label} stop failed: {e}")

        # 4. Remove subnet from deny-list + tear down NAT/FORWARD rules
        try:
            ok, msg = access_control.remove_cidr(snap.get("subnet", DEFAULT_SUBNET))
            msgs.append(f"deny-list -{snap.get('subnet')}: {msg}")
        except Exception as e:
            msgs.append(f"deny-list remove failed: {e}")
        try:
            from app.tools import iptables
            ok, msg = iptables.remove_nat_and_forward(
                snap.get("subnet", DEFAULT_SUBNET))
            msgs.append(f"NAT teardown: {msg}")
        except Exception as e:
            msgs.append(f"NAT teardown failed: {e}")

        # 5. Tear down the AP interface
        iface = snap.get("iface", DEFAULT_IFACE)
        try:
            iproute.set_link_state(iface, "down")
            iproute.flush_address(iface)
            msgs.append(f"{iface} brought down + flushed")
        except Exception as e:
            msgs.append(f"{iface} teardown failed: {e}")

        return msgs


# ---------- Module singleton ----------

_service: "PineAPService | None" = None


def get_service() -> PineAPService:
    global _service
    if _service is None:
        from flask import current_app
        _service = PineAPService(current_app.config["DATA_DIR"])
    return _service


# ---------- Auto-population hooks (called by recon service) ----------

def auto_add_from_recon(ssids: list[str]) -> None:
    """Fire-and-forget add of SSIDs observed in a recon scan. Quiet on
    failure — auto-collection should never break the calling code path."""
    if not ssids:
        return
    try:
        svc = get_service()
    except Exception:
        # current_app missing (no app context) — happens in tests and
        # during early startup before the recon poller is wired
        return
    for s in ssids:
        if not s:
            continue
        try:
            svc.add_ssid(s, source=SOURCE_RECON)
        except Exception:
            log.exception("pineap.auto_add_from_recon failed for %r", s)


def auto_add_from_probes(ssids: list[str]) -> None:
    """Fire-and-forget add of SSIDs from directed probe requests."""
    if not ssids:
        return
    try:
        svc = get_service()
    except Exception:
        return
    for s in ssids:
        if not s:
            continue
        try:
            svc.add_ssid(s, source=SOURCE_PROBE)
        except Exception:
            log.exception("pineap.auto_add_from_probes failed for %r", s)
