"""Campaigns — scripted, time-boxed assessment runs (S14).

A campaign is the abstraction that makes the platform usable for a real
engagement: instead of poking recon/PineAP/handshakes by hand, you pick
a template, set a window, hit Run, and get a report. Each template
orchestrates the existing services and, at the end of the window (or on
manual Stop), produces a JSON + HTML report of what was observed.

Three templates (faithful to the Hak5 Pineapple's campaign set):

* ``recon``   — Reconnaissance (Monitor Only). Passive 802.11 scan for
  the window; reports the AP + client landscape. No frames transmitted.
* ``passive`` — Client Device Assessment (Passive). Recon scan + surface
  any handshakes captured during the window. Still no offensive frames.
* ``active``  — Client Device Assessment (Active). Offensive: brings up
  the PineAP rogue (Advanced/open = beacon + Karma probe responses) and,
  if a lab target BSSID is supplied, fires periodic broadcast deauth at
  it. Reports rogue clients, captured handshakes, captive creds, Karma
  stats. Ethics-gated (operator types ``active``). Lab-use only.

Run model: a ``duration_secs`` window auto-stops + reports at the
deadline (the default); ``duration_secs=0`` runs until the operator
hits Stop. One campaign at a time.

Radio note: ``active`` brings up PineAP, which pauses recon to claim the
monitor radio — so an active campaign is rogue-centric, not a
simultaneous recon+rogue run (the hardware can't do both). recon /
passive use the monitor radios only.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from html import escape
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------- Templates ----------

TEMPLATES: dict[str, dict[str, Any]] = {
    "recon": {
        "name": "Reconnaissance (Monitor Only)",
        "offensive": False,
        "blurb": "Passive 802.11 scan. Reports the AP + client landscape. "
                 "No frames transmitted.",
    },
    "passive": {
        "name": "Client Device Assessment (Passive)",
        "offensive": False,
        "blurb": "Recon scan + any handshakes captured during the window. "
                 "Surfaces client probe-SSID leaks. No offensive frames.",
    },
    "active": {
        "name": "Client Device Assessment (Active)",
        "offensive": True,
        "blurb": "Brings up the PineAP rogue (beacon + Karma) and, with a "
                 "lab target BSSID, periodic broadcast deauth. Reports rogue "
                 "clients, captures, captive creds. Offensive — lab only.",
    },
}

_DEAUTH_INTERVAL = 5.0


class CampaignsService:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._dir = data_dir / "campaigns"
        self._index_path = self._dir / "index.json"
        self._lock = threading.Lock()
        self._run: dict[str, Any] | None = None       # current run record
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ---------- Public ----------
    def list_templates(self) -> list[dict[str, Any]]:
        return [{"id": k, **{kk: vv for kk, vv in v.items()}}
                for k, v in TEMPLATES.items()]

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            run = dict(self._run) if self._run else None
        return {
            "running": self.is_running(),
            "run": run,
            "templates": self.list_templates(),
        }

    def start(
        self, template: str, *, duration_secs: int = 600,
        confirm: str | None = None, target_bssid: str | None = None,
    ) -> tuple[bool, str]:
        if template not in TEMPLATES:
            return False, f"unknown template {template!r}"
        if self.is_running():
            return False, "a campaign is already running — stop it first"
        try:
            duration_secs = int(duration_secs)
        except (TypeError, ValueError):
            return False, "duration_secs must be an int"
        if duration_secs < 0 or duration_secs > 24 * 3600:
            return False, "duration_secs out of range (0 = until stopped, max 24h)"
        if TEMPLATES[template]["offensive"]:
            if (confirm or "").strip().lower() != "active":
                return False, "type 'active' to confirm an offensive campaign"

        run_id = uuid.uuid4().hex[:12]
        self._run = {
            "id": run_id,
            "template": template,
            "template_name": TEMPLATES[template]["name"],
            "started_at": time.time(),
            "ended_at": None,
            "duration_secs": duration_secs,
            "status": "running",
            "stopped_early": False,
            "target_bssid": (target_bssid or "").strip().lower() or None,
            "steps": [],
        }
        self._stop_event.clear()

        try:
            from flask import current_app
            app = current_app._get_current_object()
        except Exception:
            app = None

        def _run_thread() -> None:
            ctx = app.app_context() if app is not None else None
            if ctx:
                ctx.push()
            try:
                self._execute(template, duration_secs)
            except Exception:
                log.exception("campaign %s crashed", run_id)
                with self._lock:
                    if self._run:
                        self._run["status"] = "failed"
            finally:
                if ctx:
                    ctx.pop()

        t = threading.Thread(target=_run_thread, name=f"campaign-{run_id}",
                             daemon=True)
        self._thread = t
        t.start()
        log.info("campaign %s started: %s (window %ss)",
                 run_id, template, duration_secs or "until-stopped")
        self._emit_status()
        return True, f"campaign {template} started ({run_id})"

    def stop(self) -> tuple[bool, str]:
        if not self.is_running():
            return True, "no campaign running"
        with self._lock:
            if self._run:
                self._run["stopped_early"] = True
        self._stop_event.set()
        return True, "campaign stopping — report will be written"

    # ---------- Orchestration ----------
    def _step(self, msg: str) -> None:
        log.info("campaign step: %s", msg)
        with self._lock:
            if self._run:
                self._run["steps"].append({"ts": time.time(), "msg": msg})
        self._emit_status()

    def _execute(self, template: str, duration_secs: int) -> None:
        deauth_thread: threading.Thread | None = None
        try:
            deauth_thread = self._orchestrate_start(template)
            # Run the window (or until stopped).
            if duration_secs > 0:
                self._stop_event.wait(duration_secs)
            else:
                while not self._stop_event.is_set():
                    self._stop_event.wait(1.0)
        finally:
            # Stop the in-campaign deauth loop (active) before teardown.
            self._stop_event.set()
            if deauth_thread is not None:
                deauth_thread.join(timeout=3.0)
            self._orchestrate_stop(template)
            report = self._build_report(template)
            self._persist_report(report)
            with self._lock:
                if self._run:
                    self._run["status"] = "done"
                    self._run["ended_at"] = report["ended_at"]
            self._emit_status()
            log.info("campaign %s complete", report["id"])

    def _orchestrate_start(self, template: str) -> threading.Thread | None:
        """Bring up the services this template needs. Returns a deauth
        thread for the active template (else None). Best-effort."""
        if template in ("recon", "passive"):
            try:
                from app.services.recon import get_service as get_recon
                ok, msgs = get_recon().start_scan()
                self._step(f"recon scan: {'; '.join(msgs) if msgs else ok}")
            except Exception as e:
                self._step(f"recon start failed: {e}")
            if template == "passive":
                self._step("passive: surfacing in-window handshake captures")
            return None

        # active
        self._step("active campaign — bringing up PineAP rogue (advanced/open)")
        try:
            from app.services.pineap import get_service as get_pineap
            pin = get_pineap()
            pin.set_ap_config(security_mode="open")
            pin.set_mode("advanced")
            ok, msgs = pin.start()
            self._step(f"pineap start: {'; '.join(msgs)}")
        except Exception as e:
            self._step(f"pineap start failed: {e}")

        # Optional broadcast deauth at a supplied lab target.
        with self._lock:
            target = self._run.get("target_bssid") if self._run else None
        if target:
            return self._start_deauth_loop(target)
        self._step("no target_bssid supplied — skipping deauth sweep")
        return None

    def _start_deauth_loop(self, bssid: str) -> threading.Thread:
        from app.tools._common import stub_mode

        def _loop() -> None:
            from app.tools import aireplay
            self._step(f"deauth sweep armed at {bssid} (every {int(_DEAUTH_INTERVAL)}s)")
            # Use the spare 2.4 monitor radio; campaign assumes recon is
            # paused by PineAP so it's free.
            iface = "wlan-mon-2g"
            n = 0
            while not self._stop_event.is_set():
                try:
                    ok, _msg = aireplay.send_deauth(iface, bssid,
                                                    client_mac=None, count=10)
                    if ok:
                        n += 1
                except Exception:
                    log.exception("campaign deauth burst failed")
                self._stop_event.wait(_DEAUTH_INTERVAL)
            self._step(f"deauth sweep stopped ({n} bursts)")

        t = threading.Thread(target=_loop, name="campaign-deauth", daemon=True)
        t.start()
        return t

    def _orchestrate_stop(self, template: str) -> None:
        if template in ("recon", "passive"):
            try:
                from app.services.recon import get_service as get_recon
                get_recon().stop_scan()
                self._step("recon scan stopped")
            except Exception as e:
                self._step(f"recon stop failed: {e}")
        else:  # active
            try:
                from app.services.pineap import get_service as get_pineap
                ok, msgs = get_pineap().stop()
                self._step(f"pineap stop: {'; '.join(msgs)}")
            except Exception as e:
                self._step(f"pineap stop failed: {e}")

    # ---------- Reporting ----------
    def _build_report(self, template: str) -> dict[str, Any]:
        with self._lock:
            run = dict(self._run) if self._run else {}
        started = run.get("started_at") or time.time()
        ended = time.time()

        aps: list[dict[str, Any]] = []
        clients: list[dict[str, Any]] = []
        try:
            from app.services.recon import get_service as get_recon
            snap = get_recon().get_snapshot()
            aps = snap.get("aps") or []
            clients = snap.get("clients") or []
        except Exception:
            log.debug("campaign report: recon snapshot unavailable", exc_info=True)

        # Handshakes captured during the window.
        handshakes: list[dict[str, Any]] = []
        try:
            from app.services.handshakes import get_service as get_hs
            handshakes = [c for c in get_hs().list_captures()
                          if (c.get("started_at") or 0) >= started - 1]
        except Exception:
            log.debug("campaign report: handshakes unavailable", exc_info=True)

        rogue_clients: list[dict[str, Any]] = []
        credentials: list[dict[str, Any]] = []
        karma: dict[str, Any] = {}
        if template == "active":
            try:
                from app.services.client_recon import get_service as get_cr
                rogue_clients = [c for c in get_cr().list_clients()
                                 if (c.get("last_seen") or 0) >= started - 1]
            except Exception:
                pass
            try:
                from app.services.captive_portal import get_service as get_cp
                credentials = [c for c in get_cp().list_credentials()
                               if (c.get("ts") or 0) >= started - 1]
            except Exception:
                pass
            try:
                from app.services.karma import get_service as get_karma
                karma = get_karma().get_stats()
            except Exception:
                pass

        report = {
            "id": run.get("id"),
            "template": template,
            "template_name": run.get("template_name"),
            "started_at": started,
            "ended_at": ended,
            "duration_secs": int(ended - started),
            "configured_window_secs": run.get("duration_secs"),
            "stopped_early": run.get("stopped_early", False),
            "target_bssid": run.get("target_bssid"),
            "summary": {
                "access_points": len(aps),
                "clients": len(clients),
                "handshakes_captured": len(handshakes),
                "rogue_clients": len(rogue_clients),
                "credentials_harvested": len(credentials),
            },
            "access_points": aps,
            "clients": clients,
            "handshakes": handshakes,
            "rogue_clients": rogue_clients,
            "credentials": credentials,
            "karma": karma,
            "steps": run.get("steps", []),
        }
        return report

    def _persist_report(self, report: dict[str, Any]) -> None:
        rid = report.get("id") or uuid.uuid4().hex[:12]
        run_dir = self._dir / rid
        try:
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "report.json").write_text(json.dumps(report, indent=2))
            (run_dir / "report.html").write_text(self._render_html(report))
        except OSError:
            log.exception("campaign report write failed")
            return
        # Index entry (compact).
        entry = {
            "id": rid,
            "template": report["template"],
            "template_name": report["template_name"],
            "started_at": report["started_at"],
            "ended_at": report["ended_at"],
            "duration_secs": report["duration_secs"],
            "stopped_early": report["stopped_early"],
            "summary": report["summary"],
        }
        with self._lock:
            try:
                data = json.loads(self._index_path.read_text())
                if not isinstance(data, dict) or "reports" not in data:
                    data = {"reports": []}
            except (FileNotFoundError, json.JSONDecodeError):
                data = {"reports": []}
            data["reports"].append(entry)
            tmp = self._index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(self._index_path)

    def list_reports(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._index_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []
        reports = data.get("reports") or []
        reports.sort(key=lambda r: r.get("started_at") or 0, reverse=True)
        return reports

    def report_path(self, report_id: str, fmt: str) -> Path | None:
        name = "report.json" if fmt == "json" else "report.html"
        p = self._dir / report_id / name
        return p if p.is_file() else None

    def _render_html(self, r: dict[str, Any]) -> str:
        def _ts(u: float | None) -> str:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(u)) if u else "—"

        s = r["summary"]
        ap_rows = "".join(
            f"<tr><td>{escape(str(a.get('essid') or '<hidden>'))}</td>"
            f"<td><code>{escape(str(a.get('bssid') or ''))}</code></td>"
            f"<td>{escape(str(a.get('channel') or ''))}</td>"
            f"<td>{escape(str(a.get('signal_dbm') if a.get('signal_dbm') is not None else '—'))}</td>"
            f"<td>{escape(str((a.get('encryption') or '') + (('/' + a['auth']) if a.get('auth') else '')))}</td></tr>"
            for a in r.get("access_points", [])[:200])
        hs_rows = "".join(
            f"<tr><td>{escape(str(h.get('source') or 'Recon capture'))}</td>"
            f"<td>{escape(str(h.get('essid_at_capture') or ''))}</td>"
            f"<td><code>{escape(str(h.get('bssid') or ''))}</code></td>"
            f"<td>{'complete' if h.get('is_complete') else ('partial' if h.get('is_partial') else 'none')}</td></tr>"
            for h in r.get("handshakes", []))
        cred_rows = "".join(
            f"<tr><td>{escape(str(c.get('ssid') or ''))}</td>"
            f"<td><code>{escape(str(c.get('psk') or ''))}</code></td>"
            f"<td>{'verified' if c.get('verified') else ('wrong' if c.get('verified') is False else '?')}</td></tr>"
            for c in r.get("credentials", []))
        return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Campaign report — {escape(str(r.get('template_name')))}</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;color:#1a1a1a}}
h1{{font-size:20px}}h2{{font-size:15px;margin-top:24px;border-bottom:1px solid #ddd;padding-bottom:4px}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:6px}}
td,th{{border:1px solid #e2e2e2;padding:5px 8px;text-align:left}}
.sum{{display:flex;gap:18px;flex-wrap:wrap;margin-top:10px}}
.sum div{{background:#f4f6f9;border-radius:8px;padding:10px 14px}}
.sum b{{font-size:20px;display:block}}code{{font-family:Menlo,monospace;font-size:12px}}
.muted{{color:#777}}</style></head><body>
<h1>{escape(str(r.get('template_name')))}</h1>
<p class="muted">Run {escape(str(r.get('id')))} · {_ts(r.get('started_at'))} → {_ts(r.get('ended_at'))}
 · {r.get('duration_secs')}s{' · stopped early' if r.get('stopped_early') else ''}
 {('· target ' + escape(str(r.get('target_bssid')))) if r.get('target_bssid') else ''}</p>
<div class="sum">
  <div><b>{s['access_points']}</b>access points</div>
  <div><b>{s['clients']}</b>clients</div>
  <div><b>{s['handshakes_captured']}</b>handshakes</div>
  <div><b>{s['rogue_clients']}</b>rogue clients</div>
  <div><b>{s['credentials_harvested']}</b>credentials</div>
</div>
<h2>Access points ({s['access_points']})</h2>
<table><tr><th>SSID</th><th>BSSID</th><th>Ch</th><th>Signal</th><th>Security</th></tr>{ap_rows or '<tr><td colspan=5 class=muted>none</td></tr>'}</table>
<h2>Handshakes ({s['handshakes_captured']})</h2>
<table><tr><th>Source</th><th>SSID</th><th>BSSID</th><th>Status</th></tr>{hs_rows or '<tr><td colspan=4 class=muted>none</td></tr>'}</table>
{('<h2>Harvested credentials (' + str(s['credentials_harvested']) + ')</h2><table><tr><th>SSID</th><th>Password</th><th>Verified</th></tr>' + (cred_rows or '<tr><td colspan=3 class=muted>none</td></tr>') + '</table>') if r.get('template') == 'active' else ''}
<p class="muted" style="margin-top:24px;font-size:11px;">Generated by PiPineapple · lab-authorised testing only.</p>
</body></html>"""

    def _emit_status(self) -> None:
        try:
            from app import socketio
            socketio.emit("campaign:status", self.get_status(), namespace="/")
        except Exception:
            pass


# ---------- Module singleton ----------

_service: "CampaignsService | None" = None


def get_service() -> CampaignsService:
    global _service
    if _service is None:
        from flask import current_app
        _service = CampaignsService(current_app.config["DATA_DIR"])
    return _service
