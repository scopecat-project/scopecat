import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { initialControlDrafts, type ControlDrafts } from "./ControlFields";
import { canRenderField, type FormField } from "./launch-fields";
import {
  findSubmittedProcedure,
  isKnownRejection,
  type SubmissionAttempt,
  type SubmissionRequest,
} from "./launch-submission";
import type { LaunchCatalogEntry, LaunchPreview } from "./launch-api";

export interface LaunchDraft {
  definition: string;
  experiment: string;
  values: Record<string, string>;
  controls: ControlDrafts;
  sample: string;
  actor: string;
  revision: number;
  preview?: LaunchPreview;
  requestKey?: string;
  pending: boolean;
  error: string;
  notice: string;
  admittedProcedureId?: string;
}
type DraftUpdate = (current: LaunchDraft) => LaunchDraft;
interface DraftContext {
  projectId: string | undefined;
  draft: LaunchDraft | undefined;
  select: (entry: LaunchCatalogEntry, reset?: boolean) => void;
  update: (change: DraftUpdate) => void;
  isCurrent: (revision: number) => boolean;
  configurationReady: boolean;
  configurationError: string;
  retryOriginalAllowed: boolean;
  attempt: SubmissionAttempt | undefined;
  submit: (request: SubmissionRequest, definition: string) => Promise<string | undefined>;
  checkSubmission: () => Promise<void>;
  refreshConfiguration: () => void;
}
const Context = createContext<DraftContext | null>(null);
export const definitionKey = (entry: LaunchCatalogEntry) => JSON.stringify(entry);

function initialDraft(entry: LaunchCatalogEntry, revision: number): LaunchDraft {
  return {
    experiment: entry.id,
    definition: definitionKey(entry),
    values: Object.fromEntries(
      Object.entries(entry.request.properties ?? {})
        .filter((pair): pair is [string, FormField] => canRenderField(pair[1]))
        .map(([name, field]) => [
          name,
          field.default == null
            ? ""
            : Array.isArray(field.default)
              ? field.default.join("\n")
              : typeof field.default === "string"
                ? field.default
                : JSON.stringify(field.default),
        ]),
    ),
    controls: initialControlDrafts(entry.controls),
    sample: "",
    actor: "operator",
    revision,
    pending: false,
    error: "",
    notice:
      "Editable inputs are kept for this project while this console is open. Preview before starting.",
  };
}

export function invalidateDraft(draft: LaunchDraft, notice: string): LaunchDraft {
  return {
    ...draft,
    revision: draft.revision + 1,
    preview: undefined,
    requestKey: undefined,
    pending: false,
    error: "",
    notice,
  };
}

export function LaunchDraftProvider({
  projectId,
  children,
}: {
  projectId: string | undefined;
  children: ReactNode;
}) {
  return (
    <ProjectDraft key={projectId} projectId={projectId}>
      {children}
    </ProjectDraft>
  );
}
function ProjectDraft({
  projectId,
  children,
}: {
  projectId: string | undefined;
  children: ReactNode;
}) {
  const [draft, setDraft] = useState<LaunchDraft>();
  const [attempt, setAttempt] = useState<SubmissionAttempt>();
  const queryClient = useQueryClient();
  const latest = useRef(draft);
  useEffect(() => {
    latest.current = draft;
  }, [draft]);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const source = draft?.preview?.config_source ?? attempt?.request.config_source;
  const configuration = useQuery({
    queryKey: [
      "config",
      "launch-context",
      projectId,
      source?.entry_id,
      source?.registry_generation,
    ],
    enabled: Boolean(projectId && (draft?.preview || attempt?.request.config_source)),
    queryFn: async ({ signal }) =>
      (
        await apiData(
          apiClient.GET("/api/v1/config-registry", {
            params: { query: { limit: 1 } },
            signal,
          }),
        )
      ).activation ?? null,
  });
  const matchesConfiguration =
    configuration.isSuccess &&
    (source == null ||
      (source.registry_generation === configuration.data?.generation &&
        source.entry_id === configuration.data?.entry_id));
  if (
    draft?.preview &&
    source &&
    configuration.isSuccess &&
    !configuration.isFetching &&
    !matchesConfiguration
  ) {
    setDraft(
      invalidateDraft(
        draft,
        "Configuration changed. Editable inputs are retained; preview again before starting.",
      ),
    );
  }
  const select = useCallback((entry: LaunchCatalogEntry, reset = false) => {
    setDraft((current) => {
      if (!reset && current?.definition === definitionKey(entry)) return current;
      const next = initialDraft(entry, (current?.revision ?? 0) + 1);
      if (!reset && current?.experiment === entry.id) {
        // Keep raw inputs for review; the new declaration and server validate them.
        next.values = Object.fromEntries(
          Object.entries(next.values).map(([name, value]) => [name, current.values[name] ?? value]),
        );
        next.sample = current.sample;
        next.actor = current.actor;
        next.notice =
          "Experiment definition changed. Check retained inputs and new control defaults, then preview again.";
      }
      return next;
    });
  }, []);
  async function submit(request: SubmissionRequest, definition: string) {
    const wasUnknown = attempt?.status === "unknown";
    setAttempt({ request, definition, status: "pending", error: "" });
    try {
      const receipt = await apiData(
        apiClient.POST("/api/v1/experiment-launcher/submit", { body: request }),
      );
      if (!alive.current) return;
      setAttempt({
        request,
        definition,
        status: "confirmed",
        procedureId: receipt.procedure_id,
        error: receipt.dispatch_error
          ? `Submitted; execution needs retry: ${receipt.dispatch_error}`
          : "",
      });
      return receipt.procedure_id;
    } catch (error) {
      if (alive.current)
        setAttempt({
          request,
          definition,
          status: isKnownRejection(error) && !wasUnknown ? "rejected" : "unknown",
          error: error instanceof Error ? error.message : String(error),
        });
    }
    return undefined;
  }
  async function checkSubmission() {
    if (!attempt) return;
    const selected = attempt;
    setAttempt({ ...selected, checking: true, error: "" });
    try {
      const id = await findSubmittedProcedure(selected.request);
      if (alive.current)
        setAttempt({
          ...selected,
          checking: false,
          status: "confirmed",
          procedureId: id,
          error: "",
        });
    } catch (error) {
      if (alive.current)
        setAttempt({
          ...selected,
          checking: false,
          error: error instanceof Error ? error.message : String(error),
        });
    }
  }
  return (
    <Context
      value={{
        projectId,
        draft,
        attempt,
        submit,
        checkSubmission,
        configurationError: configuration.error?.message ?? "",
        retryOriginalAllowed:
          attempt?.definition === draft?.definition &&
          configuration.isSuccess &&
          !configuration.isFetching &&
          configuration.data?.generation === attempt?.request.config_source?.registry_generation &&
          configuration.data?.entry_id === attempt?.request.config_source?.entry_id,
        refreshConfiguration: () => {
          void queryClient.invalidateQueries({ queryKey: ["config", "launch-context", projectId] });
        },
        select,
        update: (change) => setDraft((current) => (current ? change(current) : current)),
        isCurrent: (revision) => alive.current && latest.current?.revision === revision,
        configurationReady:
          matchesConfiguration && !configuration.isFetching && !configuration.isError,
      }}
    >
      {children}
    </Context>
  );
}
export function useLaunchDraft() {
  const context = useContext(Context);
  if (!context) throw new Error("Launch workspace requires its project draft provider");
  return context;
}
