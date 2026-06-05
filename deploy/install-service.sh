#!/usr/bin/env bash
# Install the PiPineapple systemd unit so the platform starts on boot.
#
# Run this ONCE on the Pi after the project is deployed. Idempotent:
# re-running just refreshes the unit file and reloads systemd.
#
# Usage:
#   sudo ./deploy/install-service.sh
#
# After install, manage the service like any other systemd unit:
#   sudo systemctl status pipineapple
#   sudo systemctl restart pipineapple
#   sudo journalctl -u pipineapple -f       # live logs
#   sudo systemctl disable pipineapple      # stop auto-start on boot

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Error: must run as root (use sudo)." >&2
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
UNIT_SRC="${REPO_DIR}/deploy/pipineapple.service"
UNIT_DST="/etc/systemd/system/pipineapple.service"

if [[ ! -f "${UNIT_SRC}" ]]; then
    echo "Error: ${UNIT_SRC} not found." >&2
    exit 1
fi

# Sanity-check the path baked into the unit matches where we actually live.
# The unit hardcodes /home/pi-lab/pipineapple; warn if the repo is elsewhere.
if [[ "${REPO_DIR}" != "/home/pi-lab/pipineapple" ]]; then
    echo "WARNING: this repo is at ${REPO_DIR} but the unit expects" >&2
    echo "         /home/pi-lab/pipineapple. Edit deploy/pipineapple.service" >&2
    echo "         (WorkingDirectory, ExecStart) before installing." >&2
    read -rp "Continue anyway? [y/N] " ans
    [[ "${ans}" =~ ^[Yy]$ ]] || exit 1
fi

# Make sure the data dir exists with the right ownership before the service
# starts. The Flask factory does mkdir(parents=True, exist_ok=True), but
# pre-creating with explicit perms is cleaner.
mkdir -p /var/lib/pipineapple
chmod 700 /var/lib/pipineapple

echo "Installing ${UNIT_SRC} -> ${UNIT_DST}"
install -m 644 "${UNIT_SRC}" "${UNIT_DST}"

systemctl daemon-reload
systemctl enable pipineapple.service
systemctl restart pipineapple.service

sleep 2
systemctl --no-pager status pipineapple.service | head -20

echo
echo "Done. PiPineapple will now start on every boot."
echo "  Live logs:    sudo journalctl -u pipineapple -f"
echo "  Stop:         sudo systemctl stop pipineapple"
echo "  Disable:      sudo systemctl disable pipineapple"
