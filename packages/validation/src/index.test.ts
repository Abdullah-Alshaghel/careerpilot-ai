import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { apiBaseUrlSchema, healthResponseSchema } from "./index";

// Contract test: the same canonical examples are validated by the Python
// models (tests/contract) — drift on either side fails CI.
const examplesDir = fileURLToPath(
  new URL("../../../shared/api-contracts/examples/", import.meta.url),
);
const healthExamples = readdirSync(examplesDir).filter((f) => f.startsWith("health."));

describe("healthResponseSchema", () => {
  it("has canonical examples to check", () => {
    expect(healthExamples.length).toBeGreaterThan(0);
  });

  it.each(healthExamples)("accepts shared contract example %s", (file) => {
    const payload = JSON.parse(readFileSync(examplesDir + file, "utf8"));
    expect(healthResponseSchema.safeParse(payload).success).toBe(true);
  });

  it("rejects unknown status values", () => {
    const bad = {
      service: "x",
      version: "1",
      status: "great",
      timestamp: "2026-11-18T00:00:00Z",
      checks: [],
    };
    expect(healthResponseSchema.safeParse(bad).success).toBe(false);
  });

  it("rejects unexpected fields (strict contract)", () => {
    const bad = {
      service: "x",
      version: "1",
      status: "ok",
      timestamp: "2026-11-18T00:00:00Z",
      checks: [],
      extra: 1,
    };
    expect(healthResponseSchema.safeParse(bad).success).toBe(false);
  });
});

describe("apiBaseUrlSchema", () => {
  it.each(["http://localhost:8080", "https://api.careerpilot.example"])("accepts %s", (url) => {
    expect(apiBaseUrlSchema.safeParse(url).success).toBe(true);
  });

  it.each(["ftp://x", "javascript:alert(1)", "http://localhost:8080/v1", "not a url"])(
    "rejects %s",
    (url) => {
      expect(apiBaseUrlSchema.safeParse(url).success).toBe(false);
    },
  );
});
