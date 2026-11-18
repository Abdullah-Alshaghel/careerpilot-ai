import { createApiClient, type FetchLike } from "@careerpilot/api-client";
import { QueryClient } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "./providers";
import { usePreferences } from "./preferences";
import { StatusPage } from "./StatusPage";

const okBody = {
  service: "api-gateway",
  version: "0.1.0",
  status: "ok",
  timestamp: "2026-11-18T12:00:00Z",
  checks: [
    { name: "redis", status: "up", latency_ms: 1.4, detail: null },
    { name: "identity-service", status: "up", latency_ms: 7.6, detail: null },
  ],
};

function renderWith(fetch: FetchLike, clientType: "desktop" | "web" = "web") {
  const apiClient = createApiClient({ baseUrl: "http://localhost:8080", fetch, clientType });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <AppProviders
      apiBaseUrl="http://localhost:8080"
      clientType={clientType}
      apiClient={apiClient}
      queryClient={queryClient}
    >
      <StatusPage />
    </AppProviders>,
  );
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("StatusPage", () => {
  beforeEach(() => {
    localStorage.clear();
    act(() => usePreferences.setState({ locale: "en" }));
  });

  it("shows a loading state, then healthy checks", async () => {
    renderWith(vi.fn<FetchLike>(async () => json(okBody)));
    expect(screen.getByTestId("health-status")).toHaveAttribute("data-state", "loading");
    await waitFor(() =>
      expect(screen.getByTestId("health-status")).toHaveAttribute("data-state", "ok"),
    );
    expect(screen.getByTestId("health-status")).toHaveTextContent("All systems operational");
    expect(screen.getByTestId("check-identity-service")).toHaveTextContent("8 ms");
  });

  it("shows degraded status", async () => {
    const degraded = {
      ...okBody,
      status: "degraded",
      checks: [{ ...okBody.checks[0], status: "down" }],
    };
    renderWith(vi.fn<FetchLike>(async () => json(degraded)));
    await waitFor(() =>
      expect(screen.getByTestId("health-status")).toHaveAttribute("data-state", "degraded"),
    );
    expect(screen.getByTestId("check-redis")).toHaveTextContent("Down");
  });

  it("shows an unreachable state and recovers on retry", async () => {
    let fail = true;
    const fetch = vi.fn<FetchLike>(async () => {
      if (fail) throw new TypeError("Failed to fetch");
      return json(okBody);
    });
    renderWith(fetch);
    // useHealth retries once (1s backoff) before surfacing the error.
    await waitFor(
      () =>
        expect(screen.getByTestId("health-status")).toHaveAttribute("data-state", "unreachable"),
      {
        timeout: 4_000,
      },
    );
    expect(screen.getByTestId("health-error")).toHaveTextContent("Cannot reach the backend");

    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(screen.getByTestId("health-status")).toHaveAttribute("data-state", "ok"),
    );
  });

  it("switches to Arabic with RTL direction and persists the preference", async () => {
    renderWith(
      vi.fn<FetchLike>(async () => json(okBody)),
      "desktop",
    );
    expect(screen.getByTestId("client-type")).toHaveTextContent("Desktop app");

    fireEvent.click(screen.getByTestId("locale-toggle"));
    await waitFor(() => expect(document.documentElement.dir).toBe("rtl"));
    expect(document.documentElement.lang).toBe("ar");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("حالة النظام");
    expect(JSON.parse(localStorage.getItem("careerpilot.preferences")!).state.locale).toBe("ar");
  });
});
