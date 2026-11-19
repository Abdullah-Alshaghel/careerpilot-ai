import type { BrowserSession, TokenResponse } from "@careerpilot/contracts";
import { describe, expect, it, vi } from "vitest";
import {
  ApiError,
  createApiClient,
  createBrowserSessionTransport,
  createDirectSessionTransport,
  createSessionManager,
  type FetchLike,
  type RefreshTokenStore,
  type SessionTransport,
} from "./index";

const user = {
  id: "6f1c2b9e-3d4a-4b8e-9a7c-2e5d1f0a9b31",
  email: "ada@example.com",
  locale: "en" as const,
  status: "active" as const,
  created_at: "2026-11-19T10:15:00Z",
};

const session = (token = "access-1", expiresIn = 600): BrowserSession => ({
  access_token: token,
  token_type: "Bearer",
  expires_in: expiresIn,
  user,
});

const tokens = (n: number): TokenResponse => ({
  ...session(`access-${n}`),
  refresh_token: String(n).repeat(43).slice(0, 43),
  refresh_expires_in: 2_592_000,
});

const json = (body: unknown, status = 200) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

const apiError = (status: number, code: string) => new ApiError("http", code, status, code, null);

function fakeTransport(overrides: Partial<SessionTransport> = {}): SessionTransport {
  return {
    register: vi.fn(async () => session()),
    login: vi.fn(async () => session()),
    refresh: vi.fn(async () => null),
    logout: vi.fn(async () => undefined),
    ...overrides,
  };
}

class MemoryStore implements RefreshTokenStore {
  constructor(public token: string | null = null) {}
  load = vi.fn(async () => this.token);
  save = vi.fn(async (t: string) => {
    this.token = t;
  });
  clear = vi.fn(async () => {
    this.token = null;
  });
}

// ------------------------------------------------------------ manager

describe("createSessionManager", () => {
  it("restores to anonymous when there is no stored session", async () => {
    const manager = createSessionManager(fakeTransport());
    expect(manager.getState()).toEqual({ status: "restoring" });
    await manager.restore();
    expect(manager.getState()).toEqual({ status: "anonymous" });
    expect(await manager.getAccessToken()).toBeNull();
  });

  it("restores an existing session", async () => {
    const manager = createSessionManager(fakeTransport({ refresh: async () => session() }));
    await manager.restore();
    expect(manager.getState()).toEqual({ status: "authenticated", user });
    expect(await manager.getAccessToken()).toBe("access-1");
  });

  it("reports unreachable (not signed out) when restore hits a network error", async () => {
    const manager = createSessionManager(
      fakeTransport({
        refresh: async () => {
          throw new ApiError("network", "down");
        },
      }),
    );
    await manager.restore();
    expect(manager.getState()).toEqual({ status: "unreachable" });
  });

  it("shares one in-flight refresh between concurrent callers", async () => {
    let n = 0;
    const refresh = vi.fn(async () => {
      await new Promise((r) => setTimeout(r, 5));
      return session(`access-${++n}`);
    });
    const manager = createSessionManager(fakeTransport({ refresh }));
    const results = await Promise.all([manager.refresh(), manager.refresh(), manager.refresh()]);
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(results).toEqual(["access-1", "access-1", "access-1"]);
  });

  it("refreshes proactively when the access token is about to expire", async () => {
    let clock = 0;
    const refresh = vi.fn(async () => session("access-2"));
    const manager = createSessionManager(fakeTransport({ refresh }), () => clock);
    await manager.login({ email: "a@b.co", password: "x" });
    expect(await manager.getAccessToken()).toBe("access-1");
    clock = 600_000 - 10_000; // 10 s left: inside the safety margin
    expect(await manager.getAccessToken()).toBe("access-2");
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("signs out when a refresh finds no valid session", async () => {
    const manager = createSessionManager(fakeTransport());
    await manager.login({ email: "a@b.co", password: "x" });
    expect(await manager.refresh()).toBeNull();
    expect(manager.getState()).toEqual({ status: "anonymous" });
  });

  it("notifies subscribers and forgets the token on logout, even if the server call fails", async () => {
    const manager = createSessionManager(
      fakeTransport({
        logout: async () => {
          throw new Error("offline");
        },
      }),
    );
    const listener = vi.fn();
    manager.subscribe(listener);
    await manager.login({ email: "a@b.co", password: "x" });
    await manager.logout().catch(() => undefined);
    expect(manager.getState()).toEqual({ status: "anonymous" });
    expect(await manager.getAccessToken()).toBeNull();
    expect(listener).toHaveBeenCalledTimes(2);
  });
});

// -------------------------------------------------- desktop transport

describe("createDirectSessionTransport", () => {
  function setup(store: MemoryStore, handler: (url: string, body: unknown) => Response) {
    const calls: { url: string; body: unknown }[] = [];
    const fetch = vi.fn<FetchLike>(async (url, init) => {
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ url, body });
      return handler(url, body);
    });
    const api = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType: "desktop" });
    return { transport: createDirectSessionTransport(api, store), calls };
  }

  it("stores the refresh token and never hands it to the caller", async () => {
    const store = new MemoryStore();
    const { transport } = setup(store, () => json(tokens(1)));
    const result = await transport.login({ email: "a@b.co", password: "x" });
    expect(result).toEqual(session("access-1"));
    expect(result).not.toHaveProperty("refresh_token");
    expect(store.token).toBe(tokens(1).refresh_token);
  });

  it("rotates the stored token on refresh", async () => {
    const store = new MemoryStore(tokens(1).refresh_token);
    const { transport, calls } = setup(store, () => json(tokens(2)));
    await transport.refresh();
    expect(calls[0]!.body).toEqual({ refresh_token: tokens(1).refresh_token });
    expect(store.token).toBe(tokens(2).refresh_token);
  });

  it("returns null without a network call when nothing is stored", async () => {
    const { transport, calls } = setup(new MemoryStore(), () => json(tokens(1)));
    expect(await transport.refresh()).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("clears the store when the server rejects the refresh token", async () => {
    const store = new MemoryStore(tokens(1).refresh_token);
    const { transport } = setup(store, () =>
      json({ error: { code: "refresh_token_reused", message: "x", correlation_id: null } }, 401),
    );
    expect(await transport.refresh()).toBeNull();
    expect(store.token).toBeNull();
  });

  it("keeps the store when the server is unreachable", async () => {
    const store = new MemoryStore(tokens(1).refresh_token);
    const transport = createDirectSessionTransport(
      createApiClient({
        baseUrl: "http://localhost:8080",
        clientType: "desktop",
        fetch: async () => {
          throw new TypeError("offline");
        },
      }),
      store,
    );
    await expect(transport.refresh()).rejects.toMatchObject({ kind: "network" });
    expect(store.token).toBe(tokens(1).refresh_token);
  });

  it("revokes server-side and clears locally on logout", async () => {
    const store = new MemoryStore(tokens(1).refresh_token);
    const { transport, calls } = setup(store, () => new Response(null, { status: 204 }));
    await transport.logout();
    expect(calls[0]).toEqual({
      url: "http://localhost:8080/v1/auth/logout",
      body: { refresh_token: tokens(1).refresh_token },
    });
    expect(store.token).toBeNull();
  });
});

// ------------------------------------------------------ web transport

describe("createBrowserSessionTransport", () => {
  it("posts to the same-origin session endpoints with cookies allowed", async () => {
    const fetch = vi.fn<FetchLike>(async () => json(session()));
    const transport = createBrowserSessionTransport({ fetch });
    await transport.login({ email: "a@b.co", password: "x" });
    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe("/api/session/login");
    expect(init!.credentials).toBe("same-origin");
  });

  it("rejects a session body that contains a refresh token", async () => {
    const fetch = vi.fn<FetchLike>(async () => json(tokens(1)));
    const transport = createBrowserSessionTransport({ fetch });
    await expect(transport.login({ email: "a@b.co", password: "x" })).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });

  it("maps 401 on refresh to 'no session'", async () => {
    const fetch = vi.fn<FetchLike>(async () =>
      json({ error: { code: "no_session", message: "x", correlation_id: null } }, 401),
    );
    expect(await createBrowserSessionTransport({ fetch }).refresh()).toBeNull();
  });

  it("retries once when another tab refreshed at the same moment", async () => {
    const fetch = vi
      .fn<FetchLike>()
      .mockResolvedValueOnce(
        json(
          { error: { code: "refresh_token_superseded", message: "x", correlation_id: null } },
          409,
        ),
      )
      .mockResolvedValueOnce(json(session("access-2")));
    const transport = createBrowserSessionTransport({ fetch, supersededRetryDelayMs: 1 });
    expect(await transport.refresh()).toEqual(session("access-2"));
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("propagates errors such as rate limiting from login", async () => {
    const fetch = vi.fn<FetchLike>(async () => {
      throw apiError(429, "rate_limited");
    });
    await expect(
      createBrowserSessionTransport({ fetch }).login({ email: "a@b.co", password: "x" }),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
