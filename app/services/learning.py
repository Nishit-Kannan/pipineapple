"""Learning Centre content.

The Learning Centre is a curriculum-as-feature: each console command the UI
wraps is documented here with its purpose, example output, and the UI surface
it backs. Content accretes session by session — when a new session introduces
new tools, either a new topic section is appended below, or commands are
added to an existing section (with the section's ``added_in_session`` value
updated to the latest one that touched it).

Shape per section::

    {
        "id":               "kebab-case-anchor",
        "title":            "Display title",
        "added_in_session": 1,
        "intro":            "One- or two-sentence framing of the topic.",
        "ui_reference":     "Where this data shows up in the UI.",
        "wrapper_modules":  ["app/tools/foo.py", ...],
        "commands": [
            {
                "command":        "the literal shell command",
                "description":    "What it does and why we wrap it.",
                "example_output": "trimmed sample output (optional)",
                "notes":          "Caveats, quirks, fallback behaviour (optional).",
            },
            ...
        ],
    }
"""

from __future__ import annotations

from typing import Any

LEARNING_SECTIONS: list[dict[str, Any]] = [
    # ------------------------------------------------------------------
    # Session 01 — System status
    # ------------------------------------------------------------------
    {
        "id": "system-status",
        "title": "System & status",
        "added_in_session": 1,
        "intro": (
            "Reading CPU temperature, memory, uptime, kernel version, and "
            "Pi model from /proc, /sys, and the Pi-native vcgencmd utility. "
            "These values back the Dashboard's stat cards."
        ),
        "ui_reference": "Dashboard → CPU temp / Memory / Uptime stat cards",
        "wrapper_modules": ["app/tools/proc.py", "app/tools/vcgencmd.py"],
        "commands": [
            {
                "command": "vcgencmd measure_temp",
                "description": (
                    "Pi-native SoC temperature read. Fastest and most accurate "
                    "source on Pi hardware. The wrapper parses the 'temp=' "
                    "prefix and the trailing °C marker."
                ),
                "example_output": "temp=47.2'C",
                "notes": (
                    "Falls back to /sys/class/thermal/thermal_zone0/temp if "
                    "vcgencmd isn't on PATH — common in containers or on "
                    "non-Pi Linux dev boxes. Some hardened systems require "
                    "the user to be in the 'video' group."
                ),
            },
            {
                "command": "cat /sys/class/thermal/thermal_zone0/temp",
                "description": (
                    "Standard Linux thermal interface. Returns the value in "
                    "millidegrees Celsius — divide by 1000 to get °C."
                ),
                "example_output": "47230",
                "notes": "Works on any Linux box, not just Pi.",
            },
            {
                "command": "cat /proc/meminfo | head -6",
                "description": (
                    "Memory statistics. The wrapper reads MemTotal and "
                    "MemAvailable; the dashboard shows the used percentage "
                    "as (Total − Available) / Total."
                ),
                "example_output": (
                    "MemTotal:        8438268 kB\n"
                    "MemFree:         5234508 kB\n"
                    "MemAvailable:    7102348 kB\n"
                    "Buffers:           45612 kB\n"
                    "Cached:          1832104 kB\n"
                    "SwapCached:            0 kB"
                ),
                "notes": (
                    "MemAvailable (since kernel 3.14) is a better 'free' "
                    "estimate than MemFree alone because it accounts for "
                    "reclaimable caches."
                ),
            },
            {
                "command": "cat /proc/uptime",
                "description": (
                    "First value is wall-clock seconds since boot. Second is "
                    "total CPU idle time, which we don't use."
                ),
                "example_output": "12345.67 4321.89",
                "notes": "Formatted as 'NNh MMm' by the format_uptime helper.",
            },
            {
                "command": "cat /proc/sys/kernel/osrelease",
                "description": (
                    "Same string as `uname -r`, read directly from /proc to "
                    "avoid a subprocess."
                ),
                "example_output": "6.12.x-current-rpi-v8+",
            },
            {
                "command": "cat /proc/device-tree/model; echo",
                "description": (
                    "Pi model string from the device tree. The trailing "
                    "'; echo' adds a newline because the file content has a "
                    "trailing null byte."
                ),
                "example_output": "Raspberry Pi 5 Model B Rev 1.0",
                "notes": (
                    "Returns nothing on non-Pi hardware. The wrapper strips "
                    "the null byte and returns None when this file is absent."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 01 — Network interfaces
    # ------------------------------------------------------------------
    {
        "id": "network-interfaces",
        "title": "Network interfaces",
        "added_in_session": 1,
        "intro": (
            "Listing network interfaces, their MAC addresses, link state, and "
            "assigned IP addresses. The wrapper uses ip -j (JSON output) for "
            "reliable parsing; the text form is for human inspection."
        ),
        "ui_reference": "Dashboard → Network interfaces table",
        "wrapper_modules": ["app/tools/iproute.py"],
        "commands": [
            {
                "command": "ip addr show",
                "description": (
                    "Human-readable form. What you'd normally use at the "
                    "console to check interface state."
                ),
                "example_output": (
                    "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 ...\n"
                    "    link/loopback 00:00:00:00:00:00\n"
                    "    inet 127.0.0.1/8 scope host lo\n"
                    "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 ...\n"
                    "    link/ether d8:3a:dd:ab:cd:ef brd ff:ff:ff:ff:ff:ff\n"
                    "    inet 192.168.8.224/24 brd 192.168.8.255 scope global"
                ),
            },
            {
                "command": "ip -j addr show",
                "description": (
                    "JSON form — what app/tools/iproute.py actually parses. "
                    "Each entry has ifname, address (MAC), operstate, mtu, "
                    "and an addr_info list."
                ),
                "example_output": (
                    '[{"ifindex":1,"ifname":"lo","operstate":"UNKNOWN",'
                    '"address":"00:00:00:00:00:00","mtu":65536,'
                    '"addr_info":[{"family":"inet","local":"127.0.0.1",'
                    '"prefixlen":8,...}]}, ...]'
                ),
                "notes": (
                    "JSON support has been standard in iproute2 since v4.6 "
                    "(2016). The wrapper skips link-local IPv6 (fe80::) for "
                    "cleaner UI display."
                ),
            },
            {
                "command": "ip -j addr show | python3 -m json.tool",
                "description": "Pretty-print the JSON for easier reading by hand.",
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 01 — Wireless radios
    # ------------------------------------------------------------------
    {
        "id": "wireless-radios",
        "title": "Wireless radios",
        "added_in_session": 1,
        "intro": (
            "Wireless interface details — mode (managed/monitor/AP), "
            "channel, frequency, SSID, transmit power, and live signal/noise "
            "statistics. Three commands feed this section of the dashboard: "
            "iw dev (interface enumeration), iw reg get (regulatory domain), "
            "and /proc/net/wireless (live signal stats)."
        ),
        "ui_reference": (
            "Dashboard → Wireless radios table + Reg domain stat card"
        ),
        "wrapper_modules": ["app/tools/iw.py", "app/tools/proc.py"],
        "commands": [
            {
                "command": "iw dev",
                "description": (
                    "List all wireless interfaces known to the kernel, with "
                    "their current mode, channel, frequency, SSID (if "
                    "associated), and tx power. Text format, parsed by regex "
                    "in the wrapper."
                ),
                "example_output": (
                    "phy#0\n"
                    "\tInterface wlan0\n"
                    "\t\tifindex 3\n"
                    "\t\twdev 0x1\n"
                    "\t\taddr d8:3a:dd:ab:cd:ef\n"
                    "\t\ttype managed\n"
                    "\t\tchannel 36 (5180 MHz), width: 20 MHz, center1: 5180 MHz\n"
                    "\t\ttxpower 31.00 dBm"
                ),
                "notes": (
                    "New phy/Interface blocks appear when you plug in "
                    "additional adapters. For Alfa AWUS036ACMs the driver "
                    "is mt76_usb (visible via ethtool)."
                ),
            },
            {
                "command": "iw reg get",
                "description": (
                    "Show the current regulatory domain (country code). The "
                    "country code controls which channels are usable for "
                    "transmit on each band — 5 GHz especially is heavily "
                    "constrained by jurisdiction."
                ),
                "example_output": (
                    "global\n"
                    "country IN: DFS-JP\n"
                    "\t(2402 - 2482 @ 40), (N/A, 20), (N/A)\n"
                    "\t(5170 - 5250 @ 80), (N/A, 23), (N/A)\n"
                    "\t(5250 - 5330 @ 80), (N/A, 23), (0 ms), DFS\n"
                    "\t..."
                ),
                "notes": (
                    "If you see 'country 00:', the regulatory domain wasn't "
                    "set — the dashboard's Reg domain card warns about this. "
                    "Set it with `sudo iw reg set <CC>` where <CC> is a "
                    "two-letter ISO 3166 country code (e.g. IN, US, GB)."
                ),
            },
            {
                "command": "sudo iw reg set IN",
                "description": (
                    "Set the regulatory domain to India. Required to open "
                    "the country-specific channel set and transmit power "
                    "envelope. Done in Session 01."
                ),
                "notes": (
                    "Setting reg domain is per-boot unless persisted via the "
                    "REGDOMAIN line in /etc/default/crda (or its Pi OS "
                    "equivalent). For lab use, re-running this each boot "
                    "is fine."
                ),
            },
            {
                "command": "cat /proc/net/wireless",
                "description": (
                    "Live signal, noise, and link quality stats per wireless "
                    "interface. Only populated when the interface is "
                    "associated (managed mode) or actively receiving frames "
                    "(monitor mode)."
                ),
                "example_output": (
                    "Inter-| sta-|   Quality        |   Discarded packets ...\n"
                    " face | tus | link level noise |  nwid  crypt   ...\n"
                    " wlan0: 0000   56.   -54.   -256.        0      0   ..."
                ),
                "notes": (
                    "Three-column format: link quality / signal level (dBm) / "
                    "noise level (dBm). Values may have trailing dots "
                    "(legacy iw_handler format) — the wrapper strips them."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 01 — Driver detection
    # ------------------------------------------------------------------
    {
        "id": "driver-detection",
        "title": "Driver detection",
        "added_in_session": 1,
        "intro": (
            "Identifying the kernel driver behind each network interface. "
            "Useful for knowing what hardware quirks to expect — Alfa "
            "AWUS036ACM = mt76_usb, Pi 5 onboard radio = brcmfmac, Pi 5 "
            "Ethernet = bcmgenet (or similar Broadcom-derived driver)."
        ),
        "ui_reference": "Dashboard → Wireless radios table → Driver column",
        "wrapper_modules": ["app/tools/ethtool.py"],
        "commands": [
            {
                "command": "ethtool -i wlan0",
                "description": (
                    "Report the kernel driver and firmware info for an "
                    "interface. The wrapper only grabs the 'driver:' line for "
                    "the dashboard."
                ),
                "example_output": (
                    "driver: brcmfmac\n"
                    "version: 7.45.x\n"
                    "firmware-version: 01-...\n"
                    "bus-info: mmc1:0001:1\n"
                    "supports-statistics: yes\n"
                    "supports-test: no\n"
                    "supports-eeprom-access: no\n"
                    "supports-register-dump: no\n"
                    "supports-priv-flags: no"
                ),
            },
            {
                "command": "ethtool -i eth0",
                "description": "Driver for the Pi 5's gigabit Ethernet — Broadcom-derived but Pi-specific.",
                "example_output": "driver: bcmgenet\n...",
            },
        ],
    },
]


def get_sections() -> list[dict[str, Any]]:
    """Return the full list of Learning Centre sections."""
    return LEARNING_SECTIONS
