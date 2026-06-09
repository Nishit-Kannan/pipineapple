"""hostapd wrapper — userspace AP daemon.

For S04.6 this drives the management AP on wlan0. The same wrapper is
reused in Phase D for the rogue AP on wlan-ap, with a different config
template. The two won't run simultaneously because they target different
interfaces, but the orchestration service ensures only one config per
interface at a time.

The daemon is launched via the JobManager so its lifecycle is owned by
the platform (no zombies, proper SIGTERM teardown, stdout streamed to
the per-job SocketIO room).

S11 added: multi-BSS rendering for PineAP (advertise N SSIDs from one
radio), open-mode (no wpa= lines), and a deterministic per-SSID BSSID
generator so the same SSID always gets the same BSSID across restarts
(returning victims see a "familiar" MAC).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Literal

from app.tools._common import stub_mode

log = logging.getLogger(__name__)


# Chip-level cap on simultaneous BSSes per radio. Real ceiling varies
# by chip/firmware:
#   * mt76x2u in our Alfa AWUS036ACM under Pi OS Trixie kernel 6.12 —
#     `iw phy info` advertises `total <= 2` but in practice running two
#     AP-mode interfaces simultaneously breaks beaconing. The primary
#     BSS makes it to `type AP` but tx-packets stays at 0; the secondary
#     BSS gets created but stays at `type managed` and the half-init
#     halts beacons on both. Verified empirically on Nishit's hardware.
#   * Other chips (ath9k, ath10k) genuinely support 8-16 BSSes.
#
# So: 1 is the safe default that works everywhere. The Hak5 "broadcast
# the whole pool" pattern needs the `hostapd_cli set_ssid` rotation
# approach instead of multi-BSS — cycle the primary SSID through pool
# entries every ~500ms. That's deferred to a later session.
#
# TODO: feature-probe `iw phy info` for the advertised cap AND smoke-
#       test with 2 BSSes at start time; fall back to 1 if the second
#       BSS doesn't make it to type=AP within a couple seconds.
DEFAULT_MAX_BSS = 1


def render_config(
    iface: str,
    ssid: str,
    password: str | None,
    channel: int = 6,
    hw_mode: Literal["g", "a"] = "g",
    country_code: str | None = None,
    *,
    extra_bsses: list[dict] | None = None,
    primary_bssid: str | None = None,
    hidden: bool = False,
    macaddr_acl: int | None = None,
    accept_mac_file: str | None = None,
    deny_mac_file: str | None = None,
) -> str:
    """Render a hostapd.conf for an AP on ``iface``.

    Defaults are 2.4 GHz channel 6 (Pi 5 onboard radio doesn't reliably
    do 5 GHz AP mode without firmware fiddling). For the rogue AP on
    wlan-ap in Phase D, pass hw_mode='a' and a 5 GHz channel for 5 GHz
    or stay on 'g' for 2.4 GHz.

    ``password`` triggers WPA2-PSK if set, open AP if None or empty.

    ``primary_bssid`` overrides the chip's EEPROM MAC for the primary
    BSS — only used by PineAP for stable rogue-AP MAC. None keeps the
    EEPROM default (management AP path).

    ``hidden`` sets ``ignore_broadcast_ssid=1`` so the primary SSID
    isn't advertised in beacons (clients have to know the name to
    associate). Probe responses still answer when asked by SSID.

    Client MAC filtering (S13 Filtering tab) maps to hostapd's native
    MAC ACL on the primary BSS:

      * ``macaddr_acl=0`` + ``deny_mac_file`` — accept everyone except
        the listed MACs (deny-list mode).
      * ``macaddr_acl=1`` + ``accept_mac_file`` — accept only the listed
        MACs (allow-list mode).

    Pass ``macaddr_acl=None`` (default) to omit ACL lines entirely
    (accept all — the management-AP and pre-S13 behaviour).

    ``extra_bsses`` is a list of additional virtual APs to advertise
    from the same radio. Each is a dict::

        {
          "ssid":     "linksys",
          "bssid":    "02:11:22:33:44:55",   # required, unique per BSS
          "password": None,                   # None = open
          "hidden":   False,
        }

    First entry shares channel + hw_mode with the primary. Up to
    ``DEFAULT_MAX_BSS - 1`` extras can be passed; the chip will reject
    more than it supports at hostapd start time.
    """
    lines = [
        f"interface={iface}",
        "driver=nl80211",
        f"ssid={ssid}",
        f"hw_mode={hw_mode}",
        f"channel={channel}",
        "auth_algs=1",
        "wmm_enabled=1",
    ]
    if primary_bssid:
        lines.insert(3, f"bssid={primary_bssid}")
    if hidden:
        # ignore_broadcast_ssid=1: send beacons with empty SSID IE, still
        # respond to directed probes that name the SSID
        lines.append("ignore_broadcast_ssid=1")
    if macaddr_acl is not None:
        lines.append(f"macaddr_acl={int(macaddr_acl)}")
        if macaddr_acl == 0 and deny_mac_file:
            lines.append(f"deny_mac_file={deny_mac_file}")
        elif macaddr_acl == 1 and accept_mac_file:
            lines.append(f"accept_mac_file={accept_mac_file}")
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

    # Extra BSSes (PineAP multi-SSID rendering).
    for i, extra in enumerate(extra_bsses or [], start=1):
        lines.append("")
        # bss=<iface>_<n> declares a new virtual AP. hostapd derives
        # the per-BSS interface name from this — kernel creates the
        # virtual interface at start time.
        lines.append(f"bss={iface}_{i}")
        lines.append(f"ssid={extra['ssid']}")
        bssid = extra.get("bssid")
        if not bssid:
            raise ValueError(
                f"extra BSS #{i} ({extra.get('ssid')!r}) missing 'bssid' — "
                f"each virtual BSS needs a unique MAC, use bssid_for_ssid()"
            )
        lines.append(f"bssid={bssid}")
        if extra.get("hidden"):
            lines.append("ignore_broadcast_ssid=1")
        epw = extra.get("password")
        if epw:
            lines += [
                "wpa=2",
                f"wpa_passphrase={epw}",
                "wpa_key_mgmt=WPA-PSK",
                "rsn_pairwise=CCMP",
            ]
        # Open BSS needs nothing extra — auth_algs=1 inherited

    return "\n".join(lines) + "\n"


def bssid_for_ssid(ssid: str, salt: str) -> str:
    """Deterministic BSSID for a given SSID.

    Hash ``salt || ssid`` → take 6 bytes → force locally-administered
    (bit 1 of first byte = 1) and unicast (bit 0 of first byte = 0).
    The salt lives in $DATA_DIR/pineap_state.json so BSSIDs are stable
    per-deployment but different across platforms. Returns colon-form
    lowercase MAC ('02:ab:cd:ef:01:23').

    Why deterministic: a returning victim sees the same BSSID for
    "HomeWiFi" each time the rogue is up — more convincing than a
    fresh random MAC every reboot.

    Why per-SSID: a real Pineapple targeting "Starbucks" and
    "HomeWiFi" from one BSSID is a tell. Distinct MACs per fake SSID
    look more like real-world deployments.
    """
    h = hashlib.blake2b((salt + "|" + ssid).encode("utf-8"), digest_size=6).digest()
    b0 = (h[0] | 0x02) & 0xFE          # set locally-administered, clear multicast
    return ":".join(f"{b:02x}" for b in (b0, *h[1:]))


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
