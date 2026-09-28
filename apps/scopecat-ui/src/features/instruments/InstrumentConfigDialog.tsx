import { useMemo, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { Cable, Check, LoaderCircle, PlugZap, Save, X } from "lucide-react";
import type {
  DriverCatalog,
  DriverConnectionSpec,
  DriverSpec,
  InstrumentConnection,
  InstrumentDescription,
  InstrumentDriverProbeReceipt,
  InstrumentSpec,
  InstrumentStateSetting,
} from "../../api-contract";
import { errorMessage } from "../../lib/presentation";
import {
  classes,
  dialogBackdrop,
  dialogDescription,
  dialogPopup,
  dialogTitle,
  dialogViewport,
  iconButton,
  primaryButton,
  secondaryButton,
} from "../../ui/styles";
import { InstrumentDefaultsEditor } from "./InstrumentDefaultsEditor";
import { probeInstrumentDriver, publishInstrumentSpec } from "./instrument-api";

import type { SavedSetupRevision as SetupRevision } from "../config/setup-api";

type ConnectionKind = InstrumentConnection["kind"];
type TcpConnection = Extract<InstrumentConnection, { kind: "tcpip_socket" }>;
type SerialConnection = Extract<InstrumentConnection, { kind: "serial" }>;
type ConnectionOptions = NonNullable<InstrumentConnection["options"]>;
type OptionValue =
  | null
  | boolean
  | number
  | string
  | OptionValue[]
  | { [key: string]: OptionValue };

interface OptionField {
  id: string;
  label: string;
  type: "array" | "boolean" | "integer" | "number" | "object" | "string";
  defaultValue?: OptionValue;
  required: boolean;
  minimum?: number;
  maximum?: number;
}

export function InstrumentConfigDialog({
  revision,
  catalog,
  instrumentId,
  description,
  onCancel,
  onPublished,
}: {
  revision: SetupRevision;
  catalog: DriverCatalog;
  instrumentId?: string;
  description?: InstrumentDescription;
  onCancel: () => void;
  onPublished: (instrumentId: string, revision: SetupRevision) => void | Promise<void>;
}) {
  const existing = instrumentId
    ? revision.setup.instrument_registry.instruments.find(
        (instrument) => instrument.id === instrumentId,
      )
    : undefined;
  if (instrumentId && !existing) {
    return <MissingInstrumentDialog instrumentId={instrumentId} onCancel={onCancel} />;
  }
  return (
    <InstrumentConfigEditor
      revision={revision}
      catalog={catalog}
      existing={existing}
      configuredDescription={description}
      onCancel={onCancel}
      onPublished={onPublished}
    />
  );
}

function InstrumentConfigEditor({
  revision,
  catalog,
  existing,
  configuredDescription,
  onCancel,
  onPublished,
}: {
  revision: SetupRevision;
  catalog: DriverCatalog;
  existing?: InstrumentSpec;
  configuredDescription?: InstrumentDescription;
  onCancel: () => void;
  onPublished: (instrumentId: string, revision: SetupRevision) => void | Promise<void>;
}) {
  const initialDriver = catalog.drivers.find((driver) => driver.driver_id === existing?.driver_id);
  const firstDriver = initialDriver ?? catalog.drivers[0];
  const [instrumentId, setInstrumentId] = useState(existing?.id ?? "");
  const [driverId, setDriverId] = useState(existing?.driver_id ?? firstDriver?.driver_id ?? "");
  const [connection, setConnection] = useState<InstrumentConnection>(() =>
    initialConnection(existing?.connection, firstDriver),
  );
  const [defaultState, setDefaultState] = useState<InstrumentStateSetting[]>(
    existing?.default_state ?? [],
  );
  const [runStart, setRunStart] = useState<InstrumentSpec["run_start"]>(
    existing?.run_start ?? "preserve",
  );
  const [successAction, setSuccessAction] = useState<InstrumentSpec["success_action"]>(
    existing?.success_action ?? "release",
  );
  const [defaultsValid, setDefaultsValid] = useState(true);
  const [invalidOptionFields, setInvalidOptionFields] = useState<Set<string>>(new Set());
  const [probedDescription, setProbedDescription] = useState<InstrumentDescription>();
  const [note, setNote] = useState("");
  const [contextName, setContextName] = useState(`${revision.id} (edited)`);
  const [probe, setProbe] = useState<InstrumentDriverProbeReceipt>();
  const [probePending, setProbePending] = useState(false);
  const [publishPending, setPublishPending] = useState(false);
  const [error, setError] = useState<string>();
  const driver = catalog.drivers.find((candidate) => candidate.driver_id === driverId);
  const description =
    existing?.driver_id === driverId
      ? (configuredDescription ?? probedDescription)
      : probedDescription;
  const connectionSpec = driver?.connections.find(
    (candidate) => candidate.kind === connection.kind,
  );
  const optionFields = connectionOptionFields(connectionSpec?.options_schema);
  const options = connection.options ?? {};
  const spec = useMemo(
    () =>
      proposedSpec(
        instrumentId.trim(),
        driverId,
        connection,
        defaultState,
        runStart,
        successAction,
        existing,
      ),
    [connection, defaultState, driverId, existing, instrumentId, runStart, successAction],
  );
  const changed = !existing || JSON.stringify(spec) !== JSON.stringify(existing);
  const bindingValid =
    driver !== undefined &&
    driver.connections.some((candidate) => candidate.kind === connection.kind) &&
    spec.id.length > 0 &&
    !(
      !existing &&
      revision.setup.instrument_registry.instruments.some((candidate) => candidate.id === spec.id)
    ) &&
    connectionIsValid(connection) &&
    optionsAreValid(options, optionFields) &&
    invalidOptionFields.size === 0;
  const publishValid =
    contextName.trim().length > 0 &&
    contextName.trim() !== revision.id &&
    bindingValid &&
    defaultsValid &&
    (runStart === "preserve" || defaultState.length > 0) &&
    (successAction !== "apply_safe_state" || safeActionsConfigured(spec));
  const busy = probePending || publishPending;

  const chooseDriver = (nextDriverId: string) => {
    const nextDriver = catalog.drivers.find((candidate) => candidate.driver_id === nextDriverId);
    const restoringExisting = existing?.driver_id === nextDriverId;
    setDriverId(nextDriverId);
    setConnection(
      initialConnection(restoringExisting ? existing.connection : undefined, nextDriver),
    );
    setDefaultState(restoringExisting ? (existing.default_state ?? []) : []);
    setRunStart(restoringExisting ? existing.run_start : "preserve");
    setDefaultsValid(true);
    setInvalidOptionFields(new Set());
    setProbedDescription(undefined);
    clearFeedback();
  };
  const chooseConnectionKind = (kind: ConnectionKind) => {
    const nextSpec = driver?.connections.find((candidate) => candidate.kind === kind);
    setConnection(newConnection(kind, nextSpec));
    setInvalidOptionFields(new Set());
    clearFeedback();
  };
  const updateConnection = (next: InstrumentConnection) => {
    setConnection(next);
    clearFeedback();
  };
  const testConnection = async () => {
    if (!bindingValid) return;
    setProbePending(true);
    setError(undefined);
    setProbe(undefined);
    try {
      const receipt = await probeInstrumentDriver({
        binding: {
          id: spec.id,
          driver_id: spec.driver_id,
          connection: spec.connection,
        },
      });
      setProbe(receipt);
      if (receipt.status === "connected" && receipt.description) {
        setProbedDescription(receipt.description);
      }
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setProbePending(false);
    }
  };
  const publish = async () => {
    if (!publishValid || !changed) return;
    setPublishPending(true);
    setError(undefined);
    try {
      const saved = await publishInstrumentSpec({
        revision,
        name: contextName.trim(),
        spec,
        originalInstrumentId: existing?.id,
        actor: "local-operator",
        note: note.trim(),
      });
      await onPublished(spec.id, saved);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setPublishPending(false);
    }
  };
  const clearFeedback = () => {
    setProbe(undefined);
    setError(undefined);
  };

  return (
    <Dialog.Root open onOpenChange={(open) => !open && !busy && onCancel()}>
      <Dialog.Portal>
        <Dialog.Backdrop className={dialogBackdrop} />
        <Dialog.Viewport className={dialogViewport}>
          <Dialog.Popup className={classes(dialogPopup, "w-[min(680px,100%)]")}>
            <header className="grid grid-cols-[38px_minmax(0,1fr)_auto] items-center gap-2.5 border-b border-line px-[18px] py-4">
              <span
                className="grid size-9 place-items-center rounded-[9px] bg-accent-soft text-accent"
                aria-hidden="true"
              >
                <Cable size={19} />
              </span>
              <div>
                <Dialog.Title className={dialogTitle}>
                  {existing ? "Configure instrument" : "Add instrument"}
                </Dialog.Title>
                <Dialog.Description
                  className={classes(
                    dialogDescription,
                    "mt-[3px] overflow-hidden text-[0.59rem] text-ellipsis whitespace-nowrap",
                  )}
                >
                  {existing
                    ? `${existing.id} · save changes as a new device context`
                    : "Choose a registered driver and save a new device context"}
                </Dialog.Description>
              </div>
              <Dialog.Close
                className={iconButton}
                aria-label="Close instrument configuration"
                disabled={busy}
              >
                <X size={16} />
              </Dialog.Close>
            </header>

            <div className="grid gap-3 p-[18px] [&_label>span]:text-[0.53rem] [&_label>span]:font-extrabold [&_label>span]:tracking-[0.07em] [&_label>span]:text-text-dim [&_label>span]:uppercase [&_input:not([type=checkbox])]:min-h-[34px] [&_input:not([type=checkbox])]:w-full [&_input:not([type=checkbox])]:min-w-0 [&_input:not([type=checkbox])]:rounded-sm [&_input:not([type=checkbox])]:border [&_input:not([type=checkbox])]:border-line [&_input:not([type=checkbox])]:bg-bg [&_input:not([type=checkbox])]:px-[9px] [&_input:not([type=checkbox])]:text-[0.64rem] [&_input:not([type=checkbox])]:text-text [&_input:not([type=checkbox])]:outline-0 [&_input:not([type=checkbox]):focus]:border-accent [&_select]:min-h-[34px] [&_select]:w-full [&_select]:min-w-0 [&_select]:rounded-sm [&_select]:border [&_select]:border-line [&_select]:bg-bg [&_select]:px-[9px] [&_select]:text-[0.64rem] [&_select]:text-text [&_select]:outline-0 [&_select:focus]:border-accent">
              <div className="grid grid-cols-2 gap-2.5 max-[680px]:grid-cols-2 max-[460px]:grid-cols-1 [&_label]:grid [&_label]:gap-[5px]">
                <label>
                  <span>Instrument ID</span>
                  <input
                    value={instrumentId}
                    disabled={existing !== undefined}
                    onChange={(event) => {
                      setInstrumentId(event.target.value);
                      clearFeedback();
                    }}
                    placeholder="readout-vna"
                  />
                </label>
                <label>
                  <span>Driver</span>
                  <select value={driverId} onChange={(event) => chooseDriver(event.target.value)}>
                    {!driver && <option value={driverId}>Unavailable · {driverId}</option>}
                    {catalog.drivers.map((candidate) => (
                      <option key={candidate.driver_id} value={candidate.driver_id}>
                        {driverLabel(candidate)}
                      </option>
                    ))}
                  </select>
                </label>
                {driver && (
                  <label>
                    <span>Connection</span>
                    <select
                      value={connection.kind}
                      onChange={(event) =>
                        chooseConnectionKind(event.target.value as ConnectionKind)
                      }
                    >
                      {driver.connections.map((candidate) => (
                        <option key={candidate.kind} value={candidate.kind}>
                          {connectionKindLabel(candidate.kind)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>

              {connection.kind === "tcpip_socket" && (
                <TcpConnectionFields connection={connection} onChange={updateConnection} />
              )}
              {connection.kind === "serial" && (
                <SerialConnectionFields connection={connection} onChange={updateConnection} />
              )}

              {optionFields.length > 0 && (
                <fieldset className="m-0 min-w-0 rounded-sm border border-line p-2.5">
                  <legend className="px-[5px] text-[0.53rem] font-extrabold tracking-[0.07em] text-text-dim uppercase">
                    Driver options
                  </legend>
                  <div className="grid grid-cols-3 gap-2.5 max-[680px]:grid-cols-2 max-[460px]:grid-cols-1 [&_label:not([data-toggle])]:grid [&_label:not([data-toggle])]:gap-[5px]">
                    {optionFields.map((field) => (
                      <OptionInput
                        key={`${driverId}-${connection.kind}-${field.id}`}
                        field={field}
                        value={jsonOptionValue(options[field.id]) ?? field.defaultValue}
                        onChange={(value) => {
                          const nextOptions = { ...options };
                          if (value === undefined) delete nextOptions[field.id];
                          else nextOptions[field.id] = value;
                          updateConnection({ ...connection, options: nextOptions });
                        }}
                        onValidityChange={(valid) =>
                          setInvalidOptionFields((current) => {
                            const next = new Set(current);
                            if (valid) next.delete(field.id);
                            else next.add(field.id);
                            return next;
                          })
                        }
                      />
                    ))}
                  </div>
                </fieldset>
              )}

              {existing && existing.driver_id !== driverId && (
                <p className={configNote}>
                  Changing the driver clears configured startup state because property contracts may
                  differ.
                </p>
              )}

              {driver && (
                <InstrumentDefaultsEditor
                  key={`${driverId}-${description?.implementation_id ?? "unknown"}`}
                  description={description}
                  defaultState={defaultState}
                  runStart={runStart}
                  onDefaultStateChange={setDefaultState}
                  onRunStartChange={setRunStart}
                  onValidityChange={setDefaultsValid}
                />
              )}

              <label className="grid gap-[5px]">
                <span>After successful run</span>
                <select
                  value={successAction}
                  onChange={(event) =>
                    setSuccessAction(event.target.value as InstrumentSpec["success_action"])
                  }
                >
                  <option value="release">Release in terminal state</option>
                  <option value="restore_baseline">Restore baseline</option>
                  <option value="apply_safe_state" disabled={!safeActionsConfigured(spec)}>
                    Apply configured safe state
                  </option>
                </select>
              </label>

              <div className="grid grid-cols-1 gap-2.5 border-t border-line pt-3">
                <label className="grid gap-[5px]">
                  <span>Save as device context</span>
                  <input
                    value={contextName}
                    onChange={(event) => setContextName(event.target.value)}
                  />
                </label>
                <p className={configNote}>
                  Saves a new context for this page. Other pages and submitted work keep their
                  selections.
                </p>
                <label className="grid gap-[5px]">
                  <span>Note</span>
                  <input
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                    placeholder="Why is this device changing?"
                  />
                </label>
              </div>

              <div className="flex min-h-[34px] items-center gap-2.5">
                <button
                  type="button"
                  className={secondaryButton}
                  disabled={!bindingValid || busy}
                  onClick={() => void testConnection()}
                >
                  {probePending ? (
                    <LoaderCircle className="animate-spin" size={15} />
                  ) : (
                    <PlugZap size={15} />
                  )}
                  {probePending ? "Testing connection" : "Test connection"}
                </button>
                {probe && <ProbeResult receipt={probe} />}
              </div>

              {error && (
                <div className={inlineError} role="alert">
                  {error}
                </div>
              )}
            </div>

            <footer className="flex justify-end gap-2 border-t border-line bg-panel-soft px-[18px] py-[13px]">
              <Dialog.Close className={secondaryButton} disabled={busy}>
                Cancel
              </Dialog.Close>
              <button
                type="button"
                className={primaryButton}
                disabled={!publishValid || !changed || busy}
                onClick={() => void publish()}
              >
                {publishPending ? (
                  <LoaderCircle className="animate-spin" size={15} />
                ) : (
                  <Save size={15} />
                )}
                Save device context
              </button>
            </footer>
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function TcpConnectionFields({
  connection,
  onChange,
}: {
  connection: TcpConnection;
  onChange: (connection: TcpConnection) => void;
}) {
  return (
    <div className="grid grid-cols-3 gap-2.5 max-[680px]:grid-cols-2 max-[460px]:grid-cols-1 [&_label]:grid [&_label]:gap-[5px]">
      <label>
        <span>Host</span>
        <input
          value={connection.host}
          onChange={(event) => onChange({ ...connection, host: event.target.value })}
        />
      </label>
      <NumberField
        label="Port"
        value={connection.port}
        minimum={1}
        maximum={65_535}
        integer
        onChange={(port) => onChange({ ...connection, port })}
      />
      <NumberField
        label="Timeout (seconds)"
        value={connection.timeout_seconds}
        minimum={0.001}
        onChange={(timeout_seconds) => onChange({ ...connection, timeout_seconds })}
      />
    </div>
  );
}

function SerialConnectionFields({
  connection,
  onChange,
}: {
  connection: SerialConnection;
  onChange: (connection: SerialConnection) => void;
}) {
  return (
    <fieldset className="m-0 min-w-0 rounded-sm border border-line p-2.5">
      <legend className="px-[5px] text-[0.53rem] font-extrabold tracking-[0.07em] text-text-dim uppercase">
        Serial transport
      </legend>
      <div className="grid grid-cols-3 gap-2.5 max-[680px]:grid-cols-2 max-[460px]:grid-cols-1 [&_label]:grid [&_label]:gap-[5px]">
        <label>
          <span>Port</span>
          <input
            value={connection.port}
            placeholder="/dev/ttyUSB0"
            onChange={(event) => onChange({ ...connection, port: event.target.value })}
          />
        </label>
        <NumberField
          label="Baud rate"
          value={connection.baud_rate}
          minimum={1}
          integer
          onChange={(baud_rate) => onChange({ ...connection, baud_rate })}
        />
        <label>
          <span>Data bits</span>
          <select
            value={connection.data_bits}
            onChange={(event) =>
              onChange({
                ...connection,
                data_bits: Number(event.target.value) as SerialConnection["data_bits"],
              })
            }
          >
            {[5, 6, 7, 8].map((dataBits) => (
              <option value={dataBits} key={dataBits}>
                {dataBits}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Parity</span>
          <select
            value={connection.parity}
            onChange={(event) =>
              onChange({
                ...connection,
                parity: event.target.value as SerialConnection["parity"],
              })
            }
          >
            {(["none", "even", "odd", "mark", "space"] as const).map((parity) => (
              <option value={parity} key={parity}>
                {titleCase(parity)}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Stop bits</span>
          <select
            value={connection.stop_bits}
            onChange={(event) => onChange({ ...connection, stop_bits: Number(event.target.value) })}
          >
            {[1, 1.5, 2].map((stopBits) => (
              <option value={stopBits} key={stopBits}>
                {stopBits}
              </option>
            ))}
          </select>
        </label>
        <NumberField
          label="Read timeout (seconds)"
          value={connection.timeout_seconds}
          minimum={0.001}
          onChange={(timeout_seconds) => onChange({ ...connection, timeout_seconds })}
        />
        <NumberField
          label="Write timeout (seconds)"
          value={connection.write_timeout_seconds}
          minimum={0.001}
          onChange={(write_timeout_seconds) => onChange({ ...connection, write_timeout_seconds })}
        />
        <label className="flex min-h-[34px] items-center gap-[7px]" data-toggle>
          <input
            className="min-h-[15px] w-[15px]"
            type="checkbox"
            checked={connection.rtscts}
            onChange={(event) => onChange({ ...connection, rtscts: event.target.checked })}
          />
          <span className="text-[0.62rem] font-normal tracking-normal text-text-soft normal-case">
            RTS/CTS flow control
          </span>
        </label>
        <label className="flex min-h-[34px] items-center gap-[7px]" data-toggle>
          <input
            className="min-h-[15px] w-[15px]"
            type="checkbox"
            checked={connection.xonxoff}
            onChange={(event) => onChange({ ...connection, xonxoff: event.target.checked })}
          />
          <span className="text-[0.62rem] font-normal tracking-normal text-text-soft normal-case">
            XON/XOFF flow control
          </span>
        </label>
        <label className="flex min-h-[34px] items-center gap-[7px]" data-toggle>
          <input
            className="min-h-[15px] w-[15px]"
            type="checkbox"
            checked={connection.dsrdtr}
            onChange={(event) => onChange({ ...connection, dsrdtr: event.target.checked })}
          />
          <span className="text-[0.62rem] font-normal tracking-normal text-text-soft normal-case">
            DSR/DTR flow control
          </span>
        </label>
      </div>
    </fieldset>
  );
}

function OptionInput({
  field,
  value,
  onChange,
  onValidityChange,
}: {
  field: OptionField;
  value: OptionValue | undefined;
  onChange: (value: OptionValue | undefined) => void;
  onValidityChange: (valid: boolean) => void;
}) {
  if (field.type === "boolean") {
    return (
      <label className="flex min-h-[34px] items-center gap-[7px]" data-toggle>
        <input
          className="min-h-[15px] w-[15px]"
          type="checkbox"
          checked={value === true}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="text-[0.62rem] font-normal tracking-normal text-text-soft normal-case">
          {field.label}
        </span>
      </label>
    );
  }
  if (field.type === "string") {
    return (
      <label>
        <span>{field.label}</span>
        <input
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
    );
  }
  if (field.type === "array" || field.type === "object") {
    return (
      <JsonOptionInput
        field={field}
        value={value}
        onChange={onChange}
        onValidityChange={onValidityChange}
      />
    );
  }
  return (
    <NumberField
      label={field.label}
      value={typeof value === "number" ? value : Number.NaN}
      minimum={field.minimum}
      maximum={field.maximum}
      integer={field.type === "integer"}
      onChange={onChange}
    />
  );
}

function JsonOptionInput({
  field,
  value,
  onChange,
  onValidityChange,
}: {
  field: OptionField;
  value: OptionValue | undefined;
  onChange: (value: OptionValue | undefined) => void;
  onValidityChange: (valid: boolean) => void;
}) {
  const [raw, setRaw] = useState(() => (value === undefined ? "" : JSON.stringify(value, null, 2)));
  const [valid, setValid] = useState(true);
  const edit = (nextRaw: string) => {
    setRaw(nextRaw);
    if (nextRaw.trim().length === 0 && !field.required) {
      setValid(true);
      onValidityChange(true);
      onChange(undefined);
      return;
    }
    try {
      const parsed: unknown = JSON.parse(nextRaw);
      const shapeValid =
        isOptionValue(parsed) &&
        (field.type === "array" ? Array.isArray(parsed) : isRecord(parsed));
      if (!shapeValid) throw new Error("Unexpected JSON shape");
      setValid(true);
      onValidityChange(true);
      onChange(parsed);
    } catch {
      setValid(false);
      onValidityChange(false);
    }
  };
  return (
    <label className="col-span-full grid gap-[5px]">
      <span>{field.label}</span>
      <textarea
        className="min-h-[76px] w-full resize-y rounded-sm border border-line bg-bg px-[9px] py-2 font-mono text-[0.59rem] text-text outline-0 focus:border-accent aria-invalid:border-red"
        value={raw}
        aria-invalid={!valid}
        placeholder={field.type === "array" ? "[]" : "{}"}
        onChange={(event) => edit(event.target.value)}
      />
      {!valid && (
        <small className="text-[0.52rem] text-red">Enter a valid JSON {field.type}.</small>
      )}
    </label>
  );
}

function NumberField({
  label,
  value,
  minimum,
  maximum,
  integer = false,
  onChange,
}: {
  label: string;
  value: number;
  minimum?: number;
  maximum?: number;
  integer?: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      <span>{label}</span>
      <input
        type="number"
        value={Number.isNaN(value) ? "" : value}
        min={minimum}
        max={maximum}
        step={integer ? 1 : "any"}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function ProbeResult({ receipt }: { receipt: InstrumentDriverProbeReceipt }) {
  if (receipt.status === "connected") {
    return (
      <span
        className="inline-flex items-center gap-[5px] text-[0.6rem] leading-[1.4] text-accent"
        role="status"
      >
        <Check size={14} />
        {receipt.description?.label
          ? `Connected to ${receipt.description.label}`
          : "Connection succeeded"}
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-[5px] text-[0.6rem] leading-[1.4] text-red"
      role="status"
    >
      {receipt.problems.map((problem) => problem.message).join(" ") || "Connection rejected"}
    </span>
  );
}

const configNote =
  "m-0 rounded-sm border border-[rgb(128_163_207_/_20%)] bg-accent-soft px-2.5 py-[9px] text-[0.58rem] leading-normal text-text-dim";
const inlineError =
  "flex items-center gap-2 rounded-md border border-[rgb(215_126_121_/_25%)] bg-red-soft px-3 py-2.5 text-[0.66rem] leading-normal text-[#edb5b2]";

function proposedSpec(
  instrumentId: string,
  driverId: string,
  connection: InstrumentConnection,
  defaultState: InstrumentStateSetting[],
  runStart: InstrumentSpec["run_start"],
  successAction: InstrumentSpec["success_action"],
  existing?: InstrumentSpec,
): InstrumentSpec {
  return {
    id: instrumentId,
    exclusivity_key: existing?.exclusivity_key ?? instrumentId,
    driver_id: driverId,
    connection,
    default_state: defaultState,
    run_start: runStart,
    success_action: successAction,
    failure_action: existing?.failure_action ?? "abort_and_release",
    ...(existing?.safe_state === undefined ? {} : { safe_state: existing.safe_state }),
    ...(existing?.safe_operations === undefined
      ? {}
      : { safe_operations: existing.safe_operations }),
    ...(existing?.safe_state_requirement === undefined
      ? {}
      : { safe_state_requirement: existing.safe_state_requirement }),
  };
}

function safeActionsConfigured(spec: InstrumentSpec): boolean {
  return (spec.safe_state?.length ?? 0) > 0 || (spec.safe_operations?.length ?? 0) > 0;
}

function initialConnection(
  existing: InstrumentConnection | undefined,
  driver: DriverSpec | undefined,
): InstrumentConnection {
  const supported = driver?.connections.find((candidate) => candidate.kind === existing?.kind);
  if (existing && supported) {
    const options = {
      ...defaultOptions(supported.options_schema),
      ...existing.options,
    };
    return {
      ...existing,
      ...(existing.options || Object.keys(options).length > 0 ? { options } : {}),
    };
  }
  const connectionSpec = driver?.connections[0];
  return newConnection(connectionSpec?.kind ?? "virtual", connectionSpec);
}

function newConnection(
  kind: ConnectionKind,
  connectionSpec?: DriverConnectionSpec,
): InstrumentConnection {
  const options = defaultOptions(connectionSpec?.options_schema);
  const configuredOptions = Object.keys(options).length > 0 ? { options } : {};
  if (kind === "virtual" || kind === "driver_managed") {
    return { kind, ...configuredOptions };
  }
  if (kind === "tcpip_socket") {
    return { kind, host: "", port: 5025, timeout_seconds: 5, ...configuredOptions };
  }
  return {
    kind,
    port: "",
    baud_rate: 9600,
    timeout_seconds: 1,
    write_timeout_seconds: 1,
    data_bits: 8,
    parity: "none",
    stop_bits: 1,
    xonxoff: false,
    rtscts: false,
    dsrdtr: false,
    ...configuredOptions,
  };
}

function connectionIsValid(connection: InstrumentConnection): boolean {
  if (connection.kind === "virtual" || connection.kind === "driver_managed") return true;
  if (connection.kind === "tcpip_socket") {
    return (
      connection.host.trim().length > 0 &&
      Number.isInteger(connection.port) &&
      connection.port >= 1 &&
      connection.port <= 65_535 &&
      Number.isFinite(connection.timeout_seconds) &&
      connection.timeout_seconds > 0
    );
  }
  return (
    connection.port.trim().length > 0 &&
    Number.isInteger(connection.baud_rate) &&
    connection.baud_rate > 0 &&
    Number.isFinite(connection.timeout_seconds) &&
    connection.timeout_seconds > 0 &&
    Number.isFinite(connection.write_timeout_seconds) &&
    connection.write_timeout_seconds > 0
  );
}

function optionsAreValid(options: ConnectionOptions, fields: OptionField[]): boolean {
  return fields.every((field) => {
    const value = options[field.id] ?? field.defaultValue;
    if (value === undefined) return !field.required;
    if (field.type === "boolean") return typeof value === "boolean";
    if (field.type === "string") return typeof value === "string";
    if (field.type === "array") return Array.isArray(value);
    if (field.type === "object") return isRecord(value);
    return (
      typeof value === "number" &&
      Number.isFinite(value) &&
      (field.type !== "integer" || Number.isInteger(value)) &&
      (field.minimum === undefined || value >= field.minimum) &&
      (field.maximum === undefined || value <= field.maximum)
    );
  });
}

// Driver options stay opaque; the editor projects their top-level JSON Schema fields.
function connectionOptionFields(schema: unknown): OptionField[] {
  if (!isRecord(schema) || !isRecord(schema.properties)) return [];
  const required = new Set(
    Array.isArray(schema.required)
      ? schema.required.filter((value): value is string => typeof value === "string")
      : [],
  );
  return Object.entries(schema.properties).flatMap(([id, value]) => {
    if (!isRecord(value)) return [];
    const type = value.type;
    if (
      type !== "array" &&
      type !== "boolean" &&
      type !== "integer" &&
      type !== "number" &&
      type !== "object" &&
      type !== "string"
    ) {
      return [];
    }
    return [
      {
        id,
        label: typeof value.title === "string" ? value.title : titleCase(id),
        type,
        defaultValue: jsonOptionValue(value.default),
        required: required.has(id),
        minimum: typeof value.minimum === "number" ? value.minimum : undefined,
        maximum: typeof value.maximum === "number" ? value.maximum : undefined,
      },
    ];
  });
}

function defaultOptions(schema: unknown): ConnectionOptions {
  return Object.fromEntries(
    connectionOptionFields(schema).flatMap((field) =>
      field.defaultValue !== undefined
        ? [[field.id, field.defaultValue]]
        : field.required && field.type === "array"
          ? [[field.id, []]]
          : field.required && field.type === "object"
            ? [[field.id, {}]]
            : [],
    ),
  );
}

function jsonOptionValue(value: unknown): OptionValue | undefined {
  return isOptionValue(value) ? value : undefined;
}

function driverLabel(driver: DriverSpec): string {
  return driver.manufacturer && driver.model
    ? `${driver.manufacturer} ${driver.model}`
    : driver.label;
}

function connectionKindLabel(kind: ConnectionKind): string {
  if (kind === "virtual") return "Virtual simulator";
  if (kind === "tcpip_socket") return "TCP/IP socket";
  if (kind === "serial") return "Serial port";
  return "Driver managed";
}

function titleCase(value: string): string {
  return value
    .split("_")
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isOptionValue(value: unknown): value is OptionValue {
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "number" ||
    typeof value === "string"
  ) {
    return true;
  }
  if (Array.isArray(value)) return value.every(isOptionValue);
  return isRecord(value) && Object.values(value).every(isOptionValue);
}

function MissingInstrumentDialog({
  instrumentId,
  onCancel,
}: {
  instrumentId: string;
  onCancel: () => void;
}) {
  return (
    <Dialog.Root open onOpenChange={(open) => !open && onCancel()}>
      <Dialog.Portal>
        <Dialog.Backdrop className={dialogBackdrop} />
        <Dialog.Viewport className={dialogViewport}>
          <Dialog.Popup className={classes(dialogPopup, "w-[min(440px,100%)]")}>
            <div className="grid grid-cols-[34px_minmax(0,1fr)] items-start gap-[11px] p-[18px]">
              <span
                className="grid size-8 place-items-center rounded-md bg-accent-soft text-accent"
                aria-hidden="true"
              >
                <Cable size={18} />
              </span>
              <div>
                <Dialog.Title className={dialogTitle}>Instrument changed</Dialog.Title>
                <Dialog.Description className={dialogDescription}>
                  {instrumentId} is no longer present in the selected device context. Refresh before
                  editing.
                </Dialog.Description>
              </div>
            </div>
            <div className="flex justify-end gap-2 border-t border-line bg-panel-soft px-[18px] py-3">
              <Dialog.Close className={secondaryButton}>Close</Dialog.Close>
            </div>
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
