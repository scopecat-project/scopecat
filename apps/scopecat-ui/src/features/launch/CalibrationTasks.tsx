import { useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import type { MethodResponse } from "openapi-fetch";
import { CalibrationEvidence } from "./CalibrationEvidence";

type TaskControl = components["schemas"]["CalibrationTaskControl"];
type TaskView = MethodResponse<typeof apiClient, "get", "/api/v1/calibration-tasks/{task_id}">;

export function CalibrationTasks({
  projectId,
  onProcedure,
}: {
  projectId: string;
  onProcedure: (id: string) => void;
}) {
  const [selected, setSelected] = useState(
    () => new URLSearchParams(window.location.search).get("task") ?? "",
  );
  const [expanded, setExpanded] = useState(Boolean(selected));
  const history = useInfiniteQuery({
    queryKey: ["calibration-tasks", projectId],
    enabled: expanded,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/calibration-tasks", {
          params: { query: { limit: 20, cursor: pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 3000,
  });
  function select(id: string) {
    setSelected(id);
    const url = new URL(window.location.href);
    url.searchParams.set("task", id);
    window.history.replaceState(null, "", url);
  }
  return (
    <details
      open={expanded}
      className="border rounded p-4 space-y-3"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary className="font-semibold">Calibration tasks</summary>
      {expanded && (
        <>
          <p>Inspect retained check plans and control their background progress.</p>
          {history.isPending && <p role="status">Loading calibration tasks…</p>}
          {history.error && <p role="alert">{history.error.message}</p>}
          {history.data?.pages[0]?.items.length === 0 && (
            <p>
              No calibration tasks yet. Create a plan with lab.calibration_tasks.create() in your
              author code.
            </p>
          )}
          <ul className="space-y-1">
            {history.data?.pages
              .flatMap((page) => page.items)
              .map((task) => (
                <li key={task.specification.task_id}>
                  <button
                    type="button"
                    className="underline text-left"
                    aria-current={selected === task.specification.task_id ? "true" : undefined}
                    onClick={() => select(task.specification.task_id)}
                  >
                    {task.specification.task_id} · {task.mode} · {task.created_at}
                  </button>
                </li>
              ))}
          </ul>
          {history.hasNextPage && (
            <button
              type="button"
              disabled={history.isFetchingNextPage}
              onClick={() => {
                void history.fetchNextPage();
              }}
            >
              Load earlier tasks
            </button>
          )}
          {selected && (
            <TaskDetail
              key={`${projectId}:${selected}`}
              taskId={selected}
              projectId={projectId}
              onProcedure={onProcedure}
            />
          )}
        </>
      )}
    </details>
  );
}

function TaskDetail({
  taskId,
  projectId,
  onProcedure,
}: {
  taskId: string;
  projectId: string;
  onProcedure: (id: string) => void;
}) {
  const client = useQueryClient();
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const queryKey = ["calibration-task", projectId, taskId];
  const detail = useQuery({
    queryKey,
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/calibration-tasks/{task_id}", {
          params: { path: { task_id: taskId } },
          signal,
        }),
      ),
    refetchInterval: 1000,
  });
  async function control(action: TaskControl["action"]) {
    if (!detail.data) return;
    setPending(true);
    setError("");
    try {
      const result = await apiData(
        apiClient.POST("/api/v1/calibration-tasks/control", {
          body: {
            task_id: taskId,
            expected_revision: detail.data.task.control_revision,
            action,
            actor: actor.trim(),
            reason: reason.trim(),
          },
        }),
      );
      client.setQueryData(queryKey, result);
      await client.invalidateQueries({ queryKey: ["calibration-tasks", projectId] });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      await detail.refetch();
    } finally {
      setPending(false);
    }
  }
  const view = detail.data;
  const terminal = view?.task.mode === "cancelled" || view?.task.mode === "finished";
  const disabled = pending || !actor.trim() || !reason.trim() || detail.isError;
  return (
    <section className="space-y-3" aria-label="Calibration task details">
      <h3 className="font-semibold">{taskId}</h3>
      <a className="underline" href={`?task=${encodeURIComponent(taskId)}#launch`}>
        Reopen this task
      </a>
      {detail.isPending && <p role="status">Loading task progress…</p>}
      {detail.error && <p role="alert">{detail.error.message}</p>}
      {error && <p role="alert">{error}</p>}
      {view && (
        <>
          <p>
            Advancement: {view.task.mode}.{" "}
            {view.progress.complete
              ? view.progress.successful
                ? "All planned checks passed."
                : "Plan ended with partial results."
              : "Plan is not complete."}
          </p>
          <p>
            Pause or cancel stops new stages. Already admitted measurements may continue; open their
            execution to inspect or cancel them.
          </p>
          {!terminal && (
            <fieldset disabled={pending} className="flex flex-wrap gap-3 items-end">
              <label>
                Task operator
                <input
                  className="block border rounded p-1"
                  value={actor}
                  onChange={(event) => setActor(event.target.value)}
                />
              </label>
              <label>
                Task control reason
                <input
                  className="block border rounded p-1"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              <button
                type="button"
                disabled={
                  disabled ||
                  (view.task.mode === "running" &&
                    Object.keys(view.task.dispatch_errors ?? {}).length === 0)
                }
                onClick={() => {
                  void control("start");
                }}
              >
                {view.task.mode === "manual" ? "Start task" : "Resume / retry admission"}
              </button>
              <button
                type="button"
                disabled={disabled || view.task.mode === "paused"}
                onClick={() => {
                  void control("pause");
                }}
              >
                Pause task
              </button>
              <button
                type="button"
                disabled={disabled}
                onClick={() => {
                  void control("cancel");
                }}
              >
                Cancel remaining stages
              </button>
            </fieldset>
          )}
          {view.task.last_control && (
            <p>
              Last control: {view.task.last_control.action} by {view.task.last_control.actor} —{" "}
              {view.task.last_control.reason}
            </p>
          )}
          <ol className="space-y-3">
            {view.progress.stages.map((stage) => {
              const planned = view.task.specification.plan.stages.find(
                (item) => item.id === stage.id,
              )!;
              const admissionError = view.task.dispatch_errors?.[stage.id];
              return (
                <li key={stage.id} className="border rounded p-3 space-y-1">
                  <h4 className="font-semibold">
                    {stage.id} · {stage.state.replaceAll("_", " ")}
                  </h4>
                  <p>
                    {planned.check.scope.capability} · {planned.check.scope.targets.join(", ")} ·{" "}
                    {planned.check.scope.conditions}
                  </p>
                  {!!stage.blocked_by?.length && (
                    <p>Prerequisites: {stage.blocked_by.join(", ")}</p>
                  )}
                  {admissionError && (
                    <p role="alert">
                      Admission stopped: {admissionError}. Correct the cause, then resume to retry
                      admission.
                    </p>
                  )}
                  {stage.state === "queued" && (
                    <p>
                      Waiting for execution. Open execution to inspect worker and resource status.
                    </p>
                  )}
                  {(stage.state === "attention_required" ||
                    stage.state === "waiting_for_input") && (
                    <p>
                      Open execution to inspect retained evidence and resolve the required action.
                    </p>
                  )}
                  {stage.procedure_run_id && (
                    <button
                      className="underline"
                      type="button"
                      onClick={() => onProcedure(stage.procedure_run_id!)}
                    >
                      Open execution for {stage.id}
                    </button>
                  )}
                  <details>
                    <summary>Frozen measurement context</summary>
                    <dl className="text-sm space-y-1 break-all">
                      <dt>Parameters</dt>
                      <dd>{planned.check.context.parameters.revision_id}</dd>
                      <dt>Measurement subject</dt>
                      <dd>{subjectName(planned.check.context.subject)}</dd>
                      <dt>Execution scenario</dt>
                      <dd>{planned.check.context.scenario?.label ?? "Physical equipment"}</dd>
                      <dt>Setup fingerprint</dt>
                      <dd>{planned.check.context.setup_content_hash}</dd>
                      <dt>Parameter fingerprint</dt>
                      <dd>{planned.check.context.parameters.content_hash}</dd>
                    </dl>
                  </details>
                  <CalibrationEvidence stage={planned} onProcedure={onProcedure} />
                </li>
              );
            })}
          </ol>
        </>
      )}
    </section>
  );
}

function subjectName(
  subject: TaskView["task"]["specification"]["plan"]["stages"][number]["check"]["context"]["subject"],
): string {
  if (subject.kind === "unbound") return "No sample selected";
  if (subject.kind === "registered_target")
    return `${subject.ref.target_id} · ${subject.sample.display_name}`;
  return subject.samples.map((sample) => `${sample.role}: ${sample.display_name}`).join(", ");
}
