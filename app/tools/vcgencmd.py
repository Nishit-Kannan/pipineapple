"""Wrapper for the Pi-specific ``vcgencmd`` utility.

For Session 01 we only call ``measure_temp``. Future sessions may use
``measure_volts``, ``measure_clock``, and the throttling status flags
(useful for warning when sustained capture is causing CPU throttle).
"""

from __future__ import annotations

import logging

from app.tools._common import run, stub_mode
from app.tools.proc import read_cpu_temp_c

log = logging.getLogger(__name__)


def measure_temp_c() -> float | None:
    """Return the SoC temperature in Celsius.

    Tries ``vcgencmd measure_temp`` first (Pi-native, fastest, requires
    the user to be in the ``video`` group). Falls back to the /sys read
    if vcgencmd isn't available — that's the common case in containers
    and on non-Pi Linux dev boxes.

    Output of ``vcgencmd measure_temp`` looks like ``temp=47.2'C\n``.
    """
    if stub_mode():
        return read_cpu_temp_c()  # stub mode handled there
    result = run(["vcgencmd", "measure_temp"], timeout=2.0)
    if result.returncode == 0 and result.stdout.startswith("temp="):
        try:
            return float(result.stdout.strip().removeprefix("temp=").rstrip("'C"))
        except ValueError:
            log.debug("vcgencmd unparseable output: %r", result.stdout)
    # Fall back to /sys
    return read_cpu_temp_c()
