#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use std::path::PathBuf;
use std::sync::Mutex;

use serde::Serialize;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_deep_link::DeepLinkExt;
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

const HOTKEY: &str = "Alt+Space";
const HEADLESS_ARG: &str = "--headless";
const LOOPBACK_ANY_PORT: &str = "http://127.0.0.1:*";
const STATUS_EVERY: std::time::Duration = std::time::Duration::from_secs(5);

struct Shell {
    backend: Mutex<Option<backend::Backend>>,
}

#[derive(Serialize)]
struct Connection {
    base: String,
    token: String,
}

#[tauri::command]
fn connection(app: AppHandle) -> Result<Connection, String> {
    let runtime = backend::wait_for_runtime(backend_pid(&app))?;
    Ok(Connection {
        base: format!("http://127.0.0.1:{}", runtime.port),
        token: runtime.app_token,
    })
}

#[tauri::command]
fn restart_backend(app: AppHandle) -> Result<(), String> {
    let shell = app.state::<Shell>();
    let mut guard = shell.backend.lock().map_err(|e| e.to_string())?;
    if let Some(running) = guard.take() {
        running.stop();
    }
    *guard = Some(backend::start(&resources(&app)?)?);
    drop(guard);
    // Each window's security policy names the backend's port, which has changed.
    for window in app.webview_windows().values() {
        let _ = window.eval("window.location.reload()");
    }
    Ok(())
}

#[tauri::command]
fn show_in_finder(path: String) -> Result<(), String> {
    std::process::Command::new("open")
        .args(["-R", &path])
        .status()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn set_open_at_login(app: AppHandle, enabled: bool) -> Result<(), String> {
    let manager = app.autolaunch();
    if enabled {
        manager.enable().map_err(|e| e.to_string())
    } else {
        manager.disable().map_err(|e| e.to_string())
    }
}

#[tauri::command]
fn is_open_at_login(app: AppHandle) -> Result<bool, String> {
    app.autolaunch().is_enabled().map_err(|e| e.to_string())
}

#[tauri::command]
fn hide_quick(app: AppHandle) {
    if let Some(window) = app.get_webview_window("quick") {
        let _ = window.hide();
    }
}

#[tauri::command]
fn open_main(app: AppHandle, route: String) {
    hide_quick(app.clone());
    show_window(&app, "main", &route);
    let _ = app.emit_to("main", "open-route", route);
}

#[tauri::command]
fn install_extension(app: AppHandle) -> Result<(), String> {
    let extension = resources(&app)?.join("securefolio.mcpb");
    std::process::Command::new("open")
        .arg(extension)
        .status()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn bridge_path(app: AppHandle) -> Result<String, String> {
    Ok(resources(&app)?
        .join("securefolio-mcp")
        .to_string_lossy()
        .into_owned())
}

fn backend_pid(app: &AppHandle) -> Option<u32> {
    let shell = app.state::<Shell>();
    let guard = shell.backend.lock().ok()?;
    guard.as_ref().and_then(backend::Backend::pid)
}

// Tauri can't place a bundle still in the build folder, so it's found from the program.
fn resources(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .resource_dir()
        .or_else(|_| resources_beside(&std::env::current_exe()?))
        .map_err(|e| e.to_string())
}

fn resources_beside(program: &std::path::Path) -> Result<PathBuf, std::io::Error> {
    program
        .parent()
        .and_then(std::path::Path::parent)
        .map(|contents| contents.join("Resources"))
        .ok_or_else(|| std::io::Error::other("the program isn't in a bundle"))
}

fn show_window(app: &AppHandle, label: &str, route: &str) {
    if let Some(window) = app.get_webview_window(label) {
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    if let Err(error) = build_window(app, label, route, true) {
        eprintln!("couldn't open a window: {error}");
    }
}

// The app's page may connect to the backend's own port and no other. The configured
// policy can't name it, so the header is filled in as each page loads.
fn with_backend_port(app: &AppHandle, policy: &str) -> Option<String> {
    let port = backend::port_of(backend_pid(app)).ok()?;
    Some(policy.replace(LOOPBACK_ANY_PORT, &format!("http://127.0.0.1:{port}")))
}

fn build_window(
    app: &AppHandle,
    label: &str,
    route: &str,
    visible: bool,
) -> tauri::Result<tauri::WebviewWindow> {
    let url = WebviewUrl::App(format!("index.html#{route}").into());
    let handle = app.clone();
    let builder = WebviewWindowBuilder::new(app, label, url)
        .visible(visible)
        .on_web_resource_request(move |_request, response| {
            let name = "Content-Security-Policy";
            let filled = response
                .headers()
                .get(name)
                .and_then(|value| value.to_str().ok())
                .and_then(|policy| with_backend_port(&handle, policy));
            if let Some(value) = filled.and_then(|policy| policy.parse().ok()) {
                response.headers_mut().insert(name, value);
            }
        });
    if label == "quick" {
        builder
            .title("SecureFolio quick search")
            .inner_size(640.0, 420.0)
            .always_on_top(true)
            .decorations(false)
            .center()
            .build()
    } else {
        builder
            .title("SecureFolio")
            .inner_size(1280.0, 800.0)
            .build()
    }
}

// The menu bar's first line. A thread of its own, as the window may be closed.
fn keep_status_current(app: AppHandle, status: MenuItem<tauri::Wry>) {
    std::thread::spawn(move || loop {
        if let Ok(line) = backend::status_line(backend_pid(&app)) {
            let _ = status.set_text(line);
        }
        std::thread::sleep(STATUS_EVERY);
    });
}

fn toggle_quick_search(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("quick") {
        if window.is_visible().unwrap_or(false) {
            let _ = window.hide();
            return;
        }
    }
    show_window(app, "quick", "/quick");
}

// securefolio://doc/<id>/rev/<rev>#chunk=<chunk> opens the viewer at that passage.
// docsynth:// is the same link from before the rename, as older answers still hold.
const LINK_SCHEMES: [&str; 2] = ["securefolio://", "docsynth://"];

fn is_link(arg: &str) -> bool {
    LINK_SCHEMES.iter().any(|scheme| arg.starts_with(scheme))
}

fn route_for_link(link: &str) -> Option<String> {
    let rest = LINK_SCHEMES
        .iter()
        .find_map(|scheme| link.strip_prefix(scheme)?.strip_prefix("doc/"))?;
    let (path, fragment) = rest.split_once('#').unwrap_or((rest, ""));
    let id = path.split('/').next()?;
    let chunk = fragment.strip_prefix("chunk=").unwrap_or("");
    Some(format!("/doc/{id}?chunk={chunk}"))
}

fn open_link(app: &AppHandle, link: &str) {
    if let Some(route) = route_for_link(link) {
        // A window just built starts at the place. One already open is told to go.
        let already_open = app.get_webview_window("main").is_some();
        show_window(app, "main", &route);
        if already_open {
            let _ = app.emit("open-route", route);
        }
    }
}

const TRAY_TEMPLATE: &[u8] = include_bytes!("../icons/tray-template.png");

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let status = MenuItem::with_id(app, "status", "SecureFolio", false, None::<&str>)?;
    keep_status_current(app.clone(), status.clone());
    let open = MenuItem::with_id(app, "open", "Open SecureFolio", true, None::<&str>)?;
    let quick = MenuItem::with_id(app, "quick", "Quick search", true, Some(HOTKEY))?;
    let pause = MenuItem::with_id(app, "pause", "Pause or resume indexing", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit SecureFolio", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&status, &open, &quick, &pause, &quit])?;
    let mut builder = TrayIconBuilder::with_id("main")
        .menu(&menu)
        .icon_as_template(true)
        .tooltip("SecureFolio")
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_window(app, "main", "/home"),
            "quick" => toggle_quick_search(app),
            "pause" => {
                if let Err(error) = backend::toggle_pause(backend_pid(app)) {
                    eprintln!("couldn't change indexing: {error}");
                }
            }
            "quit" => app.exit(0),
            _ => {}
        });
    // The logo's outline alone, black on clear, as a template image macOS tints to
    // match the menu bar; scripts/make-icons.mjs makes it from the logo.
    builder = builder.icon(Image::from_bytes(TRAY_TEMPLATE)?);
    builder.build(app)?;
    Ok(())
}

fn main() {
    let headless = std::env::args().any(|arg| arg == HEADLESS_ARG);
    let built = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            if let Some(link) = args.iter().find(|arg| is_link(arg)) {
                open_link(app, link);
            } else if !args.iter().any(|arg| arg == HEADLESS_ARG) {
                show_window(app, "main", "/home");
            }
        }))
        .plugin(tauri_plugin_deep_link::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        toggle_quick_search(app);
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            connection,
            restart_backend,
            show_in_finder,
            set_open_at_login,
            is_open_at_login,
            install_extension,
            bridge_path,
            hide_quick,
            open_main
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            let started = backend::start(&resources(&handle)?)?;
            app.manage(Shell {
                backend: Mutex::new(Some(started)),
            });
            build_tray(&handle)?;
            handle.global_shortcut().register(HOTKEY)?;
            let links = handle.clone();
            app.deep_link().on_open_url(move |event| {
                for url in event.urls() {
                    open_link(&links, url.as_str());
                }
            });
            if !headless {
                show_window(&handle, "main", "/home");
                // Built ahead of time, hidden, so the hotkey only has to show it.
                if let Err(error) = build_window(&handle, "quick", "/quick", false) {
                    eprintln!("couldn't prepare quick search: {error}");
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing a window leaves the app in the menu bar, so MCP keeps working.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!());
    let app = match built {
        Ok(app) => app,
        Err(error) => {
            eprintln!("SecureFolio couldn't start: {error}");
            std::process::exit(1);
        }
    };
    app.run(|handle, event| match event {
        // Opening the app again, from the Dock or Finder, while it's in the menu bar.
        RunEvent::Reopen { .. } => show_window(handle, "main", "/home"),
        RunEvent::Exit => {
            if let Ok(mut guard) = handle.state::<Shell>().backend.lock() {
                if let Some(running) = guard.take() {
                    running.stop();
                }
            }
        }
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    #[test]
    fn resources_sit_beside_the_program_in_a_bundle() {
        let program = std::path::Path::new("/a/SecureFolio.app/Contents/MacOS/securefolio");
        let found = crate::resources_beside(program).expect("a bundle");
        assert_eq!(
            found,
            std::path::Path::new("/a/SecureFolio.app/Contents/Resources")
        );
    }

    #[test]
    fn the_status_line_says_how_the_queue_stands() {
        let busy = serde_json::json!({"active": 3, "done": 48, "total": 51});
        assert_eq!(crate::backend::describe_counts(&busy), "Indexing 48 of 51");
        let idle = serde_json::json!({"active": 0});
        assert_eq!(crate::backend::describe_counts(&idle), "Up to date");
    }

    use super::route_for_link;

    #[test]
    fn a_passage_link_becomes_a_viewer_route() {
        assert_eq!(
            route_for_link("securefolio://doc/d1/rev/r2#chunk=c3").as_deref(),
            Some("/doc/d1?chunk=c3")
        );
    }

    #[test]
    fn a_link_from_before_the_rename_still_opens() {
        assert_eq!(
            route_for_link("docsynth://doc/d1/rev/r2#chunk=c3").as_deref(),
            Some("/doc/d1?chunk=c3")
        );
    }

    #[test]
    fn anything_else_is_ignored() {
        assert_eq!(route_for_link("https://evil.example"), None);
    }
}
