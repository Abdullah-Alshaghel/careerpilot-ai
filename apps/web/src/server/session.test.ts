import { describe, expect, it, vi } from "vitest";
import { handleSessionRequest, REFRESH_COOKIE } from "./session";

const user = {
  id: "6f1c2b9e-3d4a-4b8e-9a7c-2e5d1f0a9b31",
  email: "ada@example.com",
  locale: "en",
  status: "active",
  created_at: "2026-11-19T10:15:00Z",
};
const OLD = "o".repeat(43);
const NEW = "n".repeat(43);
const tokens = (refresh = NEW) => ({
  access_token: "access-1",
  token_type: "Bearer",
  expires_in: 600,
  refresh_token: refresh,
  refresh_expires_in: 3600,
  user,
});

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
const gatewayError = (status: number, code: string, headers: Record<string, string> = {}) =>
  json({ error: { code, message: code, correlation_id: "gw-12345678" } }, status, headers);

function browserRequest(
  action: string,
  {
    body = {},
    cookie,
    headers = {},
  }: { body?: unknown; cookie?: string; headers?: Record<string, string> } = {},
) {
  return new Request(`http://localhost:3000/api/session/${action}`, {
    method: "POST",
    body: JSON.stringify(body),
    headers: {
      host: "localhost:3000",
      origin: "http://localhost:3000",
      "sec-fetch-site": "same-origin",
      "content-type": "application/json",
      "user-agent": "Playwright",
      ...(cookie ? { cookie: `theme=dark; ${REFRESH_COOKIE}=${cookie}` } : {}),
      ...headers,
    },
  });
}

function setup(response: () => Response | Promise<Response>) {
  const fetch = vi.fn(async (_url: string | URL | Request, _init?: RequestInit) => response());
  const run = (request: Request, action = new URL(request.url).pathname.split("/").pop()!) =>
    handleSessionRequest(action, request, {
      gatewayUrl: "http://gateway.test",
      secureCookies: true,
      fetch: fetch as unknown as typeof globalThis.fetch,
    });
  const sent = (i = 0) => {
    const [url, init] = fetch.mock.calls[i]!;
    return {
      url: String(url),
      headers: init!.headers as Record<string, string>,
      body: JSON.parse(init!.body as string),
    };
  };
  return { fetch, run, sent };
}

describe("login / register", () => {
  it("keeps the refresh token in a hardened HttpOnly cookie and out of the body", async () => {
    const { run, sent } = setup(() => json(tokens()));
    const resp = await run(browserRequest("login", { body: { email: "a@b.co", password: "pw" } }));

    expect(resp.status).toBe(200);
    const body = await resp.json();
    expect(body).toEqual({ access_token: "access-1", token_type: "Bearer", expires_in: 600, user });
    expect(JSON.stringify(body)).not.toContain(NEW);

    const cookie = resp.headers.get("set-cookie")!;
    expect(cookie).toContain(`${REFRESH_COOKIE}=${NEW}`);
    for (const flag of [
      "HttpOnly",
      "Secure",
      "SameSite=Strict",
      "Path=/api/session",
      "Max-Age=3600",
    ]) {
      expect(cookie).toContain(flag);
    }
    expect(resp.headers.get("cache-control")).toBe("no-store");

    const call = sent();
    expect(call.url).toBe("http://gateway.test/v1/auth/login");
    expect(call.body).toEqual({ email: "a@b.co", password: "pw" });
    expect(call.headers["x-client-type"]).toBe("web");
    expect(call.headers["user-agent"]).toBe("Playwright");
  });

  it("returns 201 for registration", async () => {
    const { run } = setup(() => json(tokens(), 201));
    expect((await run(browserRequest("register"))).status).toBe(201);
  });

  it("passes gateway errors through with Retry-After but without setting a cookie", async () => {
    const { run } = setup(() => gatewayError(429, "rate_limited", { "retry-after": "60" }));
    const resp = await run(browserRequest("login"));
    expect(resp.status).toBe(429);
    expect(resp.headers.get("retry-after")).toBe("60");
    expect(resp.headers.get("set-cookie")).toBeNull();
    expect((await resp.json()).error.code).toBe("rate_limited");
  });

  it("rejects a gateway response that breaks the token contract", async () => {
    const { run } = setup(() => json({ ...tokens(), refresh_token: "short" }));
    const resp = await run(browserRequest("login"));
    expect(resp.status).toBe(502);
    expect(resp.headers.get("set-cookie")).toBeNull();
  });

  it("reports an unreachable gateway as 502 without details", async () => {
    const { run } = setup(() => {
      throw new TypeError("connect ECONNREFUSED 10.0.0.5:8080");
    });
    const resp = await run(browserRequest("login"));
    expect(resp.status).toBe(502);
    expect(await resp.text()).not.toContain("10.0.0.5");
  });
});

describe("cross-site request protection", () => {
  it.each<[string, Record<string, string>]>([
    ["a foreign Origin", { origin: "https://evil.example" }],
    ["a cross-site fetch", { "sec-fetch-site": "cross-site" }],
    ["a form-encoded body", { "content-type": "application/x-www-form-urlencoded" }],
  ])("refuses %s without contacting the gateway", async (_name, headers) => {
    const { run, fetch } = setup(() => json(tokens()));
    expect((await run(browserRequest("refresh", { cookie: OLD, headers }))).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("refuses a request without an Origin header", async () => {
    const { run, fetch } = setup(() => json(tokens()));
    const request = browserRequest("refresh", { cookie: OLD });
    const headers = new Headers(request.headers);
    headers.delete("origin");
    expect((await run(new Request(request, { headers }), "refresh")).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("returns 404 for unknown actions", async () => {
    const { run } = setup(() => json(tokens()));
    expect((await run(browserRequest("admin"))).status).toBe(404);
  });
});

describe("refresh", () => {
  it("sends the cookie's token and rotates the cookie", async () => {
    const { run, sent } = setup(() => json(tokens(NEW)));
    const resp = await run(browserRequest("refresh", { cookie: OLD }));
    expect(resp.status).toBe(200);
    expect(sent().body).toEqual({ refresh_token: OLD });
    expect(resp.headers.get("set-cookie")).toContain(`${REFRESH_COOKIE}=${NEW}`);
  });

  it("answers 401 without a cookie, never calling the gateway", async () => {
    const { run, fetch } = setup(() => json(tokens()));
    const resp = await run(browserRequest("refresh"));
    expect(resp.status).toBe(401);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("ignores cookies that can't be one of our tokens", async () => {
    const { run, fetch } = setup(() => json(tokens()));
    expect((await run(browserRequest("refresh", { cookie: "x;drop table" }))).status).toBe(401);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("clears the cookie when the session is no longer valid", async () => {
    const { run } = setup(() => gatewayError(401, "refresh_token_reused"));
    const resp = await run(browserRequest("refresh", { cookie: OLD }));
    expect(resp.status).toBe(401);
    expect(resp.headers.get("set-cookie")).toContain("Max-Age=0");
  });

  it("keeps the cookie when another tab refreshed first (409, client retries)", async () => {
    const { run } = setup(() => gatewayError(401, "refresh_token_superseded"));
    const resp = await run(browserRequest("refresh", { cookie: OLD }));
    expect(resp.status).toBe(409);
    expect(resp.headers.get("set-cookie")).toBeNull();
  });
});

describe("logout", () => {
  it("revokes the session at the gateway and clears the cookie", async () => {
    const { run, sent } = setup(() => new Response(null, { status: 204 }));
    const resp = await run(browserRequest("logout", { cookie: OLD }));
    expect(resp.status).toBe(204);
    expect(sent()).toMatchObject({
      url: "http://gateway.test/v1/auth/logout",
      body: { refresh_token: OLD },
    });
    expect(resp.headers.get("set-cookie")).toContain("Max-Age=0");
  });

  it("still clears the cookie when the gateway is down", async () => {
    const { run } = setup(() => {
      throw new TypeError("offline");
    });
    const resp = await run(browserRequest("logout", { cookie: OLD }));
    expect(resp.status).toBe(204);
    expect(resp.headers.get("set-cookie")).toContain("Max-Age=0");
  });
});
