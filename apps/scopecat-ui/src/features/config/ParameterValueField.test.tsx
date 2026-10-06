// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import { ParameterValueField } from "./ParameterValueField";

it("renders zero as a known value and keeps unknown distinct", () => {
  const change = vi.fn();
  const view = render(
    <ParameterValueField
      label="drive[q0].amplitude"
      type={{ type: "float", finite: true }}
      value={{ text: "0", unit: "" }}
      entities={[]}
      onChange={change}
    />,
  );
  expect(screen.getByLabelText("drive[q0].amplitude")).toHaveValue("0");
  expect(screen.getByText("Value set")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Mark unknown" }));
  expect(change).toHaveBeenCalledWith(undefined);
  view.rerender(
    <ParameterValueField
      label="drive[q0].amplitude"
      type={{ type: "float", finite: true }}
      entities={[]}
      onChange={change}
    />,
  );
  expect(screen.getByLabelText("drive[q0].amplitude")).toHaveValue("");
  expect(screen.getByText("Unknown")).toBeInTheDocument();
});

it("retains incomplete numeric text without marking it unknown", () => {
  const change = vi.fn();
  const view = render(
    <ParameterValueField
      label="frequency"
      type={{ type: "float", finite: true }}
      entities={[]}
      onChange={change}
    />,
  );
  for (const text of ["-", "1e", ""]) {
    fireEvent.change(screen.getByLabelText("frequency"), { target: { value: text } });
    expect(change).toHaveBeenLastCalledWith({ text, unit: "" });
    view.rerender(
      <ParameterValueField
        label="frequency"
        type={{ type: "float", finite: true }}
        value={{ text, unit: "" }}
        entities={[]}
        onChange={change}
      />,
    );
  }
  view.unmount();
});

it("uses entity identity despite metadata differences and displays retained unavailable entities", () => {
  const change = vi.fn();
  const view = render(
    <ParameterValueField
      label="target"
      type={{ type: "entity" }}
      value={{
        text: JSON.stringify({ id: "q0", kind: "qubit", metadata: { old: true } }),
        unit: "",
      }}
      entities={[
        { id: "q0", kind: "qubit", metadata: { new: true } },
        { id: "q0", kind: "resonator" },
      ]}
      onChange={change}
    />,
  );
  expect(screen.getByLabelText("target")).toHaveValue(JSON.stringify(["qubit", "q0"]));
  fireEvent.change(screen.getByLabelText("target"), {
    target: { value: JSON.stringify(["resonator", "q0"]) },
  });
  expect(change).toHaveBeenCalledWith({
    text: JSON.stringify({ id: "q0", kind: "resonator" }),
    unit: "",
  });
  view.rerender(
    <ParameterValueField
      label="target"
      type={{ type: "entity" }}
      value={{ text: JSON.stringify({ id: "q0", kind: "qubit" }), unit: "" }}
      entities={[]}
      onChange={change}
    />,
  );
  expect(screen.getByRole("option", { name: "Retained qubit: q0" })).toBeVisible();
  view.unmount();
});
