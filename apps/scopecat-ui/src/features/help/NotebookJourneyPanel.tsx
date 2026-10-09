import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { type LessonTopic, useDesktopAvailable } from "../application/DesktopSession";
import { navigate, navigateLink, useLocationUrl } from "../../lib/navigation";
import { primaryButton, secondaryButton } from "../../ui/styles";

const lessons: Record<LessonTopic, { title: string; description: string }> = {
  parameters: {
    title: "Parameters and scans",
    description:
      "Explore a seven-point synthetic scan, edit its Python source and parameters, and compare retained results in this application.",
  },
  groups: {
    title: "Grouped analysis and history",
    description:
      "Scan two synthetic curves, analyze each group, and reopen their saved analysis without collecting again.",
  },
  refresh: {
    title: "Edit and refresh experiments",
    description:
      "Save Python source edits, preview a new request using the updated source, and add an experiment while keeping earlier runs available.",
  },
  compute: {
    title: "Mean IQ and typed results",
    description: "Compare synthetic IQ results and reopen retained data with typed result readers.",
  },
  calibration: {
    title: "Parameter calibration and recovery",
    description:
      "Submit a synthetic calibration procedure and inspect its correction, verification, and publication result.",
  },
  "joint-calibration": {
    title: "Joint calibration and coupled checks",
    description:
      "Compare individual corrections with a joint proposal and inspect coupled verification.",
  },
  "task-calibration": {
    title: "Background calibration and publication",
    description:
      "Start a bounded calibration task and inspect its retained evidence and verified publication outcome.",
  },
};

function lessonAt(location: URL): LessonTopic {
  const selected = location.searchParams.get("lesson");
  return selected && Object.hasOwn(lessons, selected) ? (selected as LessonTopic) : "parameters";
}

export function NotebookJourneyPanel({ reachable }: { reachable: boolean }) {
  const topic = lessonAt(useLocationUrl());
  // URL/history navigation must reset transient operation state just like the selector.
  return <NotebookJourneyCourse key={topic} topic={topic} reachable={reachable} />;
}

function NotebookJourneyCourse({ topic, reachable }: { topic: LessonTopic; reachable: boolean }) {
  const desktop = useDesktopAvailable();
  const native = desktop ? window.pywebview?.api : undefined;
  const client = useQueryClient();
  const active = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const stillSelected = () => active.current && lessonAt(new URL(window.location.href)) === topic;
  const [parent, setParent] = useState<string>();
  const status = useQuery({
    queryKey: ["notebook-journey", topic],
    queryFn: () => native!.notebook_journey(topic),
    enabled: !!native,
    refetchInterval: 1_000,
  });
  const open = useMutation({
    mutationFn: (requested: LessonTopic) => native!.open_lesson_notebook(requested),
  });
  const prepare = useMutation({
    mutationFn: (request: { topic: LessonTopic; parent?: string; repair?: boolean }) =>
      request.repair
        ? native!.prepare_notebook_journey(request.parent, request.topic, true)
        : native!.prepare_notebook_journey(request.parent, request.topic),
    onSuccess: (journey, request) => {
      client.setQueryData(["notebook-journey", request.topic], { state: "ready", journey });
      if (stillSelected()) open.mutate(request.topic);
    },
    onSettled: (_journey, _error, request) =>
      client.invalidateQueries({ queryKey: ["notebook-journey", request.topic] }),
  });
  const choose = useMutation({
    mutationFn: () => native!.choose_directory(),
    onSuccess: (directory) => {
      if (directory && stillSelected()) setParent(directory);
    },
  });
  const journey = status.data?.journey;
  const phase = status.data?.state;
  const previousPhase = useRef(phase);
  const { isError: preparationFailed, reset: resetPreparation } = prepare;
  useEffect(() => {
    if (
      preparationFailed &&
      previousPhase.current === "retryable" &&
      (phase === "preparing" || phase === "ready")
    ) {
      resetPreparation();
    }
    previousPhase.current = phase;
  }, [phase, preparationFailed, resetPreparation]);
  const preparing = prepare.isPending || status.data?.state === "preparing";
  const busy = preparing || open.isPending || choose.isPending;
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
            const next = new URL(window.location.href);
            next.searchParams.set("lesson", event.target.value);
            navigate(next, { replace: true });
          }}
          className="rounded border border-line bg-panel p-2"
        >
          {Object.entries(lessons).map(([value, lesson]) => (
            <option key={value} value={value}>
              {lesson.title}
            </option>
          ))}
        </select>
      </label>
      <p>
        {lessons[topic].description} No devices are needed. Each course keeps its own code folder
        and teaching parameters in this application’s data space.
      </p>
      <p>
        Help prepares a code folder and Python environment. Edit the Notebook in VS Code with the
        Python and Jupyter extensions; choose the folder’s .venv kernel. Only explicitly running
        acquisition, procedure, or task cells starts new work. To read existing results after a
        restart, use the Notebook’s history section.
      </p>
      <p>
        Return to this course in Help to continue its existing folder. Closing the Notebook or
        application keeps saved files and results; save editor changes before closing. After a
        kernel restart, run the connection and history cells, not Run All.
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
                prepare.mutate({ topic, parent });
              }}
            >
              {preparing
                ? "Preparing Notebook…"
                : journey?.ready
                  ? `Continue ${topic} Notebook`
                  : journey
                    ? "Retry preparation"
                    : `Start ${topic} Notebook`}
            </button>
            {!journey && status.isSuccess && !preparing && (
              <button className={secondaryButton} disabled={busy} onClick={() => choose.mutate()}>
                Choose another save location…
              </button>
            )}
          </div>
          {!journey && status.isSuccess && !preparing && (
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
          {preparing
            ? "Preparing code and Python; this may take a few minutes. Preparation does not start measurements."
            : choose.isPending
              ? "Choosing save location…"
              : "Opening editor…"}
        </p>
      )}
      {open.isSuccess && !busy && (
        <p role="status">Editor open requested. Select the displayed Python kernel in VS Code.</p>
      )}
      {journey && (
        <div className="grid gap-1 break-all">
          <p>
            {preparing
              ? "Preparation is running. This folder will be available when it finishes."
              : journey.ready
                ? "Preparation saved. Continue checks this folder and opens its Notebook."
                : "Preparation unfinished. Retry uses the same folder and keeps your edits."}
          </p>
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
            <a className="underline" href="#runs" onClick={navigateLink}>
              Runs
            </a>{" "}
            to reopen a result without collecting again. Manage retained runs in Data; keep your
            edited files when removing records.
          </p>
          <details>
            <summary>Repair Notebook environments</summary>
            <p>
              If imports fail or the Python environment is incomplete, close this folder’s notebooks
              and Python terminals, then repair. Repair keeps your edited files and saved results,
              retains the previous local environment, and prepares matching dependencies for the
              notebook and future background work. Existing tasks keep their original execution
              environment. Reinstall any packages you added locally afterwards.
            </p>
            <button
              className={secondaryButton}
              disabled={!reachable || busy || status.isError}
              onClick={() => {
                open.reset();
                prepare.mutate({ topic, repair: true });
              }}
            >
              Repair environments and open Notebook
            </button>
          </details>
          {journey.ready && (
            <>
              <p>
                Keep writing your own experiments in this ordinary author folder: edit source in
                <code> src/my_experiment</code>, then construct a new request as shown in the
                Notebook. Earlier runs keep their original source and parameters.
              </p>
              <p>
                <a
                  className="underline"
                  href={`?lesson=${topic}&source=${encodeURIComponent(journey.directory)}#settings`}
                  onClick={navigateLink}
                >
                  Manage this code folder in Settings
                </a>{" "}
                to check its registered Python or repair a missing local environment. For a separate
                experiment folder, use Author code → New code folder there. No copying or publishing
                step is needed to keep using this folder.
              </p>
            </>
          )}
        </div>
      )}
      {error && <p role="alert">{error instanceof Error ? error.message : String(error)}</p>}
    </section>
  );
}
