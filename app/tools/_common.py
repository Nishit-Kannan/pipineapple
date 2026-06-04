"""Shared helpers for tool wrappers.

Three concerns live here:

1. ``stub_mode()`` — read the USE_REAL_TOOLS env flag.
2. ``run()`` — the centralised subprocess.run wrapper that every tool
   uses, with consistent timeout / encoding / FileNotFoundError handling.
3. The command-stream plumbing — a polling ``ContextVar`` and a callback
   registry that the terminal service hooks into. Tools call ``run()``;
   ``run()`` checks if it's inside a polling context and, if not, fires
   the registered listeners (the terminal service is the only one
   today).

Tools do not import ``app.services.terminal`` directly — they call
listeners that the factory registers at app construction time. This
preserves the ``routes → services → tools`` dependency direction.
"""

from __future__ import annotations

import contextvars
import logging
import os
import subprocess
import time
from typing import Callable, Sequence

log = logging.getLogger(__name__)


# ---------- Stub-mode flag -----------------------------------------------
def stub_mode() -> bool:
    """Return True if tool wrappers should return canned data."""
    val = os.environ.get("PIPINEAPPLE_USE_REAL_TOOLS", "1").strip().lower()
    return val in ("0", "false", "no", "off")


# ---------- Polling context ----------------------------------------------
# ``run()`` checks this before broadcasting to the command stream. Set to
# True around routine periodic reads (sysinfo gather) so the stream isn't
# spammed with iw / ip / vcgencmd noise every 2 seconds.
_polling: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "pipineapple_polling", default=False
)


def in_polling_context() -> bool:
    return _polling.get()


class polling_context:
    """Context manager that marks a block as polling.

    Use in services that do periodic work, like the sysinfo broadcaster::

        with polling_context():
            status = sysinfo.get_system_status()
            socketio.emit("sysinfo", status)
    """

    def __enter__(self):
        self._token = _polling.set(True)
        return self

    def __exit__(self, *exc):
        _polling.reset(self._token)
        return False


# ---------- Command-stream listener registry ------------------------------
# Listeners are called with (cmd_list, source, rc, duration_ms) after
# every non-polling run(). The terminal service hooks in via
# register_command_listener().
CommandListener = Callable[[list[str], str, int | None, float | None], None]
_listeners: list[CommandListener] = []


def register_command_listener(callback: CommandListener) -> None:
    """Subscribe a callback to non-polling command executions."""
    _listeners.append(callback)


def clear_command_listeners() -> None:
    """Useful for tests."""
    _listeners.clear()


def _fire_listeners(cmd: list[str], source: str, rc: int | None, duration_ms: float | None) -> None:
    if _polling.get():
        return
    for listener in _listeners:
        try:
            listener(cmd, source, rc, duration_ms)
        except Exception:
            log.exception("command listener failed")


# ---------- The run() function tools use --------------------------------
def run(
    cmd: Sequence[str],
    *,
    timeout: float = 5.0,
    check: bool = False,
    source: str = "tool",
) -> subprocess.CompletedProcess[str]:
    """Run a command, capture stdout/stderr as text, return the result.

    Centralised so every tool wrapper has consistent timeout, encoding,
    and logging behaviour. When called outside a polling context, fires
    the registered command listeners after the call returns so the
    terminal stream can render it.

    ``source`` is a short tag that listeners can use to categorise where
    the command came from. Defaults to ``"tool"``; the JobManager uses
    its own broadcast (with ``source="job"``) for long-running ones.
    """
    cmd_list = list(cmd)
    log.debug("exec: %s", " ".join(cmd_list))
    started = time.monotonic()
    try:
        result = subprocess.run(
            cmd_list,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )
    except FileNotFoundError as e:
        log.warning("tool not found: %s (%s)", cmd_list[0], e)
        result = subprocess.CompletedProcess(
            args=cmd_list, returncode=127, stdout="", stderr=str(e)
        )
    except subprocess.TimeoutExpired as e:
        log.warning("tool timeout: %s after %.1fs", cmd_list[0], timeout)
        result = subprocess.CompletedProcess(
            args=cmd_list, returncode=124, stdout="", stderr=f"timeout: {e}"
        )

    duration_ms = (time.monotonic() - started) * 1000.0
    _fire_listeners(cmd_list, source, result.returncode, duration_ms)
    return result
