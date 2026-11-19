import {
  forwardRef,
  useId,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";

export type Tone = "neutral" | "success" | "warning" | "danger";

export function Button({
  variant = "secondary",
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" }) {
  return <button className="cp-button" data-variant={variant} type={type} {...props} />;
}

export function Badge({
  tone = "neutral",
  children,
  ...rest
}: {
  tone?: Tone;
  children: ReactNode;
  "data-testid"?: string;
}) {
  return (
    <span className="cp-badge" data-tone={tone} {...rest}>
      {children}
    </span>
  );
}

export function Card({ children, ...rest }: { children: ReactNode; "aria-labelledby"?: string }) {
  return (
    <section className="cp-card" {...rest}>
      {children}
    </section>
  );
}

export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <header className="cp-page-header">
      <h1 className="cp-page-title">{title}</h1>
      {subtitle ? <p className="cp-page-subtitle">{subtitle}</p> : null}
    </header>
  );
}

export function Alert({
  tone = "danger",
  children,
  ...rest
}: {
  tone?: Tone;
  children: ReactNode;
  "data-testid"?: string;
}) {
  return (
    <div
      className="cp-alert"
      data-tone={tone}
      role={tone === "danger" ? "alert" : "status"}
      {...rest}
    >
      {children}
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="cp-spinner" role="status" aria-live="polite">
      <span className="cp-spinner-dot" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  error?: string;
  hint?: string;
  /** Adds a show/hide toggle for password fields. */
  revealLabels?: { show: string; hide: string };
};

/** Labelled input with hint and error text wired up for screen readers. */
export const TextField = forwardRef<HTMLInputElement, FieldProps>(function TextField(
  { label, error, hint, revealLabels, type = "text", id, ...input },
  ref,
) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const [revealed, setRevealed] = useState(false);
  const describedBy = [hint && `${inputId}-hint`, error && `${inputId}-error`]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="cp-field">
      <label className="cp-field-label" htmlFor={inputId}>
        {label}
      </label>
      <div className="cp-field-control">
        <input
          ref={ref}
          id={inputId}
          className="cp-input"
          type={revealLabels && revealed ? "text" : type}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy || undefined}
          {...input}
        />
        {revealLabels ? (
          <button
            type="button"
            className="cp-field-reveal"
            onClick={() => setRevealed((v) => !v)}
            aria-pressed={revealed}
            aria-controls={inputId}
          >
            {revealed ? revealLabels.hide : revealLabels.show}
          </button>
        ) : null}
      </div>
      {hint ? (
        <p className="cp-field-hint" id={`${inputId}-hint`}>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p className="cp-field-error" id={`${inputId}-error`}>
          {error}
        </p>
      ) : null}
    </div>
  );
});
