import { Notice, Button, Field, Select, Input } from "../../ui/Controls";
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
  active = true,
}: {
  entry: LaunchCatalogEntry;
  onAdmitted: (id: string) => void;
  catalogReady: boolean;
  active?: boolean;
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
    rerun,
    recovery,
  } = useLaunchDraft();
  if (!retained) throw new Error("Select a launch draft before rendering its form");
  const draft: LaunchDraft = retained;
  const { controls: drafts, actor, values, error, pending } = draft;
  const activePreparation = useRef<LaunchPreparation | undefined>(undefined);
  const sentPreparation = useRef<LaunchPreparation | undefined>(undefined);
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
            : "The working table has newer saved edits. 使用当前工作输入, then review the changes before starting. Saved edits do not replace the adopted copy automatically.";
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
    readyToContinue.current = active && catalogReady && workingCurrent;
    if (!active && activePreparation.current !== sentPreparation.current) {
      // Hidden editors retain field focus state, but leaving the editor revokes
      // any unsent Start intent permanently, including a late preview response.
      activePreparation.current?.finish();
      activePreparation.current = undefined;
    }
  }, [active, catalogReady, workingCurrent]);
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
        "输入已变化，开始时会在采集前检查新输入。",
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
    active &&
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
        manual_line: draft.manualLine,
        plan_ref: draft.planDirty ? undefined : draft.plan?.ref,
        decision_source: draft.handoff
          ? {
              run_id: draft.handoff.source_run,
              analysis_id: draft.handoff.source_analysis,
              publication_hash: draft.handoff.source_hash,
            }
          : (draft.lineSource ?? draft.plan?.definition.source),
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
        notice: "预览与当前输入及已检查的项目配置一致。",
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
          onSending: () => {
            sentPreparation.current = operation;
            update((current) => ({ ...current, pending: "submit" }));
          },
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
      if (sentPreparation.current === operation) sentPreparation.current = undefined;
    }
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        const previewOnly = event.nativeEvent.submitter?.getAttribute("value") === "preview";
        void prepare(!previewOnly && entry.actions.includes("submit"));
      }}
      className="experiment-form space-y-3 max-w-4xl"
    >
      {draft.manualLine && (
        <Notice>
          Physical line: {draft.manualLine.label}. Start prepares this attempt; confirm the current
          wiring before measurement.
        </Notice>
      )}
      <section aria-label="Selected experiment" className="border-b border-line pb-2 space-y-1">
        <h3 className="sr-only">{entry.title}</h3>
        <p className="text-sm whitespace-pre-line">{entry.description}</p>
        {entry.configuration_effect !== "none" && (
          <p>
            {entry.configuration_effect === "candidate"
              ? "This task produces a parameter candidate. Creating it does not publish it to a branch."
              : "This task can publish parameter changes after its declared review. Review may be automated; starting does not add a separate human approval step."}
          </p>
        )}
        {entry.review && <p>{entry.review.instructions}</p>}
      </section>
      <div className="experiment-input-stage">
        <section aria-label="本次 run 的输入" className="space-y-2 border-t border-line pt-2">
          <div>
            <h4 className="font-semibold">本次 run 的输入</h4>
          </div>
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
            className="grid grid-cols-1 sm:grid-cols-2 gap-4"
          >
            {fields.map(([name, field]) => (
              <Field key={name} className="flex-col">
                {field.title ?? name}
                {field.type === "array" && field.items?.enum ? (
                  <Select
                    multiple
                    size={5}
                    className="min-h-32"
                    aria-label={field.title ?? name}
                    required={entry.request.required?.includes(name)}
                    value={(values[name] ?? "").split("\n").filter(Boolean)}
                    onChange={(event) =>
                      change(
                        name,
                        Array.from(event.target.selectedOptions, (option) => option.value).join(
                          "\n",
                        ),
                      )
                    }
                  >
                    {field.items.enum.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </Select>
                ) : field.enum || field.type === "boolean" ? (
                  <Select
                    aria-label={field.title ?? name}
                    required={entry.request.required?.includes(name)}
                    value={values[name]}
                    onChange={(event) => change(name, event.target.value)}
                  >
                    <option value="">请选择…</option>
                    {(field.enum ?? ["true", "false"]).map((value) => (
                      <option key={String(value)} value={String(value)}>
                        {String(value)}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <Input
                    aria-label={field.title ?? name}
                    required={entry.request.required?.includes(name)}
                    type="text"
                    inputMode={
                      ["number", "integer"].includes(field.type ?? "") ? "decimal" : undefined
                    }
                    step={field.type === "integer" ? 1 : "any"}
                    min={field.minimum ?? field.exclusiveMinimum ?? undefined}
                    max={field.maximum ?? undefined}
                    value={values[name]}
                    onChange={(event) => change(name, event.target.value)}
                  />
                )}
              </Field>
            ))}
          </fieldset>
        </section>
        <fieldset disabled={pending === "submit" || !recovery.ready}>
          <MeasurementContext draft={draft} projectId={projectId} onChange={changeInput} />
        </fieldset>
      </div>
      <section aria-label="预览与开始" className="space-y-2 border-b border-line pb-3">
        <h4 className="sr-only">开始或检查</h4>
        <p className="text-sm text-text-dim">开始前会检查输入。预览只检查输入，不执行采集。</p>
        <div className="flex flex-wrap items-center gap-3">
          {entry.actions.includes("submit") && (
            <Button
              type="submit"
              value="start"
              variant="primary"
              disabled={!canPrepare || Boolean(pending) || Boolean(attempt) || !attemptsReady}
              aria-describedby={draft.workingInput ? workingStatusId : undefined}
            >
              {pending === "prepare"
                ? "正在准备采集…"
                : pending === "submit"
                  ? "正在提交采集…"
                  : "开始采集"}
            </Button>
          )}
          {entry.actions.includes("preview") && (
            <Button
              type="submit"
              value="preview"
              disabled={!canPrepare || Boolean(pending)}
              aria-describedby={draft.workingInput ? workingStatusId : undefined}
            >
              {pending === "preview" ? "正在准备预览…" : "预览"}
            </Button>
          )}
          {attempt?.status === "confirmed" && (
            <Button type="button" disabled={Boolean(pending)} onClick={rerun}>
              准备另一次采集
            </Button>
          )}
          {(pending === "prepare" || pending === "preview") && (
            <Button
              type="button"
              onClick={() => {
                activePreparation.current?.finish();
                update((current) =>
                  invalidateDraft(current, "Preparation cancelled. No acquisition was submitted."),
                );
              }}
            >
              取消准备
            </Button>
          )}
        </div>
        {pending && (
          <Notice role="status" className="font-medium text-text-soft">
            {pending === "submit"
              ? "正在等待提交确认，采集可能已经开始。"
              : "正在检查实验输入，尚未提交采集。"}
          </Notice>
        )}
        <p className="text-sm text-text-dim">
          {attempt
            ? "已有保留提交，请在上方核实状态，或明确准备新的 run。"
            : "开始时保留已检查的精确输入与原始提交回执，供恢复使用。"}
        </p>
        {fence && !manualReady && (
          <p role={manual.isError ? "alert" : "status"}>
            {manual.isError
              ? "无法检查近期设备变更，请重新预览或等待连接恢复。"
              : "正在检查相关设备变更…"}
          </p>
        )}
        {error &&
          (draft.rejection ? (
            <LaunchRejectionDetails rejection={draft.rejection} />
          ) : (
            <Notice role="alert">{error}</Notice>
          ))}
      </section>
      <Notice role="status">{recovery.status}</Notice>
      {recovery.unsavedTargets.length > 0 && (
        <Notice role="alert">
          Input is not confirmed saved for:{" "}
          {recovery.unsavedTargets
            .map(([workspace, experiment, draftId]) => `${workspace} / ${experiment} / ${draftId}`)
            .join(", ")}
          . Keep this application open and return to retry these copies.
        </Notice>
      )}
      {(!recovery.ready || recovery.unsavedTargets.length > 0) && (
        <Button
          type="button"
          disabled={recovery.writing}
          onClick={() => {
            void recovery.retry();
          }}
        >
          重试保存输入
        </Button>
      )}
      {recovery.conflict && (
        <section aria-label="实验输入冲突">
          <p>两个副本均已保留，请先核实另一窗口保存的输入再作选择。</p>
          <pre>{JSON.stringify(recovery.head?.input, null, 2)}</pre>
          <Button type="button" disabled={recovery.writing} onClick={() => recovery.adoptLocal()}>
            核实后保留我的副本
          </Button>
        </section>
      )}
      {draft.needsReview && (
        <section aria-label="核实恢复的实验">
          <p>请对照当前代码、声明与参数基准核实保留输入。新的默认值未覆盖你的编辑。</p>
          {Boolean(draft.unresolvedFields?.length) && (
            <Notice role="alert">
              Unavailable fields need explicit removal: {draft.unresolvedFields?.join(", ")}. Their
              original values remain in recovery history.
            </Notice>
          )}
          <Button
            type="button"
            disabled={!recovery.ready}
            onClick={() =>
              recovery.ready &&
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
            确认已核实输入
            {draft.unresolvedFields?.length ? " and remove unavailable fields" : ""}
          </Button>
        </section>
      )}

      {draft.workingInput && (
        <section aria-label="工作参数输入" className="border border-line rounded p-3">
          <p>Working inputs · revision {draft.workingInput.revision}</p>
          <details>
            <summary>来源工作表</summary>
            <p className="break-all">{draft.workingInput.draft_id}</p>
          </details>
          <Notice id={workingStatusId} role="status">
            {workingBlockReason ?? (result ? "预览已更新" : "输入已更新")}
          </Notice>
          {!workingCurrent && working.data && !working.isError && (
            <p>工作输入已变化或无法核实，预览与采集已阻止。</p>
          )}
          {workingError && <Notice role="alert">{workingError}</Notice>}
          <Button
            type="button"
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
            使用当前工作输入
          </Button>
        </section>
      )}
      {draft.selection.configuration.kind === "parameters" && (
        <section
          aria-label="参数来源"
          className="rounded border border-line bg-panel p-3 space-y-2"
        >
          <h4 className="font-semibold">参数来源</h4>
          <p className="text-sm break-words">
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
        </section>
      )}
      {draft.selection.configuration.kind === "unselected" ? (
        <p>开始或预览前，请选择参数分支或参数与绑定中的已保存版本，再选择实验配置。</p>
      ) : null}
      <p className="text-sm">
        {draft.preview && !result && !pending
          ? "Checking the retained preview against current project context…"
          : draft.notice}
      </p>
      <Button
        type="button"
        disabled={Boolean(pending) || !recovery.ready}
        onClick={() => select(entry, true)}
      >
        重置实验草稿
      </Button>
      {!supported && (
        <Notice role="alert">
          This request schema needs a project-specific form. Use the project's Python workflow.
        </Notice>
      )}
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
      <section aria-label="保存此次准备" className="border-t border-line pt-4">
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
