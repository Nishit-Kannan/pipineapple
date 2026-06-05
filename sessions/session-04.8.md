# Session 04.8 — Smoother setup flow

**Date:** 2026-06-04
**Phase:** A/B interlude — UX cleanup after multi-radio (S04.7), before internet sharing (S04.9)
**Goal:** Cut the friction out of getting from "Pi just booted on fresh SD card" to "Pi is up, my Mac/phone is on the mgmt AP, I'm logged in, ready to use the platform." The S04.7 setup wizard worked but demanded too much: change AP creds *and* set login password *and* reboot for udev. Aim is roughly half the clicks and zero reboots.

---

## Checkpoint 1 — Concepts

**Decided:**

- **Setup wizard sets login password only.** The bootstrap AP (`PiPineapple-Setup` / `pineapple-setup`) keeps broadcasting after setup. The operator changes AP credentials later, ideally after configuring upstream Wi-Fi so they have a fallback path when the AP restart drops them. Trying to do both in the wizard was always going to mean someone gets kicked off mid-setup.
- **No reboot to apply udev sticky names.** `udevadm trigger` doesn't rename existing interfaces, but `ip link set <old> name <new>` does. Apply the rename at runtime when the role is assigned, then write the udev rules file so it survives reboots. Operator sees the new name in the adapters table immediately.
- **Pre-configure adapters by MAC in the udev rules file.** For known hardware (the operator's three Alfas + one Realtek), ship a `99-pipineapple-adapters.rules` file with the MACs already mapped to roles. Operator who buys the recommended kit skips role-assignment entirely; everyone else falls back to the UI assignment flow.

---

## Checkpoint 2 — Service changes

**Edited `app/services/networking.py`:**

- `reconfigure_and_restart_ap(ssid, pw, channel)` — single atomic op replacing the old two-step "save then enable" dance for changing AP creds. Stops the current AP, saves new creds, starts new AP. Used by both the setup wizard and the Settings AP Apply button.
- `is_running_bootstrap()` predicate — true iff the active AP is the bootstrap one (presence of `bootstrap: true` flag in state). The setup wizard renders a warning when this is true: "you're on the bootstrap AP; changing credentials here will drop your connection."
- Bootstrap state writes `bootstrap: true` flag so we can tell apart "operator never customised AP creds" from "operator set creds that happen to equal the bootstrap defaults." `reconfigure_and_restart_ap` removes the flag.

**Edited `app/services/adapters.py`:**

- `apply_udev_rules()` now does two things: write the persistence file AND run `ip link set <old> name <new>` for every assignment whose current iface name differs from its target. Both surfaced in the per-step message log.
- Added `set_role` "uniqueness enforcement" — assigning a role that's already held by a different MAC clears it from the previous holder. Avoids two adapters claiming `wlan-mon-2g`.

**Edited `app/routes/auth.py`:**

- `/setup` route — POST handler only sets the login password and logs the user in. AP credential change moved to Settings → Networking. Template `auth/setup.html` updated to show the bootstrap AP creds in a "you're connected to this" callout, with a link to the Settings page for changing them later.

---

## Checkpoint 3 — udev pre-config for known hardware

**Manual setup on the Pi** (one-time, over SSH the first time the operator's hardware shows up):

```
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="90:de:80:d8:04:26", NAME="wlan-mgmt-ap"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="00:c0:ca:b9:65:42", NAME="wlan-mon-2g"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="00:c0:ca:b9:65:44", NAME="wlan-mon-5g"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="00:c0:ca:b9:65:4a", NAME="wlan-ap"
```

Saved at `/etc/udev/rules.d/99-pipineapple-adapters.rules`. On next reboot (or `udevadm trigger`), the four adapters come up with their canonical names. The Settings UI shows them with their roles pre-filled — no clicking through "assign role, apply udev" needed.

For operators with different hardware, the UI flow still works: detect adapter → assign role → apply udev → live rename. The pre-config is an optimisation for the known kit, not a requirement.

---

## Checkpoint 4 — Verification on Pi

- Wiped `auth.json` and `networking.json` to simulate fresh install.
- Reboot → bootstrap AP comes up on `wlan-mgmt-ap` (since udev names are pre-configured; otherwise wlan0).
- Connect from Mac to `PiPineapple-Setup` with `pineapple-setup`.
- Open `http://10.42.0.1:5000`. Setup wizard appears.
- Set login password (one form, two fields). Submit. Redirect to dashboard, logged in.
- Adapters table on Settings page shows the four pre-named adapters with their roles already filled in.
- AP credentials still show as bootstrap defaults. Changed to operator-preferred SSID+password via Settings → Networking → Apply. AP restarted with new creds, reconnected from phone with new creds.
- No reboot at any point in the flow.

---

## Session-wide findings

- **The "runtime rename" trick removes the worst step in the setup loop.** Previously: assign role → apply udev → reboot Pi → wait → reconnect → continue. Now: assign role → apply udev → keep working. The `ip link set name` call is non-obvious if you don't know it exists; documenting it in the Learning Centre is high-value.
- **Splitting the wizard saved more than clicks.** Doing AP cred change in the wizard means the operator changes the network they're currently *on*. Even with auto-reconnect, the AP restart causes a 5-10s disconnect, the wizard form gets POSTed mid-cutover, the response sometimes never reaches the browser, the operator is left wondering if it worked. Moving cred change to Settings (after the operator has wlan0 client mode configured as a fallback path) makes the workflow recoverable.
- **`bootstrap: true` as a state flag is cleaner than inferring from "creds equal the defaults".** Defaults can change; flag is explicit. Removed in `reconfigure_and_restart_ap` because after that, the AP is no longer bootstrap-ish regardless of what creds the operator chose.

---

## Parked for later

- **Auto-generate a bootstrap AP password** per-Pi from a hash of the MAC + serial, instead of the static `pineapple-setup`. Better security-of-defaults but breaks the "join `PiPineapple-Setup` with `pineapple-setup`" muscle memory. Defer.
- **Detect "you're connected via the AP you're about to restart" and warn explicitly.** Already implied by `is_running_bootstrap`, but a more general check (any AP you're connected to) would be useful. Defer until I notice an actual footgun.
- **Bundled udev rules.** Ship `99-pipineapple-adapters.rules` as a template that operators can copy in via the Settings page or scp. Today they paste the MACs manually.

---

## What's now possible

- **Setup-from-zero in under two minutes** with known hardware: connect to bootstrap AP, set login password, done. Operator hits the dashboard fully authenticated, AP and adapter names all correctly configured, no SSH or reboot needed.
- **Hot adapter swaps** — plug in a new Alfa, assign a role from the UI, apply udev. Interface gets its canonical name immediately. The platform sees the new monitor adapter in `list_adapters()` on the next refresh.
- **Setup wizard no longer drops your connection** — the AP it's running on stays unchanged through the wizard. Defer AP cred changes until you've got a fallback path.
