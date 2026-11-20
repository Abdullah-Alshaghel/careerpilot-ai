import { useState } from "react";
import { AppShell, type NavItem } from "./AppShell";
import { AuthScreen } from "./AuthScreen";
import { Alert, Button, Spinner } from "./components";
import { AccountPage, SystemStatusPage } from "./pages";
import { CompaniesPage } from "./companies/CompaniesPage";
import { JobsPage } from "./jobs/JobsPage";
import { CareerProfilePage } from "./profile/CareerProfilePage";
import { useAuth, useI18n } from "./providers";

type Page = "profile" | "companies" | "jobs" | "status" | "account";

const NAV: readonly NavItem<Page>[] = [
  { id: "profile", label: "nav.profile" },
  { id: "companies", label: "nav.companies" },
  { id: "jobs", label: "nav.jobs" },
  { id: "account", label: "nav.account" },
  { id: "status", label: "nav.systemStatus" },
];

const PAGES: Record<Page, () => React.ReactNode> = {
  profile: () => <CareerProfilePage />,
  companies: () => <CompaniesPage />,
  jobs: () => <JobsPage />,
  account: () => <AccountPage />,
  status: () => <SystemStatusPage />,
};

function Workspace() {
  const [page, setPage] = useState<Page>("profile");
  return (
    <AppShell nav={NAV} current={page} onNavigate={setPage}>
      {PAGES[page]()}
    </AppShell>
  );
}

/** Shows the workspace only to a signed-in user; otherwise sign-in/registration. */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const { state, restore } = useAuth();

  switch (state.status) {
    case "restoring":
      return (
        <div className="cp-centered" data-testid="session-restoring">
          <Spinner label={t("auth.restoring")} />
        </div>
      );
    case "unreachable":
      return (
        <div className="cp-centered" data-testid="session-unreachable">
          <Alert>{t("auth.unreachable")}</Alert>
          <Button onClick={() => void restore()}>{t("status.retry")}</Button>
        </div>
      );
    case "anonymous":
      return <AuthScreen />;
    case "authenticated":
      return <>{children}</>;
  }
}

/** The complete application, rendered identically by Desktop and Web (ADR-009). */
export function CareerPilotApp() {
  return (
    <AuthGate>
      <Workspace />
    </AuthGate>
  );
}
