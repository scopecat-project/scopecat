import { primaryButton, secondaryButton } from "../../ui/styles";
import { readParameterDraft, freezeParameterDraft } from "../config/parameter-draft-api";
import { LaunchRejectionDetails } from "./LaunchRejectionDetails";
import { ExecutionScenario } from "../../ui/ExecutionScenario";
import { reviewedForRequest } from "./scientific-selection";
import { useEffect, useLayoutEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData, ApiError } from "../../api-client";
import type { LaunchCatalogEntry, LaunchPreview } from "./launch-api";
import { ControlFields, ControlSummary, controlEdits } from "./ControlFields";
import {
  invalidateDraft,
  useLaunchDraft,
  type LaunchDraft,
  type LaunchPreparation,
} from "./LaunchDraft";
import { MeasurementContext, type MeasurementContextChange } from "./MeasurementContext";
import { PlanSave } from "./PlanSave";
import { PreflightSummary } from "./PreflightSummary";
import { canRenderField, type FormField } from "./launch-fields";

export function LaunchForm({
  entry,
  onAdmitted,
  catalogReady,
}: {
  entry: LaunchCatalogEntry;
  onAdmitted: (id: string) => void;
  catalogReady: boolean;
}) {
  const allFields = Object.entries(entry.request.properties ?? {});
  const fields = allFields.filter((pair): pair is [string, FormField] => canRenderField(pair[1]));
  const fieldsByName = new Map(fields);
  const supported = fields.length === allFields.length;
  const {
    projectId,
    selectConfiguration,
    draft: retained,
    update,
    select,
    isCurrent,
    submit,
    beginPreparation,
    attemptsReady,
    attempt,
    recovery,
  } = useLaunchDraft();
  if (!retained) throw new Error("Select a launch draft before rendering its form");
  const draft: LaunchDraft = retained;
  const { controls: drafts, actor, values, error, pending } = draft;
  const activePreparation = useRef<LaunchPreparation | undefined>(undefined);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      activePreparation.current?.finish();
    };
  }, []);
  const workingStatusId = useId();
  const [workingError, setWorkingError] = useState<string>();
  const working = useQuery({
    queryKey: ["launch-working-input", projectId, draft.workingInput?.draft_id],
    enabled: Boolean(draft.workingInput),
    retry: false,
    refetchInterval: 1000,
    queryFn: () => readParameterDraft(draft.workingInput!.draft_id),
  });
  const workingCurrent =
    !draft.workingInput ||
    (!!working.data &&
      !working.isError &&
      working.data.head_revision === draft.workingInput.revision &&
      working.data.draft.state === "saved" &&
      !working.data.branch_changed);
  const workingBlockReason = workingCurrent
    ? undefined
    : working.isError
      ? "Cannot check the source working table. Reconnect and retry; the adopted copy is unchanged."
      : !working.data
        ? "Checking the source working table before preview or acquisition…"
        : working.data.draft.state !== "saved"
          ? "The source working table is closed or conflicted. Open Configuration to review it and explicitly choose inputs again."
          : working.data.branch_changed
            ? "The working table’s branch changed. Review the latest branch head in Configuration, keep your table after review, then use current working inputs and preview again."
            : "The working table has newer saved edits. Use current working inputs, then review the changes before starting. Saved edits do not replace the adopted copy automatically.";
  useEffect(() => {
    if (!draft.workingInput || !working.data || workingCurrent || draft.pending || !draft.preview)
      return;
    update((current) =>
      current.workingInput?.draft_id === draft.workingInput?.draft_id
        ? invalidateDraft(
            current,
            "Working inputs changed. Use current inputs and review the changes before starting.",
          )
        : current,
    );
  }, [working.data, workingCurrent, draft.workingInput, draft.pending, draft.preview, update]);
  const readyToContinue = useRef(true);
  useLayoutEffect(() => {
    readyToContinue.current = catalogReady && workingCurrent;
  }, [catalogReady, workingCurrent]);
  const result = catalogReady && !pending && workingCurrent ? draft.preview : undefined;
  const fence = result?.manual_state;
  const manual = useQuery({
    queryKey: ["launch-manual-validity", projectId, fence],
    enabled: Boolean(fence),
    retry: false,
    refetchInterval: 1000,
    queryFn: () => {
      if (!fence) throw new Error("Preview before checking instrument changes");
      return apiData(apiClient.POST("/api/v1/experiment-launcher/validity", { body: fence }));
    },
  });
  const manualReady = Boolean(fence && manual.data?.valid && !manual.isError);
  useEffect(() => {
    if (!fence || !manual.data || manual.data.valid) return;
    const changes = manual.data.changes.map(
      (mutation) =>
        `${mutation.instrument_ids.join(", ")}: ${mutation.reason} (${new Date(mutation.occurred_at).toLocaleTimeString()})`,
    );
    update((current) =>
      current.preview?.manual_state?.event_id === fence.event_id
        ? invalidateDraft(
            current,
            `Manual instrument changes invalidate this preview. ${changes.join(" ")} Review the changes, then start again.`,
          )
        : current,
    );
  }, [fence, manual.data, update]);
  function changeInput(
    changes: Partial<Pick<LaunchDraft, "values" | "controls">> & MeasurementContextChange,
  ) {
    update((current) =>
      invalidateDraft(
        {
          ...current,
          ...changes,
          selection: { ...current.selection, ...changes.selection },
          workingInput:
            changes.selection?.configuration &&
            (changes.selection.configuration.kind !== "parameters" ||
              current.selection.configuration.kind !== "parameters" ||
              JSON.stringify(changes.selection.configuration.ref) !==
                JSON.stringify(current.selection.configuration.ref) ||
              JSON.stringify(changes.selection.configuration.overrides) !==
                JSON.stringify(current.selection.configuration.overrides))
              ? undefined
              : current.workingInput,
          planDirty:
            current.planDirty ||
            (Boolean(current.plan) &&
              Object.keys(changes).some((key) => key !== "actor" && key !== "collection")),
        },
        "Inputs changed. Start will check the new inputs before acquisition.",
      ),
    );
  }
  function change(name: string, value: string) {
    changeInput({ values: { ...values, [name]: value } });
  }
  function inputValues() {
    return Object.fromEntries(
      Object.entries(values)
        .filter(([, value]) => value !== "")
        .map(([name, value]) => [
          name,
          ["number", "integer"].includes(fieldsByName.get(name)?.type ?? "string")
            ? Number(value)
            : fieldsByName.get(name)?.type === "array"
              ? value.split("\n").filter(Boolean)
              : fieldsByName.get(name)?.type === "boolean"
                ? value === "true"
                : value,
        ]),
    );
  }
  const canPrepare =
    entry.actions.includes("preview") &&
    supported &&
    catalogReady &&
    workingCurrent &&
    !draft.needsReview &&
    !draft.unresolvedFields?.length &&
    recovery.ready &&
    !recovery.conflict &&
    Boolean(actor.trim()) &&
    !(draft.selection.configuration.kind === "parameters" && !draft.selection.configuration.setup);
  async function prepare(startAfter: boolean) {
    if (!canPrepare || pending || (startAfter && (attempt || !attemptsReady))) return;
    const operation = beginPreparation();
    if (!operation) {
      update((current) => ({
        ...current,
        error: "The previous preparation is still finishing. Wait before starting again.",
      }));
      return;
    }
    activePreparation.current = operation;
    const intended = () => mounted.current && readyToContinue.current && operation.isIntended();
    update((current) => ({
      ...current,
      pending: startAfter ? "prepare" : "preview",
      error: "",
      rejection: undefined,
    }));
    async function checkWorkingInput() {
      if (!draft.workingInput) return;
      const latest = await readParameterDraft(draft.workingInput.draft_id);
      if (!intended()) return;
      if (
        latest.head_revision !== draft.workingInput.revision ||
        latest.draft.state !== "saved" ||
        latest.branch_changed
      )
        throw new Error("Working inputs changed. Review and use current inputs before starting.");
    }
    try {
      const request = {
        scan_mode: "cartesian" as const,
        parameter_sweeps: [],
        action: "preview" as const,
        experiment: entry.id,
        version: entry.version,
        selection: draft.selection,
        record_collection: draft.collection || undefined,
        plan_ref: draft.planDirty ? undefined : draft.plan?.ref,
        inputs: inputValues(),
        control_edits: controlEdits(drafts),
        actor,
        request_key: "",
        code_revision: draft.codeRevision ?? draft.sourceBaseline,
        workspace_id: draft.workspaceId,
      };
      await checkWorkingInput();
      if (!intended()) return;
      const checked =
        startAfter && draft.preview
          ? draft.preview
          : await apiData<LaunchPreview>(
              apiClient.POST("/api/v1/experiment-launcher/preview", { body: request }),
            );
      if (!intended()) return;
      update((current) => ({
        ...current,
        preview: checked,
        requestKey:
          current.preview?.request_hash === checked.request_hash &&
          JSON.stringify(current.preview.reviewed) === JSON.stringify(checked.reviewed) &&
          JSON.stringify(current.preview.manual_state) === JSON.stringify(checked.manual_state)
            ? current.requestKey
            : undefined,
        notice: "Preview matches these inputs and the checked project configuration.",
      }));
      if (!startAfter) return;
      if (!checked.manual_state || !checked.procedure_definition)
        throw new Error(
          "Preparation did not return the required submission binding. Nothing was submitted.",
        );
      const validity = await apiData(
        apiClient.POST("/api/v1/experiment-launcher/validity", { body: checked.manual_state }),
      );
      if (!intended()) return;
      if (!validity.valid)
        throw new Error(
          `Instrument changes stopped this start. ${validity.changes.map((mutation) => `${mutation.instrument_ids.join(", ")}: ${mutation.reason}`).join(" ")} Review the changes, then start again.`,
        );
      await checkWorkingInput();
      if (!intended()) return;
      const requestKey =
        draft.preview === checked && draft.requestKey ? draft.requestKey : crypto.randomUUID();
      update((current) => ({ ...current, requestKey }));
      const procedureId = await submit(
        {
          ...request,
          action: "submit",
          request_key: requestKey,
          reviewed: reviewedForRequest(checked.reviewed),
          code_revision: checked.code_revision,
          workspace_id: checked.workspace_id,
          manual_state: checked.manual_state,
          expected_request_hash: checked.request_hash,
        },
        draft.definition,
        checked.procedure_definition,
        {
          isIntended: intended,
          onSending: () => update((current) => ({ ...current, pending: "submit" })),
        },
      );
      if (procedureId && intended()) {
        update((current) => ({ ...current, admittedProcedureId: procedureId }));
        onAdmitted(procedureId);
      }
    } catch (caught) {
      if (intended())
        update((current) => ({
          ...current,
          error: caught instanceof Error ? caught.message : String(caught),
          rejection: caught instanceof ApiError ? caught.launchRejection : undefined,
          preview: undefined,
          requestKey: undefined,
        }));
    } finally {
      operation.finish();
      if (activePreparation.current === operation) activePreparation.current = undefined;
    }
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const previewOnly = event.nativeEvent.submitter?.getAttribute("value") === "preview";
        void prepare(!previewOnly && entry.actions.includes("submit"));
      }}
      className="space-y-4 max-w-3xl"
    >
      <p>{entry.description}</p>
      <p role="status">{recovery.status}</p>
      {recovery.unsavedTargets.length > 0 && (
        <p role="alert">
          Input is not confirmed saved for:{" "}
          {recovery.unsavedTargets
            .map(([workspace, experiment]) => `${workspace} / ${experiment}`)
            .join(", ")}
          . Keep this application open and return to retry these copies.
        </p>
      )}
      <button
        type="button"
        className={secondaryButton}
        onClick={() => {
          void recovery.retry();
        }}
      >
        Retry saving input
      </button>
      {recovery.conflict && (
        <section aria-label="Conflicting experiment input">
          <p>Both copies are retained. Review the other window’s saved input before choosing.</p>
          <pre>{JSON.stringify(recovery.head?.input, null, 2)}</pre>
          <button
            className={secondaryButton}
            type="button"
            disabled={recovery.writing}
            onClick={() => recovery.adoptLocal()}
          >
            Keep my copy after review
          </button>
        </section>
      )}
      {draft.needsReview && (
        <section aria-label="Review recovered experiment">
          <p>
            Review retained inputs against the current code, declarations and parameter baseline.
            New defaults have not replaced your edits.
          </p>
          {Boolean(draft.unresolvedFields?.length) && (
            <p role="alert">
              Unavailable fields need explicit removal: {draft.unresolvedFields?.join(", ")}. Their
              original values remain in recovery history.
            </p>
          )}
          <button
            type="button"
            className={secondaryButton}
            onClick={() =>
              update((current) => ({
                ...invalidateDraft(current, "Input reviewed. Ready to start or inspect a preview."),
                needsReview: false,
                values: Object.fromEntries(
                  Object.entries(current.values).filter(
                    ([name]) => name in (entry.request.properties ?? {}),
                  ),
                ),
                controls: Object.fromEntries(
                  Object.entries(current.controls).filter(([id]) =>
                    entry.controls.some(
                      (control) => control.id === id && control.ownership === "editable",
                    ),
                  ),
                ),
                unresolvedFields: [],
              }))
            }
          >
            Confirm reviewed input
            {draft.unresolvedFields?.length ? " and remove unavailable fields" : ""}
          </button>
        </section>
      )}
      <fieldset disabled={pending === "submit" || !recovery.ready}>
        <MeasurementContext draft={draft} projectId={projectId} onChange={changeInput} />
      </fieldset>
      {draft.workingInput && (
        <section aria-label="Working parameter input" className="border border-line rounded p-3">
          <p>
            <strong>Adopted working-table copy.</strong> Using working input revision{" "}
            {draft.workingInput.revision}. This is a saved editing step, not a parameter version.
            Submitted experiments keep the inputs captured when they were started.
          </p>
          <details>
            <summary>Source working table</summary>
            <p className="break-all">{draft.workingInput.draft_id}</p>
          </details>
          <p id={workingStatusId} role="status">
            {workingBlockReason ??
              (result
                ? "The preview uses this adopted copy."
                : "The adopted copy matches the saved working table. Start checks it before acquisition.")}
          </p>
          {!workingCurrent && working.data && !working.isError && (
            <p>
              Working inputs changed or could not be checked. Preview and acquisition are blocked.
            </p>
          )}
          {workingError && <p role="alert">{workingError}</p>}
          <button
            type="button"
            className={secondaryButton}
            disabled={Boolean(pending) || !recovery.ready}
            onClick={async () => {
              const revision = draft.revision;
              try {
                const latest = await readParameterDraft(draft.workingInput!.draft_id);
                const frozen = await freezeParameterDraft(
                  latest.draft.draft_id,
                  latest.head_revision,
                );
                if (!mounted.current || !isCurrent(revision)) return;
                selectConfiguration(frozen.configuration, {
                  draft_id: frozen.draft_id,
                  revision: frozen.revision,
                });
                setWorkingError(undefined);
                void working.refetch();
              } catch (failure) {
                if (mounted.current && isCurrent(revision))
                  setWorkingError(failure instanceof Error ? failure.message : String(failure));
              }
            }}
          >
            Use current working inputs
          </button>
        </section>
      )}
      {draft.selection.configuration.kind === "parameters" && (
        <p>
          Parameter baseline: revision {draft.selection.configuration.ref.revision_id}
          {draft.selection.configuration.overrides.length > 0
            ? ` with ${draft.selection.configuration.overrides.length} parameter override(s)`
            : " with no overrides"}
          .{" "}
          {draft.workingInput &&
            "The adopted working-table values are applied over this baseline. "}
          The checked preview retains the exact setup used. This does not accept calibration or
          change defaults.
        </p>
      )}
      {draft.selection.configuration.kind === "unselected" ? (
        <p>
          Select a parameter branch or use a saved version from Configuration, then choose an
          experiment setup before starting or previewing.
        </p>
      ) : null}
      <p className="text-sm">
        {draft.preview && !result && !pending
          ? "Checking the retained preview against current project context…"
          : draft.notice}
      </p>
      <button
        type="button"
        disabled={Boolean(pending) || !recovery.ready}
        onClick={() => select(entry, true)}
        className={secondaryButton}
      >
        Reset launch draft
      </button>
      {!supported && (
        <p role="alert">
          This request schema needs a project-specific form. Use the project's Python workflow.
        </p>
      )}
      <fieldset disabled={pending === "submit" || !recovery.ready}>
        <ControlFields
          controls={entry.controls}
          drafts={drafts}
          onChange={(id, controlDraft) => {
            changeInput({ controls: { ...drafts, [id]: controlDraft } });
          }}
        />
      </fieldset>
      <fieldset
        disabled={pending === "submit" || !recovery.ready}
        className="grid grid-cols-2 gap-4"
      >
        {fields.map(([name, field]) => (
          <label key={name} className="flex flex-col gap-1">
            {field.title ?? name}
            {field.type === "array" && field.items?.enum ? (
              <select
                multiple
                aria-label={field.title ?? name}
                required={entry.request.required?.includes(name)}
                value={(values[name] ?? "").split("\n").filter(Boolean)}
                onChange={(event) =>
                  change(
                    name,
                    Array.from(event.target.selectedOptions, (option) => option.value).join("\n"),
                  )
                }
                className="border rounded p-2 min-h-32"
              >
                {field.items.enum.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            ) : field.enum || field.type === "boolean" ? (
              <select
                aria-label={field.title ?? name}
                required={entry.request.required?.includes(name)}
                value={values[name]}
                onChange={(event) => change(name, event.target.value)}
                className="border rounded p-2"
              >
                <option value="">Select…</option>
                {(field.enum ?? ["true", "false"]).map((value) => (
                  <option key={String(value)} value={String(value)}>
                    {String(value)}
                  </option>
                ))}
              </select>
            ) : (
              <input
                aria-label={field.title ?? name}
                required={entry.request.required?.includes(name)}
                type="text"
                inputMode={["number", "integer"].includes(field.type ?? "") ? "decimal" : undefined}
                step={field.type === "integer" ? 1 : "any"}
                min={field.minimum ?? field.exclusiveMinimum ?? undefined}
                max={field.maximum ?? undefined}
                value={values[name]}
                onChange={(event) => change(name, event.target.value)}
                className="border rounded p-2"
              />
            )}
          </label>
        ))}
      </fieldset>
      <section aria-label="Preview and start" className="space-y-3 border-t border-line pt-4">
        <p className="text-sm text-text-dim">
          Start checks the current inputs before submitting acquisition. Preview is optional and
          never acquires data.
        </p>
        <p>
          {entry.configuration_effect === "none"
            ? "This task does not publish parameter changes."
            : entry.configuration_effect === "candidate"
              ? "This task produces a parameter candidate. Creating it does not publish it to a branch."
              : "This task can publish parameter changes after its declared review. Review may be automated; starting does not add a separate human approval step."}
        </p>
        {entry.review && <p>{entry.review.instructions}</p>}
        <div className="flex flex-wrap items-center gap-3">
          {entry.actions.includes("submit") && (
            <button
              type="submit"
              value="start"
              className={primaryButton}
              disabled={!canPrepare || Boolean(pending) || Boolean(attempt) || !attemptsReady}
              aria-describedby={draft.workingInput ? workingStatusId : undefined}
            >
              {pending === "prepare"
                ? "Preparing acquisition…"
                : pending === "submit"
                  ? "Submitting acquisition…"
                  : "Start acquisition"}
            </button>
          )}
          {entry.actions.includes("preview") && (
            <button
              type="submit"
              value="preview"
              className={secondaryButton}
              disabled={!canPrepare || Boolean(pending)}
              aria-describedby={draft.workingInput ? workingStatusId : undefined}
            >
              {pending === "preview" ? "Preparing preview…" : "Preview"}
            </button>
          )}
          {(pending === "prepare" || pending === "preview") && (
            <button
              type="button"
              className={secondaryButton}
              onClick={() => {
                activePreparation.current?.finish();
                update((current) =>
                  invalidateDraft(current, "Preparation cancelled. No acquisition was submitted."),
                );
              }}
            >
              Cancel preparation
            </button>
          )}
        </div>
        {pending && (
          <p role="status">
            {pending === "submit"
              ? "Waiting for submission confirmation. Acquisition may already have started."
              : "Checking the experiment inputs. Acquisition has not been submitted."}
          </p>
        )}
        <p className="text-sm text-text-dim">
          {attempt
            ? "A submission is already retained. Check its status above, or explicitly prepare a new run."
            : "Start preserves the exact checked inputs and an original submission receipt for recovery."}
        </p>
        {fence && !manualReady && (
          <p role={manual.isError ? "alert" : "status"}>
            {manual.isError
              ? "Cannot check recent instrument changes. Preview again or wait for the connection to recover."
              : "Checking relevant instrument changes…"}
          </p>
        )}
        {error &&
          (draft.rejection ? (
            <LaunchRejectionDetails rejection={draft.rejection} />
          ) : (
            <p role="alert">{error}</p>
          ))}
        {result && (
          <>
            <ExecutionScenario
              scenario={result.reviewed.binding.scenario}
              label="Reviewed execution scenario"
            />
            <PreflightSummary preview={result} />
            <ControlSummary fields={entry.controls} values={result.controls} />
          </>
        )}
      </section>
      <section aria-label="Save this preparation" className="border-t border-line pt-4">
        <PlanSave
          key={`${draft.plan?.ref.plan_id ?? "new"}:${draft.plan?.ref.revision ?? 0}`}
          preview={result}
          request={() => ({
            workspace_id: draft.workspaceId,
            scan_mode: "cartesian",
            parameter_sweeps: [],
            action: "preview",
            request_key: "",
            selection: draft.selection,
            reviewed: result ? reviewedForRequest(result.reviewed) : undefined,
            experiment: entry.id,
            version: entry.version,
            inputs: inputValues(),
            control_edits: controlEdits(drafts),
            actor,
          })}
        />
      </section>
    </form>
  );
}
