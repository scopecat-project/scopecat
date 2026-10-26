// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { navigate } from "../../lib/navigation";
import type { NotebookJourney } from "../application/DesktopSession";
import { NotebookJourneyPanel } from "./NotebookJourneyPanel";

type PrepareNotebook = NonNullable<Window["pywebview"]>["api"]["prepare_notebook_journey"];

const course = (_topic?: string) => screen.getByRole("combobox", { name: "课程" });
const chooseCourse = (topic: string) => fireEvent.change(course(), { target: { value: topic } });

function savedStatus(journey: NotebookJourney | null) {
  return { state: journey ? (journey.ready ? "ready" : "retryable") : "not_started", journey };
}

function show(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={client}>
      <NotebookJourneyPanel reachable />
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  delete window.pywebview;
  window.history.replaceState(null, "", "/");
});
it("prepares without forms and preserves the saved path when opening the editor fails", async () => {
  const journey = {
    directory: "/authors/lesson",
    notebook: "/authors/lesson/notebooks/parameters.ipynb",
    python: "/authors/lesson/.venv/bin/python",
    ready: true,
  };
  const status = vi.fn().mockResolvedValue(savedStatus(null));
  const prepare = vi.fn().mockImplementation(async () => {
    status.mockResolvedValue(savedStatus(journey));
    return journey;
  });
  const open = vi.fn().mockRejectedValue(new Error("Open the Notebook manually"));
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: {
      api: {
        notebook_journey: status,
        prepare_notebook_journey: prepare,
        open_lesson_notebook: open,
      },
    },
  });
  show();
  const start = await screen.findByRole("button", { name: "开始参数与扫描 Notebook" });
  await vi.waitFor(() => expect(start).toBeEnabled());
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(start);
  expect(await screen.findByRole("alert")).toHaveTextContent("Open the Notebook manually");
  expect(screen.getByText(journey.notebook)).toBeVisible();
  expect(screen.getByRole("link", { name: "在数据页查找已保存结果" })).toHaveAttribute(
    "href",
    "#history",
  );
  expect(prepare).toHaveBeenCalledWith(undefined, "parameters");
  expect(open).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "继续参数与扫描 Notebook" })).toBeEnabled();
});
it("keeps desktop preparation distinct from browser-only Help", () => {
  show();
  expect(screen.getByText(/请在 Scopecat 桌面应用中打开帮助/)).toBeVisible();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  expect(screen.getAllByRole("option")).toHaveLength(8);
  chooseCourse("groups");
  expect(course()).toHaveValue("groups");
});
it.each([
  "procedure",
  "groups",
  "refresh",
  "compute",
  "calibration",
  "joint-calibration",
  "task-calibration",
])(
  "selects %s without borrowing the parameters receipt and locks selection during preparation",
  async (topic) => {
    const parameters = {
      directory: "/parameters",
      notebook: "/parameters/notebooks/parameters.ipynb",
      python: "/parameters/.venv/bin/python",
      ready: true,
    };
    const groups = {
      directory: "/groups",
      notebook: "/groups/notebooks/groups.ipynb",
      python: "/groups/.venv/bin/python",
      ready: true,
    };
    let finish!: (value: typeof groups) => void;
    const status = vi
      .fn()
      .mockImplementation(async (selected) =>
        savedStatus(selected === "parameters" ? parameters : null),
      );
    const prepare = vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const open = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(window, "pywebview", {
      configurable: true,
      value: {
        api: {
          notebook_journey: status,
          prepare_notebook_journey: prepare,
          open_lesson_notebook: open,
        },
      },
    });
    show();
    expect(await screen.findByRole("button", { name: "继续参数与扫描 Notebook" })).toBeEnabled();
    chooseCourse(topic);
    const start = await screen.findByRole("button", { name: /^开始.+ Notebook$/ });
    await vi.waitFor(() => expect(start).toBeEnabled());
    expect(screen.queryByText(parameters.notebook)).not.toBeInTheDocument();
    fireEvent.click(start);
    await vi.waitFor(() => expect(course(topic)).toBeDisabled());
    await vi.waitFor(() => expect(prepare).toHaveBeenCalledWith(undefined, topic));
    status.mockImplementation(async (selected) =>
      savedStatus(selected === "parameters" ? parameters : groups),
    );
    finish(groups);
    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: /^继续.+ Notebook$/ })).toBeEnabled(),
    );
    await vi.waitFor(() => expect(open).toHaveBeenCalledWith(topic));
    await vi.waitFor(() => expect(course(topic)).toBeEnabled());
    chooseCourse("parameters");
    expect(await screen.findByRole("button", { name: "继续参数与扫描 Notebook" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "继续参数与扫描 Notebook" })).toBeEnabled();
    expect(prepare).toHaveBeenCalledTimes(1);
  },
);

it("returns to the selected course and opens its exact Settings folder without preparing again", async () => {
  const journey = {
    directory: "/authors/notes & experiments",
    notebook: "/authors/notes & experiments/notebooks/groups.ipynb",
    python: "/authors/notes & experiments/.venv/bin/python",
    ready: true,
  };
  const status = vi.fn().mockResolvedValue(savedStatus(journey));
  const prepare = vi.fn();
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: { api: { notebook_journey: status, prepare_notebook_journey: prepare } },
  });
  const first = show();
  chooseCourse("groups");
  expect(await screen.findByRole("button", { name: "继续分组分析与历史 Notebook" })).toBeEnabled();
  expect(screen.queryByText(journey.notebook)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "在设置中管理此代码目录" }));
  expect(new URL(window.location.href).searchParams.get("source")).toBe(journey.directory);
  expect(window.location.hash).toBe("#settings");
  first.unmount();
  // A newly mounted Help reads the page selection, not a component-local default.
  show();
  expect(course()).toHaveValue("groups");
  expect(await screen.findByRole("button", { name: "继续分组分析与历史 Notebook" })).toBeEnabled();
  expect(status).toHaveBeenLastCalledWith("groups");
  expect(prepare).not.toHaveBeenCalled();
});

it("shows unfinished preparation without claiming the folder is ready", async () => {
  window.history.replaceState(null, "", "/?lesson=unknown#help");
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: {
      api: {
        notebook_journey: vi.fn().mockResolvedValue(
          savedStatus({
            directory: "/authors/unfinished",
            notebook: "/authors/unfinished/notebooks/parameters.ipynb",
            python: "/authors/unfinished/.venv/bin/python",
            ready: false,
          }),
        ),
      },
    },
  });
  show();
  expect(await screen.findByText("准备未完成")).toBeVisible();
  expect(screen.queryByRole("link", { name: "在设置中管理此代码目录" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试准备" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: /选择其他保存位置/ })).not.toBeInTheDocument();
  expect(course()).toHaveValue("parameters");
});

function goToCourse(topic: string, via: "navigate" | "popstate") {
  act(() => {
    const url = `/?lesson=${topic}#help`;
    if (via === "navigate") navigate(url);
    else {
      window.history.replaceState(null, "", url);
      window.dispatchEvent(new PopStateEvent("popstate"));
    }
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

const parametersJourney = {
  directory: "/parameters",
  notebook: "/parameters/notebooks/parameters.ipynb",
  python: "/parameters/.venv/bin/python",
  ready: true,
};

it.each(["navigate", "popstate"] as const)(
  "keeps delayed preparation in its requested course after %s",
  async (via) => {
    const pending = deferred<typeof parametersJourney>();
    const open = vi.fn();
    const prepare = vi.fn().mockReturnValue(pending.promise);
    const status = vi.fn().mockResolvedValue(savedStatus(null));
    Object.defineProperty(window, "pywebview", {
      configurable: true,
      value: {
        api: {
          notebook_journey: status,
          prepare_notebook_journey: prepare,
          open_lesson_notebook: open,
        },
      },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    show(client);
    const start = await screen.findByRole("button", { name: "开始参数与扫描 Notebook" });
    await vi.waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);
    await vi.waitFor(() => expect(prepare).toHaveBeenCalledWith(undefined, "parameters"));
    goToCourse("groups", via);
    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: "开始分组分析与历史 Notebook" })).toBeEnabled(),
    );
    expect(screen.queryByText(/Preparing code and Python/)).not.toBeInTheDocument();
    await act(async () => pending.resolve(parametersJourney));
    await vi.waitFor(() =>
      expect(client.getQueryData(["notebook-journey", "parameters"])).toEqual(
        savedStatus(parametersJourney),
      ),
    );
    expect(client.getQueryData(["notebook-journey", "groups"])).toEqual(savedStatus(null));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["notebook-journey", "parameters"] });
    expect(open).not.toHaveBeenCalled();
    expect(screen.queryByText(parametersJourney.notebook)).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  },
);

it.each(["prepare", "choose", "open"] as const)(
  "does not show a delayed %s error in another course",
  async (operation) => {
    const pending = deferred<never>();
    const bridge = {
      notebook_journey: vi.fn().mockResolvedValue(savedStatus(null)),
      prepare_notebook_journey: vi.fn<PrepareNotebook>().mockResolvedValue(parametersJourney),
      choose_directory: vi.fn().mockReturnValue(pending.promise),
      open_lesson_notebook: vi.fn().mockReturnValue(pending.promise),
    };
    if (operation === "prepare") bridge.prepare_notebook_journey.mockReturnValue(pending.promise);
    Object.defineProperty(window, "pywebview", { configurable: true, value: { api: bridge } });
    show();
    const start = await screen.findByRole("button", { name: "开始参数与扫描 Notebook" });
    await vi.waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(
      operation === "choose" ? screen.getByRole("button", { name: /选择其他保存位置/ }) : start,
    );
    const requested =
      operation === "open"
        ? bridge.open_lesson_notebook
        : operation === "choose"
          ? bridge.choose_directory
          : bridge.prepare_notebook_journey;
    await vi.waitFor(() => expect(requested).toHaveBeenCalled());
    goToCourse("groups", "popstate");
    await act(async () => pending.reject(new Error("parameters-only failure")));
    await vi.waitFor(() =>
      expect(screen.getByRole("button", { name: "开始分组分析与历史 Notebook" })).toBeEnabled(),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  },
);

it("discards a delayed folder choice after URL navigation", async () => {
  const pending = deferred<string>();
  const prepare = vi.fn().mockResolvedValue(parametersJourney);
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: {
      api: {
        notebook_journey: vi.fn().mockResolvedValue(savedStatus(null)),
        choose_directory: vi.fn().mockReturnValue(pending.promise),
        prepare_notebook_journey: prepare,
        open_lesson_notebook: vi.fn(),
      },
    },
  });
  show();
  fireEvent.click(await screen.findByRole("button", { name: /选择其他保存位置/ }));
  goToCourse("groups", "navigate");
  await act(async () => pending.resolve("/parameters chosen parent"));
  const start = await screen.findByRole("button", { name: "开始分组分析与历史 Notebook" });
  await vi.waitFor(() => expect(start).toBeEnabled());
  expect(screen.queryByText(/parameters chosen parent/)).not.toBeInTheDocument();
  fireEvent.click(start);
  await vi.waitFor(() => expect(prepare).toHaveBeenCalledWith(undefined, "groups"));
});

it.each([false, true])(
  "isolates editor success when navigation precedes completion: %s",
  async (navigateFirst) => {
    const pending = deferred<void>();
    const open = vi.fn().mockReturnValue(pending.promise);
    Object.defineProperty(window, "pywebview", {
      configurable: true,
      value: {
        api: {
          notebook_journey: vi.fn().mockResolvedValue(savedStatus(parametersJourney)),
          prepare_notebook_journey: vi.fn<PrepareNotebook>().mockResolvedValue(parametersJourney),
          open_lesson_notebook: open,
        },
      },
    });
    show();
    fireEvent.click(await screen.findByRole("button", { name: "继续参数与扫描 Notebook" }));
    await vi.waitFor(() => expect(open).toHaveBeenCalledWith("parameters"));
    if (navigateFirst) goToCourse("groups", "popstate");
    await act(async () => pending.resolve());
    await vi.waitFor(() =>
      expect(screen.queryAllByText(/已请求打开编辑器/)).toHaveLength(navigateFirst ? 0 : 1),
    );
    if (!navigateFirst) goToCourse("groups", "navigate");
    expect(screen.queryByText(/已请求打开编辑器/)).not.toBeInTheDocument();
    expect(open).toHaveBeenCalledTimes(1);
  },
);

it("observes another window preparing, failing, retrying and completing without opening its editor", async () => {
  const pending = deferred<typeof parametersJourney>();
  const retry = deferred<typeof parametersJourney>();
  let remote = { state: "not_started", journey: null as NotebookJourney | null };
  const status = vi.fn().mockImplementation(async () => remote);
  const prepare = vi
    .fn()
    .mockImplementationOnce(() => {
      remote = { state: "preparing", journey: { ...parametersJourney, ready: false } };
      return pending.promise;
    })
    .mockImplementationOnce(() => {
      remote = { state: "preparing", journey: { ...parametersJourney, ready: false } };
      return retry.promise;
    });
  const open = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: {
      api: {
        notebook_journey: status,
        prepare_notebook_journey: prepare,
        open_lesson_notebook: open,
      },
    },
  });
  const first = within(show().container);
  const second = within(show().container);
  const start = await first.findByRole("button", { name: "开始参数与扫描 Notebook" });
  await vi.waitFor(() => expect(start).toBeEnabled());
  fireEvent.click(start);
  expect(
    await second.findByRole("button", { name: "正在准备 Notebook…" }, { timeout: 3_000 }),
  ).toBeDisabled();
  expect(second.queryByText(/准备未完成/)).not.toBeInTheDocument();
  remote = { state: "retryable", journey: { ...parametersJourney, ready: false } };
  await act(async () => pending.reject(new Error("dependency unavailable")));
  const retryButton = await second.findByRole("button", { name: "重试准备" }, { timeout: 3_000 });
  expect(retryButton).toBeEnabled();
  fireEvent.click(retryButton);
  expect(
    await first.findByRole("button", { name: "正在准备 Notebook…" }, { timeout: 3_000 }),
  ).toBeDisabled();
  remote = { state: "ready", journey: parametersJourney };
  await act(async () => retry.resolve(parametersJourney));
  expect(
    await first.findByRole("button", { name: "继续参数与扫描 Notebook" }, { timeout: 3_000 }),
  ).toBeEnabled();
  expect(await second.findByRole("button", { name: "继续参数与扫描 Notebook" })).toBeEnabled();
  expect(first.queryByRole("alert")).not.toBeInTheDocument();
  expect(open).toHaveBeenCalledTimes(1);
});

it("explicitly repairs the retained course and explains kernel closure and preserved edits", async () => {
  const prepare = vi.fn().mockResolvedValue(parametersJourney);
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: {
      api: {
        notebook_journey: vi.fn().mockResolvedValue(savedStatus(parametersJourney)),
        prepare_notebook_journey: prepare,
        open_lesson_notebook: vi.fn().mockResolvedValue(undefined),
      },
    },
  });
  show();
  fireEvent.click(await screen.findByText("修复 Notebook 环境"));
  expect(screen.getByText(/关闭此目录的 Notebook 和 Python 终端/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "修复环境并打开 Notebook" }));
  await vi.waitFor(() => expect(prepare).toHaveBeenCalledWith(undefined, "parameters", true));
});

it("still shows a Continue failure when the saved receipt was ready", async () => {
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: {
      api: {
        notebook_journey: vi.fn().mockResolvedValue(savedStatus(parametersJourney)),
        prepare_notebook_journey: vi.fn().mockRejectedValue(new Error("Local Python is missing")),
      },
    },
  });
  show();
  fireEvent.click(await screen.findByRole("button", { name: "继续参数与扫描 Notebook" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Local Python is missing");
});

it("browses grouped goals without preparing or opening a course", async () => {
  const status = vi
    .fn()
    .mockImplementation(async (topic) =>
      savedStatus(topic === "parameters" ? parametersJourney : null),
    );
  const prepare = vi.fn();
  const open = vi.fn();
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: {
      api: {
        notebook_journey: status,
        prepare_notebook_journey: prepare,
        open_lesson_notebook: open,
      },
    },
  });
  show();
  for (const name of ["基础", "可选", "高级"]) {
    expect(screen.getByRole("group", { name })).toBeInTheDocument();
  }
  expect(await screen.findByText("可以继续")).toBeVisible();
  expect(course("parameters")).toHaveAccessibleDescription(
    "运行、查看结果、编辑 Python，再次运行。",
  );
  chooseCourse("task-calibration");
  expect(await screen.findByText("尚未准备")).toBeVisible();
  expect(course()).toHaveValue("task-calibration");
  expect(screen.queryByText(parametersJourney.directory)).not.toBeInTheDocument();
  expect(screen.queryByText(/does not mark it complete/)).toBeNull();
  expect(prepare).not.toHaveBeenCalled();
  expect(open).not.toHaveBeenCalled();
});
