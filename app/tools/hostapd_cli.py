"""hostapd_cli wrapper — runtime control of a live hostapd.

hostapd's control socket lets us poke a *running* AP without a full
restart. S13 uses three things:

* ``deauthenticate`` / ``disassociate <mac>`` — kick a connected client
  (the Clients tab's "Kick" button). deauth is the harder boot (client
  must re-auth from scratch); disassociate is the gentler one.
* ``reload`` — re-read the config file on disk and apply it to the
  running AP. The Impersonation tab uses this to rotate the broadcast
  SSID: rewrite hostapd.conf with the next pool SSID + its BSSID, then
  ``reload`` — far cheaper than tearing the daemon down and back up
  every dwell. (If a given hostapd build doesn't pick up an SSID change
  on reload, the impersonation service falls back to a job restart.)

All commands target a specific BSS via ``-i <iface>``. Everything
self-stubs on Mac dev (USE_REAL_TOOLS=0) so the lifecycle is testable
without a radio.
"""

from __future__ import annotations

import logging
import re

from app.tools._common import run, stub_mode

log = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


def _cli(iface: str, *args: str, timeout: float = 4.0) -> tuple[bool, str]:
    """Run ``hostapd_cli -i <iface> <args...>``. Returns (ok, output)."""
    if stub_mode():
        return True, f"(stub) hostapd_cli -i {iface} {' '.join(args)}"
    result = run(["hostapd_cli", "-i", iface, *args],
                 timeout=timeout, source="hostapd_cli")
    out = (result.stdout or "").strip()
    if result.returncode != 0:
        return False, (result.stderr or out or "hostapd_cli failed").strip()
    # hostapd_cli prints "FAIL" (not a non-zero exit) when a command is
    # rejected — treat that as failure too.
    if out.upper().startswith("FAIL"):
        return False, f"hostapd_cli rejected: {out}"
    return True, out or "OK"


def deauthenticate(iface: str, mac: str) -> tuple[bool, str]:
    """Boot a client off ``iface`` by deauth (full re-auth required)."""
    if not _MAC_RE.match(mac):
        return False, f"{mac!r} is not a MAC address"
    ok, out = _cli(iface, "deauthenticate", mac.lower())
    return ok, (f"deauthenticated {mac} on {iface}" if ok else out)


def disassociate(iface: str, mac: str) -> tuple[bool, str]:
    """Boot a client off ``iface`` by disassociation (gentler than deauth)."""
    if not _MAC_RE.match(mac):
        return False, f"{mac!r} is not a MAC address"
    ok, out = _cli(iface, "disassociate", mac.lower())
    return ok, (f"disassociated {mac} on {iface}" if ok else out)


def reload(iface: str) -> tuple[bool, str]:
    """Tell the running hostapd to re-read its config file. Used by the
    impersonation rotation to swap the broadcast SSID live."""
    ok, out = _cli(iface, "reload")
    return ok, (f"hostapd reloaded on {iface}" if ok else out)


def list_stations(iface: str) -> list[str]:
    """MACs currently associated to ``iface`` (via ``all_sta``). Best-
    effort; empty list on any failure or in stub mode."""
    ok, out = _cli(iface, "all_sta")
    if not ok:
        return []
    return [ln.strip().lower() for ln in out.splitlines()
            if _MAC_RE.match(ln.strip())]
