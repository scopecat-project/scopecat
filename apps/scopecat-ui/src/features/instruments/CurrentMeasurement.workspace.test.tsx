// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CurrentMeasurement, type CurrentMeasurementResult } from "./CurrentMeasurement";
vi.mock("./BoundedMeasurement", () => ({
  BoundedMeasurement: ({ workspace }: { workspace?: boolean }) => (
    <div data-testid="canvas" data-workspace={workspace} />
  ),
}));
afterEach(cleanup);
const result: CurrentMeasurementResult = {
  target: {
    interfaceId: "scopecat.network_sweep/v1",
    componentPath: [],
    acquisition: { id: "sweep", results: [] },
  },
  selection: {
    session_id: "old",
    instrument_id: "vna",
    command_id: "one",
    title: "Saved trace",
    line_id: "",
    line_label: "Line A",
    series_id: "",
    note: "",
  },
  receivedAt: "2026-11-15T12:00:00Z",
  savedRecordId: "saved-one",
  relationshipChanged: "设备设置已改变",
};
it("keeps scientific warnings visible while moving receipt identity and download into explicit details", async () => {
  render(<CurrentMeasurement result={result} staged sessionId="new" workspace />);
  expect(screen.getByTestId("canvas")).toHaveAttribute("data-workspace", "true");
  expect(screen.getByText(/结果来自此前的控制会话/)).toBeVisible();
  expect(screen.getByText(/设备设置已改变/)).toBeVisible();
  expect(screen.getByText(/有未发送的设置草稿/)).toBeVisible();
  expect(screen.queryByText(/scopecat.network_sweep/)).not.toBeInTheDocument();
  const trigger = screen.getByRole("button", { name: "结果详情" });
  fireEvent.click(trigger);
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/scopecat.network_sweep/)).toBeVisible();
  expect(within(dialog).getByRole("link", { name: /下载原始回执/ })).toHaveAttribute(
    "href",
    expect.stringContaining("saved-one/receipt"),
  );
  fireEvent.click(within(dialog).getByRole("button", { name: "关闭详情" }));
  expect(screen.getByTestId("canvas")).toBeVisible();
});
it("does not turn an unverified result into a plot when the workspace is compact", () => {
  render(
    <CurrentMeasurement
      result={{ ...result, receipt_validation: "unverified" }}
      staged={false}
      workspace
    />,
  );
  expect(screen.getByRole("alert")).toHaveTextContent("未验证原件");
  expect(screen.queryByTestId("canvas")).not.toBeInTheDocument();
});
