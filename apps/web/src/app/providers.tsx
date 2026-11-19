"use client";

import { createBrowserSessionTransport } from "@careerpilot/api-client";
import { AppProviders } from "@careerpilot/ui";
import type { ReactNode } from "react";
import { publicConfig } from "../config";

// The refresh token stays in an HttpOnly cookie owned by this app's server
// (src/server/session.ts); the page only ever holds the access token in memory.
const sessionTransport = () => createBrowserSessionTransport();

export function Providers({ children }: { children: ReactNode }) {
  return (
    <AppProviders
      apiBaseUrl={publicConfig.apiBaseUrl}
      clientType="web"
      sessionTransport={sessionTransport}
    >
      {children}
    </AppProviders>
  );
}
