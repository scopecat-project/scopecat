// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ApplicationSettings } from "./ApplicationSettings";

vi.mock("../instruments/device-api", () => ({
  getDevices: async () => ({ items: [] }),
}));
afterEach(() => {
  cleanup();
  delete window.pywebview;
});

it("creates an example from a chosen parent and keeps cancelled picks harmless", async () => {
  const choose = vi.fn().mockResolvedValueOnce(null).mockResolvedValueOnce("/Users/test/Science");
  const create = vi.fn().mockResolvedValue("/Users/test/Science/first experiment");
  const register = vi.fn();
  const status = vi.fn();
  const backup = vi.fn().mockRejectedValueOnce(new Error("后台仍有工作"));
  const restart = vi.fn();
  window.pywebview = {
    api: {
      exit_status: vi.fn(),
      notebook_journey: vi.fn(),
      prepare_notebook_journey: vi.fn(),
      open_lesson_notebook: vi.fn(),
      open_run_window: vi.fn(),
      set_window_title: vi.fn(),
      open_capture: vi.fn(),
      save_configuration: vi.fn(),
      save_configuration_source: vi.fn(),
      save_capture: vi.fn(),
      export_run: vi.fn(),
      save_captured_artifact: vi.fn(),
      status: status.mockResolvedValue({
        home: "/app",
        sources: [],
        installation: {
          python: "/app/python",
          static_dir: "/app/gui",
          environment: {},
          adapter_identity: null,
        },
      }),
      choose_directory: choose,
      create_source: create,
      register_source: register,
      select_source_environment: vi.fn(),
      backup_application: backup,
      restart,
      retry: vi.fn(),
      exit: vi.fn(),
      request_exit: vi.fn(),
      wait_for_idle: vi.fn(),
      prepare_author_environment: vi.fn(),
      create_author_environment: vi.fn(),
    },
  };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ApplicationSettings />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "备份应用数据…" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("后台仍有工作");
  expect(backup).toHaveBeenCalledTimes(1);
  expect(restart).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "备份应用数据…" }));
  await waitFor(() => expect(backup).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByRole("button", { name: "备份应用数据…" })).toBeEnabled());
  expect(status).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "新建代码目录" }));
  const submit = screen.getByRole("button", { name: "创建目录并准备 Python" });
  fireEvent.click(screen.getByRole("button", { name: "选择保存位置…" }));
  await waitFor(() => expect(choose).toHaveBeenCalledTimes(1));
  expect(submit).toBeDisabled();
  expect(create).not.toHaveBeenCalled();
  expect(register).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByRole("button", { name: "选择保存位置…" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "选择保存位置…" }));
  expect(await screen.findByText("/Users/test/Science")).toBeVisible();
  fireEvent.change(screen.getByLabelText("新目录名称"), {
    target: { value: "first experiment" },
  });
  fireEvent.click(submit);
  await waitFor(() =>
    expect(create).toHaveBeenCalledWith("/Users/test/Science", "first experiment"),
  );
});

it("keeps author dependencies independent and uses native application updates", async () => {
  const installation = {
    python: "/current/python",
    static_dir: "/current/gui",
    environment: { python: "current Python" },
    adapter_identity: "current-capability",
  };
  const state = {
    home: "/application",
    state: "running",
    detail: null,
    installation,
    sources: [
      {
        directory: "/authors",
        python: "/authors/.venv/bin/python",
        execution_python: "/execution/bin/python",
      },
    ],
  };
  const dependencies = vi.fn().mockResolvedValue("Dependencies ready");
  const selectEnvironment = vi.fn();
  const client = vi.fn().mockResolvedValue("/authors/.venv/bin/python");
  const restart = vi.fn();
  window.pywebview = {
    api: {
      exit_status: vi.fn(),
      notebook_journey: vi.fn(),
      prepare_notebook_journey: vi.fn(),
      open_lesson_notebook: vi.fn(),
      open_run_window: vi.fn(),
      set_window_title: vi.fn(),
      open_capture: vi.fn(),
      save_configuration: vi.fn(),
      save_configuration_source: vi.fn(),
      save_capture: vi.fn(),
      export_run: vi.fn(),
      save_captured_artifact: vi.fn(),
      request_exit: vi.fn(),
      wait_for_idle: vi.fn(),
      status: vi.fn().mockImplementation(async () => ({ ...state })),
      register_source: vi.fn(),
      select_source_environment: selectEnvironment,
      choose_directory: vi.fn(),
      create_source: vi.fn(),
      prepare_author_environment: dependencies,
      create_author_environment: client,
      restart,
      backup_application: vi.fn(),
      retry: vi.fn(),
      exit: vi.fn(),
    },
  };
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ApplicationSettings />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("/current/python")).not.toBeVisible();
  fireEvent.click(screen.getByText("技术诊断"));
  expect(screen.getByText("/current/python")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Stop and apply prepared update" })).toBeNull();
  fireEvent.change(screen.getByLabelText("作者目录"), { target: { value: "/authors" } });
  fireEvent.click(screen.getByRole("button", { name: "按 pyproject.toml 准备执行环境" }));
  expect(await screen.findByText("Dependencies ready")).toBeVisible();
  expect(dependencies).toHaveBeenCalledWith("/authors");
  expect(restart).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("查看或修改 Python 环境"));
  fireEvent.change(screen.getByLabelText("执行 Python"), {
    target: { value: "/custom/python" },
  });
  fireEvent.click(screen.getByRole("button", { name: "使用此执行 Python" }));
  await waitFor(() => expect(selectEnvironment).toHaveBeenCalledWith("/authors", "/custom/python"));
  expect(dependencies).toHaveBeenCalledTimes(1);
  expect(restart).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "重建本地 Python 环境" }));
  expect(await screen.findByText("/authors/.venv/bin/python")).toBeVisible();
  expect(client).toHaveBeenCalledWith("/authors", true);
  expect(screen.queryByLabelText("Delivery directory")).toBeNull();
  expect(screen.getByText(/安装新版本/)).toBeVisible();
});
