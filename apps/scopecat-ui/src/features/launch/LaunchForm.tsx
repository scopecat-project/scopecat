import { useRef, useState } from "react";
import { apiClient, apiData } from "../../api-client";
import type { LaunchCatalogEntry, LaunchPreview } from "./launch-api";
import { PreflightSummary } from "./PreflightSummary";
import { canRenderField, type FormField } from "./launch-fields";

export function LaunchForm({
  entry,
  onAdmitted,
}: {
  entry: LaunchCatalogEntry;
  onAdmitted: (id: string) => void;
}) {
  const allFields = Object.entries(entry.request.properties ?? {});
  const fields = allFields.filter((pair): pair is [string, FormField] => canRenderField(pair[1]));
  const fieldsByName = new Map(fields);
  const supported = fields.length === allFields.length;
  const [sample, setSample] = useState("");
  const [actor, setActor] = useState("operator");
  const requestKey = useRef<string | undefined>(undefined);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      fields.map(([name, field]) => [
        name,
        field.default == null
          ? ""
          : Array.isArray(field.default)
            ? field.default.join("\n")
            : typeof field.default === "string"
              ? field.default
              : JSON.stringify(field.default),
      ]),
    ),
  );
  function invalidate() {
    setResult(undefined);
    setError("");
    requestKey.current = undefined;
  }
  const [result, setResult] = useState<LaunchPreview>();
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  // A result only describes the exact visible request that produced it.
  function change(name: string, value: string) {
    setValues({ ...values, [name]: value });
    invalidate();
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
    if (!entry.actions.includes("preview") || !supported) return;
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
              version: entry.version,
              sample: sample.trim() || null,
              inputs,
              actor,
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
  const source = result?.config_source;
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
            version: entry.version,
            inputs: inputValues(),
            request_key: requestKey.current,
            sample: sample.trim() || null,
            actor,
            config_source: source,
            expected_request_hash: result?.request_hash,
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
      {!supported && (
        <p role="alert">
          This request schema needs a project-specific form. Use the project's Python workflow.
        </p>
      )}
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
          disabled={pending || !supported || !actor.trim()}
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
              value={sample}
              onChange={(e) => {
                setSample(e.target.value);
                invalidate();
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
                setActor(e.target.value);
                invalidate();
              }}
              className="border rounded p-2"
            />
          </label>
          <button
            type="button"
            disabled={!source || !actor.trim() || pending}
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
      {error && <p role="alert">{error}</p>}
      {result && <PreflightSummary entry={entry} preview={result} />}
    </form>
  );
}
