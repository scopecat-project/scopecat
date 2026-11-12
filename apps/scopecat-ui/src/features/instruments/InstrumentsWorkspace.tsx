import { useDesktopAvailable } from "../application/DesktopSession";
import { registerDeviceClose } from "./device-close";
import { OperatorSettings, readOperatorSettings } from "../application/OperatorSettings";
import * as Controls from "../../ui/Controls";
import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { InstrumentSession } from "../../api-contract";
import { ApiError } from "../../api-client";
import { errorMessage } from "../../lib/presentation";
import { connectionOptionFields } from "./connection-fields";
import { InstrumentConfigDialog } from "./InstrumentConfigDialog";
import { InstrumentHeading, InstrumentInspector } from "./InstrumentInspector";
import { DeviceTable } from "./DeviceTable";
import { LocalDriverSourcesPanel } from "./LocalDriverSourcesPanel";
import {
  getDevices,
  prepareDeviceAccess,
  testDeviceConnection,
  retireDevice,
  releaseDeviceConnection,
  type DeviceView,
} from "./device-api";
import {
  abortInstrumentSession,
  closeInstrumentSession,
  connectionSummary,
  createInstrumentCommandId,
  getDriverCatalog,
  getInstruments,
  openInstrumentSession,
  renewInstrumentSession,
  retryTransientInstrumentMutation,
} from "./instrument-api";

type OpenAttempt = {
  kind: "open";
  key: string;
  id: string;
  setup: InstrumentSession["setup"];
  operation: string;
  actor?: string;
  submission: "not_submitted" | "unknown" | "rejected";
  hadUnknownOutcome?: boolean;
};

class MissingConnectionActor extends Error {
  constructor() {
    super(
      "当前连接需要操作署名。请在下方保存默认操作者，再点击连接；署名不是登录账号，不要求填写实名。",
    );
  }
}

function isOpenValidationRejection(error: unknown) {
  return (
    error instanceof ApiError &&
    error.status === 422 &&
    Array.isArray(error.detail) &&
    error.detail.length > 0 &&
    error.detail.every(
      (item: unknown) =>
        !!item && typeof item === "object" && "loc" in item && Array.isArray(item.loc),
    )
  );
}

function rejectedOpenAttempt(variables: unknown) {
  return (
    !!variables &&
    typeof variables === "object" &&
    "kind" in variables &&
    variables.kind === "open" &&
    "submission" in variables &&
    variables.submission !== "unknown"
  );
}

type ConfigTarget = { kind: "add" } | { kind: "edit"; deviceId: string };
export function InstrumentsWorkspace({
  daemonUnavailable,
  controlDeviceId,
}: {
  daemonUnavailable: boolean;
  controlDeviceId?: string;
}) {
  const desktop = useDesktopAvailable();
  const manageOnly = desktop && !controlDeviceId;
  const [closingWindow, setClosingWindow] = useState(false);
  const [windowError, setWindowError] = useState<string>();
  const [releaseUnconfirmed, setReleaseUnconfirmed] = useState(false);
  const [releasePending, setReleasePending] = useState(false);
  const releaseInFlight = useRef<Promise<void> | undefined>(undefined);
  const closeProblem = useRef(false);
  const closeDecision = useRef<((status: "saved" | "blocked" | "cancelled") => void) | undefined>(
    undefined,
  );
  const [exitRequest, setExitRequest] = useState(0);
  const noteCloseProblem = useCallback((problem: boolean) => {
    closeProblem.current = problem;
  }, []);
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | undefined>(controlDeviceId);
  const [session, setSession] = useState<InstrumentSession>();
  const sessionRef = useRef<InstrumentSession | undefined>(undefined);
  const [recoverySession, setRecoverySession] = useState<InstrumentSession>();
  const recoverySessionRef = useRef<InstrumentSession | undefined>(undefined);
  const [sessionError, setSessionError] = useState<string>();
  const [sessionErrorBlocksClose, setSessionErrorBlocksClose] = useState(false);
  const [showOperatorSettings, setShowOperatorSettings] = useState(false);
  const [savedRevision, setSavedRevision] = useState<string>();
  const [configTarget, setConfigTarget] = useState<ConfigTarget>();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [lifecycle, setLifecycle] = useState("available");
  const [tabs, setTabs] = useState<Record<string, string>>(() => {
    try {
      const stored: unknown = JSON.parse(sessionStorage.getItem("device-workbench-tabs") ?? "{}");
      if (!stored || typeof stored !== "object" || Array.isArray(stored)) return {};
      return Object.fromEntries(
        Object.entries(stored).filter(
          ([, value]) =>
            value === "operations" || value === "connection" || value === "diagnostics",
        ),
      );
    } catch {
      return {};
    }
  });
  const rememberTab = (deviceId: string, value: string) => {
    setTabs((current) => {
      const next = { ...current, [deviceId]: value };
      try {
        sessionStorage.setItem("device-workbench-tabs", JSON.stringify(next));
      } catch {
        /* The workbench remains usable when storage is unavailable. */
      }
      return next;
    });
  };
  const chooseTab = (value: string) => {
    if (selectedId) rememberTab(selectedId, value);
  };
  const openAttempt = useRef<OpenAttempt | undefined>(undefined);
  const devicesQuery = useQuery({
    queryKey: ["devices"],
    queryFn: ({ signal }) => getDevices(signal),
    enabled: !daemonUnavailable,
    refetchInterval: 3_000,
  });
  const devices = devicesQuery.data?.items ?? [];
  const visibleDevices = devices.filter(
    (item) =>
      (lifecycle === "all" || item.device.state === lifecycle) &&
      (filter === "all" || item.availability === filter) &&
      `${item.device.label} ${connectionSummary(item.revision.content.connection)}`
        .toLocaleLowerCase()
        .includes(search.toLocaleLowerCase()),
  );
  const firstDeviceId = visibleDevices[0]?.device.id;
  const selectedDevice = devices.find((item) => item.device.id === selectedId);
  const tab = controlDeviceId
    ? "operations"
    : selectedId
      ? (manageOnly || selectedDevice?.device.state === "retired") &&
        (!tabs[selectedId] || tabs[selectedId] === "operations")
        ? "connection"
        : (tabs[selectedId] ?? "operations")
      : "operations";
  const accessQuery = useQuery({
    queryKey: ["device-access", selectedId, selectedDevice?.device.head.revision_id],
    queryFn: () => {
      if (!selectedId) throw new Error("请选择设备。");
      return prepareDeviceAccess(selectedId);
    },
    enabled: !daemonUnavailable && selectedDevice?.device.state === "available",
  });
  const revision = accessQuery.data;
  const instrumentsQuery = useQuery({
    queryKey: ["instruments", revision?.id, revision?.content_hash],
    queryFn: ({ signal }) => {
      if (!revision) throw new Error("请先准备设备连接。");
      return getInstruments(
        { revision_id: revision.id, content_hash: revision.content_hash },
        signal,
      );
    },
    enabled: !daemonUnavailable && !!revision && selectedDevice?.device.state === "available",
    refetchInterval: 3_000,
  });
  const selected = instrumentsQuery.data?.items.find((item) => item.instrument_id === selectedId);
  const catalog = useQuery({
    queryKey: ["instrument-drivers"],
    queryFn: ({ signal }) => getDriverCatalog(signal),
    enabled: !daemonUnavailable,
  });
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["devices"] });
    void queryClient.invalidateQueries({ queryKey: ["instruments"] });
    void queryClient.invalidateQueries({ queryKey: ["instrument-drivers"] });
  };
  const loseSession = (message: string) => {
    window.dispatchEvent(new Event("scopecat:device-close-cancelled"));
    closeDecision.current?.("blocked");
    recoverySessionRef.current = sessionRef.current ?? recoverySessionRef.current;
    setRecoverySession(recoverySessionRef.current);
    sessionRef.current = undefined;
    setSession(undefined);
    closeProblem.current = true;
    setSessionError(message);
    setSessionErrorBlocksClose(true);
    setClosingWindow(false);
    refresh();
  };
  const connect = useMutation({
    mutationKey: ["device-control", selectedId],
    mutationFn: async (command: OpenAttempt) => {
      command.actor ??= (await readOperatorSettings()).operator;
      if (!command.actor.trim()) throw new MissingConnectionActor();
      command.submission = "unknown";
      try {
        return await openInstrumentSession(
          command.id,
          command.actor,
          command.setup,
          command.operation,
        );
      } catch (error) {
        // A later automatic retry cannot make an earlier unacknowledged submission safe to replace.
        if (!isOpenValidationRejection(error)) command.hadUnknownOutcome = true;
        throw error;
      }
    },
    retry: retryTransientInstrumentMutation,
    retryDelay: 250,
    onSuccess: (opened) => {
      sessionRef.current = opened;
      setSession(opened);
      setSessionError(undefined);
      setSessionErrorBlocksClose(false);
      setShowOperatorSettings(false);
      setWindowError(undefined);
      setReleaseUnconfirmed(false);
      closeProblem.current = false;
      openAttempt.current = undefined;
      refresh();
    },
    onError: (error, command) => {
      const rejected =
        command.submission === "not_submitted" ||
        (!command.hadUnknownOutcome && isOpenValidationRejection(error));
      if (rejected) {
        command.submission = "rejected";
        if (openAttempt.current === command) openAttempt.current = undefined;
      }
      if (!rejected) command.hadUnknownOutcome = true;
      setSessionErrorBlocksClose(!rejected);
      setShowOperatorSettings(error instanceof MissingConnectionActor);
      setSessionError(
        rejected && isOpenValidationRejection(error)
          ? "连接请求未被接纳，尚未取得控制权。请检查操作署名和连接设置后重试。"
          : errorMessage(error),
      );
    },
  });
  const close = useMutation({
    mutationKey: ["device-control", selectedId],
    mutationFn: ({ id, abort }: { id: string; abort: boolean; selectAfter?: string }) =>
      abort ? abortInstrumentSession(id) : closeInstrumentSession(id),
    retry: retryTransientInstrumentMutation,
    retryDelay: 250,
    onSuccess: (_, { id, selectAfter }) => {
      if (sessionRef.current?.session_id === id) {
        sessionRef.current = undefined;
        setSession(undefined);
      }
      if (recoverySessionRef.current?.session_id === id) {
        recoverySessionRef.current = undefined;
        setRecoverySession(undefined);
      }
      setSessionError(undefined);
      setSessionErrorBlocksClose(false);
      setWindowError(undefined);
      setReleaseUnconfirmed(false);
      closeDecision.current?.("saved");
      if (selectAfter) selectDevice(selectAfter);
      refresh();
    },
    onError: (error) => {
      setSessionError(errorMessage(error));
      setSessionErrorBlocksClose(true);
      closeDecision.current?.("blocked");
    },
  });
  const restore = useMutation({
    mutationKey: ["device-control", selectedId],
    retry: false,
    mutationFn: async () => {
      const previous = recoverySessionRef.current;
      if (!previous) throw new Error("没有可恢复的原会话。");
      const renewed = await renewInstrumentSession(previous.session_id);
      return { ...previous, ...renewed };
    },
    onSuccess: (restored) => {
      if (recoverySessionRef.current?.session_id !== restored.session_id) return;
      sessionRef.current = restored;
      setSession(restored);
      recoverySessionRef.current = undefined;
      setRecoverySession(undefined);
      setSessionError(undefined);
      setSessionErrorBlocksClose(false);
      refresh();
    },
    onError: (error) => setSessionError(`恢复原会话未确认：${errorMessage(error)}`),
  });
  const probe = useMutation({
    mutationFn: async (device: DeviceView) => {
      const actor = (await readOperatorSettings()).operator;
      return testDeviceConnection(device, createInstrumentCommandId("test"), actor);
    },
    onSettled: refresh,
  });
  const probeError =
    probe.variables?.device.head.revision_id === selectedDevice?.device.head.revision_id
      ? probe.error
      : null;
  const releaseConnection = useMutation({
    retry: false,
    mutationFn: () => {
      if (!selectedId || !revision) throw new Error("请选择已配置设备。");
      return releaseDeviceConnection(selectedId, {
        revision_id: revision.id,
        content_hash: revision.content_hash,
      });
    },
    onSettled: refresh,
  });
  const retire = useMutation({
    mutationFn: () => {
      if (!selectedDevice) throw new Error("请选择设备。");
      return retireDevice(selectedDevice);
    },
    onSuccess: (retired) => {
      queryClient.setQueryData<Awaited<ReturnType<typeof getDevices>>>(["devices"], (current) =>
        current
          ? {
              ...current,
              items: current.items.map((item) =>
                item.device.id === retired.id ? { ...item, device: retired } : item,
              ),
            }
          : current,
      );
      setSelectedId(undefined);
      refresh();
    },
  });
  useEffect(() => {
    if (!manageOnly && !selectedId && firstDeviceId) setSelectedId(firstDeviceId);
  }, [manageOnly, selectedId, firstDeviceId]);
  useEffect(() => {
    const release = () => {
      const current = sessionRef.current;
      if (
        !current ||
        releaseInFlight.current ||
        queryClient.isMutating({ mutationKey: ["device-control"] })
      )
        return;
      sessionRef.current = undefined;
      void closeInstrumentSession(current.session_id, true).catch(() => {});
    };
    // Browser unload cannot confirm a release. Cancellation must not end control;
    // abnormal disappearance is covered by the server lease, not an implied stop.
    return release;
  }, [queryClient]);
  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    let timeout: number | undefined;
    const schedule = (
      lease: Pick<InstrumentSession, "session_id" | "renewed_at" | "expires_at">,
    ) => {
      const renewed = Date.parse(lease.renewed_at);
      timeout = window.setTimeout(
        () => {
          void renewInstrumentSession(lease.session_id).then(
            (next) => {
              if (cancelled || sessionRef.current?.session_id !== next.session_id) return;
              const current = { ...sessionRef.current, ...next };
              sessionRef.current = current;
              setSession(current);
              schedule(next);
            },
            () => {
              if (!cancelled && sessionRef.current?.session_id === lease.session_id)
                loseSession("设备会话已中断，其 lease 将释放占用。重新连接前请检查设备。");
            },
          );
        },
        Math.max(0, renewed + (Date.parse(lease.expires_at) - renewed) / 3 - Date.now()),
      );
    };
    schedule(session);
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
    // Each renewal schedules itself from the authoritative receipt.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.session_id]);
  useEffect(() => {
    if (!session || !selected) return;
    if (
      selected.availability === "quarantined" ||
      (selected.availability === "active" && selected.owner_id !== session.session_id)
    )
      loseSession("设备占用方已改变。重新连接前请检查当前占用方或处理报告的问题。");
    // Reconcile only after a fresh server response.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrumentsQuery.dataUpdatedAt, session?.session_id]);
  useEffect(() => {
    if (!controlDeviceId) return;
    return registerDeviceClose(async (signal, choosing) => {
      setClosingWindow(true);
      setWindowError(undefined);
      const thaw = () => {
        window.dispatchEvent(new Event("scopecat:device-close-cancelled"));
        setClosingWindow(false);
      };
      signal.addEventListener("abort", thaw, { once: true });
      const pending = queryClient
        .getMutationCache()
        .findAll({ mutationKey: ["device-control", controlDeviceId], status: "pending" });
      await new Promise<void>((resolve) => {
        const check = () => {
          if (
            signal.aborted ||
            queryClient.isMutating({ mutationKey: ["device-control", controlDeviceId] }) === 0
          ) {
            unsubscribe();
            signal.removeEventListener("abort", check);
            resolve();
          }
        };
        const unsubscribe = queryClient.getMutationCache().subscribe(check);
        signal.addEventListener("abort", check, { once: true });
        check();
      });
      if (signal.aborted) return "unknown";
      if (
        closeProblem.current ||
        pending.some((mutation) => {
          const data: unknown = mutation.state.data;
          return (
            (mutation.state.status === "error" && !rejectedOpenAttempt(mutation.state.variables)) ||
            (data !== null &&
              typeof data === "object" &&
              "status" in data &&
              ["unknown", "not_applied", "not_collected", "not_invoked", "rejected"].includes(
                String(data.status),
              ))
          );
        })
      ) {
        const reviewedSession = sessionRef.current ?? recoverySessionRef.current;
        const reviewingLiveSession = !!sessionRef.current;
        if (!reviewedSession) {
          setClosingWindow(false);
          setWindowError(
            "连接结果尚未确认。请用原连接请求重试核查后再关闭；不会改写原署名或重新生成请求。",
          );
          return "blocked";
        }
        await choosing();
        if (signal.aborted) return "unknown";
        if (
          (reviewingLiveSession ? sessionRef.current : recoverySessionRef.current)?.session_id !==
          reviewedSession.session_id
        )
          return "blocked";
        setClosingWindow(false);
        // Keep this native close intent alive while the existing review surface
        // handles saving and the user's explicit output disposition.
        const finished = await new Promise<"saved" | "blocked" | "cancelled">((resolve) => {
          const settle = (value: "saved" | "blocked" | "cancelled") => {
            signal.removeEventListener("abort", cancel);
            closeDecision.current = undefined;
            resolve(value);
          };
          const cancel = () => settle("blocked");
          closeDecision.current = settle;
          signal.addEventListener("abort", cancel, { once: true });
          setExitRequest((current) => current + 1);
        });
        return signal.aborted ? "blocked" : finished;
      }
      const current = sessionRef.current;
      if (current || releaseInFlight.current) {
        try {
          // Host timeout cancels the close attempt, not this HTTP operation.
          // Every later preflight waits on the same release until it settles.
          if (!releaseInFlight.current && current) {
            setReleasePending(true);
            setReleaseUnconfirmed(true);
            releaseInFlight.current = closeInstrumentSession(current.session_id)
              .then(() => {
                if (sessionRef.current?.session_id === current.session_id) {
                  sessionRef.current = undefined;
                  setSession(undefined);
                }
                setReleaseUnconfirmed(false);
                setSessionError(undefined);
                closeProblem.current = false;
                refresh();
              })
              .finally(() => {
                releaseInFlight.current = undefined;
                setReleasePending(false);
              });
          }
          await releaseInFlight.current;
        } catch (error) {
          setReleaseUnconfirmed(true);
          setWindowError(`释放控制未确认：${errorMessage(error)}。请保留窗口并重试释放控制。`);
          return "unknown";
        }
      }
      return signal.aborted ? "unknown" : "saved";
    });
    // The current session is read from its authoritative ref after pending operations finish.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [controlDeviceId, queryClient]);
  useEffect(() => {
    if (!controlDeviceId) return;
    const protect = (event: BeforeUnloadEvent) => {
      if (
        sessionRef.current ||
        recoverySessionRef.current ||
        closeProblem.current ||
        queryClient.isMutating({ mutationKey: ["device-control", controlDeviceId] })
      ) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [controlDeviceId, queryClient]);
  const deviceLabel = selectedDevice?.device.label;
  useEffect(() => {
    if (controlDeviceId && deviceLabel && desktop)
      void window.pywebview?.api.set_window_title(`${deviceLabel} · 设备控制`).catch(() => {});
  }, [controlDeviceId, deviceLabel, desktop]);
  const connectCurrent = () => {
    if (releaseInFlight.current) return;
    if (recoverySessionRef.current) {
      setExitRequest((current) => current + 1);
      return;
    }
    releaseConnection.reset();
    if (openAttempt.current?.submission === "unknown") {
      connect.mutate(openAttempt.current);
      return;
    }
    if (!revision || !selectedId) return;
    const key = `${revision.id}:${selectedId}`;
    if (openAttempt.current?.key !== key)
      openAttempt.current = {
        kind: "open",
        key,
        id: selectedId,
        setup: { revision_id: revision.id, content_hash: revision.content_hash },
        operation: createInstrumentCommandId("open"),
        submission: "not_submitted",
      };
    connect.mutate(openAttempt.current);
  };
  const busy =
    connect.isPending ||
    close.isPending ||
    probe.isPending ||
    releaseConnection.isPending ||
    !!recoverySession ||
    (sessionErrorBlocksClose && !!openAttempt.current);
  const owned =
    selectedDevice?.availability === "active" ||
    selectedDevice?.availability === "quarantined" ||
    selected?.availability === "active" ||
    selected?.availability === "quarantined";
  const selectDevice = (id: string) => {
    setSelectedId(id);
    setSessionError(undefined);
    setSessionErrorBlocksClose(false);
    probe.reset();
    retire.reset();
    releaseConnection.reset();
    openAttempt.current = undefined;
  };
  const openControlWindow = (id: string) => {
    if (devices.find((item) => item.device.id === id)?.device.state !== "available") return;
    setWindowError(undefined);
    const api = window.pywebview?.api;
    if (!api?.open_device_window) {
      setWindowError("当前桌面宿主不支持设备控制窗口，请更新宿主。");
      return;
    }
    void api.open_device_window(id).catch((error) => setWindowError(errorMessage(error)));
  };
  const connectionDetails = selectedDevice ? (
    <div className="grid gap-3 text-sm">
      <p className="text-text-dim">
        已保存的连接配置。查看不会连接或断开设备；编辑请先释放控制，再前往设备管理。
      </p>
      <p>{connectionSummary(selectedDevice.revision.content.connection)}</p>
      <dl className="grid gap-2" aria-label="已保存连接设置">
        {connectionOptionFields(
          catalog.data?.drivers
            .find((driver) => driver.driver_id === selectedDevice.revision.content.driver.driver_id)
            ?.connections.find(
              (spec) => spec.kind === selectedDevice.revision.content.connection.kind,
            )?.options_schema,
        ).map((field) => {
          const value = selectedDevice.revision.content.connection.options?.[field.id];
          return (
            <div key={field.id} className="min-w-0">
              <dt className="font-semibold">{field.label}</dt>
              <dd className="break-all">
                {field.sensitive ? (
                  value == null ? (
                    "未设置"
                  ) : (
                    "已设置（已隐藏）"
                  )
                ) : value == null ? (
                  (field.choiceLabels?.[""] ?? "未指定")
                ) : field.stringMap && typeof value === "object" && !Array.isArray(value) ? (
                  <ul>
                    {Object.entries(value).map(([name, entry]) => (
                      <li key={name}>
                        {name}：{typeof entry === "string" ? entry : "复杂值"}
                      </li>
                    ))}
                  </ul>
                ) : typeof value === "string" ? (
                  (field.choiceLabels?.[value] ?? value)
                ) : typeof value === "boolean" ? (
                  value ? (
                    "是"
                  ) : (
                    "否"
                  )
                ) : typeof value === "number" ? (
                  String(value)
                ) : (
                  "复杂设置（编辑时查看）"
                )}
              </dd>
              {field.description && <dd className="text-sm text-text-dim">{field.description}</dd>}
            </div>
          );
        })}
      </dl>
    </div>
  ) : undefined;
  const inspector =
    selected &&
    selectedDevice &&
    (selectedDevice.device.state === "available" || session || recoverySession) ? (
      <InstrumentInspector
        key={`${(session ?? recoverySession)?.setup.revision_id ?? revision?.id}:${selected.instrument_id}`}
        instrument={selected}
        setup={
          revision ? { revision_id: revision.id, content_hash: revision.content_hash } : undefined
        }
        displayLabel={selectedDevice.device.label}
        hideHeading={!!controlDeviceId}
        registeredDeviceId={selectedDevice.device.id}
        configuration={connectionDetails}
        session={session}
        recoverySession={recoverySession}
        sessionError={sessionError}
        sessionErrorBlocksClose={sessionErrorBlocksClose}
        releaseUnconfirmed={releaseUnconfirmed}
        connectPending={connect.isPending}
        closePending={close.isPending || releasePending}
        onConnect={connectCurrent}
        onClose={() =>
          !releaseInFlight.current &&
          session &&
          close.mutate({ id: session.session_id, abort: false })
        }
        onRecoveryFinished={() => {
          recoverySessionRef.current = undefined;
          setRecoverySession(undefined);
          setSessionError(undefined);
          setSessionErrorBlocksClose(false);
          setWindowError(undefined);
          closeProblem.current = false;
          closeDecision.current?.("saved");
        }}
        onRestoreSession={() => restore.mutate()}
        restorePending={restore.isPending}
        onSessionLost={loseSession}
        onCloseProblem={noteCloseProblem}
        exitRequest={exitRequest}
        onExitCancelled={() => closeDecision.current?.("cancelled")}
        onDisconnectOwner={() => {
          if (selected.owner_kind === "instrument_session" && selected.owner_id)
            close.mutate({ id: selected.owner_id, abort: true });
        }}
      />
    ) : !selectedDevice ? (
      <p>设备不存在或正在加载，请在设备管理中检查登记。</p>
    ) : instrumentsQuery.isFetching ? (
      <p>正在读取设备能力…</p>
    ) : null;
  const connectionRecovery = (
    <>
      {showOperatorSettings && <OperatorSettings onSaved={() => setShowOperatorSettings(false)} />}
      {!session && !recoverySession && sessionErrorBlocksClose && openAttempt.current && (
        <Controls.Button disabled={connect.isPending} onClick={connectCurrent}>
          用原请求核查连接结果
        </Controls.Button>
      )}
    </>
  );
  if (controlDeviceId)
    return (
      <section aria-label="设备控制" className="min-w-0">
        <div className="p-5 pb-0 max-[680px]:px-[13px]">
          {selected ? (
            <InstrumentHeading
              primary
              instrument={selected}
              session={session}
              displayLabel={selectedDevice?.device.label}
              releaseUnconfirmed={releaseUnconfirmed}
              actions={<Controls.Button onClick={refresh}>刷新占用</Controls.Button>}
            />
          ) : (
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3.5">
              <h1 className="min-w-0 text-lg font-semibold [overflow-wrap:anywhere]">
                {selectedDevice?.device.label ?? "设备控制"}
              </h1>
              <Controls.Button onClick={refresh}>刷新占用</Controls.Button>
            </header>
          )}
        </div>
        {closingWindow && <p role="status">正在等待当前操作完成并释放控制…</p>}
        {!closingWindow && releasePending && (
          <p role="status">仍在等待释放回执，暂不能重试或操作设备。</p>
        )}
        {windowError && <p role="alert">{windowError}</p>}
        {connectionRecovery}
        {(devicesQuery.error || accessQuery.error || instrumentsQuery.error) && (
          <p role="alert">
            {errorMessage(devicesQuery.error ?? accessQuery.error ?? instrumentsQuery.error)}
          </p>
        )}
        {selected?.owner_kind === "run" &&
          selected.owner_id &&
          (desktop ? (
            <Controls.Button
              onClick={() => void window.pywebview?.api.open_run_window(selected.owner_id!)}
            >
              查看占用实验
            </Controls.Button>
          ) : (
            <a className="underline" href={`?run=${encodeURIComponent(selected.owner_id)}#runs`}>
              查看占用实验
            </a>
          ))}
        <fieldset disabled={closingWindow || releasePending} className="min-w-0">
          {inspector}
        </fieldset>
      </section>
    );
  return (
    <section className="grid gap-3" aria-labelledby="instruments-heading">
      <header className="flex flex-wrap items-center justify-between rounded-lg border border-line bg-panel p-4">
        <div>
          <h2 className="text-lg font-semibold leading-7" id="instruments-heading">
            设备与驱动
          </h2>
          <p className="text-sm text-text-dim">统一管理连接，在各实验中复用同一设备。</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Controls.Button
            disabled={daemonUnavailable || !catalog.data?.drivers.length || busy || !!configTarget}
            onClick={() => setConfigTarget({ kind: "add" })}
          >
            添加设备
          </Controls.Button>
          <Controls.Button onClick={refresh}>刷新</Controls.Button>
        </div>
      </header>
      <details
        open={devices.length === 0 || !catalog.data?.drivers.length}
        className="rounded-lg border border-line bg-panel p-4"
      >
        <summary>驱动来源与可用驱动</summary>
        {catalog.error && (
          <p role="alert">
            {errorMessage(catalog.error) === "project does not configure an instrument backend"
              ? "尚未启用设备驱动。请先选择驱动来源。"
              : errorMessage(catalog.error)}
          </p>
        )}
        {catalog.data?.drivers.length === 0 && <p>当前来源没有提供可选驱动。</p>}
        <ul>
          {catalog.data?.drivers.map((driver) => (
            <li key={driver.driver_id}>{driver.label}</li>
          ))}
        </ul>
        <LocalDriverSourcesPanel
          daemonUnavailable={daemonUnavailable}
          sessionBusy={busy || !!session}
          hasDrivers={!!catalog.data?.drivers.length}
        />
      </details>
      {devicesQuery.error && <p role="alert">{errorMessage(devicesQuery.error)}</p>}
      {devices.length === 0 && (
        <p className="text-sm">
          暂时没有设备或驱动？
          <a className="underline" href="?lesson=parameters#help">
            先用合成数据体验参数与扫描
          </a>
          ，无需先配置仪器。课程不是设备驱动模拟器。
        </p>
      )}
      {devices.length === 0 && !!catalog.data?.drivers.length && (
        <p className="rounded-lg border border-line bg-panel p-4">
          驱动已就绪。点击“添加设备”，选择驱动并填写连接设置；无需先创建实验。
        </p>
      )}
      <div className={devices.length > 0 ? "grid gap-3" : "hidden"}>
        <section
          aria-label="设备清单"
          className="min-w-0 rounded-lg border border-line bg-panel p-3"
        >
          <div className="mb-3 flex flex-wrap items-end gap-3">
            <Controls.Field>
              搜索设备
              <Controls.Input
                value={search}
                disabled={busy || !!configTarget || !!session}
                onChange={(event) => setSearch(event.target.value)}
              />
            </Controls.Field>
            <Controls.Field>
              显示设备
              <Controls.Select
                value={lifecycle}
                disabled={busy || !!configTarget || !!session}
                onChange={(event) => {
                  setLifecycle(event.target.value);
                  setSelectedId(undefined);
                }}
              >
                <option value="available">在用</option>
                <option value="retired">已停用 / 历史</option>
                <option value="all">全部</option>
              </Controls.Select>
            </Controls.Field>
            <Controls.Field>
              占用状态
              <Controls.Select
                value={filter}
                disabled={busy || !!configTarget || !!session}
                onChange={(event) => setFilter(event.target.value)}
              >
                <option value="all">全部</option>
                <option value="idle">空闲</option>
                <option value="active">占用中</option>
                <option value="quarantined">需要处理</option>
              </Controls.Select>
            </Controls.Field>
          </div>
          {devicesQuery.isPending && <p>正在加载设备…</p>}
          {visibleDevices.length === 0 && (
            <p>没有符合筛选的设备。可切换到“已停用 / 历史”查看旧登记。</p>
          )}
          {session && <p className="mb-3 text-sm">选择其他设备会释放当前手动会话。</p>}
          <DeviceTable
            devices={visibleDevices}
            selectedId={selectedId}
            disabled={busy || !!configTarget}
            driverLabels={
              new Map(catalog.data?.drivers.map((driver) => [driver.driver_id, driver.label]))
            }
            onSelect={(id) => {
              if (id === selectedId) return;
              if (openAttempt.current?.submission === "unknown") {
                setWindowError("请先用原请求核查连接结果，再切换设备。");
                return;
              }
              if (
                recoverySessionRef.current ||
                (session && closeProblem.current && !close.isError)
              ) {
                setExitRequest((current) => current + 1);
                setWindowError("请先在当前设备中保存记录并完成输出处置选择，再切换设备。");
                return;
              }
              if (session) close.mutate({ id: session.session_id, abort: false, selectAfter: id });
              else selectDevice(id);
            }}
            onControl={manageOnly ? openControlWindow : undefined}
          />
          {windowError && <p role="alert">{windowError}</p>}
          {connectionRecovery}
        </section>
        <div className="grid content-start gap-3">
          {selectedDevice && (
            <header className="rounded-lg border border-line bg-panel p-3">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-base font-semibold leading-6">{selectedDevice.device.label}</h3>
                {manageOnly && (
                  <Controls.Button
                    disabled={busy || !!configTarget}
                    onClick={() => setSelectedId(undefined)}
                  >
                    收起详情
                  </Controls.Button>
                )}
              </div>
              {selected?.owner_actor && (
                <p className="text-sm">
                  当前占用：{selected.owner_actor}
                  {selected.owner_kind === "run" ? " · 实验运行" : " · 手动会话"}
                </p>
              )}
              <div className="flex flex-wrap gap-3">
                {selectedDevice.device.state !== "retired" && (
                  <Controls.Button
                    disabled={!!session || busy || owned || !!configTarget}
                    onClick={() => {
                      releaseConnection.reset();
                      probe.mutate(selectedDevice);
                    }}
                  >
                    测试连接
                  </Controls.Button>
                )}

                <Controls.Button
                  disabled={!!session || busy || owned || !!configTarget || !catalog.data}
                  onClick={() => {
                    chooseTab("connection");
                    setConfigTarget({ kind: "edit", deviceId: selectedDevice.device.id });
                  }}
                >
                  {selectedDevice.device.state === "retired" ? "重新启用" : "编辑设备"}
                </Controls.Button>
                {selectedDevice.device.state !== "retired" && (
                  <Controls.Button
                    variant="danger"
                    disabled={!!session || busy || owned || !!configTarget || retire.isPending}
                    onClick={() => retire.mutate()}
                  >
                    停用设备
                  </Controls.Button>
                )}
              </div>
              {savedRevision === selectedDevice.revision.id &&
                selectedDevice.device.state === "retired" && (
                  <p role="status">提交已确认，但设备当前已停用。请核对后再决定是否重新启用。</p>
                )}
              {savedRevision === selectedDevice.revision.id &&
                selectedDevice.device.state !== "retired" &&
                !selectedDevice.last_connection_test && (
                  <p role="status">
                    连接配置已保存，尚未测试。请点击“测试连接”；保存不代表设备状态已读回。
                  </p>
                )}
              {selectedDevice.last_connection_test ? (
                <p role="status">
                  {selectedDevice.device.state === "retired" && "停用前记录："}
                  {selectedDevice.last_connection_test.error
                    ? `上次连接测试失败： ${selectedDevice.last_connection_test.error}`
                    : "连接测试通过，测试会话已释放。"}
                  {selectedDevice.last_connection_test.recorded_at &&
                    ` ${new Date(selectedDevice.last_connection_test.recorded_at).toLocaleString()}`}
                </p>
              ) : savedRevision !== selectedDevice.revision.id ? (
                <p>此连接版本尚未测试。</p>
              ) : null}
              {(probeError || retire.error) && (
                <p role="alert">{errorMessage(probeError ?? retire.error)}</p>
              )}
              {selected?.owner_kind === "run" && selected.owner_id && (
                <a
                  className="underline"
                  href={`?run=${encodeURIComponent(selected.owner_id)}#runs`}
                >
                  查看占用实验
                </a>
              )}
              <nav aria-label="设备详情" className="mt-3 flex flex-wrap gap-2">
                {[
                  ["operations", "操作"],
                  ["connection", "连接与环境"],
                  ["diagnostics", "诊断记录"],
                ]
                  .filter(
                    ([value]) =>
                      (!manageOnly && selectedDevice.device.state !== "retired") ||
                      value !== "operations",
                  )
                  .map(([value, label]) => (
                    <Controls.Button
                      key={value}
                      aria-pressed={tab === value}
                      disabled={!!configTarget}
                      onClick={() => chooseTab(value!)}
                    >
                      {label}
                    </Controls.Button>
                  ))}
              </nav>
              {selectedDevice.device.state === "retired" && (
                <p>此设备已停用，历史记录保留。重新启用会保留设备身份，不会自动连接。</p>
              )}
            </header>
          )}
          {instrumentsQuery.error && <p role="alert">{errorMessage(instrumentsQuery.error)}</p>}
          {accessQuery.error && <p role="alert">{errorMessage(accessQuery.error)}</p>}
          {!!instrumentsQuery.data?.problems.length && (
            <section aria-label="仪器 provider 问题">
              <h3 className="text-base font-semibold leading-6">仪器 provider 问题</h3>
              {instrumentsQuery.data.problems.map((problem, index) => (
                <p role="alert" key={index}>
                  {problem.message}
                </p>
              ))}
            </section>
          )}
          {selectedDevice && tab === "connection" && !configTarget && (
            <section
              className="grid gap-3 rounded-lg border border-line bg-panel p-3"
              aria-label="连接与环境"
            >
              {connectionDetails}
              {selectedDevice.device.state !== "retired" && (
                <>
                  <p>使用“编辑设备”修改连接与驱动环境。设备调试不需要样品或实验路由。</p>
                  <Controls.Button
                    disabled={!!session || owned || busy || !revision}
                    onClick={() => releaseConnection.mutate()}
                  >
                    释放物理连接
                  </Controls.Button>
                  <small>
                    交给外部脚本前先释放控制，再释放物理连接。此操作执行驱动的断开逻辑；不表示输出归零，也不替外部程序预留设备。
                  </small>
                  {releaseConnection.isSuccess && (
                    <p role="status">
                      上次释放物理连接成功；这不是持续连接状态。外部程序结束后重新连接并读取状态。
                    </p>
                  )}
                  {releaseConnection.error && (
                    <p role="alert">{errorMessage(releaseConnection.error)}</p>
                  )}
                </>
              )}
            </section>
          )}
          {selectedDevice && tab === "diagnostics" && (
            <section
              className="grid gap-2 rounded-lg border border-line bg-panel p-3"
              aria-label="诊断记录"
            >
              <h4>最近连接测试</h4>
              <p>
                {selectedDevice.last_connection_test?.error ??
                  (selectedDevice.last_connection_test
                    ? "测试通过；不是当前硬件状态读回。"
                    : "尚无测试记录。")}
              </p>
              <p>
                {selectedDevice.availability === "quarantined"
                  ? "需要处理上次不确定的操作。请在操作页核查占用与恢复入口。"
                  : selectedDevice.availability === "active"
                    ? "当前有任务或手动会话占用；请在操作页查看。"
                    : "当前没有登记的占用。"}
              </p>
              <details>
                <summary>技术信息</summary>
                <code className="break-all">{selectedDevice.revision.id}</code>
                <p className="break-all">{selectedDevice.revision.content.driver.driver_id}</p>
              </details>
            </section>
          )}
          {configTarget?.kind === "edit" && catalog.data && (
            <InstrumentConfigDialog
              key={configTarget.deviceId}
              embedded
              registeredDevices={devices}
              device={selectedDevice}
              catalog={catalog.data}
              description={selected?.description ?? undefined}
              onCancel={() => setConfigTarget(undefined)}
              onPublished={async (saved) => {
                setConfigTarget(undefined);
                setSavedRevision(saved.revision.id);
                if (selectedDevice?.device.state === "retired") setLifecycle(saved.device.state);
                releaseConnection.reset();
                refresh();
                await queryClient.invalidateQueries({ queryKey: ["setup-revisions"] });
              }}
            />
          )}
          <div hidden={!selectedDevice || tab !== "operations" || !!configTarget}>
            {!manageOnly && inspector}
          </div>
        </div>
      </div>
      {configTarget?.kind === "add" && catalog.data && (
        <InstrumentConfigDialog
          key="new"
          registeredDevices={devices}
          catalog={catalog.data}
          description={selected?.description ?? undefined}
          onCancel={() => setConfigTarget(undefined)}
          onPublished={async (saved) => {
            setConfigTarget(undefined);
            setSelectedId(saved.device.id);
            setSavedRevision(saved.revision.id);
            rememberTab(saved.device.id, "connection");
            refresh();
            await queryClient.invalidateQueries({ queryKey: ["setup-revisions"] });
          }}
        />
      )}
    </section>
  );
}
