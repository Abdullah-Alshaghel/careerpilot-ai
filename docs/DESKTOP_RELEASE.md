# Desktop build, signing and release

## Channels

| Channel | Config | Identifier | API (CSP `connect-src`) |
|---|---|---|---|
| Development | `src-tauri/tauri.conf.json` | `ai.careerpilot.desktop.dev` | `http://localhost:8080` |
| Production | `tauri.conf.json` + `tauri.production.conf.json` (merged) | `ai.careerpilot.desktop` | `https://api.careerpilot.example` (replace with the real API host) |

Different identifiers mean both channels can be installed side by side. `security.rs` enforces that the production CSP allows HTTPS only.

## Prerequisites

- Node 22+, Rust stable (`rustup`), and the platform toolchain:
  - **Windows:** Visual Studio 2022 Build Tools with "Desktop development with C++", WebView2 (preinstalled on Windows 11).
  - **macOS:** Xcode Command Line Tools.

## Commands (from `apps/desktop`)

```bash
npm run tauri:dev                           # dev window against the local stack
npx tauri build --bundles nsis,msi          # Windows installers (dev channel, unsigned)
npx tauri build --bundles app,dmg           # macOS (run on a Mac)
npm run tauri:build:prod                    # production channel (set VITE_API_BASE_URL to the HTTPS API first)
```

Output: `src-tauri/target/release/bundle/{nsis,msi,macos,dmg}/`.

## Code signing (not configured: credentials required)

No signed release exists yet. Builds produced so far are **unsigned**: Windows SmartScreen will warn, and macOS Gatekeeper will block unless the user overrides it.

- **Windows:** obtain an OV/EV code-signing certificate (or Azure Trusted Signing). In CI, set `bundle.windows.signCommand` (e.g. `trusted-signing-cli …` or `signtool sign /fd sha256 /tr <timestamp-url> /td sha256 %1`) with credentials from GitHub encrypted secrets.
- **macOS:** Apple Developer ID Application certificate + notarization. Set `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_PASSWORD` (app-specific), `APPLE_TEAM_ID` as CI secrets. Tauri signs and notarizes during `tauri build`. `hardenedRuntime` is already enabled.
- Signing runs only in a protected, manually approved release workflow, never on pull requests.

## Updates (planned, Phase 9)

Use `tauri-plugin-updater` with a signed update manifest:

1. `npx tauri signer generate` → keep the private key in CI secrets (`TAURI_SIGNING_PRIVATE_KEY`); commit only the public key to `plugins.updater.pubkey`.
2. Separate endpoints per channel (`/desktop/dev/latest.json`, `/desktop/stable/latest.json`), HTTPS only.
3. Updates are verified against the embedded public key before install. Grant only the `updater:default` permission and extend the `security.rs` allow-list in the same change.

## Security boundaries

The webview has **no IPC permissions** (`capabilities/main.json`), `withGlobalTauri: false`, `freezePrototype: true`, and no inline/eval scripts in the release CSP. No secrets are compiled in: `VITE_*` values are public, and `npm run check:secrets` scans `dist/` for credential patterns and private `.env` values.
