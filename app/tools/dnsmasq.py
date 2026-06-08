"""dnsmasq wrapper — DHCP + DNS server.

For S04.6 we use dnsmasq only as a DHCP server on the management AP
subnet. DNS lookups stay basic — point ``pipineapple.local`` and the
management gateway at the Pi so muscle-memory hostnames work.

Like hostapd, dnsmasq is a long-running daemon launched via the
JobManager. The same wrapper handles both the management AP DHCP and
the Phase D rogue AP DHCP (different config files, different interface
bindings).
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.tools._common import stub_mode

log = logging.getLogger(__name__)


def render_config(
    iface: str,
    gateway_ip: str,
    dhcp_range_start: str,
    dhcp_range_end: str,
    dhcp_lease: str = "12h",
    local_hostnames: dict[str, str] | None = None,
    log_queries: bool = False,
    forward_dns: bool = False,
    upstream_dns: tuple[str, ...] = ("1.1.1.1", "8.8.8.8"),
    coexist_with_other_dnsmasq: bool = False,
) -> str:
    """Render a minimal dnsmasq.conf bound to one interface.

    ``local_hostnames`` is a dict of {hostname: ip} mappings dnsmasq
    will resolve directly (without consulting upstream DNS). Useful for
    pointing 'pipineapple.local' at the management gateway.

    ``forward_dns=True`` makes dnsmasq forward queries to upstream
    resolvers (default Cloudflare + Google). Required when AP clients
    need internet — without it, clients get IPs from DHCP but can't
    resolve any hostnames.

    ``coexist_with_other_dnsmasq`` switches from ``bind-interfaces``
    (which grabs 0.0.0.0:67 + 127.0.0.1:53 and refuses to share) to
    ``bind-dynamic`` (uses ``SO_BINDTODEVICE`` so the DHCP socket is
    restricted to ``iface``) plus ``except-interface=lo`` (skip the
    loopback DNS bind that fights any other dnsmasq for ``127.0.0.1:53``).
    Needed when running multiple dnsmasq instances on the same host —
    e.g. the management AP and the PineAP rogue AP simultaneously.
    """
    lines = [
        f"interface={iface}",
    ]
    if coexist_with_other_dnsmasq:
        # bind-dynamic uses SO_BINDTODEVICE on the DHCP socket so it
        # only catches DHCP frames from `iface` even though port 67
        # itself is shared. except-interface=lo skips the loopback DNS
        # listener so we don't fight the other dnsmasq for 127.0.0.1:53.
        lines += [
            "bind-dynamic",
            "except-interface=lo",
        ]
    else:
        lines.append("bind-interfaces")
    lines += [
        f"dhcp-range={dhcp_range_start},{dhcp_range_end},{dhcp_lease}",
        f"dhcp-option=3,{gateway_ip}",   # default route
        f"dhcp-option=6,{gateway_ip}",   # DNS server
        # Disable reading /etc/hosts so it doesn't conflict
        "no-hosts",
    ]
    if forward_dns:
        for dns in upstream_dns:
            lines.append(f"server={dns}")
    else:
        # Don't forward queries upstream — isolated subnet
        lines.append("no-resolv")
    if log_queries:
        lines += ["log-queries", "log-dhcp"]
    for hostname, ip in (local_hostnames or {}).items():
        lines.append(f"address=/{hostname}/{ip}")
    return "\n".join(lines) + "\n"


def write_config(path: Path, config_body: str) -> tuple[bool, str]:
    """Write the dnsmasq config file."""
    if stub_mode():
        preview = Path(f"/tmp/pipineapple-dnsmasq-{path.name}.preview")
        preview.write_text(config_body)
        return True, f"(stub) wrote {preview}"
    log.info("dnsmasq.write_config -> %s", path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config_body)
    except Exception as e:
        log.exception("dnsmasq config write failed (%s)", path)
        return False, f"dnsmasq config write failed: {e}"
    log.info("dnsmasq config written: %s (%d bytes)", path, len(config_body))
    return True, f"wrote {path}"
