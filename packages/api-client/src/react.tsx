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
  // User-scoped data. The whole cache is cleared on sign-out, so one user's
  // data can never be shown to the next person who signs in on this device.
  me: ["users", "me"] as const,
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

/** The signed-in user's account (GET /v1/users/me). */
export function useMe() {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => client.users.me(signal),
  });
}
