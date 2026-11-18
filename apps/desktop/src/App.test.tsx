import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

describe("Desktop App", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("identifies itself as the desktop client and reaches the gateway health endpoint", async () => {
    const fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            service: "api-gateway",
            version: "0.1.0",
            status: "ok",
            timestamp: "2026-11-18T12:00:00Z",
            checks: [],
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
    );
    vi.stubGlobal("fetch", fetch);

    render(<App />);

    await waitFor(() =>
      expect(screen.getByTestId("health-status")).toHaveAttribute("data-state", "ok"),
    );
    expect(screen.getByTestId("client-type")).toHaveTextContent("Desktop app");
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://localhost:8080/v1/health");
    expect((init.headers as Record<string, string>)["x-client-type"]).toBe("desktop");
  });
});
