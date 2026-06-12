"""nmap wrapper — build the argv per scan profile, run it, parse the XML.

We always emit XML to stdout (``-oX -``) and parse it with the stdlib
``xml.etree`` rather than scraping the human-readable output — the XML is
stable and structured. Stub mode returns a canned scan so the UI and the
service lifecycle can be exercised on the Mac without nmap or a live subnet.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

from app.tools._common import run, stub_mode

log = logging.getLogger(__name__)

# Scan profiles → extra nmap args. -T4 for lab-speed; -oX - is added by run_scan.
#   discovery : host discovery only (no port scan)
#   quick     : fast top-100 TCP ports
#   services  : top-1000 TCP ports + service/version detection
#   scripts   : services + nmap's default NSE script set (-sC)
PROFILES: dict[str, dict[str, Any]] = {
    "discovery": {"label": "Ping sweep (host discovery)", "args": ["-sn"]},
    "quick":     {"label": "Quick scan (top 100 ports)",  "args": ["-F", "-T4"]},
    "services":  {"label": "Service + version detect",     "args": ["-sV", "-T4"]},
    "scripts":   {"label": "Default NSE scripts (-sC -sV)", "args": ["-sC", "-sV", "-T4"]},
}
DEFAULT_PROFILE = "quick"


def is_available() -> tuple[bool, str]:
    """Check whether the nmap binary is installed. Returns (ok, detail) —
    detail is the version string when present, else an install hint."""
    if stub_mode():
        return True, "stub mode"
    res = run(["nmap", "--version"], timeout=5, source="nmap")
    if res.returncode == 127:
        return False, "not installed — run 'sudo apt install nmap' on the Pi"
    first = (res.stdout or "").splitlines()[0] if res.stdout else ""
    return True, first or "installed"


def build_argv(profile: str, target: str,
               extra: list[str] | None = None) -> list[str]:
    p = PROFILES.get(profile) or PROFILES[DEFAULT_PROFILE]
    # --host-timeout keeps a single unresponsive host from stalling the run.
    # Operator ``extra`` flags go after the profile args; we always keep
    # ``-oX -`` last so the XML parse still works regardless of what they add.
    return ["nmap", *p["args"], *(extra or []),
            "--host-timeout", "120s", "-oX", "-", target]


def run_scan(profile: str, target: str, *, extra: list[str] | None = None,
             timeout: float = 900.0
             ) -> tuple[bool, str, list[dict[str, Any]]]:
    """Run a scan and parse it. Returns ``(ok, message, hosts)``.

    ``hosts`` is a list of ``{ip, mac, vendor, hostname, state, ports[]}``
    where each port is ``{port, proto, state, service, product, version}``.
    """
    if stub_mode():
        return True, "stub scan", _STUB_HOSTS

    argv = build_argv(profile, target, extra)
    res = run(argv, timeout=timeout, source="nmap")
    return interpret(res.returncode, res.stdout, res.stderr)


def interpret(rc: int, stdout: str | None, stderr: str | None
              ) -> tuple[bool, str, list[dict[str, Any]]]:
    """Turn an nmap process result into ``(ok, message, hosts)``. Shared by
    the standalone ``run_scan`` and the service's killable Popen path."""
    if rc == 127:
        return False, ("nmap not installed on this host — run "
                       "'sudo apt install nmap' on the Pi, then re-run "
                       "(no restart needed)"), []
    if rc == 124:
        return False, "nmap timed out", []
    if not (stdout or "").strip():
        return (False,
                f"nmap produced no output (rc={rc}): "
                f"{(stderr or '').strip()[:200]}",
                [])
    try:
        hosts = parse_xml(stdout)
    except ET.ParseError as e:
        return False, f"could not parse nmap XML: {e}", []
    return True, f"scan complete — {len(hosts)} host(s)", hosts


def parse_xml(xml_text: str) -> list[dict[str, Any]]:
    """Parse nmap ``-oX`` output into a list of host dicts."""
    root = ET.fromstring(xml_text)
    hosts: list[dict[str, Any]] = []
    for h in root.findall("host"):
        status = h.find("status")
        state = status.get("state") if status is not None else "unknown"

        ip = mac = vendor = ""
        for addr in h.findall("address"):
            atype = addr.get("addrtype")
            if atype == "ipv4" or (atype == "ipv6" and not ip):
                ip = addr.get("addr", "")
            elif atype == "mac":
                mac = addr.get("addr", "")
                vendor = addr.get("vendor", "")

        hostname = ""
        hn = h.find("hostnames/hostname")
        if hn is not None:
            hostname = hn.get("name", "")

        ports: list[dict[str, Any]] = []
        for port in h.findall("ports/port"):
            pstate = port.find("state")
            if pstate is not None and pstate.get("state") != "open":
                continue  # only surface open ports
            svc = port.find("service")
            ports.append({
                "port":    int(port.get("portid", 0)),
                "proto":   port.get("protocol", ""),
                "state":   pstate.get("state") if pstate is not None else "",
                "service": (svc.get("name") if svc is not None else "") or "",
                "product": (svc.get("product") if svc is not None else "") or "",
                "version": (svc.get("version") if svc is not None else "") or "",
            })
        ports.sort(key=lambda p: p["port"])

        # Skip down hosts in port-scan profiles, but keep them in -sn output.
        if state != "up" and not ports:
            continue
        hosts.append({
            "ip": ip, "mac": mac, "vendor": vendor, "hostname": hostname,
            "state": state, "ports": ports,
        })
    hosts.sort(key=lambda x: tuple(int(o) for o in x["ip"].split(".")) if x["ip"].count(".") == 3 else (999,))
    return hosts


# Canned data for stub mode (Mac dev): one router, two clients.
_STUB_HOSTS: list[dict[str, Any]] = [
    {"ip": "10.0.0.1", "mac": "AA:BB:CC:00:00:01", "vendor": "TP-Link",
     "hostname": "gateway", "state": "up",
     "ports": [{"port": 53, "proto": "tcp", "state": "open", "service": "domain",
                "product": "dnsmasq", "version": "2.90"},
               {"port": 80, "proto": "tcp", "state": "open", "service": "http",
                "product": "lighttpd", "version": "1.4"}]},
    {"ip": "10.0.0.50", "mac": "AA:BB:CC:11:22:33", "vendor": "Apple",
     "hostname": "iphone", "state": "up",
     "ports": [{"port": 62078, "proto": "tcp", "state": "open", "service": "iphone-sync",
                "product": "", "version": ""}]},
    {"ip": "10.0.0.51", "mac": "DE:AD:BE:EF:00:99", "vendor": "Intel",
     "hostname": "victim-vm", "state": "up",
     "ports": [{"port": 22, "proto": "tcp", "state": "open", "service": "ssh",
                "product": "OpenSSH", "version": "8.9p1"},
               {"port": 445, "proto": "tcp", "state": "open", "service": "microsoft-ds",
                "product": "Samba", "version": "4.x"}]},
]
