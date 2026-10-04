import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { MethodResponse } from "openapi-fetch";
import { ApiError, apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import { errorMessage } from "../../lib/presentation";
import { getDevices } from "../instruments/device-api";
import { getParameterRevisions } from "./parameter-api";
import { getSetupDefinitions } from "./setup-api";

type Document = Inspection["document"];
type Inspection = MethodResponse<
  typeof apiClient,
  "get",
  "/api/v1/configuration-exchange/imports/{content_hash}"
>;
type Receipt = MethodResponse<typeof apiClient, "post", "/api/v1/configuration-exchange/derive">;
type Derive = components["schemas"]["ConfigurationDerive"];
const root = "/api/v1/configuration-exchange";
const maxBytes = 16 * 1024 * 1024;

async function checked(response: Response) {
  if (!response.ok) {
    const detail = (await response.json()) as { detail?: unknown };
    throw new Error(
      typeof detail.detail === "string" ? detail.detail : `Request failed (${response.status})`,
    );
  }
  return response;
}
async function inspect(document: string, retain = false): Promise<Inspection> {
  return (
    await checked(
      await fetch(`${root}/${retain ? "imports" : "inspect"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: document,
      }),
    )
  ).json() as Promise<Inspection>;
}
function download(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ConfigurationExchange({
  operator,
  onCreated,
}: {
  operator: string;
  onCreated: (revision: string) => Promise<void>;
}) {
  const cache = useQueryClient();
  const versions = useQuery({
    queryKey: ["parameter-revisions"],
    queryFn: ({ signal }) => getParameterRevisions(signal),
  });
  const setups = useQuery({
    queryKey: ["setup-definitions"],
    queryFn: ({ signal }) => getSetupDefinitions(signal),
  });
  const devices = useQuery({ queryKey: ["devices"], queryFn: ({ signal }) => getDevices(signal) });
  const sources = useQuery({
    queryKey: ["exchange-sources"],
    queryFn: () => apiData(apiClient.GET("/api/v1/author-workspaces")),
  });
  const imports = useQuery({
    queryKey: ["configuration-imports"],
    queryFn: () => apiData(apiClient.GET("/api/v1/configuration-exchange/imports")),
  });
  const [exporting, setExporting] = useState(false);
  const [version, setVersion] = useState("");
  const [setup, setSetup] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [label, setLabel] = useState("");
  const [values, setValues] = useState(true);
  const [exported, setExported] = useState<Document>();
  const [review, setReview] = useState<Inspection>();
  const [name, setName] = useState("");
  const [includeSetup, setIncludeSetup] = useState(false);
  const [bindings, setBindings] = useState<Record<string, string>>({});
  const [receipt, setReceipt] = useState<Receipt>();
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const loading = useRef(0);
  const operation = useRef<{ intent: string; id: string } | undefined>(undefined);
  const sourceState = useQuery({
    queryKey: ["exchange-source-state", workspace],
    enabled: !!workspace,
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/author-revisions", {
          params: { header: { "X-Scopecat-Workspace": workspace } },
        }),
      ),
  });
  const clearReview = () => {
    loading.current++;
    setReview(undefined);
    setReceipt(undefined);
    setError("");
    setNotice("");
    operation.current = undefined;
  };
  const openReview = (next: Inspection) => {
    setReview(next);
    setName(`${next.document.label} (copy)`);
    setIncludeSetup(false);
    setBindings({});
    setReceipt(undefined);
    setError("");
    setNotice("");
    operation.current = undefined;
  };
  const readFile = async (file: File) => {
    clearReview();
    saveCopy.reset();
    const request = loading.current;
    setBusy(true);
    try {
      if (file.size > maxBytes) throw new Error("Configuration file exceeds 16 MiB.");
      const next = await inspect(await file.text());
      if (request === loading.current) openReview(next);
    } catch (problem) {
      if (request === loading.current) setError(errorMessage(problem));
    } finally {
      if (request === loading.current) setBusy(false);
    }
  };
  const exportingMutation = useMutation({
    mutationFn: () =>
      apiData(
        apiClient.POST("/api/v1/configuration-exchange/export", {
          body: {
            parameter_revision: version,
            label: label.trim(),
            include_values: values,
            setup_definition: setup || null,
            workspace: workspace || null,
            source_revision: workspace ? sourceState.data?.active?.content_hash : null,
          },
        }),
      ),
    onSuccess: setExported,
  });
  const saveCopy = useMutation({
    mutationFn: async () => {
      if (!review) throw new Error("Inspect a configuration first.");
      const mapped: NonNullable<Derive["bindings"]> = {};
      if (includeSetup)
        for (const role of review.document.setup?.definition.instruments ?? []) {
          const device = devices.data?.items.find((item) => item.device.id === bindings[role.id]);
          if (!device || device.device.state === "retired")
            throw new Error(`Choose a local device for ${role.id}.`);
          mapped[role.id] = device.device.head;
        }
      const fields = {
        document: review.document,
        name: name.trim(),
        actor: operator,
        include_setup: includeSetup,
        bindings: mapped,
      };
      const intent = JSON.stringify(fields);
      if (operation.current?.intent !== intent)
        operation.current = { intent, id: crypto.randomUUID() };
      return apiData(
        apiClient.POST("/api/v1/configuration-exchange/derive", {
          body: { ...fields, operation_id: operation.current.id },
        }),
      );
    },
    onSuccess: async (created) => {
      setReceipt(created);
      setNotice("");
      await cache.invalidateQueries({ queryKey: ["configuration-imports"] });
      await cache.invalidateQueries({ queryKey: ["setup-definitions"] });
      await onCreated(created.branch.revision.revision_id);
    },
  });
  const keep = async () => {
    if (!review) return;
    setBusy(true);
    setError("");
    try {
      await inspect(JSON.stringify(review.document), true);
      await cache.invalidateQueries({ queryKey: ["configuration-imports"] });
      setNotice("Original saved for review. No parameters, setup or source were activated.");
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  const saveExport = async () => {
    if (!exported) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const content = JSON.stringify(exported);
      if (window.pywebview) {
        const path = await window.pywebview.api.save_configuration(content);
        setNotice(path ? `Configuration saved to ${path}.` : "Configuration save cancelled.");
      } else {
        download(new Blob([content], { type: "application/json" }), "configuration.json");
        setNotice(
          "Configuration download requested. Check your browser downloads for configuration.json.",
        );
      }
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  const acceptSource = async () => {
    if (!review) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await inspect(JSON.stringify(review.document), true);
      if (window.pywebview) {
        const path = await window.pywebview.api.save_configuration_source(review.content_hash);
        setNotice(
          path
            ? `Source saved to ${path}. Extract into a new folder, then continue in Application settings. It is not registered or runnable yet.`
            : "Source save cancelled. The original remains available; no source was registered.",
        );
      } else {
        const response = await checked(
          await fetch(
            `${root}/imports/${encodeURIComponent(review.content_hash)}/source?accepted=true`,
            { method: "POST" },
          ),
        );
        download(await response.blob(), "author-source.zip");
        setNotice(
          "Source download requested. Check your browser downloads, extract to a new folder, then continue in Application settings. It is not registered or runnable yet.",
        );
      }
      await cache.invalidateQueries({ queryKey: ["configuration-imports"] });
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className="grid gap-3 rounded-lg border border-line bg-panel p-4"
      aria-label="Share and import configuration"
    >
      <h3>Share and import configuration</h3>
      <p>
        Share saved parameter definitions and optional initial values, setup and retained source. No
        acquired run is required. Copies use normal parameter and setup editors below.
      </p>
      <div className="flex gap-3">
        <button
          onClick={() => {
            setExporting(true);
            setExported(undefined);
          }}
        >
          Export configuration…
        </button>
        <label>
          Import configuration file{" "}
          <input
            type="file"
            accept=".json,application/json"
            disabled={busy || saveCopy.isPending}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void readFile(file);
              event.target.value = "";
            }}
          />
        </label>
      </div>
      {exporting && (
        <fieldset
          disabled={exportingMutation.isPending}
          className="grid gap-3 border border-line p-3"
        >
          <legend>Choose saved content to share</legend>
          <p>
            Unsaved edits are not included. Save them in the normal editor first, or choose an
            existing saved version.
          </p>
          <label>
            Parameter version{" "}
            <select
              value={version}
              onChange={(event) => {
                setVersion(event.target.value);
                setExported(undefined);
              }}
            >
              <option value="">Choose a version</option>
              {versions.data?.items.map((item) => (
                <option key={item.id}>{item.id}</option>
              ))}
            </select>
          </label>
          <label>
            File label{" "}
            <input
              value={label}
              onChange={(event) => {
                setLabel(event.target.value);
                setExported(undefined);
              }}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={values}
              onChange={(event) => {
                setValues(event.target.checked);
                setExported(undefined);
              }}
            />
            Include current values as initial inputs (not transferred calibration)
          </label>
          <label>
            Setup (optional){" "}
            <select
              value={setup}
              onChange={(event) => {
                setSetup(event.target.value);
                setExported(undefined);
              }}
            >
              <option value="">Parameters only</option>
              {setups.data?.items.map((item) => (
                <option key={item.id}>{item.id}</option>
              ))}
            </select>
          </label>
          <label>
            Retained source (optional){" "}
            <select
              value={workspace}
              onChange={(event) => {
                setWorkspace(event.target.value);
                setExported(undefined);
              }}
            >
              <option value="">Use recipient’s existing source</option>
              {sources.data?.items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          {workspace && (
            <p>
              {sourceState.data?.active
                ? "Includes the retained source revision, not unsaved file edits."
                : "No retained revision available. Prepare trusted author code in Experiments first; acquiring data is not required."}
            </p>
          )}
          <p>
            Device connection addresses and SDK installation are not exported as device bindings.
            Review source files and custom setup content for embedded machine information.
          </p>
          <div className="flex gap-3">
            <button disabled={exportingMutation.isPending} onClick={() => setExporting(false)}>
              Cancel export
            </button>
            <button
              disabled={
                !version ||
                !label.trim() ||
                exportingMutation.isPending ||
                (!!workspace && !sourceState.data?.active)
              }
              onClick={() => exportingMutation.mutate()}
            >
              Review export
            </button>
          </div>
          {exportingMutation.error && <p role="alert">{errorMessage(exportingMutation.error)}</p>}
          {exported && (
            <>
              <Content document={exported} />
              <button disabled={busy} onClick={() => void saveExport()}>
                Save configuration file
              </button>
            </>
          )}
        </fieldset>
      )}
      {imports.data && imports.data.length > 0 && (
        <details>
          <summary>Previously imported originals (latest 100)</summary>
          {imports.data.map((item) => (
            <div key={item.content_hash}>
              <button
                disabled={busy || saveCopy.isPending}
                onClick={async () => {
                  setBusy(true);
                  try {
                    openReview(
                      await apiData(
                        apiClient.GET("/api/v1/configuration-exchange/imports/{content_hash}", {
                          params: { path: { content_hash: item.content_hash } },
                        }),
                      ),
                    );
                    saveCopy.reset();
                  } catch (problem) {
                    setError(errorMessage(problem));
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {item.label}
              </button>
              <span>
                {" "}
                {item.derivations.map((entry) => entry.branch.name).join(", ") ||
                  "Kept for review; no copy created"}
              </span>
            </div>
          ))}
        </details>
      )}
      {review && (
        <fieldset className="grid gap-3 border border-line p-3">
          <legend>Inspect received configuration</legend>
          <Content document={review.document} />
          {review.notices.map((message) => (
            <p key={message}>{message}</p>
          ))}
          <details>
            <summary>Original identity and content</summary>
            <p>
              {review.document.origin_store} · {review.content_hash}
            </p>
            <pre className="max-h-80 overflow-auto">{JSON.stringify(review.document, null, 2)}</pre>
          </details>
          {!receipt && (
            <>
              <label>
                Name for my new parameter branch{" "}
                <input
                  value={name}
                  disabled={saveCopy.isPending}
                  onChange={(event) => {
                    setName(event.target.value);
                    saveCopy.reset();
                  }}
                />
              </label>
              <p>
                Creates a new branch and version. An existing name is rejected; nothing is
                overwritten. The original stays unchanged.
              </p>
              {review.document.setup && (
                <>
                  <label>
                    <input
                      type="checkbox"
                      checked={includeSetup}
                      disabled={saveCopy.isPending}
                      onChange={(event) => {
                        setIncludeSetup(event.target.checked);
                        saveCopy.reset();
                      }}
                    />
                    Also create my setup
                  </label>
                  {!includeSetup && (
                    <p>
                      Only parameters will be created. The original setup remains available for
                      later review. Adding it later requires another named copy with explicit local
                      bindings.
                    </p>
                  )}
                  {includeSetup &&
                    review.document.setup.definition.instruments.map((role) => (
                      <label key={role.id}>
                        Local device for {role.id}
                        <select
                          value={bindings[role.id] ?? ""}
                          disabled={saveCopy.isPending}
                          onChange={(event) => {
                            setBindings({ ...bindings, [role.id]: event.target.value });
                            saveCopy.reset();
                          }}
                        >
                          <option value="">Choose explicitly</option>
                          {devices.data?.items
                            .filter((item) => item.device.state !== "retired")
                            .map((item) => (
                              <option key={item.device.id} value={item.device.id}>
                                {item.device.label}
                              </option>
                            ))}
                        </select>
                      </label>
                    ))}
                  {includeSetup && (
                    <p>
                      Missing a device? Keep the original for later, then register it in{" "}
                      <a href="#instruments">Devices and drivers</a>.
                      <button onClick={() => void devices.refetch()}>Refresh local devices</button>
                      Import never connects a device.
                    </p>
                  )}
                </>
              )}
              <div className="flex gap-3">
                <button
                  disabled={busy || saveCopy.isPending}
                  onClick={() => {
                    clearReview();
                    saveCopy.reset();
                  }}
                >
                  Cancel inspection
                </button>
                <button disabled={busy || saveCopy.isPending} onClick={() => void keep()}>
                  Keep original for later
                </button>
                <button
                  disabled={
                    !name.trim() ||
                    !operator.trim() ||
                    busy ||
                    saveCopy.isPending ||
                    (includeSetup &&
                      (review.document.setup?.definition.instruments ?? []).some(
                        (role) => !bindings[role.id],
                      ))
                  }
                  onClick={() => saveCopy.mutate()}
                >
                  Create my copy
                </button>
              </div>
            </>
          )}
          {saveCopy.error && (
            <p role="alert">
              {errorMessage(saveCopy.error)}{" "}
              {saveCopy.error instanceof ApiError && saveCopy.error.status
                ? "The request was rejected. Review the name and local bindings before retrying."
                : "The outcome is not confirmed. Retry the unchanged request or reopen the original to check saved copies."}
            </p>
          )}
          {receipt && (
            <div role="status">
              <p>
                Created parameter branch “{receipt.branch.name}”. Its saved version is selected in
                Parameter versions below. Finish any existing unsaved draft first, then choose Edit
                a copy to change and save the imported version.
              </p>
              {receipt.setup && (
                <p>
                  Created setup “{receipt.setup.resolution.definition_id}”. Select it in Experiment
                  setups below to edit its mappings.
                </p>
              )}
              {receipt.setup_pending && (
                <p>
                  Setup not imported. To import it later, reopen this original, choose local devices
                  and create another named copy including the setup.
                </p>
              )}
              <p>
                No calibration qualification was transferred. Use a fresh normal experiment preview
                before running.
              </p>
              <button
                onClick={() => {
                  clearReview();
                  saveCopy.reset();
                }}
              >
                Done
              </button>
            </div>
          )}
          {review.document.source && (
            <section aria-label="Source next steps">
              <h4>Source is not ready to run</h4>
              <p>
                Review the files and dependencies above. Accepting downloads an inert archive; it
                does not register code, prepare dependencies, load experiments or create a runnable
                plan.
              </p>
              <button disabled={busy || saveCopy.isPending} onClick={() => void acceptSource()}>
                Accept source and download…
              </button>
              <ol>
                <li>Extract the downloaded archive into a new folder.</li>
                <li>
                  Open <a href="#settings">Application settings</a>, add that folder and choose
                  Execution Python, or explicitly prepare its pyproject.toml environment.
                </li>
                <li>
                  In Experiments, select that source and your local parameters/setup. Review trusted
                  loading and obtain a fresh preview before saving a plan or running.
                </li>
              </ol>
              <p>
                Until those steps succeed, this import supplies editable inputs only, not a reusable
                runnable method.
              </p>
            </section>
          )}
        </fieldset>
      )}
      {busy && <p role="status">Checking or saving…</p>}
      {notice && <p role="status">{notice}</p>}
      {(error ||
        imports.error ||
        devices.error ||
        versions.error ||
        setups.error ||
        sources.error ||
        sourceState.error) && (
        <p role="alert">
          {error ||
            errorMessage(
              imports.error ??
                devices.error ??
                versions.error ??
                setups.error ??
                sources.error ??
                sourceState.error,
            )}
        </p>
      )}
    </section>
  );
}

function Content({ document }: { document: Document }) {
  return (
    <section aria-label="Selected configuration content">
      <h4>{document.label}</h4>
      <p>
        {document.catalog.definitions?.length ?? 0} parameter definitions ·{" "}
        {document.values_included ? "Initial values included" : "Definitions only; values unknown"}
      </p>
      <ul>
        {(document.catalog.definitions ?? []).map((definition) => (
          <li key={definition.id}>
            <strong>{definition.id}</strong>
            {definition.description ? ` — ${definition.description}` : ""}
          </li>
        ))}
      </ul>
      <details>
        <summary>Parameter schema and values</summary>
        <pre className="max-h-64 overflow-auto">
          {JSON.stringify({ catalog: document.catalog, parameters: document.parameters }, null, 2)}
        </pre>
      </details>
      {document.setup && (
        <details>
          <summary>Setup {document.setup.id} — requires local review</summary>
          <pre className="max-h-64 overflow-auto">
            {JSON.stringify(document.setup.definition, null, 2)}
          </pre>
        </details>
      )}
      {document.source && (
        <details>
          <summary>Attached source files and dependencies</summary>
          <ul>
            {Object.entries(document.source.files).map(([name, encoded]) => (
              <li key={name}>
                <details>
                  <summary>{name}</summary>
                  <SourceText encoded={encoded} />
                </details>
              </li>
            ))}
          </ul>
          <p>
            Python {document.source.manifest.python}; requirements:{" "}
            {(document.source.manifest.import_requirements ?? []).join(", ") ||
              "No explicit requirements recorded"}
          </p>
          <p>
            File contents are retained without execution. No static reconstruction of arbitrary
            methods is promised.
          </p>
        </details>
      )}
    </section>
  );
}

function SourceText({ encoded }: { encoded: string }) {
  if (encoded.length > 512 * 1024)
    return <p>Large file; inspect the downloaded archive before trusted loading.</p>;
  let content: string;
  try {
    const bytes = Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
    content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    if (content.includes("\0")) throw new Error("Binary content");
  } catch {
    return <p>Binary file; inspect the downloaded archive before trusted loading.</p>;
  }
  return <pre className="max-h-64 overflow-auto whitespace-pre-wrap">{content}</pre>;
}
