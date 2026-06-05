"""iptables wrapper — NAT + FORWARD rule management for internet sharing.

Used to share upstream internet (typically wlan0 → home Wi-Fi → ISP)
with clients on the management AP subnet (10.42.0.0/24 → wlan-mgmt-ap).

Rules are idempotent — each operation checks with -C first, only adds
if missing. Iptables doesn't persist across reboots by default; we
re-apply on every AP enable.
"""

from __future__ import annotations

import logging

from app.tools._common import run, stub_mode

log = logging.getLogger(__name__)


def enable_ip_forward() -> tuple[bool, str]:
    """Turn on IPv4 forwarding via sysctl."""
    if stub_mode():
        return True, "(stub) sysctl net.ipv4.ip_forward=1"
    r = run(["sysctl", "-w", "net.ipv4.ip_forward=1"], timeout=2.0)
    if r.returncode == 0:
        return True, "ip_forward=1"
    return False, f"sysctl failed: {r.stderr.strip()}"


def _rule_present(table: str, chain: str, rule_args: list[str]) -> bool:
    """Check if a specific iptables rule already exists."""
    cmd = ["iptables", "-t", table, "-C", chain] + rule_args
    r = run(cmd, timeout=2.0)
    return r.returncode == 0


def ensure_nat_masquerade(subnet: str) -> tuple[bool, str]:
    """Add the POSTROUTING MASQUERADE rule for the AP subnet, if missing."""
    if stub_mode():
        return True, f"(stub) iptables -t nat -A POSTROUTING -s {subnet} -j MASQUERADE"
    rule = ["-s", subnet, "-j", "MASQUERADE"]
    if _rule_present("nat", "POSTROUTING", rule):
        return True, f"MASQUERADE rule for {subnet} already present"
    r = run(["iptables", "-t", "nat", "-A", "POSTROUTING"] + rule, timeout=3.0)
    if r.returncode == 0:
        return True, f"added MASQUERADE for {subnet}"
    return False, f"iptables add failed: {r.stderr.strip()}"


def ensure_forward_rules(subnet: str) -> tuple[bool, str]:
    """Add the FORWARD rules that let traffic flow both ways between
    the AP subnet and the rest of the network."""
    if stub_mode():
        return True, f"(stub) FORWARD rules for {subnet}"
    msgs: list[str] = []
    rules = [
        ["-s", subnet, "-j", "ACCEPT"],
        ["-d", subnet, "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
    ]
    for rule in rules:
        if _rule_present("filter", "FORWARD", rule):
            msgs.append(f"FORWARD rule present: {' '.join(rule)}")
            continue
        r = run(["iptables", "-A", "FORWARD"] + rule, timeout=3.0)
        if r.returncode != 0:
            return False, f"FORWARD add failed: {r.stderr.strip()}"
        msgs.append(f"added FORWARD: {' '.join(rule)}")
    return True, "; ".join(msgs)


def remove_nat_and_forward(subnet: str) -> tuple[bool, str]:
    """Remove the NAT + FORWARD rules for the AP subnet. Best-effort —
    ignores 'rule does not exist' errors so it's safe to call when
    nothing's configured."""
    if stub_mode():
        return True, f"(stub) removed NAT+FORWARD for {subnet}"
    msgs: list[str] = []
    teardown = [
        ["iptables", "-t", "nat", "-D", "POSTROUTING", "-s", subnet, "-j", "MASQUERADE"],
        ["iptables", "-D", "FORWARD", "-s", subnet, "-j", "ACCEPT"],
        ["iptables", "-D", "FORWARD", "-d", subnet, "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
    ]
    for cmd in teardown:
        r = run(cmd, timeout=3.0)
        if r.returncode == 0:
            msgs.append("removed")
        # rc=1 with "does not exist" is fine — rule wasn't there
    return True, f"teardown attempted ({len(teardown)} rules)"
