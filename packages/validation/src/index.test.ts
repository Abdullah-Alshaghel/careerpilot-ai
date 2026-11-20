import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  apiBaseUrlSchema,
  browserSessionSchema,
  careerProfileSchema,
  companyFormSchema,
  companyListSchema,
  companySchema,
  connectorSlugSchema,
  documentListSchema,
  documentSchema,
  factValueSchemas,
  healthResponseSchema,
  loginFormSchema,
  registerFormSchema,
  tokenResponseSchema,
  userSchema,
} from "./index";

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

const example = (name: string) => JSON.parse(readFileSync(examplesDir + name, "utf8"));

describe("identity contracts", () => {
  it.each([
    ["user.json", userSchema],
    ["auth-token.json", tokenResponseSchema],
    ["auth-session.json", browserSessionSchema],
  ] as const)("accepts shared contract example %s", (file, schema) => {
    expect(schema.safeParse(example(file)).success).toBe(true);
  });

  it("never lets a refresh token into a browser session", () => {
    const withRefresh = { ...example("auth-session.json"), refresh_token: "x".repeat(43) };
    expect(browserSessionSchema.safeParse(withRefresh).success).toBe(false);
  });

  it("rejects malformed refresh tokens and unknown locales", () => {
    const token = example("auth-token.json");
    expect(tokenResponseSchema.safeParse({ ...token, refresh_token: "short" }).success).toBe(false);
    expect(userSchema.safeParse({ ...token.user, locale: "fr" }).success).toBe(false);
  });
});

describe("registerFormSchema", () => {
  const valid = {
    email: "ada@example.com",
    password: "correct horse battery",
    confirmPassword: "correct horse battery",
  };
  const firstError = (form: object) =>
    registerFormSchema.safeParse(form).error?.issues[0]?.message ?? null;

  it("accepts a valid form", () => expect(firstError(valid)).toBeNull());
  it.each([
    [{ email: "nope" }, "auth.error.emailInvalid"],
    [{ email: "" }, "auth.error.emailRequired"],
    [{ password: "short", confirmPassword: "short" }, "auth.error.passwordTooShort"],
    [{ password: " ".repeat(12), confirmPassword: " ".repeat(12) }, "auth.error.passwordTooShort"],
    [{ confirmPassword: "different value" }, "auth.error.passwordMismatch"],
  ])("rejects %o with %s", (override, message) => {
    expect(firstError({ ...valid, ...override })).toBe(message);
  });
});

describe("loginFormSchema", () => {
  it("requires both fields but does not judge password strength", () => {
    expect(loginFormSchema.safeParse({ email: "a@b.co", password: "x" }).success).toBe(true);
    expect(loginFormSchema.safeParse({ email: "", password: "" }).success).toBe(false);
  });
});

describe("document and profile contracts", () => {
  it.each([
    ["document.json", documentSchema],
    ["document-list.json", documentListSchema],
    ["career-profile.json", careerProfileSchema],
  ] as const)("accepts shared contract example %s", (file, schema) => {
    const result = schema.safeParse(example(file));
    expect(result.error?.issues ?? []).toEqual([]);
  });

  it("every example fact satisfies its per-type value schema", () => {
    const profile = careerProfileSchema.parse(example("career-profile.json"));
    for (const fact of profile.facts) {
      expect(factValueSchemas[fact.fact_type].safeParse(fact.value).success).toBe(true);
    }
  });

  it("rejects unknown document statuses and verification states", () => {
    expect(
      documentSchema.safeParse({ ...example("document.json"), status: "scanning" }).success,
    ).toBe(false);
    const profile = example("career-profile.json");
    profile.facts[0].verification_status = "auto_verified";
    expect(careerProfileSchema.safeParse(profile).success).toBe(false);
  });
});

describe("factValueSchemas (editor)", () => {
  it("normalizes empty optional fields to null", () => {
    const job = factValueSchemas.employment.parse({
      title: " Engineer ",
      organization: "",
      start: "2020-03",
      end: "",
      current: true,
      description: "",
    });
    expect(job).toEqual({
      title: "Engineer",
      organization: null,
      start: "2020-03",
      end: null,
      current: true,
      description: null,
    });
  });

  it.each([
    [
      "employment",
      { title: "", organization: null, start: null, end: null, current: false, description: null },
      "profile.error.required",
    ],
    [
      "employment",
      {
        title: "x",
        organization: null,
        start: "March 2020",
        end: null,
        current: false,
        description: null,
      },
      "profile.error.date",
    ],
    [
      "education",
      { degree: "", institution: "", start: null, end: null },
      "profile.error.degreeOrInstitution",
    ],
    ["certification", { name: "CKA", year: 1800 }, "profile.error.year"],
    ["skill", { name: "   " }, "profile.error.required"],
  ] as const)("%s rejects %o", (type, value, message) => {
    const result = factValueSchemas[type].safeParse(value);
    expect(result.error?.issues[0]?.message).toBe(message);
  });
});

describe("company contracts", () => {
  it.each([
    ["company.json", companySchema],
    ["company-list.json", companyListSchema],
  ] as const)("accepts shared contract example %s", (file, schema) => {
    expect(schema.safeParse(example(file)).error?.issues ?? []).toEqual([]);
  });

  it.each([
    ["", true],
    ["https://acme.com/jobs", true],
    ["acme.com/careers", true],
    ["acme.com:443/jobs", true],
    ["http://acme.com", false],
    ["javascript:alert(1)", false],
  ])("company form careers_url %s -> %s", (url, ok) => {
    const form = { name: "Acme", careers_url: url, region: "", priority: "medium", notes: "" };
    expect(companyFormSchema.safeParse(form).success).toBe(ok);
  });

  it.each([
    ["acme", true],
    ["acme-inc_2.0", true],
    ["../admin", false],
    ["", false],
  ])("connector slug %s -> %s", (slug, ok) => {
    expect(connectorSlugSchema.safeParse(slug).success).toBe(ok);
  });
});
