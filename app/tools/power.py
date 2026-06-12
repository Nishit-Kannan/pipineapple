"""Power control — reboot / shut down the Pi from the UI.

The command is scheduled on a short delay in a daemon thread so the HTTP
response (and the SocketIO notification) reach the browser *before* the box
starts going down — otherwise the request would hang or error as the
server dies mid-response. Uses ``systemctl reboot`` / ``systemctl poweroff``
(the app runs as root under systemd, so no sudo needed). Stub mode logs and
does nothing, so the Mac dev box never actually reboots.
"""

from __future__ import annotations

import logging
import threading

from app.tools._common import run, stub_mode

log = logging.getLogger(__name__)

_DELAY_SECONDS = 2.0
_ACTIONS = {
    "reboot":   ("systemctl", "reboot"),
    "shutdown": ("systemctl", "poweroff"),
}


def _schedule(action: str) -> None:
    cmd = _ACTIONS[action]

    def _fire() -> None:
        import time
        time.sleep(_DELAY_SECONDS)
        if stub_mode():
            log.warning("power: (stub) would run %s", " ".join(cmd))
            return
        log.warning("power: executing %s now", " ".join(cmd))
        run(list(cmd), timeout=30, source="power")

    threading.Thread(target=_fire, name=f"power-{action}", daemon=True).start()


def reboot() -> tuple[bool, str]:
    """Schedule a reboot ``_DELAY_SECONDS`` from now."""
    if stub_mode():
        log.info("power: (stub) reboot requested")
    _schedule("reboot")
    return True, f"rebooting in {_DELAY_SECONDS:.0f}s — reconnect once the Pi is back up"


def shutdown() -> tuple[bool, str]:
    """Schedule a power-off ``_DELAY_SECONDS`` from now."""
    if stub_mode():
        log.info("power: (stub) shutdown requested")
    _schedule("shutdown")
    return True, (f"shutting down in {_DELAY_SECONDS:.0f}s — the Pi will need "
                  "physical power to start again")
