//! Starts the Python backend in its own process group, and finds it again through
//! runtime.json.

use std::io::{Read, Write};
use std::net::TcpStream;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::time::{Duration, Instant};

use serde::Deserialize;

const VAULT_ENV: &str = "SECUREFOLIO_VAULT";
// A development .env from before the rename names its vault by the old variable.
const LEGACY_VAULT_ENV: &str = "DOCSYNTH_VAULT";
const DEV_BACKEND_ENV: &str = "SECUREFOLIO_DEV_BACKEND";
const STARTUP_LIMIT: Duration = Duration::from_secs(60);
/// A job in progress may take most of a minute. It's safe to kill: it runs again.
const STOP_LIMIT: Duration = Duration::from_secs(3);

#[derive(Deserialize)]
pub struct Runtime {
    pub pid: u32,
    pub port: u16,
    pub app_token: String,
}

pub struct Backend {
    child: Option<Child>,
}

impl Backend {
    pub fn pid(&self) -> Option<u32> {
        self.child.as_ref().map(Child::id)
    }

    /// Ends the whole process group, the worker included.
    pub fn stop(mut self) {
        if let Some(child) = self.child.as_mut() {
            stop_group(child, STOP_LIMIT);
        }
    }
}

/// Asks the group to end, and makes it after `limit`, so quitting never waits on a job.
fn stop_group(child: &mut Child, limit: Duration) {
    let group = child.id() as i32;
    // SAFETY: killpg takes plain integers; the group is our child's own.
    unsafe {
        libc::killpg(group, libc::SIGTERM);
    }
    let deadline = Instant::now() + limit;
    while Instant::now() < deadline {
        if !matches!(child.try_wait(), Ok(None)) {
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    // SAFETY: as above. Nothing happens if the group has already gone.
    unsafe {
        libc::killpg(group, libc::SIGKILL);
    }
    let _ = child.wait();
}

fn vault() -> PathBuf {
    if let Ok(configured) = std::env::var(VAULT_ENV).or_else(|_| std::env::var(LEGACY_VAULT_ENV)) {
        return PathBuf::from(configured);
    }
    let home = std::env::var("HOME").unwrap_or_default();
    Path::new(&home).join("Library/Application Support/SecureFolio")
}

fn runtime_path() -> PathBuf {
    vault().join("runtime.json")
}

pub fn start(resources: &Path) -> Result<Backend, String> {
    if std::env::var(DEV_BACKEND_ENV).is_ok() {
        return Ok(Backend { child: None });
    }
    let code = resources.join("code");
    let child = Command::new(resources.join("python/bin/python3"))
        .args(["-m", "backend.main"])
        .env("PYTHONPATH", &code)
        .env("PYTHONNOUSERSITE", "1")
        // Compiled when bundled. Writing here would break the app's signature.
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env_remove("PYTHONHOME")
        .current_dir(&code)
        .process_group(0)
        .spawn()
        .map_err(|e| format!("couldn't start the library service: {e}"))?;
    Ok(Backend { child: Some(child) })
}

/// The backend's address once `expected` (our own child, when we started one) has
/// written it; a file left by an older backend doesn't count.
pub fn wait_for_runtime(expected: Option<u32>) -> Result<Runtime, String> {
    let begun = Instant::now();
    loop {
        if let Ok(text) = std::fs::read_to_string(runtime_path()) {
            if let Ok(runtime) = serde_json::from_str::<Runtime>(&text) {
                if expected.is_none() || expected == Some(runtime.pid) {
                    return Ok(runtime);
                }
            }
        }
        if begun.elapsed() > STARTUP_LIMIT {
            return Err("The library service didn't start.".into());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

/// The menu bar's Pause, through the backend's own route: nothing else writes settings.
/// A plain request on loopback, so the token never appears in a process list.
fn ask_backend(expected: Option<u32>, method: &str, path: &str) -> Result<String, String> {
    let runtime = wait_for_runtime(expected)?;
    let mut stream = TcpStream::connect(("127.0.0.1", runtime.port)).map_err(|e| e.to_string())?;
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        runtime.port, runtime.app_token
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|e| e.to_string())?;
    let mut answer = String::new();
    stream
        .read_to_string(&mut answer)
        .map_err(|e| e.to_string())?;
    Ok(answer)
}

pub fn toggle_pause(expected: Option<u32>) -> Result<(), String> {
    ask_backend(expected, "POST", "/api/indexing/toggle-pause").map(|_| ())
}

// What the menu bar says about the queue.
pub fn status_line(expected: Option<u32>) -> Result<String, String> {
    let answer = ask_backend(expected, "GET", "/api/jobs/counts")?;
    let body = answer.split("\r\n\r\n").nth(1).unwrap_or("");
    let counts: serde_json::Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
    Ok(describe_counts(&counts))
}

pub fn describe_counts(counts: &serde_json::Value) -> String {
    let active = counts["active"].as_i64().unwrap_or(0);
    if active == 0 {
        return "Up to date".to_string();
    }
    let done = counts["done"].as_i64().unwrap_or(0);
    let total = counts["total"].as_i64().unwrap_or(0);
    format!("Indexing {done} of {total}")
}

pub fn port_of(expected: Option<u32>) -> Result<u16, String> {
    wait_for_runtime(expected).map(|runtime| runtime.port)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quitting_never_waits_on_something_that_ignores_the_signal() {
        let mut child = Command::new("sh")
            .args(["-c", "trap '' TERM; sleep 30 & wait"])
            .process_group(0)
            .spawn()
            .expect("sh starts");
        std::thread::sleep(Duration::from_millis(200));
        let begun = Instant::now();
        stop_group(&mut child, Duration::from_millis(300));
        assert!(begun.elapsed() < Duration::from_secs(5));
        assert!(child.try_wait().expect("it can be asked").is_some());
    }
}
