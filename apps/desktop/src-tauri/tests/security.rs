//! Desktop security-boundary tests (critical test #17).
//! They read the shipped configuration and fail if privileges widen silently.

use serde_json::Value;
use std::fs;
use std::path::Path;

/// Every permission the frontend may hold. Extending this list is a reviewed,
/// deliberate change.
///
/// Phase 2: the three refresh-token keychain commands (src/session.rs). They
/// accept only refresh-token-shaped values and touch no other keychain entry.
const ALLOWED_PERMISSIONS: &[&str] = &[
    "allow-load-refresh-token",
    "allow-store-refresh-token",
    "allow-clear-refresh-token",
    // Phase 4: open company careers pages in the system browser. Scope pinned
    // to https:// below (opener_can_only_open_https_urls).
    "opener:allow-open-url",
];

/// Commands the app registers. Each must be declared in build.rs's app
/// manifest, so none is callable without an explicit permission.
const APP_COMMANDS: &[&str] = &["load_refresh_token", "store_refresh_token", "clear_refresh_token"];

#[test]
fn every_registered_command_is_gated_by_the_app_manifest() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let build = fs::read_to_string(root.join("build.rs")).expect("build.rs");
    let main = fs::read_to_string(root.join("src/main.rs")).expect("src/main.rs");
    for command in APP_COMMANDS {
        assert!(build.contains(&format!("\"{command}\"")), "{command} missing from build.rs manifest");
        assert!(main.contains(&format!("session::{command}")), "{command} not registered in main.rs");
    }
    // A handler registered in main.rs but missing from the manifest would be
    // callable without any permission.
    assert_eq!(
        main.matches("session::").count(),
        APP_COMMANDS.len(),
        "main.rs registers a command that is not on the reviewed list"
    );
}

#[test]
fn keychain_permissions_apply_only_to_the_bundled_main_window() {
    for file in capability_files() {
        let cap = read_json(&file);
        let grants_keychain = cap["permissions"]
            .as_array()
            .unwrap()
            .iter()
            .any(|p| p.as_str().is_some_and(|id| id.contains("refresh-token")));
        if grants_keychain {
            assert_eq!(cap["windows"], serde_json::json!(["main"]), "{file}");
            assert!(cap.get("remote").is_none(), "{file}");
        }
    }
}

fn read_json(rel: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(rel);
    let text = fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("parse {}: {e}", path.display()))
}

fn capability_files() -> Vec<String> {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("capabilities");
    fs::read_dir(dir)
        .expect("capabilities dir")
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .map(|p| format!("capabilities/{}", p.file_name().unwrap().to_string_lossy()))
        .collect()
}

#[test]
fn capabilities_only_grant_allow_listed_permissions() {
    let files = capability_files();
    assert!(!files.is_empty(), "expected at least one capability file");
    for file in files {
        let cap = read_json(&file);
        for perm in cap["permissions"].as_array().expect("permissions array") {
            let id = perm.as_str().or_else(|| perm["identifier"].as_str()).unwrap_or_default();
            assert!(
                ALLOWED_PERMISSIONS.contains(&id),
                "{file}: permission `{id}` is not on the reviewed allow-list"
            );
        }
        // Capabilities must never apply to remote (non-bundled) content.
        assert!(cap.get("remote").is_none(), "{file}: remote capabilities are forbidden");
    }
}

#[test]
fn global_tauri_object_is_not_injected() {
    let conf = read_json("tauri.conf.json");
    assert_eq!(conf["app"]["withGlobalTauri"], Value::Bool(false));
}

fn assert_strict_csp(conf: &Value, name: &str) {
    let csp = &conf["app"]["security"]["csp"];
    assert!(csp.is_object(), "{name}: CSP must be configured");
    assert_eq!(csp["default-src"], "'self'", "{name}");
    assert_eq!(csp["script-src"], "'self'", "{name}: no inline/eval scripts in release CSP");
    assert_eq!(csp["object-src"], "'none'", "{name}");
    let connect = csp["connect-src"].as_str().unwrap();
    assert!(!connect.contains('*'), "{name}: wildcard connect-src");
    assert_eq!(conf["app"]["security"]["freezePrototype"], Value::Bool(true), "{name}");
}

#[test]
fn release_csp_is_strict_for_every_channel() {
    assert_strict_csp(&read_json("tauri.conf.json"), "tauri.conf.json");
    assert_strict_csp(&read_json("tauri.production.conf.json"), "tauri.production.conf.json");
}

#[test]
fn production_channel_is_https_only_and_distinct_from_dev() {
    let dev = read_json("tauri.conf.json");
    let prod = read_json("tauri.production.conf.json");
    assert_ne!(dev["identifier"], prod["identifier"], "dev and prod must install side by side");
    let connect = prod["app"]["security"]["csp"]["connect-src"].as_str().unwrap();
    for source in connect.split_whitespace() {
        assert!(
            !source.starts_with("http://") || source == "http://ipc.localhost",
            "production CSP allows insecure origin {source}"
        );
    }
}

#[test]
fn file_drops_go_to_the_page_not_to_native_handlers() {
    // The CV upload zone uses HTML drag-and-drop. With Tauri's native handler
    // disabled, a dropped file reaches only the page's own upload code (which
    // validates it and sends it to the backend); no native path is exposed.
    for name in ["tauri.conf.json", "tauri.production.conf.json"] {
        let conf = read_json(name);
        for window in conf["app"]["windows"].as_array().unwrap() {
            assert_eq!(window["dragDropEnabled"], Value::Bool(false), "{name}");
        }
    }
}

#[test]
fn opener_can_only_open_https_urls() {
    // Never file://, custom schemes or plain http: only web pages, in the
    // user's default browser (the app never renders external content itself).
    for file in capability_files() {
        let cap = read_json(&file);
        for perm in cap["permissions"].as_array().unwrap() {
            let id = perm.as_str().or_else(|| perm["identifier"].as_str()).unwrap_or_default();
            if !id.starts_with("opener:") {
                continue;
            }
            assert_eq!(id, "opener:allow-open-url", "{file}: unexpected opener permission {id}");
            let scopes = perm["allow"].as_array().expect("opener permission must carry an explicit scope");
            assert!(!scopes.is_empty(), "{file}: empty opener scope");
            for scope in scopes {
                let url = scope["url"].as_str().unwrap_or_default();
                assert_eq!(url, "https://*", "{file}: opener scope must be https:// only, got {url}");
                assert!(scope.get("path").is_none(), "{file}: opener must not open local paths");
            }
            assert!(perm.get("deny").is_none() || perm["deny"].is_array(), "{file}");
        }
    }
}
