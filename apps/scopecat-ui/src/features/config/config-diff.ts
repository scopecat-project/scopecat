import { scientificChange, type ScientificChange } from "./quantity-change";
import type {
  ConfigProfileSnapshot,
  ParameterAtom,
  ParameterDefinition,
  ParameterEntity,
  ParameterQuantity,
  StoredParameterValue,
  TableParameterType,
  TableParameterValue,
} from "../../api-contract";

export type ParameterDiffStatus = "unchanged" | "changed" | "added" | "removed" | "schema-changed";

export type TableDiffStatus = "unchanged" | "changed" | "added" | "removed";

export interface TableCellDiff {
  columnId: string;
  changeKind?: ScientificChange;
  status: TableDiffStatus;
  before?: ParameterAtom;
  after?: ParameterAtom;
}

export interface TableRowDiff {
  identity: string;
  status: TableDiffStatus;
  key: Record<string, ParameterAtom>;
  before?: Record<string, ParameterAtom>;
  after?: Record<string, ParameterAtom>;
  cells: TableCellDiff[];
}

export interface TableParameterDiff {
  mode: "keyed" | "complete-replacement";
  rows: TableRowDiff[];
}

export interface ParameterDiff {
  parameterId: string;
  status: ParameterDiffStatus;
  beforeDefinition?: ParameterDefinition;
  afterDefinition?: ParameterDefinition;
  before?: StoredParameterValue;
  after?: StoredParameterValue;
  table?: TableParameterDiff;
}

export function diffConfigParameters(
  active: ConfigProfileSnapshot,
  selected: ConfigProfileSnapshot,
): ParameterDiff[] {
  const beforeDefinitions = active.system.parameter_catalog.definitions ?? [];
  const afterDefinitions = selected.system.parameter_catalog.definitions ?? [];
  const beforeValues = active.parameter_snapshot.values ?? [];
  const afterValues = selected.parameter_snapshot.values ?? [];
  const beforeDefinitionsById = byId(beforeDefinitions);
  const afterDefinitionsById = byId(afterDefinitions);
  const beforeValuesById = byId(beforeValues);
  const afterValuesById = byId(afterValues);
  const order = unique([
    ...afterDefinitions.map((item) => item.id),
    ...afterValues.map((item) => item.id),
    ...beforeDefinitions.map((item) => item.id),
    ...beforeValues.map((item) => item.id),
  ]);

  return order.map((parameterId) =>
    diffParameter(
      parameterId,
      beforeDefinitionsById.get(parameterId),
      afterDefinitionsById.get(parameterId),
      beforeValuesById.get(parameterId),
      afterValuesById.get(parameterId),
    ),
  );
}

function diffParameter(
  parameterId: string,
  beforeDefinition: ParameterDefinition | undefined,
  afterDefinition: ParameterDefinition | undefined,
  before: StoredParameterValue | undefined,
  after: StoredParameterValue | undefined,
): ParameterDiff {
  const common = {
    parameterId,
    beforeDefinition,
    afterDefinition,
    before,
    after,
  };
  if (!beforeDefinition || !before) return { ...common, status: "added" };
  if (!afterDefinition || !after) return { ...common, status: "removed" };
  if (
    !equal(beforeDefinition.value_type, afterDefinition.value_type) ||
    before.shape !== after.shape
  ) {
    return { ...common, status: "schema-changed" };
  }
  const status = equal(before, after) ? "unchanged" : "changed";
  if (
    afterDefinition.value_type.shape !== "table" ||
    before.shape !== "table" ||
    after.shape !== "table"
  ) {
    return { ...common, status };
  }
  return {
    ...common,
    status,
    table: diffTable(afterDefinition.value_type, before, after),
  };
}

function diffTable(
  valueType: TableParameterType,
  before: TableParameterValue,
  after: TableParameterValue,
): TableParameterDiff {
  const primaryKey = valueType.primary_key ?? [];
  if (primaryKey.length === 0) {
    return { mode: "complete-replacement", rows: [] };
  }
  const beforeRows = keyedRows(valueType, before.rows ?? []);
  const afterRows = keyedRows(valueType, after.rows ?? []);
  const identities = unique([...afterRows.keys(), ...beforeRows.keys()]);
  return {
    mode: "keyed",
    rows: identities.map((identity) => {
      const beforeRow = beforeRows.get(identity);
      const afterRow = afterRows.get(identity);
      const keySource = afterRow ?? beforeRow;
      if (!keySource) throw new Error("parameter table row identity disappeared");
      const key = Object.fromEntries(
        primaryKey.map((columnId) => [columnId, keySource[columnId]!]),
      );
      const cells = valueType.columns.map(({ id }) =>
        diffCell(id, beforeRow?.[id], afterRow?.[id]),
      );
      const status: TableDiffStatus = !beforeRow
        ? "added"
        : !afterRow
          ? "removed"
          : cells.some((cell) => cell.status !== "unchanged")
            ? "changed"
            : "unchanged";
      return {
        identity,
        status,
        key,
        before: beforeRow,
        after: afterRow,
        cells,
      };
    }),
  };
}

function diffCell(
  columnId: string,
  before: ParameterAtom | undefined,
  after: ParameterAtom | undefined,
): TableCellDiff {
  let status: TableDiffStatus;
  if (before === undefined && after === undefined) status = "unchanged";
  else if (before === undefined) status = "added";
  else if (after === undefined) status = "removed";
  else status = equal(before, after) ? "unchanged" : "changed";
  return {
    columnId,
    changeKind: status === "changed" ? scientificChange(before, after) : undefined,
    status,
    before,
    after,
  };
}

function keyedRows(
  valueType: TableParameterType,
  rows: Array<Record<string, ParameterAtom>>,
): Map<string, Record<string, ParameterAtom>> {
  return new Map(
    rows.map((row) => [
      JSON.stringify(
        (valueType.primary_key ?? []).map((columnId) => parameterAtomIdentity(row[columnId]!)),
      ),
      row,
    ]),
  );
}

export function parameterAtomIdentity(value: ParameterAtom): unknown {
  if (isQuantity(value)) return ["quantity", value.unit, value.value];
  if (isEntity(value)) return ["entity", value.kind ?? null, value.id];
  return [value === null ? "null" : typeof value, value];
}

export function parameterAtomLabel(value: ParameterAtom | undefined): string {
  if (value === undefined) return "Unknown";
  if (value === null) return "null";
  if (isQuantity(value)) return `${formatNumber(value.value)} ${value.unit}`;
  if (isEntity(value)) {
    return value.kind ? `${value.id} (${value.kind})` : value.id;
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  return typeof value === "number" ? formatNumber(value) : value;
}

export function parameterTypeLabel(definition: ParameterDefinition | undefined): string {
  if (!definition) return "Not defined";
  const valueType = definition.value_type;
  if (valueType.shape === "table") {
    return `Table · ${valueType.columns.length} columns`;
  }
  const suffix =
    valueType.atom.type === "quantity" && valueType.atom.unit ? ` · ${valueType.atom.unit}` : "";
  return `${title(valueType.shape)} · ${title(valueType.atom.type)}${suffix}`;
}

function isQuantity(value: ParameterAtom): value is ParameterQuantity {
  return typeof value === "object" && value !== null && "value" in value && "unit" in value;
}

function isEntity(value: ParameterAtom): value is ParameterEntity {
  return typeof value === "object" && value !== null && "id" in value;
}

function byId<T extends { id: string }>(items: T[]): Map<string, T> {
  return new Map(items.map((item) => [item.id, item]));
}

function unique<T>(items: Iterable<T>): T[] {
  return [...new Set(items)];
}

function equal(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat(undefined, {
    maximumSignificantDigits: 12,
  }).format(value);
}

function title(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
