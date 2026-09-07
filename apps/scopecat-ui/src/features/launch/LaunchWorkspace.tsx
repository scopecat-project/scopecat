import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

type Field = {
  type: string;
  title?: string;
  default?: string | number | boolean | string[];
  enum?: string[];
  items?: { type?: string; enum?: string[] };
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
};
type Entry = {
  id: string;
  can_submit?: boolean;
  title: string;
  description: string;
  request: { properties: Record<string, Field>; required?: string[] };
};

export function LaunchWorkspace() {
  const catalog = useQuery({
    queryKey: ["experiment-launcher"],
    queryFn: async () => {
      const result = await apiData(apiClient.GET("/api/v1/experiment-launcher"));
      return (result.calibrations ?? []) as unknown as Entry[];
    },
  });
  const [procedureId, setProcedureId] = useState(
    () => new URLSearchParams(window.location.search).get("procedure") ?? "",
  );
  function admitted(id: string) {
    setProcedureId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("procedure", id);
    window.history.replaceState(null, "", url);
  }
  const [selected, setSelected] = useState("");
  const entry = catalog.data?.find((item) => item.id === selected) ?? catalog.data?.[0];
  return (
    <section className="p-6 space-y-4">
      <h2 className="text-lg font-semibold">Calibrations</h2>
      <p>Select a maintained experiment and preview its configured parameters.</p>
      {catalog.isPending && <p role="status">Loading calibrations…</p>}
      {catalog.error && <p role="alert">{catalog.error.message}</p>}
      {catalog.data?.length === 0 && <p>This project has no calibration preview provider.</p>}
      {entry && (
        <>
          <label className="block">
            Experiment{" "}
            <select
              aria-label="Experiment"
              value={entry.id}
              onChange={(event) => setSelected(event.target.value)}
              className="border rounded p-2 ml-2"
            >
              {catalog.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
          <LaunchForm key={entry.id} entry={entry} onAdmitted={admitted} />
        </>
      )}
      {procedureId && <ProcedureProgress procedureId={procedureId} />}
    </section>
  );
}

function LaunchForm({ entry, onAdmitted }: { entry: Entry; onAdmitted: (id: string) => void }) {
  const [sample, setSample] = useState("");
  const [actor, setActor] = useState("");
  const requestKey = useRef<string | undefined>(undefined);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(entry.request.properties).map(([name, field]) => [
        name,
        Array.isArray(field.default) ? field.default.join("\n") : String(field.default ?? ""),
      ]),
    ),
  );
  const [result, setResult] = useState<unknown>();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  // A result only describes the exact visible request that produced it.
  function change(name: string, value: string) {
    setValues({ ...values, [name]: value });
    setResult(undefined);
    setError("");
    requestKey.current = undefined;
  }
  function inputValues() {
    return Object.fromEntries(
      Object.entries(values)
        .filter(([, value]) => value !== "")
        .map(([name, value]) => [
          name,
          ["number", "integer"].includes(entry.request.properties[name]?.type ?? "string")
            ? Number(value)
            : entry.request.properties[name]?.type === "array"
              ? value.split("\n").filter(Boolean)
              : entry.request.properties[name]?.type === "boolean"
                ? value === "true"
                : value,
        ]),
    );
  }
  async function preview(event: React.FormEvent) {
    event.preventDefault();
    requestKey.current = undefined;
    setPending(true);
    setError("");
    setResult(undefined);
    const inputs = inputValues();
    try {
      setResult(
        await apiData(
          apiClient.POST("/api/v1/experiment-launcher/preview", {
            body: {
              action: "preview",
              experiment: entry.id,
              inputs,
              actor: actor || "operator",
              request_key: "",
            },
          }),
        ),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  const source = (
    result as { config_source?: { content_hash: string; registry_generation: number } } | undefined
  )?.config_source;
  async function start() {
    if (!source) return;
    requestKey.current ??= crypto.randomUUID();
    setPending(true);
    setError("");
    try {
      const receipt = await apiData(
        apiClient.POST("/api/v1/experiment-launcher/submit", {
          body: {
            action: "submit",
            experiment: entry.id,
            inputs: inputValues(),
            request_key: requestKey.current,
            sample,
            actor,
            expected_config_hash: source.content_hash,
            expected_generation: source.registry_generation,
          },
        }),
      );
      onAdmitted(receipt.procedure_id);
      if (receipt.dispatch_error)
        setError(`Submitted; execution needs retry: ${receipt.dispatch_error}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
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
      <fieldset disabled={pending} className="grid grid-cols-2 gap-4">
        {Object.entries(entry.request.properties).map(([name, field]) => (
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
                type={["number", "integer"].includes(field.type) ? "number" : "text"}
                step={field.type === "integer" ? 1 : "any"}
                min={field.minimum ?? field.exclusiveMinimum}
                max={field.maximum}
                value={values[name]}
                onChange={(event) => change(name, event.target.value)}
                className="border rounded p-2"
              />
            )}
          </label>
        ))}
      </fieldset>
      <button type="submit" disabled={pending} className="border rounded px-4 py-2">
        {pending ? "Compiling…" : "Preview"}
      </button>
      {entry.can_submit && (
        <fieldset disabled={pending} className="flex flex-wrap gap-3">
          <label>
            Sample ID{" "}
            <input
              aria-label="Sample ID"
              value={sample}
              onChange={(e) => {
                setSample(e.target.value);
                requestKey.current = undefined;
              }}
              className="border rounded p-2"
            />
          </label>
          <label>
            Operator{" "}
            <input
              aria-label="Operator"
              value={actor}
              onChange={(e) => setActor(e.target.value)}
              className="border rounded p-2"
            />
          </label>
          <button
            type="button"
            disabled={!source || !sample.trim() || !actor.trim() || pending}
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
        Preview compiles only. Start acquisition submits a durable procedure; configuration
        acceptance waits for review.
      </p>
      {error && <p role="alert">{error}</p>}
      {result !== undefined && (
        <div role="status">
          <h3 className="font-semibold">Preview ready</h3>
          <details>
            <summary>Resolved request and configuration</summary>
            <pre className="overflow-auto text-xs p-3">{JSON.stringify(result, null, 2)}</pre>
          </details>
        </div>
      )}
    </form>
  );
}

function ProcedureProgress({ procedureId }: { procedureId: string }) {
  const [cancelActor, setCancelActor] = useState("");
  const [cancelReason, setCancelReason] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState("");
  const status = useQuery({
    queryKey: ["launch-procedure", procedureId],
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/procedures/{procedure_run_id}", {
          params: { path: { procedure_run_id: procedureId } },
        }),
      ),
    refetchInterval: 1000,
  });
  const steps = useQuery({
    queryKey: ["launch-procedure-steps", procedureId],
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/procedures/{procedure_run_id}/steps", {
          params: { path: { procedure_run_id: procedureId }, query: { limit: 50 } },
        }),
      ),
    refetchInterval: 1000,
  });
  async function cancel() {
    if (!status.data) return;
    setError("");
    setCancelling(true);
    try {
      await apiData(
        apiClient.POST("/api/v1/procedures/{procedure_run_id}/cancel", {
          params: { path: { procedure_run_id: procedureId } },
          body: {
            procedure_run_id: procedureId,
            expected_run_revision: status.data.revision,
            actor: cancelActor,
            reason: cancelReason,
          },
        }),
      );
      await status.refetch();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setCancelling(false);
    }
  }
  async function resume() {
    setError("");
    try {
      const receipt = await apiData(
        apiClient.POST("/api/v1/procedures/{procedure_run_id}/dispatch", {
          params: { path: { procedure_run_id: procedureId } },
        }),
      );
      if (receipt.dispatch_error) setError(receipt.dispatch_error);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  return (
    <section className="border rounded p-4 space-y-2">
      <h3>Procedure progress</h3>
      <p>{procedureId}</p>
      {status.error && <p role="alert">{status.error.message}</p>}
      <p>
        {status.data?.state === "leased" && status.data.cancellation
          ? "Cancellation requested — finishing current step"
          : status.data?.state === "ready" && status.data.resource_wait
            ? "Waiting for resources"
            : status.data && statusLabel(status.data.closure?.status ?? status.data.state)}
      </p>
      {status.data?.resource_wait && (
        <a href={`?run=${encodeURIComponent(status.data.resource_wait.run_id)}#runs`}>
          Inspect waiting child run: {status.data.resource_wait.run_id}
        </a>
      )}
      {(status.data?.attention_reason || status.data?.closure?.reason) && (
        <p>{status.data.attention_reason ?? status.data.closure?.reason}</p>
      )}
      {status.data?.state === "waiting_for_input" && (
        <a href="#decisions">Review results in Decisions</a>
      )}
      {status.data && ["ready", "waiting_for_input"].includes(status.data.state) && (
        <button
          type="button"
          onClick={() => {
            void resume();
          }}
          className="border rounded px-3 py-1"
        >
          Resume execution
        </button>
      )}
      {status.data &&
        !status.data.cancellation &&
        ["ready", "waiting_for_input", "leased"].includes(status.data.state) && (
          <details>
            <summary>Cancel remaining procedure</summary>
            <p>
              Retains completed results. A running step completes and settles before the procedure
              stops.
            </p>
            <label>
              Cancellation actor
              <input value={cancelActor} onChange={(event) => setCancelActor(event.target.value)} />
            </label>
            <label>
              Cancellation reason
              <input
                value={cancelReason}
                onChange={(event) => setCancelReason(event.target.value)}
              />
            </label>
            <button
              type="button"
              disabled={cancelling || !cancelActor.trim() || !cancelReason.trim()}
              onClick={() => {
                void cancel();
              }}
            >
              {status.data.state === "leased" ? "Stop after current step" : "Cancel procedure"}
            </button>
          </details>
        )}
      {status.data?.closure?.actor && <p>Closed by {status.data.closure.actor}</p>}
      {error && <p role="alert">{error}</p>}
      <ul>
        {steps.data?.items.map((step) => (
          <li key={`${step.step_key}:${step.attempt}`}>
            {step.step_key}:{" "}
            {status.data?.resource_wait?.step_key === step.step_key &&
            status.data.closure?.status === "cancelled"
              ? "Cancelled before acquisition"
              : status.data?.resource_wait?.step_key === step.step_key &&
                  status.data.state === "ready"
                ? "Waiting for resources"
                : status.data?.closure?.status === "cancelled" && step.state === "waiting_for_input"
                  ? "Review cancelled"
                  : statusLabel(step.state)}{" "}
            {step.failure_reason}
            {step.output?.kind === "run" && (
              <a
                className="ml-2 underline"
                href={`?run=${encodeURIComponent(step.output.run_id)}#runs`}
              >
                Open run
              </a>
            )}
            {step.output?.kind === "analysis" && (
              <a
                className="ml-2 underline"
                href={
                  step.output.subject.kind === "run"
                    ? `?run=${encodeURIComponent(step.output.subject.run_id)}#runs`
                    : step.output.subject.kind === "sample"
                      ? `?sample=${encodeURIComponent(step.output.subject.sample_id)}#samples`
                      : `?analysis=${encodeURIComponent(step.output.analysis_record_id)}#analyses`
                }
              >
                Open analysis
              </a>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function statusLabel(state: string): string {
  const labels: Record<string, string> = {
    ready: "Queued",
    leased: "Running",
    waiting_for_input: "Waiting for review",
    attention_required: "Needs attention",
    succeeded: "Completed",
    failed: "Failed",
    closed: "Finished",
    cancelled: "Cancelled",
    running: "Running",
  };
  return labels[state] ?? state.replaceAll("_", " ");
}
