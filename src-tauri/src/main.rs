// Tauri shell: starts the packaged Python backend (mavobox-server) as a child process,
// waits (with a timeout) for its "TIKTOL_READY <url>" line, then opens a window on that URL.
// On Windows the child is placed in a kill-on-close Job Object, so the backend and anything
// it spawns (Playwright / Chromium) die with the shell even if the shell itself is killed.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

struct Backend(Mutex<Option<Child>>);

#[cfg(windows)]
const SERVER_BIN: &str = "mavobox-server.exe";
#[cfg(not(windows))]
const SERVER_BIN: &str = "mavobox-server";

fn server_exe(app: &tauri::AppHandle) -> PathBuf {
    // Installed: <resources>/mavobox-server/<SERVER_BIN>
    if let Ok(dir) = app.path().resource_dir() {
        let p = dir.join("mavobox-server").join(SERVER_BIN);
        if p.exists() {
            return p;
        }
    }
    // Dev: built by scripts/build.cmd
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../backend/dist/mavobox-server")
        .join(SERVER_BIN)
}

#[cfg(windows)]
fn kill_with_parent(child: &Child) {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::*;
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return;
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        AssignProcessToJobObject(job, child.as_raw_handle() as _);
        // The job handle is intentionally leaked: when this process ends, Windows closes it
        // and terminates every process still in the job.
    }
}

#[cfg(not(windows))]
fn kill_with_parent(_child: &Child) {}

fn start_backend(exe: &PathBuf) -> Result<(Child, String), String> {
    let mut cmd = Command::new(exe);
    cmd.args(["--no-browser", "--port", "8000"]).stdout(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("无法启动后端 {}: {e}", exe.display()))?;
    kill_with_parent(&child);
    let stdout = child.stdout.take().ok_or("后端无输出管道")?;
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(u) = line.strip_prefix("TIKTOL_READY ") {
                let _ = tx.send(u.trim().to_string());
                return;
            }
        }
    });
    match rx.recv_timeout(Duration::from_secs(60)) {
        Ok(url) => Ok((child, url)),
        Err(mpsc::RecvTimeoutError::Timeout) => {
            let _ = child.kill();
            Err("后端在 60 秒内没有就绪。可能是电脑较慢、被杀毒软件扫描或拦截。".into())
        }
        Err(mpsc::RecvTimeoutError::Disconnected) => {
            // The backend closed its output before saying it was ready: it exited on startup.
            let code = child.wait().ok().and_then(|s| s.code());
            Err(format!(
                "后端启动后立即退出（退出码 {}）。常见原因：被杀毒软件拦截、数据目录无法写入、已有另一个实例异常占用。",
                code.map(|c| c.to_string()).unwrap_or_else(|| "未知".into())
            ))
        }
    }
}

/// Percent-encodes a message for use in a query string (std has no helper for this).
fn encode_component(text: &str) -> String {
    let mut out = String::new();
    for b in text.bytes() {
        if b.is_ascii_alphanumeric() || b == b'-' || b == b'_' || b == b'.' {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{:02X}", b));
        }
    }
    out
}

/// Checks the update feed once at startup (silently ignores network/feed errors) and, if the
/// user agrees, downloads + installs the signed update and restarts. Runs on its own thread so
/// the window is never blocked.
fn check_for_update(app: tauri::AppHandle) {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
    use tauri_plugin_updater::UpdaterExt;
    std::thread::spawn(move || {
        // MAVOBOX_UPDATE_URL: use another update feed (for testing the update flow). Updates must still
        // carry a valid signature for the public key embedded in tauri.conf.json, so this cannot be
        // used to install unsigned code; plain http is refused unless the build explicitly allows it.
        let built = match std::env::var("MAVOBOX_UPDATE_URL").ok().and_then(|u| u.parse::<tauri::Url>().ok()) {
            Some(url) => app.updater_builder().endpoints(vec![url]).and_then(|b| b.build()),
            None => app.updater(),
        };
        let Ok(updater) = built else { return };
        let Ok(Some(update)) = tauri::async_runtime::block_on(updater.check()) else { return };
        // MAVOBOX_UPDATE_SILENT=1 installs without asking (used by automated update tests).
        let silent = std::env::var("MAVOBOX_UPDATE_SILENT").map(|v| v == "1").unwrap_or(false);
        let wants = silent || app
            .dialog()
            .message(format!(
                "发现新版本 {}。
是否现在更新？更新后应用会自动重启，你的数据和配置会保留。",
                update.version
            ))
            .title("MAVO BOX 更新")
            .buttons(MessageDialogButtons::OkCancelCustom("立即更新".into(), "稍后".into()))
            .blocking_show();
        if !wants {
            return;
        }
        if tauri::async_runtime::block_on(update.download_and_install(|_, _| {}, || {})).is_ok() {
            app.restart();
        }
    });
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            // Show a window right away (a small local "starting…" page) instead of nothing for the
            // seconds the backend needs; then point it at the backend, or at an error page.
            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("loading.html".into()))
                .title("MAVO BOX")
                .inner_size(1280.0, 860.0)
                .build()?;
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let exe = server_exe(&handle);
                match start_backend(&exe) {
                    Ok((child, url)) => {
                        *handle.state::<Backend>().0.lock().unwrap() = Some(child);
                        match url.parse::<tauri::Url>() {
                            Ok(parsed) => {
                                let _ = window.navigate(parsed);
                                check_for_update(handle.clone());
                            }
                            Err(_) => {
                                let msg = encode_component(&format!("后端返回了无效地址: {url}"));
                                let _ = window.eval(&format!("location.replace('failed.html?msg={msg}')"));
                            }
                        }
                    }
                    Err(message) => {
                        let msg = encode_component(&message);
                        let _ = window.eval(&format!("location.replace('failed.html?msg={msg}')"));
                    }
                }
            });
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
