import { pointBinding } from "./point-binding";
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
  decisionMetadata,
  decisionFormValue,
  inactiveField,
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
          aria-label="人工判断选项"
          title="可选备注与草稿恢复"
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
        title="连接本地 daemon"
        detail="人工判断请求直接从 daemon 管理的项目存储读取。"
        icon={<CircleOff />}
      />
    );
  }
  if (procedures.isPending) return <WorkspaceMessage title="正在加载人工判断请求" pending />;
  if (procedures.isError) {
    return (
      <WorkspaceMessage title="无法读取人工判断请求" detail={errorMessage(procedures.error)} />
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
        <h2 className="text-lg font-semibold leading-7">判断已保存或任务已不再等待</h2>
        <p>打开任务可核实已记录的判断与执行状态。</p>
        <a
          className="text-accent underline"
          href={`?procedure=${encodeURIComponent(selectedId)}#launch`}
        >
          打开任务
        </a>
        <button
          type="button"
          className="ml-4 text-accent underline"
          onClick={() => setSelectedId(undefined)}
        >
          查看待处理判断
        </button>
      </section>
    );
  }
  if (procedures.data.items.length === 0) {
    return (
      <WorkspaceMessage
        title="当前没有等待人工判断的实验"
        detail="只有声明的实验流程包含人工、AI 或服务判断时，任务才会在此等待。"
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
              人工判断
            </div>
            <p className="m-0 text-xs leading-4 text-text-dim">
              {procedures.data.items.length} 个实验请求
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
                    ? `提交于 ${formatDateTime(procedure.created_at)}`
                    : "提交时间不可用"}
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
                    标识 · {procedure.procedure_run_id.slice(-8)}
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

  if (steps.isPending) return <WorkspaceMessage title="正在加载判断请求" pending compact />;
  if (steps.isError) {
    return <WorkspaceMessage title="无法读取判断请求" detail={errorMessage(steps.error)} compact />;
  }
  const step = steps.data.items.find((item) => item.state === "waiting_for_input");
  if (!step?.interpretation_request) {
    return (
      <WorkspaceMessage
        title="等待中的任务没有可见判断请求"
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
  if (saved.isPending) return <WorkspaceMessage title="正在加载已保存草稿" pending compact />;
  if (saved.isError)
    return <WorkspaceMessage title="无法恢复草稿" detail={errorMessage(saved.error)} compact />;
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
  const metadata = decisionMetadata(request.metadata, request.answer_contract);
  const fields = decisionFields(request.structure);
  const analysisInputs = step.inputs
    .map((input, index) => ({ input, index }))
    .filter(({ input }) => input.kind === "analysis");
  const binding = pointBinding(metadata, request.structure);
  const defaults = {
    actor:
      initial.default_operator || (typeof metadata?.reviewer === "string" ? metadata.reviewer : ""),
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
      setParseError("请输入作出判断的操作人姓名。");
      return;
    }
    let value: unknown;
    try {
      value = JSON.parse(valueText);
      if (fields) value = decisionFormValue(fields, value, metadata);
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
    const constraintError = decisionConstraintError(value, metadata);
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
      // An untouched template must also retain its actor before an uncertain send.
      draft.edit(draft.input);
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
          请求已变化或不再等待判断。输入已保留，但无法基于原请求提交。请查看任务；丢弃当前草稿后可重新填写。
        </Controls.Notice>
      )}
      {(draft.status === "failed" || draft.status === "conflict") && (
        <div className="mb-3 space-y-2" role="alert">
          <p>
            {draft.status === "conflict"
              ? "当前草稿版本已变化，冲突副本已保留在草稿历史中。"
              : draft.discardFailed
                ? `尚未确认丢弃草稿。输入仍保留在此，请重试丢弃。 ${draft.error ?? ""}`
                : `草稿保存失败。请保持窗口打开并重试。 ${draft.error ?? ""}`}
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
              ? "将我的副本设为当前草稿"
              : draft.discardFailed
                ? "重试丢弃草稿"
                : "重试保存草稿"}
          </Controls.Button>
        </div>
      )}
      {step.inputs.some((input) => input.kind !== "analysis") && (
        <div className="mb-4 flex flex-wrap gap-2" aria-label="判断依据">
          {step.inputs
            .filter((input) => input.kind !== "analysis")
            .map((input) => (
              <EvidenceLink input={input} key={JSON.stringify(input)} />
            ))}
        </div>
      )}
      {metadata &&
        "point_binding" in metadata &&
        (!binding || step.inputs[binding.input]?.kind !== "analysis") && (
          <Controls.Notice role="alert">
            不支持当前图表选点声明。请手动填写表单，或请流程作者修正选点绑定。
          </Controls.Notice>
        )}
      <div
        className={classes(
          "grid items-start gap-4",
          analysisInputs.length > 0 && "xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]",
        )}
      >
        {analysisInputs.length > 0 && (
          <div className="grid min-w-0 gap-3">
            {analysisInputs.map(
              ({ input, index }) =>
                input.kind === "analysis" && (
                  <DecisionEvidence
                    key={JSON.stringify(input)}
                    input={input}
                    binding={binding?.input === index ? binding : undefined}
                    disabled={
                      draft.invalid ||
                      draft.discarding ||
                      committing ||
                      submit.isPending ||
                      submit.isSuccess ||
                      draft.status === "conflict" ||
                      !fields ||
                      !isRecord(formValue) ||
                      Boolean(
                        binding &&
                        fields[binding.field] &&
                        inactiveField(
                          binding.field,
                          fields[binding.field]!,
                          formValue,
                          metadata,
                          fields,
                        ),
                      )
                    }
                    onPick={(value) => {
                      if (
                        !binding ||
                        binding.input !== index ||
                        !fields ||
                        !isRecord(formValue) ||
                        !draft.isCurrent(draft.input) ||
                        recording.current ||
                        draft.status === "conflict" ||
                        draft.discarding ||
                        inactiveField(
                          binding.field,
                          fields[binding.field]!,
                          formValue,
                          metadata,
                          fields,
                        )
                      )
                        return false;
                      if (fields[binding.field]?.type === "int" && !Number.isInteger(value))
                        return false;
                      setValueText(
                        JSON.stringify({ ...formValue, [binding.field]: value }, null, 2),
                      );
                      return true;
                    }}
                  />
                ),
            )}
          </div>
        )}
        {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- Delegate IME Enter protection from the native form controls without adding a keyboard role to the form. */}
        <form
          aria-label="记录人工判断"
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
                labels={isRecord(metadata?.field_labels) ? metadata.field_labels : {}}
                value={isRecord(formValue) ? formValue : {}}
                metadata={metadata}
                onChange={(value) => setValueText(JSON.stringify(value, null, 2))}
              />
            ) : (
              <Controls.Notice role="alert">
                {!fields
                  ? "此请求包含表单尚不支持的回答类型。请流程作者使用支持的字段或补充相应控件。草稿已保留，此处无法提交该请求。"
                  : "已保存的回答无法显示为表单。草稿已保留在恢复历史中。请核对请求后再丢弃草稿并重新填写。"}
              </Controls.Notice>
            )}
            <Controls.Field>
              判断操作人
              <Controls.Input
                placeholder="操作人姓名"
                value={actor}
                onChange={(event) => setActor(event.target.value)}
              />
            </Controls.Field>
            {(parseError || submit.isError) && (
              <Controls.Notice role="alert">
                {parseError ?? `提交判断失败：${errorMessage(submit.error)}`}
              </Controls.Notice>
            )}
            {submit.isSuccess && (
              <p role="status">判断已保存。 {submit.data.dispatch_error ?? "已请求继续执行。"}</p>
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
              提交判断并继续
            </button>
            <p role="status" className="text-xs text-text-dim">
              {draft.status === "saved"
                ? draft.started
                  ? "草稿已保存"
                  : "没有未保存的修改"
                : draft.status === "saving"
                  ? "正在保存草稿…"
                  : draft.status === "unsaved"
                    ? "草稿尚未保存"
                    : ""}
            </p>
            {options && (
              <section aria-label="判断选项" className="border-t border-line pt-3 text-sm">
                <div className="mt-3 grid gap-3">
                  <Controls.Field>
                    判断依据备注（可选）
                    <Controls.Textarea
                      value={note}
                      onChange={(event) => setNote(event.target.value)}
                      placeholder="说明此选择的实验依据"
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
          丢弃草稿（保留恢复历史）
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
        title={`测量记录 ${input.run_id}`}
        href={`/?run=${encodeURIComponent(input.run_id)}#runs`}
        rel="noreferrer"
        target="_blank"
      >
        打开测量数据
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
        分析 · {input.analysis_record_id}
      </a>
    );
  }
  if (input.kind === "interpretation") {
    return <span className={common}>判断 · {input.step_key}</span>;
  }
  if (input.kind === "parameter_publish") {
    return (
      <span
        className={common}
        title={`代次 ${input.branch.generation} · ${input.branch.revision.revision_id}`}
      >
        参数 · {input.branch.name}
      </span>
    );
  }
  return <span className={common}>配置 · {input.entry_id}</span>;
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
      <h2 className="text-lg font-semibold">{receipt ? "判断已保存" : "核实判断提交"}</h2>
      <p role="status">
        {dispatch?.failure?.message ??
          receipt?.dispatch_error ??
          (receipt
            ? "已请求继续执行。打开任务可查看进度。"
            : "任务已不再等待。请重试同一次提交，以核实已记录的回答与继续执行状态。")}
      </p>
      {retry.isError && <p role="alert">重试继续执行失败：{errorMessage(retry.error)}</p>}
      {(!receipt || retryable) && (
        <button
          className={primaryButton}
          disabled={retry.isPending}
          onClick={() => retry.mutate(submitted.command)}
        >
          {retry.isPending ? "正在重试…" : "重试继续执行"}
        </button>
      )}
      <a
        className="text-accent underline"
        href={`?procedure=${encodeURIComponent(submitted.command.procedure_run_id)}#launch`}
      >
        打开任务
      </a>
      <button className="ml-4 text-accent underline" onClick={onDismiss}>
        查看待处理判断
      </button>
    </section>
  );
}
