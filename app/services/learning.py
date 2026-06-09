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
    # Session 02 — Subprocess inspection & signals
    # ------------------------------------------------------------------
    {
        "id": "subprocess-signals",
        "title": "Subprocesses & signals",
        "added_in_session": 2,
        "intro": (
            "Inspecting and controlling long-running subprocesses from "
            "the shell. The JobManager service (app/services/job_manager.py) "
            "wraps these same operations — start a process, watch its state "
            "via /proc, signal it cleanly to stop. Worth running these by "
            "hand against a JobManager-spawned job so the wrapper feels "
            "transparent."
        ),
        "ui_reference": (
            "Backs every long-running tool in later sessions (airodump, "
            "hostapd, aireplay, hashcat). Exposed via /debug/job/* in "
            "DEBUG mode for Session 02 testing."
        ),
        "wrapper_modules": ["app/services/job_manager.py"],
        "commands": [
            {
                "command": "ps -p <pid>",
                "description": (
                    "Quick info about one process: PID, TTY, time, command. "
                    "Pass -fp for a fuller view (user, parent PID, start "
                    "time, full command line)."
                ),
                "example_output": (
                    "  PID TTY          TIME CMD\n"
                    " 2678 ?        00:00:00 sleep"
                ),
            },
            {
                "command": "ps -fp <pid>",
                "description": "Full format: user, parent PID, start time, full argv.",
                "example_output": (
                    "UID          PID    PPID  C STIME TTY          TIME CMD\n"
                    "pi-lab      2678    2641  0 12:30 ?        00:00:00 sleep 20"
                ),
                "notes": "PPID points at the Flask process that launched the job.",
            },
            {
                "command": "ps -ef --forest | grep -E 'flask|python|pipineapple' | head -20",
                "description": (
                    "Process tree filtered around the PiPineapple Flask "
                    "process. Useful for seeing parent → child → grandchild "
                    "relationships when JobManager launches tools."
                ),
                "notes": "On Pi OS, `pstree -p $(pgrep -f run.py)` is a cleaner alternative.",
            },
            {
                "command": "ps -fLp <pid>",
                "description": (
                    "Show all threads of a process (one row per LWP — "
                    "lightweight process). Useful for seeing the reader "
                    "thread JobManager spawns per running job."
                ),
            },
            {
                "command": "cat /proc/<pid>/status",
                "description": (
                    "Kernel-level state: State (R/S/D/Z/T), VmRSS (resident "
                    "memory), Threads, signal masks. State 'S' is "
                    "interruptible sleep (waiting for IO or syscall) — "
                    "what sleep 20 looks like most of its life."
                ),
                "example_output": (
                    "Name:   sleep\n"
                    "State:  S (sleeping)\n"
                    "Tgid:   2678\n"
                    "Pid:    2678\n"
                    "PPid:   2641\n"
                    "..."
                ),
            },
            {
                "command": "cat /proc/<pid>/cmdline; echo",
                "description": (
                    "Original argv, with null bytes between arguments. The "
                    "; echo adds a newline because /proc/<pid>/cmdline "
                    "has no trailing newline."
                ),
                "example_output": "sleep\\x0020",
                "notes": "Use `tr '\\0' ' '` to make the argv human-readable.",
            },
            {
                "command": "ls -l /proc/<pid>/fd/",
                "description": (
                    "Open file descriptors. Each entry is a symlink to the "
                    "actual file/socket/pipe. For a JobManager-spawned job, "
                    "you'll see stdin (closed or pipe), stdout (pipe — "
                    "what the reader thread drains), stderr (the same pipe "
                    "since we use stderr=STDOUT)."
                ),
                "example_output": (
                    "lr-x------ 1 pi-lab pi-lab 64 Jun  4 12:30 0 -> /dev/null\n"
                    "l-wx------ 1 pi-lab pi-lab 64 Jun  4 12:30 1 -> pipe:[12345]\n"
                    "l-wx------ 1 pi-lab pi-lab 64 Jun  4 12:30 2 -> pipe:[12345]"
                ),
            },
            {
                "command": "kill -l",
                "description": (
                    "List all signal names and numbers the kernel supports. "
                    "The four we care about in practice: TERM (15, graceful), "
                    "KILL (9, uncatchable), INT (2, Ctrl-C), HUP (1, "
                    "reload-config convention)."
                ),
                "example_output": (
                    " 1) SIGHUP   2) SIGINT   3) SIGQUIT  9) SIGKILL\n"
                    "15) SIGTERM 17) SIGCHLD ..."
                ),
            },
            {
                "command": "kill -TERM <pid>",
                "description": (
                    "Polite stop. The target gets a SIGTERM and has the "
                    "opportunity to clean up (flush buffers, close sockets, "
                    "remove tmpfiles) before exiting. This is what "
                    "JobManager.stop_job tries first."
                ),
                "notes": (
                    "Equivalent to `kill <pid>` with no flag. Most programs "
                    "respect SIGTERM; some (rare) install a handler that "
                    "ignores it."
                ),
            },
            {
                "command": "kill -KILL <pid>",
                "description": (
                    "Forced stop. The kernel kills the process immediately; "
                    "no chance to clean up. JobManager escalates to SIGKILL "
                    "if SIGTERM hasn't taken effect within the grace window."
                ),
                "notes": (
                    "Equivalent to `kill -9 <pid>`. Cannot be caught, blocked, "
                    "or ignored — even by processes running as root."
                ),
            },
            {
                "command": "lsof -p <pid>",
                "description": (
                    "Lists all open files, sockets, and pipes for a process. "
                    "Heavier than ls /proc/<pid>/fd because it adds type "
                    "labels (REG/PIPE/IPv4/CHR/DIR) and inode info."
                ),
                "notes": (
                    "`sudo apt install lsof` if not already present. Slow on "
                    "the Pi if you don't filter — usually pair with `-iTCP` "
                    "or `-iUDP` for network-only output."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 04 — Monitor mode & adapter state
    # ------------------------------------------------------------------
    {
        "id": "monitor-mode",
        "title": "Monitor mode & adapter state",
        "added_in_session": 4,
        "intro": (
            "Putting a wireless interface into monitor mode is the gateway "
            "to recon and capture. Three commands per toggle: down, set "
            "type, up. The driver refuses to change interface type while "
            "the interface is up, so the bracketing matters."
        ),
        "ui_reference": (
            "Settings → Adapter Management → Monitor / Managed buttons. "
            "The three-step sequence shows up in the Command Stream tagged "
            "'tool' when you click the button."
        ),
        "wrapper_modules": [
            "app/tools/iw.py", "app/tools/iproute.py",
            "app/services/adapters.py",
        ],
        "commands": [
            {
                "command": "sudo ip link set <iface> down",
                "description": (
                    "Bring the interface down before changing its type. "
                    "Most drivers reject a type change on an up interface "
                    "with an EBUSY error."
                ),
            },
            {
                "command": "sudo iw dev <iface> set type monitor",
                "description": (
                    "Reconfigure the radio to monitor mode. The driver "
                    "stops filtering frames by BSSID/destination and starts "
                    "passing every 802.11 frame it can decode up to "
                    "userspace, prepended with a radiotap header (signal "
                    "strength, channel, antenna, etc.)."
                ),
                "notes": (
                    "Other valid types: managed (default), ibss (ad-hoc), "
                    "__ap (in-kernel AP mode, not what hostapd uses)."
                ),
            },
            {
                "command": "sudo ip link set <iface> up",
                "description": "Bring the interface back up so the radio starts receiving.",
            },
            {
                "command": "iw dev <iface> info",
                "description": (
                    "Verify the new mode. After a monitor-mode toggle, the "
                    "'type' line should read 'monitor'. Compare to the same "
                    "command before the toggle to see exactly what changed."
                ),
                "example_output": (
                    "Interface wlan-mon-2g\n"
                    "\tifindex 5\n"
                    "\twdev 0x100000001\n"
                    "\taddr 00:c0:ca:11:22:33\n"
                    "\ttype monitor\n"
                    "\twiphy 1\n"
                    "\tchannel 0 (0 MHz), width: 20 MHz (no HT), center1: 0 MHz\n"
                    "\ttxpower 20.00 dBm"
                ),
            },
            {
                "command": "iw dev <iface> set channel <N>",
                "description": (
                    "Lock a monitor-mode adapter to a specific channel "
                    "(no hopping). Required for targeted handshake capture "
                    "in Phase C."
                ),
                "notes": (
                    "Channel numbers map to frequencies: 2.4 GHz channels "
                    "1-11 (US) / 1-13 (other), 5 GHz channels 36-165 "
                    "depending on reg domain and DFS rules."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 04 — udev sticky names
    # ------------------------------------------------------------------
    {
        "id": "udev-sticky-names",
        "title": "udev sticky names",
        "added_in_session": 4,
        "intro": (
            "udev rules pin a stable interface name (wlan-mon-2g, "
            "wlan-mon-5g, wlan-ap) to a specific MAC address, so the "
            "kernel renames the interface before userspace sees it. "
            "Without this, plug order and detection timing decide which "
            "Alfa becomes wlan1 vs wlan2 on each reboot."
        ),
        "ui_reference": (
            "Settings → Adapter Management → 'Generate & apply udev rules' "
            "button. The file written is /etc/udev/rules.d/"
            "99-pipineapple-adapters.rules."
        ),
        "wrapper_modules": ["app/tools/udev.py", "app/services/adapters.py"],
        "commands": [
            {
                "command": "cat /etc/udev/rules.d/99-pipineapple-adapters.rules",
                "description": "Inspect the rules file PiPineapple generated.",
                "example_output": (
                    'SUBSYSTEM=="net", ACTION=="add", '
                    'ATTR{address}=="00:c0:ca:11:22:33", NAME="wlan-mon-2g"\n'
                    'SUBSYSTEM=="net", ACTION=="add", '
                    'ATTR{address}=="00:c0:ca:44:55:66", NAME="wlan-mon-5g"\n'
                    'SUBSYSTEM=="net", ACTION=="add", '
                    'ATTR{address}=="00:c0:ca:77:88:99", NAME="wlan-ap"'
                ),
                "notes": (
                    "MAC addresses MUST be lowercase — udev string-compares "
                    "against ATTR{address} which is always lowercase in "
                    "/sys/class/net/*/address."
                ),
            },
            {
                "command": "sudo udevadm control --reload-rules",
                "description": (
                    "Tell udev to re-read its rules files. Required after "
                    "editing rules. Doesn't apply them to currently-attached "
                    "devices — only affects future hotplug events."
                ),
            },
            {
                "command": "sudo udevadm trigger",
                "description": (
                    "Re-process every device's udev attributes. In theory "
                    "this could rename a live interface. In practice with "
                    "wireless drivers it usually doesn't — you need to "
                    "unplug+replug the adapter or reboot for the new name "
                    "to land."
                ),
            },
            {
                "command": "udevadm info /sys/class/net/<iface>",
                "description": (
                    "Show every udev attribute for an interface. Useful "
                    "when writing a new rule: see exactly what ATTR{...} "
                    "values are available to match on."
                ),
                "notes": (
                    "ID_VENDOR_ID and ID_MODEL_ID (USB devices) are good "
                    "alternative match keys if you don't want to hardcode "
                    "the MAC — though for our three identical Alfas, MAC "
                    "is the only way to distinguish them."
                ),
            },
            {
                "command": "ls /sys/class/net/",
                "description": (
                    "List every interface the kernel knows about. Confirm "
                    "your new sticky names appear here after reboot."
                ),
                "example_output": "eth0  lo  wlan-ap  wlan-mon-2g  wlan-mon-5g  wlan0",
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 04 — NetworkManager control
    # ------------------------------------------------------------------
    {
        "id": "network-manager",
        "title": "NetworkManager control",
        "added_in_session": 4,
        "intro": (
            "NetworkManager will silently flip offensive radios back to "
            "managed mode if you don't disarm it. Two ways: the scalpel "
            "(unmanaging config that tells NM to ignore specific "
            "interface names) or the sledgehammer (systemctl stop "
            "NetworkManager wpa_supplicant, equivalent of 'airmon-ng "
            "check kill'). PiPineapple uses the scalpel by default."
        ),
        "ui_reference": (
            "Settings → Adapter Management → 'Generate & apply NM config' "
            "(scalpel) and 'Stop NM + wpa_supplicant' (sledgehammer)."
        ),
        "wrapper_modules": ["app/tools/nm.py", "app/services/adapters.py"],
        "commands": [
            {
                "command": "cat /etc/NetworkManager/conf.d/99-pipineapple-unmanaged.conf",
                "description": (
                    "Inspect the unmanaging config. The pattern matches "
                    "any interface starting with 'wlan-mon-' plus the "
                    "single 'wlan-ap' interface."
                ),
                "example_output": (
                    "[keyfile]\n"
                    "unmanaged-devices=interface-name:wlan-mon-*;"
                    "interface-name:wlan-ap"
                ),
            },
            {
                "command": "sudo nmcli general reload",
                "description": "Tell NM to re-read its config. Idempotent, no service restart needed.",
                "notes": (
                    "If nmcli isn't available, fall back to "
                    "`sudo systemctl reload NetworkManager`."
                ),
            },
            {
                "command": "nmcli device status",
                "description": (
                    "Show every interface NM knows about and its state. "
                    "After the unmanaging config takes effect, offensive "
                    "radios should show STATE 'unmanaged' instead of "
                    "'disconnected'/'connected'."
                ),
                "example_output": (
                    "DEVICE        TYPE      STATE         CONNECTION\n"
                    "wlan0         wifi      connected     HomeWiFi\n"
                    "eth0          ethernet  connected     Wired\n"
                    "wlan-mon-2g   wifi      unmanaged     --\n"
                    "wlan-mon-5g   wifi      unmanaged     --\n"
                    "wlan-ap       wifi      unmanaged     --"
                ),
            },
            {
                "command": "sudo systemctl stop NetworkManager wpa_supplicant",
                "description": (
                    "The sledgehammer — kill both services until next boot. "
                    "Equivalent to `airmon-ng check kill`. Use this when "
                    "the unmanaging config isn't enough (some legacy Karma "
                    "attacks need NM completely out of the picture)."
                ),
                "notes": (
                    "wlan0 will lose its home Wi-Fi connection. Ensure "
                    "you're on Ethernet for the management plane before "
                    "running this."
                ),
            },
            {
                "command": "sudo systemctl start NetworkManager",
                "description": "Bring NM back. The unmanaging config still applies; offensive radios stay untouched.",
            },
            {
                "command": "ps -ef | grep -E 'NetworkManager|wpa_supplicant' | grep -v grep",
                "description": (
                    "Verify whether the supervisors are running. Useful "
                    "before a session to confirm you're starting from a "
                    "known state."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 04.5 — Auth & access control
    # ------------------------------------------------------------------
    {
        "id": "auth-access",
        "title": "Auth & access control",
        "added_in_session": 4.5,
        "intro": (
            "Inspecting and recovering the authentication + access "
            "control state. The auth password is stored as a salted "
            "scrypt hash in $DATA_DIR/auth.json — file presence acts "
            "as the 'platform initialised' signal. The deny-list CIDRs "
            "live in $DATA_DIR/access_control.json. Both are JSON, "
            "owner-readable only."
        ),
        "ui_reference": (
            "Settings → Security (change password, deny-list manager). "
            "Login page at /login, first-run setup at /setup."
        ),
        "wrapper_modules": [
            "app/services/auth.py",
            "app/services/access_control.py",
        ],
        "commands": [
            {
                "command": "cat $PIPINEAPPLE_DATA_DIR/auth.json",
                "description": (
                    "Inspect the stored hash. The hash format is "
                    "'scrypt:<work-factor>:<salt>:<hash>'. Cannot be "
                    "reversed — werkzeug salts and runs scrypt with a "
                    "deliberately slow work factor."
                ),
                "example_output": (
                    "{\"password_hash\": \"scrypt:32768:8:1$abc...$def...\", "
                    "\"set_at\": 1780600000.123}"
                ),
            },
            {
                "command": "rm $PIPINEAPPLE_DATA_DIR/auth.json",
                "description": (
                    "Emergency password reset. Removes the password "
                    "file; the next page load redirects to /setup. "
                    "Requires shell access to the Pi (so you must "
                    "already control the machine to use this)."
                ),
                "notes": (
                    "There's no 'forgot password' email flow — this is "
                    "single-user, single-machine. Shell access IS the "
                    "recovery path."
                ),
            },
            {
                "command": "cat $PIPINEAPPLE_DATA_DIR/access_control.json",
                "description": "Inspect the configured deny-list CIDRs.",
                "example_output": (
                    "{\n"
                    "  \"deny_cidrs\": [\n"
                    "    \"10.0.0.0/24\"\n"
                    "  ]\n"
                    "}"
                ),
            },
            {
                "command": "python3 -c \"from werkzeug.security import generate_password_hash; print(generate_password_hash('your-pw'))\"",
                "description": (
                    "Manually generate a hash. Useful if you want to "
                    "set the password via shell rather than the UI — "
                    "drop the hash into auth.json by hand."
                ),
            },
            {
                "command": "python3 -c \"from werkzeug.security import check_password_hash; print(check_password_hash('STORED_HASH', 'guess'))\"",
                "description": "Verify a guess against a stored hash. Useful for forensics or debugging.",
            },
            {
                "command": "ip a | grep 'inet '",
                "description": (
                    "Find your own IP addresses before configuring the "
                    "deny-list. Make sure you don't deny-list a subnet "
                    "that contains the Mac you browse from — you'll "
                    "lock yourself out."
                ),
                "notes": (
                    "Localhost (127.0.0.0/8) is always allowed "
                    "regardless of deny-list, so a Pi-shell `curl` is "
                    "always your fallback path even if you mis-configure."
                ),
            },
            {
                "command": "curl -i http://pi-lab.local:5000/ --interface <local-iface>",
                "description": (
                    "Test access from a specific local interface. With "
                    "the rogue AP up on wlan-ap, you can curl from "
                    "--interface wlan-ap to simulate a victim client "
                    "and confirm the deny-list returns 403."
                ),
                "notes": (
                    "Output: HTTP/1.1 403 FORBIDDEN if the source IP "
                    "is in a deny CIDR; otherwise the normal HTML "
                    "response (or 302 to /login if not authenticated)."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 04.6 — Management AP + Wi-Fi client mode
    # ------------------------------------------------------------------
    {
        "id": "mgmt-ap-client-mode",
        "title": "Management AP & client mode (wlan0)",
        "added_in_session": 4.6,
        "intro": (
            "The same wlan0 radio is either a Wi-Fi client (joining "
            "upstream) or an access point (broadcasting the management "
            "SSID). The platform's Networking tab is the orchestrator; "
            "these are the shell commands it runs under the hood. "
            "Useful for verifying state, diagnosing why a mode switch "
            "didn't take, or recovering manually if locked out."
        ),
        "ui_reference": "Settings → Networking (mode switcher, scan, saved networks, AP config).",
        "wrapper_modules": [
            "app/services/networking.py",
            "app/tools/hostapd.py", "app/tools/dnsmasq.py",
            "app/tools/nm.py", "app/tools/iproute.py",
        ],
        "commands": [
            {
                "command": "nmcli device wifi list ifname wlan0",
                "description": (
                    "Scan for nearby Wi-Fi networks. Same operation the "
                    "Networking tab's 'Scan' button runs. With "
                    "--rescan=yes, forces a fresh scan rather than "
                    "using cache."
                ),
                "example_output": (
                    "IN-USE  BSSID              SSID            MODE   CHAN  RATE       SIGNAL  SECURITY\n"
                    "        AA:BB:CC:DD:EE:01  HomeWiFi        Infra  36    540 Mbit/s 86      WPA2\n"
                    "        AA:BB:CC:DD:EE:02  NeighborGuest   Infra  6     270 Mbit/s 51      WPA2"
                ),
            },
            {
                "command": "sudo nmcli device wifi connect \"<SSID>\" password \"<password>\" ifname wlan0",
                "description": "Save a connection profile + connect in one step. Profile persists across reboots.",
                "notes": "Use nmcli connection modify <SSID> connection.autoconnect yes to ensure auto-reconnect.",
            },
            {
                "command": "nmcli connection show",
                "description": "List all saved connection profiles (Wi-Fi, Ethernet, VPN). The Networking tab's 'Saved networks' table is filtered to 802-11-wireless rows.",
            },
            {
                "command": "sudo nmcli connection delete <SSID>",
                "description": "Forget a saved Wi-Fi profile. The 'Forget' button in the UI runs this.",
            },
            {
                "command": "sudo nmcli device set wlan0 managed no",
                "description": (
                    "Release wlan0 from NetworkManager so hostapd can "
                    "take over. Done at the start of the management AP "
                    "enable sequence."
                ),
                "notes": "Reverse: `nmcli device set wlan0 managed yes`.",
            },
            {
                "command": "sudo ip addr add 10.42.0.1/24 dev wlan0",
                "description": "Assign the management AP gateway IP to wlan0. Required before dnsmasq can serve DHCP on that subnet.",
                "notes": "`sudo ip addr flush dev wlan0` first to clear any prior addresses.",
            },
            {
                "command": "cat /etc/pipineapple/mgmt-ap-hostapd.conf",
                "description": "Inspect the hostapd config the platform generated.",
                "example_output": (
                    "interface=wlan0\n"
                    "driver=nl80211\n"
                    "ssid=PiPineapple-Mgmt\n"
                    "hw_mode=g\n"
                    "channel=6\n"
                    "auth_algs=1\n"
                    "wmm_enabled=1\n"
                    "wpa=2\n"
                    "wpa_passphrase=<your-pw>\n"
                    "wpa_key_mgmt=WPA-PSK\n"
                    "rsn_pairwise=CCMP"
                ),
            },
            {
                "command": "cat /etc/pipineapple/mgmt-ap-dnsmasq.conf",
                "description": "Inspect the dnsmasq config (DHCP only, local hostnames mapped to the AP gateway).",
            },
            {
                "command": "sudo hostapd /etc/pipineapple/mgmt-ap-hostapd.conf",
                "description": "Run hostapd manually for debugging. Foreground output shows client associations and WPA2 handshakes.",
                "notes": "Ctrl-C to stop. The platform runs this via the JobManager so it survives backgrounded.",
            },
            {
                "command": "sudo dnsmasq -C /etc/pipineapple/mgmt-ap-dnsmasq.conf -k --log-facility=-",
                "description": "Run dnsmasq foreground for debugging. -k keeps it from forking; --log-facility=- logs to stderr.",
            },
            {
                "command": "iw dev wlan0 info",
                "description": "Confirm wlan0 type after a mode switch. 'type AP' = management AP active; 'type managed' = client mode.",
            },
            {
                "command": "nmcli device status",
                "description": "Overview of every interface's state. Useful for confirming the right mode is active.",
                "example_output": (
                    "DEVICE        TYPE      STATE         CONNECTION\n"
                    "wlan0         wifi      connected     HomeWiFi          # client mode\n"
                    "wlan0         wifi      unmanaged     --                # AP mode\n"
                    "eth0          ethernet  connected     Wired"
                ),
            },
            {
                "command": "rm $PIPINEAPPLE_DATA_DIR/networking.json",
                "description": (
                    "Emergency reset of the networking state. Next "
                    "Flask startup falls back to defaults (wlan0 idle, "
                    "management AP config defaulted). Used if you "
                    "lock yourself out via misconfigured AP."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 04.9 — Internet sharing, persistent state, hands-off boot
    # ------------------------------------------------------------------
    {
        "id": "internet-sharing-and-boot",
        "title": "Internet sharing & hands-off boot",
        "added_in_session": 4.9,
        "intro": (
            "Letting management AP clients reach the internet through "
            "whatever upstream the Pi has (home Wi-Fi via wlan0, or "
            "Ethernet via eth0): NAT, IP forwarding, and dnsmasq as a "
            "DNS forwarder. Plus the operational glue — persistent "
            "state directory, systemd unit so the platform boots itself "
            "after power-on, and the Python bytecode flag that defeats "
            "a class of stale-deploy bugs."
        ),
        "ui_reference": (
            "Settings → Networking → Management AP → 'Share upstream "
            "internet with AP clients' toggle"
        ),
        "wrapper_modules": [
            "app/tools/iptables.py",
            "app/tools/dnsmasq.py",
            "app/services/networking.py",
            "deploy/pipineapple.service",
            "deploy/install-service.sh",
        ],
        "commands": [
            # ---- NAT + IP forwarding ----
            {
                "command": "sudo sysctl -w net.ipv4.ip_forward=1",
                "description": (
                    "Enable IPv4 forwarding so the Pi will route packets "
                    "between interfaces. Without this, packets arriving "
                    "on wlan-mgmt-ap from clients are dropped instead of "
                    "being forwarded out wlan0/eth0. The platform "
                    "re-applies this on every AP enable; iptables and "
                    "this sysctl both reset on reboot."
                ),
                "notes": (
                    "Make it survive reboots by also adding "
                    "`net.ipv4.ip_forward=1` to /etc/sysctl.conf — but "
                    "we don't bother because the platform sets it on "
                    "every enable anyway."
                ),
            },
            {
                "command": "sudo iptables -t nat -A POSTROUTING -s 10.42.0.0/24 -j MASQUERADE",
                "description": (
                    "NAT rule: rewrite the source address of any packet "
                    "from the AP subnet (10.42.0.0/24) to the Pi's "
                    "outbound interface IP. Without this, return packets "
                    "from the internet have no way back to 10.42.0.x."
                ),
                "notes": (
                    "We deliberately use -s subnet (not -o iface). The "
                    "first manual fix used `-o wlan0` and broke when the "
                    "Pi's default route was actually via eth0 — packets "
                    "left out eth0 un-NAT'd, ISP dropped them, browser "
                    "showed 'page keeps loading'. Subnet-based rules "
                    "work regardless of which interface egress picks."
                ),
            },
            {
                "command": "sudo iptables -A FORWARD -s 10.42.0.0/24 -j ACCEPT",
                "description": (
                    "Explicit accept for outbound forwarded packets from "
                    "the AP subnet. Pair it with the conntrack rule for "
                    "return traffic. Often redundant when the FORWARD "
                    "chain default policy is ACCEPT, but defensive — if "
                    "you ever tighten the firewall, these rules ensure "
                    "AP clients still reach the internet."
                ),
            },
            {
                "command": "sudo iptables -A FORWARD -d 10.42.0.0/24 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
                "description": (
                    "Accept return traffic destined for the AP subnet, "
                    "but only for connections that were initiated from "
                    "inside. Standard stateful-firewall pattern."
                ),
            },
            {
                "command": "sudo iptables -t nat -S POSTROUTING",
                "description": "List NAT POSTROUTING rules in iptables-save format. Confirms the MASQUERADE rule landed.",
                "example_output": (
                    "-P POSTROUTING ACCEPT\n"
                    "-A POSTROUTING -s 10.42.0.0/24 -j MASQUERADE"
                ),
            },
            {
                "command": "sudo iptables -t nat -L POSTROUTING -n -v",
                "description": (
                    "Same data with packet counters. The 'pkts' column "
                    "tells you whether traffic is hitting your rules — "
                    "if it's zero, packets aren't reaching this chain "
                    "(usually because the client's gateway is wrong, or "
                    "the egress interface bypasses NAT)."
                ),
            },
            {
                "command": "sudo iptables -t nat -D POSTROUTING -s 10.42.0.0/24 -j MASQUERADE",
                "description": "Delete the MASQUERADE rule. Used when internet sharing is toggled off.",
            },

            # ---- DNS forwarding via dnsmasq ----
            {
                "command": "grep -E '^server=|^no-resolv' /etc/pipineapple/mgmt-ap-dnsmasq.conf",
                "description": (
                    "Inspect dnsmasq's upstream-DNS config. With "
                    "internet-sharing OFF, expect `no-resolv` (DNS "
                    "queries from clients go nowhere). With it ON, "
                    "expect `server=1.1.1.1` + `server=8.8.8.8` and NO "
                    "`no-resolv` line — dnsmasq forwards client queries "
                    "to those upstream resolvers."
                ),
            },
            {
                "command": "dig @10.42.0.1 google.com +short",
                "description": (
                    "Test DNS resolution through the Pi's dnsmasq. Run "
                    "from the Pi itself or any AP client. Returning IPs "
                    "means forwarding works end to end. 'no servers "
                    "could be reached' means dnsmasq isn't forwarding "
                    "(check server= lines)."
                ),
                "notes": (
                    "Install with `sudo apt install -y dnsutils` — Pi "
                    "OS Lite ships without dig/nslookup."
                ),
            },

            # ---- dnsmasq port race ----
            {
                "command": "sudo ss -tulnp | grep ':53'",
                "description": (
                    "Find what's bound to port 53. Useful when "
                    "restarting dnsmasq fails with 'Address already in "
                    "use' — usually a lingering dnsmasq the kernel "
                    "hasn't fully reaped, occasionally systemd-resolved."
                ),
            },
            {
                "command": "sudo pkill -9 dnsmasq",
                "description": (
                    "Hard-kill all dnsmasq processes. Use only when "
                    "the platform's JobManager has lost track of one "
                    "(e.g. a previous restart raced on port 53 and the "
                    "new instance failed silently)."
                ),
                "notes": (
                    "The platform now waits for port 10.42.0.1:53 to "
                    "be bindable (with SO_REUSEADDR) before starting "
                    "the new dnsmasq — see _wait_for_port_free in "
                    "networking.py. Manual cleanup should rarely be "
                    "needed."
                ),
            },

            # ---- Phone-side DHCP cache ----
            {
                "command": "(phone) forget Wi-Fi network, rejoin",
                "description": (
                    "Modern phones aggressively cache DHCP leases. If "
                    "the AP came up before you configured internet "
                    "sharing, the phone's lease may not have the right "
                    "gateway/DNS. Forgetting + rejoining forces a fresh "
                    "lease. Symptom: page loads forever, or 'wrong "
                    "password' error on iOS when the password is "
                    "correct (iOS reports 'no DHCP' as a credential "
                    "error)."
                ),
            },

            # ---- Persistent DATA_DIR ----
            {
                "command": "echo $PIPINEAPPLE_DATA_DIR; ls -la /var/lib/pipineapple/",
                "description": (
                    "The DATA_DIR holds auth.json, networking.json, "
                    "adapter_roles.json, and the deny-list. Default is "
                    "/tmp/pipineapple which is wiped on every reboot. "
                    "The systemd unit pins it to /var/lib/pipineapple "
                    "(FHS-correct for runtime state) so credentials and "
                    "AP config survive reboots. If you launch Flask "
                    "manually, run-as-root.sh exports the same path."
                ),
            },
            {
                "command": "sudo cat /var/lib/pipineapple/networking.json | python3 -m json.tool",
                "description": (
                    "Inspect the persisted networking state. "
                    "mgmt_ap_active=true + ssid+password present is "
                    "what restore_on_startup needs to bring the AP up "
                    "automatically. internet_sharing=true means the "
                    "NAT + DNS-forwarding rules get re-applied on "
                    "every enable."
                ),
            },

            # ---- systemd unit ----
            {
                "command": "sudo ./deploy/install-service.sh",
                "description": (
                    "Idempotent installer that copies "
                    "deploy/pipineapple.service into "
                    "/etc/systemd/system/, runs daemon-reload, enables "
                    "auto-start, and restarts the service. Run once "
                    "per Pi — re-runs just refresh the unit file."
                ),
            },
            {
                "command": "sudo systemctl status pipineapple",
                "description": "Service health: active/inactive, PID, recent log lines.",
            },
            {
                "command": "sudo systemctl restart pipineapple",
                "description": (
                    "Pick up new code without rebooting. Triggers "
                    "atexit cleanup of the JobManager's children "
                    "(hostapd/dnsmasq), then the new process re-runs "
                    "restore_on_startup."
                ),
            },
            {
                "command": "sudo journalctl -u pipineapple -f",
                "description": (
                    "Live log tail for the platform. Filter with grep "
                    "to find specific things, e.g. "
                    "`sudo journalctl -u pipineapple -f | grep -iE "
                    "'restoring|hostapd|dnsmasq'`."
                ),
            },
            {
                "command": "sudo journalctl -u pipineapple -b --no-pager | head -100",
                "description": (
                    "First 100 lines of logs from this boot. Use to "
                    "diagnose why the AP didn't come up on startup — "
                    "the relevant lines are usually within the first "
                    "10 seconds of the process starting."
                ),
            },
            {
                "command": "sudo cat /proc/$(pgrep -f 'python.*run.py')/environ | tr '\\0' '\\n' | grep PIPINEAPPLE",
                "description": (
                    "Ground truth for what environment variables the "
                    "running Flask process actually sees. Critical "
                    "when DATA_DIR seems wrong — confirms whether the "
                    "systemd unit's Environment= line is reaching "
                    "Python, or whether something else launched Flask "
                    "without the env var."
                ),
            },

            # ---- Python bytecode caching ----
            {
                "command": "sudo find /home/pi-lab/pipineapple -name '__pycache__' -type d -exec rm -rf {} +",
                "description": (
                    "Nuke all .pyc cache directories. Necessary when "
                    "Python loads stale bytecode after a deploy — "
                    "happens when the deploy method (rsync -a, git "
                    "checkout) sets source mtimes older than the .pyc, "
                    "and Python trusts the cache. The systemd unit "
                    "sets PYTHONDONTWRITEBYTECODE=1 to prevent this "
                    "permanently, so manual cache clears should be a "
                    "last-resort diagnostic, not part of normal deploy."
                ),
            },
            {
                "command": "ls -la app/tools/nm.py app/tools/__pycache__/nm.cpython-*.pyc",
                "description": (
                    "Compare source vs cached bytecode timestamps. If "
                    ".pyc mtime > .py mtime AND the .pyc was compiled "
                    "from an old version of .py (rare, requires "
                    "specific deploy ordering), Python may run the old "
                    "code. Definitive test is to inspect what Python "
                    "actually loaded — see next command."
                ),
            },
            {
                "command": "cd /home/pi-lab/pipineapple && sudo .venv/bin/python -c \"import inspect; from app.tools import nm; print(inspect.getsource(nm.set_managed)[:400])\"",
                "description": (
                    "Print the actual source code Python loaded for a "
                    "function. Bypasses any guessing about cache "
                    "validity — if the printed code doesn't match what "
                    "you expect, Python isn't loading the file you "
                    "think it is."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 05 — Recon (airodump-ng, CSV polling, dual-band parallel)
    # ------------------------------------------------------------------
    {
        "id": "recon-airodump",
        "title": "Recon — airodump-ng",
        "added_in_session": 5,
        "intro": (
            "Passive 802.11 scan via airodump-ng. The Recon page runs "
            "two airodump processes in parallel — one per monitor "
            "adapter, one per band — and polls each airodump's CSV "
            "output once per second to populate the AP + Client tables "
            "live. The first session that consumes the JobManager as a "
            "data source rather than just a daemon launcher."
        ),
        "ui_reference": (
            "Recon page → Start scan / Stop scan + live AP and Client tables"
        ),
        "wrapper_modules": [
            "app/tools/airodump.py",
            "app/services/recon.py",
            "app/routes/recon.py",
        ],
        "commands": [
            # ---- airodump fundamentals ----
            {
                "command": "sudo iw dev wlan-mon-2g info",
                "description": (
                    "Confirm an interface is in monitor mode before "
                    "running airodump. 'type AP', 'type managed', or "
                    "'type monitor'. airodump-ng requires monitor; the "
                    "Recon page calls adapter_svc.set_mode(iface, "
                    "'monitor') as the first step of Start scan."
                ),
            },
            {
                "command": "sudo airodump-ng --output-format csv --write /tmp/recon-test --band bg wlan-mon-2g",
                "description": (
                    "Run airodump-ng manually on the 2.4 GHz monitor "
                    "adapter. Writes /tmp/recon-test-01.csv (auto-"
                    "increment suffix). Ctrl-C to stop. Use --band a "
                    "for 5 GHz, --band abg for both."
                ),
                "notes": (
                    "We skip pcap output (--output-format csv only) for "
                    "the live scan to save disk IO. Session 06 will add "
                    "pcap for handshake capture."
                ),
            },
            {
                "command": "sudo airodump-ng --channel 1,6,11 --write /tmp/recon-test wlan-mon-2g",
                "description": (
                    "Pin channel hopping to specific channels instead "
                    "of airodump's default cycle. Useful when you know "
                    "the target AP's channel and want maximum dwell "
                    "time on it."
                ),
            },
            {
                "command": "sudo airodump-ng --write-interval 1 --berlin 60 ...",
                "description": (
                    "Tune CSV refresh rate (--write-interval, seconds) "
                    "and the 'stale entry' display window (--berlin, "
                    "seconds). The platform uses both: write-interval=1 "
                    "for snappy UI, berlin=60 so transient stations "
                    "don't churn the table every second."
                ),
            },

            # ---- CSV parsing ----
            {
                "command": "head -30 /tmp/recon-test-01.csv",
                "description": (
                    "Inspect the CSV airodump writes. Two sections "
                    "separated by a blank line: APs first (15 fields), "
                    "Clients second (6 fixed fields + variable trailing "
                    "fields for probed ESSIDs). The parser in "
                    "app/tools/airodump.py walks the file line by line, "
                    "uses the blank line as the section boundary."
                ),
                "example_output": (
                    "BSSID, First time seen, Last time seen, channel, "
                    "Speed, Privacy, Cipher, Authentication, Power, "
                    "# beacons, # IV, LAN IP, ID-length, ESSID, Key\n"
                    "AA:BB:CC:DD:EE:01, 2026-06-05 14:00:00, ...,  6,  "
                    "54, WPA2, CCMP, PSK,  -52,  1240,  842, ...\n"
                    "\n"
                    "Station MAC, First time seen, ..., Probed ESSIDs\n"
                    "11:22:33:44:55:01, ..., AA:BB:CC:DD:EE:01, HomeWiFi"
                ),
            },
            {
                "command": "awk -F, '/^Station MAC/{exit} NR>1 && $1~/^[0-9A-Fa-f]/' /tmp/recon-test-01.csv | wc -l",
                "description": (
                    "Count APs in a CSV by stopping at the Station MAC "
                    "header. Useful for sanity-checking parser output "
                    "against ground truth."
                ),
            },

            # ---- Two-adapter parallel ----
            {
                "command": "sudo airodump-ng --band bg --write /tmp/recon-2g wlan-mon-2g &\nsudo airodump-ng --band a --write /tmp/recon-5g wlan-mon-5g &",
                "description": (
                    "Run both bands in parallel — exactly what the "
                    "Recon service does via the JobManager. Two "
                    "separate processes, two separate CSVs, merged "
                    "by BSSID/MAC in the service layer."
                ),
                "notes": (
                    "Don't forget to bring both monitor adapters up "
                    "first (`sudo ip link set wlan-mon-2g up`) — "
                    "airodump-ng won't error helpfully if they're "
                    "down."
                ),
            },

            # ---- Lifecycle ----
            {
                "command": "pgrep -af airodump-ng",
                "description": (
                    "Check what airodump-ng processes are running. "
                    "When the Recon UI shows 'running', expect two "
                    "processes — one per band. Stuck processes after "
                    "a crash: `sudo pkill -9 airodump-ng`."
                ),
            },
            {
                "command": "sudo iw dev wlan-mon-2g set type managed",
                "description": (
                    "Return a monitor adapter to managed mode. Stop "
                    "the recon scan first, then either flip mode via "
                    "Settings → Adapter Management or run this "
                    "directly. The Recon Stop scan button deliberately "
                    "does NOT restore managed mode — recon often runs "
                    "in cycles and the down/up dance is wasteful."
                ),
            },
            {
                "command": "rm /tmp/pipineapple-recon-*-*.csv /tmp/pipineapple-recon-*.cap /tmp/pipineapple-recon-*.log.csv",
                "description": (
                    "Wipe any leftover airodump output files. The "
                    "recon service does this before each scan start "
                    "so airodump always lands on the -01 suffix; "
                    "manual cleanup is rarely needed."
                ),
            },

            # ---- Snapshot inspection ----
            {
                "command": "curl -s http://localhost:5000/recon/snapshot | python3 -m json.tool | head -30",
                "description": (
                    "Hit the JSON API directly to inspect the merged "
                    "snapshot the UI renders. Use it to confirm the "
                    "service is producing data before assuming the JS "
                    "is broken."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 06 — Beacon / probe parsing, deauth, driver reset
    # ------------------------------------------------------------------
    {
        "id": "beacon-probe-deauth",
        "title": "Beacon IEs, probe requests, deauth, driver reset",
        "added_in_session": 6,
        "intro": (
            "The Recon slide-out backend: parsing the full beacon "
            "Information Elements (RSN cipher suites, HT/VHT/HE, "
            "vendor IEs) from pcap with scapy, aggregating probe "
            "requests per client to surface Preferred Network Lists, "
            "and sending deauthentication frames from the dedicated "
            "injection radio. Plus the Realtek-specific dance to "
            "fully reset a wireless netdev (needed for AP "
            "reconfigure to not fail with \"Could not configure "
            "driver mode\")."
        ),
        "ui_reference": (
            "Recon page → click an AP or Client row → slide-out tabs "
            "(Overview, Security, Tagged params, Clients / Probes) + "
            "Deauth All Clients button"
        ),
        "wrapper_modules": [
            "app/tools/beacon_parser.py",
            "app/tools/aireplay.py",
            "app/tools/iw.py",
            "app/services/recon.py",
        ],
        "commands": [
            # ---- Beacon parsing ----
            {
                "command": "tshark -r /tmp/pipineapple-recon-2g-01.cap -Y 'wlan.fc.type_subtype == 0x08' -V | head -120",
                "description": (
                    "Dump beacon frames from the airodump pcap with "
                    "full IE decoding. Useful sanity-check for what "
                    "the scapy parser sees. Subtype 0x08 = beacon."
                ),
            },
            {
                "command": "python3 -c \"from scapy.all import rdpcap; from scapy.layers.dot11 import Dot11Beacon; pkts = rdpcap('/tmp/pipineapple-recon-2g-01.cap'); print(sum(1 for p in pkts if p.haslayer(Dot11Beacon)), 'beacons')\"",
                "description": (
                    "Count beacons in a pcap with scapy. Same library "
                    "the slide-out backend uses; if this works the "
                    "platform's parser will too."
                ),
            },
            {
                "command": "iw dev wlan-mon-2g scan",
                "description": (
                    "Live beacon dump from one radio. Decodes RSN, HT, "
                    "VHT, country, vendor IEs in a human-readable way. "
                    "Requires the iface in monitor mode is fine (some "
                    "drivers want managed). Useful when you want a "
                    "single beacon parse without going through the "
                    "scan workflow."
                ),
            },

            # ---- RSN element interpretation ----
            {
                "command": "(reference) RSN AKM suite IDs (OUI 00-0F-AC)",
                "description": (
                    "1 = 802.1X (WPA2-Enterprise) · 2 = PSK "
                    "(WPA2-Personal) · 3 = FT-802.1X · 4 = FT-PSK · "
                    "5 = 802.1X-SHA256 · 6 = PSK-SHA256 · 8 = SAE "
                    "(WPA3-Personal) · 11 = Suite-B-192 · "
                    "18 = OWE (open-with-encryption). The slide-out's "
                    "Security tab summary uses these to pick "
                    "'WPA2-Personal' / 'WPA3-Personal' / "
                    "'WPA2-Enterprise' etc."
                ),
            },
            {
                "command": "(reference) RSN cipher suite IDs (OUI 00-0F-AC)",
                "description": (
                    "1 = WEP-40 · 2 = TKIP · 4 = CCMP-128 (the "
                    "standard) · 6 = BIP-CMAC-128 · 8 = GCMP-128 · "
                    "9 = GCMP-256 · 10 = CCMP-256. Group cipher and "
                    "pairwise cipher are encoded the same way."
                ),
            },
            {
                "command": "(reference) MFP bits in RSN capabilities",
                "description": (
                    "Bit 6 = MFPC (Management Frame Protection "
                    "Capable). Bit 7 = MFPR (Required). When MFPR is "
                    "set, deauth frames from us get rejected by the "
                    "client because the AP-client deauth is "
                    "cryptographically protected. WPA3 mandates MFP; "
                    "WPA2 makes it optional. The slide-out's Deauth "
                    "button auto-disables when MFPR is set."
                ),
            },

            # ---- Probe requests ----
            {
                "command": "tshark -r /tmp/pipineapple-recon-2g-01.cap -Y 'wlan.fc.type_subtype == 0x04 && wlan.ssid != \\\"\\\"'",
                "description": (
                    "Filter directed probe requests (subtype 0x04) "
                    "with non-empty SSID — these are the privacy-leaky "
                    "ones revealing the client's Preferred Network "
                    "List. Broadcast probes (empty SSID) aren't "
                    "interesting on their own."
                ),
            },
            {
                "command": "(behavior) iOS / Android probe patterns",
                "description": (
                    "iPhones randomise MAC per saved SSID — each "
                    "network gets its own MAC for probes AND for "
                    "association. They send broadcast probes from the "
                    "real hardware MAC when actively scanning (e.g., "
                    "Settings → Wi-Fi open). Android does similar "
                    "randomisation since Android 10; older Android "
                    "devices use real MACs throughout. Forgetting a "
                    "stale SSID stops the device probing for it."
                ),
            },

            # ---- Deauth via aireplay-ng ----
            {
                "command": "sudo aireplay-ng --deauth 10 -a <BSSID> <iface>",
                "description": (
                    "Broadcast deauth — send 10 deauth bursts at "
                    "<BSSID> with destination ff:ff:ff:ff:ff:ff (all "
                    "associated clients). Fastest way to force a "
                    "reassociation storm. <iface> must be in monitor "
                    "mode and pinned to <BSSID>'s channel."
                ),
                "notes": (
                    "Lab equipment only. Sending deauth frames at "
                    "networks you don't own or have written "
                    "authorisation to test is illegal in most "
                    "jurisdictions (US: 18 U.S.C. § 1362, FCC Part 15)."
                ),
            },
            {
                "command": "sudo aireplay-ng --deauth 10 -a <BSSID> -c <CLIENT_MAC> <iface>",
                "description": (
                    "Targeted deauth — kick a single client. Useful "
                    "when you want to disturb one device (e.g., a "
                    "test phone) without affecting the rest of the "
                    "network."
                ),
            },
            {
                "command": "sudo iw dev wlan-ap set channel 6",
                "description": (
                    "Pin the injection radio to the target's channel "
                    "BEFORE sending deauth. Frames sent off-channel "
                    "are silently dropped by the AP. The recon "
                    "service does this automatically as step 5 of "
                    "the deauth flow."
                ),
            },

            # ---- Realtek nl80211 reset (the AP reconfigure fix) ----
            {
                "command": "sudo iw dev wlan-mgmt-ap del; sudo iw phy phy4 interface add wlan-mgmt-ap type managed",
                "description": (
                    "Hard-reset a wireless netdev by destroying and "
                    "recreating it. Required between hostapd runs on "
                    "the Realtek rtw_8821cu driver — the standard "
                    "down → set type managed → up dance is not enough "
                    "to release the per-interface nl80211 vif state, "
                    "and the next hostapd start fails with: "
                    "\"nl80211: kernel reports: Match already configured\" "
                    "→ \"Could not configure driver mode\" → \"AP-DISABLED\". "
                    "mt76 chipsets don't strictly need this but it's "
                    "harmless."
                ),
                "notes": (
                    "Get the phy index from 'iw dev <iface> info'. "
                    "The recon service wraps this as "
                    "iw.recreate_interface(iface) and calls it from "
                    "_disable_mgmt_ap_unlocked."
                ),
            },
            {
                "command": "sudo modprobe -r 8821cu && sleep 1 && sudo modprobe 8821cu",
                "description": (
                    "Heavier alternative if the iw del+add doesn't "
                    "work — reload the Realtek kernel module to fully "
                    "destroy and recreate the netdev with a clean "
                    "driver. ~5 second outage; udev rules re-fire "
                    "and the iface gets renamed back to wlan-mgmt-ap "
                    "via the MAC mapping."
                ),
            },

            # ---- Operational gotchas from the build ----
            {
                "command": "(gotcha) airodump-ng stdout must go to /dev/null",
                "description": (
                    "airodump's stdout is the live curses-style "
                    "refreshing table — full redraw with ANSI escapes "
                    "every --write-interval second. Redirecting it to "
                    "a file (the obvious thing, via JobManager's "
                    "stdout_path) produced ~2 GB per band per 5 "
                    "minutes. /tmp on Pi OS is tmpfs (RAM-backed), so "
                    "this consumed RAM, triggered swap, and ground "
                    "the Pi to a halt. The useful data is all in the "
                    "CSV + pcap files airodump writes via --write. "
                    "Recon service uses stdout_path='/dev/null'."
                ),
            },
            {
                "command": "(gotcha) SIGINT, not SIGTERM, for aircrack-ng tools",
                "description": (
                    "aircrack-ng tools install a SIGINT handler that "
                    "flushes CSV + pcap, releases the radio, and "
                    "tears down the channel hopper. SIGTERM bypasses "
                    "that handler and leaves the mt76 driver in a "
                    "state where the next op (or even just idleness) "
                    "can hard-hang the USB controller — which on the "
                    "Pi 5 also serves the SSD, locking the whole "
                    "machine. JobManager.stop_job now takes a "
                    "first_signal parameter for this; recon passes "
                    "signal.SIGINT."
                ),
            },
            {
                "command": "(gotcha) Service-level state needs a singleton",
                "description": (
                    "If a service stores live process state (job IDs, "
                    "open file handles) on the instance, get_service() "
                    "must return a singleton. Otherwise every request "
                    "gets a fresh instance with no idea what the "
                    "previous one started, and stop_job calls silently "
                    "no-op because the job IDs are all None. "
                    "NetworkingService had this bug for several "
                    "sessions — the stop hostapd / stop dnsmasq lines "
                    "never appeared in the journal, and AP "
                    "reconfigure failed because the old daemons were "
                    "still holding port 53. Fixed by switching to a "
                    "module-level singleton, same pattern ReconService "
                    "uses."
                ),
            },
            {
                "command": "(gotcha) Async work in Flask needs app_context",
                "description": (
                    "Background threads spawned outside a request "
                    "(e.g., recon teardown thread, networking restore "
                    "thread) don't inherit Flask's request context. "
                    "Calling get_adapter_service() or anything else "
                    "that uses current_app raises RuntimeError, the "
                    "exception is swallowed by the thread runner, and "
                    "the work never completes. Fix: capture "
                    "current_app._get_current_object() before "
                    "spawning, then `with app.app_context(): ...` "
                    "inside the thread."
                ),
            },
            {
                "command": "(gotcha) Enable persistent journald to debug crashes",
                "description": (
                    "Pi OS Lite's default journald is volatile — logs "
                    "are lost on every reboot. When the Pi hard-hangs "
                    "and you power-cycle, all kernel messages from "
                    "before the crash are gone. Enable persistence "
                    "ONCE with: sudo mkdir -p /var/log/journal && "
                    "sudo systemd-tmpfiles --create --prefix "
                    "/var/log/journal && sudo systemctl restart "
                    "systemd-journald. After that, 'journalctl -k "
                    "-b -1' shows kernel messages from the previous "
                    "boot — invaluable for mt76 / USB / RCU stall "
                    "diagnosis."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 07 / 07.5 — Handshake capture: EAPOL 4-way + PMKID via hcxdumptool
    # ------------------------------------------------------------------
    {
        "id": "handshake-capture",
        "title": "Handshake capture — EAPOL 4-way + PMKID",
        "added_in_session": 7,
        "intro": (
            "Capturing the cryptographic material needed to crack a "
            "WPA2/WPA3-Personal pre-shared key offline. Two distinct "
            "signals we can target:\n"
            " * The EAPOL 4-way handshake (M1-M4) that happens during "
            "client association. Needs a client to associate (or "
            "deauth + reassociate). The MIC in M2 is what hashcat "
            "tries to recreate by guessing the PSK.\n"
            " * The PMKID, which the AP includes in M1 of its own "
            "accord. hcxdumptool's active-scan mode triggers M1 from "
            "the AP by sending a fake association — **no client "
            "involvement at all**. PMKID alone is enough for hashcat "
            "mode 22000. This is the real-pen-test approach.\n"
            "Session 07 originally built this with airodump-ng; "
            "Session 07.5 swapped to hcxdumptool because in actual "
            "engagements you can't ask the client to forget the "
            "network, and modern devices cache PMK aggressively, "
            "skipping M1/M2 on reconnect."
        ),
        "ui_reference": (
            "Recon page → click an AP row → AP slide-out → "
            "'Capture handshakes' button + 'Captures' tab. "
            "Top-level Captures card lists everything across all APs."
        ),
        "wrapper_modules": [
            "app/tools/hcxdumptool.py",
            "app/tools/handshake_detector.py",
            "app/services/handshakes.py",
            "app/routes/handshakes.py",
        ],
        "commands": [
            # ---- Install ----
            {
                "command": "sudo apt install -y hcxtools",
                "description": (
                    "Provides hcxdumptool (capture) + hcxpcapngtool "
                    "(format conversion to hashcat .22000). Modern "
                    "successor to the airodump-ng-based workflow. "
                    "Pi OS Trixie has it in the default repos."
                ),
            },

            # ---- The EAPOL 4-way handshake ----
            {
                "command": "(reference) EAPOL 4-way message identification",
                "description": (
                    "All four are EAPOL-Key frames (type 3) with "
                    "Pairwise=1 in Key Information. The (Install, Ack, "
                    "MIC, Secure) tuple distinguishes them:\n"
                    "  M1 : (0, 1, 0, 0)  AP→STA  ANonce, no MIC yet\n"
                    "  M2 : (0, 0, 1, 0)  STA→AP  SNonce + first MIC ← what hashcat needs\n"
                    "  M3 : (1, 1, 1, 1)  AP→STA  ANonce again + MIC + key install\n"
                    "  M4 : (0, 0, 1, 1)  STA→AP  final ack\n"
                    "Frame direction (FromDS/ToDS bits) tells you "
                    "which address is the BSSID vs the station. "
                    "Group-key handshakes (GTK rekeys) have "
                    "Pairwise=0 and are ignored."
                ),
            },
            {
                "command": "(reference) What constitutes a usable capture",
                "description": (
                    "For hashcat mode 22000 (the universal WPA "
                    "format):\n"
                    "  * Full 4-way (M1+M2+M3+M4) — best. "
                    "aircrack-ng calls this '1 handshake'.\n"
                    "  * Partial (M1+M2 OR M2+M3) — also crackable. "
                    "Aircrack labels '0 handshakes' but hashcat 22000 "
                    "accepts it.\n"
                    "  * PMKID alone — crackable on its own. No "
                    "client interaction required. Modern best practice."
                ),
            },

            # ---- PMKID ----
            {
                "command": "(reference) Where PMKID lives in the frame",
                "description": (
                    "PMKID is a vendor-specific KDE inside M1's Key "
                    "Data field. KDE format:\n"
                    "  type=0xDD (vendor)\n"
                    "  length=0x14 (20 bytes total)\n"
                    "  OUI=00-0F-AC (IEEE)\n"
                    "  subtype=0x04 (PMKID)\n"
                    "  data=16-byte PMKID (HMAC-SHA1 of PMK + "
                    "  'PMK Name' + AA + SPA)\n"
                    "Most APs include PMKID in every M1 by default. "
                    "Some can be configured to omit it ('PMK caching "
                    "disabled' in the RSN advertisement)."
                ),
            },

            # ---- hcxdumptool ----
            {
                "command": "sudo hcxdumptool -i wlan-ap -w /tmp/test.pcapng -c 6",
                "description": (
                    "Capture on channel 6, write to pcapng. Default "
                    "is active mode — sends fake association requests "
                    "to APs on this channel, extracts PMKID from "
                    "their M1 responses. No client needs to be "
                    "associated. This is what makes hcxdumptool the "
                    "right tool for pen-testing handshake capture vs "
                    "airodump's wait-for-client approach."
                ),
                "notes": (
                    "Caller is responsible for putting the interface "
                    "in monitor mode + on the right channel first. "
                    "hcxdumptool doesn't manage interface state."
                ),
            },
            {
                "command": "sudo hcxdumptool -i wlan-ap -w /tmp/test.pcapng -c 6 --disable_active_scan",
                "description": (
                    "Passive mode — captures EAPOL when a client "
                    "happens to (re)associate, but does NOT extract "
                    "PMKID without a client. Quieter on the air, much "
                    "less effective. Useful when stealth is required."
                ),
            },

            # ---- Verification / conversion ----
            {
                "command": "aircrack-ng /var/lib/pipineapple/handshakes/<bssid>/<file>.pcapng",
                "description": (
                    "Quick check — does the pcap contain a full "
                    "4-way? '1 handshake' = yes. '0 handshakes' "
                    "doesn't mean 'nothing crackable' — aircrack-ng "
                    "is strict and ignores PMKID-only captures. "
                    "Always check with hcxpcapngtool too."
                ),
            },
            {
                "command": "sudo hcxpcapngtool -o /tmp/out.22000 /var/lib/pipineapple/handshakes/<bssid>/<file>.pcapng",
                "description": (
                    "Convert pcapng to hashcat mode 22000 format "
                    "(also called .hc22000). Each line is one "
                    "crackable target — either a PMKID hash or an "
                    "EAPOL 4-way hash. The output's first column "
                    "tells you which type:\n"
                    "  WPA*01 = PMKID\n"
                    "  WPA*02 = EAPOL handshake\n"
                    "Counts in the tool's stdout reveal what was "
                    "actually captured (e.g. 'PMKIDs written: 3, "
                    "EAPOL pairs written: 1')."
                ),
            },
            {
                "command": "hashcat -m 22000 /tmp/out.22000 /path/to/wordlist.txt",
                "description": (
                    "Mode 22000 cracks both PMKID and EAPOL formats "
                    "from the same .22000 file. Session 09 will "
                    "automate this off-Pi (the Pi 5's CPU/GPU is "
                    "way too slow for serious cracking; we dispatch "
                    "to a Mac/Jetson over SSH)."
                ),
            },

            # ---- Deauth as a complement (not requirement) ----
            {
                "command": "sudo aireplay-ng --deauth 10 -a <BSSID> wlan-ap",
                "description": (
                    "Force a fresh 4-way handshake from associated "
                    "clients. NOT needed for PMKID — that comes "
                    "from active scan. Useful when you also want "
                    "the full 4-way from a specific client. The "
                    "platform's capture-modal deauth checkbox runs "
                    "this in a loop every few seconds while "
                    "hcxdumptool is capturing."
                ),
                "notes": (
                    "Lab equipment only. Modern devices cache PMK "
                    "and often skip M1/M2 even after deauth, "
                    "producing only M3-only captures. PMKID via "
                    "active scan is more reliable."
                ),
            },

            # ---- Gotchas from the build ----
            {
                "command": "(gotcha) PMK caching defeats deauth-and-wait",
                "description": (
                    "iOS / macOS / modern Android cache the PMK "
                    "after a successful association — often for "
                    "hours. When deauth fires and the client "
                    "reconnects, both sides skip M1/M2 (PMKSA "
                    "cache hit) and jump straight to M3. The user "
                    "sees 'M3 only' captures, aircrack-ng says "
                    "'0 handshakes'. The fix is hcxdumptool's "
                    "active scan + PMKID — works regardless of "
                    "client cache state."
                ),
            },
            {
                "command": "(gotcha) hcxdumptool stdout to /dev/null, same as airodump",
                "description": (
                    "hcxdumptool prints status updates to stdout. "
                    "We don't read them — the pcap is the data "
                    "path. JobManager's stdout_path must be "
                    "/dev/null for the same reason airodump's was "
                    "(captured stdout balloons over time, fills "
                    "tmpfs, swap thrashes the Pi)."
                ),
            },
            {
                "command": "(gotcha) Orphan processes if Flask gets SIGKILLed",
                "description": (
                    "If systemd SIGKILLs pipineapple before its "
                    "atexit handler fires, JobManager-spawned "
                    "children (airodump, hcxdumptool, aireplay) "
                    "survive and get reparented to init. They "
                    "keep writing pcap and holding the radio. "
                    "Recovery: sudo killall -9 airodump-ng "
                    "aireplay-ng hcxdumptool. Long-term fix on "
                    "the to-do: process groups + startup orphan "
                    "scan in JobManager."
                ),
            },
            {
                "command": "(gotcha) AP reconfigure + JobManager child shutdown timing",
                "description": (
                    "Stopping a capture used to be synchronous on "
                    "the HTTP request thread: SIGINT airodump (up "
                    "to 5s) + scapy re-parse of the pcap (10s+) + "
                    "index write. The blocking starved werkzeug's "
                    "worker pool, SocketIO polls dropped, browser "
                    "saw 'offline'. Fix: stop_capture spawns a "
                    "daemon thread with app_context, returns "
                    "instantly. UI sees 'stopping' then 'idle' "
                    "via the capture:status SocketIO event."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 08 / 08.1 — Handshakes page: .22000 conversion, downloads, cleanup
    # ------------------------------------------------------------------
    {
        "id": "handshakes-page",
        "title": "Handshakes page — .22000 export, downloads",
        "added_in_session": 8,
        "intro": (
            "The dedicated cross-AP handshakes view: every persisted "
            "capture across all targets, downloadable as raw "
            "pcap/pcapng (for Wireshark / re-conversion / forensics) "
            "or as hashcat mode 22000 (.22000) format for offline "
            "cracking. Conversion happens on demand via hcxpcapngtool "
            "and is cached next to the source pcap so the second "
            "download is instant."
        ),
        "ui_reference": (
            "Sidebar → Handshakes page. Full table of captures with "
            "per-row download (pcap + .22000) and delete buttons."
        ),
        "wrapper_modules": [
            "app/tools/hcxpcapngtool.py",
            "app/services/handshakes.py",
            "app/routes/handshakes.py",
            "app/templates/handshakes.html",
            "app/static/handshakes.js",
        ],
        "commands": [
            # ---- .22000 format basics ----
            {
                "command": "(reference) hashcat mode 22000 line format",
                "description": (
                    "Each line is one crackable target:\n"
                    "  WPA*<type>*<mic_or_pmkid>*<MAC_AP>*<MAC_STA>"
                    "*<ESSID_hex>*<ANONCE_hex>*<EAPOL_hex>*<flags>\n"
                    "  type 01 = PMKID (most fields empty)\n"
                    "  type 02 = EAPOL handshake (full nonces + MIC + EAPOL bytes)\n"
                    "A single pcap can produce both types. ESSID is "
                    "hex-encoded to avoid escaping issues with names "
                    "that contain colons/spaces/non-ASCII."
                ),
            },
            {
                "command": "sudo hcxpcapngtool -o /tmp/out.22000 <pcap_or_pcapng>",
                "description": (
                    "Convert any pcap (airodump-ng's .cap or "
                    "hcxdumptool's .pcapng) to hashcat .22000. Tool is "
                    "format-agnostic on input. Stdout summary shows "
                    "what was extracted:\n"
                    "  EAPOL pairs M1M2 / M1M3 / M2M3 — any non-zero is "
                    "crackable\n"
                    "  PMKIDs written — crackable on their own\n"
                    "Returns 0 even when nothing was extracted; check "
                    "the output file size."
                ),
            },
            {
                "command": "hashcat -m 22000 /tmp/out.22000 /path/to/wordlist.txt",
                "description": (
                    "Mode 22000 cracks both PMKID and EAPOL pairs from "
                    "the same file. Session 09 (planned) will automate "
                    "this off-Pi — the Pi 5 is way too slow for serious "
                    "wordlists; dispatch to Mac/Jetson over SSH."
                ),
            },

            # ---- Crackable combinations (fixed in S08.1) ----
            {
                "command": "(reference) Which message sets are crackable",
                "description": (
                    "hashcat mode 22000 accepts any of:\n"
                    "  * M1+M2+M3+M4 — full handshake (best)\n"
                    "  * M1+M2 — most common partial, M2 has the MIC\n"
                    "  * M1+M3 — happens with PMK caching (fast reassoc)\n"
                    "  * M2+M3 — less common but valid\n"
                    "  * PMKID alone — hcxdumptool active-scan signature\n"
                    "M1 alone / M2 alone / M3 alone are NOT enough "
                    "(M2+ and M3 carry MICs but you need an ANonce "
                    "or another MIC to pair with). Our handshake "
                    "detector classifies all five crackable cases as "
                    "'partial' or better; everything else is 'no hs'."
                ),
            },

            # ---- The Handshakes page + download endpoints ----
            {
                "command": "(reference) Storage layout under $DATA_DIR/handshakes/",
                "description": (
                    "$DATA_DIR/handshakes/\n"
                    "    AA-BB-CC-DD-EE-01/\n"
                    "        20260605-220000.pcapng        # hcxdumptool source\n"
                    "        20260605-220000.22000         # cached conversion\n"
                    "        20260607-215615-01.cap        # airodump source\n"
                    "        20260607-215615-01.22000      # cached conversion\n"
                    "    index.json                          # metadata index\n"
                    "The .22000 cache lives next to the source pcap; "
                    "we rebuild only if the pcap's mtime is newer than "
                    "the cache."
                ),
            },
            {
                "command": "curl -s http://localhost:5000/handshakes/list | python3 -m json.tool",
                "description": (
                    "JSON list of every persisted capture. Each entry: "
                    "id, bssid, essid, channel, tool, deauth flags, "
                    "messages_seen, is_complete/is_partial, "
                    "has_pmkid, pcap_relative_path, pcap_size_bytes."
                ),
            },
            {
                "command": "curl -O -J http://localhost:5000/handshakes/<id>/download/pcap",
                "description": (
                    "Download the raw pcap/pcapng with auto-generated "
                    "filename (BSSID + short id). Content-Disposition "
                    "header. Useful for re-running through tshark or "
                    "trying different hcxpcapngtool flags."
                ),
            },
            {
                "command": "curl -O -J http://localhost:5000/handshakes/<id>/download/22000",
                "description": (
                    "Download the .22000 hash file. Conversion happens "
                    "on first download (cached for subsequent calls). "
                    "Returns 404 with a clear message if the pcap "
                    "contains no crackable targets, instead of a "
                    "0-byte file."
                ),
            },

            # ---- The hcxdumptool / mt76 compatibility note ----
            {
                "command": "(gotcha) hcxdumptool 6.3.5 + Alfa mt76x2u + Pi OS Trixie",
                "description": (
                    "hcxdumptool fails to arm the interface on this "
                    "exact combo with 'failed to arm interface' / "
                    "'driver is broken (most likely)'. Known upstream "
                    "issue. Workaround: pick airodump-ng in the "
                    "capture-tool radio (now the default). hcxdumptool "
                    "stays selectable for when the upstream fix lands "
                    "or operators run on different hardware."
                ),
            },

            # ---- Static file caching ----
            {
                "command": "(gotcha) Browser caches static files for 5 min",
                "description": (
                    "config.py sets SEND_FILE_MAX_AGE_DEFAULT = 300 "
                    "(5 minutes). Without this, every JS/CSS request "
                    "did an ETag round trip over the slow mgmt AP — "
                    "page loads took 6-8 seconds. With it, subsequent "
                    "loads come from browser cache. Cmd-Shift-R / "
                    "Ctrl-Shift-R bypasses cache (required after "
                    "deploying static changes during dev)."
                ),
            },

            # ---- The Recon-page client-count column ----
            {
                "command": "(reference) Per-AP client count in the Recon table",
                "description": (
                    "Each AP row now shows a 'Clients' column with the "
                    "number of stations whose airodump-reported BSSID "
                    "matches the AP. Computed in the recon service's "
                    "_tick from the merged_clients dict, after the "
                    "SSID-enrichment pass. Bold when >0 (highlights "
                    "APs with active clients — your real targets); "
                    "muted '0' for beacon-only APs."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 09 — Crack dispatch: scp + remote hashcat over SSH
    # ------------------------------------------------------------------
    {
        "id": "crack-dispatch",
        "title": "Crack dispatch — remote hashcat over SSH",
        "added_in_session": 9,
        "intro": (
            "The Pi can't realistically crack WPA passwords itself — "
            "the Pi 5 CPU does maybe a few thousand H/s against "
            "PBKDF2-SHA1 (4096 iters), so even a 14M-line wordlist "
            "like rockyou takes ~1 hour. A modest GPU clears it in "
            "seconds. So the platform is a dispatcher: it scps the "
            ".22000 file to a configured SSH-reachable host (Mac, "
            "Linux box with NVIDIA, Jetson, anything with hashcat "
            "installed) and runs `hashcat -m 22000` there over SSH, "
            "streaming progress back to the UI by tailing the "
            "per-job log file. SocketIO `crack:status` events update "
            "the Cracks card on the Handshakes page every ~2s."
        ),
        "ui_reference": (
            "Settings → Crack Targets tab: shows the platform's "
            "public key + add/test/remove targets. Handshakes page: "
            "per-row 'Crack' button opens a target picker modal; the "
            "'Crack jobs' card below the captures table shows live "
            "progress and the cracked PSK in green."
        ),
        "wrapper_modules": [
            "app/services/crack_targets.py",
            "app/services/crack.py",
            "app/routes/crack.py",
        ],
        "commands": [
            # ---- SSH key + TOFU setup ----
            {
                "command": "ssh-keygen -t ed25519 -f $DATA_DIR/ssh/id_ed25519 -N '' -C pipineapple",
                "description": (
                    "Generates the platform's SSH keypair the first "
                    "time the Crack Targets tab is opened. Stored "
                    "under $DATA_DIR/ssh/ (separate from operator's "
                    "~/.ssh/) so platform state is self-contained "
                    "and rebootable to known good by removing one "
                    "directory. ed25519 over RSA: shorter keys, "
                    "faster, modern default."
                ),
            },
            {
                "command": "ssh-keygen -l -f $DATA_DIR/ssh/id_ed25519.pub",
                "description": (
                    "SHA256 fingerprint — what the Crack Targets tab "
                    "displays under the public key block so the "
                    "operator can verify against the key shown on "
                    "the remote after installing it."
                ),
                "example_output": "256 SHA256:abcd…xyz pipineapple (ED25519)",
            },
            {
                "command": (
                    "ssh -i $DATA_DIR/ssh/id_ed25519 "
                    "-o UserKnownHostsFile=$DATA_DIR/ssh/known_hosts "
                    "-o StrictHostKeyChecking=accept-new "
                    "-o BatchMode=yes -o ConnectTimeout=10 "
                    "-o IdentitiesOnly=yes user@host '<cmd>'"
                ),
                "description": (
                    "Exact ssh invocation the dispatcher uses. Key "
                    "flags:\n"
                    "  • StrictHostKeyChecking=accept-new — OpenSSH's "
                    "modern TOFU mode: accept the host key on first "
                    "connect, reject if the key ever changes.\n"
                    "  • BatchMode=yes — refuses password prompts; "
                    "fails fast instead of hanging on a TTY prompt "
                    "nobody will answer.\n"
                    "  • IdentitiesOnly=yes — don't try the operator's "
                    "other ~/.ssh keys, only ours.\n"
                    "  • Per-platform known_hosts so we don't touch "
                    "the operator's ~/.ssh/known_hosts."
                ),
            },

            # ---- Installing the key on the remote ----
            {
                "command": (
                    "echo '<paste platform public key>' | "
                    "ssh user@host 'cat >> ~/.ssh/authorized_keys'"
                ),
                "description": (
                    "One-liner from the operator's laptop to install "
                    "the platform's public key on a remote crack "
                    "target. The Crack Targets tab shows this exact "
                    "command with the platform's actual key prefilled. "
                    "After this, ssh as 'user@host' from the Pi works "
                    "key-only, no password."
                ),
            },

            # ---- Target sanity test ----
            {
                "command": (
                    "(compound remote check) ssh user@host "
                    "'command -v hashcat && [ -r <wordlist> ] && "
                    "hashcat --version && uname -srm && wc -l < <wordlist>'"
                ),
                "description": (
                    "What 'Test' button on a crack target runs. One "
                    "round trip checks ssh reachable, hashcat "
                    "installed, wordlist readable + line count, "
                    "hashcat version, kernel arch. Exit codes 11/12 "
                    "are mapped to friendly error messages "
                    "('hashcat not installed', 'wordlist not "
                    "readable'). 255 + 'permission denied' in stderr → "
                    "'copy the platform's public key to "
                    "~/.ssh/authorized_keys'. The crack_targets "
                    "service maps each rc to a clear UI message."
                ),
            },

            # ---- hashcat invocation ----
            {
                "command": (
                    "hashcat -m 22000 --quiet --status --status-timer=10 "
                    "--potfile-disable /tmp/<job>.22000 /path/to/wordlist.txt"
                ),
                "description": (
                    "Exact hashcat command the dispatcher runs on "
                    "the remote. Flag notes:\n"
                    "  • -m 22000 — universal WPA-PBKDF2-PMKID+EAPOL "
                    "mode (handles both PMKID and 4-way handshake "
                    "from the same file).\n"
                    "  • --quiet — suppress hashcat's noisy startup "
                    "banner, leave just status blocks + cracked lines.\n"
                    "  • --status --status-timer=10 — emit a status "
                    "block every 10s (Speed/Progress/Recovered/"
                    "Time.Estimated). The parser keys on these.\n"
                    "  • --potfile-disable — don't auto-recover from "
                    "the remote's potfile; each run is independent. "
                    "(Operator's local hashcat use isn't affected.)"
                ),
            },
            {
                "command": "(reference) hashcat status block — what the parser reads",
                "description": (
                    "Every --status-timer seconds, hashcat dumps:\n"
                    "  Speed.#1.........:   312456 H/s (4.92ms) @ Accel:512…\n"
                    "  Progress.........: 2499584/14344391 (17.43%)\n"
                    "  Recovered........: 0/1 (0.00%) Digests\n"
                    "  Time.Estimated...: Fri Jun  5 23:48:13 2026 "
                    "(3 mins, 4 secs)\n"
                    "The parser greps these four fields with a "
                    "regex apiece, normalises Speed to H/s "
                    "(handles kH/s/MH/s/GH/s/TH/s suffixes), and "
                    "emits crack:status SocketIO events when state "
                    "changes (no spam if values stay identical)."
                ),
            },
            {
                "command": "(reference) Cracked PSK line format",
                "description": (
                    "When hashcat finds the PSK it prints the "
                    "matched 22000 line followed by ':password':\n"
                    "  WPA*02*<MIC>*<MAC_AP>*<MAC_STA>*<ESSID_hex>"
                    "*<ANONCE>*<EAPOL>*<flags>:hunter2\n"
                    "All 22000 header fields are hex/numeric and "
                    "never contain ':', so str.partition(':', 1) on "
                    "the FIRST colon recovers the password verbatim — "
                    "passwords containing ':' (like 'p@ss:word!') "
                    "survive. The early rsplit(':', 1) version "
                    "truncated them; that was the first bug we "
                    "caught with the parser test."
                ),
            },

            # ---- Stop semantics ----
            {
                "command": "(behavior) Stopping a running crack job",
                "description": (
                    "JobManager.stop_job sends SIGTERM to the local "
                    "ssh process. OpenSSH propagates the signal to "
                    "the remote command, hashcat catches SIGTERM, "
                    "flushes status, and exits cleanly. The session "
                    "collapses with it. The remote /tmp/<job>.22000 "
                    "is cleaned up by the trailing `rm -f` we chain "
                    "after the hashcat invocation, regardless of "
                    "hashcat's exit status."
                ),
            },

            # ---- Pi-can't-crack reality check ----
            {
                "command": "(reality check) Why we don't crack on the Pi 5",
                "description": (
                    "Pi 5 CPU on WPA-PBKDF2 (mode 22000): "
                    "~3-5k H/s. A modest discrete GPU: 100k-1M H/s. "
                    "A high-end one: tens of MH/s. The Pi's "
                    "VideoCore 7 GPU isn't hashcat-supported "
                    "anyway (OpenCL/HIP backends only). "
                    "rockyou.txt has 14M lines — Pi takes ~1 hour to "
                    "exhaust; a 3060 takes ~14s; an A100 takes <1s. "
                    "Hence the dispatcher-only role."
                ),
            },

            # ---- Exit codes ----
            {
                "command": "(reference) hashcat exit codes the dispatcher distinguishes",
                "description": (
                    "0 = something was cracked. Parser will have "
                    "captured the PSK from the WPA*...:password "
                    "line; job.status='done'.\n"
                    "1 = exhausted (wordlist run through, nothing "
                    "found). Not a 'failure' — expected outcome. "
                    "job.status='exhausted'.\n"
                    "Other non-zero + we sent SIGTERM = "
                    "job.status='stopped'.\n"
                    "Other non-zero with no stop signal = "
                    "job.status='failed' (real error, e.g. malformed "
                    ".22000, missing wordlist after Test passed, etc.)."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 10 — PineAP: hostapd, SSID pool, beacon broadcasting
    # ------------------------------------------------------------------
    {
        "id": "pineap-engine",
        "title": "PineAP — rogue-AP engine + SSID pool",
        "added_in_session": 10,
        "intro": (
            "The Hak5 Pineapple's headline feature, faithfully ported "
            "to PiPineapple. A single `hostapd` instance on the "
            "`wlan-ap` adapter advertises one or more SSIDs (the "
            "pool), with three operation modes: Passive (configured "
            "but silent), Active (broadcasting the pool), and "
            "Advanced (Active + Karma probe responses). Session 10 "
            "lands the pool store, mode state, Settings tab, and "
            "auto-population from recon + probe-request observations. "
            "Active/Advanced broadcasting + actual hostapd lifecycle "
            "land in Session 11."
        ),
        "ui_reference": (
            "Sidebar → PineAP page → Settings tab. Mode radios + "
            "broadcast/capture toggles + SSID pool table with "
            "pin/hide/remove actions. Start gated behind an "
            "ethics-confirm modal (type 'pineap' to confirm)."
        ),
        "wrapper_modules": [
            "app/services/pineap.py",
            "app/routes/pineap.py",
            "app/templates/pineap.html",
            "app/static/pineap.js",
        ],
        "commands": [
            # ---- hostapd basics ----
            {
                "command": "(reference) hostapd.conf for a multi-SSID rogue AP",
                "description": (
                    "Same wrapper as the management AP (app/tools/"
                    "hostapd.py), different config. Primary BSS plus "
                    "zero-or-more `bss=...` stanzas, each with its "
                    "own SSID + auth + BSSID. Chip-level cap is "
                    "usually 4-8 simultaneous BSSes per radio. For "
                    "larger pools, S11 will cycle the SSID via "
                    "`hostapd_cli set_ssid` every few hundred ms — "
                    "the Hak5 approach."
                ),
                "example_output": (
                    "interface=wlan-ap\n"
                    "driver=nl80211\n"
                    "ssid=HomeWiFi\n"
                    "hw_mode=g\n"
                    "channel=6\n"
                    "auth_algs=1\n"
                    "\n"
                    "bss=wlan-ap_1\n"
                    "ssid=linksys\n"
                    "bssid=02:11:22:33:44:55\n"
                    "\n"
                    "bss=wlan-ap_2\n"
                    "ssid=Starbucks WiFi\n"
                    "bssid=02:11:22:33:44:56"
                ),
            },
            {
                "command": "sudo hostapd /etc/pipineapple/pineap.conf",
                "description": (
                    "Launch the rogue AP daemon. PiPineapple wraps "
                    "this via JobManager so the process lifecycle is "
                    "platform-owned. Foreground for `-dd` debug "
                    "tracing during console exercise; the service "
                    "drops `-dd` in production."
                ),
            },
            {
                "command": "sudo hostapd_cli -i wlan-ap status",
                "description": (
                    "Live state of the running daemon. Shows the "
                    "active BSSes, their MACs, current channel, and "
                    "associated stations. Same control socket used "
                    "by S11 to cycle SSIDs in 'broadcast the whole "
                    "pool' mode."
                ),
            },

            # ---- PineAP three-mode model ----
            {
                "command": "(reference) PineAP three operation modes",
                "description": (
                    "Faithful to the Hak5 Pineapple's semantics:\n"
                    "  • passive — engine configured, hostapd not "
                    "broadcasting. Staging area for settings.\n"
                    "  • active — broadcasting the pool as fake "
                    "beacons. Every device in range sees the pool as "
                    "available networks; matching auto-join saved "
                    "networks may try to associate.\n"
                    "  • advanced — active + Karma probe responses. "
                    "Replies to ANY probe request claiming to be the "
                    "requested SSID, not just pool entries. The most "
                    "dangerous mode against saved open networks.\n"
                    "S10 only the passive path is wired; active/"
                    "advanced persist the setting but start refuses "
                    "until S11."
                ),
            },

            # ---- SSID pool design ----
            {
                "command": "(reference) PineAP SSID pool entry",
                "description": (
                    "Stored at $DATA_DIR/pineap_pool.json, one record "
                    "per SSID:\n"
                    "  {\n"
                    "    \"ssid\":           \"HomeWiFi\",\n"
                    "    \"source\":         \"recon|probe|manual|import\",\n"
                    "    \"first_seen\":     unix_ts,\n"
                    "    \"last_seen\":      unix_ts,\n"
                    "    \"observed_count\": 7,\n"
                    "    \"pinned\":         false,\n"
                    "    \"hidden\":         false\n"
                    "  }\n"
                    "Validation: 1-32 bytes UTF-8 (the 802.11 limit). "
                    "Manual adds must be printable ASCII to keep the "
                    "UI form sane; auto-population (recon/probe) "
                    "bypasses the ASCII gate because real-world "
                    "SSIDs include emoji + CJK and dropping them "
                    "silently is worse than storing them. Pinned "
                    "entries survive 'Clear (unpinned)'. Hidden "
                    "entries stay in the pool but are excluded from "
                    "broadcast."
                ),
            },

            # ---- Auto-population hooks ----
            {
                "command": "(reference) Auto-population from recon + probes",
                "description": (
                    "Wired into `ReconService._tick`. After the SSID-"
                    "enrichment pass, every merged AP with a "
                    "non-empty SSID is pushed via "
                    "`pineap.auto_add_from_recon`; every directed "
                    "probe request (probed_essids minus the empty-"
                    "string broadcast probes) is pushed via "
                    "`pineap.auto_add_from_probes`. Both go through "
                    "`add_ssid` which de-dupes and bumps "
                    "observed_count + last_seen. Wrapped in a "
                    "try/except in the recon loop — pool write "
                    "failures cannot break recon."
                ),
            },

            # ---- Why probe-request leakage matters ----
            {
                "command": "(reference) Why directed probe requests fill the pool",
                "description": (
                    "Phones and laptops periodically probe for "
                    "saved networks ('anyone here named MyHomeWifi, "
                    "MyOfficeWifi, ...?'). Modern iOS 14+/Android 11+ "
                    "have moved mostly to passive scanning + "
                    "randomised MAC in probes, but they still leak "
                    "the SSID name list under various scan strategies "
                    "(hidden-SSID handling, certain power states, "
                    "OEM defaults). PineAP's pool grows by listening "
                    "for exactly this leakage — the more crowded "
                    "the airspace, the bigger your auto-collected "
                    "pool."
                ),
            },

            # ---- Defenses (the threat model context) ----
            {
                "command": "(reference) Defenses against Karma / Evil Twin",
                "description": (
                    "1. MFP / 802.11w — beacons + management frames "
                    "are signed by the original AP's session key, so "
                    "a Karma response gets rejected by the client's "
                    "supplicant. Mandatory in WPA3, optional in "
                    "WPA2-MFP. Defeats Karma against WPA3/WPA2-MFP "
                    "networks.\n"
                    "2. PSK strength — for WPA2/WPA3-PSK without "
                    "MFP, the rogue AP can complete the association "
                    "up through the 4-way handshake and capture a "
                    "crackable partial. Strong PSKs make the post-"
                    "capture crack infeasible.\n"
                    "3. Auto-join discipline — the operator's only "
                    "controllable defense for open networks. iOS/"
                    "Android/macOS all support per-network Auto-Join "
                    "off. Forget unused open networks entirely.\n"
                    "4. Server-cert validation for enterprise WiFi — "
                    "defeats Evil Enterprise even with auto-join on."
                ),
            },

            # ---- Ethics gate ----
            {
                "command": "(behavior) Ethics modal — type 'pineap' to confirm",
                "description": (
                    "Same pattern as S06's deauth modal. Every Start "
                    "press surfaces a confirm dialog showing the "
                    "selected mode + interface, requiring the "
                    "operator to type the literal word `pineap` "
                    "before the confirm button enables. Notifications "
                    "service logs the start + mode + pool size so "
                    "the bell-drawer audit trail captures every "
                    "rogue-AP launch."
                ),
            },

            # ---- Management UI deny-list interaction ----
            {
                "command": "(reference) Why the deny-list matters here",
                "description": (
                    "When PineAP advertises an open SSID and a "
                    "victim phone associates, the phone gets a DHCP "
                    "lease on the rogue subnet (10.0.0.0/24 typical) "
                    "and is now on the same L2 broadcast as the "
                    "platform's management UI on wlan0. The S04.5 "
                    "management-access deny-list, configured for the "
                    "rogue subnet, blocks the victim's source IPs at "
                    "the WSGI layer before any auth even runs. The "
                    "Settings tab surfaces a reminder banner so this "
                    "doesn't get forgotten."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 11 — Open SSID + Karma/Mana + captive sentinel + DHCP/DNS
    # ------------------------------------------------------------------
    {
        "id": "pineap-open-ssid",
        "title": "PineAP — Open AP + Karma + client recon",
        "added_in_session": 11,
        "intro": (
            "S10 built the PineAP foundation; S11 makes radio waves. "
            "Real hostapd lifecycle on wlan-ap (open multi-BSS), "
            "dnsmasq for DHCP + DNS, a captive-portal sentinel HTTP "
            "listener on the gateway IP that answers iOS/Android/"
            "Windows probes so the OS treats the rogue as 'real "
            "internet', a Scapy-based Karma/Mana injector that "
            "replies to probe requests for pool SSIDs, automatic "
            "deny-list management for the rogue subnet, and a "
            "client-recon service that parses dnsmasq's verbose log "
            "to enrich each connected client with OS fingerprint + "
            "DNS query history."
        ),
        "ui_reference": (
            "PineAP page → Open SSID tab. AP config form (primary "
            "SSID + channel + band + hidden), connected-clients "
            "table with expand-on-click DNS query history, captive "
            "probe log. Live updates via SocketIO."
        ),
        "wrapper_modules": [
            "app/tools/hostapd.py (extended: multi-BSS + bssid_for_ssid)",
            "app/services/pineap.py (full lifecycle)",
            "app/services/karma.py",
            "app/services/captive_sentinel.py",
            "app/services/client_recon.py",
            "app/routes/pineap.py (extended)",
        ],
        "commands": [
            # ---- hostapd multi-BSS ----
            {
                "command": "(reference) Open multi-BSS hostapd.conf",
                "description": (
                    "Primary BSS + ``bss=<iface>_<n>`` stanzas, each "
                    "with its own SSID and locally-administered BSSID. "
                    "Cap is chip-dependent (~4-8 for mt76x2u); we cap "
                    "at 8 in DEFAULT_MAX_BSS. Pool entries that are "
                    "pinned sort to the front; hidden entries skip; "
                    "duplicates of the primary SSID skip."
                ),
                "example_output": (
                    "interface=wlan-ap\n"
                    "driver=nl80211\n"
                    "ssid=RogueAP-Open\n"
                    "bssid=8e:59:82:a3:0b:25\n"
                    "hw_mode=g\n"
                    "channel=6\n"
                    "auth_algs=1\n"
                    "\n"
                    "bss=wlan-ap_1\n"
                    "ssid=Pinned-SSID\n"
                    "bssid=c2:26:34:0d:2a:13\n"
                    "\n"
                    "bss=wlan-ap_2\n"
                    "ssid=HomeWiFi\n"
                    "bssid=82:9f:40:5b:23:1d"
                ),
            },
            {
                "command": "(reference) Deterministic per-SSID BSSID",
                "description": (
                    "``hostapd.bssid_for_ssid(ssid, salt)`` returns a "
                    "MAC by hashing ``salt || ssid`` (BLAKE2b → 6 "
                    "bytes), forcing the locally-administered bit on "
                    "and the multicast bit off. Salt lives in "
                    "$DATA_DIR/pineap_state.json — auto-generated on "
                    "first save. Same SSID always gets the same MAC "
                    "across reboots (returning victims see a familiar "
                    "BSSID); different SSIDs get different MACs (no "
                    "tell-tale shared BSSID across the pool)."
                ),
            },

            # ---- Open AP bring-up sequence ----
            {
                "command": "(sequence) Open AP bring-up on wlan-ap",
                "description": (
                    "Each step is a single subprocess call wrapped in "
                    "our tool modules. Order matters:\n"
                    "  1. rfkill unblock wifi                — soft-unblock\n"
                    "  2. nmcli device set wlan-ap managed no — NM hands off\n"
                    "  3. ip addr flush dev wlan-ap          — kill stale state\n"
                    "  4. ip addr add 10.0.0.1/24 dev wlan-ap — gateway IP\n"
                    "  5. ip link set wlan-ap up              — bring iface up\n"
                    "  6. dnsmasq -C <conf>                   — DHCP+DNS server\n"
                    "  7. hostapd <conf>                       — beacon broadcaster\n"
                    "Stop reverses 6,7 (SIGTERM via JobManager) then "
                    "5,4 (ip link down + flush)."
                ),
            },

            # ---- dnsmasq verbose-log config ----
            {
                "command": "(reference) dnsmasq.conf with log-dhcp + log-queries",
                "description": (
                    "PineAP's dnsmasq is launched with the verbose "
                    "options enabled, plus log-facility pointing at a "
                    "specific file (not syslog) so we can tail it "
                    "without root syslog perms. Key fields:\n"
                    "  log-dhcp        — emit DHCP DISCOVER/REQUEST/ACK lines\n"
                    "  log-queries     — emit a line per DNS query\n"
                    "  log-facility=/tmp/pipineapple-pineap-dnsmasq.log\n"
                    "  dhcp-leasefile=/tmp/pipineapple-pineap-dnsmasq.leases\n"
                    "  forward DNS to 1.1.1.1 + 8.8.8.8 so association "
                    "succeeds end-to-end."
                ),
            },

            # ---- DHCP option-55 fingerprinting ----
            {
                "command": "(reference) DHCP option 55 → OS fingerprint",
                "description": (
                    "Option 55 (Parameter Request List) is the ordered "
                    "list of DHCP options the client wants in the "
                    "offer. The list shape is remarkably stable per "
                    "OS family:\n"
                    "  iOS:     1,3,6,15,119,252\n"
                    "  macOS:   1,121,3,6,15,119,252,95,44,46  (or similar)\n"
                    "  Android: 1,3,6,15,26,28,51,58,59,43\n"
                    "  Windows: 1,3,6,15,31,33,43,44,46,47,121,249,252\n"
                    "  Linux dhclient: 1,28,2,3,15,6,119,12,44,47,26,121,42\n"
                    "client_recon.py keeps a small table; longest-"
                    "prefix-match wins."
                ),
            },

            # ---- Captive portal sentinels ----
            {
                "command": "(reference) Captive-portal probe endpoints",
                "description": (
                    "Every modern OS probes a known URL to decide "
                    "'real internet vs captive portal'. Our sentinel "
                    "binds 10.0.0.1:80 and answers each truthfully:\n"
                    "  iOS    : GET /hotspot-detect.html → 200 'Success' HTML\n"
                    "  Android: GET /generate_204         → 204 No Content\n"
                    "  Windows: GET /connecttest.txt      → 200 'Microsoft Connect Test'\n"
                    "  Firefox: GET /canonical.html       → 200 success HTML\n"
                    "Truthful responses make the OS mark the network "
                    "as healthy so app traffic flows. The S17 MITM "
                    "module will add a 'lie' toggle to force the OS "
                    "into captive-portal mode (system browser pops up "
                    "the rogue's landing page)."
                ),
            },

            # ---- Karma vs Mana ----
            {
                "command": "(reference) Karma vs Mana — pool-only is the safer default",
                "description": (
                    "Classical Karma (Hak5 default): reply to every "
                    "directed probe request with a probe-response "
                    "claiming the requested SSID. Maximum blast radius. "
                    "\n\nMana (sensepost refinement, our default): only "
                    "reply to probes whose SSID is in the curated pool. "
                    "Bounded collateral — the operator chose what to "
                    "impersonate. The pool gets auto-populated from "
                    "recon scans + probe-request observations (S10), "
                    "and the operator can pin / hide / clear it."
                ),
            },
            {
                "command": "(reference) Probe-response frame construction",
                "description": (
                    "Karma can't ride hostapd alone — hostapd only "
                    "responds to probes for SSIDs it advertises. We run "
                    "a parallel Scapy sniffer on wlan-mon-5g (recon is "
                    "paused while Karma is up) and inject:\n"
                    "  RadioTap / Dot11(type=0, subtype=5, addr1=client,\n"
                    "                    addr2=our_bssid, addr3=our_bssid)\n"
                    "  / Dot11ProbeResp(beacon_interval=100, cap=0x0021)\n"
                    "  / Dot11Elt(ID=0,  info=ssid)            # SSID IE\n"
                    "  / Dot11Elt(ID=1,  info=basic_rates)     # rates\n"
                    "  / Dot11Elt(ID=3,  info=bytes([channel])) # DS Param Set\n"
                    "  / Dot11Elt(ID=50, info=extended_rates)\n"
                    "cap=0x0021 = ESS + Short Preamble, Privacy=0 (open). "
                    "Without the DS Parameter Set IE, the client doesn't "
                    "know what channel to switch to for the follow-up."
                ),
            },

            # ---- Channel coordination ----
            {
                "command": "(reference) Why the injector locks to hostapd's channel",
                "description": (
                    "Clients scanning hop fast (~50ms per channel). To "
                    "reply to a probe, you must be on the SAME channel "
                    "as the client at the moment the probe is sent. "
                    "Options: hop ourselves (halves beacon presence on "
                    "every channel) or stick on hostapd's channel and "
                    "accept that we only catch probes the client sends "
                    "on that channel during its scan. Hak5 takes the "
                    "lock approach; so do we. Probes are frequent "
                    "enough that we catch one within a few seconds in "
                    "practice."
                ),
            },

            # ---- Rate limiting ----
            {
                "command": "(behavior) Per-(client, SSID) rate limit, 30s",
                "description": (
                    "Without dedup, a client's scan burst (10+ probes "
                    "per second for a few seconds) would get 10+ "
                    "identical probe-responses. We track "
                    "{(client_mac, ssid) → last_reply_ts} and drop "
                    "replies within 30s of the previous. Map capped at "
                    "5000 entries with GC on threshold."
                ),
            },

            # ---- Recon coordination ----
            {
                "command": "(behavior) Recon pause during Advanced mode",
                "description": (
                    "When PineAP starts in Advanced mode, "
                    "recon.stop_scan() runs first to free wlan-mon-5g. "
                    "On stop, recon.start_scan() restores the previous "
                    "scan with the same settings. 2.4G recon on "
                    "wlan-mon-2g keeps running; only 5G coverage "
                    "drops while Karma is live. Trade-off documented "
                    "in the journal."
                ),
            },

            # ---- Deny-list auto-add ----
            {
                "command": "(behavior) Auto-add rogue subnet to deny-list",
                "description": (
                    "On PineAP start: access_control.add_cidr("
                    "10.0.0.0/24). On stop: remove_cidr. Means a "
                    "victim phone that auto-joins the rogue and gets "
                    "an IP in that subnet can't reach the management "
                    "UI on wlan0 — the WSGI deny-list blocks them "
                    "before auth even runs. One less thing for the "
                    "operator to forget."
                ),
            },

            # ---- Live console for hardware verify ----
            {
                "command": "tail -F /tmp/pipineapple-pineap-dnsmasq.log",
                "description": (
                    "Watch the verbose dnsmasq stream the platform "
                    "tails. You'll see DHCP exchanges (dhcp-discover "
                    "→ dhcp-request → requested options → dhcp-ack), "
                    "then a flood of DNS queries the moment the client "
                    "captive-probes (captive.apple.com, "
                    "connectivitycheck.gstatic.com, etc.)."
                ),
            },
            {
                "command": "sudo iw dev wlan-ap station dump",
                "description": (
                    "Live list of stations associated with hostapd. "
                    "Useful console cross-check for the UI's "
                    "'Connected Clients' table — same data, different "
                    "lens. Shows signal strength + tx/rx bytes per "
                    "station, which we don't surface in the UI yet."
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

    # ------------------------------------------------------------------
    # Session 12.5 — Captive-portal credential phishing
    # ------------------------------------------------------------------
    {
        "id": "pineap-captive-portal",
        "title": "PineAP — Captive-portal phishing (verify against handshake)",
        "added_in_session": 12.5,
        "intro": (
            "The answer to 'modern clients won't hand over a handshake': "
            "stop cracking, just ask. After Evil WPA captures M1+M2, the "
            "bait-switch flips the rogue from WPA2 to an Open clone of the "
            "same SSID; the victim's device rejoins password-free, the "
            "captive sentinel (now in 'lie' mode) forces the OS captive "
            "browser, and a fake 'router firmware update' page collects the "
            "Wi-Fi password. The submitted password is verified INSTANTLY "
            "against the captured handshake — same maths hashcat does, but "
            "for one phished guess: PMK = PBKDF2-SHA1(psk, ssid, 4096, 32) "
            "→ PTK → recompute the M2 MIC → compare. No wordlist, no GPU. "
            "Default OFF, opt-in behind a stronger ethics gate (type "
            "'phishing'), lab-use only."
        ),
        "ui_reference": (
            "Settings → Security → Captive-portal credential capture (opt-in "
            "+ verify mode A/B/C). PineAP → Evil WPA tab → 'launch captive-"
            "portal phishing' start option. PineAP → Captive Portal tab "
            "(status + harvested/verified credentials)."
        ),
        "wrapper_modules": [
            "app/tools/wpa_crypto.py (PMK/PTK/MIC verify)",
            "app/services/captive_portal.py (config, creds, template, arm)",
            "app/services/captive_sentinel.py (portal/lie mode + POST)",
            "app/services/pineap.py (bait-switch: WPA→Open flip)",
            "app/routes/settings.py + app/routes/pineap.py",
        ],
        "commands": [
            {
                "command": "(concept) Verify a PSK against a captured handshake",
                "description": (
                    "Per phished candidate:\n"
                    "  PMK = PBKDF2-HMAC-SHA1(psk, ssid, 4096, 32)\n"
                    "  PTK = PRF-512(PMK, \"Pairwise key expansion\",\n"
                    "        min(AA,SA)||max(AA,SA)||min(An,Sn)||max(An,Sn))\n"
                    "  KCK = PTK[0:16]\n"
                    "  MIC'= HMAC(KCK, <M2 EAPOL frame, MIC field zeroed>)[:16]\n"
                    "  valid ⟺ MIC' == captured MIC\n"
                    "MIC algorithm by EAPOL key-descriptor version: v1 → "
                    "HMAC-MD5 (TKIP), v2 → HMAC-SHA1 (CCMP/WPA2, the usual "
                    "case), v3 → AES-CMAC (802.11w/PMF). Every input comes "
                    "out of the .22000 line; SNonce is parsed from the M2 "
                    "EAPOL frame itself."
                ),
                "notes": (
                    "This is exactly one hashcat -m 22000 candidate "
                    "evaluation. Validated against the IEEE 802.11i PBKDF2 "
                    "vector (\"password\"/\"IEEE\" → f42c6f…0a12e)."
                ),
            },
            {
                "command": ("python3 -c \"from app.tools.wpa_crypto import "
                            "verify_psk_against_line as v; "
                            "print(v('YOURPSK', open('cap.22000').read().strip()))\""),
                "description": (
                    "Verify a guessed password against a real captured "
                    ".22000 line, by hand. Returns True if it's the actual "
                    "PSK, False otherwise. The fastest way to confirm the "
                    "engine on a real capture (e.g. the GL.iNet handshake "
                    "you already have with a known password)."
                ),
            },
            {
                "command": "wpa_passphrase <ssid> <passphrase>",
                "description": (
                    "Show the PSK (PMK in hex) wpa_supplicant would derive "
                    "for an SSID+passphrase — the PBKDF2 step on its own. "
                    "Handy to cross-check derive_pmk() against a system "
                    "tool. Output's psk= line is the 32-byte PMK in hex."
                ),
                "example_output": (
                    "network={\n\tssid=\"IEEE\"\n\t#psk=\"password\"\n"
                    "\tpsk=f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e\n}"
                ),
            },
            {
                "command": "(concept) Captive-portal 'lie' mode",
                "description": (
                    "Every OS probes a known URL on join (Apple "
                    "/hotspot-detect.html → expects 'Success', Android "
                    "/generate_204 → expects 204, Windows /connecttest.txt). "
                    "S11's sentinel answered truthfully so the phone treated "
                    "the rogue as real internet. Portal mode does the "
                    "opposite: it answers every probe with the landing-page "
                    "HTML instead of the expected token, so the OS decides "
                    "it's behind a captive portal and pops its sign-in "
                    "browser straight onto our page."
                ),
            },
            {
                "command": "cat $PIPINEAPPLE_DATA_DIR/captive_template.html",
                "description": (
                    "Operator custom landing page (optional). If present it "
                    "overrides the built-in firmware-update template. Use "
                    "{ssid} and {msg} placeholders and a form POSTing a "
                    "'psk' field to /portal/submit. Without it, the built-in "
                    "generic 'Router firmware update' page is served."
                ),
            },
            {
                "command": "cat $PIPINEAPPLE_DATA_DIR/captive_creds.json | python3 -m json.tool",
                "description": (
                    "Harvested credential attempts: each submitted password "
                    "with client MAC/IP, SSID, and the verify-against-"
                    "handshake result (true/false/null). The Captive Portal "
                    "tab renders this live; verify mode A/B/C only changes "
                    "what the *victim* is told, not what's recorded."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 12 — PineAP Evil WPA (partial-handshake harvest)
    # ------------------------------------------------------------------
    {
        "id": "pineap-evil-wpa",
        "title": "PineAP — Evil WPA (partial-handshake harvest)",
        "added_in_session": 12,
        "intro": (
            "Evil WPA clones a WPA2-PSK network — same SSID, same channel "
            "— but stands it up with a fresh random passphrase. A device "
            "that has the real network saved will try to auto-join and "
            "begins the 4-way handshake. We capture M1+M2, which already "
            "contains a MIC computed from the PMK; the client then fails "
            "at M3 against our wrong PSK and gives up. We never learn the "
            "real PSK on the air — but the captured M1+M2 is a crackable "
            "hashcat 22000 target, so the PSK can be recovered offline. "
            "This is the higher-value sibling of S11's Karma: instead of "
            "just luring opens, it harvests crackable WPA material."
        ),
        "ui_reference": (
            "PineAP page → Evil WPA tab (config, live EAPOL-sniffer stats, "
            "harvested-partials table). Recon AP slide-out → 'Clone to "
            "PineAP' button (WPA targets only). Harvested partials appear "
            "on the Handshakes page tagged source='Evil WPA' and dispatch "
            "to Crack through the same flow as recon captures."
        ),
        "wrapper_modules": [
            "app/services/evil_wpa.py (EAPOL sniffer + extractor)",
            "app/services/pineap.py (security_mode=wpa2, random PSK, clone)",
            "app/services/handshakes.py (register_external_capture)",
            "app/tools/hcxpcapngtool.py (pcap → .22000)",
            "app/routes/pineap.py (/pineap/evil-wpa/*)",
        ],
        "commands": [
            # ---- why M1+M2 is enough ----
            {
                "command": "(concept) WPA2 4-way handshake — what each message leaks",
                "description": (
                    "M1 (AP→STA): ANonce, in the clear.\n"
                    "M2 (STA→AP): SNonce + a MIC over M2, keyed by the "
                    "PTK. The PTK is derived from PMK + ANonce + SNonce + "
                    "both MACs — everything except the PMK is now known to "
                    "a sniffer. The MIC is the verifier.\n"
                    "M3/M4: install + confirm; not needed to crack.\n"
                    "So with M1+M2 a cracker can, for each candidate "
                    "passphrase, derive PMK = PBKDF2-SHA1(psk, ssid, 4096, "
                    "256), then PTK, recompute the MIC, and compare. A "
                    "match means the guess is the real PSK. That's exactly "
                    "what `hashcat -m 22000` does."
                ),
                "notes": (
                    "Our rogue AP's random PSK is irrelevant to this — the "
                    "client computes M2's MIC with the REAL PMK it derived "
                    "from the saved password, before it ever checks whether "
                    "our M3 verifies. We grab M2 and discard the failed "
                    "association."
                ),
            },
            # ---- the rogue hostapd config ----
            {
                "command": "(reference) WPA2-PSK rogue hostapd.conf",
                "description": (
                    "Same renderer as the Open SSID tab, but with a "
                    "passphrase. The platform generates a fresh "
                    "secrets.token_urlsafe(16) PSK at every Start "
                    "(persisted to pineap_state.json as last_rogue_psk and "
                    "echoed to the notification drawer). Randomising it "
                    "guarantees we never accidentally complete a real "
                    "association — we only ever want M1+M2."
                ),
                "example_output": (
                    "interface=wlan-ap\n"
                    "driver=nl80211\n"
                    "ssid=HomeNet\n"
                    "bssid=8e:59:82:71:a3:0b\n"
                    "hw_mode=g\n"
                    "channel=6\n"
                    "auth_algs=1\n"
                    "wpa=2\n"
                    "wpa_key_mgmt=WPA-PSK\n"
                    "rsn_pairwise=CCMP\n"
                    "wpa_passphrase=Hf8xK2-qVxN7pLrZ9aQ1bw"
                ),
                "notes": (
                    "Cloning sets the SSID + channel from the Recon target "
                    "so the beacon looks like home. The BSSID is our "
                    "deterministic salted MAC (bssid_for_ssid), not the "
                    "real AP's — a returning victim sees a familiar SSID, "
                    "and the Handshakes page records our rogue BSSID so "
                    "you can tell captures apart."
                ),
            },
            # ---- the EAPOL sniffer ----
            {
                "command": "(reference) Scapy EAPOL filter on wlan-mon-5g",
                "description": (
                    "evil_wpa.py runs a Scapy sniff() on the monitor radio "
                    "(the same wlan-mon-5g Karma uses — only one at a "
                    "time), locked to hostapd's channel. lfilter keeps:\n"
                    "  • mgmt frames (type 0) involving our BSSID — auth/"
                    "assoc give hcxpcapngtool the SSID context;\n"
                    "  • data frames (type 2) carrying EAPOL (the M1-M4 "
                    "frames).\n"
                    "Everything kept is written to a per-session "
                    "pcapng (RadioTap linktype 127). An extractor thread "
                    "converts the pcap to .22000 every 30s and registers "
                    "any new partials."
                ),
            },
            {
                "command": "sudo iw dev wlan-mon-5g set channel 6",
                "description": (
                    "Pin the monitor radio to the rogue AP's channel "
                    "before sniffing — a client's 4-way only happens on "
                    "the AP's channel, so an unpinned hopping monitor "
                    "would miss most of it. The service does this via "
                    "iw.set_channel() at start."
                ),
            },
            # ---- manual reproduction on the Pi ----
            {
                "command": "sudo hostapd /tmp/pipineapple-pineap-hostapd.conf",
                "description": (
                    "Run the rogue WPA2 AP by hand (foreground). Watch the "
                    "association attempts scroll past — a victim that has "
                    "the SSID saved will show 'authenticated' → "
                    "'associated' → EAPOL 1/4, 2/4, then a 4-way timeout "
                    "as M3 fails against the random PSK."
                ),
                "notes": (
                    "The platform launches this via the JobManager; "
                    "running it by hand is the way to see the handshake "
                    "progression live."
                ),
            },
            {
                "command": "sudo tcpdump -i wlan-mon-5g -w /tmp/evil.pcapng "
                           "'wlan type mgt or (wlan type data and ether proto 0x888e)'",
                "description": (
                    "Capture the same frames the service captures, by "
                    "hand. ether proto 0x888e is EAPOL. -w writes pcapng. "
                    "Ctrl-C to stop. This is the audit-trail file the UI "
                    "lets you download from the Handshakes page."
                ),
                "notes": (
                    "tcpdump's BPF runs in-kernel (cheaper than Scapy's "
                    "Python lfilter); we use Scapy in the service for "
                    "consistency with Karma's injector and easy per-frame "
                    "bookkeeping, but tcpdump is the faster manual tool."
                ),
            },
            {
                "command": "hcxpcapngtool -o /tmp/evil.22000 /tmp/evil.pcapng",
                "description": (
                    "Convert the capture to hashcat 22000 format. "
                    "hcxpcapngtool writes one line per crackable target — "
                    "it only emits a WPA*02 (EAPOL) line if it found a "
                    "usable M1+M2 (or M2+M3) pair with a MIC, so a line "
                    "appearing IS the signal that you have a crackable "
                    "partial. The service runs exactly this under the "
                    "hood every 30s."
                ),
                "example_output": (
                    "WPA*02*<mic>*<ap_mac>*<sta_mac>*<essid_hex>*"
                    "<anonce>*<eapol>*<flags>"
                ),
                "notes": (
                    "Field 2 is the type: 01 = PMKID, 02 = EAPOL handshake "
                    "(what Evil WPA produces). Field 6 is the ESSID in hex "
                    "— `echo 486f6d654e6574 | xxd -r -p` → HomeNet."
                ),
            },
            {
                "command": "hashcat -m 22000 /tmp/evil.22000 /path/to/wordlist.txt",
                "description": (
                    "Crack the partial offline. mode 22000 (WPA-PBKDF2-"
                    "PMKID+EAPOL) handles both PMKID and EAPOL lines. The "
                    "Crack button on the Handshakes page dispatches this "
                    "to a configured remote (Mac/Jetson) over SSH — the "
                    "Pi 5 itself is too slow and previously segfaulted (see "
                    "S09 notes). The partial harvested by Evil WPA is the "
                    "same kind of 22000 line a full capture produces, so "
                    "it cracks identically."
                ),
            },
            {
                "command": "(operational) why an Evil WPA capture comes up empty",
                "description": (
                    "Hardware-learned checklist when frames/EAPOL stay at "
                    "0 (S12 first run):\n"
                    "  1. Sniffer radio actually up + on the AP's channel? "
                    "`iw dev wlan-mon-5g info` must show the channel and not "
                    "be down — `iw set channel` is a no-op on a down iface. "
                    "(The service now re-asserts this in the sniff loop.)\n"
                    "  2. Is the 4-way even happening? hostapd showing "
                    "`authenticated` but never `associated` = the client is "
                    "picking the REAL AP. Power the real AP off so the twin "
                    "is the only SSID on air.\n"
                    "  3. Client cooperation: iOS Private-MAC, WPA3/SAE, and "
                    "WPA2+PMF clients associate but won't complete the 4-way "
                    "against a WPA2-PSK twin — by design. Use a laptop "
                    "(`nmcli device wifi connect <ssid> password <psk>`) for "
                    "a clean, deterministic handshake.\n"
                    "  4. Clients stop auto-retrying after one PSK failure "
                    "(our random M3) — forget+rejoin to force a fresh attempt."
                ),
                "notes": (
                    "Evil WPA harvests M1+M2 from WPA2-PSK networks with "
                    "cooperative clients. It is not a WPA3/PMF attack — those "
                    "are specifically hardened against handshake capture."
                ),
            },
            {
                "command": "sudo nmcli device wifi connect TL password <psk>",
                "description": (
                    "Drive a clean 4-way from a Linux laptop against the "
                    "twin — the reliable way to prove capture works. It "
                    "associates, exchanges M1/M2, fails on M3 (our random "
                    "PSK), and you've got the partial. Far more predictable "
                    "than a phone."
                ),
            },
            {
                "command": "(concept) Evil-twin deauth coupling — three radios",
                "description": (
                    "Passive Evil WPA waits for a device to roam to the "
                    "clone on its own. The active play deauths the REAL AP "
                    "so its clients drop and re-associate — some land on "
                    "our same-SSID clone and start the 4-way. While it "
                    "runs:\n"
                    "  wlan-ap      → rogue WPA2 hostapd (the twin)\n"
                    "  wlan-mon-5g  → EAPOL sniffer\n"
                    "  wlan-mon-2g  → deauth injection (free; recon paused)\n"
                    "All three pinned to the target's channel. Opt-in, "
                    "only when cloned from Recon (we need the real BSSID), "
                    "default off, lab-only."
                ),
                "notes": (
                    "Hard limit: 802.11w / MFP. Deauth frames are "
                    "management frames, so an MFP-required AP "
                    "cryptographically rejects them and nobody is "
                    "dislodged. We parse rsn.mfp_required from the beacon "
                    "and warn in the UI; the toggle still lets you opt in "
                    "but it'll be a no-op there. WPA3 mandates MFP."
                ),
            },
            {
                "command": "sudo aireplay-ng --deauth 10 -a <REAL_AP_BSSID> wlan-mon-2g",
                "description": (
                    "Broadcast deauth at the real AP (no -c → destination "
                    "ff:ff:ff:ff:ff:ff, hits every associated client). "
                    "This is exactly what evil_wpa.py's deauth loop fires "
                    "every 5s on the spare radio when the coupling is "
                    "armed. The radio must already be monitor + locked to "
                    "the AP's channel (the service does that first)."
                ),
                "notes": (
                    "-a is the AP BSSID; add -c <STA_MAC> for a targeted "
                    "single-client deauth instead of broadcast. aireplay "
                    "returns non-zero if the radio isn't in monitor mode, "
                    "the channel isn't pinned, or MFP rejected the frames."
                ),
            },
            {
                "command": "cat $PIPINEAPPLE_DATA_DIR/handshakes/index.json | python3 -m json.tool",
                "description": (
                    "Inspect the persisted capture index. Evil WPA "
                    "partials are registered here with source='Evil WPA', "
                    "a pcap_relative_path pointing back into evil_wpa/<"
                    "session>/, a single-line hash_22000_relative_path, "
                    "and crackable=true. That's what makes them show up "
                    "on the Handshakes page and enables their Crack button."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 13 — PineAP Impersonation / Filtering / Clients
    # ------------------------------------------------------------------
    {
        "id": "pineap-impersonation-filtering-clients",
        "title": "PineAP — Impersonation, Filtering & Clients",
        "added_in_session": 13,
        "intro": (
            "The Phase D finale: broadcast a pool of fake SSIDs, control "
            "who can connect, and manage/kick the clients that do. "
            "Impersonation rotates the broadcast SSID through the pool "
            "(the Alfa can only beacon one BSS at a time — cap=1 — so "
            "rotation replaces true multi-BSS). Filtering maps to "
            "hostapd's native MAC ACL. The Clients tab kicks associated "
            "stations over the hostapd control socket."
        ),
        "ui_reference": (
            "PineAP → Impersonation tab (rotation enable + dwell + BSSID "
            "strategy + Karma stats), Filtering tab (client-MAC + SSID "
            "allow/deny), Clients tab (connected list + Kick)."
        ),
        "wrapper_modules": [
            "app/tools/hostapd.py (macaddr_acl + accept/deny_mac_file)",
            "app/tools/hostapd_cli.py (deauthenticate/disassociate/reload)",
            "app/services/pineap.py (filters, impersonation rotation)",
            "app/routes/pineap.py (/filters, /impersonation, /clients/<mac>/kick)",
        ],
        "commands": [
            {
                "command": "(concept) SSID rotation vs multi-BSS",
                "description": (
                    "A real Pineapple beacons the whole impersonation pool "
                    "at once via multiple BSSes. The mt76x2u Alfa under Pi "
                    "OS Trixie practically caps at 1 BSS (S11 finding), so "
                    "we ROTATE instead: rewrite hostapd.conf with the next "
                    "pool SSID + its BSSID and `hostapd_cli reload` every "
                    "dwell. Each SSID beacons for its window; a device "
                    "probing for it can latch on while it's up. Cheaper "
                    "than a full daemon restart; falls back to a job "
                    "restart if a hostapd build ignores SSID changes on "
                    "reload."
                ),
                "notes": (
                    "BSSID strategy: per-ssid (deterministic salted MAC, "
                    "default — returning victims see a stable BSSID), "
                    "shared (one MAC for all — a tell), or random "
                    "(fresh per rotation — evades tracking, no stability)."
                ),
            },
            {
                "command": "(reference) hostapd MAC ACL",
                "description": (
                    "Client filtering uses hostapd's native ACL on the "
                    "primary BSS:\n"
                    "  allow-list: macaddr_acl=1 + accept_mac_file=<path>\n"
                    "  deny-list:  macaddr_acl=0 + deny_mac_file=<path>\n"
                    "Each file is one MAC per line. PiPineapple writes them "
                    "to /tmp/pipineapple-pineap-{accept,deny}-mac and "
                    "references them in the rendered config. Applied on "
                    "Start (we don't hot-reload the ACL mid-session on this "
                    "driver)."
                ),
                "example_output": (
                    "macaddr_acl=1\n"
                    "accept_mac_file=/tmp/pipineapple-pineap-accept-mac\n"
                    "# accept-mac file:\n"
                    "aa:bb:cc:dd:ee:ff\n11:22:33:44:55:66"
                ),
            },
            {
                "command": "sudo hostapd_cli -i wlan-ap deauthenticate <mac>",
                "description": (
                    "Kick a connected client off the rogue AP (the Clients "
                    "tab's Kick button). deauth forces a full re-auth; "
                    "`disassociate <mac>` is the gentler boot. Pair with a "
                    "deny-list entry to keep them out."
                ),
                "notes": (
                    "`hostapd_cli -i wlan-ap all_sta` lists currently-"
                    "associated stations straight from the daemon — a "
                    "ground-truth cross-check against the lease-file view."
                ),
            },
            {
                "command": "sudo hostapd_cli -i wlan-ap reload",
                "description": (
                    "Re-read hostapd.conf into the running daemon. The "
                    "impersonation rotation rewrites the config (next SSID "
                    "+ BSSID) then calls this every dwell to swap the "
                    "broadcast SSID without a full restart."
                ),
            },
            {
                "command": "cat /tmp/pipineapple-pineap-deny-mac",
                "description": (
                    "Inspect the generated deny-list MAC file hostapd is "
                    "enforcing. Editing the list in the Filtering tab + "
                    "restarting regenerates it."
                ),
            },
        ],
    },

    # ------------------------------------------------------------------
    # Session 14 — Campaigns (scripted assessment runs + reports)
    # ------------------------------------------------------------------
    {
        "id": "campaigns",
        "title": "Campaigns — scripted runs & reports",
        "added_in_session": 14,
        "intro": (
            "A campaign is the abstraction that makes the platform usable "
            "for a real engagement: pick a template, set a window, hit Run, "
            "and the platform orchestrates recon / PineAP / capture and "
            "writes a JSON + HTML report. Three templates — Reconnaissance "
            "(monitor only), Client Device Assessment Passive (recon + "
            "in-window handshakes), and Active (PineAP rogue + Karma + "
            "optional broadcast deauth, ethics-gated). Timed window auto-"
            "stops; window=0 runs until you Stop."
        ),
        "ui_reference": (
            "Campaigns page → Run tab (template cards + window) + Reports "
            "tab (past runs, JSON/HTML download)."
        ),
        "wrapper_modules": [
            "app/services/campaigns.py",
            "app/routes/campaigns.py",
        ],
        "commands": [
            {
                "command": "(concept) campaign = orchestration + report",
                "description": (
                    "The service doesn't invent new attacks — it sequences "
                    "the ones you already built. recon/passive start the "
                    "recon scan for the window; active brings up the PineAP "
                    "rogue (advanced/open) and, with a lab target BSSID, a "
                    "broadcast-deauth loop. At the deadline (or Stop) it "
                    "tears everything down and snapshots recon APs/clients, "
                    "in-window handshakes, rogue clients, captive creds, and "
                    "Karma stats into report.json + report.html."
                ),
                "notes": (
                    "Radio reality: active brings up PineAP which pauses "
                    "recon (one radio can't scan + rogue at once), so an "
                    "active run is rogue-centric. recon/passive use the "
                    "monitor radios only."
                ),
            },
            {
                "command": "ls $PIPINEAPPLE_DATA_DIR/campaigns/<id>/",
                "description": (
                    "Each run writes report.json (machine-readable, full "
                    "detail) + report.html (styled, shareable) under its "
                    "run id. The index.json lists all runs with a compact "
                    "summary (AP/client/handshake/cred counts)."
                ),
                "example_output": "report.html  report.json",
            },
            {
                "command": "(reference) curl the campaign API",
                "description": (
                    "POST /campaigns/start {template,duration_secs,confirm?,"
                    "target_bssid?} → start; POST /campaigns/stop → stop + "
                    "report; GET /campaigns/status → live run + step log; "
                    "GET /campaigns/reports → index; GET "
                    "/campaigns/reports/<id>/<json|html> → download. Active "
                    "needs confirm='active' (the ethics gate)."
                ),
            },
        ],
    },
]


def get_sections() -> list[dict[str, Any]]:
    """Return the full list of Learning Centre sections."""
    return LEARNING_SECTIONS
