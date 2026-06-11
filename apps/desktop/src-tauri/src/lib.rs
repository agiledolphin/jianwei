//! 见微 Jianwei 桌面壳：窗口 + Python 引擎 sidecar 生命周期，无业务逻辑。
//!
//! 启动时取空闲端口、生成随机 token，拉起 `jianwei serve`；
//! 前端经 `engine_info` 命令取得 {port, token} 后直连引擎 HTTP。
//! 应用退出时回收引擎进程。

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

struct Engine {
    port: u16,
    token: String,
    child: Mutex<Option<Child>>,
}

#[tauri::command]
fn engine_info(state: tauri::State<Engine>) -> serde_json::Value {
    serde_json::json!({ "port": state.port, "token": state.token })
}

fn free_port() -> u16 {
    // 绑定后立刻释放，本机自用场景下竞态可忽略
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .expect("no free port")
}

fn random_token() -> String {
    use std::hash::{BuildHasher, Hasher, RandomState};
    let mut t = String::new();
    for _ in 0..4 {
        let mut h = RandomState::new().build_hasher();
        h.write_u64(std::process::id() as u64);
        t.push_str(&format!("{:016x}", h.finish()));
    }
    t
}

/// 开发期用 uv 跑仓库内引擎；分发期改为 PyInstaller 产物（JIANWEI_ENGINE_BIN 覆盖）。
fn spawn_engine(port: u16, token: &str) -> std::io::Result<Child> {
    let mut cmd = match std::env::var("JIANWEI_ENGINE_BIN") {
        Ok(bin) => Command::new(bin),
        Err(_) => {
            let engine_dir: PathBuf = [env!("CARGO_MANIFEST_DIR"), "..", "..", "..", "engine"]
                .iter()
                .collect();
            let mut c = Command::new("uv");
            c.arg("run").current_dir(engine_dir).arg("jianwei");
            c
        }
    };
    cmd.args(["serve", "--port", &port.to_string(), "--token", token]);
    if !cfg!(debug_assertions) {
        if let Ok(home) = std::env::var("HOME") {
            cmd.env("JIANWEI_DATA_DIR", format!("{home}/.jianwei/data"));
        }
    }
    cmd.spawn()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = free_port();
    let token = random_token();
    let child = spawn_engine(port, &token).expect("failed to spawn jianwei engine");

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Engine { port, token, child: Mutex::new(Some(child)) })
        .invoke_handler(tauri::generate_handler![engine_info])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                let engine = app.state::<Engine>();
                let taken = engine.child.lock().unwrap().take();
                if let Some(mut c) = taken {
                    let _ = c.kill();
                    let _ = c.wait();
                }
            }
        });
}
