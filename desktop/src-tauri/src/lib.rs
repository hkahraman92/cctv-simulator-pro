//! Desktop shell: hosts the web UI and owns the Python core process.
//!
//! The core (`python -m cctv_simulator.service`, or the frozen `cctv-core.exe`
//! in a release) speaks JSON-RPC 2.0, one JSON object per line on
//! stdin/stdout. This side is a supervised pipe: it starts the process, writes
//! request lines from the UI, emits every stdout line back as a `core://frame`
//! event, forwards stderr as `core://log`, reports `core://exit`, and kills the
//! process when the window closes. Request/response matching lives in the UI
//! (`src/core/rpc.ts`) so this layer never has to understand the API.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::thread;

use tauri::{AppHandle, Emitter, Manager, RunEvent, State};

#[derive(Default)]
struct Core {
    inner: Mutex<Option<Running>>,
    /// Why the last start failed (shown by the UI's disconnected badge).
    last_error: Mutex<Option<String>>,
}

struct Running {
    child: Child,
    stdin: ChildStdin,
    generation: u64,
}

/// How to start the core. Order: `CCTV_CORE` env (a full command line for
/// debugging), then in a release build the bundled `core/cctv-core.exe`
/// resource (desktop/packaging/build_core.bat), in a debug build the source
/// tree's Python — so `tauri dev` always runs the code being edited.
fn core_command(app: &AppHandle) -> Result<Command, String> {
    if let Ok(cmdline) = std::env::var("CCTV_CORE") {
        let mut parts = cmdline.split_whitespace();
        let program = parts.next().ok_or("CCTV_CORE boş")?;
        let mut cmd = Command::new(program);
        cmd.args(parts);
        return Ok(cmd);
    }
    if !cfg!(debug_assertions) {
        let dir = app
            .path()
            .resource_dir()
            .map_err(|e| format!("Kaynak klasörü bulunamadı: {e}"))?
            .join("core");
        let exe = dir.join(if cfg!(windows) { "cctv-core.exe" } else { "cctv-core" });
        if !exe.is_file() {
            return Err(format!("Çekirdek bulunamadı: {}", exe.display()));
        }
        let mut cmd = Command::new(exe);
        cmd.current_dir(dir);
        return Ok(cmd);
    }
    // Development: repository root is two levels above src-tauri.
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    let mut cmd = if cfg!(windows) {
        let mut c = Command::new("py");
        c.args(["-3.13", "-m", "cctv_simulator.service"]);
        c
    } else {
        let mut c = Command::new("python3");
        c.args(["-m", "cctv_simulator.service"]);
        c
    };
    cmd.current_dir(repo);
    Ok(cmd)
}

fn spawn(app: &AppHandle, core: &Core) -> Result<(), String> {
    let result = spawn_inner(app, core);
    if let Ok(mut last) = core.last_error.lock() {
        *last = result.as_ref().err().cloned();
    }
    result
}

fn spawn_inner(app: &AppHandle, core: &Core) -> Result<(), String> {
    let mut slot = core.inner.lock().map_err(|_| "kilit bozuk")?;
    let generation = slot.as_ref().map_or(0, |r| r.generation) + 1;
    if let Some(mut old) = slot.take() {
        let _ = old.child.kill();
        let _ = old.child.wait();
    }

    let mut cmd = core_command(app)?;
    cmd.stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUNBUFFERED", "1");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Çekirdek başlatılamadı: {e}"))?;
    let stdin = child.stdin.take().ok_or("stdin yok")?;
    let stdout = child.stdout.take().ok_or("stdout yok")?;
    let stderr = child.stderr.take().ok_or("stderr yok")?;

    let out_app = app.clone();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(l) if !l.is_empty() => {
                    let _ = out_app.emit("core://frame", l);
                }
                Ok(_) => {}
                Err(_) => break,
            }
        }
        // stdout closed: the process ended (or is ending).
        let _ = out_app.emit("core://exit", generation);
    });
    let err_app = app.clone();
    thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            let _ = err_app.emit("core://log", line);
        }
    });

    *slot = Some(Running { child, stdin, generation });
    Ok(())
}

/// Write one JSON-RPC line to the core.
#[tauri::command]
fn core_send(line: String, core: State<'_, Core>) -> Result<(), String> {
    if line.contains('\n') {
        return Err("Bir istek tek satır olmalı".into());
    }
    let mut slot = core.inner.lock().map_err(|_| "kilit bozuk")?;
    let running = slot.as_mut().ok_or("Çekirdek çalışmıyor")?;
    running
        .stdin
        .write_all(line.as_bytes())
        .and_then(|_| running.stdin.write_all(b"\n"))
        .and_then(|_| running.stdin.flush())
        .map_err(|e| format!("Çekirdeğe yazılamadı: {e}"))
}

/// Why the core is not running, if a start failed.
#[tauri::command]
fn core_status(core: State<'_, Core>) -> Option<String> {
    core.last_error.lock().ok().and_then(|e| e.clone())
}

/// Kill and start the core again (UI uses this after `core://exit`).
#[tauri::command]
fn core_restart(app: AppHandle, core: State<'_, Core>) -> Result<(), String> {
    spawn(&app, &core)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(Core::default())
        .setup(|app| {
            let handle = app.handle().clone();
            let core = app.state::<Core>();
            if let Err(e) = spawn(&handle, &core) {
                // The UI shows the disconnected state; keep the window up.
                eprintln!("{e}");
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![core_send, core_restart, core_status])
        .build(tauri::generate_context!())
        .expect("Tauri uygulaması kurulamadı");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let core = handle.state::<Core>();
            if let Ok(mut slot) = core.inner.lock() {
                if let Some(mut running) = slot.take() {
                    let _ = running.child.kill();
                    let _ = running.child.wait();
                }
            };
        }
    });
}
