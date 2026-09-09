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
            <strong className="text-[0.7rem] text-text-soft">Interactive session connected</strong>
            <small className="overflow-hidden text-[0.58rem] leading-[1.4] text-ellipsis text-text-dim">
              Opened {formatRelative(session.opened_at)}
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
              Apply configured defaults
            </button>
          )}
          <button
            type="button"
            className={secondaryButton}
            onClick={onRefresh}
            disabled={interactionPending || closePending}
            title={
              interactionPending
                ? "Wait for the current instrument interaction to finish"
                : undefined
            }
          >
            <RefreshCw className={refreshPending ? "animate-spin" : undefined} size={14} />
            Refresh state
          </button>
          <button
            type="button"
            className={secondaryButton}
            onClick={onClose}
            disabled={closePending || interactionPending}
            title={
              interactionPending
                ? "Wait for the current instrument interaction to finish"
                : undefined
            }
          >
            {closePending ? (
              <LoaderCircle className="animate-spin" size={14} />
            ) : (
              <Unplug size={14} />
            )}
            Disconnect
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
          <strong className="text-[0.7rem] text-text-soft">Operator resolution required</strong>
          <p className={attentionCopy}>
            The previous operation left hardware state uncertain. Automatic reuse is blocked.
          </p>
          <OwnerDescription instrument={instrument} />
        </div>
        <button
          type="button"
          className={secondaryButton}
          onClick={onResolve}
          disabled={!canResolve || resolvePending}
          title={
            canResolve
              ? "Acknowledge the quarantined interactive session and release it"
              : "Resolve this owner from its run workflow"
          }
        >
          {resolvePending ? (
            <LoaderCircle className="animate-spin" size={14} />
          ) : (
            <ShieldAlert size={14} />
          )}
          Resolve quarantine
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
          <strong className="text-[0.7rem] text-text-soft">Manual controls unavailable</strong>
          <p className={attentionCopy}>
            {instrument.owner_kind === "run"
              ? "This instrument is in use. Wait for the run to finish, or request a stop from its run page and wait for cleanup before reconnecting."
              : "Another manual session is connected. Finish or disconnect that session before reconnecting here."}
          </p>
          <OwnerDescription instrument={instrument} />
        </div>
        {canDisconnectSession && (
          <button
            type="button"
            className={secondaryButton}
            onClick={onDisconnectOwner}
            disabled={closePending}
            title="Disconnect this daemon-owned interactive session"
          >
            {closePending ? (
              <LoaderCircle className="animate-spin" size={14} />
            ) : (
              <Unplug size={14} />
            )}
            Disconnect session
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
          <strong className="text-[0.7rem] text-text-soft">Connection unavailable</strong>
          <p className={attentionCopy}>
            Fix the provider or connection configuration before opening a session.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className={sessionPanel}>
      <div className="grid min-w-0 flex-1 gap-1 max-[680px]:w-full max-[680px]:basis-full">
        <strong className="text-[0.7rem] text-text-soft">
          Connect only when you are ready to interact
        </strong>
        <small className="overflow-hidden text-[0.58rem] leading-[1.4] text-ellipsis text-text-dim">
          Connect to read or adjust this instrument. Disconnect when finished so an experiment can
          use it. Selecting it does not connect.
        </small>
      </div>
      <button type="button" className={primaryButton} onClick={onConnect} disabled={connectPending}>
        {connectPending ? <LoaderCircle className="animate-spin" size={14} /> : <Cable size={14} />}
        Connect
      </button>
    </div>
  );
}

function OwnerDescription({ instrument }: { instrument: InstrumentView }) {
  const ownerKind = instrument.owner_kind === "run" ? "Run" : "Interactive session";
  return <small className="mt-[7px] block text-[0.59rem] text-text-dim">{ownerKind}</small>;
}

const sessionPanel =
  "flex min-h-16 items-center gap-3.5 rounded-md border border-line bg-panel-soft px-[13px] py-[11px] max-[680px]:flex-wrap max-[680px]:items-stretch";
const attentionPanel =
  "flex min-h-16 items-start gap-3.5 rounded-md border border-[rgb(207_173_104_/_24%)] bg-yellow-soft px-[13px] py-[11px] text-yellow max-[680px]:flex-wrap max-[680px]:items-stretch";
const attentionCopy = "mt-1 mb-0 text-[0.62rem] leading-normal text-text-dim";
