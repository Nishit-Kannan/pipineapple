"""rfkill wrapper — manage soft-blocks on wireless radios.

Pi 5 frequently boots with wlan0 in a soft-RF-killed state. Symptom:
``ip link set wlan0 up`` returns ``RTNETLINK answers: Operation not
possible due to RF-kill``. Running ``rfkill unblock wifi`` clears it.

The platform calls ``unblock_wifi()`` before bringing wlan0 up for AP
mode so this never requires manual intervention.
"""

from __future__ import annotations

import logging

from app.tools._common import run, stub_mode

log = logging.getLogger(__name__)


def list_blocks() -> list[dict]:
    """Parse ``rfkill list`` output. Each entry: {id, type, name, soft_blocked, hard_blocked}."""
    if stub_mode():
        return []
    r = run(["rfkill", "list"], timeout=3.0)
    if r.returncode != 0:
        return []
    out: list[dict] = []
    current: dict | None = None
    for line in r.stdout.splitlines():
        if line and not line.startswith("\t") and ":" in line:
            # Header line: "0: phy0: Wireless LAN"
            head, _, name = line.partition(": ")
            if current is not None:
                out.append(current)
            try:
                idx = int(head)
            except ValueError:
                idx = None
            type_, _, label = name.partition(": ")
            current = {
                "id":   idx,
                "type": type_,
                "name": label or type_,
                "soft_blocked": False,
                "hard_blocked": False,
            }
        elif current is not None:
            stripped = line.strip()
            if stripped.startswith("Soft blocked:"):
                current["soft_blocked"] = stripped.endswith("yes")
            elif stripped.startswith("Hard blocked:"):
                current["hard_blocked"] = stripped.endswith("yes")
    if current is not None:
        out.append(current)
    return out


def is_wifi_blocked() -> bool:
    """True if any wlan device is soft- or hard-blocked."""
    for entry in list_blocks():
        if "wlan" in (entry.get("type") or "") or "wifi" in (entry.get("name") or "").lower():
            if entry.get("soft_blocked") or entry.get("hard_blocked"):
                return True
    return False


def unblock_wifi() -> tuple[bool, str]:
    """Soft-unblock all wifi radios. Idempotent — no-op if already unblocked."""
    if stub_mode():
        return True, "(stub) rfkill unblock wifi"
    r = run(["rfkill", "unblock", "wifi"], timeout=3.0)
    if r.returncode == 0:
        return True, "rfkill unblock wifi"
    return False, f"rfkill unblock failed: {r.stderr.strip()}"


def unblock_all() -> tuple[bool, str]:
    """Soft-unblock everything (wifi, bluetooth, etc.)."""
    if stub_mode():
        return True, "(stub) rfkill unblock all"
    r = run(["rfkill", "unblock", "all"], timeout=3.0)
    if r.returncode == 0:
        return True, "rfkill unblock all"
    return False, f"rfkill unblock failed: {r.stderr.strip()}"
