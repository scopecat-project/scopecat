import { expect, it } from "vitest";
import { parameterChanges, proposalChanges } from "./parameter-changes";

it("expands changed table cells, retaining units and identifying the row", () => {
  const row = {
    qubit: { id: "Q12", kind: "qubit" },
    duration: 64,
    amplitude: 0.15,
    frequency: { value: 5, unit: "GHz" },
  };
  const changes = parameterChanges(
    "drive",
    [row],
    [{ ...row, amplitude: 0.16, frequency: { value: 5e9, unit: "Hz" } }],
  );
  expect(changes.map((change) => change.path)).toEqual([
    "drive[0] (Q12).amplitude",
    "drive[0] (Q12).frequency",
  ]);
  expect(changes[1]?.before).toEqual({ value: 5, unit: "GHz" });
  expect(parameterChanges("drive", [row], [JSON.parse(JSON.stringify(row))])).toEqual([]);
});

it("uses retained entity/key/field scope and distinguishes equivalent representation", () => {
  const changes = proposalChanges({
    parameterId: "channels",
    before: [],
    after: [],
    cells: [
      {
        key: { qubit: { id: "q1", kind: "qubit" } },
        field: "frequency",
        before: { value: 5, unit: "GHz" },
        after: { value: 5000, unit: "MHz" },
        change_kind: "representation",
      },
    ],
  });
  expect(changes).toEqual([
    {
      path: "channels[qubit=q1].frequency",
      before: { value: 5, unit: "GHz" },
      after: { value: 5000, unit: "MHz" },
      changeKind: "representation",
    },
  ]);
});

it("does not invent scientific edits for an authoritative empty keyed-cell diff", () => {
  const before = [
    { qubit: "q0", frequency: 5 },
    { qubit: "q1", frequency: 6 },
  ];
  const changes = proposalChanges({
    parameterId: "channels",
    before,
    after: before.toReversed(),
    cells: [],
  });
  expect(changes).toEqual([
    {
      path: "channels.row_order",
      before: "Original row order",
      after: "Reordered rows · cell values unchanged",
      changeKind: "representation",
    },
  ]);
});
