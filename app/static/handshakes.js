/* Handshakes top-level page (Session 08).
 *
 * Loads /handshakes/list on page load, re-renders the table, supports:
 *   - per-row download (raw pcap, .22000 hashcat format)
 *   - per-row delete
 *   - bulk select + bulk delete
 *   - refresh button (manual)
 *   - automatic refresh on capture:status SocketIO events (ended=true)
 *
 * Other recon.js helpers (_fmtBytes, _fmtTs, _fmtMsgDots) aren't shared
 * across files in this codebase, so we re-implement the small ones we
 * need locally. Keeps the page self-contained.
 */

(function () {
  "use strict";
  console.log("[handshakes.js] script loaded, document.readyState=", document.readyState);
  // The bootstrap-or-listen call is at the BOTTOM of this IIFE — see
  // the very last lines. We can't call any helper that uses `$` (or
  // any other const) up here because const declarations have a
  // temporal dead zone; references before the declaration line
  // throw ReferenceError. Putting the bootstrap at the bottom means
  // every const + function is initialised by the time it runs.

  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");

  const selected = new Set();   // capture ids currently checked

  function fmtBytes(n) {
    if (n == null) return "—";
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  }
  function fmtTs(u) {
    if (!u) return "—";
    return new Date(u * 1000).toLocaleString();
  }
  function fmtMsgDots(msgs) {
    const set = new Set(msgs || []);
    return [1, 2, 3, 4].map((n) =>
      set.has(n)
        ? `<span class="msg-dot msg-dot-on" title="M${n} captured">M${n}</span>`
        : `<span class="msg-dot" title="M${n} not seen">M${n}</span>`
    ).join("");
  }

  function init() {
    $("hs-refresh").addEventListener("click", reload);
    $("hs-bulk-delete").addEventListener("click", onBulkDelete);
    $("hs-select-all").addEventListener("change", onSelectAll);

    // Crack panel buttons (only if those elements exist — they do once
    // we've added the new card to the template)
    if ($("crack-refresh")) $("crack-refresh").addEventListener("click", reloadCracks);
    if ($("crack-modal-cancel")) $("crack-modal-cancel").addEventListener("click", closeCrackModal);
    if ($("crack-modal-backdrop")) $("crack-modal-backdrop").addEventListener("click", closeCrackModal);
    if ($("crack-modal-target")) $("crack-modal-target").addEventListener("change", () => {
      $("crack-modal-start").disabled = !$("crack-modal-target").value;
    });
    if ($("crack-modal-start")) $("crack-modal-start").addEventListener("click", onCrackStart);

    // Subscribe to capture + crack lifecycle for live refresh
    const tryWire = () => {
      const sock = window.pipineapple && window.pipineapple.socket;
      if (!sock) { setTimeout(tryWire, 200); return; }
      sock.on("capture:status", (p) => {
        if (p && p.ended) reload();
      });
      // Every crack:status event refreshes the cracks table. Cheap:
      // one /crack/jobs fetch returns the full list and we re-render.
      sock.on("crack:status", () => reloadCracks());
    };
    tryWire();

    reload();
    reloadCracks();
  }

  async function reload() {
    let captures = null;   // null = fetch failed; [] = succeeded but empty
    let errMsg = "";
    try {
      const r = await fetch("/handshakes/list");
      if (!r.ok) {
        errMsg = `HTTP ${r.status} from /handshakes/list`;
      } else {
        const data = await r.json();
        captures = data.captures || [];
      }
    } catch (e) {
      errMsg = `network error fetching /handshakes/list: ${e}`;
    }
    selected.clear();
    updateBulkButton();
    if (captures === null) {
      renderError(errMsg);
    } else {
      render(captures);
    }
  }

  function renderError(msg) {
    const tbody = $("hs-tbody");
    const table = $("hs-table");
    const empty = $("hs-empty");
    const error = $("hs-error");
    if (tbody) tbody.innerHTML = "";
    if (table) table.hidden = true;
    if (empty) empty.hidden = true;
    if (error) {
      error.hidden = false;
      error.textContent = "Could not load captures: " + msg;
    }
    console.error("[handshakes]", msg);
  }

  function render(captures) {
    const tbody = $("hs-tbody");
    const table = $("hs-table");
    const empty = $("hs-empty");
    const error = $("hs-error");
    const count = $("hs-count");

    if (error) error.hidden = true;
    if (count) count.textContent = String(captures.length);

    if (!captures.length) {
      if (tbody) tbody.innerHTML = "";
      if (table) table.hidden = true;
      if (empty) empty.hidden = false;
      return;
    }

    if (table) table.hidden = false;
    if (empty) empty.hidden = true;

    tbody.innerHTML = captures.map((c) => rowHtml(c)).join("");

    // Per-row delete
    tbody.querySelectorAll("[data-act=del]").forEach((b) => {
      b.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm("Delete this capture? The pcap and .22000 cache will be removed.")) return;
        await fetch("/handshakes/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: b.dataset.id }),
        });
        reload();
      });
    });

    // Per-row crack — opens target picker modal
    tbody.querySelectorAll("[data-act=crack]").forEach((b) => {
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        openCrackModal({
          captureId: b.dataset.id,
          essid:     b.dataset.essid || "(unknown SSID)",
          bssid:     b.dataset.bssid || "",
        });
      });
    });

    // Per-row checkboxes
    tbody.querySelectorAll("input[type=checkbox][data-cid]").forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) selected.add(cb.dataset.cid);
        else selected.delete(cb.dataset.cid);
        updateBulkButton();
      });
    });
  }

  function rowHtml(c) {
    let pill;
    if (c.is_complete)      pill = `<span class="capture-pill capture-complete">complete</span>`;
    else if (c.is_partial)  pill = `<span class="capture-pill capture-partial">partial</span>`;
    else                     pill = `<span class="capture-pill capture-progress">no hs</span>`;
    const pmkid = c.has_pmkid
      ? ` <span class="capture-pill capture-complete" title="PMKID captured — crackable on its own">PMKID</span>`
      : "";
    // Source label: Recon Capture for everything we make today. Phase D
    // will add Evil WPA / Evil Enterprise sources; index entries from
    // those will carry source="..." that we'll display verbatim.
    const source = escapeHtml(c.source || "Recon capture");
    const deauthBit = c.deauth_used ? `deauth ×${c.deauth_count || 0}` : "passive";

    return `<tr data-id="${escapeHtml(c.id)}">
      <td><input type="checkbox" data-cid="${escapeHtml(c.id)}"
                 ${selected.has(c.id) ? "checked" : ""}></td>
      <td><span class="muted">${source}</span></td>
      <td><strong>${escapeHtml(c.essid_at_capture || "<unknown>")}</strong></td>
      <td><code>${escapeHtml(c.bssid)}</code></td>
      <td>${escapeHtml(c.channel_at_capture || "—")}</td>
      <td><span class="muted">${escapeHtml(c.tool || "—")}</span></td>
      <td>${pill}${pmkid} ${fmtMsgDots(c.messages_seen)}
        <div class="muted" style="font-size:10px; margin-top:2px;">${deauthBit}</div></td>
      <td class="muted">${escapeHtml(fmtTs(c.started_at))}</td>
      <td>${escapeHtml(c.duration_secs || 0)}s</td>
      <td>${escapeHtml(fmtBytes(c.pcap_size_bytes))}</td>
      <td>
        <a class="actbtn actbtn-muted" style="font-size:11px;"
           href="/handshakes/${encodeURIComponent(c.id)}/download/pcap"
           title="Download raw pcap / pcapng">pcap</a>
        <a class="actbtn actbtn-muted" style="font-size:11px;"
           href="/handshakes/${encodeURIComponent(c.id)}/download/22000"
           title="Convert (cached) and download hashcat .22000 format">.22000</a>
        <button class="actbtn" data-act="crack" data-id="${escapeHtml(c.id)}"
                data-essid="${escapeHtml(c.essid_at_capture || "")}"
                data-bssid="${escapeHtml(c.bssid)}"
                style="font-size:11px;"
                title="Dispatch to a remote and run hashcat"
                ${(c.is_complete || c.has_pmkid) ? "" : "disabled"}>Crack</button>
        <button class="actbtn actbtn-muted" data-act="del" data-id="${escapeHtml(c.id)}"
                style="font-size:11px;" title="Delete">×</button>
      </td>
    </tr>`;
  }

  function onSelectAll(e) {
    const tbody = $("hs-tbody");
    if (!tbody) return;
    const cbs = tbody.querySelectorAll("input[type=checkbox][data-cid]");
    cbs.forEach((cb) => {
      cb.checked = e.target.checked;
      if (e.target.checked) selected.add(cb.dataset.cid);
      else selected.delete(cb.dataset.cid);
    });
    updateBulkButton();
  }

  function updateBulkButton() {
    const btn = $("hs-bulk-delete");
    if (!btn) return;
    btn.disabled = selected.size === 0;
    btn.textContent = selected.size === 0
      ? "Delete selected"
      : `Delete ${selected.size} selected`;
  }

  async function onBulkDelete() {
    if (selected.size === 0) return;
    if (!confirm(`Delete ${selected.size} capture(s)? Their pcap and .22000 cache files will be removed.`)) return;
    // Sequential — backend is fast, no need for parallel
    for (const id of Array.from(selected)) {
      await fetch("/handshakes/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
    }
    selected.clear();
    reload();
  }

  // ---- Crack modal + crack-jobs table (Session 09) ----
  // Captured capture context while the modal is open. Cleared on close.
  let crackModalContext = null;

  async function openCrackModal(ctx) {
    crackModalContext = ctx;
    const modal = $("crack-modal");
    if (!modal) return;
    $("crack-modal-target-cap").textContent = `${ctx.essid} (${ctx.bssid})`;
    $("crack-modal-msg").textContent = "";
    $("crack-modal-start").disabled = true;
    modal.hidden = false;

    // Populate target dropdown
    const sel = $("crack-modal-target");
    sel.innerHTML = `<option value="">— loading targets… —</option>`;
    try {
      const r = await fetch("/crack/targets");
      const data = await r.json();
      const targets = data.targets || [];
      if (!targets.length) {
        sel.innerHTML = `<option value="">— no targets configured —</option>`;
        $("crack-modal-msg").innerHTML =
          `Add one in <a href="/settings">Settings → Crack Targets</a> first.`;
        return;
      }
      sel.innerHTML = `<option value="">— pick a target —</option>` +
        targets.map(t => {
          const tested = (t.last_test_ok === true)  ? " ✓"
                     : (t.last_test_ok === false) ? " (test failed)"
                     :                              " (untested)";
          return `<option value="${escapeHtml(t.id)}">${escapeHtml(t.name)} — ${escapeHtml(t.user)}@${escapeHtml(t.host)}${tested}</option>`;
        }).join("");
    } catch (e) {
      sel.innerHTML = `<option value="">— failed to load —</option>`;
      $("crack-modal-msg").textContent = "fetch failed: " + e;
    }
  }

  function closeCrackModal() {
    crackModalContext = null;
    const modal = $("crack-modal");
    if (modal) modal.hidden = true;
  }

  async function onCrackStart() {
    if (!crackModalContext) return;
    const targetId = $("crack-modal-target").value;
    if (!targetId) return;
    const btn = $("crack-modal-start");
    btn.disabled = true;
    btn.textContent = "Dispatching…";
    $("crack-modal-msg").textContent = "scp + ssh hashcat launching…";
    try {
      const r = await fetch("/crack/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          capture_id: crackModalContext.captureId,
          target_id:  targetId,
        }),
      });
      const data = await r.json();
      if (data.ok) {
        closeCrackModal();
        reloadCracks();
      } else {
        $("crack-modal-msg").textContent =
          "Failed: " + ((data.messages || []).join("; ") || "unknown");
      }
    } catch (e) {
      $("crack-modal-msg").textContent = "Network error: " + e;
    } finally {
      btn.disabled = false;
      btn.textContent = "Start crack";
    }
  }

  async function reloadCracks() {
    const tbody = $("crack-tbody");
    if (!tbody) return;
    try {
      const r = await fetch("/crack/jobs");
      const data = await r.json();
      renderCracks(data.jobs || []);
    } catch (e) {
      console.error("[handshakes] reloadCracks failed:", e);
    }
  }

  function renderCracks(jobs) {
    const tbody = $("crack-tbody");
    const table = $("crack-table");
    const empty = $("crack-empty");
    const count = $("crack-count");
    if (count) count.textContent = String(jobs.length);
    if (!jobs.length) {
      if (tbody) tbody.innerHTML = "";
      if (table) table.hidden = true;
      if (empty) empty.hidden = false;
      return;
    }
    if (table) table.hidden = false;
    if (empty) empty.hidden = true;

    tbody.innerHTML = jobs.map(j => {
      let statusPill;
      switch (j.status) {
        case "running":   statusPill = `<span class="capture-pill capture-progress">running</span>`; break;
        case "done":      statusPill = `<span class="capture-pill capture-complete">cracked</span>`; break;
        case "exhausted": statusPill = `<span class="capture-pill capture-partial">exhausted</span>`; break;
        case "stopped":   statusPill = `<span class="capture-pill capture-partial">stopped</span>`; break;
        case "failed":    statusPill = `<span class="capture-pill capture-progress" style="background:#a33;">failed</span>`; break;
        case "queued":    statusPill = `<span class="capture-pill capture-progress">queued</span>`; break;
        default:          statusPill = `<span class="muted">${escapeHtml(j.status || "?")}</span>`;
      }
      const speed = j.last_speed_hs != null ? fmtSpeed(j.last_speed_hs) : "—";
      const prog  = j.last_percent != null ? `${j.last_percent.toFixed(1)}%` : "—";
      const eta   = j.last_eta || "—";
      const result = j.cracked_password
        ? `<code style="font-weight:bold; color:#6fcf6f;">${escapeHtml(j.cracked_password)}</code>`
        : (j.status === "exhausted" ? `<span class="muted">wordlist exhausted</span>` : `<span class="muted">—</span>`);
      const stopBtn = j.status === "running"
        ? `<button class="actbtn actbtn-muted crack-stop" data-id="${escapeHtml(j.id)}" style="font-size:11px;">Stop</button>`
        : "";
      return `<tr data-id="${escapeHtml(j.id)}">
        <td class="muted" style="font-size:11px;">${escapeHtml(fmtTs(j.started_at))}</td>
        <td><strong>${escapeHtml(j.capture_essid || "<unknown>")}</strong>
            <div class="muted" style="font-size:10px;"><code>${escapeHtml(j.capture_bssid || "")}</code></div></td>
        <td>${escapeHtml(j.target_name || "")}
            <div class="muted" style="font-size:10px;">${escapeHtml(j.target_host || "")}</div></td>
        <td>${statusPill}</td>
        <td>${escapeHtml(speed)}</td>
        <td>${escapeHtml(prog)}</td>
        <td class="muted" style="font-size:11px;">${escapeHtml(eta)}</td>
        <td>${result}</td>
        <td>${stopBtn}</td>
      </tr>`;
    }).join("");

    tbody.querySelectorAll(".crack-stop").forEach(b => {
      b.addEventListener("click", async () => {
        if (!confirm("Stop this crack job?")) return;
        b.disabled = true;
        await fetch(`/crack/${encodeURIComponent(b.dataset.id)}/stop`, { method: "POST" });
        reloadCracks();
      });
    });
  }

  function fmtSpeed(hs) {
    if (hs == null) return "—";
    if (hs >= 1e9) return (hs / 1e9).toFixed(2) + " GH/s";
    if (hs >= 1e6) return (hs / 1e6).toFixed(2) + " MH/s";
    if (hs >= 1e3) return (hs / 1e3).toFixed(2) + " kH/s";
    return hs + " H/s";
  }

  // ---- Bootstrap (kept at the bottom so it runs AFTER all `const`
  // declarations above are initialised — avoids the TDZ ReferenceError
  // that bit us on `defer` script execution where readyState was
  // already past "loading"). ----
  function bootstrap() {
    if (!document.getElementById("hs-tbody")) {
      console.log("[handshakes.js] hs-tbody not found, skipping init (not on Handshakes page)");
      return;
    }
    console.log("[handshakes.js] init firing");
    init();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();   // DOM already parsed
  }
})();
