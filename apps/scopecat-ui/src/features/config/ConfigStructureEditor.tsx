import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import type { ParameterAtom, ParameterScalarType } from "../../api-contract";
import { errorMessage } from "../../lib/presentation";
import { primaryButton, secondaryButton } from "../../ui/styles";
import { ContextAtom } from "./ConfigContextEditor";
import {
  createConfigOperationId,
  previewConfigStructure,
  saveConfigContext,
  type ConfigRegistryEntryDetail,
  type ParameterStructurePlan,
} from "./config-api";

type EditKind = "add_column" | "rename_column" | "change_column" | "change_key";
type Conversion = "unknown" | "compatible_unit" | "lossless_numeric" | "explicit_values";

export function ConfigStructureEditor({
  detail,
  operator,
  onCancel,
  onSaved,
}: {
  detail: ConfigRegistryEntryDetail;
  operator: string;
  onCancel: () => void;
  onSaved: (id: string) => void;
}) {
  const tables = (detail.config.system.parameter_catalog.definitions ?? []).filter(
    (item) => item.value_type.shape === "table",
  );
  const [tableId, setTableId] = useState(tables[0]?.id ?? "");
  const [kind, setKind] = useState<EditKind>("add_column");
  const [columnId, setColumnId] = useState("");
  const [newId, setNewId] = useState("");
  const [typeName, setTypeName] = useState("float");
  const [unit, setUnit] = useState("GHz");
  const [conversion, setConversion] = useState<Conversion>("unknown");
  const [values, setValues] = useState<Record<number, ParameterAtom | undefined>>({});
  const [origin, setOrigin] = useState<"imported" | "estimated">("estimated");
  const [note, setNote] = useState("");
  const [label, setLabel] = useState(
    `${detail.entry.source.kind === "parameter_context" ? detail.entry.source.context.label : "Context"} / revised structure`,
  );
  const [entryId] = useState(() => createConfigOperationId("structure"));
  const definition = tables.find((item) => item.id === tableId);
  const table = definition?.value_type.shape === "table" ? definition.value_type : undefined;
  const stored = (detail.config.parameter_snapshot.values ?? []).find(
    (item) => item.id === tableId,
  );
  const rows = stored?.shape === "table" ? (stored.rows ?? []) : [];
  const atomType: ParameterScalarType =
    typeName === "quantity"
      ? { type: "quantity", unit, finite: true }
      : typeName === "float"
        ? { type: "float", finite: true }
        : typeName === "int"
          ? { type: "int" }
          : typeName === "bool"
            ? { type: "bool" }
            : { type: "string" };
  const preview = useMutation({ mutationFn: previewConfigStructure });
  const base = { entry_id: detail.entry.id, content_hash: detail.entry.content_hash };
  const plan = (): ParameterStructurePlan => {
    if (!table || !detail.structureVersion) throw new Error("Select a saved table revision.");
    const column = {
      id: columnId.trim(),
      value_type: { shape: "scalar" as const, atom: atomType },
    };
    const decisions = rows.map((row, index) => ({
      ...(table.primary_key.length
        ? {
            key: Object.fromEntries(
              Object.entries(row).filter(([key]) => table.primary_key.includes(key)),
            ),
          }
        : { row_index: index }),
      value: values[index] ?? null,
      origin: values[index] === undefined ? ("unknown" as const) : origin,
      note: note.trim() || "Explicitly left unknown",
    }));
    const edit =
      kind === "add_column"
        ? { kind, parameter_id: tableId, column, values: decisions }
        : kind === "rename_column"
          ? { kind, parameter_id: tableId, column_id: columnId, new_id: newId.trim() }
          : kind === "change_key"
            ? {
                kind,
                parameter_id: tableId,
                columns: newId
                  .split(",")
                  .map((id) => id.trim())
                  .filter(Boolean),
              }
            : {
                kind,
                parameter_id: tableId,
                column,
                conversion,
                values: conversion === "explicit_values" ? decisions : [],
              };
    return { base, structure_version: detail.structureVersion, edits: [edit], consumers: [] };
  };
  const save = useMutation({
    mutationFn: () => {
      const metadata =
        detail.entry.source.kind === "parameter_context" ? detail.entry.source.context : undefined;
      if (!metadata || !preview.data || !preview.variables)
        throw new Error("Preview this saved working point first.");
      return saveConfigContext({
        entry_id: entryId,
        base,
        sample: {
          sample_id: metadata.sample.sample_id,
          revision: metadata.sample.revision,
          role: metadata.sample.role,
        },
        working_point_id: metadata.working_point_id,
        label: label.trim(),
        actor: operator,
        note,
        structure_plan: preview.variables,
      });
    },
    onSuccess: (saved) => onSaved(saved.entry.id),
  });
  const invalidate = () => {
    preview.reset();
    save.reset();
  };
  const explicit =
    kind === "add_column" || (kind === "change_column" && conversion === "explicit_values");
  return (
    <section
      role="dialog"
      aria-label="Change parameter table structure"
      className="grid gap-4 rounded-lg border border-line p-5"
      onChange={invalidate}
    >
      <h3>Change parameter table structure</h3>
      <p>
        Save a new working point revision. Existing runs and the lab default retain their original
        tables. Missing cells stay unknown.
      </p>
      <label>
        Table
        <select
          aria-label="Structure table"
          value={tableId}
          onChange={(event) => {
            setTableId(event.target.value);
            setValues({});
          }}
        >
          {tables.map((item) => (
            <option key={item.id}>{item.id}</option>
          ))}
        </select>
      </label>
      <label>
        Change
        <select
          aria-label="Structure change"
          value={kind}
          onChange={(event) => {
            setKind(event.target.value as EditKind);
            setValues({});
          }}
        >
          <option value="add_column">Add optional column</option>
          <option value="rename_column">Rename column ID</option>
          <option value="change_column">Change type or unit</option>
          <option value="change_key">Change lookup key</option>
        </select>
      </label>
      {table && (
        <p>
          Current columns: {table.columns.map((column) => column.id).join(", ")}. Lookup key:{" "}
          {table.primary_key.join(", ") || "row index"}.
        </p>
      )}
      <datalist id="structure-existing-columns">
        {table?.columns.map((column) => (
          <option key={column.id} value={column.id}>
            {column.id}
          </option>
        ))}
      </datalist>
      {kind !== "change_key" && (
        <label>
          Column ID
          <input
            aria-label="Structure column ID"
            list="structure-existing-columns"
            value={columnId}
            onChange={(event) => setColumnId(event.target.value)}
          />
        </label>
      )}
      {(kind === "rename_column" || kind === "change_key") && (
        <label>
          {kind === "change_key" ? "New key columns, separated by commas" : "New column ID"}
          <input
            aria-label="Structure new IDs"
            value={newId}
            onChange={(event) => setNewId(event.target.value)}
          />
        </label>
      )}
      {(kind === "add_column" || kind === "change_column") && (
        <>
          <label>
            Type
            <select
              aria-label="Structure value type"
              value={typeName}
              onChange={(event) => {
                setTypeName(event.target.value);
                setValues({});
              }}
            >
              {["float", "int", "string", "bool", "quantity"].map((name) => (
                <option key={name}>{name}</option>
              ))}
            </select>
          </label>
          {typeName === "quantity" && (
            <label>
              Unit
              <input
                aria-label="Structure unit"
                value={unit}
                onChange={(event) => setUnit(event.target.value)}
              />
            </label>
          )}
        </>
      )}
      {kind === "change_column" && (
        <label>
          Existing values
          <select
            aria-label="Structure conversion"
            value={conversion}
            onChange={(event) => setConversion(event.target.value as Conversion)}
          >
            <option value="unknown">Mark unknown</option>
            <option value="compatible_unit">Convert compatible units</option>
            <option value="lossless_numeric">Convert numeric values without rounding</option>
            <option value="explicit_values">Provide explicit values</option>
          </select>
        </label>
      )}
      {explicit && (
        <>
          <label>
            Provided value origin
            <select
              aria-label="Structure value origin"
              value={origin}
              onChange={(event) => setOrigin(event.target.value as typeof origin)}
            >
              <option value="estimated">Estimated</option>
              <option value="imported">Imported</option>
            </select>
          </label>
          {rows.map((_row, index) => (
            <ContextAtom
              key={index}
              label={`New value row ${index + 1}`}
              type={atomType}
              value={values[index]}
              entities={detail.config.system.topology.entities ?? []}
              onChange={(value) => {
                setValues((current) => ({ ...current, [index]: value }));
                invalidate();
              }}
            />
          ))}
        </>
      )}
      <label>
        Revision label
        <input
          aria-label="Structure revision label"
          value={label}
          onChange={(event) => setLabel(event.target.value)}
        />
      </label>
      <label>
        Change / value source note
        <input
          aria-label="Structure note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      <div className="flex gap-2">
        <button className={secondaryButton} onClick={onCancel}>
          Cancel
        </button>
        <button
          className={secondaryButton}
          disabled={preview.isPending || !table || !detail.structureVersion || !note.trim()}
          onClick={() => preview.mutate(plan())}
        >
          Preview structure change
        </button>
        <button
          className={primaryButton}
          disabled={!preview.data || save.isPending || !label.trim()}
          onClick={() => save.mutate()}
        >
          Save revised working point
        </button>
      </div>
      {(preview.error || save.error) && (
        <p role="alert">{errorMessage(preview.error || save.error)}</p>
      )}
      {preview.data && (
        <div aria-label="Structure impact preview">
          <p>{preview.data.consumer_scope}</p>
          {preview.data.impacts.map((impact, index) => (
            <p key={index}>
              {impact.parameter_id}
              {impact.column_id ? `.${impact.column_id}` : ""}:{" "}
              {
                {
                  scalar_added: "scalar added",
                  table_added: "table added",
                  added: "column added",
                  renamed: "column renamed",
                  type_changed: "type or unit changed",
                  key_changed: "lookup key changed",
                }[impact.kind]
              }
              ; {impact.affected_rows} rows. {impact.consumer_action}
            </p>
          ))}
          <p>Unknown: {preview.data.missing_values.join(", ") || "none"}</p>
        </div>
      )}
    </section>
  );
}
