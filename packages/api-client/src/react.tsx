import type {
  CareerDocument,
  CareerProfile,
  CompanyInput,
  ConnectorType,
  DocumentList,
  FactType,
  JobFilters,
  SearchRunList,
} from "@careerpilot/contracts";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import type { ApiClient } from "./index";

const ApiClientContext = createContext<ApiClient | null>(null);

export function ApiClientProvider({
  client,
  children,
}: {
  client: ApiClient;
  children: ReactNode;
}) {
  return <ApiClientContext.Provider value={client}>{children}</ApiClientContext.Provider>;
}

export function useApiClient(): ApiClient {
  const client = useContext(ApiClientContext);
  if (!client) throw new Error("useApiClient must be used inside <ApiClientProvider>");
  return client;
}

export const queryKeys = {
  health: ["platform", "health"] as const,
  // User-scoped data. The whole cache is cleared on sign-out, so one user's
  // data can never be shown to the next person who signs in on this device.
  me: ["users", "me"] as const,
  documents: ["users", "documents"] as const,
  profile: ["users", "profile"] as const,
  companies: ["users", "companies"] as const,
  searches: ["users", "searches"] as const,
  jobs: ["users", "jobs"] as const,
};

const IN_FLIGHT: ReadonlySet<CareerDocument["status"]> = new Set(["uploaded", "processing"]);
export const DOCUMENT_POLL_MS = 1_500;
/** After a document finishes, its suggested facts arrive a moment later via an event. */
export const AWAITING_POLL_MS = 2_500;

export function hasDocumentsInFlight(list: DocumentList | undefined): boolean {
  return Boolean(list?.documents.some((d) => IN_FLIGHT.has(d.status)));
}

export const HEALTH_REFRESH_MS = 15_000;

/** Platform health, polled while the window is visible. */
export function useHealth() {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => client.health(signal),
    refetchInterval: HEALTH_REFRESH_MS,
    refetchIntervalInBackground: false,
    retry: 1,
  });
}

/** The signed-in user's account (GET /v1/users/me). */
export function useMe() {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => client.users.me(signal),
  });
}

/** The signed-in user's documents; polls while any is still being read. */
export function useDocuments() {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.documents,
    queryFn: ({ signal }) => client.documents.list(signal),
    refetchInterval: (query) => (hasDocumentsInFlight(query.state.data) ? DOCUMENT_POLL_MS : false),
  });
}

/**
 * How the profile may still change because of documents:
 * - "reading": a document is still being read;
 * - "awaiting": a document finished after the profile last changed, so its
 *   suggestions haven't been processed yet;
 * - null: nothing pending.
 *
 * Only *server* timestamps are compared, never this device's clock (which may
 * be wrong). Processing a document always bumps the profile, even when it
 * yields no facts, so "awaiting" always ends.
 */
export function pendingProfileChange(
  documents: DocumentList | undefined,
  profile: CareerProfile | undefined,
): "reading" | "awaiting" | null {
  if (hasDocumentsInFlight(documents)) return "reading";
  if (!profile) return null;
  const profileUpdated = Date.parse(profile.updated_at);
  const awaiting = documents?.documents.some(
    (d) => d.status === "extracted" && Date.parse(d.updated_at) > profileUpdated,
  );
  return awaiting ? "awaiting" : null;
}

/** The career profile; refreshes while suggestions from documents are arriving. */
export function useProfile(documents?: DocumentList) {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.profile,
    queryFn: ({ signal }) => client.profile.get(signal),
    refetchInterval: (query) => {
      const pending = pendingProfileChange(documents, query.state.data);
      // While waiting on the event pipeline, poll more gently.
      return pending === "reading"
        ? DOCUMENT_POLL_MS
        : pending === "awaiting"
          ? AWAITING_POLL_MS
          : false;
    },
  });
}

function useInvalidateUserData() {
  const queries = useQueryClient();
  return () =>
    Promise.all([
      queries.invalidateQueries({ queryKey: queryKeys.documents }),
      queries.invalidateQueries({ queryKey: queryKeys.profile }),
    ]);
}

export function useUploadDocument() {
  const client = useApiClient();
  const invalidate = useInvalidateUserData();
  return useMutation({
    mutationFn: (file: File) => client.documents.upload(file, file.name),
    onSuccess: invalidate,
  });
}

export function useDeleteDocument() {
  const client = useApiClient();
  const invalidate = useInvalidateUserData();
  return useMutation({
    mutationFn: (id: string) => client.documents.remove(id),
    onSuccess: invalidate,
  });
}

export type ReviewStatus = "user_verified" | "rejected";

export function useFactMutations() {
  const client = useApiClient();
  const invalidate = useInvalidateUserData();
  return {
    review: useMutation({
      mutationFn: async ({ ids, status }: { ids: string[]; status: ReviewStatus }) => {
        if (ids.length === 1)
          await client.profile.updateFact(ids[0]!, { verification_status: status });
        else await client.profile.setStatus(ids, status);
      },
      onSuccess: invalidate,
    }),
    edit: useMutation({
      mutationFn: ({ id, value }: { id: string; value: Record<string, unknown> }) =>
        client.profile.updateFact(id, { value }),
      onSuccess: invalidate,
    }),
    add: useMutation({
      mutationFn: ({ factType, value }: { factType: FactType; value: Record<string, unknown> }) =>
        client.profile.addFact(factType, value),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: string) => client.profile.removeFact(id),
      onSuccess: invalidate,
    }),
  };
}

/** The signed-in user's target companies (priority, then name). */
export function useCompanies() {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.companies,
    queryFn: ({ signal }) => client.companies.list(signal),
  });
}

export function useCompanyMutations() {
  const client = useApiClient();
  const queries = useQueryClient();
  const invalidate = () => queries.invalidateQueries({ queryKey: queryKeys.companies });
  return {
    create: useMutation({
      mutationFn: (input: CompanyInput) => client.companies.create(input),
      onSuccess: invalidate,
    }),
    update: useMutation({
      mutationFn: ({
        id,
        change,
      }: {
        id: string;
        change: Partial<CompanyInput> & { version?: number };
      }) => client.companies.update(id, change),
      onSettled: invalidate,
    }),
    remove: useMutation({
      mutationFn: (id: string) => client.companies.remove(id),
      onSuccess: invalidate,
    }),
    addConnector: useMutation({
      mutationFn: ({
        companyId,
        connectorType,
        config,
      }: {
        companyId: string;
        connectorType: ConnectorType;
        config: Record<string, string>;
      }) => client.companies.addConnector(companyId, { connector_type: connectorType, config }),
      onSuccess: invalidate,
    }),
    toggleConnector: useMutation({
      mutationFn: ({
        companyId,
        connectorId,
        enabled,
      }: {
        companyId: string;
        connectorId: string;
        enabled: boolean;
      }) => client.companies.updateConnector(companyId, connectorId, { enabled }),
      onSuccess: invalidate,
    }),
    removeConnector: useMutation({
      mutationFn: ({ companyId, connectorId }: { companyId: string; connectorId: string }) =>
        client.companies.removeConnector(companyId, connectorId),
      onSuccess: invalidate,
    }),
  };
}

export const SEARCH_POLL_MS = 2_000;

export function isSearchActive(list: SearchRunList | undefined): boolean {
  const latest = list?.runs[0];
  return latest?.status === "queued" || latest?.status === "running";
}

/** Recent searches (newest first); polls while the latest one is still running. */
export function useSearches() {
  const client = useApiClient();
  return useQuery({
    queryKey: queryKeys.searches,
    queryFn: ({ signal }) => client.searches.list(signal),
    refetchInterval: (query) => (isSearchActive(query.state.data) ? SEARCH_POLL_MS : false),
  });
}

export function useJobs(filters: JobFilters) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...queryKeys.jobs, "list", filters],
    queryFn: ({ signal }) => client.jobs.list(filters, signal),
  });
}

export function useJob(id: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: [...queryKeys.jobs, "detail", id],
    queryFn: ({ signal }) => client.jobs.get(id!, signal),
    enabled: id !== null,
  });
}

export function useDiscoveryMutations() {
  const client = useApiClient();
  const queries = useQueryClient();
  return {
    startSearch: useMutation({
      mutationFn: () => client.searches.start(),
      onSettled: () => queries.invalidateQueries({ queryKey: queryKeys.searches }),
    }),
    importJob: useMutation({
      mutationFn: (url: string) => client.jobs.importFromUrl(url),
      onSuccess: () => queries.invalidateQueries({ queryKey: queryKeys.jobs }),
    }),
  };
}
