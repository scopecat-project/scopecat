import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, CircleOff, FlaskConical, LoaderCircle, UserRound } from "lucide-react";
import type {
  ProcedureRun,
  ProcedureStepAttempt,
  ProcedureStepInputSubmitCommand,
} from "../../api-contract";
import { DecisionEvidence } from "./DecisionEvidence";
import { DecisionFields, decisionFields } from "./DecisionFields";
import { errorMessage, formatRelative } from "../../lib/presentation";
import { classes, primaryButton } from "../../ui/styles";
import { getProcedureSteps, getWaitingProcedures, submitProcedureInput } from "./decision-api";

export function DecisionWorkspace({ daemonUnavailable }: { daemonUnavailable: boolean }) {
  const [selectedId, setSelectedId] = useState<string | undefined>(
    () => new URLSearchParams(window.location.search).get("procedure") ?? undefined,
  );
  const procedures = useQuery({
    queryKey: ["procedure-decisions"],
    queryFn: ({ signal }) => getWaitingProcedures(signal),
    enabled: !daemonUnavailable,
    refetchInterval: 1_000,
  });

  if (daemonUnavailable) {
    return (
      <WorkspaceMessage
        title="Connect to the local daemon"
        detail="Experiment decision requests are read directly from the daemon-owned project store."
        icon={<CircleOff />}
      />
    );
  }
  if (procedures.isPending)
    return <WorkspaceMessage title="Loading experiment decisions" pending />;
  if (procedures.isError) {
    return (
      <WorkspaceMessage
        title="Experiment decisions unavailable"
        detail={errorMessage(procedures.error)}
      />
    );
  }
  if (procedures.data.items.length === 0) {
    return (
      <WorkspaceMessage
        title="No experiment is waiting for a decision"
        detail="Procedures continue to appear here only when a human, AI, or service judgment is part of the declared experiment flow."
      />
    );
  }
  const selected =
    procedures.data.items.find((item) => item.procedure_run_id === selectedId) ??
    procedures.data.items[0];
  if (!selected) return null;

  return (
    <div className="grid min-h-[680px] grid-cols-[260px_minmax(0,1fr)] overflow-hidden rounded-lg border border-line bg-panel max-[900px]:grid-cols-1">
      <aside className="border-r border-line bg-panel-soft p-2.5 max-[900px]:border-r-0 max-[900px]:border-b">
        <div className="px-2 py-2">
          <div className="mb-1 flex items-center gap-2 text-[0.66rem] font-extrabold tracking-[0.1em] text-accent uppercase">
            <FlaskConical size={14} aria-hidden="true" />
            Decisions
          </div>
          <p className="m-0 text-[0.6rem] leading-4 text-text-dim">
            {procedures.data.items.length} experiment request
            {procedures.data.items.length === 1 ? "" : "s"}
          </p>
        </div>
        <div className="grid gap-1.5 max-[900px]:grid-cols-[repeat(auto-fit,minmax(210px,1fr))]">
          {procedures.data.items.map((procedure) => (
            <button
              className={classes(
                "grid cursor-pointer gap-1 rounded-md border border-transparent bg-transparent px-2.5 py-2.5 text-left text-text-soft hover:bg-panel",
                procedure.procedure_run_id === selected.procedure_run_id &&
                  "border-line-strong bg-panel",
              )}
              key={procedure.procedure_run_id}
              onClick={() => setSelectedId(procedure.procedure_run_id)}
              type="button"
            >
              <span className="truncate text-[0.68rem] font-bold">{procedure.definition.id}</span>
              <span className="truncate font-mono text-[0.57rem] text-text-dim">
                {procedure.procedure_run_id}
              </span>
            </button>
          ))}
        </div>
      </aside>
      <main className="min-w-0 p-4 max-[680px]:p-2.5">
        <header className="mb-3 rounded-md border border-line bg-panel-soft px-3.5 py-3">
          <p className="m-0 text-[0.66rem] leading-5 text-text-dim">
            Inspect the retained evidence and record one structured judgment. The procedure then
            continues with the recorded response.
          </p>
        </header>
        <DecisionCard key={selected.procedure_run_id} procedure={selected} />
      </main>
    </div>
  );
}

function DecisionCard({ procedure }: { procedure: ProcedureRun }) {
  const steps = useQuery({
    queryKey: ["procedure-decision-steps", procedure.procedure_run_id],
    queryFn: ({ signal }) => getProcedureSteps(procedure.procedure_run_id, signal),
    refetchInterval: 1_000,
  });

  if (steps.isPending) return <WorkspaceMessage title="Loading decision request" pending compact />;
  if (steps.isError) {
    return (
      <WorkspaceMessage
        title="Decision request unavailable"
        detail={errorMessage(steps.error)}
        compact
      />
    );
  }
  const step = steps.data.items.find((item) => item.state === "waiting_for_input");
  if (!step?.interpretation_request) {
    return (
      <WorkspaceMessage
        title="Waiting procedure has no visible decision request"
        detail={procedure.procedure_run_id}
        compact
      />
    );
  }
  return (
    <DecisionForm key={`${step.step_key}:${step.revision}`} procedure={procedure} step={step} />
  );
}

function DecisionForm({
  procedure,
  step,
}: {
  procedure: ProcedureRun;
  step: ProcedureStepAttempt;
}) {
  const request = step.interpretation_request;
  if (!request) throw new Error("waiting interpretation request is missing");
  const [actor, setActor] = useState("");
  const [actorKind, setActorKind] = useState<"ai" | "human" | "service">("human");
  const [note, setNote] = useState("");
  const [valueText, setValueText] = useState(() =>
    JSON.stringify(request.response_template ?? initialValue(request.structure), null, 2),
  );
  const fields = decisionFields(request.structure);
  const [useJson, setUseJson] = useState(!fields);
  const [parseError, setParseError] = useState<string>();
  const queryClient = useQueryClient();
  const submit = useMutation({
    mutationFn: submitProcedureInput,
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procedure-decisions"] }),
        queryClient.invalidateQueries({
          queryKey: ["procedure-decision-steps", procedure.procedure_run_id],
        }),
      ]);
    },
  });

  useEffect(() => {
    if (!submit.isSuccess) return;
    const timer = window.setTimeout(() => submit.reset(), 5_000);
    return () => window.clearTimeout(timer);
  }, [submit]);

  const record = () => {
    if (!actor.trim()) {
      setParseError("Identify the person, agent, or service making this judgment.");
      return;
    }
    let value: unknown;
    try {
      value = JSON.parse(valueText);
    } catch (error) {
      setParseError(errorMessage(error));
      return;
    }
    setParseError(undefined);
    const command: ProcedureStepInputSubmitCommand = {
      procedure_run_id: procedure.procedure_run_id,
      expected_run_revision: procedure.revision,
      step_key: step.step_key,
      attempt: step.attempt,
      expected_step_revision: step.revision,
      request_hash: step.intent_hash,
      actor: actor.trim(),
      actor_kind: actorKind,
      value,
      note,
    };
    submit.mutate(command);
  };

  return (
    <article className="rounded-lg border border-line bg-panel p-4">
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-line pb-3">
        <div>
          <div className="mb-1 text-[0.61rem] font-bold tracking-[0.08em] text-text-dim uppercase">
            {procedure.definition.id} · {step.step_key}
          </div>
          <h2 className="m-0 text-[1rem] tracking-[-0.02em]">{request.title}</h2>
          <p className="mt-1.5 mb-0 max-w-[900px] text-[0.7rem] leading-5 text-text-soft">
            {request.instructions}
          </p>
        </div>
        <div className="text-right font-mono text-[0.58rem] leading-5 text-text-dim">
          <div>{request.schema_id}</div>
          <div>waiting {step.updated_at ? formatRelative(step.updated_at) : "now"}</div>
        </div>
      </header>

      {step.inputs.length > 0 && (
        <section className="mb-4 rounded-md border border-line bg-panel-soft p-3">
          <div className="mb-2 text-[0.62rem] font-bold tracking-[0.06em] text-text-dim uppercase">
            Evidence retained by this decision
          </div>
          <div className="flex flex-wrap gap-2">
            {step.inputs.map((input) => (
              <EvidenceLink input={input} key={JSON.stringify(input)} />
            ))}
          </div>
        </section>
      )}

      <div className="mb-4 grid gap-3 lg:grid-cols-2">
        {step.inputs
          .filter((input) => input.kind === "analysis")
          .map((input) => (
            <DecisionEvidence key={JSON.stringify(input)} input={input} />
          ))}
      </div>
      <div className="grid grid-cols-[minmax(0,1.3fr)_minmax(260px,0.7fr)] gap-4 max-[850px]:grid-cols-1">
        <div className="grid content-start gap-3">
          {fields && (
            <button
              type="button"
              className="text-left text-xs text-accent"
              onClick={() => {
                try {
                  if (!isRecord(JSON.parse(valueText)))
                    throw new Error("Judgment must be an object.");
                  setUseJson(!useJson);
                  setParseError(undefined);
                } catch (error) {
                  setParseError(errorMessage(error));
                }
              }}
            >
              {useJson ? "Use form" : "Edit JSON"}
            </button>
          )}
          {!useJson && fields ? (
            <DecisionFields
              fields={fields}
              value={JSON.parse(valueText)}
              onChange={(value) => setValueText(JSON.stringify(value, null, 2))}
            />
          ) : (
            <label className="grid gap-1.5 text-[0.64rem] font-bold text-text-dim">
              Structured judgment (JSON)
              <textarea
                className="min-h-[220px] resize-y rounded-md border border-line bg-bg p-3 font-mono text-[0.68rem] leading-5 text-text outline-none focus:border-accent"
                spellCheck={false}
                value={valueText}
                onChange={(event) => setValueText(event.target.value)}
              />
            </label>
          )}
        </div>
        <div className="grid content-start gap-3">
          <div className="grid gap-1.5 text-[0.64rem] text-text-dim">
            <label className="grid gap-1.5 font-bold">
              Recorded reviewer
              <input
                className="rounded-md border border-line bg-bg px-3 py-2.5 text-[0.7rem] text-text outline-none focus:border-accent"
                placeholder="name, agent id, or service id"
                value={actor}
                onChange={(event) => setActor(event.target.value)}
              />
            </label>
            <span className="font-normal leading-4 text-text-dim">
              Name the lab operator, analysis agent, or service so this judgment can be traced
              during later experiment review.
            </span>
          </div>
          <div
            className="grid grid-cols-3 gap-1 rounded-md border border-line bg-bg p-1"
            role="group"
          >
            {(["human", "ai", "service"] as const).map((kind) => (
              <button
                className={classes(
                  "inline-flex cursor-pointer items-center justify-center gap-1.5 rounded px-2 py-2 text-[0.62rem] font-bold capitalize text-text-dim",
                  actorKind === kind && "bg-panel-strong text-text",
                )}
                key={kind}
                onClick={() => setActorKind(kind)}
                type="button"
              >
                {kind === "human" ? <UserRound size={13} /> : <Bot size={13} />}
                {kind}
              </button>
            ))}
          </div>
          <label className="grid gap-1.5 text-[0.64rem] font-bold text-text-dim">
            Reasoning note (optional)
            <textarea
              className="min-h-[90px] resize-y rounded-md border border-line bg-bg px-3 py-2.5 text-[0.68rem] leading-5 text-text outline-none focus:border-accent"
              placeholder="Why this selection is experimentally credible"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
          <details className="rounded-md border border-line bg-panel-soft px-3 py-2 text-[0.62rem] text-text-dim">
            <summary className="cursor-pointer font-bold text-text-soft">
              Expected structure
            </summary>
            <pre className="mt-2 overflow-auto whitespace-pre-wrap font-mono text-[0.58rem] leading-4">
              {JSON.stringify(request.structure, null, 2)}
            </pre>
          </details>
          {request.metadata && Object.keys(request.metadata).length > 0 && (
            <details className="rounded-md border border-line bg-panel-soft px-3 py-2 text-[0.62rem] text-text-dim">
              <summary className="cursor-pointer font-bold text-text-soft">
                Presentation hints
              </summary>
              <pre className="mt-2 overflow-auto whitespace-pre-wrap font-mono text-[0.58rem] leading-4">
                {JSON.stringify(request.metadata, null, 2)}
              </pre>
            </details>
          )}
          {(parseError || submit.isError) && (
            <p className="m-0 text-[0.64rem] leading-5 text-red" role="alert">
              {parseError ?? errorMessage(submit.error)}
            </p>
          )}
          {submit.isSuccess && (
            <p className="m-0 inline-flex items-center gap-1.5 text-[0.64rem] text-green">
              <Check size={14} /> Decision recorded. The procedure is ready to continue.
            </p>
          )}
          <button
            className={primaryButton}
            disabled={submit.isPending || submit.isSuccess}
            onClick={record}
            type="button"
          >
            {submit.isPending && <LoaderCircle className="animate-spin" size={14} />}
            Record decision
          </button>
        </div>
      </div>
    </article>
  );
}

function EvidenceLink({ input }: { input: ProcedureStepAttempt["inputs"][number] }) {
  const common =
    "inline-flex items-center gap-1.5 rounded-md border border-line bg-panel px-2.5 py-2 font-mono text-[0.6rem] text-text-soft";
  if (input.kind === "run") {
    return (
      <a
        className={`${common} no-underline hover:border-line-strong hover:text-text`}
        href={`/?run=${encodeURIComponent(input.run_id)}#runs`}
        rel="noreferrer"
        target="_blank"
      >
        run · {input.run_id}
      </a>
    );
  }
  if (input.kind === "analysis") {
    const href =
      input.subject.kind === "run"
        ? `/?run=${encodeURIComponent(input.subject.run_id)}#runs`
        : input.subject.kind === "sample"
          ? `/?sample=${encodeURIComponent(input.subject.sample_id)}#samples`
          : `/?analysis=${encodeURIComponent(input.analysis_record_id)}#analyses`;
    return (
      <a
        className={`${common} no-underline hover:border-line-strong hover:text-text`}
        href={href}
        rel="noreferrer"
        target="_blank"
      >
        analysis · {input.analysis_record_id}
      </a>
    );
  }
  if (input.kind === "interpretation") {
    return <span className={common}>decision · {input.step_key}</span>;
  }
  return <span className={common}>configuration · {input.entry_id}</span>;
}

function initialValue(structure: unknown): unknown {
  if (!isRecord(structure) || typeof structure.type !== "string") return {};
  if (structure.type === "object" && isRecord(structure.fields)) {
    return Object.fromEntries(
      Object.entries(structure.fields).map(([name, field]) => [name, initialValue(field)]),
    );
  }
  if (structure.type === "list" || structure.type === "tuple") return [];
  if (structure.type === "bool") return false;
  if (structure.type === "float" || structure.type === "int") return 0;
  if (structure.type === "string") return "";
  if (structure.type === "quantity") return { value: 0, unit: "" };
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function WorkspaceMessage({
  title,
  detail,
  pending = false,
  compact = false,
  icon,
}: {
  title: string;
  detail?: string;
  pending?: boolean;
  compact?: boolean;
  icon?: ReactNode;
}) {
  return (
    <div
      className={classes(
        "grid place-content-center justify-items-center rounded-lg border border-line bg-panel text-center",
        compact ? "min-h-[150px]" : "min-h-[560px]",
      )}
    >
      {pending && <LoaderCircle className="mb-3 animate-spin text-accent" size={22} />}
      {icon && <div className="mb-3 text-text-dim">{icon}</div>}
      <h2 className="m-0 text-[0.9rem]">{title}</h2>
      {detail && (
        <p className="mt-2 mb-0 max-w-[560px] text-[0.68rem] leading-5 text-text-dim">{detail}</p>
      )}
    </div>
  );
}
