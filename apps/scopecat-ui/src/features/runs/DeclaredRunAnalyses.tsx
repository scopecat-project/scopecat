import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { errorMessage, titleCase } from "../../lib/presentation";
import { secondaryButton } from "../../ui/styles";

import type { components } from "../../api-schema";

export type AnalysisActionInput = components["schemas"]["AnalysisActionInput"];
export type RunAnalysisAction = components["schemas"]["RunAnalysisAction"];
export type RunAnalysisActions = components["schemas"]["RunAnalysisActions"];

export function RunAnalysisActionsCard({ runId }: { runId: string }) {
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
  });
  if (catalog.isPending)
    return <p className="text-sm text-text-soft">Loading declared analyses…</p>;
  if (catalog.isError)
    return (
      <p role="status" className="text-sm text-text-soft">
        Declared analyses unavailable: {errorMessage(catalog.error)} Existing results remain
        available.
      </p>
    );
  return catalog.data ? <DeclaredRunAnalyses catalog={catalog.data} /> : null;
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
    <section className="rounded-md border border-line p-4" aria-label="Analyze this result">
      <h3 className="text-sm font-semibold">Analyze this result</h3>
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
  const execute = useMutation({
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
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["analyses", "run", catalog.run_id] }),
        client.invalidateQueries({ queryKey: ["run-contents", catalog.run_id] }),
      ]);
    },
  });
  const edit = (name: string, field: keyof Value, value: string) =>
    setValues((old) => ({ ...old, [name]: { ...old[name]!, [field]: value } }));
  return (
    <form
      className="rounded border border-line bg-panel-soft p-3 text-sm"
      aria-label={action.title}
      onSubmit={(event) => {
        event.preventDefault();
        execute.mutate();
      }}
    >
      <h4 className="font-semibold">{action.title}</h4>
      {action.description && (
        <p className="mt-1 whitespace-pre-wrap text-text-soft">{action.description}</p>
      )}
      {action.unavailable_reason ? (
        <p className="mt-2 text-text-soft">{action.unavailable_reason}</p>
      ) : (
        <>
          <fieldset disabled={execute.isPending} className="my-3 grid gap-3 sm:grid-cols-2">
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
          <button type="submit" className={secondaryButton} disabled={execute.isPending}>
            {execute.isPending ? "Analyzing…" : `Run ${action.title}`}
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
