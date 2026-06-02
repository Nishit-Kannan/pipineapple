# PiPineapple — Roadmap

A UI-first WiFi pen-testing platform built on the Raspberry Pi 5, in the spirit of the Hak5 Pineapple. Each feature is built in a Flask web UI on the Pi; each session ends with a console exercise that runs the same operation by hand, so the UI is never a black box.

## Mission

Two parallel deliverables grow together over the course:

1. **A working Pineapple-style platform** on the Pi — browse to `https://pi-lab.local`, run recon, capture handshakes, crack, run rogue APs, MITM clients, all from the browser.
2. **Deep understanding of every command underneath each button**, gained by dropping to the Pi shell after every feature lands and running the same flow by hand.

The UI is the teacher. The shell is the proof.

## Ground rules

Same as the original lab discipline: every attack runs against networks you own or have written authorization to test. Lab uses a dedicated GL.iNet router on its own subnet, lab-only test devices (an old phone, a spare laptop), and the offensive Alfa adapters never touch the home network. Before any session: `airmon-ng check kill` and verify the target MAC is the lab AP. Lab journal stays per-session.

## Hardware

Raspberry Pi 5 (8 GB) running Raspberry Pi OS Lite 64-bit (Bookworm), booting from USB SSD. **Three Alfa AWUS036ACM adapters** on a powered TP-Link UH700 hub. Mac as the operator browser. GL.iNet travel router as the target AP. iPhone + a spare device as victims.

**Adapter role split.** The three offensive radios get sticky udev names tied to their MAC addresses, with dedicated roles so the UI can reason about them by function rather than enumeration order:

- `wlan-mon-2g` — monitor mode on the 2.4 GHz band for recon and capture
- `wlan-mon-5g` — monitor mode on 5 GHz, or repurposed as a dedicated injection/deauth radio depending on the session
- `wlan-ap` — managed mode, hosts the rogue / Evil Twin AP via hostapd

The Pi's built-in `wlan0` stays on the home network for upstream internet (tool updates, browsing the PiPineapple UI from the Mac) and never participates in offensive operations.

**Why three radios matters.** Two adapters force constant role-shuffling: you can capture *or* host a rogue AP, but not both bands simultaneously and not while also injecting cleanly. The third radio resolves the three failure modes that bite hardest in practice — missed handshakes when the client band-steers to the band you weren't watching, single-band Evil Twins that modern devices ignore in favor of the real 5 GHz AP, and deauth bursts that corrupt the timing of a concurrent capture on the same radio. Sessions 04, 06, 11, and 12 specifically take advantage of having three.

Power and bandwidth budget: three AWUS036ACMs peak around 10.5 W total, well inside the UH700's 36 W budget. All three on one USB 3.0 controller is fine on bandwidth (worst case ~300 Mbps against 5 Gbps available). No hub-shuffling needed.

## Tech stack

Python 3.11+ on the Pi (ships by default with Pi OS Bookworm). Flask + Flask-SocketIO for the web app and live updates. Jinja2 templates. Vanilla JS with HTMX where it pays off — no React/Vue/Svelte. SQLite for persistent state (saved configs, capture metadata). systemd unit to run the service. nginx in front with a self-signed cert in the polish phase.

## OS philosophy

Pi OS Lite is a minimal base. Each session that introduces a new tool starts with the explicit `apt install` for that tool — not a kitchen-sink "everything is already there" environment. This is deliberate: the console exercise becomes more meaningful when you've consciously chosen to install hostapd or hcxdumptool or bettercap, because the act of installing forces you to confront what the tool is for. Session 01 installs the build prerequisites (`python3-pip python3-venv git iw ethtool`); later sessions add their own.

Backend pattern: each attack tool gets a thin Python wrapper module (`app/tools/airodump.py`, `app/tools/hostapd.py`, etc.) that knows how to start, stop, and parse output from one shell tool. A `JobManager` keeps long-running subprocesses tracked and addressable from the UI. WebSockets stream live state to the browser.

## Project layout

```
pipineapple/
  app/
    __init__.py          # Flask factory
    routes/              # Blueprint per UI feature
    tools/               # Subprocess wrappers (airodump, hostapd, etc.)
    services/            # JobManager, parsers, state store
    templates/
    static/
  sessions/              # Per-session journals
  tests/
  config.py
  pyproject.toml
  run.py
  README.md
  ROADMAP.md             # this file
```

## Curriculum

Seven phases, seventeen sessions. Each session lands one usable feature in the UI and one console exercise. Order matters — later sessions build on earlier scaffolding.

### Phase A — Skeleton

**Session 01.** Project scaffold, Flask app factory, base template with nav, system status panel (CPU temp, memory, IP addresses, adapter list with mode/channel/driver). Browseable from the Mac. Console: how `iw dev`, `ip link`, `/proc/net/wireless`, and `vcgencmd measure_temp` produce the data the panel displays.

**Session 02.** WebSocket integration via Flask-SocketIO, live-updating status panel (no more page refresh). JobManager skeleton — service that owns subprocess lifecycles and streams stdout to the UI. Console: anatomy of a long-running subprocess, signal handling, `/proc/<pid>/status`.

### Phase B — Recon

**Session 03.** Adapter management panel — list both Alfas, toggle monitor mode per adapter from the UI. Backend wraps `airmon-ng start/stop` and direct `iw dev` calls. Status badge per adapter (managed / monitor / down). Console: `iw dev <iface> set type monitor`, NetworkManager interactions, why `airmon-ng check kill` matters.

**Session 04.** Live AP scan panel — start/stop airodump in background, parse its CSV output as it rotates, push AP list to UI via WebSocket. Columns: BSSID, SSID, channel, encryption, signal, beacons. Filterable. With three adapters, the UI runs *two* parallel scans by default — one on `wlan-mon-2g` locked to the 2.4 GHz band and one on `wlan-mon-5g` locked to 5 GHz — and merges the AP lists. No channel hopping means no missed beacons. Console: airodump's `-w` CSV format, `--band abg` vs band-locked scans, why channel hopping costs you frames.

**Session 05.** Client/station panel — for a selected AP, show associated STATIONs with their MACs, probes, signal. MAC vendor lookup (OUI database lookup) shown inline. Console: how to interpret the airodump STATION table, MAC randomization indicators, probe behavior in modern OSes.

### Phase C — Capture and crack

**Session 06.** Targeted handshake capture — select AP from the scan panel, click "capture," backend locks airodump to that channel/BSSID and writes a named `.cap`. UI shows handshake-detected status when EAPOL frames are seen. With three adapters, the UI optionally locks `wlan-mon-2g` to the AP's 2.4 GHz BSSID *and* `wlan-mon-5g` to the 5 GHz BSSID at the same time — band-steered clients can't slip through. Captures merge via `mergecap` at session end. Console: `-c`, `--bssid`, the EAPOL 4-way structure, the band-steering miss mode.

**Session 07.** Deauth helper — from the STATION list, button to send N targeted deauth bursts. Backend wraps `aireplay-ng -0`, dispatched on a dedicated injection radio (`wlan-mon-5g` retasked, or `wlan-mon-2g` if the target is 2.4 GHz) so the capture radios stay clean. Console: deauth frame structure, broadcast vs targeted, why some clients ignore broadcasts, why separating injection from capture matters at the radio level.

**Session 08.** Crack panel — select a `.cap` from disk, choose wordlist, optionally select hashcat rule file, launch crack. UI shows progress (H/s, current candidate, ETA). Backend uses `hcxpcapngtool` for conversion and either `hashcat -m 22000` or `aircrack-ng` depending on selection. Console: `.hc22000` format, mode 22000 internals, the Pi-vs-Mac-vs-Jetson hardware decision.

### Phase D — Rogue AP

**Session 09.** Hostapd launcher — form-based UI to spin up an AP on `wlan2` with chosen SSID, channel, band, optional WPA2 PSK. Backend renders a `hostapd.conf`, starts the daemon, tails its log to the UI. Console: every hostapd.conf field and why it matters.

**Session 10.** DHCP + DNS for the rogue AP — extend the launcher with dnsmasq configuration (DHCP range, gateway, DNS server, DNS overrides). Static IP assignment to `wlan2`. Optional NAT pass-through to `wlan0` for upstream connectivity. Console: dnsmasq config syntax, `iptables -t nat` MASQUERADE mechanics.

**Session 11.** Evil Twin one-click — combine Sessions 7/9/10 into a single attack flow: pick a target AP, click "Evil Twin," backend deauths the target's clients on the injection radio while standing up a lookalike AP on the same SSID on `wlan-ap`. If the target AP is dual-band, a stretch goal is dual-band Evil Twin — `wlan-ap` on the target's primary band and a virtual interface (or `wlan-mon-5g` repurposed momentarily) on the other, since modern devices that prefer 5 GHz will ignore a 2.4-only rogue. Live view of clients associating to the rogue. Console: end-to-end flow timing, captive portal probe traffic (`captive.apple.com`, `connectivitycheck.gstatic.com`).

**Session 12.** Probe responder / MANA mode — UI to enable a mode where the rogue AP responds to *any* directed probe request, claiming to be that SSID. Backend uses `hostapd-mana`. With three radios, MANA can run on a separate adapter from `wlan-ap`, meaning you can advertise a *specific* lookalike SSID (Evil Twin) and a *catch-all* probe responder (Karma) at the same time — devices picky enough to ignore unknown SSIDs get the Evil Twin, devices loose enough to roam to anything familiar get caught by MANA. Live view of probes received and the SSIDs they reveal. Console: how directed probes work, modern OS mitigations, when MANA still wins (IoT, legacy devices, devices that recently saw a beacon for a saved SSID).

### Phase E — MITM

**Session 13.** bettercap integration — UI panel for ARP spoof and DNS spoof modules. Backend launches bettercap with a generated caplet, parses events.stream JSON output to the UI. Selectable targets (from the associated-clients list). Console: bettercap's REPL, caplets, what ARP spoof actually looks like on the wire.

**Session 14.** Traffic inspection panel — live decoded stream of "interesting" traffic from the bettercap session: DNS queries, HTTP hosts, plaintext credentials if any. Captive portal builder — drop a template HTML file as a phishing portal, served from the Pi when DNS spoof redirects victims. Console: serving content via dnsmasq's `address=/host/ip` plus a local web server.

### Phase F — Post-association recon

**Session 15.** nmap panel — once Evil Twin victims associate or you've cracked into a network, scan from the UI. Subnet discovery (`-sn`), service/version detect (`-sV`), targeted full scans, NSE script bundles. Results rendered as sortable tables with port → service → version columns. Console: nmap modes, when to use which, NSE script categories.

### Phase G — Polish and modules

**Session 16.** Captures browser — UI for navigating saved `.cap` and `.hc22000` files: download, preview frame counts, see SSID/BSSID summary, kick off a crack from the file's context menu. Optional: in-browser pcap viewer using a JS pcap parser for quick first-look without scp.

**Session 17.** Module system + production polish — plugin architecture so new attacks can be added as drop-in Python modules with a manifest declaring routes, templates, and tools needed. HTTPS via self-signed cert. Basic auth. systemd unit. Persistent config (saved hostapd profiles, common wordlists, etc.). Deployment runbook.

## Per-session shape

Every session follows the same pattern:

1. **Outline the feature** — what the button does, why it's the next logical thing to build.
2. **Build the UI piece** — Flask route + Jinja template + minimal JS.
3. **Wire the backend** — subprocess wrapper around the underlying tool.
4. **Run it in the browser** — from the Mac, against the lab.
5. **Drop to the Pi shell** — run the same operation by hand to understand what the wrapper just orchestrated.
6. **Journal** — `sessions/session-NN.md` with what was built, what the console exercise revealed, and any parked questions.

## Cadence

Reasonable evening-pace: one session per evening or split across two depending on depth. Full course is 6–8 weeks. Don't rush the Phase A skeleton — clean foundations make later sessions fast.

## Archived

The pre-pivot console-first roadmap, cheatsheet, and sessions 01–02 are at `../archive/` for reference. The nmap notes, the hashcat-on-Pi-5 segfault writeup, and the EAPOL deep dive in archived session-02 are still useful background reading.
