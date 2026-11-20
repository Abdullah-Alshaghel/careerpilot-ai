import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { usePreferences } from "../preferences";
import { ADA, apiError, fakeGateway, json, MemoryStore, renderApp, tokensFor } from "../test-utils";

const DOC_ID = "3d2c1b0a-9e8f-4a7b-8c6d-5e4f3a2b1c0d";
const now = "2026-11-20T09:00:00Z";

function doc(status: string, extra: Record<string, unknown> = {}) {
  return {
    id: DOC_ID,
    filename: "Ada CV.pdf",
    mime_type: "application/pdf",
    size_bytes: 2048,
    status,
    failure_reason: null,
    created_at: now,
    updated_at: now,
    ...extra,
  };
}

let seq = 0;
function fact(
  fact_type: string,
  value: Record<string, unknown>,
  extra: Record<string, unknown> = {},
) {
  seq += 1;
  return {
    id: `a1000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
    fact_type,
    value,
    source: { document_id: DOC_ID, name: "Ada CV.pdf", excerpt: `line ${seq} of the CV` },
    confidence: 0.75,
    verification_status: "extracted",
    valid_from: null,
    valid_to: null,
    created_at: now,
    updated_at: now,
    ...extra,
  };
}

/** A fake backend that remembers documents and facts. */
function backend() {
  const state = {
    documents: [] as ReturnType<typeof doc>[],
    facts: [] as ReturnType<typeof fact>[],
    conflicts: [] as unknown[],
    requests: [] as { key: string; body: unknown }[],
  };
  const profile = () => ({
    id: "b2000000-0000-4000-8000-000000000001",
    version: 1,
    updated_at: now,
    facts: state.facts,
    conflicts: state.conflicts,
  });
  const record = (key: string) => (body: unknown) => {
    state.requests.push({ key, body });
  };
  const fetch = fakeGateway({
    "POST /v1/auth/refresh": () => json(tokensFor(ADA, 2)),
    "GET /v1/documents": () => json({ documents: state.documents }),
    "GET /v1/profile": () => json(profile()),
    "POST /v1/documents": (_b, init) => {
      const file = (init!.body as FormData).get("file") as File;
      record("upload")(file.name);
      const created = doc("uploaded", { filename: file.name });
      const existed = state.documents.some((d) => d.id === created.id);
      if (!existed) state.documents.push(created);
      return json(created, existed ? 200 : 201);
    },
    "POST /v1/profile/facts/bulk-status": (body) => {
      record("bulk")(body);
      const { fact_ids, verification_status } = body as {
        fact_ids: string[];
        verification_status: string;
      };
      for (const f of state.facts)
        if (fact_ids.includes(f.id)) f.verification_status = verification_status;
      return json({ updated: fact_ids.length });
    },
    "POST /v1/profile/facts": (body) => {
      record("add")(body);
      const { fact_type, value } = body as { fact_type: string; value: Record<string, unknown> };
      const created = fact(fact_type, value, {
        source: null,
        confidence: 1,
        verification_status: "user_verified",
      });
      state.facts.push(created);
      return json(created, 201);
    },
  });
  // Routes with ids: delegate to the fake above for everything else.
  const routed = async (url: string, init?: RequestInit) => {
    const path = new URL(url).pathname;
    const factMatch = /^\/v1\/profile\/facts\/([^/]+)$/.exec(path);
    if (factMatch && init?.method === "PATCH") {
      const body = JSON.parse(init.body as string);
      record("patch")({ id: factMatch[1], ...body });
      const target = state.facts.find((f) => f.id === factMatch[1])!;
      if (body.value)
        Object.assign(target, { value: body.value, verification_status: "user_verified" });
      if (body.verification_status) target.verification_status = body.verification_status;
      return json(target);
    }
    if (factMatch && init?.method === "DELETE") {
      record("delete-fact")(factMatch[1]);
      state.facts = state.facts.filter((f) => f.id !== factMatch[1]);
      return new Response(null, { status: 204 });
    }
    const docMatch = /^\/v1\/documents\/([^/]+)$/.exec(path);
    if (docMatch && init?.method === "DELETE") {
      record("delete-doc")(docMatch[1]);
      state.documents = state.documents.filter((d) => d.id !== docMatch[1]);
      return new Response(null, { status: 204 });
    }
    return fetch(url, init);
  };
  return { state, fetch: routed };
}

async function open(b = backend()) {
  renderApp(b.fetch, new MemoryStore(tokensFor(ADA).refresh_token));
  await screen.findByTestId("signed-in-user");
  return b;
}

function pdf(name = "Ada CV.pdf", size = 2048) {
  return new File([new Uint8Array(size)], name, { type: "application/pdf" });
}

beforeEach(() => {
  localStorage.clear();
  act(() => usePreferences.setState({ locale: "en" }));
});

describe("career profile: documents", () => {
  it("is the first page after sign-in and explains what to do", async () => {
    await open();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Career profile");
    expect(await screen.findByTestId("review-empty")).toHaveTextContent("Upload your CV");
    expect(screen.getByText("No documents yet.")).toBeInTheDocument();
  });

  it("uploads a file and shows it being read, then the suggestions", async () => {
    const b = await open();
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [pdf()] } });

    expect(await screen.findByTestId("document-status")).toHaveTextContent("Queued");
    expect(b.state.requests).toContainEqual({ key: "upload", body: "Ada CV.pdf" });
    expect(screen.getByTestId("review-empty")).toHaveTextContent("Reading your document");

    // The worker finishes and the profile service adds suggestions.
    // The worker finishes and the profile service adds suggestions.
    b.state.documents[0]!.status = "extracted";
    b.state.facts.push(fact("skill", { name: "Python" }));
    await waitFor(() => expect(screen.getByTestId("document-status")).toHaveTextContent("Read"), {
      timeout: 5_000,
    });
    expect(await screen.findByText("Python", {}, { timeout: 5_000 })).toBeInTheDocument();
    expect(screen.getByTestId("review-counts")).toHaveTextContent("1 to review");
  });

  it("refuses files over 10 MB before uploading them", async () => {
    const b = await open();
    fireEvent.change(screen.getByTestId("file-input"), {
      target: { files: [pdf("huge.pdf", 10 * 1024 * 1024 + 1)] },
    });
    expect(await screen.findByTestId("upload-message")).toHaveTextContent("larger than 10 MB");
    expect(b.state.requests.filter((r) => r.key === "upload")).toEqual([]);
  });

  it("explains server-side rejections", async () => {
    const b = backend();
    const fetch = b.fetch;
    b.fetch = async (url, init) =>
      url.endsWith("/v1/documents") && init?.method === "POST"
        ? apiError(415, "macro_document")
        : fetch(url, init);
    await open(b);
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [pdf("cv.docx")] } });
    expect(await screen.findByTestId("upload-message")).toHaveTextContent(
      "macros are not accepted",
    );
  });

  it("says when a file was already uploaded", async () => {
    const b = backend();
    b.state.documents.push(doc("extracted"));
    await open(b);
    await screen.findByTestId("document-row");
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [pdf()] } });
    expect(await screen.findByTestId("upload-message")).toHaveTextContent("already uploaded");
  });

  it("shows why a document could not be read", async () => {
    const b = backend();
    b.state.documents.push(doc("failed", { failure_reason: "no_text" }));
    await open(b);
    expect(await screen.findByText(/Scanned documents are not supported/)).toBeInTheDocument();
  });

  it("asks before deleting a document", async () => {
    const b = backend();
    b.state.documents.push(doc("extracted"));
    await open(b);
    fireEvent.click(await screen.findByRole("button", { name: "Delete Ada CV.pdf" }));
    expect(screen.getByText(/Suggestions from it that you have not reviewed/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(screen.queryByTestId("document-row")).not.toBeInTheDocument());
    expect(b.state.requests).toContainEqual({ key: "delete-doc", body: DOC_ID });
  });
});

describe("career profile: review", () => {
  function withFacts() {
    const b = backend();
    b.state.documents.push(doc("extracted"));
    b.state.facts.push(
      fact("personal", { field: "full_name", value: "Ada Lovelace" }, { confidence: 0.6 }),
      fact(
        "employment",
        {
          title: "Software Engineer",
          organization: "Acme Inc",
          start: "2021-03",
          end: null,
          current: true,
          description: null,
        },
        { confidence: 0.45 },
      ),
      fact("skill", { name: "Python" }, { confidence: 0.8 }),
      fact("skill", { name: "Go" }, { confidence: 0.8 }),
    );
    return b;
  }

  it("groups facts by section with source, confidence and status", async () => {
    await open(withFacts());
    const experience = await screen.findByTestId("section-employment");
    expect(within(experience).getByText("Software Engineer · Acme Inc")).toBeInTheDocument();
    expect(within(experience).getByText("Mar 2021 – present")).toBeInTheDocument();
    expect(within(experience).getByText("Low confidence: check carefully")).toBeInTheDocument();
    expect(within(experience).getByText("From Ada CV.pdf")).toBeInTheDocument();
    expect(screen.getByTestId("review-counts")).toHaveTextContent("4 to review · 0 approved");

    fireEvent.click(within(experience).getByRole("button", { name: "Show source text" }));
    expect(within(experience).getByTestId("fact-excerpt")).toHaveTextContent("of the CV");
  });

  it("approves and rejects single facts", async () => {
    const b = await open(withFacts());
    fireEvent.click(await screen.findByRole("button", { name: "Approve: Python" }));
    fireEvent.click(screen.getByRole("button", { name: "Reject: Go" }));
    await waitFor(() =>
      expect(screen.getByTestId("review-counts")).toHaveTextContent(
        "2 to review · 1 approved · 1 rejected",
      ),
    );
    const patches = b.state.requests.filter((r) => r.key === "patch").map((r) => r.body);
    expect(patches).toEqual([
      expect.objectContaining({ verification_status: "user_verified" }),
      expect.objectContaining({ verification_status: "rejected" }),
    ]);
  });

  it("approves everything remaining in one request", async () => {
    const b = await open(withFacts());
    fireEvent.click(await screen.findByRole("button", { name: "Approve all remaining" }));
    await waitFor(() =>
      expect(screen.getByTestId("review-counts")).toHaveTextContent("0 to review · 4 approved"),
    );
    const bulk = b.state.requests.find((r) => r.key === "bulk")!.body as { fact_ids: string[] };
    expect(bulk.fact_ids).toHaveLength(4);
  });

  it("edits a fact with validation, which approves it", async () => {
    const b = await open(withFacts());
    fireEvent.click(
      await screen.findByRole("button", { name: "Edit: Software Engineer · Acme Inc" }),
    );
    const editor = screen.getByTestId("editor-employment");
    expect(within(editor).getByLabelText("Job title")).toHaveValue("Software Engineer");

    fireEvent.change(within(editor).getByLabelText("Start (YYYY or YYYY-MM)"), {
      target: { value: "March 2021" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: "Save" }));
    expect(await within(editor).findByText("Use YYYY or YYYY-MM")).toBeInTheDocument();

    fireEvent.change(within(editor).getByLabelText("Start (YYYY or YYYY-MM)"), {
      target: { value: "2020-01" },
    });
    fireEvent.change(within(editor).getByLabelText("Job title"), {
      target: { value: "Senior Software Engineer" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Senior Software Engineer · Acme Inc")).toBeInTheDocument();
    const patch = b.state.requests.find((r) => r.key === "patch")!.body as {
      value: Record<string, unknown>;
    };
    expect(patch.value).toEqual({
      title: "Senior Software Engineer",
      organization: "Acme Inc",
      start: "2020-01",
      end: null,
      current: true,
      description: null,
    });
  });

  it("adds and deletes a fact entered by hand", async () => {
    const b = await open(withFacts());
    fireEvent.click(await screen.findByRole("button", { name: "Add: Skills" }));
    const editor = screen.getByTestId("editor-skill");
    fireEvent.change(within(editor).getByLabelText("Name"), { target: { value: "Kubernetes" } });
    fireEvent.click(within(editor).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Kubernetes")).toBeInTheDocument();
    expect(screen.getAllByText("Added by you")).toHaveLength(1);
    expect(b.state.requests).toContainEqual({
      key: "add",
      body: { fact_type: "skill", value: { name: "Kubernetes" } },
    });

    fireEvent.click(screen.getByRole("button", { name: "Delete: Kubernetes" }));
    await waitFor(() => expect(screen.queryByText("Kubernetes")).not.toBeInTheDocument());
  });

  it("shows conflicts for the user to resolve", async () => {
    const b = withFacts();
    const other = fact("personal", { field: "full_name", value: "Ada King" });
    b.state.facts.push(other);
    b.state.conflicts.push({
      kind: "multiple_values",
      fact_type: "personal",
      fact_ids: [b.state.facts[0]!.id, other.id],
    });
    await open(b);
    const banner = await screen.findByTestId("conflicts");
    expect(banner).toHaveTextContent("Your documents disagree");
    expect(banner).toHaveTextContent(
      "Different values for Personal details: Ada Lovelace / Ada King",
    );
  });

  it("renders the review in Arabic", async () => {
    await open(withFacts());
    await screen.findByTestId("section-skill");
    fireEvent.click(screen.getByTestId("locale-toggle"));
    await waitFor(() => expect(document.documentElement.dir).toBe("rtl"));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("الملف المهني");
    expect(within(screen.getByTestId("section-skill")).getByText("المهارات")).toBeInTheDocument();
    expect(screen.getByText("مارس 2021 – حتى الآن")).toBeInTheDocument();
  });
});
