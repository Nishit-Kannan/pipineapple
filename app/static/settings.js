// Settings page — adapter management interactions.
//
// All state mutations go through the JSON API at /settings/adapters/*.
// Each action surfaces in the Command Stream (via the backend's run()
// wrapper) and triggers a notification on completion.

(function () {
  "use strict";

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  function showStatus(msg, kind = "info") {
    const el = $("#settings-status");
    if (!el) return;
    el.hidden = false;
    el.textContent = msg;
    el.classList.remove("ok", "fail");
    if (kind === "ok") el.classList.add("ok");
    if (kind === "fail") el.classList.add("fail");
  }

  async function postJSON(url, body = {}) {
    const res = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    return res.json();
  }

  async function refreshAdapters() {
    try {
      const res = await fetch("/settings/adapters");
      const data = await res.json();
      renderAdapters(data.adapters || []);
    } catch (e) {
      console.warn("refreshAdapters failed", e);
    }
  }

  function renderAdapters(adapters) {
    const tbody = document.querySelector("#adapter-table tbody");
    if (!tbody) return;

    tbody.innerHTML = adapters.map(a => {
      let modeBadge;
      if (a.mode === "monitor")      modeBadge = '<span class="badge badge-warn">monitor</span>';
      else if (a.mode === "AP")      modeBadge = '<span class="badge badge-warn">ap</span>';
      else if (a.mode === "managed") modeBadge = '<span class="badge badge-muted">managed</span>';
      else                           modeBadge = `<span class="badge badge-muted">${escapeHtml(a.mode) || "—"}</span>`;

      const channel = a.channel
        ? `${a.channel} <span class="muted">(${a.frequency_mhz} MHz)</span>`
        : "—";

      const roleOpts = [
        ["none",         "— none —"],
        ["wlan-mgmt-ap", "wlan-mgmt-ap"],
        ["wlan-mon-2g",  "wlan-mon-2g"],
        ["wlan-mon-5g",  "wlan-mon-5g"],
        ["wlan-ap",      "wlan-ap"],
      ].map(([v, label]) => {
        const sel = v === a.role ? "selected" : "";
        return `<option value="${v}" ${sel}>${escapeHtml(label)}</option>`;
      }).join("");

      return `<tr data-mac="${escapeHtml(a.mac)}" data-iface="${escapeHtml(a.name)}">
        <td><code>${escapeHtml(a.name)}</code></td>
        <td><code>${escapeHtml(a.mac) || "—"}</code></td>
        <td><code>${escapeHtml(a.driver) || "—"}</code></td>
        <td>${modeBadge}</td>
        <td>${channel}</td>
        <td>
          <select class="role-select" data-mac="${escapeHtml(a.mac)}">${roleOpts}</select>
        </td>
        <td class="action-cell">
          <button class="actbtn" data-action="monitor" data-iface="${escapeHtml(a.name)}">Monitor</button>
          <button class="actbtn" data-action="managed" data-iface="${escapeHtml(a.name)}">Managed</button>
          <button class="actbtn actbtn-muted" data-action="down" data-iface="${escapeHtml(a.name)}">Down</button>
        </td>
      </tr>`;
    }).join("");

    // Update header stat cards
    const assignedCount = adapters.filter(a => a.is_offensive).length;
    const monitorCount = adapters.filter(a => a.mode === "monitor").length;
    const rolesField = $('[data-field="roles_count"]');
    if (rolesField) rolesField.textContent = `${assignedCount} / 3`;
    const monField = $('[data-field="monitor_count"]');
    if (monField) monField.textContent = String(monitorCount);
  }

  function renderNetworkingState(state) {
    if (!state) return;
    const modeEl = document.querySelector('[data-field="wlan0-mode"]');
    if (modeEl) modeEl.textContent = state.wlan0_mode || "idle";
    const apStatusEl = document.querySelector('[data-field="mgmt-ap-status"]');
    if (apStatusEl) apStatusEl.textContent = state.mgmt_ap_active ? "active" : "inactive";
    const apIfaceEl = document.querySelector('[data-field="mgmt-ap-iface"]');
    if (apIfaceEl) apIfaceEl.textContent = (state.mgmt_ap && state.mgmt_ap.interface) || "—";
    const apSsidEls = document.querySelectorAll('[data-field="mgmt-ap-ssid"], [data-field="mgmt-ap-ssid-2"]');
    apSsidEls.forEach(el => el.textContent = (state.mgmt_ap && state.mgmt_ap.ssid) || "—");
    const savedCountEl = document.querySelector('[data-field="saved-count"]');
    if (savedCountEl) savedCountEl.textContent = String((state.saved_wifi || []).length);

    // Rebuild saved-networks table
    const tbody = document.querySelector("#saved-wifi-table tbody");
    if (tbody && state.saved_wifi) {
      if (state.saved_wifi.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="muted">No saved networks. Scan + connect below.</td></tr>';
      } else {
        tbody.innerHTML = state.saved_wifi.map(w => `
          <tr data-name="${escapeHtml(w.name)}">
            <td><code>${escapeHtml(w.ssid)}</code></td>
            <td>${w.active ? '<span class="badge badge-good">connected</span>' : '<span class="badge badge-muted">—</span>'}</td>
            <td>${w.autoconnect ? "yes" : "no"}</td>
            <td class="action-cell">
              <button class="actbtn wifi-connect" data-ssid="${escapeHtml(w.ssid)}">Connect</button>
              <button class="actbtn actbtn-muted wifi-forget" data-name="${escapeHtml(w.name)}">Forget</button>
            </td>
          </tr>
        `).join("");
      }
    }
  }

  function renderWifiScan(networks) {
    const table = document.querySelector("#wifi-scan-table");
    const tbody = document.querySelector("#wifi-scan-table tbody");
    if (!table || !tbody) return;
    table.hidden = false;
    if (networks.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" class="muted">No networks found.</td></tr>';
      return;
    }
    tbody.innerHTML = networks.map(n => {
      const secBadge = n.security && n.security !== "OPEN" && n.security !== "--"
        ? `<span class="badge badge-muted">${escapeHtml(n.security)}</span>`
        : '<span class="badge badge-warn">OPEN</span>';
      const freq = n.freq_mhz ? `${n.freq_mhz} MHz` : "—";
      const signal = n.signal != null ? `${n.signal}%` : "—";
      return `<tr>
        <td><code>${escapeHtml(n.ssid)}</code></td>
        <td>${signal}</td>
        <td>${secBadge}</td>
        <td class="muted">${freq}</td>
        <td><button class="actbtn scan-connect" data-ssid="${escapeHtml(n.ssid)}" data-open="${(!n.security || n.security === 'OPEN' || n.security === '--')}">Connect…</button></td>
      </tr>`;
    }).join("");
  }

  // Connect-from-scan handler — prompts for password if secured
  document.body.addEventListener("click", async (e) => {
    const btn = e.target.closest(".scan-connect");
    if (!btn) return;
    const ssid = btn.dataset.ssid;
    const isOpen = btn.dataset.open === "true";
    let pw = "";
    if (!isOpen) {
      pw = prompt(`Password for "${ssid}":`) || "";
      if (!pw) return;
    }
    btn.disabled = true;
    showStatus(`saving + connecting to ${ssid}…`);
    try {
      const res = await postJSON("/settings/networking/wifi/connect", { ssid, password: pw });
      showStatus(res.msg, res.ok ? "ok" : "fail");
      if (res.state) renderNetworkingState(res.state);
    } finally {
      btn.disabled = false;
    }
  });

  function renderDenyTable(cidrs) {
    const tbody = document.querySelector("#deny-table tbody");
    if (!tbody) return;
    if (!cidrs || cidrs.length === 0) {
      tbody.innerHTML = '<tr class="deny-empty"><td colspan="2" class="muted">No deny CIDRs configured. UI is reachable from any source.</td></tr>';
      return;
    }
    tbody.innerHTML = cidrs.map(c => `
      <tr data-cidr="${escapeHtml(c)}">
        <td><code>${escapeHtml(c)}</code></td>
        <td><button class="actbtn deny-remove" data-cidr="${escapeHtml(c)}">Remove</button></td>
      </tr>
    `).join("");
  }

  function escapeHtml(s) {
    if (s == null) return "";
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ---------- Event handlers ----------
  // ---------- Tab switching ----------
  function activateTab(name) {
    document.querySelectorAll(".tab").forEach(b => {
      if (b.dataset.tab === name) b.classList.add("active");
      else b.classList.remove("active");
    });
    document.querySelectorAll(".tab-panel").forEach(p => {
      p.hidden = !p.id.endsWith(`-${name}`);
    });
  }

  function init() {
    if (!document.querySelector("#adapter-table")) return; // not on settings page

    // Tab buttons
    document.querySelectorAll(".tab").forEach(btn => {
      if (btn.disabled) return;
      btn.addEventListener("click", () => {
        const name = btn.dataset.tab;
        if (name) activateTab(name);
      });
    });

    // (Captive-portal credential capture moved to the PineAP → Evil WPA tab.)

    // Password change form (Security tab)
    const pwForm = $("#change-password-form");
    if (pwForm) {
      pwForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = new FormData(pwForm);
        const body = {
          old: f.get("old"),
          new: f.get("new"),
          new_confirm: f.get("new_confirm"),
        };
        showStatus("changing password…");
        const res = await postJSON("/auth/change-password", body);
        showStatus(res.msg || (res.ok ? "password changed" : "failed"), res.ok ? "ok" : "fail");
        if (res.ok) pwForm.reset();
      });
    }

    // Deny CIDR add/remove
    const denyAdd = $("#deny-cidr-add");
    if (denyAdd) {
      denyAdd.addEventListener("click", async () => {
        const input = $("#deny-cidr-input");
        const cidr = (input.value || "").trim();
        if (!cidr) return;
        denyAdd.disabled = true;
        showStatus(`adding ${cidr} to deny list…`);
        try {
          const res = await postJSON("/settings/access/deny", { cidr });
          showStatus(res.msg, res.ok ? "ok" : "fail");
          if (res.ok) input.value = "";
          renderDenyTable(res.deny_cidrs || []);
        } finally {
          denyAdd.disabled = false;
        }
      });
    }

    // ---------- Networking tab ----------
    // Save management AP config
    const apForm = $("#mgmt-ap-form");
    if (apForm) {
      apForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = new FormData(apForm);
        const body = {
          ssid: f.get("ssid"),
          password: f.get("password"),
          channel: parseInt(f.get("channel"), 10),
        };
        showStatus("saving management AP config…");
        const res = await postJSON("/settings/networking/mgmt-ap/configure", body);
        showStatus(res.msg, res.ok ? "ok" : "fail");
      });
    }

    const apEnable = $("#mgmt-ap-enable");
    if (apEnable) {
      apEnable.addEventListener("click", async () => {
        if (!confirm(
          "Enable management AP?\nwlan0 will stop being a client to any upstream Wi-Fi and start broadcasting the management SSID.\nIf you reached this page via wlan0 (home Wi-Fi), you will lose the connection."
        )) return;
        apEnable.disabled = true;
        showStatus("enabling management AP (you may lose this connection)…");
        try {
          const res = await postJSON("/settings/networking/mgmt-ap/enable");
          showStatus((res.messages || []).join(" / "), res.ok ? "ok" : "fail");
          if (res.state) renderNetworkingState(res.state);
        } finally {
          apEnable.disabled = false;
        }
      });
    }

    // Internet-sharing toggle
    const sharingToggle = $("#internet-sharing-toggle");
    if (sharingToggle) {
      sharingToggle.addEventListener("change", async () => {
        const enabled = sharingToggle.checked;
        sharingToggle.disabled = true;
        showStatus(`${enabled ? "enabling" : "disabling"} internet sharing…`);
        try {
          const res = await postJSON("/settings/networking/mgmt-ap/internet-sharing", { enabled });
          showStatus((res.messages || []).join(" / "), res.ok ? "ok" : "fail");
          if (res.state) renderNetworkingState(res.state);
        } finally {
          sharingToggle.disabled = false;
        }
      });
    }

    // Save & Apply (write config + restart AP with new credentials)
    const apApplyBtn = $("#mgmt-ap-apply-btn");
    if (apApplyBtn) {
      apApplyBtn.addEventListener("click", async () => {
        const form = $("#mgmt-ap-form");
        if (!form) return;
        const f = new FormData(form);
        const body = {
          ssid: f.get("ssid"),
          password: f.get("password"),
          channel: parseInt(f.get("channel"), 10),
        };
        if (!body.password) {
          alert("Save & Apply needs a password. Either enter one (≥8 chars) or use Save (no restart) to update other fields without restarting.");
          return;
        }
        if (!confirm(
          `Save and restart AP with SSID "${body.ssid}"?\n` +
          `The AP will briefly drop. Your device sees the SSID change and you'll need to reconnect using the new password. ` +
          `Make sure you have a fallback (Ethernet or upstream Wi-Fi via wlan0) before proceeding.`
        )) return;
        apApplyBtn.disabled = true;
        showStatus(`saving + restarting AP with new credentials…`);
        try {
          const res = await postJSON("/settings/networking/mgmt-ap/apply", body);
          showStatus((res.messages || [res.msg]).join(" / "), res.ok ? "ok" : "fail");
          if (res.state) renderNetworkingState(res.state);
        } finally {
          apApplyBtn.disabled = false;
        }
      });
    }

    // Move AP from one interface to another (atomic swap)
    const apMoveBtn = $("#mgmt-ap-move-btn");
    if (apMoveBtn) {
      apMoveBtn.addEventListener("click", async () => {
        const sel = $("#mgmt-ap-iface-select");
        if (!sel) return;
        const target = sel.value;
        if (!target) return;
        if (!confirm(
          `Move management AP to ${target}?\n` +
          `The AP will drop on the current interface and come back on ${target} with the same SSID/password. ` +
          `You may briefly lose the management connection (your device should reconnect to the same SSID once the new radio comes up).`
        )) return;
        apMoveBtn.disabled = true;
        showStatus(`moving management AP to ${target}…`);
        try {
          const res = await postJSON("/settings/networking/mgmt-ap/move", { interface: target });
          showStatus((res.messages || []).join(" / "), res.ok ? "ok" : "fail");
          if (res.state) renderNetworkingState(res.state);
        } finally {
          apMoveBtn.disabled = false;
        }
      });
    }

    const apDisable = $("#mgmt-ap-disable");
    if (apDisable) {
      apDisable.addEventListener("click", async () => {
        apDisable.disabled = true;
        showStatus("disabling management AP…");
        try {
          const res = await postJSON("/settings/networking/mgmt-ap/disable");
          showStatus((res.messages || []).join(" / "), res.ok ? "ok" : "fail");
          if (res.state) renderNetworkingState(res.state);
        } finally {
          apDisable.disabled = false;
        }
      });
    }

    // Manual-add Wi-Fi profile (works while wlan0 is busy hosting AP)
    const manualAdd = $("#wifi-manual-add");
    if (manualAdd) {
      manualAdd.addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = new FormData(manualAdd);
        const body = {
          ssid: (f.get("ssid") || "").trim(),
          password: f.get("password") || "",
        };
        if (!body.ssid) {
          showStatus("missing SSID", "fail");
          return;
        }
        showStatus(`saving profile for ${body.ssid}…`);
        const res = await postJSON("/settings/networking/wifi/save", body);
        showStatus(res.msg, res.ok ? "ok" : "fail");
        if (res.ok) manualAdd.reset();
        if (res.state) renderNetworkingState(res.state);
      });
    }

    // Wi-Fi scan
    const scanBtn = $("#wifi-scan-btn");
    if (scanBtn) {
      scanBtn.addEventListener("click", async () => {
        scanBtn.disabled = true;
        showStatus("scanning…");
        try {
          const res = await postJSON("/settings/networking/wifi/scan");
          renderWifiScan(res.networks || []);
          showStatus(`scan complete: ${res.networks.length} networks`, "ok");
        } finally {
          scanBtn.disabled = false;
        }
      });
    }

    // Wi-Fi disconnect
    const disBtn = $("#wifi-disconnect-btn");
    if (disBtn) {
      disBtn.addEventListener("click", async () => {
        if (!confirm("Disconnect wlan0 from upstream Wi-Fi?")) return;
        const res = await postJSON("/settings/networking/wifi/disconnect");
        showStatus(res.msg, res.ok ? "ok" : "fail");
        if (res.state) renderNetworkingState(res.state);
      });
    }

    // Connect to saved network (delegated)
    document.body.addEventListener("click", async (e) => {
      const btn = e.target.closest(".wifi-connect");
      if (!btn) return;
      const ssid = btn.dataset.ssid;
      if (!confirm(`Connect wlan0 to "${ssid}"?\nIf the management AP is currently active it will be disabled first.`)) return;
      btn.disabled = true;
      showStatus(`connecting to ${ssid}…`);
      try {
        const res = await postJSON("/settings/networking/wifi/connect", { ssid });
        showStatus(res.msg, res.ok ? "ok" : "fail");
        if (res.state) renderNetworkingState(res.state);
      } finally {
        btn.disabled = false;
      }
    });

    // Forget saved network
    document.body.addEventListener("click", async (e) => {
      const btn = e.target.closest(".wifi-forget");
      if (!btn) return;
      const name = btn.dataset.name;
      if (!confirm(`Forget the saved Wi-Fi profile "${name}"?`)) return;
      const res = await postJSON("/settings/networking/wifi/forget", { name });
      showStatus(res.msg, res.ok ? "ok" : "fail");
      if (res.state) renderNetworkingState(res.state);
    });

    // ---------- Deny-table remove buttons (delegated) ----------
    const denyTable = $("#deny-table");
    if (denyTable) {
      denyTable.addEventListener("click", async (e) => {
        const btn = e.target.closest(".deny-remove");
        if (!btn) return;
        const cidr = btn.dataset.cidr;
        if (!confirm(`Remove ${cidr} from the management access deny list?`)) return;
        btn.disabled = true;
        showStatus(`removing ${cidr}…`);
        try {
          const res = await postJSON("/settings/access/deny/remove", { cidr });
          showStatus(res.msg, res.ok ? "ok" : "fail");
          renderDenyTable(res.deny_cidrs || []);
        } finally {
          btn.disabled = false;
        }
      });
    }

    // Role select changes
    document.body.addEventListener("change", async (e) => {
      const sel = e.target.closest(".role-select");
      if (!sel) return;
      const mac = sel.dataset.mac;
      const role = sel.value;
      showStatus(`assigning ${role} to ${mac}…`);
      const res = await postJSON("/settings/adapters/role", { mac, role });
      if (res.ok) {
        showStatus(`role set: ${res.msg}`, "ok");
        renderAdapters(res.adapters || []);
      } else {
        showStatus(`role failed: ${res.msg}`, "fail");
      }
    });

    // Action buttons (monitor / managed / down)
    document.body.addEventListener("click", async (e) => {
      const btn = e.target.closest(".actbtn");
      if (!btn) return;
      const iface = btn.dataset.iface;
      const action = btn.dataset.action;
      btn.disabled = true;
      try {
        if (action === "monitor" || action === "managed") {
          showStatus(`${iface} → ${action}…`);
          const res = await postJSON(`/settings/adapters/${iface}/mode`, { mode: action });
          showStatus(
            (res.messages || []).join(" / "),
            res.ok ? "ok" : "fail"
          );
        } else if (action === "down") {
          showStatus(`bringing ${iface} down…`);
          const res = await postJSON(`/settings/adapters/${iface}/down`);
          showStatus(res.msg, res.ok ? "ok" : "fail");
        }
        await refreshAdapters();
      } finally {
        btn.disabled = false;
      }
    });

    // Apply buttons
    const applyUdev = $("#apply-udev");
    if (applyUdev) applyUdev.addEventListener("click", async () => {
      applyUdev.disabled = true;
      showStatus("writing udev rules…");
      try {
        const res = await postJSON("/settings/adapters/apply-udev");
        showStatus(res.msg, res.ok ? "ok" : "fail");
      } finally {
        applyUdev.disabled = false;
      }
    });

    const applyNm = $("#apply-nm");
    if (applyNm) applyNm.addEventListener("click", async () => {
      applyNm.disabled = true;
      showStatus("writing NM config…");
      try {
        const res = await postJSON("/settings/adapters/apply-nm");
        showStatus(res.msg, res.ok ? "ok" : "fail");
      } finally {
        applyNm.disabled = false;
      }
    });

    const stopMgrs = $("#stop-managers");
    if (stopMgrs) stopMgrs.addEventListener("click", async () => {
      if (!confirm("Stop NetworkManager + wpa_supplicant?\nwlan0 will lose home Wi-Fi until reboot or systemctl start.")) return;
      stopMgrs.disabled = true;
      showStatus("stopping managers…");
      try {
        const res = await postJSON("/settings/adapters/stop-managers");
        showStatus(res.msg, res.ok ? "ok" : "fail");
        await refreshAdapters();
      } finally {
        stopMgrs.disabled = false;
      }
    });

    // Auto-refresh on every sysinfo update so the table reflects mode
    // changes triggered from elsewhere or by external actions.
    if (window.pipineapple && window.pipineapple.socket) {
      const sock = window.pipineapple.socket;
      sock.on("sysinfo", () => refreshAdapters());
    } else {
      // socket might not be ready yet — defer
      window.addEventListener("load", () => {
        if (window.pipineapple && window.pipineapple.socket) {
          window.pipineapple.socket.on("sysinfo", () => refreshAdapters());
        }
      });
    }

    // ---------- Crack Targets tab (Session 09) ----------
    // Wire even if the tab isn't active yet — loads are cheap and the
    // table appearing populated when the operator first clicks the tab
    // is a nicer UX than a flash of "loading…".
    if ($("#crack-targets-tbody")) {
      loadCrackPubkey();
      loadCrackTargets();
      const addBtn = $("#ct-add");
      if (addBtn) {
        addBtn.addEventListener("click", async () => {
          const body = {
            name:          ($("#ct-name").value     || "").trim(),
            host:          ($("#ct-host").value     || "").trim(),
            user:          ($("#ct-user").value     || "").trim(),
            port:          parseInt($("#ct-port").value || "22", 10),
            wordlist_path: ($("#ct-wordlist").value || "").trim(),
          };
          if (!body.name || !body.host || !body.user || !body.wordlist_path) {
            showStatus("name, host, user, wordlist are all required", "fail");
            return;
          }
          addBtn.disabled = true;
          showStatus(`adding crack target ${body.name}…`);
          try {
            const res = await postJSON("/crack/targets", body);
            showStatus(res.msg || (res.ok ? "added" : "failed"),
                       res.ok ? "ok" : "fail");
            if (res.ok) {
              ["ct-name", "ct-host", "ct-user", "ct-wordlist"].forEach(id => {
                const el = $("#" + id);
                if (el) el.value = "";
              });
              $("#ct-port").value = "22";
              loadCrackTargets();
            }
          } finally {
            addBtn.disabled = false;
          }
        });
      }
    }
  }

  // ---------- Crack target helpers ----------
  async function loadCrackPubkey() {
    try {
      const r = await fetch("/crack/public-key");
      const data = await r.json();
      const pre = $("#crack-pubkey");
      const fp = $("#crack-pubkey-fingerprint");
      if (pre) pre.textContent = data.key || "(no key)";
      if (fp)  fp.textContent  = data.fingerprint || "";
    } catch (e) {
      const pre = $("#crack-pubkey");
      if (pre) pre.textContent = "failed to load public key: " + e;
    }
  }

  async function loadCrackTargets() {
    const tbody = $("#crack-targets-tbody");
    if (!tbody) return;
    try {
      const r = await fetch("/crack/targets");
      const data = await r.json();
      renderCrackTargets(data.targets || []);
    } catch (e) {
      tbody.innerHTML =
        `<tr class="deny-empty"><td colspan="7" class="muted">
           failed to load targets: ${escapeHtml(String(e))}
         </td></tr>`;
    }
  }

  function renderCrackTargets(targets) {
    const tbody = $("#crack-targets-tbody");
    if (!tbody) return;
    if (!targets.length) {
      tbody.innerHTML =
        `<tr class="deny-empty"><td colspan="7" class="muted">
           No crack targets configured. Add one above.
         </td></tr>`;
      return;
    }
    tbody.innerHTML = targets.map(t => {
      let testBadge = `<span class="muted">untested</span>`;
      if (t.last_test_ok === true)  testBadge = `<span class="badge badge-ok"   title="${escapeHtml(t.last_test_msg || "")}">ok</span>`;
      if (t.last_test_ok === false) testBadge = `<span class="badge badge-warn" title="${escapeHtml(t.last_test_msg || "")}">fail</span>`;
      const lastMsg = t.last_test_msg
        ? `<div class="muted" style="font-size:10px; margin-top:2px;">${escapeHtml(t.last_test_msg)}</div>`
        : "";
      return `<tr data-id="${escapeHtml(t.id)}">
        <td><strong>${escapeHtml(t.name)}</strong></td>
        <td><code>${escapeHtml(t.host)}</code></td>
        <td><code>${escapeHtml(t.user)}</code></td>
        <td>${escapeHtml(String(t.port || 22))}</td>
        <td><code style="font-size:11px;">${escapeHtml(t.wordlist_path)}</code></td>
        <td>${testBadge}${lastMsg}</td>
        <td>
          <button class="actbtn ct-test"   data-id="${escapeHtml(t.id)}">Test</button>
          <button class="actbtn actbtn-muted ct-remove" data-id="${escapeHtml(t.id)}">×</button>
        </td>
      </tr>`;
    }).join("");

    tbody.querySelectorAll(".ct-test").forEach(b => {
      b.addEventListener("click", async () => {
        b.disabled = true;
        b.textContent = "Testing…";
        try {
          const r = await fetch(`/crack/targets/${encodeURIComponent(b.dataset.id)}/test`,
                                { method: "POST" });
          const data = await r.json();
          showStatus(data.msg || (data.ok ? "ok" : "failed"),
                     data.ok ? "ok" : "fail");
        } finally {
          b.disabled = false;
          b.textContent = "Test";
          loadCrackTargets();
        }
      });
    });
    tbody.querySelectorAll(".ct-remove").forEach(b => {
      b.addEventListener("click", async () => {
        if (!confirm("Remove this crack target?")) return;
        b.disabled = true;
        try {
          const r = await fetch(`/crack/targets/${encodeURIComponent(b.dataset.id)}`,
                                { method: "DELETE" });
          const data = await r.json();
          showStatus(data.msg, data.ok ? "ok" : "fail");
        } finally {
          loadCrackTargets();
        }
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
