import { ApiError } from "@careerpilot/api-client";
import {
  hasDocumentsInFlight,
  useDeleteDocument,
  useDocuments,
  useFactMutations,
  useProfile,
  useUploadDocument,
} from "@careerpilot/api-client/react";
import {
  ACCEPTED_UPLOAD_EXTENSIONS,
  MAX_UPLOAD_BYTES,
  type CareerDocument,
  type CareerFact,
  type FactConflict,
  type FactType,
} from "@careerpilot/contracts";
import type { MessageKey } from "@careerpilot/i18n";
import { useId, useRef, useState, type DragEvent } from "react";
import { Alert, Badge, Button, Card, PageHeader, Spinner, type Tone } from "../components";
import { useI18n } from "../providers";
import { FactEditor } from "./FactEditor";
import {
  SECTION_LABEL,
  SECTION_ORDER,
  confidenceBand,
  describeFact,
  failureMessage,
} from "./factFormat";

const UPLOAD_ERRORS = new Set([
  "file_too_large",
  "unsupported_type",
  "macro_document",
  "empty_file",
  "document_limit",
]);

function uploadErrorKey(err: unknown): MessageKey {
  if (err instanceof ApiError) {
    if (err.kind === "network" || err.kind === "timeout") return "upload.error.network";
    if (err.code && UPLOAD_ERRORS.has(err.code)) return `upload.error.${err.code}` as MessageKey;
  }
  return "upload.error.generic";
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ------------------------------------------------------------- documents

const DOC_TONE: Record<CareerDocument["status"], Tone> = {
  uploaded: "neutral",
  processing: "warning",
  extracted: "success",
  failed: "danger",
};

function DocumentRow({ document }: { document: CareerDocument }) {
  const { t } = useI18n();
  const remove = useDeleteDocument();
  const [confirming, setConfirming] = useState(false);

  return (
    <li className="cp-doc" data-testid="document-row" data-status={document.status}>
      <div className="cp-doc-main">
        <span className="cp-doc-name">{document.filename}</span>
        <span className="cp-doc-meta">{formatSize(document.size_bytes)}</span>
        {document.status === "failed" ? (
          <span className="cp-field-error">{t(failureMessage(document.failure_reason))}</span>
        ) : null}
      </div>
      <Badge tone={DOC_TONE[document.status]} data-testid="document-status">
        {t(`doc.status.${document.status}` as MessageKey)}
      </Badge>
      {confirming ? (
        <div className="cp-confirm" role="group">
          <span>{t("doc.deleteConfirm", { name: document.filename })}</span>
          <Button
            variant="primary"
            disabled={remove.isPending}
            onClick={() => remove.mutate(document.id, { onSettled: () => setConfirming(false) })}
          >
            {t("action.confirmDelete")}
          </Button>
          <Button onClick={() => setConfirming(false)}>{t("action.cancel")}</Button>
        </div>
      ) : (
        <Button
          aria-label={`${t("doc.delete")} ${document.filename}`}
          onClick={() => setConfirming(true)}
        >
          {t("doc.delete")}
        </Button>
      )}
    </li>
  );
}

function UploadCard({ documents }: { documents: CareerDocument[] }) {
  const { t } = useI18n();
  const upload = useUploadDocument();
  const inputRef = useRef<HTMLInputElement>(null);
  const inputId = useId();
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState<{ tone: Tone; text: string } | null>(null);

  async function send(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      if (file.size > MAX_UPLOAD_BYTES) {
        setMessage({ tone: "danger", text: t("upload.error.file_too_large") });
        continue;
      }
      setMessage(null);
      try {
        const known = new Set(documents.map((d) => d.id));
        const result = await upload.mutateAsync(file);
        if (known.has(result.id)) {
          setMessage({
            tone: "neutral",
            text: t("upload.alreadyUploaded", { name: result.filename }),
          });
        }
      } catch (err) {
        setMessage({ tone: "danger", text: t(uploadErrorKey(err)) });
      }
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length) void send(event.dataTransfer.files);
  }

  return (
    <Card aria-labelledby="documents-heading">
      <h2 id="documents-heading" className="cp-section-title">
        {t("upload.title")}
      </h2>
      <div
        className="cp-dropzone"
        data-dragging={dragging || undefined}
        data-testid="dropzone"
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        {upload.isPending ? (
          <Spinner label={t("upload.uploading", { name: upload.variables?.name ?? "" })} />
        ) : (
          <>
            <span>{t("upload.drop")}</span>
            <Button variant="primary" onClick={() => inputRef.current?.click()}>
              {t("upload.choose")}
            </Button>
            <span className="cp-doc-meta">{t("upload.hint")}</span>
          </>
        )}
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          className="cp-visually-hidden"
          data-testid="file-input"
          aria-label={t("upload.choose")}
          accept={[
            ...ACCEPTED_UPLOAD_EXTENSIONS,
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
          ].join(",")}
          multiple
          onChange={(e) => {
            if (e.target.files?.length) void send(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      {message ? (
        <Alert tone={message.tone} data-testid="upload-message">
          {message.text}
        </Alert>
      ) : null}
      {documents.length === 0 ? (
        <p className="cp-doc-meta">{t("doc.none")}</p>
      ) : (
        <ul className="cp-doc-list">
          {documents.map((document) => (
            <DocumentRow key={document.id} document={document} />
          ))}
        </ul>
      )}
    </Card>
  );
}

// ------------------------------------------------------------------ facts

const STATUS_TONE: Record<CareerFact["verification_status"], Tone> = {
  extracted: "warning",
  user_verified: "success",
  rejected: "neutral",
};

function FactRow({ fact, conflicted }: { fact: CareerFact; conflicted: boolean }) {
  const { t, locale } = useI18n();
  const { review, edit, remove } = useFactMutations();
  const [editing, setEditing] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const { primary, secondary } = describeFact(fact, t, locale);
  const busy = review.isPending || remove.isPending;

  if (editing) {
    return (
      <li className="cp-fact" data-testid="fact-row">
        <FactEditor
          factType={fact.fact_type}
          initial={fact.value}
          onCancel={() => setEditing(false)}
          onSave={async (value) => {
            await edit.mutateAsync({ id: fact.id, value });
            setEditing(false);
          }}
        />
      </li>
    );
  }

  return (
    <li
      className="cp-fact"
      data-testid="fact-row"
      data-status={fact.verification_status}
      data-conflict={conflicted || undefined}
    >
      <div className="cp-fact-main">
        <div className="cp-fact-text">
          <span className="cp-fact-primary">{primary}</span>
          {secondary ? <span className="cp-fact-secondary">{secondary}</span> : null}
        </div>
        <div className="cp-fact-meta">
          <Badge tone={STATUS_TONE[fact.verification_status]}>
            {t(`fact.status.${fact.verification_status}` as MessageKey)}
          </Badge>
          {fact.source ? (
            <>
              <span>{fact.source.name ? t("fact.from", { name: fact.source.name }) : null}</span>
              {fact.verification_status === "extracted" ? (
                <span data-band={confidenceBand(fact.confidence)} className="cp-confidence">
                  {t(`fact.confidence.${confidenceBand(fact.confidence)}` as MessageKey)}
                </span>
              ) : null}
              {fact.source.excerpt ? (
                <button
                  type="button"
                  className="cp-link"
                  aria-expanded={showSource}
                  onClick={() => setShowSource((v) => !v)}
                >
                  {t(showSource ? "fact.hideSource" : "fact.showSource")}
                </button>
              ) : null}
            </>
          ) : (
            <span>{t("fact.manual")}</span>
          )}
        </div>
        {showSource && fact.source?.excerpt ? (
          <blockquote className="cp-excerpt" data-testid="fact-excerpt">
            {fact.source.excerpt}
          </blockquote>
        ) : null}
      </div>
      <div className="cp-fact-actions">
        {fact.verification_status !== "user_verified" ? (
          <Button
            variant={fact.verification_status === "extracted" ? "primary" : "secondary"}
            disabled={busy}
            aria-label={`${t("action.approve")}: ${primary}`}
            onClick={() => review.mutate({ ids: [fact.id], status: "user_verified" })}
          >
            {t("action.approve")}
          </Button>
        ) : null}
        {fact.verification_status !== "rejected" ? (
          <Button
            disabled={busy}
            aria-label={`${t("action.reject")}: ${primary}`}
            onClick={() => review.mutate({ ids: [fact.id], status: "rejected" })}
          >
            {t("action.reject")}
          </Button>
        ) : null}
        <Button aria-label={`${t("action.edit")}: ${primary}`} onClick={() => setEditing(true)}>
          {t("action.edit")}
        </Button>
        {fact.source === null ? (
          <Button
            disabled={busy}
            aria-label={`${t("action.delete")}: ${primary}`}
            onClick={() => remove.mutate(fact.id)}
          >
            {t("action.delete")}
          </Button>
        ) : null}
      </div>
    </li>
  );
}

function FactSection({
  type,
  facts,
  conflictIds,
}: {
  type: FactType;
  facts: CareerFact[];
  conflictIds: Set<string>;
}) {
  const { t } = useI18n();
  const { add } = useFactMutations();
  const [adding, setAdding] = useState(false);
  const headingId = useId();

  return (
    <section
      className="cp-fact-section"
      aria-labelledby={headingId}
      data-testid={`section-${type}`}
    >
      <div className="cp-row cp-row-between">
        <h3 id={headingId} className="cp-section-title">
          {t(SECTION_LABEL[type])}
        </h3>
        {!adding ? (
          <Button
            aria-label={`${t("action.add")}: ${t(SECTION_LABEL[type])}`}
            onClick={() => setAdding(true)}
          >
            {t("action.add")}
          </Button>
        ) : null}
      </div>
      {adding ? (
        <FactEditor
          factType={type}
          initial={null}
          onCancel={() => setAdding(false)}
          onSave={async (value) => {
            await add.mutateAsync({ factType: type, value });
            setAdding(false);
          }}
        />
      ) : null}
      {facts.length ? (
        <ul className="cp-fact-list">
          {facts.map((fact) => (
            <FactRow key={fact.id} fact={fact} conflicted={conflictIds.has(fact.id)} />
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function ConflictBanner({ conflicts, facts }: { conflicts: FactConflict[]; facts: CareerFact[] }) {
  const { t, locale } = useI18n();
  const byId = new Map(facts.map((f) => [f.id, f]));
  return (
    <Alert tone="warning" data-testid="conflicts">
      <strong>{t("conflict.title")}</strong>
      <p className="cp-conflict-hint">{t("conflict.hint")}</p>
      <ul className="cp-conflict-list">
        {conflicts.map((conflict) => (
          <li key={conflict.fact_ids.join()}>
            {t(`conflict.${conflict.kind}` as MessageKey, {
              section: t(SECTION_LABEL[conflict.fact_type]),
            })}
            {": "}
            {conflict.fact_ids
              .map((id) => byId.get(id))
              .filter((f): f is CareerFact => Boolean(f))
              .map((f) => {
                const { primary, secondary } = describeFact(f, t, locale);
                return secondary && f.fact_type !== "personal"
                  ? `${primary} (${secondary})`
                  : primary;
              })
              .join(" / ")}
          </li>
        ))}
      </ul>
    </Alert>
  );
}

// ------------------------------------------------------------------ page

/** Upload a CV and review every suggested fact (Phase 3). */
export function CareerProfilePage() {
  const { t } = useI18n();
  const documents = useDocuments();
  const profile = useProfile(documents.data);
  const { review } = useFactMutations();
  const inFlight = hasDocumentsInFlight(documents.data);

  const facts = profile.data?.facts ?? [];
  const conflicts = profile.data?.conflicts ?? [];
  const conflictIds = new Set(conflicts.flatMap((c) => c.fact_ids));
  const pending = facts.filter((f) => f.verification_status === "extracted");
  const counts = {
    pending: pending.length,
    approved: facts.filter((f) => f.verification_status === "user_verified").length,
    rejected: facts.filter((f) => f.verification_status === "rejected").length,
  };

  return (
    <>
      <PageHeader title={t("nav.profile")} subtitle={t("profile.subtitle")} />
      <div className="cp-stack">
        <UploadCard documents={documents.data?.documents ?? []} />

        <Card aria-labelledby="review-heading">
          <div className="cp-row cp-row-between">
            <div>
              <h2 id="review-heading" className="cp-section-title">
                {t("review.title")}
              </h2>
              {facts.length ? (
                <p className="cp-doc-meta" data-testid="review-counts">
                  {t("review.counts", counts)}
                </p>
              ) : null}
            </div>
            {pending.length > 1 ? (
              <Button
                variant="primary"
                disabled={review.isPending}
                onClick={() =>
                  review.mutate({ ids: pending.map((f) => f.id), status: "user_verified" })
                }
              >
                {t("review.approveAll")}
              </Button>
            ) : null}
          </div>

          {profile.isPending ? <Spinner label={t("common.loading")} /> : null}
          {profile.isError ? <Alert>{t("profile.loadError")}</Alert> : null}
          {conflicts.length ? <ConflictBanner conflicts={conflicts} facts={facts} /> : null}
          {profile.data && facts.length === 0 ? (
            <p className="cp-doc-meta" data-testid="review-empty">
              {inFlight ? t("review.waiting") : t("review.empty")}
            </p>
          ) : null}

          {profile.data
            ? SECTION_ORDER.map((type) => (
                <FactSection
                  key={type}
                  type={type}
                  facts={facts.filter((f) => f.fact_type === type)}
                  conflictIds={conflictIds}
                />
              ))
            : null}
        </Card>
      </div>
    </>
  );
}
