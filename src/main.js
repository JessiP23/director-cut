/**
 * Director's Cut – Frontend application v2
 * WM Studio auth: Supabase-js (same project as wm.studio web app).
 */
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.49.1";

const { invoke } = window.__TAURI__.core;

let backendRunning = false;
let sb = null;
let authCfg = {};
let authorizationHeader = null;
let currentRunId = null;
let eventSource = null;
let selectedProjectId = null;
let previewClips = [];
let autoPlayPreview = true;
let mediaProjectFilter = "all";
let projectsCache = [];

const STORAGE_KEYS = {
  selectedProjectId: "director.selectedProjectId",
  mediaProjectFilter: "director.mediaProjectFilter",
};

function pickPersistedNonSecretSettings(settingsPayload) {
  if (!settingsPayload || typeof settingsPayload !== "object") return {};
  const keys = ["video_model", "model", "ffmpeg_path"];
  const o = {};
  for (const k of keys) {
    const v = settingsPayload[k];
    if (v != null && String(v).trim() !== "") o[k] = v;
  }
  return o;
}

const MAX_TAKE_SCENES = 120;
let takeSequence = 0;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function classifyTakeKind(data) {
  const type = (data?.type || "").toLowerCase();
  const stage = (data?.stage || "").toLowerCase();
  const message = (data?.message || "").toLowerCase();

  if (type.includes("failed") || type.includes("error") || message.includes("blocked")) return "blocked";
  if (type.includes("complete") || /test(s)?\s+pass/.test(message)) return "pass";
  if (message.includes("warn")) return "warning";
  if (type.includes("thinking") || stage.includes("planning") || type.includes("planning")) return "planning";
  if (type.includes("shell") || type.includes("stdout") || type.includes("stderr")) return "shell";
  return "shell";
}

function renderFilePath(pathValue) {
  if (!pathValue) return "";
  const clean = String(pathValue);
  const parts = clean.split("/").filter(Boolean);
  if (!parts.length) return `<div class="scene-path"><span class="scene-file-name">${escapeHtml(clean)}</span></div>`;
  const file = parts[parts.length - 1];
  const dir = clean.slice(0, clean.length - file.length);
  return `<div class="scene-path"><span class="scene-file-dir">${escapeHtml(dir)}</span><span class="scene-file-name">${escapeHtml(file)}</span></div>`;
}

function renderDiff(diffText) {
  if (!diffText) return "";
  const lines = String(diffText).split("\n");
  const rows = lines.map((line, index) => {
    const trimmed = line.trimStart();
    let cls = "";
    if (trimmed.startsWith("+")) cls = " diff-line-add";
    else if (trimmed.startsWith("-")) cls = " diff-line-del";
    return `<div class="diff-line${cls}"><span class="diff-line-no">${index + 1}</span><span class="diff-line-txt">${escapeHtml(line)}</span></div>`;
  }).join("");
  return `<div class="scene-diff">${rows}</div>`;
}

function renderSceneBody(data) {
  const message = data?.message ? `<pre class="scene-shell">${escapeHtml(data.message)}</pre>` : "";
  const shellOutput = data?.shell_output ? `<pre class="scene-shell">${escapeHtml(data.shell_output)}</pre>` : "";
  const testOutput = data?.test_results
    ? `<pre class="scene-shell">${escapeHtml(typeof data.test_results === "string" ? data.test_results : JSON.stringify(data.test_results, null, 2))}</pre>`
    : "";
  const path = renderFilePath(data?.file_path || data?.path || data?.file);
  const diff = renderDiff(data?.diff || data?.patch || data?.git_diff);

  return `${path}${diff}${shellOutput}${testOutput}${message || (!path && !diff && !shellOutput && !testOutput ? `<pre class="scene-shell">${escapeHtml(JSON.stringify(data, null, 2))}</pre>` : "")}`;
}

function setTakeExecutionActive(isActive) {
  const btn = document.getElementById("interrupt-btn");
  if (btn) btn.style.display = isActive ? "inline-flex" : "none";
}

function updateTakeIndicator(sceneEl) {
  const indicator = document.getElementById("take-indicator");
  if (!indicator || !sceneEl) return;
  // Now just a small dot row — no absolute positioning needed
  indicator.style.display = "flex";
}

function resetAgentTakeTimeline() {
  const timeline = document.getElementById("agent-take-timeline");
  const indicator = document.getElementById("take-indicator");
  const thinking = document.getElementById("agent-thinking");
  if (timeline) timeline.innerHTML = "";
  if (indicator) indicator.style.display = "none";
  if (thinking) thinking.style.display = "none";
  takeSequence = 0;
}

function appendAgentTakeScene(data) {
  const timeline = document.getElementById("agent-take-timeline");
  if (!timeline) return;

  const kind = classifyTakeKind(data);
  const details = document.createElement("details");
  details.className = `scene-card scene-card--${kind}`;
  details.dataset.sceneIndex = String(++takeSequence);

  const when = new Date().toLocaleTimeString();
  const title = escapeHtml(data?.stage || data?.type || "agent-step");
  const subtitle = escapeHtml(data?.type || "event");
  details.innerHTML = `
    <summary>
      <div class="scene-summary-main">
        <span class="scene-title">${title}</span>
        <span class="scene-kind">${subtitle}</span>
      </div>
      <span class="scene-time">${when}</span>
    </summary>
    <div class="scene-body">${renderSceneBody(data)}</div>
  `;

  timeline.appendChild(details);

  while (timeline.children.length > MAX_TAKE_SCENES) {
    timeline.removeChild(timeline.firstElementChild);
  }

  details.open = true;
  updateTakeIndicator(details);
  timeline.scrollTop = timeline.scrollHeight;
}

function persistContext() {
  if (selectedProjectId) {
    localStorage.setItem(STORAGE_KEYS.selectedProjectId, selectedProjectId);
  } else {
    localStorage.removeItem(STORAGE_KEYS.selectedProjectId);
  }
  localStorage.setItem(STORAGE_KEYS.mediaProjectFilter, mediaProjectFilter || "all");
}

function restoreContext() {
  selectedProjectId = localStorage.getItem(STORAGE_KEYS.selectedProjectId);
  mediaProjectFilter = localStorage.getItem(STORAGE_KEYS.mediaProjectFilter) || "all";
}

function projectNameById(projectId) {
  if (!projectId || projectId === "all") return "All projects";
  const match = projectsCache.find((p) => p.id === projectId);
  return match ? match.name : "Selected project";
}

function refreshScopeBadges() {
  const runsBadge = document.getElementById("runs-scope-badge");
  const mediaBadge = document.getElementById("media-scope-badge");
  if (runsBadge) {
    runsBadge.textContent = `Scoped to: ${projectNameById(selectedProjectId || "all")}`;
  }
  if (mediaBadge) {
    mediaBadge.textContent = `Scoped to: ${projectNameById(mediaProjectFilter || "all")}`;
  }
}

// --- Toast notification helper ---
function toast(msg, type = "info") {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    document.body.appendChild(container);
  }
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 300); }, 4000);
}

// --- Backend auth / API ---
async function rawApi(method, path, body, bearer) {
  const raw = await invoke("api_request", {
    method,
    path,
    body: body ? JSON.stringify(body) : null,
    authorization: bearer && bearer.trim() ? bearer : null,
  });
  return JSON.parse(raw);
}

async function api(method, path, body) {
  try {
    return await rawApi(method, path, body, authorizationHeader);
  } catch (e) {
    if (authorizationHeader && sb && String(e).includes("401")) {
      try {
        const { data, error } = await sb.auth.refreshSession();
        if (!error && data.session) await applyDirectorSession(data.session);
        return await rawApi(method, path, body, authorizationHeader);
      } catch (_) { /* fallback */ }
    }
    console.error(`API ${method} ${path} failed:`, e);
    toast(`API error: ${e}`, "error");
    throw e;
  }
}

function sseAccessToken() {
  if (!authorizationHeader || !authorizationHeader.startsWith("Bearer ")) return "";
  return authorizationHeader.slice(7).trim();
}

function setAuthChromeVisible(signedIn) {
  const wall = document.getElementById("auth-wall");
  if (!wall) return;
  wall.style.display = signedIn ? "none" : "flex";
}

function authError(text) {
  const el = document.getElementById("auth-error");
  if (!el) return;
  el.style.display = text ? "block" : "none";
  el.textContent = text || "";
}

async function refreshAppShell() {
  try {
    await loadProjects();
    await loadRuns();
    await loadMediaLibrary();
    await loadSettings();
  } catch {
    /* ignore */
  }
}

async function applyDirectorSession(session) {
  if (!session?.access_token) {
    authorizationHeader = null;
    const emailEl = document.getElementById("sidebar-user-email");
    if (emailEl) emailEl.textContent = "";
    setAuthChromeVisible(false);
    return;
  }
  authorizationHeader = `Bearer ${session.access_token}`;
  const emailEl = document.getElementById("sidebar-user-email");
  if (emailEl) emailEl.textContent = session.user?.email || session.user?.id || "Signed in";
  setAuthChromeVisible(true);
  await refreshAppShell();
}

async function bootstrapBackendAndAuthConfig() {
  try {
    await invoke("stop_backend");
  } catch (_) { /* noop */ }
  backendRunning = false;
  document.getElementById("backend-status-dot")?.classList.remove("online");
  document.getElementById("backend-label").textContent = "Starting…";
  try {
    await invoke("start_backend");
  } catch (e) {
    console.error("[DIRECTOR_BOOT] start_backend failed:", e);
    document.getElementById("backend-label").textContent = "Start Engine";
    toast(`Backend failed to start: ${e}`, "error");
    return;
  }
  backendRunning = true;
  document.getElementById("backend-status-dot")?.classList.add("online");
  document.getElementById("backend-label").textContent = "Engine Running";
  await new Promise((r) => setTimeout(r, 900));
  const cfg = await rawApi("GET", "/api/auth/config", null, null);
  authCfg = cfg;
  console.log(
    `[DIRECTOR_BOOT] /api/auth/config wmstudio_origin="${cfg.wmstudio_origin || ""}" (empty=${!cfg.wmstudio_origin}) oauth=${cfg.oauth_redirect} supabase_ok=${Boolean(cfg.supabase_url && cfg.supabase_anon_key)} locale=${cfg.locale} tried_files=${(cfg.env_files_tried || []).length}`,
  );

  const bh = document.getElementById("auth-backend-hint");
  if (bh) {
    if (!cfg.supabase_url || !cfg.supabase_anon_key) {
      bh.style.display = "block";
      bh.textContent = cfg.hint || "Supabase keys missing — see backend logs env_files_tried.";
    } else bh.style.display = "none";
  }
  if (!cfg.supabase_url || !cfg.supabase_anon_key) {
    sb = null;
    setAuthChromeVisible(false);
    authError("");
    return;
  }
  sb = createClient(cfg.supabase_url, cfg.supabase_anon_key, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: false,
      flowType: "pkce",
    },
  });
  sb.auth.onAuthStateChange(async (_evt, session) => {
    await applyDirectorSession(session);
  });
  const { data } = await sb.auth.getSession();
  await applyDirectorSession(data.session);
}

async function pollDesktopOAuthBridge() {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 1200));
    try {
      const raw = await invoke("api_request", {
        method: "GET",
        path: "/api/auth/desktop-oauth-bridge",
        body: null,
        authorization: null,
      });
      const j = JSON.parse(raw);
      if (j?.access_token && j?.refresh_token) {
        const { error } = await sb.auth.setSession({
          access_token: j.access_token,
          refresh_token: j.refresh_token,
        });
        if (error) {
          authError(error.message || "Could not attach session.");
          return false;
        }
        authError("");
        return true;
      }
    } catch {
      /* backend still starting */
    }
  }
  return false;
}

async function runOAuth(provider) {
  if (!sb) {
    authError("Supabase client not ready.");
    return;
  }
  authError("");
  let j;
  try {
    const raw = await invoke("api_request", {
      method: "GET",
      path: `/api/auth/oauth/start?provider=${encodeURIComponent(provider)}`,
      body: null,
      authorization: null,
    });
    j = JSON.parse(raw);
  } catch (e) {
    authError(String(e));
    return;
  }
  if (!j?.url) {
    const detail = j?.detail != null ? (Array.isArray(j.detail) ? j.detail.join(" ") : String(j.detail)) : "";
    authError(detail || "No OAuth URL from Director backend.");
    return;
  }
  try {
    await invoke("open_external_url", { url: j.url });
  } catch (e) {
    authError(String(e));
    return;
  }
  toast("Complete sign-in in your browser — this window will continue automatically.", "info");
  const ok = await pollDesktopOAuthBridge();
  if (!ok) {
    authError("Sign-in timed out. Complete the browser flow sooner, then try again.");
    return;
  }
  toast("Signed in.", "success");
}

async function openWmStudioAuthInBrowser() {
  authError("");
  const base = (authCfg.wmstudio_origin || "").replace(/\/$/, "");
  const loc = authCfg.locale || "en";
  if (!base) {
    authError("Set NEXT_PUBLIC_APP_URL in .env.");
    return;
  }
  const url = `${base}/${encodeURIComponent(loc)}/auth?director=1`;
  try {
    await invoke("open_external_url", { url });
    toast("Sign in at wmstudio if prompted, then we will connect Director on this Mac.", "info");
  } catch (e) {
    authError(String(e));
    return;
  }
  const ok = await pollDesktopOAuthBridge();
  if (!ok) authError("No session linked. Sign in at wmstudio, wait for the localhost page, then return here.");
}

window.addEventListener("message", async (ev) => {
  if (!ev?.data || ev.data.type !== "director_wmstudio_session") return;
  const s = ev.data.session;
  if (!sb || !s?.access_token || !s?.refresh_token) return;
  authError("");
  const { error } = await sb.auth.setSession({
    access_token: s.access_token,
    refresh_token: s.refresh_token,
  });
  if (error) authError(error.message || "Could not attach session.");
});

// --- Navigation ---
function navigateTo(page) {
  document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
  document.querySelectorAll(".nav-links a").forEach((a) => a.classList.remove("active"));
  const el = document.getElementById(`page-${page}`);
  if (el) el.classList.add("active");
  const link = document.querySelector(`[data-page="${page}"]`);
  if (link) link.classList.add("active");
}

// --- Backend control ---
async function toggleBackend() {
  const btn = document.getElementById("btn-backend-toggle");
  const dot = document.getElementById("backend-status-dot");
  const label = document.getElementById("backend-label");
  if (!backendRunning) {
    label.textContent = "Starting…";
    btn.disabled = true;
    try {
      const result = await invoke("start_backend");
      console.log("start_backend result:", result);
      backendRunning = true;
      label.textContent = "Engine Running";
      dot.classList.add("online");
      toast("Backend engine started!", "success");
      setTimeout(checkBackendHealth, 2000);
    } catch (e) {
      console.error("start_backend error:", e);
      label.textContent = "Start Engine";
      toast("Failed to start backend: " + e, "error");
    }
    btn.disabled = false;
  } else {
    try {
      await invoke("stop_backend");
      backendRunning = false;
      label.textContent = "Start Engine";
      dot.classList.remove("online");
      toast("Backend stopped", "info");
    } catch (e) {
      toast("Failed to stop backend: " + e, "error");
    }
  }
}

async function checkBackendHealth() {
  try {
    const resp = await invoke("backend_health");
    const data = JSON.parse(resp);
    if (data.status === "ok") {
      backendRunning = true;
      document.getElementById("backend-label").textContent = "Engine Running";
      document.getElementById("backend-status-dot").classList.add("online");
    }
  } catch { /* offline */ }
}

// --- Projects ---
async function loadProjects() {
  try {
    const projects = await api("GET", "/api/projects");
    projectsCache = projects;
    const runs = await api("GET", "/api/runs");

    const projectIds = new Set(projects.map((p) => p.id));
    if (selectedProjectId && !projectIds.has(selectedProjectId)) {
      selectedProjectId = null;
    }
    if (mediaProjectFilter !== "all" && !projectIds.has(mediaProjectFilter)) {
      mediaProjectFilter = "all";
    }
    persistContext();
    refreshScopeBadges();

    document.getElementById("stat-projects").textContent = projects.length;

    // Populate the quick-start project selector
    const quickProj = document.getElementById("quick-project");
    if (quickProj) {
      const prev = quickProj.value;
      quickProj.innerHTML = projects.map((p) =>
        `<option value="${p.id}">${p.name}</option>`
      ).join("") + '<option value="_new">+ New Project…</option>';
      // Restore selection
      if (selectedProjectId && projectIds.has(selectedProjectId)) {
        quickProj.value = selectedProjectId;
      } else if (prev && prev !== "_new") {
        quickProj.value = prev;
      }
    }

    const list = document.getElementById("project-list");
    list.innerHTML = projects.length
      ? projects.map((p) =>
          `<div class="item" data-id="${p.id}"><div><span class="item-name">${p.name}</span><br><span class="item-meta">${p.description || "No description"}</span></div><span class="item-meta">${runs.filter(r => r.project_id === p.id).length} runs</span></div>`
        ).join("")
      : '<p class="item-meta" style="padding:20px;text-align:center;">No projects yet. Start a production from the Command Center.</p>';

    list.querySelectorAll(".item").forEach((el) => {
      el.addEventListener("click", () => {
        selectedProjectId = el.dataset.id;
        mediaProjectFilter = selectedProjectId;
        persistContext();
        const filterEl = document.getElementById("artifact-project-filter");
        if (filterEl) {
          filterEl.value = mediaProjectFilter;
        }
        refreshScopeBadges();
        navigateTo("runs");
        loadRuns(selectedProjectId);
      });
    });
  } catch { /* offline */ }
}

async function createProject() {
  return new Promise((resolve) => {
    const overlay = document.getElementById("new-project-overlay");
    const nameInput = document.getElementById("new-project-name");
    const descInput = document.getElementById("new-project-desc");
    nameInput.value = "";
    descInput.value = "";
    overlay.style.display = "flex";
    nameInput.focus();

    const cleanup = () => { overlay.style.display = "none"; };

    document.getElementById("new-project-cancel").onclick = () => { cleanup(); resolve(null); };
    overlay.onclick = (e) => { if (e.target === overlay) { cleanup(); resolve(null); } };

    document.getElementById("new-project-create").onclick = async () => {
      const name = nameInput.value.trim();
      if (!name) { nameInput.focus(); return; }
      const created = await api("POST", "/api/projects", { name, description: descInput.value.trim() });
      selectedProjectId = created.id;
      mediaProjectFilter = created.id;
      persistContext();
      refreshScopeBadges();
      loadProjects();
      cleanup();
      resolve(created);
    };
  });
}

// --- Runs ---
async function startQuickRun(prompt) {
  try {
    if (!backendRunning) {
      toast("Starting engine…", "info");
      try {
        await invoke("start_backend");
        backendRunning = true;
        document.getElementById("backend-status-dot").classList.add("online");
        document.getElementById("backend-label").textContent = "Engine Running";
      } catch (e) {
        toast("Failed to start engine: " + e, "error");
        return;
      }
      await new Promise(r => setTimeout(r, 3000));
    }
    let projects;
    try {
      projects = await api("GET", "/api/projects");
    } catch {
      toast("Cannot reach backend. Make sure it's running.", "error");
      return;
    }

    // Resolve project from the selector
    const selector = document.getElementById("quick-project");
    let pid = selector.value;
    if (pid === "_new") {
      const created = await createProject();
      if (!created) return; // user cancelled
      pid = created.id;
    }
    selectedProjectId = pid;
    persistContext();
    refreshScopeBadges();

    let persistedSettings = {};
    try {
      persistedSettings = await api("GET", "/api/settings");
    } catch {
      persistedSettings = {};
    }

    const quickMaxScenes = parseInt(document.getElementById("quick-max-scenes")?.value || "4", 10);
    const runSettings = {
      ...pickPersistedNonSecretSettings(persistedSettings),
      max_scenes: Number.isFinite(quickMaxScenes) ? quickMaxScenes : 4,
    };

    const run = await api("POST", "/api/runs", { project_id: pid, prompt, settings: runSettings });
    currentRunId = run.id;
    toast("Production run created!", "success");
    navigateTo("runs");
    showPipeline(run);
    streamLogs(run.id);
    loadRuns(selectedProjectId);
  } catch (e) {
    toast("Failed to start run: " + e, "error");
  }
}

async function loadRuns(projectId = null) {
  try {
    if (projectId !== null && projectId !== undefined) {
      selectedProjectId = projectId;
      persistContext();
      refreshScopeBadges();
    }
    const runs = await api("GET", "/api/runs");
    const filtered = projectId ? runs.filter((r) => r.project_id === projectId) : runs;

    const active = runs.filter((r) => r.status === "running" || r.status === "awaiting_approval").length;
    document.getElementById("stat-runs").textContent = active;

    // Update sidebar active-runs badge
    const navBadge = document.getElementById("active-runs-badge");
    if (navBadge) {
      if (active > 0) {
        navBadge.textContent = active;
        navBadge.style.display = "inline-flex";
      } else {
        navBadge.style.display = "none";
      }
    }

    const list = document.getElementById("run-list");
    if (!filtered.length) {
      list.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">🎬</div>
          <p class="empty-title">No productions yet</p>
          <p class="empty-sub">Go to Command Center and type a prompt to kick off your first AI video.</p>
        </div>`;
      return;
    }

    const statusIcon = { running: "🟢", awaiting_approval: "🟡", completed: "✅", failed: "🔴" };
    list.innerHTML = filtered.map((r) => `
      <div class="item run-item" data-id="${r.id}">
        <div class="run-item-main">
          <span class="item-name">${(r.prompt || "Untitled").slice(0, 80)}</span>
          <span class="run-status run-status--${r.status}">${statusIcon[r.status] || "⚪"} ${r.status.replace(/_/g, " ")}</span>
        </div>
        <div class="run-item-meta">
          <span class="item-meta">Stage: ${r.current_stage || "—"}</span>
          <span class="item-meta">${new Date(r.created_at).toLocaleString()}</span>
        </div>
      </div>
    `).join("");

    list.querySelectorAll(".run-item").forEach((el) => {
      el.addEventListener("click", async () => {
        const runId = el.dataset.id;
        const run = filtered.find((x) => x.id === runId);
        if (!run) return;
        showPipeline(run);
        if (run.status === "running" || run.status === "awaiting_approval") {
          streamLogs(runId);
        } else {
          loadRunResults(runId);
        }
      });
    });
  } catch {
    // offline
  }
}

function showPipeline(run) {
  currentRunId = run.id;
  document.getElementById("run-list").style.display = "none";
  document.getElementById("pipeline-view").style.display = "block";
  document.getElementById("output-view").style.display = "none";
  document.getElementById("pipeline-run-id").textContent = `Run ${run.id.slice(0, 8)}`;
  resetAgentTakeTimeline();
  setTakeExecutionActive(run.status === "running" || run.status === "awaiting_approval");
  // Hide video + results panels
  document.getElementById("preview-panel").style.display = "none";
  document.getElementById("video-panel").style.display = "none";
  document.getElementById("results-panel").style.display = "none";
  document.getElementById("preview-list").innerHTML = "";
  previewClips = [];
}

function appendPreviewClip(url, index, total) {
  if (!url || previewClips.some((c) => c.url === url)) return;
  previewClips.push({ url, index, total });

  const outputView = document.getElementById("output-view");
  const previewPanel = document.getElementById("preview-panel");
  const previewPlayer = document.getElementById("preview-player");
  const previewMeta = document.getElementById("preview-meta");
  const previewList = document.getElementById("preview-list");

  outputView.style.display = "block";
  previewPanel.style.display = "block";

  if (!previewPlayer.src) {
    previewPlayer.src = `${url}?t=${Date.now()}`;
  } else if (autoPlayPreview) {
    previewPlayer.src = `${url}?t=${Date.now()}`;
    previewPlayer.play().catch(() => {});
  }

  previewMeta.innerHTML = `<span>${previewClips.length} clip(s) ready</span><span>Latest: Scene ${index + 1}/${total}</span>`;

  previewList.innerHTML = previewClips
    .sort((a, b) => a.index - b.index)
    .map((clip) => `
      <div class="item preview-item" data-url="${clip.url}">
        <div><span class="item-name">Scene ${clip.index + 1}</span></div>
        <span class="item-meta">Preview</span>
      </div>
    `)
    .join("");

  previewList.querySelectorAll(".preview-item").forEach((el) => {
    el.addEventListener("click", () => {
      previewPlayer.src = `${el.dataset.url}?t=${Date.now()}`;
      previewPlayer.play().catch(() => {});
    });
  });
}

function streamLogs(runId) {
  if (eventSource) eventSource.close();
  resetAgentTakeTimeline();
  setTakeExecutionActive(true);
  const _tk = sseAccessToken();
  const _q = _tk ? `?access_token=${encodeURIComponent(_tk)}` : "";
  eventSource = new EventSource(`http://127.0.0.1:9420/api/events/stream/${runId}${_q}`);
  eventSource.onmessage = (e) => {
    const data = JSON.parse(e.data);

    appendAgentTakeScene(data);

    const thinking = document.getElementById("agent-thinking");
    if (thinking) {
      thinking.style.display = data.type === "stage_thinking" ? "inline-flex" : "none";
    }

    // Approval gate
    if (data.type === "stage_progress" && data.message?.includes("awaiting approval")) {
      showApprovalModal(runId, data.stage);
    }

    if (data.preview_clip_url) {
      appendPreviewClip(data.preview_clip_url, data.preview_scene_index ?? 0, data.preview_scene_total ?? 0);
    }

    // Terminal events
    if (data.type === "run_completed") {
      toast("Production complete.", "success");
      eventSource.close();
      setTakeExecutionActive(false);
      if (thinking) thinking.style.display = "none";
      loadRunResults(runId);
      loadRuns(selectedProjectId);
    }
    if (data.type === "run_failed") {
      toast("Run failed: " + (data.error || "unknown"), "error");
      eventSource.close();
      setTakeExecutionActive(false);
      if (thinking) thinking.style.display = "none";
    }
  };
  eventSource.onerror = () => {
    appendAgentTakeScene({ type: "stream_closed", stage: "connection", message: "[connection closed]" });
    eventSource.close();
    setTakeExecutionActive(false);
    const thinking = document.getElementById("agent-thinking");
    if (thinking) thinking.style.display = "none";
  };
}

// --- Run Results with Video Player ---
async function loadRunResults(runId) {
  try {
    const data = await api("GET", `/api/runs/${runId}/outputs`);
    document.getElementById("output-view").style.display = "block";
    const resultsPanel = document.getElementById("results-panel");
    const content = document.getElementById("results-content");
    const videoPanel = document.getElementById("video-panel");
    const videoPlayer = document.getElementById("video-player");
    const videoMeta = document.getElementById("video-meta");

    // ── Check for rendered video ──
    const renderOutput = data.outputs?.render;
    if (renderOutput?.rendered && renderOutput?.output_path) {
      // Build URL: output_path is like "data/exports/{runId}/render.mp4"
      // Backend serves /media/exports/{runId}/render.mp4
      const videoUrl = `http://127.0.0.1:9420/media/exports/${runId}/render.mp4`;
      videoPlayer.src = videoUrl;
      videoPanel.style.display = "block";

      // Show metadata
      const dur = renderOutput.duration_seconds ? `${Math.round(renderOutput.duration_seconds)}s` : "—";
      const size = renderOutput.file_size_bytes ? `${(renderOutput.file_size_bytes / 1024).toFixed(0)} KB` : "—";
      const scenes = renderOutput.scene_count || "—";
      videoMeta.innerHTML = `<span>Duration ${dur}</span><span>Scenes ${scenes}</span><span>Size ${size}</span>`;

      const previewPaths = renderOutput.preview_clip_paths || [];
      const previewUrls = previewPaths.map((p) => `http://127.0.0.1:9420/media/exports/${runId}/${p.split('/').pop()}`);
      if (previewUrls.length) {
        const previewPanel = document.getElementById("preview-panel");
        const previewPlayer = document.getElementById("preview-player");
        const previewMeta = document.getElementById("preview-meta");
        const previewList = document.getElementById("preview-list");
        previewPanel.style.display = "block";
        previewPlayer.src = `${previewUrls[0]}?t=${Date.now()}`;
        previewMeta.innerHTML = `<span>${previewUrls.length} clip(s)</span><span>Run ${runId.slice(0, 8)}</span>`;
        previewList.innerHTML = previewUrls.map((u, i) => `
          <div class="item preview-item" data-url="${u}">
            <div><span class="item-name">Scene ${i + 1}</span></div>
            <span class="item-meta">Preview</span>
          </div>
        `).join("");
        previewList.querySelectorAll(".preview-item").forEach((el) => {
          el.addEventListener("click", () => {
            previewPlayer.src = `${el.dataset.url}?t=${Date.now()}`;
            previewPlayer.play().catch(() => {});
          });
        });
        if (autoPlayPreview && previewUrls.length) {
          const latest = previewUrls[previewUrls.length - 1];
          previewPlayer.src = `${latest}?t=${Date.now()}`;
          previewPlayer.play().catch(() => {});
        }
      }
    }

    // ── Stage-by-stage results ──
    resultsPanel.style.display = "block";

    const stageLabels = {
      intake: "Intake",
      planning: "Production Plan",
      research: "Research",
      script: "Script",
      storyboard: "Storyboard",
      assets: "Assets",
      audio: "Audio",
      edit_assembly: "Edit Assembly",
      qa: "QA Report",
      render: "Render",
      package: "Package",
      export: "Export",
    };

    let html = "";
    for (const [stage, output] of Object.entries(data.outputs || {})) {
      const label = stageLabels[stage] || stage;
      const isSimulated = output.simulated;
      const isOpen = stage === "planning" || stage === "script" || stage === "storyboard";
      html += `<details class="result-stage" ${isOpen ? "open" : ""}>`;
      html += `<summary>${label}${isSimulated ? '<span class="sim-badge">simulated</span>' : ""}</summary>`;
      html += `<div class="result-body">`;

      if (output.title) html += `<p><strong>Title:</strong> ${output.title}</p>`;
      if (output.tone) html += `<p><strong>Tone:</strong> ${output.tone} · <strong>Style:</strong> ${output.style || "—"}</p>`;
      if (output.target_length_seconds) html += `<p><strong>Duration:</strong> ${output.target_length_seconds}s</p>`;

      if (output.scenes) {
        html += `<h4 style="margin-top:8px;">Scenes (${output.scenes.length})</h4><ul>`;
        for (const s of output.scenes) {
          html += `<li><strong>${s.id || ""}.</strong> ${s.description || s.text || JSON.stringify(s)} <em>(${s.duration || s.duration_seconds || "?"}s)</em></li>`;
        }
        html += `</ul>`;
      }

      if (output.script_lines) {
        html += `<h4 style="margin-top:8px;">Script Lines</h4><ul>`;
        for (const l of output.script_lines) {
          html += `<li>"${l.text}" <em>(${l.duration || l.duration_seconds || "?"}s)</em></li>`;
        }
        html += `</ul>`;
      }

      if (output.shots) {
        html += `<h4 style="margin-top:8px;">Shots (${output.shots.length})</h4><ul>`;
        for (const s of output.shots) {
          html += `<li><strong>${s.camera_angle || ""}:</strong> ${s.description || JSON.stringify(s)}</li>`;
        }
        html += `</ul>`;
      }

      if (output.facts) {
        html += `<h4>Facts</h4><ul>${output.facts.map(f => `<li>${typeof f === "string" ? f : JSON.stringify(f)}</li>`).join("")}</ul>`;
      }

      if (output.notes) html += `<p style="opacity:0.6;font-style:italic;margin-top:8px;">${output.notes}</p>`;

      // Fallback: raw JSON
      if (!output.title && !output.scenes && !output.script_lines && !output.shots && !output.facts && !output.rendered && !output.exported && !output.packaged && !output.timeline_built && !output.audio_generated && !output.assets_acquired) {
        html += `<pre>${JSON.stringify(output, null, 2)}</pre>`;
      }

      html += `</div></details>`;
    }

    if (!html) html = '<p style="color:var(--text-2);text-align:center;padding:20px;">No outputs recorded.</p>';
    content.innerHTML = html;
  } catch (e) {
    console.error("Failed to load results:", e);
  }
}

// --- Media Library ---
async function loadMediaLibrary() {
  const list = document.getElementById("artifact-list");
  const filterEl = document.getElementById("artifact-project-filter");
  list.innerHTML = '<p class="item-meta" style="padding:20px;text-align:center;">Loading media…</p>';
  try {
    const projects = await api("GET", "/api/projects");
    const runs = await api("GET", "/api/runs");

    if (filterEl) {
      const current = mediaProjectFilter || "all";
      filterEl.innerHTML = [
        '<option value="all">All projects</option>',
        ...projects.map((p) => `<option value="${p.id}">${p.name}</option>`),
      ].join("");
      filterEl.value = current;
    }
    refreshScopeBadges();

    const completed = runs.filter((r) => r.status === "completed");
    const completedFiltered = mediaProjectFilter === "all"
      ? completed
      : completed.filter((r) => r.project_id === mediaProjectFilter);
    const mediaEntries = [];

    for (const run of completedFiltered.slice(0, 30)) {
      try {
        const outputs = await api("GET", `/api/runs/${run.id}/outputs`);
        const render = outputs.outputs?.render;
        if (render?.rendered) {
          mediaEntries.push({
            runId: run.id,
            prompt: run.prompt,
            duration: render.duration_seconds,
            scenes: render.scene_count,
            size: render.file_size_bytes,
            url: `http://127.0.0.1:9420/media/exports/${run.id}/render.mp4`,
          });
        }
      } catch {
        // ignore broken runs
      }
    }

    document.getElementById("stat-artifacts").textContent = String(mediaEntries.length);
    document.getElementById("stat-exports").textContent = String(mediaEntries.length);

    if (!mediaEntries.length) {
      list.innerHTML = '<p class="item-meta" style="padding:20px;text-align:center;">No rendered media found yet.</p>';
      return;
    }

    list.innerHTML = mediaEntries.map((m) => `
      <div class="item media-item" data-run-id="${m.runId}" data-url="${m.url}">
        <div>
          <span class="item-name">${(m.prompt || "Untitled run").slice(0, 80)}</span><br>
          <span class="item-meta">Run ${m.runId.slice(0, 8)} · ${m.scenes || "—"} scenes · ${Math.round(m.duration || 0)}s</span>
        </div>
        <span class="item-meta">${m.size ? `${Math.round(m.size / 1024)} KB` : "—"}</span>
      </div>
    `).join("");

    list.querySelectorAll(".media-item").forEach((el) => {
      el.addEventListener("click", async () => {
        const runId = el.dataset.runId;
        navigateTo("runs");
        try {
          const runsNow = await api("GET", "/api/runs");
          const run = runsNow.find((r) => r.id === runId);
          if (run) {
            showPipeline(run);
            await loadRunResults(runId);
          }
        } catch {
          // ignore
        }
      });
    });
  } catch {
    list.innerHTML = '<p class="item-meta" style="padding:20px;text-align:center;">Media Library is unavailable while backend is offline.</p>';
  }
}

// --- Approvals ---
function showApprovalModal(runId, stage) {
  document.getElementById("approval-modal").style.display = "flex";
  document.getElementById("approval-stage").textContent = `Stage: ${stage}`;
  document.getElementById("btn-approve").onclick = () => submitApproval(runId, stage, "approve");
  document.getElementById("btn-revise").onclick = () => submitApproval(runId, stage, "revise");
  document.getElementById("btn-reject").onclick = () => submitApproval(runId, stage, "reject");
}

async function submitApproval(runId, stage, decision) {
  const notes = document.getElementById("approval-notes").value;
  await api("POST", `/api/approvals/${runId}`, { stage, decision, notes });
  document.getElementById("approval-modal").style.display = "none";
  document.getElementById("approval-notes").value = "";
  toast(`${stage} ${decision}d`, decision === "approve" ? "success" : "info");
}

// --- Settings ---
async function loadSettings() {
  try {
    const s = await api("GET", "/api/settings");
    if (s.video_model) document.getElementById("setting-video-model").value = s.video_model;
    if (s.model) document.getElementById("setting-model").value = s.model;
    if (s.ffmpeg_path) document.getElementById("setting-ffmpeg").value = s.ffmpeg_path;
    document.getElementById("setting-max-scenes").value = s.max_scenes ?? 4;
    document.getElementById("quick-max-scenes").value = s.max_scenes ?? 4;
    document.getElementById("quick-max-scenes-value").textContent = String(s.max_scenes ?? 4);
  } catch { /* offline */ }
}

async function saveSettings(e) {
  e.preventDefault();
  await api("PUT", "/api/settings", {
    video_model: document.getElementById("setting-video-model").value,
    model: document.getElementById("setting-model").value,
    ffmpeg_path: document.getElementById("setting-ffmpeg").value,
    max_scenes: parseInt(document.getElementById("setting-max-scenes").value || "4", 10),
  });
  toast("Settings saved!", "success");
}

// --- Init ---
window.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-page]").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const page = link.dataset.page;
      navigateTo(page);
      if (page === "projects") loadProjects();
      if (page === "runs") {
        document.getElementById("run-list").style.display = "";
        document.getElementById("pipeline-view").style.display = "none";
        loadRuns(selectedProjectId);
      }
      if (page === "artifacts") loadMediaLibrary();
      if (page === "settings") loadSettings();
    });
  });
  const produceBtn = document.querySelector("#quick-start-form .btn-glow");
  const quickPrompt = document.getElementById("quick-prompt");
  if (produceBtn && quickPrompt) {
    produceBtn.disabled = !quickPrompt.value.trim();
    quickPrompt.addEventListener("input", () => {
      produceBtn.disabled = !quickPrompt.value.trim();
    });
  }
  document.getElementById("quick-start-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const p = quickPrompt.value.trim();
    if (p) startQuickRun(p);
  });
  document.getElementById("quick-max-scenes").addEventListener("input", (e) => {
    document.getElementById("quick-max-scenes-value").textContent = e.target.value;
    updateCostHint();
  });

  function updateCostHint() {
    const scenes = parseInt(document.getElementById("quick-max-scenes").value || "4", 10);
    // Cost per clip by model (rough estimates)
    const modelCosts = {
      "fal-ai/wan/v2.2-a14b/text-to-video": 0.04,
      "fal-ai/ltx-video/v0.9.1/text-to-video": 0.01,
      "fal-ai/minimax/hailuo-02/standard/text-to-video": 0.45,
      "fal-ai/kling-video/v2.5-turbo/pro/text-to-video": 0.32,
    };
    const modelEl = document.getElementById("setting-video-model");
    const model = modelEl?.value || "fal-ai/wan/v2.2-a14b/text-to-video";
    const costPer = modelCosts[model] ?? 0.04;
    const total = (scenes * costPer).toFixed(2);
    const modelLabel = modelEl?.options[modelEl.selectedIndex]?.text?.split("—")[0]?.trim() || "Wan 2.2";
    const hint = document.getElementById("cost-hint");
    if (hint) hint.textContent = `~$${total} estimated · ${scenes} scene${scenes !== 1 ? "s" : ""} · ${modelLabel}`;
  }
  updateCostHint();

  // Back button in pipeline view
  document.getElementById("btn-back-to-runs")?.addEventListener("click", () => {
    if (eventSource) { eventSource.close(); eventSource = null; }
    setTakeExecutionActive(false);
    document.getElementById("pipeline-view").style.display = "none";
    document.getElementById("output-view").style.display = "none";
    document.getElementById("run-list").style.display = "";
    loadRuns(selectedProjectId);
  });

  // Pause / interrupt button — calls backend cancel endpoint
  document.getElementById("interrupt-btn")?.addEventListener("click", async () => {
    if (!currentRunId) return;
    const btn = document.getElementById("interrupt-btn");
    btn.disabled = true;
    btn.textContent = "Pausing…";
    try {
      await api("POST", `/api/runs/${currentRunId}/cancel`);
      if (eventSource) { eventSource.close(); eventSource = null; }
      setTakeExecutionActive(false);
      toast("Run paused / cancelled.", "info");
      appendAgentTakeScene({ type: "run_cancelled", stage: "interrupt", message: "Run cancelled by user." });
      loadRuns(selectedProjectId);
    } catch (e) {
      toast("Failed to pause run: " + e, "error");
    } finally {
      btn.disabled = false;
      btn.innerHTML = "⏸ Pause Run";
    }
  });
  // When project selector changes to _new, trigger create
  document.getElementById("quick-project").addEventListener("change", async (e) => {
    if (e.target.value === "_new") {
      const created = await createProject();
      if (created) {
        e.target.value = created.id;
        selectedProjectId = created.id;
        persistContext();
        refreshScopeBadges();
        loadProjects();
      } else {
        // Cancelled — pick first project or keep _new
        if (projectsCache.length) e.target.value = projectsCache[0].id;
      }
    }
  });
  document.getElementById("btn-new-project").addEventListener("click", createProject);
  document.getElementById("settings-form").addEventListener("submit", saveSettings);
  const autoplayToggle = document.getElementById("preview-autoplay-toggle");
  if (autoplayToggle) {
    autoPlayPreview = localStorage.getItem("preview.autoplay") !== "0";
    autoplayToggle.checked = autoPlayPreview;
    autoplayToggle.addEventListener("change", (e) => {
      autoPlayPreview = !!e.target.checked;
      localStorage.setItem("preview.autoplay", autoPlayPreview ? "1" : "0");
    });
  }
  const mediaFilterEl = document.getElementById("artifact-project-filter");
  if (mediaFilterEl) {
    mediaFilterEl.addEventListener("change", (e) => {
      mediaProjectFilter = e.target.value;
      persistContext();
      refreshScopeBadges();
      loadMediaLibrary();
    });
  }
  restoreContext();
  refreshScopeBadges();
  document.getElementById("btn-auth-google")?.addEventListener("click", () => runOAuth("google"));
  document.getElementById("btn-auth-github")?.addEventListener("click", () => runOAuth("github"));
  document.getElementById("btn-auth-apple")?.addEventListener("click", () => runOAuth("apple"));
  document.getElementById("btn-open-wm-auth")?.addEventListener("click", () => {
    openWmStudioAuthInBrowser().catch((e) => console.error(e));
  });
  document.getElementById("btn-email-signin")?.addEventListener("click", async () => {
    authError("");
    if (!sb) {
      authError("Supabase client not ready.");
      return;
    }
    const email = document.getElementById("auth-email").value.trim();
    const password = document.getElementById("auth-password").value;
    const { error } = await sb.auth.signInWithPassword({ email, password });
    if (error) authError(error.message || "Sign in failed.");
  });
  document.getElementById("btn-sign-out")?.addEventListener("click", async () => {
    if (sb) await sb.auth.signOut();
    await applyDirectorSession(null);
  });

  (async () => {
    try {
      await bootstrapBackendAndAuthConfig();
    } catch (e) {
      console.error("Backend/auth bootstrap failed:", e);
    }
  })();
  // Keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey) {
      switch (e.key) {
        case "1": navigateTo("dashboard"); break;
        case "2": navigateTo("projects"); break;
        case "3": navigateTo("runs"); break;
        case "4": navigateTo("artifacts"); break;
        case "5": navigateTo("settings"); break;
      }
    }
  });
});
