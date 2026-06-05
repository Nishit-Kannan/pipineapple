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
