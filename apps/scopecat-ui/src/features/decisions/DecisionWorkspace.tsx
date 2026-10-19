import * as Controls from "../../ui/Controls";
import { createContext, useContext, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleOff, Ellipsis, FlaskConical, LoaderCircle } from "lucide-react";
import type {
  ProcedureRun,
  ProcedureStepAttempt,
  ProcedureStepInputSubmitCommand,
} from "../../api-contract";
import { DecisionDraftHistory } from "./DecisionDraftHistory";
import { useDecisionDraft } from "./use-decision-draft";
import { readDecisionDraft, type DecisionDraftView } from "./decision-api";
import { DecisionEvidence } from "./DecisionEvidence";
import {
  DecisionFields,
  decisionFields,
  decisionValueError,
  decisionConstraintError,
  decisionFormValue,
} from "./DecisionFields";
import { errorMessage, formatDateTime } from "../../lib/presentation";
import { classes, iconButton, primaryButton } from "../../ui/styles";
import {
  getProcedureSteps,
  getDecisionProgress,
  getWaitingProcedures,
  submitProcedureInput,
  type ProcedureInputContinueReceipt,
} from "./decision-api";

const DecisionOptions = createContext(false);

export function DecisionWorkspace({ daemonUnavailable }: { daemonUnavailable: boolean }) {
  const [options, setOptions] = useState(false);
  return (
    <DecisionOptions value={options}>
      <div className="mb-2 flex justify-end">
        <button
          type="button"
          className={iconButton}
          aria-label="Decision options"
          title="Optional notes and draft recovery"
          aria-expanded={options}
          onClick={() => setOptions(!options)}
        >
          <Ellipsis size={18} />
        </button>
      </div>
      <DecisionWorkspaceBody daemonUnavailable={daemonUnavailable} />
      {options && <DecisionDraftHistory />}
    </DecisionOptions>
  );
}

function DecisionWorkspaceBody({ daemonUnavailable }: { daemonUnavailable: boolean }) {
  const [selectedId, setSelectedId] = useState<string | undefined>(
    () => new URLSearchParams(window.location.search).get("procedure") ?? undefined,
  );
  const [submitted, setSubmitted] = useState<{
    command: ProcedureStepInputSubmitCommand;
    receipt?: ProcedureInputContinueReceipt;
  }>();
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
  if (
    submitted &&
    (submitted.receipt?.continuation === "retryable" ||
      submitted.receipt?.continuation === "blocked" ||
      !procedures.data.items.some(
        (item) => item.procedure_run_id === submitted.command.procedure_run_id,
      ))
  ) {
    return (
      <DecisionContinuation
        submitted={submitted}
        onUpdated={(receipt) => setSubmitted({ ...submitted, receipt })}
        onDismiss={() => {
          setSubmitted(undefined);
          setSelectedId(undefined);
        }}
      />
    );
  }
  if (selectedId && !procedures.data.items.some((item) => item.procedure_run_id === selectedId)) {
    return (
      <section className="rounded-lg border border-line bg-panel p-5">
        <h2 className="text-lg font-semibold leading-7">Decision saved or no longer waiting</h2>
        <p>Open the task to check its answer and execution status.</p>
        <a
          className="text-accent underline"
          href={`?procedure=${encodeURIComponent(selectedId)}#launch`}
        >
          Open task
        </a>
        <button
          type="button"
          className="ml-4 text-accent underline"
          onClick={() => setSelectedId(undefined)}
        >
          View waiting decisions
        </button>
      </section>
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
    <div
      className={classes(
        "grid min-w-0 overflow-hidden rounded-lg border border-line bg-panel",
        procedures.data.items.length > 1 &&
          "grid-cols-[260px_minmax(0,1fr)] max-[900px]:grid-cols-1",
      )}
    >
      {procedures.data.items.length > 1 && (
        <aside className="border-r border-line bg-panel-soft p-2.5 max-[900px]:border-r-0 max-[900px]:border-b">
          <div className="px-2 py-2">
            <div className="mb-1 flex items-center gap-2 text-xs font-extrabold tracking-[0.1em] text-accent uppercase">
              <FlaskConical size={14} aria-hidden="true" />
              Decisions
            </div>
            <p className="m-0 text-xs leading-4 text-text-dim">
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
                title={procedure.procedure_run_id}
                onClick={() => setSelectedId(procedure.procedure_run_id)}
                type="button"
              >
                <span className="truncate text-xs font-bold">{procedure.definition.id}</span>
                <span className="text-xs text-text-dim">
                  {procedure.created_at
                    ? `Submitted ${formatDateTime(procedure.created_at)}`
                    : "Submission time unavailable"}
                </span>
                {procedure.resolved_samples.length > 0 && (
                  <span className="truncate text-xs text-text-soft">
                    {procedure.resolved_samples.map((sample) => sample.sample_id).join(", ")}
                  </span>
                )}
                {procedures.data.items.some(
                  (other) =>
                    other.procedure_run_id !== procedure.procedure_run_id &&
                    other.definition.id === procedure.definition.id &&
                    (other.created_at ? formatDateTime(other.created_at) : "") ===
                      (procedure.created_at ? formatDateTime(procedure.created_at) : ""),
                ) && (
                  <span className="text-xs text-text-dim">
                    Reference · {procedure.procedure_run_id.slice(-8)}
                  </span>
                )}
              </button>
            ))}
          </div>
        </aside>
      )}
      <main className="min-w-0 p-4 max-[680px]:p-2.5">
        <DecisionCard
          key={selected.procedure_run_id}
          procedure={selected}
          onSubmitted={(command) => setSubmitted({ command })}
          onRecorded={(receipt, command) => {
            setSubmitted({ command, receipt });
            setSelectedId(selected.procedure_run_id);
            const url = new URL(window.location.href);
            url.searchParams.set("procedure", selected.procedure_run_id);
            window.history.replaceState(window.history.state, "", url);
          }}
        />
      </main>
    </div>
  );
}

function DecisionCard({
  procedure,
  onRecorded,
  onSubmitted,
}: {
  procedure: ProcedureRun;
  onSubmitted: (command: ProcedureStepInputSubmitCommand) => void;
  onRecorded: (
    receipt: ProcedureInputContinueReceipt,
    command: ProcedureStepInputSubmitCommand,
  ) => void;
}) {
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
    <DecisionForm
      key={`${step.step_key}:${step.attempt}`}
      procedure={procedure}
      step={step}
      onRecorded={onRecorded}
      onSubmitted={onSubmitted}
    />
  );
}

function DecisionForm(props: {
  procedure: ProcedureRun;
  step: ProcedureStepAttempt;
  onSubmitted: (command: ProcedureStepInputSubmitCommand) => void;
  onRecorded: (
    receipt: ProcedureInputContinueReceipt,
    command: ProcedureStepInputSubmitCommand,
  ) => void;
}) {
  const target = {
    procedure_run_id: props.procedure.procedure_run_id,
    step_key: props.step.step_key,
    attempt: props.step.attempt,
  };
  const saved = useQuery({
    queryKey: ["decision-draft", target],
    queryFn: () => readDecisionDraft(target),
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  if (saved.isPending) return <WorkspaceMessage title="Loading saved draft" pending compact />;
  if (saved.isError)
    return (
      <WorkspaceMessage
        title="Draft recovery unavailable"
        detail={errorMessage(saved.error)}
        compact
      />
    );
  return <DecisionEditor {...props} initial={saved.data} />;
}

function DecisionEditor({
  procedure,
  step,
  onRecorded,
  onSubmitted,
  initial,
}: {
  procedure: ProcedureRun;
  step: ProcedureStepAttempt;
  onSubmitted: (command: ProcedureStepInputSubmitCommand) => void;
  onRecorded: (
    receipt: ProcedureInputContinueReceipt,
    command: ProcedureStepInputSubmitCommand,
  ) => void;
  initial: DecisionDraftView;
}) {
  const options = useContext(DecisionOptions);
  const request = step.interpretation_request;
  if (!request) throw new Error("waiting interpretation request is missing");
  const fields = decisionFields(request.structure);
  const analysisInputs = step.inputs.filter((input) => input.kind === "analysis");
  const defaults = {
    actor: typeof request.metadata?.reviewer === "string" ? request.metadata.reviewer : "",
    actor_kind: "human" as const,
    note: "",
    value_text: JSON.stringify(
      request.response_template ?? initialValue(request.structure),
      null,
      2,
    ),
    use_json: false,
  };
  const draft = useDecisionDraft(
    {
      procedure_run_id: procedure.procedure_run_id,
      step_key: step.step_key,
      attempt: step.attempt,
    },
    {
      run_revision: procedure.revision,
      step_revision: step.revision,
      request_hash: step.intent_hash,
    },
    initial,
    defaults,
  );
  const { actor, note, value_text: valueText } = draft.input;
  let formValue: unknown;
  try {
    formValue = JSON.parse(valueText);
  } catch {
    /* Preserve malformed retained drafts. */
  }
  const formUnavailable = !fields || !isRecord(formValue);
  const setActor = (text: string) => draft.edit({ ...draft.input, actor: text });
  const setNote = (text: string) => draft.edit({ ...draft.input, note: text });
  const setValueText = (value_text: string) => {
    setParseError(undefined);
    draft.edit({ ...draft.input, value_text, use_json: false });
  };
  const [parseError, setParseError] = useState<string>();
  const queryClient = useQueryClient();
  const submit = useMutation({
    mutationFn: submitProcedureInput,
    onSuccess: (receipt, command) => {
      onRecorded(receipt, command);
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["procedure-decisions"] }),
        queryClient.invalidateQueries({
          queryKey: ["procedure-decision-steps", procedure.procedure_run_id],
        }),
      ]);
    },
  });

  const recording = useRef(false);
  const composing = useRef(false);
  const [committing, setCommitting] = useState(false);
  const record = async () => {
    if (
      formUnavailable ||
      recording.current ||
      submit.isPending ||
      submit.isSuccess ||
      draft.invalid ||
      draft.discarding ||
      draft.status === "conflict"
    )
      return;
    if (!actor.trim()) {
      setParseError("Enter the name of the person making this judgment.");
      return;
    }
    let value: unknown;
    try {
      value = JSON.parse(valueText);
      if (fields) value = decisionFormValue(fields, value, request.metadata);
    } catch (error) {
      setParseError(errorMessage(error));
      return;
    }
    if (fields) {
      const error = decisionValueError(fields, value);
      if (error) {
        setParseError(error);
        return;
      }
    }
    const constraintError = decisionConstraintError(value, request.metadata);
    if (constraintError) {
      setParseError(constraintError);
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
      actor_kind: "human",
      value,
      note,
    };
    recording.current = true;
    setCommitting(true);
    try {
      if ((await draft.flush()) && draft.isCurrent(draft.input)) {
        onSubmitted(command);
        await submit.mutateAsync(command);
      }
    } catch {
      // The mutation retains the server error; the draft stays editable for correction.
    } finally {
      recording.current = false;
      setCommitting(false);
    }
  };

  return (
    <article className="min-w-0 rounded-lg border border-line bg-panel p-4">
      <header className="mb-4 space-y-2">
        <a
          className="text-sm text-text-dim underline"
          href={`?procedure=${encodeURIComponent(procedure.procedure_run_id)}#launch`}
        >
          {procedure.definition.id}
        </a>
        <h2 className="text-lg font-semibold leading-7">{request.title}</h2>
        <p className="text-sm leading-6 text-text-soft">{request.instructions}</p>
      </header>
      {draft.invalid && (
        <Controls.Notice role="alert">
          The request changed or is no longer waiting. Your input is retained, but cannot be
          recorded against this baseline. Review the task; discard to start a fresh draft.
        </Controls.Notice>
      )}
      {(draft.status === "failed" || draft.status === "conflict") && (
        <div className="mb-3 space-y-2" role="alert">
          <p>
            {draft.status === "conflict"
              ? "The current draft revision changed. Your conflicting copy is retained in draft history."
              : draft.discardFailed
                ? `Discard was not confirmed. Your text is still here; retry discard. ${draft.error ?? ""}`
                : `Draft save failed. Keep this window open and retry. ${draft.error ?? ""}`}
          </p>
          <Controls.Button
            type="button"
            disabled={committing}
            onClick={() => {
              void (draft.status === "conflict"
                ? draft.resolve()
                : draft.discardFailed
                  ? draft.discard()
                  : draft.flush());
            }}
          >
            {draft.status === "conflict"
              ? "Use my copy as current draft"
              : draft.discardFailed
                ? "Retry draft discard"
                : "Retry draft save"}
          </Controls.Button>
        </div>
      )}
      {step.inputs.some((input) => input.kind !== "analysis") && (
        <div className="mb-4 flex flex-wrap gap-2" aria-label="Decision data">
          {step.inputs
            .filter((input) => input.kind !== "analysis")
            .map((input) => (
              <EvidenceLink input={input} key={JSON.stringify(input)} />
            ))}
        </div>
      )}
      <div
        className={classes(
          "grid items-start gap-4",
          analysisInputs.length > 0 && "xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]",
        )}
      >
        {analysisInputs.length > 0 && (
          <div className="grid min-w-0 gap-3">
            {analysisInputs.map((input) => (
              <DecisionEvidence key={JSON.stringify(input)} input={input} />
            ))}
          </div>
        )}
        {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- Delegate IME Enter protection from the native form controls without adding a keyboard role to the form. */}
        <form
          aria-label="Record judgment"
          className="min-w-0"
          onSubmit={(event) => {
            event.preventDefault();
            if (!composing.current) void record();
          }}
          onCompositionStart={() => {
            composing.current = true;
          }}
          onCompositionEnd={() => {
            composing.current = false;
          }}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              (composing.current || event.nativeEvent.isComposing || event.keyCode === 229)
            )
              event.preventDefault();
          }}
        >
          <fieldset
            disabled={draft.discarding || committing || submit.isPending || submit.isSuccess}
            className="grid min-w-0 gap-4"
          >
            {!formUnavailable && fields ? (
              <DecisionFields
                fields={fields}
                labels={
                  isRecord(request.metadata?.field_labels) ? request.metadata.field_labels : {}
                }
                value={isRecord(formValue) ? formValue : {}}
                metadata={request.metadata}
                onChange={(value) => setValueText(JSON.stringify(value, null, 2))}
              />
            ) : (
              <Controls.Notice role="alert">
                {!fields
                  ? "This request contains a response type the form does not support yet. Ask the workflow author to use supported fields or add a matching form control. Your draft is retained; this request cannot be submitted here."
                  : "The saved response cannot be displayed as a form. Your draft is retained in recovery history. Review the request before discarding it and starting again."}
              </Controls.Notice>
            )}
            <Controls.Field>
              Recorded reviewer
              <Controls.Input
                placeholder="Person’s name"
                value={actor}
                onChange={(event) => setActor(event.target.value)}
              />
            </Controls.Field>
            {(parseError || submit.isError) && (
              <Controls.Notice role="alert">
                {parseError ?? errorMessage(submit.error)}
              </Controls.Notice>
            )}
            {submit.isSuccess && (
              <p role="status">
                Decision saved. {submit.data.dispatch_error ?? "Continuation requested."}
              </p>
            )}
            <button
              className={primaryButton}
              type="submit"
              disabled={
                formUnavailable ||
                committing ||
                submit.isPending ||
                submit.isSuccess ||
                draft.invalid ||
                draft.status === "conflict"
              }
            >
              {(committing || submit.isPending) && (
                <LoaderCircle className="animate-spin" size={14} />
              )}
              Submit and continue
            </button>
            <p role="status" className="text-xs text-text-dim">
              {draft.status === "saved"
                ? draft.started
                  ? "Draft saved in application data"
                  : "No unsaved edits"
                : draft.status === "saving"
                  ? "Saving draft…"
                  : draft.status === "unsaved"
                    ? "Draft not yet saved"
                    : ""}
            </p>
            {options && (
              <section aria-label="Response options" className="border-t border-line pt-3 text-sm">
                <div className="mt-3 grid gap-3">
                  <Controls.Field>
                    Reasoning note (optional)
                    <Controls.Textarea
                      value={note}
                      onChange={(event) => setNote(event.target.value)}
                      placeholder="Why this selection is experimentally credible"
                    />
                  </Controls.Field>
                </div>
              </section>
            )}
          </fieldset>
        </form>
      </div>
      {(options || draft.invalid) && (
        <Controls.Button
          type="button"
          className="mt-4"
          disabled={draft.discarding || draft.status === "saving" || committing || submit.isPending}
          onClick={() => void draft.discard()}
        >
          Discard draft (keep recovery history)
        </Controls.Button>
      )}
    </article>
  );
}

function EvidenceLink({ input }: { input: ProcedureStepAttempt["inputs"][number] }) {
  const common =
    "inline-flex items-center gap-1.5 rounded-md border border-line bg-panel px-2.5 py-2 font-mono text-xs text-text-soft";
  if (input.kind === "run") {
    return (
      <a
        className={`${common} no-underline hover:border-line-strong hover:text-text`}
        title={`Run ${input.run_id}`}
        href={`/?run=${encodeURIComponent(input.run_id)}#runs`}
        rel="noreferrer"
        target="_blank"
      >
        Open measurement data
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
  if (input.kind === "parameter_publish") {
    return (
      <span
        className={common}
        title={`Generation ${input.branch.generation} · ${input.branch.revision.revision_id}`}
      >
        Parameters · {input.branch.name}
      </span>
    );
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
  if (structure.type === "array" || structure.type === "list" || structure.type === "tuple")
    return [];
  if (structure.type === "bool") return false;
  if (structure.type === "float" || structure.type === "int") return null;
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
        <p className="mt-2 mb-0 max-w-[560px] text-xs leading-5 text-text-dim">{detail}</p>
      )}
    </div>
  );
}

function DecisionContinuation({
  submitted,
  onUpdated,
  onDismiss,
}: {
  submitted: { command: ProcedureStepInputSubmitCommand; receipt?: ProcedureInputContinueReceipt };
  onUpdated: (receipt: ProcedureInputContinueReceipt) => void;
  onDismiss: () => void;
}) {
  const retry = useMutation({ mutationFn: submitProcedureInput, onSuccess: onUpdated });
  const receipt = submitted.receipt;
  const progress = useQuery({
    queryKey: ["decision-continuation", submitted.command.procedure_run_id],
    queryFn: ({ signal }) => getDecisionProgress(submitted.command.procedure_run_id, signal),
    refetchInterval: 1_000,
  });
  const dispatch = progress.data?.dispatch;
  const retryable = receipt?.continuation === "retryable" || dispatch?.input_continuation_retryable;

  return (
    <section className="rounded-lg border border-line bg-panel p-5 space-y-3">
      <h2 className="text-lg font-semibold">
        {receipt ? "Decision saved" : "Check decision submission"}
      </h2>
      <p role="status">
        {dispatch?.failure?.message ??
          receipt?.dispatch_error ??
          (receipt
            ? "Continuation requested. Open the task to follow its progress."
            : "The task is no longer waiting. Retry the same submission to check its recorded answer and continuation.")}
      </p>
      {retry.isError && <p role="alert">{errorMessage(retry.error)}</p>}
      {(!receipt || retryable) && (
        <button
          className={primaryButton}
          disabled={retry.isPending}
          onClick={() => retry.mutate(submitted.command)}
        >
          {retry.isPending ? "Retrying…" : "Retry continuation"}
        </button>
      )}
      <a
        className="text-accent underline"
        href={`?procedure=${encodeURIComponent(submitted.command.procedure_run_id)}#launch`}
      >
        Open task
      </a>
      <button className="ml-4 text-accent underline" onClick={onDismiss}>
        View waiting decisions
      </button>
    </section>
  );
}
