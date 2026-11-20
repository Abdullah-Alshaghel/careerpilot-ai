/**
 * API contract types shared by the Desktop and Web clients.
 * Source of truth: shared/api-contracts/schemas/*.schema.json
 * Runtime validation lives in @careerpilot/validation.
 */

export type HealthCheckStatus = "up" | "down";
export type HealthStatus = "ok" | "degraded" | "down";

export interface HealthCheckResult {
  name: string;
  status: HealthCheckStatus;
  latency_ms: number;
  detail?: string | null;
}

/** GET /v1/health */
export interface HealthResponse {
  service: string;
  version: string;
  status: HealthStatus;
  /** ISO-8601 UTC timestamp */
  timestamp: string;
  checks: HealthCheckResult[];
}

/** Uniform error body returned by every backend service. */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    correlation_id: string | null;
    fields?: { loc: string[]; msg: string }[];
  };
}

export const API_VERSION = "v1" as const;

// ---------------------------------------------------------------- identity

export type Locale = "en" | "ar";
export type UserStatus = "active" | "disabled";

/** GET /v1/users/me (shared/api-contracts/schemas/user.v1.schema.json) */
export interface User {
  id: string;
  email: string;
  locale: Locale;
  status: UserStatus;
  /** ISO-8601 UTC timestamp */
  created_at: string;
}

/** POST /v1/auth/register | /v1/auth/login | /v1/auth/refresh (auth-token.v1) */
export interface TokenResponse {
  access_token: string;
  token_type: "Bearer";
  /** Access-token lifetime, seconds */
  expires_in: number;
  /** Opaque, single-use; rotated on every refresh. Never stored by the Web client. */
  refresh_token: string;
  /** Seconds until the session ends (absolute; refresh does not extend it) */
  refresh_expires_in: number;
  user: User;
}

/** What a client keeps in memory: the token response minus the refresh token (auth-session.v1). */
export interface BrowserSession {
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  user: User;
}

export interface RegisterRequest {
  email: string;
  password: string;
  locale?: Locale;
}

export interface LoginRequest {
  email: string;
  password: string;
}

/** Error codes the identity endpoints and the gateway's auth layer can return. */
export type AuthErrorCode =
  | "invalid_credentials"
  | "email_taken"
  | "account_disabled"
  | "invalid_refresh_token"
  | "refresh_token_superseded"
  | "refresh_token_reused"
  | "invalid_token"
  | "token_expired"
  | "unauthorized"
  | "rate_limited"
  | "auth_unavailable";

/** Password rules enforced by the Identity Service (clients mirror them for early feedback). */
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;

// --------------------------------------------------------------- documents

export type DocumentMimeType =
  | "application/pdf"
  | "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  | "text/plain";

/** uploaded = queued for extraction; processing; extracted; failed (see failure_reason) */
export type DocumentStatus = "uploaded" | "processing" | "extracted" | "failed";

/** document.v1 */
export interface CareerDocument {
  id: string;
  filename: string;
  mime_type: DocumentMimeType;
  size_bytes: number;
  status: DocumentStatus;
  /** e.g. encrypted_pdf, no_text, unreadable_file, timeout, too_complex, processing_failed */
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

/** document-list.v1 */
export interface DocumentList {
  documents: CareerDocument[];
}

/** Upload limits enforced by the Document Service (mirrored for early feedback). */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
export const ACCEPTED_UPLOAD_EXTENSIONS = [".pdf", ".docx", ".txt"] as const;

// ----------------------------------------------------------- career profile

export type FactType =
  "personal" | "summary" | "employment" | "education" | "skill" | "certification" | "language";

/** extracted = suggested by a parser and NOT yet a fact; the user approves or rejects it. */
export type VerificationStatus = "extracted" | "user_verified" | "rejected";

/** "YYYY" or "YYYY-MM" */
export type PartialDate = string;

export interface PersonalValue {
  field: "full_name" | "email" | "phone" | "location" | "link";
  value: string;
}
export interface SummaryValue {
  text: string;
}
export interface EmploymentValue {
  title: string;
  organization: string | null;
  start: PartialDate | null;
  end: PartialDate | null;
  current: boolean;
  description: string | null;
}
export interface EducationValue {
  degree: string | null;
  institution: string | null;
  start: PartialDate | null;
  end: PartialDate | null;
}
export interface SkillValue {
  name: string;
}
export interface CertificationValue {
  name: string;
  year: number | null;
}
export interface LanguageValue {
  name: string;
  level: string | null;
}

export interface FactValueByType {
  personal: PersonalValue;
  summary: SummaryValue;
  employment: EmploymentValue;
  education: EducationValue;
  skill: SkillValue;
  certification: CertificationValue;
  language: LanguageValue;
}

export interface FactSource {
  /** null when the source document was deleted (the excerpt is kept) */
  document_id: string | null;
  name: string | null;
  excerpt: string | null;
}

export interface CareerFact {
  id: string;
  fact_type: FactType;
  /** Shape depends on fact_type (FactValueByType); validated per type on write. */
  value: Record<string, unknown>;
  /** null = entered by the user */
  source: FactSource | null;
  /** 0..1, how sure the extraction rule was */
  confidence: number;
  verification_status: VerificationStatus;
  valid_from: string | null;
  valid_to: string | null;
  created_at: string;
  updated_at: string;
}

export interface FactConflict {
  kind: "multiple_values" | "different_dates" | "different_details";
  fact_type: FactType;
  fact_ids: string[];
}

/** GET /v1/profile (career-profile.v1) */
export interface CareerProfile {
  id: string;
  version: number;
  updated_at: string;
  facts: CareerFact[];
  conflicts: FactConflict[];
}

// --------------------------------------------------------------- companies

export type CompanyPriority = "high" | "medium" | "low";

/** Where Phase 5 will look for jobs. Public identifiers only, never credentials. */
export type ConnectorType = "careers_page" | "greenhouse" | "lever" | "ashby";

export interface ConnectorConfigByType {
  careers_page: { url: string };
  greenhouse: { board_token: string };
  lever: { company: string };
  ashby: { organization: string };
}

export interface Connector {
  id: string;
  connector_type: ConnectorType;
  config: Record<string, string>;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface DetectedConnector {
  connector_type: ConnectorType;
  config: Record<string, string>;
}

/** company.v1 */
export interface Company {
  id: string;
  name: string;
  /** https only, public host; normalized by the server */
  careers_url: string | null;
  region: string | null;
  priority: CompanyPriority;
  notes: string | null;
  /** Send back on update; a stale version gets 409 version_conflict. */
  version: number;
  connectors: Connector[];
  /** A public ATS board recognised from careers_url, not yet added as a connector. */
  detected_connector: DetectedConnector | null;
  created_at: string;
  updated_at: string;
}

/** company-list.v1 (sorted by priority, then name) */
export interface CompanyList {
  companies: Company[];
}

export interface CompanyInput {
  name: string;
  careers_url: string | null;
  region: string | null;
  priority: CompanyPriority;
  notes: string | null;
}

// ----------------------------------------------------------- job discovery

export type SearchRunStatus = "queued" | "running" | "completed" | "failed";
export type SearchSourceStatus = "pending" | "succeeded" | "failed";

export interface SearchRunSource {
  company_id: string;
  company_name: string;
  connector_type: string;
  status: SearchSourceStatus;
  /** not_found, timeout, unreachable, blocked_address, disallowed_by_robots, ... */
  error_code: string | null;
  jobs_found: number;
  jobs_new: number;
}

/** search-run.v1 */
export interface SearchRun {
  id: string;
  status: SearchRunStatus;
  jobs_found: number;
  jobs_new: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  sources: SearchRunSource[];
}

export interface SearchRunList {
  runs: SearchRun[];
}

export type ListingStatus = "active" | "no_longer_listed";

/** A job in a list (job-list.v1). */
export interface JobSummary {
  id: string;
  company_id: string | null;
  company_name: string;
  /** greenhouse | lever | ashby | careers_page | manual */
  source_type: string;
  title: string;
  /** Link to the original posting (https). */
  url: string | null;
  location: string | null;
  remote: boolean | null;
  employment_type: string | null;
  department: string | null;
  posted_at: string | null;
  first_seen_at: string;
  listing_status: ListingStatus;
  duplicate_of_id: string | null;
  /** First found by the most recent completed search. */
  is_new: boolean;
}

/** job.v1: description is plain text (never HTML). */
export interface Job extends JobSummary {
  description: string;
}

export interface JobList {
  jobs: JobSummary[];
  total: number;
}

export interface JobFilters {
  q?: string;
  company_id?: string;
  remote_only?: boolean;
  include_unlisted?: boolean;
  include_duplicates?: boolean;
  limit?: number;
  offset?: number;
}
