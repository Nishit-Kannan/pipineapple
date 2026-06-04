"""Wrapper for the ``iw`` command — wireless device information.

Session 01 uses only read-only ops: ``iw dev`` (list wireless interfaces
with mode/channel/frequency) and ``iw reg get`` (regulatory domain).

Session 03 will extend this with the mutating ops: ``iw dev <iface> set
type monitor`` / ``managed``, ``iw reg set``, etc. Keeping them in the
same module preserves the dependency rule that services don't shell out
directly.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.tools._common import run, stub_mode

log = logging.getLogger(__name__)


# ``iw dev`` is line-based, indented text. Each ``Interface <name>`` block
# is followed by a handful of ``\t<key> <value>`` lines.
_INTERFACE_RE = re.compile(r"^\s*Interface\s+(\S+)\s*$")
_FIELD_RE = re.compile(r"^\s+([a-z_ ]+?)\s+(.+?)\s*$")

# ``iw dev <iface> info`` shows the same data plus ``type managed/monitor/AP``
# more reliably. We parse both to maximise robustness.


_STUB_WIRELESS = [
    {
        "name":           "wlan0",
        "mode":           "managed",
        "channel":        None,
        "frequency_mhz":  None,
        "width_mhz":      None,
        "ssid":           None,
        "txpower_dbm":    None,
    },
    {
        "name":           "wlan-mon-2g",
        "mode":           "monitor",
        "channel":        6,
        "frequency_mhz":  2437,
        "width_mhz":      20,
        "ssid":           None,
        "txpower_dbm":    20.0,
    },
    {
        "name":           "wlan-mon-5g",
        "mode":           "monitor",
        "channel":        36,
        "frequency_mhz":  5180,
        "width_mhz":      20,
        "ssid":           None,
        "txpower_dbm":    20.0,
    },
    {
        "name":           "wlan-ap",
        "mode":           "managed",
        "channel":        None,
        "frequency_mhz":  None,
        "width_mhz":      None,
        "ssid":           None,
        "txpower_dbm":    20.0,
    },
]


def list_wireless_devices() -> list[dict[str, Any]]:
    """Return one dict per wireless interface known to the kernel.

    Shape per entry::

        {
          "name":           "wlan-mon-2g",
          "mode":           "monitor",       # or "managed", "AP", "P2P", ...
          "channel":        6,
          "frequency_mhz":  2437,
          "width_mhz":      20,               # channel width in MHz, if set
          "ssid":           None,             # only set in managed/AP modes
          "txpower_dbm":    20.0,
        }
    """
    if stub_mode():
        return list(_STUB_WIRELESS)

    result = run(["iw", "dev"], timeout=3.0)
    if result.returncode != 0:
        log.warning("iw dev failed: %s", result.stderr.strip())
        return []

    out: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in result.stdout.splitlines():
        m = _INTERFACE_RE.match(line)
        if m:
            if current is not None:
                out.append(current)
            current = {
                "name":          m.group(1),
                "mode":          None,
                "channel":       None,
                "frequency_mhz": None,
                "width_mhz":     None,
                "ssid":          None,
                "txpower_dbm":   None,
            }
            continue
        if current is None:
            continue

        # Indented "key: value" or "key value" fields
        stripped = line.strip()
        if stripped.startswith("type "):
            current["mode"] = stripped.removeprefix("type ").strip()
        elif stripped.startswith("ssid "):
            current["ssid"] = stripped.removeprefix("ssid ").strip()
        elif stripped.startswith("channel "):
            # e.g. "channel 6 (2437 MHz), width: 20 MHz, center1: 2437 MHz"
            m2 = re.match(
                r"channel\s+(\d+)\s*\((\d+)\s*MHz\)"
                r"(?:.*?width:\s*(\d+)\s*MHz)?",
                stripped,
            )
            if m2:
                current["channel"] = int(m2.group(1))
                current["frequency_mhz"] = int(m2.group(2))
                if m2.group(3):
                    current["width_mhz"] = int(m2.group(3))
        elif stripped.startswith("txpower "):
            # "txpower 20.00 dBm"
            m3 = re.match(r"txpower\s+([\d.]+)\s+dBm", stripped)
            if m3:
                current["txpower_dbm"] = float(m3.group(1))

    if current is not None:
        out.append(current)

    return out


def set_type(iface: str, mode: str) -> tuple[bool, str]:
    """Set the wireless interface type (monitor / managed / __ap).

    The interface must be down before the type change; we do *not*
    handle that here (the orchestrating service runs ``ip link set
    <iface> down`` first). Returns ``(ok, message)``.
    """
    if stub_mode():
        return True, f"(stub) set {iface} -> {mode}"
    if mode not in ("monitor", "managed", "ibss", "__ap"):
        return False, f"refusing unknown mode {mode!r}"
    result = run(["iw", "dev", iface, "set", "type", mode], timeout=4.0)
    if result.returncode == 0:
        return True, f"set {iface} to type {mode}"
    return False, f"iw set type failed: {result.stderr.strip() or result.stdout.strip()}"


def set_reg_domain(country_code: str) -> tuple[bool, str]:
    """Set the regulatory domain. Two-letter ISO 3166 country code."""
    if stub_mode():
        return True, f"(stub) set reg domain {country_code}"
    cc = country_code.strip().upper()
    if not (len(cc) == 2 and cc.isalpha()):
        return False, f"invalid country code {country_code!r}"
    result = run(["iw", "reg", "set", cc], timeout=3.0)
    if result.returncode == 0:
        return True, f"set regulatory domain to {cc}"
    return False, f"iw reg set failed: {result.stderr.strip()}"


def get_reg_domain() -> str | None:
    """Return the current regulatory domain (country code), e.g. ``"US"``.

    Parsed from the first ``country XX:`` line in ``iw reg get`` output.
    Returns ``None`` if iw isn't available or reports ``country 00`` (the
    world-default that's almost always wrong for 5 GHz capture).
    """
    if stub_mode():
        return "US"
    result = run(["iw", "reg", "get"], timeout=2.0)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        m = re.match(r"\s*country\s+([A-Z]{2}):", line)
        if m:
            code = m.group(1)
            return code if code != "00" else None
    return None
