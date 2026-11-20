import { ApiError } from "@careerpilot/api-client";
import {
  isSearchActive,
  useDiscoveryMutations,
  useJob,
  useJobs,
  useSearches,
} from "@careerpilot/api-client/react";
import type { JobFilters, JobSummary, SearchRun } from "@careerpilot/contracts";
import { formatDate, isMessageKey, type Translate } from "@careerpilot/i18n";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
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

function fetchMessage(code: string | null | undefined, t: Translate): string {
  const key = `fetch.${code}`;
  return isMessageKey(key) ? t(key) : t("fetch.generic");
}

function sourceName(type: string, t: Translate): string {
  const key = `source.${type}`;
  return isMessageKey(key) ? t(key) : type;
}

// ----------------------------------------------------------------- search

function SearchPanel() {
  const { t } = useI18n();
  const searches = useSearches();
  const { startSearch } = useDiscoveryMutations();
  const latest: SearchRun | undefined = searches.data?.runs[0];
  const active = isSearchActive(searches.data);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setError(null);
    try {
      await startSearch.mutateAsync();
    } catch (err) {
      if (err instanceof ApiError && err.code === "no_sources") setError(t("jobs.noSources"));
      else if (err instanceof ApiError && err.code === "search_too_soon")
        setError(t("jobs.tooSoon", { seconds: err.retryAfter ?? 60 }));
      else if (err instanceof ApiError && err.code === "search_in_progress")
        setError(t("jobs.inProgress"));
      else setError(t("jobs.searchError"));
    }
  }

  return (
    <Card aria-labelledby="search-heading">
      <div className="cp-row cp-row-between">
        <h2 id="search-heading" className="cp-section-title">
          {t("jobs.search")}
        </h2>
        <Button
          variant="primary"
          disabled={active || startSearch.isPending}
          onClick={() => void start()}
        >
          {t("jobs.search")}
        </Button>
      </div>
      {error ? <Alert data-testid="search-error">{error}</Alert> : null}
      {latest ? (
        <div className="cp-search-status" data-testid="search-status" data-status={latest.status}>
          {active ? (
            <Spinner label={t("jobs.searching", { count: latest.sources.length })} />
          ) : latest.status === "failed" ? (
            <p className="cp-field-error">{t("jobs.searchFailed")}</p>
          ) : (
            <p className="cp-doc-meta">
              {t("jobs.lastSearch", { found: latest.jobs_found, new: latest.jobs_new })}
            </p>
          )}
          <ul className="cp-source-list">
            {latest.sources.map((source, i) => (
              <li
                key={i}
                className="cp-source"
                data-testid="search-source"
                data-status={source.status}
              >
                <span className="cp-source-name">
                  {source.company_name} · {sourceName(source.connector_type, t)}
                </span>
                {source.status === "succeeded" ? (
                  <Badge tone="success">{t("jobs.sourceOk", { count: source.jobs_found })}</Badge>
                ) : source.status === "failed" ? (
                  <span className="cp-field-error">{fetchMessage(source.error_code, t)}</span>
                ) : (
                  <Badge>…</Badge>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Card>
  );
}

// ----------------------------------------------------------------- import

function ImportForm({ onImported }: { onImported: (id: string) => void }) {
  const { t } = useI18n();
  const { importJob } = useDiscoveryMutations();
  const [url, setUrl] = useState("");
  const [message, setMessage] = useState<{ tone: Tone; text: string } | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setMessage(null);
    try {
      const job = await importJob.mutateAsync(url.trim());
      setUrl("");
      setMessage({ tone: "success", text: t("import.done", { title: job.title }) });
      onImported(job.id);
    } catch (err) {
      setMessage({
        tone: "danger",
        text: fetchMessage(err instanceof ApiError ? err.code : null, t),
      });
    }
  }

  return (
    <Card aria-labelledby="import-heading">
      <form className="cp-import" onSubmit={submit} noValidate data-testid="import-form">
        <h2 id="import-heading" className="cp-section-title">
          {t("import.title")}
        </h2>
        <div className="cp-row">
          <TextField
            label={t("import.field")}
            dir="ltr"
            inputMode="url"
            placeholder="https://"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <Button type="submit" disabled={!url.trim() || importJob.isPending}>
            {importJob.isPending ? t("auth.submitting") : t("import.submit")}
          </Button>
        </div>
        {message ? (
          <Alert tone={message.tone} data-testid="import-message">
            {message.text}
          </Alert>
        ) : null}
      </form>
    </Card>
  );
}

// ------------------------------------------------------------------- list

function JobRow({
  job,
  selected,
  onSelect,
}: {
  job: JobSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const { t } = useI18n();
  return (
    <li>
      <button
        type="button"
        className="cp-job-row"
        aria-current={selected ? "true" : undefined}
        data-testid="job-row"
        onClick={onSelect}
      >
        <span className="cp-job-title">{job.title}</span>
        <span className="cp-job-meta">
          {job.company_name}
          {job.location ? ` · ${job.location}` : ""}
        </span>
        <span className="cp-row">
          {job.is_new ? <Badge tone="success">{t("job.new")}</Badge> : null}
          {job.remote ? <Badge>{t("job.remote")}</Badge> : null}
          {job.listing_status === "no_longer_listed" ? (
            <Badge tone="warning">{t("job.noLongerListed")}</Badge>
          ) : null}
        </span>
      </button>
    </li>
  );
}

function JobDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { t, locale } = useI18n();
  const job = useJob(id);
  const openExternal = useOpenExternal();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const headingId = useId();

  useEffect(() => headingRef.current?.focus(), [job.data?.id]);

  return (
    <Card aria-labelledby={headingId}>
      <article className="cp-job-detail" data-testid="job-detail">
        {job.isPending ? <Spinner label={t("common.loading")} /> : null}
        {job.data ? (
          <>
            <div className="cp-row cp-row-between">
              <h2 id={headingId} ref={headingRef} tabIndex={-1} className="cp-page-title">
                {job.data.title}
              </h2>
              <Button onClick={onClose}>{t("job.close")}</Button>
            </div>
            <p className="cp-job-meta">
              {job.data.company_name}
              {job.data.location ? ` · ${job.data.location}` : ""}
              {" · "}
              {t("job.via", { source: sourceName(job.data.source_type, t) })}
              {job.data.posted_at
                ? ` · ${t("job.posted", { date: formatDate(job.data.posted_at, locale) })}`
                : ""}
            </p>
            <div className="cp-row">
              {job.data.remote ? <Badge>{t("job.remote")}</Badge> : null}
              {job.data.employment_type ? <Badge>{job.data.employment_type}</Badge> : null}
              {job.data.department ? <Badge>{job.data.department}</Badge> : null}
              {job.data.url ? (
                <Button variant="primary" onClick={() => openExternal(job.data!.url!)}>
                  {t("job.open")}
                </Button>
              ) : null}
            </div>
            <p className="cp-doc-meta">{t("job.untrusted")}</p>
            {/* Plain text only: third-party markup is never rendered. */}
            <div className="cp-job-description" data-testid="job-description">
              {job.data.description}
            </div>
          </>
        ) : null}
      </article>
    </Card>
  );
}

// ------------------------------------------------------------------- page

/** Discovered jobs and the searches that find them (Phase 5). */
export function JobsPage() {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [remoteOnly, setRemoteOnly] = useState(false);
  const [showUnlisted, setShowUnlisted] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const searches = useSearches();
  const filters: JobFilters = {
    q: query.trim() || undefined,
    remote_only: remoteOnly,
    include_unlisted: showUnlisted,
  };
  const jobs = useJobs(filters);
  const active = isSearchActive(searches.data);
  const refetchJobs = jobs.refetch;

  // New jobs appear when a running search finishes.
  const wasActive = useRef(active);
  useEffect(() => {
    if (wasActive.current && !active) void refetchJobs();
    wasActive.current = active;
  }, [active, refetchJobs]);

  const list = jobs.data?.jobs ?? [];
  const filtering = Boolean(filters.q || remoteOnly || showUnlisted);

  return (
    <>
      <PageHeader title={t("nav.jobs")} subtitle={t("jobs.subtitle")} />
      <div className="cp-stack">
        <SearchPanel />
        <ImportForm onImported={setSelected} />
        <div className="cp-row cp-jobs-filters">
          <input
            type="search"
            className="cp-input cp-search"
            placeholder={t("jobs.filter")}
            aria-label={t("jobs.filter")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <label className="cp-checkbox">
            <input
              type="checkbox"
              checked={remoteOnly}
              onChange={(e) => setRemoteOnly(e.target.checked)}
            />
            {t("jobs.remoteOnly")}
          </label>
          <label className="cp-checkbox">
            <input
              type="checkbox"
              checked={showUnlisted}
              onChange={(e) => setShowUnlisted(e.target.checked)}
            />
            {t("jobs.showUnlisted")}
          </label>
          {jobs.data ? (
            <span className="cp-doc-meta" data-testid="jobs-count">
              {t("jobs.count", { count: jobs.data.total })}
            </span>
          ) : null}
        </div>
        {jobs.isError ? <Alert>{t("fetch.generic")}</Alert> : null}
        {jobs.data && list.length === 0 ? (
          <p className="cp-doc-meta" data-testid="jobs-empty">
            {filtering ? t("jobs.noMatch") : t("jobs.empty")}
          </p>
        ) : null}
        <div className="cp-jobs-layout">
          {list.length ? (
            <ul className="cp-job-list" data-testid="job-list">
              {list.map((job) => (
                <JobRow
                  key={job.id}
                  job={job}
                  selected={job.id === selected}
                  onSelect={() => setSelected(job.id)}
                />
              ))}
            </ul>
          ) : null}
          {selected ? <JobDetail id={selected} onClose={() => setSelected(null)} /> : null}
        </div>
      </div>
    </>
  );
}
