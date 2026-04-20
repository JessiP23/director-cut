/**
 * Director's Cut – Frontend application v2
 */
const { invoke } = window.__TAURI__.core;

let backendRunning = false;
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
      ...persistedSettings,
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

    const list = document.getElementById("run-list");
    if (!filtered.length) {
      list.innerHTML = '<p class="item-meta" style="padding:20px;text-align:center;">No productions yet for this scope.</p>';
      return;
    }

    list.innerHTML = filtered.map((r) => `
      <div class="item run-item" data-id="${r.id}">
        <div>
          <span class="item-name">${(r.prompt || "Untitled").slice(0, 80)}</span><br>
          <span class="item-meta">Status: ${r.status} · Stage: ${r.current_stage}</span>
        </div>
        <span class="item-meta">${new Date(r.created_at).toLocaleString()}</span>
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
  document.getElementById("run-list").style.display = "none";
  document.getElementById("pipeline-view").style.display = "block";
  document.getElementById("output-view").style.display = "none";
  document.getElementById("pipeline-run-id").textContent = run.id.slice(0, 8);
  // Reset stages
  document.querySelectorAll(".stage-node").forEach((s) => s.classList.remove("active", "completed", "failed"));
  // Hide video + results panels
  document.getElementById("preview-panel").style.display = "none";
  document.getElementById("video-panel").style.display = "none";
  document.getElementById("results-panel").style.display = "none";
  document.getElementById("preview-list").innerHTML = "";
  previewClips = [];
  // Mark current
  const cur = document.querySelector(`.stage-node[data-stage="${run.current_stage}"]`);
  if (cur) cur.classList.add("active");
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

    if (data.preview_clip_url) {
      appendPreviewClip(data.preview_clip_url, data.preview_scene_index ?? 0, data.preview_scene_total ?? 0);
    }

    // Terminal events
    if (data.type === "run_completed") {
      toast("Production complete.", "success");
      eventSource.close();
      loadRunResults(runId);
      loadRuns(selectedProjectId);
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
    if (s.groq_api_key) document.getElementById("setting-groq-key").value = s.groq_api_key;
    if (s.fal_api_key) document.getElementById("setting-fal-key").value = s.fal_api_key;
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
    groq_api_key: document.getElementById("setting-groq-key").value,
    fal_api_key: document.getElementById("setting-fal-key").value,
    video_model: document.getElementById("setting-video-model").value,
    model: document.getElementById("setting-model").value,
    ffmpeg_path: document.getElementById("setting-ffmpeg").value,
    max_scenes: parseInt(document.getElementById("setting-max-scenes").value || "4", 10),
  });
  toast("Settings saved!", "success");
}

// --- Onboarding ---
function showOnboarding(existingSettings = {}) {
  const overlay = document.getElementById("onboarding-overlay");
  const groqInput = document.getElementById("onboard-groq-key");
  const falInput = document.getElementById("onboard-fal-key");
  groqInput.value = existingSettings.groq_api_key || "";
  falInput.value = existingSettings.fal_api_key || "";
  overlay.style.display = "flex";

  document.getElementById("onboard-save-btn").onclick = async () => {
    const groq = groqInput.value.trim();
    const fal = falInput.value.trim();
    if (!groq) { toast("Groq API key is required", "error"); groqInput.focus(); return; }
    if (!fal) { toast("fal.ai API key is required", "error"); falInput.focus(); return; }
    try {
      await api("PUT", "/api/settings", {
        groq_api_key: groq,
        fal_api_key: fal,
        video_model: "fal-ai/wan/v2.2-a14b/text-to-video",
        model: "llama-3.3-70b-versatile",
        max_scenes: 4,
      });
      overlay.style.display = "none";
      toast("You're all set! Start your first production.", "success");
      loadSettings();
    } catch (e) {
      toast("Failed to save: " + e, "error");
    }
  };
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
  document.getElementById("quick-start-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const p = document.getElementById("quick-prompt").value.trim();
    if (p) startQuickRun(p);
  });
  document.getElementById("quick-max-scenes").addEventListener("input", (e) => {
    document.getElementById("quick-max-scenes-value").textContent = e.target.value;
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
  // Auto-start backend engine
  (async () => {
    try {
      await checkBackendHealth();
      if (!backendRunning) {
        await invoke("start_backend");
        backendRunning = true;
        document.getElementById("backend-status-dot").classList.add("online");
        document.getElementById("backend-label").textContent = "Engine Running";
      }
      // Wait a moment for backend to be ready, then check onboarding
      await new Promise(r => setTimeout(r, 1500));
      try {
        const settings = await api("GET", "/api/settings");
        const hasGroq = settings.groq_api_key && String(settings.groq_api_key).startsWith("gsk_");
        const hasFal = settings.fal_api_key && String(settings.fal_api_key).length > 10;
        if (!hasGroq || !hasFal) {
          showOnboarding(settings);
        }
      } catch { /* backend not ready yet, skip onboarding check */ }
    } catch (e) {
      console.error("Auto-start backend failed:", e);
    }
  })();
  loadProjects();
  loadRuns();
  loadMediaLibrary();
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
