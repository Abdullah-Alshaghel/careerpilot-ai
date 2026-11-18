// Hide the console window on Windows release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! CareerPilot AI desktop shell.
//!
//! The native layer is intentionally thin: all business logic, authorization
//! and approval rules live in the backend. The webview is granted no IPC
//! permissions until a feature needs one (see capabilities/ and
//! docs/DESKTOP_RELEASE.md). No secrets are compiled into this binary.

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running CareerPilot AI");
}
