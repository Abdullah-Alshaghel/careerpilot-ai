//! Desktop security-boundary tests (critical test #17).
//! They read the shipped configuration and fail if privileges widen silently.

use serde_json::Value;
use std::fs;
use std::path::Path;

/// Every permission the frontend may hold. Extending this list is a reviewed,
/// deliberate change.
const ALLOWED_PERMISSIONS: &[&str] = &[];

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
