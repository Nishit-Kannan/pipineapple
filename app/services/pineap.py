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
import re
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


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
                self._state = json.loads(self._state_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                self._state = self._default_state()
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
            "iface":             "wlan-ap",
            "last_changed":      time.time(),
            "running":           False,    # True only while hostapd is up (S11)
            "job_id":            None,     # JobManager job id (S11)
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

    # ---------- Public: hostapd lifecycle (stubbed in S10) ----------
    def start(self) -> tuple[bool, list[str]]:
        """Bring up PineAP per the current mode.

        S10 limit: only ``passive`` and ``off`` work — S10 doesn't
        actually launch hostapd. ``active`` / ``advanced`` return a
        clear "wait for S11" message instead of pretending to start.
        Refactoring this in S11 is straightforward: replace the
        passive no-op with hostapd config render + JobManager.start_job.
        """
        msgs: list[str] = []
        with self._lock:
            self._load()
            mode = self._state["mode"]
            if mode == PineAPMode.OFF.value:
                return False, ["mode is 'off' — pick a mode before starting"]
            if mode in (PineAPMode.ACTIVE.value, PineAPMode.ADVANCED.value):
                return False, [
                    f"mode '{mode}' requires hostapd broadcast — "
                    "not yet wired (lands in S11). Use 'passive' for now."
                ]
            # Passive: nothing to launch, just flip the running flag
            # so the UI status reflects "armed but silent".
            self._state["running"]      = True
            self._state["last_changed"] = time.time()
            self._save_state()
        msgs.append("started in passive mode (no broadcast — S10 limit)")
        log.info("pineap: %s", msgs[-1])
        return True, msgs

    def stop(self) -> tuple[bool, list[str]]:
        msgs: list[str] = []
        with self._lock:
            self._load()
            if not self._state.get("running"):
                return True, ["already stopped"]
            # S11 will SIGTERM the hostapd job here.
            self._state["running"]      = False
            self._state["job_id"]       = None
            self._state["last_changed"] = time.time()
            self._save_state()
        msgs.append("stopped")
        log.info("pineap: stopped")
        return True, msgs


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
