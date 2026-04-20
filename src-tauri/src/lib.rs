use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::State;

// ---------------------------------------------------------------------------
// App state
// ---------------------------------------------------------------------------

struct AppState {
    python_process: Mutex<Option<Child>>,
    backend_port: u16,
    backend_dir: Mutex<PathBuf>,
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

fn venv_python(backend_dir: &PathBuf) -> PathBuf {
    let venv_py = backend_dir.join("venv").join("bin").join("python");
    if venv_py.exists() {
        venv_py
    } else {
        PathBuf::from("python3")
    }
}

/// Ensure the venv exists and deps are installed (for bundled app).
fn ensure_venv(backend_dir: &PathBuf) {
    // Ensure data dir exists for SQLite
    let data_dir = backend_dir.join("data");
    if !data_dir.exists() {
        let _ = std::fs::create_dir_all(&data_dir);
    }

    let venv_dir = backend_dir.join("venv");
    if venv_dir.join("bin").join("python").exists() {
        return; // already set up
    }
    // Create venv
    let _ = Command::new("python3")
        .args(["-m", "venv", "venv"])
        .current_dir(backend_dir)
        .output();
    // Install deps
    let pip = venv_dir.join("bin").join("pip");
    if pip.exists() {
        let _ = Command::new(&pip)
            .args(["install", "-q", "fastapi", "uvicorn[standard]", "aiosqlite", "httpx", "python-dotenv", "groq"])
            .current_dir(backend_dir)
            .output();
    }
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

/// Start the Python backend server.
#[tauri::command]
fn start_backend(state: State<AppState>) -> Result<BackendStatus, String> {
    let mut proc = state.python_process.lock().map_err(|e| e.to_string())?;
    if proc.is_some() {
        return Ok(BackendStatus { running: true, port: state.backend_port, message: "Already running".into() });
    }
    let bd = state.backend_dir.lock().map_err(|e| e.to_string())?;
    ensure_venv(&bd);
    let python = venv_python(&bd);
    let child = Command::new(&python)
        .args(["-m", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", &state.backend_port.to_string()])
        .current_dir(&*bd)
        .spawn()
        .map_err(|e| format!("Failed to start backend with {:?}: {e}", python))?;
    *proc = Some(child);
    Ok(BackendStatus { running: true, port: state.backend_port, message: format!("Started with {:?}", python) })
}

/// Stop the Python backend server.
#[tauri::command]
fn stop_backend(state: State<AppState>) -> Result<(), String> {
    let mut proc = state.python_process.lock().map_err(|e| e.to_string())?;
    if let Some(ref mut child) = *proc {
        child.kill().map_err(|e| e.to_string())?;
    }
    *proc = None;
    Ok(())
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
) -> Result<String, String> {
    let url = format!("http://127.0.0.1:{}{}", state.backend_port, path);
    let client = reqwest::Client::new();
    let req = match method.to_uppercase().as_str() {
        "GET" => client.get(&url),
        "POST" => {
            let r = client.post(&url).header("Content-Type", "application/json");
            if let Some(b) = body { r.body(b) } else { r }
        }
        "PUT" => {
            let r = client.put(&url).header("Content-Type", "application/json");
            if let Some(b) = body { r.body(b) } else { r }
        }
        "DELETE" => client.delete(&url),
        _ => return Err("Unsupported method".into()),
    };
    let resp = req.send().await.map_err(|e| e.to_string())?;
    resp.text().await.map_err(|e| e.to_string())
}

// ---------------------------------------------------------------------------
// App entry
// ---------------------------------------------------------------------------

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let bd = resolve_backend_dir();
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(AppState {
            python_process: Mutex::new(None),
            backend_port: 9420,
            backend_dir: Mutex::new(bd),
        })
        .invoke_handler(tauri::generate_handler![
            start_backend,
            stop_backend,
            backend_health,
            api_request,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
