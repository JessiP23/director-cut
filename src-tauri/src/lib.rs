use reqwest::header::{ACCEPT, AUTHORIZATION, CONTENT_TYPE};
use serde::{Deserialize, Serialize};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;
use tauri::{AppHandle, State};
use tauri_plugin_opener::OpenerExt;

// ---------------------------------------------------------------------------
// App state
// ---------------------------------------------------------------------------

struct AppState {
    python_process: Mutex<Option<Child>>,
    backend_port: u16,
    backend_dir: Mutex<PathBuf>,
}

#[derive(Default)]
struct McpHttpState {
    client: reqwest::Client,
    session_id: Mutex<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct McpRequestPayload {
    path: String,
    method: String,
    body: Option<String>,
    auth_token: Option<String>,
}

// ---------------------------------------------------------------------------
// Resolve the backend directory and venv python path
// ---------------------------------------------------------------------------

fn resolve_backend_dir() -> PathBuf {
    // 1. Bundled inside .app → Contents/Resources/backend
    #[cfg(target_os = "macos")]
    {
        if let Ok(exe) = std::env::current_exe() {
            // exe is .app/Contents/MacOS/director-cut
            if let Some(macos_dir) = exe.parent() {
                let resources_backend = macos_dir
                    .parent() // Contents
                    .map(|p| p.join("Resources").join("backend"));
                if let Some(rb) = resources_backend {
                    if rb.join("app").exists() {
                        return rb;
                    }
                }
            }
        }
    }
    // 2. Dev mode – try relative paths from CWD / CARGO_MANIFEST_DIR
    let candidates = vec![
        PathBuf::from("backend"),
        PathBuf::from("../backend"),
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("backend"),
    ];
    for c in &candidates {
        if c.join("app").exists() {
            return std::fs::canonicalize(c).unwrap_or_else(|_| c.clone());
        }
    }
    // Fallback
    std::fs::canonicalize("../backend").unwrap_or_else(|_| PathBuf::from("../backend"))
}

/// Tauri bundle `Resources/` (macOS app) or `./resources/` beside the exe (Linux/Windows portable).
fn resolve_bundle_resources_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    #[cfg(target_os = "macos")]
    {
        let macos_bins = exe.parent()?;
        let contents = macos_bins.parent()?;
        let resources = contents.join("Resources");
        return if resources.is_dir() {
            Some(resources)
        } else {
            None
        };
    }
    #[cfg(not(target_os = "macos"))]
    {
        let dir = exe.parent()?;
        let resources = dir.join("resources");
        if resources.is_dir() {
            Some(resources)
        } else {
            None
        }
    }
}

/// Bundled `.app` backend lives under read-only-ish Resources; keep Python env user-writable.
#[cfg(target_os = "macos")]
fn backend_is_inside_macos_app_bundle(backend_dir: &Path) -> bool {
    backend_dir
        .to_string_lossy()
        .contains(".app/Contents/Resources/")
}

#[cfg(not(target_os = "macos"))]
fn backend_is_inside_macos_app_bundle(_backend_dir: &Path) -> bool {
    false
}

fn python_venv_root(backend_dir: &Path) -> PathBuf {
    #[cfg(target_os = "macos")]
    if backend_is_inside_macos_app_bundle(backend_dir) {
        if let Some(home) = std::env::var_os("HOME") {
            let dir = PathBuf::from(home).join("Library/Application Support/director-cut");
            return dir.join("python-venv");
        }
    }
    backend_dir.join("venv")
}

/// Prefer Homebrew / concrete paths — `.app` processes launched from Finder often have no `python3` on `PATH`.
fn resolve_host_python3() -> PathBuf {
    #[cfg(target_os = "macos")]
    {
        for p in [
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/usr/bin/python3",
        ] {
            if Path::new(p).is_file() {
                return PathBuf::from(p);
            }
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        for p in ["/usr/local/bin/python3", "/usr/bin/python3"] {
            if Path::new(p).is_file() {
                return PathBuf::from(p);
            }
        }
    }
    PathBuf::from("python3")
}

fn venv_python(backend_dir: &PathBuf) -> PathBuf {
    let venv_py = python_venv_root(backend_dir).join("bin").join("python");
    if venv_py.exists() {
        venv_py
    } else {
        PathBuf::from("python3")
    }
}

/// Ensure the venv exists and deps match `backend/pyproject.toml` (bundled + dev).
fn ensure_venv(backend_dir: &PathBuf) {
    let data_dir = backend_dir.join("data");
    if !data_dir.exists() {
        let _ = std::fs::create_dir_all(&data_dir);
    }

    let venv_root = python_venv_root(backend_dir);
    if let Some(parent) = venv_root.parent() {
        let _ = std::fs::create_dir_all(parent);
    }

    let venv_py = venv_root.join("bin").join("python");
    if !venv_py.exists() {
        let host = resolve_host_python3();
        match Command::new(&host)
            .args(["-m", "venv"])
            .arg(&venv_root)
            .output()
        {
            Ok(o) if o.status.success() => {}
            Ok(o) => eprintln!(
                "[director-cut] `{} -m venv` failed: {}",
                host.display(),
                String::from_utf8_lossy(&o.stderr)
            ),
            Err(e) => eprintln!(
                "[director-cut] could not run `{}` for venv: {e}",
                host.display()
            ),
        }
    }

    if venv_py.exists() {
        match Command::new(&venv_py)
            .args(["-m", "pip", "install", "-q", "."])
            .current_dir(backend_dir)
            .output()
        {
            Ok(o) if o.status.success() => {}
            Ok(o) => eprintln!(
                "[director-cut] pip install failed in {}: {}",
                venv_root.display(),
                String::from_utf8_lossy(&o.stderr)
            ),
            Err(e) => eprintln!("[director-cut] pip spawn failed: {e}"),
        }
    } else {
        eprintln!(
            "[director-cut] no venv at {} — install Python 3 (Homebrew or Xcode CLT)",
            venv_py.display()
        );
    }
}

/// Apply `backend/.env` lines to the child process cmd (WM/SUPABASE URLs and keys survive before Python resolves paths).
fn apply_backend_dotenv(cmd: &mut Command, backend_dir: &PathBuf) {
    let p = backend_dir.join(".env");
    let Ok(text) = std::fs::read_to_string(&p) else {
        eprintln!("[director-cut] no backend .env at {}", p.display());
        return;
    };
    let mut n = 0u32;
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((k, v)) = line.split_once('=') else { continue };
        let k = k.trim();
        let v = v.trim();
        if !k.is_empty() {
            cmd.env(k, v);
            n += 1;
        }
    }
    eprintln!(
        "[director-cut] passed {} vars from {}",
        n,
        p.display()
    );
}

/// macOS / Linux: terminate anything still listening on localhost:port (orphaned uvicorn after a crashed / hot-reloaded shell).
#[cfg(any(target_os = "macos", target_os = "linux"))]
fn kill_tcp_listeners_on_port(port: u16) {
    let our_pid = std::process::id();
    fn listen_pids(port: u16, our_pid: u32) -> Vec<u32> {
        let Ok(output) = Command::new("lsof")
            .args(["-nP", &format!("-iTCP:{port}"), "-sTCP:LISTEN", "-t"])
            .output()
        else {
            return Vec::new();
        };
        String::from_utf8_lossy(&output.stdout)
            .lines()
            .filter_map(|l| l.trim().parse().ok())
            .filter(|&p| p != our_pid)
            .collect()
    }
    let pids = listen_pids(port, our_pid);
    for &pid in &pids {
        eprintln!("[director-cut] stopping listener PID {pid} on :{port}");
        let _ = Command::new("kill")
            .args(["-TERM", &pid.to_string()])
            .status();
    }
    thread::sleep(Duration::from_millis(400));
    for pid in listen_pids(port, our_pid) {
        eprintln!("[director-cut] force-kill PID {pid} still listening on :{port}");
        let _ = Command::new("kill")
            .args(["-KILL", &pid.to_string()])
            .status();
    }
}

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
fn kill_tcp_listeners_on_port(_port: u16) {}

/// Release the backend listen port so uvicorn can bind (drops test socket; reclaims orphan listeners on Unix).
fn ensure_backend_port_available(port: u16) -> Result<(), String> {
    for attempt in 0..8 {
        match TcpListener::bind(("127.0.0.1", port)) {
            Ok(l) => {
                drop(l);
                return Ok(());
            }
            Err(e) if attempt == 0 || attempt == 7 => {
                eprintln!(
                    "[director-cut] port {port} blocked (attempt {}): {e}",
                    attempt + 1
                );
            }
            Err(_) => {}
        }
        kill_tcp_listeners_on_port(port);
        thread::sleep(Duration::from_millis(250));
    }
    Err(format!(
        "Port {port} is still in use. See: lsof -nP -iTCP:{port} -sTCP:LISTEN"
    ))
}

// ---------------------------------------------------------------------------
// Commands – exposed to the frontend via Tauri IPC
// ---------------------------------------------------------------------------

#[derive(Serialize, Deserialize)]
struct BackendStatus {
    running: bool,
    port: u16,
    message: String,
}

/// Start the Python backend server (respawns whenever called so `.env`/code reload).
#[tauri::command]
fn start_backend(state: State<AppState>) -> Result<BackendStatus, String> {
    let backend_dir = {
        let bd = state.backend_dir.lock().map_err(|e| e.to_string())?;
        let dir = bd.clone();
        ensure_venv(&dir);
        dir
    };

    let python = venv_python(&backend_dir);
    let mut proc = state.python_process.lock().map_err(|e| e.to_string())?;
    if let Some(mut child) = proc.take() {
        let _ = child.kill();
        let _ = child.wait();
    }

    ensure_backend_port_available(state.backend_port)?;

    let mut cmd = Command::new(&python);
    cmd.env("PYTHONUNBUFFERED", "1");
    apply_backend_dotenv(&mut cmd, &backend_dir);
    if let Some(res) = resolve_bundle_resources_dir() {
        cmd.env("DIRECTOR_RESOURCES_DIR", res.to_string_lossy().as_ref());
    }
    cmd.args([
        "-m",
        "uvicorn",
        "app.server:app",
        "--host",
        "127.0.0.1",
        "--port",
        &state.backend_port.to_string(),
    ])
    .current_dir(&backend_dir);
    // Optional compile-time bake for release/build:
    if let Some(k) = option_env!("DIRECTOR_EMBED_GROQ_API_KEY").filter(|s| !s.is_empty()) {
        cmd.env("GROQ_API_KEY", k);
    }
    if let Some(k) = option_env!("DIRECTOR_EMBED_FAL_KEY").filter(|s| !s.is_empty()) {
        cmd.env("FAL_KEY", k);
        cmd.env("FAL_API_KEY", k);
    }
    if let Some(k) = option_env!("DIRECTOR_EMBED_SUPABASE_URL").filter(|s| !s.is_empty()) {
        cmd.env("SUPABASE_URL", k);
        cmd.env("NEXT_PUBLIC_SUPABASE_URL", k); // in case python reads this name
    }
    if let Some(k) = option_env!("DIRECTOR_EMBED_SUPABASE_ANON_KEY").filter(|s| !s.is_empty()) {
        cmd.env("SUPABASE_ANON_KEY", k);
        cmd.env("NEXT_PUBLIC_SUPABASE_ANON_KEY", k);
    }
    if let Some(k) = option_env!("DIRECTOR_EMBED_SUPABASE_URL").filter(|s|!s.is_empty()) {
        eprintln!("[director-cut] RUST BAKED URL: {}", k);
        cmd.env("SUPABASE_URL", k);
        cmd.env("NEXT_PUBLIC_SUPABASE_URL", k);
    } else {
        eprintln!("[director-cut] RUST URL NOT BAKED");
    }
    eprintln!(
        "[director-cut] uvicorn cwd={} bin={:?}",
        backend_dir.display(),
        python
    );
    let child = cmd.spawn().map_err(|e| {
        format!(
            "Failed to start backend with {:?}: {e}. If port {} is stuck, quit other processes binding it.",
            python, state.backend_port
        )
    })?;
    *proc = Some(child);
    Ok(BackendStatus {
        running: true,
        port: state.backend_port,
        message: format!("Started with {:?}", python),
    })
}

/// Stop the Python backend server.
#[tauri::command]
fn stop_backend(state: State<AppState>) -> Result<(), String> {
    let mut proc = state.python_process.lock().map_err(|e| e.to_string())?;
    if let Some(mut child) = proc.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    Ok(())
}

/// Open a URL in the system browser (WKWebView blocks `window.open` for OAuth and external links).
#[tauri::command]
fn open_external_url(app: AppHandle, url: String) -> Result<(), String> {
    let u = url.trim();
    if !u.starts_with("https://") && !u.starts_with("http://") {
        return Err("Only http(s) URLs are allowed".into());
    }
    app.opener()
        .open_url(u, Option::<&str>::None)
        .map_err(|e| e.to_string())
}

/// Check backend health.
#[tauri::command]
async fn backend_health(state: State<'_, AppState>) -> Result<String, String> {
    let url = format!("http://127.0.0.1:{}/health", state.backend_port);
    let resp = reqwest::get(&url).await.map_err(|e| e.to_string())?;
    let body = resp.text().await.map_err(|e| e.to_string())?;
    Ok(body)
}

/// Proxy a request to the backend API.
#[tauri::command]
async fn api_request(
    state: State<'_, AppState>,
    method: String,
    path: String,
    body: Option<String>,
    authorization: Option<String>,
) -> Result<String, String> {
    let url = format!("http://127.0.0.1:{}{}", state.backend_port, path);
    let client = reqwest::Client::new();
    let mut req = match method.to_uppercase().as_str() {
        "GET" => client.get(&url),
        "POST" => {
            let r = client.post(&url).header("Content-Type", "application/json");
            if let Some(b) = &body {
                r.body(b.clone())
            } else {
                r
            }
        }
        "PUT" => {
            let r = client.put(&url).header("Content-Type", "application/json");
            if let Some(b) = &body {
                r.body(b.clone())
            } else {
                r
            }
        }
        "DELETE" => client.delete(&url),
        _ => return Err("Unsupported method".into()),
    };
    if let Some(h) = authorization {
        let t = h.trim();
        if !t.is_empty() {
            req = req.header("Authorization", t);
        }
    }
    let resp = req.send().await.map_err(|e| e.to_string())?;
    resp.text().await.map_err(|e| e.to_string())
}

/// Proxy MCP Streamable HTTP for the Vue/WebView (sessions + Bearer tokens).
#[tauri::command]
async fn mcp_request(
    backend: State<'_, AppState>,
    mcp_state: State<'_, McpHttpState>,
    payload: McpRequestPayload,
) -> Result<String, String> {
    let McpRequestPayload {
        path,
        method,
        body,
        auth_token,
    } = payload;
    let base = format!("http://127.0.0.1:{}/mcp", backend.backend_port);
    let url = if path.is_empty() {
        base
    } else if path.starts_with('/') {
        format!("{base}{path}")
    } else {
        format!("{base}/{path}")
    };

    let upper = method.to_uppercase();
    let mut req = match upper.as_str() {
        "GET" => mcp_state.client.get(&url),
        "POST" => {
            let mut r = mcp_state
                .client
                .post(&url)
                .header(CONTENT_TYPE, "application/json");
            if let Some(b) = body {
                r = r.body(b);
            }
            r
        }
        "DELETE" => {
            let mut r = mcp_state.client.delete(&url);
            if let Some(b) = body {
                r = r.body(b);
            }
            r
        }
        _ => return Err(format!("Unsupported MCP method {upper}")),
    };

    req = req.header(
        ACCEPT,
        "application/json, text/event-stream",
    );

    if let Some(t) = auth_token.filter(|s| !s.trim().is_empty()) {
        let bearer = format!("Bearer {}", t.trim());
        req = req.header(AUTHORIZATION, bearer);
    }

    let sid_snapshot = {
        let guard = mcp_state.session_id.lock().map_err(|e| e.to_string())?;
        guard.clone()
    };
    if !sid_snapshot.is_empty() {
        req = req.header("Mcp-Session-Id", sid_snapshot);
    }

    let resp = req.send().await.map_err(|e| e.to_string())?;
    let status = resp.status();

    let new_sid = resp
        .headers()
        .get("mcp-session-id")
        .and_then(|v| v.to_str().ok())
        .map(str::to_string);

    let text = resp.text().await.map_err(|e| e.to_string())?;

    if status.as_u16() == 401 {
        if let Ok(mut g) = mcp_state.session_id.lock() {
            g.clear();
        }
        return Err("MCP_AUTH_REQUIRED".into());
    }

    if let Some(ns) = new_sid {
        if let Ok(mut g) = mcp_state.session_id.lock() {
            *g = ns;
        }
    }

    Ok(text)
}

#[tauri::command]
async fn mcp_get_session(mcp_state: State<'_, McpHttpState>) -> Result<String, String> {
    let g = mcp_state.session_id.lock().map_err(|e| e.to_string())?;
    Ok(g.clone())
}

// ---------------------------------------------------------------------------
// App entry
// ---------------------------------------------------------------------------
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let bd = resolve_backend_dir(); // <-- You were missing () and ;
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(AppState {
            python_process: Mutex::new(None),
            backend_port: 9420,
            backend_dir: Mutex::new(bd), // <-- Now bd exists
        })
        .manage(McpHttpState::default())
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            open_external_url,
            backend_health,
            api_request,
            mcp_request,
            mcp_get_session,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}