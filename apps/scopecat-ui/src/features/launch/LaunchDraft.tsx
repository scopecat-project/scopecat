import { importLaunchHandoff } from "./launch-handoff";
import type { ComparisonHandoff } from "../analyses/RunComparison";
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
import type { ConfigContextResolution } from "../config/config-api";
import type { LaunchCatalogEntry, LaunchPreview } from "./launch-api";

export interface LaunchDraft {
  handoff?: ComparisonHandoff;
  definition: string;
  controlDefinition: string;
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
  selectedContext: ConfigContextResolution | undefined;
  selectContext: (resolution?: ConfigContextResolution) => void;
  draft: LaunchDraft | undefined;
  importHandoff: (entry: LaunchCatalogEntry, handoff: ComparisonHandoff) => void;
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

const controlDefinitionKey = (entry: LaunchCatalogEntry) =>
  JSON.stringify(
    entry.controls.map((control) => ({
      id: control.id,
      default: control.default,
      unit: control.unit,
      minimum: control.minimum,
      maximum: control.maximum,
      scannable: control.scannable,
      ownership: control.ownership,
    })),
  );

function initialDraft(entry: LaunchCatalogEntry, revision: number): LaunchDraft {
  return {
    experiment: entry.id,
    definition: definitionKey(entry),
    controlDefinition: controlDefinitionKey(entry),
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
  const [selectedContext, setSelectedContext] = useState<ConfigContextResolution>();
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
    queryKey: ["config", "launch-context", projectId, source],
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
  const matchesConfiguration = configuration.isSuccess && matchesActive(source, configuration.data);
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
        if (current.controlDefinition === next.controlDefinition) {
          next.controls = current.controls;
          next.notice =
            "Experiment revision changed. Inputs and control edits are retained; preview again.";
        } else {
          next.notice =
            "Control declarations changed. Check retained inputs and new control defaults, then preview again.";
        }
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
        selectedContext,
        selectContext: (resolved) => {
          if (!alive.current) return;
          setSelectedContext(resolved);
          setDraft((current) =>
            current
              ? invalidateDraft(
                  current,
                  "Parameter context changed. Preview again before starting.",
                )
              : current,
          );
        },
        draft,
        attempt,
        submit,
        checkSubmission,
        configurationError: configuration.error?.message ?? "",
        retryOriginalAllowed:
          attempt?.definition === draft?.definition &&
          configuration.isSuccess &&
          !configuration.isFetching &&
          matchesActive(attempt?.request.config_source, configuration.data),
        refreshConfiguration: () => {
          void queryClient.invalidateQueries({ queryKey: ["config", "launch-context", projectId] });
        },
        importHandoff: (entry, handoff) => {
          if (!alive.current) return;
          const current = latest.current;
          const next = initialDraft(entry, (current?.revision ?? 0) + 1);
          try {
            const imported = importLaunchHandoff(
              next,
              entry,
              handoff,
              selectedContext?.config_source,
            );
            if (!handoff.request.context) setSelectedContext(undefined);
            setDraft(imported);
          } catch (error) {
            setDraft({
              ...(current ?? next),
              error: error instanceof Error ? error.message : String(error),
            });
          }
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

function matchesActive(
  source: LaunchPreview["config_source"] | null | undefined,
  activation: { generation: number; entry_id: string } | null | undefined,
) {
  if (source == null) return true;
  return source.kind === "parameter_context"
    ? source.lab_generation === activation?.generation
    : source.registry_generation === activation?.generation &&
        source.entry_id === activation?.entry_id;
}
