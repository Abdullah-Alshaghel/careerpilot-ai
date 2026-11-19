import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { usePreferences } from "./preferences";
import {
  ADA,
  apiError,
  fakeGateway,
  healthyBody,
  json,
  MemoryStore,
  renderApp,
  tokensFor,
} from "./test-utils";

const GRACE = { ...ADA, id: "0b6e5d1a-2c3f-4e5a-8b7c-9d0e1f2a3b4c", email: "grace@example.com" };

function fill(label: string | RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function bearer(init: RequestInit | undefined) {
  return (init?.headers as Record<string, string>).authorization;
}

beforeEach(() => {
  localStorage.clear();
  act(() => usePreferences.setState({ locale: "en" }));
});

describe("sign-in", () => {
  it("shows the sign-in screen when there is no stored session", async () => {
    const fetch = fakeGateway({});
    renderApp(fetch);
    expect(await screen.findByTestId("login-form")).toBeInTheDocument();
    expect(await screen.findByTestId("auth-connection")).toHaveTextContent(
      "All systems operational",
    );
    // No refresh attempt without a stored token.
    expect(fetch.mock.calls.some(([url]) => url.endsWith("/v1/auth/refresh"))).toBe(false);
  });

  it("signs in, keeps the refresh token only in the store, and loads the account", async () => {
    const fetch = fakeGateway({
      "POST /v1/auth/login": () => json(tokensFor(ADA)),
      "GET /v1/users/me": (_b, init) =>
        bearer(init) === `Bearer access-${ADA.id}-1` ? json(ADA) : apiError(401, "invalid_token"),
    });
    const { store } = renderApp(fetch);
    await screen.findByTestId("login-form");

    fill("Email", "ada@example.com");
    fill("Password", "correct horse battery");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByTestId("signed-in-user")).toHaveTextContent("ada@example.com");
    expect(store.token).toBe(tokensFor(ADA).refresh_token);
    expect(JSON.stringify(localStorage)).not.toContain(tokensFor(ADA).refresh_token);

    fireEvent.click(screen.getByTestId("nav-account"));
    expect(await screen.findByTestId("account-email")).toHaveTextContent("ada@example.com");
  });

  it("shows a generic error for bad credentials and clears the password", async () => {
    const fetch = fakeGateway({
      "POST /v1/auth/login": () => apiError(401, "invalid_credentials"),
    });
    renderApp(fetch);
    await screen.findByTestId("login-form");
    fill("Email", "ada@example.com");
    fill("Password", "wrong");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByTestId("auth-error")).toHaveTextContent(
      "Email or password is incorrect",
    );
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("tells the user how long to wait when rate limited", async () => {
    const fetch = fakeGateway({
      "POST /v1/auth/login": () => apiError(429, "rate_limited", { "retry-after": "540" }),
    });
    renderApp(fetch);
    await screen.findByTestId("login-form");
    fill("Email", "ada@example.com");
    fill("Password", "whatever");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByTestId("auth-error")).toHaveTextContent("Try again in 9 min");
  });

  it("validates required fields before calling the server", async () => {
    const fetch = fakeGateway({});
    renderApp(fetch);
    await screen.findByTestId("login-form");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter your email address")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    expect(fetch.mock.calls.some(([url]) => url.endsWith("/v1/auth/login"))).toBe(false);
  });

  it("can reveal the password", async () => {
    renderApp(fakeGateway({}));
    await screen.findByTestId("login-form");
    const input = screen.getByLabelText("Password");
    expect(input).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByRole("button", { name: "Show" }));
    expect(input).toHaveAttribute("type", "text");
  });
});

describe("registration", () => {
  async function openRegister(routes = {}) {
    const fetch = fakeGateway(routes);
    const result = renderApp(fetch);
    await screen.findByTestId("login-form");
    fireEvent.click(screen.getByRole("button", { name: /Create an account/ }));
    await screen.findByTestId("register-form");
    return { fetch, ...result };
  }

  it("checks password length and confirmation locally", async () => {
    const { fetch } = await openRegister();
    fill("Email", "ada@example.com");
    fill("Password", "short");
    fill("Confirm password", "different");
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("Use at least 12 characters")).toBeInTheDocument();
    expect(screen.getByText("Passwords do not match")).toBeInTheDocument();
    expect(fetch.mock.calls.some(([url]) => url.endsWith("/v1/auth/register"))).toBe(false);
  });

  it("registers with the current UI language and signs straight in", async () => {
    let sent: unknown;
    await openRegister({
      "POST /v1/auth/register": (body: unknown) => {
        sent = body;
        return json(tokensFor(ADA), 201);
      },
    });
    fill("Email", "ada@example.com");
    fill("Password", "correct horse battery");
    fill("Confirm password", "correct horse battery");
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByTestId("signed-in-user")).toHaveTextContent("ada@example.com");
    expect(sent).toEqual({
      email: "ada@example.com",
      password: "correct horse battery",
      locale: "en",
    });
  });

  it("explains when the email is already registered", async () => {
    await openRegister({ "POST /v1/auth/register": () => apiError(409, "email_taken") });
    fill("Email", "ada@example.com");
    fill("Password", "correct horse battery");
    fill("Confirm password", "correct horse battery");
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByTestId("auth-error")).toHaveTextContent("already exists");
  });
});

describe("session lifecycle", () => {
  it("resumes a stored session on start without showing the sign-in screen", async () => {
    const fetch = fakeGateway({ "POST /v1/auth/refresh": () => json(tokensFor(ADA, 2)) });
    const { store } = renderApp(fetch, new MemoryStore(tokensFor(ADA, 1).refresh_token));
    expect(screen.getByTestId("session-restoring")).toBeInTheDocument();
    expect(await screen.findByTestId("signed-in-user")).toHaveTextContent("ada@example.com");
    expect(store.token).toBe(tokensFor(ADA, 2).refresh_token); // rotated
  });

  it("asks to sign in again when the stored session was revoked", async () => {
    const fetch = fakeGateway({
      "POST /v1/auth/refresh": () => apiError(401, "refresh_token_reused"),
    });
    const { store } = renderApp(fetch, new MemoryStore(tokensFor(ADA).refresh_token));
    expect(await screen.findByTestId("login-form")).toBeInTheDocument();
    expect(store.token).toBeNull();
  });

  it("offers a retry when the backend is unreachable at start, keeping the stored session", async () => {
    let online = false;
    const fetch = fakeGateway({
      "POST /v1/auth/refresh": () => {
        if (!online) throw new TypeError("Failed to fetch");
        return json(tokensFor(ADA, 2));
      },
    });
    const { store } = renderApp(fetch, new MemoryStore(tokensFor(ADA).refresh_token));
    expect(await screen.findByTestId("session-unreachable")).toBeInTheDocument();
    expect(store.token).toBe(tokensFor(ADA).refresh_token);

    online = true;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("signed-in-user")).toBeInTheDocument();
  });

  it("renews an expired access token transparently and retries the request", async () => {
    const fetch = fakeGateway({
      "POST /v1/auth/refresh": (body) =>
        (body as { refresh_token: string }).refresh_token === tokensFor(ADA, 1).refresh_token
          ? json(tokensFor(ADA, 2))
          : json(tokensFor(ADA, 3)),
      "GET /v1/users/me": (_b, init) =>
        bearer(init) === `Bearer access-${ADA.id}-2` ? apiError(401, "token_expired") : json(ADA),
    });
    renderApp(fetch, new MemoryStore(tokensFor(ADA, 1).refresh_token));
    await screen.findByTestId("signed-in-user");
    fireEvent.click(screen.getByTestId("nav-account"));
    expect(await screen.findByTestId("account-email")).toHaveTextContent("ada@example.com");
  });

  it("signs out, revokes the server session, and never shows the previous user's data", async () => {
    let current = ADA;
    const logoutBodies: unknown[] = [];
    const fetch = fakeGateway({
      "POST /v1/auth/refresh": () => json(tokensFor(ADA, 2)),
      "POST /v1/auth/logout": (body) => {
        logoutBodies.push(body);
        return new Response(null, { status: 204 });
      },
      "POST /v1/auth/login": () => {
        current = GRACE;
        return json(tokensFor(GRACE, 1));
      },
      "GET /v1/users/me": () => json(current),
    });
    const { store } = renderApp(fetch, new MemoryStore(tokensFor(ADA, 1).refresh_token));
    await screen.findByTestId("signed-in-user");
    fireEvent.click(screen.getByTestId("nav-account"));
    await screen.findByText("ada@example.com", { selector: "[data-testid=account-email]" });

    fireEvent.click(screen.getByTestId("sign-out"));
    expect(await screen.findByTestId("login-form")).toBeInTheDocument();
    expect(logoutBodies).toEqual([{ refresh_token: tokensFor(ADA, 2).refresh_token }]);
    expect(store.token).toBeNull();

    fill("Email", "grace@example.com");
    fill("Password", "another long password");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByTestId("signed-in-user");
    fireEvent.click(screen.getByTestId("nav-account"));
    // The account page must not flash Ada's cached details.
    expect(screen.queryByText("ada@example.com")).not.toBeInTheDocument();
    expect(await screen.findByTestId("account-email")).toHaveTextContent("grace@example.com");
  });
});

describe("system status (signed in)", () => {
  function signedIn(
    health: () => Response | Promise<Response>,
    clientType: "desktop" | "web" = "web",
  ) {
    const fetch = fakeGateway({
      "POST /v1/auth/refresh": () => json(tokensFor(ADA, 2)),
      "GET /v1/health": health,
    });
    return renderApp(fetch, new MemoryStore(tokensFor(ADA).refresh_token), clientType);
  }

  it("shows healthy checks", async () => {
    signedIn(() => json(healthyBody));
    await waitFor(() =>
      expect(screen.getByTestId("health-status")).toHaveAttribute("data-state", "ok"),
    );
    expect(screen.getByTestId("check-identity-service")).toHaveTextContent("8 ms");
  });

  it("shows degraded status", async () => {
    signedIn(() =>
      json({
        ...healthyBody,
        status: "degraded",
        checks: [{ ...healthyBody.checks[0], status: "down" }],
      }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("health-status")).toHaveAttribute("data-state", "degraded"),
    );
    expect(screen.getByTestId("check-redis")).toHaveTextContent("Down");
  });

  it("shows an unreachable state and recovers on retry", async () => {
    let fail = true;
    signedIn(() => {
      if (fail) throw new TypeError("Failed to fetch");
      return json(healthyBody);
    });
    await waitFor(
      () =>
        expect(screen.getByTestId("health-status")).toHaveAttribute("data-state", "unreachable"),
      { timeout: 4_000 },
    );
    expect(screen.getByTestId("health-error")).toHaveTextContent("Cannot reach the backend");
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(screen.getByTestId("health-status")).toHaveAttribute("data-state", "ok"),
    );
  });

  it("switches to Arabic with RTL direction and persists the preference", async () => {
    signedIn(() => json(healthyBody), "desktop");
    await screen.findByTestId("signed-in-user");
    expect(screen.getByTestId("client-type")).toHaveTextContent("Desktop app");

    fireEvent.click(screen.getByTestId("locale-toggle"));
    await waitFor(() => expect(document.documentElement.dir).toBe("rtl"));
    expect(document.documentElement.lang).toBe("ar");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("حالة النظام");
    expect(JSON.parse(localStorage.getItem("careerpilot.preferences")!).state.locale).toBe("ar");
  });
});
