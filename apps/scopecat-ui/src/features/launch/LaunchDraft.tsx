import { readOperatorSettings } from "../application/OperatorSettings";
import {
  useLaunchRecovery,
  attemptHistory,
  attemptRequest,
  restoreDraft,
  rawInput,
  resolveAttempt,
  type DraftRecord,
  type AttemptRecord,
} from "./launch-recovery";
import type { WorkingInput } from "../config/parameter-draft-api";
import type { components } from "../../api-schema";
import {
  defaultSelection,
  normalizeSelection,
  type ScientificSelection,
} from "./scientific-selection";
import type { PlanRevision } from "./experiment-plans";
import { importLaunchRequest, importLaunchHandoff } from "./launch-handoff";
import type { ComparisonHandoff } from "../analyses/RunComparison";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type Dispatch,
  type SetStateAction,
} from "react";
import { apiClient, apiData, type LaunchRejection } from "../../api-client";
import { initialControlDrafts, type ControlDrafts } from "./ControlFields";
import { canRenderField, type FormField } from "./launch-fields";
import {
  isKnownRejection,
  type SubmissionAttempt,
  type SubmissionRequest,
} from "./launch-submission";
import type { LaunchCatalogEntry, LaunchPreview } from "./launch-api";

export type SavedLaunchTemplate =
  | PlanRevision
  | {
      name: string;
      definition: PlanRevision["definition"];
      manualLine: components["schemas"]["ManualLineSelection"];
    };

export interface LaunchDraft {
  draftId?: string;
  draftName?: string;
  manualLine?: components["schemas"]["ManualLineSelection"];
  lineSource?: PlanRevision["definition"]["source"];
  sourceBaseline?: PlanRevision["definition"]["code_revision"];
  needsReview?: boolean;
  unresolvedFields?: string[];
  workingInput?: WorkingInput;
  rejection?: LaunchRejection;
  handoff?: ComparisonHandoff;
  plan?: PlanRevision;
  planDirty?: boolean;
  selection: ScientificSelection;
  workspaceId: string;
  codeRevision?: PlanRevision["definition"]["code_revision"];
  definition: string;
  controlDefinition: string;
  experiment: string;
  values: Record<string, string>;
  controls: ControlDrafts;
  collection?: string;
  actor: string;
  defaultActor?: string;
  revision: number;
  preview?: LaunchPreview;
  requestKey?: string;
  pending: false | "preview" | "prepare" | "submit";
  error: string;
  notice: string;
  admittedProcedureId?: string;
}
type DraftUpdate = (current: LaunchDraft) => LaunchDraft;
export interface LaunchPreparation {
  isIntended: () => boolean;
  finish: () => void;
}
interface DraftContext {
  copyDraft: () => Promise<void>;
  switchDraft: (record: DraftRecord) => Promise<void>;
  renameDraft: (name: string) => void;
  beginPreparation: () => LaunchPreparation | undefined;
  recovery: ReturnType<typeof useLaunchRecovery>;
  recover: (record: DraftRecord) => void;
  recoverAttempt: (record: AttemptRecord) => void;
  attemptsReady: boolean;
  retryAttempts: () => void;
  sourceObserved: (workspace: string, source: LaunchDraft["sourceBaseline"]) => void;
  rerun: () => void;
  projectId: string | undefined;
  workspaceId: string;
  selectWorkspace: (workspaceId: string) => void;
  selectCurrentSource: (workspaceId?: string) => void;
  authorRefreshed: (workspaceId: string) => void;
  selectConfiguration: (
    choice: components["schemas"]["ConfigurationChoice-Input"],
    workingInput?: WorkingInput,
    subject?: ScientificSelection["subject"],
  ) => void;
  draft: LaunchDraft | undefined;
  openPlan: (
    plan: SavedLaunchTemplate,
    entry: LaunchCatalogEntry,
    isIntended?: () => boolean,
  ) => Promise<boolean>;
  importHandoff: (entry: LaunchCatalogEntry, handoff: ComparisonHandoff) => Promise<boolean>;
  select: (entry: LaunchCatalogEntry, reset?: boolean, workspaceId?: string) => Promise<void>;
  update: (change: DraftUpdate) => void;
  isCurrent: (revision: number | undefined) => boolean;
  attempt: SubmissionAttempt | undefined;
  submit: (
    request: SubmissionRequest,
    definition: string,
    identity?: components["schemas"]["ProcedureDefinitionRef"],
    preparation?: { isIntended: () => boolean; onSending: () => void },
  ) => Promise<string | undefined>;
  checkSubmission: () => Promise<void>;
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

function initialDraft(
  entry: LaunchCatalogEntry,
  revision: number,
  workspaceId: string,
): LaunchDraft {
  return {
    draftId: "default",
    draftName: "Draft",
    workspaceId,
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
    selection: defaultSelection(),
    actor: "operator",
    revision,
    pending: false,
    error: "",
    notice:
      "Experiment inputs are saved in application data. Start checks them before acquisition.",
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
    rejection: undefined,
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
  const [draft, setDraftState] = useState<LaunchDraft>();
  const [operatorError, setOperatorError] = useState<string>();
  const operatorSelection = useRef(0);
  const operatorPending = useRef(false);
  const latest = useRef(draft);
  const preparationToken = useRef<symbol | undefined>(undefined);
  // Admission continuations must observe edits synchronously, before a React effect
  // can run or a late preview/receipt response can continue the old intent.
  const setDraft = useCallback<Dispatch<SetStateAction<LaunchDraft | undefined>>>((change) => {
    const next = typeof change === "function" ? change(latest.current) : change;
    if (next !== latest.current) {
      operatorSelection.current += 1;
      operatorPending.current = false;
    }
    if (
      next?.revision !== latest.current?.revision ||
      next?.workspaceId !== latest.current?.workspaceId ||
      next?.draftId !== latest.current?.draftId
    )
      preparationToken.current = undefined;
    latest.current = next;
    setDraftState(next);
  }, []);
  const recovery = useLaunchRecovery(draft, setDraft);
  const [workspaceId, setWorkspaceId] = useState(
    () => new URLSearchParams(window.location.search).get("workspace") || "",
  );
  const currentWorkspace = useRef(workspaceId);
  const [selectedConfiguration, setSelectedConfiguration] =
    useState<components["schemas"]["ConfigurationChoice-Input"]>();
  const [selectedSubject, setSelectedSubject] = useState<ScientificSelection["subject"]>();
  const [selectedWorkingInput, setSelectedWorkingInput] = useState<WorkingInput>();
  const [attempt, setAttempt] = useState<SubmissionAttempt>();
  const attemptGeneration = useRef(0);
  const submitting = useRef(false);
  const [attemptsReady, setAttemptsReady] = useState(false);
  const [attemptReload, setAttemptReload] = useState(0);
  useEffect(() => {
    let active = true;
    const generation = attemptGeneration.current;
    void attemptHistory()
      .then((page) => {
        if (!active) return;
        const record = page.items[0];
        if (record && generation === attemptGeneration.current)
          setAttempt({
            request: attemptRequest(record),
            definition: "",
            sequence: record.sequence,
            status: "unknown",
            error:
              "Recovered original request. Check the original task or explicitly prepare a new run.",
          });
        setAttemptsReady(true);
      })
      .catch(() => {
        if (active) setAttemptsReady(false);
      });
    return () => {
      active = false;
    };
  }, [attemptReload]);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const canLeave = recovery.canLeave;
  const mayLeave = useCallback(() => {
    if (canLeave()) return true;
    setDraft((current) => {
      const error =
        "Wait for input saving or retry the failed save before switching experiments or source. Your current edits remain here.";
      return current && current.error !== error ? { ...current, error } : current;
    });
    return false;
  }, [canLeave, setDraft]);
  const mayLeaveRef = useRef(mayLeave);
  useEffect(() => {
    mayLeaveRef.current = mayLeave;
  }, [mayLeave]);
  const resetSource = (owner: string) => {
    if (!mayLeave()) return;
    if (owner === currentWorkspace.current) {
      setDraft((current) =>
        current
          ? {
              ...invalidateDraft(
                current,
                "Current source selected. Review retained input before starting or previewing.",
              ),
              codeRevision: undefined,
              needsReview: true,
            }
          : current,
      );
      return;
    }
    currentWorkspace.current = owner;
    setWorkspaceId(owner);
    setDraft((current) =>
      current
        ? {
            selection: current.selection,
            workingInput: current.workingInput,
            actor: current.actor,
            collection: current.collection,
            workspaceId: owner,
            experiment: "",
            definition: "",
            controlDefinition: "",
            values: {},
            controls: {},
            revision: current.revision + 1,
            pending: false,
            error: "",
            notice: "Code workspace changed. Select an experiment; Start will check its inputs.",
          }
        : current,
    );
  };
  const select = useCallback(
    async (entry: LaunchCatalogEntry, reset = false, owner = workspaceId) => {
      if (
        latest.current?.experiment &&
        latest.current.experiment !== entry.id &&
        !mayLeaveRef.current()
      )
        return;
      const original = latest.current;
      const generation = ++operatorSelection.current;
      operatorPending.current = false;
      preparationToken.current = undefined;
      if (!reset && original?.workspaceId === owner && original.definition === definitionKey(entry))
        return;
      let operator: string;
      operatorPending.current = true;
      try {
        operator =
          original &&
          (original.actor !== original.defaultActor ||
            (!reset && original.workspaceId === owner && original.experiment === entry.id))
            ? original.actor
            : (await readOperatorSettings()).operator;
      } catch (error) {
        if (generation === operatorSelection.current) setOperatorError(String(error));
        return;
      } finally {
        if (generation === operatorSelection.current) operatorPending.current = false;
      }
      if (!alive.current || generation !== operatorSelection.current || latest.current !== original)
        return;
      setOperatorError(undefined);
      setDraft((current) => {
        if (currentWorkspace.current !== owner) return current;
        if (!reset && current?.workspaceId === owner && current.definition === definitionKey(entry))
          return current;
        const next = initialDraft(entry, (current?.revision ?? 0) + 1, owner);
        next.workspaceId = owner;
        if (current?.workspaceId === owner && current.experiment === entry.id) {
          next.draftId = current.draftId;
          next.draftName = current.draftName;
        }
        next.codeRevision = current?.workspaceId === owner ? current.codeRevision : undefined;
        next.selection =
          current?.selection ??
          (selectedConfiguration
            ? {
                ...defaultSelection(),
                configuration: selectedConfiguration,
                subject: selectedSubject ?? defaultSelection().subject,
              }
            : defaultSelection());
        next.workingInput = current ? current.workingInput : selectedWorkingInput;
        next.collection = current?.collection;
        next.actor = operator;
        next.defaultActor = original?.actor !== original?.defaultActor ? undefined : operator;
        if (!reset && current?.workspaceId === owner && current.experiment === entry.id) {
          next.values = { ...next.values, ...current.values };
          next.controls = { ...next.controls, ...current.controls };
          next.sourceBaseline = current.sourceBaseline;
          next.needsReview = true;
          next.unresolvedFields = [
            ...Object.keys(current.values).filter(
              (name) => !(name in (entry.request.properties ?? {})),
            ),
            ...Object.keys(current.controls).filter(
              (id) =>
                !entry.controls.some(
                  (control) => control.id === id && control.ownership === "editable",
                ),
            ),
          ];
          next.notice =
            "Experiment declaration changed. Your edits and old defaults are retained. Review changed fields before preview.";
        }
        return next;
      });
    },
    [selectedConfiguration, selectedWorkingInput, selectedSubject, workspaceId, setDraft],
  );
  async function submit(
    request: SubmissionRequest,
    definition: string,
    identity?: components["schemas"]["ProcedureDefinitionRef"],
    preparation?: { isIntended: () => boolean; onSending: () => void },
  ) {
    if (attempt) throw new Error("Recover the original task or explicitly choose a new run first.");
    if (!attemptsReady)
      throw new Error(
        "Original submission history is not loaded. Reconnect before starting acquisition.",
      );
    if (submitting.current) throw new Error("A submission is already being prepared.");
    submitting.current = true;
    const generation = ++attemptGeneration.current;
    const input = latest.current;
    const inputIdentity = input ? JSON.stringify(rawInput(input)) : undefined;
    const stillIntended = () =>
      alive.current &&
      (!preparation || preparation.isIntended()) &&
      attemptGeneration.current === generation &&
      latest.current?.revision === input?.revision &&
      latest.current?.workspaceId === input?.workspaceId &&
      latest.current?.draftId === input?.draftId &&
      (latest.current ? JSON.stringify(rawInput(latest.current)) : undefined) === inputIdentity;
    try {
      await recovery.flush();
      if (!stillIntended()) return undefined;
      if (!identity)
        throw new Error(
          "Preview does not identify the original procedure definition. Preview again before submitting.",
        );
      const retained = await apiData(
        apiClient.POST("/api/v1/launch-attempts", { body: { definition: identity, request } }),
      );
      if (!stillIntended()) return undefined;
      setAttempt({
        request,
        definition,
        sequence: retained.sequence,
        status: "pending",
        error: "",
      });
      try {
        preparation?.onSending();
        const receipt = await apiData(
          apiClient.POST("/api/v1/experiment-launcher/submit", { body: request }),
        );
        if (!alive.current || attemptGeneration.current !== generation) return;
        setAttempt({
          request,
          definition,
          sequence: retained.sequence,
          status: "confirmed",
          procedureId: receipt.procedure_id,
          error: receipt.dispatch_error
            ? `Submitted; execution needs retry: ${receipt.dispatch_error}`
            : "",
        });
        return receipt.procedure_id;
      } catch (error) {
        if (alive.current && attemptGeneration.current === generation)
          setAttempt({
            request,
            definition,
            sequence: retained.sequence,
            status: isKnownRejection(error) ? "rejected" : "unknown",
            error: error instanceof Error ? error.message : String(error),
          });
      }
      return undefined;
    } finally {
      submitting.current = false;
    }
  }
  async function checkSubmission() {
    if (!attempt) return;
    const selected = attempt;
    const generation = ++attemptGeneration.current;
    setAttempt({ ...selected, checking: true, error: "" });
    try {
      if (!selected.sequence) throw new Error("Original submission has no retained receipt.");
      const result = await resolveAttempt(selected.sequence);
      if (!result.procedure_id)
        throw new Error(
          "No retained procedure found yet. Original submission remains unconfirmed; recovery never resubmits.",
        );
      const id = result.procedure_id;
      if (alive.current && attemptGeneration.current === generation)
        setAttempt({
          ...selected,
          checking: false,
          status: "confirmed",
          procedureId: id,
          error: "",
        });
    } catch (error) {
      if (alive.current && attemptGeneration.current === generation)
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
        copyDraft: async () => {
          const original = latest.current;
          if (!original || !mayLeave() || submitting.current) return;
          try {
            await recovery.flush();
            if (!alive.current || latest.current !== original) return;
            const draftId = crypto.randomUUID();
            if (!recovery.keepExplicitInput(original.workspaceId, original.experiment, draftId))
              return;
            if (attempt?.status === "confirmed" || attempt?.status === "rejected") {
              attemptGeneration.current += 1;
              setAttempt(undefined);
            }
            setDraft({
              ...invalidateDraft(
                structuredClone(original),
                "Independent draft created. Review and start when ready.",
              ),
              draftId,
              draftName: `${original.draftName ?? "Draft"} copy`.slice(0, 200),
              admittedProcedureId: undefined,
              needsReview: true,
            });
          } catch (error) {
            setDraft((current) =>
              current === original ? { ...current, error: String(error) } : current,
            );
          }
        },
        switchDraft: async (record) => {
          const original = latest.current;
          if (!mayLeave() || submitting.current) return;
          try {
            await recovery.flush();
            if (!alive.current || latest.current !== original) return;
            if (attempt?.status === "confirmed" || attempt?.status === "rejected") {
              attemptGeneration.current += 1;
              setAttempt(undefined);
            }
            currentWorkspace.current = record.target.workspace_id;
            setWorkspaceId(record.target.workspace_id);
            setDraft(restoreDraft(record, (original?.revision ?? 0) + 1));
          } catch (error) {
            setDraft((current) =>
              current === original && current ? { ...current, error: String(error) } : current,
            );
          }
        },
        renameDraft: (name) => {
          if (!name.trim() || name.length > 200) return;
          setDraft((current) => (current ? { ...current, draftName: name.trim() } : current));
        },
        beginPreparation: () => {
          if (operatorPending.current || preparationToken.current || submitting.current) return;
          const token = Symbol("launch preparation");
          preparationToken.current = token;
          const input = latest.current;
          const isIntended = () =>
            alive.current &&
            preparationToken.current === token &&
            latest.current?.revision === input?.revision &&
            latest.current?.workspaceId === input?.workspaceId &&
            latest.current?.draftId === input?.draftId;
          return {
            isIntended,
            finish: () => {
              if (preparationToken.current !== token) return;
              if (isIntended())
                setDraft((current) => (current ? { ...current, pending: false } : current));
              preparationToken.current = undefined;
            },
          };
        },
        recovery,
        recover: (record) => {
          if (!mayLeave()) return;
          if (!recovery.selectCopy(record)) return;
          currentWorkspace.current = record.target.workspace_id;
          setWorkspaceId(record.target.workspace_id);
          setDraft(restoreDraft(record, (latest.current?.revision ?? 0) + 1));
        },
        recoverAttempt: (record) => {
          preparationToken.current = undefined;
          setDraft((current) =>
            current
              ? invalidateDraft(current, "Original receipt selected. Preparation stopped.")
              : current,
          );
          attemptGeneration.current += 1;
          setAttempt({
            request: attemptRequest(record),
            definition: "",
            sequence: record.sequence,
            status: "unknown",
            error: "Recovered original request. Query it without resubmitting.",
          });
        },
        sourceObserved: (owner, source) =>
          setDraft((current) => {
            if (
              !current ||
              current.workspaceId !== owner ||
              current.codeRevision ||
              current.sourceBaseline?.content_hash === source?.content_hash
            )
              return current;
            return {
              ...invalidateDraft(
                current,
                "Author source changed. Retained inputs need review and a fresh preview.",
              ),
              sourceBaseline: source,
              needsReview: Boolean(current.sourceBaseline) || current.needsReview,
            };
          }),
        rerun: () => {
          attemptGeneration.current += 1;
          setAttempt(undefined);
          setDraft((current) =>
            current
              ? invalidateDraft(
                  { ...current, admittedProcedureId: undefined },
                  "New run selected. Start checks the inputs for another acquisition. The original receipt remains in recovery history.",
                )
              : current,
          );
        },
        workspaceId,
        selectWorkspace: (owner) => {
          if (owner !== currentWorkspace.current) resetSource(owner);
        },
        selectCurrentSource: (owner) => resetSource(owner ?? currentWorkspace.current),
        authorRefreshed: (owner) =>
          setDraft((current) =>
            currentWorkspace.current === owner &&
            current?.workspaceId === owner &&
            !current.codeRevision
              ? invalidateDraft(
                  current,
                  "Author code refreshed. Inputs are retained; review them before starting.",
                )
              : current,
          ),
        selectConfiguration: (choice, workingInput, subject) => {
          if (!alive.current) return;
          setSelectedConfiguration(choice);
          setSelectedWorkingInput(workingInput);
          setSelectedSubject(subject);
          setDraft((current) =>
            current
              ? invalidateDraft(
                  {
                    ...current,
                    planDirty: Boolean(current.plan),
                    workingInput,
                    selection: {
                      ...current.selection,
                      subject: subject ?? current.selection.subject,
                      configuration:
                        workingInput &&
                        choice.kind === "parameters" &&
                        current.selection.configuration.kind === "parameters"
                          ? {
                              ...choice,
                              setup: choice.setup ?? current.selection.configuration.setup,
                            }
                          : choice,
                    },
                  },
                  "Configuration selected. Start will check these inputs before acquisition.",
                )
              : current,
          );
        },
        draft,
        attemptsReady,
        retryAttempts: () => {
          if (!attemptsReady) setAttemptReload((value) => value + 1);
        },
        attempt,
        submit,
        checkSubmission,
        openPlan: async (plan, entry, isIntended = () => true) => {
          if (!mayLeave()) return false;
          const draftId = crypto.randomUUID();
          if (!recovery.keepExplicitInput(plan.definition.workspace_id, entry.id, draftId))
            return false;
          if (!alive.current) return false;
          const line = "manualLine" in plan;
          if (line && (!attemptsReady || submitting.current || attempt?.status === "pending"))
            return false;
          if (line && attempt && attempt.request.manual_line?.scan_id !== plan.manualLine.scan_id)
            return false;
          const receiptGeneration = attemptGeneration.current;
          const current = latest.current;
          const generation = ++operatorSelection.current;
          operatorPending.current = true;
          preparationToken.current = undefined;
          let operator: string;
          try {
            if (line && attempt?.status === "unknown") {
              if (!attempt.sequence) return false;
              const receipt = await resolveAttempt(attempt.sequence);
              if (!receipt.procedure_id) return false;
            }
            operator =
              current && current.actor !== current.defaultActor
                ? current.actor
                : (await readOperatorSettings()).operator;
          } catch (error) {
            setOperatorError(String(error));
            return false;
          } finally {
            if (generation === operatorSelection.current) operatorPending.current = false;
          }
          if (
            !alive.current ||
            latest.current !== current ||
            generation !== operatorSelection.current ||
            !isIntended() ||
            (line && receiptGeneration !== attemptGeneration.current)
          )
            return false;
          setOperatorError(undefined);
          const next = initialDraft(
            entry,
            (current?.revision ?? 0) + 1,
            plan.definition.workspace_id,
          );
          next.draftId = draftId;
          next.draftName = "Imported draft";
          next.defaultActor = current?.actor !== current?.defaultActor ? undefined : operator;
          next.workingInput = undefined;
          next.collection = current?.collection;
          const d = plan.definition;
          const imported = importLaunchRequest(next, entry, {
            workspace_id: d.workspace_id,
            scan_mode: d.scan_mode,
            parameter_sweeps: d.parameter_sweeps,
            action: "preview",
            request_key: "",
            experiment: d.experiment,
            version: d.version,
            inputs: d.inputs,
            control_edits: d.control_edits,
            selection: normalizeSelection(d.selection),
            actor: operator,
          });
          if (line) {
            // This explicit selection only changes UI focus. The same scan's
            // durable admission fence still blocks active or unreconciled runs.
            attemptGeneration.current += 1;
            setAttempt(undefined);
          }
          currentWorkspace.current = d.workspace_id;
          setWorkspaceId(d.workspace_id);
          setDraft({
            ...imported,
            plan: "ref" in plan ? plan : undefined,
            manualLine: "manualLine" in plan ? plan.manualLine : undefined,
            lineSource: "manualLine" in plan ? d.source : undefined,
            planDirty: false,
            selection: normalizeSelection(d.selection),
            collection: current?.collection,
            codeRevision: d.code_revision,
            workspaceId: d.workspace_id,
            handoff: undefined,
            notice:
              "ref" in plan
                ? `Opened ${plan.name}, revision ${plan.ref.revision}. Saved by ${plan.saved_by}; current operator is ${operator}. Start will prepare these saved inputs again.`
                : `Preparing ${plan.manualLine.label}. Adjust measurement settings, then Start; wiring confirmation is required before acquisition.`,
          });
          return true;
        },
        importHandoff: async (entry, handoff) => {
          if (!mayLeave()) return false;
          const draftId = crypto.randomUUID();
          if (!recovery.keepExplicitInput(handoff.request.workspace_id, entry.id, draftId))
            return false;
          if (!alive.current) return false;
          const current = latest.current;
          const generation = ++operatorSelection.current;
          operatorPending.current = true;
          preparationToken.current = undefined;
          let operator: string;
          try {
            operator =
              current && current.actor !== current.defaultActor
                ? current.actor
                : (await readOperatorSettings()).operator;
          } catch (error) {
            setOperatorError(String(error));
            return false;
          } finally {
            if (generation === operatorSelection.current) operatorPending.current = false;
          }
          if (
            !alive.current ||
            latest.current !== current ||
            generation !== operatorSelection.current
          )
            return false;
          setOperatorError(undefined);
          const next = initialDraft(
            entry,
            (current?.revision ?? 0) + 1,
            handoff.request.workspace_id,
          );
          next.draftId = draftId;
          next.draftName = "Imported draft";
          next.defaultActor = current?.actor !== current?.defaultActor ? undefined : operator;
          next.workingInput = undefined;
          next.collection = current?.collection;
          try {
            const imported = importLaunchHandoff(next, entry, handoff);
            const owner = imported.workspaceId;
            if (!owner)
              throw new Error("This draft has no author source. Select code and preview again.");
            currentWorkspace.current = owner;
            setWorkspaceId(owner);
            setDraft({ ...imported, workspaceId: owner, actor: operator });
            return true;
          } catch (error) {
            setDraft({
              ...(current ?? next),
              error: error instanceof Error ? error.message : String(error),
            });
            return false;
          }
        },
        select,
        update: (change) =>
          setDraft((current) => {
            if (!current) return current;
            const next = change(current);
            return next.actor !== current.actor ? { ...next, defaultActor: undefined } : next;
          }),
        isCurrent: (revision) =>
          !operatorPending.current &&
          alive.current &&
          currentWorkspace.current === workspaceId &&
          latest.current?.revision === revision,
      }}
    >
      {operatorError && (
        <p role="alert">
          Default operator could not be read. Check Settings and select the operation again.{" "}
          {operatorError}
        </p>
      )}
      {children}
    </Context>
  );
}
export function useLaunchDraft() {
  const context = useContext(Context);
  if (!context) throw new Error("Launch workspace requires its project draft provider");
  return context;
}
