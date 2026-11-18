"use client";

import { AppProviders } from "@careerpilot/ui";
import type { ReactNode } from "react";
import { publicConfig } from "../config";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <AppProviders apiBaseUrl={publicConfig.apiBaseUrl} clientType="web">
      {children}
    </AppProviders>
  );
}
