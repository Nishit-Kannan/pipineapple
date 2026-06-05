# Session 04.7 — Multi-radio management AP

**Date:** 2026-06-04
**Phase:** A/B interlude — third portability insertion, between mgmt-AP-on-wlan0 (S04.6) and recon (S05)
**Goal:** Stop pretending the management AP has to live on `wlan0`. With three Alfa adapters plus a Realtek already on hand, the right architecture is "AP on its own radio so wlan0 stays a client." Pi 5's `brcmfmac` does AP just fine but locks `wlan0` out of being a Wi-Fi client at the same time — which means no upstream Wi-Fi for the platform unless you bring a cable. Field-portable demands both at once.

---

## Checkpoint 1 — Concepts: decouple AP state from `wlan0` state

**Decided:**

- **One state variable per concern.** The old single field `wlan0_mode ∈ {idle, client, ap}` conflated two things. New schema:
  - `mgmt_ap_active: bool` — is the management AP running? On any interface, including not-`wlan0`.
  - `wlan0_mode ∈ {idle, client, ap}` — `wlan0`'s actual mode, independent of where the AP is.
- **Auto-migration in `_load()`.** Existing operator state stays valid: if `mgmt_ap_active` is absent, derive it from the legacy `wlan0_mode == "ap"` check, then correct `wlan0_mode` to `idle` if the AP is actually on a different interface.
- **AP interface lives in the AP config dict.** `mgmt_ap.interface` defaults to `wlan0` for first-boot bootstrap. After udev sticky names land, the operator can move it to `wlan-mgmt-ap` (Realtek or whichever dedicated AP radio) via the new UI dropdown.
- **Move = stop on old iface, update config, start on new iface.** Single atomic operation. Operator briefly loses the connection during the transition and reconnects to the same SSID now broadcast by a different physical radio. No half-states.

---

## Checkpoint 2 — Service changes

**Edited `app/services/networking.py`:**

- New `move_mgmt_ap(new_iface)` method — atomic teardown + state-edit + start on new iface. Validates the target is a real wireless interface; refuses to "move" to the same iface.
- `_enable_mgmt_ap_unlocked(state, ap)` now reads the iface from `ap["interface"]` instead of hardcoding `wlan0`. Per-step messages prefix with `target interface: <iface>` so the log shows where the AP is being stood up.
- State bookkeeping: end of enable sets `mgmt_ap_active = True` unconditionally; only sets `wlan0_mode = "ap"` if the iface is actually `wlan0`. Same logic in reverse for disable.
- `connect_wifi(ssid, pw)` only tears down the mgmt AP if it's currently on `wlan0`. If the AP is on a different radio, wlan0 client connect doesn't disturb it. Two-radio operation in practice.
- `save_wifi(ssid, pw)` added — saves an NM profile without trying to connect right now. Used by the UI when wlan0 is busy as a client of one network and the operator wants to stash credentials for a different one.

---

## Checkpoint 3 — UI: interface picker for the AP

**Settings → Networking tab:**

- New `<select>` "Interface" dropdown lists every wireless device on the Pi. Current AP iface is pre-selected.
- "Move AP to this interface" button → `POST /settings/networking/mgmt-ap/move`. Disabled until the dropdown selection differs from the current AP iface.
- Status pill now shows "AP on `<iface>`" instead of just "AP active" — operator can see at a glance which radio is broadcasting.

**Wired up `_socketio_auth_gate`** so SocketIO connects bypass the redirect-to-login flow but still respect the auth check. Same `before_request` middleware deferring to it.

---

## Checkpoint 4 — Verification (Pi only — this needs real adapters)

Stub mode can't really exercise the multi-radio path. Verified on the Pi:

- Boot fresh, AP comes up on `wlan0` (bootstrap path).
- Add a Realtek as a second wireless adapter; it shows up as `wlan1` initially.
- Set role `wlan-mgmt-ap` for the Realtek's MAC, apply udev rules — interface renames live to `wlan-mgmt-ap`.
- In Settings → Networking, pick `wlan-mgmt-ap` from the dropdown, click Move AP.
- AP drops on wlan0, comes up on `wlan-mgmt-ap`. Phone reconnects automatically (same SSID/password).
- `wlan0` now free to be a client. `nmcli device wifi list ifname wlan0` works; `nmcli device wifi connect <SSID>` brings wlan0 up on the home network.
- Pi has internet via wlan0 client AND broadcasts the management AP on `wlan-mgmt-ap` simultaneously. The thing we couldn't do before.

---

## Session-wide findings

- **The state-machine simplification was the real win.** Conflating "AP is running" with "wlan0's mode" looked harmless in S04.6 because the AP only ever ran on wlan0. The moment a second radio appeared, every code path that checked `wlan0_mode == "ap"` was wrong in a new way. Splitting `mgmt_ap_active` out is the kind of refactor that pays back forever.
- **`nmcli connection add ... ifname wlan0` refuses if wlan0 is unmanaged.** The `save_wifi` flow runs while the mgmt AP holds wlan0 (so NM has it unmanaged). NM's check fires before the profile is stored. Workaround: omit `ifname` from the `add` command — NM stores the profile interface-agnostic, picks an iface when later activated. Documented this in the wrapper docstring so it doesn't get "fixed" later.
- **Pi 5 onboard radio behaves better than I expected as an AP.** Even on bootstrap path before udev names settle, hostapd on wlan0 holds clients reliably. The reason to move to an Alfa is `wlan0` freedom, not AP quality.

---

## Parked for later

- **AP load-balance across radios.** Could in principle run the management AP on two radios simultaneously and let clients roam. Overkill for a single-operator lab; deferred indefinitely.
- **5 GHz management AP.** Currently the AP is always 2.4 GHz channel 6. Some Alfas (and many phones in noisy environments) prefer 5 GHz. Adding a `band` field to `mgmt_ap` is small; deferring until I notice an actual problem.
- **Per-interface NM profiles for client saves.** Today `save_wifi` is iface-agnostic which works but loses the "this network is only on wlan0" intent. Acceptable for now; revisit if multi-radio client mode ever shows up.

---

## What's now possible

- **Mgmt UI reachable from a Mac while the Pi is online via home Wi-Fi.** Bootstrap setup, then move AP off wlan0, then connect wlan0 to home Wi-Fi → operator has internet + a stable management AP, simultaneously.
- **The bootstrap iface auto-selection** in `restore_on_startup` now prefers `wlan-mgmt-ap` if it exists, falls back to `wlan0`. Operators who set up udev rules over SSH get a clean multi-radio start on first power-on; everyone else still gets the wlan0 bootstrap path.
- **S05 (recon)** can now run with monitor adapters in parallel without disturbing the management AP. The AP-on-its-own-radio architecture was specifically a recon-enablement move.
