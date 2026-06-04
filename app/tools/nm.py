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


def set_managed(iface: str, managed: bool) -> tuple[bool, str]:
    """Tell NM to release/reclaim an interface.

    Used when standing up the management AP on wlan0: NM has to let go
    of wlan0 before hostapd can take over. The orchestrator calls this
    with managed=False before starting hostapd, and managed=True after
    stopping it.
    """
    if stub_mode():
        return True, f"(stub) nmcli device set {iface} managed {'yes' if managed else 'no'}"
    state = "yes" if managed else "no"
    r = run(["nmcli", "device", "set", iface, "managed", state], timeout=5.0)
    if r.returncode == 0:
        return True, f"nmcli device set {iface} managed {state}"
    return False, f"nmcli set managed failed: {r.stderr.strip()}"


def wifi_scan(iface: str = "wlan0", rescan: bool = True) -> list[dict]:
    """Return nearby Wi-Fi networks via ``nmcli device wifi list``.

    Output shape per row::

        {"ssid": "...", "bssid": "...", "signal": 73, "security": "WPA2",
         "in_use": false, "freq_mhz": 2462, "rate_mbps": 270}
    """
    if stub_mode():
        return [
            {"ssid": "HomeWiFi",      "bssid": "AA:BB:CC:DD:EE:01", "signal": 86, "security": "WPA2", "in_use": False, "freq_mhz": 5180, "rate_mbps": 540},
            {"ssid": "NeighborGuest", "bssid": "AA:BB:CC:DD:EE:02", "signal": 51, "security": "WPA2", "in_use": False, "freq_mhz": 2437, "rate_mbps": 270},
            {"ssid": "iPhone-Nishit", "bssid": "AA:BB:CC:DD:EE:03", "signal": 78, "security": "WPA2", "in_use": False, "freq_mhz": 2437, "rate_mbps": 130},
        ]
    args = ["nmcli", "-t", "-f",
            "IN-USE,SSID,BSSID,SIGNAL,SECURITY,FREQ,RATE",
            "device", "wifi", "list", "ifname", iface]
    if rescan:
        args += ["--rescan", "yes"]
    r = run(args, timeout=15.0)
    if r.returncode != 0:
        log.warning("nmcli wifi list failed: %s", r.stderr.strip())
        return []
    out: list[dict] = []
    for line in r.stdout.splitlines():
        # nmcli terse format uses ':' separators. SSIDs and BSSIDs that
        # contain colons get \-escaped. Lightweight handling: split with
        # a re that respects backslash escapes.
        parts = _terse_split(line)
        if len(parts) < 7:
            continue
        in_use, ssid, bssid, signal, security, freq, rate = parts[:7]
        if not ssid:
            continue
        try:
            sig = int(signal) if signal else None
        except ValueError:
            sig = None
        try:
            freq_mhz = int(freq.split(" ")[0]) if freq else None
        except ValueError:
            freq_mhz = None
        try:
            rate_mbps = int(rate.split(" ")[0]) if rate else None
        except ValueError:
            rate_mbps = None
        out.append({
            "ssid": ssid,
            "bssid": bssid,
            "signal": sig,
            "security": security or "OPEN",
            "in_use": in_use == "*",
            "freq_mhz": freq_mhz,
            "rate_mbps": rate_mbps,
        })
    # Sort by signal strength descending
    out.sort(key=lambda d: d["signal"] or -999, reverse=True)
    return out


def _terse_split(line: str) -> list[str]:
    """Split an nmcli -t line on unescaped ':' separators."""
    parts: list[str] = []
    buf = []
    escape = False
    for ch in line:
        if escape:
            buf.append(ch)
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == ":":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def wifi_connect(ssid: str, password: str | None, iface: str = "wlan0") -> tuple[bool, str]:
    """Save a connection profile and connect.

    Creates the profile if it doesn't exist; reuses it if it does.
    Sets connection.autoconnect=yes so the Pi rejoins this network on
    every boot when wlan0 is free.
    """
    if stub_mode():
        return True, f"(stub) nmcli connect {iface} to {ssid}"
    args = ["nmcli", "device", "wifi", "connect", ssid, "ifname", iface]
    if password:
        args += ["password", password]
    r = run(args, timeout=30.0)
    if r.returncode != 0:
        return False, f"nmcli connect failed: {r.stderr.strip() or r.stdout.strip()}"
    # Make sure it'll auto-reconnect later
    run(["nmcli", "connection", "modify", ssid, "connection.autoconnect", "yes"], timeout=5.0)
    return True, f"connected {iface} to {ssid}"


def wifi_disconnect(iface: str = "wlan0") -> tuple[bool, str]:
    if stub_mode():
        return True, f"(stub) nmcli disconnect {iface}"
    r = run(["nmcli", "device", "disconnect", iface], timeout=10.0)
    if r.returncode == 0:
        return True, f"disconnected {iface}"
    return False, f"disconnect failed: {r.stderr.strip()}"


def list_saved_wifi() -> list[dict]:
    """Return saved Wi-Fi connection profiles via ``nmcli connection show``."""
    if stub_mode():
        return [
            {"name": "HomeWiFi", "ssid": "HomeWiFi", "autoconnect": True, "active": True},
        ]
    r = run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show"], timeout=5.0)
    if r.returncode != 0:
        return []
    out: list[dict] = []
    for line in r.stdout.splitlines():
        parts = _terse_split(line)
        if len(parts) < 3:
            continue
        name, ctype, device = parts[:3]
        if ctype != "802-11-wireless":
            continue
        # Look up autoconnect + active state
        r2 = run(["nmcli", "-t", "-f", "connection.autoconnect,GENERAL.STATE",
                  "connection", "show", name], timeout=3.0)
        autoconn = "yes"  # default
        active = False
        if r2.returncode == 0:
            for ln in r2.stdout.splitlines():
                if ln.startswith("connection.autoconnect:"):
                    autoconn = ln.split(":", 1)[1].strip()
                if ln.startswith("GENERAL.STATE:"):
                    active = "activated" in ln
        out.append({
            "name": name,
            "ssid": name,
            "autoconnect": autoconn == "yes",
            "active": active,
            "device": device or None,
        })
    return out


def forget_wifi(name: str) -> tuple[bool, str]:
    if stub_mode():
        return True, f"(stub) nmcli connection delete {name}"
    r = run(["nmcli", "connection", "delete", name], timeout=5.0)
    if r.returncode == 0:
        return True, f"deleted profile {name}"
    return False, f"delete failed: {r.stderr.strip()}"


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
