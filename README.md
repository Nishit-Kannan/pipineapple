# PiPineapple

A DIY WiFi pen-testing platform for the Raspberry Pi 5, in the spirit of the Hak5 Pineapple. Flask web UI that drives `airodump-ng`, `hostapd`, `dnsmasq`, `aireplay-ng`, `hashcat`, `bettercap`, and `nmap` against a controlled lab environment.

UI-first learning project — every feature has a console equivalent so the orchestration is never a mystery. See `ROADMAP.md` for the full curriculum.

## Lab-only

This software is for testing networks you own or have written permission to test. Don't run it against networks you don't have authorization to attack.

## Hardware

- Raspberry Pi 5 (8 GB), Raspberry Pi OS Lite 64-bit (Bookworm), booting from USB SSD
- 3× Alfa AWUS036ACM (MT7612U), on a powered TP-Link UH700 hub
- Adapter role split: `wlan-mon-2g`, `wlan-mon-5g`, `wlan-ap` (sticky udev names)

## Quickstart (Pi)

```bash
# System prerequisites (one-time)
sudo apt update
sudo apt install -y python3-pip python3-venv git iw ethtool

# Project
git clone <repo-url> ~/pipineapple
cd ~/pipineapple
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python run.py
```

Then from any machine on the same network: `http://pi-lab.local:5000` (or the Pi's IP).

Each later session that introduces a new offensive tool installs it explicitly at the start of that session (e.g. `apt install aircrack-ng` in Session 03, `apt install hostapd dnsmasq` in Session 09).

## Project layout

```
app/
  __init__.py         # Flask app factory
  config.py           # Dev / Test / Prod configs
  routes/             # Blueprints (one per feature area)
  services/           # Stateful orchestration (sysinfo, JobManager, parsers)
  tools/              # Subprocess wrappers (airodump, hostapd, ...)
  templates/          # Jinja2 templates
  static/             # CSS, JS, assets
sessions/             # Per-session learning journals
tests/                # pytest
pyproject.toml
run.py                # Development entrypoint
ROADMAP.md            # Curriculum and architecture
```

Strict dependency direction: `routes/` → `services/` → `tools/`. Routes are thin (call services, render templates); services orchestrate; tools encapsulate subprocesses.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```
