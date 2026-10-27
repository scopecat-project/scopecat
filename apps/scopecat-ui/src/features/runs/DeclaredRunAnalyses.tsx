import { Notice, Button, Field, Select, Input } from "../../ui/Controls";
import { useRef, useState } from "react";
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { errorMessage, titleCase } from "../../lib/presentation";
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
      <summary className="cursor-pointer text-sm font-semibold">分析此结果</summary>
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
      if (result && result.run_id !== runId) throw new Error("分析操作属于另一次运行。");
      return result;
    },
    staleTime: Infinity,
    retry: false,
    retryOnMount: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  if (catalog.isPending) return <p className="text-sm text-text-soft">正在加载已声明的分析…</p>;
  if (catalog.isError)
    return (
      <div className="space-y-2 text-sm text-text-soft">
        <Notice role="status">无法加载已声明的分析： {errorMessage(catalog.error)}</Notice>
        <p>
          若应用连接中断，请重新连接后再加载。否则请根据上述错误检查本次运行的原始源码和环境，
          或联系作者。重新加载只读取已保存源码中的声明，不执行分析；已有结果仍可阅读。
        </p>
        <Button
          type="button"
          disabled={catalog.isFetching}
          onClick={() => {
            void catalog.refetch({ cancelRefetch: false });
          }}
        >
          {catalog.isFetching ? "正在重新加载分析…" : "重新加载分析声明"}
        </Button>
      </div>
    );
  if (!catalog.data)
    return (
      <p className="text-sm text-text-soft">
        本次运行未保存作者源码身份，无法加载分析声明。仍可阅读已有结果，或用 Python
        分析已保存的数据。
      </p>
    );
  return catalog.data.actions.length ? (
    <DeclaredRunAnalyses catalog={catalog.data} />
  ) : (
    <p className="text-sm text-text-soft">
      本次运行保存的源码未声明分析。可阅读已有结果，或用 Python 分析已保存的数据。
      在当前代码中新增声明会用于新的运行。
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
    <section aria-label="分析此结果">
      <p className="mt-1 text-sm text-text-soft">
        以下操作由本实验声明，使用其已保存的作者代码。它们分析已有数据，不采集测量，也不采纳参数。
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
          throw new Error(`${input.name} 为必填项。`);
        if (input.kind === "quantity" || input.kind === "number" || input.kind === "integer") {
          const number = Number(selected.value);
          if (
            !Number.isFinite(number) ||
            (input.kind === "integer" && !Number.isSafeInteger(number))
          )
            throw new Error(
              `${input.name} 需要有限${input.kind === "integer" ? "整数" : "数值"}。`,
            );
          if (input.kind === "quantity") {
            if (!selected.unit.trim()) throw new Error(`${input.name} 需要单位。`);
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
        client.invalidateQueries({ queryKey: ["analyses", "related"] }),
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
          {action.unavailable_reason} 此操作无法从表单执行。请使用
          Python、已保存的数据和原始作者源码，或联系作者。 已有结果仍可阅读。
        </p>
      ) : (
        <>
          <fieldset disabled={pending} className="my-3 grid gap-3 sm:grid-cols-2">
            {inputs.map((input) => (
              <Field key={input.name} className="min-w-0">
                <span>
                  {titleCase(input.name)}
                  {input.required ? "（必填）" : ""}
                </span>
                {input.kind === "boolean" || input.kind === "choice" ? (
                  <Select
                    aria-label={titleCase(input.name)}
                    className="min-w-0"
                    value={values[input.name]!.value}
                    required={input.required}
                    onChange={(event) => edit(input.name, "value", event.target.value)}
                  >
                    <option value="">{input.required ? "请选择值" : "使用源码默认值"}</option>
                    {(input.kind === "boolean" ? ["true", "false"] : (input.choices ?? [])).map(
                      (choice) => (
                        <option key={choice} value={choice}>
                          {choice}
                        </option>
                      ),
                    )}
                  </Select>
                ) : (
                  <div className="flex min-w-0 gap-2">
                    <Input
                      aria-label={titleCase(input.name)}
                      className="min-w-0 w-full"
                      type={
                        ["number", "integer", "quantity"].includes(input.kind) ? "number" : "text"
                      }
                      step={input.kind === "integer" ? "1" : "any"}
                      required={input.required && input.kind !== "text"}
                      placeholder={
                        !input.required && input.kind !== "text" ? "使用源码默认值" : undefined
                      }
                      value={values[input.name]!.value}
                      onChange={(event) => edit(input.name, "value", event.target.value)}
                    />
                    {input.kind === "quantity" && (
                      <Input
                        aria-label={`${titleCase(input.name)} 单位`}
                        className="w-24 min-w-0"
                        placeholder="单位"
                        required={input.required || values[input.name]!.value !== ""}
                        value={values[input.name]!.unit}
                        onChange={(event) => edit(input.name, "unit", event.target.value)}
                      />
                    )}
                  </div>
                )}
              </Field>
            ))}
          </fieldset>
          <Button type="submit" disabled={pending}>
            {pending ? "正在分析…" : `执行 ${action.title}`}
          </Button>
        </>
      )}
      {execute.data && (
        <Notice className="mt-2" role="status">
          分析已完成。{" "}
          <a
            className="text-accent underline"
            href={`?run=${encodeURIComponent(catalog.run_id)}&run-analysis=${encodeURIComponent(execute.data.analysis_id)}#runs`}
          >
            查看已保存的结果
          </a>
        </Notice>
      )}
      {execute.isError && (
        <Notice className="mt-2 text-red" role="alert">
          {errorMessage(execute.error)} 若响应丢失，请先检查分析历史再决定是否重试。未自动提交重试。
        </Notice>
      )}
    </form>
  );
}
