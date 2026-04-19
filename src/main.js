/**
 * Director's Cut – Frontend application
 */
const { invoke } = window.__TAURI__.core;

let backendRunning = false;
let currentRunId = null;
let eventSource = null;

// --- Toast notification helper ---
function toast(msg, type = "info") {
  let container = document.getElementById("toast-container");
  if (!container) {
    container = document.createElement("div");
    container.id = "toast-container";
    container.style.cssText = "position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;";
    document.body.appendChild(container);
  }
  const el = document.createElement("div");
  const colors = { info: "#3b82f6", success: "#22c55e", error: "#ef4444" };
  el.style.cssText = `background:${colors[type] || colors.info};color:#fff;padding:12px 20px;border-radius:8px;font-size:14px;box-shadow:0 4px 12px rgba(0,0,0,0.3);transition:opacity 0.3s;max-width:400px;`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.remove(), 300); }, 4000);
}

// --- API helper ---
async function api(method, path, body) {
  try {
    const raw = await invoke("api_request", { method, path, body: body ? JSON.stringify(body) : null });
    return JSON.parse(raw);
  } catch (e) {
    console.error(`API ${method} ${path} failed:`, e);
    toast(`API error: ${e}`, "error");
    throw e;
  }
}

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
  const dot = document.getElementById("backend-status");
  if (!backendRunning) {
    btn.textContent = "Starting…";
    btn.disabled = true;
    try {
      const result = await invoke("start_backend");
      console.log("start_backend result:", result);
      backendRunning = true;
      btn.textContent = "Stop Backend";
      dot.className = "status-dot online";
      toast("Backend started! " + (result.message || ""), "success");
      // Wait a moment then check health
      setTimeout(checkBackendHealth, 2000);
    } catch (e) {
      console.error("start_backend error:", e);
      btn.textContent = "Start Backend";
      toast("Failed to start backend: " + e, "error");
    }
    btn.disabled = false;
  } else {
    try {
      await invoke("stop_backend");
      backendRunning = false;
      btn.textContent = "Start Backend";
      dot.className = "status-dot offline";
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
      document.getElementById("btn-backend-toggle").textContent = "Stop Backend";
      document.getElementById("backend-status").className = "status-dot online";
    }
  } catch { /* offline */ }
}

// --- Projects ---
async function loadProjects() {
  try {
    const projects = await api("GET", "/api/projects");
    document.getElementById("stat-projects").textContent = projects.length;
    const list = document.getElementById("project-list");
    list.innerHTML = projects.length
      ? projects.map((p) =>
          `<div class="item" data-id="${p.id}"><div><span class="item-name">${p.name}</span><br><span class="item-meta">${p.description || ""}</span></div><span class="item-meta">${new Date(p.created_at).toLocaleDateString()}</span></div>`
        ).join("")
      : '<p class="item-meta">No projects yet.</p>';
  } catch { /* offline */ }
}

async function createProject() {
  const name = prompt("Project name:");
  if (!name) return;
  await api("POST", "/api/projects", { name, description: "" });
  loadProjects();
}

// --- Runs ---
async function startQuickRun(prompt) {
  toast("Starting production: " + prompt, "info");
  try {
    if (!backendRunning) {
      toast("Backend not running — starting it first…", "info");
      await toggleBackend();
      // Give uvicorn time to boot
      await new Promise(r => setTimeout(r, 3000));
    }
    let projects;
    try {
      projects = await api("GET", "/api/projects");
    } catch {
      toast("Cannot reach backend. Make sure it's running.", "error");
      return;
    }
    let pid = projects.length ? projects[0].id : (await api("POST", "/api/projects", { name: "Quick Project" })).id;
    const run = await api("POST", "/api/runs", { project_id: pid, prompt });
    currentRunId = run.id;
    toast("Production run created!", "success");
    navigateTo("runs");
    showPipeline(run);
    streamLogs(run.id);
  } catch (e) {
    toast("Failed to start run: " + e, "error");
  }
}

function showPipeline(run) {
  document.getElementById("pipeline-view").style.display = "block";
  document.getElementById("pipeline-run-id").textContent = run.id.slice(0, 8);
  document.querySelectorAll(".stage").forEach((s) => s.classList.remove("active", "completed"));
  const cur = document.querySelector(`[data-stage="${run.current_stage}"]`);
  if (cur) cur.classList.add("active");
}

function streamLogs(runId) {
  if (eventSource) eventSource.close();
  const logEl = document.getElementById("log-output");
  logEl.textContent = "";
  eventSource = new EventSource(`http://127.0.0.1:9420/api/events/stream/${runId}`);
  eventSource.onmessage = (e) => {
    const data = JSON.parse(e.data);
    logEl.textContent += `[${data.type}] ${data.message || JSON.stringify(data)}\n`;
    logEl.scrollTop = logEl.scrollHeight;
    if (data.stage) {
      const el = document.querySelector(`[data-stage="${data.stage}"]`);
      if (el) el.classList.add("completed");
    }
    if (data.type === "stage_progress" && data.message?.includes("awaiting approval")) {
      showApprovalModal(runId, data.stage);
    }
  };
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
}

// --- Settings ---
async function loadSettings() {
  try {
    const s = await api("GET", "/api/settings");
    if (s.openrouter_api_key) document.getElementById("setting-api-key").value = s.openrouter_api_key;
    if (s.model) document.getElementById("setting-model").value = s.model;
    if (s.ffmpeg_path) document.getElementById("setting-ffmpeg").value = s.ffmpeg_path;
  } catch { /* offline */ }
}

async function saveSettings(e) {
  e.preventDefault();
  await api("PUT", "/api/settings", {
    openrouter_api_key: document.getElementById("setting-api-key").value,
    model: document.getElementById("setting-model").value,
    ffmpeg_path: document.getElementById("setting-ffmpeg").value,
  });
}

// --- Init ---
window.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-page]").forEach((link) => {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      const page = link.dataset.page;
      navigateTo(page);
      if (page === "projects") loadProjects();
      if (page === "settings") loadSettings();
    });
  });
  document.getElementById("btn-backend-toggle").addEventListener("click", toggleBackend);
  document.getElementById("quick-start-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const p = document.getElementById("quick-prompt").value.trim();
    if (p) startQuickRun(p);
  });
  document.getElementById("btn-new-project").addEventListener("click", createProject);
  document.getElementById("settings-form").addEventListener("submit", saveSettings);
  checkBackendHealth();
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
