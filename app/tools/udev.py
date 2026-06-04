"""udev tooling — write sticky-name rules and trigger a re-process.

Writing to ``/etc/udev/rules.d/`` and running ``udevadm`` both require
root. The platform runs as root from Session 04 onwards.

The rules file we generate looks like::

    SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="aa:bb:cc:dd:ee:ff", NAME="wlan-mon-2g"
    ...

This binds a MAC to a stable interface name. The new name takes effect
on next interface add (unplug+replug, or reboot). ``udevadm trigger`` can
re-process devices in some cases but doesn't always rename live
interfaces; a reboot is the only reliable apply path.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.tools._common import run, stub_mode

log = logging.getLogger(__name__)

RULES_PATH = Path("/etc/udev/rules.d/99-pipineapple-adapters.rules")


def render_rules(mac_to_name: dict[str, str]) -> str:
    """Build the rules file body from a dict of {mac: friendly_name}.

    MAC addresses are lowercased — udev string-compares ATTR{address}
    case-sensitively against `cat /sys/class/net/*/address` which is
    always lowercase on Linux.
    """
    lines = [
        "# pipineapple — auto-generated. Do not edit manually; the Settings page",
        "# overwrites this file when adapter roles are reapplied.",
        "",
    ]
    for mac, name in sorted(mac_to_name.items(), key=lambda kv: kv[1]):
        mac_lc = mac.strip().lower()
        lines.append(
            f'SUBSYSTEM=="net", ACTION=="add", ATTR{{address}}=="{mac_lc}", NAME="{name}"'
        )
    return "\n".join(lines) + "\n"


def write_rules(mac_to_name: dict[str, str]) -> tuple[bool, str]:
    """Write the rules file to /etc/udev/rules.d/.

    Returns (ok, message). In stub mode writes to /tmp/ instead so the
    Mac-side dev mode can exercise the flow without actual root or udev.
    """
    body = render_rules(mac_to_name)
    target = RULES_PATH
    if stub_mode():
        target = Path("/tmp/pipineapple-udev.rules.preview")
    try:
        target.write_text(body)
    except PermissionError as e:
        return False, f"cannot write {target}: {e}"
    except OSError as e:
        return False, f"write failed: {e}"
    return True, f"wrote {target} ({len(mac_to_name)} rules)"


def reload_rules() -> tuple[bool, str]:
    """`udevadm control --reload-rules && udevadm trigger`.

    Doesn't rename live interfaces — for new names to take effect you
    have to unplug+replug the adapter or reboot the Pi. We surface a
    note about that in the UI.
    """
    if stub_mode():
        return True, "(stub) reloaded udev rules"
    r1 = run(["udevadm", "control", "--reload-rules"], timeout=5.0)
    if r1.returncode != 0:
        return False, f"reload-rules failed: {r1.stderr.strip()}"
    r2 = run(["udevadm", "trigger"], timeout=5.0)
    if r2.returncode != 0:
        return False, f"udevadm trigger failed: {r2.stderr.strip()}"
    return True, "udev rules reloaded; reboot for new names to take effect"
