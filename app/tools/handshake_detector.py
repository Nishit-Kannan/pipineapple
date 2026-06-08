"""Handshake detector — find WPA 4-way EAPOL exchanges in a pcap.

For each (AP BSSID, station MAC) pair seen in EAPOL-Key frames, track
which of M1/M2/M3/M4 we've captured. Returns the set of pairs with
their state so the UI can render M1/M2/M3/M4 status dots while a
capture is running.

Used by ``app/services/handshakes.py``. Polled every ~1 s by the
capture service during an active capture; cached on (pcap path, mtime)
for ``_CACHE_TTL`` seconds so back-to-back polls hit memory.

A pair is:
  * "complete" when all of M1, M2, M3, M4 are present
  * "partial" when at least {M1, M2} OR {M2, M3} is present (M2 carries
    the MIC that hashcat needs — that's the bar for offline cracking)
  * neither otherwise — a single message or a {M1} sighting alone isn't
    useful but we still track it so the UI status changes incrementally
    instead of jumping from "nothing" to "partial"

EAPOL Key Information bit layout (IEEE 802.11-2020 §12.7.6.2), read as
a big-endian 16-bit field at bytes 5-6 of the EAPOL-Key body:

    bits 0-2  : Descriptor Version
    bit 3     : Key Type           (1 = Pairwise / PTK, 0 = Group / GTK)
    bit 6     : Install
    bit 7     : Key Ack
    bit 8     : Key MIC
    bit 9     : Secure
    bits 10+  : Error / Request / Encrypted Key Data / SMK / reserved

Message classification by the (Install, Ack, MIC, Secure) tuple (all
with Pairwise=1):

    M1 : (0, 1, 0, 0)   AP -> STA   ANonce only, no MIC yet
    M2 : (0, 0, 1, 0)   STA -> AP   SNonce + first MIC (the crackable one)
    M3 : (1, 1, 1, 1)   AP -> STA   ANonce + MIC + key install
    M4 : (0, 0, 1, 1)   STA -> AP   confirmation

Group-key handshakes (Pairwise=0, used for GTK rekeys) are ignored —
they're not the 4-way and don't help with cracking.

Frame direction (FromDS / ToDS bits) tells us which address is the
BSSID vs the station:

    AP -> STA (M1, M3) : FromDS=1, ToDS=0  ->  BSSID=addr2, STA=addr1
    STA -> AP (M2, M4) : FromDS=0, ToDS=1  ->  BSSID=addr1, STA=addr2
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from app.tools._common import stub_mode

log = logging.getLogger(__name__)


# ---------- Result cache --------------------------------------------------
# Tight TTL — the capture-status poller hits us once per second and wants
# fresh data within a second of new frames landing. 2 s strikes a balance
# between "every poll re-parses the pcap" and "status feels stale".
_CACHE_TTL = 2.0
_cache_lock = threading.Lock()
_cache: dict[tuple[str, float], tuple[float, list]] = {}


# ---------- Public API ----------------------------------------------------

def detect_handshakes(pcap_path: str | Path) -> list[dict[str, Any]]:
    """Parse pcap, return list of (BSSID, station, M-set) dicts.

    Each entry::

        {
          "bssid":         "aa:bb:cc:dd:ee:ff",
          "station_mac":   "11:22:33:44:55:66",
          "messages_seen": [1, 2, 3, 4],
          "is_complete":   True,
          "is_partial":    False,
          "first_seen":    1717603200.123,
          "last_seen":     1717603200.456,
        }

    Sorted by last_seen descending. Returns empty list if scapy isn't
    installed or the pcap is missing.
    """
    if stub_mode():
        return _stub_handshakes()

    p = Path(pcap_path)
    if not p.is_file():
        return []

    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []

    cache_key = (str(p), mtime)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit is not None and (now - hit[0]) < _CACHE_TTL:
            return hit[1]

    try:
        from scapy.all import PcapReader
        from scapy.layers.dot11 import Dot11
        from scapy.layers.eap import EAPOL
    except ImportError:
        log.warning("scapy not installed; handshake detection unavailable")
        return []

    # (bssid, station_mac) -> state dict (with a Python set until the end)
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    started = time.monotonic()

    try:
        with PcapReader(str(p)) as r:
            for pkt in r:
                # Cheap filter first — most frames in a recon pcap are
                # beacons / probes / data, EAPOL is rare. haslayer is fast.
                if not pkt.haslayer(EAPOL):
                    continue
                if not pkt.haslayer(Dot11):
                    continue

                d11 = pkt.getlayer(Dot11)
                fc = int(d11.FCfield)
                to_ds = bool(fc & 0x01)
                from_ds = bool(fc & 0x02)

                if from_ds and not to_ds:
                    bssid = (d11.addr2 or "").lower()
                    sta = (d11.addr1 or "").lower()
                elif to_ds and not from_ds:
                    bssid = (d11.addr1 or "").lower()
                    sta = (d11.addr2 or "").lower()
                else:
                    # WDS (both bits set) or ad-hoc (neither) — not relevant
                    continue
                if not bssid or not sta:
                    continue

                eapol = pkt.getlayer(EAPOL)
                # EAPOL Type 3 = EAPOL-Key (the 4-way uses this; other
                # types are EAP-Packet / Start / Logoff / ASF-Alert and
                # don't carry handshake state).
                if int(eapol.type) != 3:
                    continue

                # Parse Key Information field from raw bytes. scapy has
                # an EAPOL_KEY layer but its field naming varies across
                # versions; raw decoding is more robust.
                raw = bytes(eapol)
                if len(raw) < 7:
                    continue
                key_info = int.from_bytes(raw[5:7], "big")

                if not (key_info & 0x0008):
                    # Key Type bit clear = Group-key handshake (GTK
                    # rekey). Not part of the 4-way.
                    continue
                install = bool(key_info & 0x0040)
                ack     = bool(key_info & 0x0080)
                mic     = bool(key_info & 0x0100)
                secure  = bool(key_info & 0x0200)

                msg = _classify(install, ack, mic, secure)
                if msg is None:
                    continue

                # PMKID detection — only on M1. PMKID lives in M1's
                # Key Data field as a vendor-specific KDE (00-0F-AC:4).
                # Modern APs include it in every M1 unless explicitly
                # disabled. PMKID alone is enough for hashcat mode
                # 22000 — the entire reason hcxdumptool's active-scan
                # mode is useful: it provokes M1 from APs without
                # needing a client to associate.
                has_pmkid = (msg == 1) and _m1_has_pmkid(raw)

                ts = float(pkt.time) if hasattr(pkt, "time") else 0.0
                pkey = (bssid, sta)
                entry = pairs.get(pkey)
                if entry is None:
                    pairs[pkey] = {
                        "bssid":         bssid,
                        "station_mac":   sta,
                        "messages_seen": {msg},
                        "has_pmkid":     has_pmkid,
                        "first_seen":    ts,
                        "last_seen":     ts,
                    }
                else:
                    entry["messages_seen"].add(msg)
                    if has_pmkid:
                        entry["has_pmkid"] = True
                    if ts < entry["first_seen"]:
                        entry["first_seen"] = ts
                    if ts > entry["last_seen"]:
                        entry["last_seen"] = ts
    except Exception as e:
        log.warning("handshake detect failed %s: %s", pcap_path, e)
        return []

    elapsed = time.monotonic() - started
    if elapsed > 2.0:
        try:
            size_mb = p.stat().st_size // (1024 * 1024)
        except OSError:
            size_mb = -1
        log.warning(
            "handshake detect slow: %.1fs for %s (pcap size %d MB)",
            elapsed, pcap_path, size_mb,
        )

    out: list[dict[str, Any]] = []
    for entry in pairs.values():
        msgs = entry["messages_seen"]
        is_complete = msgs >= {1, 2, 3, 4}
        # Partial crackability — any of these is enough for hashcat
        # mode 22000:
        #   * PMKID alone (hcxdumptool active-scan signature)
        #   * M1+M2 — most common, M2 carries the crackable MIC
        #   * M1+M3 — happens with PMK caching: client uses cached
        #             PMK, both sides skip to M3. M3's MIC + M1's
        #             ANonce is hashcat's "EAPOL pair M1M3" target.
        #   * M2+M3 — uncommon but valid: M3's MIC + M2's EAPOL bytes
        # M3 alone OR M2 alone OR M1 alone don't have enough material.
        # hcxpcapngtool reports M1M2 / M1M3 / M2M3 pair counts
        # separately for exactly this reason.
        is_partial = (
            ({1, 2} <= msgs) or
            ({1, 3} <= msgs) or
            ({2, 3} <= msgs) or
            entry.get("has_pmkid", False)
        ) and not is_complete
        entry["messages_seen"] = sorted(msgs)
        entry["is_complete"] = is_complete
        entry["is_partial"] = is_partial
        entry.setdefault("has_pmkid", False)
        out.append(entry)
    out.sort(key=lambda d: d["last_seen"], reverse=True)

    with _cache_lock:
        _cache[cache_key] = (now, out)
    return out


def summarize_for_capture(
    pcap_path: str | Path, target_bssid: str | None = None,
) -> dict[str, Any]:
    """Capture-side summary used by the service's status endpoint.

    If ``target_bssid`` is given (typical for the AP-focused capture
    flow), filter pairs to that BSSID only. Returns the union of
    messages seen across all matching pairs (so any client of the AP
    contributing M2 counts as "M2 seen"), plus pair-level counts.
    """
    hs = detect_handshakes(pcap_path)
    if target_bssid:
        target = target_bssid.lower()
        hs = [h for h in hs if h["bssid"] == target]

    msgs_seen: set[int] = set()
    complete_pairs = 0
    partial_pairs = 0
    any_pmkid = False
    for h in hs:
        msgs_seen.update(h["messages_seen"])
        if h.get("has_pmkid"):
            any_pmkid = True
        if h["is_complete"]:
            complete_pairs += 1
        elif h["is_partial"]:
            partial_pairs += 1

    union_complete = msgs_seen >= {1, 2, 3, 4}
    # Same M1M2 / M1M3 / M2M3 / PMKID logic as detect_handshakes —
    # see comment there for which combinations hashcat 22000 cracks.
    union_partial = (
        ({1, 2} <= msgs_seen)
        or ({1, 3} <= msgs_seen)
        or ({2, 3} <= msgs_seen)
        or any_pmkid
    ) and not union_complete

    return {
        "target_bssid":    target_bssid.lower() if target_bssid else None,
        "pairs":           hs,                # per-(bssid, sta) detail
        "messages_seen":   sorted(msgs_seen), # union across pairs
        "has_pmkid":       any_pmkid,         # AP-level: PMKID captured?
        "is_complete":     union_complete,
        "is_partial":      union_partial,
        "complete_pairs":  complete_pairs,
        "partial_pairs":   partial_pairs,
    }


# ---------- Internals -----------------------------------------------------

def _classify(install: bool, ack: bool, mic: bool, secure: bool) -> int | None:
    """Map the (Install, Ack, MIC, Secure) tuple to M1/M2/M3/M4. None
    if the combination doesn't match any of the four."""
    if ack and not mic and not install and not secure:
        return 1
    if not ack and mic and not install and not secure:
        return 2
    if ack and mic and install and secure:
        return 3
    if not ack and mic and not install and secure:
        return 4
    return None


# PMKID KDE marker: vendor OUI 00-0F-AC + type 0x04. Per IEEE 802.11-2020
# §12.7.2 KDE table. The whole KDE looks like:
#     0xDD <kde_len> 00 0F AC 04 <16-byte PMKID>
# where kde_len = 0x14 = 20 (4 bytes of OUI+type header + 16-byte PMKID).
_PMKID_KDE_PREFIX = b"\xdd\x14\x00\x0f\xac\x04"


def _m1_has_pmkid(eapol_bytes: bytes) -> bool:
    """True if this EAPOL-Key M1 frame contains the PMKID KDE in Key Data.

    EAPOL-Key descriptor layout (after the 4-byte EAPOL header):
        offset  0  : Descriptor Type (1 byte)
        offset  1  : Key Information (2 bytes, big-endian)
        offset  3  : Key Length (2)
        offset  5  : Replay Counter (8)
        offset 13  : Key Nonce (32)
        offset 45  : EAPOL Key IV (16)
        offset 61  : Key RSC (8)
        offset 69  : Reserved (8)
        offset 77  : Key MIC (16)  -- size varies in newer AKMs; common 16
        offset 93  : Key Data Length (2 bytes, big-endian)
        offset 95  : Key Data (Key Data Length bytes)

    The full EAPOL frame in scapy's bytes() output prepends a 4-byte
    EAPOL header (version, type, length), so the descriptor starts at
    raw[4]. The KDL field is at raw[4+93+4 .. 4+95+4] = raw[97:99],
    Key Data at raw[99:99+kdl].

    For PMKID specifically: scan the Key Data for the marker
    DD 14 00 0F AC 04 ...
    Returns True if found.
    """
    # Need enough bytes to even have a Key Data Length field
    # Minimum descriptor body size is 95 octets + 2 for KDL = 97;
    # plus EAPOL header (4) = 101.
    if len(eapol_bytes) < 101:
        return False
    try:
        # EAPOL header is 4 bytes; Key Data Length is at descriptor byte 93
        kdl_off = 4 + 93
        kdl = int.from_bytes(eapol_bytes[kdl_off:kdl_off + 2], "big")
        if kdl == 0:
            return False
        key_data_start = kdl_off + 2
        key_data_end = key_data_start + kdl
        if key_data_end > len(eapol_bytes):
            # Truncated frame — be conservative
            key_data_end = len(eapol_bytes)
        key_data = eapol_bytes[key_data_start:key_data_end]
        return _PMKID_KDE_PREFIX in key_data
    except (IndexError, ValueError):
        return False


# ---------- Stub data for Mac dev -----------------------------------------

def _stub_handshakes() -> list[dict[str, Any]]:
    """Synthetic data: one full 4-way + one M1+PMKID + one M3-only."""
    return [
        {
            "bssid":         "aa:bb:cc:dd:ee:01",
            "station_mac":   "11:22:33:44:55:01",
            "messages_seen": [1, 2, 3, 4],
            "has_pmkid":     True,
            "is_complete":   True,
            "is_partial":    False,
            "first_seen":    1717603200.0,
            "last_seen":     1717603200.5,
        },
        {
            # The hcxdumptool active-scan signature: M1 from the AP
            # in response to our probe, containing PMKID. No client
            # involved.
            "bssid":         "aa:bb:cc:dd:ee:01",
            "station_mac":   "00:c0:ca:b9:65:4a",   # our wlan-ap MAC
            "messages_seen": [1],
            "has_pmkid":     True,
            "is_complete":   False,
            "is_partial":    True,
            "first_seen":    1717603300.0,
            "last_seen":     1717603300.1,
        },
        {
            "bssid":         "aa:bb:cc:dd:ee:01",
            "station_mac":   "11:22:33:44:55:02",
            "messages_seen": [1, 2],
            "has_pmkid":     False,
            "is_complete":   False,
            "is_partial":    True,
            "first_seen":    1717603310.0,
            "last_seen":     1717603310.3,
        },
    ]
