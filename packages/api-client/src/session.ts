/**
 * Client-side session handling shared by Desktop and Web.
 *
 * The access token lives only in memory (here). How the long-lived refresh
 * token is kept is platform-specific and hidden behind a SessionTransport:
 *  - Desktop: OS keychain, via a scoped Tauri command (createDirectSessionTransport).
 *  - Web: HttpOnly cookie set by the web app's own server (createBrowserSessionTransport).
 * See ADR-011 and docs/SECURITY.md.
 */
import type {
  BrowserSession,
  LoginRequest,
  RegisterRequest,
  TokenResponse,
  User,
} from "@careerpilot/contracts";
import { browserSessionSchema } from "@careerpilot/validation";
import {
  ApiError,
  isUnauthorized,
  newCorrelationId,
  sendJson,
  type ApiClient,
  type FetchLike,
} from "./index";

export interface SessionTransport {
  register(body: RegisterRequest): Promise<BrowserSession>;
  login(body: LoginRequest): Promise<BrowserSession>;
  /** Resume or extend the session. `null` means there is no valid session. */
  refresh(): Promise<BrowserSession | null>;
  /** Best effort server-side revocation; always forgets the local session. */
  logout(): Promise<void>;
}

export type SessionState =
  | { status: "restoring" }
  | { status: "unreachable" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: User };

export interface SessionManager {
  getState(): SessionState;
  subscribe(listener: () => void): () => void;
  /** A usable access token, refreshing first if it is about to expire. */
  getAccessToken(): Promise<string | null>;
  /** Called on app start: resume a stored session if there is one. */
  restore(): Promise<void>;
  /** Single-flight refresh. Resolves to the new access token, or null when signed out. */
  refresh(): Promise<string | null>;
  login(body: LoginRequest): Promise<void>;
  register(body: RegisterRequest): Promise<void>;
  logout(): Promise<void>;
}

/** Refresh this long before expiry so requests don't race the deadline. */
const EXPIRY_MARGIN_MS = 30_000;

export function createSessionManager(
  transport: SessionTransport,
  now: () => number = Date.now,
): SessionManager {
  let state: SessionState = { status: "restoring" };
  let accessToken: string | null = null;
  let expiresAt = 0;
  let inflight: Promise<string | null> | null = null;
  const listeners = new Set<() => void>();

  function setState(next: SessionState) {
    state = next;
    for (const listener of listeners) listener();
  }

  function accept(session: BrowserSession) {
    accessToken = session.access_token;
    expiresAt = now() + session.expires_in * 1000;
    setState({ status: "authenticated", user: session.user });
  }

  function forget() {
    accessToken = null;
    expiresAt = 0;
    setState({ status: "anonymous" });
  }

  async function doRefresh(): Promise<string | null> {
    try {
      const session = await transport.refresh();
      if (session) {
        accept(session);
        return session.access_token;
      }
      forget();
      return null;
    } catch (err) {
      // Network trouble is not a sign-out: keep the stored session for later.
      if (state.status === "restoring") setState({ status: "unreachable" });
      throw err;
    }
  }

  const manager: SessionManager = {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    async getAccessToken() {
      if (state.status !== "authenticated") return null;
      if (accessToken && expiresAt - now() > EXPIRY_MARGIN_MS) return accessToken;
      try {
        return await manager.refresh();
      } catch {
        return accessToken; // offline: let the request fail with its own error
      }
    },
    refresh() {
      // Concurrent callers share one refresh: refresh tokens are single-use.
      inflight ??= doRefresh().finally(() => {
        inflight = null;
      });
      return inflight;
    },
    async restore() {
      setState({ status: "restoring" });
      try {
        await manager.refresh();
      } catch {
        // state is now "unreachable"; the UI offers a retry.
      }
    },
    async login(body) {
      accept(await transport.login(body));
    },
    async register(body) {
      accept(await transport.register(body));
    },
    async logout() {
      try {
        await transport.logout();
      } finally {
        forget();
      }
    },
  };
  return manager;
}

// ------------------------------------------------------- desktop transport

/** Where a native host keeps the refresh token (Desktop: the OS keychain). */
export interface RefreshTokenStore {
  load(): Promise<string | null>;
  save(token: string): Promise<void>;
  clear(): Promise<void>;
}

function toSession({
  refresh_token: _refresh,
  refresh_expires_in: _expires,
  ...session
}: TokenResponse): BrowserSession {
  return session;
}

/** Talks to the gateway directly and keeps the refresh token in `store`. */
export function createDirectSessionTransport(
  api: ApiClient,
  store: RefreshTokenStore,
): SessionTransport {
  async function keep(tokens: TokenResponse): Promise<BrowserSession> {
    await store.save(tokens.refresh_token);
    return toSession(tokens);
  }

  async function refreshWith(token: string, retried: boolean): Promise<BrowserSession | null> {
    try {
      return await keep(await api.auth.refresh(token));
    } catch (err) {
      if (!isUnauthorized(err)) throw err;
      if (err.code === "refresh_token_superseded" && !retried) {
        // Another app instance rotated it a moment ago and stored the new one.
        const latest = await store.load();
        if (latest && latest !== token) return refreshWith(latest, true);
      }
      await store.clear();
      return null;
    }
  }

  return {
    register: async (body) => keep(await api.auth.register(body)),
    login: async (body) => keep(await api.auth.login(body)),
    async refresh() {
      const token = await store.load();
      return token ? refreshWith(token, false) : null;
    },
    async logout() {
      const token = await store.load();
      try {
        if (token) await api.auth.logout(token);
      } catch {
        // Offline: the server session expires on its own; forget it locally anyway.
      } finally {
        await store.clear();
      }
    },
  };
}

// ----------------------------------------------------------- web transport

export interface BrowserSessionTransportOptions {
  /** Same-origin path of the web app's session endpoints. */
  basePath?: string;
  fetch?: FetchLike;
  timeoutMs?: number;
  /** Wait before retrying a refresh another tab just performed. */
  supersededRetryDelayMs?: number;
}

/**
 * Talks to the web app's own server (`/api/session/*`), which holds the
 * refresh token in an HttpOnly cookie that page scripts can never read.
 */
export function createBrowserSessionTransport(
  options: BrowserSessionTransportOptions = {},
): SessionTransport {
  const basePath = (options.basePath ?? "/api/session").replace(/\/+$/, "");
  const doFetch: FetchLike = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
  const delay = options.supersededRetryDelayMs ?? 400;

  const post = <T extends boolean>(action: string, body: unknown, expectSession: T) =>
    sendJson(
      doFetch,
      {
        method: "POST",
        url: `${basePath}/${action}`,
        headers: { "x-correlation-id": newCorrelationId() },
        body,
        timeoutMs: options.timeoutMs ?? 15_000,
        credentials: "same-origin",
      },
      expectSession ? browserSessionSchema : null,
    ) as Promise<T extends true ? BrowserSession : undefined>;

  return {
    register: (body) => post("register", body, true),
    login: (body) => post("login", body, true),
    async refresh() {
      for (let attempt = 0; ; attempt++) {
        try {
          return await post("refresh", {}, true);
        } catch (err) {
          const superseded =
            err instanceof ApiError && err.code === "refresh_token_superseded" && attempt === 0;
          if (superseded) {
            // Another tab refreshed; the browser now holds its new cookie.
            await new Promise((resolve) => setTimeout(resolve, delay));
            continue;
          }
          if (isUnauthorized(err)) return null;
          throw err;
        }
      }
    },
    async logout() {
      try {
        await post("logout", {}, false);
      } catch {
        // The server clears the cookie on every path; nothing more to do offline.
      }
    },
  };
}
