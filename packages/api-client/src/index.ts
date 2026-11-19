import type {
  HealthResponse,
  LoginRequest,
  RegisterRequest,
  TokenResponse,
  User,
} from "@careerpilot/contracts";
import {
  apiBaseUrlSchema,
  apiErrorBodySchema,
  healthResponseSchema,
  tokenResponseSchema,
  userSchema,
} from "@careerpilot/validation";
import type { z } from "zod";

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface ApiClientOptions {
  /** Gateway origin, e.g. http://localhost:8080. Public value — never a secret. */
  baseUrl: string;
  /** Injected for tests and for platform-specific transports. */
  fetch?: FetchLike;
  /** Per-request timeout in milliseconds. */
  timeoutMs?: number;
  /** Supplies the current access token. Tokens live in the session manager, not here. */
  getAccessToken?: () => string | null | Promise<string | null>;
  /**
   * Called once when an authenticated request gets a 401. Returns a fresh
   * access token to retry with, or null to give up (the user is signed out).
   */
  onUnauthorized?: () => Promise<string | null>;
  /** Identifies the calling client in server logs/analytics. */
  clientType: "desktop" | "web";
}

export type ApiErrorKind = "network" | "timeout" | "http" | "invalid_response";

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    message: string,
    readonly status: number | null = null,
    readonly code: string | null = null,
    readonly correlationId: string | null = null,
    /** Seconds to wait before retrying (from a 429's Retry-After header). */
    readonly retryAfter: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiClient {
  readonly baseUrl: string;
  health(signal?: AbortSignal): Promise<HealthResponse>;
  auth: {
    register(body: RegisterRequest): Promise<TokenResponse>;
    login(body: LoginRequest): Promise<TokenResponse>;
    refresh(refreshToken: string): Promise<TokenResponse>;
    /** Revokes the session server-side. Idempotent. */
    logout(refreshToken: string): Promise<void>;
  };
  users: {
    me(signal?: AbortSignal): Promise<User>;
  };
}

type Method = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

export interface JsonRequest {
  method: Method;
  url: string;
  headers: Record<string, string>;
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs: number;
  /** Fetch credentials mode: "omit" for the gateway, "same-origin" for the Web BFF. */
  credentials: RequestCredentials;
}

export function newCorrelationId(): string {
  return globalThis.crypto.randomUUID();
}

function parseRetryAfter(value: string | null): number | null {
  if (!value) return null;
  const seconds = Number(value);
  return Number.isFinite(seconds) && seconds >= 0 ? Math.ceil(seconds) : null;
}

/**
 * One HTTP exchange with uniform error handling. Returns the parsed body, or
 * `undefined` for 204. Shared by the gateway client and the Web session client.
 */
export async function sendJson<S extends z.ZodType>(
  doFetch: FetchLike,
  req: JsonRequest,
  schema: S | null,
): Promise<z.infer<S>> {
  const correlationId = req.headers["x-correlation-id"] ?? null;
  const headers: Record<string, string> = { accept: "application/json", ...req.headers };
  if (req.body !== undefined) headers["content-type"] = "application/json";

  const timeout = AbortSignal.timeout(req.timeoutMs);
  const signal = req.signal ? AbortSignal.any([req.signal, timeout]) : timeout;

  let response: Response;
  try {
    response = await doFetch(req.url, {
      method: req.method,
      headers,
      body: req.body === undefined ? undefined : JSON.stringify(req.body),
      signal,
      credentials: req.credentials,
    });
  } catch (err) {
    if (timeout.aborted)
      throw new ApiError("timeout", "Request timed out", null, null, correlationId);
    if (req.signal?.aborted) throw err;
    throw new ApiError(
      "network",
      "Cannot reach the CareerPilot service",
      null,
      null,
      correlationId,
    );
  }

  const serverCorrelationId = response.headers.get("x-correlation-id") ?? correlationId;
  let json: unknown;
  try {
    json = response.status === 204 ? undefined : await response.json();
  } catch {
    json = undefined;
  }

  if (!response.ok) {
    const parsed = apiErrorBodySchema.safeParse(json);
    throw new ApiError(
      "http",
      parsed.success ? parsed.data.error.message : `Request failed with status ${response.status}`,
      response.status,
      parsed.success ? parsed.data.error.code : null,
      serverCorrelationId,
      parseRetryAfter(response.headers.get("retry-after")),
    );
  }

  if (schema === null) return undefined as z.infer<S>;
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    throw new ApiError(
      "invalid_response",
      "The service returned an unexpected response",
      response.status,
      null,
      serverCorrelationId,
    );
  }
  return parsed.data;
}

export function isUnauthorized(err: unknown): err is ApiError {
  return err instanceof ApiError && err.kind === "http" && err.status === 401;
}

export function createApiClient(options: ApiClientOptions): ApiClient {
  const baseUrl = apiBaseUrlSchema.parse(options.baseUrl).replace(/\/+$/, "");
  const doFetch: FetchLike = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
  const timeoutMs = options.timeoutMs ?? 10_000;

  async function request<S extends z.ZodType>(
    method: Method,
    path: `/${string}`,
    schema: S | null,
    init: { body?: unknown; signal?: AbortSignal; authenticated?: boolean } = {},
  ): Promise<z.infer<S>> {
    const authenticated = init.authenticated ?? true;

    const attempt = (token: string | null) =>
      sendJson(
        doFetch,
        {
          method,
          url: `${baseUrl}${path}`,
          headers: {
            "x-correlation-id": newCorrelationId(),
            "x-client-type": options.clientType,
            ...(token ? { authorization: `Bearer ${token}` } : {}),
          },
          body: init.body,
          signal: init.signal,
          timeoutMs,
          // Cookies are not used for API auth; tokens go in the Authorization header.
          credentials: "omit",
        },
        schema,
      );

    // Auth endpoints never carry an access token (and must not recurse into refresh).
    const token = authenticated ? ((await options.getAccessToken?.()) ?? null) : null;
    try {
      return await attempt(token);
    } catch (err) {
      if (!authenticated || !token || !options.onUnauthorized || !isUnauthorized(err)) throw err;
      // The token expired or was revoked mid-flight: refresh once and retry.
      const fresh = await options.onUnauthorized();
      if (!fresh) throw err;
      return attempt(fresh);
    }
  }

  return {
    baseUrl,
    health: (signal) =>
      request("GET", "/v1/health", healthResponseSchema, { signal, authenticated: false }),
    auth: {
      register: (body) =>
        request("POST", "/v1/auth/register", tokenResponseSchema, { body, authenticated: false }),
      login: (body) =>
        request("POST", "/v1/auth/login", tokenResponseSchema, { body, authenticated: false }),
      refresh: (refreshToken) =>
        request("POST", "/v1/auth/refresh", tokenResponseSchema, {
          body: { refresh_token: refreshToken },
          authenticated: false,
        }),
      logout: async (refreshToken) => {
        await request("POST", "/v1/auth/logout", null, {
          body: { refresh_token: refreshToken },
          authenticated: false,
        });
      },
    },
    users: {
      me: (signal) => request("GET", "/v1/users/me", userSchema, { signal }),
    },
  };
}

export * from "./session";
