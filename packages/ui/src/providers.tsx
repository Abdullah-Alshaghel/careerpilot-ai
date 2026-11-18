import { createApiClient, type ApiClient, type ApiClientOptions } from "@careerpilot/api-client";
import { ApiClientProvider } from "@careerpilot/api-client/react";
import {
  applyDocumentLocale,
  createTranslator,
  getDirection,
  type Locale,
  type Translate,
} from "@careerpilot/i18n";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { usePreferences } from "./preferences";

export type ClientType = ApiClientOptions["clientType"];

interface I18nValue {
  locale: Locale;
  dir: "ltr" | "rtl";
  t: Translate;
  setLocale: (locale: Locale) => void;
}

const I18nContext = createContext<I18nValue | null>(null);
const ClientTypeContext = createContext<ClientType>("web");

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside <AppProviders>");
  return value;
}

export function useClientType(): ClientType {
  return useContext(ClientTypeContext);
}

export interface AppProvidersProps {
  apiBaseUrl: string;
  clientType: ClientType;
  children: ReactNode;
  /** Tests inject a pre-built client. */
  apiClient?: ApiClient;
  queryClient?: QueryClient;
}

/** Composition root shared by Desktop and Web: API client, data cache, i18n. */
export function AppProviders({
  apiBaseUrl,
  clientType,
  children,
  apiClient,
  queryClient,
}: AppProvidersProps) {
  const [client] = useState(
    () => apiClient ?? createApiClient({ baseUrl: apiBaseUrl, clientType }),
  );
  const [queries] = useState(
    () =>
      queryClient ??
      new QueryClient({
        defaultOptions: { queries: { staleTime: 5_000, refetchOnWindowFocus: true } },
      }),
  );
  const locale = usePreferences((s) => s.locale);
  const setLocale = usePreferences((s) => s.setLocale);

  useEffect(() => {
    void usePreferences.persist.rehydrate();
  }, []);

  useEffect(() => {
    applyDocumentLocale(locale);
  }, [locale]);

  const i18n = useMemo<I18nValue>(
    () => ({ locale, dir: getDirection(locale), t: createTranslator(locale), setLocale }),
    [locale, setLocale],
  );

  return (
    <ClientTypeContext.Provider value={clientType}>
      <QueryClientProvider client={queries}>
        <ApiClientProvider client={client}>
          <I18nContext.Provider value={i18n}>{children}</I18nContext.Provider>
        </ApiClientProvider>
      </QueryClientProvider>
    </ClientTypeContext.Provider>
  );
}
