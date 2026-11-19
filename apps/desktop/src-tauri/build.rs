// Declaring the app's commands makes Tauri generate one permission per command
// (`allow-<command>`). A command is then callable only if a capability grants
// it explicitly; tests/security.rs pins exactly which ones are granted.
const COMMANDS: &[&str] = &["load_refresh_token", "store_refresh_token", "clear_refresh_token"];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("failed to run tauri-build");
}
