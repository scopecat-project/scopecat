import { useQuery } from "@tanstack/react-query";
import { getInstrumentFinishPlan } from "./instrument-finish-api";
import {
  useInstrumentFinishRecovery,
  type InstrumentFinishRecovery,
} from "./instrument-finish-recovery";
import { secondaryButton } from "../../ui/styles";

export function InstrumentFinishChoice({
  sessionId,
  onFinished,
  onCancel,
  disabled = false,
  cancelDisabled = false,
  embedded = false,
  onStarted,
  finishRecovery,
  sessionUnavailable = false,
}: {
  sessionId: string;
  onFinished: () => void;
  onCancel: () => void;
  disabled?: boolean;
  cancelDisabled?: boolean;
  embedded?: boolean;
  onStarted?: () => void;
  finishRecovery?: InstrumentFinishRecovery;
  sessionUnavailable?: boolean;
}) {
  const localRecovery = useInstrumentFinishRecovery(sessionId);
  const recovery = finishRecovery ?? localRecovery;
  const state = recovery.state;
  const fixed = state && state.phase !== "rejected" ? state.command : undefined;
  const busy = state?.phase === "pending";
  const confirmed = state?.phase === "confirmed";
  const plan = useQuery({
    queryKey: ["instrument-finish-plan", sessionId],
    queryFn: () => getInstrumentFinishPlan(sessionId),
    enabled: !sessionUnavailable,
  });
  const start = (mode: "keep_current" | "configured_finish") => {
    onStarted?.();
    recovery.start(mode, onFinished);
  };
  return (
    <section
      role={embedded ? undefined : "dialog"}
      aria-label="结束直接控制"
      className="grid gap-2 rounded border border-line p-3"
    >
      <h3>结束直接控制</h3>
      <p>
        本次已执行设备动作。保持当前只释放控制权，不额外发送输出命令，也不恢复进入前状态。物理连接的后续释放或应用退出仍遵循驱动的独立生命周期。
      </p>
      <button
        type="button"
        className={secondaryButton}
        disabled={
          disabled ||
          busy ||
          confirmed ||
          (sessionUnavailable && !fixed) ||
          (!!fixed && fixed.mode !== "keep_current")
        }
        onClick={() => start("keep_current")}
      >
        {state?.phase === "unknown" && fixed?.mode === "keep_current"
          ? "核查原收尾结果（保持当前状态）"
          : "保持当前状态并结束"}
      </button>
      <p>关闭 / 归零按该会话固定的设备收尾配置执行，影响范围如下；动作回执不是模拟量读回证明。</p>
      {plan.data?.actions.map((action) => (
        <div key={action.instrument_id} className="text-xs">
          <strong>{action.instrument_id}</strong>
          {action.states.map((setting, index) => (
            <p key={index}>
              {JSON.stringify(setting.target)} → {JSON.stringify(setting.value)}
            </p>
          ))}
          {action.operations.map((operation, index) => (
            <p key={index}>
              {operation.interface_id} / {operation.component_path?.join("/") || "设备"} /{" "}
              {operation.operation_id} {JSON.stringify(operation.arguments)}
            </p>
          ))}
        </div>
      ))}
      {!plan.isPending &&
        !plan.data?.configured_finish_available &&
        !fixed &&
        !sessionUnavailable && (
          <p>
            没有完整的已配置收尾动作，不能自动猜测关闭或归零。请取消后使用已声明的设备控制明确处理。
          </p>
        )}
      <button
        type="button"
        className={secondaryButton}
        disabled={
          disabled ||
          (!fixed && (!plan.data?.configured_finish_available || sessionUnavailable)) ||
          busy ||
          confirmed ||
          (!!fixed && fixed.mode !== "configured_finish")
        }
        onClick={() => start("configured_finish")}
      >
        {state?.phase === "unknown" && fixed?.mode === "configured_finish"
          ? "核查原收尾结果（关闭 / 归零配置）"
          : "执行上述关闭 / 归零配置并结束"}
      </button>
      {busy && <p role="status">正在等待收尾回执；取消等待不会撤销已提交的动作。</p>}
      {state?.phase === "rejected" && (
        <p role="alert">收尾未被接纳：{state.error}。没有固定收尾选择，可继续控制或重新选择。</p>
      )}
      {state?.phase === "unknown" && (
        <p role="alert">
          {state.error}。控制结束未确认，物理输出状态未知。只能用原收尾选择与原操作 ID
          核查；不会自动重发设备动作。
        </p>
      )}
      {plan.error && !state && (
        <p role="alert">无法读取收尾配置：{plan.error.message}。可继续控制或重试读取配置。</p>
      )}
      {plan.error && !sessionUnavailable && (
        <button type="button" className={secondaryButton} onClick={() => void plan.refetch()}>
          重新读取收尾配置
        </button>
      )}
      {confirmed && (
        <>
          <p role="status">原收尾已确认结束；取消等待没有撤销该操作，也没有自动关闭窗口。</p>
          <button
            type="button"
            className={secondaryButton}
            disabled={disabled}
            onClick={onFinished}
          >
            确认结束控制
          </button>
        </>
      )}
      <button
        type="button"
        className={secondaryButton}
        disabled={cancelDisabled}
        onClick={() => {
          recovery.cancelWaiting();
          onCancel();
        }}
      >
        {fixed ? "返回工作台（保留待核查状态）" : "继续控制"}
      </button>
    </section>
  );
}
