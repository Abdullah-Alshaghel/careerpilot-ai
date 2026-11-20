import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { usePreferences } from "../preferences";
import { ADA, apiError, fakeGateway, json, MemoryStore, renderApp, tokensFor } from "../test-utils";

const now = "2026-11-20T12:00:00Z";
const RUN = "e1000000-0000-4000-8000-000000000001";

function job(n: number, extra: Record<string, unknown> = {}) {
  return {
    id: `f1000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
    company_id: "c1000000-0000-4000-8000-000000000001",
    company_name: "Acme",
    source_type: "greenhouse",
    title: `Engineer ${n}`,
    url: `https://job-boards.greenhouse.io/acme/jobs/${n}`,
    location: "Riyadh",
    remote: null,
    employment_type: null,
    department: null,
    posted_at: "2026-11-18T09:00:00Z",
    first_seen_at: now,
    listing_status: "active",
    duplicate_of_id: null,
    is_new: true,
    ...extra,
  };
}

function run(status: string, sources: Record<string, unknown>[]) {
  return {
    id: RUN,
    status,
    jobs_found: sources.reduce((n, s) => n + Number(s.jobs_found ?? 0), 0),
    jobs_new: sources.reduce((n, s) => n + Number(s.jobs_new ?? 0), 0),
    created_at: now,
    started_at: now,
    finished_at: status === "completed" || status === "failed" ? now : null,
    sources,
  };
}

const source = (company: string, status: string, extra: Record<string, unknown> = {}) => ({
  company_id: "c1000000-0000-4000-8000-000000000001",
  company_name: company,
  connector_type: "greenhouse",
  status,
  error_code: null,
  jobs_found: 0,
  jobs_new: 0,
  ...extra,
});

function backend() {
  const state = {
    runs: [] as ReturnType<typeof run>[],
    jobs: [] as ReturnType<typeof job>[],
    descriptions: new Map<string, string>(),
    startResponse: null as Response | null,
    listQueries: [] as string[],
    imports: [] as unknown[],
  };
  const base = fakeGateway({
    "POST /v1/auth/refresh": () => json(tokensFor(ADA, 2)),
    "GET /v1/documents": () => json({ documents: [] }),
    "GET /v1/searches": () => json({ runs: state.runs }),
    "POST /v1/searches": () => {
      if (state.startResponse) return state.startResponse;
      const created = run("running", [source("Acme", "pending"), source("Globex", "pending")]);
      state.runs.unshift(created);
      return json(created, 202);
    },
    "POST /v1/jobs/import": (body) => {
      state.imports.push(body);
      const { url } = body as { url: string };
      if (url.includes("nothing")) return apiError(422, "no_job_found");
      const imported = job(99, { title: "Imported Role", source_type: "manual", is_new: false });
      state.jobs.push(imported);
      return json({ ...imported, description: "From a link" }, 201);
    },
  });
  const fetch = async (url: string, init?: RequestInit) => {
    const parsed = new URL(url);
    if (parsed.pathname === "/v1/jobs" && (init?.method ?? "GET") === "GET") {
      state.listQueries.push(parsed.search);
      const q = parsed.searchParams.get("q")?.toLowerCase();
      const jobs = state.jobs.filter((j) => !q || j.title.toLowerCase().includes(q));
      return json({ jobs, total: jobs.length });
    }
    const detail = /^\/v1\/jobs\/([^/]+)$/.exec(parsed.pathname);
    if (detail && detail[1] !== "import") {
      const found = state.jobs.find((j) => j.id === detail[1])!;
      return json({
        ...found,
        description: state.descriptions.get(found.id) ?? "Plain description",
      });
    }
    return base(url, init);
  };
  return { state, fetch };
}

async function open(b = backend(), openExternal?: (url: string) => void) {
  renderApp(b.fetch, new MemoryStore(tokensFor(ADA).refresh_token), "web", openExternal);
  fireEvent.click(await screen.findByTestId("nav-jobs"));
  await screen.findByRole("heading", { level: 1, name: "Jobs" });
  return b;
}

beforeEach(() => {
  localStorage.clear();
  act(() => usePreferences.setState({ locale: "en" }));
});

describe("jobs", () => {
  it("explains what to do when there are no jobs or sources", async () => {
    const b = backend();
    b.state.startResponse = apiError(422, "no_sources");
    await open(b);
    expect(await screen.findByTestId("jobs-empty")).toHaveTextContent("Search your job sources");
    fireEvent.click(screen.getByRole("button", { name: "Search my job sources" }));
    expect(await screen.findByTestId("search-error")).toHaveTextContent(
      "Add a company with a job source",
    );
  });

  it("runs a search, shows each source's result, then the jobs", async () => {
    const b = await open();
    fireEvent.click(screen.getByRole("button", { name: "Search my job sources" }));
    expect(await screen.findByText("Searching 2 sources…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Search my job sources" })).toBeDisabled();

    // The worker finishes: one source worked, one was refused for safety.
    b.state.runs[0] = run("completed", [
      source("Acme", "succeeded", { jobs_found: 2, jobs_new: 2 }),
      source("Globex", "failed", { error_code: "blocked_address" }),
    ]);
    b.state.jobs.push(job(1), job(2, { remote: true }));

    const status = await screen.findByTestId("search-status", {}, { timeout: 5_000 });
    await waitFor(() => expect(status).toHaveAttribute("data-status", "completed"), {
      timeout: 5_000,
    });
    expect(status).toHaveTextContent("Last search: 2 jobs found, 2 new");
    expect(status).toHaveTextContent("Refused for safety");
    await waitFor(() => expect(screen.getAllByTestId("job-row")).toHaveLength(2), {
      timeout: 5_000,
    });
    expect(screen.getAllByText("New")).toHaveLength(2);
    expect(screen.getByText("Remote")).toBeInTheDocument();
  });

  it("asks the user to wait when searching too often", async () => {
    const b = backend();
    b.state.startResponse = apiError(429, "search_too_soon", { "retry-after": "42" });
    await open(b);
    fireEvent.click(screen.getByRole("button", { name: "Search my job sources" }));
    expect(await screen.findByTestId("search-error")).toHaveTextContent("wait 42 seconds");
  });

  it("filters the list on the server", async () => {
    const b = backend();
    b.state.jobs.push(job(1, { title: "Data Engineer" }), job(2, { title: "Designer" }));
    await open(b);
    await waitFor(() => expect(screen.getAllByTestId("job-row")).toHaveLength(2));
    fireEvent.change(screen.getByLabelText("Filter jobs"), { target: { value: "data" } });
    await waitFor(() => expect(screen.getAllByTestId("job-row")).toHaveLength(1));
    fireEvent.click(screen.getByLabelText("Remote only"));
    await waitFor(() => expect(b.state.listQueries.at(-1)).toBe("?q=data&remote_only=true"));
  });

  it("shows job text as plain text and opens the posting outside the app", async () => {
    const b = backend();
    const hostile = job(1, { title: "Engineer" });
    b.state.jobs.push(hostile);
    b.state.descriptions.set(
      hostile.id,
      'Great role <img src=x onerror="alert(1)"><script>steal()</script>',
    );
    const openExternal = vi.fn();
    await open(b, openExternal);

    fireEvent.click(await screen.findByTestId("job-row"));
    const detail = await screen.findByTestId("job-detail");
    const description = await within(detail).findByTestId("job-description");
    expect(description.textContent).toContain('<img src=x onerror="alert(1)">');
    expect(detail.querySelector("img, script")).toBeNull();

    fireEvent.click(within(detail).getByRole("button", { name: "Open job posting" }));
    expect(openExternal).toHaveBeenCalledWith(hostile.url);
  });

  it("imports a job from a link and explains failures", async () => {
    const b = await open();
    const form = screen.getByTestId("import-form");
    const field = within(form).getByLabelText("Job posting link (https://)");

    fireEvent.change(field, { target: { value: "https://example.com/nothing-here" } });
    fireEvent.click(within(form).getByRole("button", { name: "Import" }));
    expect(await within(form).findByTestId("import-message")).toHaveTextContent(
      "No job posting data was found",
    );

    fireEvent.change(field, { target: { value: "https://careers.example.com/jobs/1" } });
    fireEvent.click(within(form).getByRole("button", { name: "Import" }));
    expect(await within(form).findByTestId("import-message")).toHaveTextContent(
      "Imported: Imported Role",
    );
    expect(await screen.findByTestId("job-detail")).toHaveTextContent("Imported Role");
    expect(b.state.imports.at(-1)).toEqual({ url: "https://careers.example.com/jobs/1" });
  });

  it("works in Arabic", async () => {
    await open();
    fireEvent.click(screen.getByTestId("locale-toggle"));
    await waitFor(() => expect(document.documentElement.dir).toBe("rtl"));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("الوظائف");
    expect(screen.getAllByText("ابحث في مصادر الوظائف").length).toBeGreaterThan(0);
  });
});
