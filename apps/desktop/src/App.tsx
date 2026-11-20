import { createDirectSessionTransport, type RefreshTokenStore } from "@careerpilot/api-client";
import { AppProviders, CareerPilotApp, type OpenExternal } from "@careerpilot/ui";
import { isTauri } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useState } from "react";
import { publicConfig } from "./config";
import { defaultStore } from "./keychain";

/** https:// links open in the system browser (opener plugin, scope https://* only). */
const openInSystemBrowser: OpenExternal = (url) => {
  void openUrl(url);
};

export function App({ store }: { store?: RefreshTokenStore }) {
  const [tokenStore] = useState(() => store ?? defaultStore());
  return (
    <AppProviders
      apiBaseUrl={publicConfig.apiBaseUrl}
      clientType="desktop"
      openExternal={isTauri() ? openInSystemBrowser : undefined}
      sessionTransport={(api) => createDirectSessionTransport(api, tokenStore)}
    >
      <CareerPilotApp />
    </AppProviders>
  );
}
