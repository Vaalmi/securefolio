//! A small stdio bridge: reads JSON-RPC lines, forwards each to the backend's MCP
//! route with the MCP token, and writes back the answer. It holds no state, and
//! reads runtime.json afresh when a call can't get through, since the port and
//! tokens change on every launch.

use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

use serde::Deserialize;
use serde_json::{json, Value};

const VAULT_ENV: &str = "SECUREFOLIO_VAULT";
// A development .env from before the rename names its vault by the old variable.
const LEGACY_VAULT_ENV: &str = "DOCSYNTH_VAULT";
const NO_LAUNCH_ENV: &str = "SECUREFOLIO_NO_LAUNCH";
const APP_NAME: &str = "SecureFolio";
const START_LIMIT: Duration = Duration::from_secs(90);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(120);

#[derive(Deserialize, Clone)]
struct Runtime {
    port: u16,
    mcp_token: String,
}

fn runtime_path() -> PathBuf {
    if let Ok(vault) = std::env::var(VAULT_ENV).or_else(|_| std::env::var(LEGACY_VAULT_ENV)) {
        return Path::new(&vault).join("runtime.json");
    }
    let home = std::env::var("HOME").unwrap_or_default();
    Path::new(&home).join("Library/Application Support/SecureFolio/runtime.json")
}

fn read_runtime() -> Option<Runtime> {
    let text = std::fs::read_to_string(runtime_path()).ok()?;
    serde_json::from_str(&text).ok()
}

fn launch_headless() {
    if std::env::var(NO_LAUNCH_ENV).is_ok() {
        return;
    }
    let _ = Command::new("open")
        .args(["-g", "-a", APP_NAME, "--args", "--headless"])
        .status();
}

fn wait_for_backend() -> Result<Runtime, String> {
    let begun = Instant::now();
    let mut launched = false;
    loop {
        if let Some(runtime) = read_runtime() {
            if answers(&runtime) {
                return Ok(runtime);
            }
        }
        if !launched {
            launch_headless();
            launched = true;
        }
        if begun.elapsed() > START_LIMIT {
            return Err("SecureFolio didn't start.".into());
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

fn answers(runtime: &Runtime) -> bool {
    std::net::TcpStream::connect_timeout(
        &std::net::SocketAddr::from(([127, 0, 0, 1], runtime.port)),
        Duration::from_millis(500),
    )
    .is_ok()
}

fn forward(runtime: &Runtime, message: &Value) -> Result<Option<Value>, String> {
    let url = format!("http://127.0.0.1:{}/mcp", runtime.port);
    let response = ureq::post(&url)
        .timeout(REQUEST_TIMEOUT)
        .set("Authorization", &format!("Bearer {}", runtime.mcp_token))
        .set("Accept", "application/json, text/event-stream")
        .set("MCP-Protocol-Version", "2025-06-18")
        .send_json(message);
    match response {
        Ok(answer) if answer.status() == 202 => Ok(None),
        Ok(answer) => answer
            .into_json::<Value>()
            .map(Some)
            .map_err(|e| e.to_string()),
        Err(ureq::Error::Status(code, answer)) => {
            let detail = answer
                .into_json::<Value>()
                .ok()
                .and_then(|body| body.get("detail").and_then(Value::as_str).map(String::from))
                .unwrap_or_else(|| "The library refused this call.".into());
            Err(format!("{code}: {detail}"))
        }
        Err(error) => Err(error.to_string()),
    }
}

fn error_reply(id: &Value, message: &str) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "error": {"code": -32000, "message": message}})
}

fn handle(line: &str) -> Option<Value> {
    let message: Value = match serde_json::from_str(line) {
        Ok(value) => value,
        Err(_) => return Some(error_reply(&Value::Null, "That wasn't valid JSON.")),
    };
    let id = message.get("id").cloned().unwrap_or(Value::Null);
    let reply = wait_for_backend().and_then(|runtime| forward(&runtime, &message));
    match reply {
        Ok(answer) => answer,
        // A notification (no id) gets no reply, even when it can't be delivered.
        Err(_) if id.is_null() => None,
        Err(reason) => Some(error_reply(&id, &reason)),
    }
}

fn main() {
    let stdin = io::stdin();
    let mut stdout = io::stdout();
    for line in stdin.lock().lines().map_while(Result::ok) {
        if line.trim().is_empty() {
            continue;
        }
        if let Some(reply) = handle(&line) {
            if writeln!(stdout, "{reply}").is_err() || stdout.flush().is_err() {
                return;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invalid_json_gets_an_error_reply() {
        let reply = handle("not json").expect("a reply");
        assert_eq!(reply["id"], Value::Null);
        assert!(reply["error"]["message"].is_string());
    }

    #[test]
    fn an_error_reply_keeps_the_id() {
        let reply = error_reply(&json!(7), "no");
        assert_eq!(reply["id"], 7);
    }

    #[test]
    fn the_runtime_file_needs_its_fields() {
        let parsed: Result<Runtime, _> = serde_json::from_str(r#"{"port": 1}"#);
        assert!(parsed.is_err());
    }
}
