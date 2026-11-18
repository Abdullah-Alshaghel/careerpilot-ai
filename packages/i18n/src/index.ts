import { ar, en, type MessageKey, type Messages } from "./messages";

export type { MessageKey, Messages };

export const SUPPORTED_LOCALES = ["en", "ar"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export type Direction = "ltr" | "rtl";

export const DEFAULT_LOCALE: Locale = "en";

const dictionaries: Record<Locale, Messages> = { en, ar };
const RTL_LOCALES: ReadonlySet<Locale> = new Set(["ar"]);

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

export function getDirection(locale: Locale): Direction {
  return RTL_LOCALES.has(locale) ? "rtl" : "ltr";
}

export type Translate = (key: MessageKey, params?: Record<string, string | number>) => string;

export function createTranslator(locale: Locale): Translate {
  const messages = dictionaries[locale];
  return (key, params) => {
    const template = messages[key] ?? en[key] ?? key;
    if (!params) return template;
    return template.replace(/\{(\w+)\}/g, (match, name: string) =>
      name in params ? String(params[name]) : match,
    );
  };
}

export function formatTime(date: Date, locale: Locale): string {
  // Arabic UI with Latin digits is the common convention for product UIs in the Gulf;
  // switch to "ar-u-nu-arab" here if Eastern Arabic numerals are preferred.
  const tag = locale === "ar" ? "ar-u-nu-latn" : "en-GB";
  return new Intl.DateTimeFormat(tag, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

/** Apply lang/dir to the document root (call from the host app on locale change). */
export function applyDocumentLocale(locale: Locale, doc: Document = document): void {
  doc.documentElement.lang = locale;
  doc.documentElement.dir = getDirection(locale);
}

export const messages = dictionaries;
