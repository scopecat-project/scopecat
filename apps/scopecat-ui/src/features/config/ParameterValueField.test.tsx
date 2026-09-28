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
      value={0}
      entities={[]}
      onChange={change}
    />,
  );
  expect(screen.getByLabelText("drive[q0].amplitude")).toHaveValue(0);
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
  expect(screen.getByLabelText("drive[q0].amplitude")).toHaveValue(null);
  expect(screen.getByText("Unknown")).toBeInTheDocument();
});
