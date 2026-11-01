import { readOperatorSettings } from "../application/OperatorSettings";
import * as Controls from "../../ui/Controls";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { InstrumentSession } from "../../api-contract";
import { errorMessage } from "../../lib/presentation";
import { InstrumentConfigDialog } from "./InstrumentConfigDialog";
import { InstrumentInspector } from "./InstrumentInspector";
import { DriverSourcePanel } from "./DriverSourcePanel";
import {
  getDevices,
  prepareDeviceAccess,
  testDeviceConnection,
  retireDevice,
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
  key: string;
  id: string;
  setup: InstrumentSession["setup"];
  operation: string;
  actor?: string;
};

type ConfigTarget = { kind: "add" } | { kind: "edit"; deviceId: string };
export function InstrumentsWorkspace({ daemonUnavailable }: { daemonUnavailable: boolean }) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string>();
  const [session, setSession] = useState<InstrumentSession>();
  const sessionRef = useRef<InstrumentSession | undefined>(undefined);
  const [sessionError, setSessionError] = useState<string>();
  const [savedRevision, setSavedRevision] = useState<string>();
  const [configTarget, setConfigTarget] = useState<ConfigTarget>();
  const openAttempt = useRef<OpenAttempt | undefined>(undefined);
  const devicesQuery = useQuery({
    queryKey: ["devices"],
    queryFn: ({ signal }) => getDevices(signal),
    enabled: !daemonUnavailable,
  });
  const devices = devicesQuery.data?.items ?? [];
  const firstDeviceId = devices[0]?.device.id;
  const selectedDevice = devices.find((item) => item.device.id === selectedId);
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
  };
  const loseSession = (message: string) => {
    sessionRef.current = undefined;
    setSession(undefined);
    setSessionError(message);
    refresh();
  };
  const connect = useMutation({
    mutationFn: async (command: OpenAttempt) => {
      command.actor ??= (await readOperatorSettings()).operator;
      return openInstrumentSession(command.id, command.actor, command.setup, command.operation);
    },
    retry: retryTransientInstrumentMutation,
    retryDelay: 250,
    onSuccess: (opened) => {
      sessionRef.current = opened;
      setSession(opened);
      setSessionError(undefined);
      openAttempt.current = undefined;
      refresh();
    },
    onError: (error) => setSessionError(errorMessage(error)),
  });
  const close = useMutation({
    mutationFn: ({ id, abort }: { id: string; abort: boolean; selectAfter?: string }) =>
      abort ? abortInstrumentSession(id) : closeInstrumentSession(id),
    retry: retryTransientInstrumentMutation,
    retryDelay: 250,
    onSuccess: (_, { id, selectAfter }) => {
      if (sessionRef.current?.session_id === id) {
        sessionRef.current = undefined;
        setSession(undefined);
      }
      setSessionError(undefined);
      if (selectAfter) selectDevice(selectAfter);
      refresh();
    },
    onError: (error) => setSessionError(errorMessage(error)),
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
  const retire = useMutation({
    mutationFn: () => {
      if (!selectedDevice) throw new Error("请选择设备。");
      return retireDevice(selectedDevice);
    },
    onSuccess: refresh,
  });
  useEffect(() => {
    if (!selectedId && firstDeviceId) setSelectedId(firstDeviceId);
  }, [selectedId, firstDeviceId]);
  useEffect(() => {
    const release = () => {
      const current = sessionRef.current;
      if (!current) return;
      sessionRef.current = undefined;
      void closeInstrumentSession(current.session_id, true).catch(() => {});
    };
    window.addEventListener("beforeunload", release);
    return () => {
      window.removeEventListener("beforeunload", release);
      release();
    };
  }, []);
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
  const connectCurrent = () => {
    if (!revision || !selectedId) return;
    const key = `${revision.id}:${selectedId}`;
    if (openAttempt.current?.key !== key)
      openAttempt.current = {
        key,
        id: selectedId,
        setup: { revision_id: revision.id, content_hash: revision.content_hash },
        operation: createInstrumentCommandId("open"),
      };
    connect.mutate(openAttempt.current);
  };
  const busy = connect.isPending || close.isPending || probe.isPending;
  const owned =
    selectedDevice?.availability === "active" ||
    selectedDevice?.availability === "quarantined" ||
    selected?.availability === "active" ||
    selected?.availability === "quarantined";
  const selectDevice = (id: string) => {
    setSelectedId(id);
    setSessionError(undefined);
    probe.reset();
    retire.reset();
    openAttempt.current = undefined;
  };
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
            disabled={daemonUnavailable || !catalog.data || busy}
            onClick={() => setConfigTarget({ kind: "add" })}
          >
            添加设备
          </Controls.Button>
          <Controls.Button onClick={refresh}>刷新</Controls.Button>
        </div>
      </header>
      <details className="rounded-lg border border-line bg-panel p-4">
        <summary>已安装驱动</summary>
        <p className="my-2 text-sm text-text-dim">
          更新驱动请前往{" "}
          <a className="underline" href="#settings">
            应用设置
          </a>
          .
        </p>
        {catalog.error && <p role="alert">{errorMessage(catalog.error)}</p>}
        {catalog.data?.drivers.length === 0 && <p>未安装可选驱动。</p>}
        <ul>
          {catalog.data?.drivers.map((driver) => (
            <li key={driver.driver_id}>
              {driver.label} · {driver.implementation_version} · {driver.driver_id}
            </li>
          ))}
        </ul>
        <DriverSourcePanel daemonUnavailable={daemonUnavailable} sessionBusy={busy || !!session} />
      </details>
      {devicesQuery.error && <p role="alert">{errorMessage(devicesQuery.error)}</p>}
      <div className="grid min-h-[640px] grid-cols-[300px_minmax(0,1fr)] gap-3 max-[880px]:grid-cols-1">
        <aside aria-label="已登记设备" className="rounded-lg border border-line bg-panel p-3">
          {devicesQuery.isPending && <p>正在加载设备…</p>}
          {devicesQuery.isSuccess && devices.length === 0 && (
            <p>尚无设备。添加设备即可开始，无需先创建实验 setup。</p>
          )}
          {session && <p className="mb-3 text-sm">选择其他设备会释放当前手动会话。</p>}
          {devices.map((item) => (
            <button
              key={item.device.id}
              title={`查看仪器 ${item.device.id}`}
              aria-pressed={item.device.id === selectedId}
              disabled={busy}
              className="mb-2 grid w-full gap-1 rounded-md border border-line p-3 text-left hover:bg-panel-strong aria-pressed:border-accent aria-pressed:bg-accent-soft disabled:opacity-60"
              onClick={() => {
                if (item.device.id === selectedId) return;
                if (session)
                  close.mutate({
                    id: session.session_id,
                    abort: false,
                    selectAfter: item.device.id,
                  });
                else selectDevice(item.device.id);
              }}
            >
              <strong>{item.device.label}</strong>
              <span className="text-sm">{connectionSummary(item.revision.content.connection)}</span>
              <span>
                {item.device.state === "retired"
                  ? "已退役"
                  : item.availability === "quarantined"
                    ? "需要处理"
                    : item.availability === "active"
                      ? item.owner_kind === "run"
                        ? "运行中"
                        : "手动会话占用中"
                      : "空闲"}
              </span>
            </button>
          ))}
        </aside>
        <div className="grid content-start gap-3">
          {selectedDevice && (
            <header className="rounded-lg border border-line bg-panel p-3">
              <h3 className="text-base font-semibold leading-6">{selectedDevice.device.label}</h3>
              <div className="flex flex-wrap gap-3">
                <Controls.Button
                  disabled={selectedDevice.device.state === "retired" || !!session || busy || owned}
                  onClick={() => probe.mutate(selectedDevice)}
                >
                  测试连接
                </Controls.Button>
                <Controls.Button
                  disabled={
                    selectedDevice.device.state === "retired" ||
                    !!session ||
                    busy ||
                    owned ||
                    !catalog.data
                  }
                  onClick={() =>
                    setConfigTarget({ kind: "edit", deviceId: selectedDevice.device.id })
                  }
                >
                  编辑设备
                </Controls.Button>
                <Controls.Button
                  variant="danger"
                  disabled={
                    selectedDevice.device.state === "retired" ||
                    !!session ||
                    busy ||
                    owned ||
                    retire.isPending
                  }
                  onClick={() => retire.mutate()}
                >
                  退役设备
                </Controls.Button>
              </div>
              {savedRevision === selectedDevice.revision.id &&
                !selectedDevice.last_connection_test && (
                  <p role="status">
                    连接配置已保存，尚未测试。请点击“测试连接”；保存不代表设备状态已读回。
                  </p>
                )}
              <details>
                <summary>已保存的连接版本</summary>
                <code className="break-all">{selectedDevice.revision.id}</code>
              </details>
              {selectedDevice.last_connection_test ? (
                <p role="status">
                  {selectedDevice.last_connection_test.error
                    ? `上次连接测试失败： ${selectedDevice.last_connection_test.error}`
                    : "连接测试通过，测试会话已释放。"}
                  {selectedDevice.last_connection_test.recorded_at &&
                    ` ${new Date(selectedDevice.last_connection_test.recorded_at).toLocaleString()}`}
                </p>
              ) : (
                <p>此连接版本尚未测试。</p>
              )}
              {(probeError || retire.error) && (
                <p role="alert">{errorMessage(probeError ?? retire.error)}</p>
              )}
              {selectedDevice.device.state === "retired" && <p>此设备已退役，执行记录仍保留。</p>}
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
          {selected && selectedDevice?.device.state === "available" ? (
            <InstrumentInspector
              key={`${revision?.id}:${selected.instrument_id}`}
              instrument={selected}
              session={session}
              sessionError={sessionError}
              connectPending={connect.isPending}
              closePending={close.isPending}
              onConnect={connectCurrent}
              onClose={() => session && close.mutate({ id: session.session_id, abort: false })}
              onSessionLost={loseSession}
              onDisconnectOwner={() => {
                if (selected.owner_kind === "instrument_session" && selected.owner_id)
                  close.mutate({ id: selected.owner_id, abort: true });
              }}
            />
          ) : !selectedDevice ? (
            <p>选择设备以查看控制项。</p>
          ) : instrumentsQuery.isFetching ? (
            <p>正在读取设备能力…</p>
          ) : null}
        </div>
      </div>
      {configTarget && catalog.data && (
        <InstrumentConfigDialog
          key={configTarget.kind === "edit" ? configTarget.deviceId : "new"}
          device={
            configTarget.kind === "edit"
              ? devices.find((item) => item.device.id === configTarget.deviceId)
              : undefined
          }
          catalog={catalog.data}
          description={selected?.description ?? undefined}
          onCancel={() => setConfigTarget(undefined)}
          onPublished={async (saved) => {
            setConfigTarget(undefined);
            setSelectedId(saved.device.id);
            setSavedRevision(saved.revision.id);
            refresh();
            await queryClient.invalidateQueries({ queryKey: ["setup-revisions"] });
          }}
        />
      )}
    </section>
  );
}
