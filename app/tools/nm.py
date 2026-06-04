"""NetworkManager configuration tooling — unmanage offensive interfaces.

The "scalpel" approach to NetworkManager interference: write a conf.d
snippet telling NM to leave specific interfaces alone (by name pattern)
while continuing to manage everything else. Permanent across reboots,
non-disruptive to your home Wi-Fi management on wlan0.

Alternative: the sledgehammer (`systemctl stop NetworkManager wpa_supplicant`)
also exposed here, used by the "kill managers" one-time button.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.tools._common import run, stub_mode

log = logging.getLogger(__name__)

CONF_PATH = Path("/etc/NetworkManager/conf.d/99-pipineapple-unmanaged.conf")

# The patterns we unmanage. Anything matching wlan-mon-* (recon radios)
# or wlan-ap (rogue AP host) — same naming the udev rules establish.
UNMANAGED_PATTERNS = ["interface-name:wlan-mon-*", "interface-name:wlan-ap"]


def render_conf() -> str:
    body = [
        "# pipineapple — auto-generated. Tells NetworkManager to leave",
        "# the offensive interfaces alone while still managing wlan0",
        "# (home Wi-Fi upstream) and eth0.",
        "",
        "[keyfile]",
        f"unmanaged-devices={';'.join(UNMANAGED_PATTERNS)}",
        "",
    ]
    return "\n".join(body)


def write_conf() -> tuple[bool, str]:
    body = render_conf()
    target = CONF_PATH
    if stub_mode():
        target = Path("/tmp/pipineapple-nm-unmanaged.conf.preview")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    except PermissionError as e:
        return False, f"cannot write {target}: {e}"
    except OSError as e:
        return False, f"write failed: {e}"
    return True, f"wrote {target}"


def reload() -> tuple[bool, str]:
    """Tell NetworkManager to re-read its config."""
    if stub_mode():
        return True, "(stub) reloaded NetworkManager"
    # `nmcli general reload` is the proper way; falls back to systemctl reload
    # if nmcli isn't present.
    r = run(["nmcli", "general", "reload"], timeout=5.0)
    if r.returncode == 0:
        return True, "nmcli general reload"
    r2 = run(["systemctl", "reload", "NetworkManager"], timeout=5.0)
    if r2.returncode == 0:
        return True, "systemctl reload NetworkManager"
    return False, f"reload failed: {r.stderr.strip()} / {r2.stderr.strip()}"


def stop_managers() -> tuple[bool, str]:
    """The sledgehammer — stop NetworkManager + wpa_supplicant.

    Used as a one-time "airmon-ng check kill" equivalent. After this,
    NM is dead until reboot or `systemctl start NetworkManager`. wlan0
    will lose its upstream Wi-Fi connection if it was connected.
    """
    if stub_mode():
        return True, "(stub) stopped NetworkManager + wpa_supplicant"
    msgs = []
    for unit in ("NetworkManager", "wpa_supplicant"):
        r = run(["systemctl", "stop", unit], timeout=5.0)
        if r.returncode == 0:
            msgs.append(f"stopped {unit}")
        else:
            msgs.append(f"{unit}: {r.stderr.strip() or 'failed'}")
    return True, "; ".join(msgs)
