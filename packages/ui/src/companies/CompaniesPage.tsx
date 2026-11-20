import { ApiError } from "@careerpilot/api-client";
import { useCompanies, useCompanyMutations } from "@careerpilot/api-client/react";
import type { Company, CompanyPriority, Connector, ConnectorType } from "@careerpilot/contracts";
import { isMessageKey, type MessageKey, type Translate } from "@careerpilot/i18n";
import {
  companyFormSchema,
  connectorSlugSchema,
  type CompanyForm as CompanyFormValues,
} from "@careerpilot/validation";
import { zodResolver } from "@hookform/resolvers/zod";
import { useId, useState, type FormEvent } from "react";
import { useForm } from "react-hook-form";
import {
  Alert,
  Badge,
  Button,
  Card,
  PageHeader,
  Spinner,
  TextField,
  type Tone,
} from "../components";
import { useI18n, useOpenExternal } from "../providers";

const PRIORITIES: readonly CompanyPriority[] = ["high", "medium", "low"];
const PRIORITY_TONE: Record<CompanyPriority, Tone> = {
  high: "success",
  medium: "neutral",
  low: "neutral",
};
const CONNECTOR_TYPES: readonly ConnectorType[] = ["careers_page", "greenhouse", "lever", "ashby"];
/** The single config field of each connector type. */
const CONNECTOR_FIELD: Record<ConnectorType, string> = {
  careers_page: "url",
  greenhouse: "board_token",
  lever: "company",
  ashby: "organization",
};

const SERVER_ERRORS = new Set([
  "https_required",
  "credentials_in_url",
  "non_default_port",
  "ip_address_not_allowed",
  "private_host",
  "invalid_url",
  "company_exists",
  "company_limit",
  "version_conflict",
  "connector_exists",
  "connector_limit",
]);

/** A server rejection as a message; URL problems are attached to their field. */
function explain(err: unknown, t: Translate): string {
  if (err instanceof ApiError && err.code && SERVER_ERRORS.has(err.code)) {
    return t(`company.error.${err.code}` as MessageKey);
  }
  return t("company.error.generic");
}

function connectorLabel(connector: Pick<Connector, "connector_type" | "config">): string {
  return connector.config[CONNECTOR_FIELD[connector.connector_type]] ?? "";
}

// ------------------------------------------------------------- company form

function CompanyForm({
  initial,
  onSaved,
  onCancel,
}: {
  initial?: Company;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const { create, update } = useCompanyMutations();
  const [failure, setFailure] = useState<string | null>(null);
  const form = useForm<CompanyFormValues>({
    resolver: zodResolver(companyFormSchema),
    defaultValues: {
      name: initial?.name ?? "",
      careers_url: initial?.careers_url ?? "",
      region: initial?.region ?? "",
      priority: initial?.priority ?? "medium",
      notes: initial?.notes ?? "",
    },
  });
  const { errors, isSubmitting } = form.formState;
  const fieldError = (message?: string) =>
    message ? t(isMessageKey(message) ? message : "company.error.generic") : undefined;

  const onSubmit = form.handleSubmit(async (values) => {
    setFailure(null);
    const input = {
      name: values.name,
      careers_url: values.careers_url || null,
      region: values.region || null,
      priority: values.priority,
      notes: values.notes || null,
    };
    try {
      if (initial)
        await update.mutateAsync({
          id: initial.id,
          change: { ...input, version: initial.version },
        });
      else await create.mutateAsync(input);
      onSaved();
    } catch (err) {
      const code = err instanceof ApiError ? err.code : null;
      if (
        code &&
        code !== "version_conflict" &&
        SERVER_ERRORS.has(code) &&
        !code.startsWith("company_")
      ) {
        form.setError("careers_url", { message: `company.error.${code}` });
      } else if (code === "company_exists") {
        form.setError("name", { message: "company.error.company_exists" });
      } else {
        setFailure(explain(err, t));
      }
    }
  });

  return (
    <form className="cp-company-form" onSubmit={onSubmit} noValidate data-testid="company-form">
      {failure ? <Alert data-testid="company-form-error">{failure}</Alert> : null}
      <TextField
        label={t("company.field.name")}
        error={fieldError(errors.name?.message)}
        {...form.register("name")}
      />
      <TextField
        label={t("company.field.careers_url")}
        dir="ltr"
        inputMode="url"
        placeholder="https://"
        error={fieldError(errors.careers_url?.message)}
        {...form.register("careers_url")}
      />
      <div className="cp-row cp-form-row">
        <TextField label={t("company.field.region")} {...form.register("region")} />
        <label className="cp-field">
          <span className="cp-field-label">{t("company.field.priority")}</span>
          <select className="cp-input" {...form.register("priority")}>
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {t(`company.priority.${p}` as MessageKey)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="cp-field">
        <span className="cp-field-label">{t("company.field.notes")}</span>
        <textarea className="cp-input" rows={2} {...form.register("notes")} />
      </label>
      <div className="cp-row">
        <Button type="submit" variant="primary" disabled={isSubmitting}>
          {t("action.save")}
        </Button>
        <Button onClick={onCancel} disabled={isSubmitting}>
          {t("action.cancel")}
        </Button>
      </div>
    </form>
  );
}

// --------------------------------------------------------------- connectors

function AddConnectorForm({ company, onDone }: { company: Company; onDone: () => void }) {
  const { t } = useI18n();
  const { addConnector } = useCompanyMutations();
  const [type, setType] = useState<ConnectorType>("careers_page");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const typeId = useId();

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = value.trim();
    if (type !== "careers_page" && !connectorSlugSchema.safeParse(trimmed).success) {
      setError(t("company.error.slug"));
      return;
    }
    if (!trimmed) {
      setError(t("company.error.invalid_url"));
      return;
    }
    try {
      await addConnector.mutateAsync({
        companyId: company.id,
        connectorType: type,
        config: { [CONNECTOR_FIELD[type]]: trimmed },
      });
      onDone();
    } catch (err) {
      setError(explain(err, t));
    }
  }

  return (
    <form className="cp-connector-form" onSubmit={submit} noValidate data-testid="connector-form">
      <label className="cp-field" htmlFor={typeId}>
        <span className="cp-field-label">{t("connector.type")}</span>
        <select
          id={typeId}
          className="cp-input"
          value={type}
          onChange={(e) => {
            setType(e.target.value as ConnectorType);
            setError(null);
          }}
        >
          {CONNECTOR_TYPES.map((c) => (
            <option key={c} value={c}>
              {t(`connector.type.${c}` as MessageKey)}
            </option>
          ))}
        </select>
      </label>
      <TextField
        label={t(`connector.field.${CONNECTOR_FIELD[type]}` as MessageKey)}
        dir="ltr"
        value={value}
        error={error ?? undefined}
        onChange={(e) => setValue(e.target.value)}
      />
      <div className="cp-row">
        <Button type="submit" variant="primary" disabled={addConnector.isPending}>
          {t("action.add")}
        </Button>
        <Button onClick={onDone}>{t("action.cancel")}</Button>
      </div>
    </form>
  );
}

function Connectors({ company }: { company: Company }) {
  const { t } = useI18n();
  const { addConnector, toggleConnector, removeConnector } = useCompanyMutations();
  const [adding, setAdding] = useState(false);
  const detected = company.detected_connector;

  return (
    <div className="cp-connectors" data-testid="connectors">
      <div className="cp-row cp-row-between">
        <h3 className="cp-subtitle">{t("connectors.title")}</h3>
        {!adding ? (
          <Button
            aria-label={`${t("connectors.add")}: ${company.name}`}
            onClick={() => setAdding(true)}
          >
            {t("connectors.add")}
          </Button>
        ) : null}
      </div>
      {detected ? (
        <Alert tone="neutral" data-testid="detected-connector">
          {t("connector.detected", {
            type: t(`connector.type.${detected.connector_type}` as MessageKey),
            id: connectorLabel(detected),
          })}{" "}
          <Button
            variant="primary"
            disabled={addConnector.isPending}
            onClick={() =>
              addConnector.mutate({
                companyId: company.id,
                connectorType: detected.connector_type,
                config: detected.config,
              })
            }
          >
            {t("connector.addDetected")}
          </Button>
        </Alert>
      ) : null}
      {adding ? <AddConnectorForm company={company} onDone={() => setAdding(false)} /> : null}
      {company.connectors.length === 0 ? (
        <p className="cp-doc-meta">{t("connectors.none")}</p>
      ) : (
        <ul className="cp-connector-list">
          {company.connectors.map((connector) => (
            <li key={connector.id} className="cp-connector" data-testid="connector-row">
              <span className="cp-connector-type">
                {t(`connector.type.${connector.connector_type}` as MessageKey)}
              </span>
              <span className="cp-mono cp-connector-id">{connectorLabel(connector)}</span>
              <Badge tone={connector.enabled ? "success" : "neutral"}>
                {t(connector.enabled ? "connector.enabled" : "connector.paused")}
              </Badge>
              <Button
                disabled={toggleConnector.isPending}
                onClick={() =>
                  toggleConnector.mutate({
                    companyId: company.id,
                    connectorId: connector.id,
                    enabled: !connector.enabled,
                  })
                }
              >
                {t(connector.enabled ? "connector.pause" : "connector.resume")}
              </Button>
              <Button
                aria-label={`${t("connector.remove")}: ${connectorLabel(connector)}`}
                disabled={removeConnector.isPending}
                onClick={() =>
                  removeConnector.mutate({ companyId: company.id, connectorId: connector.id })
                }
              >
                {t("connector.remove")}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <p className="cp-doc-meta">{t("connectors.hint")}</p>
    </div>
  );
}

// ------------------------------------------------------------------ cards

function CompanyCard({ company }: { company: Company }) {
  const { t } = useI18n();
  const openExternal = useOpenExternal();
  const { remove } = useCompanyMutations();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const headingId = useId();

  return (
    <Card aria-labelledby={headingId}>
      <article className="cp-company" data-testid="company-card">
        {editing ? (
          <CompanyForm
            initial={company}
            onSaved={() => setEditing(false)}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <>
            <div className="cp-row cp-row-between">
              <div className="cp-company-head">
                <h2 id={headingId} className="cp-section-title">
                  {company.name}
                </h2>
                <div className="cp-row">
                  <Badge tone={PRIORITY_TONE[company.priority]}>
                    {t(`company.priority.${company.priority}` as MessageKey)}
                  </Badge>
                  {company.region ? <span className="cp-doc-meta">{company.region}</span> : null}
                </div>
              </div>
              <div className="cp-row">
                {company.careers_url ? (
                  <Button onClick={() => openExternal(company.careers_url!)}>
                    {t("company.openCareers")}
                  </Button>
                ) : null}
                <Button
                  aria-label={`${t("action.edit")}: ${company.name}`}
                  onClick={() => setEditing(true)}
                >
                  {t("action.edit")}
                </Button>
                <Button
                  aria-label={`${t("action.delete")}: ${company.name}`}
                  onClick={() => setConfirming(true)}
                >
                  {t("action.delete")}
                </Button>
              </div>
            </div>
            {company.careers_url ? (
              <p className="cp-mono cp-company-url">{company.careers_url}</p>
            ) : null}
            {company.notes ? <p className="cp-company-notes">{company.notes}</p> : null}
            {confirming ? (
              <div className="cp-confirm" role="group">
                <span>{t("company.deleteConfirm", { name: company.name })}</span>
                <Button
                  variant="primary"
                  disabled={remove.isPending}
                  onClick={() =>
                    remove.mutate(company.id, { onSettled: () => setConfirming(false) })
                  }
                >
                  {t("action.confirmDelete")}
                </Button>
                <Button onClick={() => setConfirming(false)}>{t("action.cancel")}</Button>
              </div>
            ) : null}
          </>
        )}
        <Connectors company={company} />
      </article>
    </Card>
  );
}

// ------------------------------------------------------------------- page

/** Target companies and their job sources (Phase 4). */
export function CompaniesPage() {
  const { t } = useI18n();
  const companies = useCompanies();
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");

  const all = companies.data?.companies ?? [];
  const needle = query.trim().toLocaleLowerCase();
  const shown = needle ? all.filter((c) => c.name.toLocaleLowerCase().includes(needle)) : all;

  return (
    <>
      <PageHeader title={t("nav.companies")} subtitle={t("companies.subtitle")} />
      <div className="cp-stack">
        <div className="cp-row cp-row-between">
          {all.length > 0 ? (
            <input
              type="search"
              className="cp-input cp-search"
              placeholder={t("companies.search")}
              aria-label={t("companies.search")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          ) : (
            <span />
          )}
          {!adding ? (
            <Button variant="primary" onClick={() => setAdding(true)}>
              {t("companies.add")}
            </Button>
          ) : null}
        </div>
        {adding ? (
          <Card>
            <CompanyForm onSaved={() => setAdding(false)} onCancel={() => setAdding(false)} />
          </Card>
        ) : null}
        {companies.isPending ? <Spinner label={t("common.loading")} /> : null}
        {companies.isError ? <Alert>{t("companies.loadError")}</Alert> : null}
        {companies.data && all.length === 0 && !adding ? (
          <p className="cp-doc-meta" data-testid="companies-empty">
            {t("companies.empty")}
          </p>
        ) : null}
        {needle && shown.length === 0 ? (
          <p className="cp-doc-meta">{t("companies.noMatch")}</p>
        ) : null}
        {shown.map((company) => (
          <CompanyCard key={company.id} company={company} />
        ))}
      </div>
    </>
  );
}
