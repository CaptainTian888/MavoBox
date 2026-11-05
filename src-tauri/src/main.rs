// Tauri shell: starts the packaged Python backend (tiktol-server) as a child process,
// waits for its "TIKTOL_READY <url>" line, then opens a window pointing at that URL.
// The child is killed when the app exits.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

struct Backend(Mutex<Option<Child>>);

fn server_exe(app: &tauri::AppHandle) -> PathBuf {
    // Installed: <resources>/tiktol-server/tiktol-server.exe
    if let Ok(dir) = app.path().resource_dir() {
        let p = dir.join("tiktol-server").join("tiktol-server.exe");
        if p.exists() {
            return p;
        }
    }
    // Dev: built by scripts/build.cmd
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../backend/dist/tiktol-server/tiktol-server.exe")
}

fn main() {
    let app = tauri::Builder::default()
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            let exe = server_exe(app.handle());
            let mut child = Command::new(&exe)
                .args(["--no-browser", "--port", "8000"])
                .stdout(Stdio::piped())
                .spawn()
                .map_err(|e| format!("无法启动后端 {}: {e}", exe.display()))?;
            let stdout = child.stdout.take().ok_or("后端无输出管道")?;
            let mut url = None;
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if let Some(u) = line.strip_prefix("TIKTOL_READY ") {
                    url = Some(u.trim().to_string());
                    break;
                }
            }
            *app.state::<Backend>().0.lock().unwrap() = Some(child);
            let url = url.ok_or("后端启动失败：未收到 READY 信号")?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url.parse().unwrap()))
                .title("TikTol")
                .inner_size(1280.0, 860.0)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build tauri app");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(mut child) = handle.state::<Backend>().0.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}
