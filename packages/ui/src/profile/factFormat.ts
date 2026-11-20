import type { CareerFact, FactType } from "@careerpilot/contracts";
import { formatPartialDate, type Locale, type MessageKey, type Translate } from "@careerpilot/i18n";

export const SECTION_ORDER: readonly FactType[] = [
  "personal",
  "summary",
  "employment",
  "education",
  "skill",
  "certification",
  "language",
];

export const SECTION_LABEL: Record<FactType, MessageKey> = {
  personal: "section.personal",
  summary: "section.summary",
  employment: "section.employment",
  education: "section.education",
  skill: "section.skill",
  certification: "section.certification",
  language: "section.language",
};

const PERSONAL_LABEL: Record<string, MessageKey> = {
  full_name: "personal.full_name",
  email: "personal.email",
  phone: "personal.phone",
  location: "personal.location",
  link: "personal.link",
};

const str = (value: unknown): string => (typeof value === "string" ? value : "");

function dateRange(value: Record<string, unknown>, t: Translate, locale: Locale): string | null {
  const start = str(value.start);
  const end = value.current ? t("fact.present") : str(value.end);
  if (!start && !end) return null;
  const fmt = (d: string) => (d === t("fact.present") ? d : formatPartialDate(d, locale));
  return [start && fmt(start), end && fmt(end)].filter(Boolean).join(" – ");
}

/** The main line, a secondary line (dates, level, field name) for display. */
export function describeFact(
  fact: Pick<CareerFact, "fact_type" | "value">,
  t: Translate,
  locale: Locale,
): { primary: string; secondary: string | null } {
  const v = fact.value;
  switch (fact.fact_type) {
    case "personal": {
      const label = PERSONAL_LABEL[str(v.field)];
      return { primary: str(v.value), secondary: label ? t(label) : null };
    }
    case "summary":
      return { primary: str(v.text), secondary: null };
    case "employment":
      return {
        primary: [str(v.title), str(v.organization)].filter(Boolean).join(" · "),
        secondary: dateRange(v, t, locale),
      };
    case "education":
      return {
        primary: [str(v.degree), str(v.institution)].filter(Boolean).join(" · "),
        secondary: dateRange(v, t, locale),
      };
    case "certification":
      return {
        primary: str(v.name),
        secondary: typeof v.year === "number" ? String(v.year) : null,
      };
    case "language":
      return { primary: str(v.name), secondary: str(v.level) || null };
    case "skill":
      return { primary: str(v.name), secondary: null };
  }
}

export type ConfidenceBand = "high" | "medium" | "low";

export function confidenceBand(confidence: number): ConfidenceBand {
  if (confidence >= 0.8) return "high";
  if (confidence >= 0.6) return "medium";
  return "low";
}

export function failureMessage(reason: string | null): MessageKey {
  switch (reason) {
    case "encrypted_pdf":
      return "doc.failure.encrypted_pdf";
    case "no_text":
      return "doc.failure.no_text";
    case "timeout":
    case "too_complex":
    case "processing_failed":
      return "doc.failure.slow";
    default:
      return "doc.failure.unreadable_file";
  }
}
