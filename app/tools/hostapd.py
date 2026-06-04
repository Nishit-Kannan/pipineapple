"""hostapd wrapper — userspace AP daemon.

For S04.6 this drives the management AP on wlan0. The same wrapper is
reused in Phase D for the rogue AP on wlan-ap, with a different config
template. The two won't run simultaneously because they target different
interfaces, but the orchestration service ensures only one config per
interface at a time.

The daemon is launched via the JobManager so its lifecycle is owned by
the platform (no zombies, proper SIGTERM teardown, stdout streamed to
the per-job SocketIO room).
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from typing import Literal

from app.tools._common import stub_mode

log = logging.getLogger(__name__)


def render_config(
    iface: str,
    ssid: str,
    password: str | None,
    channel: int = 6,
    hw_mode: Literal["g", "a"] = "g",
    country_code: str | None = None,
) -> str:
    """Render a minimal hostapd.conf for an AP on ``iface``.

    Defaults are 2.4 GHz channel 6 (Pi 5 onboard radio doesn't reliably
    do 5 GHz AP mode without firmware fiddling). For the rogue AP on
    wlan-ap in Phase D we'll pass hw_mode='a' and a 5 GHz channel.

    ``password`` triggers WPA2-PSK if set, open AP if None or empty.
    """
    lines = [
        f"interface={iface}",
        "driver=nl80211",
        f"ssid={ssid}",
        f"hw_mode={hw_mode}",
        f"channel={channel}",
        "auth_algs=1",
        "wmm_enabled=1",
        # Ignore broadcast probes (slight stealth)
        # "ignore_broadcast_ssid=0",
    ]
    if country_code:
        lines += [
            f"country_code={country_code}",
            "ieee80211d=1",
        ]
    if password:
        lines += [
            "wpa=2",
            f"wpa_passphrase={password}",
            "wpa_key_mgmt=WPA-PSK",
            "rsn_pairwise=CCMP",
        ]
    return "\n".join(lines) + "\n"


def write_config(path: Path, config_body: str) -> tuple[bool, str]:
    """Write hostapd.conf to disk with strict permissions.

    The file contains the SSID password in plaintext (that's how hostapd
    consumes it). Mode 0600 + owned by root.
    """
    if stub_mode():
        preview = Path(f"/tmp/pipineapple-hostapd-{path.name}.preview")
        preview.write_text(config_body)
        return True, f"(stub) wrote {preview}"
    log.info("hostapd.write_config -> %s", path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config_body)
        path.chmod(0o600)
    except Exception as e:
        log.exception("hostapd config write failed (%s)", path)
        return False, f"hostapd config write failed: {e}"
    log.info("hostapd config written: %s (%d bytes)", path, len(config_body))
    return True, f"wrote {path}"
