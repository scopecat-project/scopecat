import { readOperatorSettings } from "../application/OperatorSettings";
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../../api-client";
import { DriverSourcePanel } from "./DriverSourcePanel";
import { getDriverSource, updateDriverSource } from "./device-api";

vi.mock("./device-api", () => ({
  getDriverSource: vi.fn(),
  updateDriverSource: vi.fn(),
}));

const selection = {
  request: {
    operation_id: "previous",
    source_root: "/lab/source",
    actor: "maintainer",
    expected_previous: null,
  },
  factory: "lab.drivers:build",
  python: "/execution/python",
  artifact_hash: "sha256:driver",
  code_revision: { content_hash: "sha256:source" },
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 1, operator: "local-operator" });
  vi.mocked(getDriverSource).mockResolvedValue({ active: selection });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show(sessionBusy = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <DriverSourcePanel daemonUnavailable={false} sessionBusy={sessionBusy} hasDrivers />
    </QueryClientProvider>,
  );
  return invalidate;
}

it("only activates on explicit submission and refreshes device and driver views", async () => {
  vi.mocked(updateDriverSource).mockImplementation(async (request) => ({ ...selection, request }));
  const invalidate = show();
  await screen.findByText("当前驱动源码：/lab/source");
  expect(updateDriverSource).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("驱动源码目录"), {
    target: { value: "/lab/new-source" },
  });
  fireEvent.click(screen.getByRole("button", { name: "从源码更新" }));
  await screen.findByRole("status");
  expect(updateDriverSource).toHaveBeenCalledWith({
    operation_id: expect.stringMatching(/^ui-driver-source-/),
    source_root: "/lab/new-source",
    expected_previous: "previous",
    actor: "local-operator",
  });
  for (const key of [
    "driver-source",
    "devices",
    "instruments",
    "instrument-drivers",
    "device-drivers",
  ]) {
    expect(invalidate).toHaveBeenCalledWith({ queryKey: [key] });
  }
});

it("keeps the selected source visible and retries an uncertain result with the same request", async () => {
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 1, operator: "Alice" });
  vi.mocked(updateDriverSource)
    .mockRejectedValueOnce(new ApiError("Connection lost"))
    .mockImplementation(async (request) => ({ ...selection, request }));
  show();
  await screen.findByText("当前驱动源码：/lab/source");
  const button = screen.getByRole("button", { name: "从源码更新" });
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent("Connection lost");
  expect(screen.getByText("当前驱动源码：/lab/source")).toBeVisible();
  await waitFor(() => expect(button).toBeEnabled());
  expect(updateDriverSource).toHaveBeenCalledTimes(1);
  expect(vi.mocked(updateDriverSource).mock.calls[0]![0].actor).toBe("Alice");
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 2, operator: "Bob" });
  fireEvent.click(button);
  await screen.findByRole("status");
  expect(vi.mocked(updateDriverSource).mock.calls[1]).toEqual(
    vi.mocked(updateDriverSource).mock.calls[0],
  );
});

it("uses the refreshed selection on an explicit retry after a rejected update", async () => {
  vi.mocked(updateDriverSource)
    .mockImplementationOnce(async () => {
      vi.mocked(getDriverSource).mockResolvedValue({
        active: { ...selection, request: { ...selection.request, operation_id: "other-update" } },
      });
      throw new ApiError("Source changed; inspect and retry", 409);
    })
    .mockImplementation(async (request) => ({ ...selection, request }));
  show();
  await screen.findByText("当前驱动源码：/lab/source");
  const button = screen.getByRole("button", { name: "从源码更新" });
  fireEvent.click(button);
  await screen.findByRole("alert");
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await screen.findByRole("status");
  expect(vi.mocked(updateDriverSource).mock.calls[1]?.[0].expected_previous).toBe("other-update");
});

it("requires the current manual session to be released", async () => {
  show(true);
  await screen.findByText("当前驱动源码：/lab/source");
  expect(screen.getByRole("button", { name: "从源码更新" })).toBeDisabled();
  expect(updateDriverSource).not.toHaveBeenCalled();
});

vi.mock("../application/OperatorSettings", () => ({
  OperatorSettings: ({ onSaved }: { onSaved: () => void }) => (
    <button
      onClick={() => {
        vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 1, operator: "Ada" });
        onSaved();
      }}
    >
      保存默认操作者
    </button>
  ),
  readOperatorSettings: vi.fn(async () => ({ revision: 1, operator: "local-operator" })),
}));

it("selects a source using the native picker without enabling it until submission", async () => {
  const choose = vi.fn().mockResolvedValueOnce("/chosen/source").mockResolvedValueOnce(null);
  vi.stubGlobal("pywebview", { api: { choose_directory: choose } });
  show();
  await screen.findByText("当前驱动源码：/lab/source");
  fireEvent.click(screen.getByRole("button", { name: "选择源码目录" }));
  await waitFor(() => expect(screen.getByLabelText("驱动源码目录")).toHaveValue("/chosen/source"));
  expect(updateDriverSource).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "选择源码目录" }));
  await waitFor(() => expect(choose).toHaveBeenCalledTimes(2));
  expect(screen.getByLabelText("驱动源码目录")).toHaveValue("/chosen/source");
});

it("keeps manual input without advertising a picker in browser mode", async () => {
  show();
  await screen.findByText("当前驱动源码：/lab/source");
  expect(screen.queryByRole("button", { name: "选择源码目录" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("驱动源码目录")).toBeEnabled();
});

it("does not submit an empty attribution or lose the chosen source", async () => {
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 0, operator: "" });
  show();
  await screen.findByText("当前驱动源码：/lab/source");
  fireEvent.click(screen.getByRole("button", { name: "从源码更新" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("启用驱动需要记录操作署名");
  expect(updateDriverSource).not.toHaveBeenCalled();
  expect(screen.getByLabelText("驱动源码目录")).toHaveValue("/lab/source");
});

it("returns from inline attribution settings with the source draft intact and requires explicit activation", async () => {
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 0, operator: "" });
  vi.mocked(updateDriverSource).mockImplementation(async (request) => ({ ...selection, request }));
  show();
  await screen.findByText("当前驱动源码：/lab/source");
  fireEvent.change(screen.getByLabelText("驱动源码目录"), { target: { value: "/new/project" } });
  fireEvent.click(screen.getByRole("button", { name: "从源码更新" }));
  fireEvent.click(await screen.findByRole("button", { name: "保存默认操作者" }));
  expect(screen.getByLabelText("驱动源码目录")).toHaveValue("/new/project");
  expect(updateDriverSource).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole("button", { name: "从源码更新" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "从源码更新" }));
  await screen.findByRole("status");
  expect(updateDriverSource).toHaveBeenCalledWith(
    expect.objectContaining({ source_root: "/new/project", actor: "Ada" }),
  );
});
