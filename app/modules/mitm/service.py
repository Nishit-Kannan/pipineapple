"""MitmService — runs a bettercap MITM session and streams its events.

Default OFF: every start requires the operator to type ``mitm`` (same ethics
gate pattern as pineap/phishing/active). Targets are fenced to **private
(RFC1918) ranges** — never the public internet — but may be any private IP/
CIDR, a PineAP client, or an nmap-discovered host (the hybrid fence). bettercap
runs as a killable child process; its stdout is read line by line, each line
bucketed (dns / http / cred / info) and pushed to the UI.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from typing import Any

from . import tools

log = logging.getLogger(__name__)

_CONFIRM_PHRASE = "mitm"
_MAX_PER_BUCKET = 300


def is_private_target(target: str) -> bool:
    """True iff every IP/CIDR in ``target`` (comma/space list) is RFC1918 /
    loopback / link-local. Refuses hostnames and public addresses."""
    parts = [t for t in (target or "").replace(",", " ").split() if t]
    if not parts:
        return False
    for tok in parts:
        try:
            if "/" in tok:
                net = ipaddress.ip_network(tok, strict=False)
                ok = net.is_private or net.is_loopback or net.is_link_local
            else:
                ip = ipaddress.ip_address(tok)
                ok = ip.is_private or ip.is_loopback or ip.is_link_local
            if not ok:
                return False
        except ValueError:
            return False
    return True


class MitmService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._proc: Any = None
        self._stop_event = threading.Event()
        self._status: dict[str, Any] = {
            "running": False, "iface": None, "targets": None,
            "dns_spoof": False, "command": None,
            "started_at": None, "finished_at": None, "message": "idle",
        }
        self._events: dict[str, list[dict[str, Any]]] = {
            "dns": [], "http": [], "cred": [], "info": [],
        }

    # ---------- Target candidates + iface ----------
    def candidate_targets(self) -> dict[str, list[dict[str, Any]]]:
        """Targets the operator can pick: current PineAP client leases and
        the most recent nmap-discovered hosts (both private by construction)."""
        clients = []
        try:
            from app.services.client_recon import get_service as get_cr
            for c in get_cr().list_clients():
                if c.get("ip"):
                    clients.append({"ip": c["ip"], "label": c.get("hostname") or c.get("mac") or c["ip"]})
        except Exception:
            log.exception("mitm: client lookup failed")
        hosts = []
        try:
            from app.modules.nmap_scan.service import get_service as get_nmap
            for h in get_nmap().get_results():
                if h.get("ip"):
                    hosts.append({"ip": h["ip"], "label": h.get("hostname") or h["ip"]})
        except Exception:
            pass
        return {"clients": clients, "nmap_hosts": hosts}

    def iface_for_target(self, target: str) -> str | None:
        """The Pi interface on the same subnet as the (first) target IP —
        the link bettercap must spoof on. None if undetermined."""
        from app.tools._common import stub_mode
        from app.tools import iproute
        first = next((t for t in (target or "").replace(",", " ").split()), "")
        ip_str = first.split("/")[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return None
        if stub_mode():
            return "wlan-ap"
        for iface in iproute.list_interfaces():
            name = iface.get("name", "")
            if name in ("lo",) or name.startswith("wlan-mon"):
                continue
            for cidr in iface.get("addresses", []):
                try:
                    if ip in ipaddress.ip_network(cidr, strict=False):
                        return name
                except ValueError:
                    continue
        return None

    # ---------- Lifecycle ----------
    def start(self, *, targets: str, confirm: str | None,
              iface: str | None = None, dns_spoof: bool = False,
              dns_domains: str = "*", dns_redirect_ip: str | None = None,
              ) -> tuple[bool, str]:
        if (confirm or "").strip().lower() != _CONFIRM_PHRASE:
            return False, "type 'mitm' to confirm — MITM intercepts other devices' traffic"
        targets = (targets or "").strip()
        if not targets:
            return False, "no target specified"
        if not is_private_target(targets):
            return False, ("target refused — MITM is fenced to private/lab "
                           "ranges (RFC1918). Got: " + targets)
        use_iface = (iface or "").strip() or self.iface_for_target(targets)
        if not use_iface:
            return False, ("couldn't determine which interface the target is "
                           "on — specify one, or check the target is on a "
                           "subnet the Pi is connected to")
        if dns_redirect_ip and not is_private_target(dns_redirect_ip):
            return False, "dns redirect IP must be private"

        argv = tools.build_argv(use_iface, targets, dns_spoof=dns_spoof,
                                dns_domains=dns_domains,
                                dns_redirect_ip=dns_redirect_ip)
        self._stop_event.clear()
        with self._lock:
            if self._status["running"]:
                return False, "a MITM session is already running"
            self._proc = None
            self._events = {"dns": [], "http": [], "cred": [], "info": []}
            self._status.update({
                "running": True, "iface": use_iface, "targets": targets,
                "dns_spoof": bool(dns_spoof), "command": " ".join(argv),
                "started_at": time.time(), "finished_at": None,
                "message": "MITM active",
            })

        try:
            from flask import current_app
            app = current_app._get_current_object()
        except Exception:
            app = None

        t = threading.Thread(target=self._run, args=(argv, app),
                             name="mitm-bettercap", daemon=True)
        with self._lock:
            self._thread = t
        t.start()
        return True, f"MITM started on {use_iface} → {targets}"

    def _run(self, argv: list[str], app) -> None:
        ctx = app.app_context() if app is not None else None
        if ctx:
            ctx.push()
        msg = "session ended"
        try:
            from app.tools._common import stub_mode
            if stub_mode():
                for line in tools.STUB_EVENT_LINES:
                    if self._stop_event.is_set():
                        break
                    self._ingest(line)
                    self._stop_event.wait(0.05)
                # idle until stopped so the UI shows a running session
                while not self._stop_event.is_set():
                    self._stop_event.wait(0.2)
            else:
                import subprocess
                try:
                    proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True,
                                            bufsize=1)
                except FileNotFoundError:
                    msg = ("bettercap not installed — run "
                           "'sudo apt install bettercap' on the Pi")
                    log.warning(msg)
                    return
                with self._lock:
                    self._proc = proc
                for line in iter(proc.stdout.readline, ""):
                    if self._stop_event.is_set():
                        break
                    self._ingest(line)
                try:
                    proc.terminate()
                except Exception:
                    pass
            if self._stop_event.is_set():
                msg = "MITM stopped by operator"
        except Exception as e:
            log.exception("mitm session crashed")
            msg = f"session crashed: {e}"
        finally:
            with self._lock:
                self._proc = None
                self._status.update({
                    "running": False, "finished_at": time.time(), "message": msg,
                })
            self._emit_status()
            if ctx:
                ctx.pop()

    def _ingest(self, raw_line: str) -> None:
        ev = tools.parse_event_line(raw_line)
        if not ev:
            return
        ev["ts"] = time.time()
        bucket = ev["kind"]
        with self._lock:
            lst = self._events.setdefault(bucket, [])
            lst.append(ev)
            if len(lst) > _MAX_PER_BUCKET:
                del lst[: len(lst) - _MAX_PER_BUCKET]
        self._emit_event(ev)

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            if not self._status["running"]:
                return False, "no MITM session is running"
            proc = self._proc
        self._stop_event.set()
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                log.exception("mitm: terminate failed")
        return True, "stopping MITM…"

    # ---------- Getters ----------
    def get_status(self) -> dict[str, Any]:
        with self._lock:
            st = dict(self._status)
            st["counts"] = {k: len(v) for k, v in self._events.items()}
        return st

    def get_events(self, *, limit: int = 200) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            return {k: list(reversed(v[-limit:])) for k, v in self._events.items()}

    def _emit_status(self) -> None:
        try:
            from app import socketio
            socketio.emit("mitm:status", self.get_status(), namespace="/")
        except Exception:
            pass

    def _emit_event(self, ev: dict[str, Any]) -> None:
        try:
            from app import socketio
            socketio.emit("mitm:event", ev, namespace="/")
        except Exception:
            pass


_service: "MitmService | None" = None


def get_service() -> MitmService:
    global _service
    if _service is None:
        _service = MitmService()
    return _service
