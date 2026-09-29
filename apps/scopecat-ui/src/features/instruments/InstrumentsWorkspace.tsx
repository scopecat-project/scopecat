import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { InstrumentSession } from "../../api-contract";
import { errorMessage } from "../../lib/presentation";
import { InstrumentConfigDialog } from "./InstrumentConfigDialog";
import { InstrumentInspector } from "./InstrumentInspector";
import { DriverSourcePanel } from "./DriverSourcePanel";
import { getDevices, prepareDeviceAccess, testDeviceConnection, retireDevice } from "./device-api";
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

type ConfigTarget = { kind: "add" } | { kind: "edit"; deviceId: string };
export function InstrumentsWorkspace({ daemonUnavailable }: { daemonUnavailable: boolean }) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string>();
  const [session, setSession] = useState<InstrumentSession>();
  const sessionRef = useRef<InstrumentSession | undefined>(undefined);
  const [sessionError, setSessionError] = useState<string>();
  const [configTarget, setConfigTarget] = useState<ConfigTarget>();
  const openAttempt = useRef<{ key: string; operation: string } | undefined>(undefined);
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
      if (!selectedId) throw new Error("Choose a device.");
      return prepareDeviceAccess(selectedId);
    },
    enabled: !daemonUnavailable && selectedDevice?.device.state === "available",
  });
  const revision = accessQuery.data;
  const instrumentsQuery = useQuery({
    queryKey: ["instruments", revision?.id, revision?.content_hash],
    queryFn: ({ signal }) => {
      if (!revision) throw new Error("Prepare the device connection first.");
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
    mutationFn: ({
      id,
      setup,
      operation,
    }: {
      id: string;
      setup: InstrumentSession["setup"];
      operation: string;
    }) => openInstrumentSession(id, "local-operator", setup, operation),
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
    mutationFn: () => {
      if (!selectedDevice) throw new Error("Choose a device.");
      return testDeviceConnection(selectedDevice, createInstrumentCommandId("test"));
    },
    onSettled: refresh,
  });
  const retire = useMutation({
    mutationFn: () => {
      if (!selectedDevice) throw new Error("Choose a device.");
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
                loseSession(
                  "The device session was interrupted. Its lease will release ownership; inspect the device before reconnecting.",
                );
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
      loseSession(
        "Device ownership changed. Inspect its current owner or resolve the reported attention before reconnecting.",
      );
    // Reconcile only after a fresh server response.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrumentsQuery.dataUpdatedAt, session?.session_id]);
  const connectCurrent = () => {
    if (!revision || !selectedId) return;
    const key = `${revision.id}:${selectedId}`;
    if (openAttempt.current?.key !== key)
      openAttempt.current = { key, operation: createInstrumentCommandId("open") };
    connect.mutate({
      id: selectedId,
      setup: { revision_id: revision.id, content_hash: revision.content_hash },
      operation: openAttempt.current.operation,
    });
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
      <header className="flex items-center justify-between rounded-lg border border-line bg-panel p-4">
        <div>
          <h2 id="instruments-heading">Devices and drivers</h2>
          <p className="text-sm text-text-dim">
            Manage connections once, then use the same devices in your experiments.
          </p>
        </div>
        <div className="flex gap-3">
          <button
            disabled={daemonUnavailable || !catalog.data || busy}
            onClick={() => setConfigTarget({ kind: "add" })}
          >
            Add device
          </button>
          <button onClick={refresh}>Refresh</button>
        </div>
      </header>
      <details className="rounded-lg border border-line bg-panel p-4">
        <summary>Installed drivers</summary>
        <p className="my-2 text-sm text-text-dim">
          Viewing driver metadata does not connect devices. Prepare capability updates in{" "}
          <a className="underline" href="#settings">
            Application settings
          </a>
          .
        </p>
        {catalog.error && <p role="alert">{errorMessage(catalog.error)}</p>}
        {catalog.data?.drivers.length === 0 && <p>No optional drivers are installed.</p>}
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
        <aside
          aria-label="Registered devices"
          className="rounded-lg border border-line bg-panel p-3"
        >
          {devicesQuery.isPending && <p>Loading devices…</p>}
          {devicesQuery.isSuccess && devices.length === 0 && (
            <p>No devices yet. Add a device to start; no experiment setup is required.</p>
          )}
          {session && (
            <p className="mb-3 text-sm">Selecting another device releases this manual session.</p>
          )}
          {devices.map((item) => (
            <button
              key={item.device.id}
              title={`Inspect instrument ${item.device.id}`}
              aria-pressed={item.device.id === selectedId}
              disabled={busy}
              className="mb-2 grid w-full gap-1 rounded border border-line p-3 text-left disabled:opacity-60"
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
                  ? "Retired"
                  : item.availability === "quarantined"
                    ? "Needs attention"
                    : item.availability === "active"
                      ? item.owner_kind === "run"
                        ? "Run in progress"
                        : "Manual session in use"
                      : "Idle"}
              </span>
            </button>
          ))}
        </aside>
        <div className="grid content-start gap-3">
          {selectedDevice && (
            <header className="rounded-lg border border-line bg-panel p-3">
              <h3>{selectedDevice.device.label}</h3>
              <div className="flex gap-3">
                <button
                  disabled={selectedDevice.device.state === "retired" || !!session || busy || owned}
                  onClick={() => probe.mutate()}
                >
                  Test connection
                </button>
                <button
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
                  Edit device
                </button>
                <button
                  disabled={
                    selectedDevice.device.state === "retired" ||
                    !!session ||
                    busy ||
                    owned ||
                    retire.isPending
                  }
                  onClick={() => retire.mutate()}
                >
                  Retire device
                </button>
              </div>
              {selectedDevice.last_connection_test ? (
                <p role="status">
                  {selectedDevice.last_connection_test.error
                    ? `Last connection test failed: ${selectedDevice.last_connection_test.error}`
                    : "Connection test passed; the test session was released."}
                  {selectedDevice.last_connection_test.recorded_at &&
                    ` ${new Date(selectedDevice.last_connection_test.recorded_at).toLocaleString()}`}
                </p>
              ) : (
                <p>This connection version has not been tested.</p>
              )}
              {(probe.error || retire.error) && (
                <p role="alert">{errorMessage(probe.error ?? retire.error)}</p>
              )}
              {selectedDevice.device.state === "retired" && (
                <p>This device is retired. Its execution records are retained.</p>
              )}
            </header>
          )}
          {instrumentsQuery.error && <p role="alert">{errorMessage(instrumentsQuery.error)}</p>}
          {accessQuery.error && <p role="alert">{errorMessage(accessQuery.error)}</p>}
          {!!instrumentsQuery.data?.problems.length && (
            <section aria-label="Instrument provider issues">
              <h3>Instrument provider issues</h3>
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
            <p>Select a device to inspect its controls. Selection does not connect hardware.</p>
          ) : instrumentsQuery.isFetching ? (
            <p>Reading device capabilities…</p>
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
            refresh();
            await queryClient.invalidateQueries({ queryKey: ["setup-revisions"] });
          }}
        />
      )}
    </section>
  );
}
