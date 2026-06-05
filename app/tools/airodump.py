"""airodump-ng wrapper — passive 802.11 sniffer.

airodump-ng is the foundation of every recon workflow: put a radio in
monitor mode, point airodump at it, and it decodes every frame the
antenna can hear (beacons, probe requests, data) into a CSV file that
gets rewritten roughly once per second.

We use it for the Recon page. Two instances run in parallel — one on
``wlan-mon-2g`` hopping 2.4 GHz, one on ``wlan-mon-5g`` hopping 5 GHz.
The recon service polls both CSVs, parses, merges by BSSID, and pushes
deltas to the UI over SocketIO.

The wrapper is intentionally split:

* ``build_cmd()`` constructs the argv. No subprocess invocation here —
  the recon service hands the argv to the JobManager so the process is
  owned by the platform, gets a stable job id, and tears down cleanly.
* ``parse_csv()`` reads a CSV file written by a running airodump and
  returns ``(aps, clients)``. Safe to call mid-write — if the file is
  truncated or empty, we return whatever we can parse and skip the rest.

The CSV format (per the aircrack-ng source):

* Header line ``BSSID, First time seen, ...`` (15 AP fields).
* Zero or more AP rows.
* Blank line.
* Header line ``Station MAC, First time seen, ...`` (7+ client fields).
* Zero or more client rows.
* Trailing blank line(s).

Client rows have a variable number of trailing fields because the
"Probed ESSIDs" column is rendered as a comma-joined list without
quoting — so a client probing three networks produces 9 fields total.
We collapse fields[6:] back into the probed list.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.tools._common import stub_mode

log = logging.getLogger(__name__)


# ---------- Command construction ----------------------------------------

def build_cmd(
    iface: str,
    output_prefix: str | Path,
    *,
    band: str | None = None,
    channels: str | None = None,
    write_interval: int = 1,
    berlin_seconds: int = 60,
) -> list[str]:
    """Build the airodump-ng command line.

    * ``band`` — one of ``"bg"`` (2.4 GHz), ``"a"`` (5 GHz),
      ``"abg"`` (both); ``None`` lets airodump use its default.
    * ``channels`` — explicit channel list like ``"1,6,11"`` to pin
      hopping. Overrides ``band``.
    * ``write_interval`` — seconds between CSV refreshes. 1s is the
      Pineapple feel; lower spams the kernel for negligible gain.
    * ``berlin_seconds`` — how long a station stays in the "current"
      table after we stop seeing it. The CSV always holds every station
      ever seen, but airodump's ``--berlin`` flag also controls the
      console display freshness window.

    We emit CSV only (``--output-format csv``). Skipping the pcap saves
    disk IO; Session 06 will add pcap when we need handshakes.
    """
    cmd = [
        "airodump-ng",
        "--output-format", "csv",
        "--write", str(output_prefix),
        "--write-interval", str(write_interval),
        "--berlin", str(berlin_seconds),
    ]
    if channels:
        cmd += ["--channel", channels]
    elif band:
        cmd += ["--band", band]
    cmd.append(iface)
    return cmd


# ---------- Data shapes -------------------------------------------------

@dataclass
class AccessPoint:
    bssid: str
    essid: str
    channel: int | None
    signal_dbm: int | None       # negative dBm; -50 strong, -90 weak
    encryption: str              # raw "Privacy" field: WPA2, WPA3, OPN, WEP, ...
    cipher: str                  # CCMP, TKIP, ""
    auth: str                    # PSK, MGT, SAE, ""
    beacons: int
    data_packets: int
    first_seen: str
    last_seen: str
    band: str | None = None      # derived from channel ("2.4GHz" / "5GHz")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Client:
    station_mac: str
    bssid: str                   # "(not associated)" if no association
    signal_dbm: int | None
    packets: int
    first_seen: str
    last_seen: str
    probed_essids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- CSV parsing -------------------------------------------------

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_AP_HEADER_PREFIX = "BSSID,"
_CLIENT_HEADER_PREFIX = "Station MAC,"


def parse_csv(path: str | Path) -> tuple[list[AccessPoint], list[Client]]:
    """Read an airodump CSV file and return ``(aps, clients)``.

    Returns empty lists if the file is missing or unreadable. Tolerates
    mid-write reads — we'll get whatever rows have been flushed so far
    and silently skip malformed ones.
    """
    p = Path(path)
    if not p.is_file():
        return [], []
    try:
        text = p.read_text(errors="replace")
    except OSError as e:
        log.warning("airodump.parse_csv: read failed %s: %s", path, e)
        return [], []
    return _parse_text(text)


def _parse_text(text: str) -> tuple[list[AccessPoint], list[Client]]:
    aps: list[AccessPoint] = []
    clients: list[Client] = []
    lines = text.splitlines()

    # Locate section boundaries by header prefixes.
    ap_start: int | None = None
    client_start: int | None = None
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if ap_start is None and stripped.startswith(_AP_HEADER_PREFIX):
            ap_start = i + 1
        elif stripped.startswith(_CLIENT_HEADER_PREFIX):
            client_start = i + 1
            break

    if ap_start is None:
        return [], []

    # AP rows run from ap_start to the first blank line (or the client
    # header if no blank precedes it, or EOF).
    ap_end = client_start - 1 if client_start is not None else len(lines)
    for i in range(ap_start, ap_end):
        if not lines[i].strip():
            ap_end = i
            break
    for raw in lines[ap_start:ap_end]:
        if not raw.strip():
            continue
        ap = _parse_ap_row(raw)
        if ap is not None:
            aps.append(ap)

    if client_start is not None:
        for raw in lines[client_start:]:
            if not raw.strip():
                continue
            client = _parse_client_row(raw)
            if client is not None:
                clients.append(client)

    return aps, clients


def _split_csv_line(line: str) -> list[str]:
    """Split a CSV line with the stdlib csv module + strip whitespace.

    airodump pads numeric fields with spaces (``  6`` for channel 6).
    """
    try:
        reader = csv.reader(io.StringIO(line))
        fields = next(reader)
    except (StopIteration, csv.Error):
        return []
    return [f.strip() for f in fields]


def _to_int(s: str, default: int | None = None) -> int | None:
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


def _parse_ap_row(line: str) -> AccessPoint | None:
    """Parse an AP row.

    Fields (15):
      [0]  BSSID
      [1]  First time seen
      [2]  Last time seen
      [3]  channel
      [4]  Speed (max rate)
      [5]  Privacy   (WPA2 / WPA3 / WPA / OPN / WEP)
      [6]  Cipher    (CCMP / TKIP / "")
      [7]  Authentication  (PSK / SAE / MGT / "")
      [8]  Power     (signal in dBm, often negative)
      [9]  # beacons
      [10] # IV (data packets carrying initialization vectors)
      [11] LAN IP (rarely populated)
      [12] ID-length (ESSID length; redundant with [13])
      [13] ESSID
      [14] Key (recovered key — empty for passive scan)
    """
    fields = _split_csv_line(line)
    if len(fields) < 14:
        return None
    bssid = fields[0]
    if not _MAC_RE.match(bssid):
        return None

    channel = _to_int(fields[3])
    if channel is not None and channel < 0:
        channel = None
    signal = _to_int(fields[8])
    if signal == 0 or signal == -1:
        signal = None   # airodump uses -1 for "unknown"; 0 is impossible
    beacons = _to_int(fields[9], 0) or 0
    data_packets = _to_int(fields[10], 0) or 0
    essid = fields[13] if len(fields) > 13 else ""

    band: str | None = None
    if channel is not None:
        band = "5GHz" if channel >= 36 else "2.4GHz"

    return AccessPoint(
        bssid=bssid,
        essid=essid,
        channel=channel,
        signal_dbm=signal,
        encryption=fields[5] or "",
        cipher=fields[6] or "",
        auth=fields[7] or "",
        beacons=beacons,
        data_packets=data_packets,
        first_seen=fields[1],
        last_seen=fields[2],
        band=band,
    )


def _parse_client_row(line: str) -> Client | None:
    """Parse a Client row.

    Fields (6 fixed + variable trailing for probed ESSIDs):
      [0]  Station MAC
      [1]  First time seen
      [2]  Last time seen
      [3]  Power (dBm)
      [4]  # packets
      [5]  BSSID of associated AP (or "(not associated)")
      [6:] Probed ESSIDs — comma-joined and NOT csv-quoted, so the
           stdlib csv reader splits them into separate fields. We
           rejoin and split on commas, dropping empties.
    """
    fields = _split_csv_line(line)
    if len(fields) < 6:
        return None
    mac = fields[0]
    if not _MAC_RE.match(mac):
        return None

    signal = _to_int(fields[3])
    if signal == 0 or signal == -1:
        signal = None
    packets = _to_int(fields[4], 0) or 0

    probed_raw = ", ".join(fields[6:]) if len(fields) > 6 else ""
    probed = [p.strip() for p in probed_raw.split(",") if p.strip()]

    return Client(
        station_mac=mac,
        bssid=fields[5] or "",
        signal_dbm=signal,
        packets=packets,
        first_seen=fields[1],
        last_seen=fields[2],
        probed_essids=probed,
    )


# ---------- Stub data for Mac dev ---------------------------------------

def stub_snapshot(band: str = "bg") -> tuple[list[AccessPoint], list[Client]]:
    """Return synthetic data when stub_mode() is True.

    The recon service uses this on the Mac (no real adapters, no real
    airodump) so the UI development loop still works. We tailor per
    band so the dual-adapter merge logic exercises both halves.
    """
    if band == "a":
        return _stub_5ghz()
    return _stub_2ghz()


def _stub_2ghz() -> tuple[list[AccessPoint], list[Client]]:
    aps = [
        AccessPoint(
            bssid="AA:BB:CC:DD:EE:01", essid="HomeWiFi",
            channel=6, signal_dbm=-52, encryption="WPA2",
            cipher="CCMP", auth="PSK",
            beacons=1240, data_packets=842,
            first_seen="2026-06-05 14:00:00",
            last_seen="2026-06-05 14:30:12",
            band="2.4GHz",
        ),
        AccessPoint(
            bssid="AA:BB:CC:DD:EE:03", essid="GuestNetwork",
            channel=11, signal_dbm=-75, encryption="OPN",
            cipher="", auth="",
            beacons=520, data_packets=12,
            first_seen="2026-06-05 14:05:00",
            last_seen="2026-06-05 14:29:55",
            band="2.4GHz",
        ),
        AccessPoint(
            bssid="AA:BB:CC:DD:EE:04", essid="",  # hidden
            channel=1, signal_dbm=-68, encryption="WPA2",
            cipher="CCMP", auth="PSK",
            beacons=210, data_packets=4,
            first_seen="2026-06-05 14:12:00",
            last_seen="2026-06-05 14:29:40",
            band="2.4GHz",
        ),
    ]
    clients = [
        Client(
            station_mac="11:22:33:44:55:01",
            bssid="AA:BB:CC:DD:EE:01",
            signal_dbm=-55, packets=412,
            first_seen="2026-06-05 14:01:00",
            last_seen="2026-06-05 14:30:10",
            probed_essids=["HomeWiFi"],
        ),
        Client(
            station_mac="11:22:33:44:55:03",
            bssid="(not associated)",
            signal_dbm=-80, packets=15,
            first_seen="2026-06-05 14:10:00",
            last_seen="2026-06-05 14:28:30",
            probed_essids=["AirportFreeWifi", "Starbucks WiFi"],
        ),
    ]
    return aps, clients


def _stub_5ghz() -> tuple[list[AccessPoint], list[Client]]:
    aps = [
        AccessPoint(
            bssid="AA:BB:CC:DD:EE:02", essid="HomeWiFi-5G",
            channel=149, signal_dbm=-60, encryption="WPA3",
            cipher="CCMP", auth="SAE",
            beacons=980, data_packets=521,
            first_seen="2026-06-05 14:00:00",
            last_seen="2026-06-05 14:30:12",
            band="5GHz",
        ),
        AccessPoint(
            bssid="AA:BB:CC:DD:EE:05", essid="Office5GHz",
            channel=36, signal_dbm=-72, encryption="WPA2",
            cipher="CCMP", auth="PSK",
            beacons=600, data_packets=180,
            first_seen="2026-06-05 14:03:00",
            last_seen="2026-06-05 14:30:00",
            band="5GHz",
        ),
    ]
    clients = [
        Client(
            station_mac="11:22:33:44:55:02",
            bssid="AA:BB:CC:DD:EE:02",
            signal_dbm=-63, packets=200,
            first_seen="2026-06-05 14:02:00",
            last_seen="2026-06-05 14:29:50",
            probed_essids=["HomeWiFi-5G"],
        ),
    ]
    return aps, clients


# Exposed for the recon service: True when we should skip launching
# real airodump processes and fabricate snapshots instead.
def is_stub() -> bool:
    return stub_mode()
