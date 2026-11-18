import { AppShell } from "./AppShell";
import { PageHeader } from "./components";
import { useI18n } from "./providers";
import { SystemStatusPanel } from "./SystemStatusPanel";

/** The complete Phase 1 screen, rendered identically by Desktop and Web. */
export function StatusPage() {
  const { t } = useI18n();
  return (
    <AppShell>
      <PageHeader title={t("nav.systemStatus")} subtitle={t("status.subtitle")} />
      <SystemStatusPanel />
    </AppShell>
  );
}
