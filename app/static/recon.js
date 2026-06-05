/* Recon page — live AP + Client tables driven by SocketIO 'recon:update'.
 *
 * Initial render comes from the server-side template; after that, every
 * recon:update event re-renders both <tbody>s in place. Sortable columns
 * are click-driven and remember their state in-memory only.
 */

(function () {
  "use strict";

  // ---- State ----
  let aps = [];       // last-known AP list (from server)
  let clients = [];   // last-known Client list
  let sortAps = { key: "signal_dbm", dir: "desc" };
  let sortClients = { key: "last_seen", dir: "desc" };

  // ---- Helpers ----
  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  function signalClass(dbm) {
    if (dbm == null || dbm === "") return "sig-unknown";
    const n = Number(dbm);
    if (n >= -55) return "sig-strong";
    if (n >= -70) return "sig-medium";
    if (n >= -85) return "sig-weak";
    return "sig-faint";
  }

  function compareBy(key, dir) {
    const sign = dir === "desc" ? -1 : 1;
    return (a, b) => {
      const av = a[key];
      const bv = b[key];
      // null/undefined sort last regardless of direction
      const aMissing = av == null || av === "";
      const bMissing = bv == null || bv === "";
      if (aMissing && bMissing) return 0;
      if (aMissing) return 1;
      if (bMissing) return -1;
      // Numeric vs string detection
      if (typeof av === "number" && typeof bv === "number") {
        return sign * (av - bv);
      }
      return sign * String(av).localeCompare(String(bv));
    };
  }

  // ---- Rendering ----
  function renderAps() {
    const tbody = $("recon-ap-tbody");
    if (!tbody) return;
    const sorted = [...aps].sort(compareBy(sortAps.key, sortAps.dir));
    if (sorted.length === 0) {
      tbody.innerHTML = "";
      const empty = $("recon-ap-empty");
      if (empty) empty.hidden = false;
    } else {
      const empty = $("recon-ap-empty");
      if (empty) empty.hidden = true;
      tbody.innerHTML = sorted.map((ap) => {
        const sec = ap.encryption + (ap.auth ? "/" + ap.auth : "");
        const sigVal = ap.signal_dbm == null ? "—" : ap.signal_dbm;
        return `<tr data-bssid="${escapeHtml(ap.bssid)}">
          <td><span class="sig-pill ${signalClass(ap.signal_dbm)}">${escapeHtml(sigVal)}</span></td>
          <td>${escapeHtml(ap.band || "—")}</td>
          <td>${escapeHtml(ap.channel || "—")}</td>
          <td>${ap.essid ? escapeHtml(ap.essid) : "<em class=\"muted\">&lt;hidden&gt;</em>"}</td>
          <td><code>${escapeHtml(ap.bssid)}</code></td>
          <td>${escapeHtml(sec)}</td>
          <td>${escapeHtml(ap.beacons)}</td>
          <td>${escapeHtml(ap.data_packets)}</td>
          <td class="muted">${escapeHtml(ap.last_seen || "—")}</td>
        </tr>`;
      }).join("");
    }
    const count = $("recon-ap-count");
    if (count) count.textContent = String(aps.length);
  }

  function renderClients() {
    const tbody = $("recon-client-tbody");
    if (!tbody) return;
    const sorted = [...clients].sort(compareBy(sortClients.key, sortClients.dir));
    if (sorted.length === 0) {
      tbody.innerHTML = "";
      const empty = $("recon-client-empty");
      if (empty) empty.hidden = false;
    } else {
      const empty = $("recon-client-empty");
      if (empty) empty.hidden = true;
      tbody.innerHTML = sorted.map((c) => {
        const sigVal = c.signal_dbm == null ? "—" : c.signal_dbm;

        // Associated-AP cell: prefer SSID (reconciled from the APs table
        // server-side as ap_ssid); fall back to raw BSSID; show muted
        // "(not associated)" for clients with no association.
        let apCell;
        if (c.ap_ssid) {
          apCell = `<strong>${escapeHtml(c.ap_ssid)}</strong>
            <div class="muted" style="font-size:11px;"><code>${escapeHtml(c.bssid)}</code></div>`;
        } else if (c.bssid && c.bssid !== "(not associated)") {
          apCell = `<code>${escapeHtml(c.bssid)}</code>
            <div class="muted" style="font-size:11px;">SSID not in range</div>`;
        } else {
          apCell = `<span class="muted">(not associated)</span>`;
        }

        // Probed-for cell: badge SSIDs whose AP is currently in range
        // (cross-referenced server-side as probed_in_range). These are
        // the most interesting names — the client is asking for a
        // network we can see, which is the basis for Karma-style
        // impersonation later in Phase D.
        let probedCell;
        if (c.probed_essids && c.probed_essids.length) {
          const inRange = new Set(c.probed_in_range || []);
          probedCell = c.probed_essids.map((s) => {
            const safe = escapeHtml(s);
            return inRange.has(s)
              ? `<span class="badge badge-info" title="An AP with this SSID is in range">${safe}</span>`
              : safe;
          }).join(", ");
        } else {
          probedCell = "—";
        }

        return `<tr data-mac="${escapeHtml(c.station_mac)}">
          <td><span class="sig-pill ${signalClass(c.signal_dbm)}">${escapeHtml(sigVal)}</span></td>
          <td><code>${escapeHtml(c.station_mac)}</code></td>
          <td>${apCell}</td>
          <td>${escapeHtml(c.packets)}</td>
          <td>${probedCell}</td>
          <td class="muted">${escapeHtml(c.last_seen || "—")}</td>
        </tr>`;
      }).join("");
    }
    const count = $("recon-client-count");
    if (count) count.textContent = String(clients.length);
  }

  // Scan duration — tick once per second from started_at. Cleared
  // when the scan goes idle. We render even in starting/stopping
  // states so the operator sees the running time during teardown.
  let _scanStartedAt = null;          // unix seconds, server time
  let _durationTimer = null;

  function _fmtDuration(secs) {
    if (secs < 0) secs = 0;
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = Math.floor(secs % 60);
    return (h ? `${h}h ` : "") +
           (h || m ? `${String(m).padStart(2, "0")}m ` : "") +
           `${String(s).padStart(2, "0")}s`;
  }

  function _updateDurationLabel() {
    const el = $("recon-duration");
    if (!el) return;
    if (_scanStartedAt == null) {
      el.textContent = "";
      return;
    }
    const elapsed = (Date.now() / 1000) - _scanStartedAt;
    el.textContent = `running ${_fmtDuration(elapsed)}`;
  }

  function setStatus(status) {
    const badge = $("recon-state-badge");
    if (badge) {
      badge.textContent = status.state;
      badge.dataset.state = status.state;
      badge.className = "badge " + (
        status.state === "running" ? "badge-good" :
        status.state === "starting" || status.state === "stopping" ? "badge-warn" :
        ""
      );
    }
    const counts = $("recon-counts");
    if (counts) {
      counts.textContent = `${status.ap_count} APs · ${status.client_count} clients`;
    }
    const startBtn = $("recon-start");
    const stopBtn = $("recon-stop");
    if (startBtn) startBtn.disabled = status.state !== "idle";
    if (stopBtn)  stopBtn.disabled  = status.state === "idle";

    // Duration ticker
    if (status.state === "idle" || status.started_at == null) {
      _scanStartedAt = null;
      if (_durationTimer) {
        clearInterval(_durationTimer);
        _durationTimer = null;
      }
      const el = $("recon-duration");
      if (el) el.textContent = "";
    } else {
      _scanStartedAt = status.started_at;
      _updateDurationLabel();
      if (!_durationTimer) {
        _durationTimer = setInterval(_updateDurationLabel, 1000);
      }
    }
  }

  // ---- Actions ----
  async function postJson(url, body) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : "{}",
      });
      return await res.json();
    } catch (e) {
      return { ok: false, messages: ["network error: " + e] };
    }
  }

  async function onStart() {
    const startBtn = $("recon-start");
    if (startBtn) startBtn.disabled = true;
    const res = await postJson("/recon/start");
    if (res.status) setStatus(res.status);
    if (!res.ok && startBtn) startBtn.disabled = false;
  }

  async function onStop() {
    const stopBtn = $("recon-stop");
    if (stopBtn) stopBtn.disabled = true;
    const res = await postJson("/recon/stop");
    if (res.status) setStatus(res.status);
    // Clear tables immediately for snappy UX
    aps = [];
    clients = [];
    renderAps();
    renderClients();
  }

  // ---- Sortable column headers ----
  function attachSortHandlers() {
    document.querySelectorAll("#recon-ap-table th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        sortAps.dir = (sortAps.key === key && sortAps.dir === "desc") ? "asc" : "desc";
        sortAps.key = key;
        renderAps();
      });
    });
    document.querySelectorAll("#recon-client-table th[data-sort]").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        sortClients.dir = (sortClients.key === key && sortClients.dir === "desc") ? "asc" : "desc";
        sortClients.key = key;
        renderClients();
      });
    });
  }

  // ==================================================================
  // Slide-out detail panel (Session 06)
  // ==================================================================

  let activeTab = null;   // for re-rendering after tab switch
  let activeDetail = null;
  let activeKind = null;  // "ap" or "client"

  function openSlideout() {
    const el = $("slideout");
    const bd = $("slideout-backdrop");
    if (!el) return;
    if (bd) bd.hidden = false;
    el.hidden = false;
    // Force a reflow so the transition fires
    requestAnimationFrame(() => {
      el.classList.add("open");
      if (bd) bd.classList.add("open");
    });
  }

  function closeSlideout() {
    const el = $("slideout");
    const bd = $("slideout-backdrop");
    if (!el) return;
    el.classList.remove("open");
    if (bd) bd.classList.remove("open");
    // Wait for the transition before re-hiding (matches CSS 180ms)
    setTimeout(() => {
      el.hidden = true;
      if (bd) bd.hidden = true;
    }, 200);
    activeDetail = null;
    activeKind = null;
    activeTab = null;
  }

  async function openApSlideout(bssid) {
    let detail;
    try {
      const r = await fetch(`/recon/ap/${encodeURIComponent(bssid)}/detail`);
      if (!r.ok) return;
      detail = await r.json();
    } catch (e) { return; }
    activeKind = "ap";
    activeDetail = detail;
    activeTab = "overview";
    renderApSlideout();
    openSlideout();
  }

  async function openClientSlideout(mac) {
    let detail;
    try {
      const r = await fetch(`/recon/client/${encodeURIComponent(mac)}/detail`);
      if (!r.ok) return;
      detail = await r.json();
    } catch (e) { return; }
    activeKind = "client";
    activeDetail = detail;
    activeTab = "overview";
    renderClientSlideout();
    openSlideout();
  }

  function setSlideoutTabs(tabs) {
    const nav = $("slideout-tabs");
    if (!nav) return;
    nav.innerHTML = tabs.map((t) =>
      `<button class="slideout-tab ${t.id === activeTab ? "active" : ""}"
               data-tab="${t.id}">${escapeHtml(t.label)}</button>`
    ).join("");
    nav.querySelectorAll(".slideout-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        activeTab = btn.dataset.tab;
        if (activeKind === "ap") renderApSlideout();
        else renderClientSlideout();
      });
    });
  }

  function renderApSlideout() {
    if (!activeDetail) return;
    const { ap, beacon, associated } = activeDetail;
    $("slideout-title").textContent = ap.essid || "<hidden SSID>";

    setSlideoutTabs([
      { id: "overview",   label: "Overview" },
      { id: "security",   label: "Security" },
      { id: "tags",       label: "Tagged params" },
      { id: "clients",    label: `Clients (${associated.length})` },
    ]);

    const body = $("slideout-body");
    if (activeTab === "overview") {
      body.innerHTML = `
        <h3>Identity</h3>
        <dl>
          <dt>SSID</dt><dd>${escapeHtml(ap.essid || "<hidden>")}</dd>
          <dt>BSSID</dt><dd>${escapeHtml(ap.bssid)}</dd>
          <dt>Vendor</dt><dd class="muted">— (S06+ adds OUI lookup)</dd>
        </dl>
        <h3>Radio</h3>
        <dl>
          <dt>Band</dt><dd>${escapeHtml(ap.band || "—")}</dd>
          <dt>Channel</dt><dd>${escapeHtml(ap.channel || "—")}</dd>
          <dt>Signal</dt><dd>${ap.signal_dbm == null ? "—" : ap.signal_dbm + " dBm"}</dd>
        </dl>
        <h3>Activity</h3>
        <dl>
          <dt>Beacons</dt><dd>${escapeHtml(ap.beacons)}</dd>
          <dt>Data frames</dt><dd>${escapeHtml(ap.data_packets)}</dd>
          <dt>First seen</dt><dd class="muted">${escapeHtml(ap.first_seen || "—")}</dd>
          <dt>Last seen</dt><dd class="muted">${escapeHtml(ap.last_seen || "—")}</dd>
        </dl>`;
    } else if (activeTab === "security") {
      if (!beacon || !beacon.rsn) {
        body.innerHTML = `<p class="muted">No RSN element parsed.
          ${beacon ? "(AP is likely open / WEP — pre-RSN security.)"
                  : "(Beacon not in pcap yet — scan may need more time, or scapy isn't installed on the Pi.)"}</p>`;
      } else {
        const r = beacon.rsn;
        body.innerHTML = `
          <h3>Summary</h3>
          <p style="margin:0 0 12px 0;"><strong>${escapeHtml(r.summary)}</strong></p>
          <h3>Cipher suites</h3>
          <dl>
            <dt>Group</dt><dd>${escapeHtml(r.group_cipher)}</dd>
            <dt>Pairwise</dt><dd>${(r.pairwise_ciphers || []).map(escapeHtml).join(", ") || "—"}</dd>
          </dl>
          <h3>Authentication</h3>
          <dl>
            <dt>AKM suites</dt><dd>${(r.akms || []).map(escapeHtml).join(", ") || "—"}</dd>
            <dt>MFP capable</dt><dd>${r.mfp_capable ? "yes" : "no"}</dd>
            <dt>MFP required</dt><dd>${r.mfp_required ? "<strong>yes — deauth blocked</strong>" : "no"}</dd>
            <dt>RSN caps</dt><dd>0x${escapeHtml(r.rsn_capabilities_hex)}</dd>
          </dl>`;
      }
    } else if (activeTab === "tags") {
      if (!beacon || !beacon.ies) {
        body.innerHTML = `<p class="muted">No beacon parsed yet.</p>`;
      } else {
        const rows = beacon.ies.map((ie) =>
          `<tr><td>${ie.tag}</td><td>${escapeHtml(ie.name)}</td>
               <td>${ie.length}</td>
               <td class="ie-hex">${escapeHtml(ie.hex || "")}</td></tr>`).join("");
        body.innerHTML = `
          <h3>Raw Information Elements</h3>
          <table class="ie-table">
            <thead><tr><th>Tag</th><th>Name</th><th>Len</th><th>Value (hex)</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>`;
      }
    } else if (activeTab === "clients") {
      if (!associated.length) {
        body.innerHTML = `<p class="muted">No clients currently
          associated to this AP in our scan window.</p>`;
      } else {
        body.innerHTML = `<dl>` + associated.map((c) =>
          `<dt>${escapeHtml(c.station_mac)}</dt>
           <dd>signal ${c.signal_dbm == null ? "—" : c.signal_dbm} dBm,
               ${c.packets} pkts, last ${escapeHtml(c.last_seen || "—")}</dd>`
        ).join("") + `</dl>`;
      }
    }

    // Actions row — deauth always present; will gate via ethics modal
    const actions = $("slideout-actions");
    const mfpRequired = beacon && beacon.rsn && beacon.rsn.mfp_required;
    actions.innerHTML = `
      <button class="bigbtn bigbtn-danger" id="slideout-deauth"
              ${mfpRequired ? "disabled title=\"MFP required — deauth will be rejected\"" : ""}>
        Deauth all clients
      </button>`;
    const dbtn = $("slideout-deauth");
    if (dbtn && !mfpRequired) {
      dbtn.addEventListener("click", () => showEthicsModal(
        `${ap.essid || ap.bssid} (BSSID ${ap.bssid})`,
        async () => {
          const r = await postJson(`/recon/ap/${encodeURIComponent(ap.bssid)}/deauth`,
                                   { count: 10 });
          return r;
        }
      ));
    }
  }

  function renderClientSlideout() {
    if (!activeDetail) return;
    const { client, probes } = activeDetail;
    $("slideout-title").textContent = client.station_mac;

    setSlideoutTabs([
      { id: "overview", label: "Overview" },
      { id: "probes",   label: `Probe history (${probes.length})` },
    ]);

    const body = $("slideout-body");
    if (activeTab === "overview") {
      const apLine = client.ap_ssid
        ? `<strong>${escapeHtml(client.ap_ssid)}</strong>
           <span class="muted">(${escapeHtml(client.bssid)})</span>`
        : (client.bssid && client.bssid !== "(not associated)"
            ? `<code>${escapeHtml(client.bssid)}</code> <span class="muted">— SSID not in range</span>`
            : `<span class="muted">(not associated)</span>`);

      const inRange   = client.probed_in_range     || [];
      const outRange  = client.probed_not_in_range || [];

      const inRangeHtml = inRange.length
        ? inRange.map((s) => `<span class="badge badge-good">${escapeHtml(s)}</span>`).join(" ")
        : `<span class="muted">none</span>`;

      // Highlight the not-in-range probes — these are the
      // privacy-leaky ones. A device asking for "OldOfficeWifi" or
      // "HotelLasVegas2019" away from those places reveals where
      // it's been.
      const outRangeHtml = outRange.length
        ? outRange.map((s) => `<span class="badge badge-warn">${escapeHtml(s)}</span>`).join(" ")
        : `<span class="muted">none</span>`;

      body.innerHTML = `
        <h3>Identity</h3>
        <dl>
          <dt>Station MAC</dt><dd>${escapeHtml(client.station_mac)}</dd>
          <dt>Associated to</dt><dd>${apLine}</dd>
        </dl>
        <h3>Activity</h3>
        <dl>
          <dt>Signal</dt><dd>${client.signal_dbm == null ? "—" : client.signal_dbm + " dBm"}</dd>
          <dt>Packets</dt><dd>${escapeHtml(client.packets)}</dd>
          <dt>First seen</dt><dd class="muted">${escapeHtml(client.first_seen || "—")}</dd>
          <dt>Last seen</dt><dd class="muted">${escapeHtml(client.last_seen || "—")}</dd>
        </dl>
        <h3>Probed SSIDs — in range here</h3>
        <p>${inRangeHtml}</p>
        <h3>Probed SSIDs — NOT in range (PNL leak)</h3>
        <p>${outRangeHtml}</p>
        <p class="muted" style="margin-top:8px; font-size:11px;">
          The "not in range" list shows networks this device remembers
          from elsewhere and is actively asking about. If it's a device
          you own, consider forgetting old networks it no longer needs.
        </p>`;
    } else if (activeTab === "probes") {
      // Merge two data sources so nothing is lost:
      //   1. pcap-derived ``probes`` — full timing + count per SSID
      //   2. CSV-derived ``client.probed_essids`` — basic name list
      // The pcap parser may miss frames (slow start, scapy not installed,
      // or just hasn't caught a probe for that SSID yet). The CSV list
      // is always populated by airodump's own merger. Fall back to the
      // CSV when the pcap entry is missing, so the full probed-SSID
      // history is visible regardless of whether scapy is doing its job.
      const pcapSsids = new Set(probes.map((p) => p.ssid));
      const csvProbed = client.probed_essids || [];
      const inRangeSet = new Set(client.probed_in_range || []);

      const synthetic = csvProbed
        .filter((s) => !pcapSsids.has(s))
        .map((s) => ({
          ssid:         s,
          is_broadcast: false,
          count:        null,          // sentinel → "?"
          first_seen:   null,
          last_seen:    null,
          synthetic:    true,
        }));
      const all = [...probes, ...synthetic];

      if (!all.length) {
        body.innerHTML = `<p class="muted">No probed SSIDs captured yet
          for this client.</p>`;
      } else {
        // Sort: not-in-range first (the interesting ones), then by
        // count desc within each group.
        all.sort((a, b) => {
          const aIn = inRangeSet.has(a.ssid) || a.is_broadcast ? 1 : 0;
          const bIn = inRangeSet.has(b.ssid) || b.is_broadcast ? 1 : 0;
          if (aIn !== bIn) return aIn - bIn;
          return (b.count || 0) - (a.count || 0);
        });
        const rows = all.map((p) => {
          const ssidCell = p.is_broadcast
            ? `<span class="muted">&lt;broadcast&gt;</span>`
            : escapeHtml(p.ssid);
          const inRangeFlag = p.is_broadcast
            ? `<span class="muted">—</span>`
            : (inRangeSet.has(p.ssid)
                ? `<span class="badge badge-good">in range</span>`
                : `<span class="badge badge-warn">not in range</span>`);
          const countCell = p.count == null
            ? `<span class="muted">? <span title="airodump CSV only, pcap parse hasn't seen this yet">(CSV)</span></span>`
            : p.count;
          const tFirst = p.first_seen
            ? new Date(p.first_seen * 1000).toLocaleTimeString()
            : `<span class="muted">—</span>`;
          const tLast = p.last_seen
            ? new Date(p.last_seen * 1000).toLocaleTimeString()
            : `<span class="muted">—</span>`;
          return `<tr>
            <td>${ssidCell}</td>
            <td>${inRangeFlag}</td>
            <td style="text-align:right;">${countCell}</td>
            <td class="muted">${tFirst}</td>
            <td class="muted">${tLast}</td>
          </tr>`;
        }).join("");
        body.innerHTML = `
          <h3>Per-SSID probe activity</h3>
          <p class="muted" style="font-size:11px;">
            Sorted with not-in-range SSIDs first — those are the
            privacy-interesting ones (networks the device remembers
            from elsewhere). "(CSV)" means the SSID is known from
            airodump's summary but the pcap parser hasn't caught a
            specific frame yet (so no timing data).
          </p>
          <table class="ie-table">
            <thead><tr><th>SSID</th><th>Status</th>
                       <th style="text-align:right;">Count</th>
                       <th>First seen</th><th>Last seen</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>`;
      }
    }

    // No action buttons for clients in S06 (per-client deauth lands with
    // S07's handshake-capture flow).
    $("slideout-actions").innerHTML = "";
  }

  // Event delegation on the tbody so re-renders don't lose handlers
  function attachRowHandlers() {
    const apT = $("recon-ap-tbody");
    if (apT) apT.addEventListener("click", (e) => {
      const tr = e.target.closest("tr[data-bssid]");
      if (tr) openApSlideout(tr.dataset.bssid);
    });
    const clT = $("recon-client-tbody");
    if (clT) clT.addEventListener("click", (e) => {
      const tr = e.target.closest("tr[data-mac]");
      if (tr) openClientSlideout(tr.dataset.mac);
    });
  }

  function attachSlideoutChrome() {
    const close = $("slideout-close");
    const backdrop = $("slideout-backdrop");
    if (close) close.addEventListener("click", closeSlideout);
    if (backdrop) backdrop.addEventListener("click", closeSlideout);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (!$("ethics-modal").hidden) cancelEthicsModal();
        else if ($("slideout") && !$("slideout").hidden) closeSlideout();
      }
    });
  }

  // ==================================================================
  // Ethics-confirm modal (deauth gate)
  // ==================================================================

  let pendingConfirm = null;

  function showEthicsModal(targetLabel, onConfirm) {
    const modal = $("ethics-modal");
    const input = $("ethics-input");
    const ok = $("ethics-confirm");
    $("ethics-target").textContent = targetLabel;
    input.value = "";
    ok.disabled = true;
    pendingConfirm = onConfirm;
    modal.hidden = false;
    input.focus();
  }

  function cancelEthicsModal() {
    $("ethics-modal").hidden = true;
    pendingConfirm = null;
  }

  function attachEthicsHandlers() {
    const input = $("ethics-input");
    const ok = $("ethics-confirm");
    const cancel = $("ethics-cancel");
    const backdrop = $("ethics-backdrop");
    if (input) input.addEventListener("input", () => {
      ok.disabled = input.value.trim() !== "deauth";
    });
    if (cancel)   cancel.addEventListener("click", cancelEthicsModal);
    if (backdrop) backdrop.addEventListener("click", cancelEthicsModal);
    if (ok) ok.addEventListener("click", async () => {
      if (!pendingConfirm) return;
      ok.disabled = true;
      ok.textContent = "Sending…";
      try { await pendingConfirm(); }
      finally {
        ok.textContent = "Send deauth";
        cancelEthicsModal();
      }
    });
  }

  // ---- Wire-up ----
  document.addEventListener("DOMContentLoaded", () => {
    const startBtn = $("recon-start");
    const stopBtn = $("recon-stop");
    if (startBtn) startBtn.addEventListener("click", onStart);
    if (stopBtn)  stopBtn.addEventListener("click", onStop);
    attachSortHandlers();
    attachRowHandlers();
    attachSlideoutChrome();
    attachEthicsHandlers();

    // Fetch one snapshot immediately so we don't wait up to POLL_INTERVAL
    // for the first SocketIO event after page load.
    fetch("/recon/snapshot")
      .then((r) => r.json())
      .then((snap) => {
        aps = snap.aps || [];
        clients = snap.clients || [];
        if (snap.status) setStatus(snap.status);
        renderAps();
        renderClients();
      })
      .catch(() => {});

    // Subscribe to live updates.
    const tryWire = () => {
      const sock = window.pipineapple && window.pipineapple.socket;
      if (!sock) {
        setTimeout(tryWire, 200);
        return;
      }
      sock.on("recon:update", (snap) => {
        aps = snap.aps || [];
        clients = snap.clients || [];
        if (snap.status) setStatus(snap.status);
        renderAps();
        renderClients();
      });
    };
    tryWire();
  });
})();
