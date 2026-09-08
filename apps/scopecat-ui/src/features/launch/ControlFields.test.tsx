// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { components } from "../../api-schema";
import { ControlFields, controlEdits, initialControlDrafts } from "./ControlFields";

type Control = components["schemas"]["LaunchControl"];
const frequency: Control = {
  id: "frequency",
  title: "Frequency",
  group: "Signal",
  default: { value: 4.8, unit: "GHz" },
  unit: "GHz",
  minimum: 4.5,
  maximum: 5.5,
  scannable: true,
  ownership: "editable",
  provenance: "Reviewed project default",
};
function Form({ fields }: { fields: Control[] }) {
  const [drafts, setDrafts] = useState(() => initialControlDrafts(fields));
  return (
    <>
      <ControlFields
        controls={fields}
        drafts={drafts}
        onChange={(id, draft) => setDrafts({ ...drafts, [id]: draft })}
      />
      <output data-testid="edits">{JSON.stringify(controlEdits(drafts))}</output>
    </>
  );
}
afterEach(cleanup);
it("converts GHz to MHz and replaces a scalar rather than hiding it behind a scan", () => {
  render(<Form fields={[frequency]} />);
  fireEvent.change(screen.getByLabelText("Frequency"), { target: { value: "5.1" } });
  fireEvent.change(screen.getByLabelText("Frequency unit"), { target: { value: "MHz" } });
  expect(screen.getByLabelText("Frequency")).toHaveValue(5100);
  expect(JSON.parse(screen.getByTestId("edits").textContent)).toEqual({
    frequency: { mode: "fixed", value: { value: 5100, unit: "MHz" } },
  });
  fireEvent.change(screen.getByLabelText("Frequency source"), { target: { value: "values" } });
  expect(screen.queryByLabelText("Frequency")).toBeNull();
  expect(screen.getByLabelText("Frequency scan values")).toHaveValue("4.8");
  expect(JSON.parse(screen.getByTestId("edits").textContent)).toEqual({
    frequency: { mode: "scan", axis: { kind: "values", values: [{ value: 4.8, unit: "GHz" }] } },
  });
  fireEvent.change(screen.getByLabelText("Frequency source"), { target: { value: "fixed" } });
  expect(screen.getByLabelText("Frequency")).toHaveValue(4.8);
});
it.each(["dBm", "unknown-project-unit"])(
  "retains %s without offering a silent linear relabel",
  (unit) => {
    render(<Form fields={[{ ...frequency, unit, default: { value: 5, unit } }]} />);
    const select = screen.getByLabelText("Frequency unit");
    expect(within(select).getAllByRole("option")).toHaveLength(1);
    expect(within(select).getByRole("option", { name: unit })).toBeVisible();
    expect(within(select).queryByRole("option", { name: "W" })).toBeNull();
    expect(JSON.parse(screen.getByTestId("edits").textContent)).toEqual({
      frequency: { mode: "fixed", value: { value: 5, unit } },
    });
  },
);
