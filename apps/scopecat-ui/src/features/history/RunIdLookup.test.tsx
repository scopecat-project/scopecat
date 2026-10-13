// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../api-client";
import { getRun } from "../runs/run-api";
import type { ProjectRun } from "../../types";
import { RunIdLookup } from "./RunIdLookup";
vi.mock("../runs/run-api", () => ({ getRun: vi.fn() }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const names = new Map([["collection", "Morning"]]);
function subject(id: string): ProjectRun {
  return {
    runId: id,
    address: { collection_id: "collection", number: 7, run_id: id },
  } as ProjectRun;
}
function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const open = vi.fn();
  const view = (projectId = "a", unavailable = false) => (
    <QueryClientProvider client={client}>
      <RunIdLookup
        key={projectId}
        projectId={projectId}
        unavailable={unavailable}
        names={names}
        onOpenRun={open}
      />
    </QueryClientProvider>
  );
  const mounted = render(view());
  return { client, open, mounted, view, find };
}
it("distinguishes invalid input, missing opaque IDs, and unavailable service", async () => {
  const { find } = setup();
  find("https://example/run/123");
  expect(screen.getByRole("alert")).toHaveTextContent("Invalid run ID format");
  expect(getRun).not.toHaveBeenCalled();
  vi.mocked(getRun).mockRejectedValueOnce(new ApiError("Not found", 404));
  find("run_legacy_opaque");
  expect(await screen.findByRole("alert")).toHaveTextContent("Run not found in this data space");
  vi.mocked(getRun).mockRejectedValueOnce(new ApiError("Unavailable", 503));
  find("01a13403-f4f3-743a-b1a1-2ea87934729b");
  expect(await screen.findByRole("alert")).toHaveTextContent("Run lookup unavailable");
});
it("ignores late requests and separates identical IDs across data spaces and offline state", async () => {
  let finish!: (value: ProjectRun) => void;
  vi.mocked(getRun).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { find, view, mounted, open } = setup();
  find("earlier");
  vi.mocked(getRun).mockResolvedValue(subject("same-id"));
  find("same-id");
  await screen.findByText("Morning · #7");
  await act(async () => finish(subject("earlier")));
  fireEvent.click(screen.getByRole("button", { name: "Open found run", hidden: true }));
  expect(open).toHaveBeenCalledWith("same-id");
  mounted.rerender(view("b"));
  expect(screen.getByLabelText("Full run ID")).toHaveValue("");
  expect(screen.queryByText("Morning · #7")).not.toBeInTheDocument();
  vi.mocked(getRun).mockRejectedValueOnce(new ApiError("missing", 404));
  find("same-id");
  expect(await screen.findByRole("alert")).toHaveTextContent("Run not found");
  mounted.rerender(view("b", true));
  expect(screen.getByText(/reconnect to this data space/)).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Open found run", hidden: true }),
  ).not.toBeInTheDocument();
});

it("hides the cached result while repeating an exact lookup and reports its failure", async () => {
  vi.mocked(getRun).mockResolvedValueOnce(subject("same-id"));
  const { find } = setup();
  find("same-id");
  await screen.findByRole("button", { name: "Open found run" });
  let fail!: (error: Error) => void;
  vi.mocked(getRun).mockImplementationOnce(
    () =>
      new Promise((_resolve, reject) => {
        fail = reject;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Find exact run" }));
  await waitFor(() => expect(getRun).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("button", { name: "Open found run" })).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Finding run");
  await act(async () => fail(new ApiError("Unavailable", 503)));
  expect(await screen.findByRole("alert")).toHaveTextContent("Run lookup unavailable");
  expect(screen.queryByRole("button", { name: "Open found run" })).not.toBeInTheDocument();
});

const find = (id: string) => {
  screen.getByText("Find by full run ID").closest("details")!.open = true;
  fireEvent.change(screen.getByLabelText("Full run ID"), { target: { value: id } });
  fireEvent.submit(screen.getByLabelText("Full run ID").closest("form")!);
};
