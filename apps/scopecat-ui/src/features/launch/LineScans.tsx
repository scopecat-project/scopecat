import { stateLabel } from "./procedure-operator";
import { bindingForRequest } from "./scientific-selection";
import type { MethodResponse } from "openapi-fetch";
import { useOperatorInput } from "../application/use-operator-input";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData, ApiError } from "../../api-client";
import type { components } from "../../api-schema";
import { Button, Field, Input, Select } from "../../ui/Controls";
import { formControl } from "../../ui/styles";
import { useLaunchDraft } from "./LaunchDraft";
import { navigate, useLocationUrl } from "../../lib/navigation";

type Scan = View["scan"];
type View = MethodResponse<typeof apiClient, "get", "/api/v1/manual-line-scans/{scan_id}">;
type Create = components["schemas"]["ManualLineScanCreate"];
const lineStateLabels: Record<View["lines"][number]["state"], string> = {
  not_started: "尚未开始",
  completed: "已完成",
  in_progress: "进行中",
  uncertain: "结果未知",
  incomplete: "未完成",
  confirmation_required: "需要确认接线",
};
const id = (prefix: string) => `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;

export function LineScans() {
  const location = useLocationUrl();
  const [expanded, setExpanded] = useState(false);
  const visible = expanded || location.searchParams.has("line-scan");
  return (
    <details open={visible} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>物理线路扫描</summary>
      {visible && <LineScanWorkspace />}
    </details>
  );
}

export function LineScanWorkspace() {
  const { projectId, workspaceId, openPlan, draft, isCurrent, recovery } = useLaunchDraft();
  const cache = useQueryClient();
  const operator = useOperatorInput();
  const location = useLocationUrl();
  const selected = location.searchParams.get("line-scan") ?? "";
  const [name, setName] = useState("");
  const [lines, setLines] = useState("");
  const [template, setTemplate] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Create>();
  const opening = useRef(0);
  useEffect(
    () => () => {
      opening.current++;
    },
    [],
  );
  const catalog = useQuery({
    queryKey: ["line-scans", projectId, workspaceId],
    enabled: Boolean(projectId),
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/manual-line-scans", {
          headers: { "X-Scopecat-Workspace": workspaceId },
        }),
      ),
  });
  const plans = useQuery({
    queryKey: ["line-scan-templates", projectId, workspaceId],
    enabled: Boolean(projectId && workspaceId),
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/experiment-plans", {
          headers: { "X-Scopecat-Workspace": workspaceId },
        }),
      ),
  });
  const detail = useQuery({
    queryKey: ["line-scans", projectId, workspaceId, selected],
    enabled: Boolean(selected),
    refetchInterval: 1500,
    queryFn: () =>
      apiData<View>(
        apiClient.GET("/api/v1/manual-line-scans/{scan_id}", {
          params: { path: { scan_id: selected } },
          headers: { "X-Scopecat-Workspace": workspaceId },
        }),
      ),
  });
  function choose(scanId: string) {
    opening.current++;
    setBusy(false);
    const url = new URL(window.location.href);
    url.searchParams.set("line-scan", scanId);
    navigate(url);
  }
  async function create() {
    setError("");
    setBusy(true);
    try {
      const plan = plans.data?.items.find((item) => item.ref.plan_id === template);
      if (!pending && !plan) throw new Error("请选择已保存的测量模板。");
      const command = pending ?? {
        scan_id: id("lines"),
        saved_by: await operator.freshSnapshot(),
        name: name.trim(),
        lines: lines
          .split("\n")
          .map((label) => label.trim())
          .filter(Boolean)
          .map((label) => ({ line_id: id("line"), label })),
        template: {
          ...plan!.definition,
          scientific_binding: bindingForRequest(plan!.definition.scientific_binding),
        },
      };
      setPending(command);
      const saved = await apiData(
        apiClient.POST("/api/v1/manual-line-scans", {
          body: command,
          headers: { "X-Scopecat-Workspace": command.template.workspace_id },
        }),
      );
      setPending(undefined);
      setName("");
      setLines("");
      await cache.invalidateQueries({ queryKey: ["line-scans", projectId, workspaceId] });
      choose(saved.scan_id);
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        caught.status !== undefined &&
        caught.status >= 400 &&
        caught.status < 500
      )
        setPending(undefined);
      setError(String(caught));
    } finally {
      setBusy(false);
    }
  }
  async function prepare(scan: Scan, line: Scan["lines"][number]) {
    const token = ++opening.current;
    const revision = draft?.revision;
    setError("");
    setBusy(true);
    try {
      const saved = scan.template;
      const definitions = await apiData(
        apiClient.GET("/api/v1/experiment-launcher", {
          params: {
            query: { code_revision: saved.code_revision?.content_hash },
            header: { "X-Scopecat-Workspace": saved.workspace_id },
          },
        }),
      );
      const entry = definitions.entries.find(
        (item) => item.id === saved.experiment && item.version === saved.version,
      );
      if (!entry) throw new Error("测量此清单前，请恢复已保存模板对应的源码。");
      if (opening.current !== token) return;
      if (!isCurrent(revision)) {
        setError("加载模板时当前草稿已变化。请等待输入恢复完成，再次准备此线路。");
        return;
      }
      const accepted = await openPlan(
        {
          name: scan.name,
          definition: saved,
          manualLine: { scan_id: scan.scan_id, line_id: line.line_id, label: line.label },
        },
        entry,
        () => opening.current === token,
      );
      if (opening.current !== token) return;
      if (!accepted) {
        setError("模板未打开。请先保存或核实当前输入，并检查其他清单的原提交，再次准备此线路。");
        return;
      }
      const url = new URL(window.location.href);
      url.searchParams.delete("procedure");
      url.searchParams.set("preparation", "1");
      navigate(url);
    } catch (caught) {
      if (opening.current === token) setError(String(caught));
    } finally {
      if (opening.current === token) setBusy(false);
    }
  }
  return (
    <section
      aria-label="物理线路扫描"
      className="min-w-0 break-words [overflow-wrap:anywhere] border border-line rounded p-3 space-y-3"
    >
      <h3 className="font-semibold">物理线路扫描</h3>
      <p>
        共用一个测量模板，按线路清单顺序测量并保留所有尝试。每次采集前需确认实际接线；打开清单不会启动测量。
      </p>
      <Field>
        线路清单
        <Select
          aria-label="线路清单"
          value={selected}
          onChange={(event) => choose(event.target.value)}
        >
          <option value="">选择线路清单</option>
          {catalog.data?.items.map((scan) => (
            <option key={scan.scan_id} value={scan.scan_id}>
              {scan.name}
            </option>
          ))}
        </Select>
      </Field>
      <details>
        <summary>创建线路清单</summary>
        <Field>
          操作者
          <Input
            aria-label="线路清单操作者"
            value={pending?.saved_by ?? operator.actor}
            disabled={!!pending}
            onChange={(event) => operator.setActor(event.target.value)}
          />
        </Field>
        <p>先预览并保存实验计划，作为共用测量模板。每次采集都可调整常规设置。</p>
        <Field>
          测量模板
          <Select
            aria-label="测量模板"
            value={template}
            disabled={!!pending}
            onChange={(event) => setTemplate(event.target.value)}
          >
            <option value="">选择已保存的计划</option>
            {plans.data?.items
              .filter((plan) => !plan.definition.manual_lines)
              .map((plan) => (
                <option key={plan.ref.plan_id} value={plan.ref.plan_id}>
                  {plan.name}
                </option>
              ))}
          </Select>
        </Field>
        <Field>
          清单名称
          <Input
            aria-label="线路清单名称"
            value={name}
            disabled={!!pending}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field>
          物理线路，每行一条
          <textarea
            className={formControl}
            aria-label="物理线路，每行一条"
            value={lines}
            disabled={!!pending}
            onChange={(event) => setLines(event.target.value)}
          />
        </Field>
        <Button
          type="button"
          disabled={busy || (!pending && (!name.trim() || !lines.trim() || !template))}
          onClick={() => void create()}
        >
          {pending ? "重试保存线路清单" : "保存线路清单"}
        </Button>
      </details>
      {(error || catalog.error || detail.error) && (
        <p role="alert">{error || catalog.error?.message || detail.error?.message}</p>
      )}
      {detail.data && (
        <LineScanDetail
          key={selected}
          view={detail.data}
          busy={busy || !draft || !recovery.canLeave()}
          onPrepare={(line) => void prepare(detail.data.scan, line)}
          onSaved={() =>
            cache.invalidateQueries({ queryKey: ["line-scans", projectId, workspaceId] })
          }
        />
      )}
    </section>
  );
}

function LineScanDetail({
  view,
  busy,
  onPrepare,
  onSaved,
}: {
  view: View;
  busy: boolean;
  onPrepare: (line: Scan["lines"][number]) => void;
  onSaved: () => Promise<void>;
}) {
  const operator = useOperatorInput();
  const [editing, setEditing] = useState(false);
  const [labels, setLabels] = useState(view.scan.lines);
  const [revision, setRevision] = useState(view.scan.revision);
  const [error, setError] = useState("");
  const [selectedRuns, setSelectedRuns] = useState<string[]>([]);
  const firstNew = view.lines.find((item) => item.attempts.length === 0)?.line.line_id;
  const blocked = !!view.blocked_reason;
  const attempts = view.lines.flatMap((item) => item.attempts);
  const compared = selectedRuns
    .map((runId) => attempts.find((item) => item.run?.snapshot.run_id === runId))
    .filter((item) => !!item);
  async function saveLabels() {
    try {
      await apiData(
        apiClient.PUT("/api/v1/manual-line-scans/{scan_id}/labels", {
          params: { path: { scan_id: view.scan.scan_id } },
          headers: { "X-Scopecat-Workspace": view.scan.template.workspace_id },
          body: {
            saved_by: await operator.freshSnapshot(),
            name: view.scan.name,
            expected_revision: revision,
            lines: labels,
          },
        }),
      );
      setEditing(false);
      setError("");
      await onSaved();
    } catch (caught) {
      setError(String(caught));
    }
  }
  return (
    <div className="space-y-3">
      <h4 className="font-semibold">{view.scan.name}</h4>
      <p>
        重新打开后需核实当前接线。结果未知时必须先核对处理，再继续。重新测量会新增一次尝试，不会自动选定最佳结果。
      </p>
      <Button
        type="button"
        onClick={() => {
          setEditing(!editing);
          setLabels(view.scan.lines);
          setRevision(view.scan.revision);
        }}
      >
        编辑线路名称
      </Button>
      {error && <p role="alert">{error}</p>}
      <ol className="space-y-3">
        {view.lines.map((item, index) => (
          <li key={item.line.line_id} className="border border-line rounded p-3 space-y-2">
            <strong>
              {index + 1}. {item.line.label}
            </strong>{" "}
            <span>{lineStateLabels[item.state] ?? item.state}</span>
            {editing && (
              <Input
                aria-label={`第 ${index + 1} 条线路名称`}
                value={labels[index]?.label ?? ""}
                onChange={(event) =>
                  setLabels(
                    labels.map((line, position) =>
                      position === index ? { ...line, label: event.target.value } : line,
                    ),
                  )
                }
              />
            )}
            <Button
              type="button"
              disabled={
                busy || blocked || (item.attempts.length === 0 && firstNew !== item.line.line_id)
              }
              onClick={() => onPrepare(item.line)}
            >
              {item.attempts.length ? `重新测量 ${item.line.label}` : `准备 ${item.line.label}`}
            </Button>
            {item.attempts.map((attempt, number) => (
              <div key={attempt.procedure.procedure_run_id} className="space-y-1">
                <a
                  className="underline"
                  href={`?line-scan=${view.scan.scan_id}&procedure=${attempt.procedure.procedure_run_id}#launch`}
                >
                  第 {number + 1} 次尝试： {attempt.selection.label} ·{" "}
                  {stateLabel(attempt.procedure.closure?.status ?? attempt.procedure.state)}
                </a>
                {attempt.run && (
                  <>
                    <a className="underline ml-2" href={`?run=${attempt.run.snapshot.run_id}#runs`}>
                      曲线与结果
                    </a>
                    <label className="block">
                      <input
                        type="checkbox"
                        checked={selectedRuns.includes(attempt.run.snapshot.run_id)}
                        disabled={
                          !selectedRuns.includes(attempt.run.snapshot.run_id) &&
                          selectedRuns.length === 2
                        }
                        onChange={(event) => {
                          const runId = attempt.run!.snapshot.run_id;
                          setSelectedRuns(
                            event.target.checked
                              ? [...selectedRuns, runId]
                              : selectedRuns.filter((chosen) => chosen !== runId),
                          );
                        }}
                      />{" "}
                      比较此次尝试
                    </label>
                  </>
                )}
                <details>
                  <summary>已记录的设置与来源</summary>
                  <pre className="text-xs overflow-auto">
                    {JSON.stringify(
                      {
                        source: attempt.procedure.source,
                        inputs: attempt.procedure.intent,
                        run: attempt.run?.snapshot,
                      },
                      null,
                      2,
                    )}
                  </pre>
                </details>
              </div>
            ))}
          </li>
        ))}
      </ol>
      {editing && (
        <Button type="button" onClick={() => void saveLabels()}>
          保存线路名称
        </Button>
      )}
      {compared.length === 2 && (
        <section aria-label="线路测量尝试比较">
          <h4>所选尝试的设置</h4>
          <ul>
            {settingDifferences(
              measurementSettings(compared[0]!.procedure.intent),
              measurementSettings(compared[1]!.procedure.intent),
            ).map((text) => (
              <li key={text}>{text}</li>
            ))}
          </ul>
          <a
            className="underline"
            href={`?compare=${selectedRuns[0]}&compare-with=${selectedRuns[1]}&workspace=${encodeURIComponent(view.scan.template.workspace_id)}#analyses`}
          >
            比较已保留曲线
          </a>
          <p>
            曲线比较使用现有的显式比较模型和各次运行的原始网格。此清单不会插值，也不会将测量合并为矩形数组。
          </p>
        </section>
      )}
    </div>
  );
}

function measurementSettings(
  intent: View["lines"][number]["attempts"][number]["procedure"]["intent"],
) {
  return {
    configuration: intent.config,
    controls: intent.edits,
    inputs: intent.inputs,
    scan_mode: intent.scan_mode,
    parameter_sweeps: intent.parameter_sweeps,
  };
}

export function settingDifferences(left: unknown, right: unknown, path = ""): string[] {
  if (JSON.stringify(left) === JSON.stringify(right)) return [];
  if (
    left &&
    right &&
    typeof left === "object" &&
    typeof right === "object" &&
    !Array.isArray(left) &&
    !Array.isArray(right)
  ) {
    const a = left as Record<string, unknown>;
    const b = right as Record<string, unknown>;
    return [...new Set([...Object.keys(a), ...Object.keys(b)])].flatMap((key) =>
      settingDifferences(a[key], b[key], path ? `${path}.${key}` : key),
    );
  }
  return [`${path}: ${JSON.stringify(left) ?? "缺失"} → ${JSON.stringify(right) ?? "缺失"}`];
}
