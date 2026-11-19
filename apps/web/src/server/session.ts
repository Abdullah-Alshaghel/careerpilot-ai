/**
 * Web session endpoints (POST /api/session/{login,register,refresh,logout}).
 *
 * The browser never sees the refresh token: this server keeps it in an
 * HttpOnly, SameSite=Strict cookie scoped to /api/session, and hands the page
 * only the short-lived access token (ADR-011). Runs on the web app's server,
 * never in the browser bundle.
 */
import { apiErrorBodySchema, tokenResponseSchema } from "@careerpilot/validation";

export const REFRESH_COOKIE = "cp_refresh";
const COOKIE_PATH = "/api/session";
const MAX_BODY_BYTES = 16 * 1024;
const ACTIONS = new Set(["login", "register", "refresh", "logout"]);
const CORRELATION_ID = /^[A-Za-z0-9._-]{8,128}$/;
const REFRESH_TOKEN = /^[A-Za-z0-9_-]{43}$/;

export interface SessionHandlerConfig {
  /** Gateway origin as reachable from this server. */
  gatewayUrl: string;
  /** Mark the cookie Secure (always in production). */
  secureCookies: boolean;
  fetch?: typeof fetch;
  timeoutMs?: number;
}

function errorResponse(
  status: number,
  code: string,
  message: string,
  correlationId: string | null,
  headers: Headers = new Headers(),
): Response {
  headers.set("content-type", "application/json");
  headers.set("cache-control", "no-store");
  return new Response(JSON.stringify({ error: { code, message, correlation_id: correlationId } }), {
    status,
    headers,
  });
}

function refreshCookie(token: string, maxAgeSeconds: number, secure: boolean): string {
  return [
    `${REFRESH_COOKIE}=${token}`,
    `Path=${COOKIE_PATH}`,
    `Max-Age=${Math.max(0, Math.floor(maxAgeSeconds))}`,
    "HttpOnly",
    "SameSite=Strict",
    ...(secure ? ["Secure"] : []),
  ].join("; ");
}

function clearedCookie(secure: boolean): string {
  return refreshCookie("", 0, secure);
}

function readCookie(request: Request, name: string): string | null {
  for (const part of (request.headers.get("cookie") ?? "").split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return rest.join("=");
  }
  return null;
}

/**
 * CSRF defence in depth on top of SameSite=Strict: the request must come from
 * a page on this same origin and carry a JSON body (which cross-site forms
 * cannot send without a CORS preflight, and we answer none).
 */
function isSameOriginJson(request: Request): boolean {
  if (!(request.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) {
    return false;
  }
  const fetchSite = request.headers.get("sec-fetch-site");
  if (fetchSite && fetchSite !== "same-origin") return false;
  const origin = request.headers.get("origin");
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

async function readBody(request: Request): Promise<string | null> {
  const text = await request.text();
  return new TextEncoder().encode(text).length > MAX_BODY_BYTES ? null : text;
}

export async function handleSessionRequest(
  action: string,
  request: Request,
  config: SessionHandlerConfig,
): Promise<Response> {
  const incomingId = request.headers.get("x-correlation-id");
  const correlationId =
    incomingId && CORRELATION_ID.test(incomingId) ? incomingId : crypto.randomUUID();

  if (!ACTIONS.has(action)) return errorResponse(404, "not_found", "Not found", correlationId);
  if (request.method !== "POST" || !isSameOriginJson(request)) {
    return errorResponse(403, "forbidden", "Request not allowed", correlationId);
  }
  const body = await readBody(request);
  if (body === null) {
    return errorResponse(413, "payload_too_large", "Request body too large", correlationId);
  }

  const secure = config.secureCookies;
  const cookie = readCookie(request, REFRESH_COOKIE);
  const refreshToken = cookie && REFRESH_TOKEN.test(cookie) ? cookie : null;

  let upstreamBody: string;
  if (action === "login" || action === "register") {
    upstreamBody = body;
  } else if (refreshToken) {
    upstreamBody = JSON.stringify({ refresh_token: refreshToken });
  } else if (action === "logout") {
    return new Response(null, {
      status: 204,
      headers: { "set-cookie": clearedCookie(secure), "cache-control": "no-store" },
    });
  } else {
    return errorResponse(
      401,
      "no_session",
      "Not signed in",
      correlationId,
      new Headers({ "set-cookie": clearedCookie(secure) }),
    );
  }

  const headers: Record<string, string> = {
    accept: "application/json",
    "content-type": "application/json",
    "x-client-type": "web",
    "x-correlation-id": correlationId,
  };
  const userAgent = request.headers.get("user-agent");
  if (userAgent) headers["user-agent"] = userAgent;
  // Passed on so the gateway can rate-limit per browser when it trusts this server.
  const forwardedFor = request.headers.get("x-forwarded-for");
  if (forwardedFor) headers["x-forwarded-for"] = forwardedFor;

  let upstream: Response;
  try {
    upstream = await (config.fetch ?? fetch)(
      `${config.gatewayUrl.replace(/\/+$/, "")}/v1/auth/${action}`,
      {
        method: "POST",
        headers,
        body: upstreamBody,
        signal: AbortSignal.timeout(config.timeoutMs ?? 15_000),
        redirect: "error",
        cache: "no-store",
      },
    );
  } catch {
    if (action === "logout") {
      // Best effort: the session expires on its own; forget it here regardless.
      return new Response(null, {
        status: 204,
        headers: { "set-cookie": clearedCookie(secure), "cache-control": "no-store" },
      });
    }
    return errorResponse(
      502,
      "upstream_unavailable",
      "Cannot reach the CareerPilot service",
      correlationId,
    );
  }

  if (action === "logout") {
    return new Response(null, {
      status: 204,
      headers: { "set-cookie": clearedCookie(secure), "cache-control": "no-store" },
    });
  }

  const payload: unknown = await upstream.json().catch(() => undefined);

  if (!upstream.ok) {
    const parsed = apiErrorBodySchema.safeParse(payload);
    const code = parsed.success ? parsed.data.error.code : "upstream_error";
    const message = parsed.success ? parsed.data.error.message : "Request failed";
    const out = new Headers();
    const retryAfter = upstream.headers.get("retry-after");
    if (retryAfter) out.set("retry-after", retryAfter);

    if (action === "refresh" && code === "refresh_token_superseded") {
      // Another tab rotated the cookie moments ago. Keep the cookie (clearing
      // it would sign that tab out) and let this tab retry.
      return errorResponse(409, code, message, correlationId, out);
    }
    if (action === "refresh" && upstream.status === 401) {
      out.set("set-cookie", clearedCookie(secure));
    }
    const status = upstream.status >= 400 && upstream.status < 600 ? upstream.status : 502;
    return errorResponse(status, code, message, correlationId, out);
  }

  const tokens = tokenResponseSchema.safeParse(payload);
  if (!tokens.success) {
    return errorResponse(
      502,
      "invalid_upstream_response",
      "The service returned an unexpected response",
      correlationId,
    );
  }
  const { refresh_token, refresh_expires_in, ...session } = tokens.data;
  return new Response(JSON.stringify(session), {
    status: action === "register" ? 201 : 200,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
      "set-cookie": refreshCookie(refresh_token, refresh_expires_in, secure),
      "x-correlation-id": correlationId,
    },
  });
}
