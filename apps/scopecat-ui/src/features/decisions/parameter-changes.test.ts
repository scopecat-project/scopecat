import { expect, it } from "vitest";
import { parameterChanges } from "./parameter-changes";

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
