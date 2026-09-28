import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "../../api-schema";
import { errorMessage } from "../../lib/presentation";
import { getDevices } from "../instruments/device-api";
import { ConfigurationTemplatesPanel } from "./ConfigurationTemplatesPanel";
import { getSetupDefinitions, saveSetupDefinition, type SetupDefinition } from "./setup-api";

export function SetupPanel({
  operator,
  onSelectConfiguration,
}: {
  operator: string;
  onSelectConfiguration?: (choice: components["schemas"]["ConfigurationChoice-Input"]) => void;
}) {
  const cache = useQueryClient();
  const definitions = useQuery({
    queryKey: ["setup-definitions"],
    queryFn: ({ signal }) => getSetupDefinitions(signal),
  });
  const devices = useQuery({ queryKey: ["devices"], queryFn: ({ signal }) => getDevices(signal) });
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<SetupDefinition>();
  const [alias, setAlias] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const refresh = async () => {
    await cache.invalidateQueries({ queryKey: ["setup-definitions"] });
    await cache.invalidateQueries({ queryKey: ["setup-revisions"] });
  };
  const save = useMutation({
    mutationFn: () => {
      if (!draft) throw new Error("Choose a setup to edit.");
      return saveSetupDefinition(draft, name.trim(), operator);
    },
    onSuccess: async (saved) => {
      setSelected(saved.resolution.definition_id);
      setDraft(undefined);
      setName("");
      await refresh();
    },
  });
  const choose = (id: string) => {
    setSelected(id);
    save.reset();
    const definition = definitions.data?.items.find((item) => item.id === id)?.definition;
    setDraft(
      definition
        ? structuredClone(definition)
        : { topology: {}, instruments: [], routing: {}, domain_target: null },
    );
    setName(id ? `${id} (revised)` : "");
  };
  return (
    <section
      aria-label="Experiment setups"
      className="grid gap-3 rounded-lg border border-line bg-panel p-4"
    >
      <h3>Experiment setups</h3>
      <p>
        A setup maps experiment roles and channels to registered devices. Edit addresses and device
        safety in Devices and drivers.
      </p>
      <ConfigurationTemplatesPanel
        actor={operator}
        onSelectConfiguration={onSelectConfiguration}
        onImported={async (result) => {
          setSelected(result.setup.resolution.definition_id);
          setDraft(undefined);
          await refresh();
          await cache.invalidateQueries({ queryKey: ["devices"] });
        }}
      />
      <label>
        Saved setup
        <select value={selected} onChange={(event) => choose(event.target.value)}>
          <option value="">Choose a setup</option>
          {definitions.data?.items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.id}
            </option>
          ))}
        </select>
      </label>
      <button onClick={() => choose("")}>New setup</button>
      {draft && (
        <>
          <label>
            Save as
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          {draft.instruments.map((binding, index) => (
            <fieldset className="grid gap-2 rounded border border-line p-3" key={binding.id}>
              <legend>{binding.id}</legend>
              <label>
                Registered device
                <select
                  value={binding.device_id}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      instruments: draft.instruments.map((item, position) =>
                        position === index ? { ...item, device_id: event.target.value } : item,
                      ),
                    })
                  }
                >
                  {devices.data?.items.map((item) => (
                    <option
                      key={item.device.id}
                      value={item.device.id}
                      disabled={item.device.state === "retired"}
                    >
                      {item.device.label}
                      {item.device.state === "retired" ? " · retired" : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                After successful work
                <select
                  value={binding.success_action}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      instruments: draft.instruments.map((item, position) =>
                        position === index
                          ? {
                              ...item,
                              success_action: event.target.value as typeof binding.success_action,
                            }
                          : item,
                      ),
                    })
                  }
                >
                  <option value="release">Leave final state</option>
                  <option value="restore_baseline">Restore initial state</option>
                  <option value="apply_safe_state">Apply device safe state</option>
                </select>
              </label>
              <label>
                After failure
                <select
                  value={binding.failure_action}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      instruments: draft.instruments.map((item, position) =>
                        position === index
                          ? {
                              ...item,
                              failure_action: event.target.value as typeof binding.failure_action,
                            }
                          : item,
                      ),
                    })
                  }
                >
                  <option value="abort_and_release">Abort and release</option>
                  <option value="abort_then_safe_state">Abort and apply device safe state</option>
                </select>
              </label>
              <p>
                Start:{" "}
                {binding.run_start === "preserve"
                  ? "Preserve observed state"
                  : "Apply experiment defaults"}
                . Device safety requirements remain mandatory.
              </p>
              {(draft.routing.routes ?? [])
                .filter((route) => route.instrument_id === binding.id)
                .map((route) => (
                  <p key={route.id}>
                    Route {route.id}:{" "}
                    {route.endpoints
                      .map((endpoint) => endpoint.channel_id ?? endpoint.interface_id)
                      .join(", ")}
                  </p>
                ))}
            </fieldset>
          ))}
          <fieldset className="flex flex-wrap gap-2 border border-line p-3">
            <legend>Add device binding</legend>
            <label>
              Experiment alias
              <input value={alias} onChange={(event) => setAlias(event.target.value)} />
            </label>
            <label>
              Device
              <select value={deviceId} onChange={(event) => setDeviceId(event.target.value)}>
                <option value="">Choose a device</option>
                {devices.data?.items
                  .filter((item) => item.device.state === "available")
                  .map((item) => (
                    <option key={item.device.id} value={item.device.id}>
                      {item.device.label}
                    </option>
                  ))}
              </select>
            </label>
            <button
              disabled={
                !alias.trim() ||
                !deviceId ||
                draft.instruments.some(
                  (item) => item.id === alias.trim() || item.device_id === deviceId,
                )
              }
              onClick={() => {
                const safety = devices.data?.items.find((item) => item.device.id === deviceId)
                  ?.revision.content.safety;
                setDraft({
                  ...draft,
                  instruments: [
                    ...draft.instruments,
                    {
                      id: alias.trim(),
                      device_id: deviceId,
                      default_state: [],
                      run_start: "preserve",
                      success_action: safety?.require_safe_success ? "apply_safe_state" : "release",
                      failure_action: safety?.require_safe_failure
                        ? "abort_then_safe_state"
                        : "abort_and_release",
                    },
                  ],
                });
                setAlias("");
              }}
            >
              Add binding
            </button>
          </fieldset>
          <button
            disabled={!name.trim() || !operator.trim() || save.isPending}
            onClick={() => save.mutate()}
          >
            Save setup
          </button>
          <button onClick={() => setDraft(undefined)}>Cancel editing</button>
          <p>
            Saving creates a named definition. Preparing an experiment resolves its current device
            connections; submitted work retains its exact snapshot.
          </p>
        </>
      )}
      {(definitions.error || devices.error || save.error) && (
        <p role="alert">{errorMessage(definitions.error ?? devices.error ?? save.error)}</p>
      )}
    </section>
  );
}
