import { Button, Notice, Select } from "../../ui/Controls";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { type LessonTopic, useDesktopAvailable } from "../application/DesktopSession";
import { navigate, navigateLink, useLocationUrl } from "../../lib/navigation";

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
  procedure: {
    title: "Procedures and human decisions",
    goal: "Scan, inspect evidence, answer a typed question and explicitly continue.",
    description:
      "After the basics: edit a small synthetic procedure, capture its pending question and record an answer without calibration or task scheduling.",
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
  {
    title: "Optional",
    hint: "Choose the skill you need next.",
    topics: ["compute", "groups", "procedure"],
  },
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
      <p>Start with Parameters and scans, or continue a course you have already prepared.</p>
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
    <label className="grid gap-1.5">
      <span id={`${courseId}-label`}>Course</span>
      <Select
        aria-labelledby={`${courseId}-label`}
        disabled={busy}
        value={topic}
        aria-describedby={`${courseId}-goal`}
        onChange={(event) => {
          const next = new URL(window.location.href);
          next.searchParams.set("lesson", event.target.value);
          navigate(next, { replace: true });
        }}
      >
        {courseGroups.map((group) => (
          <optgroup key={group.title} label={group.title}>
            {group.topics.map((value) => (
              <option key={value} value={value}>
                {lessons[value].title}
              </option>
            ))}
          </optgroup>
        ))}
      </Select>
      <span id={`${courseId}-goal`} className="text-sm text-text-dim">
        {lessons[topic].goal}
      </span>
    </label>
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
      <p>{lessons[topic].description} Uses synthetic data; no devices are needed.</p>
      {!native ? (
        <p>Open Help in the Scopecat desktop application to prepare this Notebook.</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
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
            </Button>
            {!journey && status.isSuccess && !preparing && (
              <Button disabled={busy} onClick={() => choose.mutate()}>
                Choose another save location…
              </Button>
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
        <Notice role="status">
          {preparing
            ? "Preparing code and Python; this may take a few minutes. "
            : choose.isPending
              ? "Choosing save location…"
              : "Opening editor…"}
        </Notice>
      )}
      {open.isSuccess && !busy && (
        <Notice role="status">
          Editor open requested. Select the Python kernel below. After a kernel restart, use the
          connection and history cells to resume; Run All starts new work.
        </Notice>
      )}
      {journey && (
        <div className="grid gap-1 break-words">
          {(preparing || !journey.ready) && (
            <p>
              {preparing
                ? "Preparation is running. This folder will be available when it finishes."
                : "Preparation unfinished. Retry uses the same folder and keeps your edits."}
            </p>
          )}
          {(open.isError || !journey.ready) && (
            <>
              <p>
                Code folder: <code className="break-all">{journey.directory}</code>
              </p>
              <p>
                Notebook: <code className="break-all">{journey.notebook}</code>
              </p>
            </>
          )}
          {(open.isSuccess || open.isError) && (
            <p>
              Python kernel: <code className="break-all">{journey.python}</code>
            </p>
          )}
          <a className="underline" href="#history" onClick={navigateLink}>
            Find saved results in Data
          </a>
          <details>
            <summary>Repair Notebook environments</summary>
            <p>
              If imports fail or the Python environment is incomplete, close this folder’s notebooks
              and Python terminals, then repair. Repair keeps your edited files and saved results,
              retains the previous local environment, and prepares matching dependencies for the
              notebook and future background work. Existing tasks keep their original execution
              environment. Reinstall any packages you added locally afterwards.
            </p>
            <Button
              disabled={!reachable || busy || status.isError}
              onClick={() => {
                open.reset();
                prepare.mutate({ topic, repair: true });
              }}
            >
              Repair environments and open Notebook
            </Button>
          </details>
          {journey.ready && (
            <>
              <p>
                Continue editing <code>src/my_experiment</code> in this course’s code folder.
              </p>
              <p>
                <a
                  className="underline"
                  href={`?lesson=${topic}&source=${encodeURIComponent(journey.directory)}#settings`}
                  onClick={navigateLink}
                >
                  Manage this code folder in Settings
                </a>
              </p>
            </>
          )}
        </div>
      )}
      {error && (
        <Notice role="alert">{error instanceof Error ? error.message : String(error)}</Notice>
      )}
    </section>
  );
}
