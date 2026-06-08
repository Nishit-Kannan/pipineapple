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
    # iOS — distinctive shapes (older + newer)
    ("1,3,6,15,119,252",                          "iOS"),
    ("1,3,6,15,119,252,95,44,46",                 "iOS"),
    # iOS 17+ on dnsmasq logs: 1,121,3,6,15,108,114,119,162,252
    # (option 121 = classless-static-route, 108 = ipv6-only-pref,
    # 114 = captive-portal, 162 = dnr, 252 = wpad)
    ("1,121,3,6,15,108,114,119,162,252",          "iOS"),
    # macOS — similar but with more options + leading 252
    ("1,3,6,15,119,95,252,44,46,47",              "macOS"),
    ("1,121,3,6,15,119,252,95,44,46",             "macOS"),
    # macOS 14+ shapes
    ("1,121,3,6,15,108,114,119,252,95,44,46",     "macOS"),
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
#
# dnsmasq's verbose log shape (with log-dhcp + log-queries) on Pi OS
# Trixie / dnsmasq 2.90+:
#
#   Jun  8 18:09:03 dnsmasq-dhcp[287949]: 905408650 DHCPDISCOVER(wlan-ap) 46:cb:c9:7e:29:c8
#   Jun  8 18:09:03 dnsmasq-dhcp[287949]: 905408650 DHCPOFFER(wlan-ap) 10.0.0.104 46:cb:c9:7e:29:c8
#   Jun  8 18:09:03 dnsmasq-dhcp[287949]: 905408650 requested options: 1:netmask, 121:classless-static-route, 3:router,
#   Jun  8 18:09:03 dnsmasq-dhcp[287949]: 905408650 requested options: 6:dns-server, 15:domain-name, 108:ipv6-only,
#   Jun  8 18:09:03 dnsmasq-dhcp[287949]: 905408650 requested options: 114, 119:domain-search, 162, 252
#   Jun  8 18:09:03 dnsmasq-dhcp[287949]: 905408650 client provides name: Joes-iPhone
#   Jun  8 18:09:03 dnsmasq-dhcp[287949]: 905408650 vendor class: ...
#   Jun  8 18:09:03 dnsmasq-dhcp[287949]: 905408650 DHCPREQUEST(wlan-ap) 10.0.0.104 46:cb:c9:7e:29:c8
#   Jun  8 18:09:03 dnsmasq-dhcp[287949]: 905408650 DHCPACK(wlan-ap) 10.0.0.104 46:cb:c9:7e:29:c8 [hostname]
#   Jun  8 18:09:08 dnsmasq[287949]: query[A] init.itunes.apple.com from 10.0.0.104
#
# Key shape choices for our parser:
#   * DHCP types are UPPERCASE (DHCPDISCOVER/OFFER/REQUEST/ACK/NAK).
#     Older dnsmasq used lowercase ``dhcp-discover``; we accept both.
#   * Each transaction is keyed by the numeric ID prefix
#     (``905408650`` above). Multi-line ``requested options`` lines
#     share the same ID — we accumulate opt55 per-ID and flush on
#     DHCPACK.
#   * DHCPDISCOVER lines have no IP (only MAC). DHCPOFFER/REQUEST/ACK
#     have both. Hostname is only on DHCPACK and only if the client
#     sent option 12. iOS sends ``*`` (privacy) so often missing.

_RE_DHCP = re.compile(
    r"(?P<txn>\d+)\s+"
    r"(?:DHCP(?P<typeu>DISCOVER|OFFER|REQUEST|ACK|NAK|INFORM|RELEASE)"
    r"|dhcp-(?P<typel>discover|offer|request|ack|nak|inform|release))"
    r"\((?P<iface>[^)]+)\)\s+"
    r"(?:(?P<ip>\d+\.\d+\.\d+\.\d+)\s+)?"
    r"(?P<mac>[0-9a-fA-F:]{17})"
    r"(?:\s+(?P<hostname>\S+))?"
)
_RE_REQUESTED = re.compile(
    r"(?:(?P<txn>\d+)\s+)?requested options:\s+(?P<opts>[\d:a-zA-Z, \-]+)"
)
_RE_VENDOR = re.compile(
    r"(?:(?P<txn>\d+)\s+)?vendor class:\s+(?P<vc>.+)"
)
_RE_NAME = re.compile(
    r"(?:(?P<txn>\d+)\s+)?client provides name:\s+(?P<name>\S+)"
)
_RE_QUERY = re.compile(
    r"query\[(?P<qtype>[A-Z]+)\]\s+(?P<name>\S+)\s+from\s+"
    r"(?P<src>\d+\.\d+\.\d+\.\d+)"
)


def _extract_opt55(reqline: str) -> str | None:
    """Pull the numeric option codes out of 'requested options:' text.

    dnsmasq's verbose format is comma-separated chunks like
    ``1:netmask, 121:classless-static-route, 3:router`` — but some
    options come through as just bare numbers (no colon + name) when
    dnsmasq doesn't have a friendly name for them, e.g. iOS sends
    options 114 and 162 which often appear as ``114,`` and ``162,``
    in the log. Match the leading digit run of each comma-separated
    chunk to handle both shapes."""
    nums: list[str] = []
    for chunk in reqline.split(","):
        chunk = chunk.strip()
        m = re.match(r"^(\d+)", chunk)
        if m:
            nums.append(m.group(1))
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
        # In-flight DHCP state keyed by transaction ID (or "mac:<mac>"
        # for old-format dnsmasq that doesn't emit txn IDs). Each entry
        # accumulates {mac, ip, hostname, opt55_parts, vendor_class}
        # across the multiple log lines a single DHCP exchange produces.
        # Flushed to an upsert_dhcp call on the DHCPACK line.
        self._pending_txn: dict[str, dict[str, Any]] = {}
        # Lease-file poller state. Set by start_lease_poller; cleared
        # by stop_lease_poller. The poller is the *primary* source of
        # truth for "who's connected right now" — log parsing is
        # enrichment only.
        self._lease_path: Path | None = None
        self._lease_thread: threading.Thread | None = None
        self._lease_stop = threading.Event()
        # MACs the operator has explicitly cleared, mapped to the
        # cleared-at timestamp. The lease poller skips re-adding a
        # MAC if its lease was issued before the clear time (i.e. the
        # lease entry is leftover state, not a fresh association).
        # When the client genuinely renews/re-associates after the
        # clear, the new lease's issue time is > cleared_at and we
        # un-suppress the MAC automatically.
        self._suppressed_macs: dict[str, float] = {}

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
        """Wipe the persisted client store AND record current MACs as
        suppressed so the lease-file poller doesn't immediately re-add
        them from leftover dnsmasq lease entries. A client only
        reappears after they genuinely renew/re-DHCP (their new lease's
        issue time will be > the cleared_at timestamp)."""
        with self._lock:
            cleared_at = time.time()
            n = len(self._clients)
            # Suppress every currently-tracked MAC from poller re-adds.
            # Old entries in the suppression dict stay (we don't reset
            # the map) so prior Clear operations remain in effect.
            for mac in self._clients:
                self._suppressed_macs[mac] = cleared_at
            self._clients = {}
            self._ip_to_mac = {}
            self._persist()
        log.info("client_recon: cleared %d clients, %d MACs suppressed",
                 n, len(self._suppressed_macs))
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

    # ---------- Lease-file poller (primary client source) ----------
    def start_lease_poller(self, lease_path: Path,
                           interval: float = 3.0) -> tuple[bool, str]:
        """Poll dnsmasq's lease file every ``interval`` seconds and
        upsert any new clients. The lease file is the authoritative
        source for "who's connected right now" — log parsing might
        miss transactions (e.g. older clients that renew without a
        full DISCOVER), but every active client has a lease row."""
        with self._lock:
            if self._lease_thread is not None and self._lease_thread.is_alive():
                return True, f"already polling {self._lease_path}"
            self._lease_path = lease_path
            self._lease_stop.clear()

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
                while not self._lease_stop.is_set():
                    try:
                        self._poll_leases(lease_path)
                    except Exception:
                        log.exception("client_recon lease-poll tick failed")
                    self._lease_stop.wait(interval)
            finally:
                if ctx:
                    ctx.pop()

        t = threading.Thread(target=_run, name="client-recon-lease",
                             daemon=True)
        with self._lock:
            self._lease_thread = t
        t.start()
        return True, f"polling {lease_path} every {interval}s"

    def stop_lease_poller(self) -> None:
        self._lease_stop.set()

    def _poll_leases(self, lease_path: Path) -> None:
        """Read dnsmasq's lease file (format: ``<expiry> <mac> <ip>
        <hostname> <client_id>`` per line) and upsert anything new.
        Hostname ``*`` means the client didn't send DHCP option 12 —
        we treat that as None, not the literal asterisk.

        Honors the suppression map: a MAC the operator has Cleared
        won't be re-added by this poller unless the lease was issued
        AFTER the clear (i.e. the client did a fresh DHCP renewal)."""
        if not lease_path.is_file():
            return
        try:
            text = lease_path.read_text(errors="replace")
        except OSError:
            return
        # dnsmasq's standard lease length matches what we configured
        # in pineap.py (12h). The lease entry's first column is the
        # absolute expiry timestamp, so issue_time = expiry - 12h.
        _LEASE_LEN_SEC = 12 * 3600
        for line in text.splitlines():
            parts = line.strip().split()
            if len(parts) < 3:
                continue
            try:
                expiry = int(parts[0])
            except ValueError:
                continue
            mac = parts[1].lower()
            ip = parts[2]
            hostname = parts[3] if len(parts) >= 4 else None
            if hostname == "*":
                hostname = None

            # Suppression check: was this MAC just Cleared, and is this
            # lease leftover (issued before the clear)? Use a small
            # fudge window (-10s) to handle clock skew between dnsmasq
            # writing the lease and Python's time.time() recording the
            # cleared_at — without it, a lease renewed right after a
            # clear can still appear "stale" due to sub-second timing.
            _CLEAR_FUDGE_SEC = 10
            with self._lock:
                cleared_at = self._suppressed_macs.get(mac)
            if cleared_at is not None:
                lease_issued_at = expiry - _LEASE_LEN_SEC
                if lease_issued_at < cleared_at - _CLEAR_FUDGE_SEC:
                    # Stale lease from before clear — skip
                    continue
                # Fresh renewal since clear — un-suppress
                with self._lock:
                    self._suppressed_macs.pop(mac, None)
                log.info("client_recon: %s renewed after clear, un-suppressing", mac)

            with self._lock:
                existing = self._clients.get(mac)
            if existing and existing.get("ip") == ip and (
                hostname is None or existing.get("hostname") == hostname
            ):
                # No change — skip the upsert to avoid touching last_seen
                # unnecessarily from the poller. (Real DHCP exchanges
                # in the log keep last_seen fresh.)
                continue
            # New client or changed IP/hostname — upsert (this also
            # fires the client:upsert SocketIO event)
            self.upsert_dhcp(mac=mac, ip=ip, hostname=hostname)

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
                    # Start from BEGINNING of file. PineAP's lifecycle
                    # truncates the log right before dnsmasq starts, so
                    # everything in the file is from the current run.
                    # Earlier we seeked-to-end to avoid replaying stale
                    # lines, but that lost DHCP-acks logged in the gap
                    # between dnsmasq launch and tailer attach — meaning
                    # clients that joined fast never made it into the
                    # IP→MAC map, so their DNS queries got dropped as
                    # "unknown source IP" and the Clients view stayed
                    # empty. Read from 0 so we catch everything.
                    f.seek(0, 0)
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
        """Single log-line dispatch.

        dnsmasq emits DHCP info across several lines per exchange, all
        sharing a numeric transaction-ID prefix (e.g. ``905408650``).
        We accumulate per-transaction state in ``self._pending_txn``
        and flush to an upsert on the DHCPACK line. Continuation lines
        for ``requested options:`` (which dnsmasq wraps at ~70 chars)
        are concatenated by transaction ID to recover the full opt55
        fingerprint.

        Falls back to MAC-keyed accumulation for older dnsmasq versions
        that don't emit the transaction ID prefix.
        """
        # 1. DNS query — single-line, attribute and move on
        m = _RE_QUERY.search(line)
        if m:
            self.record_query(m.group("src"), m.group("qtype"), m.group("name"))
            return

        # 2. DHCP DISCOVER/OFFER/REQUEST/ACK — populate per-txn record
        m = _RE_DHCP.search(line)
        if m:
            txn = m.group("txn")
            mac = m.group("mac").lower()
            ip = m.group("ip")
            hostname = m.group("hostname")
            # iOS sends '*' as option 12 placeholder (privacy default).
            # Treat as None so the UI shows "no hostname" rather than literal "*".
            if hostname == "*":
                hostname = None

            key = txn or f"mac:{mac}"
            pending = self._pending_txn.setdefault(key, {"opt55_parts": []})
            pending["mac"] = mac
            pending["last_update"] = time.time()
            if ip:
                pending["ip"] = ip
            if hostname:
                pending["hostname"] = hostname
            # Always re-upsert with the current accumulated state.
            # Idempotent: same MAC just updates the record. We don't
            # pop the pending entry on ACK (the way the old code did)
            # because dnsmasq logs the `requested options:` lines AFTER
            # DHCPACK on lease renewals — flushing on ACK loses opt55.
            # Cleanup runs in _gc_pending_txns instead.
            self._flush_pending(key)
            return

        # 3. "requested options: ..." — possibly continuation of a
        #    multi-line option list. The transaction ID prefix matches
        #    the DHCP line; accumulate by ID. Re-upsert if we know the
        #    MAC so the OS guess updates as more option bytes arrive.
        m = _RE_REQUESTED.search(line)
        if m:
            opt_nums = _extract_opt55(m.group("opts"))
            txn = m.group("txn")
            if opt_nums:
                self._append_opt_part(txn, opt_nums)
                # Re-flush so opt55 gets recomputed and pushed to the
                # client record (and the SocketIO `client:upsert` event
                # carries the now-richer fingerprint).
                key = txn or (next(reversed(self._pending_txn))
                              if self._pending_txn else None)
                if key:
                    self._flush_pending(key)
            return

        # 4. "client provides name: ..." — pending hostname
        m = _RE_NAME.search(line)
        if m:
            txn = m.group("txn")
            target = self._lookup_pending(txn)
            if target is not None:
                target["hostname"] = m.group("name")
                target["last_update"] = time.time()
                key = txn or (next(reversed(self._pending_txn))
                              if self._pending_txn else None)
                if key:
                    self._flush_pending(key)
            return

        # 5. "vendor class: MSFT 5.0"
        m = _RE_VENDOR.search(line)
        if m:
            txn = m.group("txn")
            target = self._lookup_pending(txn)
            if target is not None:
                target["vendor_class"] = m.group("vc").strip()
                target["last_update"] = time.time()
                key = txn or (next(reversed(self._pending_txn))
                              if self._pending_txn else None)
                if key:
                    self._flush_pending(key)
            return

    def _flush_pending(self, key: str) -> None:
        """Upsert the client record for ``key`` with whatever's in
        the pending entry. Idempotent — multiple calls during a single
        DHCP transaction just refine the record progressively."""
        p = self._pending_txn.get(key)
        if not p or not p.get("mac"):
            return
        opt55 = ",".join(p.get("opt55_parts") or []) or None
        if opt55 and opt55.endswith(","):
            opt55 = opt55.rstrip(",")
        # GC any aged-out entries while we're here (cheap, runs on
        # every DHCP touch — keeps the dict bounded without a timer)
        self._gc_pending_txns()
        self.upsert_dhcp(
            mac=p["mac"],
            ip=p.get("ip"),
            hostname=p.get("hostname"),
            opt55=opt55,
            vendor_class=p.get("vendor_class"),
        )

    def _gc_pending_txns(self, max_age: float = 60.0) -> None:
        """Drop pending transactions we haven't touched in ``max_age``
        seconds. Each transaction's actual log lines arrive within
        about one second, so 60s is generous. Without this the dict
        would accumulate one entry per DHCP exchange across the
        platform's lifetime."""
        now = time.time()
        stale = [k for k, p in self._pending_txn.items()
                 if (now - (p.get("last_update") or 0)) > max_age]
        for k in stale:
            self._pending_txn.pop(k, None)

    def _append_opt_part(self, txn: str | None, opt_nums: str) -> None:
        """Append a chunk of opt55 numbers to the matching transaction's
        accumulator. Without a txn ID, falls back to the most-recent
        pending entry."""
        if txn:
            pending = self._pending_txn.get(txn)
            if pending is None:
                # Continuation line arrived before the corresponding
                # DHCPDISCOVER (shouldn't normally happen, but be
                # defensive — pre-create the entry).
                pending = self._pending_txn.setdefault(
                    txn, {"opt55_parts": []})
            pending.setdefault("opt55_parts", []).append(opt_nums)
        elif self._pending_txn:
            # Old-format fallback: append to the most-recent pending
            last_key = next(reversed(self._pending_txn))
            self._pending_txn[last_key].setdefault("opt55_parts", []).append(opt_nums)

    def _lookup_pending(self, txn: str | None) -> dict[str, Any] | None:
        if txn:
            return self._pending_txn.get(txn)
        if self._pending_txn:
            last_key = next(reversed(self._pending_txn))
            return self._pending_txn[last_key]
        return None

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
