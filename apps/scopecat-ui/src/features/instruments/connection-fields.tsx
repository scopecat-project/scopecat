import { useState } from "react";
import type { DriverConnectionSpec, DriverSpec, InstrumentConnection } from "../../api-contract";
export type ConnectionKind = InstrumentConnection["kind"];
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

export function TcpConnectionFields({
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

export function SerialConnectionFields({
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

export function OptionInput({
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

export function initialConnection(
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

export function newConnection(
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

export function connectionIsValid(connection: InstrumentConnection): boolean {
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

export function optionsAreValid(options: ConnectionOptions, fields: OptionField[]): boolean {
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
export function connectionOptionFields(schema: unknown): OptionField[] {
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

export function jsonOptionValue(value: unknown): OptionValue | undefined {
  return isOptionValue(value) ? value : undefined;
}

export function driverLabel(driver: DriverSpec): string {
  return driver.manufacturer && driver.model
    ? `${driver.manufacturer} ${driver.model}`
    : driver.label;
}

export function connectionKindLabel(kind: ConnectionKind): string {
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
