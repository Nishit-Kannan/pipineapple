"""Shared helpers for tool wrappers.

Kept deliberately small. The functions here let each tool module decide
whether to shell out for real or return stub data, without any of them
depending on Flask or on each other.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Sequence

log = logging.getLogger(__name__)


def stub_mode() -> bool:
    """Return True if tool wrappers should return canned data.

    Set by the Flask config via the ``PIPINEAPPLE_USE_REAL_TOOLS`` env var
    (the factory exports the config value on app construction). When the
    var is missing we default to real tools — running on the Pi is the
    common case.
    """
    val = os.environ.get("PIPINEAPPLE_USE_REAL_TOOLS", "1").strip().lower()
    return val in ("0", "false", "no", "off")


def run(
    cmd: Sequence[str],
    *,
    timeout: float = 5.0,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a command, capture stdout/stderr as text, return the result.

    Centralised so every tool wrapper has consistent timeout, encoding,
    and logging behaviour. Callers decide whether to raise on non-zero
    exit (``check=True``) or inspect ``returncode`` themselves.
    """
    log.debug("exec: %s", " ".join(cmd))
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
        )
    except FileNotFoundError as e:
        log.warning("tool not found: %s (%s)", cmd[0], e)
        # Return a synthetic "failed" result so callers don't need to
        # special-case FileNotFoundError everywhere.
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=127, stdout="", stderr=str(e)
        )
    except subprocess.TimeoutExpired as e:
        log.warning("tool timeout: %s after %.1fs", cmd[0], timeout)
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=124, stdout="", stderr=f"timeout: {e}"
        )
