"""Client reconnaissance — parse dnsmasq logs for fingerprint + DNS.

When PineAP runs in open-AP mode (S11), dnsmasq is launched with
``log-dhcp`` and ``log-queries`` so we get a structured stream of:

* DHCP DISCOVER / REQUEST lines with the client's MAC + DHCP option
  list (option 55 = the "parameter request list" — the ordered list
  of options the client wants in the offer, which is a remarkably
  consistent OS fingerprint) and vendor class (option 60).
* DNS query lines: ``query[A] hostname.example from 10.0.0.42``.

This service tails the log file in a daemon thread, parses each line,
and maintains a per-client enriched record:

    {
      "mac":          "aa:bb:cc:dd:ee:ff",
      "ip":           "10.0.0.42",
      "hostname":     "Joes-iPhone",
      "first_seen":   1717800000,
      "last_seen":    1717800500,
      "dhcp_option55_fingerprint": "1,3,6,15,119,252",
      "dhcp_vendor_class":         "MSFT 5.0",
      "os_guess":     "iOS",
      "recent_queries": [
        {"ts": ..., "name": "init.itunes.apple.com", "type": "A"},
        ...
      ]
    }

The UI's Clients view reads ``list_clients()`` and ``get_client(mac)``
for the per-row detail expand. SocketIO events fire on new-client
appearance and on each new DNS query so the view updates live.

Stub mode lets us test the parser on a synthetic log stream without
needing dnsmasq actually running.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------- DHCP option-55 → OS fingerprint table ----------
#
# Source of truth: empirical fingerprints collected from real devices +
# the fingerbank.org dataset (open licence). We keep this small and
# tunable rather than vendoring the full 50k-entry dataset; PineAP only
# needs to distinguish iOS/Android/macOS/Windows/Linux/IoT in practice.
# Match on exact prefix of the option-55 list; longest match wins.

_FINGERPRINTS: list[tuple[str, str]] = [
    # iOS — distinctive: 1,3,6,15,119,252
    ("1,3,6,15,119,252",                "iOS"),
    ("1,3,6,15,119,252,95,44,46",       "iOS"),
    # macOS — similar but with more options + leading 252
    ("1,3,6,15,119,95,252,44,46,47",    "macOS"),
    ("1,121,3,6,15,119,252,95,44,46",   "macOS"),
    # Android — adds 26 (interface MTU)
    ("1,3,6,15,26,28,51,58,59,43",      "Android"),
    ("1,33,3,6,15,26,28,51,58,59,43",   "Android"),
    # Windows 10/11 — long list including 252 + 43 + 81
    ("1,15,3,6,44,46,47,31,33,121,249,43,252", "Windows"),
    ("1,3,6,15,31,33,43,44,46,47,121,249,252", "Windows"),
    # Generic Linux dhclient
    ("1,28,2,3,15,6,119,12,44,47,26,121,42",   "Linux"),
    # Common IoT — small lists
    ("1,3,6,15",                         "IoT / embedded"),
    ("1,3,6,42",                         "IoT / embedded"),
]


def fingerprint_os(opt55: str, vendor_class: str | None = None) -> str | None:
    """Best-effort OS guess. Returns None if no match."""
    if not opt55:
        return None
    # Longest prefix wins
    matches = [(fp, os_) for fp, os_ in _FINGERPRINTS
               if opt55.startswith(fp) or opt55 == fp]
    if matches:
        return max(matches, key=lambda x: len(x[0]))[1]
    # Fallback: vendor class hints
    if vendor_class:
        vc = vendor_class.lower()
        if "msft" in vc:    return "Windows"
        if "android" in vc: return "Android"
        if "dhcp" in vc and "ios" in vc: return "iOS"
    return None


# ---------- Log line patterns ----------
# dnsmasq logs look like (with timestamp prefix depending on syslog setup):
#   dhcp-request(wlan-ap) 10.0.0.42 aa:bb:cc:dd:ee:ff Joes-iPhone
#   client provides name: Joes-iPhone
#   requested options: 1:netmask, 3:router, 6:dns-server, 15:domain-name, ...
#   vendor class: MSFT 5.0
#   query[A] init.itunes.apple.com from 10.0.0.42

_RE_DHCP_REQ = re.compile(
    r"dhcp-(?:request|discover|inform|ack)\([^)]*\)\s+"
    r"(?P<ip>\d+\.\d+\.\d+\.\d+)\s+"
    r"(?P<mac>[0-9a-fA-F:]{17})"
    r"(?:\s+(?P<hostname>\S+))?"
)
_RE_REQUESTED = re.compile(r"requested options:\s+(?P<opts>[\d:a-zA-Z, \-]+)")
_RE_VENDOR    = re.compile(r"vendor class:\s+(?P<vc>.+)")
_RE_NAME      = re.compile(r"client provides name:\s+(?P<name>\S+)")
_RE_QUERY     = re.compile(
    r"query\[(?P<qtype>[A-Z]+)\]\s+(?P<name>\S+)\s+from\s+"
    r"(?P<src>\d+\.\d+\.\d+\.\d+)"
)


def _extract_opt55(reqline: str) -> str | None:
    """Pull the numeric option codes out of 'requested options:' text.

    dnsmasq formats as ``1:netmask, 3:router, 6:dns-server, ...``;
    we want ``"1,3,6,..."`` for fingerprint matching."""
    nums = re.findall(r"\b(\d+):", reqline)
    return ",".join(nums) if nums else None


# ---------- Service ----------

class ClientReconService:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._store_path = data_dir / "pineap_clients.json"
        # In-memory store. Persisted to disk on every meaningful change.
        self._clients: dict[str, dict[str, Any]] = self._load_persisted()
        # ip → mac so DNS queries (which only carry IP) can be attributed
        # to the right client record.
        self._ip_to_mac: dict[str, str] = {
            c["ip"]: mac for mac, c in self._clients.items() if c.get("ip")
        }
        self._lock = threading.Lock()
        # Tailer thread state
        self._tail_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._tail_path: Path | None = None
        # In-flight DHCP state for the multi-line records dnsmasq emits.
        # Cleared after each dhcp-ack so different clients don't bleed
        # into each other.
        self._pending_dhcp: dict[str, Any] = {}

    # ---------- Persistence ----------
    def _load_persisted(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._store_path.read_text())
            return dict(data.get("clients") or {})
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _persist(self) -> None:
        # Caller holds self._lock.
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._store_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"clients": self._clients}, indent=2))
        tmp.replace(self._store_path)

    # ---------- Public ----------
    def list_clients(self) -> list[dict[str, Any]]:
        """All known clients, most-recently-seen first. Returns
        deep-ish copies so callers can't mutate internal state."""
        with self._lock:
            entries = []
            for c in self._clients.values():
                d = dict(c)
                # Bound the queries list for the list view; full
                # history available via get_client.
                rq = list(c.get("recent_queries") or [])
                d["recent_queries"] = rq[-20:]
                d["query_count"] = len(rq)
                entries.append(d)
        entries.sort(key=lambda c: -(c.get("last_seen") or 0))
        return entries

    def get_client(self, mac: str) -> dict[str, Any] | None:
        with self._lock:
            c = self._clients.get(mac.lower())
            if c is None:
                return None
            d = dict(c)
            d["recent_queries"] = list(c.get("recent_queries") or [])
            return d

    def upsert_dhcp(
        self, mac: str, ip: str, hostname: str | None = None,
        opt55: str | None = None, vendor_class: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a client record from DHCP info. Returns
        the (deep-copied) record post-update."""
        mac = mac.lower()
        now = time.time()
        with self._lock:
            existing = self._clients.get(mac, {})
            new_ip = ip or existing.get("ip")
            # Old IP → new IP: remove the stale reverse lookup
            old_ip = existing.get("ip")
            if old_ip and old_ip != new_ip:
                self._ip_to_mac.pop(old_ip, None)
            record = {
                "mac":          mac,
                "ip":           new_ip,
                "hostname":     hostname or existing.get("hostname"),
                "first_seen":   existing.get("first_seen") or now,
                "last_seen":    now,
                "dhcp_option55_fingerprint": opt55 or existing.get("dhcp_option55_fingerprint"),
                "dhcp_vendor_class":         vendor_class or existing.get("dhcp_vendor_class"),
                "recent_queries":            existing.get("recent_queries") or [],
            }
            record["os_guess"] = fingerprint_os(
                record.get("dhcp_option55_fingerprint") or "",
                record.get("dhcp_vendor_class"),
            )
            self._clients[mac] = record
            if new_ip:
                self._ip_to_mac[new_ip] = mac
            self._persist()
            self._emit("client:upsert", record)
            return dict(record)

    def record_query(self, src_ip: str, qtype: str, name: str) -> None:
        """Attribute a DNS query to whichever client currently holds
        ``src_ip``. Drops the query if we don't know the IP (haven't
        seen the DHCP exchange yet)."""
        mac = self._ip_to_mac.get(src_ip)
        if not mac:
            return
        now = time.time()
        with self._lock:
            c = self._clients.get(mac)
            if c is None:
                return
            queries = list(c.get("recent_queries") or [])
            queries.append({"ts": now, "type": qtype, "name": name})
            # Cap per-client history at 200 to keep memory bounded
            if len(queries) > 200:
                queries = queries[-200:]
            c["recent_queries"] = queries
            c["last_seen"] = now
            self._persist()
            self._emit("client:query", {
                "mac": mac, "src_ip": src_ip,
                "type": qtype, "name": name, "ts": now,
            })

    def clear(self) -> tuple[bool, str, int]:
        with self._lock:
            n = len(self._clients)
            self._clients = {}
            self._ip_to_mac = {}
            self._persist()
        return True, f"cleared {n} clients", n

    # ---------- Log tailer ----------
    def start_tailer(self, log_path: Path) -> tuple[bool, str]:
        """Tail dnsmasq's log file in a daemon thread. The file may
        not exist yet at start time; the tailer waits patiently."""
        with self._lock:
            if self._tail_thread is not None and self._tail_thread.is_alive():
                return True, f"already tailing {self._tail_path}"
            self._tail_path = log_path
            self._stop_event.clear()

        # Push app_context so emit() works from the thread (same pattern
        # as the recon poller and crack parser).
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
                self._tail_loop(log_path)
            except Exception:
                log.exception("client_recon tailer crashed")
            finally:
                if ctx:
                    ctx.pop()

        t = threading.Thread(target=_run, name="client-recon-tailer",
                             daemon=True)
        with self._lock:
            self._tail_thread = t
        t.start()
        return True, f"tailing {log_path}"

    def stop_tailer(self) -> None:
        self._stop_event.set()

    def _tail_loop(self, log_path: Path) -> None:
        """Follow log_path à la tail -F: handle file-not-yet-present,
        truncation, and rotation."""
        f = None
        inode = None
        while not self._stop_event.is_set():
            try:
                if f is None:
                    if not log_path.exists():
                        self._stop_event.wait(1.0)
                        continue
                    f = log_path.open("r", errors="replace")
                    # Start from end of file so we don't re-process old
                    # lines from a previous PineAP run.
                    f.seek(0, 2)
                    inode = log_path.stat().st_ino
                line = f.readline()
                if not line:
                    # Detect rotation
                    try:
                        cur_inode = log_path.stat().st_ino
                    except FileNotFoundError:
                        cur_inode = None
                    if cur_inode != inode:
                        log.info("dnsmasq log rotated, reopening %s", log_path)
                        try:
                            f.close()
                        except Exception:
                            pass
                        f = None
                        continue
                    self._stop_event.wait(0.5)
                    continue
                self._handle_line(line.rstrip("\n"))
            except Exception:
                log.exception("client_recon tail iter")
                self._stop_event.wait(1.0)
        try:
            if f:
                f.close()
        except Exception:
            pass
        log.info("client_recon tailer exiting")

    def _handle_line(self, line: str) -> None:
        """Single log-line dispatch. dnsmasq emits DHCP info across
        several lines per exchange; we accumulate in self._pending_dhcp
        keyed by MAC and flush on the matching ack."""
        # 1. DNS query — single-line, attribute and move on
        m = _RE_QUERY.search(line)
        if m:
            self.record_query(m.group("src"), m.group("qtype"), m.group("name"))
            return

        # 2. DHCP request/discover/ack — record MAC+IP+hostname
        m = _RE_DHCP_REQ.search(line)
        if m:
            mac = m.group("mac").lower()
            self._pending_dhcp.setdefault(mac, {})
            self._pending_dhcp[mac].update({
                "ip":       m.group("ip"),
                "hostname": m.group("hostname"),
            })
            # On dhcp-ack lines: flush
            if "dhcp-ack" in line:
                p = self._pending_dhcp.pop(mac, {})
                self.upsert_dhcp(
                    mac=mac,
                    ip=p.get("ip") or m.group("ip"),
                    hostname=p.get("hostname"),
                    opt55=p.get("opt55"),
                    vendor_class=p.get("vendor_class"),
                )
            return

        # 3. "client provides name: ..." — pending hostname
        m = _RE_NAME.search(line)
        if m:
            # Hostname lines don't carry the MAC directly; they belong
            # to whichever DHCP exchange is currently in flight. dnsmasq
            # processes one client at a time so the most-recent pending
            # MAC wins.
            if self._pending_dhcp:
                last_mac = next(reversed(self._pending_dhcp))
                self._pending_dhcp[last_mac]["hostname"] = m.group("name")
            return

        # 4. "requested options: 1:netmask, 3:router, ..."
        m = _RE_REQUESTED.search(line)
        if m:
            opt55 = _extract_opt55(m.group("opts"))
            if opt55 and self._pending_dhcp:
                last_mac = next(reversed(self._pending_dhcp))
                self._pending_dhcp[last_mac]["opt55"] = opt55
            return

        # 5. "vendor class: MSFT 5.0"
        m = _RE_VENDOR.search(line)
        if m and self._pending_dhcp:
            last_mac = next(reversed(self._pending_dhcp))
            self._pending_dhcp[last_mac]["vendor_class"] = m.group("vc").strip()
            return

    # ---------- SocketIO emit helper ----------
    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        try:
            from app import socketio
            socketio.emit(event, payload, namespace="/")
        except Exception:
            # No app context (tests) — silent skip
            pass


# ---------- Module singleton ----------

_service: "ClientReconService | None" = None


def get_service() -> ClientReconService:
    global _service
    if _service is None:
        from flask import current_app
        _service = ClientReconService(current_app.config["DATA_DIR"])
    return _service
