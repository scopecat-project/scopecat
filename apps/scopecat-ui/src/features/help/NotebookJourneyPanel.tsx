import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { type LessonTopic, useDesktopAvailable } from "../application/DesktopSession";
import { primaryButton, secondaryButton } from "../../ui/styles";

export function NotebookJourneyPanel({ reachable }: { reachable: boolean }) {
  const desktop = useDesktopAvailable();
  const native = desktop ? window.pywebview?.api : undefined;
  const client = useQueryClient();
  const [topic, setTopic] = useState<LessonTopic>("parameters");
  const [parent, setParent] = useState<string>();
  const status = useQuery({
    queryKey: ["notebook-journey", topic],
    queryFn: () => native!.notebook_journey(topic),
    enabled: !!native,
  });
  const open = useMutation({ mutationFn: () => native!.open_lesson_notebook(topic) });
  const prepare = useMutation({
    mutationFn: () => native!.prepare_notebook_journey(parent, topic),
    onSuccess: (journey) => {
      client.setQueryData(["notebook-journey", topic], journey);
      open.mutate();
    },
    onSettled: () => client.invalidateQueries({ queryKey: ["notebook-journey", topic] }),
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
      aria-labelledby="notebook-journey-heading"
      className="grid gap-3 rounded-lg border border-line bg-panel p-4"
    >
      <h3 id="notebook-journey-heading" className="font-semibold">
        Learn with Notebooks
      </h3>
      <label className="grid gap-1">
        Course
        <select
          value={topic}
          disabled={busy}
          onChange={(event) => {
            setTopic(event.target.value as LessonTopic);
            setParent(undefined);
            prepare.reset();
            open.reset();
            choose.reset();
          }}
          className="rounded border border-line bg-panel p-2"
        >
          <option value="parameters">Parameters and scans</option>
          <option value="groups">Grouped analysis and history</option>
        </select>
      </label>
      <p>
        {topic === "parameters"
          ? "Explore a seven-point synthetic scan, edit its Python source and parameters, and compare retained results in this application."
          : "Scan two synthetic curves, analyze each group, and reopen their saved analysis without collecting again."}{" "}
        No devices are needed. Each course keeps its own code folder and teaching parameters in this
        application’s data space.
      </p>
      <p>
        Help prepares a code folder and Python environment. Edit the Notebook in VS Code with the
        Python and Jupyter extensions; choose the folder’s .venv kernel. Only explicitly running the
        Notebook’s acquisition cell starts a new run.
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
                  ? `Continue ${topic} Notebook`
                  : journey
                    ? "Retry preparation"
                    : `Start ${topic} Notebook`}
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
            Continue preserves edited files and saved parameters. Use{" "}
            <a className="underline" href="#runs">
              Runs
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
