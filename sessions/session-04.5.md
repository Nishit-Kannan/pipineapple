# Session 04.5 — Authentication + Management Access scoping

**Date:** 2026-06-04
**Phase:** A/B interlude — security insertion between adapter management (S04) and the recon scan table (S05)
**Goal:** Close two real security gaps before the offensive surface grows further. Add a login page so anyone on your home LAN can't just hit `pi-lab.local:5000` and start toggling monitor mode under root. Add a Management Access deny-list so future rogue-AP victims (Phase D, S11+) can't see the management UI at all.

---

## Checkpoint 1 — Concepts: session cookies, password hashing, route protection, deny-list

**Decided:**

- **Session cookies over basic auth or API tokens.** Real login UI, proper logout, password only on the wire during login. Flask provides this via `flask.session` out of the box. The real Pineapple uses the same pattern.
- **Password storage:** werkzeug's `generate_password_hash` / `check_password_hash` (scrypt under the hood, salted, deliberately slow). Hash + metadata stored as JSON at `$DATA_DIR/auth.json` with mode `0600`. File presence = "platform initialised."
- **First-run setup wizard.** No `auth.json` → redirect to `/setup` → prompt for password (twice, to catch typos) → write the file → auto-login → redirect to dashboard.
- **Route protection via `before_request` middleware**, not per-route `@login_required` decorators. Single point of enforcement; harder to forget. Whitelists `/setup`, `/login`, `/logout`, `/static`. Everything else needs a valid session.
- **Defense in depth via deny-list of source CIDRs.** Second filter that runs before auth, blocks specific subnets (e.g. the rogue AP's `10.0.0.0/24`) at the WSGI layer. Localhost (`127.0.0.0/8`) is always allowed regardless — emergency console fallback. Stored at `$DATA_DIR/access_control.json`.

---

## Checkpoint 2 — Auth service + first-run setup

**Built `app/services/auth.py`:** `is_configured()`, `set_password(pw)`, `verify(pw)`, plus session helpers `login(session)` / `logout(session)` / `is_logged_in(session)`. The set/verify operations are file-backed (read auth.json each call); the session helpers just mutate Flask's session dict.

**Built `app/routes/auth.py`:** `/setup`, `/login`, `/logout`, plus `POST /auth/change-password` (the Security tab uses this). Setup auto-logs in the user who set the password — no awkward "you just set the password but now log in again" step.

**Templates** `auth/setup.html` and `auth/login.html` are standalone (don't extend `base.html`) so the chrome doesn't load on unauthenticated pages. Same amber-on-dark Pineapple aesthetic, single centered card layout.

---

## Checkpoint 3 — `before_request` middleware

**Built `_install_auth_middleware(app)` in `app/__init__.py`:**

```
on every request:
    if request.remote_addr is in any deny CIDR (and not loopback):
        return 403
    if endpoint is in AUTH_EXEMPT_ENDPOINTS (auth.setup, auth.login, auth.logout, static):
        continue normally
    if platform not configured:
        redirect to /setup
    if not authenticated:
        redirect to /login
    continue
```

The two filters compose cleanly — deny-list runs first so a banned source IP never even sees the login form.

---

## Checkpoint 4 — Access control service + Security tab UI

**Built `app/services/access_control.py`:** module-level singleton, deny-list stored as `[ip_network, ...]` after parsing JSON. Re-loads from disk on every check if the file's mtime changed — that lets the UI write the file via `add_cidr` / `remove_cidr` and have the middleware see the new state on the very next request, no Flask restart needed.

**Settings → Security tab:**

- **Change password form** — three inputs (current / new / confirm) → `POST /auth/change-password`. Validates old password before accepting new.
- **Management access deny-list manager** — input field for `<cidr>`, "Add to deny list" button, table of current entries with Remove buttons per row. Empty-state placeholder when no CIDRs are configured.

**Title bar updated** — added a Sign out icon at the far right of the title bar (exit/log-out glyph). Single click → `GET /logout` → session cleared → redirect to `/login`.

**Settings tab switching** — JS in `settings.js` toggles which `.tab-panel` is visible based on the active `.tab` button. No URL hash routing for now, simple in-page state.

---

## Checkpoint 5 — Verification (sandbox + Pi)

All 17 sandbox checks pass:

- Fresh install (`auth.json` absent) → `GET /` → 302 to `/setup` ✓
- `POST /setup` → password file written + auto-login + redirect to `/` ✓
- After setup, `GET /` returns 200 ✓
- `GET /logout` → 302 to `/login` ✓
- Post-logout `GET /` → 302 to `/login` ✓
- Wrong password rejected with visible error ✓
- Correct password → 302 to `/` ✓
- Settings page has Security tab + change-password form + deny-list UI + sign-out icon ✓
- `POST /settings/access/deny` with `10.0.0.0/24` → 200, persisted to disk ✓
- `GET /` from `10.0.0.42` → **403 Forbidden** ✓
- `GET /` from `192.168.8.50` → 200 (not denied) ✓
- `GET /` from `127.0.0.1` → 200 (localhost always allowed) ✓
- `POST /auth/change-password` with correct old + matching new → 200 ✓

Pi deploy: pushed, pulled, restarted via `./run-as-root.sh`. First page load redirected to `/setup`. Set password. Verified login required after logout. Added `192.168.99.0/24` to deny-list as a no-op test (no real client on that subnet). Removed it via the table's Remove button.

---

## Checkpoint 6 — Learning Centre + roadmap updated

**Added "Auth & access control" topic section to the Learning Centre.** Eight commands covering: inspecting auth.json and access_control.json, emergency password reset via `rm auth.json`, manually generating a hash with `werkzeug.security`, verifying a hash, finding your own IP before configuring deny-list (don't lock yourself out), curling from a specific interface to test deny behavior.

**Roadmap updated** with the inserted Session 04.5 entry between S04 and S05. Session numbering for S05+ is unchanged — Session 04.5 is explicitly an inserted security session, not a renumber.

---

## Session-wide findings

- **Running as root makes auth non-negotiable** in a way it isn't for an unprivileged web app. Every operation is now a root operation; unauthenticated access means anyone with network reachability to the Pi can do anything we can. The S19 timeline for auth was too late.
- **The `before_request` middleware pattern is robust.** New routes added in any blueprint inherit auth and access-control checks automatically. Adding a Recon blueprint in S05 doesn't need to think about auth — it just works.
- **CIDR-based access control composes with auth defense-in-depth.** Login is "are you allowed in once you can reach me." Deny-list is "are you allowed to even see me." Both are cheap, both are valuable, the combination defends against different threats.
- **Localhost-always-allowed is the right escape hatch.** If you ever lock yourself out by mis-configuring the deny-list, SSH to the Pi and `curl http://localhost:5000/settings/access/deny/remove` to remove the offending CIDR. No need to restart Flask or edit files by hand.

---

## Parked for later

- **HTTPS via self-signed cert.** Currently passwords go over plain HTTP on the LAN. Fine for the lab; will be added in Session 19 alongside nginx + gunicorn.
- **Rate-limiting login attempts.** A determined attacker on your LAN could brute-force the password. Not implementing for the lab; revisit in S19.
- **Per-user accounts.** Single-user, single-machine for now. The auth service is single-password; a real multi-user system needs a user model and per-user sessions.
- **Bind-to-interface scoping.** The deny-list works at the WSGI layer. An even stronger alternative is binding Flask only to specific network interfaces. More invasive (requires restart on interface IP changes), but defense-deeper. May add as an option in S19.

---

## What's now possible

The platform has a real security perimeter:

- **First-run experience matches the real Pineapple** — setup wizard sets the password, login flow protects every subsequent visit.
- **Multi-layered defense** — login protects against any unauthenticated request; deny-list protects against entire subnets of attackers (rogue-AP clients) seeing the login form at all.
- **Self-recoverable** — even if you mess up the configuration, shell access to the Pi gets you out of any hole.

Now we can proceed to Session 05 (recon scan table) knowing that the powerful features we're about to add (`airodump-ng`, deauth from the UI, eventually `hostapd` and friends) are gated behind real auth.
