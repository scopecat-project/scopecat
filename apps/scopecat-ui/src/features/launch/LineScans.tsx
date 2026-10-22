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
const id = (prefix: string) => `${prefix}_${crypto.randomUUID().replaceAll("-", "")}`;

export function LineScans() {
  const location = useLocationUrl();
  const [expanded, setExpanded] = useState(false);
  const visible = expanded || location.searchParams.has("line-scan");
  return (
    <details open={visible} onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>Physical line scans</summary>
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
      if (!pending && !plan) throw new Error("Choose a saved measurement template.");
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
      if (!entry) throw new Error("Restore the saved template source before measuring this list.");
      if (opening.current !== token) return;
      if (!isCurrent(revision)) {
        setError(
          "The current draft changed while loading the template. Prepare the line again when input recovery is complete.",
        );
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
        setError(
          "The template was not opened. Finish saving or reviewing the current input and check any original submission from another list, then prepare this line again.",
        );
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
      aria-label="Physical line scans"
      className="min-w-0 break-words [overflow-wrap:anywhere] border border-line rounded p-3 space-y-3"
    >
      <h3 className="font-semibold">Physical line scans</h3>
      <p>
        One measurement template, an ordered line list and all attempts. Confirm each physical
        connection before acquisition. Opening this list never starts a measurement.
      </p>
      <Field>
        Line list
        <Select
          aria-label="Line list"
          value={selected}
          onChange={(event) => choose(event.target.value)}
        >
          <option value="">Choose a line list</option>
          {catalog.data?.items.map((scan) => (
            <option key={scan.scan_id} value={scan.scan_id}>
              {scan.name}
            </option>
          ))}
        </Select>
      </Field>
      <details>
        <summary>Create a line list</summary>
        <Field>
          Operator
          <Input
            aria-label="Line list operator"
            value={pending?.saved_by ?? operator.actor}
            disabled={!!pending}
            onChange={(event) => operator.setActor(event.target.value)}
          />
        </Field>
        <p>
          Preview and save a measurement plan to use as the shared template. Each acquisition can
          adjust its ordinary settings.
        </p>
        <Field>
          Measurement template
          <Select
            aria-label="Measurement template"
            value={template}
            disabled={!!pending}
            onChange={(event) => setTemplate(event.target.value)}
          >
            <option value="">Choose a saved plan</option>
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
          List name
          <Input
            aria-label="Line list name"
            value={name}
            disabled={!!pending}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field>
          Physical lines, one per line
          <textarea
            className={formControl}
            aria-label="Physical lines, one per line"
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
          {pending ? "Retry saving line list" : "Save line list"}
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
        Recheck the current wiring after reopening. Unknown results must be reconciled before
        continuing. Remeasurement adds an attempt; no attempt is chosen as the winner.
      </p>
      <Button
        type="button"
        onClick={() => {
          setEditing(!editing);
          setLabels(view.scan.lines);
          setRevision(view.scan.revision);
        }}
      >
        Edit line names
      </Button>
      {error && <p role="alert">{error}</p>}
      <ol className="space-y-3">
        {view.lines.map((item, index) => (
          <li key={item.line.line_id} className="border border-line rounded p-3 space-y-2">
            <strong>
              {index + 1}. {item.line.label}
            </strong>{" "}
            <span>{item.state.replaceAll("_", " ")}</span>
            {editing && (
              <Input
                aria-label={`Line ${index + 1} name`}
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
              {item.attempts.length ? `Remeasure ${item.line.label}` : `Prepare ${item.line.label}`}
            </Button>
            {item.attempts.map((attempt, number) => (
              <div key={attempt.procedure.procedure_run_id} className="space-y-1">
                <a
                  className="underline"
                  href={`?line-scan=${view.scan.scan_id}&procedure=${attempt.procedure.procedure_run_id}#launch`}
                >
                  Attempt {number + 1}: {attempt.selection.label} ·{" "}
                  {attempt.procedure.closure?.status ??
                    attempt.procedure.state.replaceAll("_", " ")}
                </a>
                {attempt.run && (
                  <>
                    <a className="underline ml-2" href={`?run=${attempt.run.snapshot.run_id}#runs`}>
                      Curve and result
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
                      Compare this attempt
                    </label>
                  </>
                )}
                <details>
                  <summary>Recorded settings and source</summary>
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
          Save line names
        </Button>
      )}
      {compared.length === 2 && (
        <section aria-label="Line attempt comparison">
          <h4>Selected attempt settings</h4>
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
            Compare retained curves
          </a>
          <p>
            Curve comparison uses the existing explicit comparison model and each run's original
            grid. This list does not interpolate or combine measurements into a rectangular array.
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
  return [`${path}: ${JSON.stringify(left) ?? "absent"} → ${JSON.stringify(right) ?? "absent"}`];
}
