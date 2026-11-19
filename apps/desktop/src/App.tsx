import { createDirectSessionTransport, type RefreshTokenStore } from "@careerpilot/api-client";
import { AppProviders, CareerPilotApp } from "@careerpilot/ui";
import { useState } from "react";
import { publicConfig } from "./config";
import { defaultStore } from "./keychain";

export function App({ store }: { store?: RefreshTokenStore }) {
  const [tokenStore] = useState(() => store ?? defaultStore());
  return (
    <AppProviders
      apiBaseUrl={publicConfig.apiBaseUrl}
      clientType="desktop"
      sessionTransport={(api) => createDirectSessionTransport(api, tokenStore)}
    >
      <CareerPilotApp />
    </AppProviders>
  );
}
