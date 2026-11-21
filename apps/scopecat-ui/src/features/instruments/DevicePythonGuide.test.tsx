// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DevicePythonGuide, networkSweepExamples } from "./DevicePythonGuide";
import type { InstrumentDescription } from "../../api-contract";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
Object.defineProperty(navigator, "clipboard", { configurable: true, get: () => undefined });
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
  const guide = screen.getByRole("region", { name: "在 Python 中使用此设备" });
  expect(guide.querySelector("details, summary")).toBeNull();
  expect(screen.queryByText(/查看或复制/)).not.toBeInTheDocument();
  const code = screen.getByRole("textbox", { name: "即时调试 Python channels/2" });
  fireEvent.focus(code);
  expect(code).toHaveValue(networkSweepExamples('device-"one"', ["channels", "2"]).live);
  expect(code).toBeVisible();
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
  expect(screen.getByText(/暂无内置 Python 示例/)).toBeInTheDocument();
});

it("keeps names display-only and safely encodes hostile labels in one comment line", () => {
  const name = 'same name\nprint("not code")\r#\u2028\u202e';
  const code = networkSweepExamples("stable-original", [], name).live;
  expect(code.split("\n").filter((line) => line.startsWith("# Device name"))).toHaveLength(1);
  expect(code).toContain('DEVICE_ID = "stable-original"');
  expect(code).toContain('\\nprint(\\"not code\\")\\r#\\u2028\\u202e');
  expect(code).not.toContain('\nprint("not code")');
  expect(networkSweepExamples("stable-original", [], "renamed").live).toContain(
    'DEVICE_ID = "stable-original"',
  );
  expect(networkSweepExamples("different-id", [], name).live).toContain(
    'DEVICE_ID = "different-id"',
  );
  expect(code).toContain("lab.devices.open(DEVICE_ID)");
});

it("copies the exact displayed example only on click, never executes it", async () => {
  let finish!: () => void;
  const write = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  vi.spyOn(navigator, "clipboard", "get").mockReturnValue({
    writeText: write,
  } as unknown as Clipboard);
  const fetch = vi.spyOn(globalThis, "fetch");
  render(
    <DevicePythonGuide
      deviceId="original-id"
      deviceLabel="Readout VNA"
      description={description}
    />,
  );
  expect(screen.getByText("设备名称：Readout VNA")).toBeVisible();
  expect(write).not.toHaveBeenCalled();
  const button = screen.getByRole("button", { name: "复制即时调试 Python 整机" });
  fireEvent.click(button);
  expect(write).toHaveBeenCalledExactlyOnceWith(
    networkSweepExamples("original-id", [], "Readout VNA").live,
  );
  expect(button).toBeDisabled();
  expect(screen.queryByText("示例已复制，未执行代码。")).toBeNull();
  await act(async () => finish());
  expect(screen.getByText("示例已复制，未执行代码。")).toBeVisible();
  expect(fetch).not.toHaveBeenCalled();
});

it.each([true, false])(
  "does not report stale copy completion after changing devices (success=%s)",
  async (success) => {
    let finish!: () => void;
    vi.spyOn(navigator, "clipboard", "get").mockReturnValue({
      writeText: () =>
        new Promise<void>((resolve, reject) => {
          finish = success ? resolve : () => reject(new Error("denied"));
        }),
    } as unknown as Clipboard);
    const { rerender } = render(
      <DevicePythonGuide deviceId="old-id" deviceLabel="Same name" description={description} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "复制即时调试 Python 整机" }));
    rerender(
      <DevicePythonGuide deviceId="new-id" deviceLabel="Same name" description={description} />,
    );
    await act(async () => finish());
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("textbox", { name: "即时调试 Python 整机" })).toHaveValue(
      networkSweepExamples("new-id", [], "Same name").live,
    );
  },
);

it.each(["missing", "denied"])("keeps full selectable code if clipboard is %s", async (failure) => {
  if (failure === "denied")
    vi.spyOn(navigator, "clipboard", "get").mockReturnValue({
      writeText: () => Promise.reject(new Error("denied")),
    } as unknown as Clipboard);
  render(<DevicePythonGuide deviceId="exact-id" description={description} />);
  fireEvent.click(screen.getByRole("button", { name: "复制即时调试 Python 整机" }));
  expect(await screen.findByText(/无法访问剪贴板/)).toBeVisible();
  const code = screen.getByRole<HTMLTextAreaElement>("textbox", { name: "即时调试 Python 整机" });
  fireEvent.focus(code);
  expect(code.value).toBe(networkSweepExamples("exact-id", []).live);
  expect(code.selectionEnd - code.selectionStart).toBe(code.value.length);
});

it("clears pending copy feedback on rename without changing the registered ID", async () => {
  let finish!: () => void;
  vi.spyOn(navigator, "clipboard", "get").mockReturnValue({
    writeText: () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  } as unknown as Clipboard);
  const { rerender } = render(
    <DevicePythonGuide deviceId="original" deviceLabel="Old name" description={description} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "复制即时调试 Python 整机" }));
  rerender(
    <DevicePythonGuide deviceId="original" deviceLabel="New name" description={description} />,
  );
  await act(async () => finish());
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.getByRole("textbox", { name: "即时调试 Python 整机" })).toHaveValue(
    networkSweepExamples("original", [], "New name").live,
  );
});

it("allows retry after clipboard failure and reports only the successful retry", async () => {
  const write = vi.fn().mockRejectedValueOnce(new Error("denied")).mockResolvedValue(undefined);
  vi.spyOn(navigator, "clipboard", "get").mockReturnValue({
    writeText: write,
  } as unknown as Clipboard);
  render(<DevicePythonGuide deviceId="exact-id" description={description} />);
  const button = screen.getByRole("button", { name: "复制即时调试 Python 整机" });
  fireEvent.click(button);
  await screen.findByText(/无法访问剪贴板/);
  fireEvent.click(button);
  expect(await screen.findByText("示例已复制，未执行代码。")).toBeVisible();
  expect(screen.queryByText(/无法访问剪贴板/)).toBeNull();
  expect(write).toHaveBeenCalledTimes(2);
  expect(write).toHaveBeenNthCalledWith(1, networkSweepExamples("exact-id", []).live);
  expect(write).toHaveBeenNthCalledWith(2, networkSweepExamples("exact-id", []).live);
});

it("guides explicit preparation-time binding and names the unsupported-demand fallback", () => {
  render(<DevicePythonGuide deviceId="registered-vna" description={description} />);
  expect(screen.getByText(/无需先创建具名 setup/)).toBeVisible();
  expect(screen.getByText(/明确选择登记设备、端点和生命周期策略/)).toBeVisible();
  expect(screen.getByText(/按准备诊断核对原因，再提供明确的 setup/)).toBeVisible();
  expect(screen.getByRole("link", { name: /Python 渐进绑定/ })).toHaveAttribute(
    "href",
    expect.stringContaining("progressive-instrument-bindings"),
  );
  expect(screen.getByRole("textbox", { name: "实验声明 Python 整机" })).toHaveValue(
    networkSweepExamples("registered-vna", []).experiment,
  );
});
