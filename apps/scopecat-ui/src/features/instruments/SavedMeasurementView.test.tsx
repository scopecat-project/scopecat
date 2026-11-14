// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "../../App";
import { SavedMeasurementLink } from "./SavedMeasurementView";
type DesktopAPI = NonNullable<Window["pywebview"]>["api"];

vi.mock("./DirectMeasurements", () => ({
  SavedCurve: ({
    record,
    readOnly,
    hideHeading,
  }: {
    record: { id: string };
    readOnly: boolean;
    hideHeading?: boolean;
  }) => (
    <div>
      {!hideHeading && <h4>Duplicate title</h4>}
      Saved receipt {record.id} {readOnly ? "read only" : "editable"}
    </div>
  ),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.pywebview;
  window.history.replaceState({}, "", "/");
});
function showApp() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <App />
    </QueryClientProvider>,
  );
}
it("opens the browser's single saved record in a new tab without replacing device control", () => {
  window.history.replaceState({}, "", "/?device-control=device-a");
  render(<SavedMeasurementLink recordId="a b" />);
  expect(screen.getByRole("link", { name: "查看已保存结果" })).toHaveAttribute(
    "href",
    "/?saved-measurement=a%20b",
  );
  expect(screen.getByRole("link")).toHaveAttribute("target", "_blank");
  expect(window.location.search).toBe("?device-control=device-a");
});
it("uses the native bridge and prevents duplicate pending opens", async () => {
  let finish!: () => void;
  const open = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  window.pywebview = { api: { open_saved_measurement_window: open } as unknown as DesktopAPI };
  render(<SavedMeasurementLink recordId="record-a" />);
  const button = screen.getByRole("button", { name: "查看已保存结果" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(open).toHaveBeenCalledExactlyOnceWith("record-a");
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  finish();
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  expect(open).toHaveBeenCalledTimes(2);
  finish();
  await waitFor(() => expect(button).toBeEnabled());
});
it.each([false, true])(
  "reports unavailable native bridge without a browser fallback (bridge present: %s)",
  (present) => {
    window.history.replaceState({}, "", "/?desktop=1&device-control=device-a");
    if (present) window.pywebview = { api: {} as DesktopAPI };
    render(<SavedMeasurementLink recordId="record-a" />);
    fireEvent.click(screen.getByRole("button", { name: "查看已保存结果" }));
    expect(screen.getByRole("alert")).toHaveTextContent("结果窗口尚不可用");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  },
);
it("surfaces native failures without navigating the controlling window", async () => {
  window.pywebview = {
    api: {
      open_saved_measurement_window: vi.fn().mockRejectedValue(new Error("host offline")),
    } as unknown as DesktopAPI,
  };
  render(<SavedMeasurementLink recordId="record-a" />);
  fireEvent.click(screen.getByRole("button", { name: "查看已保存结果" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("host offline");
  expect(window.location.pathname).toBe("/");
});
it("restores only the URL record on fresh mount and refresh, without workspace or history requests", async () => {
  const paths: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      paths.push(new URL(request.url).pathname);
      return Response.json({
        id: "record-a",
        selection: { title: "Persisted title", note: "Saved note" },
      });
    }),
  );
  window.history.replaceState({}, "", "/?saved-measurement=record-a");
  let view = showApp();
  expect(await screen.findByRole("heading", { name: "Persisted title" })).toBeVisible();
  expect(screen.getByRole("main", { name: "已保存测量结果" })).toHaveTextContent(
    "Saved receipt record-a read only",
  );
  expect(screen.getAllByRole("heading")).toHaveLength(1);
  expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: "查找当前页记录" })).not.toBeInTheDocument();
  view.unmount();
  view = showApp();
  expect(await screen.findByRole("heading", { name: "Persisted title" })).toBeVisible();
  expect(paths).toEqual([
    "/api/v1/direct-measurements/record-a",
    "/api/v1/direct-measurements/record-a",
  ]);
  view.unmount();
});
it("reports missing saved records without substituting history or live data", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ detail: "record not found" }, { status: 404 })),
  );
  window.history.replaceState({}, "", "/?saved-measurement=missing");
  showApp();
  expect(await screen.findByRole("alert")).toHaveTextContent("record not found");
  expect(screen.queryByText(/Saved receipt/)).not.toBeInTheDocument();
});
