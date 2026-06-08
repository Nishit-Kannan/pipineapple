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

  function init() {
    if (!$("pa-pool-tbody")) return; // not on PineAP page

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
    if ($("open-client-count")) $("open-client-count").textContent = String(clients.length);
    if (!clients.length) {
      tbody.innerHTML = `<tr><td colspan="7" class="muted">
        No clients yet. Once a device associates and DHCPs, it'll appear here.
      </td></tr>`;
      return;
    }
    tbody.innerHTML = clients.map((c) => {
      const osLabel = c.os_guess
        ? `<span class="badge badge-ok">${escapeHtml(c.os_guess)}</span>`
        : `<span class="muted">unknown</span>`;
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
        <td><code>${escapeHtml(c.mac)}</code></td>
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
  }

  async function onStop() {
    showStatus("stopping…");
    const res = await postJSON("/pineap/stop");
    showStatus((res.messages || []).join("; ") || "stopped", res.ok ? "ok" : "fail");
    if (res.state) renderState(res.state);
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
