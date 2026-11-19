// Hide the console window on Windows release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! CareerPilot AI desktop shell.
//!
//! The native layer is intentionally thin: all business logic, authorization
//! and approval rules live in the backend. The webview's only native ability
//! is keeping the session's refresh token in the OS credential store
//! (`session.rs`), granted per command in capabilities/main.json. No secrets
//! are compiled into this binary.

mod session;

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            session::load_refresh_token,
            session::store_refresh_token,
            session::clear_refresh_token
        ])
        .run(tauri::generate_context!())
        .expect("error while running CareerPilot AI");
}
