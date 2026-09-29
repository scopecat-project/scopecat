import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ProjectHealth } from "../../types";
import { primaryButton, secondaryButton } from "../../ui/styles";
import { errorMessage } from "../../lib/presentation";
import { getDevices } from "../instruments/device-api";
import type { InstallationStatus } from "./DesktopSession";

const section = "grid gap-3 rounded-lg border border-line bg-panel p-4";

export function ApplicationSettings({ health }: { health?: ProjectHealth }) {
  const [native, setNative] = useState(window.pywebview?.api);
  const [directory, setDirectory] = useState("");
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
  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: ({ signal }) => getDevices(signal),
    enabled: !!native,
  });
  const operation = useMutation({
    mutationFn: async (
      action:
        | "prepare"
        | "apply"
        | "source"
        | "restart"
        | "recheck"
        | "dependencies"
        | "client"
        | "rebuild-client",
    ) => {
      if (!native) return undefined;
      if (action === "prepare") await native.prepare_update(directory.trim());
      else if (action === "apply") await native.apply_update();
      else if (action === "source") await native.register_source(source.trim());
      else if (action === "dependencies") return native.prepare_author_environment(source.trim());
      else if (action === "client") return native.create_author_environment(source.trim());
      else if (action === "rebuild-client")
        return native.create_author_environment(source.trim(), true);
      else if (action === "recheck") await native.requalify();
      else await native.restart();
      return undefined;
    },
    onSuccess: () => {
      void status.refetch();
    },
  });
  const candidate = status.data?.candidate;
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
        {status.data && <InstallationDetails installation={status.data.installation} />}
        <p>
          Updates keep scientific data and source folders. Reopening does not repeat measurements.
        </p>
        {!native && (
          <p>
            Open the Scopecat desktop application for local installation and source-folder changes.
            The command-line application entry offers the same operations.
          </p>
        )}
      </section>
      {native && (
        <>
          <section className={section}>
            <h3 className="font-semibold">Prepare an update</h3>
            <p>
              Choose a delivery prepared by your maintainer. Preparation checks the candidate
              without stopping current work or connecting devices. A failed preparation keeps the
              selected installation.
            </p>
            <label className="grid gap-1">
              Delivery directory
              <input
                value={directory}
                onChange={(event) => setDirectory(event.target.value)}
                disabled={busy}
                placeholder="Full directory path"
              />
            </label>
            <button
              className={secondaryButton}
              disabled={busy || !directory.trim()}
              onClick={() => operation.mutate("prepare")}
            >
              Prepare update
            </button>
            {candidate && (
              <>
                <h4 className="font-semibold">Prepared candidate</h4>
                <InstallationDetails installation={candidate} />
                <p>
                  Applying restarts this application and interrupts active work. Finish measurements
                  and release devices first. Your source folder's Python environment is kept
                  unchanged.
                </p>
                {devices.data?.items.map((item) => (
                  <p key={item.device.id}>
                    {item.device.label}: {item.availability}
                    {item.owner_id && ` · ${item.owner_kind} ${item.owner_id}`}
                  </p>
                ))}
                <button
                  className={primaryButton}
                  disabled={busy}
                  onClick={() => operation.mutate("apply")}
                >
                  Stop and apply prepared update
                </button>
              </>
            )}
          </section>
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
              Use the source folder's .venv for Python and notebooks. Installing packages there does
              not change this application. Declare packages needed by background experiments in
              pyproject.toml, then prepare their execution environment. Existing tasks keep their
              original environment.
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
            <h3 className="font-semibold">Connection recovery</h3>
            <p>
              Stop this application's recorded background process and reopen its workbench. This
              interrupts active work; other application homes are independent.
            </p>
            <button
              className={secondaryButton}
              disabled={busy}
              onClick={() => operation.mutate("restart")}
            >
              Stop and reopen application
            </button>
            <p>
              If local settings or a development capability changed in place, stop and recheck the
              selected environment before reopening. Failed qualification preserves its identity.
            </p>
            <button
              className={secondaryButton}
              disabled={busy}
              onClick={() => operation.mutate("recheck")}
            >
              Stop and recheck selected environment
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
