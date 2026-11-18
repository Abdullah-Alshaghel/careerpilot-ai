import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import type { ApiClient } from "./index";

const ApiClientContext = createContext<ApiClient | null>(null);

export function ApiClientProvider({
  client,
  children,
}: {
  client: ApiClient;
  children: ReactNode;
}) {
  return <ApiClientContext.Provider value={client}>{children}</ApiClientContext.Provider>;
}

export function useApiClient(): ApiClient {
  const client = useContext(ApiClientContext);
  if (!client) throw new Error("useApiClient must be used inside <ApiClientProvider>");
  return client;
}

export const queryKeys = {
  health: ["platform", "health"] as const,
};

export const HEALTH_REFRESH_MS = 15_000;

/** Platform health, polled while the window is visible. */
export function useHealth() {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => client.health(signal),
    refetchInterval: HEALTH_REFRESH_MS,
    refetchIntervalInBackground: false,
    retry: 1,
  });
}
