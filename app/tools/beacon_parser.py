"""Beacon + probe-request parser — pcap → structured AP/client detail.

airodump-ng's CSV summarises maybe four fields per AP. To populate the
slide-out's Security and Tagged Parameters tabs we need the full
beacon body: RSN cipher/AKM/MFP bits, HT/VHT/HE capabilities, country
code, vendor-specific IEs, etc.

Same pcap (`<prefix>-01.cap`) also contains every probe request the
monitor adapter saw. Parsing those gives us per-(client, SSID) timing,
counts, and the broadcast-vs-directed distinction airodump's CSV
flattens away.

This module owns the scapy dependency. Keep it isolated: the recon
service imports parse_latest_beacon / parse_probe_requests and only
ever sees plain dicts back.

Performance: today both parsers read the whole pcap from start. Fine
for typical scan durations (minutes, MB-scale files). For long-running
scans we'd switch to ``PcapReader`` with a kept file position; not
needed yet.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.tools._common import stub_mode

log = logging.getLogger(__name__)


# ---------- RSN OUI:type lookups ---------------------------------------
# IEEE 802.11 RSN suite identifiers. OUI 00-0F-AC is "IEEE 802.11" itself;
# vendor suites use vendor OUIs (Microsoft 00-50-F2 for WPA1, etc.).
_RSN_OUI_IEEE = b"\x00\x0f\xac"
_RSN_OUI_MS   = b"\x00\x50\xf2"

_RSN_CIPHER_NAMES = {
    0: "Use group cipher",
    1: "WEP-40",
    2: "TKIP",
    3: "Reserved",
    4: "CCMP-128",
    5: "WEP-104",
    6: "BIP-CMAC-128",
    7: "Group address traffic not allowed",
    8: "GCMP-128",
    9: "GCMP-256",
    10: "CCMP-256",
    11: "BIP-GMAC-128",
    12: "BIP-GMAC-256",
    13: "BIP-CMAC-256",
}

_RSN_AKM_NAMES = {
    1: "802.1X",
    2: "PSK",
    3: "FT-802.1X",
    4: "FT-PSK",
    5: "802.1X-SHA256",
    6: "PSK-SHA256",
    7: "TDLS",
    8: "SAE",
    9: "FT-SAE",
    10: "AP-PeerKey",
    11: "802.1X-Suite-B",
    12: "802.1X-Suite-B-192",
    13: "FT-802.1X-SHA384",
    14: "FILS-SHA256",
    15: "FILS-SHA384",
    16: "FT-FILS-SHA256",
    17: "FT-FILS-SHA384",
    18: "OWE",
}


# ---------- Public API --------------------------------------------------

def parse_latest_beacon(pcap_path: str | Path, bssid: str) -> dict[str, Any] | None:
    """Return parsed IEs from the most recent beacon for ``bssid``.

    Walks the pcap end-to-end (a beacon may appear many times — typical
    100 ms cadence) and keeps the last one whose BSSID matches. Returns
    None if the pcap doesn't exist or no matching beacon is found.
    """
    if stub_mode():
        return _stub_beacon(bssid)

    p = Path(pcap_path)
    if not p.is_file():
        return None

    try:
        # Late import — scapy is the heaviest dep in the project, only
        # loaded when we actually need to parse.
        from scapy.all import PcapReader
        from scapy.layers.dot11 import Dot11, Dot11Beacon
    except ImportError:
        log.warning("scapy not installed; beacon parsing unavailable")
        return None

    target = bssid.lower()
    latest = None
    try:
        with PcapReader(str(p)) as r:
            for pkt in r:
                if not pkt.haslayer(Dot11Beacon):
                    continue
                d11 = pkt.getlayer(Dot11)
                ap = (d11.addr3 or d11.addr2 or "").lower()
                if ap == target:
                    latest = pkt
    except Exception as e:
        log.warning("beacon parse failed %s: %s", pcap_path, e)
        return None

    if latest is None:
        return None
    return _decode_beacon(latest)


def parse_probe_requests(pcap_path: str | Path) -> list[dict[str, Any]]:
    """Aggregate every probe-request frame in the pcap.

    Returns a list of dicts keyed conceptually on (client_mac, ssid).
    Each entry::

        {
          "station_mac":  "aa:bb:cc:dd:ee:ff",
          "ssid":         "HomeWiFi",       # empty string for broadcast probes
          "is_broadcast": False,             # True iff SSID IE was zero-length
          "count":        17,                # number of probe frames seen
          "first_seen":   1717603200.123,
          "last_seen":    1717603312.456,
        }

    Sorted by last_seen descending. Empty list if pcap is missing or
    scapy is unavailable.
    """
    if stub_mode():
        return _stub_probes()

    p = Path(pcap_path)
    if not p.is_file():
        return []

    try:
        from scapy.all import PcapReader
        from scapy.layers.dot11 import Dot11, Dot11Elt, Dot11ProbeReq
    except ImportError:
        log.warning("scapy not installed; probe parsing unavailable")
        return []

    # (mac, ssid) -> stats dict
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        with PcapReader(str(p)) as r:
            for pkt in r:
                if not pkt.haslayer(Dot11ProbeReq):
                    continue
                d11 = pkt.getlayer(Dot11)
                # In a probe request: addr1=DA (broadcast), addr2=SA (client),
                # addr3=BSSID (often broadcast since the client doesn't know
                # which AP it's asking).
                mac = (d11.addr2 or "").lower()
                if not mac:
                    continue
                ssid, is_broadcast = _extract_probe_ssid(pkt)
                ts = float(pkt.time) if hasattr(pkt, "time") else 0.0

                key = (mac, ssid)
                entry = agg.get(key)
                if entry is None:
                    agg[key] = {
                        "station_mac":  mac,
                        "ssid":         ssid,
                        "is_broadcast": is_broadcast,
                        "count":        1,
                        "first_seen":   ts,
                        "last_seen":    ts,
                    }
                else:
                    entry["count"] += 1
                    if ts < entry["first_seen"]:
                        entry["first_seen"] = ts
                    if ts > entry["last_seen"]:
                        entry["last_seen"] = ts
    except Exception as e:
        log.warning("probe parse failed %s: %s", pcap_path, e)
        return []

    out = list(agg.values())
    out.sort(key=lambda d: d["last_seen"], reverse=True)
    return out


# ---------- Beacon decoding internals ----------------------------------

def _decode_beacon(pkt) -> dict[str, Any]:
    """Turn a scapy beacon packet into our slide-out-friendly dict."""
    from scapy.layers.dot11 import Dot11, Dot11Elt
    d11 = pkt.getlayer(Dot11)
    bssid = (d11.addr3 or d11.addr2 or "").lower()

    ies: list[dict[str, Any]] = []
    rsn: dict[str, Any] | None = None
    ssid: str = ""
    channel: int | None = None
    country: str | None = None
    ht_present = False
    vht_present = False
    he_present = False
    vendor_ies: list[dict[str, Any]] = []

    elt = pkt.getlayer(Dot11Elt)
    while elt is not None:
        tag = int(elt.ID)
        raw = bytes(elt.info) if elt.info else b""
        length = len(raw)
        # Friendly name for the common tags
        name = _IE_NAMES.get(tag, f"Tag {tag}")
        ies.append({
            "tag":    tag,
            "name":   name,
            "length": length,
            "hex":    raw.hex(),
        })

        if tag == 0:                       # SSID
            try:
                ssid = raw.decode("utf-8", errors="replace")
            except Exception:
                ssid = ""
        elif tag == 3 and length >= 1:     # DS Parameter Set (channel)
            channel = raw[0]
        elif tag == 7 and length >= 2:     # Country
            country = raw[:2].decode("ascii", errors="replace")
        elif tag == 45:                    # HT Capabilities
            ht_present = True
        elif tag == 48:                    # RSN
            rsn = _parse_rsn(raw)
        elif tag == 191:                   # VHT Capabilities
            vht_present = True
        elif tag == 221:                   # Vendor Specific
            if length >= 4:
                oui = raw[:3].hex(":")
                vendor_type = raw[3]
                vendor_ies.append({
                    "oui":  oui,
                    "type": vendor_type,
                    "length": length,
                })
        elif tag == 255 and length >= 1:   # Element ID Extension
            ext = raw[0]
            if ext == 35:                  # HE Capabilities
                he_present = True

        # Walk to the next IE in the chain
        elt = elt.payload.getlayer(Dot11Elt)

    return {
        "bssid":      bssid,
        "ssid":       ssid,
        "channel":    channel,
        "country":    country,
        "ht":         ht_present,
        "vht":        vht_present,
        "he":         he_present,
        "rsn":        rsn,
        "vendor_ies": vendor_ies,
        "ies":        ies,
    }


def _parse_rsn(raw: bytes) -> dict[str, Any]:
    """Parse the RSN Information Element.

    Layout per 802.11-2020 §9.4.2.24::

        version            (2 bytes, little-endian)
        group cipher       (4 bytes: OUI[3] + type[1])
        pairwise count     (2 bytes)
        pairwise list      (count * 4 bytes)
        AKM count          (2 bytes)
        AKM list           (count * 4 bytes)
        RSN capabilities   (2 bytes)
        PMKID count        (2 bytes, optional)
        PMKID list         (count * 16 bytes)
        group mgmt cipher  (4 bytes, optional)
    """
    if len(raw) < 8:
        return {"error": "truncated", "raw_hex": raw.hex()}

    version = int.from_bytes(raw[0:2], "little")
    group_oui = raw[2:5]
    group_type = raw[5]
    group_name = _suite_name(group_oui, group_type)

    pos = 6
    pairwise_count = int.from_bytes(raw[pos:pos+2], "little")
    pos += 2
    pairwise: list[str] = []
    for _ in range(pairwise_count):
        if pos + 4 > len(raw):
            break
        pairwise.append(_suite_name(raw[pos:pos+3], raw[pos+3]))
        pos += 4

    if pos + 2 > len(raw):
        return _rsn_dict(version, group_name, pairwise, [], 0)
    akm_count = int.from_bytes(raw[pos:pos+2], "little")
    pos += 2
    akms: list[str] = []
    for _ in range(akm_count):
        if pos + 4 > len(raw):
            break
        akms.append(_akm_name(raw[pos:pos+3], raw[pos+3]))
        pos += 4

    rsn_caps = 0
    if pos + 2 <= len(raw):
        rsn_caps = int.from_bytes(raw[pos:pos+2], "little")

    return _rsn_dict(version, group_name, pairwise, akms, rsn_caps)


def _rsn_dict(version, group, pairwise, akms, caps) -> dict[str, Any]:
    mfpc = bool(caps & 0x0080)
    mfpr = bool(caps & 0x0040)
    return {
        "version":         version,
        "group_cipher":    group,
        "pairwise_ciphers": pairwise,
        "akms":            akms,
        "rsn_capabilities_hex": f"{caps:04x}",
        "mfp_capable":     mfpc,
        "mfp_required":    mfpr,
        # Convenience: human-readable summary
        "summary":         _rsn_summary(pairwise, akms, mfpr),
    }


def _suite_name(oui: bytes, suite_type: int) -> str:
    if oui == _RSN_OUI_IEEE:
        return _RSN_CIPHER_NAMES.get(suite_type, f"IEEE:{suite_type}")
    if oui == _RSN_OUI_MS:
        return f"WPA1:{suite_type}"
    return f"{oui.hex(':')}:{suite_type}"


def _akm_name(oui: bytes, akm_type: int) -> str:
    if oui == _RSN_OUI_IEEE:
        return _RSN_AKM_NAMES.get(akm_type, f"AKM:{akm_type}")
    return f"{oui.hex(':')}:{akm_type}"


def _rsn_summary(pairwise: list[str], akms: list[str], mfpr: bool) -> str:
    """Short label for the Security panel header."""
    if "SAE" in akms:
        label = "WPA3-Personal"
    elif "802.1X" in akms or "802.1X-SHA256" in akms:
        label = "WPA2-Enterprise"
    elif "PSK" in akms or "PSK-SHA256" in akms:
        label = "WPA2-Personal"
    elif "OWE" in akms:
        label = "OWE (Opportunistic Wireless Encryption)"
    else:
        label = "/".join(akms) or "Unknown"
    if mfpr:
        label += " + MFP required"
    return label


_IE_NAMES = {
    0:   "SSID",
    1:   "Supported Rates",
    3:   "DS Parameter Set",
    5:   "TIM",
    7:   "Country",
    32:  "Power Constraint",
    45:  "HT Capabilities",
    48:  "RSN",
    50:  "Extended Supported Rates",
    61:  "HT Operation",
    127: "Extended Capabilities",
    191: "VHT Capabilities",
    192: "VHT Operation",
    221: "Vendor Specific",
    255: "Element ID Extension",
}


def _extract_probe_ssid(pkt) -> tuple[str, bool]:
    """Pull SSID from a probe request. Returns (ssid_str, is_broadcast)."""
    from scapy.layers.dot11 import Dot11Elt
    elt = pkt.getlayer(Dot11Elt)
    while elt is not None:
        if int(elt.ID) == 0:               # SSID IE
            raw = bytes(elt.info) if elt.info else b""
            if len(raw) == 0:
                return ("", True)
            try:
                return (raw.decode("utf-8", errors="replace"), False)
            except Exception:
                return ("", False)
        elt = elt.payload.getlayer(Dot11Elt)
    return ("", True)


# ---------- Stub data for Mac dev --------------------------------------

def _stub_beacon(bssid: str) -> dict[str, Any]:
    """Synthetic beacon parse for stub mode."""
    return {
        "bssid":   bssid.lower(),
        "ssid":    "StubNetwork",
        "channel": 6,
        "country": "US",
        "ht":      True,
        "vht":     True,
        "he":      False,
        "rsn": {
            "version":          1,
            "group_cipher":     "CCMP-128",
            "pairwise_ciphers": ["CCMP-128"],
            "akms":             ["PSK"],
            "rsn_capabilities_hex": "0000",
            "mfp_capable":      False,
            "mfp_required":     False,
            "summary":          "WPA2-Personal",
        },
        "vendor_ies": [
            {"oui": "00:50:f2", "type": 1, "length": 24},  # WPA1
            {"oui": "00:50:f2", "type": 4, "length": 109}, # WPS
        ],
        "ies": [
            {"tag": 0,   "name": "SSID",            "length": 11, "hex": "537475624e6574776f726b"},
            {"tag": 1,   "name": "Supported Rates", "length": 8,  "hex": "82848b962430486c"},
            {"tag": 3,   "name": "DS Parameter Set","length": 1,  "hex": "06"},
            {"tag": 45,  "name": "HT Capabilities", "length": 26, "hex": "ef1900..."},
            {"tag": 48,  "name": "RSN",             "length": 20, "hex": "0100000fac0401000fac0401000fac020000"},
            {"tag": 221, "name": "Vendor Specific", "length": 24, "hex": "0050f201..."},
        ],
    }


def _stub_probes() -> list[dict[str, Any]]:
    """Synthetic probe requests for stub mode."""
    return [
        {
            "station_mac":  "11:22:33:44:55:01",
            "ssid":         "HomeWiFi",
            "is_broadcast": False,
            "count":        47,
            "first_seen":   1717603200.0,
            "last_seen":    1717603550.0,
        },
        {
            "station_mac":  "11:22:33:44:55:03",
            "ssid":         "AirportFreeWifi",
            "is_broadcast": False,
            "count":        12,
            "first_seen":   1717603300.0,
            "last_seen":    1717603480.0,
        },
        {
            "station_mac":  "11:22:33:44:55:03",
            "ssid":         "Starbucks WiFi",
            "is_broadcast": False,
            "count":        8,
            "first_seen":   1717603350.0,
            "last_seen":    1717603470.0,
        },
        {
            "station_mac":  "11:22:33:44:55:03",
            "ssid":         "",
            "is_broadcast": True,
            "count":        21,
            "first_seen":   1717603290.0,
            "last_seen":    1717603495.0,
        },
    ]
