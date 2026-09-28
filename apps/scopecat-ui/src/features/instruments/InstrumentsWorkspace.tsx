import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Cable,
  CircleOff,
  LoaderCircle,
  Plus,
  RefreshCw,
  ServerCrash,
} from "lucide-react";
import type { InstrumentSession, InstrumentSessionLease, InstrumentView } from "../../api-contract";
import { errorMessage } from "../../lib/presentation";
import { classes, eyebrow, iconButton } from "../../ui/styles";
import { InstrumentConfigDialog } from "./InstrumentConfigDialog";
import { AvailabilityBadge } from "./InstrumentInterfaceControls";
import { InstrumentInspector } from "./InstrumentInspector";
import { getSetupRevisions, type SavedSetupRevision as SetupRevision } from "../config/setup-api";
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

const EMPTY_INSTRUMENTS: InstrumentView[] = [];
const LOCAL_OPERATOR = "local-operator";
type ConfigTarget = { kind: "add" } | { kind: "edit"; instrumentId: string };

export function InstrumentsWorkspace({ daemonUnavailable }: { daemonUnavailable: boolean }) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string>();
  const [revision, setRevision] = useState<SetupRevision>();
  const [session, setSession] = useState<InstrumentSession>();
  const [sessionError, setSessionError] = useState<string>();
  const [configTarget, setConfigTarget] = useState<ConfigTarget>();
  const sessionRef = useRef<InstrumentSession | undefined>(undefined);
  const closingRef = useRef(false);
  const pendingSelectionRef = useRef<string | undefined>(undefined);
  const openAttemptRef = useRef<{ key: string; operationId: string } | undefined>(undefined);

  const instrumentsQuery = useQuery({
    queryKey: ["instruments", revision?.id, revision?.content_hash],
    queryFn: ({ signal }) => {
      if (!revision) throw new Error("Choose a device context first.");
      return getInstruments(
        { revision_id: revision.id, content_hash: revision.content_hash },
        signal,
      );
    },
    enabled: !daemonUnavailable && revision !== undefined,
  });
  const setupsQuery = useQuery({
    queryKey: ["setup-revisions"],
    queryFn: ({ signal }) => getSetupRevisions(signal),
    enabled: !daemonUnavailable,
  });
  const driverCatalogQuery = useQuery({
    queryKey: ["instrument-drivers"],
    queryFn: ({ signal }) => getDriverCatalog(signal),
    enabled: !daemonUnavailable,
  });

  const instruments = instrumentsQuery.data?.items ?? EMPTY_INSTRUMENTS;
  useEffect(() => {
    if (!revision && setupsQuery.data?.items.length === 1) setRevision(setupsQuery.data.items[0]);
  }, [revision, setupsQuery.data]);
  const selected = instruments.find((instrument) => instrument.instrument_id === selectedId);

  useEffect(() => {
    if (instruments.length === 0) {
      setSelectedId(undefined);
      return;
    }
    if (!selectedId || !instruments.some((instrument) => instrument.instrument_id === selectedId)) {
      setSelectedId(instruments[0]?.instrument_id);
    }
  }, [instruments, selectedId]);

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  useEffect(() => {
    const releaseOnExit = () => {
      const current = sessionRef.current;
      if (!current || closingRef.current) return;
      sessionRef.current = undefined;
      closingRef.current = true;
      void closeInstrumentSession(current.session_id, true).catch(() => {
        // Lease expiry releases the session if this fast close cannot reach the daemon.
      });
    };
    window.addEventListener("beforeunload", releaseOnExit);
    return () => {
      window.removeEventListener("beforeunload", releaseOnExit);
      releaseOnExit();
    };
  }, []);

  const connectMutation = useMutation({
    mutationFn: ({
      instrumentId,
      operator,
      operationId,
      setup,
    }: {
      instrumentId: string;
      operator: string;
      operationId: string;
      setup: InstrumentSession["setup"];
    }) => openInstrumentSession(instrumentId, operator, setup, operationId),
    retry: retryTransientInstrumentMutation,
    retryDelay: 250,
    onSuccess: async (opened) => {
      openAttemptRef.current = undefined;
      closingRef.current = false;
      sessionRef.current = opened;
      setSession(opened);
      setSessionError(undefined);
      await queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
    onError: (cause) => setSessionError(errorMessage(cause)),
  });
  const endMutation = useMutation({
    mutationFn: ({ sessionId, abort }: { sessionId: string; abort: boolean }) =>
      abort ? abortInstrumentSession(sessionId) : closeInstrumentSession(sessionId),
    retry: retryTransientInstrumentMutation,
    retryDelay: 250,
    onSuccess: (_, { sessionId }) => {
      if (sessionRef.current?.session_id === sessionId) {
        sessionRef.current = undefined;
        setSession(undefined);
      }
      const pendingSelection = pendingSelectionRef.current;
      pendingSelectionRef.current = undefined;
      if (pendingSelection) setSelectedId(pendingSelection);
      setSessionError(undefined);
    },
    onError: (cause) => {
      pendingSelectionRef.current = undefined;
      setSessionError(errorMessage(cause));
    },
    onSettled: async () => {
      closingRef.current = false;
      await queryClient.invalidateQueries({ queryKey: ["instruments"] });
    },
  });

  const selectInstrument = (instrumentId: string) => {
    openAttemptRef.current = undefined;
    connectMutation.reset();
    const current = sessionRef.current;
    if (current && !current.instrument_ids.includes(instrumentId)) {
      pendingSelectionRef.current = instrumentId;
      if (!closingRef.current) endSession(current.session_id);
      return;
    }
    setSelectedId(instrumentId);
    setSessionError(undefined);
  };
  const closeCurrent = () => {
    const current = sessionRef.current;
    if (!current || closingRef.current) return;
    pendingSelectionRef.current = undefined;
    endSession(current.session_id);
  };
  const loseCurrent = (message: string) => {
    sessionRef.current = undefined;
    setSession(undefined);
    setSessionError(message);
    void queryClient.invalidateQueries({ queryKey: ["instruments"] });
  };
  const connectCurrent = (instrumentId: string) => {
    if (!revision) return;
    const key = `${revision.id}:${revision.content_hash}:${instrumentId}`;
    if (openAttemptRef.current?.key !== key) {
      openAttemptRef.current = {
        key,
        operationId: createInstrumentCommandId("open"),
      };
    }
    connectMutation.mutate({
      instrumentId,
      setup: { revision_id: revision.id, content_hash: revision.content_hash },
      operator: LOCAL_OPERATOR,
      operationId: openAttemptRef.current.operationId,
    });
  };
  const endSession = (sessionId: string, abort = false) => {
    closingRef.current = true;
    setSessionError(undefined);
    endMutation.mutate({ sessionId, abort });
  };

  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    let timeout: number | undefined;

    const scheduleRenewal = (lease: InstrumentSessionLease) => {
      const renewedAt = Date.parse(lease.renewed_at);
      const leaseDuration = Date.parse(lease.expires_at) - renewedAt;
      const delay = Math.max(0, renewedAt + leaseDuration / 3 - Date.now());
      timeout = window.setTimeout(() => {
        void renewInstrumentSession(lease.session_id).then(
          (renewed) => {
            const current = sessionRef.current;
            if (cancelled || current?.session_id !== renewed.session_id) return;
            const next = { ...current, ...renewed };
            sessionRef.current = next;
            setSession(next);
            scheduleRenewal(renewed);
          },
          () => {
            if (cancelled || sessionRef.current?.session_id !== lease.session_id) return;
            loseCurrent(
              "The instrument session lease was lost. The daemon will release the instrument when the lease expires.",
            );
          },
        );
      }, delay);
    };

    scheduleRenewal(session);
    return () => {
      cancelled = true;
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
    // Renewal reschedules itself from each authoritative lease receipt.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.session_id]);

  useEffect(() => {
    if (!session) return;
    const sessionInstrument = instruments.find((instrument) =>
      session.instrument_ids.includes(instrument.instrument_id),
    );
    if (!sessionInstrument) return;
    const ownerChanged =
      sessionInstrument.availability === "active" &&
      (sessionInstrument.owner_kind !== "instrument_session" ||
        sessionInstrument.owner_id !== session.session_id);
    if (sessionInstrument.availability === "quarantined" || ownerChanged) {
      loseCurrent(
        sessionInstrument.availability === "quarantined"
          ? "The daemon quarantined this instrument after an uncertain operation."
          : "The instrument is now owned by another run or session.",
      );
    }
    // Reconcile the local session whenever a fresh canonical instrument view arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrumentsQuery.dataUpdatedAt, session?.session_id]);

  return (
    <section className="grid gap-2.5" aria-labelledby="instruments-heading">
      <header className="flex min-h-[70px] items-center justify-between gap-6 rounded-lg border border-line bg-panel px-3.5 py-3 max-[680px]:items-start max-[460px]:grid">
        <div>
          <span className={eyebrow}>Direct hardware interaction</span>
          <h2 className="m-0 text-base font-[650] tracking-[-0.025em]" id="instruments-heading">
            Instruments
          </h2>
          <p className="mt-1 mb-0 text-[0.66rem] text-text-dim">
            Inspect device controls, then explicitly connect one device for direct interaction.
          </p>
        </div>
      </header>

      <label className="flex flex-col gap-1">
        Device context
        <select
          aria-label="Device context"
          className="border rounded p-2"
          value={revision?.id ?? ""}
          disabled={
            daemonUnavailable ||
            !!session ||
            connectMutation.isPending ||
            endMutation.isPending ||
            !!configTarget
          }
          onChange={(event) => {
            const nextContext = setupsQuery.data?.items.find(
              (item) => item.id === event.target.value,
            );
            if (!nextContext) return;
            setRevision(nextContext);
            setSelectedId(undefined);
            setSessionError(undefined);
            openAttemptRef.current = undefined;
            connectMutation.reset();
          }}
        >
          <option value="" disabled>
            Choose devices for this page
          </option>
          {revision && !setupsQuery.data?.items.some((item) => item.id === revision.id) && (
            <option value={revision.id}>{revision.id}</option>
          )}
          {(setupsQuery.data?.items ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.id}
            </option>
          ))}
        </select>
        <span className="text-sm">
          Selection applies to this page and does not connect devices. Disconnect before changing
          context.
        </span>
      </label>
      <button
        type="button"
        disabled={setupsQuery.isFetching || daemonUnavailable}
        onClick={() => void setupsQuery.refetch()}
      >
        Refresh device contexts
      </button>
      {setupsQuery.error && <p role="alert">{errorMessage(setupsQuery.error)}</p>}
      {setupsQuery.isSuccess && setupsQuery.data.items.length === 0 && (
        <p>No device contexts saved yet. Save a setup in Configuration.</p>
      )}

      {(instrumentsQuery.data?.problems ?? []).length > 0 && (
        <div
          className="flex items-start gap-2.5 rounded-md border border-[rgb(207_173_104_/_25%)] bg-yellow-soft px-3 py-2.5 text-yellow"
          role="status"
        >
          <AlertTriangle className="mt-px flex-none" size={17} aria-hidden="true" />
          <div>
            <strong className="text-[0.67rem] text-text-soft">Instrument provider issues</strong>
            {(instrumentsQuery.data?.problems ?? []).map((problem, index) => (
              <p
                className="mt-[3px] mb-0 text-[0.61rem] leading-[1.45] text-text-dim"
                key={`${problem.code}-${index}`}
              >
                {problem.message}
              </p>
            ))}
          </div>
        </div>
      )}

      <div className="grid min-h-[640px] grid-cols-[minmax(300px,350px)_minmax(0,1fr)] items-start overflow-hidden rounded-lg border border-line bg-panel max-[1100px]:grid-cols-[minmax(280px,315px)_minmax(0,1fr)] max-[880px]:block max-[880px]:min-h-0 max-[880px]:overflow-visible max-[880px]:border-0 max-[880px]:bg-transparent">
        <aside
          className="sticky top-[60px] flex min-h-[640px] max-h-[calc(100vh-72px)] flex-col border-r border-line bg-panel-soft px-2.5 py-3 max-[880px]:static max-[880px]:mb-2.5 max-[880px]:min-h-0 max-[880px]:max-h-none max-[880px]:rounded-lg max-[880px]:border max-[880px]:border-line"
          aria-label="Configured instruments"
        >
          <div className="flex items-center justify-between px-[3px] pb-2.5">
            <div>
              <h3 className="m-0 text-[0.82rem] font-bold">Devices</h3>
              <small className="mt-0.5 block text-[0.58rem] text-text-dim">
                {instruments.length} configured
              </small>
            </div>
            <div className="flex gap-1.5">
              <button
                type="button"
                className={classes(iconButton, "size-[30px]")}
                aria-label="Add instrument"
                title={driverCatalogQuery.isError ? "Driver catalog unavailable" : "Add instrument"}
                disabled={
                  !revision ||
                  !!session ||
                  connectMutation.isPending ||
                  endMutation.isPending ||
                  driverCatalogQuery.isPending ||
                  driverCatalogQuery.isError
                }
                onClick={() => {
                  setSessionError(undefined);
                  setConfigTarget({ kind: "add" });
                }}
              >
                <Plus size={15} />
              </button>
              <button
                type="button"
                className={classes(iconButton, "size-[30px]")}
                aria-label="Refresh instruments"
                title="Refresh instruments"
                onClick={() => void queryClient.invalidateQueries({ queryKey: ["instruments"] })}
              >
                <RefreshCw
                  size={15}
                  className={instrumentsQuery.isFetching ? "animate-spin" : undefined}
                />
              </button>
            </div>
          </div>

          {daemonUnavailable ? (
            <InstrumentListMessage
              icon={<ServerCrash />}
              title="Instrument index unavailable"
              detail="Reconnect to the local daemon to inspect configured devices."
            />
          ) : !revision ? (
            <InstrumentListMessage
              icon={<Cable />}
              title="Choose a device context"
              detail="Select saved devices above to inspect their controls."
            />
          ) : instrumentsQuery.isPending ? (
            <InstrumentListMessage
              icon={<LoaderCircle className="animate-spin" />}
              title="Loading instruments"
              detail="Reading the active configuration and current instrument owners."
            />
          ) : instrumentsQuery.isError ? (
            <InstrumentListMessage
              icon={<AlertTriangle />}
              title="Could not load instruments"
              detail={errorMessage(instrumentsQuery.error)}
              error
            />
          ) : instruments.length === 0 ? (
            <InstrumentListMessage
              icon={<CircleOff />}
              title="No instruments configured"
              detail="Add a registered device to the active laboratory configuration."
            />
          ) : (
            <div className="flex min-h-0 flex-1 flex-col gap-[7px] overflow-auto p-0.5 [scrollbar-color:#344252_transparent] [scrollbar-width:thin] max-[880px]:grid max-[880px]:grid-flow-col max-[880px]:auto-cols-[minmax(280px,46vw)] max-[880px]:overflow-x-auto">
              {instruments.map((instrument) => (
                <InstrumentListItem
                  key={instrument.instrument_id}
                  instrument={instrument}
                  selected={instrument.instrument_id === selected?.instrument_id}
                  connected={session?.instrument_ids.includes(instrument.instrument_id)}
                  onSelect={() => selectInstrument(instrument.instrument_id)}
                />
              ))}
            </div>
          )}
        </aside>

        {selected ? (
          <InstrumentInspector
            key={`${revision?.id}:${revision?.content_hash}:${selected.instrument_id}`}
            instrument={selected}
            session={session}
            sessionError={sessionError}
            connectPending={
              connectMutation.isPending &&
              connectMutation.variables?.instrumentId === selected.instrument_id
            }
            closePending={endMutation.isPending}
            configurationPending={
              configTarget?.kind === "edit" &&
              configTarget.instrumentId === selected.instrument_id &&
              driverCatalogQuery.isFetching
            }
            configurationUnavailable={
              !!session ||
              connectMutation.isPending ||
              endMutation.isPending ||
              driverCatalogQuery.isError
            }
            onConnect={() => connectCurrent(selected.instrument_id)}
            onClose={closeCurrent}
            onSessionLost={loseCurrent}
            onDisconnectOwner={() => {
              if (
                selected.owner_kind === "instrument_session" &&
                selected.owner_id &&
                !closingRef.current
              ) {
                endSession(selected.owner_id, true);
              }
            }}
            onConfigure={() => {
              setSessionError(undefined);
              setConfigTarget({ kind: "edit", instrumentId: selected.instrument_id });
            }}
          />
        ) : (
          <div className="relative grid min-h-[640px] min-w-0 place-items-center content-start gap-3.5 p-5 max-[880px]:min-h-[560px] max-[880px]:rounded-lg max-[880px]:border max-[880px]:border-line max-[680px]:px-[13px] max-[680px]:py-4">
            <InstrumentListMessage
              icon={<Cable />}
              title="Nothing selected"
              detail="Choose a configured instrument to inspect its controls."
            />
          </div>
        )}
      </div>

      {configTarget &&
        revision &&
        driverCatalogQuery.data &&
        !driverCatalogQuery.isFetching &&
        !driverCatalogQuery.isError && (
          <InstrumentConfigDialog
            key={`${configTarget.kind}-${
              configTarget.kind === "edit" ? configTarget.instrumentId : "new"
            }-${revision.id}`}
            revision={revision}
            catalog={driverCatalogQuery.data}
            instrumentId={configTarget.kind === "edit" ? configTarget.instrumentId : undefined}
            description={
              configTarget.kind === "edit"
                ? (instruments.find(
                    (instrument) => instrument.instrument_id === configTarget.instrumentId,
                  )?.description ?? undefined)
                : undefined
            }
            onCancel={() => setConfigTarget(undefined)}
            onPublished={async (instrumentId, saved) => {
              setConfigTarget(undefined);
              setRevision(saved);
              setSelectedId(instrumentId);
              await Promise.all([
                queryClient.invalidateQueries({ queryKey: ["setup-revisions"] }),
                queryClient.invalidateQueries({ queryKey: ["instruments"] }),
              ]);
            }}
          />
        )}
    </section>
  );
}

function InstrumentListItem({
  instrument,
  selected,
  connected,
  onSelect,
}: {
  instrument: InstrumentView;
  selected: boolean;
  connected?: boolean;
  onSelect: () => void;
}) {
  const label = instrument.description?.label ?? instrument.instrument_id;
  return (
    <button
      type="button"
      className={classes(
        "grid w-full cursor-pointer gap-[7px] rounded-md border border-transparent bg-transparent p-2.5 text-left text-text hover:border-line hover:bg-[rgb(255_255_255_/_2%)]",
        selected && "border-line-strong bg-panel-strong",
      )}
      aria-current={selected ? "true" : undefined}
      title={`Inspect instrument ${instrument.instrument_id}`}
      onClick={onSelect}
    >
      <span className="flex min-w-0 items-center justify-between gap-2">
        <strong className="overflow-hidden text-[0.76rem] font-[680] text-ellipsis whitespace-nowrap">
          {label}
        </strong>
        <AvailabilityBadge availability={instrument.availability} connected={connected} />
      </span>
      <code className="overflow-hidden text-[0.58rem] text-ellipsis whitespace-nowrap text-text-dim">
        {instrument.instrument_id}
      </code>
      <dl className="mt-0.5 mb-0 grid gap-1">
        <div className="grid min-w-0 grid-cols-[65px_minmax(0,1fr)] gap-1.5">
          <dt className="text-[0.54rem] font-bold text-text-dim uppercase">Connection</dt>
          <dd className="m-0 overflow-hidden text-[0.61rem] text-ellipsis whitespace-nowrap text-text-soft">
            {connectionSummary(instrument.connection)}
          </dd>
        </div>
      </dl>
      {instrument.owner_id && (
        <span className="overflow-hidden border-t border-line pt-[7px] text-[0.57rem] text-ellipsis whitespace-nowrap text-yellow">
          {instrument.owner_kind === "run" ? "Run in progress" : "Interactive session active"}
        </span>
      )}
    </button>
  );
}

function InstrumentListMessage({
  icon,
  title,
  detail,
  error = false,
}: {
  icon: React.ReactNode;
  title: string;
  detail: string;
  error?: boolean;
}) {
  return (
    <div className="grid justify-items-center px-[18px] py-[55px] text-center text-text-dim max-[880px]:min-w-[280px]">
      <span
        className={classes(
          "mb-3 grid size-[38px] place-items-center rounded-full bg-panel-strong text-text-dim",
          error && "text-red",
        )}
        aria-hidden="true"
      >
        {icon}
      </span>
      <strong className={classes("text-[0.74rem] text-text-soft", error && "text-red")}>
        {title}
      </strong>
      <p className="mt-1.5 mb-0 max-w-[270px] text-[0.63rem] leading-[1.55]">{detail}</p>
    </div>
  );
}
