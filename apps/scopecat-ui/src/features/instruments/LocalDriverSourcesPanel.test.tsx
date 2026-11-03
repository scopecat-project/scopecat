// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../../api-client";
import { readOperatorSettings } from "../application/OperatorSettings";
import { getLocalDriverSources, manageLocalDriverSource } from "./device-api";
import { LocalDriverSourcesPanel } from "./LocalDriverSourcesPanel";

vi.mock("./device-api", () => ({
  getLocalDriverSources: vi.fn(),
  manageLocalDriverSource: vi.fn(),
}));
vi.mock("../application/OperatorSettings", () => ({
  readOperatorSettings: vi.fn(),
  OperatorSettings: () => <p>保存操作署名</p>,
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 1, operator: "operator" });
  vi.mocked(getLocalDriverSources).mockResolvedValue({
    unmanaged_backend: false,
    collection: { provider_id: "scopecat.local-sources", sources: [], operation_id: null },
    errors: {},
  });
  vi.mocked(manageLocalDriverSource).mockResolvedValue({
    operation_id: "updated",
    provider_id: "scopecat.local-sources",
    sources: [],
  });
});
afterEach(cleanup);
function show(sessionBusy = false) {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <LocalDriverSourcesPanel daemonUnavailable={false} sessionBusy={sessionBusy} />
    </QueryClientProvider>,
  );
}

it("offers explicit simulation without requiring a known source directory", async () => {
  show();
  const button = await screen.findByRole("button", { name: "启用模拟设备体验（无硬件）" });
  await waitFor(() => expect(button).toBeEnabled());
  expect(manageLocalDriverSource).not.toHaveBeenCalled();
  fireEvent.click(button);
  await screen.findByRole("status");
  expect(manageLocalDriverSource).toHaveBeenCalledWith(
    expect.objectContaining({
      builtin: "simulation",
      action: "capture",
      actor: "operator",
      expected_previous: null,
    }),
  );
  expect(screen.getByText(/只产生合成响应/)).toBeVisible();
});

it("retains the exact uncertain operation and blocks conflicting new actions", async () => {
  vi.mocked(manageLocalDriverSource).mockRejectedValueOnce(new Error("connection lost"));
  show();
  const button = await screen.findByRole("button", { name: "添加内置公共驱动" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  const retry = await screen.findByRole("button", { name: "核对并重试原操作" });
  expect(button).toBeDisabled();
  fireEvent.click(retry);
  await waitFor(() => expect(manageLocalDriverSource).toHaveBeenCalledTimes(2));
  expect(vi.mocked(manageLocalDriverSource).mock.calls[1]).toEqual(
    vi.mocked(manageLocalDriverSource).mock.calls[0],
  );
});

it("shows a rejected source conflict and leaves the local path editable", async () => {
  vi.mocked(manageLocalDriverSource).mockRejectedValueOnce(new ApiError("driver ID conflict", 409));
  show();
  const input = await screen.findByLabelText("本地驱动项目目录");
  await waitFor(() => expect(input).toBeEnabled());
  fireEvent.change(input, { target: { value: "/lab/drivers" } });
  fireEvent.click(screen.getByRole("button", { name: "添加本地来源" }));
  await screen.findByText("driver ID conflict");
  await waitFor(() => expect(input).toBeEnabled());
  expect(input).toHaveValue("/lab/drivers");
});

it("blocks source mutation during a manual session", async () => {
  show(true);
  expect(await screen.findByRole("button", { name: "启用模拟设备体验（无硬件）" })).toBeDisabled();
  expect(manageLocalDriverSource).not.toHaveBeenCalled();
});
