import { useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, CircleOff, LoaderCircle, RotateCcw } from "lucide-react";
import type {
  InstrumentCollectReceipt,
  InstrumentConfiguredDefaultsApplyReceipt,
  InstrumentInvokeReceipt,
  InstrumentSession,
  InstrumentState,
  InstrumentStateTarget,
  InstrumentView,
} from "../../api-contract";
import { ApiError } from "../../api-client";
import { titleCase } from "../../lib/presentation";
import { classes, eyebrow, primaryButton, secondaryButton } from "../../ui/styles";
import type { InstrumentPropertyDraft } from "./InstrumentPropertyInput";
import {
  applyInstrumentConfiguredDefaults,
  applyInstrumentState,
  collectInstrumentAcquisition,
  createInstrumentCommandId,
  invokeInstrumentOperation,
  readInstrumentState,
  resolveInstrumentAttention,
  retryTransientInstrumentMutation,
  type InstrumentAcquisitionTarget,
  type InstrumentOperationArgument,
  type InstrumentOperationTarget,
  type StagedInstrumentMember,
} from "./instrument-api";
import {
  AvailabilityBadge,
  configuredDefaultsReceiptMessage,
  DeviceStateCard,
  filterDrafts,
  inlineError,
  InspectorEmpty,
  instrumentStateTargets,
  interfaceMountPaths,
  InterfaceCard,
  mutationError,
  problems,
  stateMemberKey,
  receiptStyle,
  receiptTone,
  requireSession,
  stagedInstrumentMembers,
  acquisitionKey,
  operationKey,
} from "./InstrumentInterfaceControls";
import { InstrumentSessionPanel } from "./InstrumentSessionPanel";

type DraftValue = InstrumentPropertyDraft;

interface InstrumentInspectorProps {
  instrument: InstrumentView;
  session?: InstrumentSession;
  sessionError?: string;
  connectPending: boolean;
  closePending: boolean;
  onConnect: () => void;
  onClose: () => void;
  onSessionLost: (message: string) => void;
  onDisconnectOwner: () => void;
}

export function InstrumentInspector(props: InstrumentInspectorProps) {
  const { instrument, session } = props;
  const instrumentId = instrument.instrument_id;
  const connected = session?.instrument_ids.includes(instrumentId) ?? false;
  const description =
    session?.descriptions.find((candidate) => candidate.instrument_id === instrumentId) ??
    instrument.description ??
    undefined;
  const targetKey = [...instrumentStateTargets(description).keys()];
  const inspectorKey = JSON.stringify([
    instrumentId,
    connected ? session?.session_id : null,
    targetKey,
  ]);
  return <InstrumentInspectorContent key={inspectorKey} {...props} />;
}

function InstrumentInspectorContent({
  instrument,
  session,
  sessionError,
  connectPending,
  closePending,
  onConnect,
  onClose,
  onSessionLost,
  onDisconnectOwner,
}: InstrumentInspectorProps) {
  const queryClient = useQueryClient();
  const instrumentId = instrument.instrument_id;
  const connected = session?.instrument_ids.includes(instrumentId) ?? false;
  const description =
    session?.descriptions.find((candidate) => candidate.instrument_id === instrumentId) ??
    instrument.description ??
    undefined;
  const synchronizedState = session?.observed_state.find(
    (snapshot) => snapshot.instrument_id === instrumentId,
  );
  const [readState, setReadState] = useState<{
    source?: InstrumentState;
    value: InstrumentState;
  }>();
  const state =
    connected && readState !== undefined && readState.source === synchronizedState
      ? readState.value
      : synchronizedState;
  const [drafts, setDrafts] = useState<Record<string, DraftValue>>({});
  const [applyResult, setApplyResult] = useState<string>();
  const [configuredDefaultsResult, setConfiguredDefaultsResult] =
    useState<InstrumentConfiguredDefaultsApplyReceipt>();
  const [collectResults, setCollectResults] = useState<Record<string, InstrumentCollectReceipt>>(
    {},
  );
  const [invokeResults, setInvokeResults] = useState<Record<string, InstrumentInvokeReceipt>>({});
  const applyCommandIdRef = useRef<string | undefined>(undefined);
  const configuredDefaultsOperationIdRef = useRef<string | undefined>(undefined);
  const collectCommandIdsRef = useRef<Record<string, string>>({});
  const invokeCommandIdsRef = useRef<Record<string, string>>({});

  const hasConfiguredDefaults =
    session?.configured_default_instrument_ids.includes(instrumentId) ?? false;

  const readMutation = useMutation({
    mutationFn: () => readInstrumentState(requireSession(session), instrumentId),
    onSuccess: (value) => setReadState({ source: synchronizedState, value }),
  });
  const declaredTargets = useMemo(() => instrumentStateTargets(description), [description]);
  const declaredDrafts = useMemo(
    () => filterDrafts(drafts, declaredTargets),
    [declaredTargets, drafts],
  );
  const stagedMembers = useMemo(
    () => stagedInstrumentMembers(declaredDrafts, declaredTargets),
    [declaredDrafts, declaredTargets],
  );
  const stagedCount = Object.keys(declaredDrafts).length;
  const invalidDraft = Object.values(declaredDrafts).some((draft) => draft.value === undefined);
  const applyMutation = useMutation({
    mutationFn: ({
      members,
      commandId,
    }: {
      members: StagedInstrumentMember[];
      commandId: string;
    }) => applyInstrumentState(requireSession(session), instrumentId, members, commandId),
    retry: retryTransientInstrumentMutation,
    retryDelay: 250,
    onSuccess: (receipt) => {
      applyCommandIdRef.current = undefined;
      setApplyResult(receipt.status);
      if (receipt.status === "applied") {
        collectCommandIdsRef.current = {};
        invokeCommandIdsRef.current = {};
        setCollectResults({});
        setInvokeResults({});
        setDrafts({});
        readMutation.mutate();
      } else if (receipt.status === "unknown") {
        onSessionLost(
          "The apply result is unknown. The daemon quarantined this session for operator review.",
        );
      }
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
  });
  const configuredDefaultsMutation = useMutation({
    mutationFn: ({ operationId }: { operationId: string }) =>
      applyInstrumentConfiguredDefaults(requireSession(session), instrumentId, operationId),
    retry: retryTransientInstrumentMutation,
    retryDelay: 250,
    onSuccess: (receipt) => {
      configuredDefaultsOperationIdRef.current = undefined;
      setConfiguredDefaultsResult(receipt);
      if (receipt.status === "applied" || receipt.status === "unchanged") {
        applyCommandIdRef.current = undefined;
        collectCommandIdsRef.current = {};
        invokeCommandIdsRef.current = {};
        setApplyResult(undefined);
        setDrafts({});
        setCollectResults({});
        setInvokeResults({});
        readMutation.mutate();
      }
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
    onError: async (cause) => {
      await queryClient.invalidateQueries({ queryKey: ["instruments"] });
      if (!(cause instanceof ApiError) || ![404, 409].includes(cause.status ?? 0)) {
        return;
      }
      if (!session) return;
      const canonical = queryClient
        .getQueryData<{ items: InstrumentView[] }>([
          "instruments",
          session.setup.revision_id,
          session.setup.content_hash,
        ])
        ?.items.find((candidate) => candidate.instrument_id === instrumentId);
      const stillOwned =
        canonical?.availability === "active" &&
        canonical.owner_kind === "instrument_session" &&
        canonical.owner_id === session?.session_id;
      if (canonical && !stillOwned) {
        onSessionLost(
          canonical?.availability === "quarantined"
            ? "The daemon quarantined this instrument while applying configured defaults."
            : "The interactive session ended while applying configured defaults.",
        );
      }
    },
  });
  const collectMutation = useMutation({
    mutationFn: ({
      target,
      commandId,
    }: {
      target: InstrumentAcquisitionTarget;
      commandId: string;
    }) => collectInstrumentAcquisition(requireSession(session), instrumentId, target, commandId),
    retry: retryTransientInstrumentMutation,
    retryDelay: 250,
    onSuccess: (receipt, { target }) => {
      const key = acquisitionKey(target);
      delete collectCommandIdsRef.current[key];
      setCollectResults((current) => ({ ...current, [key]: receipt }));
      if (receipt.status === "unknown") {
        onSessionLost(
          "The collection result is unknown. The daemon quarantined this session for operator review.",
        );
      }
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
  });
  const invokeMutation = useMutation({
    mutationFn: ({
      target,
      operationArguments,
      commandId,
    }: {
      target: InstrumentOperationTarget;
      operationArguments: InstrumentOperationArgument[];
      commandId: string;
    }) =>
      invokeInstrumentOperation(
        requireSession(session),
        instrumentId,
        target,
        operationArguments,
        commandId,
      ),
    retry: retryTransientInstrumentMutation,
    retryDelay: 250,
    onSuccess: (receipt, { target }) => {
      const key = operationKey(target);
      delete invokeCommandIdsRef.current[key];
      setInvokeResults((current) => ({ ...current, [key]: receipt }));
      if (receipt.status === "invoked") {
        collectCommandIdsRef.current = {};
        setCollectResults({});
        readMutation.mutate();
      } else if (receipt.status === "unknown") {
        onSessionLost(
          "The operation result is unknown. The daemon quarantined this session for operator review.",
        );
      }
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
  });
  const resolveMutation = useMutation({
    mutationFn: () => {
      if (instrument.owner_kind !== "instrument_session" || !instrument.owner_id) {
        throw new Error("This attention state is not owned by an interactive session.");
      }
      return resolveInstrumentAttention(instrument.owner_id);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
  });

  const stage = (target: InstrumentStateTarget, draft?: DraftValue) => {
    const key = stateMemberKey(target);
    applyCommandIdRef.current = undefined;
    setDrafts((current) => {
      const next = { ...current };
      if (draft === undefined) delete next[key];
      else next[key] = draft;
      return next;
    });
    setApplyResult(undefined);
    applyMutation.reset();
  };
  const applyStaged = () => {
    applyCommandIdRef.current ??= createInstrumentCommandId("apply");
    applyMutation.mutate({
      members: stagedMembers,
      commandId: applyCommandIdRef.current,
    });
  };
  const resetStaged = () => {
    applyCommandIdRef.current = undefined;
    setDrafts({});
    applyMutation.reset();
  };
  const refreshState = () => {
    applyCommandIdRef.current = undefined;
    configuredDefaultsOperationIdRef.current = undefined;
    collectCommandIdsRef.current = {};
    invokeCommandIdsRef.current = {};
    setDrafts({});
    setApplyResult(undefined);
    setConfiguredDefaultsResult(undefined);
    setCollectResults({});
    setInvokeResults({});
    readMutation.mutate();
  };
  const applyConfiguredDefaults = () => {
    configuredDefaultsOperationIdRef.current ??= createInstrumentCommandId("configured-defaults");
    configuredDefaultsMutation.mutate({
      operationId: configuredDefaultsOperationIdRef.current,
    });
  };
  const collectAcquisition = (target: InstrumentAcquisitionTarget) => {
    const key = acquisitionKey(target);
    const commandId = collectCommandIdsRef.current[key] ?? createInstrumentCommandId("collect");
    collectCommandIdsRef.current[key] = commandId;
    collectMutation.mutate({
      target,
      commandId,
    });
  };
  const invokeOperation = (
    target: InstrumentOperationTarget,
    operationArguments: InstrumentOperationArgument[],
  ) => {
    const key = operationKey(target);
    const commandId = invokeCommandIdsRef.current[key] ?? createInstrumentCommandId("invoke");
    invokeCommandIdsRef.current[key] = commandId;
    invokeMutation.mutate({ target, operationArguments, commandId });
  };
  const editOperation = (target: InstrumentOperationTarget) => {
    const key = operationKey(target);
    delete invokeCommandIdsRef.current[key];
    setInvokeResults((current) => {
      if (!(key in current)) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
    invokeMutation.reset();
  };
  const interactionError =
    sessionError ??
    mutationError(readMutation.error) ??
    mutationError(applyMutation.error) ??
    mutationError(configuredDefaultsMutation.error) ??
    mutationError(collectMutation.error) ??
    mutationError(invokeMutation.error) ??
    mutationError(resolveMutation.error);
  const interactionPending =
    readMutation.isPending ||
    applyMutation.isPending ||
    configuredDefaultsMutation.isPending ||
    collectMutation.isPending ||
    invokeMutation.isPending;
  const interactionDisabled = closePending || interactionPending;
  const configuredDefaultsDisabled =
    interactionDisabled || stagedCount > 0 || resolveMutation.isPending;
  const configuredDefaultsDisabledReason =
    stagedCount > 0
      ? "Apply or reset staged properties first"
      : configuredDefaultsDisabled
        ? "Wait for the current instrument interaction to finish"
        : undefined;

  return (
    <section
      className="relative grid min-h-[640px] min-w-0 content-start gap-3.5 p-5 max-[880px]:min-h-[560px] max-[880px]:rounded-lg max-[880px]:border max-[880px]:border-line max-[680px]:px-[13px] max-[680px]:py-4"
      aria-live="polite"
    >
      <header className="flex items-start justify-between gap-[18px] border-b border-line pb-3.5 max-[460px]:grid">
        <div>
          <div className="flex items-center gap-2.5">
            <h2 className="m-0 text-[1.1rem] font-[650] tracking-[-0.025em]">
              {description?.label ?? instrumentId}
            </h2>
            <AvailabilityBadge availability={instrument.availability} connected={connected} />
          </div>
          <code className="mt-1 block text-[0.61rem] text-text-dim">{instrumentId}</code>
          {description?.description && (
            <p className="mt-[9px] mb-0 max-w-[760px] text-[0.68rem] leading-[1.55] text-text-dim">
              {description.description}
            </p>
          )}
        </div>
      </header>

      <InstrumentSessionPanel
        instrument={instrument}
        session={session}
        connected={connected}
        connectPending={connectPending}
        closePending={closePending}
        interactionPending={interactionPending}
        refreshPending={readMutation.isPending}
        configuredDefaultsVisible={hasConfiguredDefaults}
        configuredDefaultsPending={configuredDefaultsMutation.isPending}
        configuredDefaultsDisabled={configuredDefaultsDisabled}
        configuredDefaultsDisabledReason={configuredDefaultsDisabledReason}
        resolvePending={resolveMutation.isPending}
        onConnect={onConnect}
        onClose={onClose}
        onDisconnectOwner={onDisconnectOwner}
        onRefresh={refreshState}
        onApplyConfiguredDefaults={applyConfiguredDefaults}
        onResolve={() => resolveMutation.mutate()}
      />

      {interactionError && (
        <div className={inlineError} role="alert">
          <AlertTriangle size={15} />
          {interactionError}
        </div>
      )}

      {configuredDefaultsResult && (
        <p
          className={classes(receiptStyle, receiptTone(configuredDefaultsResult.status))}
          role="status"
        >
          {configuredDefaultsReceiptMessage(configuredDefaultsResult)}
        </p>
      )}

      {(instrument.problems ?? []).length > 0 && (
        <div className={problems} role="status">
          <strong className="mb-1 block">Driver is unavailable</strong>
          {instrument.problems.map((problem, index) => (
            <p className="my-0.5" key={`${problem.code}-${index}`}>
              {problem.message}
            </p>
          ))}
        </div>
      )}

      {description ? (
        <>
          <div
            className="mt-[3px] flex items-end justify-between max-[460px]:flex-col max-[460px]:items-start"
            data-testid="interface-heading"
          >
            <div>
              <span className={eyebrow}>Device controls</span>
              <h3 className="m-0 text-[0.9rem] font-[650]">Interfaces</h3>
            </div>
          </div>

          {(description.interfaces ?? []).length > 0 ||
          (description.device_schemas ?? []).length > 0 ? (
            <div className="grid gap-2.5">
              {(description.interfaces ?? []).flatMap((instrumentInterface) =>
                interfaceMountPaths(description, instrumentInterface.id).map((mountPath) => (
                  <InterfaceCard
                    key={`${instrumentInterface.id}:${mountPath.join("/")}`}
                    description={description}
                    instrumentInterface={instrumentInterface}
                    mountPath={mountPath}
                    connected={connected}
                    interactionDisabled={interactionDisabled}
                    state={state}
                    drafts={declaredDrafts}
                    collectResults={collectResults}
                    invokeResults={invokeResults}
                    collectingTarget={
                      collectMutation.isPending ? collectMutation.variables?.target : undefined
                    }
                    invokingTarget={
                      invokeMutation.isPending ? invokeMutation.variables?.target : undefined
                    }
                    onStage={(target, draft) => stage(target, draft)}
                    onCollect={(target) => collectAcquisition(target)}
                    onInvoke={(target, operationArguments) =>
                      invokeOperation(target, operationArguments)
                    }
                    onOperationEdit={editOperation}
                  />
                )),
              )}
              {(description.device_schemas ?? []).map((deviceState) => (
                <DeviceStateCard
                  key={deviceState.id}
                  deviceState={deviceState}
                  connected={connected}
                  interactionDisabled={interactionDisabled}
                  state={state}
                  drafts={declaredDrafts}
                  onStage={(target, draft) => stage(target, draft)}
                />
              ))}
            </div>
          ) : (
            <InspectorEmpty
              icon={<CircleOff />}
              title="No interactive interfaces"
              detail="This device does not declare interactive properties, operations, or acquisitions."
            />
          )}

          {connected && stagedCount > 0 && (
            <div className="sticky bottom-2.5 z-[5] grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-2 rounded-md border border-[rgb(128_163_207_/_35%)] bg-[color-mix(in_srgb,var(--color-panel-strong)_94%,transparent)] px-[11px] py-2.5 shadow-[0_10px_30px_rgb(0_0_0_/_28%)] backdrop-blur-[12px] max-[460px]:grid-cols-2">
              <div className="grid gap-0.5 max-[460px]:col-span-full">
                <strong className="text-[0.67rem]">
                  {stagedCount} staged {stagedCount === 1 ? "property" : "properties"}
                </strong>
                <small className="text-[0.54rem] text-text-dim">
                  Values remain local until you apply the complete staged change once.
                </small>
              </div>
              <button
                type="button"
                className={secondaryButton}
                onClick={resetStaged}
                disabled={applyMutation.isPending}
              >
                <RotateCcw size={14} />
                Reset
              </button>
              <button
                type="button"
                className={primaryButton}
                onClick={applyStaged}
                disabled={interactionDisabled || invalidDraft || stagedMembers.length === 0}
                title={
                  interactionDisabled
                    ? "Wait for the current instrument interaction to finish"
                    : invalidDraft
                      ? "Correct invalid staged values before applying"
                      : undefined
                }
              >
                {applyMutation.isPending ? (
                  <LoaderCircle className="animate-spin" size={14} />
                ) : (
                  <Check size={14} />
                )}
                Apply staged
              </button>
            </div>
          )}
          {applyResult && (
            <p className={classes(receiptStyle, receiptTone(applyResult))}>
              Apply receipt: {titleCase(applyResult)}
            </p>
          )}
        </>
      ) : (
        <InspectorEmpty
          icon={<CircleOff />}
          title="Device controls unavailable"
          detail="Review the listed problem or edit this instrument’s connection configuration."
        />
      )}
    </section>
  );
}
