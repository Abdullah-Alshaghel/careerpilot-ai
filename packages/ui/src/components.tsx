import type { ButtonHTMLAttributes, ReactNode } from "react";

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
