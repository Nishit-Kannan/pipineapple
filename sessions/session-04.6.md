# Session 04.6 — Management AP + Wi-Fi client mode (field portability)

**Date:** 2026-06-04
**Phase:** A/B interlude — second security/portability insertion, before recon
**Goal:** Make the platform fully UI-controllable for network access. Operator can configure home Wi-Fi as upstream from the UI, toggle a private management AP on `wlan0` for field use, and switch between modes — all without SSH or Ethernet. Field-portable means the operator can carry the Pi anywhere and reach the UI as long as there's a Mac nearby.

---

## Checkpoint 1 — Concepts: management AP, client mode, wlan0 mutual exclusion

**Decided:**

- **wlan0 is either AP or client, not both.** Single physical radio = single mode at a time. The platform UI surfaces this honestly — switching modes always drops one connection in favor of the other. Virtual interfaces on Pi 5's `brcmfmac` are flaky; we don't rely on them.
- **Management AP runs `hostapd` + `dnsmasq` on `wlan0`.** Subnet `10.42.0.0/24`, gateway `10.42.0.1`, WPA2 with operator-set password. Saved in `$DATA_DIR/networking.json` and restored on Flask startup.
- **Client mode uses NetworkManager.** `nmcli device wifi connect <SSID> password <pw>` saves a profile that auto-reconnects on every boot. The platform doesn't persist Wi-Fi passwords itself — NM owns that.
- **Mode switching is atomic from the UI.** Each switch confirms with the operator first ("you'll lose this connection"). Backend sequences: AP → stop hostapd+dnsmasq → flush IP → NM reclaims wlan0 → optional client reconnect. Client → AP: NM releases wlan0 → static IP → start daemons.
- **Locked-out recovery paths.** Localhost-always-allowed in the access-control layer means SSH-curl from the Pi shell can always undo any networking misconfig. Removing `$DATA_DIR/networking.json` reverts to defaults on next Flask start.

---

## Checkpoint 2 — Tool wrappers: hostapd + dnsmasq + nmcli wifi

**Built:**

- `app/tools/hostapd.py` — `render_config(iface, ssid, password, channel, hw_mode, country_code)` + `write_config(path, body)`. Stub mode writes to `/tmp/pipineapple-hostapd-*.preview` for inspection. WPA2 if password set, open AP if not. Reused in Phase D for the rogue AP on `wlan-ap`.
- `app/tools/dnsmasq.py` — same shape. Renders a minimal DHCP-only config bound to one interface with `bind-interfaces`, `no-resolv`, `no-hosts`. Local hostname overrides (e.g. `address=/pipineapple.local/10.42.0.1`) supported.
- `app/tools/nm.py` extended — `set_managed(iface, bool)`, `wifi_scan(iface)` parsing `nmcli -t` terse format (escape-aware splitter for SSIDs/BSSIDs that contain colons), `wifi_connect`, `wifi_disconnect`, `list_saved_wifi` (filtered to wireless connections with autoconnect + active state), `forget_wifi`.
- `app/tools/iproute.py` extended — `add_address(iface, cidr)` and `flush_address(iface)` so the static-IP step on AP enable is stub-mode-safe.

---

## Checkpoint 3 — Networking service + state machine

**Built `app/services/networking.py`:**

State machine over `wlan0_mode: "idle" | "client" | "ap"`. Public API:

- `get_state()` — current mode, mgmt-ap config (password redacted), saved Wi-Fi list.
- `scan_wifi()` — returns sorted-by-signal network list.
- `connect_wifi(ssid, password)` — saves NM profile, disables AP first if active, connects, persists `wlan0_mode=client`.
- `disconnect_wifi()` — `nmcli device disconnect wlan0`, mode → `idle`.
- `forget_wifi(name)` — delete NM profile.
- `configure_mgmt_ap(ssid, password, channel)` — saves AP config without enabling. Validates SSID 1-32 chars, WPA2 password ≥8 chars.
- `enable_mgmt_ap()` — full sequence: release wlan0 from NM, flush + add static IP, bring up, render configs, launch `dnsmasq` + `hostapd` via JobManager, persist `wlan0_mode=ap`.
- `disable_mgmt_ap()` — stop both jobs, flush IP, return wlan0 to NM.
- `restore_on_startup()` — re-applies saved mode after Flask restart.

**The JobManager is the load-bearing piece here.** `hostapd` and `dnsmasq` are long-running daemons; JobManager owns their lifecycle (no orphan/zombie processes, SIGTERM grace teardown, stdout streamed to per-job SocketIO rooms for debugging). This is the **first real consumer of the JobManager skeleton** built in Session 02.

---

## Checkpoint 4 — Networking tab in Settings

**Built `/tab-networking` panel inside Settings:**

- **Three stat cards** at the top — wlan0 mode (idle/client/ap), management AP status (active/inactive + SSID), count of saved NM profiles.
- **Client mode card** — saved-networks table with Connect/Forget per row, "Scan for networks" button (renders a second table with signal + security + click-to-connect), "Disconnect wlan0" button.
- **Management AP card** — form for SSID + WPA2 password + channel (saved via `POST /networking/mgmt-ap/configure`), Enable + Disable buttons with confirmation prompts that explain the wlan0 mutex (you'll lose connection if reaching the UI via wlan0). Visual cue at the bottom telling the operator where to reconnect (`http://10.42.0.1:5000`) after enabling.

**JS additions in `settings.js`:**

- `renderNetworkingState(state)` updates stat cards + saved-networks table from the JSON API.
- `renderWifiScan(networks)` renders the scan-results table with click-to-connect.
- Connect-from-scan handler prompts for password (browser `prompt()`) if the network is secured.
- All actions show progress in the status pill, confirm on destructive operations.

---

## Checkpoint 5 — Startup restore + persistent state

**Built:** Factory now spawns a `networking-restore` daemon thread on app startup that calls `NetworkingService.restore_on_startup()`. If the saved state was `ap`, re-launches `hostapd`+`dnsmasq` automatically; if `client`, NM handles reconnection via the saved profile.

**Safety net:** If saved state is `ap` but the AP config is incomplete (missing SSID/password), the restore falls back to `idle` and logs a warning rather than entering a broken state.

---

## Checkpoint 6 — Verification (stub mode)

All checks pass:

- `GET /settings/networking` returns full state JSON (mode, mgmt_ap with password redacted, saved_wifi list) ✓
- `POST /settings/networking/mgmt-ap/configure` saves config ✓
- `POST /settings/networking/mgmt-ap/enable` runs the full enable sequence — 7 steps, all logged ✓
- `hostapd.conf` rendered correctly with WPA2, channel 6, SSID ✓
- `dnsmasq.conf` rendered with bind-interfaces, DHCP range 10.42.0.10–100, local-hostname mappings ✓
- `POST /settings/networking/wifi/scan` returns parsed network list (sorted by signal) ✓
- `POST /settings/networking/wifi/connect` saves profile + transitions to client mode ✓
- `POST /settings/networking/mgmt-ap/disable` tears down cleanly ✓
- Settings page UI renders Networking tab with all sections ✓

In sandbox stub mode JobManager couldn't actually launch `hostapd`/`dnsmasq` (binaries missing), but it failed gracefully — job status went FAILED, error logged, app stayed responsive. On Pi after `apt install hostapd dnsmasq`, those steps actually launch the daemons.

---

## Checkpoint 7 — Deploy + verify on Pi

**Done (Pi-side prereqs):**

```bash
sudo apt update
sudo apt install -y hostapd dnsmasq
sudo systemctl disable hostapd dnsmasq   # we manage them via JobManager, not systemd
sudo systemctl stop hostapd dnsmasq      # in case they auto-started
```

**Done (on the Pi):**

1. Pulled latest, restarted via `./run-as-root.sh`.
2. Logged in, navigated Settings → Networking. Networking tab live with the stat cards + client-mode + management-AP sections.
3. **Tested client mode setup:** clicked "Scan for networks," saw home Wi-Fi + a couple neighbors, clicked Connect on home Wi-Fi, prompted for password, connected. wlan0 now showed `client` in the mode stat card, and the home Wi-Fi appeared as a saved network with "connected" badge.
4. **Tested management AP enable:** filled in form (SSID `PiPineapple-Mgmt`, WPA2 password, channel 6), clicked Save Config, then Enable. Confirmation prompt appeared. After confirming, the page lost the WebSocket briefly because wlan0 switched modes — the live indicator went grey for a moment. From the Mac, the home-Wi-Fi connection dropped (wlan0 stopped being a client). The new SSID appeared in the Mac's Wi-Fi list. Joined it. Mac got `10.42.0.10` from DHCP. Browsed to `http://10.42.0.1:5000` — login page from the new subnet.
5. **Tested switch back to client mode:** logged in via the management AP, went to Settings → Networking, clicked Connect on the home Wi-Fi saved network. Backend disabled mgmt AP first, then reconnected wlan0 to home Wi-Fi. Mac's management AP connection dropped; Mac rejoined home Wi-Fi; reached the UI via the home-network IP.

**Both directions work.** The platform is now fully UI-controlled.

---

## Checkpoint 8 — Learning Centre updated

**Added "Management AP & client mode (wlan0)" topic section** to the Learning Centre. Thirteen commands covering:

- `nmcli device wifi list ifname wlan0` (scan)
- `nmcli device wifi connect ... password ...` (save + connect)
- `nmcli connection show` (list profiles)
- `nmcli connection delete <SSID>` (forget)
- `nmcli device set wlan0 managed no` (release for hostapd)
- `ip addr add 10.42.0.1/24 dev wlan0` (static IP for AP)
- Inspecting `mgmt-ap-hostapd.conf` and `mgmt-ap-dnsmasq.conf`
- Manual `hostapd` + `dnsmasq` invocations for debugging
- `iw dev wlan0 info` to verify mode after switch
- `nmcli device status` for the platform-wide view
- Emergency reset via `rm $PIPINEAPPLE_DATA_DIR/networking.json`

Learning Centre now has nine topic sections.

---

## Session-wide findings

- **The wlan0 mutex is real and worth surfacing in the UI.** Users *will* enable management AP while connected via home Wi-Fi at least once and be momentarily confused when the connection drops. The confirmation prompts + post-action message ("connect to 10.42.0.1:5000") prevent this from being a wedge.
- **JobManager pays off the moment you have a daemon.** Without it, hostapd would either need to be `systemctl start`'d (less platform control) or wrapped in shell scripts (no UI integration). The S02 work made S04.6's daemon handling trivial.
- **`nmcli -t` (terse) format with escape-aware splitting** is the right way to parse NM output programmatically. The human-readable format breaks on SSIDs with colons.
- **State persistence makes the platform feel real.** Reboot the Pi → it comes back in the same mode it was in. Without the `restore_on_startup` step, the operator would have to re-enable the AP on every boot.

---

## Parked for later

- **First-boot default of management AP enabled.** The platform supports it (the code path exists), but it's not auto-triggered yet because a totally-fresh-boot user might not expect their Pi to silently become a Wi-Fi AP. Revisit when we package the platform as a flashable image.
- **Operator's Wi-Fi password manager.** Currently each `wifi_connect` call passes the password through; NM stores it in `/etc/NetworkManager/system-connections/<SSID>.nmconnection` (encrypted at rest on most installs). The UI doesn't currently let you view saved passwords (intentional). Adding a "show password" eye icon would be reasonable later.
- **5th radio support** — adding a USB Wi-Fi dongle dedicated to management AP so it can run concurrently with wlan0 client mode. Hardware-detection + role-assignment logic already exists in S04's Adapter Management; just need to extend the role enum.

---

## What's now possible

- **Fully field-portable.** Operator carries the Pi anywhere, powers it on, joins `PiPineapple-Mgmt`, operates from the UI. No SSH, no Ethernet, no shell access needed.
- **Home use** still works the same way (Pi joined to home Wi-Fi via wlan0, Mac on same Wi-Fi, reach UI by hostname).
- **Switching between modes** is a one-click operation with proper user expectations set.

We're truly ready for Session 05 (recon scan table) — the operator has a stable management surface regardless of where the Pi is physically located.
