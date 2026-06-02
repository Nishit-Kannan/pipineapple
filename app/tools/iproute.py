"""Wrapper for the iproute2 ``ip`` command.

For Session 01 we only need read-only operations: listing interfaces
with their MAC addresses, link state, and IP addresses. Session 09+ will
extend this with ``ip addr add`` for assigning the rogue-AP gateway IP.

We use ``ip -j`` to get JSON output rather than parsing the human-
readable text format. JSON support has been standard in iproute2 since
v4.6 (2016) and is reliable on Kali.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.tools._common import run, stub_mode

log = logging.getLogger(__name__)


_STUB_INTERFACES = [
    {
        "name": "lo",
        "state": "UNKNOWN",
        "mac": "00:00:00:00:00:00",
        "addresses": ["127.0.0.1/8", "::1/128"],
        "mtu": 65536,
    },
    {
        "name": "eth0",
        "state": "UP",
        "mac": "d8:3a:dd:ab:cd:ef",
        "addresses": ["192.168.8.224/24"],
        "mtu": 1500,
    },
    {
        "name": "wlan0",
        "state": "DOWN",
        "mac": "d8:3a:dd:ab:cd:f0",
        "addresses": [],
        "mtu": 1500,
    },
    {
        "name": "wlan-mon-2g",
        "state": "UP",
        "mac": "00:c0:ca:11:22:33",
        "addresses": [],
        "mtu": 1500,
    },
    {
        "name": "wlan-mon-5g",
        "state": "UP",
        "mac": "00:c0:ca:11:22:44",
        "addresses": [],
        "mtu": 1500,
    },
    {
        "name": "wlan-ap",
        "state": "DOWN",
        "mac": "00:c0:ca:11:22:55",
        "addresses": [],
        "mtu": 1500,
    },
]


def list_interfaces() -> list[dict[str, Any]]:
    """Return one dict per network interface.

    Shape per entry::

        {
          "name": "eth0",
          "state": "UP",                # operstate from `ip` JSON
          "mac":   "d8:3a:dd:ab:cd:ef",
          "addresses": ["192.168.8.224/24"],
          "mtu":   1500,
        }
    """
    if stub_mode():
        return list(_STUB_INTERFACES)

    result = run(["ip", "-j", "addr", "show"], timeout=3.0)
    if result.returncode != 0:
        log.warning("ip -j addr show failed: %s", result.stderr.strip())
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        log.error("ip -j produced invalid JSON: %s", e)
        return []

    out: list[dict[str, Any]] = []
    for entry in data:
        addresses: list[str] = []
        for ai in entry.get("addr_info", []):
            family = ai.get("family")
            local = ai.get("local")
            prefix = ai.get("prefixlen")
            if local and prefix is not None and family in ("inet", "inet6"):
                # Skip auto-generated link-local IPv6 (fe80::) unless we
                # have nothing else — keeps the UI uncluttered.
                if family == "inet6" and local.lower().startswith("fe80"):
                    continue
                addresses.append(f"{local}/{prefix}")
        out.append({
            "name":      entry.get("ifname", "?"),
            "state":     entry.get("operstate", "UNKNOWN"),
            "mac":       entry.get("address", ""),
            "addresses": addresses,
            "mtu":       entry.get("mtu"),
        })
    return out
