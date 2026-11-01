import * as Controls from "../../ui/Controls";
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
  description?: string;
  choices?: string[];
  stringMap?: boolean;
  minLength?: number;
  minProperties?: number;
}

export function TcpConnectionFields({
  connection,
  onChange,
}: {
  connection: TcpConnection;
  onChange: (connection: TcpConnection) => void;
}) {
  return (
    <div className="grid grid-cols-3 gap-2.5 max-[680px]:grid-cols-2 max-[460px]:grid-cols-1">
      <Controls.Field>
        <span>主机</span>
        <Controls.Input
          value={connection.host}
          onChange={(event) => onChange({ ...connection, host: event.target.value })}
        />
      </Controls.Field>
      <NumberField
        label="端口"
        value={connection.port}
        minimum={1}
        maximum={65_535}
        integer
        onChange={(port) => onChange({ ...connection, port })}
      />
      <NumberField
        label="超时（秒）"
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
      <legend className="px-[5px] text-xs font-extrabold tracking-[0.07em] text-text-dim uppercase">
        串口传输
      </legend>
      <div className="grid grid-cols-3 gap-2.5 max-[680px]:grid-cols-2 max-[460px]:grid-cols-1">
        <Controls.Field>
          <span>端口</span>
          <Controls.Input
            value={connection.port}
            placeholder="/dev/ttyUSB0"
            onChange={(event) => onChange({ ...connection, port: event.target.value })}
          />
        </Controls.Field>
        <NumberField
          label="波特率"
          value={connection.baud_rate}
          minimum={1}
          integer
          onChange={(baud_rate) => onChange({ ...connection, baud_rate })}
        />
        <Controls.Field>
          <span>数据位</span>
          <Controls.Select
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
          </Controls.Select>
        </Controls.Field>
        <Controls.Field>
          <span>校验位</span>
          <Controls.Select
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
          </Controls.Select>
        </Controls.Field>
        <Controls.Field>
          <span>停止位</span>
          <Controls.Select
            value={connection.stop_bits}
            onChange={(event) => onChange({ ...connection, stop_bits: Number(event.target.value) })}
          >
            {[1, 1.5, 2].map((stopBits) => (
              <option value={stopBits} key={stopBits}>
                {stopBits}
              </option>
            ))}
          </Controls.Select>
        </Controls.Field>
        <NumberField
          label="读取超时（秒）"
          value={connection.timeout_seconds}
          minimum={0.001}
          onChange={(timeout_seconds) => onChange({ ...connection, timeout_seconds })}
        />
        <NumberField
          label="写入超时（秒）"
          value={connection.write_timeout_seconds}
          minimum={0.001}
          onChange={(write_timeout_seconds) => onChange({ ...connection, write_timeout_seconds })}
        />
        <Controls.Field className="flex min-h-[34px] items-center gap-[7px]" data-toggle>
          <input
            className="min-h-[15px] w-[15px]"
            type="checkbox"
            checked={connection.rtscts}
            onChange={(event) => onChange({ ...connection, rtscts: event.target.checked })}
          />
          <span className="text-xs font-normal tracking-normal text-text-soft normal-case">
            RTS/CTS 流控
          </span>
        </Controls.Field>
        <Controls.Field className="flex min-h-[34px] items-center gap-[7px]" data-toggle>
          <input
            className="min-h-[15px] w-[15px]"
            type="checkbox"
            checked={connection.xonxoff}
            onChange={(event) => onChange({ ...connection, xonxoff: event.target.checked })}
          />
          <span className="text-xs font-normal tracking-normal text-text-soft normal-case">
            XON/XOFF 流控
          </span>
        </Controls.Field>
        <Controls.Field className="flex min-h-[34px] items-center gap-[7px]" data-toggle>
          <input
            className="min-h-[15px] w-[15px]"
            type="checkbox"
            checked={connection.dsrdtr}
            onChange={(event) => onChange({ ...connection, dsrdtr: event.target.checked })}
          />
          <span className="text-xs font-normal tracking-normal text-text-soft normal-case">
            DSR/DTR 流控
          </span>
        </Controls.Field>
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
      <Controls.Field className="flex min-h-[34px] items-center gap-[7px]" data-toggle>
        <input
          className="min-h-[15px] w-[15px]"
          type="checkbox"
          checked={value === true}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="text-xs font-normal tracking-normal text-text-soft normal-case">
          {field.label}
        </span>
      </Controls.Field>
    );
  }
  if (field.type === "object" && field.stringMap) {
    return (
      <StringMapInput
        field={field}
        value={value}
        onChange={onChange}
        onValidityChange={onValidityChange}
      />
    );
  }
  if (field.type === "string") {
    return (
      <Controls.Field>
        <span>{field.label}</span>
        {field.choices ? (
          <Controls.Select
            value={typeof value === "string" ? value : ""}
            onChange={(event) => onChange(event.target.value || undefined)}
          >
            <option value="">请选择</option>
            {field.choices.map((choice) => (
              <option key={choice} value={choice}>
                {choice}
              </option>
            ))}
          </Controls.Select>
        ) : (
          <Controls.Input
            value={typeof value === "string" ? value : ""}
            onChange={(event) => onChange(event.target.value || (field.required ? "" : undefined))}
          />
        )}
        {field.description && <small className="text-text-dim">{field.description}</small>}
      </Controls.Field>
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

function StringMapInput({
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
  const [rows, setRows] = useState(() =>
    Object.entries(isRecord(value) ? value : {}).map(([name, entry], id) => ({
      id,
      name,
      value: typeof entry === "string" ? entry : "",
    })),
  );
  const [nextId, setNextId] = useState(rows.length);
  const [error, setError] = useState("");
  const update = (next: typeof rows) => {
    setRows(next);
    const names = next.map((row) => row.name.trim());
    const valid =
      names.every(Boolean) &&
      new Set(names).size === names.length &&
      next.every((row) => row.value.trim()) &&
      next.length >= (field.minProperties ?? 0);
    setError(valid ? "" : "请填写名称和值，名称不能重复。");
    onValidityChange(valid);
    if (valid) onChange(Object.fromEntries(next.map((row) => [row.name.trim(), row.value.trim()])));
  };
  return (
    <fieldset className="grid min-w-0 gap-2" aria-label={field.label}>
      <legend>{field.label}</legend>
      {field.description && <small className="text-text-dim">{field.description}</small>}
      {rows.map((row, index) => (
        <div
          key={row.id}
          className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)_auto] items-end gap-2 max-[460px]:grid-cols-1"
        >
          <Controls.Field>
            名称
            <Controls.Input
              aria-label={`${field.label} 名称 ${index + 1}`}
              value={row.name}
              onChange={(event) =>
                update(
                  rows.map((item) =>
                    item.id === row.id ? { ...item, name: event.target.value } : item,
                  ),
                )
              }
            />
          </Controls.Field>
          <Controls.Field>
            值
            <Controls.Input
              aria-label={`${field.label} 值 ${index + 1}`}
              value={row.value}
              onChange={(event) =>
                update(
                  rows.map((item) =>
                    item.id === row.id ? { ...item, value: event.target.value } : item,
                  ),
                )
              }
            />
          </Controls.Field>
          <Controls.Button
            type="button"
            onClick={() => update(rows.filter((item) => item.id !== row.id))}
            aria-label={`移除${field.label}第 ${index + 1} 行`}
          >
            移除
          </Controls.Button>
        </div>
      ))}
      <Controls.Button
        type="button"
        onClick={() => {
          update([...rows, { id: nextId, name: "", value: "" }]);
          setNextId(nextId + 1);
        }}
      >
        添加{field.label}
      </Controls.Button>
      {error && <p role="alert">{error}</p>}
    </fieldset>
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
      if (!shapeValid) throw new Error("JSON 结构不符合预期");
      setValid(true);
      onValidityChange(true);
      onChange(parsed);
    } catch {
      setValid(false);
      onValidityChange(false);
    }
  };
  return (
    <Controls.Field className="col-span-full grid gap-[5px]">
      <span>{field.label}</span>
      <Controls.Textarea
        className="min-h-[76px] w-full resize-y font-mono aria-invalid:border-red"
        value={raw}
        aria-invalid={!valid}
        placeholder={field.type === "array" ? "[]" : "{}"}
        onChange={(event) => edit(event.target.value)}
      />
      {!valid && <small className="text-xs text-red">请输入有效 JSON {field.type}.</small>}
    </Controls.Field>
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
    <Controls.Field>
      <span>{label}</span>
      <Controls.Input
        type="number"
        value={Number.isNaN(value) ? "" : value}
        min={minimum}
        max={maximum}
        step={integer ? 1 : "any"}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </Controls.Field>
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
    if (value === undefined || value === null) return !field.required;
    if (field.type === "boolean") return typeof value === "boolean";
    if (field.type === "string")
      return (
        typeof value === "string" &&
        value.length >= (field.minLength ?? 0) &&
        (!field.choices || field.choices.includes(value))
      );
    if (field.type === "array") return Array.isArray(value);
    if (field.type === "object")
      return isRecord(value) && Object.keys(value).length >= (field.minProperties ?? 0);
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
    const shape = Array.isArray(value.anyOf)
      ? value.anyOf.find((item) => isRecord(item) && item.type !== "null")
      : value;
    if (!isRecord(shape)) return [];
    const type = shape.type;
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
        description: typeof value.description === "string" ? value.description : undefined,
        choices:
          Array.isArray(shape.enum) && shape.enum.every((item) => typeof item === "string")
            ? shape.enum
            : undefined,
        stringMap:
          type === "object" &&
          isRecord(shape.additionalProperties) &&
          shape.additionalProperties.type === "string",
        minLength: typeof shape.minLength === "number" ? shape.minLength : undefined,
        minProperties: typeof shape.minProperties === "number" ? shape.minProperties : undefined,
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
  if (kind === "virtual") return "虚拟模拟器";
  if (kind === "tcpip_socket") return "TCP/IP socket";
  if (kind === "serial") return "串口";
  return "由驱动管理";
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
