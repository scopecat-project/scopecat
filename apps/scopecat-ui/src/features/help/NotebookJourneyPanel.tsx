import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { type LessonTopic, useDesktopAvailable } from "../application/DesktopSession";
import { navigate, navigateLink, useLocationUrl } from "../../lib/navigation";
import { primaryButton, secondaryButton } from "../../ui/styles";

const lessons: Record<LessonTopic, { title: string; goal: string; description: string }> = {
  parameters: {
    title: "Parameters and scans",
    goal: "Run, view a result, edit Python and run again.",
    description:
      "Start here: run a seven-point synthetic scan, view it in this application, then edit Python and compare a new run with the original.",
  },
  refresh: {
    title: "Edit and refresh experiments",
    goal: "Compare source edits and add an experiment.",
    description:
      "After parameters and scans: save source edits, compare old and new requests, and add an experiment while keeping earlier runs available.",
  },
  compute: {
    title: "Mean IQ and typed results",
    goal: "Calculate mean IQ and read typed results.",
    description:
      "Optional after basic scans; basic NumPy helps. Compare per-shot IQ with its mean and reopen the exact saved run with typed readers.",
  },
  groups: {
    title: "Grouped analysis and history",
    goal: "Analyze curves by group and reopen saved analysis.",
    description:
      "Optional after basic scans: analyze two synthetic curves by group, add a third, and reopen the saved analysis without collecting again.",
  },
  calibration: {
    title: "Parameter calibration and recovery",
    goal: "Verify a proposed correction before adopting it.",
    description:
      "Advanced; first understand saved parameters and analysis. Compare a verified correction that is published with a rejected correction that leaves its branch unchanged.",
  },
  "joint-calibration": {
    title: "Joint calibration and coupled checks",
    goal: "Check whether combined corrections work together.",
    description:
      "After parameter calibration: explain why individual corrections can pass while their combination fails, and inspect the coupled decision.",
  },
  "task-calibration": {
    title: "Background calibration and publication",
    goal: "Distinguish task progress from verified publication.",
    description:
      "After joint calibration: compare accepted, rejected and branch-conflict tasks; distinguish finished stages from verified publication.",
  },
};

const courseGroups: { title: string; hint: string; topics: LessonTopic[] }[] = [
  { title: "Basics", hint: "Start here, then keep authoring.", topics: ["parameters", "refresh"] },
  { title: "Optional", hint: "Choose the skill you need next.", topics: ["compute", "groups"] },
  {
    title: "Advanced",
    hint: "Combine verified corrections.",
    topics: ["calibration", "joint-calibration", "task-calibration"],
  },
];

function lessonAt(location: URL): LessonTopic {
  const selected = location.searchParams.get("lesson");
  return selected && Object.hasOwn(lessons, selected) ? (selected as LessonTopic) : "parameters";
}

export function NotebookJourneyPanel({ reachable }: { reachable: boolean }) {
  const topic = lessonAt(useLocationUrl());
  const [busy, setBusy] = useState(false);
  return (
    <section
      aria-labelledby="notebook-journey-heading"
      className="grid gap-3 rounded-lg border border-line bg-panel p-4"
    >
      <h3 id="notebook-journey-heading" className="font-semibold">
        Learn with Notebooks
      </h3>
      <p>
        Begin with Parameters and scans, then continue editing. Choose calculation or grouped
        analysis as needed; calibration is an advanced route. You do not need all seven courses or
        another course’s data.
      </p>
      <CourseBrowser topic={topic} busy={busy} />
      <NotebookJourneyCourse
        key={topic}
        topic={topic}
        reachable={reachable}
        onBusyChange={setBusy}
      />
    </section>
  );
}

function CourseBrowser({ topic, busy }: { topic: LessonTopic; busy: boolean }) {
  const courseId = useId();
  return (
    <fieldset disabled={busy} className="min-w-0">
      <legend className="mb-3 font-semibold">Choose a course</legend>
      <div className="grid gap-4 lg:grid-cols-3">
        {courseGroups.map((group) => (
          <div key={group.title} className="min-w-0">
            <h4 className="font-semibold">{group.title}</h4>
            <p className="mb-2 text-text-dim">{group.hint}</p>
            <div className="grid gap-2">
              {group.topics.map((value) => (
                <label key={value} className="block">
                  <input
                    type="radio"
                    name={`${courseId}-course`}
                    value={value}
                    checked={topic === value}
                    aria-label={lessons[value].title}
                    aria-describedby={`${courseId}-${value}-goal`}
                    onChange={() => {
                      const next = new URL(window.location.href);
                      next.searchParams.set("lesson", value);
                      navigate(next, { replace: true });
                    }}
                    className="peer sr-only"
                  />
                  <span className="grid min-h-24 cursor-pointer gap-1 rounded-md border border-line bg-panel-soft p-3 peer-checked:border-accent peer-checked:bg-panel-strong peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent peer-disabled:cursor-not-allowed peer-disabled:opacity-60">
                    <span className="flex items-start justify-between gap-2 font-semibold">
                      {lessons[value].title}
                      {topic === value && (
                        <span className="text-accent" aria-hidden="true">
                          ●
                        </span>
                      )}
                    </span>
                    <span id={`${courseId}-${value}-goal`} className="text-text-soft">
                      {lessons[value].goal}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </div>
        ))}
      </div>
    </fieldset>
  );
}

function NotebookJourneyCourse({
  topic,
  reachable,
  onBusyChange,
}: {
  topic: LessonTopic;
  reachable: boolean;
  onBusyChange: (busy: boolean) => void;
}) {
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
  useEffect(() => {
    onBusyChange(busy);
    return () => onBusyChange(false);
  }, [busy, onBusyChange]);
  const error = prepare.error ?? open.error ?? choose.error ?? status.error;
  return (
    <section className="grid gap-3 border-t border-line pt-4" aria-label="Selected course">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-semibold">{lessons[topic].title}</h4>
        <span className="rounded border border-line px-2 py-1 text-text-soft">
          {!native
            ? "Desktop required"
            : preparing
              ? "Preparing"
              : status.isError
                ? "Status unavailable"
                : status.isPending
                  ? "Checking preparation…"
                  : journey?.ready
                    ? "Ready to continue"
                    : journey
                      ? "Preparation unfinished"
                      : "Not prepared"}
        </span>
      </div>
      <p>
        {lessons[topic].description} No devices are needed. Each course keeps its own code folder
        and teaching parameters in this application’s data space. Selecting or opening a course does
        not mark it complete.
      </p>
      <details>
        <summary>Notebook setup and keeping your work</summary>
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
      </details>
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
        <div className="grid gap-1 break-words">
          <p>
            {preparing
              ? "Preparation is running. This folder will be available when it finishes."
              : journey.ready
                ? "Preparation saved. Continue checks this folder and opens its Notebook."
                : "Preparation unfinished. Retry uses the same folder and keeps your edits."}
          </p>
          <p>
            Code folder: <code className="break-all">{journey.directory}</code>
          </p>
          <p>
            Notebook: <code className="break-all">{journey.notebook}</code>
          </p>
          <p>
            Python kernel: <code className="break-all">{journey.python}</code>
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
