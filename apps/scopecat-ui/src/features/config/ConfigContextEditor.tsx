import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type {
  ConfigProfileSnapshot,
  ConfigRegistryEntry,
  ParameterAtom,
  ParameterEntity,
  ParameterScalarType,
  StoredParameterValue,
} from "../../api-contract";
import { getSamples } from "../samples/sample-api";
import { errorMessage } from "../../lib/presentation";
import { primaryButton, secondaryButton } from "../../ui/styles";
import { createConfigOperationId, saveConfigContext } from "./config-api";

export function ConfigContextEditor({
  entry,
  config,
  operator,
  onCancel,
  onSaved,
}: {
  entry: ConfigRegistryEntry;
  config: ConfigProfileSnapshot;
  operator: string;
  onCancel: () => void;
  onSaved: (entryId: string) => void;
}) {
  const metadata = entry.source.kind === "parameter_context" ? entry.source.context : undefined;
  const samples = useQuery({
    queryKey: ["samples", "contexts"],
    queryFn: ({ signal }) => getSamples(undefined, signal),
  });
  const [sampleId, setSampleId] = useState(metadata?.sample.sample_id ?? "");
  const [point, setPoint] = useState(metadata?.working_point_id ?? "");
  const [label, setLabel] = useState(metadata?.label ?? "");
  const [entryId] = useState(() => createConfigOperationId("context"));
  const [values, setValues] = useState<StoredParameterValue[]>([
    ...(config.parameter_snapshot.values ?? []),
  ]);
  const [note, setNote] = useState("");
  const selectedSample = samples.data?.items.find((item) => item.record.id === sampleId);
  const mutation = useMutation({
    mutationFn: () => {
      if (!selectedSample) throw new Error("Select a physical sample revision.");
      return saveConfigContext({
        entry_id: entryId,
        base: { entry_id: entry.id, content_hash: entry.content_hash },
        sample: {
          sample_id: sampleId,
          revision: selectedSample.record.active_revision,
          role: "subject",
        },
        working_point_id: point.trim(),
        label: label.trim(),
        parameters: { ...config.parameter_snapshot, values },
        actor: operator,
        note,
      });
    },
    onSuccess: (saved) => onSaved(saved.entry.id),
  });
  const setValue = (id: string, value?: StoredParameterValue) =>
    setValues((current) =>
      value === undefined
        ? current.filter((item) => item.id !== id)
        : current.some((item) => item.id === id)
          ? current.map((item) => (item.id === id ? value : item))
          : [...current, value],
    );
  const entities = config.system.topology.entities ?? [];
  return (
    <section
      role="dialog"
      aria-label="Save parameter context"
      className="grid gap-4 rounded-lg border border-line bg-panel p-5"
    >
      <h3>Save a working point</h3>
      <p>
        A new saved version keeps the lab default unchanged. Unknown values stay unknown; saving
        does not validate a calibration.
      </p>
      <label>
        Physical sample
        <select
          aria-label="Physical sample"
          value={sampleId}
          onChange={(event) => setSampleId(event.target.value)}
        >
          <option value="">Choose a sample</option>
          {(samples.data?.items ?? []).map((item) => (
            <option key={item.record.id} value={item.record.id}>
              {item.revision.content.display_name} · {item.record.id} · r
              {item.record.active_revision}
            </option>
          ))}
        </select>
      </label>
      {samples.error && <p role="alert">{errorMessage(samples.error)}</p>}
      <label>
        Working point
        <input
          aria-label="Working point"
          value={point}
          onChange={(event) => setPoint(event.target.value)}
        />
      </label>
      <label>
        Label
        <input
          aria-label="Context label"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
        />
      </label>
      <p>
        Source: {entry.id}. Explicit edits override this saved source; the current lab default is
        not overlaid.
      </p>
      {(config.system.parameter_catalog.definitions ?? []).map((definition) => {
        const value = values.find((item) => item.id === definition.id);
        if (definition.value_type.shape === "scalar")
          return (
            <ContextAtom
              key={definition.id}
              label={definition.id}
              type={definition.value_type.atom}
              value={value?.shape === "scalar" ? value.value : undefined}
              entities={entities}
              onChange={(atom) =>
                setValue(
                  definition.id,
                  atom === undefined
                    ? undefined
                    : { id: definition.id, shape: "scalar", value: atom },
                )
              }
            />
          );
        const rows = value?.shape === "table" ? (value.rows ?? []) : [];
        const table = definition.value_type;
        return (
          <fieldset key={definition.id}>
            <legend>{definition.id}</legend>
            {value === undefined && (
              <p>Unknown table. Add its rows through the Python context API.</p>
            )}
            {rows.map((row, index) => (
              <div className="grid gap-2 border-t border-line py-2" key={index}>
                {table.columns.map((column) => (
                  <ContextAtom
                    key={column.id}
                    label={`${definition.id}[${index}].${column.id}`}
                    type={column.value_type}
                    value={row[column.id]}
                    entities={entities}
                    disabled={table.primary_key?.includes(column.id)}
                    onChange={(atom) => {
                      const next = { ...row };
                      if (atom === undefined) delete next[column.id];
                      else next[column.id] = atom;
                      setValue(definition.id, {
                        id: definition.id,
                        shape: "table",
                        rows: rows.map((item, rowIndex) => (rowIndex === index ? next : item)),
                      });
                    }}
                  />
                ))}
              </div>
            ))}
          </fieldset>
        );
      })}
      <label>
        Note
        <input
          aria-label="Context note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      {mutation.error && <p role="alert">{errorMessage(mutation.error)}</p>}
      <div className="flex gap-2">
        <button type="button" className={secondaryButton} onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className={primaryButton}
          disabled={
            mutation.isPending ||
            !selectedSample ||
            !point.trim() ||
            !label.trim() ||
            !operator.trim()
          }
          onClick={() => mutation.mutate()}
        >
          Save context
        </button>
      </div>
    </section>
  );
}

function ContextAtom({
  label,
  type,
  value,
  entities,
  disabled,
  onChange,
}: {
  label: string;
  type: ParameterScalarType;
  value?: ParameterAtom;
  entities: ParameterEntity[];
  disabled?: boolean;
  onChange: (value?: ParameterAtom) => void;
}) {
  const quantity =
    value != null && typeof value === "object" && "unit" in value ? value : undefined;
  const entity = value != null && typeof value === "object" && "id" in value ? value : undefined;
  return (
    <label className="flex flex-wrap items-center gap-2">
      {label}
      {type.type === "bool" ? (
        <select
          aria-label={label}
          value={value === undefined ? "" : String(value)}
          disabled={disabled}
          onChange={(event) =>
            onChange(event.target.value === "" ? undefined : event.target.value === "true")
          }
        >
          <option value="">Unknown</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      ) : type.type === "entity" ? (
        <select
          aria-label={label}
          value={entity?.id ?? ""}
          disabled={disabled}
          onChange={(event) => onChange(entities.find((item) => item.id === event.target.value))}
        >
          <option value="">Unknown</option>
          {entities
            .filter((item) => !type.entity_kind || item.kind === type.entity_kind)
            .map((item) => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
        </select>
      ) : (
        <input
          aria-label={label}
          placeholder="Unknown"
          type={type.type === "string" ? "text" : "number"}
          step="any"
          disabled={disabled}
          value={
            quantity?.value ?? (typeof value === "string" || typeof value === "number" ? value : "")
          }
          onChange={(event) => {
            const text = event.target.value;
            if (type.type === "string") onChange(text);
            else if (text === "") onChange(undefined);
            else if (type.type === "quantity")
              onChange({ value: Number(text), unit: quantity?.unit ?? type.unit ?? "" });
            else onChange(Number(text));
          }}
        />
      )}
      {type.type === "quantity" && (
        <input
          aria-label={`${label} unit`}
          value={quantity?.unit ?? type.unit ?? ""}
          disabled={disabled || quantity === undefined}
          onChange={(event) => {
            if (quantity) onChange({ ...quantity, unit: event.target.value });
          }}
        />
      )}
      {!disabled && (
        <button type="button" className={secondaryButton} onClick={() => onChange(undefined)}>
          Mark unknown
        </button>
      )}
    </label>
  );
}
