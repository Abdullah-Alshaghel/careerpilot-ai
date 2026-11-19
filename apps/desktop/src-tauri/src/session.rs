//! Refresh-token storage in the operating system's credential store
//! (Windows Credential Manager, macOS Keychain).
//!
//! The webview can only load, store or clear this one value, and only in the
//! shape of a CareerPilot refresh token. Errors carry a short code, never OS
//! details. Access tokens are never persisted: they live in webview memory.

use tauri::{AppHandle, Runtime};

#[cfg(any(target_os = "windows", target_os = "macos"))]
const ACCOUNT: &str = "refresh-token";
const UNAVAILABLE: &str = "keychain_unavailable";

/// Our refresh tokens are 32 random bytes, base64url without padding.
pub fn is_valid_refresh_token(token: &str) -> bool {
    token.len() == 43
        && token
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}

#[cfg(any(target_os = "windows", target_os = "macos"))]
fn entry<R: Runtime>(app: &AppHandle<R>) -> Result<keyring::Entry, String> {
    // Keyed by bundle identifier, so dev and production builds keep separate sessions.
    keyring::Entry::new(&app.config().identifier, ACCOUNT).map_err(|_| UNAVAILABLE.to_string())
}

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
fn entry<R: Runtime>(_app: &AppHandle<R>) -> Result<keyring::Entry, String> {
    // No native store is compiled in for this OS. Refuse rather than fall back
    // to an in-memory mock that would silently lose sessions.
    Err(UNAVAILABLE.to_string())
}

#[tauri::command]
pub async fn load_refresh_token<R: Runtime>(app: AppHandle<R>) -> Result<Option<String>, String> {
    let entry = entry(&app)?;
    match entry.get_password() {
        Ok(token) if is_valid_refresh_token(&token) => Ok(Some(token)),
        Ok(_) => {
            // Not something we wrote: discard it.
            let _ = entry.delete_credential();
            Ok(None)
        }
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(_) => Err(UNAVAILABLE.to_string()),
    }
}

#[tauri::command]
pub async fn store_refresh_token<R: Runtime>(app: AppHandle<R>, token: String) -> Result<(), String> {
    if !is_valid_refresh_token(&token) {
        return Err("invalid_token".to_string());
    }
    entry(&app)?
        .set_password(&token)
        .map_err(|_| UNAVAILABLE.to_string())
}

#[tauri::command]
pub async fn clear_refresh_token<R: Runtime>(app: AppHandle<R>) -> Result<(), String> {
    match entry(&app)?.delete_credential() {
        Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
        Err(_) => Err(UNAVAILABLE.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::is_valid_refresh_token;

    #[test]
    fn accepts_only_refresh_token_shaped_values() {
        assert!(is_valid_refresh_token(&"a".repeat(43)));
        assert!(is_valid_refresh_token("Zm9vYmFyYmF6cXV4cXV1eGNvcmdlZ3JhdWx0Z2FycGx"));
        assert!(is_valid_refresh_token(&format!("{}-_", "A".repeat(41))));
        for bad in [
            String::new(),
            "a".repeat(42),
            "a".repeat(44),
            format!("{}=", "a".repeat(42)),
            format!("{} ", "a".repeat(42)),
            "a".repeat(10_000),
            format!("{}\u{e9}", "a".repeat(41)),
        ] {
            assert!(!is_valid_refresh_token(&bad), "accepted {bad:?}");
        }
    }
}
