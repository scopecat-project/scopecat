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
  fireEvent.change(screen.getByLabelText("Frequency 单位"), { target: { value: "MHz" } });
  expect(screen.getByLabelText("Frequency")).toHaveValue("5100");
  expect(JSON.parse(screen.getByTestId("edits").textContent)).toEqual({
    frequency: { mode: "fixed", value: { value: 5100, unit: "MHz" } },
  });
  fireEvent.change(screen.getByLabelText("Frequency 取值来源"), { target: { value: "values" } });
  expect(screen.queryByLabelText("Frequency")).toBeNull();
  expect(screen.getByLabelText("Frequency 扫描列表")).toHaveValue("4.8");
  expect(JSON.parse(screen.getByTestId("edits").textContent)).toEqual({
    frequency: { mode: "scan", axis: { kind: "values", values: [{ value: 4.8, unit: "GHz" }] } },
  });
  fireEvent.change(screen.getByLabelText("Frequency 取值来源"), { target: { value: "fixed" } });
  expect(screen.getByLabelText("Frequency")).toHaveValue("4.8");
});
it.each(["dBm", "unknown-project-unit"])(
  "retains %s without offering a silent linear relabel",
  (unit) => {
    render(<Form fields={[{ ...frequency, unit, default: { value: 5, unit } }]} />);
    const select = screen.getByLabelText("Frequency 单位");
    expect(within(select).getAllByRole("option")).toHaveLength(1);
    expect(within(select).getByRole("option", { name: unit })).toBeVisible();
    expect(within(select).queryByRole("option", { name: "W" })).toBeNull();
    expect(JSON.parse(screen.getByTestId("edits").textContent)).toEqual({
      frequency: { mode: "fixed", value: { value: 5, unit } },
    });
  },
);

it("keeps a required control empty and offers only explicit sources", () => {
  const control = { ...frequency, default: null };
  const drafts = initialControlDrafts([control]);
  render(<ControlFields controls={[control]} drafts={drafts} onChange={() => {}} />);
  expect(screen.getByText("Frequency（必填）")).toBeVisible();
  expect(screen.getByLabelText("Frequency")).toHaveValue("");
  expect(screen.getByLabelText("Frequency")).toBeRequired();
  expect(screen.queryByRole("option", { name: "声明默认值" })).toBeNull();
  expect(() => controlEdits(drafts)).toThrow("有限数值");
});

function DraftForm() {
  const [drafts, setDrafts] = useState(initialControlDrafts([frequency]));
  return (
    <ControlFields
      controls={[frequency]}
      drafts={drafts}
      onChange={(id, draft) => setDrafts({ ...drafts, [id]: draft })}
    />
  );
}
it("retains edits until blur and atomically refuses unit changes with invalid drafts", () => {
  render(<DraftForm />);
  const input = screen.getByLabelText("Frequency");
  fireEvent.change(input, { target: { value: "5000M" } });
  expect(input).toHaveValue("5000M");
  fireEvent.blur(input);
  expect(input).toHaveValue("5");
  fireEvent.change(input, { target: { value: "1wat" } });
  fireEvent.change(screen.getByLabelText("Frequency 单位"), { target: { value: "MHz" } });
  expect(input).toHaveValue("1wat");
  expect(screen.getByLabelText("Frequency 单位")).toHaveValue("GHz");
  expect(screen.getByRole("alert")).toHaveTextContent("未知或不兼容的单位");
});
it("parses scan endpoints and values while keeping point counts plain", () => {
  expect(controlEdits({ f: { mode: "values", unit: "MHz", values: "1G,250k" } }).f).toEqual({
    mode: "scan",
    axis: {
      kind: "values",
      values: [
        { value: 1000, unit: "MHz" },
        { value: 0.25, unit: "MHz" },
      ],
    },
  });
  expect(
    controlEdits({ f: { mode: "range", unit: "MHz", start: "250k", stop: "1G", points: "3" } }).f,
  ).toEqual({
    mode: "scan",
    axis: {
      kind: "range",
      start: { value: 0.25, unit: "MHz" },
      stop: { value: 1000, unit: "MHz" },
      points: 3,
    },
  });
  expect(() =>
    controlEdits({ f: { mode: "range", unit: "MHz", start: "1", stop: "2", points: "3k" } }),
  ).toThrow("有限数值");
});

it("switches scan units atomically and preserves empty range endpoints", () => {
  render(<DraftForm />);
  fireEvent.change(screen.getByLabelText("Frequency 取值来源"), { target: { value: "range" } });
  fireEvent.change(screen.getByLabelText("Frequency 单位"), { target: { value: "MHz" } });
  expect(screen.getByLabelText("Frequency 起点")).toHaveValue("4800");
  expect(screen.getByLabelText("Frequency 终点")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("Frequency 终点"), { target: { value: "1e" } });
  fireEvent.change(screen.getByLabelText("Frequency 单位"), { target: { value: "GHz" } });
  expect(screen.getByLabelText("Frequency 单位")).toHaveValue("MHz");
  expect(screen.getByLabelText("Frequency 起点")).toHaveValue("4800");
  expect(screen.getByLabelText("Frequency 终点")).toHaveValue("1e");
  fireEvent.change(screen.getByLabelText("Frequency 取值来源"), { target: { value: "values" } });
  fireEvent.change(screen.getByLabelText("Frequency 扫描列表"), {
    target: { value: "4.8G, 4900M" },
  });
  fireEvent.change(screen.getByLabelText("Frequency 单位"), { target: { value: "MHz" } });
  expect(screen.getByLabelText("Frequency 扫描列表")).toHaveValue("4800, 4900");
  fireEvent.change(screen.getByLabelText("Frequency 扫描列表"), {
    target: { value: "4800, oops" },
  });
  fireEvent.change(screen.getByLabelText("Frequency 单位"), { target: { value: "GHz" } });
  expect(screen.getByLabelText("Frequency 扫描列表")).toHaveValue("4800, oops");
  expect(screen.getByLabelText("Frequency 单位")).toHaveValue("MHz");
});

it("switches phase units without changing the declared default", () => {
  render(
    <Form
      fields={[{ ...frequency, title: "Phase", unit: "deg", default: { value: 180, unit: "deg" } }]}
    />,
  );
  fireEvent.change(screen.getByLabelText("Phase 单位"), { target: { value: "rad" } });
  expect(screen.getByLabelText("Phase")).toHaveValue(String(Math.PI));
  fireEvent.change(screen.getByLabelText("Phase 取值来源"), { target: { value: "default" } });
  expect(screen.getByText("180 deg · 声明默认值")).toBeVisible();
});

it("discards errors when an external plan replaces a draft with the same control id", () => {
  const invalid = { mode: "fixed" as const, value: "4.9wat", unit: "GHz" };
  const view = render(
    <ControlFields controls={[frequency]} drafts={{ frequency: invalid }} onChange={() => {}} />,
  );
  const input = screen.getByLabelText("Frequency");
  fireEvent.blur(input);
  expect(screen.getByRole("alert")).toHaveTextContent("未知或不兼容的单位");
  view.rerender(
    <ControlFields
      controls={[frequency]}
      drafts={{ frequency: { ...invalid, value: "4.8" } }}
      onChange={() => {}}
    />,
  );
  expect(screen.getByLabelText("Frequency")).toBe(input);
  expect(input).toHaveValue("4.8");
  expect(screen.queryByRole("alert")).toBeNull();
  // A different recovered plan with identical raw text must be validated afresh.
  view.rerender(
    <ControlFields
      controls={[frequency]}
      drafts={{ frequency: { ...invalid } }}
      onChange={() => {}}
    />,
  );
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent.blur(input);
  expect(screen.getByRole("alert")).toHaveTextContent("未知或不兼容的单位");
});
it("documents compact suffixes for whitespace-separated scan lists", () => {
  render(<DraftForm />);
  fireEvent.change(screen.getByLabelText("Frequency 取值来源"), { target: { value: "values" } });
  expect(screen.getByText(/单位后缀紧接数值，不加空格/)).toBeVisible();
  expect(() =>
    controlEdits({ frequency: { mode: "values", values: "1 GHz, 500 MHz", unit: "MHz" } }),
  ).toThrow("有限数值");
});
