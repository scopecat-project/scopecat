import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

type Field = {
  type: string;
  title?: string;
  default?: string | number | boolean;
  enum?: string[];
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
};
type Entry = {
  id: string;
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
          <LaunchForm key={entry.id} entry={entry} />
        </>
      )}
    </section>
  );
}

function LaunchForm({ entry }: { entry: Entry }) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(entry.request.properties).map(([name, field]) => [
        name,
        String(field.default ?? ""),
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
  }
  async function preview(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    setResult(undefined);
    const inputs = Object.fromEntries(
      Object.entries(values)
        .filter(([, value]) => value !== "")
        .map(([name, value]) => [
          name,
          ["number", "integer"].includes(entry.request.properties[name]?.type ?? "string")
            ? Number(value)
            : entry.request.properties[name]?.type === "boolean"
              ? value === "true"
              : value,
        ]),
    );
    try {
      setResult(
        await apiData(
          apiClient.POST("/api/v1/experiment-launcher/preview", {
            body: { action: "preview", experiment: entry.id, inputs },
          }),
        ),
      );
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
            {field.enum || field.type === "boolean" ? (
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
      <p className="text-sm">
        Preview does not acquire data or activate configuration. Start acquisition through the
        project calibration command.
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
