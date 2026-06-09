/* Campaigns page (Session 14).
 *
 * Run/Reports tabs. Template cards each Run a campaign (recon / passive /
 * active); the live status card tracks the running campaign + its step log
 * via the campaign:status SocketIO event. Reports tab lists past runs with
 * JSON/HTML download links.
 *
 * Bootstrap-at-bottom (TDZ-safe under defer), same pattern as the other pages.
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const escapeHtml = (s) =>
    String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  const fmtTs = (u) => (u ? new Date(u * 1000).toLocaleString() : "—");
  function fmtDur(secs) {
    secs = Math.max(0, Math.floor(secs || 0));
    const m = Math.floor(secs / 60), s = secs % 60;
    return m ? `${m}m ${s}s` : `${s}s`;
  }

  async function postJSON(url, body = {}) {
    const r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return r.json();
  }

  function activateTab(name) {
    document.querySelectorAll(".tab").forEach((b) =>
      b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab-panel").forEach((p) =>
      p.hidden = !p.id.endsWith(`-${name}`));
  }

  let _elapsedTimer = null;

  function renderStatus(st) {
    const card = $("camp-running-card");
    const run = st && st.run;
    const running = st && st.running && run && run.status === "running";
    if (card) card.hidden = !running;
    // Disable all Run buttons while a campaign is active.
    document.querySelectorAll(".camp-run").forEach((b) => { b.disabled = !!st.running; });
    if (!running) {
      if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
      return;
    }
    if ($("camp-run-name")) $("camp-run-name").textContent = run.template_name || run.template;
    if ($("camp-run-window")) {
      $("camp-run-window").textContent = run.duration_secs
        ? fmtDur(run.duration_secs) : "until stopped";
    }
    if ($("camp-run-steps")) {
      $("camp-run-steps").innerHTML = (run.steps || []).map((s) =>
        `<div><span class="muted">${escapeHtml(new Date((s.ts||0)*1000).toLocaleTimeString())}</span> ${escapeHtml(s.msg)}</div>`
      ).join("") || '<div class="muted">starting…</div>';
      $("camp-run-steps").scrollTop = $("camp-run-steps").scrollHeight;
    }
    const startedAt = run.started_at;
    const tick = () => {
      if ($("camp-run-elapsed") && startedAt)
        $("camp-run-elapsed").textContent = fmtDur(Date.now() / 1000 - startedAt);
    };
    tick();
    if (!_elapsedTimer) _elapsedTimer = setInterval(tick, 1000);
  }

  async function reloadStatus() {
    try { renderStatus(await (await fetch("/campaigns/status")).json()); }
    catch (e) { /* ignore */ }
  }

  async function onRun(tplCard, tpl) {
    const dur = tplCard.querySelector(".camp-until-stopped")?.checked
      ? 0 : parseInt(tplCard.querySelector(".camp-duration")?.value || "600", 10);
    const body = { template: tpl, duration_secs: dur };
    const confirmEl = tplCard.querySelector(".camp-confirm");
    const targetEl = tplCard.querySelector(".camp-target");
    if (confirmEl) body.confirm = confirmEl.value.trim();
    if (targetEl && targetEl.value.trim()) body.target_bssid = targetEl.value.trim();
    const res = await postJSON("/campaigns/start", body);
    if (!res.ok) { alert("Could not start: " + (res.msg || "unknown")); return; }
    if (res.status) renderStatus(res.status);
    if (confirmEl) confirmEl.value = "";
  }

  async function onStop() {
    if (!confirm("Stop the running campaign? The report will still be written.")) return;
    const res = await postJSON("/campaigns/stop");
    if (res.status) renderStatus(res.status);
  }

  async function reloadReports() {
    const tbody = $("camp-reports-tbody");
    if (!tbody) return;
    let reports = [];
    try { reports = (await (await fetch("/campaigns/reports")).json()).reports || []; }
    catch (e) { /* ignore */ }
    if ($("camp-reports-count")) $("camp-reports-count").textContent = String(reports.length);
    if (!reports.length) {
      tbody.innerHTML = `<tr><td colspan="8" class="muted" style="padding:12px;">No reports yet. Run a campaign.</td></tr>`;
      return;
    }
    tbody.innerHTML = reports.map((r) => {
      const s = r.summary || {};
      return `<tr>
        <td class="muted">${escapeHtml(fmtTs(r.started_at))}</td>
        <td>${escapeHtml(r.template_name || r.template)}</td>
        <td>${escapeHtml(fmtDur(r.duration_secs))}${r.stopped_early ? ' <span class="muted">(early)</span>' : ''}</td>
        <td>${s.access_points ?? 0}</td>
        <td>${s.clients ?? 0}</td>
        <td>${s.handshakes_captured ?? 0}</td>
        <td>${s.credentials_harvested ?? 0}</td>
        <td>
          <a class="actbtn actbtn-muted" style="font-size:11px;" target="_blank"
             href="/campaigns/reports/${encodeURIComponent(r.id)}/html">HTML</a>
          <a class="actbtn actbtn-muted" style="font-size:11px;"
             href="/campaigns/reports/${encodeURIComponent(r.id)}/json">JSON</a>
        </td>
      </tr>`;
    }).join("");
  }

  function init() {
    if (!document.querySelector(".camp-template")) return; // not on Campaigns page

    document.querySelectorAll(".tab").forEach((b) =>
      b.addEventListener("click", () => activateTab(b.dataset.tab)));

    document.querySelectorAll(".camp-run").forEach((btn) => {
      btn.addEventListener("click", () => {
        const card = btn.closest(".camp-template");
        if (card) onRun(card, btn.dataset.tpl);
      });
    });
    if ($("camp-stop")) $("camp-stop").addEventListener("click", onStop);
    if ($("camp-reports-refresh")) $("camp-reports-refresh").addEventListener("click", reloadReports);

    reloadStatus();
    reloadReports();

    const tryWire = () => {
      const sock = window.pipineapple && window.pipineapple.socket;
      if (!sock) { setTimeout(tryWire, 200); return; }
      sock.on("campaign:status", (st) => {
        renderStatus(st);
        if (st && st.run && st.run.status === "done") reloadReports();
      });
    };
    tryWire();

    // Safety poll (covers a missed socket event / report write).
    setInterval(() => { reloadStatus(); }, 5000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
