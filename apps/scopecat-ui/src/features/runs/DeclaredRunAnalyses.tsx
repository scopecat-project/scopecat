import { useRef, useState } from "react";
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { errorMessage, titleCase } from "../../lib/presentation";
import { secondaryButton } from "../../ui/styles";

import type { components } from "../../api-schema";

export type AnalysisActionInput = components["schemas"]["AnalysisActionInput"];
export type RunAnalysisAction = components["schemas"]["RunAnalysisAction"];
export type RunAnalysisActions = components["schemas"]["RunAnalysisActions"];

export function RunAnalysisActionsCard({ runId }: { runId: string }) {
  return <RunAnalysisActionsSection key={runId} runId={runId} />;
}

function RunAnalysisActionsSection({ runId }: { runId: string }) {
  const [loaded, setLoaded] = useState(false);
  return (
    <details
      className="rounded-md border border-line p-4"
      onToggle={(event) => {
        if (event.currentTarget.open) setLoaded(true);
      }}
    >
      <summary className="cursor-pointer text-sm font-semibold">Analyze this result</summary>
      {loaded && (
        <div className="mt-3">
          <DeclaredAnalysisCatalog runId={runId} />
        </div>
      )}
    </details>
  );
}

function DeclaredAnalysisCatalog({ runId }: { runId: string }) {
  const catalog = useQuery({
    queryKey: ["run-analysis-actions", runId],
    queryFn: async ({ signal }) => {
      const result = await apiData(
        apiClient.GET("/api/v1/runs/{run_id}/analysis-actions", {
          params: { path: { run_id: runId } },
          signal,
        }),
      );
      if (result && result.run_id !== runId)
        throw new Error("Analysis actions belong to another run.");
      return result;
    },
    staleTime: Infinity,
    retry: false,
    retryOnMount: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  if (catalog.isPending)
    return <p className="text-sm text-text-soft">Loading declared analyses…</p>;
  if (catalog.isError)
    return (
      <div className="space-y-2 text-sm text-text-soft">
        <p role="status">Could not load declared analyses: {errorMessage(catalog.error)}</p>
        <p>
          If the application connection was interrupted, reconnect and reload. Otherwise, use the
          error details above to check this run's original source and environment, or ask its author
          for help. Reloading reads the saved source's declarations; it does not run an analysis.
          Existing results remain available.
        </p>
        <button
          type="button"
          className={secondaryButton}
          disabled={catalog.isFetching}
          onClick={() => {
            void catalog.refetch({ cancelRefetch: false });
          }}
        >
          {catalog.isFetching ? "Reloading analyses…" : "Reload declared analyses"}
        </button>
      </div>
    );
  if (!catalog.data)
    return (
      <p className="text-sm text-text-soft">
        This run has no saved author source identity, so its declared analyses cannot be loaded. You
        can still read existing results or analyze the saved data in Python.
      </p>
    );
  return catalog.data.actions.length ? (
    <DeclaredRunAnalyses catalog={catalog.data} />
  ) : (
    <p className="text-sm text-text-soft">
      No analyses were declared for this run's saved source. Read existing results or analyze the
      saved data in Python. Adding declarations to current code applies to new runs.
    </p>
  );
}

type Value = { value: string; unit: string };
function initialValue(input: AnalysisActionInput): Value {
  const value = input.default;
  if (
    typeof value === "object" &&
    value !== null &&
    "value" in value &&
    typeof value.value === "number" &&
    "unit" in value &&
    typeof value.unit === "string"
  ) {
    return { value: String(value.value), unit: value.unit };
  }
  return {
    value:
      typeof value === "string" || typeof value === "number" || typeof value === "boolean"
        ? String(value)
        : "",
    unit: "",
  };
}

export function DeclaredRunAnalyses({ catalog }: { catalog: RunAnalysisActions }) {
  if (!catalog.actions?.length) return null;
  return (
    <section aria-label="Analyze this result">
      <p className="mt-1 text-sm text-text-soft">
        Actions declared for this experiment, using its saved author code. These actions analyze
        existing data; they do not acquire measurements or adopt parameters.
      </p>
      <div className="mt-3 grid gap-3">
        {catalog.actions.map((action) => (
          <AnalysisActionForm
            key={`${catalog.run_id}:${catalog.code_revision.content_hash}:${action.analysis}:${action.title}`}
            catalog={catalog}
            action={action}
          />
        ))}
      </div>
    </section>
  );
}

function AnalysisActionForm({
  catalog,
  action,
}: {
  catalog: RunAnalysisActions;
  action: RunAnalysisAction;
}) {
  const client = useQueryClient();
  const inputs = action.inputs ?? [];
  const [values, setValues] = useState<Record<string, Value>>(() =>
    Object.fromEntries(inputs.map((input) => [input.name, initialValue(input)])),
  );
  const mutationKey = [
    "declared-run-analysis",
    catalog.run_id,
    catalog.code_revision.content_hash,
    action.analysis,
  ];
  const pending = useIsMutating({ mutationKey }) > 0;
  const submitting = useRef(false);
  const execute = useMutation({
    mutationKey,
    mutationFn: async () => {
      const args: Record<string, string | number | boolean | { value: number; unit: string }> = {};
      for (const input of inputs) {
        const selected = values[input.name]!;
        if (selected.value === "" && !input.required && input.kind !== "text") continue;
        if (selected.value === "" && input.kind !== "text")
          throw new Error(`${input.name} is required.`);
        if (input.kind === "quantity" || input.kind === "number" || input.kind === "integer") {
          const number = Number(selected.value);
          if (
            !Number.isFinite(number) ||
            (input.kind === "integer" && !Number.isSafeInteger(number))
          )
            throw new Error(
              `${input.name} requires a finite ${input.kind === "integer" ? "integer" : "number"}.`,
            );
          if (input.kind === "quantity") {
            if (!selected.unit.trim()) throw new Error(`${input.name} requires a unit.`);
            args[input.name] = { value: number, unit: selected.unit.trim() };
          } else args[input.name] = number;
        } else if (input.kind === "boolean") args[input.name] = selected.value === "true";
        else args[input.name] = selected.value;
      }
      return apiData(
        apiClient.POST("/api/v1/author-revisions/analyze", {
          body: {
            run_id: catalog.run_id,
            workspace_id: catalog.workspace_id,
            code_revision: catalog.code_revision,
            analysis: action.analysis,
            arguments: args,
          },
        }),
      );
    },
    retry: false,
    onSettled: () => {
      submitting.current = false;
    },
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["analyses", "run", catalog.run_id] }),
        client.invalidateQueries({ queryKey: ["run-contents", catalog.run_id] }),
      ]);
    },
  });
  const edit = (name: string, field: keyof Value, value: string) => {
    execute.reset();
    setValues((old) => ({ ...old, [name]: { ...old[name]!, [field]: value } }));
  };
  return (
    <form
      className="rounded border border-line bg-panel-soft p-3 text-sm"
      aria-label={action.title}
      onSubmit={(event) => {
        event.preventDefault();
        if (submitting.current || client.isMutating({ mutationKey }) || action.unavailable_reason)
          return;
        submitting.current = true;
        execute.mutate();
      }}
    >
      <h4 className="font-semibold">{action.title}</h4>
      {action.description && (
        <p className="mt-1 whitespace-pre-wrap text-text-soft">{action.description}</p>
      )}
      {action.unavailable_reason ? (
        <p className="mt-2 text-text-soft">
          {action.unavailable_reason} This action cannot run from this form. Use Python with the
          saved data and original author source, or ask the author for help. Existing results remain
          available.
        </p>
      ) : (
        <>
          <fieldset disabled={pending} className="my-3 grid gap-3 sm:grid-cols-2">
            {inputs.map((input) => (
              <label key={input.name} className="grid min-w-0 gap-1">
                <span>
                  {titleCase(input.name)}
                  {input.required ? " (required)" : ""}
                </span>
                {input.kind === "boolean" || input.kind === "choice" ? (
                  <select
                    aria-label={titleCase(input.name)}
                    className="min-w-0 rounded border border-line bg-panel p-2"
                    value={values[input.name]!.value}
                    required={input.required}
                    onChange={(event) => edit(input.name, "value", event.target.value)}
                  >
                    <option value="">
                      {input.required ? "Choose a value" : "Use source default"}
                    </option>
                    {(input.kind === "boolean" ? ["true", "false"] : (input.choices ?? [])).map(
                      (choice) => (
                        <option key={choice} value={choice}>
                          {choice}
                        </option>
                      ),
                    )}
                  </select>
                ) : (
                  <div className="flex min-w-0 gap-2">
                    <input
                      aria-label={titleCase(input.name)}
                      className="min-w-0 w-full rounded border border-line bg-panel p-2"
                      type={
                        ["number", "integer", "quantity"].includes(input.kind) ? "number" : "text"
                      }
                      step={input.kind === "integer" ? "1" : "any"}
                      required={input.required && input.kind !== "text"}
                      placeholder={
                        !input.required && input.kind !== "text" ? "Use source default" : undefined
                      }
                      value={values[input.name]!.value}
                      onChange={(event) => edit(input.name, "value", event.target.value)}
                    />
                    {input.kind === "quantity" && (
                      <input
                        aria-label={`${titleCase(input.name)} unit`}
                        className="w-24 min-w-0 rounded border border-line bg-panel p-2"
                        placeholder="Unit"
                        required={input.required || values[input.name]!.value !== ""}
                        value={values[input.name]!.unit}
                        onChange={(event) => edit(input.name, "unit", event.target.value)}
                      />
                    )}
                  </div>
                )}
              </label>
            ))}
          </fieldset>
          <button type="submit" className={secondaryButton} disabled={pending}>
            {pending ? "Analyzing…" : `Run ${action.title}`}
          </button>
        </>
      )}
      {execute.data && (
        <p className="mt-2" role="status">
          Analysis finished.{" "}
          <a
            className="text-accent underline"
            href={`?run=${encodeURIComponent(catalog.run_id)}&run-analysis=${encodeURIComponent(execute.data.analysis_id)}#runs`}
          >
            View saved result
          </a>
        </p>
      )}
      {execute.isError && (
        <p className="mt-2 text-red" role="alert">
          {errorMessage(execute.error)} Check analysis history before trying again if the response
          was lost. No automatic retry was submitted.
        </p>
      )}
    </form>
  );
}
