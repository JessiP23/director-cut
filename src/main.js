/**
 * Director's Cut – Frontend application v2
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
    document.body.appendChild(container);
  }
  const el = document.createElement("div");
  el.className = `toast ${type}`;
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
    document.getElementById("stat-projects").textContent = projects.length;
    const list = document.getElementById("project-list");
    list.innerHTML = projects.length
      ? projects.map((p) =>
          `<div class="item" data-id="${p.id}"><div><span class="item-name">${p.name}</span><br><span class="item-meta">${p.description || ""}</span></div><span class="item-meta">${new Date(p.created_at).toLocaleDateString()}</span></div>`
        ).join("")
      : '<p class="item-meta" style="padding:20px;text-align:center;">No projects yet. Start a production from the Command Center.</p>';
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
  // Reset stages
  document.querySelectorAll(".stage-node").forEach((s) => s.classList.remove("active", "completed", "failed"));
  // Hide video + results panels
  document.getElementById("video-panel").style.display = "none";
  document.getElementById("results-panel").style.display = "none";
  // Mark current
  const cur = document.querySelector(`.stage-node[data-stage="${run.current_stage}"]`);
  if (cur) cur.classList.add("active");
}

function streamLogs(runId) {
  if (eventSource) eventSource.close();
  const logEl = document.getElementById("log-output");
  logEl.textContent = "";
  eventSource = new EventSource(`http://127.0.0.1:9420/api/events/stream/${runId}`);
  eventSource.onmessage = (e) => {
    const data = JSON.parse(e.data);
    const ts = new Date().toLocaleTimeString();

    if (data.type === "stage_thinking") {
      logEl.textContent += `  ${data.message}\n`;
    } else {
      logEl.textContent += `${ts} [${data.type}] ${data.message || JSON.stringify(data)}\n`;
    }
    logEl.scrollTop = logEl.scrollHeight;

    // Update pipeline stage indicators
    if (data.type === "stage_start" && data.stage) {
      const el = document.querySelector(`.stage-node[data-stage="${data.stage}"]`);
      if (el) { el.classList.remove("completed", "failed"); el.classList.add("active"); }
    }
    if (data.type === "stage_complete" && data.stage) {
      const el = document.querySelector(`.stage-node[data-stage="${data.stage}"]`);
      if (el) { el.classList.remove("active"); el.classList.add("completed"); }
    }

    // Approval gate
    if (data.type === "stage_progress" && data.message?.includes("awaiting approval")) {
      showApprovalModal(runId, data.stage);
    }

    // Terminal events
    if (data.type === "run_completed") {
      toast("🎬 Production complete!", "success");
      eventSource.close();
      loadRunResults(runId);
    }
    if (data.type === "run_failed") {
      toast("Run failed: " + (data.error || "unknown"), "error");
      eventSource.close();
    }
  };
  eventSource.onerror = () => {
    logEl.textContent += "[connection closed]\n";
    eventSource.close();
  };
}

// --- Run Results with Video Player ---
async function loadRunResults(runId) {
  try {
    const data = await api("GET", `/api/runs/${runId}/outputs`);
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
      videoMeta.innerHTML = `<span>⏱ ${dur}</span><span>📐 ${scenes} scenes</span><span>💾 ${size}</span>`;
    }

    // ── Stage-by-stage results ──
    resultsPanel.style.display = "block";

    const stageLabels = {
      intake: "📋 Intake",
      planning: "🗂️ Production Plan",
      research: "🔍 Research",
      script: "📝 Script",
      storyboard: "🎨 Storyboard",
      assets: "🖼️ Assets",
      audio: "🔊 Audio",
      edit_assembly: "✂️ Edit Assembly",
      qa: "✅ QA Report",
      render: "🎥 Render",
      package: "📦 Package",
      export: "🚀 Export",
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
    if (s.groq_api_key) document.getElementById("setting-groq-key").value = s.groq_api_key;
    if (s.openrouter_api_key) document.getElementById("setting-api-key").value = s.openrouter_api_key;
    if (s.model) document.getElementById("setting-model").value = s.model;
    if (s.ffmpeg_path) document.getElementById("setting-ffmpeg").value = s.ffmpeg_path;
  } catch { /* offline */ }
}

async function saveSettings(e) {
  e.preventDefault();
  await api("PUT", "/api/settings", {
    groq_api_key: document.getElementById("setting-groq-key").value,
    openrouter_api_key: document.getElementById("setting-api-key").value,
    model: document.getElementById("setting-model").value,
    ffmpeg_path: document.getElementById("setting-ffmpeg").value,
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
