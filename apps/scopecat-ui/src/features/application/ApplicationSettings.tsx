import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ProjectHealth } from "../../types";
import { secondaryButton } from "../../ui/styles";
import { errorMessage } from "../../lib/presentation";
import type { InstallationStatus } from "./DesktopSession";

const section = "grid gap-3 rounded-lg border border-line bg-panel p-4";

export function ApplicationSettings({ health }: { health?: ProjectHealth }) {
  const [native, setNative] = useState(window.pywebview?.api);
  const [source, setSource] = useState("");
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
      action: "source" | "restart" | "dependencies" | "client" | "rebuild-client",
    ) => {
      if (!native) return undefined;
      if (action === "source") await native.register_source(source.trim());
      else if (action === "dependencies") return native.prepare_author_environment(source.trim());
      else if (action === "client") return native.create_author_environment(source.trim());
      else if (action === "rebuild-client")
        return native.create_author_environment(source.trim(), true);
      else await native.restart();
      return undefined;
    },
    onSuccess: () => {
      void status.refetch();
    },
  });
  const busy = operation.isPending;
  const dataRoot = health?.details.data_root;
  return (
    <section className="grid max-w-4xl gap-4 p-6" aria-labelledby="application-settings-heading">
      <h2 id="application-settings-heading" className="text-lg font-semibold">
        Application settings
      </h2>
      {operation.error && <p role="alert">{errorMessage(operation.error)}</p>}
      {operation.data && <p role="status">{operation.data}</p>}
      {status.error && <p role="alert">{errorMessage(status.error)}</p>}
      <section className={section}>
        <h3 className="font-semibold">Software and data</h3>
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
            <h3 className="font-semibold">Author code</h3>
            <p>
              Register an existing source folder, then open it normally in VS Code. All registered
              folders use this application. Registration prepares declared dependencies and restarts
              the application; finish active work first.
            </p>
            <label className="grid gap-1">
              Author directory
              <input
                value={source}
                onChange={(event) => setSource(event.target.value)}
                disabled={busy}
                placeholder="Folder containing scopecat.toml"
              />
            </label>
            <button
              className={secondaryButton}
              disabled={busy || !source.trim()}
              onClick={() => operation.mutate("source")}
            >
              Stop, register source and reopen
            </button>
            <p>
              Create a local Python environment for this folder, then select its .venv in VS Code.
              It includes the Scopecat Python API and a notebook kernel. Install your own analysis
              packages there with pip; this does not change the application. Declare packages needed
              by background experiments in pyproject.toml, then prepare their execution environment.
              Existing tasks keep their original environment.
            </p>
            <button
              className={secondaryButton}
              disabled={busy || !source.trim()}
              onClick={() => operation.mutate("client")}
            >
              Create local Python environment
            </button>
            <button
              className={secondaryButton}
              disabled={busy || !source.trim()}
              onClick={() => operation.mutate("dependencies")}
            >
              Prepare background dependencies
            </button>
            <p>
              To repair local Python, close its terminals and notebook kernels first. Rebuilding
              preserves the previous environment separately and keeps your source files.
            </p>
            <button
              className={secondaryButton}
              disabled={busy || !source.trim()}
              onClick={() => operation.mutate("rebuild-client")}
            >
              Rebuild local Python environment
            </button>
          </section>
          <section className={section}>
            <h3 className="font-semibold">Restart application</h3>
            <p>
              Restart to recover the connection or apply changes to local settings. This stops
              active work and releases devices. Saved records and source files are retained.
            </p>
            <button
              className={secondaryButton}
              disabled={busy}
              onClick={() => operation.mutate("restart")}
            >
              Stop work and restart
            </button>
          </section>
        </>
      )}
      {busy && <p role="status">Working… You can keep this window open to see the result.</p>}
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
