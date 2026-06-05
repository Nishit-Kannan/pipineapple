#!/usr/bin/env bash
# Launch PiPineapple as root with the venv's Python.
#
# From Session 04 onwards the platform needs elevated privileges to:
#   - toggle adapter mode (iw dev set type monitor / ip link set up|down)
#   - write /etc/udev/rules.d/*.rules and /etc/NetworkManager/conf.d/*
#   - launch hostapd / dnsmasq / aireplay / hcxdumptool
#
# Session 19 will introduce proper privilege separation; for the lab,
# we run the whole thing as root.
#
# Usage:
#   ./run-as-root.sh                       # uses PIPINEAPPLE_CONFIG=dev (default)
#   PIPINEAPPLE_CONFIG=mac ./run-as-root.sh
#
# Args are forwarded to run.py (none used today, room for future).

set -euo pipefail

# Always run from the script's own directory so .venv resolves correctly
# regardless of where the user invoked us from.
cd "$(dirname "$(readlink -f "$0")")"

if [[ ! -x .venv/bin/python ]]; then
    echo "Error: .venv/bin/python not found." >&2
    echo "Set up the venv first:" >&2
    echo "    python3 -m venv .venv" >&2
    echo "    source .venv/bin/activate" >&2
    echo "    pip install -e ." >&2
    exit 1
fi

# Pin DATA_DIR to a persistent path. The config default is /tmp/pipineapple
# (handy for clean-slate dev/test on the Mac), but /tmp is wiped on every
# Pi reboot — which silently nukes auth.json, networking.json, the deny-list
# and adapter roles, so the operator gets the setup wizard every boot.
# /var/lib/pipineapple is FHS-correct for runtime state and survives reboots.
# Override per-session by exporting PIPINEAPPLE_DATA_DIR before calling.
export PIPINEAPPLE_DATA_DIR="${PIPINEAPPLE_DATA_DIR:-/var/lib/pipineapple}"

# -E preserves the environment so PIPINEAPPLE_CONFIG etc. carry through.
exec sudo -E ./.venv/bin/python ./run.py "$@"
