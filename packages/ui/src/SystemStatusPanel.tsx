import { ApiError } from "@careerpilot/api-client";
import { useApiClient, useHealth } from "@careerpilot/api-client/react";
import type { HealthStatus } from "@careerpilot/contracts";
import { formatTime, type MessageKey } from "@careerpilot/i18n";
import { Badge, Button, Card, type Tone } from "./components";
import { useI18n } from "./providers";

const STATUS_VIEW: Record<HealthStatus, { tone: Tone; key: MessageKey }> = {
  ok: { tone: "success", key: "status.ok" },
  degraded: { tone: "warning", key: "status.degraded" },
  down: { tone: "danger", key: "status.down" },
};

/** Live backend connectivity: the Phase 1 acceptance surface for both clients. */
export function SystemStatusPanel() {
  const { t, locale } = useI18n();
  const client = useApiClient();
  const health = useHealth();

  let badge: { tone: Tone; label: string; state: string };
  if (health.isPending) badge = { tone: "neutral", label: t("status.loading"), state: "loading" };
  else if (health.isError)
    badge = { tone: "danger", label: t("status.unreachable"), state: "unreachable" };
  else {
    const view = STATUS_VIEW[health.data.status];
    badge = { tone: view.tone, label: t(view.key), state: health.data.status };
  }

  const correlationId = health.error instanceof ApiError ? health.error.correlationId : null;

  return (
    <Card aria-labelledby="status-heading">
      <div className="cp-status-head">
        <h2 id="status-heading" className="cp-status-summary">
          {t("status.title")}
        </h2>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Badge tone={badge.tone} data-testid="health-status" data-state={badge.state}>
            {badge.label}
          </Badge>
          <Button onClick={() => void health.refetch()} disabled={health.isFetching}>
            {t("status.retry")}
          </Button>
        </div>
      </div>

      <div className="cp-status-meta">
        <span>
          {t("status.gateway")}: <span className="cp-mono">{client.baseUrl}</span>
        </span>
        {health.data ? (
          <>
            <span>
              {t("status.version")}: <span className="cp-mono">{health.data.version}</span>
            </span>
            <span>
              {t("status.lastChecked", {
                time: formatTime(new Date(health.dataUpdatedAt), locale),
              })}
            </span>
          </>
        ) : null}
      </div>

      {health.isError ? (
        <p className="cp-error" role="alert" data-testid="health-error">
          {t("status.unreachable")}
          {correlationId ? (
            <>
              {" · "}
              <span className="cp-mono">{t("status.correlationId", { id: correlationId })}</span>
            </>
          ) : null}
        </p>
      ) : null}

      {health.data ? (
        <ul className="cp-check-list" data-testid="health-checks">
          {health.data.checks.map((check) => (
            <li key={check.name} className="cp-check" data-testid={`check-${check.name}`}>
              <span className="cp-check-name">{check.name}</span>
              <span className="cp-check-right">
                <span className="cp-mono">
                  {t("status.latency", { ms: Math.round(check.latency_ms) })}
                </span>
                <Badge tone={check.status === "up" ? "success" : "danger"}>
                  {t(check.status === "up" ? "status.check.up" : "status.check.down")}
                </Badge>
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
