import {
  createApiClient,
  createSessionManager,
  type ApiClient,
  type ApiClientOptions,
  type FetchLike,
  type SessionManager,
  type SessionState,
  type SessionTransport,
} from "@careerpilot/api-client";
import { ApiClientProvider } from "@careerpilot/api-client/react";
import {
  applyDocumentLocale,
  createTranslator,
  getDirection,
  type Locale,
  type Translate,
} from "@careerpilot/i18n";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
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
const SessionContext = createContext<SessionManager | null>(null);

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used inside <AppProviders>");
  return value;
}

export function useClientType(): ClientType {
  return useContext(ClientTypeContext);
}

export interface AuthValue {
  state: SessionState;
  login: SessionManager["login"];
  register: SessionManager["register"];
  logout: SessionManager["logout"];
  restore: SessionManager["restore"];
}

/** Current session plus the actions that change it. */
export function useAuth(): AuthValue {
  const manager = useContext(SessionContext);
  if (!manager) throw new Error("useAuth must be used inside <AppProviders>");
  const state = useSyncExternalStore(manager.subscribe, manager.getState, manager.getState);
  return useMemo(
    () => ({
      state,
      login: manager.login,
      register: manager.register,
      logout: manager.logout,
      restore: manager.restore,
    }),
    [state, manager],
  );
}

export interface AppProvidersProps {
  apiBaseUrl: string;
  clientType: ClientType;
  /**
   * How this platform keeps the session (Desktop: OS keychain; Web: HttpOnly
   * cookie via the web server). Receives the gateway client.
   */
  sessionTransport: (api: ApiClient) => SessionTransport;
  children: ReactNode;
  /** Tests inject a fake network and query cache. */
  fetch?: FetchLike;
  queryClient?: QueryClient;
}

/** Composition root shared by Desktop and Web: API client, session, data cache, i18n. */
export function AppProviders({
  apiBaseUrl,
  clientType,
  sessionTransport,
  children,
  fetch,
  queryClient,
}: AppProvidersProps) {
  const [{ api, session }] = useState(() => {
    // The client asks the session for tokens; the session's transport uses the
    // client. The closures below resolve that cycle lazily.
    const ref: { manager?: SessionManager } = {};
    const client = createApiClient({
      baseUrl: apiBaseUrl,
      clientType,
      fetch,
      getAccessToken: () => ref.manager?.getAccessToken() ?? null,
      onUnauthorized: () => ref.manager?.refresh().catch(() => null) ?? Promise.resolve(null),
    });
    ref.manager = createSessionManager(sessionTransport(client));
    return { api: client, session: ref.manager };
  });
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

  useEffect(() => {
    void session.restore();
    // Sign-out drops every cached response, so the next person to sign in on
    // this device never sees the previous user's data.
    return session.subscribe(() => {
      if (session.getState().status === "anonymous") queries.clear();
    });
  }, [session, queries]);

  const i18n = useMemo<I18nValue>(
    () => ({ locale, dir: getDirection(locale), t: createTranslator(locale), setLocale }),
    [locale, setLocale],
  );

  return (
    <ClientTypeContext.Provider value={clientType}>
      <QueryClientProvider client={queries}>
        <ApiClientProvider client={api}>
          <SessionContext.Provider value={session}>
            <I18nContext.Provider value={i18n}>{children}</I18nContext.Provider>
          </SessionContext.Provider>
        </ApiClientProvider>
      </QueryClientProvider>
    </ClientTypeContext.Provider>
  );
}
