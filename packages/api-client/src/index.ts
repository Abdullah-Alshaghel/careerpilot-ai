import type { HealthResponse } from "@careerpilot/contracts";
import {
  apiBaseUrlSchema,
  apiErrorBodySchema,
  healthResponseSchema,
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
  /** Supplies the current access token (Phase 2). Stored by the host app, not here. */
  getAccessToken?: () => string | null | Promise<string | null>;
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
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiClient {
  readonly baseUrl: string;
  health(signal?: AbortSignal): Promise<HealthResponse>;
}

function newCorrelationId(): string {
  return globalThis.crypto.randomUUID();
}

export function createApiClient(options: ApiClientOptions): ApiClient {
  const baseUrl = apiBaseUrlSchema.parse(options.baseUrl).replace(/\/+$/, "");
  const doFetch: FetchLike = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
  const timeoutMs = options.timeoutMs ?? 10_000;

  async function request<S extends z.ZodType>(
    method: "GET" | "POST" | "PATCH" | "PUT" | "DELETE",
    path: `/${string}`,
    schema: S,
    init: { body?: unknown; signal?: AbortSignal } = {},
  ): Promise<z.infer<S>> {
    const correlationId = newCorrelationId();
    const headers: Record<string, string> = {
      accept: "application/json",
      "x-correlation-id": correlationId,
      "x-client-type": options.clientType,
    };
    const token = await options.getAccessToken?.();
    if (token) headers.authorization = `Bearer ${token}`;
    if (init.body !== undefined) headers["content-type"] = "application/json";

    const timeout = AbortSignal.timeout(timeoutMs);
    const signal = init.signal ? AbortSignal.any([init.signal, timeout]) : timeout;

    let response: Response;
    try {
      response = await doFetch(`${baseUrl}${path}`, {
        method,
        headers,
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
        signal,
        // Cookies are not used for API auth; tokens go in the Authorization header.
        credentials: "omit",
      });
    } catch (err) {
      if (timeout.aborted)
        throw new ApiError("timeout", "Request timed out", null, null, correlationId);
      if (init.signal?.aborted) throw err;
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
      json = await response.json();
    } catch {
      json = undefined;
    }

    if (!response.ok) {
      const parsed = apiErrorBodySchema.safeParse(json);
      throw new ApiError(
        "http",
        parsed.success
          ? parsed.data.error.message
          : `Request failed with status ${response.status}`,
        response.status,
        parsed.success ? parsed.data.error.code : null,
        serverCorrelationId,
      );
    }

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

  return {
    baseUrl,
    health: (signal) => request("GET", "/v1/health", healthResponseSchema, { signal }),
  };
}
