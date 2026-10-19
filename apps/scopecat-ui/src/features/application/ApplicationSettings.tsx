import { ActionRow, Notice, Button, Field, Input, Select } from "../../ui/Controls";
import { pageTitle, sectionTitle, pageContent, classes } from "../../ui/styles";
import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ProjectHealth } from "../../types";
import { errorMessage } from "../../lib/presentation";
import type { InstallationStatus } from "./DesktopSession";

const section =
  "grid min-w-0 gap-3 rounded-lg border border-line bg-panel p-4 [overflow-wrap:anywhere]";

export function ApplicationSettings({ health }: { health?: ProjectHealth }) {
  const [native, setNative] = useState(window.pywebview?.api);
  const [source, setSource] = useState(
    new URLSearchParams(window.location.search).get("source") ?? "",
  );
  const [creating, setCreating] = useState(false);
  const [interpreter, setInterpreter] = useState("");
  const [parent, setParent] = useState("");
  const [name, setName] = useState("experiments");
  useEffect(() => {
    const ready = () => setNative(window.pywebview?.api);
    window.addEventListener("pywebviewready", ready);
    return () => window.removeEventListener("pywebviewready", ready);
  }, []);
  const status = useQuery({
    queryKey: ["application-installation"],
    queryFn: () => native!.status(),
    enabled: !!native,
  });
  const operation = useMutation({
    mutationFn: async (
      action:
        | "source"
        | "environment"
        | "create"
        | "restart"
        | "backup"
        | "dependencies"
        | "client"
        | "rebuild-client",
    ) => {
      if (!native) return undefined;
      if (action === "create") return native.create_source(parent, name.trim());
      else if (action === "source") await native.register_source(source.trim(), interpreter.trim());
      else if (action === "environment")
        return native.select_source_environment(source.trim(), interpreter.trim());
      else if (action === "dependencies") return native.prepare_author_environment(source.trim());
      else if (action === "client") return native.create_author_environment(source.trim());
      else if (action === "rebuild-client")
        return native.create_author_environment(source.trim(), true);
      else if (action === "backup") await native.backup_application();
      else await native.restart();
      return undefined;
    },
    onSuccess: (_result, action) => {
      // Backup replaces this page after its bridge reply; do not start a new callback.
      if (action !== "backup") void status.refetch();
    },
  });
  const picker = useMutation({
    mutationFn: () => native!.choose_directory(),
    onSuccess: (path) => {
      if (path) {
        if (creating) setParent(path);
        else setSource(path);
      }
    },
  });
  const busy = operation.isPending || picker.isPending;
  const selected = status.data?.sources.find((item) => item.directory === source.trim());
  const dataRoot = health?.details.data_root;
  return (
    <section
      className={classes(pageContent, "max-w-4xl")}
      aria-labelledby="application-settings-heading"
    >
      <h2 id="application-settings-heading" className={pageTitle}>
        Application settings
      </h2>
      {operation.error && <Notice role="alert">{errorMessage(operation.error)}</Notice>}
      {picker.error && <Notice role="alert">{errorMessage(picker.error)}</Notice>}
      {operation.data && <Notice role="status">{operation.data}</Notice>}
      {status.error && <Notice role="alert">{errorMessage(status.error)}</Notice>}
      <section className={section}>
        <h3 className={sectionTitle}>Software and data</h3>
        {status.data && (
          <p>
            Application home: <code>{status.data.home}</code>
          </p>
        )}
        {typeof dataRoot === "string" && (
          <p>
            Scientific data: <code>{dataRoot}</code>
          </p>
        )}
        {status.data && (
          <details>
            <summary>Technical diagnostics</summary>
            <InstallationDetails installation={status.data.installation} />
          </details>
        )}
        <p>
          To update Scopecat, quit the application, install the new version, then reopen it. Startup
          uses the installed version directly. Updates keep scientific data and source folders.
          Reopening does not repeat measurements.
        </p>
        {native && (
          <>
            <p>
              Back up scientific records and retained source while idle. External code folders,
              Python environments and the application installer need separate copies. The service
              stays stopped until you reopen it from the backup result page.
            </p>
            <Button disabled={busy} onClick={() => operation.mutate("backup")}>
              Back up application data…
            </Button>
          </>
        )}
        {!native && (
          <p>
            Open the Scopecat desktop application to manage local source folders and Python
            environments.
          </p>
        )}
      </section>
      {native && (
        <>
          <section className={section}>
            <h3 className={sectionTitle}>Author code</h3>
            <p>
              Create a folder with a device-free example, or add existing Scopecat code. The
              notebook environment is for interactive editing in VS Code. A separate background
              environment validates and runs experiments. Both use this application's shared data;
              neither is the application's own Python.
            </p>
            <ActionRow>
              <Button disabled={busy} onClick={() => setCreating(true)}>
                New code folder
              </Button>
              <Button disabled={busy} onClick={() => setCreating(false)}>
                Use existing folder
              </Button>
            </ActionRow>
            {creating ? (
              <div className="grid gap-3">
                <Button disabled={busy} onClick={() => picker.mutate()}>
                  Choose save location…
                </Button>
                {parent && (
                  <p>
                    Save in: <code>{parent}</code>
                  </p>
                )}
                <Field>
                  New folder name
                  <Input
                    value={name}
                    disabled={busy}
                    onChange={(event) => setName(event.target.value)}
                  />
                </Field>
                <p>
                  Prepares a notebook environment and a separate background execution environment
                  for the new folder. Existing files are not replaced; no device connection or
                  measurement is started.
                </p>
                <Button
                  disabled={busy || !parent || !name.trim()}
                  onClick={() => operation.mutate("create")}
                >
                  Create folder and prepare Python
                </Button>
              </div>
            ) : (
              <>
                {!!status.data?.sources.length && (
                  <Field>
                    Your code folders
                    <Select
                      className="min-w-0 max-w-full"
                      value={selected ? source.trim() : ""}
                      disabled={busy}
                      onChange={(event) => setSource(event.target.value)}
                    >
                      <option value="">Choose a folder</option>
                      {status.data.sources.map((item) => (
                        <option key={item.directory} value={item.directory}>
                          {item.directory}
                        </option>
                      ))}
                    </Select>
                  </Field>
                )}
                <Button disabled={busy} onClick={() => picker.mutate()}>
                  Browse for code folder…
                </Button>
                <Field>
                  Author directory
                  <Input
                    value={source}
                    onChange={(event) => setSource(event.target.value)}
                    disabled={busy}
                    placeholder="Folder containing scopecat.toml"
                  />
                </Field>
                {!selected && (
                  <>
                    <Field>
                      Execution Python
                      <Input
                        value={interpreter}
                        disabled={busy}
                        onChange={(event) => setInterpreter(event.target.value)}
                        placeholder="Full path to python or python.exe"
                      />
                    </Field>
                    <p className="text-sm text-text-dim">
                      Use the Python environment for background validation and execution.
                      Registering an existing folder does not install packages.
                    </p>
                    <Button
                      disabled={busy || !source.trim() || !interpreter.trim()}
                      onClick={() => operation.mutate("source")}
                    >
                      Add code folder
                    </Button>
                  </>
                )}
                {selected && (
                  <Notice role="status">
                    Code folder registered. Open it in VS Code.
                    {selected.python ? (
                      <>
                        {" "}
                        Select its .venv for Python or notebook cells. Start with{" "}
                        <code>notebooks/02_edit_scan.py</code> in a new example folder.
                      </>
                    ) : (
                      <> Create its notebook environment below.</>
                    )}
                  </Notice>
                )}
                <p>
                  Create a local Python environment for this folder, then select its .venv in VS
                  Code. It includes the Scopecat Python API and a notebook kernel. Install your own
                  analysis packages there with pip; this does not change the application. Declare
                  packages needed by background experiments in pyproject.toml, then prepare their
                  execution environment. Existing tasks keep their original environment.
                </p>
                <Button
                  disabled={busy || !selected || !!selected.python}
                  onClick={() => operation.mutate("client")}
                >
                  Create local Python environment
                </Button>
                <Button
                  disabled={busy || !source.trim()}
                  onClick={() => operation.mutate("dependencies")}
                >
                  Prepare execution environment from pyproject.toml
                </Button>
                <details>
                  <summary>Inspect or change Python environments</summary>
                  {selected && (
                    <div className="mt-3 grid gap-3">
                      <p>
                        Configured background execution interpreter:{" "}
                        <code>{selected.execution_python}</code>. This is a saved configuration, not
                        a running or health status.
                      </p>
                      {selected.python && (
                        <p>
                          Notebook interpreter: <code>{selected.python}</code>
                        </p>
                      )}
                      <Field>
                        Execution Python
                        <Input
                          value={interpreter}
                          disabled={busy}
                          onChange={(event) => setInterpreter(event.target.value)}
                          placeholder="Full path to python or python.exe"
                        />
                      </Field>
                      <Button
                        disabled={busy || !interpreter.trim()}
                        onClick={() => operation.mutate("environment")}
                      >
                        Use this execution Python
                      </Button>
                    </div>
                  )}
                  <h4 className="mt-3 font-semibold">Repair notebook environment</h4>
                  <p>
                    To repair local Python, close its terminals and notebook kernels first.
                    Rebuilding preserves the previous environment separately and keeps your source
                    files.
                  </p>
                  <Button
                    disabled={busy || !selected}
                    onClick={() => operation.mutate("rebuild-client")}
                  >
                    Rebuild local Python environment
                  </Button>
                </details>
              </>
            )}
          </section>
          <section className={section}>
            <h3 className={sectionTitle}>Restart application</h3>
            <p>
              Restart to recover the connection or apply changes to local settings. This stops
              active work and releases devices. Saved records and source files are retained.
            </p>
            <Button disabled={busy} onClick={() => operation.mutate("restart")}>
              Stop work and restart
            </Button>
          </section>
        </>
      )}
      {busy && (
        <Notice role="status">Working… You can keep this window open to see the result.</Notice>
      )}
    </section>
  );
}

function InstallationDetails({ installation }: { installation: InstallationStatus }) {
  return (
    <dl className="grid gap-2 break-all">
      <div>
        <dt>Application Python (managed; not a notebook kernel)</dt>
        <dd>
          <code>{installation.python}</code>
        </dd>
      </div>
      <div>
        <dt>Runtime</dt>
        <dd>{installation.environment.python}</dd>
      </div>
      <div>
        <dt>Capability identity</dt>
        <dd>
          <code>{installation.adapter_identity ?? "No optional capability selected"}</code>
        </dd>
      </div>
    </dl>
  );
}
