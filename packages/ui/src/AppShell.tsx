import type { ReactNode } from "react";
import { Button } from "./components";
import { useClientType, useI18n } from "./providers";

/**
 * Workspace shell. Navigation grows phase by phase; only implemented
 * destinations are listed (no dead links).
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { t, locale, setLocale } = useI18n();
  const clientType = useClientType();

  return (
    <div className="cp-shell">
      <aside className="cp-sidebar">
        <div className="cp-brand">
          <span className="cp-brand-name">{t("app.name")}</span>
          <span className="cp-brand-tagline">{t("app.tagline")}</span>
        </div>
        <nav aria-label="Primary">
          <ul className="cp-nav">
            <li>
              <a className="cp-nav-item" aria-current="page" href="#status">
                {t("nav.systemStatus")}
              </a>
            </li>
          </ul>
        </nav>
        <div className="cp-sidebar-footer">
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
      <main className="cp-main" id="status">
        {children}
      </main>
    </div>
  );
}
