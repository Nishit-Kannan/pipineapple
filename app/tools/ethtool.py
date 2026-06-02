"""Wrapper for ``ethtool`` — used here to read the driver name per
interface.

This is the smallest tool wrapper in the project and lives in its own
module per the "one shell tool per module" rule. Future sessions may add
``ethtool -K`` or ``-G`` calls for tuning, in which case this is where
they go.
"""

from __future__ import annotations

import logging

from app.tools._common import run, stub_mode

log = logging.getLogger(__name__)


_STUB_DRIVERS = {
    "lo":           "",
    "eth0":         "bcm2835-eth",
    "wlan0":        "brcmfmac",
    "wlan-mon-2g":  "mt76_usb",
    "wlan-mon-5g":  "mt76_usb",
    "wlan-ap":      "mt76_usb",
}


def get_driver(iface: str) -> str | None:
    """Return the kernel driver name for ``iface``, or ``None`` if
    ethtool isn't available or the interface doesn't exist.

    Output of ``ethtool -i <iface>`` includes a ``driver: <name>`` line
    we parse out. For an MT7612U Alfa adapter we expect ``mt76_usb``.
    """
    if stub_mode():
        return _STUB_DRIVERS.get(iface)
    result = run(["ethtool", "-i", iface], timeout=2.0)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("driver:"):
            return line.split(":", 1)[1].strip() or None
    return None
