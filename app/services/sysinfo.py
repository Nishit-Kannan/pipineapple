"""System information service.

Composes the dashboard status dict by calling into the tool layer. This
module is the only one routes import from for the Session 01 dashboard;
the route stays thin and the tool wrappers stay isolated.

Shape returned by :func:`get_system_status`::

    {
      "system": {
        "cpu_temp_c":      47.2,
        "memory":          {"total_bytes": ..., "available_bytes": ...,
                            "used_bytes": ..., "used_pct": 38.0},
        "uptime_seconds":  18234.7,
        "kernel":          "6.6.20-current-rpi-v8+",
        "model":           "Raspberry Pi 5 Model B Rev 1.0",
      },
      "interfaces": [
        {"name": "eth0", "state": "UP", "mac": "...",
         "addresses": ["192.168.8.224/24"], "mtu": 1500},
        ...
      ],
      "wireless": [
        {"name": "wlan-mon-2g", "mode": "monitor", "channel": 6,
         "frequency_mhz": 2437, "width_mhz": 20, "ssid": None,
         "txpower_dbm": 20.0, "driver": "mt76_usb",
         "signal_dbm": -54, "noise_dbm": None, "link_quality": 56},
        ...
      ],
      "reg_domain": "US",
    }
"""

from __future__ import annotations

import logging
from typing import Any

from app.tools import ethtool, iproute, iw, proc, vcgencmd

log = logging.getLogger(__name__)


def get_system_status() -> dict[str, Any]:
    """Build the full status dict for the dashboard."""
    return {
        "system":     _gather_system(),
        "interfaces": iproute.list_interfaces(),
        "wireless":   _gather_wireless(),
        "reg_domain": iw.get_reg_domain(),
    }


def _gather_system() -> dict[str, Any]:
    """Collect the system block (temp, memory, uptime, kernel, model)."""
    return {
        "cpu_temp_c":     vcgencmd.measure_temp_c(),
        "memory":         proc.read_memory_info(),
        "uptime_seconds": proc.read_uptime_seconds(),
        "kernel":         proc.read_kernel_version(),
        "model":          proc.read_pi_model(),
    }


def _gather_wireless() -> list[dict[str, Any]]:
    """Compose the wireless adapter list by joining iw + ethtool + /proc.

    ``iw dev`` gives us mode/channel/frequency/SSID/txpower. ``ethtool``
    gives us the driver name. ``/proc/net/wireless`` gives us live
    signal/noise/quality when the interface is associated or actively
    receiving in monitor mode.
    """
    devices = iw.list_wireless_devices()
    wireless_stats = proc.read_proc_net_wireless()

    enriched: list[dict[str, Any]] = []
    for dev in devices:
        name = dev["name"]
        stats = wireless_stats.get(name, {})
        enriched.append({
            **dev,
            "driver":       ethtool.get_driver(name),
            "signal_dbm":   stats.get("signal_dbm"),
            "noise_dbm":    stats.get("noise_dbm"),
            "link_quality": stats.get("link_quality"),
        })
    return enriched


def format_uptime(seconds: float | None) -> str:
    """Render a friendly uptime string for the template, e.g. ``"5h 04m"``."""
    if seconds is None:
        return "—"
    secs = int(seconds)
    days, secs = divmod(secs, 86400)
    hours, secs = divmod(secs, 3600)
    mins, secs = divmod(secs, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours:02d}h")
    parts.append(f"{mins:02d}m")
    return " ".join(parts)


def format_bytes(b: int | None) -> str:
    """Human-readable byte size, e.g. ``"3.1 GB"`` or ``"512 MB"``."""
    if b is None:
        return "—"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(b)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} {units[-1]}"
