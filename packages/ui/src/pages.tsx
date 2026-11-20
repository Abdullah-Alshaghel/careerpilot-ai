import { useMe } from "@careerpilot/api-client/react";
import { formatDate } from "@careerpilot/i18n";
import { Alert, Badge, Card, PageHeader, Spinner } from "./components";
import { useI18n } from "./providers";
import { SystemStatusPanel } from "./SystemStatusPanel";

export function SystemStatusPage() {
  const { t } = useI18n();
  return (
    <>
      <PageHeader title={t("nav.systemStatus")} subtitle={t("status.subtitle")} />
      <SystemStatusPanel />
    </>
  );
}

/** The signed-in user's account, read from the backend (GET /v1/users/me). */
export function AccountPage() {
  const { t, locale } = useI18n();
  const me = useMe();

  return (
    <>
      <PageHeader title={t("nav.account")} subtitle={t("account.subtitle")} />
      <Card aria-labelledby="account-heading">
        <h2 id="account-heading" className="cp-visually-hidden">
          {t("nav.account")}
        </h2>
        {me.isPending ? <Spinner label={t("common.loading")} /> : null}
        {me.isError ? <Alert data-testid="account-error">{t("account.loadError")}</Alert> : null}
        {me.data ? (
          <dl className="cp-details" data-testid="account-details">
            <dt>{t("account.email")}</dt>
            <dd className="cp-mono" data-testid="account-email">
              {me.data.email}
            </dd>
            <dt>{t("account.status")}</dt>
            <dd>
              <Badge tone={me.data.status === "active" ? "success" : "danger"}>
                {t(
                  me.data.status === "active" ? "account.status.active" : "account.status.disabled",
                )}
              </Badge>
            </dd>
            <dt>{t("account.language")}</dt>
            <dd>{t(me.data.locale === "ar" ? "locale.name.ar" : "locale.name.en")}</dd>
            <dt>{t("account.memberSince")}</dt>
            <dd>{formatDate(me.data.created_at, locale)}</dd>
          </dl>
        ) : null}
      </Card>
    </>
  );
}
