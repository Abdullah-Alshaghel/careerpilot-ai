import { describe, expect, it, vi } from "vitest";
import { ApiError, createApiClient, type FetchLike } from "./index";

const okBody = {
  service: "api-gateway",
  version: "0.1.0",
  status: "ok",
  timestamp: "2026-11-18T12:00:00.123456Z",
  checks: [{ name: "redis", status: "up", latency_ms: 1.2, detail: null }],
};

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

describe("createApiClient", () => {
  it("rejects base URLs with a path or a non-http scheme", () => {
    expect(() => createApiClient({ baseUrl: "http://x/v1", clientType: "web" })).toThrow();
    expect(() => createApiClient({ baseUrl: "file:///etc/passwd", clientType: "web" })).toThrow();
  });

  it("calls /v1/health with correlation and client headers and validates the response", async () => {
    const fetch = vi.fn<FetchLike>(async () => jsonResponse(okBody));
    const client = createApiClient({
      baseUrl: "http://localhost:8080/",
      fetch,
      clientType: "desktop",
    });

    await expect(client.health()).resolves.toEqual(okBody);

    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe("http://localhost:8080/v1/health");
    const headers = init!.headers as Record<string, string>;
    expect(headers["x-correlation-id"]).toMatch(/^[0-9a-f-]{36}$/);
    expect(headers["x-client-type"]).toBe("desktop");
    expect(headers.authorization).toBeUndefined();
    expect(init!.credentials).toBe("omit");
  });

  it("attaches the bearer token when one is available", async () => {
    const fetch = vi.fn<FetchLike>(async () => jsonResponse(okBody));
    const client = createApiClient({
      baseUrl: "http://localhost:8080",
      fetch,
      clientType: "web",
      getAccessToken: () => "tok-123",
    });
    await client.health();
    expect((fetch.mock.calls[0]![1]!.headers as Record<string, string>).authorization).toBe(
      "Bearer tok-123",
    );
  });

  it("raises invalid_response when the payload breaks the contract", async () => {
    const fetch = vi.fn<FetchLike>(async () => jsonResponse({ ...okBody, status: "fantastic" }));
    const client = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType: "web" });
    await expect(client.health()).rejects.toMatchObject({ kind: "invalid_response" });
  });

  it("maps backend error bodies to ApiError with code and correlation id", async () => {
    const fetch = vi.fn<FetchLike>(async () =>
      jsonResponse(
        { error: { code: "forbidden", message: "Not allowed", correlation_id: "srv-1234567" } },
        403,
        { "x-correlation-id": "srv-1234567" },
      ),
    );
    const client = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType: "web" });
    const err = await client.health().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({
      kind: "http",
      status: 403,
      code: "forbidden",
      correlationId: "srv-1234567",
    });
  });

  it("maps transport failures to a network error without leaking internals", async () => {
    const fetch = vi.fn<FetchLike>(async () => {
      throw new TypeError("Failed to fetch 10.0.0.1");
    });
    const client = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType: "web" });
    await expect(client.health()).rejects.toMatchObject({
      kind: "network",
      message: "Cannot reach the CareerPilot service",
    });
  });

  it("times out slow requests", async () => {
    const fetch: FetchLike = (_url, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError")),
        );
      });
    const client = createApiClient({
      baseUrl: "http://localhost:8080",
      fetch,
      clientType: "web",
      timeoutMs: 20,
    });
    await expect(client.health()).rejects.toMatchObject({ kind: "timeout" });
  });
});
