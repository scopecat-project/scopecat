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
import {
  DirectMeasurements,
  type PendingMeasurement,
  type MeasurementSave,
} from "./DirectMeasurements";
import { InstrumentFinishChoice } from "./InstrumentFinishChoice";
import { InstrumentSessionPanel } from "./InstrumentSessionPanel";

type DraftValue = InstrumentPropertyDraft;

interface InstrumentInspectorProps {
  instrument: InstrumentView;
  setup?: InstrumentSession["setup"];
  displayLabel?: string;
  registeredDeviceId?: string;
  releaseUnconfirmed?: boolean;
  session?: InstrumentSession;
  sessionError?: string;
  connectPending: boolean;
  closePending: boolean;
  onConnect: () => void;
  onClose: () => void;
  onSessionLost: (message: string) => void;
  onDisconnectOwner: () => void;
  onCloseProblem?: (problem: boolean) => void;
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
  setup,
  displayLabel,
  registeredDeviceId,
  releaseUnconfirmed = false,
  session,
  sessionError,
  connectPending,
  closePending,
  onConnect,
  onClose,
  onSessionLost,
  onDisconnectOwner,
  onCloseProblem,
}: InstrumentInspectorProps) {
  const queryClient = useQueryClient();
  const instrumentId = instrument.instrument_id;
  const connected = session?.instrument_ids.includes(instrumentId) ?? false;
  const description =
    session?.descriptions.find((candidate) => candidate.instrument_id === instrumentId) ??
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
  const [closeUnsavedWarning, setCloseUnsavedWarning] = useState(false);
  const [pendingMeasurements, setPendingMeasurements] = useState<PendingMeasurement[]>([]);
  const [seriesId, setSeriesId] = useState(() => createInstrumentCommandId("series"));
  const [lineId, setLineId] = useState(() => createInstrumentCommandId("line"));
  const [lineLabel, setLineLabel] = useState("");
  const [lineConfirmed, setLineConfirmed] = useState(false);
  const [measurementNote, setMeasurementNote] = useState("");
  const collectionSelections = useRef<Record<string, MeasurementSave>>({});
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
        invokeCommandIdsRef.current = {};
        setCollectResults({});
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
        setOutputTouched(true);
        setPendingMeasurements((current) =>
          current.some((item) => item.selection.command_id === commandId)
            ? current
            : [...current, { selection, acquisition: target.acquisition, receipt }],
        );
      }
      const key = acquisitionKey(target);
      delete collectCommandIdsRef.current[key];
      setCollectResults((current) => ({ ...current, [key]: receipt }));
      if (receipt.status === "unknown") {
        onSessionLost("收集结果未知。后台服务已隔离此会话，需操作者核对。");
      }
      void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
    onError: () => {
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
        setCollectResults({});
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
    mutationFn: () => {
      if (instrument.owner_kind !== "instrument_session" || !instrument.owner_id) {
        throw new Error("此待处理状态不属于交互会话。");
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
    collectionSelections.current[commandId] ??= {
      session_id: requireSession(session).session_id,
      instrument_id: instrumentId,
      command_id: commandId,
      series_id: seriesId,
      line_id: lineLabel ? lineId : "",
      line_label: lineLabel,
      note: measurementNote,
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
  const interactionPending =
    readMutation.isPending ||
    applyMutation.isPending ||
    configuredDefaultsMutation.isPending ||
    collectMutation.isPending ||
    invokeMutation.isPending;
  const interactionDisabled =
    closePending || interactionPending || releaseUnconfirmed || showFinishChoice;
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
      !!interactionError || failedReceipt || pendingMeasurements.length > 0 || outputTouched,
    );
  }, [interactionError, failedReceipt, onCloseProblem, pendingMeasurements.length, outputTouched]);

  return (
    <section
      className="relative grid min-h-[640px] min-w-0 content-start gap-3.5 p-5 max-[880px]:min-h-[560px] max-[880px]:rounded-lg max-[880px]:border max-[880px]:border-line max-[680px]:px-[13px] max-[680px]:py-4"
      aria-live="polite"
    >
      <header className="flex items-start justify-between gap-[18px] border-b border-line pb-3.5 max-[460px]:grid">
        <div>
          <div className="flex items-center gap-2.5">
            <h2 className="m-0 text-[1.1rem] font-[650] tracking-[-0.025em]">
              {displayLabel ?? description?.label ?? instrumentId}
            </h2>
            {releaseUnconfirmed ? (
              <span>释放待确认</span>
            ) : (
              <AvailabilityBadge availability={instrument.availability} connected={connected} />
            )}
          </div>
          {description?.description && (
            <p className="mt-[9px] mb-0 max-w-[760px] text-xs leading-[1.55] text-text-dim">
              {description.description}
            </p>
          )}
        </div>
      </header>

      <InstrumentSessionPanel
        instrument={instrument}
        session={session}
        connected={connected}
        releaseUnconfirmed={releaseUnconfirmed}
        connectPending={connectPending}
        closePending={closePending}
        interactionPending={interactionPending}
        refreshPending={readMutation.isPending}
        stateAvailable={declaredTargets.size > 0}
        configuredDefaultsVisible={hasConfiguredDefaults}
        configuredDefaultsPending={configuredDefaultsMutation.isPending}
        configuredDefaultsDisabled={configuredDefaultsDisabled}
        configuredDefaultsDisabledReason={configuredDefaultsDisabledReason}
        resolvePending={resolveMutation.isPending}
        onConnect={onConnect}
        onClose={() => {
          if (pendingMeasurements.length > 0) setCloseUnsavedWarning(true);
          else if (outputTouched) setShowFinishChoice(true);
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

      {closeUnsavedWarning && pendingMeasurements.length > 0 && (
        <div role="alert" className={inlineError}>
          未保存结果在会话结束后将无法保存。请先保存，或明确放弃这些未保存结果。
          <button
            type="button"
            className={secondaryButton}
            onClick={() => {
              setPendingMeasurements([]);
              setCloseUnsavedWarning(false);
              if (outputTouched) setShowFinishChoice(true);
              else onClose();
            }}
          >
            放弃未保存结果并结束控制
          </button>
        </div>
      )}
      {showFinishChoice && session && (
        <InstrumentFinishChoice
          sessionId={session.session_id}
          onCancel={() => setShowFinishChoice(false)}
          onFinished={() => {
            setOutputTouched(false);
            setShowFinishChoice(false);
            onClose();
          }}
        />
      )}
      <DirectMeasurements
        instrumentId={instrumentId}
        onResume={(selection) => {
          setSeriesId(selection.series_id ?? createInstrumentCommandId("series"));
          setLineId(selection.line_id ?? createInstrumentCommandId("line"));
          setLineLabel(selection.line_label ?? "");
          setLineConfirmed(false);
          setMeasurementNote("");
        }}
        pending={pendingMeasurements}
        onSaved={(commandId) =>
          setPendingMeasurements((current) =>
            current.filter((item) => item.selection.command_id !== commandId),
          )
        }
      />
      {connected && (
        <section className="grid gap-2 border border-line p-3 text-xs">
          <h3>本次测量对象（可留空）</h3>
          <label>
            线路名称{" "}
            <input
              aria-label="线路名称"
              value={lineLabel}
              onChange={(event) => {
                setLineLabel(event.target.value);
                setLineConfirmed(false);
              }}
            />
          </label>
          <label>
            备注 / 异常标记{" "}
            <input
              aria-label="测量备注"
              value={measurementNote}
              onChange={(event) => setMeasurementNote(event.target.value)}
            />
          </label>
          {lineLabel && (
            <label>
              <input
                type="checkbox"
                checked={lineConfirmed}
                onChange={(event) => setLineConfirmed(event.target.checked)}
              />
              已人工确认当前接线为 {lineLabel}
            </label>
          )}
          <button
            type="button"
            className={secondaryButton}
            onClick={() => {
              setLineId(createInstrumentCommandId("line"));
              setLineLabel("");
              setLineConfirmed(false);
              setMeasurementNote("");
            }}
          >
            换到新线路
          </button>
          <p>
            仪器设置保持不变；同一线路再次采集会追加新尝试。改名不会修改已采集结果，换线请用“换到新线路”。
          </p>
          {pendingMeasurements.length >= 20 && (
            <p role="status">请先保存待保存结果，再继续采集。</p>
          )}
        </section>
      )}

      {description ? (
        <>
          <div
            className="mt-[3px] flex items-end justify-between max-[460px]:flex-col max-[460px]:items-start"
            data-testid="interface-heading"
          >
            <div>
              <span className={eyebrow}>设备控制</span>
              <h3 className="m-0 text-[0.9rem] font-[650]">接口</h3>
            </div>
          </div>

          <div className="text-xs leading-normal text-text-dim">
            <p>
              Invoke 调用 operation；Collect 调用 acquisition，可能发起新采集，也可能读取既有结果。
              请以各项契约说明为准；Collect 不会自动替你准备 payload 或调用另一个 operation。
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
            {registeredDeviceId && (
              <DevicePythonGuide deviceId={registeredDeviceId} description={description} />
            )}
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
                    collectionDisabled={
                      (!!lineLabel && !lineConfirmed) || pendingMeasurements.length >= 20
                    }
                    collectingTarget={
                      collectMutation.isPending ? collectMutation.variables?.target : undefined
                    }
                    invokingTarget={
                      invokeMutation.isPending ? invokeMutation.variables?.target : undefined
                    }
                    onStage={(target, draft) => stage(target, draft)}
                    onCollect={(target) => {
                      if ((!lineLabel || lineConfirmed) && pendingMeasurements.length < 20)
                        collectAcquisition(target);
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
        </>
      ) : (
        <InspectorEmpty
          icon={<CircleOff />}
          title="设备控制不可用"
          detail="请核对列出的问题，或编辑此仪器的连接配置。"
        />
      )}
    </section>
  );
}
