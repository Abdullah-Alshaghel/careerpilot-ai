import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { memoryStore } from "./keychain";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke, isTauri: () => true }));

const user = {
  id: "6f1c2b9e-3d4a-4b8e-9a7c-2e5d1f0a9b31",
  email: "ada@example.com",
  locale: "en",
  status: "active",
  created_at: "2026-11-19T10:15:00Z",
};
const REFRESH = "k".repeat(43);

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function gateway() {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    if (path === "/v1/health")
      return json({
        service: "api-gateway",
        version: "0.1.0",
        status: "ok",
        timestamp: "2026-11-18T12:00:00Z",
        checks: [],
      });
    if (path === "/v1/auth/login" && init?.method === "POST")
      return json({
        access_token: "access-1",
        token_type: "Bearer",
        expires_in: 600,
        refresh_token: REFRESH,
        refresh_expires_in: 3600,
        user,
      });
    return json({ error: { code: "not_found", message: "x", correlation_id: null } }, 404);
  });
}

describe("Desktop App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    invoke.mockReset();
  });

  it("identifies itself as the desktop client and reaches the gateway", async () => {
    const fetch = gateway();
    vi.stubGlobal("fetch", fetch);
    render(<App store={memoryStore()} />);

    await screen.findByTestId("login-form");
    await waitFor(() => expect(screen.getByTestId("auth-connection")).toBeInTheDocument());
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://localhost:8080/v1/health");
    expect((init.headers as Record<string, string>)["x-client-type"]).toBe("desktop");
  });

  it("keeps the refresh token in the OS keychain, never in web storage", async () => {
    vi.stubGlobal("fetch", gateway());
    invoke.mockImplementation(async (command: string) =>
      command === "load_refresh_token" ? null : undefined,
    );
    render(<App />);

    await screen.findByTestId("login-form");
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "ada@example.com" } });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "correct horse battery" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByTestId("client-type")).toHaveTextContent("Desktop app");
    expect(invoke).toHaveBeenCalledWith("load_refresh_token");
    expect(invoke).toHaveBeenCalledWith("store_refresh_token", { token: REFRESH });
    const everything = JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage });
    expect(everything).not.toContain(REFRESH);

    fireEvent.click(screen.getByTestId("sign-out"));
    await screen.findByTestId("login-form");
    expect(invoke).toHaveBeenCalledWith("clear_refresh_token");
  });

  it("treats a locked or unavailable keychain as signed out", async () => {
    vi.stubGlobal("fetch", gateway());
    invoke.mockRejectedValue("keychain_unavailable");
    render(<App />);
    expect(await screen.findByTestId("login-form")).toBeInTheDocument();
  });
});
