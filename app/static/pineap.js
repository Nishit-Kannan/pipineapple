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
    const res = await postJSON("/pineap/start");
    const summary = (res.messages || []).join("; ") || (res.ok ? "started" : "failed");
    showStatus(summary, res.ok ? "ok" : "fail");
    if (res.state) renderState(res.state);
    if (ewPanelVisible()) { reloadEvilWpaState(); reloadEvilWpaPartials(); }
  }

  async function onStop() {
    showStatus("stopping…");
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
