import { readOperatorSettings } from "../application/OperatorSettings";
import * as Controls from "../../ui/Controls";
import { useRef, useState } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { useQuery } from "@tanstack/react-query";
import type {
  DriverCatalog,
  InstrumentDescription,
  InstrumentStateSetting,
} from "../../api-contract";
import { ApiError } from "../../api-client";
import { errorMessage } from "../../lib/presentation";
import {
  dialogBackdrop,
  dialogPopup,
  dialogTitle,
  dialogDescription,
  dialogViewport,
  primaryButton,
  secondaryButton,
} from "../../ui/styles";
import { InstrumentDefaultsEditor } from "./InstrumentDefaultsEditor";
import { createInstrumentCommandId } from "./instrument-api";
import {
  getDeviceDrivers,
  saveDevice,
  renameDevice,
  type DeviceView,
  type DeviceSaveCommand,
  type DeviceConnection,
} from "./device-api";
import {
  TcpConnectionFields,
  SerialConnectionFields,
  OptionInput,
  initialConnection,
  newConnection,
  connectionIsValid,
  optionsAreValid,
  connectionOptionFields,
  jsonOptionValue,
  driverLabel,
  connectionKindLabel,
  type ConnectionKind,
} from "./connection-fields";

export function InstrumentConfigDialog({
  device: suppliedDevice,
  catalog,
  description,
  onCancel,
  onPublished,
  embedded = false,
}: {
  embedded?: boolean;
  device?: DeviceView;
  catalog: DriverCatalog;
  description?: InstrumentDescription;
  onCancel: () => void;
  onPublished: (saved: DeviceView) => void | Promise<void>;
}) {
  const [device] = useState(suppliedDevice);
  const [draftGeneration, setDraftGeneration] = useState(0);
  const existing = device?.revision.content;
  const first =
    catalog.drivers.find((item) => item.driver_id === existing?.driver.driver_id) ??
    catalog.drivers[0];
  const [deviceId] = useState(() => device?.device.id ?? createInstrumentCommandId("device"));
  const [label, setLabel] = useState(device?.device.label ?? "");
  const [driverId, setDriverId] = useState(first?.driver_id ?? "");
  const [connection, setConnection] = useState(() =>
    initialConnection(existing?.connection, first),
  );
  const [aliases, setAliases] = useState(existing?.access_aliases?.join("\n") ?? "");
  const [safeState, setSafeState] = useState<InstrumentStateSetting[]>(
    existing?.safety?.safe_state ?? [],
  );
  const [safety, setSafety] = useState<NonNullable<DeviceConnection["safety"]>>(
    existing?.safety ?? {
      safe_state: [],
      safe_operations: [],
      safe_state_requirement: "best_effort",
      require_safe_success: false,
      require_safe_failure: false,
    },
  );
  const [validState, setValidState] = useState(true);
  const [invalidOptions, setInvalidOptions] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");
  const [restored, setRestored] = useState(false);
  const [busy, setBusy] = useState(false);
  const saving = useRef(false);
  const connectionSaveStarted = useRef(false);
  const attempt = useRef<{ key: string; command: DeviceSaveCommand; saved?: DeviceView } | null>(
    null,
  );
  const [error, setError] = useState<string>();
  const drivers = useQuery({
    queryKey: ["device-implementations"],
    queryFn: ({ signal }) => getDeviceDrivers(signal),
  });
  const driver = catalog.drivers.find((item) => item.driver_id === driverId);
  const implementation = drivers.data?.items.find((item) => item.driver_id === driverId);
  const fields = connectionOptionFields(
    driver?.connections.find((item) => item.kind === connection.kind)?.options_schema,
  );
  const options = connection.options ?? {};
  const hasSafety = safeState.length > 0 || (safety.safe_operations?.length ?? 0) > 0;
  const valid =
    label.trim().length > 0 &&
    implementation &&
    connectionIsValid(connection) &&
    optionsAreValid(options, fields) &&
    invalidOptions.size === 0 &&
    validState &&
    (connection.kind !== "driver_managed" || aliases.trim().length > 0) &&
    (!(
      safety.require_safe_success ||
      safety.require_safe_failure ||
      safety.safe_state_requirement === "required"
    ) ||
      hasSafety);
  const proposed = {
    driver: implementation,
    connection,
    access_aliases: aliases
      .split("\n")
      .map((value) => value.trim())
      .filter(Boolean),
    safety: { ...safety, safe_state: safeState },
  };
  const changes = existing && implementation ? connectionChanges(existing, proposed, catalog) : [];
  const previous = device?.previous_revision;
  const canRestore =
    previous && JSON.stringify(previous.content.driver) === JSON.stringify(existing?.driver);
  const restorePrevious = () => {
    if (!previous || !canRestore) return;
    setDriverId(previous.content.driver.driver_id);
    setConnection(previous.content.connection);
    setAliases(previous.content.access_aliases.join("\n"));
    setSafety(previous.content.safety ?? safety);
    setSafeState(previous.content.safety?.safe_state ?? []);
    setInvalidOptions(new Set());
    setNote(`使用先前连接设置：${previous.id}`);
    setRestored(true);
    setDraftGeneration((value) => value + 1);
    setValidState(true);
    attempt.current = null;
    setError(undefined);
  };
  const save = async () => {
    if (!valid || !implementation || saving.current) return;
    saving.current = true;
    let saved: DeviceView | undefined;
    setBusy(true);
    setError(undefined);
    try {
      const content = {
        driver: implementation,
        connection,
        access_aliases: aliases
          .split("\n")
          .map((value) => value.trim())
          .filter(Boolean),
        safety: { ...safety, safe_state: safeState },
      };
      if (
        !restored &&
        !connectionSaveStarted.current &&
        device &&
        existing &&
        JSON.stringify(content.driver) === JSON.stringify(existing.driver) &&
        JSON.stringify(content.connection) === JSON.stringify(existing.connection) &&
        JSON.stringify(content.access_aliases) === JSON.stringify(existing.access_aliases) &&
        JSON.stringify(content.safety) === JSON.stringify(existing.safety)
      ) {
        saved = await renameDevice(device, label.trim());
        await onPublished(saved);
        return;
      }
      const input = {
        device_id: deviceId,
        label: label.trim(),
        expected_head: device?.device.head ?? null,
        note,
        connection: content,
      };
      const key = JSON.stringify(input);
      if (attempt.current?.key !== key) {
        const actor = (await readOperatorSettings()).operator;
        if (!actor.trim()) throw new Error("请先在应用设置中保存操作者名称，再保存设备。");
        attempt.current = {
          key,
          command: {
            ...input,
            actor,
            revision_id: createInstrumentCommandId("connection"),
          },
        };
      }
      connectionSaveStarted.current = true;
      saved = attempt.current.saved ?? (await saveDevice(attempt.current.command));
      attempt.current.saved = saved;
      await onPublished(saved);
    } catch (cause) {
      if (saved) {
        setError(`设备已保存，但视图无法刷新。请重试。 ${errorMessage(cause)}`);
      } else if (cause instanceof ApiError && cause.status === 409) {
        setError(`保存冲突。请关闭并重新打开设备，核对当前配置。 ${errorMessage(cause)}`);
      } else if (cause instanceof ApiError && (cause.status === undefined || cause.status >= 500)) {
        setError(
          `保存尚未确认。不作修改直接重试可检查同一次提交。编辑会开始新提交，可能需要核对当前配置。 ${errorMessage(cause)}`,
        );
      } else {
        setError(errorMessage(cause));
      }
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };
  const content = (
    <>
      <header className="shrink-0 border-b border-line p-4">
        {embedded ? (
          <h3 className={dialogTitle}>编辑设备</h3>
        ) : (
          <Dialog.Title className={dialogTitle}>{device ? "编辑设备" : "添加设备"}</Dialog.Title>
        )}
        <p className={dialogDescription}>
          编辑中的值是草稿。保存后再显式测试该连接版本，不会自动应用仪器属性。
        </p>
      </header>
      <div className="min-h-0 overflow-y-auto">
        <fieldset
          disabled={busy}
          onChange={() => {
            attempt.current = null;
          }}
          className="grid gap-4 p-4"
        >
          {previous && (
            <div className="grid gap-2">
              <button
                type="button"
                className={secondaryButton}
                disabled={!canRestore}
                onClick={restorePrevious}
              >
                使用上一连接版本生成草稿
              </button>
              {!canRestore && <p>上一版本的驱动实现不同，不能在此恢复；请先核对驱动版本。</p>}
              {restored && (
                <p role="status">已载入先前设置，尚未保存。提交将创建新版本，不恢复硬件状态。</p>
              )}
            </div>
          )}
          <Controls.Field>
            设备名称
            <Controls.Input value={label} onChange={(event) => setLabel(event.target.value)} />
          </Controls.Field>
          <Controls.Field>
            驱动
            <Controls.Select
              value={driverId}
              onChange={(event) => {
                setDriverId(event.target.value);
                setConnection(
                  initialConnection(
                    undefined,
                    catalog.drivers.find((item) => item.driver_id === event.target.value),
                  ),
                );
                setInvalidOptions(new Set());
              }}
            >
              {catalog.drivers.map((item) => (
                <option key={item.driver_id} value={item.driver_id}>
                  {driverLabel(item)}
                </option>
              ))}
            </Controls.Select>
          </Controls.Field>
          {driver && (
            <Controls.Field>
              连接
              <Controls.Select
                value={connection.kind}
                onChange={(event) => {
                  const kind = event.target.value as ConnectionKind;
                  setConnection(
                    newConnection(
                      kind,
                      driver.connections.find((item) => item.kind === kind),
                    ),
                  );
                  setInvalidOptions(new Set());
                }}
              >
                {driver.connections.map((item) => (
                  <option key={item.kind} value={item.kind}>
                    {connectionKindLabel(item.kind)}
                  </option>
                ))}
              </Controls.Select>
            </Controls.Field>
          )}
          {connection.kind === "tcpip_socket" && (
            <TcpConnectionFields connection={connection} onChange={setConnection} />
          )}
          {connection.kind === "serial" && (
            <SerialConnectionFields connection={connection} onChange={setConnection} />
          )}
          {fields.length > 0 && (
            <fieldset>
              <legend>驱动选项</legend>
              {fields.map((field) => (
                <OptionInput
                  key={`${draftGeneration}:${driverId}:${connection.kind}:${field.id}`}
                  field={field}
                  value={jsonOptionValue(options[field.id]) ?? field.defaultValue}
                  onChange={(value) => {
                    const next = { ...options };
                    if (value === undefined) delete next[field.id];
                    else next[field.id] = value;
                    setConnection({ ...connection, options: next });
                  }}
                  onValidityChange={(fieldValid) =>
                    setInvalidOptions((current) => {
                      const next = new Set(current);
                      if (fieldValid) next.delete(field.id);
                      else next.add(field.id);
                      return next;
                    })
                  }
                />
              ))}
            </fieldset>
          )}
          <details open={connection.kind === "driver_managed"}>
            <summary>物理标识与其他地址</summary>
            <p>
              声明指向同一设备的其他访问路径，每行一项。如地址已登记到其他设备，访问前必须先解决冲突。
            </p>
            <Controls.Field>
              访问别名
              <Controls.Textarea
                value={aliases}
                onChange={(event) => setAliases(event.target.value)}
              />
            </Controls.Field>
          </details>
          <details>
            <summary>设备安全</summary>
            <InstrumentDefaultsEditor
              key={draftGeneration}
              mode="safety"
              description={existing?.driver.driver_id === driverId ? description : undefined}
              defaultState={safeState}
              runStart="preserve"
              onDefaultStateChange={setSafeState}
              onRunStartChange={() => {}}
              onValidityChange={setValidState}
            />
            <Controls.Field>
              <input
                type="checkbox"
                checked={safety.require_safe_success ?? false}
                onChange={(event) =>
                  setSafety({ ...safety, require_safe_success: event.target.checked })
                }
              />
              任务成功后要求安全状态
            </Controls.Field>
            <Controls.Field>
              <input
                type="checkbox"
                checked={safety.require_safe_failure ?? false}
                onChange={(event) =>
                  setSafety({ ...safety, require_safe_failure: event.target.checked })
                }
              />
              失败后要求安全状态
            </Controls.Field>
            <Controls.Field>
              <input
                type="checkbox"
                checked={safety.safe_state_requirement === "required"}
                onChange={(event) =>
                  setSafety({
                    ...safety,
                    safe_state_requirement: event.target.checked ? "required" : "best_effort",
                  })
                }
              />
              无法确认安全状态时保持设备阻止状态
            </Controls.Field>
          </details>
          {device && (
            <Controls.Field>
              修改原因
              <Controls.Input value={note} onChange={(event) => setNote(event.target.value)} />
            </Controls.Field>
          )}
          {changes.length > 0 && (
            <section
              aria-label="连接修改预览"
              className="grid gap-2 rounded border border-line p-3"
            >
              <strong>保存后的变化</strong>
              <dl className="grid gap-2 break-all">
                {changes.map((change) => (
                  <div key={change.name}>
                    <dt>{change.name}</dt>
                    <dd>
                      {change.before} → {change.after}
                    </dd>
                  </div>
                ))}
              </dl>
              <p>
                保存会关闭此设备的旧空闲连接，并执行驱动规定的断开动作。保存后请测试新连接，相关实验需重新检查准备。
              </p>
            </section>
          )}
          {(error || drivers.error) && <p role="alert">{error ?? errorMessage(drivers.error)}</p>}
        </fieldset>
      </div>
      <footer className="flex shrink-0 justify-end gap-2 border-t border-line p-4">
        <button type="button" onClick={onCancel} className={secondaryButton} disabled={busy}>
          取消
        </button>
        <button className={primaryButton} disabled={!valid || busy} onClick={() => void save()}>
          {busy ? "正在保存…" : "保存设备"}
        </button>
      </footer>
    </>
  );
  if (embedded)
    return (
      <section aria-label="编辑设备" className="min-w-0 rounded-lg border border-line bg-panel">
        {content}
      </section>
    );
  return (
    <Dialog.Root open onOpenChange={(open) => !open && !busy && onCancel()}>
      <Dialog.Portal>
        <Dialog.Backdrop className={dialogBackdrop} />
        <Dialog.Viewport className={dialogViewport}>
          <Dialog.Popup
            className={`${dialogPopup} flex max-h-[calc(100dvh-40px)] w-[min(680px,100%)] flex-col max-[460px]:max-h-[calc(100dvh-20px)]`}
          >
            {content}
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function connectionChanges(
  before: DeviceConnection,
  after: {
    driver: DeviceConnection["driver"] | undefined;
    connection: DeviceConnection["connection"];
    access_aliases: string[];
    safety: DeviceConnection["safety"];
  },
  catalog: DriverCatalog,
) {
  const changes: { name: string; before: string; after: string }[] = [];
  const add = (name: string, a: unknown, b: unknown, display = displayChangeValue) => {
    if (JSON.stringify(a) === JSON.stringify(b)) return;
    const oldValue = display(a);
    const newValue = display(b);
    changes.push({
      name,
      before: oldValue,
      after: oldValue === newValue ? `${newValue}（内容已修改）` : newValue,
    });
  };
  const driverName = (value: unknown) => {
    const ref = value as DeviceConnection["driver"] | undefined;
    const spec = catalog.drivers.find((item) => item.driver_id === ref?.driver_id);
    return spec ? driverLabel(spec) : "未提供名称的驱动";
  };
  add("驱动", before.driver, after.driver, driverName);
  const labels: Record<string, string> = {
    kind: "连接方式",
    host: "主机",
    port: "端口",
    timeout_seconds: "读取超时（秒）",
    write_timeout_seconds: "写入超时（秒）",
    baud_rate: "波特率",
    data_bits: "数据位",
    stop_bits: "停止位",
    parity: "校验位",
    xonxoff: "XON/XOFF 流控",
    rtscts: "RTS/CTS 流控",
    dsrdtr: "DSR/DTR 流控",
  };
  for (const key of new Set([
    ...Object.keys(before.connection),
    ...Object.keys(after.connection),
  ])) {
    if (key === "options") continue;
    const a = (before.connection as unknown as Record<string, unknown>)[key];
    const b = (after.connection as unknown as Record<string, unknown>)[key];
    add(
      labels[key] ?? `连接选项 ${key}`,
      a,
      b,
      key === "kind" ? (value) => connectionKindLabel(value as ConnectionKind) : displayChangeValue,
    );
  }
  const optionLabels = new Map<string, string>();
  for (const config of [before, after]) {
    const spec = catalog.drivers.find((item) => item.driver_id === config.driver?.driver_id);
    for (const field of connectionOptionFields(
      spec?.connections.find((item) => item.kind === config.connection.kind)?.options_schema,
    )) {
      optionLabels.set(field.id, field.label);
    }
  }
  const oldOptions = before.connection.options ?? {};
  const newOptions = after.connection.options ?? {};
  for (const key of new Set([...Object.keys(oldOptions), ...Object.keys(newOptions)])) {
    add(`驱动选项 · ${optionLabels.get(key) ?? key}`, oldOptions[key], newOptions[key]);
  }
  add(
    "访问别名",
    before.access_aliases,
    after.access_aliases,
    (value) => (value as string[]).join("、") || "未设置",
  );
  const safetyLabels: Record<string, string> = {
    safe_state: "设备安全 · 目标状态",
    safe_operations: "设备安全 · 收尾操作",
    safe_state_requirement: "设备安全 · 状态确认要求",
    require_safe_success: "设备安全 · 任务成功后要求安全状态",
    require_safe_failure: "设备安全 · 失败后要求安全状态",
  };
  for (const key of new Set([
    ...Object.keys(before.safety ?? {}),
    ...Object.keys(after.safety ?? {}),
  ])) {
    add(
      safetyLabels[key] ?? `设备安全 · ${key}`,
      (before.safety as Record<string, unknown> | undefined)?.[key],
      (after.safety as Record<string, unknown> | undefined)?.[key],
      key === "safe_state_requirement"
        ? (value) =>
            value === "required"
              ? "必须确认"
              : value === "best_effort"
                ? "尽力确认"
                : displayChangeValue(value)
        : displayChangeValue,
    );
  }
  return changes;
}

function displayChangeValue(value: unknown): string {
  if (value === undefined || value === null) return "未设置";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value || "空文本";
  if (Array.isArray(value)) return `列表（${value.length} 项；请在对应设置中查看内容）`;
  return "复杂设置（请在对应设置中查看内容）";
}
