import { describe, expect, it, vi } from "vitest";
import { ApiError, createApiClient, type FetchLike } from "./index";
import { profileMayChange } from "./react";

const user = {
  id: "6f1c2b9e-3d4a-4b8e-9a7c-2e5d1f0a9b31",
  email: "ada@example.com",
  locale: "en",
  status: "active",
  created_at: "2026-11-19T10:15:00Z",
};

const tokens = {
  access_token: "access-1",
  token_type: "Bearer",
  expires_in: 600,
  refresh_token: "r".repeat(43),
  refresh_expires_in: 2_592_000,
  user,
};

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

  it("sends the bearer token on protected calls but never on health or auth calls", async () => {
    const fetch = vi.fn<FetchLike>(async (url) =>
      url.endsWith("/v1/users/me") ? jsonResponse(user) : jsonResponse(okBody),
    );
    const client = createApiClient({
      baseUrl: "http://localhost:8080",
      fetch,
      clientType: "web",
      getAccessToken: () => "tok-123",
    });
    await client.health();
    await client.users.me();
    const auth = (i: number) =>
      (fetch.mock.calls[i]![1]!.headers as Record<string, string>).authorization;
    expect(auth(0)).toBeUndefined();
    expect(auth(1)).toBe("Bearer tok-123");
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

describe("authentication", () => {
  const unauthorized = (code = "token_expired") =>
    jsonResponse({ error: { code, message: "Sign in again", correlation_id: null } }, 401);

  it("posts credentials to the auth endpoints and validates token responses", async () => {
    const fetch = vi.fn<FetchLike>(async () => jsonResponse(tokens));
    const client = createApiClient({
      baseUrl: "http://localhost:8080",
      fetch,
      clientType: "desktop",
    });
    await expect(client.auth.login({ email: "a@b.co", password: "pw" })).resolves.toEqual(tokens);
    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe("http://localhost:8080/v1/auth/login");
    expect(init!.method).toBe("POST");
    expect(JSON.parse(init!.body as string)).toEqual({ email: "a@b.co", password: "pw" });
  });

  it("treats a token response with a malformed refresh token as invalid", async () => {
    const fetch = vi.fn<FetchLike>(async () => jsonResponse({ ...tokens, refresh_token: "x" }));
    const client = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType: "web" });
    await expect(client.auth.refresh("r".repeat(43))).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });

  it("accepts 204 from logout", async () => {
    const fetch = vi.fn<FetchLike>(async () => new Response(null, { status: 204 }));
    const client = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType: "web" });
    await expect(client.auth.logout("r".repeat(43))).resolves.toBeUndefined();
  });

  it("refreshes once on 401 and retries with the new token", async () => {
    const fetch = vi
      .fn<FetchLike>()
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(jsonResponse(user));
    const onUnauthorized = vi.fn(async () => "access-2");
    const client = createApiClient({
      baseUrl: "http://localhost:8080",
      fetch,
      clientType: "web",
      getAccessToken: () => "access-1",
      onUnauthorized,
    });
    await expect(client.users.me()).resolves.toEqual(user);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect((fetch.mock.calls[1]![1]!.headers as Record<string, string>).authorization).toBe(
      "Bearer access-2",
    );
  });

  it("gives up when the refresh fails, without looping", async () => {
    const fetch = vi.fn<FetchLike>(async () => unauthorized());
    const onUnauthorized = vi.fn(async () => null);
    const client = createApiClient({
      baseUrl: "http://localhost:8080",
      fetch,
      clientType: "web",
      getAccessToken: () => "access-1",
      onUnauthorized,
    });
    await expect(client.users.me()).rejects.toMatchObject({ status: 401 });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("does not try to refresh when a login itself is rejected", async () => {
    const fetch = vi.fn<FetchLike>(async () => unauthorized("invalid_credentials"));
    const onUnauthorized = vi.fn(async () => "x");
    const client = createApiClient({
      baseUrl: "http://localhost:8080",
      fetch,
      clientType: "web",
      onUnauthorized,
    });
    await expect(client.auth.login({ email: "a@b.co", password: "x" })).rejects.toMatchObject({
      code: "invalid_credentials",
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("exposes Retry-After on rate-limited responses", async () => {
    const fetch = vi.fn<FetchLike>(async () =>
      jsonResponse(
        { error: { code: "rate_limited", message: "Too many attempts", correlation_id: null } },
        429,
        { "retry-after": "120" },
      ),
    );
    const client = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType: "web" });
    await expect(client.auth.login({ email: "a@b.co", password: "x" })).rejects.toMatchObject({
      code: "rate_limited",
      retryAfter: 120,
    });
  });
});

describe("documents and profile", () => {
  const doc = {
    id: "3d2c1b0a-9e8f-4a7b-8c6d-5e4f3a2b1c0d",
    filename: "cv.pdf",
    mime_type: "application/pdf",
    size_bytes: 10,
    status: "uploaded",
    failure_reason: null,
    created_at: "2026-11-20T09:00:00Z",
    updated_at: "2026-11-20T09:00:00Z",
  };

  it("uploads files as multipart with the bearer token and no JSON content type", async () => {
    const fetch = vi.fn<FetchLike>(async () => jsonResponse(doc, 201));
    const client = createApiClient({
      baseUrl: "http://localhost:8080",
      fetch,
      clientType: "web",
      getAccessToken: () => "tok",
    });
    const file = new Blob(["%PDF-1.7"], { type: "application/pdf" });
    await expect(client.documents.upload(file, "cv.pdf")).resolves.toEqual(doc);

    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe("http://localhost:8080/v1/documents");
    const headers = init!.headers as Record<string, string>;
    expect(headers["content-type"]).toBeUndefined();
    expect(headers.authorization).toBe("Bearer tok");
    const form = init!.body as FormData;
    expect((form.get("file") as File).name).toBe("cv.pdf");
  });

  it("surfaces upload rejections with their codes", async () => {
    const fetch = vi.fn<FetchLike>(async () =>
      jsonResponse(
        { error: { code: "macro_document", message: "No macros", correlation_id: null } },
        415,
      ),
    );
    const client = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType: "web" });
    await expect(client.documents.upload(new Blob(["x"]), "cv.docm")).rejects.toMatchObject({
      status: 415,
      code: "macro_document",
    });
  });

  it("escapes ids in paths and sends review decisions", async () => {
    const fetch = vi.fn<FetchLike>(async () => jsonResponse({ updated: 2 }));
    const client = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType: "web" });
    await expect(client.profile.setStatus(["a", "b"], "rejected")).resolves.toBe(2);
    expect(JSON.parse(fetch.mock.calls[0]![1]!.body as string)).toEqual({
      fact_ids: ["a", "b"],
      verification_status: "rejected",
    });

    const del = vi.fn<FetchLike>(async () => new Response(null, { status: 204 }));
    const client2 = createApiClient({
      baseUrl: "http://localhost:8080",
      fetch: del,
      clientType: "web",
    });
    await client2.documents.remove("../../admin");
    expect(del.mock.calls[0]![0]).toBe("http://localhost:8080/v1/documents/..%2F..%2Fadmin");
  });
});

describe("profileMayChange", () => {
  const now = Date.parse("2026-11-20T09:00:30Z");
  const document = (status: string, updated = "2026-11-20T09:00:00Z") => ({
    id: "d1",
    filename: "cv.pdf",
    mime_type: "application/pdf" as const,
    size_bytes: 1,
    status: status as "extracted",
    failure_reason: null,
    created_at: updated,
    updated_at: updated,
  });
  const profile = (documentIds: string[]) => ({
    id: "p",
    version: 1,
    updated_at: "2026-11-20T09:00:00Z",
    conflicts: [],
    facts: documentIds.map((id) => ({ source: { document_id: id } })),
  });

  it("polls while documents are read and until their suggestions arrive", () => {
    expect(profileMayChange({ documents: [document("processing")] }, undefined, now)).toBe(true);
    expect(
      profileMayChange({ documents: [document("extracted")] }, profile([]) as never, now),
    ).toBe(true);
    expect(
      profileMayChange({ documents: [document("extracted")] }, profile(["d1"]) as never, now),
    ).toBe(false);
  });

  it("stops after the settle window even if a CV yielded nothing", () => {
    const old = document("extracted", "2026-11-20T08:00:00Z");
    expect(profileMayChange({ documents: [old] }, profile([]) as never, now)).toBe(false);
    expect(profileMayChange({ documents: [document("failed")] }, profile([]) as never, now)).toBe(
      false,
    );
  });
});

describe("companies", () => {
  const company = {
    id: "c1000000-0000-4000-8000-000000000001",
    name: "Acme",
    careers_url: "https://jobs.lever.co/acme",
    region: null,
    priority: "high",
    notes: null,
    version: 2,
    connectors: [],
    detected_connector: { connector_type: "lever", config: { company: "acme" } },
    created_at: "2026-11-20T10:00:00Z",
    updated_at: "2026-11-20T10:00:00Z",
  };

  it("sends updates with the version and validates the response", async () => {
    const fetch = vi.fn<FetchLike>(async () => jsonResponse(company));
    const client = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType: "web" });
    await expect(
      client.companies.update(company.id, { priority: "high", version: 1 }),
    ).resolves.toEqual(company);
    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe(`http://localhost:8080/v1/companies/${company.id}`);
    expect(init!.method).toBe("PATCH");
    expect(JSON.parse(init!.body as string)).toEqual({ priority: "high", version: 1 });
  });

  it("surfaces version conflicts", async () => {
    const fetch = vi.fn<FetchLike>(async () =>
      jsonResponse(
        { error: { code: "version_conflict", message: "Changed elsewhere", correlation_id: null } },
        409,
      ),
    );
    const client = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType: "web" });
    await expect(
      client.companies.update(company.id, { notes: "x", version: 1 }),
    ).rejects.toMatchObject({
      status: 409,
      code: "version_conflict",
    });
  });

  it("rejects a company response with an unknown connector type", async () => {
    const bad = { ...company, connectors: [{ connector_type: "linkedin_scraper" }] };
    const fetch = vi.fn<FetchLike>(async () => jsonResponse(bad));
    const client = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType: "web" });
    await expect(client.companies.get(company.id)).rejects.toMatchObject({
      kind: "invalid_response",
    });
  });
});
