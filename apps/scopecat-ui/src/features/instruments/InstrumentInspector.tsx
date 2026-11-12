import { measurementKey, sameMeasurement } from "./measurement-identity";
import { usePendingMeasurements } from "./use-pending-measurements";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
import { classes, eyebrow, primaryButton, secondaryButton } from "../../ui/styles";
import type { InstrumentPropertyDraft } from "./InstrumentPropertyInput";
import {
  applyInstrumentConfiguredDefaults,
  applyInstrumentState,
  collectInstrumentAcquisition,
  createInstrumentCommandId,
  invokeInstrumentOperation,
  readInstrumentState,
  readSharedInstrumentObservation,
  resolveInstrumentAttention,
  retryTransientInstrumentMutation,
  type InstrumentAcquisitionTarget,
  type InstrumentOperationArgument,
  type InstrumentOperationTarget,
  type StagedInstrumentMember,
} from "./instrument-api";
import {
  AvailabilityBadge,
  AuthorWorkflowLinks,
  configuredDefaultsReceiptMessage,
  instrumentReceiptLabel,
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
import { DevicePythonGuide } from "./DevicePythonGuide";
import { type PendingMeasurement, type MeasurementSave } from "./DirectMeasurements";
import { InstrumentExitDialog } from "./InstrumentExitDialog";
import { type CurrentMeasurementResult } from "./CurrentMeasurement";
import {
  MeasurementWorkbench,
  MeasurementAcquisitionActions,
  acquisitionTargets,
} from "./MeasurementWorkbench";
import { InstrumentControlLayout } from "./InstrumentControlLayout";
import { InstrumentSessionPanel } from "./InstrumentSessionPanel";
import { useInstrumentFinishRecovery } from "./instrument-finish-recovery";
import { getInstrumentSessionRecovery } from "./instrument-session-recovery";

type DraftValue = InstrumentPropertyDraft;

interface InstrumentInspectorProps {
  instrument: InstrumentView;
  setup?: InstrumentSession["setup"];
  displayLabel?: string;
  hideHeading?: boolean;
  registeredDeviceId?: string;
  configuration?: React.ReactNode;
  releaseUnconfirmed?: boolean;
  session?: InstrumentSession;
  recoverySession?: InstrumentSession;
  sessionError?: string;
  sessionErrorBlocksClose?: boolean;
  connectPending: boolean;
  closePending: boolean;
  onConnect: () => void;
  onClose: () => void;
  onRecoveryFinished?: () => void;
  onRestoreSession?: () => void;
  restorePending?: boolean;
  onSessionLost: (message: string) => void;
  onDisconnectOwner: () => void;
  exitRequest?: number;
  onExitCancelled?: () => void;
  onCloseProblem?: (problem: boolean) => void;
}

export function InstrumentInspector(props: InstrumentInspectorProps) {
  const [currentMeasurement, setCurrentMeasurement] = useState<CurrentMeasurementResult>();
  const { instrument, session, recoverySession } = props;
  const instrumentId = instrument.instrument_id;
  const retainedSession = session ?? recoverySession;
  const description =
    retainedSession?.descriptions.find((candidate) => candidate.instrument_id === instrumentId) ??
    instrument.description ??
    undefined;
  const targetKey = [...instrumentStateTargets(description).keys()];
  const inspectorKey = JSON.stringify([
    instrumentId,
    retainedSession?.session_id ?? null,
    targetKey,
  ]);
  return (
    <InstrumentInspectorContent
      key={inspectorKey}
      {...props}
      currentMeasurement={currentMeasurement}
      setCurrentMeasurement={setCurrentMeasurement}
    />
  );
}

function InstrumentInspectorContent({
  instrument,
  setup,
  displayLabel,
  hideHeading = false,
  registeredDeviceId,
  configuration,
  releaseUnconfirmed = false,
  session,
  recoverySession,
  sessionError,
  sessionErrorBlocksClose = true,
  connectPending,
  closePending,
  onConnect,
  onClose,
  onRecoveryFinished,
  onRestoreSession,
  restorePending = false,
  onSessionLost,
  onDisconnectOwner,
  onCloseProblem,
  onExitCancelled,
  exitRequest = 0,
  currentMeasurement,
  setCurrentMeasurement,
}: InstrumentInspectorProps & {
  currentMeasurement?: CurrentMeasurementResult;
  setCurrentMeasurement: React.Dispatch<React.SetStateAction<CurrentMeasurementResult | undefined>>;
}) {
  const queryClient = useQueryClient();
  const instrumentId = instrument.instrument_id;
  const connected = session?.instrument_ids.includes(instrumentId) ?? false;
  const retainedSession = session ?? recoverySession;
  const finishRecovery = useInstrumentFinishRecovery(retainedSession?.session_id);
  const finishFixed = !!finishRecovery.state && finishRecovery.state.phase !== "rejected";
  const recovery = useQuery({
    queryKey: ["instrument-session-recovery", recoverySession?.session_id],
    queryFn: () => getInstrumentSessionRecovery(recoverySession!.session_id),
    enabled: !!recoverySession && !session,
    refetchInterval: 3000,
  });
  const recoverySessionClosed =
    !recovery.isFetching &&
    !recovery.error &&
    recovery.data?.state === "closed" &&
    !recovery.data.runtime_retained &&
    !recovery.data.active_operation_id &&
    !recovery.data.active_operation_kind;
  const description =
    retainedSession?.descriptions.find((candidate) => candidate.instrument_id === instrumentId) ??
    instrument.description ??
    undefined;
  const sharedObservation = useQuery({
    queryKey: ["instrument-observation", setup, instrumentId],
    queryFn: () => readSharedInstrumentObservation(setup!, instrumentId),
    enabled: !!setup && !connected,
    refetchInterval: 3000,
  });
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
      : connected
        ? synchronizedState
        : sharedObservation.data;
  const [drafts, setDrafts] = useState<Record<string, DraftValue>>({});
  const [applyResult, setApplyResult] = useState<string>();
  const [configuredDefaultsResult, setConfiguredDefaultsResult] =
    useState<InstrumentConfiguredDefaultsApplyReceipt>();
  const [collectResults, setCollectResults] = useState<Record<string, InstrumentCollectReceipt>>(
    {},
  );
  const [invokeResults, setInvokeResults] = useState<Record<string, InstrumentInvokeReceipt>>({});
  const [outputTouched, setOutputTouched] = useState(false);
  const [showFinishChoice, setShowFinishChoice] = useState(false);
  const [isolationReviewed, setIsolationReviewed] = useState(false);
  const [dismissedExitRequest, setDismissedExitRequest] = useState(exitRequest);
  const finishChoiceOpen =
    showFinishChoice || (!!retainedSession && exitRequest !== dismissedExitRequest);
  const dismissFinishChoice = () => {
    setShowFinishChoice(false);
    setDismissedExitRequest(exitRequest);
  };
  useEffect(() => {
    const cancel = () => {
      finishRecovery.cancelWaiting();
      if (finishChoiceOpen) setShowFinishChoice(true);
      setDismissedExitRequest(exitRequest);
    };
    window.addEventListener("scopecat:device-close-cancelled", cancel);
    return () => window.removeEventListener("scopecat:device-close-cancelled", cancel);
  }, [exitRequest, finishChoiceOpen, finishRecovery]);
  const [recentMeasurements, setRecentMeasurements] = useState<CurrentMeasurementResult[]>([]);
  const markPreviousResult = (reason: string) => {
    setCurrentMeasurement((current) =>
      current ? { ...current, relationshipChanged: reason } : current,
    );
    setRecentMeasurements((current) =>
      current.map((item) => ({ ...item, relationshipChanged: reason })),
    );
  };
  const [pendingMeasurements, setPendingMeasurements] = useState<PendingMeasurement[]>([]);
  const collectionSelections = useRef<Record<string, MeasurementSave>>({});
  const pendingRecovery = usePendingMeasurements(instrumentId, (recovered) => {
    const local = collectionSelections.current[recovered.selection.command_id];
    const result = {
      ...recovered,
      selection: local && sameMeasurement(local, recovered.selection) ? local : recovered.selection,
    };
    setRecentMeasurements((items) => [...items, result]);
    setPendingMeasurements((items) => [
      ...items,
      {
        selection: result.selection,
        acquisition: result.target.acquisition,
        receipt_validation: result.receipt_validation,
        validation_reason: result.validation_reason,
        validation_code: result.validation_code,
      },
    ]);
    setCurrentMeasurement((current) => current ?? result);
  });
  const rememberLocalResult = (id: string, original?: MeasurementSave) => {
    if (original) {
      pendingRecovery.remember(original);
      return;
    }
    const selection =
      pendingMeasurements.find((item) => item.selection.command_id === id)?.selection ??
      recentMeasurements.find((item) => item.selection.command_id === id)?.selection;
    if (selection) pendingRecovery.remember(selection);
  };
  const [seriesId] = useState(() => createInstrumentCommandId("series"));
  const removeLocalMeasurement = (id: string, original?: MeasurementSave) => {
    rememberLocalResult(id, original);
    const matches = (selection: MeasurementSave) =>
      original ? sameMeasurement(selection, original) : selection.command_id === id;
    setPendingMeasurements((items) => items.filter((item) => !matches(item.selection)));
    setRecentMeasurements((items) => items.filter((item) => !matches(item.selection)));
    setCurrentMeasurement((item) => (item && matches(item.selection) ? undefined : item));
  };
  const markMeasurementSaved = (
    commandId: string,
    recordId?: string,
    original?: MeasurementSave,
  ) => {
    rememberLocalResult(commandId, original);
    const matches = (selection: MeasurementSave) =>
      original ? sameMeasurement(selection, original) : selection.command_id === commandId;
    setPendingMeasurements((items) => items.filter((item) => !matches(item.selection)));
    setCurrentMeasurement((item) =>
      item && matches(item.selection) ? { ...item, savedRecordId: recordId } : item,
    );
    setRecentMeasurements((items) => {
      const updated = items.map((item) =>
        matches(item.selection) ? { ...item, savedRecordId: recordId } : item,
      );
      const saved = updated.filter((item) => item.savedRecordId);
      const keep = new Set(saved.slice(-20).map((item) => measurementKey(item.selection)));
      if (currentMeasurement && matches(currentMeasurement.selection)) {
        const key = measurementKey(currentMeasurement.selection);
        if (!keep.has(key)) {
          keep.delete(keep.values().next().value ?? "");
          keep.add(key);
        }
      }
      return updated.filter(
        (item) => !item.savedRecordId || keep.has(measurementKey(item.selection)),
      );
    });
  };
  const applyCommandIdRef = useRef<string | undefined>(undefined);
  const configuredDefaultsOperationIdRef = useRef<string | undefined>(undefined);
  const collectCommandIdsRef = useRef<Record<string, string>>({});
  const invokeCommandIdsRef = useRef<Record<string, string>>({});

  const hasConfiguredDefaults =
    session?.configured_default_instrument_ids.includes(instrumentId) ?? false;

  const readMutation = useMutation({
    mutationKey: ["device-control", instrumentId],
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
    mutationKey: ["device-control", instrumentId],
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
        setOutputTouched(true);
        collectCommandIdsRef.current = {};
        collectionSelections.current = {};
        invokeCommandIdsRef.current = {};
        setCollectResults({});
        markPreviousResult("此后已执行设置或设备动作");
        setInvokeResults({});
        setDrafts({});
        readMutation.mutate();
      } else if (receipt.status === "unknown") {
        onSessionLost("应用结果未知。后台服务已隔离此会话，需操作者核对。");
      }
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
  });
  const configuredDefaultsMutation = useMutation({
    mutationKey: ["device-control", instrumentId],
    mutationFn: ({ operationId }: { operationId: string }) =>
      applyInstrumentConfiguredDefaults(requireSession(session), instrumentId, operationId),
    retry: retryTransientInstrumentMutation,
    retryDelay: 250,
    onSuccess: (receipt) => {
      configuredDefaultsOperationIdRef.current = undefined;
      setConfiguredDefaultsResult(receipt);
      if (receipt.status === "applied" || receipt.status === "unchanged") {
        if (receipt.status === "applied") setOutputTouched(true);
        applyCommandIdRef.current = undefined;
        collectCommandIdsRef.current = {};
        collectionSelections.current = {};
        invokeCommandIdsRef.current = {};
        setApplyResult(undefined);
        setDrafts({});
        setCollectResults({});
        markPreviousResult(
          receipt.status === "applied"
            ? "此后已执行设置或设备动作"
            : "此后已重新核对默认设置；不证明原曲线生成条件相同",
        );
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
            ? "应用已配置默认值时，后台服务隔离了此仪器。"
            : "应用已配置默认值时，交互会话已结束。",
        );
      }
    },
  });
  const collectMutation = useMutation({
    mutationKey: ["device-control", instrumentId],
    mutationFn: ({
      target,
      commandId,
    }: {
      target: InstrumentAcquisitionTarget;
      commandId: string;
    }) => collectInstrumentAcquisition(requireSession(session), instrumentId, target, commandId),
    retry: retryTransientInstrumentMutation,
    retryDelay: 250,
    onSuccess: (receipt, { target, commandId }) => {
      const selection = collectionSelections.current[commandId];
      if (receipt.status === "collected" && selection) {
        pendingRecovery.remember(selection);
        setOutputTouched(true);
        const originalTarget = receipt.pending
          ? acquisitionTargets(receipt.pending.description).find(
              (item) =>
                item.interfaceId === receipt.pending!.intent.interface_id &&
                item.acquisition.id === receipt.pending!.intent.acquisition_id &&
                JSON.stringify(item.componentPath) ===
                  JSON.stringify(receipt.pending!.intent.component_path ?? []),
            )
          : undefined;
        const result = {
          receipt_validation: receipt.pending?.receipt_validation,
          validation_reason: receipt.pending?.validation_reason,
          validation_code: receipt.pending?.validation_code,
          target: originalTarget ?? target,
          selection,
          receivedAt: receipt.pending?.collected_at ?? new Date().toISOString(),
        };
        setCurrentMeasurement(result);
        setRecentMeasurements((current) =>
          current.some((item) => sameMeasurement(item.selection, selection))
            ? current
            : [...current, result],
        );
        setPendingMeasurements((current) =>
          current.some((item) => sameMeasurement(item.selection, selection))
            ? current
            : [
                ...current,
                {
                  selection,
                  acquisition: result.target.acquisition,
                  receipt_validation: result.receipt_validation,
                  validation_reason: result.validation_reason,
                  validation_code: result.validation_code,
                },
              ],
        );
      }
      if (receipt.status !== "collected")
        markPreviousResult("最近一次采集未成功，当前仍是上次成功结果");
      delete collectionSelections.current[commandId];
      const key = acquisitionKey(target);
      delete collectCommandIdsRef.current[key];
      setCollectResults((current) => ({
        ...current,
        [key]: { status: receipt.status, problems: receipt.problems },
      }));
      if (receipt.status === "unknown") {
        onSessionLost("收集结果未知。后台服务已隔离此会话，需操作者核对。");
      }
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
    onError: () => {
      markPreviousResult("最近一次采集返回未确认，当前仍是上次成功结果");
      void pendingRecovery.recover();
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
  });
  const invokeMutation = useMutation({
    mutationKey: ["device-control", instrumentId],
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
        setOutputTouched(true);
        collectCommandIdsRef.current = {};
        collectionSelections.current = {};
        setCollectResults({});
        markPreviousResult("此后已执行设置或设备动作");
        readMutation.mutate();
      } else if (receipt.status === "unknown") {
        onSessionLost("操作结果未知。后台服务已隔离此会话，需操作者核对。");
      }
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
  });
  const resolveMutation = useMutation({
    mutationKey: ["device-control", instrumentId],
    mutationFn: (originalSessionId?: string) => {
      if (originalSessionId && originalSessionId === recoverySession?.session_id)
        return resolveInstrumentAttention(originalSessionId);
      if (instrument.owner_kind !== "instrument_session" || !instrument.owner_id) {
        throw new Error("此待处理状态不属于交互会话。");
      }
      return resolveInstrumentAttention(instrument.owner_id);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["instruments"] });
      await queryClient.invalidateQueries({ queryKey: ["instrument-session-recovery"] });
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
    collectionSelections.current = {};
    invokeCommandIdsRef.current = {};
    setDrafts({});
    setApplyResult(undefined);
    setConfiguredDefaultsResult(undefined);
    setCollectResults({});
    markPreviousResult("此后已重新读取当前设置，这不是原曲线生成设置的证明");
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
    collectionSelections.current[commandId] ??= {
      session_id: requireSession(session).session_id,
      instrument_id: instrumentId,
      command_id: commandId,
      title: "",
      series_id: seriesId,
      line_id: "",
      line_label: "",
      note: "",
    };
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
  const closeError =
    (sessionErrorBlocksClose ? sessionError : undefined) ??
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
  const recoveryCloseAllowed =
    recoverySessionClosed && !interactionPending && !resolveMutation.isPending;
  const interactionDisabled =
    closePending || interactionPending || releaseUnconfirmed || finishChoiceOpen || finishFixed;
  const configuredDefaultsDisabled =
    interactionDisabled || stagedCount > 0 || resolveMutation.isPending;
  const configuredDefaultsDisabledReason =
    stagedCount > 0
      ? "请先应用或重置暂存属性"
      : configuredDefaultsDisabled
        ? "请等待当前仪器交互完成"
        : undefined;

  const failedReceipt =
    (applyResult !== undefined && applyResult !== "applied") ||
    configuredDefaultsResult?.status === "rejected" ||
    Object.values(collectResults).some((receipt) => receipt.status !== "collected") ||
    Object.values(invokeResults).some((receipt) => receipt.status !== "invoked");
  useEffect(() => {
    onCloseProblem?.(
      pendingRecovery.busy ||
        !!pendingRecovery.error ||
        !!closeError ||
        failedReceipt ||
        pendingMeasurements.length > 0 ||
        outputTouched ||
        stagedCount > 0 ||
        finishFixed,
    );
  }, [
    pendingRecovery.busy,
    pendingRecovery.error,
    closeError,
    failedReceipt,
    onCloseProblem,
    pendingMeasurements.length,
    outputTouched,
    stagedCount,
    finishFixed,
  ]);

  return (
    <section
      className={classes(
        "relative grid min-w-0 content-start gap-3.5 p-5 max-[680px]:px-[13px] max-[680px]:py-4",
        !hideHeading && "max-[880px]:rounded-lg max-[880px]:border max-[880px]:border-line",
      )}
      aria-live="polite"
    >
      {!hideHeading && (
        <InstrumentHeading
          instrument={instrument}
          session={session}
          displayLabel={displayLabel}
          releaseUnconfirmed={releaseUnconfirmed}
        />
      )}

      <InstrumentSessionPanel
        instrument={instrument}
        session={session}
        connected={connected}
        releaseUnconfirmed={releaseUnconfirmed}
        connectPending={connectPending}
        connectDisabled={!!recoverySession}
        closePending={closePending}
        interactionPending={interactionPending}
        recoveryBlocked={finishFixed || (!!recoverySession && pendingMeasurements.length > 0)}
        refreshPending={readMutation.isPending}
        stateAvailable={declaredTargets.size > 0}
        configuredDefaultsVisible={hasConfiguredDefaults}
        configuredDefaultsPending={configuredDefaultsMutation.isPending}
        configuredDefaultsDisabled={configuredDefaultsDisabled}
        configuredDefaultsDisabledReason={configuredDefaultsDisabledReason}
        resolvePending={resolveMutation.isPending}
        onConnect={onConnect}
        onClose={() => {
          if (
            finishFixed ||
            pendingMeasurements.length > 0 ||
            outputTouched ||
            stagedCount > 0 ||
            applyMutation.error ||
            configuredDefaultsMutation.error ||
            collectMutation.error ||
            invokeMutation.error ||
            failedReceipt
          )
            setShowFinishChoice(true);
          else onClose();
        }}
        onDisconnectOwner={onDisconnectOwner}
        onRefresh={refreshState}
        onApplyConfiguredDefaults={applyConfiguredDefaults}
        onResolve={() => resolveMutation.mutate()}
      />

      {!connected && (
        <p className="text-xs text-text-dim">
          只读观察：仅显示后台已有观测，不获取控制权或查询硬件。各值附来源和观察时间，可能已过期；当前物理状态未确认。
          {sharedObservation.error
            ? " 读取后台观测失败，状态未知。"
            : !sharedObservation.data?.observations?.length
              ? " 暂无可用观测，状态未知。"
              : ""}
        </p>
      )}
      {interactionError && (
        <div className={inlineError} role="alert">
          <AlertTriangle size={15} />
          {interactionError}
        </div>
      )}
      {finishFixed && !finishChoiceOpen && (
        <section className="grid gap-2 rounded border border-line p-3" aria-label="收尾结果待核查">
          <p className="text-xs">
            原收尾选择已提交，常规设备操作暂不可用。取消关闭等待不会取消动作；请核查原请求结果。
          </p>
          <button
            type="button"
            className={secondaryButton}
            onClick={() => setShowFinishChoice(true)}
          >
            核查原收尾结果
          </button>
        </section>
      )}

      {recoverySession && !session && (
        <section className="grid gap-2 rounded border border-line p-3" aria-label="恢复原会话待办">
          <h3 className="text-sm font-semibold">原会话已中断，结果与待办仍保留</h3>
          <p className="text-xs">
            近期结果、{pendingMeasurements.length} 条未保存结果和 {stagedCount}{" "}
            项未发送设置仍属于原会话。
            只要后台服务仍在，原结果仍可保存或明确放弃，不会重新采集；后台重启后不保证恢复。已保存记录可按原身份核查。
            当前设备输出与未确认操作不会因会话中断而变成已停止或已取消。
          </p>
          <button
            type="button"
            className={secondaryButton}
            onClick={() => setShowFinishChoice(true)}
          >
            核查原会话与保存待办
          </button>
          {recovery.isPending && <p role="status">正在只读核查原会话…</p>}
          {recovery.error && (
            <p role="alert">
              无法核实原会话：{recovery.error.message}。未保存结果和未知操作仍保留。
            </p>
          )}
          {recovery.data && (
            <p className="text-xs" role="status">
              {recovery.data.state === "closed"
                ? "后台已确认原会话结束。"
                : recovery.data.state === "active"
                  ? "原会话仍在后台活动。"
                  : "原会话仍需处理隔离，请先保存结果，再按现有入口核对设备。"}
              {recovery.data.has_unresolved_operations
                ? " 仍有未决动作，不能把关闭窗口视为动作已取消。"
                : " 后台未登记未决动作；这不证明物理输出已停止。"}
              {!recovery.data.runtime_retained &&
                " 原控制运行状态已释放；这不代表成功采集的待保存结果已被放弃。"}
            </p>
          )}
          <button
            type="button"
            className={secondaryButton}
            disabled={recovery.isFetching}
            onClick={() => void recovery.refetch()}
          >
            重新核查原会话
          </button>
          {recovery.data?.state === "active" &&
            !recovery.data.has_unresolved_operations &&
            !finishFixed && (
              <button
                type="button"
                className={secondaryButton}
                disabled={restorePending || recovery.isFetching || !!recovery.error}
                onClick={onRestoreSession}
              >
                恢复原会话控制
              </button>
            )}
          {recovery.data?.state === "attention_required" && (
            <div className="grid gap-2 border-t border-line pt-2">
              <p className="text-xs">
                请先核对设备和未确认操作。处理隔离沿用后台的隔离解除流程，可能清理原会话资源；不会补采或证明输出已停止。
                已成功采集的结果在后台服务运行期间独立保留；处理隔离不会自动保存或放弃它们，之后仍须明确处置。
              </p>
              <label className="text-xs">
                <input
                  type="checkbox"
                  checked={isolationReviewed}
                  disabled={resolveMutation.isPending || finishRecovery.state?.phase === "pending"}
                  onChange={(event) => setIsolationReviewed(event.target.checked)}
                />{" "}
                已核查设备，理解处理隔离不会确认未决动作或自动保存结果
              </label>
              <button
                type="button"
                className={secondaryButton}
                disabled={
                  !isolationReviewed ||
                  resolveMutation.isPending ||
                  finishRecovery.state?.phase === "pending" ||
                  recovery.isFetching ||
                  !!recovery.error
                }
                onClick={() => resolveMutation.mutate(recoverySession.session_id)}
              >
                处理原会话隔离（保留本页结果）
              </button>
            </div>
          )}
        </section>
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
          <strong className="mb-1 block">驱动不可用</strong>
          {instrument.problems.map((problem, index) => (
            <p className="my-0.5" key={`${problem.code}-${index}`}>
              {problem.message}
            </p>
          ))}
        </div>
      )}

      <section className="grid gap-2 text-xs" aria-label="后台待保存结果">
        <button
          type="button"
          className={secondaryButton}
          disabled={pendingRecovery.busy}
          onClick={() => void pendingRecovery.recover()}
        >
          {pendingRecovery.busy ? "正在核查后台待保存结果…" : "重新核查后台待保存结果"}
        </button>
        {pendingRecovery.error && <p role="alert">{pendingRecovery.error}</p>}
        {!retainedSession && pendingMeasurements.length > 0 && (
          <button
            type="button"
            className={secondaryButton}
            onClick={() => setShowFinishChoice(true)}
          >
            处理恢复的待保存结果
          </button>
        )}
      </section>
      {finishChoiceOpen && (
        <InstrumentExitDialog
          sessionId={
            retainedSession?.session_id ?? pendingMeasurements[0]?.selection.session_id ?? ""
          }
          finishRecovery={finishRecovery}
          sessionUnavailable={!session}
          recoveryCloseAllowed={recoveryCloseAllowed}
          recoveryUnresolved={recovery.data?.has_unresolved_operations ?? true}
          closeWaiting={exitRequest !== dismissedExitRequest}
          reviewRequest={exitRequest}
          pendingCheckBlocked={pendingRecovery.busy || !!pendingRecovery.error}
          onRetryPendingCheck={() => void pendingRecovery.recover()}
          onRecoveryFinished={() => {
            setOutputTouched(false);
            dismissFinishChoice();
            onRecoveryFinished?.();
          }}
          pending={pendingMeasurements}
          stagedCount={stagedCount}
          problem={interactionError}
          onSaveAttempt={(attempt) =>
            setPendingMeasurements((items) =>
              items.map((item) =>
                sameMeasurement(item.selection, attempt.selection)
                  ? { ...item, saveAttempted: true }
                  : item,
              ),
            )
          }
          onSaved={markMeasurementSaved}
          onDiscarded={removeLocalMeasurement}
          onForgotten={removeLocalMeasurement}
          onCancel={() => {
            dismissFinishChoice();
            onExitCancelled?.();
          }}
          onFinished={() => {
            setOutputTouched(false);
            dismissFinishChoice();
            if (recoverySession) onRecoveryFinished?.();
            else onClose();
          }}
        />
      )}
      <InstrumentControlLayout
        hasResults={acquisitionTargets(description).length > 0 || !!currentMeasurement}
        pendingCount={pendingMeasurements.length}
        configuration={configuration}
        code={
          <div className="grid gap-3 text-xs">
            <p>查看或复制代码不会连接设备或中断当前手动会话。执行 Python 前请先释放控制。</p>
            <AuthorWorkflowLinks />
            {registeredDeviceId && description && (
              <DevicePythonGuide deviceId={registeredDeviceId} description={description} />
            )}
          </div>
        }
        acquisition={
          <MeasurementAcquisitionActions
            description={description}
            connected={connected}
            disabled={interactionDisabled || pendingMeasurements.length >= 20}
            collectingTarget={
              collectMutation.isPending ? collectMutation.variables?.target : undefined
            }
            collectResults={collectResults}
            pendingCount={pendingMeasurements.length}
            onCollect={collectAcquisition}
          />
        }
        results={
          <MeasurementWorkbench
            layoutManaged
            collapsed={false}
            onCollapsedChange={() => {}}
            description={description}
            connected={connected}
            disabled={interactionDisabled || pendingMeasurements.length >= 20}
            collectingTarget={
              collectMutation.isPending ? collectMutation.variables?.target : undefined
            }
            collectResults={collectResults}
            recent={recentMeasurements}
            current={currentMeasurement}
            staged={stagedCount > 0}
            sessionId={session?.session_id}
            pending={pendingMeasurements}
            saveDisabled={finishChoiceOpen}
            onCollect={collectAcquisition}
            onSelect={setCurrentMeasurement}
            onSaveAttempt={(selection) => {
              setRecentMeasurements((items) =>
                items.map((item) =>
                  sameMeasurement(item.selection, selection) ? { ...item, selection } : item,
                ),
              );
              setCurrentMeasurement((item) =>
                item && sameMeasurement(item.selection, selection) ? { ...item, selection } : item,
              );
              setPendingMeasurements((items) =>
                items.map((item) =>
                  sameMeasurement(item.selection, selection)
                    ? { ...item, selection, saveAttempted: true }
                    : item,
                ),
              );
            }}
            onSaved={markMeasurementSaved}
          />
        }
        controls={
          description ? (
            <div data-testid="instrument-control-panel" className="grid min-w-0 gap-3">
              <div
                className="mt-[3px] flex items-end justify-between max-[460px]:flex-col max-[460px]:items-start"
                data-testid="interface-heading"
              >
                <div>
                  <span className={eyebrow}>设备控制</span>
                  <h3 className="m-0 text-[0.9rem] font-[650]">接口</h3>
                </div>
              </div>

              <details className="text-xs leading-normal text-text-dim">
                <summary>接口说明与 Python</summary>
                <p>
                  Invoke 调用 operation；Collect 调用
                  acquisition，可能发起新采集，也可能读取既有结果。 请以各项契约说明为准；Collect
                  不会自动替你准备 payload 或调用另一个 operation。
                  查看能力或展开帮助不会连接设备或发送设备命令。
                </p>
                <p>
                  {connected
                    ? interactionDisabled
                      ? "当前交互尚未结束，请等待完成后继续。"
                      : "本页已持有设备会话。操作会访问设备；切换到 Python 或实验前请先断开连接。"
                    : instrument.availability === "active"
                      ? "设备正被占用。请等待运行完成清理，或结束已有手动会话后再连接。"
                      : instrument.availability === "quarantined"
                        ? "设备状态不确定。请先核查设备，并通过上方所属会话或运行处理隔离状态。"
                        : instrument.availability === "unavailable"
                          ? "设备不可用。请先处理上方驱动或连接配置问题。"
                          : "本页尚未连接。确认设备与调用前提后，点击“连接” 获取手动会话。"}
                </p>
                <AuthorWorkflowLinks />
              </details>

              {(description.interfaces ?? []).length > 0 ||
              (description.device_schemas ?? []).length > 0 ? (
                <div className="grid min-w-0 items-start gap-2.5">
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
                        hideCollectionPlots
                        hideAcquisitions
                        invokeResults={invokeResults}
                        collectionDisabled={pendingMeasurements.length >= 20}
                        collectingTarget={
                          collectMutation.isPending ? collectMutation.variables?.target : undefined
                        }
                        invokingTarget={
                          invokeMutation.isPending ? invokeMutation.variables?.target : undefined
                        }
                        onStage={(target, draft) => stage(target, draft)}
                        onCollect={(target) => {
                          if (pendingMeasurements.length < 20) collectAcquisition(target);
                        }}
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
                  title="无交互接口"
                  detail="此设备未声明交互能力。请查看驱动文档或检查驱动版本；连接不会补充未声明的 operation。"
                />
              )}

              {connected && stagedCount > 0 && (
                <div className="sticky bottom-2.5 z-[5] grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-2 rounded-md border border-[rgb(128_163_207_/_35%)] bg-[color-mix(in_srgb,var(--color-panel-strong)_94%,transparent)] px-[11px] py-2.5 shadow-[0_10px_30px_rgb(0_0_0_/_28%)] backdrop-blur-[12px] max-[460px]:grid-cols-2">
                  <div className="grid gap-0.5 max-[460px]:col-span-full">
                    <strong className="text-xs">已暂存 {stagedCount} 项属性</strong>
                    <small className="text-xs text-text-dim">
                      数值仅保留在本地，直到一次性应用全部暂存修改。
                    </small>
                  </div>
                  <button
                    type="button"
                    className={secondaryButton}
                    onClick={resetStaged}
                    disabled={applyMutation.isPending}
                  >
                    <RotateCcw size={14} />
                    重置
                  </button>
                  <button
                    type="button"
                    className={primaryButton}
                    onClick={applyStaged}
                    disabled={interactionDisabled || invalidDraft || stagedMembers.length === 0}
                    title={
                      interactionDisabled
                        ? "请等待当前仪器交互完成"
                        : invalidDraft
                          ? "应用前请修正无效的暂存值"
                          : undefined
                    }
                  >
                    {applyMutation.isPending ? (
                      <LoaderCircle className="animate-spin" size={14} />
                    ) : (
                      <Check size={14} />
                    )}
                    应用暂存修改
                  </button>
                </div>
              )}
              {applyResult && (
                <p className={classes(receiptStyle, receiptTone(applyResult))}>
                  应用回执： {instrumentReceiptLabel(applyResult)}
                </p>
              )}
            </div>
          ) : (
            <InspectorEmpty
              icon={<CircleOff />}
              title="设备控制不可用"
              detail="请核对列出的问题，或编辑此仪器的连接配置。"
            />
          )
        }
      />
    </section>
  );
}

export function InstrumentHeading({
  instrument,
  session,
  displayLabel,
  releaseUnconfirmed = false,
  primary = false,
  actions,
}: Pick<
  InstrumentInspectorProps,
  "instrument" | "session" | "displayLabel" | "releaseUnconfirmed"
> & {
  primary?: boolean;
  actions?: React.ReactNode;
}) {
  const description =
    session?.descriptions.find(
      (candidate) => candidate.instrument_id === instrument.instrument_id,
    ) ?? instrument.description;
  const connected = session?.instrument_ids.includes(instrument.instrument_id) ?? false;
  const Heading = primary ? "h1" : "h2";
  return (
    <header
      className="flex min-w-0 flex-wrap items-start justify-between gap-3 border-b border-line pb-3.5"
      aria-live="polite"
    >
      <div className="min-w-0 flex-1 basis-60">
        <div className="flex min-w-0 flex-wrap items-center gap-2.5">
          <Heading className="m-0 min-w-0 text-[1.1rem] font-[650] tracking-[-0.025em] [overflow-wrap:anywhere]">
            {displayLabel ?? description?.label ?? instrument.instrument_id}
          </Heading>
          {releaseUnconfirmed ? (
            <span>释放待确认</span>
          ) : (
            <AvailabilityBadge availability={instrument.availability} connected={connected} />
          )}
        </div>
        {description?.description && (
          <p className="mt-[9px] mb-0 max-w-[760px] text-xs leading-[1.55] text-text-dim [overflow-wrap:anywhere]">
            {description.description}
          </p>
        )}
      </div>
      {actions}
    </header>
  );
}
