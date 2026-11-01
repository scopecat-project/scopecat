import {
  Cable,
  CircleOff,
  Database,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  ShieldAlert,
  Unplug,
} from "lucide-react";
import type { InstrumentSession, InstrumentView } from "../../api-contract";
import { formatRelative } from "../../lib/presentation";
import { classes, primaryButton, secondaryButton } from "../../ui/styles";

export function InstrumentSessionPanel({
  instrument,
  session,
  connected,
  connectPending,
  closePending,
  interactionPending,
  refreshPending,
  stateAvailable,
  configuredDefaultsVisible,
  configuredDefaultsPending,
  configuredDefaultsDisabled,
  configuredDefaultsDisabledReason,
  resolvePending,
  onConnect,
  onClose,
  onDisconnectOwner,
  onRefresh,
  onApplyConfiguredDefaults,
  onResolve,
}: {
  instrument: InstrumentView;
  session?: InstrumentSession;
  connected: boolean;
  connectPending: boolean;
  closePending: boolean;
  interactionPending: boolean;
  refreshPending: boolean;
  stateAvailable: boolean;
  configuredDefaultsVisible: boolean;
  configuredDefaultsPending: boolean;
  configuredDefaultsDisabled: boolean;
  configuredDefaultsDisabledReason?: string;
  resolvePending: boolean;
  onConnect: () => void;
  onClose: () => void;
  onDisconnectOwner: () => void;
  onRefresh: () => void;
  onApplyConfiguredDefaults: () => void;
  onResolve: () => void;
}) {
  if (connected && session) {
    return (
      <div className={classes(sessionPanel, "border-[rgb(128_163_207_/_24%)] bg-accent-soft")}>
        <div className="flex min-w-0 flex-1 items-center gap-2.5 max-[680px]:w-full max-[680px]:basis-full">
          <span
            className="grid size-8 place-items-center rounded-sm bg-[rgb(128_163_207_/_10%)] text-accent"
            aria-hidden="true"
          >
            <Cable size={16} />
          </span>
          <div className="grid gap-1">
            <strong className="text-xs text-text-soft">交互会话已连接</strong>
            <small className="overflow-hidden text-xs leading-[1.4] text-ellipsis text-text-dim">
              打开时间 {formatRelative(session.opened_at)}
            </small>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-[7px] max-[460px]:w-full max-[460px]:[&>button]:flex-1">
          {configuredDefaultsVisible && (
            <button
              type="button"
              className={secondaryButton}
              onClick={onApplyConfiguredDefaults}
              disabled={configuredDefaultsDisabled}
              title={configuredDefaultsDisabledReason}
            >
              {configuredDefaultsPending ? (
                <LoaderCircle className="animate-spin" size={14} />
              ) : (
                <RotateCcw size={14} />
              )}
              应用已配置默认值
            </button>
          )}
          <button
            type="button"
            className={secondaryButton}
            onClick={onRefresh}
            disabled={interactionPending || closePending || !stateAvailable}
            title={
              !stateAvailable
                ? "此驱动未声明可读状态；请通过设备前面板或驱动支持的操作核对。"
                : interactionPending
                  ? "请等待当前仪器交互完成"
                  : undefined
            }
          >
            <RefreshCw className={refreshPending ? "animate-spin" : undefined} size={14} />
            刷新状态
          </button>
          <button
            type="button"
            className={secondaryButton}
            onClick={onClose}
            disabled={closePending || interactionPending}
            title={interactionPending ? "请等待当前仪器交互完成" : undefined}
          >
            {closePending ? (
              <LoaderCircle className="animate-spin" size={14} />
            ) : (
              <Unplug size={14} />
            )}
            释放控制
          </button>
        </div>
      </div>
    );
  }

  if (instrument.availability === "quarantined") {
    const canResolve =
      instrument.owner_kind === "instrument_session" && Boolean(instrument.owner_id);
    return (
      <div className={attentionPanel}>
        <ShieldAlert className="mt-0.5 flex-none" size={19} aria-hidden="true" />
        <div className="flex-1 max-[680px]:w-full max-[680px]:basis-full">
          <strong className="text-xs text-text-soft">需要操作者处理</strong>
          <p className={attentionCopy}>上次操作后硬件状态不确定，已阻止自动复用。</p>
          <OwnerDescription instrument={instrument} />
        </div>
        <button
          type="button"
          className={secondaryButton}
          onClick={onResolve}
          disabled={!canResolve || resolvePending}
          title={canResolve ? "确认已隔离的交互会话并释放" : "请从所属运行流程处理此占用方"}
        >
          {resolvePending ? (
            <LoaderCircle className="animate-spin" size={14} />
          ) : (
            <ShieldAlert size={14} />
          )}
          处理隔离
        </button>
      </div>
    );
  }

  if (instrument.availability === "active") {
    const canDisconnectSession =
      instrument.owner_kind === "instrument_session" && Boolean(instrument.owner_id);
    return (
      <div className={attentionPanel}>
        <Database className="mt-0.5 flex-none" size={18} aria-hidden="true" />
        <div className="flex-1 max-[680px]:w-full max-[680px]:basis-full">
          <strong className="text-xs text-text-soft">手动控制不可用</strong>
          <p className={attentionCopy}>
            {instrument.owner_kind === "run"
              ? "此仪器正在使用。请等待运行完成，或在运行页请求停止并等待清理完成后再连接。"
              : "另一个手动会话已连接。请先完成或断开该会话，再从此处连接。"}
          </p>
          <OwnerDescription instrument={instrument} />
        </div>
        {canDisconnectSession && (
          <button
            type="button"
            className={secondaryButton}
            onClick={onDisconnectOwner}
            disabled={closePending}
            title="断开此后台服务持有的交互会话"
          >
            {closePending ? (
              <LoaderCircle className="animate-spin" size={14} />
            ) : (
              <Unplug size={14} />
            )}
            断开会话
          </button>
        )}
      </div>
    );
  }

  if (instrument.availability === "unavailable") {
    return (
      <div
        className={classes(attentionPanel, "border-[rgb(215_126_121_/_24%)] bg-red-soft text-red")}
      >
        <CircleOff className="mt-0.5 flex-none" size={18} aria-hidden="true" />
        <div className="flex-1">
          <strong className="text-xs text-text-soft">连接不可用</strong>
          <p className={attentionCopy}>打开会话前请修正 provider 或连接配置。</p>
        </div>
      </div>
    );
  }

  return (
    <div className={sessionPanel}>
      <div className="grid min-w-0 flex-1 gap-1 max-[680px]:w-full max-[680px]:basis-full">
        <strong className="text-xs text-text-soft">准备交互时再连接</strong>
        <small className="overflow-hidden text-xs leading-[1.4] text-ellipsis text-text-dim">
          连接后可读取或调整仪器。完成后释放控制，让实验可以使用它。选中设备不会连接。
        </small>
      </div>
      <button type="button" className={primaryButton} onClick={onConnect} disabled={connectPending}>
        {connectPending ? <LoaderCircle className="animate-spin" size={14} /> : <Cable size={14} />}
        连接
      </button>
    </div>
  );
}

function OwnerDescription({ instrument }: { instrument: InstrumentView }) {
  const ownerKind = instrument.owner_kind === "run" ? "运行" : "交互会话";
  return <small className="mt-[7px] block text-xs text-text-dim">{ownerKind}</small>;
}

const sessionPanel =
  "flex min-h-16 items-center gap-3.5 rounded-md border border-line bg-panel-soft px-[13px] py-[11px] max-[680px]:flex-wrap max-[680px]:items-stretch";
const attentionPanel =
  "flex min-h-16 items-start gap-3.5 rounded-md border border-[rgb(207_173_104_/_24%)] bg-yellow-soft px-[13px] py-[11px] text-yellow max-[680px]:flex-wrap max-[680px]:items-stretch";
const attentionCopy = "mt-1 mb-0 text-xs leading-normal text-text-dim";
