import { useRef } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { getInstrumentFinishPlan, finishInstrumentSession } from "./instrument-finish-api";
import { createInstrumentCommandId } from "./instrument-api";
import { secondaryButton } from "../../ui/styles";

export function InstrumentFinishChoice({
  sessionId,
  onFinished,
  onCancel,
}: {
  sessionId: string;
  onFinished: () => void;
  onCancel: () => void;
}) {
  const selected = useRef<
    { operation_id: string; mode: "keep_current" | "configured_finish" } | undefined
  >(undefined);
  const plan = useQuery({
    queryKey: ["instrument-finish-plan", sessionId],
    queryFn: () => getInstrumentFinishPlan(sessionId),
  });
  const finish = useMutation({
    mutationKey: ["device-control", sessionId],
    mutationFn: (mode: "keep_current" | "configured_finish") => {
      selected.current ??= { operation_id: createInstrumentCommandId("finish"), mode };
      return finishInstrumentSession(sessionId, selected.current);
    },
    onSuccess: onFinished,
  });
  return (
    <section
      role="dialog"
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
          finish.isPending ||
          (finish.variables !== undefined && finish.variables !== "keep_current")
        }
        onClick={() => finish.mutate("keep_current")}
      >
        保持当前状态并结束
      </button>
      <p>关闭 / 归零按该会话固定的设备收尾配置执行，影响范围如下；动作回执不是模拟量读回证明。</p>
      {plan.data?.actions.map((action) => (
        <div key={action.instrument_id} className="text-xs">
          <strong>{action.instrument_id}</strong>
          {action.states.map((state, index) => (
            <p key={index}>
              {JSON.stringify(state.target)} → {JSON.stringify(state.value)}
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
      {!plan.data?.configured_finish_available && (
        <p>
          没有完整的已配置收尾动作，不能自动猜测关闭或归零。请取消后使用已声明的设备控制明确处理。
        </p>
      )}
      <button
        type="button"
        className={secondaryButton}
        disabled={
          !plan.data?.configured_finish_available ||
          finish.isPending ||
          (finish.variables !== undefined && finish.variables !== "configured_finish")
        }
        onClick={() => finish.mutate("configured_finish")}
      >
        执行上述关闭 / 归零配置并结束
      </button>
      {(plan.error || finish.error) && (
        <p role="alert">
          {plan.error?.message ?? finish.error?.message}
          。控制结束未确认，物理输出状态未知；不要自动重发设备动作。
        </p>
      )}
      <button
        type="button"
        className={secondaryButton}
        disabled={finish.isPending || finish.variables !== undefined}
        onClick={onCancel}
      >
        继续控制
      </button>
    </section>
  );
}
