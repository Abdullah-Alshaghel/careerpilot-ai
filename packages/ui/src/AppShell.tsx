import type { MessageKey } from "@careerpilot/i18n";
import type { ReactNode } from "react";
import { Button } from "./components";
import { useAuth, useClientType, useI18n } from "./providers";

export interface NavItem<P extends string> {
  id: P;
  label: MessageKey;
}

/**
 * Workspace shell. Navigation grows phase by phase; only implemented
 * destinations are listed (no dead links).
 */
export function AppShell<P extends string>({
  nav,
  current,
  onNavigate,
  children,
}: {
  nav: readonly NavItem<P>[];
  current: P;
  onNavigate: (page: P) => void;
  children: ReactNode;
}) {
  const { t, locale, setLocale } = useI18n();
  const clientType = useClientType();
  const { state, logout } = useAuth();

  return (
    <div className="cp-shell">
      <aside className="cp-sidebar">
        <div className="cp-brand">
          <span className="cp-brand-name">{t("app.name")}</span>
          <span className="cp-brand-tagline">{t("app.tagline")}</span>
        </div>
        <nav aria-label="Primary">
          <ul className="cp-nav">
            {nav.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="cp-nav-item"
                  aria-current={item.id === current ? "page" : undefined}
                  data-testid={`nav-${item.id}`}
                  onClick={() => onNavigate(item.id)}
                >
                  {t(item.label)}
                </button>
              </li>
            ))}
          </ul>
        </nav>
        <div className="cp-sidebar-footer">
          {state.status === "authenticated" ? (
            <span className="cp-user" data-testid="signed-in-user">
              {t("auth.signedInAs")} <span className="cp-mono">{state.user.email}</span>
            </span>
          ) : null}
          <Button data-testid="sign-out" onClick={() => void logout()}>
            {t("auth.signOut")}
          </Button>
          <Button
            data-testid="locale-toggle"
            onClick={() => setLocale(locale === "en" ? "ar" : "en")}
            lang={locale === "en" ? "ar" : "en"}
          >
            {t("locale.switchTo")}
          </Button>
          <span data-testid="client-type">
            {t(clientType === "desktop" ? "client.desktop" : "client.web")}
          </span>
        </div>
      </aside>
      <main className="cp-main">{children}</main>
    </div>
  );
}
