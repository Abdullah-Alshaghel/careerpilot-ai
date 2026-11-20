import type { FactType } from "@careerpilot/contracts";
import { isMessageKey, type MessageKey } from "@careerpilot/i18n";
import { factValueSchemas } from "@careerpilot/validation";
import { useState, type FormEvent } from "react";
import { Alert, Button, TextField } from "../components";
import { useI18n } from "../providers";

type FieldKind = "text" | "textarea" | "date" | "checkbox" | "year" | "personal-field";

interface FieldSpec {
  key: string;
  kind: FieldKind;
  label: MessageKey;
}

const FIELDS: Record<FactType, readonly FieldSpec[]> = {
  personal: [
    { key: "field", kind: "personal-field", label: "field.field" },
    { key: "value", kind: "text", label: "field.value" },
  ],
  summary: [{ key: "text", kind: "textarea", label: "field.text" }],
  employment: [
    { key: "title", kind: "text", label: "field.title" },
    { key: "organization", kind: "text", label: "field.organization" },
    { key: "start", kind: "date", label: "field.start" },
    { key: "end", kind: "date", label: "field.end" },
    { key: "current", kind: "checkbox", label: "field.current" },
    { key: "description", kind: "textarea", label: "field.description" },
  ],
  education: [
    { key: "degree", kind: "text", label: "field.degree" },
    { key: "institution", kind: "text", label: "field.institution" },
    { key: "start", kind: "date", label: "field.start" },
    { key: "end", kind: "date", label: "field.end" },
  ],
  skill: [{ key: "name", kind: "text", label: "field.name" }],
  certification: [
    { key: "name", kind: "text", label: "field.name" },
    { key: "year", kind: "year", label: "field.year" },
  ],
  language: [
    { key: "name", kind: "text", label: "field.name" },
    { key: "level", kind: "text", label: "field.level" },
  ],
};

const PERSONAL_FIELDS = ["full_name", "email", "phone", "location", "link"] as const;

type FormState = Record<string, string | boolean>;

function toForm(type: FactType, value: Record<string, unknown> | null): FormState {
  const state: FormState = {};
  for (const field of FIELDS[type]) {
    const raw = value?.[field.key];
    if (field.kind === "checkbox") state[field.key] = raw === true;
    else if (field.kind === "personal-field")
      state[field.key] = typeof raw === "string" ? raw : "full_name";
    else state[field.key] = raw === null || raw === undefined ? "" : String(raw);
  }
  return state;
}

function toValue(type: FactType, form: FormState): Record<string, unknown> {
  const value: Record<string, unknown> = {};
  for (const field of FIELDS[type]) {
    const raw = form[field.key];
    if (field.kind === "checkbox") value[field.key] = raw === true;
    else if (field.kind === "year") {
      const text = String(raw ?? "").trim();
      value[field.key] = text === "" ? null : Number.isInteger(Number(text)) ? Number(text) : NaN;
    } else value[field.key] = String(raw ?? "");
  }
  return value;
}

export interface FactEditorProps {
  factType: FactType;
  /** Existing value to edit; null to add a new fact. */
  initial: Record<string, unknown> | null;
  onSave: (value: Record<string, unknown>) => Promise<unknown>;
  onCancel: () => void;
}

/** Form for one fact, validated with the same rules the server applies. */
export function FactEditor({ factType, initial, onSave, onCancel }: FactEditorProps) {
  const { t } = useI18n();
  const [form, setForm] = useState<FormState>(() => toForm(factType, initial));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  const set = (key: string, value: string | boolean) => setForm((f) => ({ ...f, [key]: value }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const parsed = factValueSchemas[factType].safeParse(toValue(factType, form));
    if (!parsed.success) {
      const next: Record<string, string> = {};
      for (const issue of parsed.error.issues) {
        const key = String(issue.path[0] ?? "");
        const fallback: MessageKey =
          key === "year" ? "profile.error.year" : "profile.error.required";
        next[key] ??= t(isMessageKey(issue.message) ? issue.message : fallback);
      }
      setErrors(next);
      return;
    }
    setErrors({});
    setSaving(true);
    setFailed(false);
    try {
      await onSave(parsed.data as Record<string, unknown>);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className="cp-fact-editor"
      onSubmit={submit}
      noValidate
      data-testid={`editor-${factType}`}
    >
      {failed ? <Alert>{t("profile.saveError")}</Alert> : null}
      {FIELDS[factType].map((field) => {
        const label = t(field.label);
        const error = errors[field.key];
        if (field.kind === "checkbox") {
          return (
            <label key={field.key} className="cp-checkbox">
              <input
                type="checkbox"
                checked={form[field.key] === true}
                onChange={(e) => set(field.key, e.target.checked)}
              />
              {label}
            </label>
          );
        }
        if (field.kind === "personal-field") {
          return (
            <label key={field.key} className="cp-field">
              <span className="cp-field-label">{label}</span>
              <select
                className="cp-input"
                value={String(form[field.key])}
                onChange={(e) => set(field.key, e.target.value)}
              >
                {PERSONAL_FIELDS.map((name) => (
                  <option key={name} value={name}>
                    {t(`personal.${name}` as MessageKey)}
                  </option>
                ))}
              </select>
            </label>
          );
        }
        if (field.kind === "textarea") {
          return (
            <label key={field.key} className="cp-field">
              <span className="cp-field-label">{label}</span>
              <textarea
                className="cp-input"
                rows={3}
                value={String(form[field.key])}
                aria-invalid={error ? true : undefined}
                onChange={(e) => set(field.key, e.target.value)}
              />
              {error ? <span className="cp-field-error">{error}</span> : null}
            </label>
          );
        }
        return (
          <TextField
            key={field.key}
            label={label}
            value={String(form[field.key])}
            error={error}
            inputMode={field.kind === "year" ? "numeric" : undefined}
            dir={field.kind === "date" || field.kind === "year" ? "ltr" : undefined}
            onChange={(e) => set(field.key, e.target.value)}
          />
        );
      })}
      <div className="cp-row">
        <Button type="submit" variant="primary" disabled={saving}>
          {t("action.save")}
        </Button>
        <Button onClick={onCancel} disabled={saving}>
          {t("action.cancel")}
        </Button>
      </div>
    </form>
  );
}
