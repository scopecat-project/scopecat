import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { LaunchCatalogEntry } from "./launch-api";
import { ControlFields, ControlSummary, controlEdits } from "./ControlFields";
import { invalidateDraft, useLaunchDraft, type LaunchDraft } from "./LaunchDraft";
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
    selectedContext,
    selectContext,
    draft: retained,
    update,
    select,
    isCurrent,
    configurationReady,
    configurationError,
    refreshConfiguration,
    submit,
    attempt,
  } = useLaunchDraft();
  if (!retained) throw new Error("Select a launch draft before rendering its form");
  const draft: LaunchDraft = retained;
  const { controls: drafts, sample, actor, values, error, pending } = draft;
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const result = catalogReady && configurationReady && !pending ? draft.preview : undefined;
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
      (change) =>
        `${change.instrument_ids.join(", ")}: ${change.reason} (${new Date(change.occurred_at).toLocaleTimeString()})`,
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
    changes: Partial<Pick<LaunchDraft, "values" | "controls" | "sample" | "actor">>,
  ) {
    update((current) =>
      invalidateDraft({ ...current, ...changes }, "Inputs changed. Preview again before starting."),
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
    if (!entry.actions.includes("preview") || !supported || !catalogReady) return;
    const revision = draft.revision;
    update((current) => ({ ...current, pending: true, error: "" }));
    try {
      const next = await apiData(
        apiClient.POST("/api/v1/experiment-launcher/preview", {
          body: {
            action: "preview",
            experiment: entry.id,
            version: entry.version,
            sample: selectedContext ? null : sample.trim() || null,
            context: selectedContext?.config_source.context,
            overrides: selectedContext?.config_source.overrides ?? [],
            inputs: inputValues(),
            control_edits: controlEdits(drafts),
            actor,
            request_key: "",
          },
        }),
      );
      if (isCurrent(revision))
        update((current) => ({
          ...current,
          preview: next,
          requestKey:
            current.preview?.request_hash === next.request_hash &&
            JSON.stringify(current.preview.config_source) === JSON.stringify(next.config_source)
              ? current.requestKey
              : undefined,
          notice: "Preview matches these inputs and the checked project configuration.",
        }));
      if (isCurrent(revision)) refreshConfiguration();
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
  const source = result?.config_source;
  async function start() {
    if (!source) return;
    const revision = draft.revision;
    const requestKey = draft.requestKey ?? crypto.randomUUID();
    update((current) => ({ ...current, requestKey, pending: true, error: "" }));
    try {
      const procedureId = await submit(
        {
          action: "submit",
          experiment: entry.id,
          version: entry.version,
          inputs: inputValues(),
          control_edits: controlEdits(drafts),
          request_key: requestKey,
          sample: selectedContext ? null : sample.trim() || null,
          context: selectedContext?.config_source.context,
          overrides: selectedContext?.config_source.overrides ?? [],
          actor,
          config_source: source,
          code_revision: result?.code_revision,
          manual_state: result?.manual_state,
          expected_request_hash: result?.request_hash,
        },
        draft.definition,
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
      {selectedContext ? (
        <div>
          <p>
            Parameter context: {selectedContext.config_source.context.entry_id} ·{" "}
            {selectedContext.config_source.sample.display_name} (
            {selectedContext.config_source.sample.sample_id}, r
            {selectedContext.config_source.sample.revision}) ·{" "}
            {selectedContext.config_source.sample.context_id}
          </p>
          <button
            type="button"
            onClick={() => {
              selectContext();
            }}
          >
            Use lab default
          </button>
        </div>
      ) : (
        <p>
          Using lab default. Select a saved sample working point in Configuration to use its
          parameters.
        </p>
      )}
      <p className="text-sm">
        {draft.preview && !result && !pending
          ? configurationError
            ? "Cannot verify current configuration. Retained inputs and submission keys are unchanged; refresh project data or preview again."
            : "Checking the retained preview against current project context…"
          : draft.notice}
      </p>
      <button
        type="button"
        disabled={pending}
        onClick={() => select(entry, true)}
        className="border rounded px-3 py-1"
      >
        Reset launch draft
      </button>
      {!supported && (
        <p role="alert">
          This request schema needs a project-specific form. Use the project's Python workflow.
        </p>
      )}
      <fieldset disabled={pending}>
        <ControlFields
          controls={entry.controls}
          drafts={drafts}
          onChange={(id, controlDraft) => {
            changeInput({ controls: { ...drafts, [id]: controlDraft } });
          }}
        />
      </fieldset>
      <fieldset disabled={pending} className="grid grid-cols-2 gap-4">
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
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            ) : (
              <input
                aria-label={field.title ?? name}
                required={entry.request.required?.includes(name)}
                type={["number", "integer"].includes(field.type ?? "") ? "number" : "text"}
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
      {entry.actions.includes("preview") && (
        <button
          type="submit"
          disabled={pending || !supported || !actor.trim() || !catalogReady}
          className="border rounded px-4 py-2"
        >
          {pending ? "Compiling…" : "Preview"}
        </button>
      )}
      {entry.actions.includes("submit") && (
        <fieldset disabled={pending} className="flex flex-wrap gap-3">
          <label>
            Sample ID{" "}
            <input
              aria-label="Sample ID"
              disabled={!!selectedContext}
              value={selectedContext?.config_source.sample.sample_id ?? sample}
              onChange={(e) => {
                changeInput({ sample: e.target.value });
              }}
              className="border rounded p-2"
            />
          </label>
          <label>
            Operator{" "}
            <input
              aria-label="Operator"
              value={actor}
              onChange={(e) => {
                changeInput({ actor: e.target.value });
              }}
              className="border rounded p-2"
            />
          </label>
          <button
            type="button"
            disabled={
              !source ||
              !manualReady ||
              !actor.trim() ||
              pending ||
              attempt?.status === "unknown" ||
              attempt?.status === "pending"
            }
            onClick={() => {
              void start();
            }}
            className="border rounded px-4 py-2"
          >
            Start acquisition
          </button>
        </fieldset>
      )}
      <p className="text-sm">
        Preview compiles only. Start acquisition submits a durable procedure and retains its
        results.
      </p>
      {fence && !manualReady && (
        <p role={manual.isError ? "alert" : "status"}>
          {manual.isError
            ? "Cannot check recent instrument changes. Preview again or wait for the connection to recover."
            : "Checking relevant instrument changes…"}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {result && (
        <>
          <PreflightSummary entry={entry} preview={result} />
          <ControlSummary fields={entry.controls} values={result.controls} />
        </>
      )}
    </form>
  );
}
