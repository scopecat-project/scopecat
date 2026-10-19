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
  device,
  catalog,
  description,
  onCancel,
  onPublished,
}: {
  device?: DeviceView;
  catalog: DriverCatalog;
  description?: InstrumentDescription;
  onCancel: () => void;
  onPublished: (saved: DeviceView) => void | Promise<void>;
}) {
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
        actor: "local-operator",
        note,
        connection: content,
      };
      const key = JSON.stringify(input);
      if (attempt.current?.key !== key) {
        attempt.current = {
          key,
          command: { ...input, revision_id: createInstrumentCommandId("connection") },
        };
      }
      connectionSaveStarted.current = true;
      saved = attempt.current.saved ?? (await saveDevice(attempt.current.command));
      attempt.current.saved = saved;
      await onPublished(saved);
    } catch (cause) {
      if (saved) {
        setError(`Device saved, but the view could not refresh. Try again. ${errorMessage(cause)}`);
      } else if (cause instanceof ApiError && cause.status === 409) {
        setError(
          `Save conflict. Close and reopen the device to review its current configuration. ${errorMessage(cause)}`,
        );
      } else if (cause instanceof ApiError && (cause.status === undefined || cause.status >= 500)) {
        setError(
          `Save not confirmed. Retry without editing to check the same submission. Editing starts a new submission and may require reviewing the current configuration. ${errorMessage(cause)}`,
        );
      } else {
        setError(errorMessage(cause));
      }
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };
  return (
    <Dialog.Root open onOpenChange={(open) => !open && !busy && onCancel()}>
      <Dialog.Portal>
        <Dialog.Backdrop className={dialogBackdrop} />
        <Dialog.Viewport className={dialogViewport}>
          <Dialog.Popup className={`${dialogPopup} w-[min(680px,100%)]`}>
            <header className="border-b border-line p-4">
              <Dialog.Title className={dialogTitle}>
                {device ? "Edit device" : "Add device"}
              </Dialog.Title>
              <Dialog.Description className={dialogDescription}>
                Save connection settings, then use Test connection to check the device.
              </Dialog.Description>
            </header>
            <fieldset
              disabled={busy}
              onChange={() => {
                attempt.current = null;
              }}
              className="grid gap-4 p-4"
            >
              <Controls.Field>
                Device name
                <Controls.Input value={label} onChange={(event) => setLabel(event.target.value)} />
              </Controls.Field>
              <Controls.Field>
                Driver
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
                  Connection
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
                  <legend>Driver options</legend>
                  {fields.map((field) => (
                    <OptionInput
                      key={`${driverId}:${connection.kind}:${field.id}`}
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
                <summary>Physical identity and other addresses</summary>
                <p>
                  Declare other access paths that name this same device, one per line. An address
                  already registered to another device must be resolved before access.
                </p>
                <Controls.Field>
                  Access aliases
                  <Controls.Textarea
                    value={aliases}
                    onChange={(event) => setAliases(event.target.value)}
                  />
                </Controls.Field>
              </details>
              <details>
                <summary>Device safety</summary>
                <InstrumentDefaultsEditor
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
                  Require safe state after successful work
                </Controls.Field>
                <Controls.Field>
                  <input
                    type="checkbox"
                    checked={safety.require_safe_failure ?? false}
                    onChange={(event) =>
                      setSafety({ ...safety, require_safe_failure: event.target.checked })
                    }
                  />
                  Require safe state after failure
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
                  Keep device blocked if safe state cannot be confirmed
                </Controls.Field>
              </details>
              {device && (
                <Controls.Field>
                  Reason for change
                  <Controls.Input value={note} onChange={(event) => setNote(event.target.value)} />
                </Controls.Field>
              )}
              {(error || drivers.error) && (
                <p role="alert">{error ?? errorMessage(drivers.error)}</p>
              )}
              <p className="text-sm text-text-dim">
                After saving, use Test connection in the device detail. Stop affected work before
                changing a connection.
              </p>
            </fieldset>
            <footer className="flex justify-end gap-2 border-t border-line p-4">
              <Dialog.Close className={secondaryButton} disabled={busy}>
                Cancel
              </Dialog.Close>
              <button
                className={primaryButton}
                disabled={!valid || busy}
                onClick={() => void save()}
              >
                {busy ? "Saving…" : "Save device"}
              </button>
            </footer>
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
