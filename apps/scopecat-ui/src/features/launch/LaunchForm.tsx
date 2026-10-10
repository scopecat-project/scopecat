import { primaryButton, secondaryButton } from "../../ui/styles";
import { readParameterDraft, freezeParameterDraft } from "../config/parameter-draft-api";
import { LaunchRejectionDetails } from "./LaunchRejectionDetails";
import { ExecutionScenario } from "../../ui/ExecutionScenario";
import { reviewedForRequest } from "./scientific-selection";
import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData, ApiError } from "../../api-client";
import type { LaunchCatalogEntry, LaunchPreview } from "./launch-api";
import { ControlFields, ControlSummary, controlEdits } from "./ControlFields";
import { invalidateDraft, useLaunchDraft, type LaunchDraft } from "./LaunchDraft";
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
    attempt,
    recovery,
  } = useLaunchDraft();
  if (!retained) throw new Error("Select a launch draft before rendering its form");
  const draft: LaunchDraft = retained;
  const { controls: drafts, actor, values, error, pending } = draft;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
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
            : "The working table has newer saved edits. Use current working inputs, then preview again before starting. Saved edits do not replace the adopted copy automatically.";
  useEffect(() => {
    if (!draft.workingInput || !working.data || workingCurrent || draft.pending || !draft.preview)
      return;
    update((current) =>
      current.workingInput?.draft_id === draft.workingInput?.draft_id
        ? invalidateDraft(
            current,
            "Working inputs changed. Use current inputs and preview again before starting.",
          )
        : current,
    );
  }, [working.data, workingCurrent, draft.workingInput, draft.pending, draft.preview, update]);
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
            `Manual instrument changes invalidate this preview. ${changes.join(" ")} Preview again before starting.`,
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
        "Inputs changed. Preview again before starting.",
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
  async function preview(event: React.FormEvent) {
    event.preventDefault();
    if (
      !entry.actions.includes("preview") ||
      !supported ||
      !catalogReady ||
      !workingCurrent ||
      draft.needsReview ||
      Boolean(draft.unresolvedFields?.length) ||
      !recovery.ready ||
      recovery.conflict
    )
      return;
    const revision = draft.revision;
    update((current) => ({ ...current, pending: "preview", error: "", rejection: undefined }));
    try {
      const next = await apiData<LaunchPreview>(
        apiClient.POST("/api/v1/experiment-launcher/preview", {
          body: {
            scan_mode: "cartesian",
            parameter_sweeps: [],
            action: "preview",
            experiment: entry.id,
            version: entry.version,
            selection: draft.selection,
            record_collection: draft.collection || undefined,
            plan_ref: draft.planDirty ? undefined : draft.plan?.ref,
            inputs: inputValues(),
            control_edits: controlEdits(drafts),
            actor,
            request_key: "",
            code_revision: draft.codeRevision,
            workspace_id: draft.workspaceId,
          },
        }),
      );
      if (isCurrent(revision))
        update((current) => ({
          ...current,
          preview: next,
          requestKey:
            current.preview?.request_hash === next.request_hash &&
            JSON.stringify(current.preview.reviewed) === JSON.stringify(next.reviewed) &&
            JSON.stringify(current.preview.manual_state) === JSON.stringify(next.manual_state)
              ? current.requestKey
              : undefined,
          notice: "Preview matches these inputs and the checked project configuration.",
        }));
    } catch (caught) {
      if (isCurrent(revision))
        update((current) => ({
          ...current,
          error: caught instanceof Error ? caught.message : String(caught),
          rejection: caught instanceof ApiError ? caught.launchRejection : undefined,
          preview: undefined,
          requestKey: undefined,
        }));
    } finally {
      if (isCurrent(revision)) update((current) => ({ ...current, pending: false }));
    }
  }
  const source = result?.reviewed.config_source;
  async function start() {
    if (!source) return;
    const revision = draft.revision;
    const requestKey = draft.requestKey ?? crypto.randomUUID();
    update((current) => ({
      ...current,
      requestKey,
      pending: "submit",
      error: "",
      rejection: undefined,
    }));
    try {
      if (draft.workingInput) {
        const latest = await readParameterDraft(draft.workingInput.draft_id);
        if (
          latest.head_revision !== draft.workingInput.revision ||
          latest.draft.state !== "saved" ||
          latest.branch_changed
        )
          throw new Error(
            "Working inputs changed. Use current inputs and preview again before starting.",
          );
      }
      const procedureId = await submit(
        {
          scan_mode: "cartesian",
          parameter_sweeps: [],
          action: "submit",
          experiment: entry.id,
          version: entry.version,
          inputs: inputValues(),
          control_edits: controlEdits(drafts),
          request_key: requestKey,
          selection: draft.selection,
          reviewed: result ? reviewedForRequest(result.reviewed) : undefined,
          record_collection: draft.collection || undefined,
          plan_ref: draft.planDirty ? undefined : draft.plan?.ref,
          actor,
          code_revision: result?.code_revision,
          workspace_id: result?.workspace_id,
          manual_state: result?.manual_state,
          expected_request_hash: result?.request_hash,
        },
        draft.definition,
        result?.procedure_definition ?? undefined,
      );
      if (procedureId && isCurrent(revision)) {
        update((current) => ({ ...current, admittedProcedureId: procedureId }));
        if (mounted.current) onAdmitted(procedureId);
      }
    } catch (caught) {
      if (isCurrent(revision))
        update((current) => ({
          ...current,
          error: caught instanceof Error ? caught.message : String(caught),
        }));
    } finally {
      if (isCurrent(revision)) update((current) => ({ ...current, pending: false }));
    }
  }
  return (
    <form
      onSubmit={(event) => {
        void preview(event);
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
                ...invalidateDraft(current, "Input reviewed. Preview before starting."),
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
      <fieldset disabled={!recovery.ready}>
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
                : "The adopted copy matches the saved working table. Preview is required before acquisition.")}
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
          experiment setup before preview.
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
      <fieldset disabled={Boolean(pending) || !recovery.ready}>
        <ControlFields
          controls={entry.controls}
          drafts={drafts}
          onChange={(id, controlDraft) => {
            changeInput({ controls: { ...drafts, [id]: controlDraft } });
          }}
        />
      </fieldset>
      <fieldset disabled={Boolean(pending) || !recovery.ready} className="grid grid-cols-2 gap-4">
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
          Preview checks these inputs without acquiring data. Start acquisition runs the checked
          experiment.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          {entry.actions.includes("preview") && (
            <button
              type="submit"
              aria-describedby={draft.workingInput ? workingStatusId : undefined}
              disabled={
                Boolean(pending) ||
                !recovery.ready ||
                recovery.conflict ||
                draft.needsReview ||
                !supported ||
                !actor.trim() ||
                !catalogReady ||
                !workingCurrent ||
                (draft.selection.configuration.kind === "parameters" &&
                  !draft.selection.configuration.setup)
              }
              className={result ? secondaryButton : primaryButton}
            >
              {pending === "preview" ? "Preparing preview…" : "Preview"}
            </button>
          )}
          {entry.actions.includes("submit") && (
            <fieldset
              disabled={Boolean(pending) || !recovery.ready}
              className="flex flex-wrap gap-3"
            >
              <button
                type="button"
                disabled={
                  !source ||
                  !manualReady ||
                  !actor.trim() ||
                  Boolean(pending) ||
                  Boolean(attempt) ||
                  draft.needsReview ||
                  recovery.conflict
                }
                aria-describedby={draft.workingInput ? workingStatusId : undefined}
                onClick={() => {
                  void start();
                }}
                className={primaryButton}
              >
                {pending === "submit" ? "Submitting acquisition…" : "Start acquisition"}
              </button>
            </fieldset>
          )}
        </div>
        {pending && (
          <p role="status">
            {pending === "preview"
              ? "Preparing the experiment preview. Acquisition has not been submitted."
              : "Waiting for submission confirmation. Acquisition may already have started."}
          </p>
        )}
        <p className="text-sm text-text-dim">
          {attempt
            ? "A submission is already retained. Check its status above, or explicitly prepare a new run."
            : result
              ? "Preview ready. Review the checked configuration below before starting."
              : "Start acquisition becomes available after a successful preview and validity check."}
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
            <PreflightSummary entry={entry} preview={result} />
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
