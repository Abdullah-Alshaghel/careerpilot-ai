import { AppProviders, StatusPage } from "@careerpilot/ui";
import { publicConfig } from "./config";

export function App() {
  return (
    <AppProviders apiBaseUrl={publicConfig.apiBaseUrl} clientType="desktop">
      <StatusPage />
    </AppProviders>
  );
}
