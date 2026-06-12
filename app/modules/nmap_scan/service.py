"""NmapService — orchestrates scans for the nmap module.

Runs one scan at a time in a background thread (an `-sV` sweep of a /24 can
take minutes, so we don't block the request), tracks live status, keeps the
last result set, and emits a SocketIO event on completion. Target resolution
covers the two roadmap sources — PineAP clients and the lab subnet — plus a
custom target, all behind an **RFC1918-only guard** so the module can't be
pointed at the public internet.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from typing import Any

from . import tools

log = logging.getLogger(__name__)


def _is_private_target(target: str) -> bool:
    """True iff every host implied by ``target`` (an IP, CIDR, or
    space-separated list) is RFC1918 / loopback / link-local. Refuses
    hostnames and anything routable on the public internet."""
    parts = [t for t in target.replace(",", " ").split() if t]
    if not parts:
        return False
    for tok in parts:
        try:
            if "/" in tok:
                net = ipaddress.ip_network(tok, strict=False)
                if not (net.is_private or net.is_loopback or net.is_link_local):
                    return False
            else:
                ip = ipaddress.ip_address(tok)
                if not (ip.is_private or ip.is_loopback or ip.is_link_local):
                    return False
        except ValueError:
            return False  # not an IP/CIDR (e.g. a hostname) → refuse
    return True


class NmapService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status: dict[str, Any] = {
            "running": False, "profile": None, "target": None,
            "started_at": None, "finished_at": None, "ok": None,
            "message": "no scan run yet", "host_count": 0,
        }
        self._hosts: list[dict[str, Any]] = []

    # ---------- Targets ----------
    def resolve_target(self, source: str, custom: str | None,
                       lab_cidr: str) -> tuple[str | None, str]:
        """Map a target source to an nmap target string.
        ``source`` ∈ {clients, subnet, uplink, custom}. Returns (target, msg)."""
        if source == "subnet":
            return lab_cidr, f"lab subnet {lab_cidr}"
        if source == "uplink":
            cidr = self.uplink_cidr()
            if not cidr:
                return None, "couldn't determine the uplink subnet"
            return cidr, f"uplink network {cidr}"
        if source == "clients":
            ips = self._pineap_client_ips()
            if not ips:
                return None, "no PineAP clients with a lease yet"
            return " ".join(ips), f"{len(ips)} PineAP client(s)"
        if source == "custom":
            t = (custom or "").strip()
            if not t:
                return None, "custom target is empty"
            return t, f"custom target {t}"
        return None, f"unknown target source {source!r}"

    def uplink_cidr(self) -> str | None:
        """The /N network of the Pi's *primary* uplink — the interface
        holding the lowest-metric default route (your wired travel-router
        link, not the home wifi or the rogue AP). Returns a CIDR string or
        None. Stub mode returns a sample so the Mac can exercise it."""
        from app.tools._common import run, stub_mode
        from app.tools import iproute
        if stub_mode():
            return "192.168.8.0/24"
        # 1. Find the default-route interface with the smallest metric.
        dev = None
        try:
            import json as _json
            res = run(["ip", "-j", "route"], timeout=3.0, source="nmap")
            routes = _json.loads(res.stdout or "[]")
            defaults = [r for r in routes if r.get("dst") == "default" and r.get("dev")]
            defaults.sort(key=lambda r: r.get("metric") or 0)
            if defaults:
                dev = defaults[0]["dev"]
        except Exception:
            log.exception("nmap: uplink route lookup failed")
        # 2. Map that interface to its private IPv4 network.
        for iface in iproute.list_interfaces():
            if dev and iface.get("name") != dev:
                continue
            for cidr in iface.get("addresses", []):
                try:
                    net = ipaddress.ip_network(cidr, strict=False)
                except ValueError:
                    continue
                if net.version == 4 and net.is_private and not net.is_loopback:
                    return str(net)
            if dev:  # matched the default dev but no private v4 → stop
                break
        return None

    def _pineap_client_ips(self) -> list[str]:
        try:
            from app.services.client_recon import get_service as get_cr
            return [c["ip"] for c in get_cr().list_clients() if c.get("ip")]
        except Exception:
            log.exception("nmap: pineap client lookup failed")
            return []

    # ---------- Scan lifecycle ----------
    def start_scan(self, profile: str, target: str) -> tuple[bool, str]:
        if profile not in tools.PROFILES:
            return False, f"unknown profile {profile!r}"
        if not _is_private_target(target):
            return False, ("target refused — nmap is fenced to private/lab "
                           "ranges (RFC1918). Got: " + target)
        with self._lock:
            if self._status["running"]:
                return False, "a scan is already running"
            self._status.update({
                "running": True, "profile": profile, "target": target,
                "started_at": time.time(), "finished_at": None, "ok": None,
                "message": "scanning…", "host_count": 0,
            })

        try:
            from flask import current_app
            app = current_app._get_current_object()
        except Exception:
            app = None

        def _run() -> None:
            ctx = app.app_context() if app is not None else None
            if ctx:
                ctx.push()
            ok = False
            msg = "scan crashed"
            hosts: list[dict[str, Any]] = []
            try:
                ok, msg, hosts = tools.run_scan(profile, target)
            except Exception as e:
                log.exception("nmap scan crashed")
                msg = f"scan crashed: {e}"
            finally:
                with self._lock:
                    self._hosts = hosts
                    self._status.update({
                        "running": False, "ok": ok, "message": msg,
                        "finished_at": time.time(), "host_count": len(hosts),
                    })
                self._emit()
                if ctx:
                    ctx.pop()

        t = threading.Thread(target=_run, name="nmap-scan", daemon=True)
        with self._lock:
            self._thread = t
        t.start()
        return True, f"scan started ({tools.PROFILES[profile]['label']})"

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def get_results(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(h) for h in self._hosts]

    def _emit(self) -> None:
        try:
            from app import socketio
            socketio.emit("nmap:status", self.get_status(), namespace="/")
        except Exception:
            pass


_service: "NmapService | None" = None


def get_service() -> NmapService:
    global _service
    if _service is None:
        _service = NmapService()
    return _service
