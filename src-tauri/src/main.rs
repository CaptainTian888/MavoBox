// Tauri shell: starts the packaged Python backend (tiktol-server) as a child process,
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
        Err(_) => {
            let _ = child.kill();
            Err("后端在 60 秒内未就绪（或无法读取已运行实例的地址）".into())
        }
    }
}

fn main() {
    let app = tauri::Builder::default()
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            let exe = server_exe(app.handle());
            let (child, url) = start_backend(&exe)?;
            *app.state::<Backend>().0.lock().unwrap() = Some(child);
            let parsed = url
                .parse()
                .map_err(|_| format!("后端返回了无效地址: {url}"))?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(parsed))
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
