# PiPineapple — Hardware / integration test checklist

Everything below is built and stub-verified (162 automated checks green); it
can only be *confirmed* on the Pi with real radios + a real victim + (for
cracking) a real remote box. Run roughly top-to-bottom — earlier items unblock
later ones. Lab-authorised targets only.

Pre-flight every session: `sudo systemctl restart pipineapple` (pick up code),
`sudo journalctl -u pipineapple -f` in a side terminal, and a **hard refresh**
in the browser (Cmd/Ctrl-Shift-R — CSS/JS are cached).

Reliable victim for capture tests: a **laptop** (`sudo nmcli device wifi
connect <ssid> password <psk>`). iPhones (private-MAC) and WPA3/PMF networks
resist handshake capture by design — don't debug with them.

---

## 0. Crack targets + dispatch (S09) — set this up FIRST

The Pi never cracks locally; it scp's the `.22000` to a remote (your Mac or a
Jetson) and runs `hashcat -m 22000` there over key-based SSH. This unblocks the
S12 crack-confirm and is its own test.

**Prerequisites**
- A remote box with `hashcat` installed and a wordlist on disk.
- The remote reachable from the Pi over SSH.

**Steps**
1. Settings → Crack Targets → copy the platform's **public key** (or
   `GET /crack/public-key`). Append it to the remote's
   `~/.ssh/authorized_keys`.
2. Add a target: **name, host, user, port, wordlist_path** (Settings → Crack
   Targets form). No password — auth is the key from step 1.
3. Click **Test** on the target. Expect `last_test_ok = ✓` — it confirms SSH
   reachability (`accept-new` host-key TOFU) and that `hashcat` is on the
   remote's PATH. A red/failed test tells you which (SSH vs hashcat).
4. (Dispatch is tested in §2 once you have a capture.)

**Expected:** target shows ✓ in the dropdown; the Pi's `~/.ssh` state is
isolated under `$DATA_DIR/ssh/` (its own keypair + known_hosts).

---

## 1. Evil WPA capture (S12) — the foundation for §2 and §3

**Setup:** lab GL.iNet as **WPA2-PSK only, PMF/802.11w OFF**, known PSK. Put
that PSK in the remote wordlist (plus decoys). Power off / move away any other
copy of that SSID so the twin is the only one on air.

**Steps**
1. Recon → Start scan → click the lab AP row → **Clone to PineAP** (enabled
   only for WPA targets) → lands on the Evil WPA tab.
2. Settings tab → mode Active. Start (ethics: `pineap`).
3. From the **laptop**: `nmcli device wifi connect <ssid> password <real-psk>`.
4. Watch: `iw dev wlan-mon-5g info` shows monitor + the AP's channel + up;
   Evil WPA tab **Frames/EAPOL** counters climb; a partial appears in the
   table and on the **Handshakes** page tagged `source: Evil WPA`.

**Expected:** a partial captured. If EAPOL stays 0 → re-check the sniffer radio
is up on the right channel (the S12 self-heal should handle it) and that the
laptop actually associated (hostapd logs `associated`).

**Optional — evil-twin deauth:** tick "Also deauth the real AP" (needs the real
BSSID from the clone), confirm `wlan-mon-2g` lands on the target channel and
`tcpdump -i wlan-mon-2g 'wlan type mgt subtype deauth'` shows frames.

---

## 2. Crack-to-known-PSK confirmation (closes S12)

**Steps**
1. Handshakes page → your Evil WPA partial → **Crack** → pick the §0 target →
   Start. Watch the Crack jobs table: scp → remote hashcat → live progress.
2. Alternatively verify the crypto directly on the Pi:
   `python3 -c "from app.tools.wpa_crypto import verify_psk_against_line as v; print(v('<real-psk>', open('/var/lib/pipineapple/evil_wpa/<sess>/all.22000').read().strip()))"`
   → `True`; a wrong guess → `False`. Cross-check the PMK with
   `wpa_passphrase <ssid> <psk>`.

**Expected:** hashcat recovers the exact PSK you set → the whole
capture→crack chain is proven.

---

## 3. Captive portal (S12.5)

**Note:** today the portal is gated on capturing a handshake first (the
bait-switch fires on the first partial), so §1 must succeed. (If we add the
direct-open-portal mode, this prerequisite goes away.)

**Prerequisites:** Settings → Security → enable **captive-portal credential
capture** (type `phishing`); pick a verify mode (A default).

**Steps**
1. Recon → clone the lab AP → Evil WPA tab → tick **"launch captive-portal
   phishing"** → Start (Active).
2. Laptop joins (as §1) → first partial captured → bait-switch fires.
   `journalctl` should show `captive-portal bait-switch launched` +
   `hostapd flipped to OPEN clone`.
3. The AP is now **open**; the laptop (or a phone) rejoins password-free, its
   OS captive check pops the **firmware-update** page → enter the real PSK.
4. Captive Portal tab → the credential appears with **verified ✓** (mode A
   shows the victim "Update successful!" regardless).

**Expected:** open clone visible, portal page served, submitted PSK verified
against the captured handshake. If the open clone never comes up, the partial
wasn't captured (go back to §1).

---

## 4. Impersonation / Filtering / Clients (S13)

**Impersonation:** Settings pool has a few SSIDs → Impersonation tab → enable
rotation, dwell 15s, per-SSID BSSID → Save → Settings mode Active → Start.
Expect the "Now broadcasting" SSID + a phone's network name to **cycle every
dwell**; `journalctl` shows rotation (and the restart fallback if
`hostapd_cli reload` isn't honoured).

**Filtering:** Filtering tab → Client mode = deny, add your phone's MAC → Save
→ restart engine. Expect the phone rejected (hostapd ACL) while another device
joins. Flip to allow-list with one MAC → only it joins.
`cat /tmp/pipineapple-pineap-deny-mac` shows the file. SSID filter: allow only
one pool SSID → only it rotates/broadcasts.

**Clients + Kick:** with a device associated, Clients tab → **Kick** → it
drops (re-auth required). `hostapd_cli -i wlan-ap all_sta` cross-checks.

**Karma card:** Advanced + Open mode → the Impersonation tab's Karma card shows
probes seen/answered + unique clients/SSIDs ticking.

---

## 5. Campaigns (S14)

- **Recon:** Run tab → Reconnaissance → 120s window → Run. Status card ticks,
  auto-stops, Reports tab → open the HTML report → AP/client tables match.
- **Passive:** same, with a capture running in the window → report lists the
  in-window handshakes.
- **Active:** type `active`, optionally set a lab target BSSID for the deauth
  sweep → Run. `journalctl` shows the rogue + Karma (+ deauth) come up; recon
  is paused (expected). Report shows rogue clients / captures / creds.
- Confirm JSON + HTML download from the Reports table.

---

## 6. UI button pass

Hard-refresh, then across the app confirm the one colour-code: **blue** =
pressable, **grey** = disabled, **red** = action in progress, with a pressed
dip on every click. Recon Start/Stop is the reference:
idle (Start blue/Stop grey) → Start (Start red/Stop blue) → Stop (Start
grey/Stop red while stopping) → back to idle.

---

## Quick status

| Area | Built | Stub-verified | Hardware |
|------|-------|---------------|----------|
| S09 crack targets + dispatch | ✓ | ✓ | ✅ DONE (cracked real PSK 2026-06-11) |
| S12 Evil WPA capture | ✓ | ✓ | ✅ DONE (laptop) |
| S12 crack-to-known-PSK confirm | ✓ | ✓ | ✅ DONE (trav3llit3 recovered) |
| S12 evil-twin deauth | ✓ | ✓ | ✓ (worked in testing) |
| **S12.5 captive portal** | ✓ | ✓ | ☐ **← next** |
| S13 impersonation/filter/clients | ✓ | ✓ | ☐ |
| S14 campaigns | ✓ | ✓ | ☐ |
| UI button pass | ✓ | n/a | ☐ (eyeball) |

**Crack-path fixes (2026-06-11, see session-09 addendum):** Mac-over-SSH PATH,
hashcat m22000 cracked-line parser, per-job log at
`/tmp/pipineapple-crack-<id>.log` is the go-to diagnostic. Crack-job
Delete/Clear + the per-row eye icon (shows on-disk paths) are in.
