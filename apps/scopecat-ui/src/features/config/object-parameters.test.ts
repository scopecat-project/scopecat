import { expect, it } from "vitest";
import {
  mappedEntity,
  objectContextError,
  objectRows,
  panelMatches,
  updateObjectCell,
} from "./object-parameters";
import { objectContext, objectDefinitions, objectValues } from "./object-parameters.fixtures";

it("requires exact mapped sample identity, never a same-name object on another chip or an inline association", () => {
  const context = objectContext();
  expect(mappedEntity(context, "q0")).toEqual({ id: "q0", kind: "qubit" });
  const panel = {
    node: {} as HTMLElement,
    sampleId: "chip-b",
    revision: 1,
    contentHash: "sha256:chip-b",
    entityId: "q0",
  };
  expect(panelMatches(context, panel)).toBe(false);
  expect(
    panelMatches(context, {
      ...panel,
      sampleId: "chip-a",
      contentHash: context.sample.content_hash,
    }),
  ).toBe(true);
  expect(
    objectContextError({ ...context, sample: { ...context.sample, sample_id: "chip-b" } }),
  ).toContain("another sample");
  context.resolution.context.target_binding = null;
  expect(mappedEntity(context, "q0")).toBeUndefined();
  expect(objectContextError(context)).toContain("registered");
});

it("projects typed references with kind and original positions, retaining compound rows and shared references", () => {
  const values = objectValues();
  const rows = objectRows(objectDefinitions, values, { id: "q0", kind: "qubit" });
  expect(rows.map((row) => row.rowIndex)).toEqual([1, 2]);
  expect(rows[0]!.row.offset?.text).toBe("1e");
  expect(rows[1]!.references.map((ref) => ref.id)).toEqual(["q0", "q1"]);
  const next = updateObjectCell(rows[0]!.value, rows[0]!.rowIndex, "offset", {
    text: "-",
    unit: "mV",
  });
  expect(next.rows?.[0]).toEqual(values[0]!.rows?.[0]);
  expect(next.rows?.[1]?.offset).toEqual({ text: "-", unit: "mV" });
  expect(next.rows?.slice(2)).toEqual(values[0]!.rows?.slice(2));
  expect(values[0]!.rows?.[1]?.offset?.text).toBe("1e");
});

it("does not infer entity membership from string values or an unassigned table", () => {
  const definitions = structuredClone(objectDefinitions);
  if (definitions[0]!.value_type.shape === "table")
    definitions[0]!.value_type.columns = [{ id: "qubit", value_type: { type: "string" } }];
  expect(objectRows(definitions, objectValues(), { id: "q0", kind: "qubit" })).toEqual([]);
  expect(objectRows(objectDefinitions, [], { id: "q0", kind: "qubit" })).toEqual([]);
});
