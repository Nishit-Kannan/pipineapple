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

    // Duration ticker — only tick while RUNNING. "starting" and
    // "stopping" are transient transitions, not actual scan time.
    if (status.state === "running" && status.started_at != null) {
      _scanStartedAt = status.started_at;
      _updateDurationLabel();
      if (!_durationTimer) {
        _durationTimer = setInterval(_updateDurationLabel, 1000);
      }
    } else {
      _scanStartedAt = null;
      if (_durationTimer) {
        clearInterval(_durationTimer);
        _durationTimer = null;
      }
      const el = $("recon-duration");
      if (el) el.textContent = "";
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
    // The recon service runs teardown in a background thread and emits
    // a final recon:update over SocketIO when state goes idle. Polling
    // is the fallback: if the SocketIO event doesn't land (tab not
    // focused, polling transport gap, race), this picks it up. Stops
    // as soon as state is idle, or after 30s as a safety cap.
    _pollUntilIdle(30);
  }

  function _pollUntilIdle(maxSeconds) {
    const deadline = Date.now() + maxSeconds * 1000;
    const tick = async () => {
      if (Date.now() > deadline) return;
      try {
        const r = await fetch("/recon/snapshot");
        if (r.ok) {
          const snap = await r.json();
          if (snap.status) setStatus(snap.status);
          if (snap.status && snap.status.state === "idle") return;
        }
      } catch (e) { /* ignore — try again */ }
      setTimeout(tick, 2000);
    };
    setTimeout(tick, 1500);   // first check after teardown's settle window
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
      { id: "captures",   label: "Captures" },
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
    } else if (activeTab === "captures") {
      // Loaded async — show a placeholder until the fetch returns.
      body.innerHTML = `<p class="muted">Loading captures…</p>`;
      _loadCapturesTab(ap.bssid);
    }

    // Actions row — Deauth + Capture Handshakes. Both auto-disable
    // when MFP is required (deauth gets rejected, and our capture
    // strategy relies on deauth to force handshakes).
    const actions = $("slideout-actions");
    const mfpRequired = beacon && beacon.rsn && beacon.rsn.mfp_required;
    const mfpTooltip = mfpRequired
      ? 'title="MFP required — frames will be cryptographically rejected"' : "";
    actions.innerHTML = `
      <button class="bigbtn bigbtn-danger" id="slideout-deauth"
              ${mfpRequired ? `disabled ${mfpTooltip}` : ""}>
        Deauth all
      </button>
      <button class="bigbtn" id="slideout-capture"
              ${mfpRequired ? `disabled ${mfpTooltip}` : ""}>
        Capture handshakes
      </button>
      <div id="slideout-capture-status" class="muted"
           style="margin-left:auto; font-size:12px; text-align:right;"></div>`;
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
    const cbtn = $("slideout-capture");
    if (cbtn && !mfpRequired) {
      cbtn.addEventListener("click", () => showCaptureModal(ap));
    }
    // Reflect any in-flight capture state for this BSSID
    _refreshCaptureStatus(ap.bssid);
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

  // ==================================================================
  // Handshake capture (Session 07)
  // ==================================================================
  // BSSID currently being captured, if any. Tracked so the slide-out
  // status badge updates correctly when the operator opens an AP that
  // already has a capture in flight.
  let _captureInFlightBssid = null;
  let _captureStatusByBssid = {};   // bssid -> last status payload

  function _fmtMsgDots(messagesSeen) {
    const set = new Set(messagesSeen || []);
    return [1, 2, 3, 4].map((n) =>
      set.has(n)
        ? `<span class="msg-dot msg-dot-on" title="M${n} captured">M${n}</span>`
        : `<span class="msg-dot" title="M${n} not yet seen">M${n}</span>`
    ).join("");
  }

  function _captureSummaryText(s) {
    if (!s) return "";
    const inner = s.status || {};
    const dots = _fmtMsgDots(inner.messages_seen);
    let label;
    if (inner.is_complete) {
      label = `<span class="capture-pill capture-complete">complete</span>`;
    } else if (inner.is_partial) {
      label = `<span class="capture-pill capture-partial">partial</span>`;
    } else if (inner.messages_seen && inner.messages_seen.length) {
      label = `<span class="capture-pill capture-progress">capturing</span>`;
    } else {
      label = `<span class="capture-pill capture-progress">waiting</span>`;
    }
    const deauth = s.deauth_used
      ? ` · deauth ×${s.deauth_count || 0}` : "";
    return `${label} ${dots}${deauth}`;
  }

  async function _refreshCaptureStatus(bssid) {
    // Pull the latest status (covers the case where the operator
    // opened the slide-out after a capture was already in flight)
    let s = _captureStatusByBssid[bssid.toLowerCase()];
    if (!s) {
      try {
        const r = await fetch(`/handshakes/status/${encodeURIComponent(bssid)}`);
        if (r.ok) s = await r.json();
      } catch (e) { /* 404 = no capture, expected */ }
    }
    _renderCaptureStatus(bssid, s);
  }

  function _renderCaptureStatus(bssid, s) {
    const el = $("slideout-capture-status");
    const btn = $("slideout-capture");
    if (!el) return;
    if (!s) {
      el.innerHTML = "";
      if (btn) {
        btn.textContent = "Capture handshakes";
        btn.classList.remove("bigbtn-danger");
      }
      return;
    }
    el.innerHTML = _captureSummaryText(s);
    if (btn) {
      btn.textContent = "Stop capture";
      btn.classList.add("bigbtn-danger");
    }
  }

  function showCaptureModal(ap) {
    // Reuse the ethics-confirm shape but inline; the capture flow has
    // its own deauth toggle. Build a one-off modal each time for
    // simplicity — small enough that the construction cost is fine.
    const overlay = document.createElement("div");
    overlay.className = "modal";
    overlay.innerHTML = `
      <div class="modal-backdrop"></div>
      <div class="modal-card">
        <h3>Capture handshakes — ${escapeHtml(ap.essid || ap.bssid)}</h3>
        <p>
          Locks an airodump on <code>wlan-ap</code> to channel
          <strong>${escapeHtml(ap.channel)}</strong>, BSSID
          <code>${escapeHtml(ap.bssid)}</code>, watching for the WPA
          4-way EAPOL exchange (M1/M2/M3/M4).
        </p>
        <label style="display:flex; align-items:center; gap:8px; margin-top:12px;">
          <input type="checkbox" id="capture-deauth-toggle" checked>
          <span>Also send periodic deauth bursts to force fresh
                handshakes (recommended for faster capture)</span>
        </label>
        <p class="muted" style="margin-top:8px; font-size:11px;">
          Deauth frames are <strong>offensive</strong>. Lab equipment only.
        </p>
        <div class="modal-actions">
          <button class="bigbtn actbtn-muted" data-act="cancel">Cancel</button>
          <button class="bigbtn" data-act="start">Start capture</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    const dismiss = () => overlay.remove();
    overlay.querySelector(".modal-backdrop").addEventListener("click", dismiss);
    overlay.querySelector("[data-act=cancel]").addEventListener("click", dismiss);
    overlay.querySelector("[data-act=start]").addEventListener("click", async () => {
      const deauth = overlay.querySelector("#capture-deauth-toggle").checked;
      overlay.querySelector("[data-act=start]").disabled = true;
      overlay.querySelector("[data-act=start]").textContent = "Starting…";
      const res = await postJson("/handshakes/start", {
        bssid:   ap.bssid,
        channel: ap.channel,
        essid:   ap.essid,
        deauth:  deauth,
      });
      dismiss();
      if (res.ok && res.status) {
        _captureInFlightBssid = ap.bssid.toLowerCase();
        _captureStatusByBssid[ap.bssid.toLowerCase()] = res.status;
        _renderCaptureStatus(ap.bssid, res.status);
      }
    });
  }

  async function stopCaptureFromSlideout(bssid) {
    if (!confirm("Stop the running capture? The pcap will be saved.")) return;
    await postJson("/handshakes/stop", { bssid: bssid });
    delete _captureStatusByBssid[bssid.toLowerCase()];
    if (_captureInFlightBssid === bssid.toLowerCase()) {
      _captureInFlightBssid = null;
    }
    _renderCaptureStatus(bssid, null);
  }

  // Capture button rendering is dual-purpose: when there's no active
  // capture for this BSSID it says "Capture handshakes", when there is
  // it switches to a red "Stop capture". We attach the dispatch here.
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("#slideout-capture");
    if (!btn || btn.disabled) return;
    if (!activeDetail || activeKind !== "ap") return;
    const bssid = activeDetail.ap.bssid;
    if (_captureStatusByBssid[bssid.toLowerCase()]) {
      stopCaptureFromSlideout(bssid);
    }
    // Otherwise the renderApSlideout-wired listener fires first and
    // opens the modal — we just intercept the stop case here.
  }, true);

  // ---- Captures tab (persisted handshake list per AP) ----
  function _fmtBytes(n) {
    if (n == null) return "—";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }
  function _fmtTs(unix) {
    if (!unix) return "—";
    const d = new Date(unix * 1000);
    return d.toLocaleString();
  }

  async function _loadCapturesTab(bssid) {
    let captures = [];
    try {
      const r = await fetch(`/handshakes/list?bssid=${encodeURIComponent(bssid)}`);
      if (r.ok) {
        const data = await r.json();
        captures = data.captures || [];
      }
    } catch (e) { /* render empty */ }
    _renderCapturesTab(bssid, captures);
  }

  function _renderCapturesTab(bssid, captures) {
    const body = $("slideout-body");
    if (!body) return;
    if (activeKind !== "ap" || !activeDetail ||
        activeDetail.ap.bssid.toLowerCase() !== bssid.toLowerCase() ||
        activeTab !== "captures") {
      return;  // user switched away while we were loading
    }

    let header = `<h3>Saved captures for this AP</h3>`;
    if (!captures.length) {
      body.innerHTML = header + `<p class="muted">
        No captures yet for ${escapeHtml(bssid)}. Use the "Capture
        handshakes" button below to start one.</p>`;
      return;
    }

    const bulkBtn = `<button class="bigbtn actbtn-muted" id="captures-bulk-delete"
                      style="float:right; font-size:11px; padding:4px 10px;">
                      Delete all
                    </button>`;
    const rows = captures.map((c) => {
      const dots = _fmtMsgDots(c.messages_seen);
      let pill;
      if (c.is_complete)      pill = `<span class="capture-pill capture-complete">complete</span>`;
      else if (c.is_partial)  pill = `<span class="capture-pill capture-partial">partial</span>`;
      else                     pill = `<span class="capture-pill capture-progress">no handshake</span>`;
      const deauthBit = c.deauth_used
        ? ` · deauth ×${c.deauth_count || 0}` : ` · passive`;
      const sizeBit = c.pcap_size_bytes != null
        ? _fmtBytes(c.pcap_size_bytes) : "missing";
      return `<div class="capture-row" data-id="${escapeHtml(c.id)}">
        <div class="capture-row-head">
          ${pill} ${dots}
          <button class="capture-del" data-id="${escapeHtml(c.id)}"
                  title="Delete this capture">×</button>
        </div>
        <div class="capture-row-meta muted">
          ${escapeHtml(_fmtTs(c.started_at))} ·
          ${escapeHtml(c.duration_secs)}s ·
          ${escapeHtml(sizeBit)}${deauthBit}
        </div>
        <div class="capture-row-meta muted" style="font-size:10px;">
          <code>${escapeHtml(c.pcap_relative_path || "")}</code>
        </div>
      </div>`;
    }).join("");
    body.innerHTML = header + bulkBtn + `<div class="capture-list">${rows}</div>`;

    // Per-row delete
    body.querySelectorAll(".capture-del").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const id = btn.dataset.id;
        if (!confirm("Delete this capture? The pcap file will be removed.")) return;
        await postJson("/handshakes/delete", { id });
        _loadCapturesTab(bssid);
      });
    });
    // Bulk delete
    const bulk = $("captures-bulk-delete");
    if (bulk) bulk.addEventListener("click", async () => {
      if (!confirm(
        `Delete ALL ${captures.length} capture(s) for ${bssid}? ` +
        `pcap files will be removed.`
      )) return;
      await postJson("/handshakes/delete-by-bssid", { bssid });
      _loadCapturesTab(bssid);
    });
  }

  // ---- Top-level Captures card (grouped by BSSID, on the Recon page) ----
  async function loadCapturesCard() {
    const body = $("captures-card-body");
    const count = $("captures-count");
    if (!body) return;   // page doesn't have the card (other pages)
    let captures = [];
    try {
      const r = await fetch("/handshakes/list");
      if (r.ok) {
        const data = await r.json();
        captures = data.captures || [];
      }
    } catch (e) { /* render empty */ }

    if (count) count.textContent = String(captures.length);
    if (!captures.length) {
      body.innerHTML = `<p class="muted">No saved captures yet. Use the
        "Capture handshakes" button in an AP's slide-out to record one.</p>`;
      return;
    }

    // Group by BSSID — same AP's captures stay together. Use most-recent
    // essid_at_capture as the group header.
    const groups = {};
    for (const c of captures) {
      const b = (c.bssid || "").toLowerCase();
      if (!groups[b]) groups[b] = [];
      groups[b].push(c);
    }
    const groupKeys = Object.keys(groups).sort((a, b) => {
      // Sort groups by most recent capture in each, desc
      const aMax = Math.max(...groups[a].map((c) => c.started_at || 0));
      const bMax = Math.max(...groups[b].map((c) => c.started_at || 0));
      return bMax - aMax;
    });

    const html = groupKeys.map((b) => {
      const cs = groups[b].slice().sort((x, y) => (y.started_at || 0) - (x.started_at || 0));
      const headerEssid = cs[0].essid_at_capture || "<unknown SSID>";
      const rows = cs.map((c) => {
        const dots = _fmtMsgDots(c.messages_seen);
        let pill;
        if (c.is_complete)      pill = `<span class="capture-pill capture-complete">complete</span>`;
        else if (c.is_partial)  pill = `<span class="capture-pill capture-partial">partial</span>`;
        else                     pill = `<span class="capture-pill capture-progress">no hs</span>`;
        const deauthBit = c.deauth_used ? `deauth ×${c.deauth_count || 0}` : "passive";
        const sizeBit = c.pcap_size_bytes != null ? _fmtBytes(c.pcap_size_bytes) : "missing";
        return `<div class="capture-row" data-id="${escapeHtml(c.id)}">
          <div class="capture-row-head">
            ${pill} ${dots}
            <button class="capture-del" data-act="del" data-id="${escapeHtml(c.id)}"
                    title="Delete this capture">×</button>
          </div>
          <div class="capture-row-meta muted">
            ${escapeHtml(_fmtTs(c.started_at))} · ${escapeHtml(c.duration_secs)}s ·
            ${escapeHtml(sizeBit)} · ${escapeHtml(deauthBit)}
          </div>
        </div>`;
      }).join("");
      return `<div class="capture-group" data-bssid="${escapeHtml(b)}">
        <div class="capture-group-head">
          <strong>${escapeHtml(headerEssid)}</strong>
          <code class="muted">${escapeHtml(b)}</code>
          <span class="muted">· ${cs.length} capture${cs.length === 1 ? "" : "s"}</span>
          <button class="capture-bulk-del" data-act="del-bssid" data-bssid="${escapeHtml(b)}"
                  title="Delete all captures for this AP">Delete all</button>
        </div>
        <div class="capture-group-rows">${rows}</div>
      </div>`;
    }).join("");

    body.innerHTML = html;

    // Single delegated handler for both per-row and per-group deletes
    body.addEventListener("click", async (e) => {
      const delBtn = e.target.closest("[data-act=del]");
      const bulkBtn = e.target.closest("[data-act=del-bssid]");
      if (delBtn) {
        e.stopPropagation();
        if (!confirm("Delete this capture? The pcap file will be removed.")) return;
        await postJson("/handshakes/delete", { id: delBtn.dataset.id });
        loadCapturesCard();
      } else if (bulkBtn) {
        e.stopPropagation();
        const b = bulkBtn.dataset.bssid;
        if (!confirm(`Delete ALL captures for ${b}? pcap files will be removed.`)) return;
        await postJson("/handshakes/delete-by-bssid", { bssid: b });
        loadCapturesCard();
      }
    }, { once: false });
  }

  function attachCaptureSocketHandler() {
    const tryWire = () => {
      const sock = window.pipineapple && window.pipineapple.socket;
      if (!sock) { setTimeout(tryWire, 200); return; }
      sock.on("capture:status", (payload) => {
        if (!payload || !payload.bssid) return;
        const bssid = payload.bssid.toLowerCase();
        if (payload.ended) {
          delete _captureStatusByBssid[bssid];
          if (_captureInFlightBssid === bssid) _captureInFlightBssid = null;
        } else {
          _captureStatusByBssid[bssid] = payload;
          _captureInFlightBssid = bssid;
        }
        // If the slide-out is open on this AP, re-render its status line
        if (activeKind === "ap" && activeDetail &&
            activeDetail.ap.bssid.toLowerCase() === bssid) {
          _renderCaptureStatus(activeDetail.ap.bssid,
                               payload.ended ? null : payload);
          // If the captures tab is active, refresh it so the just-saved
          // capture appears in the list immediately.
          if (payload.ended && activeTab === "captures") {
            _loadCapturesTab(activeDetail.ap.bssid);
          }
        }
        // ALWAYS refresh the top-level Captures card on ended,
        // regardless of which slide-out (if any) is open.
        if (payload.ended) loadCapturesCard();
      });
    };
    tryWire();
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
    attachCaptureSocketHandler();
    loadCapturesCard();

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
