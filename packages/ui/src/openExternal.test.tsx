import { createDirectSessionTransport } from "@careerpilot/api-client";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AppProviders, useOpenExternal } from "./providers";
import { fakeGateway, MemoryStore } from "./test-utils";

function Opener({ url }: { url: string }) {
  const open = useOpenExternal();
  return <button onClick={() => open(url)}>open</button>;
}

function renderWith(url: string, openExternal: (url: string) => void) {
  render(
    <AppProviders
      apiBaseUrl="http://localhost:8080"
      clientType="desktop"
      fetch={fakeGateway({})}
      openExternal={openExternal}
      sessionTransport={(api) => createDirectSessionTransport(api, new MemoryStore())}
    >
      <Opener url={url} />
    </AppProviders>,
  );
  fireEvent.click(screen.getByRole("button", { name: "open" }));
}

describe("useOpenExternal", () => {
  it("passes https links to the platform opener", () => {
    const open = vi.fn();
    renderWith("https://jobs.lever.co/acme", open);
    expect(open).toHaveBeenCalledWith("https://jobs.lever.co/acme");
  });

  it.each([
    "javascript:alert(1)",
    "file:///C:/Windows/System32/calc.exe",
    "http://acme.com",
    "ms-settings:privacy",
    "",
  ])("never opens %s", (url) => {
    const open = vi.fn();
    renderWith(url, open);
    expect(open).not.toHaveBeenCalled();
  });
});
