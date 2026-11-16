// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { CurrentMeasurementResult } from "./CurrentMeasurement";
import { MeasurementWorkbench } from "./MeasurementWorkbench";

vi.mock("./BoundedMeasurement", () => ({ BoundedMeasurement: () => <div>preview mounted</div> }));
afterEach(cleanup);
const results: CurrentMeasurementResult[] = (["verified", "unverified", "invalid"] as const).map(
  (receipt_validation) => ({
    receipt_validation,
    validation_reason: `Reason for ${receipt_validation}`,
    target: {
      interfaceId: "scope",
      componentPath: [],
      acquisition: { id: "capture", results: [] },
    },
    selection: {
      session_id: receipt_validation,
      instrument_id: "scope",
      // Command IDs alone must not conflate originals from different sessions.
      command_id: "same-command",
      title: receipt_validation,
      series_id: "",
      line_id: "",
      line_label: "",
      note: "",
    },
    receivedAt: "2026-01-01T00:00:00Z",
  }),
);
function Harness() {
  const [current, setCurrent] = useState(results[0]);
  return (
    <MeasurementWorkbench
      collapsed={false}
      onCollapsedChange={vi.fn()}
      connected
      disabled={false}
      collectResults={{}}
      recent={results}
      current={current}
      staged={false}
      pending={results.map((item) => ({
        selection: item.selection,
        acquisition: item.target.acquisition,
      }))}
      onCollect={vi.fn()}
      onSelect={setCurrent}
      onSaveAttempt={vi.fn()}
      onSaved={vi.fn()}
    />
  );
}
it.each([
  ["unverified", "未验证原件（验证未完成）"],
  ["invalid", "无效原件（契约不匹配）"],
])(
  "keeps %s diagnostic originals non-plottable when selected from the compact entry",
  (state, label) => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Harness />
      </QueryClientProvider>,
    );
    expect(screen.getByText("preview mounted")).toBeVisible();
    const select = screen.getByRole("combobox", { name: "选择近期结果" });
    const option = within(select).getByRole<HTMLOptionElement>("option", {
      name: new RegExp(`\\. ${state} ·`),
    });
    fireEvent.change(select, { target: { value: option.value } });
    expect(select).toHaveValue(option.value);
    expect(screen.getByText(label)).toBeVisible();
    expect(screen.getByText(`Reason for ${state}`)).toBeVisible();
    expect(screen.queryByText("preview mounted")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存所选结果" })).toBeEnabled();
    const recent = screen.getByRole("complementary", { name: "近期结果" });
    expect(within(recent).getByTitle(state, { exact: true })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(within(recent).getByTitle("verified", { exact: true }));
    expect(select).toHaveValue(
      within(select).getByRole<HTMLOptionElement>("option", { name: /\. verified ·/ }).value,
    );
    expect(screen.getByText("preview mounted")).toBeVisible();
    expect(screen.queryByText(label)).not.toBeInTheDocument();
  },
);
