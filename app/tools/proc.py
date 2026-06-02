"""Read-only system information from /proc and /sys.

Pure filesystem reads, no subprocess. Everything here is fast (single
file open) and safe to call from a request handler.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.tools._common import stub_mode

log = logging.getLogger(__name__)

_PROC = Path("/proc")
_SYS = Path("/sys")


def read_cpu_temp_c() -> float | None:
    """Read CPU temperature in Celsius from /sys.

    On the Pi 5, ``thermal_zone0`` is the SoC sensor. The file content is
    a millidegree integer (e.g. ``47230`` for 47.23 °C). Returns ``None``
    if the file isn't present (non-Linux dev box, container without /sys
    bind-mounted, etc.).

    Note: ``vcgencmd measure_temp`` is the Pi-native alternative; the
    vcgencmd wrapper falls back to this function if vcgencmd isn't on
    PATH.
    """
    if stub_mode():
        return 48.7
    path = _SYS / "class" / "thermal" / "thermal_zone0" / "temp"
    try:
        return int(path.read_text().strip()) / 1000.0
    except (FileNotFoundError, ValueError) as e:
        log.debug("cpu temp read failed: %s", e)
        return None


def read_memory_info() -> dict | None:
    """Parse /proc/meminfo and return a small useful subset.

    Returns a dict with ``total_bytes``, ``available_bytes``,
    ``used_bytes``, and ``used_pct`` (a 0-100 float). The 'available'
    field in /proc/meminfo since kernel 3.14 is the best estimate of
    memory available for new allocations without swapping.
    """
    if stub_mode():
        total = 8 * 1024**3
        avail = int(total * 0.62)
        return {
            "total_bytes": total,
            "available_bytes": avail,
            "used_bytes": total - avail,
            "used_pct": round((total - avail) / total * 100, 1),
        }
    try:
        text = (_PROC / "meminfo").read_text()
    except FileNotFoundError:
        return None
    fields: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.strip().split()
        if not parts:
            continue
        # Value is in kilobytes per the meminfo format
        try:
            fields[key.strip()] = int(parts[0]) * 1024
        except ValueError:
            continue
    total = fields.get("MemTotal")
    avail = fields.get("MemAvailable")
    if total is None or avail is None:
        return None
    return {
        "total_bytes": total,
        "available_bytes": avail,
        "used_bytes": total - avail,
        "used_pct": round((total - avail) / total * 100, 1),
    }


def read_uptime_seconds() -> float | None:
    """Return system uptime in seconds, parsed from /proc/uptime.

    The file contains two floats: total uptime and total idle CPU time.
    We only care about the first.
    """
    if stub_mode():
        return 18234.7
    try:
        first, _, _ = (_PROC / "uptime").read_text().strip().partition(" ")
        return float(first)
    except (FileNotFoundError, ValueError):
        return None


def read_kernel_version() -> str | None:
    """Return the running kernel's release string, e.g. ``6.6.20-current-rpi-v8+``.

    Equivalent to ``uname -r`` but read directly from /proc to avoid a
    subprocess.
    """
    if stub_mode():
        return "6.6.20-current-rpi-v8+"
    try:
        # /proc/sys/kernel/osrelease is the canonical source
        return (_PROC / "sys" / "kernel" / "osrelease").read_text().strip()
    except FileNotFoundError:
        return None


def read_pi_model() -> str | None:
    """Return the Pi model string from the device tree.

    On Raspberry Pi OS / Kali ARM this file exists at
    ``/proc/device-tree/model`` with content like ``Raspberry Pi 5 Model
    B Rev 1.0``. The string includes a trailing null byte we strip.
    Returns ``None`` on non-Pi hosts.
    """
    if stub_mode():
        return "Raspberry Pi 5 Model B Rev 1.0"
    try:
        raw = (_PROC / "device-tree" / "model").read_bytes()
    except FileNotFoundError:
        return None
    return raw.rstrip(b"\x00").decode("utf-8", errors="replace").strip() or None


def read_proc_net_wireless() -> dict[str, dict]:
    """Parse /proc/net/wireless, keyed by interface name.

    Format is two header lines followed by one line per wireless
    interface. Useful fields per interface: link quality, signal level
    (dBm), noise level (dBm). Only populated meaningfully when the
    interface is associated or in monitor mode receiving frames.

    Returns ``{}`` if the file doesn't exist (no wireless stack on this
    host).
    """
    if stub_mode():
        return {
            "wlan-mon-2g": {"signal_dbm": -54, "noise_dbm": None, "link_quality": 56},
            "wlan-mon-5g": {"signal_dbm": -67, "noise_dbm": None, "link_quality": 43},
            "wlan-ap":     {"signal_dbm": None, "noise_dbm": None, "link_quality": None},
        }
    try:
        text = (_PROC / "net" / "wireless").read_text()
    except FileNotFoundError:
        return {}
    out: dict[str, dict] = {}
    # Skip two header lines
    for line in text.splitlines()[2:]:
        # Format: " iface: status link level noise nwid crypt frag retry misc beacon"
        name, _, rest = line.partition(":")
        parts = rest.split()
        if len(parts) < 4:
            continue
        # Values may have trailing dots (legacy iw_handler format)
        def _f(s: str) -> float | None:
            try:
                return float(s.rstrip("."))
            except ValueError:
                return None
        out[name.strip()] = {
            "link_quality": _f(parts[1]),
            "signal_dbm":   _f(parts[2]),
            "noise_dbm":    _f(parts[3]),
        }
    return out
