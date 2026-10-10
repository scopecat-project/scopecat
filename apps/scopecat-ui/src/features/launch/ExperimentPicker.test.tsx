// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ExperimentPicker } from "./ExperimentPicker";
import type { LaunchCatalogEntry } from "./launch-api";

afterEach(cleanup);

const entries: LaunchCatalogEntry[] = [
  {
    id: "signal",
    title: "Signal",
    description: "Measure a frequency sweep",
    version: "1",
    actions: ["preview", "submit"],
    kind: "diagnostic",
    configuration_effect: "none",
    request: {},
    controls: [],
  },
  {
    id: "offset",
    title: "Offset",
    description: "Fit a correction from retained samples",
    version: "1",
    actions: ["preview", "submit"],
    kind: "calibration",
    configuration_effect: "candidate",
    request: {},
    controls: [],
  },
];

it("searches descriptions without changing selection, then explicitly selects an experiment", () => {
  const onSelect = vi.fn();
  render(
    <ExperimentPicker entries={entries} selectedId="signal" disabled={false} onSelect={onSelect} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Browse experiments" }));
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "CORRECTION retained" } });
  expect(screen.queryByRole("button", { name: "Prepare Signal" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Experiment")).toHaveValue("signal");
  expect(onSelect).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Prepare Offset" }));
  expect(onSelect).toHaveBeenCalledExactlyOnceWith(entries[1]);
});

it("keeps an unavailable selection and handles an empty search without replacing it", () => {
  const onSelect = vi.fn();
  render(
    <ExperimentPicker
      entries={entries}
      selectedId="removed"
      disabled={false}
      onSelect={onSelect}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Browse experiments" }));
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "no such experiment" } });
  expect(screen.getByText(/No matching experiments/)).toBeVisible();
  expect(screen.getByLabelText("Experiment")).toHaveValue("removed");
  fireEvent.click(screen.getByRole("button", { name: "Close experiment browser" }));
  expect(onSelect).not.toHaveBeenCalled();
});

it("prevents choosing another entry when the parent disallows selection", () => {
  const onSelect = vi.fn();
  const view = render(
    <ExperimentPicker entries={entries} selectedId="signal" disabled={false} onSelect={onSelect} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Browse experiments" }));
  view.rerender(
    <ExperimentPicker entries={entries} selectedId="signal" disabled onSelect={onSelect} />,
  );
  expect(screen.getByRole("button", { name: "Prepare Offset" })).toBeDisabled();
  expect(screen.getByLabelText("Experiment")).toBeDisabled();
});
