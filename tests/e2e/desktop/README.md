# Desktop E2E

Phase 1 desktop coverage: Vitest component test (`apps/desktop/src/App.test.tsx`), Rust security-boundary tests (`apps/desktop/src-tauri/tests/security.rs`), bundle secret scan (`npm run check:secrets`).

Phase 9 adds full WebDriver E2E via `tauri-driver` + `msedgedriver` (Windows) running the same scenarios as `tests/e2e/web`.
