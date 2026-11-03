// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DevicePythonGuide, networkSweepExamples } from "./DevicePythonGuide";
import type { InstrumentDescription } from "../../api-contract";

afterEach(cleanup);
const description: InstrumentDescription = {
  instrument_id: "logical-alias",
  implementation_id: "scopecat.virtual.vna",
  implementation_version: "1",
  interfaces: [{ id: "scopecat.network_sweep/v1" }],
};
it("offers selected registered identity and mount without device/network commands", () => {
  const fetch = vi.spyOn(globalThis, "fetch");
  render(
    <DevicePythonGuide
      deviceId={'device-"one"'}
      description={{
        ...description,
        interface_mounts: [
          { interface_id: "scopecat.network_sweep/v1", component_path: ["channels", "2"] },
        ],
      }}
    />,
  );
  fireEvent.click(screen.getByText("在 Python 中使用此设备"));
  const code = screen.getByRole("textbox", { name: "即时调试 Python channels/2" });
  fireEvent.focus(code);
  expect(code).toHaveValue(networkSweepExamples('device-"one"', ["channels", "2"]).live);
  expect(code).toHaveAttribute("readonly");
  expect(screen.getByRole("textbox", { name: "实验声明 Python channels/2" })).toHaveValue(
    networkSweepExamples('device-"one"', ["channels", "2"]).experiment,
  );
  expect(fetch).not.toHaveBeenCalled();
  fetch.mockRestore();
});
it("does not guess a client for an unknown interface version", () => {
  render(
    <DevicePythonGuide
      deviceId="device"
      description={{ ...description, interfaces: [{ id: "scopecat.network_sweep/v999" }] }}
    />,
  );
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  expect(screen.getByText(/尚无经过验证/)).toBeInTheDocument();
});
