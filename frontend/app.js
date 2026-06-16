// UniFi AI Config Auditor — frontend controller
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const api = async (url, opts = {}) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch {}
    const err = new Error(detail); err.status = r.status; throw err;
  }
  return r.status === 204 ? null : r.json();
};

// POST that transparently handles the controller's MFA challenge (HTTP 428):
// on challenge it prompts for the 6-digit code and retries with it included.
async function apiMfa(url) {
  let body = {};
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return await api(url, { method: "POST", body: JSON.stringify(body) });
    } catch (e) {
      if (e.status !== 428) throw e;
      const code = prompt(`${e.message}\n\nEnter the current MFA code:`);
      if (!code) throw new Error("MFA cancelled.");
      body = { mfa_token: code.trim() };
    }
  }
  throw new Error("MFA failed after multiple attempts.");
}

let currentResult = null;

// ---------- navigation ----------
function showView(name) {
  $$(".view").forEach((v) => v.classList.remove("active"));
  $(`#view-${name}`).classList.add("active");
  $$("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
}
document.addEventListener("click", (e) => {
  const v = e.target.dataset?.view;
  if (v) { e.preventDefault(); showView(v); if (v === "settings") loadSettings(); }
});

// ---------- settings ----------
async function loadSettings() {
  const s = await api("/api/settings");
  $("#u_host").value = s.unifi.host; $("#u_port").value = s.unifi.port;
  $("#u_user").value = s.unifi.username; $("#u_pass").value = s.unifi.password || "";
  $("#u_site").value = s.unifi.site; $("#u_unifios").checked = s.unifi.is_unifi_os;
  $("#u_verify").checked = s.unifi.verify_ssl;
  $("#l_provider").value = s.llm.provider; $("#l_model").value = s.llm.model;
  $("#l_key").value = s.llm.api_key || ""; $("#l_base").value = s.llm.base_url || "";
  $("#l_temp").value = s.llm.temperature; $("#l_maxtok").value = s.llm.max_output_tokens;
  updateProviderUI();
  refreshModels();
}

function collectSettings() {
  return {
    unifi: {
      host: $("#u_host").value, port: Number($("#u_port").value),
      username: $("#u_user").value, password: $("#u_pass").value,
      site: $("#u_site").value, is_unifi_os: $("#u_unifios").checked,
      verify_ssl: $("#u_verify").checked,
    },
    llm: {
      provider: $("#l_provider").value, model: $("#l_model").value,
      api_key: $("#l_key").value, base_url: $("#l_base").value,
      temperature: Number($("#l_temp").value), max_output_tokens: Number($("#l_maxtok").value),
    },
  };
}

function updateProviderUI() {
  const local = ["ollama", "lmstudio"].includes($("#l_provider").value);
  $("#apiKeyWrap").style.opacity = local ? ".5" : "1";
}

async function refreshModels() {
  const provider = $("#l_provider").value;
  const local = ["ollama", "lmstudio"].includes(provider);
  $("#modelStatus").textContent = "Detecting…"; $("#modelStatus").className = "status";
  try {
    const r = await api("/api/llm/models", {
      method: "POST",
      body: JSON.stringify({ provider, base_url: $("#l_base").value }),
    });
    // For local runtimes, once we've actually detected models, show ONLY those
    // (not the generic suggestions, which are localhost placeholders) so switching
    // URLs replaces the list rather than accumulating stale entries.
    const options = local
      ? (r.installed.length ? r.installed : r.suggested)
      : [...r.installed, ...r.suggested];
    const dl = $("#modelList"); dl.innerHTML = "";
    options.forEach((m) => {
      const o = document.createElement("option"); o.value = m; dl.appendChild(o);
    });
    // Nudge the input to drop its cached suggestion popup so the new list shows.
    const inp = $("#l_model");
    inp.removeAttribute("list"); void inp.offsetWidth; inp.setAttribute("list", "modelList");
    $("#l_base").placeholder = r.default_base;
    // If the entered URL was repaired (e.g. slash-before-port), reflect the fix.
    if (r.normalized_base && $("#l_base").value && r.normalized_base !== $("#l_base").value.replace(/\/+$/, "")) {
      $("#l_base").value = r.normalized_base;
    }
    if (r.installed.length) {
      $("#modelStatus").textContent = `${r.installed.length} local model(s) detected`;
      $("#modelStatus").className = "status ok";
    } else if (local && r.error) {
      $("#modelStatus").textContent = r.error;
      $("#modelStatus").className = "status err";
    } else if (local) {
      $("#modelStatus").textContent = "No models found at that address.";
      $("#modelStatus").className = "status err";
    } else {
      $("#modelStatus").textContent = "";
    }
  } catch (e) { $("#modelStatus").textContent = e.message; $("#modelStatus").className = "status err"; }
}

$("#l_provider").addEventListener("change", () => { updateProviderUI(); refreshModels(); });
$("#refreshModels").addEventListener("click", refreshModels);

$("#saveBtn").addEventListener("click", async () => {
  $("#saveStatus").textContent = "Saving…"; $("#saveStatus").className = "status";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify(collectSettings()) });
    $("#saveStatus").textContent = "Saved ✓"; $("#saveStatus").className = "status ok";
  } catch (e) { $("#saveStatus").textContent = e.message; $("#saveStatus").className = "status err"; }
});

$("#testBtn").addEventListener("click", async () => {
  const btn = $("#testBtn");
  btn.disabled = true;
  $("#testStatus").innerHTML = '<span class="spinner"></span> Saving settings…';
  $("#testStatus").className = "status";
  try {
    if (!$("#u_user").value || !$("#u_pass").value) {
      throw new Error("Enter a username and password first.");
    }
    await api("/api/settings", { method: "POST", body: JSON.stringify(collectSettings()) });
    $("#testStatus").innerHTML = '<span class="spinner"></span> Connecting to controller…';
    const r = await apiMfa("/api/unifi/test");
    $("#testStatus").textContent = `Connected ✓  Sites: ${r.sites.join(", ")}`;
    $("#testStatus").className = "status ok";
  } catch (e) {
    $("#testStatus").textContent = e.message || "Connection failed.";
    $("#testStatus").className = "status err";
  } finally {
    btn.disabled = false;
  }
});

function setStatus(html, cls = "") { const s = $("#status"); s.innerHTML = html; s.className = "status " + cls; }

// Stream newline-delimited JSON from a POST endpoint, invoking onEvent per line.
async function streamNdjson(url, body, onEvent) {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) {
    let detail = r.statusText; try { detail = (await r.json()).detail || detail; } catch {}
    const e = new Error(detail); e.status = r.status; throw e;
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
  if (buf.trim()) onEvent(JSON.parse(buf.trim()));
}

// ---------- STEP 1: collect ----------
$("#collectBtn").addEventListener("click", () => runCollect());

async function runCollect(mfaToken) {
  const btn = $("#collectBtn"); btn.disabled = true;
  const grid = $("#collectProgress"); grid.classList.remove("hidden");
  $("#collectStatus").textContent = ""; $("#collectStatus").className = "status";
  const steps = {};
  let needMfa = false, finished = false, errored = false;
  try {
    await streamNdjson("/api/collect", mfaToken ? { mfa_token: mfaToken } : {}, (ev) => {
      if (ev.type === "mfa_required") {
        needMfa = true;
        $("#collectStatus").textContent = ev.message;
      } else if (ev.type === "error") {
        errored = true;
        $("#collectStatus").textContent = ev.message; $("#collectStatus").className = "status err";
      } else if (ev.type === "start") {
        grid.innerHTML = "";
      } else if (ev.type === "step_start") {
        const el = document.createElement("div");
        el.className = "pstep active"; el.id = "ps_" + ev.key;
        el.innerHTML = `<span class="ico"><span class="spinner"></span></span>
          <span class="lbl">${esc(ev.label)}</span><span class="cnt"></span>`;
        grid.appendChild(el); steps[ev.key] = el;
        $("#collectStatus").innerHTML =
          `<span class="spinner"></span> Collecting ${ev.index}/${ev.total}…`;
      } else if (ev.type === "step_done") {
        const el = steps[ev.key]; if (!el) return;
        const ok = ev.ok && ev.count >= 0;
        const empty = ev.count === 0;
        el.className = "pstep " + (!ok ? "err" : empty ? "warn" : "done");
        el.querySelector(".ico").textContent = !ok ? "✕" : "✓";
        el.querySelector(".cnt").textContent =
          ev.count === -1 ? "n/a" : `${ev.count}`;
      } else if (ev.type === "saving") {
        $("#collectStatus").innerHTML = '<span class="spinner"></span> Saving snapshot…';
      } else if (ev.type === "snapshot") {
        finished = true;
        $("#collectStatus").textContent =
          `Collected ✓  ${ev.snapshot.total_objects ?? ""} objects saved as a snapshot.`;
        $("#collectStatus").className = "status ok";
        loadSnapshots(ev.snapshot.id);
      }
    });
    if (needMfa) {
      const code = prompt("Enter the current 6-digit MFA code:");
      if (code) { btn.disabled = false; return runCollect(code.trim()); }
    }
    // The stream ended without a completion/error/MFA signal — don't leave the UI
    // frozen on "Collecting N/N"; tell the user what happened.
    if (!finished && !errored && !needMfa) {
      $("#collectStatus").textContent =
        "Collection ended unexpectedly before the snapshot was saved. Check the controller connection and try again.";
      $("#collectStatus").className = "status err";
    }
  } catch (e) {
    $("#collectStatus").textContent = e.message; $("#collectStatus").className = "status err";
  } finally { btn.disabled = false; }
}

async function loadSnapshots(selectId) {
  const snaps = await api("/api/snapshots");
  const dataSel = $("#dataSnapshotSelect");
  dataSel.innerHTML = "";
  if (!snaps.length) {
    $("#collectedData").classList.add("hidden");
    return;
  }
  snaps.forEach((s) => {
    const label = `${new Date(s.created_at).toLocaleString()} · ${s.total_objects} objects`;
    const o = document.createElement("option"); o.value = s.id; o.textContent = label; dataSel.appendChild(o);
  });
  const chosen = selectId || snaps[0].id;
  dataSel.value = chosen;
  $("#collectedData").classList.remove("hidden");
  loadSections(chosen);
}

// ---------- collected-data browser ----------
let currentDataSnapshot = null;
let openSection = null; // section currently shown in the data modal

const redactOn = () => $("#redactToggle").checked;
const redactQS = () => (redactOn() ? "redact=true" : "redact=false");

$("#dataSnapshotSelect").addEventListener("change", (e) => loadSections(e.target.value));
$("#downloadAllBtn").addEventListener("click", () => {
  if (currentDataSnapshot)
    window.location = `/api/snapshots/${currentDataSnapshot}/download?${redactQS()}`;
});
// Re-render the open viewer when the toggle flips so it reflects the new setting.
$("#redactToggle").addEventListener("change", () => {
  if (openSection && !$("#dataModal").classList.contains("hidden"))
    browseSection(currentDataSnapshot, openSection);
});

// Section selection for analysis (keys), plus a cache of section metadata for the
// current snapshot so we can render selection chips with labels/groups.
let sectionsCache = [];
const selectedSections = new Set();

async function loadSections(snapshotId) {
  currentDataSnapshot = snapshotId;
  const box = $("#sectionList"); box.innerHTML = "";
  let sections;
  try { sections = await api(`/api/snapshots/${snapshotId}/sections`); }
  catch (e) { box.innerHTML = `<div class="muted">${esc(e.message)}</div>`; return; }
  sectionsCache = sections;
  // Default: select every section that actually returned data (skip errored/empty).
  selectedSections.clear();
  sections.forEach((s) => { if (!s.error && s.count > 0) selectedSections.add(s.key); });

  sections.forEach((s) => {
    const row = document.createElement("div");
    const cls = s.error ? "err" : s.count === 0 ? "noval" : "";
    row.className = "section-row " + cls;
    row.dataset.key = s.key;
    const cnt = s.error ? "n/a" : `${s.count}`;
    row.innerHTML = `
      <span class="grp ${s.group}"></span>
      <span class="lbl">${esc(s.label)}</span>
      <span class="cnt">${cnt}</span>
      <span class="acts">
        <button data-act="browse">Browse</button>
        <button data-act="copy">Copy</button>
        <button data-act="download">Download</button>
        <button data-act="add" class="add">Add</button>
      </span>`;
    row.querySelector('[data-act="browse"]').addEventListener("click", () => browseSection(snapshotId, s));
    row.querySelector('[data-act="copy"]').addEventListener("click", (e) => copySection(snapshotId, s, e.target));
    row.querySelector('[data-act="download"]').addEventListener("click", () =>
      window.location = `/api/snapshots/${snapshotId}/section/${s.key}?download=true&${redactQS()}`);
    row.querySelector('[data-act="add"]').addEventListener("click", () => toggleSection(s.key));
    box.appendChild(row);
  });
  renderSelection();
}

const sectionMeta = (key) => sectionsCache.find((s) => s.key === key);

function toggleSection(key) {
  if (selectedSections.has(key)) selectedSections.delete(key);
  else selectedSections.add(key);
  renderSelection();
}

function renderSelection() {
  // Update the Add buttons in the section list.
  $$("#sectionList .section-row").forEach((row) => {
    const key = row.dataset.key;
    const btn = row.querySelector('[data-act="add"]');
    if (!btn) return;
    const on = selectedSections.has(key);
    btn.textContent = on ? "Added ✓" : "Add";
    btn.classList.toggle("added", on);
  });
  // Render the selection chips in the Analyze panel.
  const box = $("#analyzeSelection"); box.innerHTML = "";
  const keys = sectionsCache.map((s) => s.key).filter((k) => selectedSections.has(k));
  $("#selCount").textContent = keys.length;
  if (!keys.length) {
    box.innerHTML = '<span class="empty-sel">No sections selected — add sections from the Collect panel above.</span>';
  } else {
    keys.forEach((k) => {
      const s = sectionMeta(k); if (!s) return;
      const chip = document.createElement("span");
      chip.className = "sel-chip";
      chip.innerHTML = `<span class="dot ${s.group}"></span>${esc(s.label)} <button title="Remove">✕</button>`;
      chip.querySelector("button").addEventListener("click", () => toggleSection(k));
      box.appendChild(chip);
    });
  }
  $("#analyzeBtn").disabled = keys.length === 0;
}

$("#addAllBtn").addEventListener("click", () => {
  sectionsCache.forEach((s) => { if (!s.error) selectedSections.add(s.key); });
  renderSelection();
});
$("#removeAllBtn").addEventListener("click", () => { selectedSections.clear(); renderSelection(); });

async function fetchSection(snapshotId, key) {
  const r = await api(`/api/snapshots/${snapshotId}/section/${key}?${redactQS()}`);
  return r.data;
}

async function browseSection(snapshotId, s) {
  openSection = s;
  const redacted = redactOn();
  $("#dataTitle").textContent = s.label;
  $("#dataMeta").textContent =
    `${s.group} · ${s.error ? "error" : s.count + " object(s)"} · ${s.key}` +
    (redacted ? " · secrets redacted" : "");
  $("#dataJson").textContent = "Loading…";
  $("#dataModal").classList.remove("hidden");
  let data;
  try { data = await fetchSection(snapshotId, s.key); }
  catch (e) { $("#dataJson").textContent = e.message; return; }
  const text = JSON.stringify(data, null, 2);
  $("#dataJson").textContent = text;
  $("#dataCopyBtn").onclick = () => copyText(text, $("#dataCopyBtn"));
  $("#dataDownloadBtn").onclick = () =>
    window.location = `/api/snapshots/${snapshotId}/section/${s.key}?download=true&${redactQS()}`;
}

async function copySection(snapshotId, s, btn) {
  try {
    const data = await fetchSection(snapshotId, s.key);
    await copyText(JSON.stringify(data, null, 2), btn);
  } catch (e) { btn.textContent = "Failed"; setTimeout(() => (btn.textContent = "Copy"), 1500); }
}

async function copyText(text, btn) {
  const label = btn.textContent;
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = "Copied ✓";
  } catch {
    // Fallback for environments without clipboard API.
    const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta);
    ta.select(); try { document.execCommand("copy"); btn.textContent = "Copied ✓"; }
    catch { btn.textContent = "Failed"; } document.body.removeChild(ta);
  }
  setTimeout(() => (btn.textContent = label), 1500);
}

const closeDataModal = () => { $("#dataModal").classList.add("hidden"); openSection = null; };
$("#dataModalClose").addEventListener("click", closeDataModal);
$("#dataModal").addEventListener("click", (e) => { if (e.target.id === "dataModal") closeDataModal(); });

// ---------- STEP 2: analyze ----------
$("#analyzeBtn").addEventListener("click", async () => {
  const id = currentDataSnapshot;
  if (!id) { $("#analyzeStatus").textContent = "Collect a snapshot first."; $("#analyzeStatus").className = "status err"; return; }
  const selected = [...selectedSections];
  if (!selected.length) {
    $("#analyzeStatus").textContent = "Add at least one section to analyze."; $("#analyzeStatus").className = "status err"; return;
  }
  const btn = $("#analyzeBtn"); btn.disabled = true;
  const list = $("#analyzeProgress"); list.classList.remove("hidden"); list.innerHTML = "";
  $("#analyzeStatus").innerHTML = '<span class="spinner"></span> Analyzing…';
  $("#analyzeStatus").className = "status";
  const chunks = {};
  try {
    await streamNdjson(`/api/analyze/${id}`, { selected }, (ev) => {
      if (ev.type === "chunk_start") {
        const el = document.createElement("div");
        el.className = "chunk active"; el.id = "ck_" + ev.key;
        el.innerHTML = `<span class="ico"><span class="spinner"></span></span>
          <span class="lbl">${esc(ev.label)}</span><span class="res"></span>`;
        list.appendChild(el); chunks[ev.key] = el;
        $("#analyzeStatus").innerHTML =
          `<span class="spinner"></span> Analyzing ${ev.index}/${ev.total}: ${esc(ev.label)}…`;
      } else if (ev.type === "chunk_done") {
        const el = chunks[ev.key]; if (!el) return;
        el.className = "chunk done"; el.querySelector(".ico").textContent = "✓";
        el.querySelector(".res").textContent =
          ev.found ? `${ev.found} issue(s)` : "no issues";
      } else if (ev.type === "chunk_error") {
        const el = chunks[ev.key]; if (!el) return;
        el.className = "chunk err"; el.querySelector(".ico").textContent = "✕";
        el.querySelector(".res").textContent = (ev.error || "").slice(0, 80);
      } else if (ev.type === "error") {
        $("#analyzeStatus").textContent = ev.message; $("#analyzeStatus").className = "status err";
      } else if (ev.type === "result") {
        $("#analyzeStatus").textContent = `Done — ${ev.result.issues.length} issue(s) found ✓`;
        $("#analyzeStatus").className = "status ok";
        loadResultList().then(() => { $("#resultSelect").value = ev.result.id; });
        renderResult(ev.result);
      }
    });
  } catch (e) {
    $("#analyzeStatus").textContent = e.message; $("#analyzeStatus").className = "status err";
  } finally { btn.disabled = false; }
});

async function loadResultList() {
  const list = await api("/api/results");
  const sel = $("#resultSelect"); sel.innerHTML = "";
  if (!list.length) { $("#empty").classList.remove("hidden"); return; }
  list.forEach((r) => {
    const o = document.createElement("option"); o.value = r.id;
    const c = r.counts;
    o.textContent = `${new Date(r.created_at).toLocaleString()} · ${r.provider}/${r.model} · ` +
      `${c.Critical}C ${c.High}H ${c.Medium}M ${c.Low}L`;
    sel.appendChild(o);
  });
  return list;
}

$("#resultSelect").addEventListener("change", async (e) => {
  const r = await api(`/api/results/${e.target.value}`);
  renderResult(r);
});

function renderResult(result) {
  currentResult = result;
  $("#empty").classList.add("hidden");
  const counts = { Critical: 0, High: 0, Medium: 0, Low: 0 };
  result.issues.forEach((i) => counts[i.severity]++);
  const bar = $("#summaryBar"); bar.classList.remove("hidden"); bar.innerHTML = "";
  ["Critical", "High", "Medium", "Low"].forEach((sev) => {
    const c = document.createElement("div"); c.className = "chip";
    c.innerHTML = `<span class="dot ${sev}"></span>${counts[sev]} ${sev}`;
    bar.appendChild(c);
  });
  $("#summaryText").textContent = result.summary || "";
  renderIssues();
}

function renderIssues() {
  if (!currentResult) return;
  const sevOn = new Set([...$$(".sev-filter:checked")].map((c) => c.value));
  const hideResolved = $("#hideResolved").checked;
  const box = $("#issues"); box.innerHTML = "";
  const visible = currentResult.issues.filter((i) =>
    sevOn.has(i.severity) && !(hideResolved && i.disposition !== "Open"));
  if (!visible.length) { box.innerHTML = '<div class="empty">No issues match the current filters.</div>'; return; }
  visible.forEach((i) => {
    const el = document.createElement("div");
    el.className = `issue ${i.severity}` + (i.disposition !== "Open" ? " resolved" : "");
    const disp = i.disposition !== "Open" ? `<span class="disp ${i.disposition}">● ${i.disposition}</span>` : "";
    el.innerHTML = `
      <div class="issue-head">
        <span class="badge ${i.severity}">${i.severity}</span>
        <span class="issue-title">${esc(i.title)}</span>
        <span class="badge cat">${esc(i.category)}</span>
        ${disp}
      </div>
      <div class="issue-desc">${esc(i.description).slice(0, 220)}</div>`;
    el.addEventListener("click", () => openModal(i));
    box.appendChild(el);
  });
}
$$(".sev-filter").forEach((c) => c.addEventListener("change", renderIssues));
$("#hideResolved").addEventListener("change", renderIssues);

// ---------- issue detail / triage ----------
function openModal(issue) {
  const rem = issue.remediation || {};
  const steps = (rem.manual_steps || []).map((s) => `<li>${esc(s)}</li>`).join("");
  const objs = (issue.affected_objects || []).map((o) => `<span class="tag">${esc(o)}</span>`).join("");
  const auto = rem.automation;
  $("#modalContent").innerHTML = `
    <h3><span class="badge ${issue.severity}">${issue.severity}</span> ${esc(issue.title)}</h3>
    <div class="tags"><span class="tag">${esc(issue.category)}</span>${objs}</div>
    <div class="section"><h4>Description</h4><div>${esc(issue.description)}</div></div>
    ${issue.evidence ? `<div class="section"><h4>Evidence</h4><pre>${esc(issue.evidence)}</pre></div>` : ""}
    <div class="section"><h4>Remediation</h4>
      <div>${esc(rem.summary || "")}</div>
      ${steps ? `<ul>${steps}</ul>` : ""}
      ${auto ? `<pre>Automated: ${esc(auto.method)} ${esc(auto.endpoint)}\n${esc(JSON.stringify(auto.payload, null, 2))}</pre>` : ""}
    </div>
    <div class="section"><h4>Note</h4>
      <input class="note-input" id="noteInput" placeholder="Optional note" value="${esc(issue.note || "")}" /></div>
    <div class="actions">
      <button class="ignore" data-disp="Ignored">Ignore</button>
      <button class="later" data-disp="Later">Leave for later</button>
      <button class="remediate" data-disp="Remediate">Mark Remediated</button>
      ${auto ? `<button class="remediate" id="autoFix">⚡ Auto-Remediate</button>` : ""}
    </div>
    <span id="modalStatus" class="status"></span>`;

  $$("#modalContent .actions button[data-disp]").forEach((b) =>
    b.addEventListener("click", () => setDisposition(issue, b.dataset.disp)));
  if (auto) $("#autoFix").addEventListener("click", () => autoRemediate(issue));
  $("#modal").classList.remove("hidden");
}
$("#modalClose").addEventListener("click", () => $("#modal").classList.add("hidden"));
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });

async function setDisposition(issue, disposition) {
  const note = $("#noteInput")?.value || "";
  $("#modalStatus").textContent = "Saving…";
  try {
    const updated = await api(
      `/api/results/${currentResult.id}/issues/${issue.id}/disposition`,
      { method: "POST", body: JSON.stringify({ disposition, note }) });
    currentResult = updated; renderIssues();
    $("#modal").classList.add("hidden");
  } catch (e) { $("#modalStatus").textContent = e.message; $("#modalStatus").className = "status err"; }
}

async function autoRemediate(issue) {
  if (!confirm("This will push a configuration change to your UniFi controller. Continue?")) return;
  $("#modalStatus").textContent = "Applying…";
  try {
    await api(`/api/results/${currentResult.id}/issues/${issue.id}/remediate`, { method: "POST" });
    const updated = await api(`/api/results/${currentResult.id}`);
    currentResult = updated; renderIssues();
    $("#modalStatus").textContent = "Applied ✓"; $("#modalStatus").className = "status ok";
    setTimeout(() => $("#modal").classList.add("hidden"), 900);
  } catch (e) { $("#modalStatus").textContent = e.message; $("#modalStatus").className = "status err"; }
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---------- init ----------
(async function init() {
  try { const v = await api("/api/version"); $("#appVersion").textContent = "v" + v.version; } catch {}
  await loadSnapshots();
  const list = await loadResultList();
  if (list && list.length) {
    const r = await api(`/api/results/${list[0].id}`);
    $("#resultSelect").value = list[0].id;
    renderResult(r);
  }
})();
