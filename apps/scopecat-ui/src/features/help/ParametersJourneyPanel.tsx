import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useDesktopAvailable } from "../application/DesktopSession";
import { primaryButton, secondaryButton } from "../../ui/styles";

export function ParametersJourneyPanel({ reachable }: { reachable: boolean }) {
  const desktop = useDesktopAvailable();
  const native = desktop ? window.pywebview?.api : undefined;
  const client = useQueryClient();
  const [parent, setParent] = useState<string>();
  const status = useQuery({
    queryKey: ["parameters-journey"],
    queryFn: () => native!.parameters_journey(),
    enabled: !!native,
  });
  const open = useMutation({ mutationFn: () => native!.open_parameters_notebook() });
  const prepare = useMutation({
    mutationFn: () => native!.prepare_parameters_journey(parent),
    onSuccess: (journey) => {
      client.setQueryData(["parameters-journey"], journey);
      open.mutate();
    },
    onSettled: () => client.invalidateQueries({ queryKey: ["parameters-journey"] }),
  });
  const choose = useMutation({
    mutationFn: () => native!.choose_directory(),
    onSuccess: (directory) => {
      if (directory) setParent(directory);
    },
  });
  const journey = status.data;
  const busy = prepare.isPending || open.isPending || choose.isPending;
  const error = prepare.error ?? open.error ?? choose.error ?? status.error;
  return (
    <section
      aria-labelledby="parameters-journey-heading"
      className="grid gap-3 rounded-lg border border-line bg-panel p-4"
    >
      <h3 id="parameters-journey-heading" className="font-semibold">
        Parameters and scans · Notebook
      </h3>
      <p>
        Explore a seven-point synthetic scan, edit its Python source and parameters, and compare
        retained results in this application. No devices are needed.
      </p>
      <p>
        Help prepares a code folder and Python environment. Edit the Notebook in VS Code with the
        Python and Jupyter extensions; choose the folder’s .venv kernel. Only the Notebook’s
        acquisition cell starts a new run.
      </p>
      {!native ? (
        <p>Open Help in the Scopecat desktop application to prepare this Notebook.</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            <button
              className={primaryButton}
              disabled={!reachable || busy || status.isPending || status.isError}
              onClick={() => {
                open.reset();
                prepare.mutate();
              }}
            >
              {prepare.isPending
                ? "Preparing Notebook…"
                : journey?.ready
                  ? "Continue parameters Notebook"
                  : journey
                    ? "Retry preparation"
                    : "Start parameters Notebook"}
            </button>
            {!journey && (
              <button className={secondaryButton} disabled={busy} onClick={() => choose.mutate()}>
                Choose another save location…
              </button>
            )}
          </div>
          {!journey && (
            <p>
              {parent
                ? `Save in: ${parent}`
                : "A new folder in your application home is used by default."}
            </p>
          )}
        </>
      )}
      {busy && (
        <p role="status">
          {prepare.isPending
            ? "Preparing code and Python; this may take a few minutes. No measurements are running."
            : "Opening editor…"}
        </p>
      )}
      {journey && (
        <div className="grid gap-1 break-all">
          <p>
            Code folder: <code>{journey.directory}</code>
          </p>
          <p>
            Notebook: <code>{journey.notebook}</code>
          </p>
          <p>
            Python kernel: <code>{journey.python}</code>
          </p>
          <p>
            Continue preserves edited files and saved parameters. Use the Notebook’s run link or{" "}
            <a className="underline" href="#history">
              History
            </a>{" "}
            to reopen a result without collecting again. Manage retained runs in Data; keep your
            edited files when removing records.
          </p>
        </div>
      )}
      {error && <p role="alert">{error instanceof Error ? error.message : String(error)}</p>}
    </section>
  );
}
