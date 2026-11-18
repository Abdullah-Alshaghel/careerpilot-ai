import { describe, expect, it } from "vitest";
import { createTranslator, getDirection, isLocale, messages, SUPPORTED_LOCALES } from "./index";

describe("i18n", () => {
  it("uses RTL for Arabic and LTR for English", () => {
    expect(getDirection("ar")).toBe("rtl");
    expect(getDirection("en")).toBe("ltr");
  });

  it("every locale defines every key with a non-empty value", () => {
    const keys = Object.keys(messages.en).sort();
    for (const locale of SUPPORTED_LOCALES) {
      expect(Object.keys(messages[locale]).sort()).toEqual(keys);
      for (const value of Object.values(messages[locale])) expect(value.trim()).not.toBe("");
    }
  });

  it("interpolates parameters and keeps unknown placeholders", () => {
    const t = createTranslator("en");
    expect(t("status.latency", { ms: 12 })).toBe("12 ms");
    expect(t("status.lastChecked")).toBe("Last checked {time}");
    expect(createTranslator("ar")("status.retry")).toBe("إعادة المحاولة");
  });

  it("guards locale values from untrusted input", () => {
    expect(isLocale("ar")).toBe(true);
    expect(isLocale("fr")).toBe(false);
    expect(isLocale(undefined)).toBe(false);
  });
});
