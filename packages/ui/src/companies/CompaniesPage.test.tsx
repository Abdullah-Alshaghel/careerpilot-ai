import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { usePreferences } from "../preferences";
import { ADA, apiError, fakeGateway, json, MemoryStore, renderApp, tokensFor } from "../test-utils";

const now = "2026-11-20T10:00:00Z";
let seq = 0;
const uuid = () => `c1000000-0000-4000-8000-${String(++seq).padStart(12, "0")}`;

type Company = {
  id: string;
  name: string;
  careers_url: string | null;
  region: string | null;
  priority: string;
  notes: string | null;
  version: number;
  connectors: {
    id: string;
    connector_type: string;
    config: Record<string, string>;
    enabled: boolean;
    created_at: string;
    updated_at: string;
  }[];
  detected_connector: { connector_type: string; config: Record<string, string> } | null;
  created_at: string;
  updated_at: string;
};

function detect(url: string | null) {
  const match = url && /^https:\/\/jobs\.lever\.co\/([\w-]+)/.exec(url);
  return match ? { connector_type: "lever", config: { company: match[1]! } } : null;
}

/** Fake Company Service with the same rules as the real one. */
function backend() {
  const state = { companies: [] as Company[], requests: [] as { key: string; body: unknown }[] };
  const refreshDetection = (c: Company) => {
    const found = detect(c.careers_url);
    const added = c.connectors.some(
      (k) =>
        found &&
        k.connector_type === found.connector_type &&
        k.config.company === found.config.company,
    );
    c.detected_connector = found && !added ? found : null;
  };
  const base = fakeGateway({
    "POST /v1/auth/refresh": () => json(tokensFor(ADA, 2)),
    "GET /v1/documents": () => json({ documents: [] }),
    "GET /v1/companies": () => json({ companies: state.companies }),
    "POST /v1/companies": (body) => {
      const input = body as Partial<Company>;
      state.requests.push({ key: "create", body });
      if (input.careers_url?.includes(".local")) return apiError(422, "private_host");
      if (state.companies.some((c) => c.name.toLowerCase() === input.name!.toLowerCase()))
        return apiError(409, "company_exists");
      const company: Company = {
        id: uuid(),
        name: input.name!,
        careers_url: input.careers_url ?? null,
        region: input.region ?? null,
        priority: input.priority ?? "medium",
        notes: input.notes ?? null,
        version: 1,
        connectors: [],
        detected_connector: null,
        created_at: now,
        updated_at: now,
      };
      refreshDetection(company);
      state.companies.push(company);
      return json(company, 201);
    },
  });
  const fetch = async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    const company = /^\/v1\/companies\/([^/]+)/.exec(path);
    const target = company && state.companies.find((c) => c.id === company[1]);
    const connector = /\/connectors\/([^/]+)$/.exec(path);
    if (target && /\/connectors$/.test(path) && init?.method === "POST") {
      state.requests.push({ key: "add-connector", body });
      target.connectors.push({
        id: uuid(),
        ...body,
        enabled: true,
        created_at: now,
        updated_at: now,
      });
      target.version += 1;
      refreshDetection(target);
      return json(target, 201);
    }
    if (target && connector && init?.method === "PATCH") {
      state.requests.push({ key: "toggle", body });
      target.connectors.find((k) => k.id === connector[1])!.enabled = body.enabled;
      target.version += 1;
      return json(target);
    }
    if (target && connector && init?.method === "DELETE") {
      target.connectors = target.connectors.filter((k) => k.id !== connector[1]);
      refreshDetection(target);
      return json(target);
    }
    if (target && init?.method === "PATCH") {
      state.requests.push({ key: "update", body });
      if (body.version !== target.version) {
        target.version += 0; // the server keeps its newer copy
        return apiError(409, "version_conflict");
      }
      Object.assign(target, body, { version: target.version + 1 });
      refreshDetection(target);
      return json(target);
    }
    if (target && init?.method === "DELETE") {
      state.companies = state.companies.filter((c) => c !== target);
      return new Response(null, { status: 204 });
    }
    return base(url, init);
  };
  return { state, fetch };
}

async function open(b = backend(), openExternal?: (url: string) => void) {
  renderApp(b.fetch, new MemoryStore(tokensFor(ADA).refresh_token), "desktop", openExternal);
  fireEvent.click(await screen.findByTestId("nav-companies"));
  await screen.findByRole("heading", { level: 1, name: "Companies" });
  return b;
}

function fillCompany(fields: Record<string, string>) {
  const form = screen.getByTestId("company-form");
  for (const [label, value] of Object.entries(fields)) {
    fireEvent.change(within(form).getByLabelText(label), { target: { value } });
  }
  fireEvent.click(within(form).getByRole("button", { name: "Save" }));
}

async function addAcme(b: ReturnType<typeof backend>) {
  fireEvent.click(screen.getByRole("button", { name: "Add company" }));
  fillCompany({
    "Company name": "Acme",
    "Careers page (https://)": "https://jobs.lever.co/acme",
    "Region or city": "Riyadh",
  });
  await screen.findByTestId("company-card");
  return b.state.companies[0]!;
}

beforeEach(() => {
  localStorage.clear();
  act(() => usePreferences.setState({ locale: "en" }));
});

describe("companies", () => {
  it("starts empty and adds a company", async () => {
    const b = await open();
    expect(await screen.findByTestId("companies-empty")).toBeInTheDocument();
    await addAcme(b);
    const card = screen.getByTestId("company-card");
    expect(within(card).getByRole("heading", { name: "Acme" })).toBeInTheDocument();
    expect(within(card).getByText("Medium priority")).toBeInTheDocument();
    expect(within(card).getByText("Riyadh")).toBeInTheDocument();
    expect(b.state.requests[0]!.body).toEqual({
      name: "Acme",
      careers_url: "https://jobs.lever.co/acme",
      region: "Riyadh",
      priority: "medium",
      notes: null,
    });
  });

  it("checks the form before sending it", async () => {
    const b = await open();
    fireEvent.click(screen.getByRole("button", { name: "Add company" }));
    fillCompany({ "Careers page (https://)": "http://acme.com" });
    expect(await screen.findByText("Enter the company name")).toBeInTheDocument();
    expect(screen.getByText("Use an https:// address")).toBeInTheDocument();
    expect(b.state.requests).toEqual([]);
  });

  it("shows the server's reason for an unsafe address next to the field", async () => {
    const b = await open();
    fireEvent.click(screen.getByRole("button", { name: "Add company" }));
    fillCompany({ "Company name": "Printer", "Careers page (https://)": "https://printer.local" });
    expect(await screen.findByText("Use a public website address")).toBeInTheDocument();
    expect(screen.getByLabelText("Careers page (https://)")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
    expect(b.state.companies).toEqual([]);
  });

  it("refuses a duplicate company name", async () => {
    const b = await open();
    await addAcme(b);
    fireEvent.click(screen.getByRole("button", { name: "Add company" }));
    fillCompany({ "Company name": "ACME" });
    expect(
      await screen.findByText("You already track a company with this name."),
    ).toBeInTheDocument();
  });

  it("edits with the version it read, and explains a conflict from another device", async () => {
    const b = await open();
    const acme = await addAcme(b);
    fireEvent.click(screen.getByRole("button", { name: "Edit: Acme" }));
    fireEvent.change(screen.getByLabelText("Priority"), { target: { value: "high" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    // Wait for the form to close: its <select> also contains the text "High priority".
    await waitFor(() => expect(screen.queryByTestId("company-form")).not.toBeInTheDocument());
    expect(
      within(screen.getByTestId("company-card")).getByText("High priority"),
    ).toBeInTheDocument();
    expect(b.state.requests.find((r) => r.key === "update")!.body).toMatchObject({
      priority: "high",
      version: 1,
    });

    // Another device changes it; this screen still has the old version.
    fireEvent.click(screen.getByRole("button", { name: "Edit: Acme" }));
    acme.version += 5;
    fireEvent.change(screen.getByLabelText("Notes"), { target: { value: "Call Sara" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByTestId("company-form-error")).toHaveTextContent(
      "changed on another device",
    );
  });

  it("offers a detected ATS board and manages job sources", async () => {
    const b = await open();
    await addAcme(b);
    const detected = await screen.findByTestId("detected-connector");
    expect(detected).toHaveTextContent("This careers page is a Lever job board: acme.");
    fireEvent.click(within(detected).getByRole("button", { name: "Add as job source" }));

    const row = await screen.findByTestId("connector-row");
    expect(row).toHaveTextContent("Lever job board");
    expect(row).toHaveTextContent("acme");
    expect(within(row).getByText("Active")).toBeInTheDocument();
    expect(screen.queryByTestId("detected-connector")).not.toBeInTheDocument();

    fireEvent.click(within(row).getByRole("button", { name: "Pause" }));
    expect(
      await within(screen.getByTestId("connector-row")).findByText("Paused"),
    ).toBeInTheDocument();
    expect(b.state.requests.find((r) => r.key === "toggle")!.body).toEqual({ enabled: false });

    fireEvent.click(screen.getByRole("button", { name: "Remove: acme" }));
    await waitFor(() => expect(screen.queryByTestId("connector-row")).not.toBeInTheDocument());
  });

  it("validates a board name before adding a job source", async () => {
    const b = await open();
    await addAcme(b);
    fireEvent.click(screen.getByRole("button", { name: "Add job source: Acme" }));
    const form = screen.getByTestId("connector-form");
    fireEvent.change(within(form).getByLabelText("Source type"), {
      target: { value: "greenhouse" },
    });
    const field = within(form).getByLabelText("Board name (from boards.greenhouse.io/…)");
    fireEvent.change(field, { target: { value: "../admin" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));
    expect(await within(form).findByText(/Use letters, digits/)).toBeInTheDocument();

    fireEvent.change(field, { target: { value: "acme" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(screen.queryByTestId("connector-form")).not.toBeInTheDocument());
    expect(b.state.requests.find((r) => r.key === "add-connector")!.body).toEqual({
      connector_type: "greenhouse",
      config: { board_token: "acme" },
    });
  });

  it("opens the careers page outside the app", async () => {
    const openExternal = vi.fn();
    const b = await open(backend(), openExternal);
    await addAcme(b);
    fireEvent.click(screen.getByRole("button", { name: "Open careers page" }));
    expect(openExternal).toHaveBeenCalledWith("https://jobs.lever.co/acme");
  });

  it("deletes a company after confirmation", async () => {
    const b = await open();
    await addAcme(b);
    fireEvent.click(screen.getByRole("button", { name: "Delete: Acme" }));
    expect(screen.getByText("Delete Acme and its job sources?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Yes, delete" }));
    expect(await screen.findByTestId("companies-empty")).toBeInTheDocument();
    expect(b.state.companies).toEqual([]);
  });

  it("filters by name and works in Arabic", async () => {
    const b = await open();
    await addAcme(b);
    fireEvent.click(screen.getByRole("button", { name: "Add company" }));
    fillCompany({ "Company name": "Globex" });
    await waitFor(() => expect(screen.getAllByTestId("company-card")).toHaveLength(2));
    fireEvent.change(screen.getByLabelText("Search companies"), { target: { value: "glo" } });
    expect(screen.getAllByTestId("company-card")).toHaveLength(1);

    fireEvent.click(screen.getByTestId("locale-toggle"));
    await waitFor(() => expect(document.documentElement.dir).toBe("rtl"));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("الشركات");
    expect(screen.getByText("مصادر الوظائف")).toBeInTheDocument();
  });
});
