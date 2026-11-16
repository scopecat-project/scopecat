// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";

const loader = vi.hoisted(() => ({ load: vi.fn() }));
vi.mock("./chart-runtime-loader", () => ({ loadChartRuntime: loader.load }));
let EChart: typeof import("./EChart").EChart;
beforeEach(async () => {
  vi.resetModules();
  ({ EChart } = await import("./EChart"));
});

function Harness() {
  const [draft, setDraft] = useState("original draft");
  return (
    <>
      <input aria-label="save draft" value={draft} onChange={(e) => setDraft(e.target.value)} />
      <button onClick={() => setDraft("saved original")}>save original</button>
      <EChart
        ariaLabel="trace"
        height={240}
        option={{ series: [] }}
        pointCount={201}
        seriesCount={1}
        seriesLabels={["signal"]}
      />
    </>
  );
}
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  loader.load.mockReset();
});

it("contains import rejection, keeps outer owner and retries the loader only on explicit action", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  loader.load.mockRejectedValueOnce(new TypeError("module unavailable"));
  loader.load.mockResolvedValueOnce({ default: () => <svg data-testid="drawn" /> });
  render(<Harness />);
  expect(await screen.findByRole("alert")).toHaveTextContent("图表暂时无法显示");
  fireEvent.change(screen.getByLabelText("save draft"), { target: { value: "retained edits" } });
  expect(loader.load).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "重试图表" }));
  expect(await screen.findByTestId("drawn")).toBeVisible();
  expect(loader.load).toHaveBeenCalledTimes(2);
  expect(screen.getByLabelText("save draft")).toHaveValue("retained edits");
  fireEvent.click(screen.getByRole("button", { name: "save original" }));
  expect(screen.getByLabelText("save draft")).toHaveValue("saved original");
  expect(loader.load).toHaveBeenCalledTimes(2);
});

it("keeps a repeated rejection visible without retry loops or removing save", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  loader.load.mockRejectedValue(new TypeError("still unavailable"));
  render(<Harness />);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "重试图表" }));
  await waitFor(() => expect(loader.load).toHaveBeenCalledTimes(2));
  expect(await screen.findByRole("alert")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "save original" }));
  expect(screen.getByLabelText("save draft")).toHaveValue("saved original");
  expect(loader.load).toHaveBeenCalledTimes(2);
});

it("distinguishes delayed loading from rejection and keeps the same lazy type on parent updates", async () => {
  let resolve!: (value: { default: () => React.JSX.Element }) => void;
  loader.load.mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  render(<Harness />);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByRole("status")).toHaveTextContent("正在加载图表");
  fireEvent.change(screen.getByLabelText("save draft"), { target: { value: "waiting edit" } });
  expect(loader.load).toHaveBeenCalledTimes(1);
  resolve({ default: () => <svg data-testid="drawn" /> });
  expect(await screen.findByTestId("drawn")).toBeVisible();
  expect(screen.getByLabelText("save draft")).toHaveValue("waiting edit");
});
