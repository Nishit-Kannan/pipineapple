/* PineAP page (Session 10).
 *
 * Mode radios + broadcast/capture toggles + SSID pool table.
 * Start goes through an ethics-confirm modal (type "pineap" — matches
 * the S06 deauth modal pattern).
 *
 * Bootstrap-or-listen at the BOTTOM of the IIFE — same TDZ-safe pattern
 * as handshakes.js. References to consts before the line they're
 * declared on throw ReferenceError under defer scripts when readyState
 * has already advanced past "loading".
 */

(function () {
  "use strict";
  console.log("[pineap.js] script loaded, document.readyState=", document.readyState);

  const $ = (id) => document.getElementById(id);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const escapeHtml = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  function fmtTs(u) {
    if (!u) return "—";
    return new Date(u * 1000).toLocaleString();
  }

  function fmtBytes(n) {
    if (n == null) return "—";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }

  // Button state: blue ready / grey disabled / red busy (action in effect).
  function setBtn(btn, mode) {
    if (!btn) return;
    btn.disabled = (mode === "disabled");
    btn.classList.toggle("is-busy", mode === "busy");
  }
  // Start/Stop pair given the engine's running flag:
  //   running     → Start red (in effect),  Stop blue
  //   not running → Start blue,             Stop grey
  function applyStartStop(startId, stopId, running) {
    setBtn($(startId), running ? "busy" : "ready");
    setBtn($(stopId), running ? "ready" : "disabled");
  }

  function showStatus(msg, kind = "info") {
    const el = $("pineap-status");
    if (!el) return;
    el.textContent = msg;
    el.hidden = !msg;
    el.className = "settings-status " + (kind === "fail" ? "fail" : kind === "ok" ? "ok" : "muted");
  }

  async function postJSON(url, body = {}) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return r.json();
  }

  // ---------- State / mode ----------
  async function reloadState() {
    try {
      const r = await fetch("/pineap/state");
      if (!r.ok) return;
      const st = await r.json();
      renderState(st);
    } catch (e) {
      console.error("[pineap] reloadState:", e);
    }
  }

  function renderState(st) {
    if ($("pa-running"))         $("pa-running").textContent       = st.running ? "running" : "stopped";
    if ($("pa-mode-disp"))       $("pa-mode-disp").textContent     = st.mode;
    if ($("pa-broadcast-disp"))  $("pa-broadcast-disp").textContent = st.broadcast_enabled ? "on" : "off";
    if ($("pa-capture-disp"))    $("pa-capture-disp").textContent   = st.capture_enabled   ? "on" : "off";
    // Re-sync the radios/checkboxes in case the change came from a
    // different browser tab or a server-side mutation.
    $$('input[name="pa-mode"]').forEach((r) => {
      r.checked = (r.value === st.mode);
    });
    if ($("pa-broadcast")) $("pa-broadcast").checked = !!st.broadcast_enabled;
    if ($("pa-capture"))   $("pa-capture").checked   = !!st.capture_enabled;
    // Lifecycle button colours follow the running flag.
    applyStartStop("pa-start", "pa-stop", !!st.running);
    if ($("ew-start")) applyStartStop("ew-start", "ew-stop", !!st.running);
  }

  // Tab switching — same pattern as settings.js. Each page with tabs
  // wires its own handlers because the activate function references
  // per-page consts.
  function activateTab(name) {
    document.querySelectorAll(".tab").forEach((b) => {
      if (b.dataset.tab === name) b.classList.add("active");
      else b.classList.remove("active");
    });
    document.querySelectorAll(".tab-panel").forEach((p) => {
      p.hidden = !p.id.endsWith(`-${name}`);
    });
  }

  function init() {
    if (!$("pa-pool-tbody")) return; // not on PineAP page

    // Tab buttons (added in S11 — Settings + Open SSID enabled,
    // others stay disabled until their sessions)
    document.querySelectorAll(".tab").forEach((btn) => {
      if (btn.disabled) return;
      btn.addEventListener("click", () => {
        const name = btn.dataset.tab;
        if (name) activateTab(name);
      });
    });

    // Mode radios
    $$('input[name="pa-mode"]').forEach((r) => {
      r.addEventListener("change", async () => {
        if (!r.checked) return;
        const res = await postJSON("/pineap/mode", { mode: r.value });
        showStatus(res.msg, res.ok ? "ok" : "fail");
        if (res.state) renderState(res.state);
      });
    });

    // Broadcast / capture toggles
    if ($("pa-broadcast")) {
      $("pa-broadcast").addEventListener("change", async (e) => {
        const res = await postJSON("/pineap/broadcast", { enabled: e.target.checked });
        showStatus(res.msg, res.ok ? "ok" : "fail");
        if (res.state) renderState(res.state);
      });
    }
    if ($("pa-capture")) {
      $("pa-capture").addEventListener("change", async (e) => {
        const res = await postJSON("/pineap/capture", { enabled: e.target.checked });
        showStatus(res.msg, res.ok ? "ok" : "fail");
        if (res.state) renderState(res.state);
      });
    }

    // Start (ethics-gated) + Stop
    if ($("pa-start")) $("pa-start").addEventListener("click", openEthicsModal);
    if ($("pa-stop"))  $("pa-stop").addEventListener("click", onStop);

    // Pool actions
    if ($("pa-add-btn"))        $("pa-add-btn").addEventListener("click", onAddSsid);
    if ($("pa-add-ssid"))       $("pa-add-ssid").addEventListener("keydown", (e) => {
      if (e.key === "Enter") onAddSsid();
    });
    if ($("pa-pool-refresh"))   $("pa-pool-refresh").addEventListener("click", reloadPool);
    if ($("pa-pool-clear"))     $("pa-pool-clear").addEventListener("click", () => onClearPool(false));
    if ($("pa-pool-clear-all")) $("pa-pool-clear-all").addEventListener("click", () => onClearPool(true));

    // Ethics modal wiring
    if ($("pa-ethics-cancel"))   $("pa-ethics-cancel").addEventListener("click", closeEthicsModal);
    if ($("pa-ethics-backdrop")) $("pa-ethics-backdrop").addEventListener("click", closeEthicsModal);
    if ($("pa-ethics-input")) {
      $("pa-ethics-input").addEventListener("input", (e) => {
        $("pa-ethics-confirm").disabled = (e.target.value.trim().toLowerCase() !== "pineap");
      });
    }
    if ($("pa-ethics-confirm")) $("pa-ethics-confirm").addEventListener("click", onStartConfirmed);

    reloadState();
    reloadPool();

    // ---------- Open SSID tab ----------
    if ($("open-clients-tbody")) {
      if ($("open-save")) $("open-save").addEventListener("click", onSaveOpenConfig);
      if ($("open-clients-refresh")) $("open-clients-refresh").addEventListener("click", reloadClients);
      if ($("open-clients-clear"))   $("open-clients-clear").addEventListener("click", onClearClients);
      // Active-only toggle just re-renders from cached client list.
      // We don't store the list locally so easiest is to refetch.
      if ($("open-clients-active-only")) {
        $("open-clients-active-only").addEventListener("change", reloadClients);
      }
      reloadClients();
      reloadProbes();

      // Live updates from client_recon (DHCP upsert + DNS query). Both
      // arrive frequently — cheap dedup: re-render on event but cap
      // the rate at ~once per 500ms via setTimeout coalescing.
      let pendingRefresh = null;
      const coalesce = () => {
        if (pendingRefresh) return;
        pendingRefresh = setTimeout(() => {
          pendingRefresh = null;
          reloadClients();
          reloadProbes();
        }, 500);
      };
      const tryWire = () => {
        const sock = window.pipineapple && window.pipineapple.socket;
        if (!sock) { setTimeout(tryWire, 200); return; }
        sock.on("client:upsert", coalesce);
        sock.on("client:query",  coalesce);
      };
      tryWire();
    }

    // ---------- Evil WPA tab (S12) ----------
    if ($("ew-partials-tbody")) {
      if ($("ew-save"))    $("ew-save").addEventListener("click", onSaveEvilWpaConfig);
      if ($("ew-start"))   $("ew-start").addEventListener("click", onEvilWpaStart);
      if ($("ew-stop"))    $("ew-stop").addEventListener("click", onStop);
      if ($("ew-refresh")) $("ew-refresh").addEventListener("click", () => {
        reloadEvilWpaState();
        reloadEvilWpaPartials();
      });

      reloadEvilWpaState();
      reloadEvilWpaPartials();

      // Live partial events from the EAPOL extractor — refresh stats +
      // table the moment a new partial lands.
      const tryWireEw = () => {
        const sock = window.pipineapple && window.pipineapple.socket;
        if (!sock) { setTimeout(tryWireEw, 200); return; }
        sock.on("evil_wpa:partial", () => {
          reloadEvilWpaState();
          reloadEvilWpaPartials();
        });
      };
      tryWireEw();

      // Light poll while the Evil WPA tab is visible so the frame /
      // EAPOL counters tick without spamming the backend when the
      // operator is on another tab.
      if (!_ewPollTimer) {
        _ewPollTimer = setInterval(() => {
          if (ewPanelVisible()) {
            reloadEvilWpaState();
            reloadEvilWpaPartials();
          }
        }, 4000);
      }
    }

    // ---------- Captive Portal tab (S12.5) ----------
    if ($("cp-creds-tbody")) {
      if ($("cp-refresh")) $("cp-refresh").addEventListener("click", () => {
        reloadCaptiveState();
        reloadCaptiveCreds();
      });
      if ($("cp-creds-clear")) $("cp-creds-clear").addEventListener("click", onClearCaptiveCreds);
      if ($("cp-direct-launch")) $("cp-direct-launch").addEventListener("click", onLaunchDirectPortal);
      reloadCaptiveState();
      reloadCaptiveCreds();

      const tryWireCp = () => {
        const sock = window.pipineapple && window.pipineapple.socket;
        if (!sock) { setTimeout(tryWireCp, 200); return; }
        sock.on("captive:credential", () => {
          reloadCaptiveCreds();
          reloadCaptiveState();
        });
        sock.on("captive:baitswitch", (p) => {
          showStatus(
            (p && p.ok ? "captive portal launched" : "bait-switch skipped")
              + (p && p.ssid ? ` (${p.ssid})` : ""),
            p && p.ok ? "ok" : "fail");
          reloadCaptiveState();
        });
      };
      tryWireCp();

      if (!_cpPollTimer) {
        _cpPollTimer = setInterval(() => {
          if (captivePanelVisible()) {
            reloadCaptiveState();
            reloadCaptiveCreds();
          }
        }, 5000);
      }
    }

    // ---------- Impersonation tab (S13) ----------
    if ($("imp-save")) {
      $("imp-save").addEventListener("click", onSaveImpersonation);
      if ($("ka-refresh")) $("ka-refresh").addEventListener("click", reloadKarma);
      reloadImpersonation();
      reloadKarma();
      const tryWireImp = () => {
        const sock = window.pipineapple && window.pipineapple.socket;
        if (!sock) { setTimeout(tryWireImp, 200); return; }
        sock.on("impersonate:rotate", (p) => {
          if ($("imp-current") && p && p.ssid) $("imp-current").textContent = p.ssid;
        });
      };
      tryWireImp();
      if (!_impPollTimer) {
        _impPollTimer = setInterval(() => {
          if (impPanelVisible()) { reloadImpersonation(); reloadKarma(); }
        }, 5000);
      }
    }

    // ---------- Filtering tab (S13) ----------
    if ($("cf-save")) {
      if ($("cf-client-add")) $("cf-client-add").addEventListener("click", () => {
        addFilterRow("cf-client-tbody", "mac", ($("cf-client-input").value || "").trim().toLowerCase());
        $("cf-client-input").value = "";
      });
      if ($("cf-ssid-add")) $("cf-ssid-add").addEventListener("click", () => {
        addFilterRow("cf-ssid-tbody", "ssid", ($("cf-ssid-input").value || "").trim());
        $("cf-ssid-input").value = "";
      });
      // Delegated remove
      document.querySelectorAll("#cf-client-tbody, #cf-ssid-tbody").forEach((tb) => {
        tb.addEventListener("click", (e) => {
          const b = e.target.closest("button");
          if (b) b.closest("tr").remove();
        });
      });
      $("cf-save").addEventListener("click", onSaveFilters);
    }

    // ---------- Clients tab (S13) ----------
    if ($("cl-tbody")) {
      if ($("cl-refresh")) $("cl-refresh").addEventListener("click", reloadKickClients);
      if ($("cl-active-only")) $("cl-active-only").addEventListener("change", reloadKickClients);
      reloadKickClients();
      if (!_clPollTimer) {
        _clPollTimer = setInterval(() => {
          if (clientsPanelVisible()) reloadKickClients();
        }, 5000);
      }
    }

    // Honour a #evil-wpa hash — set by the Recon "Clone to PineAP"
    // redirect so the operator lands directly on the Evil WPA tab.
    if (location.hash === "#evil-wpa") {
      activateTab("evil-wpa");
    }
  }

  // ---------- Open SSID handlers ----------
  async function onSaveOpenConfig() {
    const body = {
      primary_ssid:   ($("open-primary-ssid")?.value || "").trim(),
      channel:        parseInt($("open-channel")?.value || "6", 10),
      hw_mode:        $("open-hw-mode")?.value || "g",
      primary_hidden: !!$("open-primary-hidden")?.checked,
    };
    const res = await postJSON("/pineap/ap-config", body);
    showStatus(res.msg || (res.ok ? "saved" : "failed"), res.ok ? "ok" : "fail");
    if (res.state) renderState(res.state);
  }

  async function reloadClients() {
    const tbody = $("open-clients-tbody");
    if (!tbody) return;
    try {
      const r = await fetch("/pineap/clients");
      const data = await r.json();
      renderClients(data.clients || []);
    } catch (e) {
      console.error("[pineap] reloadClients:", e);
    }
  }

  function renderClients(clients) {
    const tbody = $("open-clients-tbody");
    if (!tbody) return;

    // "Active only" filter — hide leases inactive >10 min so the
    // table doesn't accumulate iOS privacy-MAC ghosts across testing.
    // The store keeps them; we just hide them in the view.
    const activeOnly = $("open-clients-active-only")?.checked;
    const ACTIVE_WINDOW_SEC = 10 * 60;
    const nowSec = Date.now() / 1000;
    const filtered = activeOnly
      ? clients.filter((c) => (nowSec - (c.last_seen || 0)) < ACTIVE_WINDOW_SEC)
      : clients;
    const hiddenCount = clients.length - filtered.length;

    if ($("open-client-count")) {
      $("open-client-count").textContent = hiddenCount > 0
        ? `${filtered.length} of ${clients.length} (${hiddenCount} stale)`
        : String(filtered.length);
    }
    if (!filtered.length) {
      const empty = clients.length === 0
        ? "No clients yet. Once a device associates and DHCPs, it'll appear here."
        : `All ${clients.length} client(s) are stale (no activity in 10 min). Uncheck 'Active only' to see them, or click 'Clear history'.`;
      tbody.innerHTML = `<tr><td colspan="7" class="muted">${escapeHtml(empty)}</td></tr>`;
      return;
    }
    tbody.innerHTML = filtered.map((c) => {
      const osLabel = c.os_guess
        ? `<span class="badge badge-ok">${escapeHtml(c.os_guess)}</span>`
        : `<span class="muted">unknown</span>`;
      // iOS privacy-MAC hint: locally-administered bit set on first
      // octet (`xx & 0x02 == 0x02`). Apple/Android randomize per-SSID
      // by default since iOS 14 / Android 10. Useful for the operator
      // to know "this isn't the device's real MAC".
      const firstOctet = parseInt((c.mac || "00").split(":")[0], 16);
      const isPrivacy = !isNaN(firstOctet) && (firstOctet & 0x02) === 0x02;
      const macDisplay = isPrivacy
        ? `<code>${escapeHtml(c.mac)}</code> <span class="muted" title="Locally-administered MAC — likely iOS/Android privacy randomization" style="font-size:10px;">(rnd)</span>`
        : `<code>${escapeHtml(c.mac)}</code>`;
      const queries = (c.recent_queries || [])
        .slice().reverse()
        .map((q) =>
          `<div style="font-size:11px;">
             <span class="muted">${escapeHtml(fmtTs(q.ts))}</span>
             <span class="muted">[${escapeHtml(q.type)}]</span>
             <code>${escapeHtml(q.name)}</code>
           </div>`)
        .join("");
      return `<tr class="open-client-row" data-mac="${escapeHtml(c.mac)}">
        <td>${macDisplay}</td>
        <td><code>${escapeHtml(c.ip || "—")}</code></td>
        <td>${escapeHtml(c.hostname || "—")}</td>
        <td>${osLabel}<div class="muted" style="font-size:10px;">${escapeHtml(c.dhcp_option55_fingerprint || "")}</div></td>
        <td>${c.query_count || 0}</td>
        <td class="muted" style="font-size:11px;">${escapeHtml(fmtTs(c.first_seen))}</td>
        <td class="muted" style="font-size:11px;">${escapeHtml(fmtTs(c.last_seen))}</td>
      </tr>
      <tr class="open-client-detail" data-for="${escapeHtml(c.mac)}" hidden>
        <td colspan="7" style="background:rgba(0,0,0,0.1); padding:8px;">
          <strong style="font-size:12px;">Recent DNS queries (${(c.recent_queries||[]).length}):</strong>
          ${queries || '<div class="muted">none yet</div>'}
        </td>
      </tr>`;
    }).join("");

    // Click row to expand detail
    tbody.querySelectorAll(".open-client-row").forEach((row) => {
      row.style.cursor = "pointer";
      row.addEventListener("click", () => {
        const mac = row.dataset.mac;
        const detail = tbody.querySelector(`.open-client-detail[data-for="${mac}"]`);
        if (detail) detail.hidden = !detail.hidden;
      });
    });
  }

  async function onClearClients() {
    if (!confirm("Wipe the persisted client history? (Doesn't kick anyone currently associated.)")) return;
    const res = await postJSON("/pineap/clients/clear");
    showStatus(res.msg, res.ok ? "ok" : "fail");
    reloadClients();
  }

  async function reloadProbes() {
    const tbody = $("open-probes-tbody");
    if (!tbody) return;
    try {
      const r = await fetch("/pineap/probes?limit=50");
      const data = await r.json();
      renderProbes(data.probes || []);
    } catch (e) {
      console.error("[pineap] reloadProbes:", e);
    }
  }

  function renderProbes(probes) {
    const tbody = $("open-probes-tbody");
    if (!tbody) return;
    if (!probes.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="muted">No probes yet.</td></tr>`;
      return;
    }
    tbody.innerHTML = probes.map((p) => {
      const label = p.label
        ? `<span class="badge badge-ok">${escapeHtml(p.label)}</span>`
        : `<span class="muted">404</span>`;
      const client = p.client_mac
        ? `<code>${escapeHtml(p.client_mac)}</code>`
        : `<span class="muted">${escapeHtml(p.client_ip || "?")}</span>`;
      return `<tr>
        <td class="muted">${escapeHtml(fmtTs(p.ts))}</td>
        <td>${client}</td>
        <td>${label}</td>
        <td><code>${escapeHtml(p.path)}</code></td>
        <td class="muted" style="max-width:300px; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(p.user_agent || "")}</td>
      </tr>`;
    }).join("");
  }

  // ---------- Evil WPA handlers (S12) ----------
  let _ewPollTimer = null;

  function ewPanelVisible() {
    const p = $("tab-evil-wpa");
    return !!(p && !p.hidden);
  }

  async function reloadEvilWpaState() {
    try {
      const r = await fetch("/pineap/evil-wpa/state");
      if (!r.ok) return;
      renderEvilWpaState(await r.json());
    } catch (e) {
      console.error("[pineap] reloadEvilWpaState:", e);
    }
  }

  function renderEvilWpaState(s) {
    if (!s) return;
    const pill = $("ew-running-pill");
    if (pill) {
      pill.textContent = s.running ? "running" : "stopped";
      pill.className = "badge " + (s.running ? "badge-good" : "");
    }
    if ($("ew-frames"))         $("ew-frames").textContent         = s.frames_seen || 0;
    if ($("ew-eapol"))          $("ew-eapol").textContent          = s.eapol_seen || 0;
    if ($("ew-partials-count")) $("ew-partials-count").textContent = s.partials_extracted || 0;
    if ($("ew-pcap-bytes"))     $("ew-pcap-bytes").textContent     = fmtBytes(s.pcap_bytes);
    if ($("ew-session"))        $("ew-session").textContent        = s.session_id || "—";
    if ($("ew-deauth-bursts")) {
      $("ew-deauth-bursts").textContent = s.deauth_enabled ? String(s.deauth_bursts || 0) : "—";
    }
    if ($("ew-deauth-target")) {
      $("ew-deauth-target").textContent = (s.deauth_enabled && s.deauth_bssid)
        ? `→ ${s.deauth_bssid}` : (s.deauth_enabled ? "" : "(off)");
    }
  }

  async function reloadEvilWpaPartials() {
    const tbody = $("ew-partials-tbody");
    if (!tbody) return;
    try {
      const r = await fetch("/pineap/evil-wpa/partials");
      const data = await r.json();
      renderEvilWpaPartials(data.partials || []);
    } catch (e) {
      console.error("[pineap] reloadEvilWpaPartials:", e);
    }
  }

  function renderEvilWpaPartials(partials) {
    const tbody = $("ew-partials-tbody");
    if (!tbody) return;
    if ($("ew-partials-total")) $("ew-partials-total").textContent = String(partials.length);
    if (!partials.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="muted">No partials harvested yet. Start the engine and have a device with the cloned SSID saved attempt to join.</td></tr>`;
      return;
    }
    // newest first
    const sorted = [...partials].sort((a, b) => (b.extracted_at || 0) - (a.extracted_at || 0));
    tbody.innerHTML = sorted.map((p) => {
      const line = p.hash_line || "";
      const shortLine = line.length > 48 ? line.slice(0, 48) + "…" : line;
      return `<tr>
        <td class="muted">${escapeHtml(fmtTs(p.extracted_at))}</td>
        <td><strong>${escapeHtml(p.essid || "—")}</strong></td>
        <td><code>${escapeHtml(p.ap_mac || "—")}</code></td>
        <td><code>${escapeHtml(p.sta_mac || "—")}</code></td>
        <td><code title="${escapeHtml(line)}">${escapeHtml(shortLine)}</code></td>
      </tr>`;
    }).join("");
  }

  async function onSaveEvilWpaConfig() {
    // Always stamp security_mode=wpa2 — this is what flips the engine
    // from the Open SSID / Karma path to the EAPOL-sniffer path at Start.
    const body = {
      primary_ssid:    ($("ew-primary-ssid")?.value || "").trim(),
      channel:         parseInt($("ew-channel")?.value || "6", 10),
      hw_mode:         $("ew-hw-mode")?.value || "g",
      security_mode:   "wpa2",
      // Only send the toggle when the checkbox is enabled (a real target
      // was cloned) — disabled means "not applicable", not "false".
      evil_wpa_deauth: ($("ew-deauth") && !$("ew-deauth").disabled)
                         ? !!$("ew-deauth").checked : undefined,
      auto_captive_portal: $("ew-auto-portal")
                         ? !!$("ew-auto-portal").checked : undefined,
    };
    const res = await postJSON("/pineap/ap-config", body);
    showStatus(res.msg || (res.ok ? "saved" : "failed"), res.ok ? "ok" : "fail");
    if (res.state) {
      renderState(res.state);
      if ($("ew-security")) $("ew-security").textContent = res.state.security_mode;
    }
    return !!res.ok;
  }

  async function onEvilWpaStart() {
    // 1. Persist the WPA config (sets security_mode=wpa2 + SSID/channel).
    // 2. Ensure we're in a broadcasting mode — Evil WPA only arms in
    //    active/advanced (passive keeps hostapd silent; off won't start).
    // 3. Gate behind the shared ethics modal, which POSTs /pineap/start —
    //    the same lifecycle entry point the Open SSID tab uses.
    const saved = await onSaveEvilWpaConfig();
    if (!saved) return;
    const curMode = $("pa-mode-disp")?.textContent || "";
    if (curMode === "off" || curMode === "passive") {
      const r = await postJSON("/pineap/mode", { mode: "active" });
      if (r.state) renderState(r.state);
    }
    openEthicsModal();
  }

  // ---------- Captive Portal handlers (S12.5) ----------
  let _cpPollTimer = null;

  function captivePanelVisible() {
    const p = $("tab-captive");
    return !!(p && !p.hidden);
  }

  async function reloadCaptiveState() {
    try {
      const r = await fetch("/pineap/captive-portal/state");
      if (!r.ok) return;
      renderCaptiveState(await r.json());
    } catch (e) {
      console.error("[pineap] reloadCaptiveState:", e);
    }
  }

  function renderCaptiveState(s) {
    if (!s) return;
    const pill = $("cp-status-pill");
    const live = !!s.portal_active;
    if (pill) {
      pill.textContent = !s.enabled ? "disabled" : (live ? "portal live" : "armed/idle");
      pill.className = "badge " + (live ? "badge-good" : (s.enabled ? "" : "badge-warn"));
    }
    if ($("cp-enabled"))     $("cp-enabled").textContent     = s.enabled ? "on" : "off (Settings → Security)";
    if ($("cp-verify-mode")) $("cp-verify-mode").textContent = s.verify_mode || "—";
    if ($("cp-active"))      $("cp-active").textContent      = live ? "yes" : "no";
    if ($("cp-armed-ssid"))  $("cp-armed-ssid").textContent  = s.armed_ssid || "—";
    if ($("cp-custom-template")) $("cp-custom-template").textContent = s.has_custom_template ? "yes" : "built-in";
    if ($("cp-attempts"))    $("cp-attempts").textContent    = s.attempts || 0;
    if ($("cp-verified"))    $("cp-verified").textContent    = s.verified_count || 0;
    if ($("cp-disabled-note")) $("cp-disabled-note").hidden  = !!s.enabled;
  }

  async function reloadCaptiveCreds() {
    const tbody = $("cp-creds-tbody");
    if (!tbody) return;
    try {
      const r = await fetch("/pineap/captive-portal/credentials?limit=100");
      const data = await r.json();
      renderCaptiveCreds(data.credentials || []);
    } catch (e) {
      console.error("[pineap] reloadCaptiveCreds:", e);
    }
  }

  function renderCaptiveCreds(creds) {
    const tbody = $("cp-creds-tbody");
    if (!tbody) return;
    if ($("cp-creds-count")) $("cp-creds-count").textContent = String(creds.length);
    if (!creds.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="muted">No credentials harvested yet.</td></tr>`;
      return;
    }
    tbody.innerHTML = creds.map((c) => {
      let v;
      if (c.verified === true)       v = `<span class="badge badge-ok">verified ✓</span>`;
      else if (c.verified === false) v = `<span class="badge badge-warn">wrong</span>`;
      else                            v = `<span class="muted">unknown</span>`;
      const client = c.client_mac
        ? `<code>${escapeHtml(c.client_mac)}</code>`
        : `<span class="muted">${escapeHtml(c.client_ip || "?")}</span>`;
      return `<tr>
        <td class="muted">${escapeHtml(fmtTs(c.ts))}</td>
        <td>${client}</td>
        <td>${escapeHtml(c.ssid || "—")}</td>
        <td><code>${escapeHtml(c.psk || "")}</code></td>
        <td>${v}</td>
      </tr>`;
    }).join("");
  }

  async function onClearCaptiveCreds() {
    if (!confirm("Clear all harvested credential records?")) return;
    const res = await postJSON("/pineap/captive-portal/clear");
    showStatus(res.msg || "cleared", res.ok ? "ok" : "fail");
    reloadCaptiveCreds();
    reloadCaptiveState();
  }

  async function onLaunchDirectPortal() {
    const ssid = ($("cp-direct-ssid")?.value || "").trim();
    if (!confirm("Stand up an OPEN evil-twin + captive portal now? "
        + "(Lab use only — submitted passwords won't be verified without a handshake.)")) return;
    const btn = $("cp-direct-launch");
    setBtn(btn, "busy");
    const res = await postJSON("/pineap/captive-portal/launch-direct", ssid ? { ssid } : {});
    const summary = (res.messages || []).join("; ") || (res.ok ? "portal up" : "failed");
    showStatus(summary, res.ok ? "ok" : "fail");
    setBtn(btn, "ready");
    reloadCaptiveState();
    if (res.state) renderState(res.state);
  }

  // ---------- Impersonation tab (S13) ----------
  let _impPollTimer = null;
  const impPanelVisible = () => { const p = $("tab-impersonation"); return !!(p && !p.hidden); };

  async function reloadImpersonation() {
    try {
      const st = await (await fetch("/pineap/state")).json();
      if ($("imp-current")) $("imp-current").textContent = st.impersonate_current_ssid || "—";
      const pill = $("imp-running-pill");
      if (pill) {
        pill.textContent = st.impersonate_running ? "rotating" : (st.impersonate_enabled ? "armed" : "off");
        pill.className = "badge " + (st.impersonate_running ? "badge-good" : "");
      }
    } catch (e) { /* ignore */ }
  }

  async function onSaveImpersonation() {
    const body = {
      enabled: !!$("imp-enabled")?.checked,
      dwell_secs: parseInt($("imp-dwell")?.value || "20", 10),
      bssid_strategy: $("imp-bssid-strategy")?.value || "per-ssid",
    };
    const res = await postJSON("/pineap/impersonation", body);
    showStatus(res.msg || (res.ok ? "saved" : "failed"), res.ok ? "ok" : "fail");
  }

  async function reloadKarma() {
    try {
      const k = await (await fetch("/pineap/karma/stats")).json();
      if ($("ka-seen"))    $("ka-seen").textContent    = k.probes_seen ?? k.seen ?? 0;
      if ($("ka-replied")) $("ka-replied").textContent = k.probes_replied ?? k.replied ?? 0;
      if ($("ka-clients")) $("ka-clients").textContent = k.unique_clients ?? k.clients ?? 0;
      if ($("ka-ssids"))   $("ka-ssids").textContent   = k.unique_ssids ?? k.ssids ?? 0;
    } catch (e) { /* karma not running — leave zeros */ }
  }

  // ---------- Filtering tab (S13) ----------
  function addFilterRow(tbodyId, kind, value) {
    if (!value) return;
    const tbody = $(tbodyId);
    if (!tbody) return;
    const attr = kind === "mac" ? "data-mac" : "data-ssid";
    // de-dup
    if (tbody.querySelector(`tr[${attr}="${CSS.escape(value)}"]`)) return;
    const cell = kind === "mac" ? `<code>${escapeHtml(value)}</code>` : `<strong>${escapeHtml(value)}</strong>`;
    const tr = document.createElement("tr");
    tr.setAttribute(attr, value);
    tr.innerHTML = `<td>${cell}</td><td style="text-align:right;">
      <button class="actbtn actbtn-muted" style="font-size:11px;">×</button></td>`;
    tbody.appendChild(tr);
  }

  async function onSaveFilters() {
    const macs = Array.from(document.querySelectorAll("#cf-client-tbody tr[data-mac]"))
      .map((tr) => tr.getAttribute("data-mac"));
    const ssids = Array.from(document.querySelectorAll("#cf-ssid-tbody tr[data-ssid]"))
      .map((tr) => tr.getAttribute("data-ssid"));
    const body = {
      client_mode: $("cf-client-mode")?.value || "off",
      client_macs: macs,
      ssid_mode: $("cf-ssid-mode")?.value || "off",
      ssid_ssids: ssids,
    };
    const res = await postJSON("/pineap/filters", body);
    const el = $("cf-save-msg");
    if (el) { el.textContent = res.msg || (res.ok ? "saved" : "failed"); el.classList.toggle("fail", !res.ok); }
    showStatus(res.msg || "filters saved", res.ok ? "ok" : "fail");
  }

  // ---------- Clients tab (S13) ----------
  let _clPollTimer = null;
  const clientsPanelVisible = () => { const p = $("tab-clients"); return !!(p && !p.hidden); };

  async function reloadKickClients() {
    const tbody = $("cl-tbody");
    if (!tbody) return;
    try {
      const data = await (await fetch("/pineap/clients")).json();
      renderKickClients(data.clients || []);
    } catch (e) { console.error("[pineap] reloadKickClients:", e); }
  }

  function renderKickClients(clients) {
    const tbody = $("cl-tbody");
    if (!tbody) return;
    const activeOnly = $("cl-active-only")?.checked;
    const nowSec = Date.now() / 1000;
    const filtered = activeOnly
      ? clients.filter((c) => (nowSec - (c.last_seen || 0)) < 600)
      : clients;
    if ($("cl-count")) $("cl-count").textContent = String(filtered.length);
    if (!filtered.length) {
      tbody.innerHTML = `<tr><td colspan="6" class="muted">No clients${activeOnly && clients.length ? " active in the last 10 min" : ""} yet.</td></tr>`;
      return;
    }
    tbody.innerHTML = filtered.map((c) => `<tr>
      <td><code>${escapeHtml(c.mac)}</code></td>
      <td><code>${escapeHtml(c.ip || "—")}</code></td>
      <td>${escapeHtml(c.hostname || "—")}</td>
      <td>${c.os_guess ? `<span class="badge badge-ok">${escapeHtml(c.os_guess)}</span>` : '<span class="muted">?</span>'}</td>
      <td class="muted" style="font-size:11px;">${escapeHtml(fmtTs(c.last_seen))}</td>
      <td style="text-align:right;"><button class="actbtn actbtn-muted cl-kick" data-mac="${escapeHtml(c.mac)}" style="font-size:11px;">Kick</button></td>
    </tr>`).join("");
    tbody.querySelectorAll(".cl-kick").forEach((b) => {
      b.addEventListener("click", async () => {
        if (!confirm(`Deauthenticate ${b.dataset.mac} off the rogue AP?`)) return;
        b.disabled = true;
        const res = await postJSON(`/pineap/clients/${encodeURIComponent(b.dataset.mac)}/kick`, { method: "deauth" });
        showStatus(res.msg || (res.ok ? "kicked" : "kick failed"), res.ok ? "ok" : "fail");
        setTimeout(reloadKickClients, 1000);
      });
    });
  }

  // ---------- Lifecycle ----------
  function openEthicsModal() {
    const st = {
      mode:  $("pa-mode-disp")?.textContent || "?",
      iface: document.querySelector('code') ? "wlan-ap" : "?",
    };
    // Pull iface from the statcard sub-text (rendered server-side)
    const ifaceCode = document.querySelector('.statcard-sub code');
    if (ifaceCode) st.iface = ifaceCode.textContent;

    $("pa-ethics-mode").textContent  = st.mode;
    $("pa-ethics-iface").textContent = st.iface;
    $("pa-ethics-input").value = "";
    $("pa-ethics-confirm").disabled = true;
    $("pa-ethics-modal").hidden = false;
    setTimeout(() => $("pa-ethics-input").focus(), 50);
  }

  function closeEthicsModal() {
    $("pa-ethics-modal").hidden = true;
  }

  async function onStartConfirmed() {
    closeEthicsModal();
    showStatus("starting…");
    setBtn($("pa-start"), "busy"); setBtn($("ew-start"), "busy");
    const res = await postJSON("/pineap/start");
    const summary = (res.messages || []).join("; ") || (res.ok ? "started" : "failed");
    showStatus(summary, res.ok ? "ok" : "fail");
    if (res.state) renderState(res.state);
    if (ewPanelVisible()) { reloadEvilWpaState(); reloadEvilWpaPartials(); }
  }

  async function onStop() {
    showStatus("stopping…");
    setBtn($("pa-stop"), "busy"); setBtn($("ew-stop"), "busy");
    const res = await postJSON("/pineap/stop");
    showStatus((res.messages || []).join("; ") || "stopped", res.ok ? "ok" : "fail");
    if (res.state) renderState(res.state);
    if (ewPanelVisible()) { reloadEvilWpaState(); reloadEvilWpaPartials(); }
  }

  // ---------- Pool ----------
  async function reloadPool() {
    try {
      const r = await fetch("/pineap/pool");
      const data = await r.json();
      renderPool(data.ssids || []);
    } catch (e) {
      console.error("[pineap] reloadPool:", e);
    }
  }

  function renderPool(entries) {
    const tbody = $("pa-pool-tbody");
    if (!tbody) return;
    // Stat-card counts
    if ($("pa-pool-count"))  $("pa-pool-count").textContent  = String(entries.length);
    if ($("pa-pinned-count")) $("pa-pinned-count").textContent = String(entries.filter(e => e.pinned).length);
    if ($("pa-hidden-count")) $("pa-hidden-count").textContent = String(entries.filter(e => e.hidden).length);

    if (!entries.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="muted" style="padding:12px;">
        Pool is empty. Add an SSID above, or run a recon scan to auto-populate.
      </td></tr>`;
      return;
    }

    tbody.innerHTML = entries.map((e) => {
      const sourceBadge = {
        recon:  '<span class="badge badge-muted">recon</span>',
        probe:  '<span class="badge badge-warn">probe</span>',
        manual: '<span class="badge badge-ok">manual</span>',
        import: '<span class="badge badge-muted">import</span>',
      }[e.source] || `<span class="badge badge-muted">${escapeHtml(e.source)}</span>`;

      return `<tr data-ssid="${escapeHtml(e.ssid)}" ${e.hidden ? 'style="opacity:.55;"' : ''}>
        <td><strong>${escapeHtml(e.ssid)}</strong></td>
        <td>${sourceBadge}</td>
        <td class="muted" style="font-size:11px;">${escapeHtml(fmtTs(e.first_seen))}</td>
        <td class="muted" style="font-size:11px;">${escapeHtml(fmtTs(e.last_seen))}</td>
        <td>${e.observed_count || 0}</td>
        <td>
          <input type="checkbox" class="pa-pin-cb" data-ssid="${escapeHtml(e.ssid)}"
                 ${e.pinned ? "checked" : ""}
                 title="Pinned entries survive the 'Clear (unpinned)' action">
        </td>
        <td>
          <input type="checkbox" class="pa-hide-cb" data-ssid="${escapeHtml(e.ssid)}"
                 ${e.hidden ? "checked" : ""}
                 title="Hidden entries stay in the pool but are excluded from broadcast">
        </td>
        <td>
          <button class="actbtn actbtn-muted pa-remove" data-ssid="${escapeHtml(e.ssid)}"
                  style="font-size:11px;">×</button>
        </td>
      </tr>`;
    }).join("");

    // Bind row controls
    tbody.querySelectorAll(".pa-pin-cb").forEach((cb) => {
      cb.addEventListener("change", async (ev) => {
        const res = await postJSON(`/pineap/pool/${encodeURIComponent(cb.dataset.ssid)}/pin`,
                                   { pinned: ev.target.checked });
        showStatus(res.msg, res.ok ? "ok" : "fail");
        reloadPool();
      });
    });
    tbody.querySelectorAll(".pa-hide-cb").forEach((cb) => {
      cb.addEventListener("change", async (ev) => {
        const res = await postJSON(`/pineap/pool/${encodeURIComponent(cb.dataset.ssid)}/hide`,
                                   { hidden: ev.target.checked });
        showStatus(res.msg, res.ok ? "ok" : "fail");
        reloadPool();
      });
    });
    tbody.querySelectorAll(".pa-remove").forEach((b) => {
      b.addEventListener("click", async () => {
        if (!confirm(`Remove '${b.dataset.ssid}' from the pool?`)) return;
        const r = await fetch(`/pineap/pool/${encodeURIComponent(b.dataset.ssid)}`,
                              { method: "DELETE" });
        const res = await r.json();
        showStatus(res.msg, res.ok ? "ok" : "fail");
        reloadPool();
      });
    });
  }

  async function onAddSsid() {
    const ssid = ($("pa-add-ssid")?.value || "").trim();
    const pin = !!($("pa-add-pin")?.checked);
    if (!ssid) {
      showStatus("type an SSID first", "fail");
      return;
    }
    const res = await postJSON("/pineap/pool", { ssid, pin });
    showStatus(res.msg, res.ok ? "ok" : "fail");
    if (res.ok) {
      $("pa-add-ssid").value = "";
      $("pa-add-pin").checked = false;
      reloadPool();
    }
  }

  async function onClearPool(includePinned) {
    const verb = includePinned ? "everything (including pinned)" : "unpinned entries";
    if (!confirm(`Clear ${verb} from the pool?`)) return;
    const res = await postJSON("/pineap/pool/clear", { include_pinned: includePinned });
    showStatus(res.msg, res.ok ? "ok" : "fail");
    reloadPool();
  }

  // ---- Bootstrap at bottom ----
  function bootstrap() {
    if (!$("pa-pool-tbody")) {
      console.log("[pineap.js] not on PineAP page, skipping init");
      return;
    }
    init();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();
  }
})();
