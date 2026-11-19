import type { RefreshTokenStore } from "@careerpilot/api-client";
import { invoke, isTauri } from "@tauri-apps/api/core";

/**
 * Refresh token in the OS credential store (Windows Credential Manager /
 * macOS Keychain) via the three scoped commands in src-tauri/src/session.rs.
 * Never localStorage: any script in the page can read that.
 */
export const keychainStore: RefreshTokenStore = {
  async load() {
    try {
      return await invoke<string | null>("load_refresh_token");
    } catch {
      // Keychain locked or unavailable: behave as signed out rather than offline.
      return null;
    }
  },
  save: (token) => invoke<void>("store_refresh_token", { token }),
  clear: () => invoke<void>("clear_refresh_token"),
};

/** For running the desktop UI in a plain browser (`npm run dev`): forgets on reload. */
export function memoryStore(): RefreshTokenStore {
  let token: string | null = null;
  return {
    load: async () => token,
    save: async (value) => {
      token = value;
    },
    clear: async () => {
      token = null;
    },
  };
}

export function defaultStore(): RefreshTokenStore {
  return isTauri() ? keychainStore : memoryStore();
}
